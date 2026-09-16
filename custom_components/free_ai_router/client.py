"""Ausfuehrung mit Reserve.

Der Router liefert die Rangfolge, dieses Modul arbeitet sie ab: einreihen,
wenn nur das Minutenfenster zu ist; wechseln, wenn ein Kanal ausfaellt; und
jeden Wechsel ins Log schreiben, damit ein simulierter Ausfall nachvollziehbar
ist, ohne dass die Automation stehenbleibt.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .adapters import (
    ChatRequest,
    ChatResponse,
    ImageAttachment,
    ProviderError,
    ToolSpec,
    get_adapter,
)
from .adapters.base import Message
from .const import REQUEST_TIMEOUT_S
from .ledger import Ledger, cost_usd
from .router import Candidate, Channel, Requirements, RoutingPlan, plan

_LOGGER = logging.getLogger(__name__)

#: So lange darf sich eine Anfrage hoechstens einreihen, bevor der naechste
#: Kanal drankommt. Laenger zu warten hilft niemandem: die Automation, die das
#: ausgeloest hat, ist dann ohnehin nicht mehr aktuell.
DEFAULT_MAX_QUEUE_WAIT_S = 20.0

#: Was fuer die Vorbuchung als Ausgabe veranschlagt wird. Nicht
#: ``max_output_tokens`` — das ist eine Obergrenze, keine Erwartung. Gemessen
#: liefert eine strukturierte Kameraanalyse rund 60 Ausgabe-Token; mit dem
#: Limit von 2048 zu buchen wuerde das Minutenfenster zweieinhalbmal zu
#: schnell fuellen und unnoetig bremsen. Der tatsaechliche Verbrauch wird
#: nach der Antwort nachgetragen.
ERWARTETE_AUSGABE_TOKEN = 256


def _kurz(err: Exception, laenge: int = 160) -> str:
    """Fehlertext auf Logzeilen-Laenge bringen.

    Anbieter antworten mit mehrzeiligem JSON; ungekuerzt macht eine einzige
    Reserve-Meldung das Log unlesbar.
    """
    text = " ".join(str(err).split())
    return text if len(text) <= laenge else text[: laenge - 1] + "…"


class NoChannelAvailable(RuntimeError):
    """Kein Kanal konnte die Anfrage uebernehmen.

    Traegt die Begruendung je Kanal mit, damit der Aufrufer eine Meldung
    formulieren kann, die dem Nutzer sagt, was zu tun ist.
    """

    def __init__(self, message: str, *, attempts: list[str]) -> None:
        super().__init__(message)
        self.attempts = attempts

    def report(self) -> str:
        lines = "; ".join(self.attempts) if self.attempts else "keine Kanäle eingerichtet"
        return f"{self.args[0]} — {lines}"


@dataclass(slots=True)
class Execution:
    """Ergebnis eines Aufrufs samt Weg dorthin."""

    response: ChatResponse
    candidate: Candidate
    attempts: list[str] = field(default_factory=list)
    waited_s: float = 0.0

    @property
    def used_reserve(self) -> bool:
        return len(self.attempts) > 1


@dataclass
class RouterClient:
    """Fuehrt Anfragen ueber die geordnete Kandidatenliste aus."""

    session: aiohttp.ClientSession
    ledger: Ledger
    keys: dict[str, str]
    max_queue_wait_s: float = DEFAULT_MAX_QUEUE_WAIT_S
    request_timeout_s: float = REQUEST_TIMEOUT_S

    async def run(
        self,
        requirements: Requirements,
        channels: list[Channel],
        *,
        instructions: str = "",
        system: str | None = None,
        messages: tuple[Message, ...] = (),
        json_schema: dict[str, Any] | None = None,
        images: tuple[ImageAttachment, ...] = (),
        tools: tuple[ToolSpec, ...] = (),
        max_output_tokens: int = 2048,
        thinking_budget: int | None = None,
    ) -> Execution:
        """Arbeite die Rangfolge ab, bis einer liefert.

        Entweder ``instructions`` (ein ``ai_task``-Aufruf) oder ``messages``
        (ein Assist-Gespraech mit Werkzeugrunden).
        """
        routing: RoutingPlan = plan(requirements, channels, self.ledger.availability)
        _LOGGER.debug("Routing %s", routing.explain())

        attempts: list[str] = []
        if not routing.has_candidate:
            raise NoChannelAvailable(
                f"Kein Kanal kann Profil {requirements.profile!r} bedienen",
                attempts=[str(item) for item in routing.rejected],
            )

        waited_total = 0.0
        for candidate in routing.candidates:
            provider = candidate.provider
            model = candidate.model
            api_key = self.keys.get(provider.id)
            if not api_key:
                attempts.append(f"{candidate.key}: kein Schlüssel hinterlegt")
                continue

            availability = candidate.availability
            if not availability.ok:
                if not availability.queueable:
                    attempts.append(f"{candidate.key}: {availability.reason}")
                    _LOGGER.debug("%s uebersprungen: %s", candidate.key, availability.reason)
                    continue
                # Nur das Minutenfenster ist zu — einreihen statt aufgeben.
                loop_start = asyncio.get_running_loop().time()
                availability = await self.ledger.wait_for_slot(
                    provider, model, max_wait_s=self.max_queue_wait_s
                )
                waited = asyncio.get_running_loop().time() - loop_start
                waited_total += waited
                if not availability.ok:
                    attempts.append(
                        f"{candidate.key}: nach {waited:.0f} s immer noch {availability.reason}"
                    )
                    _LOGGER.info(
                        "%s: Warteschlange ohne Erfolg (%s), naechster Kanal",
                        candidate.key,
                        availability.reason,
                    )
                    continue
                _LOGGER.info("%s: %.0f s eingereiht, jetzt frei", candidate.key, waited)

            adapter = get_adapter(provider.api_style)
            request = ChatRequest(
                model=model.id,
                instructions=instructions,
                system=system,
                messages=messages,
                images=images,
                json_schema=json_schema,
                tools=tools,
                max_output_tokens=max_output_tokens,
                thinking_budget=thinking_budget,
            )

            # Schaetzung vorbuchen: bei engen Tokenfenstern soll der naechste
            # Aufruf bremsen, bevor der Anbieter 429 sagt. Beim Ausgabendeckel
            # gilt dasselbe eine Stufe strenger — gegen ein aufgebrauchtes
            # Monatsbudget hilft kein Warten.
            geschaetzte_ausgabe = min(max_output_tokens, ERWARTETE_AUSGABE_TOKEN)
            geschaetzt = requirements.approx_input_tokens + geschaetzte_ausgabe
            geschaetzte_kosten = cost_usd(
                model, requirements.approx_input_tokens, geschaetzte_ausgabe
            )
            self.ledger.record_request(
                provider, model, tokens=geschaetzt, cost=geschaetzte_kosten
            )
            try:
                response = await adapter.chat(
                    self.session, provider, api_key, request, timeout=self.request_timeout_s
                )
            except ProviderError as err:
                self._note_failure(candidate, err, attempts)
                continue
            except TimeoutError:
                self.ledger.record_failure(provider, model, reason="Zeitüberschreitung")
                attempts.append(f"{candidate.key}: Zeitüberschreitung")
                _LOGGER.warning("%s: Zeitueberschreitung, wechsle auf Reserve", candidate.key)
                continue
            except aiohttp.ClientError as err:
                self.ledger.record_failure(provider, model, reason=f"Netzfehler: {err!r}")
                attempts.append(f"{candidate.key}: Netzfehler {err!r}")
                _LOGGER.warning("%s: Netzfehler %r, wechsle auf Reserve", candidate.key, err)
                continue

            self.ledger.record_success(
                provider,
                model,
                tokens=(response.input_tokens or 0) + (response.output_tokens or 0),
                estimated_tokens=geschaetzt,
                cost=cost_usd(model, response.input_tokens or 0, response.output_tokens or 0),
                estimated_cost=geschaetzte_kosten,
                info=response.rate_limit,
            )
            attempts.append(f"{candidate.key}: ok")
            if len(attempts) > 1:
                self.ledger.note_fallback()
                _LOGGER.info(
                    "Reserve gegriffen: %s hat geliefert, vorher %s",
                    candidate.key,
                    "; ".join(attempts[:-1]),
                )
            await self.ledger.async_save()
            return Execution(
                response=response,
                candidate=candidate,
                attempts=attempts,
                waited_s=waited_total,
            )

        self.ledger.note_discarded()
        await self.ledger.async_save()
        raise NoChannelAvailable(
            f"Alle Kanäle für Profil {requirements.profile!r} ausgefallen oder am Limit",
            attempts=attempts,
        )

    def _note_failure(
        self, candidate: Candidate, err: ProviderError, attempts: list[str]
    ) -> None:
        provider, model = candidate.provider, candidate.model
        if err.is_rate_limit:
            self.ledger.record_rate_limited(
                provider, model, retry_after_s=err.rate_limit.retry_after_s
            )
            attempts.append(f"{candidate.key}: Limit erreicht")
            _LOGGER.info("%s: Limit erreicht, wechsle auf Reserve", candidate.key)
            return
        if err.is_auth:
            self.ledger.record_failure(
                provider, model, fatal=True, auth=True, reason="Schlüssel abgelehnt"
            )
            attempts.append(f"{candidate.key}: Schlüssel abgelehnt")
            _LOGGER.warning(
                "%s: Schluessel abgelehnt (%s), wechsle auf Reserve",
                candidate.key,
                _kurz(err),
            )
            return
        self.ledger.record_failure(provider, model, reason=_kurz(err))
        attempts.append(f"{candidate.key}: {_kurz(err)}")
        _LOGGER.warning("%s: %s, wechsle auf Reserve", candidate.key, _kurz(err))
