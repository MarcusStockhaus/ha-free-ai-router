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

from custom_components.free_ai_router.capabilities import (
    ALL_CHECKS,
    CHECK_LIVENESS,
    probe_model,
    probe_provider,
    quick_key_check,
)
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
        self.kann_bilder = True
        self.sieht_richtig = True
        self.kann_schema = True
        self.kann_werkzeuge = True
        self.gesehene_bilder: list[tuple[str, str]] = []
        # Simuliert ein Modell, das nur manchmal wirklich hinsieht.
        self.nur_erste_runde_richtig = False

    async def chat(self, request: web.Request) -> web.Response:
        payload = await request.json()
        if self.status >= 400:
            return web.json_response({"error": {"message": "kaputt"}}, status=self.status)

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
    ok, meldung = await quick_key_check(umgebung["session"], umgebung["provider"], "key")
    assert ok
    assert "antwortet" in meldung

    umgebung["endpunkt"].status = 401
    ok, meldung = await quick_key_check(umgebung["session"], umgebung["provider"], "key")
    assert not ok
    assert "401" in meldung


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
