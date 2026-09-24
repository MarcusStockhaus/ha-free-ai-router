"""Header-Normalisierung.

Die Namen unterscheiden sich je Anbieter und aendern sich ohne Ankuendigung.
Was hier nicht erkannt wird, muss trotzdem in ``raw`` landen — sonst faellt
eine Umbenennung erst auf, wenn das Kontingent unbemerkt aufgebraucht ist.
"""

from __future__ import annotations

import time

import pytest

from custom_components.free_ai_router.ratelimit import parse_headers


def test_groq_stil() -> None:
    info = parse_headers(
        {
            "x-ratelimit-limit-requests": "14400",
            "x-ratelimit-remaining-requests": "14399",
            "x-ratelimit-reset-requests": "2m59.56s",
            "x-ratelimit-limit-tokens": "6000",
            "x-ratelimit-remaining-tokens": "5920",
            "x-ratelimit-reset-tokens": "7.66s",
            "content-type": "application/json",
        }
    )
    assert info.limit_requests == 14400
    assert info.remaining_requests == 14399
    assert info.reset_requests_s == pytest.approx(179.56)
    assert info.limit_tokens == 6000
    assert info.reset_tokens_s == pytest.approx(7.66)
    assert "content-type" not in info.raw


def test_openrouter_stil_mit_epoche_in_millisekunden() -> None:
    gleich = (time.time() + 30) * 1000
    info = parse_headers(
        {
            "X-RateLimit-Limit": "50",
            "X-RateLimit-Remaining": "49",
            "X-RateLimit-Reset": str(int(gleich)),
        }
    )
    assert info.limit_requests == 50
    assert info.remaining_requests == 49
    assert info.reset_requests_s == pytest.approx(30, abs=2)


def test_anthropic_stil() -> None:
    info = parse_headers(
        {
            "anthropic-ratelimit-requests-limit": "50",
            "anthropic-ratelimit-requests-remaining": "12",
            "anthropic-ratelimit-tokens-remaining": "9000",
        }
    )
    assert info.limit_requests == 50
    assert info.remaining_requests == 12
    assert info.remaining_tokens == 9000


def test_retry_after() -> None:
    assert parse_headers({"Retry-After": "23"}).retry_after_s == 23
    assert parse_headers({"retry-after": "1m30s"}).retry_after_s == pytest.approx(90)


def test_google_liefert_nichts() -> None:
    info = parse_headers({"content-type": "application/json", "date": "irgendwann"}, "google")
    assert info.is_empty
    assert info.limit_requests is None


def test_unbekannte_ratelimit_header_landen_trotzdem_in_raw() -> None:
    """Damit das Probe-CLI eine Umbenennung sichtbar macht, statt sie zu
    verschlucken."""
    info = parse_headers({"x-ratelimit-neuer-name": "17"})
    assert info.raw == {"x-ratelimit-neuer-name": "17"}
    assert info.limit_requests is None


def test_muell_wird_ignoriert_statt_zu_werfen() -> None:
    info = parse_headers({"x-ratelimit-limit-requests": "keine Zahl"})
    assert info.limit_requests is None
    assert info.raw


def test_mistral_kodiert_das_fenster_im_headernamen() -> None:
    """x-ratelimit-limit-req-minute nennt die Fensterlaenge im Namen. Das ist
    genauer als jede Schaetzung aus einem Reset-Zeitpunkt — und es war der
    Grund, ueberhaupt alle Rate-Limit-Header roh mitzuschreiben."""
    info = parse_headers(
        {
            "x-ratelimit-limit-req-minute": "60",
            "x-ratelimit-remaining-req-minute": "59",
        }
    )
    assert info.limit_requests == 60
    assert info.remaining_requests == 59
    assert info.reset_requests_s == 60.0
    assert info.requests_window_s == 60.0


def test_limit_null_bedeutet_kein_kontingent() -> None:
    """Mistral meldet auf Modellen ausserhalb des Tarifs eine Null — das ist
    kein voruebergehendes Limit, sondern gar kein Zugang."""
    info = parse_headers({"x-ratelimit-limit-req-minute": "0"})
    assert info.limit_requests == 0
    assert not info.is_empty
