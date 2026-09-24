"""Texte, die im Python-Code entstehen — Deutsch und Englisch.

Was Home Assistant selbst uebersetzen kann, steht in ``translations/``:
Dialoge, Entity-Namen, Reparaturhinweise, Dienste, Fehlermeldungen. Hier
steht, was aus Daten zusammengesetzt wird — Anbieterkarten, die
Abdeckungsuebersicht, Messberichte, Sperrgruende, Benachrichtigungen, die
Systemprompts an die Modelle — und die Einheiten der Zaehler, die Home
Assistant selbst immer auf Englisch uebersetzen wuerde (siehe ``sensor.py``).

Die Sprache folgt der Systemsprache von Home Assistant (``hass.config.language``):
Deutsch fuer ``de`` und jede ``de-*``-Variante, sonst Englisch. Kein
homeassistant-Import — auch Ledger, Client und Router benutzen das Modul.
"""

from __future__ import annotations

from typing import Final

DE: Final = "de"
EN: Final = "en"
SPRACHEN: Final = (DE, EN)


def sprache_aus(language: str | None) -> str:
    """Home-Assistant-Sprachcode auf eine der beiden Sprachen abbilden."""
    return DE if (language or "").lower().split("-")[0] == DE else EN


TEXTE: Final[dict[str, dict[str, str]]] = {
    DE: {
        # Profile
        "profil_schnell": "Schnell",
        "profil_vision": "Bildanalyse",
        "profil_reasoning": "Reasoning",
        # Anbieterkarte im Einrichtungsassistenten
        "karte_empfohlen": " — empfohlen für den Anfang",
        "karte_kann": "Kann: {was} · Kontext bis {kontext}k · {anzahl} Modelle",
        "karte_daten": "Daten: {hinweis}",
        "karte_zahlung": "Zahlungsdaten erforderlich, auch für die kostenlose Stufe.",
        "faehigkeit_bilder": "Bilder",
        "faehigkeit_werkzeuge": "Werkzeuge",
        "faehigkeit_schema": "Schema",
        "faehigkeit_text": "Text",
        # Empfehlung am Ende der Einrichtung
        "empfehlung_naechster": (
            "Als Nächstes empfohlen: **{namen}** — damit bekommt jedes Profil eine "
            "Reserve bei einem zweiten Anbieter."
        ),
        "empfehlung_und": " und ",
        "empfehlung_allgemein": (
            "Ein weiterer Anbieter gibt jedem Profil eine Reserve, falls einer ausfällt."
        ),
        # Schlüsseltest
        "antwortet": "{modell} antwortet ({dauer})",
        "keine_antwort": "keine Antwort",
        # Abdeckung
        "abdeckung_luecke": "- **{profil}** — keine Abdeckung. Hier bleibt eine Lücke.",
        "abdeckung_reserve": "- **{profil}** — {erst}, Reserve: {reserve}",
        "abdeckung_ohne_reserve": "- **{profil}** — {erst} — **ohne Reserve**",
        # Warum ein Modell nicht geantwortet hat
        "grund_kein_kontingent": "kein Kontingent für dieses Konto",
        "grund_keine_antwort": "keine Antwort, Zeitüberschreitung oder Netz",
        "grund_vermittler": "Fehler beim Anbieter hinter dem Vermittler",
        "grund_400": "Anfrage abgelehnt",
        "grund_401": "Schlüssel abgelehnt",
        "grund_402": "nur mit bezahltem Tarif",
        "grund_403": "kein Zugriff mit diesem Konto",
        "grund_404": "Modell gibt es nicht (mehr)",
        "grund_408": "Zeitüberschreitung",
        "grund_429": "Limit erreicht",
        "grund_5xx": "Anbieter überlastet oder gestört, HTTP {status}",
        "grund_http": "HTTP {status}",
        # Messbericht
        "bericht_erreichbar": "erreichbar",
        "bericht_nur_text": "nur Text",
        "bericht_gestoert": (
            "- **{modell}** — gerade nicht erreichbar ({grund}), wird später erneut versucht"
        ),
        "bericht_tot": "- **{modell}** — nicht nutzbar: {grund}",
        # Benachrichtigung nach einer Messung
        "meldung_titel": "Free AI Router: {anbieter} vermessen",
        "meldung_kopf": "**{anbieter}**: {erreichbar} von {gesamt} Modellen erreichbar",
        "meldung_offen": ", {anzahl} noch offen",
        "meldung_abdeckung": "So werden die Profile jetzt bedient:",
        # Warum der Router einen Kanal aussortiert
        "ablehnung_profil": "Profil passt nicht",
        "ablehnung_bilder": "kann keine Bilder",
        "ablehnung_werkzeuge": "kann keine Werkzeuge",
        "ablehnung_schema": "kann kein Structured Output",
        "ablehnung_kontext": "Kontextfenster zu klein",
        "ablehnung_abgeschaltet": "abgeschaltet",
        # Wenn kein Kanal liefern konnte
        "kein_kanal_profil": "Kein Kanal kann das Profil „{profil}“ bedienen",
        "alle_kanaele_ausgefallen": (
            "Alle Kanäle für das Profil „{profil}“ sind ausgefallen oder am Limit"
        ),
        "keine_kanaele": "keine Kanäle eingerichtet",
        "versuch_kein_schluessel": "kein Schlüssel hinterlegt",
        "versuch_zeit": "Zeitüberschreitung",
        "versuch_netz": "Netzfehler {fehler}",
        "versuch_limit": "Limit erreicht",
        "versuch_abgelehnt": "Schlüssel abgelehnt",
        "versuch_gewartet": "nach {sekunden} s immer noch: {grund}",
        # Sperren im Ledger
        "sperre_gesperrt": "gesperrt",
        "sperre_budget": "Monatsbudget aufgebraucht ({ausgegeben} von {budget} USD)",
        "sperre_tag": "Tageslimit erreicht ({genutzt}/{grenze})",
        "sperre_rest_null": "Anbieter meldet Rest 0",
        "sperre_minute": "Minutenlimit erreicht ({genutzt}/{grenze})",
        "sperre_token": "Token-Minutenlimit erreicht ({genutzt}/{grenze})",
        "sperre_429": "429 vom Anbieter, gesperrt für {sekunden} s",
        "sperre_wiederholt": "wiederholt fehlgeschlagen",
        # Dienst remeasure
        "dienst_nicht_in_registry": "steht nicht mehr in der Registry",
        "dienst_kein_schluessel": "kein Schlüssel hinterlegt",
        "dienst_kein_subentry": "kein Anbieter-Eintrag gefunden",
        "dienst_umfang_sparsam": "nur Lebendigkeit",
        "dienst_umfang_voll": "alle Prüfungen",
        # Modelle
        "systemprompt": (
            "Du bist Teil einer Home-Assistant-Automation. Antworte knapp und "
            "ausschließlich mit dem Verlangten, ohne Einleitung und ohne Rückfrage. "
            "Antworte auf Deutsch, sofern die Aufgabe nichts anderes verlangt."
        ),
        "assist_keine_antwort": "Dazu habe ich keine Antwort bekommen.",
        # Gerät je Anbieter
        "geraet_modell": "{anzahl} Modelle",
        # Einheiten der Zähler
        "einheit_anfragen": "Anfragen",
        "einheit_token": "Token",
        "einheit_wechsel": "Wechsel",
    },
    EN: {
        "profil_schnell": "Fast",
        "profil_vision": "Image analysis",
        "profil_reasoning": "Reasoning",
        "karte_empfohlen": " — recommended to start with",
        "karte_kann": "Supports: {was} · context up to {kontext}k · {anzahl} models",
        "karte_daten": "Data: {hinweis}",
        "karte_zahlung": "Payment details required, even for the free tier.",
        "faehigkeit_bilder": "images",
        "faehigkeit_werkzeuge": "tools",
        "faehigkeit_schema": "schema",
        "faehigkeit_text": "text",
        "empfehlung_naechster": (
            "Recommended next: **{namen}** — this gives every profile a fallback "
            "at a second provider."
        ),
        "empfehlung_und": " and ",
        "empfehlung_allgemein": (
            "Another provider gives every profile a fallback in case one fails."
        ),
        "antwortet": "{modell} responds ({dauer})",
        "keine_antwort": "no response",
        "abdeckung_luecke": "- **{profil}** — not covered. This is a gap.",
        "abdeckung_reserve": "- **{profil}** — {erst}, fallback: {reserve}",
        "abdeckung_ohne_reserve": "- **{profil}** — {erst} — **no fallback**",
        "grund_kein_kontingent": "no quota for this account",
        "grund_keine_antwort": "no response, timeout or network",
        "grund_vermittler": "error at the provider behind the router service",
        "grund_400": "request rejected",
        "grund_401": "key rejected",
        "grund_402": "paid plan required",
        "grund_403": "no access with this account",
        "grund_404": "model does not exist (anymore)",
        "grund_408": "timeout",
        "grund_429": "limit reached",
        "grund_5xx": "provider overloaded or unavailable, HTTP {status}",
        "grund_http": "HTTP {status}",
        "bericht_erreichbar": "reachable",
        "bericht_nur_text": "text only",
        "bericht_gestoert": "- **{modell}** — currently unreachable ({grund}), will be retried",
        "bericht_tot": "- **{modell}** — not usable: {grund}",
        "meldung_titel": "Free AI Router: {anbieter} measured",
        "meldung_kopf": "**{anbieter}**: {erreichbar} of {gesamt} models reachable",
        "meldung_offen": ", {anzahl} still pending",
        "meldung_abdeckung": "Current profile coverage:",
        "ablehnung_profil": "profile does not match",
        "ablehnung_bilder": "cannot process images",
        "ablehnung_werkzeuge": "cannot use tools",
        "ablehnung_schema": "no structured output",
        "ablehnung_kontext": "context window too small",
        "ablehnung_abgeschaltet": "disabled",
        "kein_kanal_profil": "No channel can serve the profile “{profil}”",
        "alle_kanaele_ausgefallen": (
            "All channels for the profile “{profil}” failed or are at their limit"
        ),
        "keine_kanaele": "no channels configured",
        "versuch_kein_schluessel": "no key stored",
        "versuch_zeit": "timeout",
        "versuch_netz": "network error {fehler}",
        "versuch_limit": "limit reached",
        "versuch_abgelehnt": "key rejected",
        "versuch_gewartet": "still after {sekunden} s: {grund}",
        "sperre_gesperrt": "blocked",
        "sperre_budget": "monthly budget used up ({ausgegeben} of {budget} USD)",
        "sperre_tag": "daily limit reached ({genutzt}/{grenze})",
        "sperre_rest_null": "provider reports 0 remaining",
        "sperre_minute": "per-minute limit reached ({genutzt}/{grenze})",
        "sperre_token": "per-minute token limit reached ({genutzt}/{grenze})",
        "sperre_429": "429 from provider, blocked for {sekunden} s",
        "sperre_wiederholt": "failed repeatedly",
        "dienst_nicht_in_registry": "no longer in the registry",
        "dienst_kein_schluessel": "no key stored",
        "dienst_kein_subentry": "no provider entry found",
        "dienst_umfang_sparsam": "liveness only",
        "dienst_umfang_voll": "all checks",
        "systemprompt": (
            "You are part of a Home Assistant automation. Answer briefly and with "
            "exactly what was asked, without introduction and without follow-up "
            "questions. Answer in English unless the task asks otherwise."
        ),
        "assist_keine_antwort": "I did not get an answer to that.",
        "geraet_modell": "{anzahl} models",
        "einheit_anfragen": "requests",
        "einheit_token": "tokens",
        "einheit_wechsel": "fallbacks",
    },
}


def t(sprache: str, schluessel: str, **werte: object) -> str:
    """Text in der gewuenschten Sprache, Platzhalter eingesetzt."""
    texte = TEXTE.get(sprache) or TEXTE[EN]
    return texte[schluessel].format(**werte)


def profil_name(sprache: str, profil: str) -> str:
    return t(sprache, f"profil_{profil}")


__all__ = ["DE", "EN", "SPRACHEN", "TEXTE", "profil_name", "sprache_aus", "t"]
