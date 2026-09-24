"""Faehigkeitsmessung im Hintergrund.

Frueher lief die Messung im Einrichtungsassistenten, als Fortschrittsbalken:
ein bis zwei Minuten je Anbieter, in denen der Dialog nicht geschlossen werden
durfte, und gespeichert wurde erst ganz am Ende. Live am 17.09.2026: vier
Anbieter vermessen, Dialog vor dem letzten Klick geschlossen, nichts
gespeichert. Seitdem speichert der Assistent nach dem Schluesseltest sofort,
und gemessen wird hier.

Was gemessen wird, entscheidet :func:`capabilities.faellige_modelle`: alles
nie Gemessene, alles bisher nur voruebergehend Gestoerte, und was seit einer
Woche als tot gilt. Was lebt, bleibt unangetastet — ob es noch antwortet,
zeigt der laufende Betrieb ohne einen Aufruf aus dem Kontingent.

Wann: zehn Sekunden nach jedem Laden der Integration, danach alle sechs
Stunden. Ein neuer Anbieter oder ein neuer Schluessel laedt die Integration
neu und wird damit sofort vermessen. Ein Durchlauf, in dem nichts faellig
ist, macht keinen einzigen Aufruf.

Das Ergebnis landet im Subentry des Anbieters. Der Update-Listener in
``__init__`` uebernimmt es ohne Neuladen, solange sich an Anbietern und
Schluesseln nichts geaendert hat — sonst braeche jeder gespeicherte Anbieter
die noch laufende Messung des naechsten ab.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .capabilities import (
    ProviderProbe,
    bericht_zeilen,
    describe_changes,
    faellige_modelle,
    probe_provider,
    uebernehmen,
)
from .const import CONF_API_KEY, CONF_MODELS, DOMAIN
from .registry import Provider
from .router import abdeckung_text, coverage

if TYPE_CHECKING:
    from . import FreeAIRouterConfigEntry

_LOGGER = logging.getLogger(__name__)

#: Takt fuer offene und lange tote Modelle. Kostet nur, wenn etwas faellig
#: ist, und dann je Modell einen Aufruf, solange es nicht antwortet.
MESS_TAKT = timedelta(hours=6)

#: Abstand nach dem Laden. Gibt dem Start von Home Assistant etwas Luft und
#: laesst einen gerade gespeicherten Subentry erst zu Ende laden.
START_VERZOEGERUNG_S = 10.0


@callback
def async_starten(hass: HomeAssistant, entry: FreeAIRouterConfigEntry) -> CALLBACK_TYPE:
    """Ersten Lauf kurz verzoegert anstossen, danach im Takt. Gibt den Abmelder zurueck."""

    async def _erster_lauf() -> None:
        await asyncio.sleep(START_VERZOEGERUNG_S)
        await async_faellige_messen(hass, entry)

    @callback
    def _takt(_jetzt: Any = None) -> None:
        entry.async_create_background_task(
            hass, async_faellige_messen(hass, entry), f"{DOMAIN} Messung"
        )

    entry.async_create_background_task(hass, _erster_lauf(), f"{DOMAIN} erste Messung")
    return async_track_time_interval(hass, _takt, MESS_TAKT)


async def async_faellige_messen(hass: HomeAssistant, entry: FreeAIRouterConfigEntry) -> None:
    """Alles Faellige messen — Anbieter parallel, innerhalb eines Anbieters nacheinander."""
    from . import configured_providers  # lokal: sonst Zirkelimport

    runtime = entry.runtime_data
    if runtime.messung.locked():
        _LOGGER.debug("Messung laeuft schon, dieser Durchlauf entfaellt")
        return

    async with runtime.messung:
        jetzt = time.time()
        auftraege: list[tuple[Provider, str, list[Any]]] = []
        for provider_id, daten in configured_providers(entry).items():
            provider = runtime.registry.get(provider_id)
            api_key = str(daten.get(CONF_API_KEY) or "")
            if provider is None or not api_key:
                continue
            modelle = faellige_modelle(provider, daten.get(CONF_MODELS) or {}, jetzt)
            if modelle:
                auftraege.append((provider, api_key, modelle))

        if not auftraege:
            return

        _LOGGER.info(
            "Messe im Hintergrund: %s",
            ", ".join(f"{provider.name} ({len(modelle)})" for provider, _, modelle in auftraege),
        )
        session = async_get_clientsession(hass)
        ergebnisse = await asyncio.gather(
            *(
                # Ein Modell nach dem anderen: bei 5 bis 15 Anfragen je Minute
                # misst man sonst den eigenen Ansturm statt der Faehigkeiten.
                probe_provider(session, provider, api_key, models=modelle, concurrency=1)
                for provider, api_key, modelle in auftraege
            ),
            return_exceptions=True,
        )

        for (provider, _key, _modelle), ergebnis in zip(auftraege, ergebnisse, strict=True):
            if isinstance(ergebnis, BaseException):
                _LOGGER.warning("Messung von %s gescheitert: %r", provider.name, ergebnis)
                continue
            async_speichern(hass, entry, provider, ergebnis, benachrichtigen=True)


@callback
def async_speichern(
    hass: HomeAssistant,
    entry: FreeAIRouterConfigEntry,
    provider: Provider,
    probe: ProviderProbe,
    *,
    benachrichtigen: bool,
) -> list[str] | None:
    """Messergebnis einrechnen und speichern. Gibt die Aenderungen zurueck.

    ``None``, wenn es fuer den Anbieter keinen Subentry (mehr) gibt — etwa
    weil er entfernt wurde, waehrend die Messung lief.
    """
    from . import subentry_of  # lokal: sonst Zirkelimport

    subentry = subentry_of(entry, provider.id)
    if subentry is None:
        return None

    vorher: dict[str, Any] = dict(subentry.data.get(CONF_MODELS) or {})
    nachher = uebernehmen(vorher, probe.models)
    aenderungen = describe_changes(vorher, nachher)

    if nachher != vorher:
        hass.config_entries.async_update_subentry(
            entry, subentry, data={**subentry.data, CONF_MODELS: nachher}
        )

    # Gemeldet wird, was sich geaendert hat, und die allererste Messung eines
    # Anbieters. Nicht gemeldet wird ein Modell, das weiter nicht antwortet —
    # sonst kaeme dieselbe Nachricht alle sechs Stunden wieder.
    if benachrichtigen and (aenderungen or (not vorher and nachher)):
        _async_melden(hass, entry, provider, probe, nachher)
    return aenderungen


@callback
def _async_melden(
    hass: HomeAssistant,
    entry: FreeAIRouterConfigEntry,
    provider: Provider,
    probe: ProviderProbe,
    gespeichert: dict[str, Any],
) -> None:
    """Eine Benachrichtigung je Anbieter; eine neue ersetzt die alte."""
    from . import build_channels, configured_providers  # lokal: sonst Zirkelimport

    runtime = entry.runtime_data
    erreichbar = offen = 0
    for model in provider.models:
        eintrag = gespeichert.get(model.key) or gespeichert.get(model.id)
        if not eintrag:
            offen += 1
        elif eintrag.get("alive", True):
            erreichbar += 1

    kopf = f"**{provider.name}**: {erreichbar} von {len(provider.models)} Modellen erreichbar"
    if offen:
        kopf += f", {offen} noch offen"

    # Die Kanaele direkt aus dem gespeicherten Stand bauen: der Update-Listener,
    # der runtime.channels nachzieht, laeuft erst nach diesem Aufruf.
    kanaele = build_channels(runtime.registry, configured_providers(entry))
    uebersicht = abdeckung_text(coverage(kanaele, runtime.ledger.availability))

    text = "\n\n".join(
        [
            kopf,
            "\n".join(bericht_zeilen(probe.models)),
            "So werden die Profile jetzt bedient:",
            uebersicht,
        ]
    )
    persistent_notification.async_create(
        hass,
        text,
        title=f"Free AI Router: {provider.name} vermessen",
        notification_id=f"{DOMAIN}_messung_{provider.id}",
    )


__all__ = [
    "MESS_TAKT",
    "async_faellige_messen",
    "async_speichern",
    "async_starten",
]
