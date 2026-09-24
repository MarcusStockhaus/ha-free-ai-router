"""Config Flow — Anbieterkarte, Schluesseltest, fertig.

Ablauf:

1. ``user`` — Anbieter waehlen. Die Karte nennt, was er kann, was er mit den
   Daten macht, ob eine Kreditkarte noetig ist, und verlinkt direkt auf die
   Key-Seite.
2. ``key`` — Schluessel eingeben. Sofort ein echter Aufruf. Geht er durch,
   ist die Integration eingerichtet — ohne weiteren Schritt.

**Warum so kurz.** Frueher folgten hier Faehigkeitsmessung (ein bis zwei
Minuten mit Fortschrittsbalken), Messergebnis, "weiterer Anbieter?" und eine
Uebersicht, gespeichert wurde erst ganz am Ende. Live am 17.09.2026: vier
Anbieter vermessen, der Dialog vor dem letzten Klick geschlossen, nichts
gespeichert — zehn Minuten Arbeit fuer nichts. Seitdem:

* gespeichert wird nach dem ersten gueltigen Schluessel, sofort;
* gemessen wird im Hintergrund (``hintergrund.py``), das Ergebnis kommt als
  Benachrichtigung; bis dahin gelten die Angaben aus der Anbieterdatei;
* jeder weitere Anbieter kommt ueber "Anbieter hinzufuegen" auf der
  Integrationsseite dazu — derselbe kurze Weg, ebenfalls sofort gespeichert.

Jeder eingerichtete Anbieter ist ein **Subentry** und damit eine eigene Zeile
auf der Integrationsseite. Hinzufuegen, Schluessel ersetzen und Entfernen
laufen ueber :class:`AnbieterSubentryFlow`; die Knoepfe, die Liste und der
Loeschdialog kommen von Home Assistant.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .capabilities import (
    FEHLERART_KEIN_ABO,
    FEHLERART_LIMIT,
    FEHLERART_UNBEKANNT,
    FEHLERART_UNERREICHBAR,
    quick_key_check,
)
from .const import (
    CONF_API_KEY,
    CONF_MODELS,
    CONF_PROVIDER,
    DOMAIN,
    SUBENTRY_TYPE_ANBIETER,
)
from .ledger import Availability
from .registry import Provider, Registry, RegistryError, load_registry
from .router import abdeckung_text, coverage
from .sprache import sprache_aus, t

_LOGGER = logging.getLogger(__name__)

TITLE = "Free AI Router"

#: Waehrend der Einrichtung gibt es noch keinen Ledger — fuer die Uebersicht
#: gilt jeder Kanal als frei. Die Frage lautet dort "gibt es einen Kanal",
#: nicht "ist er gerade frei".
_ALWAYS_FREE = lambda _provider, _model: Availability(ok=True)  # noqa: E731


def _provider_card(provider: Provider, sprache: str) -> str:
    """Eine Anbieterkarte als Markdown — eine ehrliche Zeile, kein Rechtstext."""
    can: list[str] = []
    if any(model.capabilities.vision for model in provider.models):
        can.append(t(sprache, "faehigkeit_bilder"))
    if any(model.capabilities.tools for model in provider.models):
        can.append(t(sprache, "faehigkeit_werkzeuge"))
    biggest = max(model.capabilities.context_tokens for model in provider.models)
    titel = f"**{provider.name}**"
    if provider.onboarding.empfohlen:
        titel += t(sprache, "karte_empfohlen")
    parts = [titel]
    if zusammenfassung := provider.onboarding.zusammenfassung(sprache):
        parts.append(zusammenfassung)
    parts.append(
        t(
            sprache,
            "karte_kann",
            was=", ".join(can) if can else t(sprache, "faehigkeit_text"),
            kontext=biggest // 1000,
            anzahl=len(provider.models),
        )
    )
    parts.append(t(sprache, "karte_daten", hinweis=provider.onboarding.datenhinweis(sprache)))
    if provider.onboarding.credit_card_required:
        parts.append(t(sprache, "karte_zahlung"))
    return "\n".join(f"  {line}" if index else f"- {line}" for index, line in enumerate(parts))


def _auswahl(anbieter: list[Provider]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_PROVIDER): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=provider.id, label=provider.name)
                        for provider in anbieter
                    ],
                    mode=SelectSelectorMode.LIST,
                )
            )
        }
    )


def _sortiert(registry: Registry) -> list[Provider]:
    return sorted(registry, key=lambda item: (item.preference, item.id))


def _empfehlung(registry: Registry, eingerichtet: set[str], sprache: str) -> str:
    """Welcher Anbieter als naechstes sinnvoll waere — ein Satz."""
    offen = [
        provider.name
        for provider in _sortiert(registry)
        if provider.onboarding.empfohlen and provider.id not in eingerichtet
    ]
    if offen:
        namen = t(sprache, "empfehlung_und").join(offen)
        return t(sprache, "empfehlung_naechster", namen=namen)
    return t(sprache, "empfehlung_allgemein")


def _neue_daten(provider: Provider, api_key: str) -> dict[str, Any]:
    """Subentry-Daten eines frisch eingerichteten Anbieters — noch ungemessen.

    Leeres ``models`` heisst: jedes Modell ist offen. Die Hintergrundmessung
    nimmt es sich beim naechsten Laden vor; bis dahin gelten die Startwerte
    aus der Anbieterdatei.
    """
    return {CONF_PROVIDER: provider.id, CONF_API_KEY: api_key, CONF_MODELS: {}}


class _SchluesselSchritt:
    """Schluesseleingabe mit sofortigem Test — von beiden Fluessen benutzt.

    Was nach einem gueltigen Schluessel passiert, entscheidet der jeweilige
    Fluss in :meth:`_async_schluessel_gueltig`: der Einrichtungsassistent legt
    den Config Entry an, der Subentry-Flow einen Anbieter.
    """

    def __init__(self) -> None:
        super().__init__()
        self._registry: Registry | None = None
        self._pending_provider: Provider | None = None

    async def _async_registry(self) -> Registry:
        if self._registry is None:
            self._registry = await self.hass.async_add_executor_job(load_registry)
        return self._registry

    @property
    def _sprache(self) -> str:
        return sprache_aus(self.hass.config.language)

    async def async_step_key(self, user_input: dict[str, Any] | None = None) -> Any:
        provider = self._pending_provider
        assert provider is not None

        sprache = self._sprache
        errors: dict[str, str] = {}
        placeholders = {
            "name": provider.name,
            "signup_link": f"[{provider.onboarding.signup_url}]({provider.onboarding.signup_url})",
            "steps": "\n".join(
                f"{index}. {step}"
                for index, step in enumerate(provider.onboarding.schritte(sprache), 1)
            ),
            "data_note": provider.onboarding.datenhinweis(sprache),
            "error_detail": "",
        }

        if user_input is not None:
            api_key = str(user_input[CONF_API_KEY]).strip()
            session = async_get_clientsession(self.hass)
            try:
                ok, message, art = await quick_key_check(
                    session, provider, api_key, sprache=sprache
                )
            except Exception as err:  # noqa: BLE001 - Netzfehler jeder Art
                _LOGGER.debug("Key-Test fehlgeschlagen: %r", err)
                ok, message, art = False, repr(err), FEHLERART_UNBEKANNT

            if ok:
                return await self._async_schluessel_gueltig(provider, api_key, message)

            # Vier Fehlerarten statt einer: "abgelehnt" ist nicht dasselbe wie
            # "kein Abo aktiviert" (Mistral) oder "Anbieter gerade gestoert" —
            # jede verlangt eine andere naechste Handlung vom Nutzer.
            if art == FEHLERART_KEIN_ABO:
                errors["base"] = "key_no_subscription"
            elif art == FEHLERART_LIMIT:
                errors["base"] = "key_rate_limited"
            elif art == FEHLERART_UNERREICHBAR:
                errors["base"] = "key_unreachable"
            else:
                errors["base"] = "key_rejected"
            placeholders["error_detail"] = message[:200]

        return self.async_show_form(
            step_id="key",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _async_schluessel_gueltig(
        self, provider: Provider, api_key: str, meldung: str
    ) -> Any:
        raise NotImplementedError


class FreeAIRouterConfigFlow(_SchluesselSchritt, ConfigFlow, domain=DOMAIN):
    """Ersteinrichtung: ein Anbieter, ein Schluessel, fertig."""

    VERSION = 2
    MINOR_VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        try:
            registry = await self._async_registry()
        except RegistryError as err:
            _LOGGER.error("Registry unbrauchbar: %s", err)
            return self.async_abort(
                reason="registry_invalid", description_placeholders={"error": str(err)}
            )

        if user_input is not None:
            self._pending_provider = registry.require(user_input[CONF_PROVIDER])
            return await self.async_step_key()

        anbieter = _sortiert(registry)
        return self.async_show_form(
            step_id="user",
            data_schema=_auswahl(anbieter),
            description_placeholders={
                "cards": "\n".join(_provider_card(p, self._sprache) for p in anbieter),
            },
        )

    async def _async_schluessel_gueltig(
        self, provider: Provider, api_key: str, meldung: str
    ) -> ConfigFlowResult:
        from . import build_channels  # lokal: sonst Zirkelimport beim Laden

        registry = await self._async_registry()
        daten = _neue_daten(provider, api_key)
        # Die Abdeckung auf Grundlage der Anbieterdatei — die eigene Messung
        # laeuft erst jetzt an. "Voraussichtlich" steht deshalb im Text.
        uebersicht = abdeckung_text(
            coverage(build_channels(registry, {provider.id: daten}), _ALWAYS_FREE),
            self._sprache,
        )
        return self.async_create_entry(
            title=TITLE,
            data={},
            description_placeholders={
                "name": provider.name,
                "check": meldung,
                "overview": uebersicht,
                "empfehlung": _empfehlung(registry, {provider.id}, self._sprache),
            },
            subentries=[
                ConfigSubentryData(
                    data=daten,
                    subentry_type=SUBENTRY_TYPE_ANBIETER,
                    title=provider.name,
                    unique_id=provider.id,
                )
            ],
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Ein Anbieter ist ein Subentry — damit gibt es ihn als eigene Zeile."""
        return {SUBENTRY_TYPE_ANBIETER: AnbieterSubentryFlow}


