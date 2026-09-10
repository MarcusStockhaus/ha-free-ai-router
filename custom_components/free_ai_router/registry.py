"""Registry-Loader: YAML einlesen, validieren, Host-Allowlist pruefen.

Kein ``homeassistant``-Import — dieses Modul wird auch vom Probe-CLI benutzt.
Das Einlesen ist blockierend (Datei-IO); in HA gehoert es hinter
``async_add_executor_job``.

Die Validierung passiert hier strukturell beim Bau der Dataclasses. Das
mitgelieferte ``registry_schema.json`` ist die maschinenlesbare Fassung
derselben Regeln fuer CI und Contributor-PRs.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from .allowlist import check_url
from .const import API_STYLES, PROFILES


class RegistryError(ValueError):
    """Eine Registry-Datei ist unbrauchbar."""


# --------------------------------------------------------------------------
# Hilfen fuer strenges Parsen
# --------------------------------------------------------------------------


def _as_mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError(f"{where}: Zuordnung erwartet, gefunden {type(value).__name__}")
    return value


def _reject_unknown(data: dict[str, Any], known: set[str], where: str) -> None:
    unknown = sorted(set(data) - known)
    if unknown:
        raise RegistryError(f"{where}: unbekannte Felder {unknown}")


def _req_str(data: dict[str, Any], key: str, where: str) -> str:
    if key not in data:
        raise RegistryError(f"{where}: Pflichtfeld {key!r} fehlt")
    value = data[key]
    if not isinstance(value, str):
        raise RegistryError(f"{where}.{key}: Text erwartet, gefunden {type(value).__name__}")
    return value


def _req_list(data: dict[str, Any], key: str, where: str) -> list[Any]:
    if key not in data:
        raise RegistryError(f"{where}: Pflichtfeld {key!r} fehlt")
    value = data[key]
    if not isinstance(value, list):
        raise RegistryError(f"{where}.{key}: Liste erwartet, gefunden {type(value).__name__}")
    return value


def _req_bool(data: dict[str, Any], key: str, where: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise RegistryError(f"{where}.{key}: true/false erwartet")
    return value


def _opt_int(data: dict[str, Any], key: str, where: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RegistryError(f"{where}.{key}: positive ganze Zahl oder leer erwartet")
    return value


# --------------------------------------------------------------------------
# Datenmodell
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Capabilities:
    """Was ein Modell kann. Startwerte aus der Datei, gemessene Werte gewinnen."""

    vision: bool
    tools: bool
    structured_output: bool
    context_tokens: int

    @classmethod
    def parse(cls, data: Any, where: str) -> Capabilities:
        data = _as_mapping(data, where)
        _reject_unknown(data, {"vision", "tools", "structured_output", "context_tokens"}, where)
        tokens = data.get("context_tokens")
        if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 1:
            raise RegistryError(f"{where}.context_tokens: positive ganze Zahl erwartet")
        return cls(
            vision=_req_bool(data, "vision", where),
            tools=_req_bool(data, "tools", where),
            structured_output=_req_bool(data, "structured_output", where),
            context_tokens=tokens,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "vision": self.vision,
            "tools": self.tools,
            "structured_output": self.structured_output,
            "context_tokens": self.context_tokens,
        }


@dataclass(frozen=True, slots=True)
class Limits:
    """Kontingente. ``None`` heisst unveroeffentlicht, nicht unbegrenzt."""

    rpm: int | None = None
    rpd: int | None = None
    tpm: int | None = None
    tpd: int | None = None

    @classmethod
    def parse(cls, data: Any, where: str) -> Limits:
        if data is None:
            return cls()
        data = _as_mapping(data, where)
        _reject_unknown(data, {"rpm", "rpd", "tpm", "tpd"}, where)
        return cls(
            rpm=_opt_int(data, "rpm", where),
            rpd=_opt_int(data, "rpd", where),
            tpm=_opt_int(data, "tpm", where),
            tpd=_opt_int(data, "tpd", where),
        )

    def as_dict(self) -> dict[str, Any]:
        return {"rpm": self.rpm, "rpd": self.rpd, "tpm": self.tpm, "tpd": self.tpd}


@dataclass(frozen=True, slots=True)
class Pricing:
    """Token-Preise. Erst ab Phase 4 relevant, jetzt nur mitgefuehrt."""

    input_per_mtok: float | None = None
    output_per_mtok: float | None = None

    @classmethod
    def parse(cls, data: Any, where: str) -> Pricing:
        if data is None:
            return cls()
        data = _as_mapping(data, where)
        _reject_unknown(data, {"input_per_mtok", "output_per_mtok"}, where)
        values: dict[str, float | None] = {}
        for key in ("input_per_mtok", "output_per_mtok"):
            value = data.get(key)
            if value is None:
                values[key] = None
            elif isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                values[key] = float(value)
            else:
                raise RegistryError(f"{where}.{key}: Zahl >= 0 erwartet")
        return cls(**values)


@dataclass(frozen=True, slots=True)
class Onboarding:
    signup_url: str
    steps_de: tuple[str, ...]
    data_note_de: str
    credit_card_required: bool
    summary_de: str = ""

    @classmethod
    def parse(cls, data: Any, where: str) -> Onboarding:
        data = _as_mapping(data, where)
        _reject_unknown(
            data,
            {"signup_url", "steps_de", "data_note_de", "credit_card_required", "summary_de"},
            where,
        )
        url = _req_str(data, "signup_url", where)
        if not url.startswith("https://"):
            raise RegistryError(f"{where}.signup_url: muss https sein")
        steps = _req_list(data, "steps_de", where)
        if not steps or not all(isinstance(step, str) and step.strip() for step in steps):
            raise RegistryError(f"{where}.steps_de: mindestens ein nicht-leerer Text")
        note = _req_str(data, "data_note_de", where).strip()
        if not note:
            raise RegistryError(f"{where}.data_note_de: darf nicht leer sein")
        return cls(
            signup_url=url,
            steps_de=tuple(step.strip() for step in steps),
            data_note_de=note,
            credit_card_required=_req_bool(data, "credit_card_required", where),
            summary_de=str(data.get("summary_de", "")).strip(),
        )


LATENCY_CLASSES = ("sehr_schnell", "schnell", "normal", "langsam")


@dataclass(frozen=True, slots=True)
class Model:
    id: str
    provider_id: str
    profiles: tuple[str, ...]
    capabilities: Capabilities
    limits: Limits = field(default_factory=Limits)
    pricing: Pricing = field(default_factory=Pricing)
    label: str = ""
    latency_class: str = "normal"

    @property
    def key(self) -> str:
        """Eindeutig ueber alle Anbieter — Ledger- und Log-Schluessel."""
        return f"{self.provider_id}/{self.id}"

    @property
    def display_name(self) -> str:
        return self.label or self.id

    @classmethod
    def parse(cls, data: Any, provider_id: str, where: str) -> Model:
        data = _as_mapping(data, where)
        _reject_unknown(
            data,
            {"id", "label", "profiles", "capabilities", "limits", "pricing", "latency_class"},
            where,
        )
        model_id = _req_str(data, "id", where).strip()
        if not model_id:
            raise RegistryError(f"{where}.id: darf nicht leer sein")

        profiles = _req_list(data, "profiles", where)
        if not profiles:
            raise RegistryError(f"{where}.profiles: mindestens ein Profil")
        for profile in profiles:
            if profile not in PROFILES:
                raise RegistryError(
                    f"{where}.profiles: {profile!r} unbekannt (erlaubt: {list(PROFILES)})"
                )

        latency = str(data.get("latency_class", "normal"))
        if latency not in LATENCY_CLASSES:
            raise RegistryError(
                f"{where}.latency_class: {latency!r} unbekannt (erlaubt: {list(LATENCY_CLASSES)})"
            )

        if "capabilities" not in data:
            raise RegistryError(f"{where}: Pflichtfeld 'capabilities' fehlt")

        return cls(
            id=model_id,
            provider_id=provider_id,
            profiles=tuple(dict.fromkeys(profiles)),
            capabilities=Capabilities.parse(data["capabilities"], f"{where}.capabilities"),
            limits=Limits.parse(data.get("limits"), f"{where}.limits"),
            pricing=Pricing.parse(data.get("pricing"), f"{where}.pricing"),
            label=str(data.get("label", "")).strip(),
            latency_class=latency,
        )


LIMIT_SCOPES = ("per_model", "per_key")
AUTH_TYPES = ("header", "query", "bearer")


@dataclass(frozen=True, slots=True)
class Provider:
    id: str
    name: str
    api_style: str
    base_url: str
    auth_type: str
    auth_name: str
    onboarding: Onboarding
    models: tuple[Model, ...]
    extra_headers: tuple[tuple[str, str], ...] = ()
    limits_scope: str = "per_model"
    daily_reset_timezone: str = "UTC"
    monthly_budget_usd: float | None = None
    """Ausgabendeckel der kostenlosen Stufe, in US-Dollar je Monat.

    Kein Zeitfenster, sondern eine Geldgrenze — der Ledger kann sie mit
    Anfragen- und Tokenzaehlern nicht fuehren. Wird erst in Phase 4 zusammen
    mit ``pricing`` ausgewertet; bis dahin nur mitgefuehrt.
    """
    preference: int = 50
    """Redaktionelle Rangfolge, kleiner = frueher.

    Die Reihenfolge der Kanaele ist eine inhaltliche Aussage ("erste Wahl"
    gegen "Reserve") und darf nicht vom Dateinamen abhaengen.
    """

    def model(self, model_id: str) -> Model | None:
        for model in self.models:
            if model.id == model_id:
                return model
        return None

    @property
    def headers_extra(self) -> dict[str, str]:
        return dict(self.extra_headers)

    @classmethod
    def parse(cls, data: Any, where: str) -> Provider:
        data = _as_mapping(data, where)
        _reject_unknown(
            data,
            {
                "id",
                "name",
                "api_style",
                "base_url",
                "auth",
                "extra_headers",
                "limits_scope",
                "daily_reset_timezone",
                "preference",
                "monthly_budget_usd",
                "onboarding",
                "models",
            },
            where,
        )
        provider_id = _req_str(data, "id", where).strip()
        if not provider_id or not provider_id.replace("_", "").isalnum():
            raise RegistryError(f"{where}.id: nur Kleinbuchstaben, Ziffern und Unterstrich")

        api_style = _req_str(data, "api_style", where)
        if api_style not in API_STYLES:
            raise RegistryError(
                f"{where}.api_style: {api_style!r} unbekannt (erlaubt: {list(API_STYLES)})"
            )

        # Zweite Sicherheitslinie: eine Datendatei darf keinen Endpunkt
        # einfuehren, der nicht in der Python-Allowlist steht.
        base_url = check_url(_req_str(data, "base_url", where), source=where)

        auth = _as_mapping(data.get("auth"), f"{where}.auth")
        _reject_unknown(auth, {"type", "name"}, f"{where}.auth")
        auth_type = _req_str(auth, "type", f"{where}.auth")
        if auth_type not in AUTH_TYPES:
            raise RegistryError(
                f"{where}.auth.type: {auth_type!r} unbekannt (erlaubt: {list(AUTH_TYPES)})"
            )
        auth_name = str(auth.get("name", "")).strip()
        if auth_type in ("header", "query") and not auth_name:
            raise RegistryError(f"{where}.auth.name: bei type={auth_type} erforderlich")

        extra = _as_mapping(data.get("extra_headers", {}), f"{where}.extra_headers")
        for header_name, header_value in extra.items():
            if not isinstance(header_value, str):
                raise RegistryError(f"{where}.extra_headers.{header_name}: Text erwartet")

        limits_scope = str(data.get("limits_scope", "per_model"))
        if limits_scope not in LIMIT_SCOPES:
            raise RegistryError(
                f"{where}.limits_scope: {limits_scope!r} unbekannt (erlaubt: {list(LIMIT_SCOPES)})"
            )

        models_raw = _req_list(data, "models", where)
        if not models_raw:
            raise RegistryError(f"{where}.models: mindestens ein Modell")
        models = tuple(
            Model.parse(item, provider_id, f"{where}.models[{index}]")
            for index, item in enumerate(models_raw)
        )
        seen: set[str] = set()
        for model in models:
            if model.id in seen:
                raise RegistryError(f"{where}.models: Modell {model.id!r} doppelt")
            seen.add(model.id)

        if "onboarding" not in data:
            raise RegistryError(f"{where}: Pflichtfeld 'onboarding' fehlt")

        budget = data.get("monthly_budget_usd")
        if budget is not None and (
            not isinstance(budget, (int, float)) or isinstance(budget, bool) or budget < 0
        ):
            raise RegistryError(f"{where}.monthly_budget_usd: Zahl >= 0 oder leer erwartet")

        preference = data.get("preference", 50)
        if not isinstance(preference, int) or isinstance(preference, bool):
            raise RegistryError(f"{where}.preference: ganze Zahl von 0 bis 100 erwartet")
        if not 0 <= preference <= 100:
            raise RegistryError(f"{where}.preference: muss zwischen 0 und 100 liegen")

        return cls(
            id=provider_id,
            name=_req_str(data, "name", where).strip(),
            api_style=api_style,
            base_url=base_url,
            auth_type=auth_type,
            auth_name=auth_name,
            onboarding=Onboarding.parse(data["onboarding"], f"{where}.onboarding"),
            models=models,
            extra_headers=tuple(sorted(extra.items())),
            limits_scope=limits_scope,
            daily_reset_timezone=str(data.get("daily_reset_timezone", "UTC")),
            preference=preference,
            monthly_budget_usd=float(budget) if budget is not None else None,
        )


@dataclass(frozen=True, slots=True)
class Registry:
    providers: tuple[Provider, ...]

    def __iter__(self) -> Iterator[Provider]:
        return iter(self.providers)

    def __len__(self) -> int:
        return len(self.providers)

    def get(self, provider_id: str) -> Provider | None:
        for provider in self.providers:
            if provider.id == provider_id:
                return provider
        return None

    def require(self, provider_id: str) -> Provider:
        provider = self.get(provider_id)
        if provider is None:
            raise RegistryError(f"Anbieter {provider_id!r} nicht in der Registry")
        return provider

    def models(self) -> Iterator[Model]:
        for provider in self.providers:
            yield from provider.models

    def model_by_key(self, key: str) -> Model | None:
        for model in self.models():
            if model.key == key:
                return model
        return None


DEFAULT_PROVIDER_DIR = Path(__file__).parent / "providers"


def load_provider_file(path: os.PathLike[str] | str) -> Provider:
    """Lies genau eine Anbieterdatei."""
    file_path = Path(path)
    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise RegistryError(f"{file_path.name}: kein gueltiges YAML — {err}") from err

    provider = Provider.parse(raw, file_path.name)
    if provider.id != file_path.stem:
        raise RegistryError(
            f"{file_path.name}: id {provider.id!r} passt nicht zum Dateinamen {file_path.stem!r}"
        )
    return provider


def load_registry(directory: os.PathLike[str] | str | None = None) -> Registry:
    """Lies alle Anbieterdateien eines Verzeichnisses.

    Eine defekte Datei laesst den gesamten Ladevorgang scheitern. Halbe
    Registries sind schlimmer als gar keine: der Router wuerde stillschweigend
    an einem Anbieter vorbeirouten, den der Nutzer eingerichtet glaubt.
    """
    provider_dir = Path(directory) if directory is not None else DEFAULT_PROVIDER_DIR
    if not provider_dir.is_dir():
        raise RegistryError(f"Registry-Verzeichnis fehlt: {provider_dir}")

    providers = [load_provider_file(path) for path in sorted(provider_dir.glob("*.yaml"))]
    if not providers:
        raise RegistryError(f"Keine Anbieterdateien in {provider_dir}")

    ids = [provider.id for provider in providers]
    duplicates = {pid for pid in ids if ids.count(pid) > 1}
    if duplicates:
        raise RegistryError(f"Doppelte Anbieter-IDs: {sorted(duplicates)}")

    return Registry(providers=tuple(providers))


def apply_measured_capabilities(model: Model, measured: dict[str, Any] | None) -> Model:
    """Gemessene Faehigkeiten schlagen die Startwerte aus der Datei."""
    if not measured:
        return model
    caps = model.capabilities.as_dict()
    for key in ("vision", "tools", "structured_output"):
        if isinstance(measured.get(key), bool):
            caps[key] = measured[key]
    tokens = measured.get("context_tokens")
    if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens > 0:
        caps["context_tokens"] = tokens
    return replace(model, capabilities=Capabilities(**caps))


def apply_measured_limits(model: Model, measured: dict[str, Any] | None) -> Model:
    """Gemessene Limit-Header schlagen die Startwerte aus der Datei."""
    if not measured:
        return model
    limits = model.limits.as_dict()
    for key in ("rpm", "rpd", "tpm", "tpd"):
        value = measured.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            limits[key] = value
    return replace(model, limits=Limits(**limits))
