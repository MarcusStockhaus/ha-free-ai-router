"""Der Feed: Messergebnisse aus fremder Hand, signiert.

Anbieter aendern ihre Modelle und Limits schneller, als eine Integration
Releases macht. Der Feed-Dienst (Phase 3) vermisst sie regelmaessig mit einem
*eigenen* Konto und veroeffentlicht das Ergebnis als statische Datei. Diese
Datei ist damit die einzige Stelle, an der fremde Daten in eine sonst
geschlossene Integration laufen — entsprechend eng sind die Regeln.

Vier Linien, von aussen nach innen:

1. **Signatur.** Ohne gueltige Ed25519-Signatur ueber die *rohen Bytes* der
   heruntergeladenen Datei gibt es kein Dokument. :func:`parse_feed` nimmt
   Bytes und Signatur zusammen entgegen; einen Weg, an den Inhalt zu kommen,
   ohne vorher zu pruefen, gibt es in diesem Modul nicht.
2. **Frische.** Der Client rechnet das Alter aus ``generated_at`` gegen seine
   eigene Obergrenze. Ein vom Feed selbst mitgeliefertes Ablaufdatum wird
   bewusst *nicht* ausgewertet: ein stehengebliebener oder uebernommener
   Dienst koennte sich sonst selbst fuer gueltig erklaeren.
3. **Kein Rueckschritt.** Wer das letzte gesehene ``generated_at`` mitgibt,
   bekommt ein aelteres Dokument abgelehnt. Sonst liesse sich ein altes,
   korrekt signiertes Dokument erneut einspielen.
4. **Die Allowlist.** Anbieter aus dem Feed laufen durch dieselbe strenge
   :func:`registry.Provider.parse` wie eine mitgelieferte YAML-Datei — samt
   :mod:`allowlist`. Der Feed darf Modelle, Limits und Texte aendern.
   Niemals, wohin Daten fliessen.

Das Dokument trennt **Definition** von **Beobachtung**: ``providers`` hat exakt
die Form der Registry-Dateien, ``measurements`` haelt je Modell, was gemessen
wurde. Deshalb braucht die Definition keinen eigenen Parser, und die Messung
kann keine Felder einschleusen.

Kein ``homeassistant``-Import und kein ``aiohttp`` — das Holen steht in
:mod:`feed_client`, damit dieses Modul auch im Prober laeuft.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from .allowlist import HostNotAllowedError
from .registry import (
    Model,
    Provider,
    Registry,
    RegistryError,
    apply_measured_capabilities,
    apply_measured_limits,
)

#: Format des Dokuments. Ein Client lehnt eine hoehere Fassung ab, statt zu
#: raten — lieber ohne Feed weiterlaufen als halb verstandene Daten uebernehmen.
SCHEMA_VERSION = 1

#: Ab wann ein Dokument als veraltet gilt. Zwei Wochen sind grosszuegig gegen
#: einen Ausfall des Dienstes und trotzdem kurz genug, dass ein eingefrorener
#: Feed nicht dauerhaft falsche Limits verteilt.
MAX_AGE = timedelta(days=14)

#: Wie oft ein Modell hintereinander ausfallen muss, bevor der Feed es als tot
#: meldet. Ein einzelner Fehlschlag ist meistens das Netz des Probers, nicht
#: das Ende des Modells — und ein faelschlich entfernter Kanal faellt beim
#: Nutzer als Ausfall auf, nicht als Vorsicht.
DEAD_AFTER_FAILURES = 3

#: Oeffentlicher Schluessel des Feed-Dienstes, Base64 der rohen 32 Byte.
#:
#: Leer heisst: dieser Build vertraut keinem Feed. Genau wie bei der
#: Host-Allowlist ist eine Aenderung hier ein Code-Review-Vorgang und kein
#: Datenupdate — der Schluessel darf nie aus einer Datei nachgeladen werden,
#: sonst schuetzt die Signatur gegen nichts.
FEED_PUBLIC_KEY_B64 = ""

#: Adresse des Feeds. Leer heisst: dieser Build holt keinen Feed.
#:
#: Steht hier und nicht in einer Einstellung, weil eine vom Nutzer oder aus
#: Daten gesetzte Adresse die Signatur entwerten wuerde — wer die Quelle
#: waehlen darf, waehlt auch den Schluessel. Eine Allowlist wie fuer die
#: Anbieter-Endpunkte braucht es deshalb nicht: diese Adresse kann gar nicht
#: aus einer Datendatei kommen.
FEED_URL = "https://marcusstockhaus.github.io/ha-free-ai-router/v1/providers.json"

#: Felder, die eine Messung fuehren darf. Bewusst als Whitelist und nicht als
#: Verbotsliste: der Feed soll strukturelle Befunde transportieren und nichts
#: ueber das Konto des Probers — keine Kontostaende, keine Restkontingente
#: eines fremden Schluessels, keine Kennungen.
MEASUREMENT_FIELDS = frozenset(
    {
        "alive",
        "consecutive_failures",
        "capabilities",
        "limits",
        "latency_total_s",
        "ttft_s",
        "checked_at",
    }
)

_CAPABILITY_FIELDS = frozenset({"vision", "tools", "structured_output", "context_tokens"})
_LIMIT_FIELDS = frozenset({"rpm", "rpd", "tpm", "tpd"})


class FeedError(ValueError):
    """Das Dokument ist nicht brauchbar. Der Client laeuft dann ohne Feed."""


class FeedSignatureError(FeedError):
    """Signatur fehlt, passt nicht, oder es gibt keinen Schluessel zum Pruefen."""


class FeedStaleError(FeedError):
    """Zu alt oder aelter als das zuletzt gesehene Dokument."""


# --------------------------------------------------------------------------
# Signatur
# --------------------------------------------------------------------------


def verify_signature(
    raw: bytes,
    signature_b64: str,
    *,
    public_key_b64: str = FEED_PUBLIC_KEY_B64,
) -> None:
    """Pruefe die Ed25519-Signatur ueber genau diese Bytes.

    Geprueft werden die heruntergeladenen Rohbytes, nicht eine kanonische
    Fassung des geparsten Inhalts. Das erspart eine Kanonisierung — und damit
    die ganze Fehlerklasse "signiert wurde etwas anderes als gelesen".
    """
    if not public_key_b64:
        raise FeedSignatureError(
            "Dieser Build hat keinen Feed-Schluessel eincompiliert — kein Feed."
        )
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as err:  # pragma: no cover - in HA immer vorhanden
        raise FeedSignatureError(f"cryptography fehlt, Signatur nicht pruefbar: {err}") from err

    try:
        key_bytes = base64.b64decode(public_key_b64, validate=True)
        signature = base64.b64decode(signature_b64.strip(), validate=True)
    except (ValueError, TypeError) as err:
        raise FeedSignatureError(f"Signatur oder Schluessel ist kein Base64: {err}") from err

    try:
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(key_bytes)
    except ValueError as err:
        raise FeedSignatureError(f"unbrauchbarer oeffentlicher Schluessel: {err}") from err

    try:
        public_key.verify(signature, raw)
    except InvalidSignature as err:
        raise FeedSignatureError("Signatur passt nicht zum Inhalt") from err


# --------------------------------------------------------------------------
# Dokument
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Measurement:
    """Was der Prober an einem Modell beobachtet hat."""

    alive: bool
    consecutive_failures: int = 0
    capabilities: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    latency_total_s: float | None = None
    ttft_s: float | None = None
    checked_at: str = ""

    @property
    def is_dead(self) -> bool:
        """Tot heisst: mehrfach hintereinander nicht erreichbar."""
        return not self.alive and self.consecutive_failures >= DEAD_AFTER_FAILURES

    @classmethod
    def parse(cls, data: Any, where: str) -> Measurement:
        if not isinstance(data, dict):
            raise FeedError(f"{where}: Zuordnung erwartet")
        unknown = sorted(set(data) - MEASUREMENT_FIELDS)
        if unknown:
            raise FeedError(f"{where}: unbekannte Felder {unknown}")
        alive = data.get("alive")
        if not isinstance(alive, bool):
            raise FeedError(f"{where}.alive: true/false erwartet")

        failures = data.get("consecutive_failures", 0)
        if not isinstance(failures, int) or isinstance(failures, bool) or failures < 0:
            raise FeedError(f"{where}.consecutive_failures: ganze Zahl >= 0 erwartet")

        return cls(
            alive=alive,
            consecutive_failures=failures,
            capabilities=_subset(
                data.get("capabilities"), _CAPABILITY_FIELDS, f"{where}.capabilities"
            ),
            limits=_subset(data.get("limits"), _LIMIT_FIELDS, f"{where}.limits"),
            latency_total_s=_opt_float(data.get("latency_total_s"), f"{where}.latency_total_s"),
            ttft_s=_opt_float(data.get("ttft_s"), f"{where}.ttft_s"),
            checked_at=str(data.get("checked_at", "")),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "alive": self.alive,
            "consecutive_failures": self.consecutive_failures,
            "capabilities": dict(self.capabilities),
            "limits": dict(self.limits),
            "latency_total_s": self.latency_total_s,
            "ttft_s": self.ttft_s,
            "checked_at": self.checked_at,
        }


def _subset(value: Any, allowed: frozenset[str], where: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise FeedError(f"{where}: Zuordnung erwartet")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise FeedError(f"{where}: unbekannte Felder {unknown}")
    return dict(value)


def _opt_float(value: Any, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FeedError(f"{where}: Zahl oder leer erwartet")
    return float(value)


@dataclass(frozen=True, slots=True)
class FeedDocument:
    """Ein geprueftes Feed-Dokument. Anders ist es nicht zu bekommen."""

    schema_version: int
    generated_at: datetime
    providers: tuple[Provider, ...]
    measurements: dict[str, Measurement]
    notes_de: str = ""

    def age(self, now: datetime | None = None) -> timedelta:
        return (now or datetime.now(UTC)) - self.generated_at


def parse_feed(
    raw: bytes,
    signature_b64: str,
    *,
    now: datetime | None = None,
    seen_generated_at: datetime | None = None,
    max_age: timedelta = MAX_AGE,
    public_key_b64: str = FEED_PUBLIC_KEY_B64,
) -> FeedDocument:
    """Signatur, Frische und Inhalt pruefen — in genau dieser Reihenfolge.

    Erst nach bestandener Signatur wird ueberhaupt JSON geparst. Ein Parser
    ist Angriffsflaeche; er soll nur fremde Bytes sehen, die schon als echt
    bewiesen sind.
    """
    verify_signature(raw, signature_b64, public_key_b64=public_key_b64)

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise FeedError(f"kein gueltiges JSON: {err}") from err
    if not isinstance(data, dict):
        raise FeedError("Wurzel ist kein Objekt")

    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise FeedError(
            f"Schemafassung {version!r} wird nicht unterstuetzt (erwartet {SCHEMA_VERSION})"
        )

    generated_at = _parse_time(data.get("generated_at"), "generated_at")
    now = now or datetime.now(UTC)
    if generated_at - now > timedelta(hours=1):
        # Eine Stunde Spielraum fuer eine schiefe Uhr auf beiden Seiten.
        raise FeedStaleError(f"Dokument liegt in der Zukunft: {generated_at.isoformat()}")
    if now - generated_at > max_age:
        raise FeedStaleError(
            f"Dokument ist {(now - generated_at).days} Tage alt (Grenze: {max_age.days})"
        )
    if seen_generated_at is not None and generated_at < seen_generated_at:
        raise FeedStaleError(
            f"aelter als das zuletzt gesehene Dokument ({generated_at.isoformat()} "
            f"< {seen_generated_at.isoformat()})"
        )

    raw_providers = data.get("providers")
    if not isinstance(raw_providers, list):
        raise FeedError("providers: Liste erwartet")
    providers: list[Provider] = []
    for index, item in enumerate(raw_providers):
        where = f"providers[{index}]"
        try:
            # Dieselbe strenge Pruefung wie fuer eine mitgelieferte
            # YAML-Datei — einschliesslich der Host-Allowlist.
            providers.append(Provider.parse(item, where))
        except (RegistryError, HostNotAllowedError) as err:
            # Beide erben von ValueError, aber nicht voneinander: eine
            # abgelehnte Adresse kaeme sonst als fremder Ausnahmetyp heraus
            # und liefe am Aufrufer vorbei, der nur FeedError abfaengt.
            raise FeedError(f"{where}: {err}") from err

    ids = [provider.id for provider in providers]
    doppelt = sorted({pid for pid in ids if ids.count(pid) > 1})
    if doppelt:
        raise FeedError(f"providers: doppelte IDs {doppelt}")

    raw_measurements = data.get("measurements") or {}
    if not isinstance(raw_measurements, dict):
        raise FeedError("measurements: Zuordnung erwartet")
    measurements = {
        str(key): Measurement.parse(value, f"measurements[{key!r}]")
        for key, value in raw_measurements.items()
    }

    return FeedDocument(
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at,
        providers=tuple(providers),
        measurements=measurements,
        notes_de=str(data.get("notes_de", "")),
    )


def _parse_time(value: Any, where: str) -> datetime:
    if not isinstance(value, str):
        raise FeedError(f"{where}: Zeitstempel erwartet")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as err:
        raise FeedError(f"{where}: {value!r} ist kein ISO-Zeitstempel") from err
    if parsed.tzinfo is None:
        raise FeedError(f"{where}: Zeitzone fehlt in {value!r}")
    return parsed.astimezone(UTC)


# --------------------------------------------------------------------------
# Anwenden
# --------------------------------------------------------------------------


def apply_feed(base: Registry, document: FeedDocument) -> Registry:
    """Lege den Feed ueber die mitgelieferte Registry.

    * Ein Anbieter aus dem Feed ersetzt den gleichnamigen mitgelieferten.
    * Ein mitgelieferter Anbieter, den der Feed nicht nennt, bleibt stehen.
      Loeschen durch Weglassen waere zu leise: ein halb uebertragenes Dokument
      wuerde dem Nutzer stillschweigend Kanaele abschalten. Totmeldungen
      laufen ueber ``measurements``, wo sie begruendet sind.
    * Messungen ueberschreiben Faehigkeiten und Limits beider Quellen.
    * Mehrfach hintereinander tote Modelle fallen heraus.
    """
    zusammen: dict[str, Provider] = {provider.id: provider for provider in base.providers}
    for provider in document.providers:
        zusammen[provider.id] = provider

    ergebnis: list[Provider] = []
    for provider in zusammen.values():
        models: list[Model] = []
        for model in provider.models:
            measurement = document.measurements.get(model.key)
            if measurement is None:
                models.append(model)
                continue
            if measurement.is_dead:
                continue
            model = apply_measured_capabilities(model, measurement.capabilities)
            model = apply_measured_limits(model, measurement.limits)
            models.append(model)
        if not models:
            # Ein Anbieter ohne Modell ist kein Anbieter. Er verschwindet aus
            # der Auswahl, statt als leere Karte im Assistenten zu stehen.
            continue
        ergebnis.append(replace(provider, models=tuple(models)))

    ergebnis.sort(key=lambda provider: (provider.preference, provider.id))
    return Registry(providers=tuple(ergebnis))


# --------------------------------------------------------------------------
# Bauen (Prober-Seite)
# --------------------------------------------------------------------------


def build_document(
    provider_dicts: list[dict[str, Any]],
    measurements: dict[str, Measurement],
    *,
    generated_at: datetime,
    notes_de: str = "",
) -> dict[str, Any]:
    """Baue das Dokument aus rohen Anbieterdefinitionen und Messungen.

    ``provider_dicts`` sind die Anbieterdateien so, wie sie auf der Platte
    liegen — unveraendert weiterzugeben ist sicherer, als sie aus den
    Dataclasses zurueckzuschreiben, wo ein vergessenes Feld still verlorenginge.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.astimezone(UTC).isoformat(),
        "providers": provider_dicts,
        "measurements": {key: value.as_dict() for key, value in sorted(measurements.items())},
        "notes_de": notes_de,
    }


def dump_document(document: dict[str, Any]) -> bytes:
    """Serialisiere das Dokument zu genau den Bytes, die signiert werden.

    Eingerueckt und sortiert: die Datei soll sich von Hand lesen und zwischen
    zwei Laeufen als Diff vergleichen lassen. Signiert wird sie danach als
    Bytes, nicht als Struktur — deshalb ist die huebsche Form gratis.
    """
    text = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False)
    return text.encode("utf-8") + b"\n"


__all__ = [
    "DEAD_AFTER_FAILURES",
    "FEED_PUBLIC_KEY_B64",
    "FEED_URL",
    "MAX_AGE",
    "SCHEMA_VERSION",
    "FeedDocument",
    "FeedError",
    "FeedSignatureError",
    "FeedStaleError",
    "Measurement",
    "apply_feed",
    "build_document",
    "dump_document",
    "parse_feed",
    "verify_signature",
]
