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


def _menu_schritte_je_klasse() -> dict[str, set[str]]:
    """``step_id`` von ``async_show_menu``-Aufrufen, nach Klasse getrennt.

    Klassengetrennt und nicht global, weil derselbe Schrittname in
    verschiedenen Fluessen unterschiedlich gerendert wird: der
    Ergebnis-Schritt ist im Haupt-Flow ein Menue (Platzhalter im Titel
    bleiben leer), im Subentry-Flow aber ein Formular (dort funktionieren
    sie). Ueber ``_BLOECKE`` weiss :func:`test_menue_titel_haben_keine_platzhalter`
    danach, welcher Textblock zu welcher Klasse gehoert.
    """
    ergebnis: dict[str, set[str]] = {}
    for knoten in ast.walk(_baum()):
        if not isinstance(knoten, ast.ClassDef):
            continue
        schritte: set[str] = set()
        for unter in ast.walk(knoten):
            if not isinstance(unter, ast.Call):
                continue
            aufgerufen = unter.func.attr if isinstance(unter.func, ast.Attribute) else None
            if aufgerufen != "async_show_menu":
                continue
            for arg in unter.keywords:
                if (
                    arg.arg == "step_id"
                    and isinstance(arg.value, ast.Constant)
                    and isinstance(arg.value.value, str)
                ):
                    schritte.add(arg.value.value)
        if schritte:
            ergebnis[knoten.name] = schritte
    return ergebnis


def test_menue_titel_haben_keine_platzhalter() -> None:
    """``async_show_menu`` gibt seinen Titel ohne ``description_placeholders``
    an ``localize()`` weiter — im Home-Assistant-Frontend nachgelesen
    (``renderMenuHeader`` in ``dialog-data-entry-flow``): anders als
    ``renderShowFormStepHeader`` fehlt dort schlicht das zweite Argument. Ein
    ``{name}`` im Titel eines Menu-Schritts bleibt deshalb fuer immer ein
    ``[formatjs Error: MISSING_VALUE]``, egal wie sorgfaeltig der Python-Code
    seine Platzhalter befuellt — live am 17.09.2026 beim Ergebnis-Schritt
    des Haupt-Flows aufgefallen. Die Beschreibung ist davon nicht betroffen,
    dort werden Platzhalter nachweislich ausgewertet; dynamischer Inhalt
    gehoert bei einem Menu-Schritt also dorthin, nicht in den Titel.

    Klassengetrennt geprueft: derselbe Schrittname "result" ist im
    Subentry-Flow ein Formular, nicht ein Menue, und darf dort seinen
    informativen, platzhalterhaltigen Titel behalten.
    """
    import re

    je_klasse = _menu_schritte_je_klasse()
    assert je_klasse, "kein async_show_menu-Schritt gefunden — Test trifft nicht mehr zu"

    fehlend: list[str] = []
    for klasse, schritte in je_klasse.items():
        for pfad in _BLOECKE.get(klasse, ()):
            texte = _block(pfad)["step"]
            for schritt in schritte:
                titel = (texte.get(schritt) or {}).get("title", "")
                if re.search(r"\{\w+\}", titel):
                    fehlend.append(f"{pfad}.step.{schritt}.title: {titel!r}")
    assert not fehlend, f"Platzhalter in einem Menu-Titel bleiben im Frontend leer: {fehlend}"


def test_der_anbieter_subentry_ist_beschriftet() -> None:
    """Ohne diese Texte heisst der Knopf auf der Integrationsseite „anbieter"."""
    block = _block("config_subentries.anbieter")
    assert block["entry_type"], "entry_type fehlt — die Zeile haette keine Bezeichnung"
    for quelle in ("user", "reconfigure"):
        assert block["initiate_flow"].get(quelle), f"initiate_flow.{quelle} fehlt"


def test_englische_uebersetzung_ist_identisch_zur_deutschen() -> None:
    """Halb uebersetzt ist schlechter als konsistent deutsch.

    Ein Einsteiger mit englischem Home Assistant soll denselben, vollstaendig
    durchdachten Text sehen wie mit deutschem — nicht eine Mischung aus
    uebersetzten und liegengebliebenen deutschen Saetzen. Solange es keine
    echte englische Fassung gibt, ist ``en.json`` bewusst eine Kopie von
    ``de.json``. Weicht sie ab, ist entweder eine Uebersetzung begonnen und
    nicht zu Ende gefuehrt worden, oder die deutsche Fassung wurde geaendert
    und die englische vergessen — beides soll aufgefallen sein, bevor es das
    Frontend erreicht.
    """
    de = json.loads((WURZEL / "translations" / "de.json").read_text(encoding="utf-8"))
    en = json.loads((WURZEL / "translations" / "en.json").read_text(encoding="utf-8"))
    assert en == de


