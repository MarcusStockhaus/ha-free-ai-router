"""Config Flow — Anbieterkarten, Key-Test, Faehigkeitserkennung, Uebersicht.

Ablauf:

1. ``user`` — Anbieter waehlen. Die Karte nennt, was er kann, was er mit den
   Daten macht, ob eine Kreditkarte noetig ist, und verlinkt direkt auf die
   Key-Seite.
2. ``key`` — Schluessel eingeben. Sofort ein echter Aufruf, sichtbares
   Ergebnis in Sekunden.
3. ``probe`` — Faehigkeitserkennung als Hintergrundtask mit
   Fortschrittsanzeige. Das muss so sein: die Messung macht mehrere echte
   API-Aufrufe je Modell und laeuft je nach Anbieter deutlich laenger, als ein
   Formularschritt stehenbleiben darf.
4. ``result`` — Messergebnis, dann Menue: weiterer Anbieter oder fertig.
5. ``summary`` — welches Profil bedient wer, wo bleibt eine Luecke.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigFlow, ConfigFlowResult
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

from .capabilities import ProviderProbe, merge_into_registry, probe_provider, quick_key_check
from .const import CONF_API_KEY, CONF_MODELS, CONF_PROVIDER, DOMAIN, PROFILE_LABELS_DE, PROFILES
from .ledger import Availability
from .registry import Provider, Registry, RegistryError, load_registry
from .router import coverage

_LOGGER = logging.getLogger(__name__)

TITLE = "Free AI Router"

#: Waehrend der Einrichtung gibt es noch keinen Ledger — fuer die Uebersicht
#: gilt jeder Kanal als frei. Die Frage lautet dort "gibt es einen Kanal",
#: nicht "ist er gerade frei".
_ALWAYS_FREE = lambda _provider, _model: Availability(ok=True)  # noqa: E731


def _provider_card(provider: Provider) -> str:
    """Eine Anbieterkarte als Markdown — eine ehrliche Zeile, kein Rechtstext."""
    can: list[str] = []
    if any(model.capabilities.vision for model in provider.models):
        can.append("Bilder")
    if any(model.capabilities.tools for model in provider.models):
        can.append("Werkzeuge")
    biggest = max(model.capabilities.context_tokens for model in provider.models)
    parts = [f"**{provider.name}**"]
    if provider.onboarding.summary_de:
        parts.append(provider.onboarding.summary_de)
    parts.append(
        f"Kann: {', '.join(can) if can else 'Text'} · "
        f"Kontext bis {biggest // 1000}k · {len(provider.models)} Modelle"
    )
    parts.append(f"Daten: {provider.onboarding.data_note_de}")
    if provider.onboarding.credit_card_required:
        parts.append("Zahlungsdaten erforderlich, auch fuer die kostenlose Stufe.")
    return "\n".join(f"  {line}" if index else f"- {line}" for index, line in enumerate(parts))


def _probe_report(provider: Provider, probe: ProviderProbe) -> str:
    """Messergebnis als Markdown-Liste."""
    lines: list[str] = []
    for model in probe.models:
        if not model.alive:
            lines.append(f"- **{model.model_id}** — nicht erreichbar: {model.error[:120]}")
            continue
        can = []
        if model.vision.ok:
            can.append("Bilder")
        if model.structured_output.ok:
            can.append("Schema")
        if model.tools.ok:
            can.append("Werkzeuge")
        latency = f"{model.latency_total_s:.1f} s" if model.latency_total_s else "?"
        ttft = f", erstes Token {model.ttft_s:.1f} s" if model.ttft_s else ""
        lines.append(
            f"- **{model.model_id}** — {', '.join(can) or 'nur Text'} · {latency}{ttft}"
        )
        if model.vision.ok is False and model.vision.detail:
            lines.append(f"    Bild: {model.vision.detail[:110]}")

    stale = probe.stale_registry_entries
    if stale:
        lines.append(
            f"- Hinweis: {provider.name} fuehrt diese Modelle nicht mehr: {', '.join(stale)}"
        )
    return "\n".join(lines) or "- keine Messwerte"


class FreeAIRouterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Mehrstufige Einrichtung, mehrere Anbieter nacheinander."""

    VERSION = 1

    def __init__(self) -> None:
        self._registry: Registry | None = None
        self._providers: dict[str, dict[str, Any]] = {}
        self._pending_provider: Provider | None = None
        self._pending_key: str = ""
        self._probe_task: asyncio.Task[ProviderProbe] | None = None
        self._probe_result: ProviderProbe | None = None

    # ------------------------------------------------------------ Registry
    async def _async_registry(self) -> Registry:
        if self._registry is None:
            self._registry = await self.hass.async_add_executor_job(load_registry)
        return self._registry

    # ------------------------------------------------------- Anbieterauswahl
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

        remaining = [
            provider
            for provider in sorted(registry, key=lambda item: (item.preference, item.id))
            if provider.id not in self._providers
        ]
        if not remaining:
            return await self.async_step_summary()

        if user_input is not None:
            self._pending_provider = registry.require(user_input[CONF_PROVIDER])
            return await self.async_step_key()

        schema = vol.Schema(
            {
                vol.Required(CONF_PROVIDER): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=provider.id, label=provider.name)
                            for provider in remaining
                        ],
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            description_placeholders={
                "cards": "\n".join(_provider_card(provider) for provider in remaining),
                "configured": ", ".join(self._providers) or "noch keiner",
            },
        )

    # --------------------------------------------------------- Key-Eingabe
    async def async_step_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        provider = self._pending_provider
        assert provider is not None

        errors: dict[str, str] = {}
        placeholders = {
            "name": provider.name,
            "signup_url": provider.onboarding.signup_url,
            "steps": "\n".join(
                f"{index}. {step}" for index, step in enumerate(provider.onboarding.steps_de, 1)
            ),
            "data_note": provider.onboarding.data_note_de,
            "error_detail": "",
        }

        if user_input is not None:
            api_key = str(user_input[CONF_API_KEY]).strip()
            session = async_get_clientsession(self.hass)
            try:
                ok, message = await quick_key_check(session, provider, api_key)
            except Exception as err:  # noqa: BLE001 - Netzfehler jeder Art
                _LOGGER.debug("Key-Test fehlgeschlagen: %r", err)
                ok, message = False, repr(err)

            if ok:
                self._pending_key = api_key
                self._probe_task = None
                self._probe_result = None
                return await self.async_step_probe()

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

    # -------------------------------------------------- Faehigkeitserkennung
    async def async_step_probe(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Messung als Hintergrundtask.

        Synchron im Formularschritt ginge nicht: pro Modell laufen vier echte
        Aufrufe, bei vier Modellen sind das schnell ein bis zwei Minuten. Ein
        Formularschritt, der so lange haengt, sieht fuer den Nutzer aus wie ein
        Absturz.
        """
        provider = self._pending_provider
        assert provider is not None

        if self._probe_task is None:
            self._probe_task = self.hass.async_create_task(self._async_probe())

        if not self._probe_task.done():
            return self.async_show_progress(
                step_id="probe",
                progress_action="probing",
                progress_task=self._probe_task,
                description_placeholders={"name": provider.name},
            )

        try:
            self._probe_result = self._probe_task.result()
        except Exception as err:  # noqa: BLE001 - die Messung darf nie den Flow toeten
            _LOGGER.warning("Faehigkeitserkennung fehlgeschlagen: %r", err)
            self._probe_result = ProviderProbe(provider_id=provider.id, error=repr(err))
        finally:
            self._probe_task = None

        return self.async_show_progress_done(next_step_id="result")

    async def _async_probe(self) -> ProviderProbe:
        provider = self._pending_provider
        assert provider is not None
        session = async_get_clientsession(self.hass)
        return await probe_provider(
            session, provider, self._pending_key, discover=True, concurrency=1
        )

    # ------------------------------------------------------------ Ergebnis
    async def async_step_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        provider = self._pending_provider
        probe = self._probe_result
        assert provider is not None and probe is not None

        # Auch ein Anbieter ohne einziges lebendes Modell wird uebernommen —
        # der Key wurde getestet, und die Registry kann morgen ein Modell
        # nachliefern. Abgeschaltet werden nur die gemessen toten Modelle.
        self._providers[provider.id] = {
            CONF_API_KEY: self._pending_key,
            CONF_MODELS: merge_into_registry(provider, probe.models),
        }
        self._pending_provider = None
        self._pending_key = ""

        registry = await self._async_registry()
        remaining = [item for item in registry if item.id not in self._providers]

        menu_options = ["add_another", "summary"] if remaining else ["summary"]
        return self.async_show_menu(
            step_id="result",
            menu_options=menu_options,
            description_placeholders={
                "name": provider.name,
                "report": _probe_report(provider, probe),
                "working": str(len(probe.working_models)),
                "total": str(len(probe.models)),
            },
        )

    async def async_step_add_another(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self.async_step_user()

    # ----------------------------------------------------------- Uebersicht
    async def async_step_summary(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        registry = await self._async_registry()
        data = {"providers": self._providers}

        if user_input is not None:
            if self.source == SOURCE_RECONFIGURE:
                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(), data=data
                )
            return self.async_create_entry(title=TITLE, data=data)

        return self.async_show_form(
            step_id="summary",
            data_schema=vol.Schema({}),
            description_placeholders={"overview": self._coverage_text(registry)},
        )

    def _coverage_text(self, registry: Registry) -> str:
        """Welches Profil wird von wem bedient, wo bleibt eine Luecke?"""
        from . import build_channels  # lokal: sonst Zirkelimport beim Laden

        channels = build_channels(registry, {"providers": self._providers})
        entries = coverage(channels, _ALWAYS_FREE)

        lines: list[str] = []
        for profile in PROFILES:
            entry = entries[profile]
            label = PROFILE_LABELS_DE[profile]
            if not entry.covered:
                lines.append(f"- **{label}** — keine Abdeckung. Hier bleibt eine Luecke.")
                continue
            reserve = (
                f", Reserve: {entry.reserves[0].key}"
                if entry.has_reserve
                else " — **ohne Reserve**"
            )
            lines.append(f"- **{label}** — {entry.primary.key}{reserve}")
        return "\n".join(lines)

    # --------------------------------------------------------- Nachtraeglich
    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Weiteren Anbieter hinzufuegen, ohne alles neu einzurichten."""
        entry = self._get_reconfigure_entry()
        self._providers = dict(entry.data.get("providers") or {})
        return await self.async_step_user()
