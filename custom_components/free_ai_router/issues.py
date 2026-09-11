"""Reparatur-Hinweise — das Gegenstueck zum Reserve-Sensor.

Ein Sensor zeigt einen Wert, wenn jemand hinsieht. Ein Reparatur-Hinweis
meldet sich von selbst, und genau das braucht dieser Router: seine
Fehlerarten sind alle still. Ein abgelehnter Schluessel faellt nicht auf, weil
die Reserve einspringt; ein Profil ohne Kanal faellt erst auf, wenn eine
Automation danach greift.

Drei Befunde, je einer fuer eine Frage, die sich nicht von selbst stellt:

* **Kein Anbieter eingerichtet** — die Entities gibt es, sie koennen nichts.
* **Schluessel abgelehnt** — laeuft im Stillen weiter, solange eine Reserve
  da ist, und faellt erst auf, wenn auch die weg ist.
* **Profil ohne Abdeckung** — die Entity existiert, jeder Aufruf scheitert.

Die Hinweise loeschen sich selbst, sobald der Zustand behoben ist. Sie sind
nicht "fixable": es gibt keinen Knopf, der einen Schluessel gueltig macht.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry

from . import RouterRuntime
from .const import DOMAIN, PROFILE_LABELS_DE, PROFILES

_LOGGER = logging.getLogger(__name__)

ISSUE_KEIN_ANBIETER = "kein_anbieter"
ISSUE_SCHLUESSEL = "schluessel_abgelehnt"
ISSUE_LUECKE = "profil_ohne_abdeckung"
ISSUE_BUDGET = "budget_aufgebraucht"


def _setzen(
    hass: HomeAssistant,
    issue_id: str,
    translation_key: str,
    platzhalter: dict[str, str],
    *,
    schwer: bool = False,
) -> None:
    issue_registry.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=(
            issue_registry.IssueSeverity.ERROR
            if schwer
            else issue_registry.IssueSeverity.WARNING
        ),
        translation_key=translation_key,
        translation_placeholders=platzhalter,
    )


def async_pruefen(hass: HomeAssistant, runtime: RouterRuntime) -> None:
    """Alle Befunde neu erheben und Hinweise angleichen.

    Bewusst zustandslos: erhoben wird jedes Mal alles, gesetzt und geloescht
    wird danach. So kann kein Hinweis haengenbleiben, weil ein Ereignis
    verpasst wurde.
    """
    gewollt: set[str] = set()

    # --- gar kein Anbieter -------------------------------------------------
    if not runtime.channels:
        gewollt.add(ISSUE_KEIN_ANBIETER)
        _setzen(hass, ISSUE_KEIN_ANBIETER, ISSUE_KEIN_ANBIETER, {}, schwer=True)

    # --- abgelehnte Schluessel --------------------------------------------
    anbieter = {channel.provider.id: channel.provider for channel in runtime.channels}
    for provider_id, provider in sorted(anbieter.items()):
        issue_id = f"{ISSUE_SCHLUESSEL}_{provider_id}"
        if runtime.ledger.key_rejected(provider):
            gewollt.add(issue_id)
            _setzen(
                hass,
                issue_id,
                ISSUE_SCHLUESSEL,
                {"anbieter": provider.name, "signup_url": provider.onboarding.signup_url},
                schwer=True,
            )

    # --- Ausgabendeckel erreicht ------------------------------------------
    # Kein schwerer Befund: der Router weicht aus, und zum Monatsersten
    # loest es sich von selbst. Aber es aendert die Erwartung — wer Mistral
    # als Tiefenpuffer eingeplant hat, hat ihn bis dahin nicht mehr.
    for provider_id, provider in sorted(anbieter.items()):
        budget = provider.monthly_budget_usd
        if not budget:
            continue
        ausgegeben = runtime.ledger.spend_state(provider).spent_usd
        if ausgegeben < budget:
            continue
        issue_id = f"{ISSUE_BUDGET}_{provider_id}"
        gewollt.add(issue_id)
        _setzen(
            hass,
            issue_id,
            ISSUE_BUDGET,
            {
                "anbieter": provider.name,
                "budget": f"{budget:.2f}",
                "ausgegeben": f"{ausgegeben:.2f}",
            },
        )

    # --- Profile ohne Kanal ------------------------------------------------
    abdeckung = runtime.coverage()
    for profile in PROFILES:
        issue_id = f"{ISSUE_LUECKE}_{profile}"
        if runtime.channels and not abdeckung[profile].covered:
            gewollt.add(issue_id)
            _setzen(
                hass,
                issue_id,
                ISSUE_LUECKE,
                {"profil": PROFILE_LABELS_DE[profile]},
            )

    # --- alles Uebrige wieder wegnehmen ------------------------------------
    bekannt = {ISSUE_KEIN_ANBIETER}
    bekannt |= {f"{ISSUE_SCHLUESSEL}_{pid}" for pid in anbieter}
    bekannt |= {f"{ISSUE_LUECKE}_{profile}" for profile in PROFILES}
    bekannt |= {f"{ISSUE_BUDGET}_{pid}" for pid in anbieter}
    for issue_id in bekannt - gewollt:
        issue_registry.async_delete_issue(hass, DOMAIN, issue_id)

    if gewollt:
        _LOGGER.debug("Offene Reparatur-Hinweise: %s", ", ".join(sorted(gewollt)))
