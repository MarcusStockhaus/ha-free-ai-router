"""Router-Tests — ohne Netzzugriff, ohne Home Assistant.

Der Router ist der Kern der Integration; hier liegt der Schwerpunkt der
Tests. Insbesondere die drei Faelle aus den Akzeptanzkriterien: kein Kandidat
kann Vision, alle Kandidaten sind am Limit, der einzige Kandidat ist tot.
"""

from __future__ import annotations

import pytest
from conftest import make_model, make_provider

from custom_components.free_ai_router.ledger import Availability
from custom_components.free_ai_router.router import (
    REASON_CONTEXT,
    REASON_DISABLED,
    REASON_PROFILE,
    REASON_STRUCTURED,
    REASON_VISION,
    Channel,
    Requirements,
    coverage,
    plan,
)

FREE = lambda _p, _m: Availability(ok=True)  # noqa: E731


def channel(
    provider_id: str,
    model_id: str,
    *,
    enabled: bool = True,
    **model_kwargs,
) -> Channel:
    model = make_model(model_id, provider_id, **model_kwargs)
    provider = make_provider(provider_id, (model,))
    return Channel(provider=provider, model=model, enabled=enabled)


# --------------------------------------------------------------------------
# Faehigkeitsfilter
# --------------------------------------------------------------------------


def test_vision_anforderung_sortiert_blinde_modelle_aus() -> None:
    channels = [
        channel("groq", "llama", profiles=("schnell",), vision=False),
        channel("google", "gemini", profiles=("vision",), vision=True),
    ]
    result = plan(Requirements.for_profile("vision"), channels, FREE)

    assert [candidate.key for candidate in result.candidates] == ["google/gemini"]
    assert ("groq/llama", REASON_VISION) in [
        (item.key, item.reason) for item in result.rejected
    ]


def test_kein_kandidat_kann_vision() -> None:
    """Akzeptanzkriterium: die Luecke muss benannt werden, nicht nur leer sein."""
    channels = [
        channel("groq", "llama", profiles=("schnell", "vision"), vision=False),
        channel("cerebras", "qwen", profiles=("vision",), vision=False),
    ]
    result = plan(Requirements.for_profile("vision"), channels, FREE)

    assert not result.has_candidate
    assert result.first is None
    assert {item.reason for item in result.rejected} == {REASON_VISION}
    assert "kein Kandidat" in result.explain()


def test_bild_am_schnell_profil_darf_ein_vision_modell_ziehen() -> None:
    """Ein Anhang schlaegt das Profil — sonst scheitert die Kameraanalyse
    daran, dass sie an der falschen Entity ausgeloest wurde."""
    channels = [
        channel("groq", "llama", profiles=("schnell",), vision=False),
        channel("google", "gemini", profiles=("vision",), vision=True),
    ]
    requirements = Requirements.for_profile("schnell", has_attachments=True)
    result = plan(requirements, channels, FREE)

    assert [candidate.key for candidate in result.candidates] == ["google/gemini"]


def test_profilfremde_modelle_bleiben_ohne_bild_aussen_vor() -> None:
    channels = [channel("google", "gemini", profiles=("vision",), vision=True)]
    result = plan(Requirements.for_profile("reasoning"), channels, FREE)

    assert not result.has_candidate
    assert result.rejected[0].reason == REASON_PROFILE


def test_structured_output_und_kontextfenster_filtern() -> None:
    channels = [
        channel("a", "ohne-schema", structured_output=False),
        channel("b", "zu-klein", context_tokens=1000),
        channel("c", "passt", context_tokens=200_000),
    ]
    requirements = Requirements.for_profile(
        "schnell", has_structure=True, approx_input_tokens=50_000
    )
    result = plan(requirements, channels, FREE)

    assert [candidate.key for candidate in result.candidates] == ["c/passt"]
    reasons = {item.key: item.reason for item in result.rejected}
    assert reasons["a/ohne-schema"] == REASON_STRUCTURED
    assert reasons["b/zu-klein"] == REASON_CONTEXT


def test_einziger_kandidat_tot() -> None:
    """Akzeptanzkriterium: ein abgeschalteter Kanal darf nicht durchrutschen."""
    channels = [channel("google", "gemini", profiles=("vision",), vision=True, enabled=False)]
    result = plan(Requirements.for_profile("vision"), channels, FREE)

    assert not result.has_candidate
    assert result.rejected[0].reason == REASON_DISABLED


# --------------------------------------------------------------------------
# Rangfolge
# --------------------------------------------------------------------------


def test_schnell_sortiert_nach_latenz() -> None:
    channels = [
        channel("langsam", "a", latency_class="langsam"),
        channel("fix", "b", latency_class="sehr_schnell"),
        channel("mittel", "c", latency_class="normal"),
    ]
    result = plan(Requirements.for_profile("schnell"), channels, FREE)

    assert [candidate.key for candidate in result.candidates] == ["fix/b", "mittel/c", "langsam/a"]


