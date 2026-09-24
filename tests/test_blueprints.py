"""Blueprints: gueltig, in sich stimmig, und jeder in beiden Sprachen.

Home Assistant uebersetzt Blueprint-Texte nicht. Deshalb gibt es jeden
Blueprint zweimal, und die englische Fassung muss auch das Modell auf Englisch
antworten lassen — ein "kurzer deutscher Satz" im Schema erzeugte auf einem
englischen System deutsche Benachrichtigungen.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ORDNER = Path(__file__).resolve().parent.parent / "blueprints" / "automation" / "free_ai_router"

#: Deutsch -> Englisch.
PAARE = {"kamera_analyse.yaml": "camera_analysis.yaml", "tuerklingel.yaml": "doorbell.yaml"}


class _Lader(yaml.SafeLoader):
    """Kennt ``!input``, ohne Home Assistant zu brauchen."""


_Lader.add_constructor("!input", lambda lader, knoten: lader.construct_scalar(knoten))


def _laden(name: str) -> tuple[str, dict]:
    text = (ORDNER / name).read_text(encoding="utf-8")
    return text, yaml.load(text, Loader=_Lader)


def test_jeder_blueprint_hat_seine_uebersetzung() -> None:
    vorhanden = {pfad.name for pfad in ORDNER.glob("*.yaml")}
    assert vorhanden == set(PAARE) | set(PAARE.values())


@pytest.mark.parametrize("name", sorted(set(PAARE) | set(PAARE.values())))
def test_eingaben_sind_vollstaendig_verdrahtet(name: str) -> None:
    text, daten = _laden(name)
    eingaben = {
        schluessel
        for gruppe in daten["blueprint"]["input"].values()
        for schluessel in gruppe["input"]
    }
    benutzt = set(re.findall(r"!input (\w+)", text))
    assert eingaben == benutzt, (sorted(eingaben - benutzt), sorted(benutzt - eingaben))
    assert daten["blueprint"]["source_url"].endswith(f"/{name}")


@pytest.mark.parametrize(("deutsch", "englisch"), sorted(PAARE.items()))
def test_beide_fassungen_tun_dasselbe(deutsch: str, englisch: str) -> None:
    _, de = _laden(deutsch)
    _, en = _laden(englisch)
    assert len(de["actions"]) == len(en["actions"])
    assert de["triggers"][0]["trigger"] == en["triggers"][0]["trigger"]
    feld_de = de["actions"][0]["data"]["structure"]
    feld_en = en["actions"][0]["data"]["structure"]
    assert len(feld_de) == len(feld_en)
    # Beide nutzen dieselbe Vorgabe-Entity — deren ID ist sprachunabhaengig.
    vorgabe = "ai_task.free_ai_router_image_analysis"
    assert vorgabe in (ORDNER / deutsch).read_text(encoding="utf-8")
    assert vorgabe in (ORDNER / englisch).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(PAARE.values()))
def test_englische_fassung_ist_englisch(name: str) -> None:
    text, _ = _laden(name)
    ohne_kommentare = "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))
    assert not re.search(r"[äöüÄÖÜß]|\b(und|der|die|das|deutsch\w*)\b", ohne_kommentare)
    assert "English sentence" in text
