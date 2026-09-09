"""Free AI Router — verwalteter Anbieterzugang fuer Home Assistant.

Eine Config-Entry haelt alle eingerichteten Anbieter. Daraus entstehen die
Kanaele (Anbieter + Modell), die der Router sortiert, und je Profil eine
``ai_task``-Entity plus eine ``conversation``-Entity fuer Assist.

**Warum die Home-Assistant-Importe in den Funktionen stehen:** Dieses Paket
wird auch vom eigenstaendigen Probe-CLI benutzt, das ohne Home Assistant
laeuft (``tools/probe_cli.py``). Waeren die HA-Importe hier auf Modulebene,
liesse sich kein einziges Modul des Pakets ohne HA importieren — und damit
waere der gemeinsame Code aus Schritt 0 nicht mehr gemeinsam. Die uebrigen
HA-Module (``config_flow``, ``ai_task``, ``conversation``, ``task_adapter``)
importieren HA ganz normal; sie werden nur von HA geladen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .client import RouterClient
from .const import (
    CONF_API_KEY,
    CONF_MODELS,
    CONF_PROVIDER,
    DOMAIN,
    PROFILES,
    STORAGE_KEY_LEDGER,
    STORAGE_VERSION_LEDGER,
)
from .ledger import Ledger
from .registry import (
    Model,
    Registry,
    RegistryError,
    apply_measured_capabilities,
    apply_measured_limits,
    load_registry,
)
from .router import Channel, CoverageEntry, coverage

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

#: Plattformen als Text, damit ``Platform`` nicht auf Modulebene noetig ist.
PLATFORMS: list[str] = ["ai_task", "conversation"]

#: Ledger nicht bei jeder Anfrage auf die SD-Karte schreiben.
LEDGER_SAVE_DELAY_S = 30


@dataclass
class RouterRuntime:
    """Alles, was die Entities zur Laufzeit brauchen."""

    registry: Registry
    ledger: Ledger
    client: RouterClient
    channels: tuple[Channel, ...] = ()
    store: Store | None = None
    _coverage: dict[str, CoverageEntry] = field(default_factory=dict, repr=False)

    def channels_for(self, profile: str) -> list[Channel]:
        """Kanaele, die dieses Profil laut Registry bedienen — in Rangfolge."""
        return [channel for channel in self.channels if profile in channel.model.profiles]

    def all_channels(self) -> list[Channel]:
        return list(self.channels)

    def coverage(self) -> dict[str, CoverageEntry]:
        return coverage(self.channels, self.ledger.availability)

    def diagnostics(self) -> dict[str, Any]:
        """Ohne Schluessel — landet in Entity-Attributen und im Log."""
        return {
            "kanaele": [channel.key for channel in self.channels if channel.enabled],
            "abgeschaltet": [channel.key for channel in self.channels if not channel.enabled],
            "abdeckung": {
                profile: (entry.primary.key if entry.primary else None)
                for profile, entry in self.coverage().items()
            },
        }


type FreeAIRouterConfigEntry = ConfigEntry[RouterRuntime]


def build_channels(registry: Registry, entry_data: dict[str, Any]) -> tuple[Channel, ...]:
    """Baue die Kanalliste aus Registry und gemessenen Ueberschreibungen.

    Reihenfolge: ``preference`` des Anbieters, dann die Reihenfolge der
    Modelle in der Datei. Das ist die redaktionelle Vorauswahl ("erste Wahl"
    gegen "Reserve"), auf die sich der Router als Rangkriterium stuetzt — und
    sie darf gerade nicht vom Dateinamen abhaengen.
    """
    configured: dict[str, Any] = entry_data.get("providers") or {}
    channels: list[Channel] = []

    providers = sorted(
        (provider for provider in registry if provider.id in configured),
        key=lambda provider: (provider.preference, provider.id),
    )
    for provider in providers:
        measured: dict[str, Any] = configured[provider.id].get(CONF_MODELS) or {}
        for model in provider.models:
            override = measured.get(model.key) or measured.get(model.id) or {}
            adjusted: Model = apply_measured_capabilities(model, override.get("capabilities"))
            adjusted = apply_measured_limits(adjusted, override.get("limits"))
            channels.append(
                Channel(
                    provider=provider,
                    model=adjusted,
                    # Nur was gemessen und fuer tot befunden wurde, wird
                    # abgeschaltet. Ungemessen heisst weiterhin "versuchen".
                    enabled=bool(override.get("alive", True)),
                )
            )

    return tuple(channels)


def api_keys(entry_data: dict[str, Any]) -> dict[str, str]:
    configured: dict[str, Any] = entry_data.get("providers") or {}
    return {
        provider_id: data[CONF_API_KEY]
        for provider_id, data in configured.items()
        if data.get(CONF_API_KEY)
    }


async def async_setup_entry(
    hass: HomeAssistant, entry: FreeAIRouterConfigEntry
) -> bool:
    """Registry laden, Ledger wiederherstellen, Entities anlegen."""
    from homeassistant.const import Platform
    from homeassistant.exceptions import ConfigEntryNotReady
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from homeassistant.helpers.storage import Store

    try:
        registry = await hass.async_add_executor_job(load_registry)
    except RegistryError as err:
        # Eine kaputte Registry ist ein Installationsproblem, kein Netzproblem.
        _LOGGER.error("Registry unbrauchbar: %s", err)
        raise ConfigEntryNotReady(f"Registry unbrauchbar: {err}") from err

    store = Store(hass, STORAGE_VERSION_LEDGER, STORAGE_KEY_LEDGER)
    stored = await store.async_load()

    async def save(data: dict[str, Any]) -> None:
        store.async_delay_save(lambda: data, LEDGER_SAVE_DELAY_S)

    ledger = Ledger(save=save)
    ledger.restore(stored)

    runtime = RouterRuntime(
        registry=registry,
        ledger=ledger,
        client=RouterClient(
            session=async_get_clientsession(hass),
            ledger=ledger,
            keys=api_keys(entry.data),
        ),
        channels=build_channels(registry, entry.data),
        store=store,
    )
    entry.runtime_data = runtime
    _log_coverage(runtime)

    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform(name) for name in PLATFORMS]
    )
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


def _log_coverage(runtime: RouterRuntime) -> None:
    """Beim Start einmal sagen, wer was bedient und wo eine Luecke bleibt."""
    if not runtime.channels:
        _LOGGER.warning("Kein Anbieter eingerichtet — die Entities bleiben ohne Kanal.")
        return

    _LOGGER.info("Kanaele: %s", ", ".join(channel.key for channel in runtime.channels))
    for profile, entry in runtime.coverage().items():
        if not entry.covered:
            _LOGGER.warning("Profil %s: kein Kanal — hier bleibt eine Luecke", profile)
            continue
        assert entry.primary is not None
        reserve = (
            f", Reserve {entry.reserves[0].key}" if entry.has_reserve else " (ohne Reserve)"
        )
        _LOGGER.info("Profil %s: %s%s", profile, entry.primary.key, reserve)


async def async_unload_entry(
    hass: HomeAssistant, entry: FreeAIRouterConfigEntry
) -> bool:
    from homeassistant.const import Platform

    unloaded = await hass.config_entries.async_unload_platforms(
        entry, [Platform(name) for name in PLATFORMS]
    )
    if unloaded:
        runtime = entry.runtime_data
        if runtime.store is not None:
            # Zaehlerstaende sofort festschreiben — ein Neustart soll nicht
            # eine halbe Minute Kontingentbuchhaltung verlieren.
            await runtime.store.async_save(runtime.ledger.snapshot())
    return unloaded


async def async_reload_entry(
    hass: HomeAssistant, entry: FreeAIRouterConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


__all__ = [
    "CONF_API_KEY",
    "CONF_MODELS",
    "CONF_PROVIDER",
    "DOMAIN",
    "PROFILES",
    "RouterRuntime",
    "build_channels",
]
