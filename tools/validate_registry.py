#!/usr/bin/env python3
"""Registry-Dateien pruefen — gegen JSON-Schema, Loader und Host-Allowlist.

Gedacht als CI-Schritt fuer Contributor-PRs (Phase 2): ein fehlerhafter PR
soll sich selbst erklaeren, statt auf eine Antwort zu warten. Laeuft schon
jetzt lokal:

    python tools/validate_registry.py
    python tools/validate_registry.py pfad/zu/einer.yaml

Rueckgabewert 0 = alles in Ordnung.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from custom_components.free_ai_router.allowlist import (  # noqa: E402
    ALLOWED_HOSTS,
    HostNotAllowedError,
    check_url,
)
from custom_components.free_ai_router.const import PROFILES  # noqa: E402
from custom_components.free_ai_router.registry import (  # noqa: E402
    DEFAULT_PROVIDER_DIR,
    RegistryError,
    load_provider_file,
)

SCHEMA_PATH = DEFAULT_PROVIDER_DIR.parent / "registry_schema.json"


def check_file(path: Path, schema: dict | None) -> list[str]:
    fehler: list[str] = []

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        return [f"kein gueltiges YAML: {err}"]

    if schema is not None:
        import jsonschema

        validator = jsonschema.Draft202012Validator(schema)
        for problem in sorted(validator.iter_errors(raw), key=lambda e: list(e.path)):
            stelle = "/".join(str(part) for part in problem.path) or "(Wurzel)"
            fehler.append(f"Schema: {stelle}: {problem.message}")

    try:
        provider = load_provider_file(path)
    except (RegistryError, HostNotAllowedError) as err:
        fehler.append(f"Loader: {err}")
        return fehler

    try:
        check_url(provider.base_url, source=path.name)
    except HostNotAllowedError as err:
        fehler.append(str(err))

    unbekannt = {
        profile
        for model in provider.models
        for profile in model.profiles
        if profile not in PROFILES
    }
    if unbekannt:
        fehler.append(f"unbekannte Profile: {sorted(unbekannt)}")

    if not provider.onboarding.signup_url.count("/") >= 3:
        fehler.append(
            "onboarding.signup_url sieht nach einer Startseite aus — "
            "es soll der Deep-Link zur Key-Seite sein"
        )

    return fehler


def main() -> int:
    argumente = sys.argv[1:]
    dateien = (
        [Path(arg) for arg in argumente]
        if argumente
        else sorted(DEFAULT_PROVIDER_DIR.glob("*.yaml"))
    )
    if not dateien:
        print("Keine Dateien gefunden.")
        return 1

    schema: dict | None = None
    try:
        import jsonschema  # noqa: F401

        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except ImportError:
        print("Hinweis: jsonschema nicht installiert, nur Loader-Pruefung.\n")

    fehlerhaft = 0
    for path in dateien:
        fehler = check_file(path, schema)
        if fehler:
            fehlerhaft += 1
            print(f"FEHLER {path.name}")
            for eintrag in fehler:
                print(f"   {eintrag}")
        else:
            print(f"ok     {path.name}")

    print(f"\nErlaubte Hosts: {', '.join(sorted(ALLOWED_HOSTS))}")
    print(
        "Ein neuer Host verlangt eine Aenderung an allowlist.py — das ist "
        "Absicht und ein Code-Review-Vorgang."
    )
    return 1 if fehlerhaft else 0


if __name__ == "__main__":
    raise SystemExit(main())
