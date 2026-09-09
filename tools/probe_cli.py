#!/usr/bin/env python3
"""Probe-CLI — Schritt 0, ohne Home Assistant.

Liest Schluessel aus ``.env``, faehrt das Faehigkeitsmodul gegen alle
Anbieter und gibt eine Tabelle aus. Dasselbe Modul laeuft spaeter im Config
Flow und in Phase 3 unter Cron; dieses Skript ist nur die Kommandozeile davor.

Benutzung:

    python tools/probe_cli.py                      # alles pruefen
    python tools/probe_cli.py --provider groq      # nur einen Anbieter
    python tools/probe_cli.py --cheap              # nur Liveness + Header
    python tools/probe_cli.py --discover           # Modell-Listen abgleichen
    python tools/probe_cli.py --json report.json   # Rohbefunde wegschreiben

Ohne Schluessel in ``.env`` wird der jeweilige Anbieter uebersprungen.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import aiohttp  # noqa: E402

from custom_components.free_ai_router.capabilities import (  # noqa: E402
    ALL_CHECKS,
    CHEAP_CHECKS,
    ProviderProbe,
    probe_provider,
)
from custom_components.free_ai_router.registry import (  # noqa: E402
    Provider,
    Registry,
    load_registry,
)

ENV_PREFIX = "FAR_KEY_"


# --------------------------------------------------------------------------
# .env
# --------------------------------------------------------------------------


def load_env(path: Path) -> dict[str, str]:
    """Minimaler .env-Leser — bewusst ohne Zusatzabhaengigkeit."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            values[name.strip()] = value
    return values


def key_for(provider_id: str, env: dict[str, str]) -> str:
    import os

    name = f"{ENV_PREFIX}{provider_id.upper()}"
    return env.get(name) or os.environ.get(name, "")


# --------------------------------------------------------------------------
# Ausgabe
# --------------------------------------------------------------------------

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"

_USE_COLOR = sys.stdout.isatty()


def paint(text: str, color: str) -> str:
    return f"{color}{text}{RESET}" if _USE_COLOR else text


def mark(value: bool | None) -> str:
    if value is None:
        return paint("  ?  ", DIM)
    return paint(" ja  ", GREEN) if value else paint("nein ", RED)


def seconds(value: float | None) -> str:
    return f"{value:6.2f}" if value is not None else "     -"


def print_table(results: Iterable[ProviderProbe], registry: Registry) -> None:
    header = (
        f"{'Modell':<38} {'lebt':^5} {'Vision':^6} {'Struct':^6} {'Tools':^6} "
        f"{'TTFT':>6} {'gesamt':>7}"
    )
    for result in results:
        provider = registry.require(result.provider_id)
        print()
        status = paint("Key ok", GREEN) if result.key_valid else paint("Key nicht nutzbar", RED)
        print(f"{paint(provider.name, BOLD)}  [{provider.api_style}]  {status}")
        if result.error:
            print(f"  {paint(result.error[:160], RED)}")
        print(f"  {paint(header, DIM)}")

        for probe in result.models:
            name = probe.model_id[:38]
            print(
                f"  {name:<38} {mark(probe.alive if probe.liveness.ok is not None else None)}"
                f" {mark(probe.vision.ok):^6} {mark(probe.structured_output.ok):^6}"
                f" {mark(probe.tools.ok):^6}"
                f" {seconds(probe.ttft_s)} {seconds(probe.latency_total_s)}"
            )
            for label, check in (
                ("lebt", probe.liveness),
                ("vision", probe.vision),
                ("struct", probe.structured_output),
                ("tools", probe.tools),
            ):
                if check.ok is False and check.detail:
                    print(f"      {paint(f'{label}: {check.detail[:110]}', DIM)}")

        stale = result.stale_registry_entries
        if stale:
            print(
                "  "
                + paint(
                    f"Registry veraltet — Anbieter fuehrt nicht mehr: {', '.join(stale)}",
                    YELLOW,
                )
            )


def print_rate_limit_report(results: Iterable[ProviderProbe], registry: Registry) -> None:
    """Rohe Rate-Limit-Header je api_style.

    Daraus entsteht die Normalisierungstabelle in ``ratelimit.py`` — deshalb
    roh und ungefiltert, nicht huebsch.
    """
    print()
    print(paint("Rate-Limit-Header (roh, je api_style)", BOLD))
    by_style: dict[str, dict[str, set[str]]] = {}
    for result in results:
        style = registry.require(result.provider_id).api_style
        bucket = by_style.setdefault(style, {})
        for probe in result.models:
            for name, value in probe.rate_limit.raw.items():
                bucket.setdefault(name, set()).add(f"{probe.model_id}={value}")

    if not any(by_style.values()):
        print(f"  {paint('kein einziger Rate-Limit-Header gesehen', DIM)}")
        return

    for style, headers in sorted(by_style.items()):
        print(f"  {paint(style, BOLD)}")
        if not headers:
            print(f"    {paint('keine', DIM)}")
            continue
        for name in sorted(headers):
            examples = sorted(headers[name])[:3]
            print(f"    {name:<42} {paint(', '.join(examples)[:100], DIM)}")


