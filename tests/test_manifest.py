"""Jeder Fremdimport ohne Schutznetz braucht ein Requirement.

Home Assistant installiert fuer eine Custom Component nur, was in ihrem
eigenen ``manifest.json`` unter ``requirements`` steht — nicht, was zufaellig
schon da ist, weil eine andere Integration dasselbe Paket mitgebracht hat.
Genau das ist am 18.09.2026 live passiert: ``voluptuous_openapi`` lief in
jedem frueheren Testlauf durch, weil es auf dem Testsystem schon installiert
war. Nach der ersten echten Installation ueber HACS auf einem sonst leeren
System fehlte es, und ``ai_task`` liess sich nicht mehr laden —
``ModuleNotFoundError`` mitten im Setup, kein einziger Entity entstand.

Dieser Test haette den Fehler vorher gefunden: er verlangt fuer jeden Import,
der nicht durch ein ``try``/``except`` abgesichert ist, entweder Standard-
bibliothek, ``homeassistant`` selbst, das eigene Paket — oder einen
passenden Eintrag in ``manifest.json``.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent / "custom_components" / "free_ai_router"

#: Import-Name -> Paketname auf PyPI, wo sie sich unterscheiden.
_PAKETNAME = {
    "yaml": "pyyaml",
    "voluptuous_openapi": "voluptuous-openapi",
}

#: Was Home Assistant selbst als Abhaengigkeit garantiert (``aiohttp``,
#: ``voluptuous``) oder was im Code bewusst per try/except abgesichert ist
#: und deshalb ohne das Paket sauber degradiert statt den Start zu
#: verhindern (``PIL`` in ``imaging.py``, ``cryptography`` in ``feed.py`` —
#: beide schon im Quelltext mit ``# pragma: no cover - in HA immer
#: vorhanden`` kommentiert). Diese Faelle brauchen kein eigenes
#: Requirement, unabhaengig davon, ob der jeweilige Import zufaellig
#: innerhalb eines try-Blocks im selben File steht.
_AUSGENOMMEN = {"aiohttp", "voluptuous", "pil", "cryptography"}


def _geschuetzte_importe(baum: ast.Module) -> set[str]:
    """Top-Level-Namen jedes Imports, der irgendwo in einem ``try``-Koerper
    dieser Datei steht — dort ist ein fehlendes Paket eine bewusste, im Code
    abgefangene Moeglichkeit, kein blinder Fleck.
    """
    namen: set[str] = set()
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.Try):
            continue
        for zweig in knoten.body:
            for unter in ast.walk(zweig):
                if isinstance(unter, ast.Import):
                    namen.update(a.name.split(".")[0] for a in unter.names)
                elif isinstance(unter, ast.ImportFrom) and not unter.level:
                    namen.add((unter.module or "").split(".")[0])
    return namen


def test_alle_ungeschuetzten_fremdimporte_stehen_im_manifest() -> None:
    manifest = json.loads((WURZEL / "manifest.json").read_text(encoding="utf-8"))
    deklariert = {
        re.split(r"[<>=!~\[]", eintrag, maxsplit=1)[0].lower()
        for eintrag in manifest["requirements"]
    }

    fehlend: list[str] = []
    for pfad in sorted(WURZEL.rglob("*.py")):
        baum = ast.parse(pfad.read_text(encoding="utf-8"), filename=str(pfad))
        geschuetzt = _geschuetzte_importe(baum)

        for knoten in ast.walk(baum):
            if isinstance(knoten, ast.Import):
                namen = [a.name.split(".")[0] for a in knoten.names]
            elif isinstance(knoten, ast.ImportFrom) and not knoten.level:
                namen = [(knoten.module or "").split(".")[0]]
            else:
                continue

            for name in namen:
                if (
                    not name
                    or name in sys.stdlib_module_names
                    or name == "homeassistant"
                    or name in geschuetzt
                    or name.lower() in _AUSGENOMMEN
                ):
                    continue
                pypi_name = _PAKETNAME.get(name, name).lower()
                if pypi_name not in deklariert:
                    fehlend.append(
                        f"{pfad.relative_to(WURZEL)}: {name!r} "
                        f"(erwartet Requirement {pypi_name!r})"
                    )

    assert not fehlend, (
        "Import ohne try/except und ohne passendes Requirement in "
        f"manifest.json: {fehlend}"
    )
