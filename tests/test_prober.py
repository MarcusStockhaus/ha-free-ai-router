"""Der Prober — Buchfuehrung ueber mehrere Laeufe und die Notbremsen.

Gemessen wird hier nichts: ``run_probes`` ist ausgetauscht. Geprueft wird das,
was der Prober ueber das Faehigkeitsmodul hinaus tut — und das ist genau die
Stelle, an der ein Feed-Dienst Nutzern schaden kann, ohne dass es auffaellt:
indem er funktionierende Kanaele faelschlich fuer tot erklaert.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from custom_components.free_ai_router.capabilities import CheckResult, ModelProbe
from custom_components.free_ai_router.feed import (
    DEAD_AFTER_FAILURES,
    parse_feed,
)
from custom_components.free_ai_router.ratelimit import RateLimitInfo
from tools import prober

JETZT = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


def lebendig(**kwargs) -> ModelProbe:
    probe = ModelProbe(
        provider_id="groq",
        model_id="qwen3.8-27b",
        alive=True,
        status=200,
        latency_total_s=1.2,
        ttft_s=0.4,
        rate_limit=RateLimitInfo(limit_requests=30, reset_requests_s=60.0),
    )
    for name, wert in kwargs.items():
        setattr(probe, name, wert)
    return probe


def tot(status: int | None = None, error: str = "Zeitueberschreitung") -> ModelProbe:
    return ModelProbe(
        provider_id="groq",
        model_id="qwen3.8-27b",
        alive=False,
        status=status,
        error=error,
    )


# --------------------------------------------------------------------------
# Verrechnung ueber mehrere Laeufe
# --------------------------------------------------------------------------


def test_erfolg_setzt_den_zaehler_zurueck() -> None:
    eintrag, _ = prober.merge_probe(
        {"consecutive_failures": 2, "alive": False, "last_error": "alt"},
        lebendig(),
        full=False,
        now=JETZT,
    )
    assert eintrag["alive"] is True
    assert eintrag["consecutive_failures"] == 0
    assert "last_error" not in eintrag
    assert eintrag["limits"]["rpm"] == 30


def test_sparsamer_lauf_loescht_keine_faehigkeiten() -> None:
    """Der stuendliche Takt prueft nur Lebendigkeit — er weiss nichts ueber Bilder."""
    eintrag, _ = prober.merge_probe(
        {"capabilities": {"vision": True}},
        lebendig(),
        full=False,
        now=JETZT,
    )
    assert eintrag["capabilities"] == {"vision": True}


def test_voller_lauf_uebernimmt_die_messung() -> None:
    probe = lebendig(
        vision=CheckResult(ok=True),
        tools=CheckResult(ok=False),
        structured_output=CheckResult(ok=True),
    )
    eintrag, _ = prober.merge_probe(
        {"capabilities": {"vision": False}}, probe, full=True, now=JETZT
    )
    assert eintrag["capabilities"] == {
        "vision": True,
        "tools": False,
        "structured_output": True,
    }
    assert eintrag["last_full_at"] == JETZT.isoformat()


def test_ausfall_zaehlt_hoch_und_behaelt_das_gewusste() -> None:
    eintrag, grund = prober.merge_probe(
        {"consecutive_failures": 1, "capabilities": {"vision": True}},
        tot(),
        full=True,
        now=JETZT,
    )
    assert grund == ""
    assert eintrag["alive"] is False
    assert eintrag["consecutive_failures"] == 2
    # Ein Modell verliert das Sehen nicht, weil es gerade nicht antwortet.
    assert eintrag["capabilities"] == {"vision": True}


@pytest.mark.parametrize("status", [401, 402, 403])
def test_abgelehnter_schluessel_zaehlt_nicht_als_ausfall(status: int) -> None:
    """Sonst meldete ein abgelaufener Proberschluessel allen Nutzern tote Kanaele."""
    eintrag, grund = prober.merge_probe(
        {"consecutive_failures": 0, "alive": True},
        tot(status=status, error="invalid api key"),
        full=True,
        now=JETZT,
    )
    assert grund == "schluessel"
    assert eintrag["consecutive_failures"] == 0
    # Der bisherige Befund bleibt stehen: ueber das Modell sagt das nichts.
    assert eintrag["alive"] is True


def test_ratenlimit_ist_kein_ausfall() -> None:
    """Im ersten Livelauf am 11.09.2026 antwortete Mistral mit 429.

    Wuerde das als Ausfall zaehlen, haetten drei gedrosselte Laeufe
    hintereinander gereicht, um allen Nutzern ein gesundes Modell
    abzuschalten. Der Endpunkt hat ja geantwortet.
    """
    eintrag, grund = prober.merge_probe(
        {"consecutive_failures": 2, "alive": True},
        tot(status=429, error="Rate limit exceeded"),
        full=False,
        now=JETZT,
    )
    assert grund == "limit"
    assert eintrag["consecutive_failures"] == 2
    assert eintrag["alive"] is True


def test_fehlertext_bleibt_im_zustand_und_nicht_im_feed() -> None:
    eintrag, _ = prober.merge_probe(None, tot(error="Konto 4711 ueberzogen"), full=True, now=JETZT)
    assert "Konto 4711" in eintrag["last_error"]
    assert "last_error" not in prober.to_measurement(eintrag).as_dict()


# --------------------------------------------------------------------------
# Ein ganzer Lauf
# --------------------------------------------------------------------------


@pytest.fixture
def schluesseldatei(tmp_path: Path) -> Path:
    key = ed25519.Ed25519PrivateKey.generate()
    pfad = tmp_path / "feed.pem"
    pfad.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return pfad


def public_b64(pfad: Path) -> str:
    key = serialization.load_pem_private_key(pfad.read_bytes(), password=None)
    roh = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(roh).decode("ascii")


def lauf(monkeypatch, probes: dict[str, ModelProbe]) -> None:
    async def fake_run_probes(providers, keys, *, full, timeout):
        return probes, []

    monkeypatch.setattr(prober, "run_probes", fake_run_probes)


def test_ein_lauf_ohne_jede_antwort_veroeffentlicht_nichts(monkeypatch, tmp_path: Path) -> None:
    """Alles tot heisst fast immer: das Netz des Probers ist tot."""
    lauf(monkeypatch, {"groq/qwen3.8-27b": tot()})
    code = prober.main(
        [
            "--cheap",
            "--out",
            str(tmp_path / "site"),
            "--state",
            str(tmp_path / "state.json"),
            "--env",
            str(tmp_path / "fehlt.env"),
        ]
    )
    assert code == 3
    assert not (tmp_path / "site").exists()
    assert not (tmp_path / "state.json").exists()


def test_voller_lauf_schreibt_signierten_feed(monkeypatch, tmp_path: Path, schluesseldatei) -> None:
    lauf(monkeypatch, {"groq/qwen3.8-27b": lebendig()})
    code = prober.main(
        [
            "--full",
            "--out",
            str(tmp_path / "site"),
            "--state",
            str(tmp_path / "state.json"),
            "--env",
            str(tmp_path / "fehlt.env"),
            "--key",
            str(schluesseldatei),
        ]
    )
    assert code == 0

    v1 = tmp_path / "site" / "v1"
    rohbytes = (v1 / "providers.json").read_bytes()
    signatur = (v1 / "providers.json.sig").read_text(encoding="utf-8")

    # Der entscheidende Punkt: was hier herauskommt, nimmt die Integration an.
    doc = parse_feed(rohbytes, signatur, public_key_b64=public_b64(schluesseldatei))
    assert doc.measurements["groq/qwen3.8-27b"].alive is True
    assert {provider.id for provider in doc.providers} >= {"groq", "google_ai_studio"}

    index = json.loads((v1 / "index.json").read_text(encoding="utf-8"))
    assert index["current"] == "providers.json"
    assert len(index["history"]) == 1
    assert (v1 / "history" / index["history"][0]).read_bytes() == rohbytes

    zustand = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert zustand["models"]["groq/qwen3.8-27b"]["consecutive_failures"] == 0


def test_abgelehnter_schluessel_meldet_sich_im_rueckgabewert(
    monkeypatch, tmp_path: Path, schluesseldatei
) -> None:
    """Ein Lauf, in dem ein Anbieter den Schluessel verweigert, endet nicht still."""
    lauf(
        monkeypatch,
        {
            "groq/qwen3.8-27b": lebendig(),
            "mistral/ministral-3b": ModelProbe(
                provider_id="mistral",
                model_id="ministral-3b",
                alive=False,
                status=401,
                error="invalid api key",
            ),
        },
    )
    code = prober.main(
        [
            "--cheap",
            "--out",
            str(tmp_path / "site"),
            "--state",
            str(tmp_path / "state.json"),
            "--env",
            str(tmp_path / "fehlt.env"),
            "--key",
            str(schluesseldatei),
        ]
    )
    assert code == 4
    # Trotzdem veroeffentlicht — und ohne den Mistral-Kanal totzumelden.
    doc = parse_feed(
        (tmp_path / "site" / "v1" / "providers.json").read_bytes(),
        (tmp_path / "site" / "v1" / "providers.json.sig").read_text(encoding="utf-8"),
        public_key_b64=public_b64(schluesseldatei),
    )
    assert doc.measurements["mistral/ministral-3b"].is_dead is False


def test_erst_mehrere_laeufe_melden_tot(monkeypatch, tmp_path: Path, schluesseldatei) -> None:
    argv = [
        "--cheap",
        "--out",
        str(tmp_path / "site"),
        "--state",
        str(tmp_path / "state.json"),
        "--env",
        str(tmp_path / "fehlt.env"),
        "--key",
        str(schluesseldatei),
    ]
    # Ein Modell antwortet immer, damit der Lauf nicht als Netzausfall gilt.
    for durchgang in range(1, DEAD_AFTER_FAILURES + 1):
        lauf(
            monkeypatch,
            {
                "google_ai_studio/gemini-3.5-flash-lite": ModelProbe(
                    provider_id="google_ai_studio",
                    model_id="gemini-3.5-flash-lite",
                    alive=True,
                    status=200,
                ),
                "groq/qwen3.8-27b": tot(),
            },
        )
        assert prober.main(argv) == 0
        doc = parse_feed(
            (tmp_path / "site" / "v1" / "providers.json").read_bytes(),
            (tmp_path / "site" / "v1" / "providers.json.sig").read_text(encoding="utf-8"),
            public_key_b64=public_b64(schluesseldatei),
        )
        messung = doc.measurements["groq/qwen3.8-27b"]
        assert messung.consecutive_failures == durchgang
        assert messung.is_dead is (durchgang >= DEAD_AFTER_FAILURES)
