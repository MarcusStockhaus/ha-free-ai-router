"""Faehigkeitserkennung gegen einen lokalen Testserver.

Das Modul aus Schritt 0 — dieselbe Messung laeuft spaeter im Config Flow und
in Phase 3 unter Cron. Geprueft wird vor allem, dass ein Fehlschlag ein
Messergebnis bleibt und nicht als Ausnahme nach oben durchschlaegt.

Der Testendpunkt *dekodiert das PNG wirklich* und antwortet mit den Farben,
die darin stehen. Damit prueft der Test nicht nur den Ablauf, sondern auch,
dass das Bild unbeschaedigt beim Anbieter ankommt.
"""

from __future__ import annotations

import base64
import json
import random
import struct
import zlib
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import make_model, make_provider

from custom_components.free_ai_router.adapters import ProviderError
from custom_components.free_ai_router.capabilities import (
    ALL_CHECKS,
    CHECK_LIVENESS,
    TOT_NACHPRUEFEN_S,
    CheckResult,
    ModelProbe,
    _nicht_messbar,
    bericht_zeilen,
    describe_changes,
    faellige_modelle,
    ist_voruebergehend,
    merge_overrides,
    probe_model,
    probe_provider,
    quick_key_check,
    should_discard,
    uebernehmen,
)
from custom_components.free_ai_router.ratelimit import RateLimitInfo
from custom_components.free_ai_router.testimage import (
    PALETTE,
    TEST_IMAGE_SIZE,
    make_challenge,
    two_tone_png,
)

# --------------------------------------------------------------------------
# PNG zurueckuebersetzen — die Gegenprobe zum Encoder
# --------------------------------------------------------------------------


def png_farben(daten: bytes) -> tuple[str, str]:
    """Lies die beiden Farbnamen aus einem two_tone_png zurueck."""
    assert daten[:8] == b"\x89PNG\r\n\x1a\n", "kein PNG"

    breite = hoehe = 0
    idat = b""
    pos = 8
    while pos < len(daten):
        (laenge,) = struct.unpack(">I", daten[pos : pos + 4])
        tag = daten[pos + 4 : pos + 8]
        payload = daten[pos + 8 : pos + 8 + laenge]
        if tag == b"IHDR":
            breite, hoehe = struct.unpack(">II", payload[:8])
        elif tag == b"IDAT":
            idat += payload
        pos += 12 + laenge

    roh = zlib.decompress(idat)
    zeilenlaenge = 1 + breite * 3  # ein Filter-Byte je Zeile

    def pixel(zeile: int) -> tuple[int, int, int]:
        start = zeile * zeilenlaenge + 1
        return tuple(roh[start : start + 3])  # type: ignore[return-value]

    def name(rgb: tuple[int, int, int]) -> str:
        for farbname, (farbwert, _woerter) in PALETTE.items():
            if farbwert == rgb:
                return farbname
        raise AssertionError(f"unbekannte Farbe {rgb}")

    return name(pixel(0)), name(pixel(hoehe - 1))


def test_decoder_findet_zurueck() -> None:
    """Erst die Gegenprobe selbst pruefen, sonst testet sie nichts."""
    bild = two_tone_png(PALETTE["lila"][0], PALETTE["gelb"][0])
    assert png_farben(bild) == ("lila", "gelb")


# --------------------------------------------------------------------------
# Testendpunkt
# --------------------------------------------------------------------------