def test_andere_profile_folgen_der_registry_reihenfolge() -> None:
    """Die Reihenfolge ist die redaktionelle Vorauswahl und schlaegt das
    Restkontingent — sonst wandert die Anfrage zur ungetesteten Reserve, nur
    weil deren Zaehler noch auf null steht."""
    channels = [
        channel("erste_wahl", "a", profiles=("reasoning",), latency_class="langsam"),
        channel("reserve", "b", profiles=("reasoning",), latency_class="sehr_schnell"),
    ]

    def availability(provider, _model):
        # Die Reserve hat mehr Rest — trotzdem bleibt sie Reserve.
        return Availability(ok=True, headroom=1.0 if provider.id == "reserve" else 0.2)

    result = plan(Requirements.for_profile("reasoning"), channels, availability)
    assert [candidate.key for candidate in result.candidates] == ["erste_wahl/a", "reserve/b"]


def test_restkontingent_entscheidet_bei_gleichstand() -> None:
    model_a = make_model("a", "p", profiles=("reasoning",))
    model_b = make_model("b", "p", profiles=("reasoning",))
    provider = make_provider("p", (model_a, model_b))
    channels = [Channel(provider, model_a), Channel(provider, model_b)]

    # Gleiche Position ist nicht moeglich; deshalb ueber die Verfuegbarkeit
    # pruefen, dass der leerere Topf hinten landet, wenn alles andere gleich
    # ist — hier durch den Nicht-frei-Zustand von a.
    def availability(_provider, model):
        return Availability(ok=model.id == "b", reason="voll", wait_s=30.0)

    result = plan(Requirements.for_profile("reasoning"), channels, availability)
    assert [candidate.key for candidate in result.candidates] == ["p/b", "p/a"]


def test_alle_kandidaten_am_limit() -> None:
    """Akzeptanzkriterium: am Limit heisst nach hinten, nicht raus — sonst
    kann die Warteschlange nicht mehr greifen."""
    channels = [channel("a", "x"), channel("b", "y")]
    blocked = lambda _p, _m: Availability(  # noqa: E731
        ok=False, reason="Minutenlimit erreicht", wait_s=25.0, headroom=0.0
    )

    result = plan(Requirements.for_profile("schnell"), channels, blocked)

    assert result.has_candidate
    assert result.queued_only
    assert result.immediate == ()
    assert all(candidate.availability.queueable for candidate in result.candidates)


def test_tageslimit_ist_nicht_einreihbar() -> None:
    """Gegen ein Tagesfenster hilft kein Warten von Sekunden."""
    channels = [channel("a", "x")]
    exhausted = lambda _p, _m: Availability(  # noqa: E731
        ok=False, reason="Tageslimit erreicht", wait_s=40_000.0
    )

    result = plan(Requirements.for_profile("schnell"), channels, exhausted)
    assert not result.candidates[0].availability.queueable


def test_freier_kanal_schlaegt_wartenden_unabhaengig_von_der_reihenfolge() -> None:
    channels = [channel("erst", "a"), channel("zweit", "b")]

    def availability(provider, _model):
        if provider.id == "erst":
            return Availability(ok=False, reason="voll", wait_s=30.0)
        return Availability(ok=True)

    result = plan(Requirements.for_profile("schnell"), channels, availability)
    assert result.first is not None
    assert result.first.key == "zweit/b"
    assert result.immediate[0].key == "zweit/b"


# --------------------------------------------------------------------------
# Abdeckung
# --------------------------------------------------------------------------


def test_abdeckung_zeigt_erste_wahl_reserve_und_luecke() -> None:
    channels = [
        channel("google", "gemini", profiles=("vision", "schnell"), vision=True),
        channel("openrouter", "qwen-vl", profiles=("vision",), vision=True),
        channel("groq", "llama", profiles=("schnell",)),
    ]
    result = coverage(channels, FREE)

    assert result["vision"].covered
    assert result["vision"].primary.key == "google/gemini"
    assert [item.key for item in result["vision"].reserves] == ["openrouter/qwen-vl"]

    assert result["schnell"].covered
    assert not result["reasoning"].covered
    assert not result["reasoning"].has_reserve


def test_abdeckung_meldet_fehlende_reserve() -> None:
    channels = [channel("google", "gemini", profiles=("vision",), vision=True)]
    result = coverage(channels, FREE)

    assert result["vision"].covered
    assert not result["vision"].has_reserve


def test_unbekanntes_profil_faellt_auf() -> None:
    with pytest.raises(ValueError, match="Unbekanntes Profil"):
        Requirements.for_profile("gibtsnicht")


def test_leere_kanalliste() -> None:
    result = plan(Requirements.for_profile("schnell"), [], FREE)
    assert not result.has_candidate
    assert result.rejected == ()
    assert "keine Kanaele" in result.explain()


def test_abdeckung_als_text_nennt_luecke_und_fehlende_reserve(free_availability) -> None:
    from custom_components.free_ai_router.router import abdeckung_text

    schnell = make_model("klein", "a", profiles=("schnell",))
    kanaele = [Channel(provider=make_provider("a", (schnell,)), model=schnell)]
    text = abdeckung_text(coverage(kanaele, free_availability))

    assert "**Schnell** — A · klein — **ohne Reserve**" in text
    assert "**Bildanalyse** — keine Abdeckung" in text
    assert "{" not in text
