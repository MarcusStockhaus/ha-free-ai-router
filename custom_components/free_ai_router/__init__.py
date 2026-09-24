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

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from .const import (
    CONF_API_KEY,
    CONF_MODELS,
    CONF_PROVIDER,
    DOMAIN,
    PROFILES,
    STORAGE_KEY_FEED,
    STORAGE_KEY_LEDGER,
    STORAGE_VERSION_FEED,
    STORAGE_VERSION_LEDGER,
    SUBENTRY_TYPE_ANBIETER,
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

    from .client import RouterClient

_LOGGER = logging.getLogger(__name__)

#: Plattformen als Text, damit ``Platform`` nicht auf Modulebene noetig ist.
PLATFORMS: list[str] = ["ai_task", "conversation", "sensor"]

#: Ledger nicht bei jeder Anfrage auf die SD-Karte schreiben.
LEDGER_SAVE_DELAY_S = 30

#: Takt, in dem die Reparatur-Hinweise neu erhoben werden. Die Befunde aendern
#: sich nur, wenn Anfragen laufen; oefter nachzusehen brauchte niemand.
ISSUE_CHECK_INTERVAL = timedelta(minutes=10)


@dataclass
class RouterRuntime:
    """Alles, was die Entities zur Laufzeit brauchen."""

    registry: Registry
    ledger: Ledger
    client: RouterClient
    channels: tuple[Channel, ...] = ()
    store: Store | None = None
    base_registry: Registry | None = None
    """Die mitgelieferte Registry ohne Feed.

    Wird aufgehoben, weil der Feed jedesmal neu darueber gelegt wird und nicht
    auf ein bereits angereichertes Ergebnis — sonst bliebe ein einmal
    uebernommener Wert stehen, auch wenn der Feed ihn zuruecknimmt.
    """
    feed: Any = None
    messung: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    """Haelt Hintergrundmessung und Dienst ``neu_vermessen`` auseinander."""
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


def signal_kanaele(entry_id: str) -> str:
    """Dispatcher-Signal: die Kanaele dieser Entry haben sich geaendert.

    Die ``ai_task``- und ``conversation``-Entities schreiben ihren Zustand
    nur, wenn sie benutzt werden. Ohne dieses Signal zeigte ihr Attribut
    ``abgeschaltet`` nach einer Hintergrundmessung weiter den alten Stand —
    live am 24.09.2026 zwei Modelle, die laengst wieder aktiv waren.
    """
    return f"{DOMAIN}_{entry_id}_kanaele"


def _kanaele_geaendert(hass: HomeAssistant, entry: FreeAIRouterConfigEntry) -> None:
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    async_dispatcher_send(hass, signal_kanaele(entry.entry_id))


def configured_providers(entry: FreeAIRouterConfigEntry) -> dict[str, dict[str, Any]]:
    """Die eingerichteten Anbieter, je Anbieter-ID.

    Sie liegen als Subentries am Config Entry — je Anbieter eine Zeile auf der
    Integrationsseite, mit Aendern und Entfernen von Home Assistant selbst.
    Die Reihenfolge spielt hier keine Rolle; sortiert wird beim Bau der
    Kanaele nach ``preference``.

    Die alte Ablage unter ``data["providers"]`` wird noch gelesen, damit ein
    Entry, dessen Migration nicht durchlief, nicht ohne Anbieter dasteht.
    """
    aus_subentries = {
        subentry.data[CONF_PROVIDER]: dict(subentry.data)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_ANBIETER and CONF_PROVIDER in subentry.data
    }
    if aus_subentries:
        return aus_subentries
    return dict(entry.data.get("providers") or {})


def subentry_of(entry: FreeAIRouterConfigEntry, provider_id: str) -> Any | None:
    """Der Subentry dieses Anbieters — oder ``None``, wenn es ihn nicht gibt.

    Gebraucht, um die Verbrauchssensoren an die richtige Zeile zu haengen und
    um beim Neuvermessen den richtigen Eintrag zu aktualisieren.
    """
    for subentry in entry.subentries.values():
        if (
            subentry.subentry_type == SUBENTRY_TYPE_ANBIETER
            and subentry.data.get(CONF_PROVIDER) == provider_id
        ):
            return subentry
    return None


def build_channels(
    registry: Registry, configured: dict[str, dict[str, Any]]
) -> tuple[Channel, ...]:
    """Baue die Kanalliste aus Registry und gemessenen Ueberschreibungen.

    Reihenfolge: ``preference`` des Anbieters, dann die Reihenfolge der
    Modelle in der Datei. Das ist die redaktionelle Vorauswahl ("erste Wahl"
    gegen "Reserve"), auf die sich der Router als Rangkriterium stuetzt — und
    sie darf gerade nicht vom Dateinamen abhaengen.
    """
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


def api_keys(configured: dict[str, dict[str, Any]]) -> dict[str, str]:
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

    # Erst hier, nicht auf Modulebene: ``client`` braucht aiohttp. Sonst
    # laesst sich kein einziges Modul dieses Pakets ohne HTTP-Bibliothek
    # importieren — und die Registry-Pruefung fuer Contributor soll mit
    # PyYAML und jsonschema auskommen. Dasselbe gilt fuer den Feed-Client.
    from .client import RouterClient
    from .feed_client import FeedManager, feed_configured
    from .hintergrund import async_starten
    from .services import async_setup_services

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

    session = async_get_clientsession(hass)
    eingerichtet = configured_providers(entry)

    # Der Feed wird aus dem Zwischenspeicher uebernommen, nicht geholt: der
    # Start soll nicht an einem fremden Server haengen. Nachgesehen wird
    # gleich danach im Hintergrund.
    feed_store = Store(hass, STORAGE_VERSION_FEED, STORAGE_KEY_FEED)
    feed = FeedManager(session, save=feed_store.async_save)
    feed.restore(await feed_store.async_load())
    wirksam = feed.apply(registry)

    runtime = RouterRuntime(
        registry=wirksam,
        ledger=ledger,
        client=RouterClient(
            session=session,
            ledger=ledger,
            keys=api_keys(eingerichtet),
        ),
        channels=build_channels(wirksam, eingerichtet),
        store=store,
        base_registry=registry,
        feed=feed,
    )
    entry.runtime_data = runtime
    _log_coverage(runtime)

    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform(name) for name in PLATFORMS]
    )
    async_setup_services(hass)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    entry.async_on_unload(_start_issue_check(hass, runtime))
    # Was noch nicht gemessen ist, wird jetzt gemessen — der Einrichtungs-
    # assistent speichert nach dem Schluesseltest sofort und misst nicht mehr.
    entry.async_on_unload(async_starten(hass, entry))
    if feed_configured():
        entry.async_on_unload(_start_feed(hass, entry, runtime))
    return True


