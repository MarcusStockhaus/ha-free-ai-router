"""Uebersetzung zwischen HA-Konzepten und dem normalisierten Anbieterformat.

Zwei Richtungen:

* hinein — HA-Selector-Schema wird JSON Schema, ``ai_task``-Attachments werden
  versandfertige Bildanhaenge,
* hinaus — die Anbieterantwort wird das, was ``ai_task.generate_data``
  zurueckgeben soll: strukturierte Daten, wenn ein Schema verlangt war, sonst
  Text.

Das ist das einzige Modul, das gleichzeitig HA-Typen und Adaptertypen kennt.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from voluptuous_openapi import convert

from .adapters import ChatResponse, ImageAttachment
from .imaging import PreparedImage, estimate_text_tokens, prepare

_LOGGER = logging.getLogger(__name__)

_IMAGE_MIME_PREFIXES = ("image/",)


def structure_to_json_schema(
    structure: vol.Schema | None,
    *,
    custom_serializer: Any = None,
) -> dict[str, Any] | None:
    """HA-Selector-Schema -> JSON Schema.

    ``voluptuous_openapi`` ist derselbe Weg, den HA-eigene KI-Integrationen
    gehen; damit verhaelt sich das Schema hier wie ueberall sonst in HA.

    Der ``custom_serializer`` ist nicht optional in der Sache: ohne ihn stolpert
    ``convert`` ueber die Selector-Objekte in ``structure`` und wirft
    "cannot use 'BooleanSelector' as a dict key". HA bringt mit
    ``llm.selector_serializer`` genau dafuer einen mit; liegt eine LLM-API vor,
    hat deren eigener Serializer Vorrang.
    """
    if structure is None:
        return None
    try:
        schema = convert(
            structure, custom_serializer=custom_serializer or llm.selector_serializer
        )
    except Exception as err:  # pragma: no cover - defensiv, Schema kommt von HA
        raise HomeAssistantError(f"Antwortschema nicht übersetzbar: {err}") from err
    if not isinstance(schema, dict):
        raise HomeAssistantError("Antwortschema ergab kein Objekt")
    schema.setdefault("type", "object")
    return schema


async def attachments_to_images(
    hass: HomeAssistant, attachments: list[Any] | None
) -> tuple[PreparedImage, ...]:
    """``ai_task``-Anhaenge einlesen und auf Versandgroesse bringen.

    HA legt den Anhang als Datei ab und reicht Pfad und MIME-Typ herein. Nur
    Bilder werden weitergegeben — fuer alles andere gibt es in Phase 1 keinen
    Weg zum Anbieter, und stillschweigend wegzulassen waere schlimmer als eine
    klare Fehlermeldung.

    Lesen und Skalieren laufen zusammen im Executor: beides ist blockierend,
    und ein 1280x720-Bild zu verkleinern dauert laenger als es zu lesen.
    """
    if not attachments:
        return ()

    bilder: list[PreparedImage] = []
    for attachment in attachments:
        mime_type = str(getattr(attachment, "mime_type", "") or "")
        path = getattr(attachment, "path", None)
        if not mime_type.startswith(_IMAGE_MIME_PREFIXES):
            raise HomeAssistantError(
                f"Anhang vom Typ {mime_type or 'unbekannt'} wird nicht unterstützt — "
                "in Phase 1 nur Bilder."
            )
        if path is None:
            raise HomeAssistantError("Anhang ohne Datei erhalten")

        bild = await hass.async_add_executor_job(
            _lesen_und_vorbereiten, Path(path), mime_type
        )
        _LOGGER.debug("Anhang %s: %s", path, bild.describe())
        bilder.append(bild)

    return tuple(bilder)


def _lesen_und_vorbereiten(path: Path, mime_type: str) -> PreparedImage:
    try:
        data = path.read_bytes()
    except OSError as err:
        raise HomeAssistantError(f"Anhang nicht lesbar: {err}") from err
    return prepare(data, mime_type)


def to_attachments(bilder: tuple[PreparedImage, ...]) -> tuple[ImageAttachment, ...]:
    """Versandfertige Bilder ins Adapterformat."""
    return tuple(
        ImageAttachment(mime_type=bild.mime_type, data=bild.data) for bild in bilder
    )


def normalize_result(response: ChatResponse, *, has_structure: bool) -> Any:
    """Anbieterantwort -> Rueckgabewert von ``ai_task.generate_data``."""
    if not has_structure:
        return response.text

    if isinstance(response.parsed, (dict, list)):
        return response.parsed

    raise HomeAssistantError(
        "Der Anbieter hat kein gültiges JSON zum verlangten Schema geliefert: "
        f"{response.text.strip()[:200]!r}"
    )


def estimate_input_tokens(instructions: str, bilder: tuple[PreparedImage, ...]) -> int:
    """Schaetzung fuer Kontextfilter und Token-Vorbuchung des Ledgers.

    Die Bildkosten kommen jetzt aus den tatsaechlichen Abmessungen statt aus
    der Dateigroesse — nach dem Skalieren ist das eine Kachel je Bild, und der
    Ledger bremst damit an der richtigen Stelle.
    """
    return estimate_text_tokens(instructions) + sum(
        bild.estimated_tokens for bild in bilder
    )
