"""Die Registry-Prüfung muss ohne HTTP-Bibliothek laufen.

Warum das eine eigene Testdatei wert ist: `tools/validate_registry.py` ist das
Werkzeug, das ein Contributor als Erstes anfasst, und der CI-Job dafür
installiert bewusst nur PyYAML und jsonschema. Ein einziger Import auf
Modulebene in `__init__.py` reicht, um das zu brechen — genau so ist der erste
CI-Lauf am 11.09.2026 gescheitert.

Geprüft wird in einem eigenen Prozess mit blockiertem ``aiohttp``. Im selben
Prozess ginge es nicht: die Bibliothek ist in der Entwicklungsumgebung
installiert und läge längst in ``sys.modules``.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent

BLOCKER = """
import builtins, sys
_echt = builtins.__import__

def _blocker(name, *a, **k):
    if name == "aiohttp" or name.startswith("aiohttp."):
        raise ModuleNotFoundError("No module named 'aiohttp'")
    return _echt(name, *a, **k)

builtins.__import__ = _blocker
sys.path.insert(0, {wurzel!r})
"""


def _ohne_aiohttp(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", BLOCKER.format(wurzel=str(WURZEL)) + textwrap.dedent(code)],
        capture_output=True,
        text=True,
        cwd=WURZEL,
    )


def test_registry_laedt_ohne_aiohttp() -> None:
    ergebnis = _ohne_aiohttp(
        """
        from custom_components.free_ai_router.registry import load_registry
        print(len(load_registry()))
        """
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert int(ergebnis.stdout.strip()) >= 3


def test_allowlist_laedt_ohne_aiohttp() -> None:
    ergebnis = _ohne_aiohttp(
        """
        from custom_components.free_ai_router.allowlist import check_url
        print(check_url("https://api.groq.com/openai/v1"))
        """
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert "api.groq.com" in ergebnis.stdout


def test_validate_registry_laeuft_ohne_aiohttp() -> None:
    """Der Ernstfall: genau das Werkzeug, das die CI im schlanken Job aufruft."""
    ergebnis = _ohne_aiohttp(
        """
        import runpy, sys
        sys.argv = ["validate_registry.py"]
        try:
            runpy.run_path("tools/validate_registry.py", run_name="__main__")
        except SystemExit as err:
            sys.exit(err.code or 0)
        """
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert "ok" in ergebnis.stdout


def test_der_blocker_blockt_wirklich() -> None:
    """Sonst prüften die Tests darüber gar nichts."""
    ergebnis = _ohne_aiohttp("import aiohttp")
    assert ergebnis.returncode != 0
    assert "aiohttp" in ergebnis.stderr