def _start_feed(hass: HomeAssistant, entry: FreeAIRouterConfigEntry, runtime: RouterRuntime):
    """Den Feed gleich einmal und danach im Takt nachsehen."""
    from homeassistant.helpers.event import async_track_time_interval

    from .feed_client import FEED_INTERVAL_HOURS

    async def _nachsehen(_now: Any = None) -> None:
        if await runtime.feed.async_update():
            _uebernehmen(entry, runtime)
            _kanaele_geaendert(hass, entry)

    entry.async_create_background_task(hass, _nachsehen(), f"{DOMAIN} Feed")
    return async_track_time_interval(
        hass, _nachsehen, timedelta(hours=FEED_INTERVAL_HOURS)
    )


def _uebernehmen(entry: FreeAIRouterConfigEntry, runtime: RouterRuntime) -> None:
    """Ein neues Feed-Dokument in Registry und Kanaele einrechnen.

    Immer auf der mitgelieferten Registry aufsetzen, nie auf der zuletzt
    angereicherten: sonst liesse sich eine Aenderung nie wieder zuruecknehmen.
    """
    basis = runtime.base_registry or runtime.registry
    runtime.registry = runtime.feed.apply(basis)
    runtime.channels = build_channels(runtime.registry, configured_providers(entry))
    _log_coverage(runtime)


