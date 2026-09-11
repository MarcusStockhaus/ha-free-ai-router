"""Registry- und Allowlist-Tests.

Die Allowlist ist die zweite Sicherheitslinie hinter der Feed-Signatur
(Phase 3). Sie muss auch dann halten, wenn eine Registry-Datei manipuliert
ist — deshalb steht sie im Python-Code und wird hier scharf geprueft.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from custom_components.free_ai_router.allowlist import (
    ALLOWED_HOSTS,
    HostNotAllowedError,
    check_url,
    is_allowed,
)
from custom_components.free_ai_router.registry import (
    DEFAULT_PROVIDER_DIR,
    Provider,
    RegistryError,
    apply_measured_capabilities,
    apply_measured_limits,
    load_provider_file,
    load_registry,
)

MINIMAL = {
    "id": "groq",
    "name": "Groq",
    "api_style": "openai_compatible",
    "base_url": "https://api.groq.com/openai/v1",
    "auth": {"type": "bearer"},
    "onboarding": {
        "signup_url": "https://console.groq.com/keys",
        "steps_de": ["anmelden"],
        "data_note_de": "kein Training",
        "credit_card_required": False,
    },
    "models": [
        {
            "id": "llama",
            "profiles": ["schnell"],
            "capabilities": {
                "vision": False,
                "tools": True,
                "structured_output": True,
                "context_tokens": 1000,
            },
        }
    ],
}


def variant(**changes):
    data = json.loads(json.dumps(MINIMAL))
    data.update(changes)
    return data


# --------------------------------------------------------------------------
# Allowlist
# --------------------------------------------------------------------------


def test_allowlist_laesst_bekannte_hosts_durch() -> None:
    assert check_url("https://api.groq.com/openai/v1") == "https://api.groq.com/openai/v1"
    assert is_allowed("https://generativelanguage.googleapis.com/v1beta")


@pytest.mark.parametrize(
    "url",
    [
        "https://boese.example/v1",
        "http://api.groq.com/openai/v1",
        "https://api.groq.com.boese.example/v1",
        "https://api.groq.com:8443/v1",
        "https://nutzer:geheim@api.groq.com/v1",
    ],
)
def test_allowlist_weist_alles_andere_ab(url: str) -> None:
    assert not is_allowed(url)
    with pytest.raises(HostNotAllowedError):
        check_url(url)


def test_registry_datei_kann_keinen_fremden_endpunkt_einfuehren() -> None:
    """Der Kern der Sache: ein kompromittierter Feed darf Modelle und Texte
    aendern, aber nicht, wohin die Kamerabilder fliessen."""
    with pytest.raises(HostNotAllowedError):
        Provider.parse(variant(base_url="https://angreifer.example/v1"), "test")


def test_alle_mitgelieferten_dateien_stehen_in_der_allowlist() -> None:
    for path in DEFAULT_PROVIDER_DIR.glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert is_allowed(data["base_url"]), path.name


def test_allowlist_ist_nicht_leer() -> None:
    assert len(ALLOWED_HOSTS) >= 4


# --------------------------------------------------------------------------
# Validierung
# --------------------------------------------------------------------------


def test_minimale_datei_laedt() -> None:
    provider = Provider.parse(MINIMAL, "test")
    assert provider.id == "groq"
    assert provider.limits_scope == "per_model"
    assert provider.preference == 50
    assert provider.models[0].limits.rpm is None


@pytest.mark.parametrize(
    ("changes", "hinweis"),
    [
        ({"api_style": "telepathie"}, "api_style"),
        ({"auth": {"type": "header"}}, "auth.name"),
        ({"limits_scope": "irgendwie"}, "limits_scope"),
        ({"preference": 200}, "preference"),
        ({"nichtvorgesehen": 1}, "unbekannte Felder"),
    ],
)
def test_kaputte_dateien_werden_mit_hinweis_abgelehnt(changes, hinweis) -> None:
    with pytest.raises(RegistryError, match=hinweis):
        Provider.parse(variant(**changes), "test")


def test_unbekanntes_profil_wird_abgelehnt() -> None:
    data = variant()
    data["models"][0]["profiles"] = ["turbo"]
    with pytest.raises(RegistryError, match="profiles"):
        Provider.parse(data, "test")


def test_doppelte_modelle_werden_abgelehnt() -> None:
    data = variant()
    data["models"].append(json.loads(json.dumps(data["models"][0])))
    with pytest.raises(RegistryError, match="doppelt"):
        Provider.parse(data, "test")


def test_fehlende_faehigkeiten_werden_abgelehnt() -> None:
    data = variant()
    del data["models"][0]["capabilities"]["vision"]
    with pytest.raises(RegistryError, match="vision"):
        Provider.parse(data, "test")


def test_dateiname_muss_zur_id_passen(tmp_path: Path) -> None:
    datei = tmp_path / "falscher_name.yaml"
    datei.write_text(yaml.safe_dump(MINIMAL), encoding="utf-8")
    with pytest.raises(RegistryError, match="Dateinamen"):
        load_provider_file(datei)


def test_kaputtes_yaml_nennt_die_datei(tmp_path: Path) -> None:
    datei = tmp_path / "groq.yaml"
    datei.write_text("id: [unbalanciert\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="groq.yaml"):
        load_provider_file(datei)


def test_leeres_verzeichnis_ist_ein_fehler(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="Keine Anbieterdateien"):
        load_registry(tmp_path)


# --------------------------------------------------------------------------
# Mitgelieferte Registry
# --------------------------------------------------------------------------


def test_mitgelieferte_registry_laedt() -> None:
    registry = load_registry()
    # Drei Anbieter: OpenCode Zen ist am 09.09.2026 rausgeflogen, weil die
    # kostenlose Stufe laut Anbieter "only be used in OpenCode" kann und ueber
    # die API mit MissingSessionID abweist.
    assert len(registry) >= 3
    assert registry.get("google_ai_studio") is not None
    assert registry.get("gibtsnicht") is None


def test_jedes_profil_hat_mindestens_einen_kanal_in_der_registry() -> None:
    """Sonst waere die Integration schon vor der Einrichtung unvollstaendig."""
    registry = load_registry()
    belegt = {profile for model in registry.models() for profile in model.profiles}
    assert belegt == {"schnell", "vision", "reasoning"}


def test_vision_haengt_erkennbar_an_wenigen_anbietern() -> None:
    """Das groesste Risiko des Entwurfs — hier festgehalten, damit ein
    Registry-Update, das die letzte Vision-Reserve entfernt, auffaellt."""
    registry = load_registry()
    anbieter = {
        model.provider_id
        for model in registry.models()
        if "vision" in model.profiles and model.capabilities.vision
    }
    assert len(anbieter) >= 2, f"Vision haengt nur noch an {anbieter}"


def test_schema_datei_passt_zu_den_dateien() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (DEFAULT_PROVIDER_DIR.parent / "registry_schema.json").read_text(encoding="utf-8")
    )
    for path in DEFAULT_PROVIDER_DIR.glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        jsonschema.validate(data, schema)


# --------------------------------------------------------------------------
# Messwerte schlagen Startwerte
# --------------------------------------------------------------------------


def test_gemessene_faehigkeiten_gewinnen() -> None:
    provider = Provider.parse(MINIMAL, "test")
    model = provider.models[0]
    assert not model.capabilities.vision

    gemessen = apply_measured_capabilities(model, {"vision": True, "tools": False})
    assert gemessen.capabilities.vision
    assert not gemessen.capabilities.tools
    assert gemessen.capabilities.structured_output


def test_leere_messung_aendert_nichts() -> None:
    provider = Provider.parse(MINIMAL, "test")
    model = provider.models[0]
    assert apply_measured_capabilities(model, {}) is model
    assert apply_measured_limits(model, None) is model


def test_gemessene_limits_gewinnen() -> None:
    provider = Provider.parse(MINIMAL, "test")
    gemessen = apply_measured_limits(provider.models[0], {"rpm": 7})
    assert gemessen.limits.rpm == 7


def test_kameraanalyse_mit_schema_hat_mehr_als_einen_kanal() -> None:
    """Der eigentliche Anwendungsfall braucht Vision *und* Structured Output.

    Googles Gemma kann Bilder, aber kein responseSchema — gemessen am
    09. und 10.09.2026. Ein Registry-Update, das die uebrigen Kanaele
    wegnimmt, faellt hier auf, statt erst beim ersten Kamerabild.
    """
    registry = load_registry()
    kanaele = [
        model.key
        for model in registry.models()
        if model.capabilities.vision and model.capabilities.structured_output
    ]
    assert len(kanaele) >= 2, f"Kameraanalyse mit Schema haengt nur noch an {kanaele}"
    assert len({key.split("/")[0] for key in kanaele}) >= 2, (
        f"alle Schema-faehigen Vision-Kanaele beim selben Anbieter: {kanaele}"
    )


def mit_preisen(**changes):
    """MINIMAL, aber mit vollstaendigen Preisangaben am Modell."""
    data = variant(**changes)
    data["models"][0]["pricing"] = {"input_per_mtok": 0.1, "output_per_mtok": 0.2}
    return data


def test_monatsdeckel_wird_gelesen() -> None:
    provider = Provider.parse(mit_preisen(monthly_budget_usd=10), "test")
    assert provider.monthly_budget_usd == 10.0
    assert Provider.parse(MINIMAL, "test").monthly_budget_usd is None


def test_deckel_ohne_preise_wird_abgelehnt() -> None:
    """Ein Deckel ohne Preise ist kein Deckel.

    Der Ledger koennte dann nur Anfragen und Token zaehlen und wuerde den
    Betrag nie erreichen — der Anbieter liefe unbegrenzt weiter, waehrend die
    Datei behauptet, er sei begrenzt. Lieber die Datei ablehnen.
    """
    with pytest.raises(RegistryError, match="pricing"):
        Provider.parse(variant(monthly_budget_usd=10), "test")


def test_halbe_preisangabe_reicht_nicht() -> None:
    """Nur der Eingabepreis: die Ausgabe bliebe unbeziffert und damit gratis."""
    data = variant(monthly_budget_usd=10)
    data["models"][0]["pricing"] = {"input_per_mtok": 0.1}
    with pytest.raises(RegistryError, match="pricing"):
        Provider.parse(data, "test")


def test_negativer_monatsdeckel_wird_abgelehnt() -> None:
    with pytest.raises(RegistryError, match="monthly_budget_usd"):
        Provider.parse(variant(monthly_budget_usd=-1), "test")


def test_gedeckelte_anbieter_stehen_hinten() -> None:
    """Ein Anbieter mit Ausgabendeckel darf nicht vor den ungedeckelten
    stehen — sonst wird Geld verbraucht, solange freies Kontingent da ist."""
    registry = load_registry()
    gedeckelt = [p for p in registry if p.monthly_budget_usd is not None]
    frei = [p for p in registry if p.monthly_budget_usd is None]
    assert gedeckelt, "Testfall haette keine Aussagekraft"
    assert min(p.preference for p in gedeckelt) > min(p.preference for p in frei)


def test_gedeckelte_anbieter_fuehren_preise() -> None:
    """Ohne Preise laesst sich ein Ausgabendeckel nicht ausrechnen."""
    registry = load_registry()
    for provider in registry:
        if provider.monthly_budget_usd is None:
            continue
        for model in provider.models:
            assert model.pricing.input_per_mtok is not None, model.key
            assert model.pricing.output_per_mtok is not None, model.key
