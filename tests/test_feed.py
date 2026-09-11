"""Der Feed — Signatur, Frische und die Grenzen dessen, was er darf.

Die interessanten Tests sind hier nicht die des Gutfalls, sondern die des
Angriffs: ein veraendertes Byte, ein fremder Schluessel, ein wieder
eingespieltes altes Dokument, ein Anbieter mit umgebogenem Endpunkt. Der Feed
ist die einzige Stelle, an der fremde Daten in die Integration laufen.
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
    DEAD_AFTER_FAILURES,
    FeedError,
    FeedSignatureError,
    FeedStaleError,
    Measurement,
    apply_feed,
    build_document,
    dump_document,
    parse_feed,
    verify_signature,
)
from custom_components.free_ai_router.registry import Registry

JETZT = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)

# Ein Anbieter in der Form, die auch in einer YAML-Datei stehen wuerde.
GROQ = {
    "id": "groq",
    "name": "Groq",
    "api_style": "openai_compatible",
    "base_url": "https://api.groq.com/openai/v1",
    "auth": {"type": "bearer"},
    "preference": 20,
    "onboarding": {
        "signup_url": "https://console.groq.com/keys",
        "steps_de": ["anmelden", "Key kopieren"],
        "data_note_de": "Kein Training auf Inhalten.",
        "credit_card_required": False,
    },
    "models": [
        {
            "id": "qwen/qwen3.8-27b",
            "label": "Qwen3.8 27B",
            "profiles": ["schnell", "vision"],
            "capabilities": {
                "vision": False,
                "tools": True,
                "structured_output": True,
                "context_tokens": 131072,
            },
            "limits": {"rpm": 30},
        }
    ],
}


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


def unterschreibe(schluessel: ed25519.Ed25519PrivateKey, rohbytes: bytes) -> str:
    return base64.b64encode(schluessel.sign(rohbytes)).decode("ascii")


def dokument(
    *,
    providers: list[dict] | None = None,
    measurements: dict[str, Measurement] | None = None,
    generated_at: datetime = JETZT,
) -> bytes:
    return dump_document(
        build_document(
            providers if providers is not None else [GROQ],
            measurements or {},
            generated_at=generated_at,
        )
    )


# --------------------------------------------------------------------------
# Signatur
# --------------------------------------------------------------------------


def test_runde_laeuft_durch(schluessel, public_b64) -> None:
    rohbytes = dokument()
    doc = parse_feed(
        rohbytes, unterschreibe(schluessel, rohbytes), now=JETZT, public_key_b64=public_b64
    )
    assert doc.schema_version == 1
    assert [provider.id for provider in doc.providers] == ["groq"]
    assert doc.providers[0].models[0].limits.rpm == 30


def test_ein_veraendertes_byte_faellt_auf(schluessel, public_b64) -> None:
    rohbytes = dokument()
    signatur = unterschreibe(schluessel, rohbytes)
    # Ein Limit hochsetzen — genau das, was ein Angreifer tun wuerde, der
    # Nutzer in ein Kontingent laufen lassen will.
    gefaelscht = rohbytes.replace(b'"rpm": 30', b'"rpm": 99')
    assert gefaelscht != rohbytes
    with pytest.raises(FeedSignatureError, match="Signatur passt nicht"):
        parse_feed(gefaelscht, signatur, now=JETZT, public_key_b64=public_b64)


def test_fremder_schluessel_wird_abgelehnt(schluessel, public_b64) -> None:
    rohbytes = dokument()
    fremd = ed25519.Ed25519PrivateKey.generate()
    with pytest.raises(FeedSignatureError, match="Signatur passt nicht"):
        parse_feed(rohbytes, unterschreibe(fremd, rohbytes), now=JETZT, public_key_b64=public_b64)


def test_ohne_eincompilierten_schluessel_kein_feed(schluessel) -> None:
    """Der Auslieferungszustand: kein Schluessel im Build, also kein Feed."""
    rohbytes = dokument()
    with pytest.raises(FeedSignatureError, match="keinen Feed-Schluessel"):
        parse_feed(rohbytes, unterschreibe(schluessel, rohbytes), now=JETZT, public_key_b64="")


def test_unsinnige_signatur(public_b64) -> None:
    with pytest.raises(FeedSignatureError, match="Base64"):
        verify_signature(b"egal", "kein base64!!", public_key_b64=public_b64)


# --------------------------------------------------------------------------
# Frische
# --------------------------------------------------------------------------


def test_zu_altes_dokument(schluessel, public_b64) -> None:
    rohbytes = dokument(generated_at=JETZT - timedelta(days=20))
    with pytest.raises(FeedStaleError, match="20 Tage alt"):
        parse_feed(
            rohbytes, unterschreibe(schluessel, rohbytes), now=JETZT, public_key_b64=public_b64
        )


def test_dokument_aus_der_zukunft(schluessel, public_b64) -> None:
    rohbytes = dokument(generated_at=JETZT + timedelta(days=1))
    with pytest.raises(FeedStaleError, match="Zukunft"):
        parse_feed(
            rohbytes, unterschreibe(schluessel, rohbytes), now=JETZT, public_key_b64=public_b64
        )


def test_kleine_uhrabweichung_ist_erlaubt(schluessel, public_b64) -> None:
    rohbytes = dokument(generated_at=JETZT + timedelta(minutes=20))
    parse_feed(rohbytes, unterschreibe(schluessel, rohbytes), now=JETZT, public_key_b64=public_b64)


def test_altes_dokument_laesst_sich_nicht_erneut_einspielen(schluessel, public_b64) -> None:
    """Signiert und frisch genug — aber aelter als das, was schon da war."""
    rohbytes = dokument(generated_at=JETZT - timedelta(days=3))
    with pytest.raises(FeedStaleError, match="aelter als das zuletzt gesehene"):
        parse_feed(
            rohbytes,
            unterschreibe(schluessel, rohbytes),
            now=JETZT,
            seen_generated_at=JETZT - timedelta(days=1),
            public_key_b64=public_b64,
        )


# --------------------------------------------------------------------------
# Was der Feed nicht darf
# --------------------------------------------------------------------------


def test_der_feed_darf_den_endpunkt_nicht_umbiegen(schluessel, public_b64) -> None:
    """Die wichtigste Zusage des ganzen Moduls.

    Ein uebernommener Feed koennte sonst Kamerabilder woanders hinschicken,
    ohne dass es irgendwo auffiele. Die Signatur ist hier absichtlich gueltig:
    geprueft wird, dass die Allowlist auch hinter einer gueltigen Signatur
    noch haelt.
    """
    boese = json.loads(json.dumps(GROQ))
    boese["base_url"] = "https://api.groq.com.angreifer.example/v1"
    rohbytes = dokument(providers=[boese])
    with pytest.raises(FeedError, match="Allowlist"):
        parse_feed(
            rohbytes, unterschreibe(schluessel, rohbytes), now=JETZT, public_key_b64=public_b64
        )


def test_messung_darf_keine_fremden_felder_fuehren(schluessel, public_b64) -> None:
    """Strukturelle Befunde, keine Kontoangaben."""
    roh = json.loads(dokument().decode("utf-8"))
    roh["measurements"] = {"groq/qwen/qwen3.8-27b": {"alive": True, "guthaben_usd": 4.20}}
    rohbytes = json.dumps(roh, indent=2, sort_keys=True).encode("utf-8")
    with pytest.raises(FeedError, match="guthaben_usd"):
        parse_feed(
            rohbytes, unterschreibe(schluessel, rohbytes), now=JETZT, public_key_b64=public_b64
        )


def test_unbekannte_schemafassung(schluessel, public_b64) -> None:
    roh = json.loads(dokument().decode("utf-8"))
    roh["schema_version"] = 2
    rohbytes = json.dumps(roh).encode("utf-8")
    with pytest.raises(FeedError, match="Schemafassung"):
        parse_feed(
            rohbytes, unterschreibe(schluessel, rohbytes), now=JETZT, public_key_b64=public_b64
        )


# --------------------------------------------------------------------------
# Anwenden
# --------------------------------------------------------------------------


def basis() -> Registry:
    return Registry(
        providers=(
            make_provider(
                "groq",
                (make_model("qwen/qwen3.8-27b", "groq", profiles=("schnell", "vision")),),
                base_url="https://api.groq.com/openai/v1",
                preference=20,
            ),
            make_provider(
                "mistral",
                (make_model("ministral-3b", "mistral"),),
                base_url="https://api.mistral.ai/v1",
                preference=50,
            ),
        )
    )


def anwenden(schluessel, public_b64, measurements, providers=None) -> Registry:
    rohbytes = dokument(providers=providers, measurements=measurements)
    doc = parse_feed(
        rohbytes, unterschreibe(schluessel, rohbytes), now=JETZT, public_key_b64=public_b64
    )
    return apply_feed(basis(), doc)


def test_messung_schlaegt_die_datei(schluessel, public_b64) -> None:
    registry = anwenden(
        schluessel,
        public_b64,
        {
            "groq/qwen/qwen3.8-27b": Measurement(
                alive=True, capabilities={"vision": True}, limits={"rpd": 1000}
            )
        },
    )
    model = registry.model_by_key("groq/qwen/qwen3.8-27b")
    assert model is not None
    assert model.capabilities.vision is True
    assert model.limits.rpd == 1000


def test_ein_ausfall_wirft_noch_nichts_hinaus(schluessel, public_b64) -> None:
    registry = anwenden(
        schluessel,
        public_b64,
        {
            "groq/qwen/qwen3.8-27b": Measurement(
                alive=False, consecutive_failures=DEAD_AFTER_FAILURES - 1
            )
        },
    )
    assert registry.model_by_key("groq/qwen/qwen3.8-27b") is not None


def test_mehrfacher_ausfall_entfernt_das_modell(schluessel, public_b64) -> None:
    registry = anwenden(
        schluessel,
        public_b64,
        {
            "groq/qwen/qwen3.8-27b": Measurement(
                alive=False, consecutive_failures=DEAD_AFTER_FAILURES
            )
        },
    )
    assert registry.model_by_key("groq/qwen/qwen3.8-27b") is None
    # Der Anbieter hatte nur dieses Modell und verschwindet mit.
    assert registry.get("groq") is None


def test_nicht_genannte_anbieter_bleiben_stehen(schluessel, public_b64) -> None:
    """Loeschen durch Weglassen gibt es nicht."""
    registry = anwenden(schluessel, public_b64, {})
    assert registry.get("mistral") is not None


def test_feed_ersetzt_den_gleichnamigen_anbieter(schluessel, public_b64) -> None:
    registry = anwenden(schluessel, public_b64, {})
    groq = registry.get("groq")
    assert groq is not None
    # Aus dem Feed, nicht aus conftest: dort steht kein Label.
    assert groq.models[0].label == "Qwen3.8 27B"


def test_reihenfolge_folgt_der_preference(schluessel, public_b64) -> None:
    registry = anwenden(schluessel, public_b64, {})
    assert [provider.id for provider in registry.providers] == ["groq", "mistral"]
