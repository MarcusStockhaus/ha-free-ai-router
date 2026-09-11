"""Jeder Schritt und jeder Fehler im Config Flow braucht einen Text.

Fehlt einer, zeigt Home Assistant im Frontend den rohen Schluessel an —
„entfernen" statt „Anbieter entfernen", „nicht_bestaetigt" statt einer
Erklaerung. Das faellt in keinem Test auf und in keinem Log, sondern nur dem
Nutzer, der gerade davorsteht.

Geprueft wird gegen den Quelltext, nicht gegen eine gepflegte Liste: was im
Flow als ``step_id`` steht, muss in ``strings.json`` stehen, und umgekehrt darf
kein Menuepunkt auf einen Schritt zeigen, den es nicht gibt. Die CI vergleicht
zusaetzlich ``strings.json`` mit den Uebersetzungen.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent / "custom_components" / "free_ai_router"
QUELLE = WURZEL / "config_flow.py"


def _baum() -> ast.Module:
    return ast.parse(QUELLE.read_text(encoding="utf-8"))


def _texte() -> dict:
    return json.loads((WURZEL / "strings.json").read_text(encoding="utf-8"))


def _argumente(funktionen: set[str], name: str) -> set[str]:
    """Zeichenketten, die einer dieser Funktionen als ``name=`` mitgegeben werden.

    Nach der aufgerufenen Funktion zu unterscheiden ist noetig, weil die Texte
    an verschiedenen Stellen liegen: ein Formular- oder Menueschritt braucht
    ``config.step.<id>``, ein Fortschrittsschritt dagegen
    ``config.progress.<action>`` — sein ``step_id`` taucht in den Texten gar
    nicht auf.
    """
    gefunden: set[str] = set()
    for knoten in ast.walk(_baum()):
        if not isinstance(knoten, ast.Call):
            continue
        aufgerufen = knoten.func.attr if isinstance(knoten.func, ast.Attribute) else None
        if aufgerufen not in funktionen:
            continue
        for arg in knoten.keywords:
            if (
                arg.arg == name
                and isinstance(arg.value, ast.Constant)
                and isinstance(arg.value.value, str)
            ):
                gefunden.add(arg.value.value)
    return gefunden


def _schritte_im_code() -> set[str]:
    return {
        knoten.name.removeprefix("async_step_")
        for knoten in ast.walk(_baum())
        if isinstance(knoten, ast.AsyncFunctionDef) and knoten.name.startswith("async_step_")
    }


def test_jeder_sichtbare_schritt_hat_einen_text() -> None:
    texte = _texte()["config"]["step"]
    sichtbar = _argumente({"async_show_form", "async_show_menu"}, "step_id")
    assert sichtbar, "keine Schritte gefunden — Test trifft nicht mehr zu"
    fehlend = sorted(sichtbar - set(texte))
    assert not fehlend, f"ohne Text in config.step: {fehlend}"


def test_jeder_fortschritt_hat_einen_text() -> None:
    """Fortschrittsschritte holen ihren Text aus ``config.progress``."""
    texte = _texte()["config"]["progress"]
    aktionen = _argumente({"async_show_progress"}, "progress_action")
    assert aktionen, "keine Fortschrittsschritte gefunden"
    fehlend = sorted(aktionen - set(texte))
    assert not fehlend, f"ohne Text in config.progress: {fehlend}"


def test_jeder_menuepunkt_zeigt_auf_einen_schritt() -> None:
    """Ein Label fuer einen Schritt, den es nicht gibt, endet in einer Sackgasse."""
    schritte = _schritte_im_code()
    fehlend: list[str] = []
    for name, block in _texte()["config"]["step"].items():
        for ziel in (block.get("menu_options") or {}):
            if ziel not in schritte:
                fehlend.append(f"{name} -> {ziel}")
    assert not fehlend, f"Menuepunkte ohne Schritt: {fehlend}"


def test_jeder_fehlerschluessel_hat_einen_text() -> None:
    """``errors["base"] = "x"`` ohne ``config.error.x`` zeigt rohes ``x``."""
    texte = _texte()["config"]["error"]
    benutzt: set[str] = set()
    for knoten in ast.walk(_baum()):
        # errors["base"] = "key_rejected"
        if isinstance(knoten, ast.Assign) and isinstance(knoten.value, ast.Constant):
            for ziel in knoten.targets:
                if (
                    isinstance(ziel, ast.Subscript)
                    and isinstance(ziel.value, ast.Name)
                    and ziel.value.id == "errors"
                    and isinstance(knoten.value.value, str)
                ):
                    benutzt.add(knoten.value.value)
    assert benutzt, "kein einziger Fehlerschluessel gefunden — Test trifft nicht mehr zu"
    fehlend = sorted(benutzt - set(texte))
    assert not fehlend, f"ohne Text in config.error: {fehlend}"


def test_die_neuen_verwaltungsschritte_sind_vollstaendig() -> None:
    """Die Verwaltung ist der Weg, auf dem Schluessel geaendert werden."""
    schritte = _texte()["config"]["step"]
    for name in ("verwalten", "schluessel", "entfernen"):
        assert name in schritte, f"{name} fehlt in strings.json"
        assert schritte[name].get("title"), f"{name} ohne Titel"
        assert schritte[name].get("description"), f"{name} ohne Beschreibung"


def test_platzhalter_werden_auch_gefuellt() -> None:
    """Ein ``{zugaenge}`` im Text ohne Wert im Code bliebe als Klammer stehen."""
    import re

    quelle = QUELLE.read_text(encoding="utf-8")
    for name in ("verwalten", "schluessel", "entfernen"):
        text = _texte()["config"]["step"][name]["description"]
        for platzhalter in re.findall(r"\{(\w+)\}", text):
            assert f'"{platzhalter}"' in quelle, (
                f"{name}: Platzhalter {{{platzhalter}}} wird im Code nicht gesetzt"
            )