def test_router_entities_ohne_geraet_tragen_den_integrationsnamen() -> None:
    """Ohne Geraet gibt es keinen Namenspraefix von Home Assistant.

    Die Router-Entities (die drei ``ai_task``-Profile, Assist, die vier
    Gesamtzaehler) haben seit Fassung 2 bewusst kein eigenes Geraet — siehe
    ``entity.py``. Der Anzeigename kommt dann ausschliesslich aus dieser
    Datei; ohne "Free AI Router" davor stehen sie unbeschriftet neben jeder
    anderen Integration in jeder Entity-Auswahl. Der Anbietersensor ist die
    Ausnahme: er haengt an seinem eigenen Anbieter-Geraet, das den Praefix
    liefert — ihn hier zu wiederholen ergaebe "Google AI Studio Google AI
    Studio Anfragen heute".
    """
    texte = _texte()["entity"]
    ohne_geraet = [
        texte["ai_task"]["schnell"]["name"],
        texte["ai_task"]["vision"]["name"],
        texte["ai_task"]["reasoning"]["name"],
        texte["conversation"]["assist"]["name"],
        texte["sensor"]["anfragen_heute"]["name"],
        texte["sensor"]["token_heute"]["name"],
        texte["sensor"]["reserve_heute"]["name"],
        texte["sensor"]["verworfen_heute"]["name"],
    ]
    for name in ohne_geraet:
        assert name.startswith("Free AI Router "), name

    # Der Anbietersensor bekommt den Praefix vom Geraet, nicht vom Text.
    assert not texte["sensor"]["anbieter_anfragen"]["name"].startswith("Free AI Router")


def test_keine_verweise_auf_einen_konfigurieren_knopf() -> None:
    """Seit dem Subentry-Umbau gibt es weder Options-Flow noch Reconfigure
    auf Ebene des Config Entry — nur die beiden Anbieter-Aktionen in der
    jeweiligen Subentry-Zeile. Ein Text, der auf "Konfigurieren" verweist,
    fuehrt in eine Sackgasse: den Knopf gibt es nicht.
    """
    for pfad in (WURZEL / "strings.json", WURZEL / "translations" / "de.json"):
        text = pfad.read_text(encoding="utf-8")
        assert "Konfigurieren" not in text, pfad.name


def test_die_uebersicht_erstellt_sofort_ohne_eigenen_bestaetigungsschritt() -> None:
    """Kein separates Formular mehr fuer "summary" — der Schritt legt den
    Eintrag direkt an.

    Fruehere Fassung zeigte hier ein leeres Formular mit nur einem Knopf,
    um vor dem Abschluss noch einmal zu bestaetigen. Live am 17.09.2026
    aufgefallen: genau dieser zusaetzliche, unauffaellige Knopf wurde
    uebersehen — vier Anbieter eingerichtet, aber kein Config Entry
    entstanden. "Fertig" in der Menue-Auswahl davor war die Bestaetigung
    schon; ein zweiter Klick bot nur eine weitere Gelegenheit, den Dialog
    versehentlich zu schliessen. Dieser Test haelt fest, dass "summary" nie
    wieder ein eigener sichtbarer Formular- oder Menue-Schritt wird.
    """
    je_klasse = _schritte_je_klasse()
    assert "summary" not in je_klasse.get("FreeAIRouterConfigFlow", set())
    assert "summary" not in _texte()["config"]["step"]


def test_create_entry_platzhalter_werden_gefuellt() -> None:
    """``config.create_entry`` bekommt keinen eigenen Test wie ``step`` —
    ohne diesen wuerde ein vergessener Platzhalter dort erst live auffallen,
    genau wie beim Menue-Titel-Fehler zuvor.
    """
    import re

    quelle = QUELLE.read_text(encoding="utf-8")
    fehlend: list[str] = []
    for name, text in _block("config")["create_entry"].items():
        for platzhalter in re.findall(r"\{(\w+)\}", text):
            if f'"{platzhalter}"' not in quelle:
                fehlend.append(f"config.create_entry.{name}: {{{platzhalter}}}")
    assert not fehlend, f"im Code nicht gesetzt: {fehlend}"


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