class AnbieterSubentryFlow(_SchluesselSchritt, ConfigSubentryFlow):
    """Anbieter hinzufuegen und seinen Schluessel ersetzen.

    Beides derselbe Weg wie beim Einrichten: Schluessel, Test, gespeichert.
    Ein ersetzter Schluessel verwirft die bisherige Messung — ein anderer
    Schluessel kann ein anderes Konto sein, und was das Konto darf, ist damit
    offen. Die Hintergrundmessung nimmt sich den Anbieter danach neu vor.

    Das Entfernen steht hier nicht: Knopf und Bestaetigungsdialog bringt Home
    Assistant selbst mit.
    """

    def _eingerichtet(self) -> dict[str, dict[str, Any]]:
        from . import configured_providers  # lokal: sonst Zirkelimport

        return configured_providers(self._get_entry())

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Anbieter waehlen — nur die, die es noch nicht gibt."""
        try:
            registry = await self._async_registry()
        except RegistryError as err:
            return self.async_abort(
                reason="registry_invalid", description_placeholders={"error": str(err)}
            )

        vorhanden = self._eingerichtet()
        offen = [provider for provider in _sortiert(registry) if provider.id not in vorhanden]
        if not offen:
            return self.async_abort(reason="alle_eingerichtet")

        if user_input is not None:
            self._pending_provider = registry.require(user_input[CONF_PROVIDER])
            return await self.async_step_key()

        return self.async_show_form(
            step_id="user",
            data_schema=_auswahl(offen),
            description_placeholders={
                "cards": "\n".join(_provider_card(p, self._sprache) for p in offen)
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Schluessel ersetzen. Der Anbieter steht schon fest."""
        registry = await self._async_registry()
        subentry = self._get_reconfigure_subentry()
        provider = registry.get(subentry.data.get(CONF_PROVIDER, ""))
        if provider is None:
            return self.async_abort(reason="unbekannter_anbieter")
        self._pending_provider = provider
        return await self.async_step_key()

    async def _async_schluessel_gueltig(
        self, provider: Provider, api_key: str, meldung: str
    ) -> SubentryFlowResult:
        daten = _neue_daten(provider, api_key)
        if self.source == SOURCE_USER:
            return self.async_create_entry(
                title=provider.name,
                data=daten,
                unique_id=provider.id,
                description_placeholders={"name": provider.name, "check": meldung},
            )
        return self.async_update_and_abort(
            self._get_entry(), self._get_reconfigure_subentry(), data=daten
        )
