"""Dienste der Integration.

Bisher genau einer: ``free_ai_router.neu_vermessen``.

**Wozu, wenn doch im Hintergrund gemessen wird.** Die Hintergrundmessung
(``hintergrund.py``) misst nur, was offen ist: neue Modelle, bisher nur
voruebergehend gestoerte, lange tote. Was einmal funktioniert hat, fasst sie
nicht mehr an — Faehigkeiten sind Eigenschaften des Modells, und jede
Messung kostet Kontingent. Dieser Dienst misst auf Zuruf alles neu, auch das
Funktionierende; noetig etwa, wenn sich das Messverfahren verbessert hat.

**Dieselbe Vorsicht wie im Hintergrund.** Gespeichert wird ueber
:func:`hintergrund.async_speichern`, also mit derselben Regel: was nur
voruebergehend gestoert war (Netz, 5xx, Ratenlimit), ueberschreibt nichts.
Eine kaputte Leitung schaltet damit keinen Kanal ab. Beim sparsamen Lauf
(``nur_lebendigkeit``) bleiben die bisherigen Faehigkeiten stehen — er prueft
sie gar nicht, und "nicht gemessen" ist nicht "kann es nicht".
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

from .capabilities import ALL_CHECKS, CHEAP_CHECKS, bericht_zeilen, probe_provider
from .const import CONF_API_KEY, DOMAIN
from .hintergrund import async_speichern

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
    # Die wirksame Registry, also mit Feed: auch Modelle, die nur der Feed
    # kennt, sind Kanaele und gehoeren gemessen.
    registry = runtime.registry

    from . import configured_providers

    configured: dict[str, Any] = configured_providers(entry)
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
    etwas_geaendert = False

    async with runtime.messung:
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
            aenderungen = async_speichern(hass, entry, provider, probe, benachrichtigen=False)
            if aenderungen is None:
                bericht[provider_id] = {"hinweis": "kein Subentry gefunden"}
                continue
            etwas_geaendert = etwas_geaendert or bool(aenderungen)

            bericht[provider_id] = {
                "gemessen": len(probe.models),
                "lebendig": len(probe.working_models),
                "modelle": bericht_zeilen(probe.models),
                "aenderungen": aenderungen or None,
            }
            if probe.stale_registry_entries:
                bericht[provider_id]["nicht_mehr_gefuehrt"] = probe.stale_registry_entries

    ergebnis: ServiceResponse = {
        "anbieter": bericht,
        "dauer_s": round(time.monotonic() - begonnen, 1),
        "umfang": "nur Lebendigkeit" if nur_lebendigkeit else "alle Pruefungen",
    }

    if not etwas_geaendert:
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
