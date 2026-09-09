"""Gemeinsame Basis der Entities: ein Geraet, ein Blick auf die Laufzeit."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from . import FreeAIRouterConfigEntry, RouterRuntime
from .const import DOMAIN

MANUFACTURER = "Free AI Router"


class RouterEntity(Entity):
    """Entity-Basis mit Zugriff auf Registry, Ledger und Router."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: FreeAIRouterConfigEntry) -> None:
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=MANUFACTURER,
            manufacturer=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
        )
        self._last_channel: str | None = None
        self._last_reserve_used: bool = False

    @property
    def runtime(self) -> RouterRuntime:
        return self._entry.runtime_data

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Sichtbar machen, welcher Kanal zuletzt geliefert hat.

        Ohne das bleibt jeder Wechsel auf die Reserve unsichtbar, solange
        niemand ins Log schaut — und dann faellt ein stiller Dauerausfall des
        Erstkanals erst auf, wenn auch die Reserve weg ist.
        """
        attributes: dict[str, Any] = {
            "zuletzt_genutzter_kanal": self._last_channel,
            "reserve_gegriffen": self._last_reserve_used,
        }
        attributes.update(self.runtime.diagnostics())
        return attributes

    def _note_channel(self, key: str, used_reserve: bool) -> None:
        self._last_channel = key
        self._last_reserve_used = used_reserve