def print_profile_coverage(results: Iterable[ProviderProbe], registry: Registry) -> None:
    """Welches Profil wird bedient, wo bleibt eine Luecke?"""
    from custom_components.free_ai_router.const import PROFILE_LABELS_DE, PROFILES

    coverage: dict[str, list[str]] = {profile: [] for profile in PROFILES}
    for result in results:
        provider = registry.require(result.provider_id)
        for probe in result.models:
            if not probe.alive:
                continue
            model = provider.model(probe.model_id)
            if model is None:
                continue
            for profile in model.profiles:
                if profile == "vision" and probe.vision.ok is False:
                    continue
                coverage[profile].append(probe.key)

    print()
    print(paint("Profil-Abdeckung", BOLD))
    for profile in PROFILES:
        entries = coverage[profile]
        label = PROFILE_LABELS_DE[profile]
        if entries:
            print(f"  {label:<12} {paint(str(len(entries)) + ' Kanaele', GREEN)}  {entries[0]}")
            for entry in entries[1:]:
                print(f"  {'':<12} {paint('Reserve', DIM)}      {entry}")
        else:
            print(f"  {label:<12} {paint('keine Abdeckung', RED)}")


# --------------------------------------------------------------------------
# Ablauf
# --------------------------------------------------------------------------


async def list_models(selected: list[tuple[Provider, str]]) -> int:
    """Nur nachsehen, welche Modelle der Anbieter fuehrt.

    Ein einziger Request je Anbieter. Wichtig, weil das Vermessen vier Aufrufe
    je Modell kostet — bei einem Modell mit 20 Anfragen pro Tag ist das ein
    Fuenftel des Tagesbudgets.
    """
    from custom_components.free_ai_router.adapters import get_adapter

    async with aiohttp.ClientSession() as session:
        for provider, api_key in selected:
            adapter = get_adapter(provider.api_style)
            print()
            print(paint(provider.name, BOLD))
            try:
                verfuegbar = await adapter.list_models(
                    session, provider, api_key, timeout=30.0
                )
            except Exception as err:  # noqa: BLE001 - Diagnoseausgabe
                print(f"  {paint(repr(err)[:200], RED)}")
                continue

            eingetragen = {model.id for model in provider.models}
            for name in sorted(verfuegbar):
                marke = paint(" [in Registry]", GREEN) if name in eingetragen else ""
                print(f"  {name}{marke}")

            fehlend = sorted(eingetragen - set(verfuegbar))
            if fehlend:
                print(
                    "  "
                    + paint(f"Registry nennt, Anbieter nicht: {', '.join(fehlend)}", YELLOW)
                )
    return 0


async def run(args: argparse.Namespace) -> int:
    registry = load_registry()
    env = load_env(REPO_ROOT / args.env)

    selected: list[tuple[Provider, str]] = []
    skipped: list[str] = []
    for provider in registry:
        if args.provider and provider.id not in args.provider:
            continue
        api_key = key_for(provider.id, env)
        if not api_key:
            skipped.append(provider.id)
            continue
        selected.append((provider, api_key))

    if skipped:
        print(
            paint(
                f"Uebersprungen (kein Schluessel in {args.env}): {', '.join(skipped)}",
                YELLOW,
            )
        )
    if not selected:
        print(paint("Kein einziger Schluessel gefunden. .env.example kopieren.", RED))
        return 2

    if args.models:
        return await list_models(selected)

    checks = CHEAP_CHECKS if args.cheap else ALL_CHECKS
    results: list[ProviderProbe] = []

    def progress(done: int, total: int, label: str) -> None:
        if _USE_COLOR:
            print(f"\r{DIM}[{done}/{total}] {label[:60]:<60}{RESET}", end="", flush=True)

    timeout = aiohttp.ClientTimeout(total=None)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for provider, api_key in selected:
            result = await probe_provider(
                session,
                provider,
                api_key,
                checks=checks,
                discover=args.discover,
                concurrency=args.concurrency,
                on_progress=progress,
            )
            results.append(result)
    if _USE_COLOR:
        print("\r" + " " * 72 + "\r", end="")

    print_table(results, registry)
    print_rate_limit_report(results, registry)
    print_profile_coverage(results, registry)

    if args.json:
        payload: dict[str, Any] = {
            "checks": list(checks),
            "providers": [result.as_dict() for result in results],
        }
        Path(args.json).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nRohbefunde: {args.json}")

    alive = sum(len(result.working_models) for result in results)
    print(f"\n{alive} von {sum(len(r.models) for r in results)} Modellen erreichbar.")
    return 0 if alive else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Anbieter-Faehigkeiten vermessen")
    parser.add_argument("--env", default=".env", help="Pfad zur .env (relativ zum Repo)")
    parser.add_argument(
        "--provider", action="append", help="nur diesen Anbieter (mehrfach moeglich)"
    )
    parser.add_argument(
        "--cheap", action="store_true", help="nur Liveness und Header, keine teuren Tests"
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Modell-Liste des Anbieters gegen die Registry halten",
    )
    parser.add_argument("--concurrency", type=int, default=2, help="parallele Anfragen je Anbieter")
    parser.add_argument(
        "--models",
        action="store_true",
        help="nur die Modell-Liste des Anbieters holen (ein Request, kein Kontingent)",
    )
    parser.add_argument("--json", help="Rohbefunde als JSON wegschreiben")
    args = parser.parse_args()

    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
