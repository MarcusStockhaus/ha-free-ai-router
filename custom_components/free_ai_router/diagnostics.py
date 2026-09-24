"""Diagnose-Export — der erste Schritt jeder Fehlersuche, ohne Rueckfrage.

Home Assistant zeigt "Diagnose herunterladen" auf der Integrationsseite,
sobald dieses Modul existiert. Ohne es ist der erste Satz in jedem Issue
"welche Anbieter hast du eingerichtet, was steht im Log" — mit ihm steht das
schon in der Datei, die der Nutzer mitschickt.

Schluessel muessen draussen bleiben. Der Config Entry selbst haelt seit dem
Subentry-Umbau keine Anbieterdaten mehr (``configured_providers`` liest die
Subentries), aber genau dort steht ``api_key`` weiterhin im Klartext — also
wird ueber jeden Anbieter-Eintrag der HA-eigene Redact-Helfer gefahren statt
sich auf "gibt's hier eh nicht" zu verlassen.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import FreeAIRouterConfigEntry, configured_providers
from .const import CONF_API_KEY

#: Alles, was nach einem Zugangsdatum aussieht. Absichtlich nur der Schluessel
#: selbst — Anbieter-ID, Modellnamen und Messwerte sind harmlos und genau das,
#: was eine Fehlersuche braucht.
_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FreeAIRouterConfigEntry
) -> dict[str, Any]:
    """Alles, was eine Fehlersuche braucht — nichts, was ein Schluessel ist."""
    del hass  # Signatur von HA vorgegeben, hier ungenutzt
    runtime = entry.runtime_data
    eingerichtet = configured_providers(entry)

    return {
        "config_entry_version": entry.version,
        "providers": {
            provider_id: async_redact_data(daten, _REDACT)
            for provider_id, daten in eingerichtet.items()
        },
        "runtime": runtime.diagnostics(),
        "ledger": runtime.ledger.snapshot(),
    }
