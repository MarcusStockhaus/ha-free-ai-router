"""Faehigkeitserkennung — misst, was ein Schluessel tatsaechlich kann.

Eigenstaendiges Modul mit drei Verwendern:

* die Hintergrundmessung in Home Assistant (``hintergrund.py``),
* das Probe-CLI fuer Entwickler (``tools/probe_cli.py``),
* der Modell-Waechter, der neue Modelle einmal anmisst (``tools/waechter.py``).

Deshalb kein ``homeassistant``-Import und keine Annahme ueber den Aufrufer.
Die Pruefungen sind in Stufen geschnitten (:data:`CHECK_LIVENESS` usw.), damit
ein sparsamer Lauf nur die Lebendigkeit pruefen kann, ohne genau das
Kontingent zu verbrauchen, das er vermessen soll.

Grundsatz: Nichts wird abgeschrieben. Was hier nicht gemessen wurde, bleibt
``None`` — und ``None`` heisst "unbekannt", nicht "kann es nicht".

Der Unterschied ist nicht akademisch. Ein gestoerter Anbieter (Zeitueberschreitung,
500, Ratenlimit) hat ueber das Modell nichts ausgesagt; nur eine gelesene und
abgelehnte Anfrage hat das. Wer beides als "kann es nicht" bucht, schaltet beim
naechsten Schluckauf des Anbieters eine Faehigkeit ab.
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
from .sprache import DE, t
from .testimage import make_challenge

_LOGGER = logging.getLogger(__name__)

CHECK_LIVENESS = "liveness"
CHECK_STRUCTURED = "structured_output"
CHECK_VISION = "vision"
CHECK_TOOLS = "tools"

ALL_CHECKS: tuple[str, ...] = (CHECK_LIVENESS, CHECK_STRUCTURED, CHECK_VISION, CHECK_TOOLS)
#: Ein Aufruf je Modell — nur: antwortet es noch?
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

#: Runden der Bildpruefung. Eine Runde laesst sich mit rund fuenf Prozent
#: Wahrscheinlichkeit erraten; zwei druecken das unter ein Promille. Mehr
#: kostet nur Kontingent, ohne noch etwas zu klaeren.
VISION_ROUNDS = 2

ProgressCallback = Callable[[int, int, str], None]

#: Statuscodes, aus denen sich ueber die Faehigkeit eines Modells nichts
#: ablesen laesst: der Anbieter hat gar nicht erst gemessen.
_SAGT_NICHTS = frozenset({401, 402, 403, 408, 409, 425, 429})


def _nicht_messbar(err: Exception) -> bool:
    """War der Fehler eine Aussage ueber das Modell — oder nur schlechtes Wetter?

    Ein 400 ist eine Aussage: der Endpunkt hat die Anfrage gelesen und
    abgelehnt, etwa weil er ``responseSchema`` nicht kennt. Eine
    Zeitueberschreitung, ein 500 oder ein Ratenlimit sind keine. Sie als
    "kann es nicht" zu buchen war der Fehler, der am 11.09.2026 im ersten
    Feed stand: Googles Gemma war eine Viertelstunde lang gestoert und wurde
    darauf fuer alle Installationen als blind veroeffentlicht.
    """
    status = getattr(err, "status", None)
    if status is None:
        return True  # Netz, Zeitueberschreitung, abgebrochene Verbindung
    # Ein Fehler mit Erfolgsstatus ist ein vom Vermittler eingepackter
    # Fehler des eigentlichen Anbieters. Live am 24.09.2026: OpenRouter
    # antwortete auf die Bildpruefung mit HTTP 200 und "Upstream error from
    # Nvidia: ResourceExhausted" — das ist ausgelastete Hardware, keine
    # Aussage darueber, ob das Modell Bilder sieht.
    return status < 400 or status in _SAGT_NICHTS or status >= 500


def _fehlversuch(err: Exception, dauer: float) -> CheckResult:
    """``ok=None`` heisst unbekannt — und unbekannt ueberschreibt nichts."""
    if _nicht_messbar(err):
        return CheckResult(None, f"nicht messbar: {err}"[:120], dauer)
    return CheckResult(False, str(err), dauer)


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
        # Ein Anfragelimit nur, wenn der Anbieter das Fenster nennt. Frueher
        # wurde es aus der Reset-Zeit geraten — und Groqs Tageslimit von
        # 1.000 landete als 1.000 je Minute im Router. Ohne genanntes Fenster
        # gilt der gepflegte Wert aus der Anbieterdatei.
        if info.limit_requests and info.requests_window_s == 60:
            limits["rpm"] = info.limit_requests
        elif info.limit_requests and info.requests_window_s == 86400:
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
        return _fehlversuch(err, time.monotonic() - started), None

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
    runden: int = VISION_ROUNDS,
) -> tuple[CheckResult, bool | None]:
    """Bildpruefung ueber mehrere Runden mit je neuen Farben.

    Warum mehrfach: eine einzelne Runde laesst sich mit rund fuenf Prozent
    Wahrscheinlichkeit erraten, und genau das ist am 11.09.2026 passiert —
    dasselbe Modell bestand einen Lauf und antwortete im naechsten "Rot, Blau",
    die beiden haeufigsten Verlegenheitsfarben. Zwei Runden druecken die
    Ratequote unter ein Promille.

    Kostet nur bei Modellen, die tatsaechlich sehen, einen zweiten Aufruf:
    die erste falsche Antwort beendet die Pruefung.
    """
    started = time.monotonic()
    erkannt: list[str] = []

    for runde in range(1, runden + 1):
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
            # Eine abgebrochene Runde ist kein Befund ueber das Modell — in
            # keiner Richtung. Frueher zaehlte hier "was bis hierhin stimmte",
            # und damit konnte eine einzige bestandene Runde als Ergebnis
            # durchgehen: genau die fuenf Prozent Ratequote, wegen der die
            # Pruefung ueberhaupt mehrfach laeuft.
            _LOGGER.debug("%s: Vision-Runde %s abgebrochen: %s", model.key, runde, err)
            return _fehlversuch(err, time.monotonic() - started), None

        if not challenge.solved(response.text):
            # Angenommen, aber nicht angesehen: manche Endpunkte verwerfen den
            # Bildteil stillschweigend und antworten trotzdem.
            falsch = challenge.wrong_colors(response.text)
            hinweis = f", genannt: {', '.join(falsch)}" if falsch else ""
            detail = (response.text or "").strip()[:60]
            vorher = f"Runde {runde} von {runden}; " if runde > 1 else ""
            return (
                CheckResult(
                    False,
                    f"{vorher}erwartet {challenge.expected_text}{hinweis} — "
                    f"Antwort: {detail!r}",
                    time.monotonic() - started,
                ),
                False,
            )
        erkannt.append(challenge.expected_text)

    return (
        CheckResult(True, " / ".join(erkannt) + " erkannt", time.monotonic() - started),
        True,
    )


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
        return _fehlversuch(err, time.monotonic() - started)

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


#: Grobe Einordnung eines gescheiterten Schluesseltests. Der Dialog braucht
#: mehr als "abgelehnt": ein Netzausfall, ein gestoerter Anbieter und ein
#: fehlendes Abonnement (Mistrals ``x-ratelimit-limit-req-minute: 0``, siehe
#: DEVELOPMENT.de.md, Befunde) sind drei verschiedene naechste Schritte fuer den Nutzer.
FEHLERART_AUTH = "auth"
FEHLERART_KEIN_ABO = "no_subscription"
FEHLERART_LIMIT = "rate_limited"
FEHLERART_UNERREICHBAR = "unreachable"
FEHLERART_UNBEKANNT = "unknown"


def _fehlerart(probe: ModelProbe) -> str:
    """Warum ein Schluesseltest gescheitert ist — fuer eine passende Meldung.

    ``rate_limit.limit_requests == 0`` ist kein Limit, sondern ein fehlendes
    Abonnement: bei Mistral etwa antwortet ein technisch gueltiger Schluessel
    ohne aktiviertes API-Abo genau so. Das darf nicht als "Schluessel falsch"
    ankommen, sonst probiert der Nutzer denselben Schluessel ein zweites Mal.
    """
    if probe.rate_limit.limit_requests == 0:
        return FEHLERART_KEIN_ABO
    fehler = probe.error.lower()
    if "schlüssel abgelehnt" in fehler:
        return FEHLERART_AUTH
    if probe.status == 429 or "limit erreicht" in fehler:
        return FEHLERART_LIMIT
    if probe.status is None or probe.status >= 500:
        return FEHLERART_UNERREICHBAR
    return FEHLERART_UNBEKANNT


async def quick_key_check(
    session: aiohttp.ClientSession,
    provider: Provider,
    api_key: str,
    *,
    timeout: float = PROBE_TIMEOUT_S,
    sprache: str = DE,
) -> tuple[bool, str, str]:
    """Ein einzelner Aufruf, der sofort nach der Key-Eingabe Rueckmeldung gibt.

    Nimmt das erste Modell des Anbieters. Ergebnis: (gueltig, Meldung, Fehlerart).
    Die Fehlerart ist nur aussagekraeftig, wenn ``gueltig`` falsch ist.
    """
    model = provider.models[0]
    probe = await probe_model(
        session, provider, api_key, model, checks=(CHECK_LIVENESS,), timeout=timeout
    )
    if probe.alive:
        latency = f"{probe.latency_total_s:.1f} s" if probe.latency_total_s else "?"
        return True, t(sprache, "antwortet", modell=model.display_name, dauer=latency), ""
    return False, probe.error or t(sprache, "keine_antwort"), _fehlerart(probe)


def merge_into_registry(
    provider: Provider, probes: Iterable[ModelProbe], sprache: str = DE
) -> dict[str, dict[str, Any]]:
    """Fasse Messergebnisse als Ueberschreibungen je Modellschluessel zusammen.

    Das Ergebnis landet im Config Entry und wird beim Laden mit
    ``registry.apply_measured_capabilities`` bzw. ``apply_measured_limits``
    ueber die Startwerte der YAML-Datei gelegt.
    """
    del provider  # Signatur bleibt symmetrisch zu probe_provider
    return {probe.key: _eintrag(probe, sprache) for probe in probes}


def _eintrag(probe: ModelProbe, sprache: str = DE) -> dict[str, Any]:
    """Ein Messergebnis in der Form, in der es im Subentry liegt."""
    eintrag: dict[str, Any] = {
        "alive": probe.alive,
        "capabilities": probe.measured_capabilities(),
        "limits": probe.measured_limits(),
        "latency_total_s": probe.latency_total_s,
        "ttft_s": probe.ttft_s,
        "checked_at": probe.checked_at,
    }
    if not probe.alive:
        # Ohne Grund laesst sich spaeter nicht unterscheiden, ob ein totes
        # Modell nachgeprueft gehoert. Eintraege ohne dieses Feld stammen aus
        # der Zeit, als ein 503 noch als "tot" galt — sie werden beim
        # naechsten Hintergrundlauf sofort nachgemessen.
        eintrag["grund"] = kurzgrund(probe, sprache)
    return eintrag


#: Felder, die :func:`describe_changes` vergleicht.
_VERGLEICHSFELDER = ("vision", "tools", "structured_output")
_JA_NEIN = {True: "ja", False: "nein", None: "unbekannt"}


def merge_overrides(vorher: dict[str, Any], nachher: dict[str, Any]) -> dict[str, Any]:
    """Neue Messung ueber die alte legen — ohne das Ungemessene zu loeschen.

    Eine sparsame Messung prueft Bilder, Schemata und Werkzeuge gar nicht.
    Ihre leeren Felder duerfen die frueheren Befunde nicht ueberschreiben:
    "nicht gemessen" ist kein "kann es nicht". Derselbe Unterschied wie bei
    ``CheckResult.ok is None``, nur eine Ebene hoeher.
    """
    ergebnis = dict(nachher)
    ergebnis["capabilities"] = {
        **(vorher.get("capabilities") or {}),
        **(nachher.get("capabilities") or {}),
    }
    ergebnis["limits"] = {**(vorher.get("limits") or {}), **(nachher.get("limits") or {})}
    return ergebnis


def describe_changes(
    vorher: dict[str, dict[str, Any]], nachher: dict[str, dict[str, Any]]
) -> list[str]:
    """Was eine Messung am bisherigen Stand geaendert hat, in Klartext.

    Das ist die eigentliche Antwort auf "was hat das gebracht" — eine
    Messung ohne Aenderung ist ein genauso gueltiges Ergebnis, aber sie soll
    sich von einer unterscheiden lassen, die etwas gedreht hat.
    """
    zeilen: list[str] = []
    for key, neu in sorted(nachher.items()):
        alt = vorher.get(key) or {}
        modell = key.split("/", 1)[-1]
        alt_lebt, neu_lebt = bool(alt.get("alive", True)), bool(neu.get("alive", True))
        if alt and alt_lebt != neu_lebt:
            zeilen.append(f"{modell}: erreichbar {_JA_NEIN[alt_lebt]} -> {_JA_NEIN[neu_lebt]}")
        alte_caps = alt.get("capabilities") or {}
        neue_caps = neu.get("capabilities") or {}
        for feld in _VERGLEICHSFELDER:
            if feld in neue_caps and alte_caps.get(feld) != neue_caps[feld]:
                zeilen.append(
                    f"{modell}: {feld} {_JA_NEIN[alte_caps.get(feld)]} "
                    f"-> {_JA_NEIN[neue_caps[feld]]}"
                )
    return zeilen


def should_discard(vorher: dict[str, dict[str, Any]], lebendig: int) -> bool:
    """Ist dieses Messergebnis unbrauchbar und sollte verworfen werden?

    Wahr, wenn kein einziges Modell geantwortet hat, obwohl vorher welche
    liefen. Das ist fast immer die eigene Leitung und nicht das Ende des
    Anbieters — und eine kaputte Leitung darf nicht dazu fuehren, dass sich
    die Installation selbst die Kanaele abschaltet.

    Beim ersten Mal (``vorher`` leer) gibt es nichts zu schuetzen: dann ist
    auch ein durchweg totes Ergebnis ein Ergebnis.
    """
    if lebendig > 0 or not vorher:
        return False
    return any(eintrag.get("alive", True) for eintrag in vorher.values())


# --------------------------------------------------------------------------
# Messen im Hintergrund: was faellig ist, was bleibt, was gemeldet wird
# --------------------------------------------------------------------------

#: So lange bleibt ein als tot gemessenes Modell unangetastet, bevor der
#: Hintergrundlauf es erneut versucht. Ein Versuch kostet genau einen Aufruf:
#: ``probe_model`` bricht nach gescheiterter Lebendpruefung ab.
TOT_NACHPRUEFEN_S = 7 * 86400


def ist_voruebergehend(probe: ModelProbe, *, schluessel_gueltig: bool) -> bool:
    """Ist das Scheitern der Lebendpruefung eine Aussage ueber das Modell?

    Nein bei allem, was mit dem Anbieter gerade los ist und nicht mit dem
    Modell: Netz, Zeitueberschreitung, 5xx, ein Ratenlimit mit echtem
    Kontingent, ein vom Vermittler eingepackter Fehler (Status unter 400).
    Live am 24.09.2026 genau so aufgetreten — ``gemini-3.8-flash`` mit 503
    "high demand", ``nemotron-3.5-lightning`` mit Zeitueberschreitung. Beide
    waren danach bis zur Handmessung abgeschaltet.

    Ja bei einem Ratenlimit ohne Kontingent (``limit_requests == 0``, bei
    Mistral das fehlende API-Abo), bei 400/404 und bei 402.

    Die Ausnahme ist 401: bei gueltigem Schluessel (ein anderes Modell hat im
    selben Lauf geantwortet) war das live eine Verzoegerung beim Anbieter —
    am 17.09.2026 lehnte Mistral denselben Schluessel fuer zwei Modelle ab,
    den es fuer ein drittes annahm, eine Woche spaeter fuer alle. Bei
    ungueltigem Schluessel ist es ein Schluesselproblem und kein
    Modellbefund; das meldet der Reparaturhinweis aus dem laufenden Betrieb.
    Ein 403 ohne gueltigen Schluessel gilt aus demselben Grund als offen.
    """
    if probe.alive:
        return False
    if probe.rate_limit.limit_requests == 0:
        return False
    status = probe.status
    if status is None or status < 400 or status >= 500:
        return True
    if status in (408, 409, 425, 429):
        return True
    if status == 401:
        return True
    if status == 403:
        return not schluessel_gueltig
    return False


def kurzgrund(probe: ModelProbe, sprache: str = DE) -> str:
    """Warum ein Modell nicht geantwortet hat — ein paar Worte statt JSON."""
    if probe.rate_limit.limit_requests == 0:
        return t(sprache, "grund_kein_kontingent")
    status = probe.status
    if status is None:
        return t(sprache, "grund_keine_antwort")
    if status < 400:
        return t(sprache, "grund_vermittler")
    if status in (400, 401, 402, 403, 404, 408, 429):
        return t(sprache, f"grund_{status}")
    if status >= 500:
        return t(sprache, "grund_5xx", status=status)
    return t(sprache, "grund_http", status=status)


def uebernehmen(
    vorher: dict[str, dict[str, Any]], probes: Iterable[ModelProbe], sprache: str = DE
) -> dict[str, dict[str, Any]]:
    """Eine Messung in den gespeicherten Stand einrechnen.

    * Hat das Modell geantwortet, gilt die neue Messung — wo sie nichts sagt
      (``None``), bleibt der alte Befund stehen.
    * Ist es definitiv nicht nutzbar, wird es als tot gespeichert, mit Grund.
    * Ist es nur voruebergehend gestoert, bleibt der alte Eintrag unveraendert.
      Gab es keinen, bleibt das Modell offen und wird beim naechsten Lauf
      wieder versucht — genau wie ein nie gemessenes.

    Damit braucht es die alte Notbremse fuer Netzausfaelle nicht mehr: eine
    tote Leitung liefert nur voruebergehende Fehler und ueberschreibt nichts.
    """
    probes = list(probes)
    schluessel_gueltig = any(probe.alive for probe in probes)
    nachher = dict(vorher)
    for probe in probes:
        if not probe.alive and ist_voruebergehend(probe, schluessel_gueltig=schluessel_gueltig):
            alt = vorher.get(probe.key) or {}
            if alt.get("alive") is False and "grund" not in alt:
                # Ein "tot" ohne Grund stammt aus der Zeit, als schon ein 503
                # als tot galt — es war nie ein Befund. Bleibt die neue
                # Messung wieder nur gestoert, ist das Modell offen, nicht tot:
                # es laeuft mit den Startwerten und wird weiter versucht. Live
                # am 24.09.2026: gemini-3.8-flash (503) und
                # nemotron-3.5-lightning (Zeitueberschreitung) blieben sonst
                # abgeschaltet, obwohl nichts gegen sie sprach.
                nachher.pop(probe.key, None)
            continue
        eintrag = _eintrag(probe, sprache)
        if probe.alive:
            eintrag = merge_overrides(vorher.get(probe.key) or {}, eintrag)
        nachher[probe.key] = eintrag
    return nachher


def ohne_geratene_anfragelimits(
    gespeichert: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Gespeicherte Messwerte ohne ``rpm``/``rpd`` — fuer die Migration.

    Vor dem 24.09.2026 wurde das Fenster eines Anfragelimits aus der
    Reset-Zeit geraten. Welcher gespeicherte Wert davon betroffen ist, laesst
    sich im Nachhinein nicht sagen; die Anbieterdateien fuehren fuer jedes
    Modell gepflegte Werte, also fallen alle geschaetzten weg.
    """
    ergebnis: dict[str, dict[str, Any]] = {}
    for key, eintrag in gespeichert.items():
        limits = {
            name: wert
            for name, wert in (eintrag.get("limits") or {}).items()
            if name not in ("rpm", "rpd")
        }
        ergebnis[key] = {**eintrag, "limits": limits} if "limits" in eintrag else dict(eintrag)
    return ergebnis


