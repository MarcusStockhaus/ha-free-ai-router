"""Erzeugt das Testbild fuer die Vision-Pruefung — ohne Pillow.

Ein 1x1-Pixel wird von mehreren Anbietern als zu klein abgelehnt, was einen
Anbieter faelschlich als "kann kein Vision" erscheinen laesst. Deshalb eine
kleine Vollflaeche in einer eindeutigen Farbe: dann laesst sich zusaetzlich
pruefen, ob das Modell das Bild wirklich angesehen hat oder nur genickt hat.
"""

from __future__ import annotations

import struct
import zlib

# Farbe, Name und die Woerter, die als richtige Antwort gelten.
TEST_COLOR_RGB = (208, 32, 32)
TEST_COLOR_NAME_DE = "rot"
TEST_COLOR_WORDS = ("rot", "red", "rouge", "rojo", "crimson", "scarlet")

TEST_IMAGE_SIZE = 64
TEST_IMAGE_MIME = "image/png"


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def solid_png(size: int = TEST_IMAGE_SIZE, rgb: tuple[int, int, int] = TEST_COLOR_RGB) -> bytes:
    """Ein quadratisches PNG in einer Volltonfarbe."""
    row = bytes((0,)) + bytes(rgb) * size  # Filter-Byte 0 je Zeile
    raw = row * size
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8 bit, Truecolor
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def looks_like_test_color(text: str) -> bool:
    """Hat das Modell die Farbe des Testbildes tatsaechlich genannt?"""
    lowered = (text or "").lower()
    return any(word in lowered for word in TEST_COLOR_WORDS)
