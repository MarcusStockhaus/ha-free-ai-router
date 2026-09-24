"""Modell-Waechter: Listen lesen, aussortieren, vergleichen, Issue entscheiden.

Die Beispiele haben die Form der echten Antworten vom 24.09.2026 — gekuerzt
auf die Felder, auf die es ankommt.
"""

from __future__ import annotations

import pytest
from conftest import make_model, make_provider

from tools.waechter import (
    AKTION_AKTUALISIEREN,
    AKTION_ANLEGEN,
    AKTION_NICHTS,
    AKTION_SCHLIESSEN,
    AKTION_WIEDER_OEFFNEN,
    Befund,
    Kandidat,
    aussortieren,
    entscheiden,
    issue_text,
    marke_lesen,
    modelle_aus_liste,
    vergleichen,
)

GOOGLE = {
    "models": [
        {"name": "models/gemini-3.5-flash-lite", "inputTokenLimit": 1048576,
         "supportedGenerationMethods": ["generateContent", "countTokens"]},
        {"name": "models/gemini-3.7-flash", "inputTokenLimit": 1048576,
         "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.5-transcribe", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-005", "supportedGenerationMethods": ["embedContent"]},
    ]
}
GROQ = {
    "data": [
        {"id": "qwen/qwen3.8-27b", "active": True, "input_modalities": ["text", "image"],
         "output_modalities": ["text"], "context_window": 131072},
        {"id": "whisper-large-v3", "active": True, "input_modalities": ["audio"],
         "output_modalities": ["text"]},
        {"id": "canopylabs/orpheus-v1-english", "active": True, "input_modalities": ["text"],
         "output_modalities": ["audio"]},
    ]
}
MISTRAL = {
    "data": [
        {"id": "ministral-14b-2512", "aliases": ["ministral-14b-latest"],
         "capabilities": {"completion_chat": True, "vision": True, "function_calling": True},
         "max_context_length": 262144},
        {"id": "ministral-14b-latest", "aliases": [],
         "capabilities": {"completion_chat": True, "vision": True}},
        {"id": "mistral-embed", "capabilities": {"completion_chat": False}},
        {"id": "alt-2401", "deprecation": "2026-01-01",
         "capabilities": {"completion_chat": True}},
    ]
}
OPENROUTER = {
    "data": [
        {"id": "qwen/qwen3.8-27b:free", "context_length": 262144,
         "architecture": {"input_modalities": ["text", "image"]},
         "supported_parameters": ["tools", "structured_outputs"]},
        {"id": "qwen/qwen3.8-27b", "architecture": {"input_modalities": ["text"]}},
        {"id": "nvidia/nemotron-3.5-content-safety:free",
         "architecture": {"input_modalities": ["text"]}},
    ]
}


def _ids(kandidaten: list[Kandidat]) -> list[str]:
    return sorted(k.id for k in kandidaten)


def test_google_nur_textfaehige_modelle_ohne_aliase_und_audio() -> None:
    kandidaten = aussortieren("google_ai_studio", modelle_aus_liste("google_ai_studio", GOOGLE))
    assert _ids(kandidaten) == ["gemini-3.5-flash-lite", "gemini-3.7-flash"]
    # Google verraet in der Liste nicht, ob ein Modell Bilder kann.
    assert kandidaten[0].bilder is None


def test_groq_ohne_audio_und_sprachausgabe() -> None:
    kandidaten = aussortieren("groq", modelle_aus_liste("groq", GROQ))
    assert _ids(kandidaten) == ["qwen/qwen3.8-27b"]
    assert kandidaten[0].bilder is True


def test_mistral_nur_versionen_keine_aliase_und_nichts_veraltetes() -> None:
    kandidaten = aussortieren("mistral", modelle_aus_liste("mistral", MISTRAL))
    assert _ids(kandidaten) == ["ministral-14b-2512"]
    assert kandidaten[0].werkzeuge is True


def test_openrouter_nur_kostenlose() -> None:
    kandidaten = aussortieren("openrouter", modelle_aus_liste("openrouter", OPENROUTER))
    assert _ids(kandidaten) == ["qwen/qwen3.8-27b:free"]
    assert kandidaten[0].bilder is True and kandidaten[0].werkzeuge is True


def test_neu_und_verschwunden() -> None:
    provider = make_provider(
        "google_ai_studio",
        (make_model("gemini-3.5-flash-lite", "google_ai_studio"),
         make_model("gemini-weg", "google_ai_studio")),
    )
    gelistet = modelle_aus_liste("google_ai_studio", GOOGLE)
    alle = {k.id for k in gelistet} | {"text-embedding-005"}
    befund = vergleichen(provider, gelistet, alle)
    assert _ids(befund.neu) == ["gemini-3.7-flash"]
    assert befund.verschwunden == ["gemini-weg"]


def test_ausschlussregel_macht_nichts_zum_verschwundenen() -> None:
    """Ein Modell in der Datei, das eine Regel aussortieren wuerde, ist nicht
    verschwunden, solange es in der Liste steht."""
    provider = make_provider("groq", (make_model("whisper-large-v3", "groq"),))
    gelistet = modelle_aus_liste("groq", GROQ)
    befund = vergleichen(provider, gelistet, {k.id for k in gelistet})
    assert befund.verschwunden == []


def _befund(*neu: str, verschwunden: tuple[str, ...] = ()) -> Befund:
    return Befund("groq", "Groq", neu=[Kandidat(id=m) for m in neu],
                  verschwunden=list(verschwunden))


def test_pruefsumme_haengt_nur_an_den_kandidaten() -> None:
    a = _befund("x", "y")
    b = _befund("y", "x")
    b.messungen = {"x": "erreichbar"}
    assert a.pruefsumme == b.pruefsumme
    assert a.pruefsumme != _befund("x").pruefsumme


def test_marke_ueberlebt_den_issue_text() -> None:
    befund = _befund("x")
    befund.messungen = {"x": "Bilder, Schema · 0.4 s"}
    marke = marke_lesen(issue_text(befund))
    assert marke == {"pruefsumme": befund.pruefsumme, "messungen": befund.messungen}


def test_tabellenzeile_bricht_nicht() -> None:
    befund = _befund("x")
    befund.messungen = {"x": "nicht nutzbar: kein Kontingent für dieses Konto"}
    zeile = [z for z in issue_text(befund).splitlines() if z.startswith("| `x`")][0]
    assert zeile.count("|") == 6


@pytest.mark.parametrize(
    ("befund", "issue", "erwartet"),
    [
        (_befund("x"), None, AKTION_ANLEGEN),
        (_befund(), None, AKTION_NICHTS),
        (_befund(), {"state": "open", "body": ""}, AKTION_SCHLIESSEN),
        (_befund(), {"state": "closed", "body": ""}, AKTION_NICHTS),
    ],
)
def test_entscheidung_einfache_faelle(befund, issue, erwartet) -> None:
    assert entscheiden(befund, issue) == erwartet


def test_geschlossen_bleibt_zu_bis_sich_etwas_aendert() -> None:
    alt = _befund("x")
    issue = {"state": "closed", "body": issue_text(alt)}
    assert entscheiden(_befund("x"), issue) == AKTION_NICHTS
    assert entscheiden(_befund("x", "y"), issue) == AKTION_WIEDER_OEFFNEN


def test_offenes_issue_wird_nur_bei_aenderung_neu_geschrieben() -> None:
    issue = {"state": "open", "body": issue_text(_befund("x"))}
    assert entscheiden(_befund("x"), issue) == AKTION_NICHTS
    assert entscheiden(_befund("y"), issue) == AKTION_AKTUALISIEREN


def test_bei_fehler_bleibt_das_issue_unangetastet() -> None:
    befund = _befund()
    befund.fehler = "HTTP 401"
    assert entscheiden(befund, {"state": "open", "body": ""}) == AKTION_NICHTS


def test_neue_messung_schreibt_offenes_issue_neu() -> None:
    """Die Pruefsumme bleibt gleich, die Messung ist dazugekommen."""
    issue = {"state": "open", "body": issue_text(_befund("x"))}
    befund = _befund("x")
    befund.messungen = {"x": "Bilder · 0.4 s"}
    assert entscheiden(befund, issue) == AKTION_AKTUALISIEREN


def test_stoerung_wird_angezeigt_aber_nicht_gemerkt() -> None:
    befund = _befund("x", "y")
    befund.messungen = {"x": "gerade nicht erreichbar", "y": "Schema · 0.3 s"}
    befund.vorlaeufig = {"x"}
    text = issue_text(befund)
    assert "gerade nicht erreichbar" in text
    assert marke_lesen(text)["messungen"] == {"y": "Schema · 0.3 s"}
