"""Uebersetzung zwischen HA-Konzepten und dem normalisierten Anbieterformat.

Zwei Richtungen:

* hinein — HA-Selector-Schema wird JSON Schema, ``ai_task``-Attachments werden
  Bildanhaenge,
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

_LOGGER = logging.getLogger(__name__)

#: Groesse, ab der ein Anhang verdaechtig ist. Ein skalierter Kamerasnapshot
#: liegt bei 50-300 KB; alles daruber deutet auf ein unskaliertes Vollbild und
#: kostet unnoetig Kontingent.
LARGE_ATTACHMENT_BYTES = 4 * 1024 * 1024

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
        raise HomeAssistantError(f"Antwortschema nicht uebersetzbar: {err}") from err
    if not isinstance(schema, dict):
        raise HomeAssistantError("Antwortschema ergab kein Objekt")
    schema.setdefault("type", "object")
    return schema


async def attachments_to_images(
    hass: HomeAssistant, attachments: list[Any] | None
) -> tuple[ImageAttachment, ...]:
    """``ai_task``-Anhaenge einlesen.

    HA legt den Anhang als Datei ab und reicht Pfad und MIME-Typ herein. Nur
    Bilder werden weitergegeben — fuer alles andere gibt es in Phase 1 keinen
    Weg zum Anbieter, und stillschweigend wegzulassen waere schlimmer als eine
    klare Fehlermeldung.
    """
    if not attachments:
        return ()

    images: list[ImageAttachment] = []
    for attachment in attachments:
        mime_type = str(getattr(attachment, "mime_type", "") or "")
        path = getattr(attachment, "path", None)
        if not mime_type.startswith(_IMAGE_MIME_PREFIXES):
            raise HomeAssistantError(
                f"Anhang vom Typ {mime_type or 'unbekannt'} wird nicht unterstuetzt — "
                "in Phase 1 nur Bilder."
            )
        if path is None:
            raise HomeAssistantError("Anhang ohne Datei erhalten")

        data = await hass.async_add_executor_job(_read_file, Path(path))
        if len(data) > LARGE_ATTACHMENT_BYTES:
            _LOGGER.warning(
                "Anhang ist %.1f MB gross. Kamerabilder vor dem Versand skalieren "
                "spart Kontingent und Zeit.",
                len(data) / 1024 / 1024,
            )
        images.append(ImageAttachment(mime_type=mime_type, data=data))

    return tuple(images)


def _read_file(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as err:
        raise HomeAssistantError(f"Anhang nicht lesbar: {err}") from err


def normalize_result(response: ChatResponse, *, has_structure: bool) -> Any:
    """Anbieterantwort -> Rueckgabewert von ``ai_task.generate_data``."""
    if not has_structure:
        return response.text

    if isinstance(response.parsed, (dict, list)):
        return response.parsed

    raise HomeAssistantError(
        "Der Anbieter hat kein gueltiges JSON zum verlangten Schema geliefert: "
        f"{response.text.strip()[:200]!r}"
    )


def estimate_input_tokens(instructions: str, images: tuple[ImageAttachment, ...]) -> int:
    """Grobe Schaetzung fuer den Kontextfilter des Routers.

    Vier Zeichen je Token ist die uebliche Faustregel; ein Bild schlaegt mit
    258 Token je 768x768-Kachel zu Buche. Die Schaetzung muss nur gut genug
    sein, um ein zu kleines Kontextfenster zu erkennen.
    """
    text_tokens = max(1, len(instructions) // 4)
    image_tokens = 0
    for image in images:
        tiles = max(1, len(image.data) // (200 * 1024))
        image_tokens += 258 * min(tiles, 16)
    return text_tokens + image_tokens
