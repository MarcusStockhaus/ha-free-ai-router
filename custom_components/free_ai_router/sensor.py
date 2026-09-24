"""Verbrauchssensoren fuers Dashboard.

Warum das mehr als Kosmetik ist: eine Bildanalyse kostet gemessen rund 1.100
Token, nicht die 258, mit denen die Kachelrechnung des Konzepts operiert. Bei
Groqs 8.000 Token je Minute sind das sechs Anfragen statt siebenundzwanzig.
Wer das nicht sieht, merkt erst am 429, dass seine Automation zu oft ausloest.

Drei Sensoren, jeder beantwortet eine eigene Frage:

* **Anfragen heute** — wie viel laeuft ueberhaupt?
* **Reserve heute** — greift der Erstkanal noch? Ein still dauerhaft
  ausgefallener Erstkanal faellt sonst erst auf, wenn auch die Reserve weg ist.
* **Verworfen heute** — hat eine Automation ins Leere gegriffen?

Dazu je Anbieter einer mit den Zaehlerstaenden seiner Toepfe.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FreeAIRouterConfigEntry, RouterRuntime
from .const import DOMAIN
from .entity import MANUFACTURER, RouterEntity
from .ledger import bucket_key
from .sprache import t

_LOGGER = logging.getLogger(__name__)

#: Der Ledger liegt im Speicher; oefter als halbminuetlich nachzusehen bringt
#: nichts und laesst nur die Datenbank wachsen.
SCAN_INTERVAL = timedelta(seconds=30)


@dataclass(frozen=True, kw_only=True)
class RouterSensorDescription(SensorEntityDescription):
    """Ein Tageszaehler und wie er aus der Laufzeit zu holen ist.

    Die Einheit folgt der Systemsprache und wird beim Anlegen gesetzt. Home
    Assistants eigener Weg (``unit_of_measurement`` in den Uebersetzungen)
    nimmt absichtlich immer die englische Fassung, damit sich Statistiken
    beim Sprachwechsel nicht aendern — auf einem deutschen System stuende dann
    "requests" neben "Anfragen heute". Live am 24.09.2026 so gesehen.
    """

    wert: Callable[[RouterRuntime], int]
    einheit: str
    """Schluessel in ``sprache.TEXTE``."""
    objekt_id: str
    """Feste Entity-ID, unabhaengig von der Systemsprache (siehe ai_task.OBJEKT_IDS)."""


GESAMT_SENSOREN: tuple[RouterSensorDescription, ...] = (
    RouterSensorDescription(
        key="anfragen_heute",
        translation_key="anfragen_heute",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        objekt_id="anfragen_heute",
        einheit="einheit_anfragen",
        wert=lambda runtime: runtime.ledger.stats.requests,
    ),
    RouterSensorDescription(
        key="token_heute",
        translation_key="token_heute",
        icon="mdi:cash-multiple",
        state_class=SensorStateClass.TOTAL_INCREASING,
        objekt_id="token_heute",
        einheit="einheit_token",
        wert=lambda runtime: runtime.ledger.stats.tokens,
    ),
    RouterSensorDescription(
        key="reserve_heute",
        translation_key="reserve_heute",
        icon="mdi:backup-restore",
        state_class=SensorStateClass.TOTAL_INCREASING,
        objekt_id="reserve_gegriffen_heute",
        einheit="einheit_wechsel",
        # Bewusst *nicht* diagnostisch: das ist laut README "der Sensor, auf
        # den es ankommt" — ein still dauerhaft ausgefallener Erstkanal faellt
        # sonst nicht auf. In der Diagnose-Kategorie fehlt er auf jedem
        # automatisch erzeugten Dashboard, genau dort, wo er hingehoert.
        wert=lambda runtime: runtime.ledger.stats.fallbacks,
    ),
    RouterSensorDescription(
        key="verworfen_heute",
        translation_key="verworfen_heute",
        icon="mdi:close-octagon-outline",
        state_class=SensorStateClass.TOTAL_INCREASING,
        objekt_id="verworfen_heute",
        einheit="einheit_anfragen",
        entity_category=EntityCategory.DIAGNOSTIC,
        wert=lambda runtime: runtime.ledger.stats.discarded,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FreeAIRouterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    from . import subentry_of

    runtime = entry.runtime_data
    async_add_entities(
        RouterGesamtSensor(entry, beschreibung) for beschreibung in GESAMT_SENSOREN
    )

    # Der Anbietersensor gehoert zu seiner Zeile auf der Integrationsseite,
    # nicht zur Integration als Ganzes. Dann steht der Verbrauch dort, wo auch
    # der Schluessel steht — und verschwindet mit, wenn der Anbieter geht.
    anbieter = {channel.provider.id: channel.provider for channel in runtime.channels}
    for provider_id in sorted(anbieter):
        subentry = subentry_of(entry, provider_id)
        async_add_entities(
            [RouterAnbieterSensor(entry, anbieter[provider_id])],
            config_subentry_id=subentry.subentry_id if subentry else None,
        )


class RouterGesamtSensor(RouterEntity, SensorEntity):
    """Ein Tageszaehler ueber alle Anbieter."""

    entity_description: RouterSensorDescription
    _attr_should_poll = True

    def __init__(
        self, entry: FreeAIRouterConfigEntry, beschreibung: RouterSensorDescription
    ) -> None:
        RouterEntity.__init__(self, entry)
        self.entity_description = beschreibung
        self._attr_unique_id = f"{entry.entry_id}_{beschreibung.key}"
        self.entity_id = f"sensor.{DOMAIN}_{beschreibung.objekt_id}"
        self._attr_native_unit_of_measurement = t(entry.runtime_data.sprache, beschreibung.einheit)

    @property
    def native_value(self) -> int:
        return self.entity_description.wert(self.runtime)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        stats = self.runtime.ledger.stats
        return {
            "tag": stats.day_key,
            "anfragen": stats.requests,
            "token": stats.tokens,
            "reserve_gegriffen": stats.fallbacks,
            "verworfen": stats.discarded,
        }


class RouterAnbieterSensor(RouterEntity, SensorEntity):
    """Anfragen eines Anbieters am heutigen Tag — in dessen eigener Zeitzone.

    Bewusst nicht in der lokalen: Google setzt sein Tageskontingent um
    Mitternacht Pacific zurueck. Ein Sensor, der um lokal Mitternacht auf null
    springt, wuerde ueber den Rest luegen.
    """

    _attr_should_poll = True
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:api"

    def __init__(self, entry: FreeAIRouterConfigEntry, provider: Any) -> None:
        RouterEntity.__init__(self, entry)
        self._provider_id = provider.id
        self._attr_unique_id = f"{entry.entry_id}_{provider.id}_anfragen"
        self.entity_id = f"sensor.{provider.id}_anfragen_heute"
        self._attr_native_unit_of_measurement = t(entry.runtime_data.sprache, "einheit_anfragen")
        # Der Anbietername steht schon am Geraet. Ihn hier zu wiederholen
        # ergaebe "Google AI Studio Google AI Studio Anfragen heute" — Home
        # Assistant setzt den Geraetenamen selbst davor.
        self._attr_translation_key = "anbieter_anfragen"
        # Eigenes Geraet je Anbieter. Ein Geraet gehoert zu genau einem
        # Subentry — haengt man die Anbietersensoren an das gemeinsame Geraet
        # der Integration, schiebt Home Assistant es bei jedem Hinzufuegen in
        # eine andere Zeile und warnt zu Recht davor. Der Anbieter ist ohnehin
        # das treffendere Geraet: er hat einen Schluessel, Kontingente und
        # verschwindet als Ganzes.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{provider.id}")},
            name=provider.name,
            manufacturer=MANUFACTURER,
            model=t(entry.runtime_data.sprache, "geraet_modell", anzahl=len(provider.models)),
            entry_type=DeviceEntryType.SERVICE,
            # Kein via_device mehr: es zeigte auf das gemeinsame Geraet der
            # Integration, das es seit dem 12.09.2026 nicht mehr gibt (siehe
            # entity.py). HA meldet den Parameter ausserdem seit 2026.8 als
            # veraltet, ab 2027.8 funktioniert er nicht mehr — live im Log
            # am 24.09.2026.
            # Der Deep-Link auf die Key-Seite, derselbe wie im Assistenten.
            # Dort fuehrt der Weg hin, wenn ein Schluessel erneuert gehoert.
            configuration_url=provider.onboarding.signup_url,
        )

    def _kanaele(self) -> list[Any]:
        return [c for c in self.runtime.channels if c.provider.id == self._provider_id]

    @property
    def native_value(self) -> int:
        ledger = self.runtime.ledger
        # Bei limits_scope per_key teilen sich alle Modelle einen Topf — dann
        # darf er nicht mehrfach gezaehlt werden.
        gezaehlt: set[str] = set()
        summe = 0
        for channel in self._kanaele():
            key = bucket_key(channel.provider, channel.model)
            if key in gezaehlt:
                continue
            gezaehlt.add(key)
            summe += ledger.bucket(key).day_requests
        return summe

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ledger = self.runtime.ledger
        modelle: dict[str, Any] = {}
        gesperrt: list[str] = []
        for channel in self._kanaele():
            zustand = ledger.usage(channel.provider, channel.model)
            grenze = channel.model.limits.rpd
            modelle[channel.model.id] = {
                "anfragen_heute": zustand["day_requests"],
                "tageslimit": grenze,
                "rest": None if grenze is None else max(0, grenze - zustand["day_requests"]),
                "aktiv": channel.enabled,
            }
            if zustand["block_reason"]:
                gesperrt.append(f"{channel.model.id}: {zustand['block_reason']}")

        attribute: dict[str, Any] = {"modelle": modelle, "gesperrt": gesperrt or None}

        # Der Ausgabendeckel gehoert an den Anbieter, nicht an das Modell:
        # alle Modelle eines Kontos teilen sich denselben Betrag.
        kanaele = self._kanaele()
        budget = kanaele[0].provider.monthly_budget_usd if kanaele else None
        if budget:
            ausgegeben = ledger.spend_state(kanaele[0].provider).spent_usd
            attribute["budget_usd"] = budget
            attribute["ausgegeben_usd"] = round(ausgegeben, 4)
            attribute["rest_usd"] = round(max(0.0, budget - ausgegeben), 4)
        return attribute
