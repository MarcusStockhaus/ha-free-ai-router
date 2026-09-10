"""Faehigkeitserkennung — misst, was ein Schluessel tatsaechlich kann.

Eigenstaendiges Modul mit zwei Verwendern:

* der Config Flow beim Einrichten (Phase 1),
* der Prober unter Cron fuer den Feed-Dienst (Phase 3).

Deshalb kein ``homeassistant``-Import und keine Annahme ueber den Aufrufer.
Die Pruefungen sind in Stufen geschnitten (:data:`CHECK_LIVENESS` usw.), damit
der Prober spaeter die billigen stuendlich und die teuren taeglich fahren kann,
ohne genau das Kontingent zu verbrauchen, das er vermessen soll.

Grundsatz: Nichts wird abgeschrieben. Was hier nicht gemessen wurde, bleibt
``None`` — und ``None`` heisst "unbekannt", nicht "kann es nicht".
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .adapters import ChatRequest, ImageAttachment, ProviderError, ToolSpec, get_adapter
from .const import PROBE_TIMEOUT_S
from .ratelimit import RateLimitInfo
from .registry import Model, Provider
from .testimage import make_challenge

_LOGGER = logging.getLogger(__name__)

CHECK_LIVENESS = "liveness"
CHECK_STRUCTURED = "structured_output"
CHECK_VISION = "vision"
CHECK_TOOLS = "tools"

ALL_CHECKS: tuple[str, ...] = (CHECK_LIVENESS, CHECK_STRUCTURED, CHECK_VISION, CHECK_TOOLS)
#: Billig genug fuer den stuendlichen Takt des Probers (ein Request je Modell).
CHEAP_CHECKS: tuple[str, ...] = (CHECK_LIVENESS,)

# Mini-Schema fuer die Structured-Output-Pruefung.
_STRUCT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "farbe": {"type": "string", "description": "die haeufigste Farbe"},
        "anzahl": {"type": "integer", "description": "Gesamtzahl der Aepfel"},
    },
    "required": ["farbe", "anzahl"],
}
_STRUCT_PROMPT = (
    "In einer Schale liegen ein roter Apfel und zwei gruene Aepfel. "
    "Nenne die haeufigste Farbe und die Gesamtzahl."
)

_TOOL = ToolSpec(
    name="licht_schalten",
    description="Schaltet das Licht in einem Raum ein oder aus.",
    parameters={
        "type": "object",
        "properties": {
            "raum": {"type": "string", "description": "Name des Raums"},
            "an": {"type": "boolean", "description": "true = einschalten"},
        },
        "required": ["raum", "an"],
    },
)
_TOOL_PROMPT = "Schalte das Licht im Wohnzimmer ein. Nutze dafuer das Werkzeug."

#: Ausgabebudget je Pruefaufruf. Grosszuegig, weil viele Modelle erst
#: nachdenken und dann antworten: mit 32 Token kommt bei ihnen eine leere
#: Antwort zurueck, und die Messung meldet faelschlich "kann es nicht".
PROBE_OUTPUT_TOKENS = 768

ProgressCallback = Callable[[int, int, str], None]


# --------------------------------------------------------------------------
# Ergebnisse
# --------------------------------------------------------------------------


@dataclass(slots=True)
class CheckResult:
    """Ergebnis einer einzelnen Pruefung."""

    ok: bool | None = None
    """``None`` = nicht geprueft."""
    detail: str = ""
    duration_s: float | None = None

    @property
    def symbol(self) -> str:
        return {True: "ja", False: "nein", None: "?"}[self.ok]


@dataclass(slots=True)
class ModelProbe:
    """Was ein Modell mit diesem Schluessel kann."""

    provider_id: str
    model_id: str
    alive: bool = False
    status: int | None = None
    error: str = ""
    latency_total_s: float | None = None
    ttft_s: float | None = None
    liveness: CheckResult = field(default_factory=CheckResult)
    structured_output: CheckResult = field(default_factory=CheckResult)
    vision: CheckResult = field(default_factory=CheckResult)
    vision_looked: bool | None = None
    """Hat das Modell die Farbe des Testbildes korrekt genannt?"""
    tools: CheckResult = field(default_factory=CheckResult)
    structured_mode: str | None = None
    rate_limit: RateLimitInfo = field(default_factory=RateLimitInfo)
    checked_at: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        return f"{self.provider_id}/{self.model_id}"

    def measured_capabilities(self) -> dict[str, Any]:
        """Nur das, was wirklich gemessen wurde — fuer die Registry-Ueberschreibung."""
        measured: dict[str, Any] = {}
        if self.vision.ok is not None:
            measured["vision"] = self.vision.ok
        if self.tools.ok is not None:
            measured["tools"] = self.tools.ok
        if self.structured_output.ok is not None:
            measured["structured_output"] = self.structured_output.ok
        return measured

    def measured_limits(self) -> dict[str, Any]:
        """Limits aus den Antwortheadern, soweit erkannt."""
        limits: dict[str, Any] = {}
        info = self.rate_limit
        if info.limit_requests and info.reset_requests_s:
            # Ein Reset unter zwei Minuten deutet auf ein Minutenfenster,
            # alles darueber auf ein Tagesfenster. Mehr ist aus einem
            # einzelnen Header nicht sicher abzuleiten.
            if info.reset_requests_s <= 120:
                limits["rpm"] = info.limit_requests
            else:
                limits["rpd"] = info.limit_requests
        if info.limit_tokens and info.reset_tokens_s and info.reset_tokens_s <= 120:
            limits["tpm"] = info.limit_tokens
        return limits

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "alive": self.alive,
            "status": self.status,
            "error": self.error,
            "latency_total_s": self.latency_total_s,
            "ttft_s": self.ttft_s,
            "checks": {
                "liveness": {"ok": self.liveness.ok, "detail": self.liveness.detail},
                "structured_output": {
                    "ok": self.structured_output.ok,
                    "detail": self.structured_output.detail,
                    "mode": self.structured_mode,
                },
                "vision": {
                    "ok": self.vision.ok,
                    "detail": self.vision.detail,
                    "looked": self.vision_looked,
                },
                "tools": {"ok": self.tools.ok, "detail": self.tools.detail},
            },
            "rate_limit": self.rate_limit.as_dict(),
            "checked_at": self.checked_at,
        }


@dataclass(slots=True)
class ProviderProbe:
    """Ergebnis fuer einen Anbieter."""

    provider_id: str
    key_valid: bool = False
    error: str = ""
    models: list[ModelProbe] = field(default_factory=list)
    discovered_models: list[str] = field(default_factory=list)
    """Was der Anbieter selbst als verfuegbar meldet — deckt tote Registry-Eintraege auf."""

    @property
    def working_models(self) -> list[ModelProbe]:
        return [probe for probe in self.models if probe.alive]

    @property
    def stale_registry_entries(self) -> list[str]:
        """Registry-Modelle, die der Anbieter nicht mehr fuehrt."""
        if not self.discovered_models:
            return []
        available = set(self.discovered_models)
        return [probe.model_id for probe in self.models if probe.model_id not in available]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "key_valid": self.key_valid,
            "error": self.error,
            "models": [probe.as_dict() for probe in self.models],
            "discovered_models": self.discovered_models,
        }


# --------------------------------------------------------------------------
# Messung
# --------------------------------------------------------------------------


async def probe_model(
    session: aiohttp.ClientSession,
    provider: Provider,
    api_key: str,
    model: Model,
    *,
    checks: Sequence[str] = ALL_CHECKS,
    timeout: float = PROBE_TIMEOUT_S,
) -> ModelProbe:
    """Vermisst ein einzelnes Modell.

    Fehler werden festgehalten, nicht geworfen: ein toter Kanal ist ein
    Messergebnis. Nur Programmierfehler duerfen nach oben durchschlagen.
    """
    adapter = get_adapter(provider.api_style)
    result = ModelProbe(provider_id=provider.id, model_id=model.id)

    # --- lebt es? Gleichzeitig Latenz- und Header-Messung. ---------------
    if CHECK_LIVENESS in checks:
        started = time.monotonic()
        try:
            response = await adapter.chat_streaming(
                session,
                provider,
                api_key,
                ChatRequest(
                    model=model.id,
                    instructions="Antworte mit genau einem Wort: bereit.",
                    max_output_tokens=PROBE_OUTPUT_TOKENS,
                    thinking_budget=0,
                ),
                timeout=timeout,
            )
        except ProviderError as err:
            result.status = err.status
            result.error = str(err)
            result.rate_limit = err.rate_limit
            result.liveness = CheckResult(
                ok=False, detail=str(err), duration_s=time.monotonic() - started
            )
            return result
        except (TimeoutError, aiohttp.ClientError) as err:
            result.error = f"Netzwerk: {err!r}"
            result.liveness = CheckResult(
                ok=False, detail=result.error, duration_s=time.monotonic() - started
            )
            return result

        result.alive = True
        result.status = response.status
        result.latency_total_s = response.total_s
        result.ttft_s = response.ttft_s
        result.rate_limit = response.rate_limit
        result.liveness = CheckResult(
            ok=True,
            detail=(response.text or "").strip()[:60],
            duration_s=response.total_s,
        )

    # --- Structured Output ----------------------------------------------
    if CHECK_STRUCTURED in checks:
        result.structured_output, result.structured_mode = await _check_structured(
            adapter, session, provider, api_key, model, timeout
        )

    # --- Vision ----------------------------------------------------------
    if CHECK_VISION in checks:
        result.vision, result.vision_looked = await _check_vision(
            adapter, session, provider, api_key, model, timeout
        )

    # --- Tool-Calling -----------------------------------------------------
    if CHECK_TOOLS in checks:
        result.tools = await _check_tools(adapter, session, provider, api_key, model, timeout)

    return result


async def _check_structured(
    adapter: Any,
    session: aiohttp.ClientSession,
    provider: Provider,
    api_key: str,
    model: Model,
    timeout: float,
) -> tuple[CheckResult, str | None]:
    started = time.monotonic()
    try:
        response = await adapter.chat(
            session,
            provider,
            api_key,
            ChatRequest(
                model=model.id,
                instructions=_STRUCT_PROMPT,
                json_schema=_STRUCT_SCHEMA,
                max_output_tokens=PROBE_OUTPUT_TOKENS,
                thinking_budget=0,
            ),
            timeout=timeout,
        )
    except (TimeoutError, ProviderError, aiohttp.ClientError) as err:
        return CheckResult(False, str(err), time.monotonic() - started), None

    parsed = response.parsed
    problem = _validate_struct(parsed)
    if problem:
        return (
            CheckResult(False, problem, response.total_s),
            response.structured_mode,
        )
    return (
        CheckResult(True, f"{parsed} ({response.structured_mode})", response.total_s),
        response.structured_mode,
    )


def _validate_struct(parsed: Any) -> str:
    """Pruefe die Antwort gegen das Mini-Schema. Leerer Text heisst: in Ordnung."""
    if not isinstance(parsed, dict):
        return f"kein JSON-Objekt: {parsed!r}"[:120]
    missing = [key for key in ("farbe", "anzahl") if key not in parsed]
    if missing:
        return f"Felder fehlen: {missing}"
    if not isinstance(parsed["farbe"], str):
        return "farbe ist kein Text"
    if not isinstance(parsed["anzahl"], int) or isinstance(parsed["anzahl"], bool):
        return "anzahl ist keine ganze Zahl"
    return ""


async def _check_vision(
    adapter: Any,
    session: aiohttp.ClientSession,
    provider: Provider,
    api_key: str,
    model: Model,
    timeout: float,
) -> tuple[CheckResult, bool | None]:
    started = time.monotonic()
    challenge = make_challenge()
    image = ImageAttachment(mime_type=challenge.mime_type, data=challenge.image)
    try:
        response = await adapter.chat(
            session,
            provider,
            api_key,
            ChatRequest(
                model=model.id,
                instructions=challenge.prompt,
                images=(image,),
                max_output_tokens=PROBE_OUTPUT_TOKENS,
                thinking_budget=0,
            ),
            timeout=timeout,
        )
    except (TimeoutError, ProviderError, aiohttp.ClientError) as err:
        return CheckResult(False, str(err), time.monotonic() - started), None

    detail = (response.text or "").strip()[:60]
    if not challenge.solved(response.text):
        # Angenommen, aber nicht angesehen: manche Endpunkte verwerfen den
        # Bildteil stillschweigend und antworten trotzdem. Als Vision-Kanal
        # taugt das nichts.
        falsch = challenge.wrong_colors(response.text)
        hinweis = f", genannt: {', '.join(falsch)}" if falsch else ""
        return (
            CheckResult(
                False,
                f"erwartet {challenge.expected_text}{hinweis} — Antwort: {detail!r}",
                response.total_s,
            ),
            False,
        )
    return CheckResult(True, f"{challenge.expected_text} erkannt", response.total_s), True


async def _check_tools(
    adapter: Any,
    session: aiohttp.ClientSession,
    provider: Provider,
    api_key: str,
    model: Model,
    timeout: float,
) -> CheckResult:
    started = time.monotonic()
    try:
        response = await adapter.chat(
            session,
            provider,
            api_key,
            ChatRequest(
                model=model.id,
                instructions=_TOOL_PROMPT,
                tools=(_TOOL,),
                max_output_tokens=PROBE_OUTPUT_TOKENS,
                thinking_budget=0,
            ),
            timeout=timeout,
        )
    except (TimeoutError, ProviderError, aiohttp.ClientError) as err:
        return CheckResult(False, str(err), time.monotonic() - started)

    if not response.tool_calls:
        return CheckResult(False, f"kein Aufruf: {response.text.strip()[:60]!r}", response.total_s)
    call = response.tool_calls[0]
    return CheckResult(True, f"{call.name}({call.arguments})"[:80], response.total_s)


async def probe_provider(
    session: aiohttp.ClientSession,
    provider: Provider,
    api_key: str,
    *,
    models: Iterable[Model] | None = None,
    checks: Sequence[str] = ALL_CHECKS,
    timeout: float = PROBE_TIMEOUT_S,
    concurrency: int = 2,
    discover: bool = False,
    on_progress: ProgressCallback | None = None,
) -> ProviderProbe:
    """Vermisst alle Modelle eines Anbieters.

    ``concurrency`` bleibt bewusst klein: mehrere gleichzeitige Anfragen
    reissen bei 5-15 RPM sofort das Minutenfenster, und dann misst man den
    eigenen Ansturm statt der Faehigkeiten.
    """
    result = ProviderProbe(provider_id=provider.id)
    todo = list(models if models is not None else provider.models)

    if discover:
        adapter = get_adapter(provider.api_style)
        try:
            result.discovered_models = await adapter.list_models(
                session, provider, api_key, timeout=timeout
            )
        except ProviderError as err:
            if err.is_auth:
                result.error = str(err)
                return result
            _LOGGER.debug("%s: Modell-Liste nicht abrufbar: %s", provider.id, err)
        except (TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.debug("%s: Modell-Liste nicht abrufbar: %r", provider.id, err)

    semaphore = asyncio.Semaphore(max(1, concurrency))
    done = 0
    total = len(todo)

    async def run(model: Model) -> ModelProbe:
        nonlocal done
        async with semaphore:
            probe = await probe_model(
                session, provider, api_key, model, checks=checks, timeout=timeout
            )
        done += 1
        if on_progress is not None:
            on_progress(done, total, f"{provider.name} / {model.display_name}")
        return probe

    result.models = list(await asyncio.gather(*(run(model) for model in todo)))
    result.key_valid = any(probe.alive for probe in result.models)
    if not result.key_valid and not result.error:
        auth_errors = [probe.error for probe in result.models if probe.status in (401, 403)]
        result.error = auth_errors[0] if auth_errors else "kein Modell erreichbar"
    return result


async def quick_key_check(
    session: aiohttp.ClientSession,
    provider: Provider,
    api_key: str,
    *,
    timeout: float = PROBE_TIMEOUT_S,
) -> tuple[bool, str]:
    """Ein einzelner Aufruf, der sofort nach der Key-Eingabe Rueckmeldung gibt.

    Nimmt das erste Modell des Anbieters. Ergebnis: (gueltig, Meldung).
    """
    model = provider.models[0]
    probe = await probe_model(
        session, provider, api_key, model, checks=(CHECK_LIVENESS,), timeout=timeout
    )
    if probe.alive:
        latency = f"{probe.latency_total_s:.1f} s" if probe.latency_total_s else "?"
        return True, f"{model.display_name} antwortet ({latency})"
    return False, probe.error or "keine Antwort"


def merge_into_registry(
    provider: Provider, probes: Iterable[ModelProbe]
) -> dict[str, dict[str, Any]]:
    """Fasse Messergebnisse als Ueberschreibungen je Modellschluessel zusammen.

    Das Ergebnis landet im Config Entry und wird beim Laden mit
    ``registry.apply_measured_capabilities`` bzw. ``apply_measured_limits``
    ueber die Startwerte der YAML-Datei gelegt.
    """
    del provider  # Signatur bleibt symmetrisch zu probe_provider
    overrides: dict[str, dict[str, Any]] = {}
    for probe in probes:
        overrides[probe.key] = {
            "alive": probe.alive,
            "capabilities": probe.measured_capabilities(),
            "limits": probe.measured_limits(),
            "latency_total_s": probe.latency_total_s,
            "ttft_s": probe.ttft_s,
            "checked_at": probe.checked_at,
        }
    return overrides


async def gather_probes(
    session: aiohttp.ClientSession,
    jobs: Sequence[tuple[Provider, str]],
    *,
    checks: Sequence[str] = ALL_CHECKS,
    timeout: float = PROBE_TIMEOUT_S,
    discover: bool = False,
    on_progress: ProgressCallback | None = None,
) -> list[ProviderProbe]:
    """Vermisst mehrere Anbieter nacheinander.

    Nacheinander und nicht parallel: die Ausgabe soll lesbar bleiben und die
    Fortschrittsanzeige im Config Flow eine ehrliche Reihenfolge zeigen.
    """
    results: list[ProviderProbe] = []
    for provider, api_key in jobs:
        results.append(
            await probe_provider(
                session,
                provider,
                api_key,
                checks=checks,
                timeout=timeout,
                discover=discover,
                on_progress=on_progress,
            )
        )
    return results


__all__ = [
    "ALL_CHECKS",
    "CHEAP_CHECKS",
    "CHECK_LIVENESS",
    "CHECK_STRUCTURED",
    "CHECK_TOOLS",
    "CHECK_VISION",
    "CheckResult",
    "ModelProbe",
    "ProviderProbe",
    "gather_probes",
    "merge_into_registry",
    "probe_model",
    "probe_provider",
    "quick_key_check",
]
