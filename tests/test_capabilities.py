"""Faehigkeitserkennung gegen einen lokalen Testserver.

Das Modul aus Schritt 0 — dieselbe Messung laeuft spaeter im Config Flow und
in Phase 3 unter Cron. Geprueft wird vor allem, dass ein Fehlschlag ein
Messergebnis bleibt und nicht als Ausnahme nach oben durchschlaegt.
"""

from __future__ import annotations

import json
import struct
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
    TEST_COLOR_NAME_DE,
    looks_like_test_color,
    solid_png,
)


class Endpunkt:
    """Antwortet je nach dem, was die Anfrage enthaelt."""

    def __init__(self) -> None:
        self.status = 200
        self.kann_bilder = True
        self.sieht_richtig = True
        self.kann_schema = True
        self.kann_werkzeuge = True
        self.gesehene_bilder: list[int] = []

    async def chat(self, request: web.Request) -> web.Response:
        payload = await request.json()
        if self.status >= 400:
            return web.json_response({"error": {"message": "kaputt"}}, status=self.status)

        text = "bereit"
        message: dict[str, Any] = {"role": "assistant"}

        if self._hat_bild(payload):
            if not self.kann_bilder:
                return web.json_response(
                    {"error": {"message": "model does not support image input"}}, status=400
                )
            text = TEST_COLOR_NAME_DE if self.sieht_richtig else "blau"

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

    def _hat_bild(self, payload: dict[str, Any]) -> bool:
        for message in payload.get("messages", []):
            content = message.get("content")
            if isinstance(content, list):
                for part in content:
                    if part.get("type") == "image_url":
                        self.gesehene_bilder.append(len(part["image_url"]["url"]))
                        return True
        return False

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
# Testbild
# --------------------------------------------------------------------------


def test_testbild_ist_ein_gueltiges_png() -> None:
    png = solid_png()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    breite, hoehe = struct.unpack(">II", png[16:24])
    assert breite == hoehe == 64


def test_farberkennung_ist_nachsichtig_mit_der_sprache() -> None:
    assert looks_like_test_color("Das Bild ist rot.")
    assert looks_like_test_color("RED")
    assert not looks_like_test_color("blau")


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


async def test_bild_wird_wirklich_mitgeschickt(umgebung) -> None:
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
    assert "Farbe falsch" in probe.vision.detail


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
    # gefahren — sie blieben unbekannt statt drei weitere Fehlschlaege zu
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
    # Reset bei 120 s: als Tagesfenster gewertet, nicht als Minutenfenster.
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
    ok, meldung = await quick_key_check(
        umgebung["session"], umgebung["provider"], "key"
    )
    assert ok
    assert "antwortet" in meldung

    umgebung["endpunkt"].status = 401
    ok, meldung = await quick_key_check(
        umgebung["session"], umgebung["provider"], "key"
    )
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
