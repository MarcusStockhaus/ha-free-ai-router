"""Dienste der Integration.

Bisher genau einer: ``free_ai_router.neu_vermessen``.

**Warum es ihn braucht.** Die Faehigkeiten, mit denen der Router arbeitet,
stammen aus dem Augenblick des Einrichtens und liegen im Config Entry. Sie
schlagen den Feed — eigene Messung vor fremder, und das ist richtig, weil
``alive`` und Limits vom Konto abhaengen und nicht vom Modell. Die Kehrseite:
ohne diesen Dienst ist ein einmal gemessener Wert unerreichbar. Ein
verbessertes Messverfahren erreichte den Nutzer nie, und ein Anbieter, den der
Feed gar nicht kennt, bliebe fuer immer auf dem Stand des ersten Tages.

**Dieselbe Vorsicht wie im Prober.** Eine Messung, die nichts erreicht hat,
ueberschreibt nichts:

* Antwortet **kein einziges** Modell eines Anbieters, der vorher welche hatte,
  wird das Ergebnis verworfen. Das ist fast immer die eigene Leitung und nicht
  das Ende des Anbieters — und eine kaputte Leitung darf nicht dazu fuehren,
  dass sich die Installation selbst die Kanaele abschaltet.
* Wird der Schluessel abgelehnt, gilt dasselbe.
* Beim sparsamen Lauf (``nur_lebendigkeit``) bleiben die bisherigen
  Faehigkeiten stehen. Er prueft sie gar nicht — sie zu loeschen, weil nicht
  danach gefragt wurde, waere der Unterschied zwischen "nein" und "nicht
  gemessen", auf den es in diesem Projekt durchgehend ankommt.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .capabilities import (
    ALL_CHECKS,
    CHEAP_CHECKS,
    describe_changes,
    merge_into_registry,
    merge_overrides,
    probe_provider,
    should_discard,
)
from .const import CONF_API_KEY, CONF_MODELS, DOMAIN

if TYPE_CHECKING:
    from . import FreeAIRouterConfigEntry

_LOGGER = logging.getLogger(__name__)

SERVICE_NEU_VERMESSEN = "neu_vermessen"
ATTR_ANBIETER = "anbieter"
ATTR_NUR_LEBENDIGKEIT = "nur_lebendigkeit"

SCHEMA_NEU_VERMESSEN = vol.Schema(
    {
        vol.Optional(ATTR_ANBIETER): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_NUR_LEBENDIGKEIT, default=False): cv.boolean,
    }
)

#: Gleichzeitige Aufrufe je Anbieter. Klein, aus demselben Grund wie im
#: Probe-CLI: bei 5 bis 15 Anfragen je Minute misst man sonst den eigenen
#: Ansturm statt der Faehigkeiten.
CONCURRENCY = 2


async def _async_neu_vermessen(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]
    if not entries:
        raise HomeAssistantError(
            "Free AI Router ist nicht geladen — nichts zu vermessen."
        )
    entry: FreeAIRouterConfigEntry = entries[0]
    runtime = entry.runtime_data
    registry = runtime.base_registry or runtime.registry

    configured: dict[str, Any] = dict(entry.data.get("providers") or {})
    gewuenscht = call.data.get(ATTR_ANBIETER)
    if gewuenscht:
        unbekannt = sorted(set(gewuenscht) - set(configured))
        if unbekannt:
            raise ServiceValidationError(
                f"Nicht eingerichtet: {', '.join(unbekannt)}. "
                f"Eingerichtet sind: {', '.join(sorted(configured)) or 'keiner'}"
            )
        ziele = [pid for pid in configured if pid in gewuenscht]
    else:
        ziele = list(configured)

    nur_lebendigkeit = bool(call.data.get(ATTR_NUR_LEBENDIGKEIT))
    checks = CHEAP_CHECKS if nur_lebendigkeit else ALL_CHECKS
    session = async_get_clientsession(hass)

    begonnen = time.monotonic()
    bericht: dict[str, Any] = {}
    neue_daten = {pid: dict(daten) for pid, daten in configured.items()}
    etwas_geaendert = False

    for provider_id in ziele:
        provider = registry.get(provider_id)
        if provider is None:
            bericht[provider_id] = {"hinweis": "steht nicht mehr in der Registry"}
            continue
        api_key = configured[provider_id].get(CONF_API_KEY) or ""
        if not api_key:
            bericht[provider_id] = {"hinweis": "kein Schluessel hinterlegt"}
            continue

        _LOGGER.info(
            "Vermesse %s neu (%s)", provider.name, "sparsam" if nur_lebendigkeit else "voll"
        )
        probe = await probe_provider(
            session, provider, api_key, checks=checks, concurrency=CONCURRENCY
        )

        vorher: dict[str, Any] = configured[provider_id].get(CONF_MODELS) or {}
        lebendig = len(probe.working_models)

        if should_discard(vorher, lebendig):
            bericht[provider_id] = {
                "gemessen": len(probe.models),
                "lebendig": 0,
                "hinweis": (
                    "kein Modell hat geantwortet — Ergebnis verworfen, "
                    f"bisheriger Stand bleibt ({probe.error or 'ohne Fehlermeldung'})"
                ),
            }
            continue

        gemessen = merge_into_registry(provider, probe.models)
        nachher = {
            key: merge_overrides(vorher.get(key) or {}, wert) for key, wert in gemessen.items()
        }
        aenderungen = describe_changes(vorher, nachher)
        neue_daten[provider_id] = {**configured[provider_id], CONF_MODELS: nachher}
        etwas_geaendert = True

        bericht[provider_id] = {
            "gemessen": len(probe.models),
            "lebendig": lebendig,
            "aenderungen": aenderungen or None,
        }
        if probe.stale_registry_entries:
            bericht[provider_id]["nicht_mehr_gefuehrt"] = probe.stale_registry_entries

    ergebnis: ServiceResponse = {
        "anbieter": bericht,
        "dauer_s": round(time.monotonic() - begonnen, 1),
        "umfang": "nur Lebendigkeit" if nur_lebendigkeit else "alle Pruefungen",
    }

    if etwas_geaendert:
        # Der Update-Listener laedt die Entry neu; dabei entstehen die Kanaele
        # aus den frischen Werten.
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "providers": neue_daten}
        )
    else:
        _LOGGER.info("Neu vermessen: nichts uebernommen")

    return ergebnis


def async_setup_services(hass: HomeAssistant) -> None:
    """Dienste anmelden. Mehrfaches Anmelden ist unschaedlich."""

    async def handle(call: ServiceCall) -> ServiceResponse:
        return await _async_neu_vermessen(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_NEU_VERMESSEN,
        handle,
        schema=SCHEMA_NEU_VERMESSEN,
        supports_response=SupportsResponse.OPTIONAL,
    )


def async_unload_services(hass: HomeAssistant) -> None:
    hass.services.async_remove(DOMAIN, SERVICE_NEU_VERMESSEN)


__all__ = [
    "SERVICE_NEU_VERMESSEN",
    "async_setup_services",
    "async_unload_services",
]