class Endpunkt:
    """Antwortet je nach dem, was die Anfrage enthaelt."""

    def __init__(self) -> None:
        self.status = 200
        self.headers: dict[str, str] = {}
        self.kann_bilder = True
        self.sieht_richtig = True
        self.kann_schema = True
        self.kann_werkzeuge = True
        self.gesehene_bilder: list[tuple[str, str]] = []
        # Simuliert ein Modell, das nur manchmal wirklich hinsieht.
        self.nur_erste_runde_richtig = False
        # Simuliert einen Anbieter, der mitten in der Messung wegbricht.
        self.anfragen = 0
        self.stoerung_ab: int | None = None
        self.stoerung_status = 500

    async def chat(self, request: web.Request) -> web.Response:
        payload = await request.json()
        self.anfragen += 1
        if self.status >= 400:
            return web.json_response(
                {"error": {"message": "kaputt"}}, status=self.status, headers=self.headers
            )
        if self.stoerung_ab is not None and self.anfragen >= self.stoerung_ab:
            return web.json_response(
                {"error": {"message": "Internal error encountered."}},
                status=self.stoerung_status,
            )

        text = "bereit"
        message: dict[str, Any] = {"role": "assistant"}

        bild = self._bild(payload)
        if bild is not None:
            if not self.kann_bilder:
                return web.json_response(
                    {"error": {"message": "model does not support image input"}}, status=400
                )
            # Ein blindes Modell raet: dann steht hier eine feste
            # Verlegenheitsantwort, die praktisch nie zufaellig passt.
            richtig = self.sieht_richtig and not (
                self.nur_erste_runde_richtig and len(self.gesehene_bilder) > 1
            )
            if richtig:
                text = f"{bild[0]}, {bild[1]}"
            else:
                # Die Antwort eines blinden Modells wird gezielt aus Farben
                # gebaut, die *nicht* im Bild sind. Eine feste Antwort wie
                # "rot, blau" traf mit rund fuenf Prozent zufaellig zu und hat
                # diesen Test sprunghaft gemacht — genau die Ratequote, wegen
                # der die Pruefung ueberhaupt zwei Runden faehrt.
                daneben = [name for name in PALETTE if name not in bild][:2]
                text = f"{daneben[0]}, {daneben[1]}"

        elif payload.get("response_format"):
            if not self.kann_schema:
                return web.json_response(
                    {"error": {"message": "response_format unsupported"}}, status=400
                )
            text = json.dumps({"farbe": "gruen", "anzahl": 3})

        elif payload.get("tools"):
            if self.kann_werkzeuge:
                message["tool_calls"] = [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "licht_schalten",
                            "arguments": json.dumps({"raum": "Wohnzimmer", "an": True}),
                        },
                    }
                ]
                text = ""
            else:
                text = "Ich kann keine Werkzeuge benutzen."

        message["content"] = text
        return web.json_response(
            {
                "model": payload["model"],
                "choices": [{"message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
            headers={
                "x-ratelimit-limit-requests": "1000",
                "x-ratelimit-remaining-requests": "999",
                "x-ratelimit-reset-requests": "2m0s",
            },
        )

    def _bild(self, payload: dict[str, Any]) -> tuple[str, str] | None:
        """Finde das Bild und lies seine Farben — wie ein sehendes Modell."""
        for message in payload.get("messages", []):
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if part.get("type") != "image_url":
                    continue
                url = part["image_url"]["url"]
                assert url.startswith("data:image/png;base64,"), url[:40]
                farben = png_farben(base64.b64decode(url.split(",", 1)[1]))
                self.gesehene_bilder.append(farben)
                return farben
        return None

    async def models(self, _request: web.Request) -> web.Response:
        return web.json_response({"data": [{"id": "modell-a"}]})


@pytest.fixture
async def umgebung():
    endpunkt = Endpunkt()
    app = web.Application()
    app.router.add_post("/v1/chat/completions", endpunkt.chat)
    app.router.add_get("/v1/models", endpunkt.models)

    server = TestServer(app)
    await server.start_server()
    base = str(server.make_url("/v1")).rstrip("/")

    model = make_model("modell-a", "test", profiles=("schnell", "vision"), vision=True)
    provider = make_provider("test", (model,), base_url=base)

    async with aiohttp.ClientSession() as session:
        yield {"endpunkt": endpunkt, "provider": provider, "model": model, "session": session}

    await server.close()


# --------------------------------------------------------------------------
# Die Vision-Aufgabe
# --------------------------------------------------------------------------


def test_testbild_ist_ein_gueltiges_png() -> None:
    challenge = make_challenge()
    assert challenge.image[:8] == b"\x89PNG\r\n\x1a\n"
    breite, hoehe = struct.unpack(">II", challenge.image[16:24])
    assert breite == hoehe == TEST_IMAGE_SIZE
    assert png_farben(challenge.image) == challenge.expected


def test_zwei_verschiedene_farben() -> None:
    for seed in range(50):
        challenge = make_challenge(random.Random(seed))
        assert challenge.expected[0] != challenge.expected[1]


def test_beide_farben_noetig_eine_genuegt_nicht() -> None:
    """Der Kern der Haertung: eine Farbe zu treffen ist Zufall, beide nicht."""
    challenge = make_challenge(random.Random(1))
    oben, unten = challenge.expected

    assert challenge.solved(f"{oben}, {unten}")
    assert challenge.solved(f"Oben {oben} und unten {unten}.")
    assert not challenge.solved(oben)
    assert not challenge.solved(unten)
    assert not challenge.solved("keine Ahnung")


def test_sprachvarianten_werden_akzeptiert() -> None:
    challenge = make_challenge(random.Random(7))
    englisch = ", ".join(PALETTE[name][1][1] for name in challenge.expected)
    assert challenge.solved(englisch)


def test_die_halbe_palette_aufzaehlen_gilt_nicht() -> None:
    """Wer den Farbraum abdeckt, statt hinzusehen, besteht nicht.

    Der Prompt verlangt zwei Woerter. Eine Antwort, die fuenf Farben nennt,
    enthaelt die beiden richtigen fast zwangslaeufig — das ist keine Messung,
    sondern Abdeckung.
    """
    challenge = make_challenge(random.Random(7))
    oben, unten = challenge.expected
    fremd = [name for name in PALETTE if name not in challenge.expected][:3]

    assert challenge.solved(f"{oben}, {unten}") is True
    assert challenge.solved(f"{oben}, {unten}, {', '.join(fremd)}") is False


def test_falsch_genannte_farben_werden_benannt() -> None:
    challenge = make_challenge(random.Random(3))
    falsche = next(name for name in PALETTE if name not in challenge.expected)
    assert challenge.wrong_colors(falsche) == [falsche]


# --------------------------------------------------------------------------
# Messung
# --------------------------------------------------------------------------


async def test_vollstaendige_messung(umgebung) -> None:
    probe = await probe_model(
        umgebung["session"], umgebung["provider"], "key", umgebung["model"]
    )

    assert probe.alive
    assert probe.vision.ok
    assert probe.vision_looked
    assert probe.structured_output.ok
    assert probe.tools.ok
    assert probe.latency_total_s is not None
    assert probe.measured_capabilities() == {
        "vision": True,
        "tools": True,
        "structured_output": True,
    }


async def test_bild_kommt_unbeschaedigt_an(umgebung) -> None:
    await probe_model(umgebung["session"], umgebung["provider"], "key", umgebung["model"])
    assert umgebung["endpunkt"].gesehene_bilder, "kein Bild im Request angekommen"


async def test_angenommen_aber_nicht_angesehen_gilt_als_fehlschlag(umgebung) -> None:
    """Ein Modell, das das Bild annimmt und dann raet, ist als Vision-Kanal
    wertlos — das muss die Messung sehen."""
    umgebung["endpunkt"].sieht_richtig = False
    probe = await probe_model(
        umgebung["session"], umgebung["provider"], "key", umgebung["model"]
    )

    assert probe.alive
    assert probe.vision.ok is False
    assert probe.vision_looked is False
    assert "erwartet" in probe.vision.detail


async def test_abgelehntes_bild(umgebung) -> None:
    umgebung["endpunkt"].kann_bilder = False
    probe = await probe_model(
        umgebung["session"], umgebung["provider"], "key", umgebung["model"]
    )
    assert probe.alive
    assert probe.vision.ok is False
    assert probe.measured_capabilities()["vision"] is False


async def test_fehlendes_werkzeug_wird_erkannt(umgebung) -> None:
    umgebung["endpunkt"].kann_werkzeuge = False
    probe = await probe_model(
        umgebung["session"], umgebung["provider"], "key", umgebung["model"]
    )
    assert probe.tools.ok is False


async def test_toter_endpunkt_ist_ein_messergebnis_keine_ausnahme(umgebung) -> None:
    umgebung["endpunkt"].status = 500
    probe = await probe_model(
        umgebung["session"], umgebung["provider"], "key", umgebung["model"]
    )

    assert not probe.alive
    assert probe.status == 500
    assert probe.liveness.ok is False
    # Nach einem toten Endpunkt werden die teuren Tests gar nicht erst
    # gefahren — sie bleiben unbekannt statt drei weitere Fehlschlaege zu
    # produzieren. Unbekannt heisst nicht "kann es nicht".
    assert probe.vision.ok is None
    assert probe.measured_capabilities() == {}


async def test_stoerung_nach_dem_lebenszeichen_bleibt_unbekannt(umgebung) -> None:
    """Ein 500 mitten in der Messung sagt nichts ueber das Modell.

    Genau das ist am 11.09.2026 passiert: Googles Gemma antwortete waehrend
    des ersten Feed-Laufs zeitweise mit 500 und Zeitueberschreitungen — und
    wurde daraufhin fuer alle Installationen als blind und werkzeuglos
    veroeffentlicht. Ein gestoerter Anbieter hat nicht gemessen, und was nicht
    gemessen wurde, ueberschreibt nichts.
    """
    umgebung["endpunkt"].stoerung_ab = 2  # das Lebenszeichen geht noch durch
    probe = await probe_model(
        umgebung["session"], umgebung["provider"], "key", umgebung["model"]
    )

    assert probe.alive
    assert probe.structured_output.ok is None
    assert probe.vision.ok is None
    assert probe.tools.ok is None
    assert probe.measured_capabilities() == {}


async def test_ratenlimit_waehrend_der_messung_bleibt_unbekannt(umgebung) -> None:
    umgebung["endpunkt"].stoerung_ab = 2
    umgebung["endpunkt"].stoerung_status = 429
    probe = await probe_model(
        umgebung["session"], umgebung["provider"], "key", umgebung["model"]
    )
    assert probe.alive
    assert probe.measured_capabilities() == {}


async def test_abgebrochene_zweite_bildrunde_ist_kein_bestanden(umgebung) -> None:
    """Eine bestandene Runde allein beweist nichts.

    Sie laesst sich mit rund fuenf Prozent erraten — genau deshalb laeuft die
    Bildpruefung ueber zwei Runden mit je neuen Farben. Frueher zaehlte bei
    einem Abbruch in Runde zwei "was bis hierhin stimmte", und damit war die
    Ratequote wieder da. Im Feed vom 11.09.2026 stand deshalb `vision: true`
    fuer ein Modell, das die Pruefung in drei anderen Laeufen nicht bestand.
    """
    # Reihenfolge der Aufrufe: Lebendigkeit, Schema, Bild-Runde 1, Runde 2.
    umgebung["endpunkt"].stoerung_ab = 4
    probe = await probe_model(
        umgebung["session"], umgebung["provider"], "key", umgebung["model"]
    )

    assert probe.alive
    assert probe.vision.ok is None
    assert "vision" not in probe.measured_capabilities()


async def test_eine_gelesene_ablehnung_bleibt_ein_echtes_nein(umgebung) -> None:
    """Die Gegenprobe: ein 400 ist sehr wohl eine Aussage ueber das Modell.

    Der Endpunkt hat die Anfrage gelesen und zurueckgewiesen — so meldet
    Googles Gemma ein unbekanntes ``responseSchema``. Wuerde auch das als
    "unbekannt" durchgehen, koennte die Messung ueberhaupt nie ein Nein
    feststellen.
    """
    umgebung["endpunkt"].kann_schema = False
    probe = await probe_model(
        umgebung["session"], umgebung["provider"], "key", umgebung["model"]
    )
    assert probe.structured_output.ok is False
    assert probe.measured_capabilities()["structured_output"] is False


async def test_ungeprueftes_bleibt_unbekannt(umgebung) -> None:
    probe = await probe_model(
        umgebung["session"],
        umgebung["provider"],
        "key",
        umgebung["model"],
        checks=(CHECK_LIVENESS,),
    )
    assert probe.alive
    assert probe.vision.ok is None
    assert probe.tools.ok is None
    assert probe.measured_capabilities() == {}


async def test_limits_aus_den_headern(umgebung) -> None:
    probe = await probe_model(
        umgebung["session"], umgebung["provider"], "key", umgebung["model"]
    )
    # Reset bei 120 s: als Minutenfenster gewertet.
    assert probe.measured_limits() == {"rpm": 1000}


async def test_anbieter_messung_und_modellabgleich(umgebung) -> None:
    veraltet = make_model("gibts-nicht-mehr", "test", profiles=("schnell",))
    provider = make_provider(
        "test", (umgebung["model"], veraltet), base_url=umgebung["provider"].base_url
    )

    ergebnis = await probe_provider(
        umgebung["session"], provider, "key", discover=True, concurrency=1
    )

    assert ergebnis.key_valid
    assert ergebnis.discovered_models == ["modell-a"]
    assert ergebnis.stale_registry_entries == ["gibts-nicht-mehr"]


async def test_schnelltest_fuer_die_key_eingabe(umgebung) -> None:
    ok, meldung, art = await quick_key_check(umgebung["session"], umgebung["provider"], "key")
    assert ok
    assert "antwortet" in meldung
    assert art == ""

    umgebung["endpunkt"].status = 401
    ok, meldung, art = await quick_key_check(umgebung["session"], umgebung["provider"], "key")
    assert not ok
    assert "401" in meldung
    assert art == "auth"


async def test_schnelltest_erkennt_fehlendes_abonnement(umgebung) -> None:
    """x-ratelimit-limit-req-minute: 0 ist kein Limit, sondern kein Abo (Mistral)."""
    umgebung["endpunkt"].status = 429
    umgebung["endpunkt"].headers = {"x-ratelimit-limit-req-minute": "0"}
    ok, _meldung, art = await quick_key_check(umgebung["session"], umgebung["provider"], "key")
    assert not ok
    assert art == "no_subscription"


async def test_schnelltest_erkennt_echtes_limit(umgebung) -> None:
    umgebung["endpunkt"].status = 429
    ok, _meldung, art = await quick_key_check(umgebung["session"], umgebung["provider"], "key")
    assert not ok
    assert art == "rate_limited"


async def test_schnelltest_erkennt_serverfehler(umgebung) -> None:
    umgebung["endpunkt"].status = 503
    ok, _meldung, art = await quick_key_check(umgebung["session"], umgebung["provider"], "key")
    assert not ok
    assert art == "unreachable"


async def test_fortschritt_wird_gemeldet(umgebung) -> None:
    schritte: list[tuple[int, int, str]] = []
    await probe_provider(
        umgebung["session"],
        umgebung["provider"],
        "key",
        checks=ALL_CHECKS,
        on_progress=lambda done, total, label: schritte.append((done, total, label)),
    )
    assert schritte == [(1, 1, "Test / modell-a")]


async def test_vision_braucht_zwei_treffer_in_folge(umgebung) -> None:
    """Ein Modell, das nur manchmal richtig raet, darf nicht durchkommen.

    Am 11.09.2026 bestand ministral-8b eine Runde und antwortete in der
    naechsten "Rot, Blau" — die beiden haeufigsten Verlegenheitsfarben.
    """
    endpunkt = umgebung["endpunkt"]
    endpunkt.sieht_richtig = True
    endpunkt.nur_erste_runde_richtig = True

    probe = await probe_model(
        umgebung["session"], umgebung["provider"], "key", umgebung["model"]
    )

    assert probe.vision.ok is False
    assert "Runde 2 von 2" in probe.vision.detail
    assert len(endpunkt.gesehene_bilder) == 2, "zweite Runde wurde nicht gefahren"


async def test_zwei_richtige_runden_zaehlen_als_bestanden(umgebung) -> None:
    probe = await probe_model(
        umgebung["session"], umgebung["provider"], "key", umgebung["model"]
    )
    assert probe.vision.ok is True
    assert len(umgebung["endpunkt"].gesehene_bilder) == 2


async def test_blindes_modell_kostet_nur_einen_aufruf(umgebung) -> None:
    """Wer die erste Runde verfehlt, wird nicht noch einmal gefragt."""
    umgebung["endpunkt"].sieht_richtig = False
    probe = await probe_model(
        umgebung["session"], umgebung["provider"], "key", umgebung["model"]
    )
    assert probe.vision.ok is False
    assert len(umgebung["endpunkt"].gesehene_bilder) == 1


# --------------------------------------------------------------------------
# Neu vermessen: was eine zweite Messung mit der ersten macht
# --------------------------------------------------------------------------


def test_sparsame_messung_loescht_keine_faehigkeiten() -> None:
    """Der Kern des Dienstes.

    Ein Lauf mit `nur_lebendigkeit` prueft Bilder, Schemata und Werkzeuge gar
    nicht. Seine leeren Felder duerfen die frueheren Befunde nicht wegraeumen —
    sonst waere "nicht gemessen" wieder dasselbe wie "kann es nicht".
    """
    vorher = {
        "alive": True,
        "capabilities": {"vision": True, "tools": False},
        "limits": {"rpd": 500},
    }
    nachher = {"alive": True, "capabilities": {}, "limits": {"rpm": 15}}

    ergebnis = merge_overrides(vorher, nachher)

    assert ergebnis["capabilities"] == {"vision": True, "tools": False}
    assert ergebnis["limits"] == {"rpd": 500, "rpm": 15}
    assert ergebnis["alive"] is True


def test_neue_messung_schlaegt_die_alte() -> None:
    vorher = {"capabilities": {"vision": False}}
    nachher = {"capabilities": {"vision": True}}
    assert merge_overrides(vorher, nachher)["capabilities"] == {"vision": True}


def test_aenderungen_werden_benannt() -> None:
    vorher = {"m/a": {"alive": True, "capabilities": {"vision": False}}}
    nachher = {"m/a": {"alive": True, "capabilities": {"vision": True, "tools": True}}}

    zeilen = describe_changes(vorher, nachher)

    assert "a: vision nein -> ja" in zeilen
    # tools war vorher unbekannt, nicht false — das soll die Meldung sagen.
    assert "a: tools unbekannt -> ja" in zeilen


def test_ohne_aenderung_keine_meldung() -> None:
    stand = {"m/a": {"alive": True, "capabilities": {"vision": True}}}
    assert describe_changes(stand, stand) == []


def test_erstmessung_meldet_keine_erreichbarkeitsaenderung() -> None:
    """Beim ersten Mal gibt es kein "vorher" — jede Meldung waere Rauschen."""
    nachher = {"m/a": {"alive": False, "capabilities": {}}}
    assert describe_changes({}, nachher) == []


def test_ein_toter_lauf_wird_verworfen() -> None:
    """Die Leitung ist tot, nicht der Anbieter — sonst schaltet sich die
    Installation bei einem Netzausfall selbst ab."""
    vorher = {"m/a": {"alive": True}, "m/b": {"alive": True}}
    assert should_discard(vorher, lebendig=0) is True
    assert should_discard(vorher, lebendig=1) is False


def test_wer_vorher_schon_tot_war_darf_tot_bleiben() -> None:
    vorher = {"m/a": {"alive": False}}
    assert should_discard(vorher, lebendig=0) is False


def test_erste_messung_wird_nie_verworfen() -> None:
    assert should_discard({}, lebendig=0) is False



# --------------------------------------------------------------------------
# Messen im Hintergrund: vorübergehend oder definitiv, fällig oder nicht
# --------------------------------------------------------------------------


def _probe(model_id: str, *, alive: bool, status: int | None = None, limit: int | None = None,
           vision: bool | None = None) -> ModelProbe:
    probe = ModelProbe(provider_id="p", model_id=model_id, alive=alive, status=status,
                       rate_limit=RateLimitInfo(limit_requests=limit), checked_at=1000.0)
    if alive:
        probe.vision = CheckResult(vision)
        probe.latency_total_s = 0.5
    return probe


@pytest.mark.parametrize(
    ("status", "limit", "voruebergehend"),
    [
        (503, None, True),   # gemini-3.8-flash, "high demand", live am 24.09.2026
        (None, None, True),  # Zeitueberschreitung, nemotron-3.5-lightning
        (429, 30, True),     # echtes Ratenlimit
        (429, 0, False),     # Mistral ohne API-Abo: kein Kontingent
        (200, None, True),   # vom Vermittler eingepackter Fehler
        (404, None, False),
        (400, None, False),
        (402, None, False),
    ],
)
def test_lebendpruefung_unterscheidet_stoerung_von_befund(status, limit, voruebergehend) -> None:
    probe = _probe("m", alive=False, status=status, limit=limit)
    assert ist_voruebergehend(probe, schluessel_gueltig=True) is voruebergehend


def test_401_bei_gueltigem_schluessel_ist_verzoegerung() -> None:
    """Live am 17.09.2026: Mistral lehnte denselben Schluessel fuer zwei Modelle
    ab, den es fuer ein drittes annahm — eine Woche spaeter fuer alle."""
    probe = _probe("m", alive=False, status=401)
    assert ist_voruebergehend(probe, schluessel_gueltig=True)
    assert ist_voruebergehend(probe, schluessel_gueltig=False)


def test_403_ist_nur_bei_gueltigem_schluessel_ein_befund() -> None:
    probe = _probe("m", alive=False, status=403)
    assert not ist_voruebergehend(probe, schluessel_gueltig=True)
    assert ist_voruebergehend(probe, schluessel_gueltig=False)


def test_eingepackter_fehler_ist_kein_befund_ueber_bilder() -> None:
    """OpenRouter meldete die ausgelastete Nvidia-Hardware mit HTTP 200."""
    fehler = ProviderError("Upstream error from Nvidia: ResourceExhausted", status=200)
    assert _nicht_messbar(fehler)
    assert not _nicht_messbar(ProviderError("image input not supported", status=400))


def test_voruebergehende_stoerung_ueberschreibt_nichts() -> None:
    vorher = {"p/a": {"alive": True, "capabilities": {"vision": True}, "checked_at": 1.0}}
    nachher = uebernehmen(vorher, [_probe("a", alive=False, status=503),
                                   _probe("b", alive=True)])
    assert nachher["p/a"] == vorher["p/a"]
    assert "p/b" in nachher


def test_voruebergehend_gestoertes_modell_bleibt_offen() -> None:
    nachher = uebernehmen({}, [_probe("a", alive=False, status=None), _probe("b", alive=True)])
    assert "p/a" not in nachher


def test_definitiv_totes_modell_wird_mit_grund_gespeichert() -> None:
    nachher = uebernehmen({}, [_probe("a", alive=False, status=429, limit=0),
                               _probe("b", alive=True)])
    assert nachher["p/a"]["alive"] is False
    assert nachher["p/a"]["grund"] == "kein Kontingent für dieses Konto"


def test_tote_leitung_schaltet_nichts_ab() -> None:
    """Die alte Notbremse (should_discard), jetzt je Modell: ein Netzausfall
    liefert nur voruebergehende Fehler und ueberschreibt deshalb nichts."""
    vorher = {"p/a": {"alive": True}, "p/b": {"alive": True}}
    nachher = uebernehmen(vorher, [_probe("a", alive=False), _probe("b", alive=False)])
    assert nachher == vorher


def test_neue_messung_behaelt_ungemessene_faehigkeiten() -> None:
    vorher = {"p/a": {"alive": True, "capabilities": {"vision": True, "tools": True}}}
    nachher = uebernehmen(vorher, [_probe("a", alive=True, vision=None)])
    assert nachher["p/a"]["capabilities"] == {"vision": True, "tools": True}


def test_faellig_ist_nur_was_offen_oder_lange_tot_ist() -> None:
    modelle = tuple(make_model(mid, "p") for mid in ("neu", "lebt", "tot", "alt_tot", "ohne_grund"))
    provider = make_provider("p", modelle)
    jetzt = 1_000_000.0
    gespeichert = {
        "p/lebt": {"alive": True, "checked_at": 0.0},
        "p/tot": {"alive": False, "grund": "HTTP 404", "checked_at": jetzt - 3600},
        "p/alt_tot": {"alive": False, "grund": "HTTP 404",
                      "checked_at": jetzt - TOT_NACHPRUEFEN_S - 1},
        # Gemessen, als ein 503 noch als tot galt: sofort nachpruefen.
        "p/ohne_grund": {"alive": False, "checked_at": jetzt},
    }
    faellig = [model.id for model in faellige_modelle(provider, gespeichert, jetzt)]
    assert faellig == ["neu", "alt_tot", "ohne_grund"]


def test_bericht_ohne_rohe_fehlertexte() -> None:
    zeilen = bericht_zeilen([
        _probe("gut", alive=True, vision=True),
        _probe("gestoert", alive=False, status=503),
        _probe("ohne_abo", alive=False, status=429, limit=0),
    ])
    assert zeilen[0] == "- **gut** — Bilder · 0.5 s"
    assert "später erneut versucht" in zeilen[1]
    assert zeilen[2] == "- **ohne_abo** — nicht nutzbar: kein Kontingent für dieses Konto"
    assert not any("{" in zeile for zeile in zeilen)


def test_altes_tot_ohne_grund_wird_bei_neuer_stoerung_wieder_offen() -> None:
    """Live am 24.09.2026: gemini-3.8-flash war mit einem 503 als tot
    gespeichert worden, bevor es den Grund gab. Bleibt die Nachmessung wieder
    nur gestoert, darf das alte "tot" nicht stehen bleiben — es war nie ein
    Befund. Ein "tot" mit Grund dagegen bleibt."""
    vorher = {
        "p/alt": {"alive": False, "checked_at": 1.0},
        "p/befund": {"alive": False, "grund": "Modell gibt es nicht (mehr)", "checked_at": 1.0},
    }
    nachher = uebernehmen(vorher, [_probe("alt", alive=False, status=503),
                                   _probe("befund", alive=False, status=503)])
    assert "p/alt" not in nachher
    assert nachher["p/befund"] == vorher["p/befund"]


def test_sparsamer_lauf_behauptet_nichts_ueber_faehigkeiten() -> None:
    """Live am 24.09.2026: nach einem Lauf nur mit Lebendpruefung stand
    "nur Text" im Bericht — fuer ein Modell, das Bilder kann."""
    zeilen = bericht_zeilen([_probe("m", alive=True, vision=None)])
    assert zeilen == ["- **m** — erreichbar · 0.5 s"]


def test_grund_ohne_doppelte_klammern() -> None:
    zeilen = bericht_zeilen([_probe("a", alive=True), _probe("b", alive=False, status=None),
                             _probe("c", alive=False, status=503)])
    assert "((" not in "".join(zeilen) and "))" not in "".join(zeilen)
