"""Schema-Umformungen zwischen JSON Schema und den Anbieterdialekten.

HA liefert Structured Output als Selector-Schema, das ueber
``voluptuous_openapi`` zu JSON Schema wird (siehe ``task_adapter.py``).
Von dort aus muss es je Dialekt noch einmal zurechtgebogen werden — Google
akzeptiert nur eine OpenAPI-Teilmenge, OpenAI-kompatible Endpunkte verlangen
im strict-Modus ``additionalProperties: false``.

Kein ``homeassistant``-Import.
"""

from __future__ import annotations

from typing import Any

# Felder, die Googles ``responseSchema`` kennt.
_GOOGLE_KEYS = {
    "type",
    "format",
    "description",
    "nullable",
    "enum",
    "items",
    "properties",
    "required",
    "propertyOrdering",
    "anyOf",
    "minItems",
    "maxItems",
}

_GOOGLE_TYPES = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}

# Formate, die Google versteht. Alles andere fliegt raus, sonst 400.
_GOOGLE_FORMATS = {
    "STRING": {"enum", "date-time"},
    "NUMBER": {"float", "double"},
    "INTEGER": {"int32", "int64"},
}


def to_google_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Uebersetze JSON Schema in Googles OpenAPI-Teilmenge."""
    if not isinstance(schema, dict):
        return {"type": "STRING"}

    out: dict[str, Any] = {}

    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        # ["string", "null"] -> STRING + nullable
        non_null = [item for item in raw_type if item != "null"]
        raw_type = non_null[0] if non_null else "string"
        out["nullable"] = True
    if isinstance(raw_type, str):
        out["type"] = _GOOGLE_TYPES.get(raw_type.lower(), "STRING")

    for key in ("description", "nullable", "enum", "minItems", "maxItems"):
        if key in schema:
            out[key] = schema[key]

    fmt = schema.get("format")
    if isinstance(fmt, str) and fmt in _GOOGLE_FORMATS.get(out.get("type", ""), set()):
        out["format"] = fmt

    if "enum" in out:
        # Google verlangt bei enum den Typ STRING und Zeichenketten.
        out["type"] = "STRING"
        out["enum"] = [str(value) for value in out["enum"]]

    if "anyOf" in schema and isinstance(schema["anyOf"], list):
        out["anyOf"] = [to_google_schema(item) for item in schema["anyOf"]]
        out.pop("type", None)
        return out

    if out.get("type") == "OBJECT" or "properties" in schema:
        properties = schema.get("properties") or {}
        out["type"] = "OBJECT"
        out["properties"] = {
            name: to_google_schema(sub) for name, sub in properties.items()
        }
        required = [name for name in schema.get("required", []) if name in properties]
        if required:
            out["required"] = required
        if properties:
            # Feste Reihenfolge — sonst variiert Googles Ausgabe zwischen Aufrufen.
            out["propertyOrdering"] = list(properties)

    if out.get("type") == "ARRAY" or "items" in schema:
        out["type"] = "ARRAY"
        out["items"] = to_google_schema(schema.get("items") or {"type": "string"})

    return {key: value for key, value in out.items() if key in _GOOGLE_KEYS}


def to_strict_openai_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Ergaenze, was der strict-Modus OpenAI-kompatibler Endpunkte verlangt.

    Im strict-Modus muessen Objekte ``additionalProperties: false`` setzen und
    alle Eigenschaften in ``required`` auffuehren. Optionale Felder werden
    deshalb nullable gemacht statt weggelassen — sonst lehnt der Endpunkt das
    Schema ab.
    """
    if not isinstance(schema, dict):
        return schema

    out = dict(schema)
    out.pop("$schema", None)
    out.pop("default", None)

    if out.get("type") == "object" or "properties" in out:
        properties = {
            name: to_strict_openai_schema(sub)
            for name, sub in (out.get("properties") or {}).items()
        }
        out["type"] = "object"
        out["properties"] = properties
        out["additionalProperties"] = False
        optional = [name for name in properties if name not in set(out.get("required", []))]
        for name in optional:
            sub = properties[name]
            sub_type = sub.get("type")
            if isinstance(sub_type, str):
                sub["type"] = [sub_type, "null"]
        out["required"] = list(properties)

    if "items" in out:
        out["items"] = to_strict_openai_schema(out["items"])

    return out


def relax_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Entferne strict-Zusaetze — fuer Endpunkte, die daran scheitern."""
    if not isinstance(schema, dict):
        return schema
    out = {key: value for key, value in schema.items() if key != "additionalProperties"}
    if "properties" in out:
        out["properties"] = {
            name: relax_schema(sub) for name, sub in out["properties"].items()
        }
    if "items" in out:
        out["items"] = relax_schema(out["items"])
    return out


def describe_schema(schema: dict[str, Any]) -> str:
    """Kurzbeschreibung fuers Prompt-Fallback, wenn ein Modell kein Schema kann."""
    import json

    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
