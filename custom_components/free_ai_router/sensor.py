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
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FreeAIRouterConfigEntry, RouterRuntime
from .entity import RouterEntity
from .ledger import bucket_key

_LOGGER = logging.getLogger(__name__)

#: Der Ledger liegt im Speicher; oefter als halbminuetlich nachzusehen bringt
#: nichts und laesst nur die Datenbank wachsen.
SCAN_INTERVAL = timedelta(seconds=30)


@dataclass(frozen=True, kw_only=True)
class RouterSensorDescription(SensorEntityDescription):
    """Ein Tageszaehler und wie er aus der Laufzeit zu holen ist."""

    wert: Callable[[RouterRuntime], int]


GESAMT_SENSOREN: tuple[RouterSensorDescription, ...] = (
    RouterSensorDescription(
        key="anfragen_heute",
        translation_key="anfragen_heute",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement="Anfragen",
        wert=lambda runtime: runtime.ledger.stats.requests,
    ),
    RouterSensorDescription(
        key="token_heute",
        translation_key="token_heute",
        icon="mdi:cash-multiple",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement="Token",
        wert=lambda runtime: runtime.ledger.stats.tokens,
    ),
    RouterSensorDescription(
        key="reserve_heute",
        translation_key="reserve_heute",
        icon="mdi:backup-restore",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement="Wechsel",
        entity_category=EntityCategory.DIAGNOSTIC,
        wert=lambda runtime: runtime.ledger.stats.fallbacks,
    ),
    RouterSensorDescription(
        key="verworfen_heute",
        translation_key="verworfen_heute",
        icon="mdi:close-octagon-outline",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement="Anfragen",
        entity_category=EntityCategory.DIAGNOSTIC,
        wert=lambda runtime: runtime.ledger.stats.discarded,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FreeAIRouterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    entities: list[SensorEntity] = [
        RouterGesamtSensor(entry, beschreibung) for beschreibung in GESAMT_SENSOREN
    ]
    entities.extend(
        RouterAnbieterSensor(entry, provider_id)
        for provider_id in sorted({channel.provider.id for channel in runtime.channels})
    )
    async_add_entities(entities)


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
    _attr_native_unit_of_measurement = "Anfragen"
    _attr_icon = "mdi:api"

    def __init__(self, entry: FreeAIRouterConfigEntry, provider_id: str) -> None:
        RouterEntity.__init__(self, entry)
        self._provider_id = provider_id
        self._attr_unique_id = f"{entry.entry_id}_{provider_id}_anfragen"
        self._attr_translation_key = "anbieter_anfragen"
        self._attr_translation_placeholders = {"anbieter": provider_id}

    def _kanaele(self) -> list[Any]:
        return [c for c in self.runtime.channels if c.provider.id == self._provider_id]

    @property
    def name(self) -> str:
        kanaele = self._kanaele()
        anbieter = kanaele[0].provider.name if kanaele else self._provider_id
        return f"{anbieter} Anfragen heute"

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