def faellige_modelle(
    provider: Provider,
    gespeichert: dict[str, dict[str, Any]],
    jetzt: float,
    *,
    tot_nachpruefen_s: float = TOT_NACHPRUEFEN_S,
) -> list[Model]:
    """Welche Modelle ein Hintergrundlauf messen soll.

    Faellig ist, was nie gemessen wurde (auch: bisher nur voruebergehend
    gestoert), was als tot gilt, aber keinen Grund traegt (Messung aus der
    Zeit vor dieser Unterscheidung), und was laenger als eine Woche tot ist.
    Was lebt, wird nicht periodisch nachgemessen: Faehigkeiten sind
    Eigenschaften des Modells, und ob es noch antwortet, zeigt der laufende
    Betrieb ohnehin — ohne einen einzigen Aufruf aus dem Kontingent.
    """
    faellig: list[Model] = []
    for model in provider.models:
        eintrag = gespeichert.get(model.key) or gespeichert.get(model.id)
        if not eintrag:
            faellig.append(model)
            continue
        if eintrag.get("alive", True):
            continue
        if "grund" not in eintrag:
            faellig.append(model)
            continue
        if jetzt - float(eintrag.get("checked_at") or 0) > tot_nachpruefen_s:
            faellig.append(model)
    return faellig


