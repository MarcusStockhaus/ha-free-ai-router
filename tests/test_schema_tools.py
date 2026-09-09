"""Schema-Umformungen.

Googles ``responseSchema`` nimmt nur eine OpenAPI-Teilmenge an und quittiert
alles andere mit einem 400 — deshalb hier scharf geprueft.
"""

from __future__ import annotations

from custom_components.free_ai_router.schema_tools import (
    relax_schema,
    to_google_schema,
    to_strict_openai_schema,
)

BEISPIEL = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "person_erkannt": {"type": "boolean", "description": "Ist jemand zu sehen?"},
        "anzahl": {"type": "integer"},
        "beschreibung": {"type": "string", "default": "unbekannt"},
        "objekte": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["person_erkannt"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# Google
# --------------------------------------------------------------------------


def test_google_typen_werden_gross_geschrieben() -> None:
    ergebnis = to_google_schema(BEISPIEL)
    assert ergebnis["type"] == "OBJECT"
    assert ergebnis["properties"]["person_erkannt"]["type"] == "BOOLEAN"
    assert ergebnis["properties"]["anzahl"]["type"] == "INTEGER"
    assert ergebnis["properties"]["objekte"]["type"] == "ARRAY"
    assert ergebnis["properties"]["objekte"]["items"]["type"] == "STRING"


def test_google_verliert_unbekannte_felder() -> None:
    """``additionalProperties`` und ``$schema`` fuehren bei Google zu 400."""
    ergebnis = to_google_schema(BEISPIEL)
    assert "additionalProperties" not in ergebnis
    assert "$schema" not in ergebnis
    assert "default" not in ergebnis["properties"]["beschreibung"]


def test_google_bekommt_eine_feste_reihenfolge() -> None:
    """Ohne propertyOrdering variiert Googles Ausgabe zwischen Aufrufen."""
    ergebnis = to_google_schema(BEISPIEL)
    assert ergebnis["propertyOrdering"] == [
        "person_erkannt",
        "anzahl",
        "beschreibung",
        "objekte",
    ]


def test_google_behaelt_beschreibungen_und_pflichtfelder() -> None:
    ergebnis = to_google_schema(BEISPIEL)
    assert ergebnis["required"] == ["person_erkannt"]
    assert ergebnis["properties"]["person_erkannt"]["description"]


def test_google_nullable_aus_typliste() -> None:
    ergebnis = to_google_schema({"type": ["string", "null"]})
    assert ergebnis["type"] == "STRING"
    assert ergebnis["nullable"] is True


def test_google_enum_wird_zu_text() -> None:
    ergebnis = to_google_schema({"type": "integer", "enum": [1, 2]})
    assert ergebnis["type"] == "STRING"
    assert ergebnis["enum"] == ["1", "2"]


def test_google_haelt_muell_aus() -> None:
    assert to_google_schema({}) == {}
    assert to_google_schema({"type": "irgendwas"})["type"] == "STRING"


# --------------------------------------------------------------------------
# OpenAI strict
# --------------------------------------------------------------------------


def test_strict_verlangt_alle_felder_und_verbietet_zusaetze() -> None:
    ergebnis = to_strict_openai_schema(BEISPIEL)
    assert ergebnis["additionalProperties"] is False
    assert set(ergebnis["required"]) == set(ergebnis["properties"])


def test_strict_macht_optionale_felder_nullable_statt_sie_zu_streichen() -> None:
    """Sonst lehnt der Endpunkt das Schema ab, weil ein Feld weder required
    noch erlaubt waere."""
    ergebnis = to_strict_openai_schema(BEISPIEL)
    assert ergebnis["properties"]["anzahl"]["type"] == ["integer", "null"]
    assert ergebnis["properties"]["person_erkannt"]["type"] == "boolean"


def test_strict_entfernt_schema_und_default() -> None:
    ergebnis = to_strict_openai_schema(BEISPIEL)
    assert "$schema" not in ergebnis
    assert "default" not in ergebnis["properties"]["beschreibung"]


def test_strict_geht_in_die_tiefe() -> None:
    verschachtelt = {
        "type": "object",
        "properties": {"innen": {"type": "object", "properties": {"a": {"type": "string"}}}},
        "required": ["innen"],
    }
    ergebnis = to_strict_openai_schema(verschachtelt)
    assert ergebnis["properties"]["innen"]["additionalProperties"] is False


def test_relax_entfernt_die_strict_zusaetze_wieder() -> None:
    entspannt = relax_schema(to_strict_openai_schema(BEISPIEL))
    assert "additionalProperties" not in entspannt
    assert "additionalProperties" not in entspannt["properties"]["objekte"]
