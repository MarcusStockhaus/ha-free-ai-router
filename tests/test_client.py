"""Ende-zu-Ende ohne Internet: Adapter, Ledger und Reserve gegen einen
lokalen Testserver.

Damit lassen sich die Akzeptanzkriterien pruefen, die sonst einen echten
Anbieterausfall braeuchten: simulierter Ausfall des ersten Anbieters,
RPM-Anschlag, sauberes Verwerfen.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import make_model, make_provider

from custom_components.free_ai_router.client import NoChannelAvailable, RouterClient
from custom_components.free_ai_router.ledger import Ledger, bucket_key
from custom_components.free_ai_router.router import Channel, Requirements


class FakeProvider:
    """Ein Anbieter, den wir zum Ausfallen bringen koennen."""

    def __init__(self, name: str, *, style: str = "openai_compatible") -> None:
        self.name = name
        self.style = style
        self.status = 200
        self.headers: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []
        self.reject_json_schema = False
        self.text = "bereit"
        self.tool_call: dict[str, Any] | None = None
        self.fehlertext = ""

    async def handle(self, request: web.Request) -> web.Response:
        payload = await request.json()
        self.calls.append(payload)

        if self.status >= 400:
            return web.json_response(
                {"error": {"message": self.fehlertext or f"{self.name} faellt aus"}},
                status=self.status,
                headers=self.headers,
            )

        if self.style == "google":
            return self._google(payload)
        return self._openai(payload)

    def _openai(self, payload: dict[str, Any]) -> web.Response:
        response_format = payload.get("response_format") or {}
        if self.reject_json_schema and response_format.get("type") == "json_schema":
            return web.json_response(
                {"error": {"message": "response_format json_schema not supported"}},
                status=400,
            )
        message: dict[str, Any] = {"role": "assistant", "content": self.text}
        if self.tool_call:
            message["tool_calls"] = [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": self.tool_call["name"],
                        "arguments": json.dumps(self.tool_call["arguments"]),
                    },
                }
            ]
        return web.json_response(
            {
                "model": payload["model"],
                "choices": [{"message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 3},
            },
            headers=self.headers,
        )

    def _google(self, payload: dict[str, Any]) -> web.Response:
        return web.json_response(
            {
                "candidates": [
                    {
                        "content": {"parts": [{"text": self.text}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 3},
            },
            headers=self.headers,
        )


@pytest.fixture
async def welt():
    """Zwei Anbieter auf einem lokalen Server: Erstwahl und Reserve."""
    erst = FakeProvider("erst")
    reserve = FakeProvider("reserve")

    app = web.Application()
    app.router.add_post("/erst/chat/completions", erst.handle)
    app.router.add_post("/reserve/chat/completions", reserve.handle)

    server = TestServer(app)
    await server.start_server()
    base = str(server.make_url("")).rstrip("/")

    # Provider direkt bauen: die Allowlist verbietet 127.0.0.1 zu Recht,
    # ihre Wirkung wird in test_registry.py geprueft.
    model_a = make_model("modell-a", "erst", profiles=("schnell", "reasoning"), rpm=2)
    model_b = make_model("modell-b", "reserve", profiles=("schnell", "reasoning"), rpm=5)
    provider_a = make_provider("erst", (model_a,), base_url=f"{base}/erst")
    provider_b = make_provider("reserve", (model_b,), base_url=f"{base}/reserve")

    channels = [Channel(provider_a, model_a), Channel(provider_b, model_b)]
    ledger = Ledger()

    import aiohttp

    async with aiohttp.ClientSession() as session:
        client = RouterClient(
            session=session,
            ledger=ledger,
            keys={"erst": "key-a", "reserve": "key-b"},
            max_queue_wait_s=0.2,
        )
        yield {
            "erst": erst,
            "reserve": reserve,
            "client": client,
            "ledger": ledger,
            "channels": channels,
            "provider_a": provider_a,
            "model_a": model_a,
        }

    await server.close()


# --------------------------------------------------------------------------
# Normalfall
# --------------------------------------------------------------------------


async def test_erste_wahl_liefert(welt) -> None:
    execution = await welt["client"].run(
        Requirements.for_profile("schnell"),
        welt["channels"],
        instructions="Wie spaet ist es?",
    )

    assert execution.candidate.key == "erst/modell-a"
    assert execution.response.text == "bereit"
    assert not execution.used_reserve
    assert welt["reserve"].calls == []
    assert welt["ledger"].usage(welt["provider_a"], welt["model_a"])["minute_requests"] == 1


# --------------------------------------------------------------------------
# Reserve
# --------------------------------------------------------------------------


async def test_ungueltiger_schluessel_wechselt_auf_die_reserve(welt, caplog) -> None:
    """Akzeptanzkriterium: Schluessel des ersten Anbieters ungueltig gemacht —
    die Anfrage geht durch und der Wechsel steht im Log."""
    welt["erst"].status = 401

    with caplog.at_level(logging.INFO):
        execution = await welt["client"].run(
            Requirements.for_profile("schnell"),
            welt["channels"],
            instructions="Wie spaet ist es?",
        )

    assert execution.candidate.key == "reserve/modell-b"
    assert execution.used_reserve
    assert "Schluessel abgelehnt" in caplog.text
    assert "Reserve gegriffen" in caplog.text


async def test_limit_wechselt_und_sperrt_den_kanal(welt) -> None:
    welt["erst"].status = 429
    welt["erst"].headers = {"retry-after": "30"}

    execution = await welt["client"].run(
        Requirements.for_profile("schnell"), welt["channels"], instructions="Hallo"
    )

    assert execution.candidate.key == "reserve/modell-b"
    gesperrt = welt["ledger"].availability(welt["provider_a"], welt["model_a"])
    assert not gesperrt.ok
    assert "429" in gesperrt.reason


async def test_serverfehler_wechselt_ebenfalls(welt) -> None:
    welt["erst"].status = 503
    execution = await welt["client"].run(
        Requirements.for_profile("schnell"), welt["channels"], instructions="Hallo"
    )
    assert execution.candidate.key == "reserve/modell-b"


async def test_alle_kanaele_tot_meldet_sauber_statt_zu_werfen(welt) -> None:
    welt["erst"].status = 500
    welt["reserve"].status = 500

    with pytest.raises(NoChannelAvailable) as fehler:
        await welt["client"].run(
            Requirements.for_profile("schnell"), welt["channels"], instructions="Hallo"
        )

    bericht = fehler.value.report()
    assert "erst/modell-a" in bericht
    assert "reserve/modell-b" in bericht


# --------------------------------------------------------------------------
# Kontingent
# --------------------------------------------------------------------------


async def test_rpm_anschlag_wird_eingereiht_statt_zu_scheitern(welt) -> None:
    """Akzeptanzkriterium: der RPM-Anschlag fuehrt zur Warteschlange, nicht
    zu einer Ausnahme in der Automation."""
    ledger = welt["ledger"]
    provider, model = welt["provider_a"], welt["model_a"]

    # Erstwahl auf Anschlag, Fenster fast abgelaufen.
    ledger.record_request(provider, model)
    ledger.record_request(provider, model)
    import time as _time

    ledger.bucket(bucket_key(provider, model)).minute_start = _time.time() - 59.95

    execution = await welt["client"].run(
        Requirements.for_profile("reasoning"), welt["channels"], instructions="Hallo"
    )
    # Entweder eingereiht und doch bei der Erstwahl gelandet oder sauber
    # gewechselt — beides ist in Ordnung, eine Ausnahme waere es nicht.
    assert execution.candidate.key in {"erst/modell-a", "reserve/modell-b"}


async def test_alle_am_limit_verwirft_sauber(welt) -> None:
    ledger = welt["ledger"]
    for channel in welt["channels"]:
        for _ in range(10):
            ledger.record_request(channel.provider, channel.model)

    with pytest.raises(NoChannelAvailable) as fehler:
        await welt["client"].run(
            Requirements.for_profile("schnell"),
            welt["channels"],
            instructions="Hallo",
        )

    assert "Minutenlimit" in fehler.value.report()
    # Nichts abgeschickt: der Anschlag wurde vorher erkannt.
    assert welt["erst"].calls == []
    assert welt["reserve"].calls == []


async def test_ohne_schluessel_kein_versuch(welt) -> None:
    welt["client"].keys = {"reserve": "key-b"}
    execution = await welt["client"].run(
        Requirements.for_profile("schnell"), welt["channels"], instructions="Hallo"
    )
    assert execution.candidate.key == "reserve/modell-b"
    assert welt["erst"].calls == []


# --------------------------------------------------------------------------
# Structured Output
# --------------------------------------------------------------------------


SCHEMA = {
    "type": "object",
    "properties": {"antwort": {"type": "string"}},
    "required": ["antwort"],
}


async def test_schema_wird_mitgeschickt(welt) -> None:
    welt["erst"].text = '{"antwort": "ja"}'
    execution = await welt["client"].run(
        Requirements.for_profile("schnell", has_structure=True),
        welt["channels"],
        instructions="Antworte",
        json_schema=SCHEMA,
    )

    assert execution.response.parsed == {"antwort": "ja"}
    gesendet = welt["erst"].calls[0]["response_format"]
    assert gesendet["type"] == "json_schema"
    assert gesendet["json_schema"]["schema"]["additionalProperties"] is False


async def test_abgelehntes_schema_faellt_auf_json_object_zurueck(welt) -> None:
    """Nicht jeder OpenAI-kompatible Endpunkt kann json_schema. Ein 400
    darauf ist kein Ausfall des Kanals."""
    welt["erst"].reject_json_schema = True
    welt["erst"].text = '{"antwort": "ja"}'

    execution = await welt["client"].run(
        Requirements.for_profile("schnell", has_structure=True),
        welt["channels"],
        instructions="Antworte",
        json_schema=SCHEMA,
    )

    assert execution.candidate.key == "erst/modell-a"
    assert execution.response.structured_mode == "json_object"
    assert execution.response.parsed == {"antwort": "ja"}
    assert len(welt["erst"].calls) == 2


async def test_antwort_im_codeblock_wird_trotzdem_gelesen(welt) -> None:
    welt["erst"].text = '```json\n{"antwort": "ja"}\n```'
    execution = await welt["client"].run(
        Requirements.for_profile("schnell", has_structure=True),
        welt["channels"],
        instructions="Antworte",
        json_schema=SCHEMA,
    )
    assert execution.response.parsed == {"antwort": "ja"}


# --------------------------------------------------------------------------
# Werkzeuge
# --------------------------------------------------------------------------


async def test_werkzeugaufruf_wird_normalisiert(welt) -> None:
    from custom_components.free_ai_router.adapters import ToolSpec

    welt["erst"].tool_call = {"name": "licht", "arguments": {"raum": "Kueche", "an": True}}
    execution = await welt["client"].run(
        Requirements.for_profile("schnell", needs_tools=True),
        welt["channels"],
        instructions="Licht an",
        tools=(ToolSpec(name="licht", description="schaltet", parameters={"type": "object"}),),
    )

    call = execution.response.tool_calls[0]
    assert call.name == "licht"
    assert call.arguments == {"raum": "Kueche", "an": True}
    assert call.call_id == "c1"


async def test_402_gilt_als_abgelehnter_schluessel(welt) -> None:
    """Cerebras antwortet mit 402, wenn der Key gueltig ist, das Konto aber
    keinen aktiven Tarif hat. Wiederholen hilft da nie — der Kanal muss sofort
    ausfallen statt dreimal anzuklopfen."""
    welt["erst"].status = 402

    execution = await welt["client"].run(
        Requirements.for_profile("schnell"), welt["channels"], instructions="Hallo"
    )

    assert execution.candidate.key == "reserve/modell-b"
    gesperrt = welt["ledger"].availability(welt["provider_a"], welt["model_a"])
    assert not gesperrt.ok
    assert "Schlüssel" in gesperrt.reason


async def test_googles_400_bei_ungueltigem_key_gilt_als_abgelehnt(welt) -> None:
    """Google meldet einen ungueltigen Schluessel mit HTTP 400 und
    API_KEY_INVALID, nicht mit 401. Ohne Sondererkennung gilt das als
    voruebergehender Fehler und der tote Kanal wird bei jeder Anfrage neu
    angeklopft — live am 11.09.2026 aufgefallen."""
    welt["erst"].status = 400
    welt["erst"].fehlertext = (
        "API key not valid. Please pass a valid API key. reason: API_KEY_INVALID"
    )

    execution = await welt["client"].run(
        Requirements.for_profile("schnell"), welt["channels"], instructions="Hallo"
    )

    assert execution.candidate.key == "reserve/modell-b"
    gesperrt = welt["ledger"].availability(welt["provider_a"], welt["model_a"])
    assert not gesperrt.ok, "ein abgelehnter Schluessel muss sofort ausscheiden"
    assert "Schlüssel" in gesperrt.reason


async def test_gewoehnlicher_400_bleibt_ein_normaler_fehlschlag(welt) -> None:
    """Nicht jeder 400 ist ein toter Schluessel — ein einzelner darf den Kanal
    nicht abschalten."""
    welt["erst"].status = 400
    welt["erst"].fehlertext = "unsupported parameter foo"

    await welt["client"].run(
        Requirements.for_profile("schnell"), welt["channels"], instructions="Hallo"
    )
    assert welt["ledger"].availability(welt["provider_a"], welt["model_a"]).ok