def bericht_zeilen(
    probes: Iterable[ModelProbe],
    *,
    schluessel_gueltig: bool | None = None,
    sprache: str = DE,
) -> list[str]:
    """Eine Zeile je gemessenem Modell, fuer Benachrichtigung und Dienst.

    Bewusst ohne rohe Fehlertexte der Anbieter: dass ein reines Textmodell
    "messages[0].content must be a string" antwortet, wenn man ihm ein Bild
    schickt, ist die erwartete Antwort und keine Meldung wert.
    """
    probes = list(probes)
    if schluessel_gueltig is None:
        schluessel_gueltig = any(probe.alive for probe in probes)
    zeilen: list[str] = []
    for probe in probes:
        if probe.alive:
            pruefungen = (
                (t(sprache, "faehigkeit_bilder"), probe.vision),
                (t(sprache, "faehigkeit_schema"), probe.structured_output),
                (t(sprache, "faehigkeit_werkzeuge"), probe.tools),
            )
            kann = [name for name, ergebnis in pruefungen if ergebnis.ok]
            if all(ergebnis.ok is None for _name, ergebnis in pruefungen):
                # Sparsamer Lauf: nur die Lebendpruefung. "nur Text" waere
                # hier eine Behauptung ueber etwas, das nicht geprueft wurde.
                befund = t(sprache, "bericht_erreichbar")
            else:
                befund = ", ".join(kann) or t(sprache, "bericht_nur_text")
            dauer = f" · {probe.latency_total_s:.1f} s" if probe.latency_total_s else ""
            zeilen.append(f"- **{probe.model_id}** — {befund}{dauer}")
        elif ist_voruebergehend(probe, schluessel_gueltig=schluessel_gueltig):
            grund = kurzgrund(probe, sprache)
            zeilen.append(t(sprache, "bericht_gestoert", modell=probe.model_id, grund=grund))
        else:
            grund = kurzgrund(probe, sprache)
            zeilen.append(t(sprache, "bericht_tot", modell=probe.model_id, grund=grund))
    return zeilen


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
    "FEHLERART_AUTH",
    "FEHLERART_KEIN_ABO",
    "FEHLERART_LIMIT",
    "FEHLERART_UNBEKANNT",
    "FEHLERART_UNERREICHBAR",
    "TOT_NACHPRUEFEN_S",
    "CheckResult",
    "ModelProbe",
    "ProviderProbe",
    "bericht_zeilen",
    "describe_changes",
    "faellige_modelle",
    "gather_probes",
    "ist_voruebergehend",
    "kurzgrund",
    "merge_into_registry",
    "merge_overrides",
    "ohne_geratene_anfragelimits",
    "probe_model",
    "probe_provider",
    "quick_key_check",
    "should_discard",
    "uebernehmen",
]
