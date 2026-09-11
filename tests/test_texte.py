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
import re
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent / "custom_components" / "free_ai_router"
QUELLE = WURZEL / "config_flow.py"


def _baum() -> ast.Module:
    return ast.parse(QUELLE.read_text(encoding="utf-8"))


def _texte() -> dict:
    return json.loads((WURZEL / "strings.json").read_text(encoding="utf-8"))


#: Welche Klasse ihre Texte wo sucht. Der Mixin liefert Schritte an beide
#: Fluesse und muss deshalb in beiden Bloecken stehen.
_BLOECKE = {
    "_MessSchritte": ("config", "config_subentries.anbieter"),
    "FreeAIRouterConfigFlow": ("config",),
    "AnbieterSubentryFlow": ("config_subentries.anbieter",),
}


def _block(pfad: str) -> dict:
    ziel = _texte()
    for teil in pfad.split("."):
        ziel = ziel[teil]
    return ziel


def _schritte_je_klasse() -> dict[str, set[str]]:
    """Sichtbare Schritte, nach der Klasse getrennt, in der sie stehen."""
    ergebnis: dict[str, set[str]] = {}
    for knoten in ast.walk(_baum()):
        if not isinstance(knoten, ast.ClassDef):
            continue
        gefunden: set[str] = set()
        for unter in ast.walk(knoten):
            if not isinstance(unter, ast.Call):
                continue
            aufgerufen = unter.func.attr if isinstance(unter.func, ast.Attribute) else None
            if aufgerufen not in {"async_show_form", "async_show_menu"}:
                continue
            for arg in unter.keywords:
                if (
                    arg.arg == "step_id"
                    and isinstance(arg.value, ast.Constant)
                    and isinstance(arg.value.value, str)
                ):
                    gefunden.add(arg.value.value)
        ergebnis[knoten.name] = gefunden
    return ergebnis


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
    """Und zwar im Block des Flusses, zu dem er gehoert.

    Der Einrichtungsassistent sucht seine Texte unter ``config``, der
    Subentry-Flow unter ``config_subentries.anbieter``. Ein Schritt aus dem
    gemeinsamen Mixin braucht beide.
    """
    je_klasse = _schritte_je_klasse()
    assert je_klasse.get("_MessSchritte"), "Mixin nicht gefunden — Test trifft nicht mehr zu"

    fehlend: list[str] = []
    for klasse, bloecke in _BLOECKE.items():
        assert klasse in je_klasse, f"Klasse {klasse} gibt es nicht mehr"
        for pfad in bloecke:
            texte = _block(pfad)["step"]
            fehlend += [
                f"{pfad}.step.{name} (aus {klasse})"
                for name in sorted(je_klasse[klasse] - set(texte))
            ]
    assert not fehlend, f"ohne Text: {fehlend}"


def test_jeder_fortschritt_hat_einen_text() -> None:
    """Fortschrittsschritte holen ihren Text aus ``progress``, nicht aus ``step``."""
    aktionen = _argumente({"async_show_progress"}, "progress_action")
    assert aktionen, "keine Fortschrittsschritte gefunden"
    fehlend: list[str] = []
    for pfad in ("config", "config_subentries.anbieter"):
        offen = sorted(aktionen - set(_block(pfad)["progress"]))
        fehlend += [f"{pfad}.progress.{name}" for name in offen]
    assert not fehlend, f"ohne Text: {fehlend}"


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


def test_der_anbieter_subentry_ist_beschriftet() -> None:
    """Ohne diese Texte heisst der Knopf auf der Integrationsseite „anbieter"."""
    block = _block("config_subentries.anbieter")
    assert block["entry_type"], "entry_type fehlt — die Zeile haette keine Bezeichnung"
    for quelle in ("user", "reconfigure"):
        assert block["initiate_flow"].get(quelle), f"initiate_flow.{quelle} fehlt"


def test_jeder_subentry_typ_im_code_hat_eine_bezeichnung() -> None:
    """Auch der Router-Typ, den niemand hinzufuegen kann.

    Home Assistant holt die Bezeichnung einer Untereintrag-Zeile aus
    ``component.<domain>.config_subentries.<typ>.entry_type`` — unabhaengig
    davon, ob der Typ zum Hinzufuegen angeboten wird. Fehlt sie, steht in der
    Oberflaeche der rohe Schluessel.
    """
    const = (WURZEL / "const.py").read_text(encoding="utf-8")
    typen = set(re.findall(r'SUBENTRY_TYPE_\w+: Final = "(\w+)"', const))
    assert typen, "keine Subentry-Typen gefunden — Test trifft nicht mehr zu"

    beschriftet = set(_texte()["config_subentries"])
    fehlend = sorted(typen - beschriftet)
    assert not fehlend, f"ohne entry_type in config_subentries: {fehlend}"
    for typ in sorted(typen):
        assert _texte()["config_subentries"][typ].get("entry_type"), f"{typ}: entry_type leer"


def test_platzhalter_werden_auch_gefuellt() -> None:
    """Ein ``{report}`` im Text ohne Wert im Code bliebe als Klammer stehen."""
    import re

    quelle = QUELLE.read_text(encoding="utf-8")
    fehlend: list[str] = []
    for pfad in ("config", "config_subentries.anbieter"):
        for name, schritt in _block(pfad)["step"].items():
            for platzhalter in re.findall(r"\{(\w+)\}", schritt.get("description", "")):
                if f'"{platzhalter}"' not in quelle:
                    fehlend.append(f"{pfad}.step.{name}: {{{platzhalter}}}")
    assert not fehlend, f"im Code nicht gesetzt: {fehlend}"