def _start_issue_check(hass: HomeAssistant, runtime: RouterRuntime):
    """Reparatur-Hinweise sofort und dann im Takt erheben."""
    from homeassistant.helpers.event import async_track_time_interval

    from .issues import async_pruefen

    async_pruefen(hass, runtime)

    def _tick(_now) -> None:
        async_pruefen(hass, runtime)

    return async_track_time_interval(hass, _tick, ISSUE_CHECK_INTERVAL)


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


async def async_migrate_entry(
    hass: HomeAssistant, entry: FreeAIRouterConfigEntry
) -> bool:
    """Anbieter aus ``data["providers"]`` in Subentries ueberfuehren.

    Fassung 1 hielt alle Anbieter in einem Feld des Config Entry. Damit gab es
    auf der Integrationsseite eine einzige Zeile, und Aendern oder Entfernen
    eines einzelnen Anbieters ging nur ueber einen selbstgebauten Dialog.
    Fassung 2 legt je Anbieter einen Subentry an — die Zeilen, die Knoepfe und
    der Loeschdialog kommen dann von Home Assistant.
    """
    from homeassistant.config_entries import ConfigSubentry

    if entry.version > 2:
        return False
    if entry.version == 2:
        return True

    providers: dict[str, Any] = dict(entry.data.get("providers") or {})
    registry: Registry | None = None
    if providers:
        try:
            registry = await hass.async_add_executor_job(load_registry)
        except RegistryError as err:
            _LOGGER.error("Migration ohne Registry nicht moeglich: %s", err)
            return False

    for provider_id, daten in providers.items():
        provider = registry.get(provider_id) if registry else None
        hass.config_entries.async_add_subentry(
            entry,
            ConfigSubentry(
                data=MappingProxyType({CONF_PROVIDER: provider_id, **daten}),
                subentry_type=SUBENTRY_TYPE_ANBIETER,
                title=provider.name if provider else provider_id,
                unique_id=provider_id,
            ),
        )

    hass.config_entries.async_update_entry(entry, data={}, version=2)
    _LOGGER.info(
        "Config Entry auf Fassung 2 gehoben: %s Anbieter als Subentries", len(providers)
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: FreeAIRouterConfigEntry
) -> bool:
    from homeassistant.const import Platform

    unloaded = await hass.config_entries.async_unload_platforms(
        entry, [Platform(name) for name in PLATFORMS]
    )
    if unloaded and not hass.config_entries.async_loaded_entries(DOMAIN):
        # Der Dienst gehoert der Domain, nicht der Entry. Abmelden erst, wenn
        # keine geladene Entry mehr uebrig ist — sonst nimmt ein Reload ihn
        # mitten im eigenen Aufruf weg.
        from .services import async_unload_services

        async_unload_services(hass)
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
    """Auf eine Aenderung am Config Entry oder einem Subentry reagieren.

    Neu geladen wird nur, wenn sich Anbieter oder Schluessel geaendert haben.
    Ein neues Messergebnis dagegen wird in die laufende Instanz uebernommen:
    die Hintergrundmessung speichert Anbieter fuer Anbieter, und ein Reload
    nach dem ersten braeche die noch laufende Messung der anderen ab.
    """
    runtime = entry.runtime_data
    eingerichtet = configured_providers(entry)
    if api_keys(eingerichtet) == runtime.client.keys:
        from .issues import async_pruefen

        runtime.channels = build_channels(runtime.registry, eingerichtet)
        _log_coverage(runtime)
        async_pruefen(hass, runtime)
        _kanaele_geaendert(hass, entry)
        return
    await hass.config_entries.async_reload(entry.entry_id)


__all__ = [
    "CONF_API_KEY",
    "configured_providers",
    "subentry_of",
    "CONF_MODELS",
    "CONF_PROVIDER",
    "DOMAIN",
    "PROFILES",
    "RouterRuntime",
    "build_channels",
]
