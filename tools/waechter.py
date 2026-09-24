#!/usr/bin/env python3
"""Modell-Waechter — meldet neue und verschwundene Modelle als GitHub-Issue.

Laeuft taeglich als GitHub-Action (``.github/workflows/modell-waechter.yml``).
Je Anbieter:

1. Die Modell-Liste des Anbieters abrufen (``GET <base_url>/models``). Das
   kostet kein Kontingent.
2. Aussortieren, was fuer den Router nicht in Frage kommt: Audio-, Sprach-,
   Bild- und Embedding-Modelle, Aliase, Veraltetes, bei OpenRouter alles ohne
   ``:free``. Die Regeln stehen in :data:`REGELN`.
3. Mit der Anbieterdatei vergleichen: was ist neu, was ist verschwunden.
4. Jedes neue Modell **einmal** anmessen — mit derselben Messung, die die
   Integration in Home Assistant faehrt. Das Ergebnis steht im Issue und wird
   dort vermerkt, damit es am naechsten Tag nicht erneut Kontingent kostet.
5. Ein Issue je Anbieter anlegen oder aktualisieren. Ein geschlossenes Issue
   bleibt zu, bis sich die Liste der Kandidaten aendert; ohne Kandidaten wird
   ein offenes Issue geschlossen.

Was der Waechter nicht tut: Anbieterdateien aendern. Welches Profil ein
Modell bedient und welches Tageslimit gilt, ist eine redaktionelle
Entscheidung — die Modell-Listen der Anbieter nennen weder das eine noch das
andere. Die Aenderung geht als Release ueber HACS an alle Installationen, und
dort misst die Integration das Modell mit dem eigenen Schluessel nach.

    python tools/waechter.py --dry-run          # nur zeigen, nichts melden
    python tools/waechter.py                    # in der Action: Issues pflegen
    python tools/waechter.py --nur groq --dry-run

Schluessel wie beim Probe-CLI: ``FAR_KEY_<ANBIETER>`` aus der Umgebung oder
aus ``.env``. In der Action kommen ``GITHUB_TOKEN`` und ``GITHUB_REPOSITORY``
dazu.

Exitcode 4: mindestens ein Anbieter hat den Schluessel abgelehnt oder keine
Liste geliefert. Die uebrigen Anbieter sind dann trotzdem abgearbeitet.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

WURZEL = Path(__file__).resolve().parent.parent
if str(WURZEL) not in sys.path:
    sys.path.insert(0, str(WURZEL))

from custom_components.free_ai_router.adapters.base import ProviderAdapter  # noqa: E402
from custom_components.free_ai_router.capabilities import (  # noqa: E402
    bericht_zeilen,
    ist_voruebergehend,
    probe_model,
)
from custom_components.free_ai_router.registry import (  # noqa: E402
    Capabilities,
    Model,
    Provider,
    load_registry,
)
from tools.probe_cli import key_for, load_env  # noqa: E402

LABEL = "modell-waechter"
MARKE = "modell-waechter"
EXIT_SCHLUESSEL = 4

#: Hoechstens so viele neue Modelle misst ein Lauf je Anbieter. Eine Messung
#: kostet bis zu fuenf Aufrufe; OpenRouter hatte am 24.09.2026 achtzehn neue
#: kostenlose Modelle bei 50 Anfragen am Tag fuer alle zusammen. Der Rest
#: kommt in den folgenden Laeufen dran.
MESSUNGEN_JE_LAUF = 3

#: Was je Anbieter aussortiert wird, bevor verglichen wird. ``ausschliessen``
#: sind Teilzeichenketten der Modell-ID. Wer ein Modell dauerhaft nicht
#: gemeldet haben will, traegt es unter ``ignorieren`` ein.
REGELN: dict[str, dict[str, Any]] = {
    "google_ai_studio": {
        "ausschliessen": (
            "tts", "image", "embedding", "aqa", "live", "audio", "transcribe",
            "lyria", "robotics", "computer-use", "deep-research", "antigravity",
            "nano-banana", "customtools", "-latest", "omni",
        ),
        "ignorieren": (),
    },
    "groq": {
        "ausschliessen": ("whisper", "guard", "orpheus", "tts"),
        "ignorieren": (),
    },
    "mistral": {
        "ausschliessen": ("-latest", "labs-", "vibe", "voxtral", "-fim", "mistral-code"),
        "ignorieren": (),
    },
    "openrouter": {
        "ausschliessen": ("content-safety",),
        "ignorieren": (),
    },
}


# --------------------------------------------------------------------------
# Modell-Listen lesen
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Kandidat:
    """Ein Modell aus der Liste des Anbieters, mit dem, was die Liste verraet."""

    id: str
    bilder: bool | None = None
    werkzeuge: bool | None = None
    kontext: int | None = None


def modelle_aus_liste(provider_id: str, daten: dict[str, Any]) -> list[Kandidat]:
    """Die rohe Antwort von ``/models`` in Kandidaten uebersetzen.

    Jeder Anbieter liefert andere Felder — und nur manche verraten, ob ein
    Modell Bilder oder Werkzeuge kann. Google verraet es gar nicht.
    """
    if provider_id == "google_ai_studio":
        return [
            Kandidat(
                id=str(eintrag.get("name", "")).removeprefix("models/"),
                kontext=eintrag.get("inputTokenLimit"),
            )
            for eintrag in daten.get("models") or []
            if "generateContent" in (eintrag.get("supportedGenerationMethods") or [])
        ]

    eintraege = [e for e in daten.get("data") or [] if isinstance(e, dict) and e.get("id")]

    if provider_id == "groq":
        return [
            Kandidat(
                id=e["id"],
                bilder="image" in (e.get("input_modalities") or []),
                kontext=e.get("context_window"),
            )
            for e in eintraege
            if e.get("active", True) and "text" in (e.get("output_modalities") or ["text"])
        ]

    if provider_id == "mistral":
        # Mistral fuehrt Versionen und Aliase als eigene Eintraege. Gemeldet
        # werden nur Versionen — die Anbieterdatei pinnt Versionen, weil ein
        # Alias auf etwas zeigen kann, fuer das das Konto kein Kontingent hat.
        aliase = {alias for e in eintraege for alias in (e.get("aliases") or [])}
        return [
            Kandidat(
                id=e["id"],
                bilder=bool((e.get("capabilities") or {}).get("vision")),
                werkzeuge=bool((e.get("capabilities") or {}).get("function_calling")),
                kontext=e.get("max_context_length"),
            )
            for e in eintraege
            if (e.get("capabilities") or {}).get("completion_chat")
            and not e.get("deprecation")
            and e["id"] not in aliase
        ]

    if provider_id == "openrouter":
        kandidaten = []
        for e in eintraege:
            if not e["id"].endswith(":free"):
                continue
            parameter = set(e.get("supported_parameters") or [])
            kandidaten.append(
                Kandidat(
                    id=e["id"],
                    bilder="image" in ((e.get("architecture") or {}).get("input_modalities") or []),
                    werkzeuge="tools" in parameter,
                    kontext=e.get("context_length"),
                )
            )
        return kandidaten

    return [Kandidat(id=e["id"]) for e in eintraege]


def aussortieren(provider_id: str, kandidaten: list[Kandidat]) -> list[Kandidat]:
    regeln = REGELN.get(provider_id, {})
    ausschliessen = tuple(regeln.get("ausschliessen", ()))
    ignorieren = set(regeln.get("ignorieren", ()))
    return [
        k
        for k in kandidaten
        if k.id not in ignorieren and not any(teil in k.id for teil in ausschliessen)
    ]


@dataclass
class Befund:
    """Was sich bei einem Anbieter gegenueber der Anbieterdatei geaendert hat."""

    provider_id: str
    name: str
    neu: list[Kandidat] = field(default_factory=list)
    verschwunden: list[str] = field(default_factory=list)
    messungen: dict[str, str] = field(default_factory=dict)
    vorlaeufig: set[str] = field(default_factory=set)
    """Messungen, die nur eine Stoerung zeigten — nicht gemerkt, morgen neu."""
    fehler: str = ""

    @property
    def leer(self) -> bool:
        return not self.neu and not self.verschwunden

    @property
    def pruefsumme(self) -> str:
        """Aendert sich nur, wenn sich die Kandidaten aendern — nicht die Messung."""
        teile = sorted(f"+{k.id}" for k in self.neu) + sorted(f"-{m}" for m in self.verschwunden)
        return hashlib.sha256("\n".join(teile).encode()).hexdigest()[:16]


def vergleichen(provider: Provider, gelistet: list[Kandidat], alle_ids: set[str]) -> Befund:
    """Neu: gelistet und nach den Regeln relevant, aber nicht in der Datei.

    Verschwunden: in der Datei, aber gar nicht mehr in der Liste — gemessen an
    *allen* gelisteten IDs, nicht nur an den relevanten, damit eine neue
    Ausschlussregel kein Modell faelschlich als verschwunden meldet.
    """
    in_datei = {model.id for model in provider.models}
    neu = [k for k in aussortieren(provider.id, gelistet) if k.id not in in_datei]
    verschwunden = sorted(in_datei - alle_ids)
    return Befund(
        provider_id=provider.id,
        name=provider.name,
        neu=sorted(neu, key=lambda k: k.id),
        verschwunden=verschwunden,
    )


# --------------------------------------------------------------------------
# Issue-Text und Entscheidung
# --------------------------------------------------------------------------


def _ja_nein(wert: bool | None) -> str:
    return {True: "ja", False: "nein", None: "?"}[wert]


def issue_titel(befund: Befund) -> str:
    return f"Modell-Wächter: {befund.name}"


def issue_text(befund: Befund) -> str:
    zeilen = [
        f"**{befund.name}:** {len(befund.neu)} neu, {len(befund.verschwunden)} verschwunden.",
        "",
    ]
    if befund.neu:
        zeilen += [
            f"### Neu in der Modell-Liste, nicht in `providers/{befund.provider_id}.yaml`",
            "",
            "| Modell | laut Liste: Bilder | Werkzeuge | Kontext | eigene Messung |",
            "|---|---|---|---:|---|",
        ]
        for k in befund.neu:
            messung = befund.messungen.get(k.id, "wird in einem der nächsten Läufe gemessen")
            kontext = f"{k.kontext // 1000}k" if k.kontext else "?"
            zeilen.append(
                f"| `{k.id}` | {_ja_nein(k.bilder)} | {_ja_nein(k.werkzeuge)} "
                f"| {kontext} | {messung} |"
            )
        zeilen.append("")
    if befund.verschwunden:
        zeilen += [
            "### In der Anbieterdatei, aber nicht mehr in der Modell-Liste",
            "",
            *[f"- `{m}`" for m in befund.verschwunden],
            "",
        ]
    zeilen += [
        "### Was zu tun ist",
        "",
        "- Relevantes Modell: in die Anbieterdatei aufnehmen, Profil und Tageslimit "
        "festlegen, Release taggen. Das Tageslimit nennt keine Modell-Liste.",
        "- Verschwundenes Modell: aus der Anbieterdatei entfernen.",
        "- Nicht relevant: in `tools/waechter.py` unter `REGELN` → `ignorieren` eintragen, "
        "oder dieses Issue schließen. Es öffnet sich erst wieder, wenn sich die Liste ändert.",
        "",
        _marke(befund),
    ]
    return "\n".join(zeilen)


def _marke(befund: Befund) -> str:
    gemerkt = {k: v for k, v in befund.messungen.items() if k not in befund.vorlaeufig}
    daten = {"pruefsumme": befund.pruefsumme, "messungen": gemerkt}
    return f"<!-- {MARKE} {json.dumps(daten, ensure_ascii=False, sort_keys=True)} -->"


def marke_lesen(text: str) -> dict[str, Any]:
    treffer = re.search(rf"<!-- {MARKE} (\{{.*?\}}) -->", text or "", re.DOTALL)
    if not treffer:
        return {}
    try:
        return json.loads(treffer.group(1))
    except json.JSONDecodeError:
        return {}


AKTION_NICHTS = "nichts"
AKTION_ANLEGEN = "anlegen"
AKTION_AKTUALISIEREN = "aktualisieren"
AKTION_WIEDER_OEFFNEN = "wieder_oeffnen"
AKTION_SCHLIESSEN = "schliessen"


def entscheiden(befund: Befund, issue: dict[str, Any] | None) -> str:
    """Was mit dem Issue dieses Anbieters geschehen soll.

    Ein geschlossenes Issue bleibt zu, solange sich die Kandidaten nicht
    aendern: schliessen heisst "gesehen, nicht relevant". Ein offenes wird
    auch dann neu geschrieben, wenn nur Messungen dazugekommen sind.
    """
    if befund.fehler:
        return AKTION_NICHTS
    if befund.leer:
        return AKTION_SCHLIESSEN if issue and issue.get("state") == "open" else AKTION_NICHTS
    if issue is None:
        return AKTION_ANLEGEN
    gleich = marke_lesen(issue.get("body", "")).get("pruefsumme") == befund.pruefsumme
    if issue.get("state") == "open":
        return AKTION_NICHTS if issue.get("body") == issue_text(befund) else AKTION_AKTUALISIEREN
    return AKTION_NICHTS if gleich else AKTION_WIEDER_OEFFNEN


# --------------------------------------------------------------------------
# Netz: Anbieter und GitHub
# --------------------------------------------------------------------------


async def liste_holen(
    session: aiohttp.ClientSession, provider: Provider, api_key: str
) -> dict[str, Any]:
    params = ProviderAdapter.auth_params(provider, api_key)
    if provider.api_style == "google":
        params = {**params, "pageSize": "1000"}
    async with session.get(
        f"{provider.base_url}/models",
        headers={
            **ProviderAdapter.auth_headers(provider, api_key),
            "User-Agent": "free-ai-router-waechter",
        },
        params=params,
        timeout=aiohttp.ClientTimeout(total=45),
    ) as antwort:
        text = await antwort.text()
        if antwort.status >= 400:
            raise RuntimeError(f"HTTP {antwort.status}: {text[:160]}")
        return json.loads(text)


async def neue_messen(
    session: aiohttp.ClientSession,
    provider: Provider,
    api_key: str,
    befund: Befund,
    bekannt: dict[str, str],
) -> None:
    """Jedes neue Modell einmal messen; schon Gemessenes aus dem Issue uebernehmen."""
    gemessen = 0
    # Absteigend nach Name: bei versionierten Namen misst das die neuesten
    # zuerst (gemini-3.7 vor gemini-2.5), und die sind fast immer die
    # interessanteren.
    for kandidat in sorted(befund.neu, key=lambda k: k.id, reverse=True):
        if kandidat.id in bekannt:
            befund.messungen[kandidat.id] = bekannt[kandidat.id]
            continue
        if gemessen >= MESSUNGEN_JE_LAUF:
            continue
        gemessen += 1
        model = Model(
            id=kandidat.id,
            provider_id=provider.id,
            profiles=("schnell",),
            capabilities=Capabilities(
                vision=bool(kandidat.bilder),
                tools=bool(kandidat.werkzeuge),
                structured_output=False,
                context_tokens=kandidat.kontext or 1,
            ),
        )
        probe = await probe_model(session, provider, api_key, model)
        # Der Schluessel ist gueltig — die Liste kam ja mit ihm.
        if ist_voruebergehend(probe, schluessel_gueltig=True):
            befund.vorlaeufig.add(kandidat.id)
        zeile = bericht_zeilen([probe], schluessel_gueltig=True)[0]
        # "- **id** — Befund" → nur der Befund, die ID steht schon in der Tabelle.
        befund.messungen[kandidat.id] = zeile.split(" — ", 1)[-1].replace("|", "/")


class GitHub:
    """Das Noetigste der Issues-API, mit dem Token der Action."""

    def __init__(self, session: aiohttp.ClientSession, repo: str, token: str) -> None:
        self._session = session
        self._basis = f"https://api.github.com/repos/{repo}"
        self._kopf = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _aufruf(self, methode: str, pfad: str, daten: dict | None = None) -> Any:
        async with self._session.request(
            methode, f"{self._basis}{pfad}", headers=self._kopf, json=daten
        ) as antwort:
            if antwort.status == 404 and methode == "GET":
                return None
            if antwort.status >= 400:
                text = await antwort.text()
                raise RuntimeError(f"GitHub {methode} {pfad}: HTTP {antwort.status} {text}")
            return await antwort.json()

    async def label_sicherstellen(self) -> None:
        if await self._aufruf("GET", f"/labels/{LABEL}") is None:
            await self._aufruf(
                "POST",
                "/labels",
                {
                    "name": LABEL,
                    "color": "0e8a16",
                    "description": "Neue oder verschwundene Modelle",
                },
            )

    async def issues(self) -> dict[str, dict[str, Any]]:
        pfad = f"/issues?state=all&labels={LABEL}&per_page=100"
        treffer = await self._aufruf("GET", pfad) or []
        return {issue["title"]: issue for issue in treffer if "pull_request" not in issue}

    async def anlegen(self, titel: str, text: str) -> None:
        await self._aufruf("POST", "/issues", {"title": titel, "body": text, "labels": [LABEL]})

    async def bearbeiten(self, nummer: int, **felder: Any) -> None:
        await self._aufruf("PATCH", f"/issues/{nummer}", felder)


# --------------------------------------------------------------------------
# Ablauf
# --------------------------------------------------------------------------


async def lauf(args: argparse.Namespace) -> int:
    registry = load_registry()
    env = load_env(Path(args.env)) if args.env else {}
    anbieter = [p for p in registry if not args.nur or p.id in args.nur]

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    trocken = args.dry_run or not (repo and token)
    if not trocken:
        print(f"Issues in {repo}")
    else:
        print("Trockenlauf — es wird nichts gemeldet.")

    exitcode = 0
    async with aiohttp.ClientSession() as session:
        github = None if trocken else GitHub(session, repo, token)
        vorhanden: dict[str, dict[str, Any]] = {}
        if github:
            await github.label_sicherstellen()
            vorhanden = await github.issues()

        for provider in anbieter:
            api_key = key_for(provider.id, env)
            if not api_key:
                print(f"::warning::{provider.name}: kein Schluessel, uebersprungen")
                continue
            try:
                daten = await liste_holen(session, provider, api_key)
            except Exception as err:  # noqa: BLE001 - jeder Fehler ist hier ein Befund
                print(f"::error::{provider.name}: Modell-Liste nicht abrufbar — {err}")
                exitcode = EXIT_SCHLUESSEL
                continue

            gelistet = modelle_aus_liste(provider.id, daten)
            alle_ids = {k.id for k in gelistet} | {
                str(e.get("name", "")).removeprefix("models/") for e in daten.get("models") or []
            } | {str(e.get("id")) for e in daten.get("data") or [] if isinstance(e, dict)}
            befund = vergleichen(provider, gelistet, alle_ids)

            titel = issue_titel(befund)
            issue = vorhanden.get(titel)
            bekannt = marke_lesen(issue.get("body", "")).get("messungen", {}) if issue else {}
            if befund.neu:
                await neue_messen(session, provider, api_key, befund, bekannt)

            aktion = entscheiden(befund, issue)
            print(
                f"{provider.name}: {len(befund.neu)} neu, "
                f"{len(befund.verschwunden)} verschwunden → {aktion}"
            )
            if trocken:
                if not befund.leer:
                    print(issue_text(befund))
                    print()
                continue

            text = issue_text(befund)
            if aktion == AKTION_ANLEGEN:
                await github.anlegen(titel, text)
            elif aktion == AKTION_AKTUALISIEREN:
                await github.bearbeiten(issue["number"], body=text)
            elif aktion == AKTION_WIEDER_OEFFNEN:
                await github.bearbeiten(issue["number"], body=text, state="open")
            elif aktion == AKTION_SCHLIESSEN:
                await github.bearbeiten(issue["number"], state="closed", state_reason="completed")

    return exitcode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--dry-run", action="store_true", help="nichts melden, nur zeigen")
    parser.add_argument("--nur", nargs="*", help="nur diese Anbieter-IDs")
    parser.add_argument("--env", default=str(WURZEL / ".env"), help="Datei mit FAR_KEY_*")
    return asyncio.run(lauf(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
