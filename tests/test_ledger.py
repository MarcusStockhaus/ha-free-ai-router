"""Ledger-Tests: Fenster, Zeitzonen, Sperren, Warteschlange."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import make_model, make_provider

from custom_components.free_ai_router.ledger import (
    DEFAULT_COOLDOWN_S,
    Ledger,
    bucket_key,
)
from custom_components.free_ai_router.ratelimit import RateLimitInfo


def build(*, rpm=None, rpd=None, scope="per_model", tz="UTC"):
    model_a = make_model("a", "p", rpm=rpm, rpd=rpd)
    model_b = make_model("b", "p", rpm=rpm, rpd=rpd)
    provider = make_provider(
        "p", (model_a, model_b), limits_scope=scope, daily_reset_timezone=tz
    )
    return provider, model_a, model_b


# --------------------------------------------------------------------------
# Toepfe
# --------------------------------------------------------------------------


def test_per_model_zaehlt_getrennt() -> None:
    provider, model_a, model_b = build(rpm=2)
    assert bucket_key(provider, model_a) != bucket_key(provider, model_b)

    ledger = Ledger()
    ledger.record_request(provider, model_a)
    ledger.record_request(provider, model_a)

    assert not ledger.availability(provider, model_a).ok
    assert ledger.availability(provider, model_b).ok


def test_per_key_zaehlt_gemeinsam() -> None:
    """OpenRouter zaehlt je Schluessel ueber alle Modelle — wer das
    verwechselt, rennt in ein Limit, das der Zaehler nicht kennt."""
    provider, model_a, model_b = build(rpm=2, scope="per_key")
    assert bucket_key(provider, model_a) == bucket_key(provider, model_b)

    ledger = Ledger()
    ledger.record_request(provider, model_a)
    ledger.record_request(provider, model_b)

    assert not ledger.availability(provider, model_a).ok
    assert not ledger.availability(provider, model_b).ok


# --------------------------------------------------------------------------
# Fenster
# --------------------------------------------------------------------------


def test_minutenfenster_dreht_weiter() -> None:
    provider, model, _ = build(rpm=1)
    ledger = Ledger()
    now = 1_000_000.0

    ledger.record_request(provider, model, now=now)
    blocked = ledger.availability(provider, model, now=now + 10)
    assert not blocked.ok
    assert "Minutenlimit" in blocked.reason
    assert blocked.wait_s == pytest.approx(50.0, abs=1.0)
    assert blocked.queueable

    assert ledger.availability(provider, model, now=now + 61).ok


def test_tagesfenster_folgt_der_zeitzone_des_anbieters() -> None:
    """Google setzt in Pacific Time zurueck, nicht lokal. Um 08:00 UTC ist
    dort noch der Vortag — der Zaehler darf da nicht schon zurueckspringen."""
    provider, model, _ = build(rpd=1, tz="America/Los_Angeles")
    ledger = Ledger()

    pacific = ZoneInfo("America/Los_Angeles")
    # 2026-03-10 23:30 Pacific
    abends = datetime(2026, 3, 10, 23, 30, tzinfo=pacific).timestamp()
    ledger.record_request(provider, model, now=abends)
    assert not ledger.availability(provider, model, now=abends + 60).ok

    # Eine Stunde spaeter ist es in Pacific der naechste Tag.
    nachts = datetime(2026, 3, 11, 0, 30, tzinfo=pacific).timestamp()
    assert ledger.availability(provider, model, now=nachts).ok


def test_tagesfenster_bleibt_zu_wenn_nur_die_lokale_zone_umschlaegt() -> None:
    provider, model, _ = build(rpd=1, tz="America/Los_Angeles")
    ledger = Ledger()

    # 2026-03-10 16:00 Pacific = 2026-03-11 00:00 UTC.
    nachmittag = datetime(2026, 3, 11, 0, 0, tzinfo=UTC).timestamp()
    ledger.record_request(provider, model, now=nachmittag)
    assert not ledger.availability(provider, model, now=nachmittag + 300).ok


def test_tageslimit_ist_nicht_einreihbar() -> None:
    provider, model, _ = build(rpd=1)
    ledger = Ledger()
    now = time.time()
    ledger.record_request(provider, model, now=now)

    availability = ledger.availability(provider, model, now=now)
    assert not availability.ok
    assert not availability.queueable
    assert availability.wait_s > 120


def test_headroom_sinkt_mit_dem_verbrauch() -> None:
    provider, model, _ = build(rpm=10)
    ledger = Ledger()
    now = 500.0
    assert ledger.availability(provider, model, now=now).headroom == 1.0
    for _ in range(5):
        ledger.record_request(provider, model, now=now)
    assert ledger.availability(provider, model, now=now).headroom == pytest.approx(0.5)


# --------------------------------------------------------------------------
# Sperren
# --------------------------------------------------------------------------


def test_beobachteter_429_sperrt_haerter_als_die_eigene_zaehlung() -> None:
    provider, model, _ = build(rpm=100)
    ledger = Ledger()
    now = 900.0

    assert ledger.availability(provider, model, now=now).ok
    ledger.record_rate_limited(provider, model, now=now)

    blocked = ledger.availability(provider, model, now=now + 1)
    assert not blocked.ok
    assert "429" in blocked.reason
    assert ledger.availability(provider, model, now=now + DEFAULT_COOLDOWN_S + 1).ok


def test_retry_after_bestimmt_die_sperrdauer() -> None:
    provider, model, _ = build()
    ledger = Ledger()
    now = 900.0
    ledger.record_rate_limited(provider, model, retry_after_s=5.0, now=now)

    assert not ledger.availability(provider, model, now=now + 2).ok
    assert ledger.availability(provider, model, now=now + 6).ok


def test_einzelner_fehler_schaltet_keinen_kanal_ab() -> None:
    provider, model, _ = build()
    ledger = Ledger()
    now = 900.0

    ledger.record_failure(provider, model, reason="Netzhaenger", now=now)
    assert ledger.availability(provider, model, now=now).ok

    ledger.record_failure(provider, model, now=now)
    ledger.record_failure(provider, model, now=now)
    assert not ledger.availability(provider, model, now=now).ok


def test_fatal_sperrt_sofort() -> None:
    provider, model, _ = build()
    ledger = Ledger()
    now = 900.0
    ledger.record_failure(provider, model, fatal=True, reason="Schluessel abgelehnt", now=now)

    availability = ledger.availability(provider, model, now=now)
    assert not availability.ok
    assert "Schluessel" in availability.reason


def test_erfolg_hebt_die_sperre_auf() -> None:
    provider, model, _ = build()
    ledger = Ledger()
    now = 900.0
    ledger.record_failure(provider, model, fatal=True, now=now)
    ledger.record_success(provider, model, now=now)

    assert ledger.availability(provider, model, now=now).ok


def test_anbieter_meldet_rest_null() -> None:
    provider, model, _ = build()
    ledger = Ledger()
    now = 900.0
    ledger.absorb_headers(
        provider,
        model,
        RateLimitInfo(remaining_requests=0, raw={"x-ratelimit-remaining-requests": "0"}),
        now=now,
    )

    availability = ledger.availability(provider, model, now=now)
    assert not availability.ok
    assert "Rest 0" in availability.reason
    # Nach zwei Minuten gilt die Header-Angabe als veraltet.
    assert ledger.availability(provider, model, now=now + 200).ok


def test_leere_header_aendern_nichts() -> None:
    provider, model, _ = build()
    ledger = Ledger()
    ledger.absorb_headers(provider, model, RateLimitInfo())
    assert ledger.availability(provider, model).ok


# --------------------------------------------------------------------------
# Warteschlange und Persistenz
# --------------------------------------------------------------------------


async def test_warteschlange_wartet_bis_das_fenster_aufgeht() -> None:
    provider, model, _ = build(rpm=1)
    ledger = Ledger()
    ledger.record_request(provider, model)
    # Fenster kuenstlich fast abgelaufen.
    ledger.bucket(bucket_key(provider, model)).minute_start = time.time() - 59.9

    availability = await ledger.wait_for_slot(provider, model, max_wait_s=2.0)
    assert availability.ok


async def test_warteschlange_verwirft_sauber_statt_zu_haengen() -> None:
    """Akzeptanzkriterium: am RPM-Anschlag wird eingereiht oder sauber
    verworfen — es fliegt keine Ausnahme."""
    provider, model, _ = build(rpm=1)
    ledger = Ledger()
    ledger.record_request(provider, model)

    started = asyncio.get_running_loop().time()
    availability = await ledger.wait_for_slot(provider, model, max_wait_s=0.05)
    dauer = asyncio.get_running_loop().time() - started

    assert not availability.ok
    assert "Minutenlimit" in availability.reason
    assert dauer < 1.0


async def test_persistenz_haelt_die_zaehler() -> None:
    provider, model, _ = build(rpm=5)
    gespeichert: dict = {}

    async def save(data):
        gespeichert.update(data)

    ledger = Ledger(save=save)
    ledger.record_request(provider, model)
    await ledger.async_save()

    wieder = Ledger()
    wieder.restore(gespeichert)
    assert wieder.bucket(bucket_key(provider, model)).minute_requests == 1


async def test_save_nur_bei_aenderung() -> None:
    provider, model, _ = build()
    aufrufe = []

    async def save(data):
        aufrufe.append(data)

    ledger = Ledger(save=save)
    await ledger.async_save()
    assert aufrufe == []

    ledger.record_request(provider, model)
    await ledger.async_save()
    assert len(aufrufe) == 1


def test_unbekannte_zeitzone_faellt_auf_utc_zurueck() -> None:
    provider, model, _ = build(rpd=1, tz="Mars/Olympus_Mons")
    ledger = Ledger()
    # Darf nicht werfen.
    assert ledger.availability(provider, model).ok


# --------------------------------------------------------------------------
# Tokenfenster
# --------------------------------------------------------------------------


def test_geschaetzte_token_bremsen_vor_dem_absenden() -> None:
    """Gemma hat 16k Token/Minute bei 30 Anfragen/Minute — das Tokenfenster
    bindet also zuerst. Es muss vor dem Absenden greifen, nicht erst nach
    einem 429."""
    model = make_model("gemma", "google", tpm=16000, rpm=30)
    provider = make_provider("google", (model,))
    ledger = Ledger()
    now = 1000.0

    for _ in range(13):
        ledger.record_request(provider, model, tokens=1250, now=now)

    availability = ledger.availability(provider, model, now=now)
    assert not availability.ok
    assert "Token-Minutenlimit" in availability.reason
    assert availability.queueable


def test_echter_verbrauch_berichtigt_die_schaetzung() -> None:
    model = make_model("gemma", "google", tpm=16000)
    provider = make_provider("google", (model,))
    ledger = Ledger()
    now = 1000.0

    ledger.record_request(provider, model, tokens=2000, now=now)
    # Tatsaechlich waren es nur 400 — die Differenz muss zurueck.
    ledger.record_success(provider, model, tokens=400, estimated_tokens=2000, now=now)

    assert ledger.bucket(bucket_key(provider, model)).minute_tokens == 400


def test_verbrauch_faellt_nie_unter_null() -> None:
    model = make_model("gemma", "google", tpm=16000)
    provider = make_provider("google", (model,))
    ledger = Ledger()
    ledger.record_success(provider, model, tokens=10, estimated_tokens=9999)
    assert ledger.bucket(bucket_key(provider, model)).minute_tokens == 0


# --------------------------------------------------------------------------
# Tageszaehler fuers Dashboard
# --------------------------------------------------------------------------


def test_tageszaehler_zaehlt_anfragen_und_token() -> None:
    provider, model, _ = build()
    ledger = Ledger()
    now = 1_000_000.0

    ledger.record_request(provider, model, tokens=1100, now=now)
    ledger.record_request(provider, model, tokens=1100, now=now)

    assert ledger.stats.requests == 2
    assert ledger.stats.tokens == 2200


def test_reserve_und_verwerfen_werden_gezaehlt() -> None:
    """Der Wert, auf den es ankommt: ein still dauerhaft ausfallender
    Erstkanal faellt sonst erst auf, wenn auch die Reserve weg ist."""
    ledger = Ledger()
    now = 1_000_000.0

    ledger.note_fallback(now=now)
    ledger.note_fallback(now=now)
    ledger.note_discarded(now=now)

    assert ledger.stats.fallbacks == 2
    assert ledger.stats.discarded == 1


def test_tageszaehler_springt_lokal_um() -> None:
    """Anders als die Kontingentfenster: die Zahlen sind fuer den Menschen
    davor, nicht fuer den Anbieter."""
    import time as _time
    from datetime import datetime, timedelta

    provider, model, _ = build()
    ledger = Ledger()

    heute = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
    morgen = heute + timedelta(days=1)

    ledger.record_request(provider, model, now=heute.timestamp())
    assert ledger.stats.requests == 1

    ledger.record_request(provider, model, now=morgen.timestamp())
    assert ledger.stats.requests == 1, "Zaehler haette zuruecksetzen muessen"
    assert ledger.stats.day_key == morgen.strftime("%Y-%m-%d")
    del _time


async def test_tageszaehler_ueberlebt_den_neustart() -> None:
    provider, model, _ = build()
    gespeichert: dict = {}

    async def save(data):
        gespeichert.clear()
        gespeichert.update(data)

    ledger = Ledger(save=save)
    ledger.record_request(provider, model, tokens=1100)
    ledger.note_fallback()
    await ledger.async_save()

    wieder = Ledger()
    wieder.restore(gespeichert)
    assert wieder.stats.requests == 1
    assert wieder.stats.tokens == 1100
    assert wieder.stats.fallbacks == 1


def test_tageszaehler_zeigt_verbrauch_nicht_vorbuchung() -> None:
    """Der Ledger bucht vor dem Absenden eine Schaetzung und berichtigt sie
    danach. Ohne dieselbe Berichtigung im Tageszaehler zeigt das Dashboard die
    Schaetzung — bei einer Kameraanalyse das Zweieinhalbfache des Verbrauchs.
    """
    provider, model, _ = build()
    ledger = Ledger()
    now = 1_000_000.0

    ledger.record_request(provider, model, tokens=1366, now=now)   # Schaetzung
    assert ledger.stats.tokens == 1366

    ledger.record_success(provider, model, tokens=1172, estimated_tokens=1366, now=now)
    assert ledger.stats.tokens == 1172, "Tageszaehler wurde nicht berichtigt"
    assert ledger.bucket(bucket_key(provider, model)).day_tokens == 1172


def test_tageszaehler_faellt_nie_unter_null() -> None:
    provider, model, _ = build()
    ledger = Ledger()
    ledger.record_success(provider, model, tokens=10, estimated_tokens=9999)
    assert ledger.stats.tokens == 0


# --------------------------------------------------------------------------
# Abgelehnte Schluessel
# --------------------------------------------------------------------------


def test_abgelehnter_schluessel_laeuft_nicht_von_selbst_ab() -> None:
    """Die 429-Sperre verfaellt nach fuenf Minuten — ein abgelehnter
    Schluessel wird dadurch nicht wieder gueltig. Daran haengt der
    Reparatur-Hinweis, der sonst im Takt auf- und zuginge."""
    provider, model, _ = build()
    ledger = Ledger()
    now = 1_000_000.0

    assert not ledger.key_rejected(provider)
    ledger.record_failure(provider, model, fatal=True, auth=True, now=now)
    assert ledger.key_rejected(provider)

    # Sperre abgelaufen, Befund bleibt.
    assert ledger.availability(provider, model, now=now + 1000).ok
    assert ledger.key_rejected(provider)


def test_erfolgreicher_aufruf_loescht_den_befund() -> None:
    provider, model, _ = build()
    ledger = Ledger()
    ledger.record_failure(provider, model, fatal=True, auth=True)
    ledger.record_success(provider, model)
    assert not ledger.key_rejected(provider)


def test_ein_kanal_genuegt_als_befund() -> None:
    """Ein Schluessel gilt fuer alle Modelle eines Anbieters."""
    provider, model_a, model_b = build()
    ledger = Ledger()
    ledger.record_failure(provider, model_a, fatal=True, auth=True)
    assert ledger.key_rejected(provider)


def test_gewoehnlicher_fehlschlag_ist_kein_schluesselproblem() -> None:
    provider, model, _ = build()
    ledger = Ledger()
    for _ in range(5):
        ledger.record_failure(provider, model, reason="Netzhaenger")
    assert not ledger.key_rejected(provider)


async def test_befund_ueberlebt_den_neustart() -> None:
    provider, model, _ = build()
    gespeichert: dict = {}

    async def save(data):
        gespeichert.clear()
        gespeichert.update(data)

    ledger = Ledger(save=save)
    ledger.record_failure(provider, model, fatal=True, auth=True)
    await ledger.async_save()

    wieder = Ledger()
    wieder.restore(gespeichert)
    assert wieder.key_rejected(provider)


def test_ein_erfolg_entlastet_den_ganzen_anbieter() -> None:
    """Ein Schluessel gilt fuer alle Modelle. Nur den benutzten Topf
    freizugeben liesse den Reparatur-Hinweis stehen, obwohl er erledigt ist —
    live am 11.09.2026 genau so aufgetreten."""
    provider, model_a, model_b = build()
    ledger = Ledger()

    ledger.record_failure(provider, model_a, fatal=True, auth=True)
    ledger.record_failure(provider, model_b, fatal=True, auth=True)
    assert ledger.key_rejected(provider)

    # Ein einziges Modell antwortet wieder.
    ledger.record_success(provider, model_a)
    assert not ledger.key_rejected(provider), "der andere Topf blieb als abgelehnt stehen"


# --------------------------------------------------------------------------
# Ausgabendeckel (Phase 4)
# --------------------------------------------------------------------------


def gedeckelt(budget: float = 10.0):
    """Ein Anbieter mit Monatsdeckel und zwei bepreisten Modellen."""
    from custom_components.free_ai_router.ledger import Ledger

    schnell = make_model(
        "klein", "mistral", input_per_mtok=0.10, output_per_mtok=0.10
    )
    gross = make_model(
        "gross", "mistral", input_per_mtok=0.15, output_per_mtok=0.60
    )
    provider = make_provider(
        "mistral", (schnell, gross), monthly_budget_usd=budget
    )
    return Ledger(), provider, schnell, gross


def test_kosten_trennen_eingabe_und_ausgabe() -> None:
    """Bei mistral-small kostet die Ausgabe das Vierfache — ein Mischpreis
    wuerde den Deckel bei langen Antworten zu spaet erreichen."""
    from custom_components.free_ai_router.ledger import cost_usd

    _, _, _, gross = gedeckelt()
    # 1 Mio Eingabe = 0.15, 1 Mio Ausgabe = 0.60
    assert cost_usd(gross, 1_000_000, 0) == pytest.approx(0.15)
    assert cost_usd(gross, 0, 1_000_000) == pytest.approx(0.60)
    assert cost_usd(gross, 1_000_000, 1_000_000) == pytest.approx(0.75)


def test_ohne_preisangabe_keine_kosten() -> None:
    """Der Normalfall der kostenlosen Stufen: ein Zeitfenster, kein Betrag."""
    from custom_components.free_ai_router.ledger import cost_usd

    assert cost_usd(make_model("gratis", "groq"), 10_000, 10_000) == 0.0


def test_vorbuchung_wird_nach_der_antwort_berichtigt() -> None:
    ledger, provider, klein, _ = gedeckelt()
    ledger.record_request(provider, klein, tokens=1000, cost=0.10)
    assert ledger.spend_state(provider).spent_usd == pytest.approx(0.10)

    # Tatsaechlich war es teurer als geschaetzt.
    ledger.record_success(provider, klein, tokens=1500, estimated_tokens=1000,
                          cost=0.13, estimated_cost=0.10)
    assert ledger.spend_state(provider).spent_usd == pytest.approx(0.13)


def test_billiger_als_geschaetzt_gibt_wieder_frei() -> None:
    ledger, provider, klein, _ = gedeckelt()
    ledger.record_request(provider, klein, tokens=1000, cost=0.10)
    ledger.record_success(provider, klein, tokens=400, estimated_tokens=1000,
                          cost=0.04, estimated_cost=0.10)
    assert ledger.spend_state(provider).spent_usd == pytest.approx(0.04)


def test_aufgebrauchtes_budget_sperrt_den_anbieter() -> None:
    ledger, provider, klein, gross = gedeckelt(budget=1.0)
    ledger.record_request(provider, klein, cost=1.0)

    verfuegbar = ledger.availability(provider, klein)
    assert verfuegbar.ok is False
    assert "Monatsbudget" in verfuegbar.reason
    assert verfuegbar.headroom == 0.0
    # Warten hilft nicht: der Deckel faellt erst zum Monatsersten.
    assert verfuegbar.queueable is False
    assert verfuegbar.wait_s > 3600


def test_das_budget_gilt_fuer_alle_modelle_des_anbieters() -> None:
    """Der Deckel haengt am Konto, nicht am Modell — anders als die
    Kontingente, die je Modell oder je Schluessel zaehlen."""
    ledger, provider, klein, gross = gedeckelt(budget=1.0)
    ledger.record_request(provider, klein, cost=1.0)
    assert ledger.availability(provider, gross).ok is False


def test_restbudget_bestimmt_den_headroom() -> None:
    ledger, provider, klein, _ = gedeckelt(budget=10.0)
    ledger.record_request(provider, klein, cost=2.5)
    verfuegbar = ledger.availability(provider, klein)
    assert verfuegbar.ok is True
    assert verfuegbar.headroom == pytest.approx(0.75)


def test_ohne_deckel_bleibt_alles_offen() -> None:
    """Preise allein begrenzen nichts — nur ein gesetzter Deckel tut das."""
    from custom_components.free_ai_router.ledger import Ledger

    model = make_model("klein", "groq", input_per_mtok=0.1, output_per_mtok=0.1)
    provider = make_provider("groq", (model,))
    ledger = Ledger()
    ledger.record_request(provider, model, cost=999.0)
    assert ledger.availability(provider, model).ok is True


def test_monatswechsel_setzt_zurueck() -> None:
    ledger, provider, klein, _ = gedeckelt(budget=1.0)
    januar = datetime(2026, 1, 20, 12, 0, tzinfo=UTC).timestamp()
    februar = datetime(2026, 2, 1, 0, 30, tzinfo=UTC).timestamp()

    ledger.record_request(provider, klein, cost=1.0, now=januar)
    assert ledger.availability(provider, klein, now=januar).ok is False
    assert ledger.availability(provider, klein, now=februar).ok is True
    assert ledger.spend_state(provider, now=februar).spent_usd == 0.0


def test_ausgaben_ueberleben_einen_neustart() -> None:
    """Sonst waere der Deckel nach jedem HA-Neustart wieder voll."""
    from custom_components.free_ai_router.ledger import Ledger

    ledger, provider, klein, _ = gedeckelt(budget=1.0)
    ledger.record_request(provider, klein, cost=0.9)

    danach = Ledger()
    danach.restore(ledger.snapshot())
    assert danach.spend_state(provider).spent_usd == pytest.approx(0.9)
    assert danach.availability(provider, klein).headroom == pytest.approx(0.1)

