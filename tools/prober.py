#!/usr/bin/env python3
"""Der Prober — vermisst die Anbieter unter Cron und baut den signierten Feed.

Dasselbe Faehigkeitsmodul, das der Einrichtungsassistent beim Nutzer fahrt,
laeuft hier regelmaessig mit einem **eigenen** Konto. Das Ergebnis wird zu
einer statischen Datei, signiert und irgendwohin gelegt, wo ein Webserver sie
ausliefert. Mehr Dienst ist es nicht — kein Server, keine Datenbank, kein
Endpunkt, der etwas entgegennimmt.

    python tools/prober.py --cheap                 # stuendlich: lebt es noch?
    python tools/prober.py --full                  # taeglich: alles messen
    python tools/prober.py --full --dry-run        # nur zeigen, nichts schreiben

Zwei Takte, weil der Prober nicht der groesste Verbraucher des Kontingents
sein darf, das er vermisst: ein Request je Modell und Stunde fuer die
Lebendigkeit, die teuren Pruefungen (Bild, Schema, Werkzeuge) einmal am Tag.

Drei Vorsichtsmassnahmen gegen Falschmeldungen — sie sind der eigentliche
Inhalt dieses Skripts, das Messen selbst steht in ``capabilities.py``:

* **Ein Lauf ohne jede lebende Antwort wird verworfen.** Das ist fast immer
  das Netz des Probers und nicht das Ende aller Anbieter gleichzeitig.
* **Abgelehnte Schluessel und Ratenlimits zaehlen nicht als Ausfall.** Ein
  abgelaufener Proberschluessel wuerde sonst allen Nutzern funktionierende
  Kanaele als tot melden, und ein 429 sagt ueber das Modell ueberhaupt
  nichts — der Endpunkt hat ja geantwortet. Ein abgelehnter Schluessel wird
  laut gemeldet und beendet den Lauf mit Fehlercode.
* **Tot erst nach mehreren Laeufen hintereinander** (``DEAD_AFTER_FAILURES``).

Der Zustand zwischen den Laeufen steht in einer eigenen Datei. Sie liegt im
selben oeffentlichen Repo wie der Feed und ist deshalb genauso zurueckhaltend:
festgehalten wird der Statuscode eines Fehlschlags, nicht der Antworttext.
Fehlerkoerper der Anbieter enthalten regelmaessig Organisations-IDs und
Kontingentangaben des Proberkontos. Veroeffentlicht wird ohnehin nur, was
``feed.Measurement`` als Feld kennt — die Zustandsdatei fuehrt darueber hinaus
nur die Buchfuehrung ueber mehrere Laeufe.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import aiohttp  # noqa: E402
import yaml  # noqa: E402

from custom_components.free_ai_router.capabilities import (  # noqa: E402
    ALL_CHECKS,
    CHEAP_CHECKS,
    ModelProbe,
    probe_provider,
)
from custom_components.free_ai_router.feed import (  # noqa: E402
    DEAD_AFTER_FAILURES,
    Measurement,
    build_document,
    dump_document,
)
from custom_components.free_ai_router.registry import (  # noqa: E402
    DEFAULT_PROVIDER_DIR,
    load_registry,
)
from tools.feed_keys import load_private_key, sign_bytes  # noqa: E402
from tools.probe_cli import key_for, load_env  # noqa: E402

STATE_VERSION = 1

#: Antworten, die eine Runde ungewertet lassen. Gemeinsam ist ihnen, dass der
#: Endpunkt geantwortet hat: er lebt, er wollte nur nicht messen lassen.
NICHT_GEWERTET = {
    401: "schluessel",
    402: "schluessel",
    403: "schluessel",
    429: "limit",
}


# --------------------------------------------------------------------------
# Zustand zwischen den Laeufen
# --------------------------------------------------------------------------


def load_state(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != STATE_VERSION:
        print(f"! Zustandsdatei hat Fassung {data.get('version')!r}, wird ignoriert")
        return {}
    models = data.get("models")
    return models if isinstance(models, dict) else {}


def save_state(path: Path, models: dict[str, dict[str, Any]], now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": STATE_VERSION,
                "updated_at": now.isoformat(),
                "models": dict(sorted(models.items())),
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def merge_probe(
    vorher: dict[str, Any] | None,
    probe: ModelProbe,
    *,
    full: bool,
    now: datetime,
) -> tuple[dict[str, Any], str]:
    """Verrechne eine Messung mit dem bisherigen Zustand.

    Rueckgabe: (neuer Zustand, Grund fuer eine nicht gewertete Runde).

    Bei einem Fehlschlag bleiben die zuletzt gemessenen Faehigkeiten stehen.
    Sie sind weiterhin das Beste, was wir wissen — ein Modell verliert nicht
    das Sehen, nur weil es gerade nicht antwortet.
    """
    eintrag: dict[str, Any] = dict(vorher or {})
    eintrag.setdefault("capabilities", {})
    eintrag.setdefault("limits", {})
    eintrag.setdefault("consecutive_failures", 0)

    if probe.alive:
        eintrag["alive"] = True
        eintrag["consecutive_failures"] = 0
        eintrag["latency_total_s"] = probe.latency_total_s
        eintrag["ttft_s"] = probe.ttft_s
        eintrag["checked_at"] = now.isoformat()
        eintrag.pop("last_status", None)
        gemessene_limits = probe.measured_limits()
        if gemessene_limits:
            eintrag["limits"] = {**eintrag["limits"], **gemessene_limits}
        if full:
            gemessene_faehigkeiten = probe.measured_capabilities()
            if gemessene_faehigkeiten:
                eintrag["capabilities"] = {
                    **eintrag["capabilities"],
                    **gemessene_faehigkeiten,
                }
            eintrag["last_full_at"] = now.isoformat()
        return eintrag, ""

    # Nur der Statuscode, nicht der Antworttext. Fehlerkoerper der Anbieter
    # enthalten regelmaessig Organisations-IDs und Kontingentangaben des
    # Proberkontos — und die Zustandsdatei liegt im oeffentlichen Repo neben
    # dem Feed. ``null`` heisst: keine HTTP-Antwort, also Netz oder Zeit.
    # Wer den Volltext braucht, faehrt den Prober von Hand.
    eintrag["last_status"] = probe.status
    eintrag["checked_at"] = now.isoformat()

    grund = NICHT_GEWERTET.get(probe.status or 0, "")
    if grund:
        # Der Endpunkt hat geantwortet, nur nicht mit einer Messung. Das sagt
        # nichts ueber das Modell — weder ``alive`` noch der Zaehler werden
        # angefasst. Sonst haette ein abgelaufener Proberschluessel oder eine
        # Stunde am Ratenlimit gereicht, um Nutzern gesunde Kanaele
        # abzuschalten. Am 11.09.2026 lief Mistral im ersten Livelauf genau
        # in diesen Fall.
        return eintrag, grund

    eintrag["alive"] = False
    eintrag["consecutive_failures"] = int(eintrag.get("consecutive_failures", 0)) + 1
    return eintrag, ""


def to_measurement(eintrag: dict[str, Any]) -> Measurement:
    """Der Ausschnitt des Zustands, der veroeffentlicht werden darf."""
    return Measurement(
        alive=bool(eintrag.get("alive", False)),
        consecutive_failures=int(eintrag.get("consecutive_failures", 0)),
        capabilities=dict(eintrag.get("capabilities") or {}),
        limits=dict(eintrag.get("limits") or {}),
        latency_total_s=eintrag.get("latency_total_s"),
        ttft_s=eintrag.get("ttft_s"),
        checked_at=str(eintrag.get("checked_at", "")),
    )


# --------------------------------------------------------------------------
# Der Lauf
# --------------------------------------------------------------------------


async def run_probes(
    providers: list[Any],
    keys: dict[str, str],
    *,
    full: bool,
    timeout: float,
) -> tuple[dict[str, ModelProbe], list[str]]:
    """Vermisst alle Anbieter, fuer die ein Schluessel da ist."""
    probes: dict[str, ModelProbe] = {}
    ohne_schluessel: list[str] = []
    checks = ALL_CHECKS if full else CHEAP_CHECKS

    async with aiohttp.ClientSession() as session:
        for provider in providers:
            api_key = keys.get(provider.id, "")
            if not api_key:
                ohne_schluessel.append(provider.id)
                continue
            print(f"  {provider.name} ...", flush=True)
            ergebnis = await probe_provider(
                session, provider, api_key, checks=checks, timeout=timeout
            )
            for probe in ergebnis.models:
                probes[probe.key] = probe
    return probes, ohne_schluessel


def write_site(
    ziel: Path,
    rohdokument: dict[str, Any],
    signatur: str,
    *,
    generated_at: datetime,
    history_keep: int,
) -> Path:
    """Schreibe ``v1/providers.json``, die Signatur, die Historie und den Index."""
    v1 = ziel / "v1"
    history = v1 / "history"
    history.mkdir(parents=True, exist_ok=True)

    rohbytes = dump_document(rohdokument)
    (v1 / "providers.json").write_bytes(rohbytes)
    (v1 / "providers.json.sig").write_text(signatur + "\n", encoding="utf-8")

    stempel = generated_at.astimezone(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    (history / f"{stempel}.json").write_bytes(rohbytes)
    (history / f"{stempel}.json.sig").write_text(signatur + "\n", encoding="utf-8")

    alte = sorted(history.glob("*.json"))
    for veraltet in alte[: max(0, len(alte) - history_keep)]:
        veraltet.unlink()
        veraltet.with_suffix(".json.sig").unlink(missing_ok=True)

    # Der Index ist fuer Menschen und bewusst unsigniert: ein Client liest
    # ausschliesslich providers.json samt Signatur. Was nicht geprueft wird,
    # soll auch nicht so aussehen, als wuerde es geprueft.
    (v1 / "index.json").write_text(
        json.dumps(
            {
                "schema_version": rohdokument["schema_version"],
                "current": "providers.json",
                "signature": "providers.json.sig",
                "generated_at": rohdokument["generated_at"],
                "history": [pfad.name for pfad in sorted(history.glob("*.json"))],
                "hinweis": (
                    "Clients lesen providers.json und pruefen providers.json.sig. "
                    "Diese Indexdatei ist unsigniert und nur zum Nachsehen."
                ),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return v1 / "providers.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    takt = parser.add_mutually_exclusive_group()
    takt.add_argument(
        "--cheap", action="store_true", help="nur Lebendigkeit und Header (stuendlich)"
    )
    takt.add_argument("--full", action="store_true", help="alle Pruefungen (taeglich)")
    parser.add_argument("--state", default="feed-state.json", help="Zustand zwischen den Laeufen")
    parser.add_argument("--out", default="site", help="Ausgabeverzeichnis der statischen Dateien")
    parser.add_argument("--providers", default=str(DEFAULT_PROVIDER_DIR))
    parser.add_argument("--key", default=None, help="PEM-Datei des Signierschluessels")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--history-keep", type=int, default=90)
    parser.add_argument("--notes", default="", help="Freitext, der im Dokument mitlaeuft")
    parser.add_argument("--dry-run", action="store_true", help="nichts schreiben, nur berichten")
    parser.add_argument(
        "--force-publish",
        action="store_true",
        help="auch veroeffentlichen, wenn kein einziges Modell geantwortet hat",
    )
    args = parser.parse_args(argv)

    full = args.full or not args.cheap
    now = datetime.now(UTC)
    provider_dir = Path(args.providers)

    registry = load_registry(provider_dir)
    # Die Definitionen gehen so weiter, wie sie auf der Platte liegen — aber
    # erst, nachdem load_registry sie durch Schema und Allowlist gezogen hat.
    provider_dicts = [
        yaml.safe_load(pfad.read_text(encoding="utf-8"))
        for pfad in sorted(provider_dir.glob("*.yaml"))
    ]

    env = load_env(Path(args.env))
    keys = {provider.id: key_for(provider.id, env) for provider in registry}

    print(f"Prober, {'voll' if full else 'sparsam'}, {now.isoformat()}")
    probes, ohne_schluessel = asyncio.run(
        run_probes(list(registry), keys, full=full, timeout=args.timeout)
    )

    lebendig = [probe for probe in probes.values() if probe.alive]
    if not lebendig and not args.force_publish:
        print(
            f"! Kein einziges Modell hat geantwortet ({len(probes)} versucht). "
            "Das ist wahrscheinlich das Netz hier und nicht die Anbieterlage — "
            "es wird nichts veroeffentlicht und nichts als Ausfall gezaehlt.",
            file=sys.stderr,
        )
        return 3

    state = load_state(Path(args.state))
    abgelehnt: list[str] = []
    gedrosselt: list[str] = []
    for key, probe in probes.items():
        state[key], grund = merge_probe(state.get(key), probe, full=full, now=now)
        if grund == "schluessel":
            abgelehnt.append(key)
        elif grund == "limit":
            gedrosselt.append(key)

    measurements = {key: to_measurement(eintrag) for key, eintrag in state.items()}

    dokument = build_document(
        provider_dicts,
        measurements,
        generated_at=now,
        notes_de=args.notes,
    )
    rohbytes = dump_document(dokument)

    tot = sorted(key for key, wert in measurements.items() if wert.is_dead)
    print()
    print(f"  {len(lebendig)} von {len(probes)} Modellen lebendig")
    if ohne_schluessel:
        print(f"  ohne Schluessel uebersprungen: {', '.join(ohne_schluessel)}")
    if tot:
        print(f"  als tot gemeldet (>= {DEAD_AFTER_FAILURES} Laeufe): {', '.join(tot)}")
    if gedrosselt:
        print(f"  am Ratenlimit, nicht gewertet: {', '.join(gedrosselt)}")
    if abgelehnt:
        print(
            f"! Schluessel abgelehnt bei: {', '.join(abgelehnt)} — "
            "nicht als Ausfall gezaehlt, aber der Proberschluessel gehoert erneuert.",
            file=sys.stderr,
        )

    if args.dry_run:
        print()
        print(rohbytes.decode("utf-8"))
        return 4 if abgelehnt else 0

    signatur = sign_bytes(load_private_key(path=args.key), rohbytes)
    ziel = write_site(
        Path(args.out),
        dokument,
        signatur,
        generated_at=now,
        history_keep=args.history_keep,
    )
    save_state(Path(args.state), state, now)
    print(f"  {ziel} ({len(rohbytes)} Bytes) und {ziel.name}.sig geschrieben")

    return 4 if abgelehnt else 0


if __name__ == "__main__":
    raise SystemExit(main())
