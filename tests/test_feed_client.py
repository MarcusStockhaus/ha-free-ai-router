"""Das Holen des Feeds — bedingte Abrufe, Groessengrenze, Ausfaelle.

Der Fake-Session ist absichtlich klein gehalten: er kann genau das, was
``feed_client`` benutzt (``get`` als Kontextmanager, ``status``, ``headers``,
``content.iter_chunked``). Alles darueber hinaus wuerde nur Aufwand erzeugen
und die naechste aiohttp-Fassung zum Feind machen.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import make_model, make_provider
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from custom_components.free_ai_router.feed import (
    Measurement,
    build_document,
    dump_document,
)
from custom_components.free_ai_router.feed_client import (
    MAX_FEED_BYTES,
    CachedFeed,
    FeedManager,
    FeedUnavailable,
    async_fetch,
)
from custom_components.free_ai_router.registry import Registry

URL = "https://feed.example.invalid/v1/providers.json"

GROQ = {
    "id": "groq",
    "name": "Groq",
    "api_style": "openai_compatible",
    "base_url": "https://api.groq.com/openai/v1",
    "auth": {"type": "bearer"},
    "preference": 20,
    "onboarding": {
        "signup_url": "https://console.groq.com/keys",
        "steps_de": ["anmelden"],
        "data_note_de": "Kein Training auf Inhalten.",
        "credit_card_required": False,
    },
    "models": [
        {
            "id": "qwen3.8-27b",
            "profiles": ["schnell"],
            "capabilities": {
                "vision": False,
                "tools": True,
                "structured_output": True,
                "context_tokens": 131072,
            },
        }
    ],
}


# --------------------------------------------------------------------------
# Testdoppel
# --------------------------------------------------------------------------


class FakeContent:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def iter_chunked(self, size: int):
        for start in range(0, len(self._body), size):
            yield self._body[start : start + size]


class FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}
        self.content = FakeContent(body)

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False


class FakeSession:
    """Liefert je URL eine feste Antwort und merkt sich die Anfragen."""

    def __init__(self, antworten: dict[str, FakeResponse]) -> None:
        self._antworten = antworten
        self.anfragen: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, headers: dict[str, str] | None = None, **_kwargs: object):
        self.anfragen.append((url, dict(headers or {})))
        if url not in self._antworten:
            return FakeResponse(404, b"")
        return self._antworten[url]


@pytest.fixture
def schluessel() -> ed25519.Ed25519PrivateKey:
    return ed25519.Ed25519PrivateKey.generate()


@pytest.fixture
def public_b64(schluessel: ed25519.Ed25519PrivateKey) -> str:
    roh = schluessel.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(roh).decode("ascii")


def feed_bytes(*, generated_at: datetime, measurements: dict[str, Measurement] | None = None):
    return dump_document(
        build_document([GROQ], measurements or {}, generated_at=generated_at)
    )


def antworten(
    schluessel: ed25519.Ed25519PrivateKey,
    rohbytes: bytes,
    *,
    etag: str = '"abc"',
) -> dict[str, FakeResponse]:
    signatur = base64.b64encode(schluessel.sign(rohbytes))
    kopf = {"ETag": etag, "Last-Modified": "Thu, 11 Sep 2026 12:00:00 GMT"}
    return {
        URL: FakeResponse(200, rohbytes, kopf),
        f"{URL}.sig": FakeResponse(200, signatur),
    }


def basis() -> Registry:
    return Registry(
        providers=(
            make_provider(
                "groq",
                (make_model("qwen3.8-27b", "groq"),),
                base_url="https://api.groq.com/openai/v1",
                preference=20,
            ),
        )
    )


# --------------------------------------------------------------------------
# Abrufen
# --------------------------------------------------------------------------


async def test_holt_dokument_und_signatur(schluessel) -> None:
    rohbytes = feed_bytes(generated_at=datetime.now(UTC))
    session = FakeSession(antworten(schluessel, rohbytes))

    frisch = await async_fetch(session, CachedFeed(), url=URL)

    assert frisch is not None
    assert frisch.document.encode("utf-8") == rohbytes
    assert frisch.etag == '"abc"'
    assert [url for url, _ in session.anfragen] == [URL, f"{URL}.sig"]


async def test_bedingter_abruf_schickt_die_marken(schluessel) -> None:
    session = FakeSession({URL: FakeResponse(304, b"")})
    cache = CachedFeed(etag='"alt"', last_modified="Thu, 10 Sep 2026 12:00:00 GMT")

    assert await async_fetch(session, cache, url=URL) is None

    _url, headers = session.anfragen[0]
    assert headers["If-None-Match"] == '"alt"'
    assert headers["If-Modified-Since"] == "Thu, 10 Sep 2026 12:00:00 GMT"


async def test_zu_grosse_datei_wird_abgebrochen() -> None:
    session = FakeSession({URL: FakeResponse(200, b"x" * (MAX_FEED_BYTES + 1))})
    with pytest.raises(FeedUnavailable, match="groesser als"):
        await async_fetch(session, CachedFeed(), url=URL)


async def test_fehlende_signaturdatei_ist_kein_feed(schluessel) -> None:
    rohbytes = feed_bytes(generated_at=datetime.now(UTC))
    session = FakeSession({URL: FakeResponse(200, rohbytes)})
    with pytest.raises(FeedUnavailable, match=r"\.sig: HTTP 404"):
        await async_fetch(session, CachedFeed(), url=URL)


# --------------------------------------------------------------------------
# FeedManager
# --------------------------------------------------------------------------


async def test_uebernimmt_und_speichert(schluessel, public_b64) -> None:
    rohbytes = feed_bytes(
        generated_at=datetime.now(UTC),
        measurements={"groq/qwen3.8-27b": Measurement(alive=True, limits={"rpd": 1000})},
    )
    gespeichert: list[dict] = []

    async def save(data: dict) -> None:
        gespeichert.append(data)

    manager = FeedManager(
        FakeSession(antworten(schluessel, rohbytes)),
        save=save,
        url=URL,
        public_key_b64=public_b64,
    )

    assert await manager.async_update() is True
    assert gespeichert and gespeichert[0]["signature"]
    model = manager.apply(basis()).model_by_key("groq/qwen3.8-27b")
    assert model is not None and model.limits.rpd == 1000


async def test_manipuliertes_dokument_laesst_den_stand_unberuehrt(schluessel, public_b64) -> None:
    echt = feed_bytes(generated_at=datetime.now(UTC))
    session = FakeSession(antworten(schluessel, echt))
    manager = FeedManager(session, url=URL, public_key_b64=public_b64)
    assert await manager.async_update() is True
    vorher = manager.document

    gefaelscht = echt.replace(b'"preference": 20', b'"preference": 99')
    assert gefaelscht != echt
    manager._session = FakeSession(  # noqa: SLF001 - Testdoppel tauschen
        {
            URL: FakeResponse(200, gefaelscht, {"ETag": '"neu"'}),
            f"{URL}.sig": FakeResponse(200, base64.b64encode(schluessel.sign(echt))),
        }
    )

    assert await manager.async_update() is False
    assert manager.document is vorher
    assert "Signatur" in manager.last_error


async def test_ohne_schluessel_im_build_wird_nichts_geholt(schluessel) -> None:
    session = FakeSession(antworten(schluessel, feed_bytes(generated_at=datetime.now(UTC))))
    manager = FeedManager(session, url=URL, public_key_b64="")

    assert await manager.async_update() is False
    assert session.anfragen == []


async def test_altes_dokument_wird_nicht_wieder_eingespielt(schluessel, public_b64) -> None:
    jetzt = datetime.now(UTC)
    neu = feed_bytes(generated_at=jetzt)
    manager = FeedManager(
        FakeSession(antworten(schluessel, neu)), url=URL, public_key_b64=public_b64
    )
    assert await manager.async_update() is True

    alt = feed_bytes(generated_at=jetzt - timedelta(days=2))
    manager._session = FakeSession(antworten(schluessel, alt, etag='"alt"'))  # noqa: SLF001

    assert await manager.async_update() is False
    assert manager.document is not None
    assert manager.document.generated_at == jetzt


def test_kaputter_zwischenspeicher_wird_verworfen(public_b64) -> None:
    manager = FeedManager(FakeSession({}), url=URL, public_key_b64=public_b64)
    manager.restore({"document": json.dumps({"schema_version": 1}), "signature": "xx"})

    assert manager.document is None
    assert manager.cache.document == ""
    # Und die mitgelieferte Registry bleibt unveraendert nutzbar.
    assert manager.apply(basis()).get("groq") is not None


def test_leerer_zwischenspeicher_stoert_nicht(public_b64) -> None:
    manager = FeedManager(FakeSession({}), url=URL, public_key_b64=public_b64)
    manager.restore(None)
    assert manager.document is None
    assert manager.diagnostics()["erzeugt_am"] is None
