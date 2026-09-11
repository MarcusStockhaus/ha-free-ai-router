"""Erzeugt die Vision-Aufgabe fuer die Faehigkeitsmessung — ohne Pillow.

Drei Fallen, die diese Pruefung umgehen muss:

1. **Ein 1x1-Pixel** wird von mehreren Anbietern als zu klein abgelehnt. Ein
   Modell erschiene dann faelschlich als "kann kein Vision".
2. **Angenommen ist nicht angesehen.** Manche OpenAI-kompatiblen Endpunkte
   verwerfen den Bildteil stillschweigend und antworten trotzdem. Deshalb wird
   geprueft, ob die Antwort zum Bild passt, nicht nur ob sie kommt.
3. **Raten.** Bei einer einzigen roten Flaeche und der Frage "welche Farbe?"
   trifft ein blindes Textmodell zu oft zufaellig — Rot ist die haeufigste
   Antwort auf diese Frage. Deshalb zwei Farbflaechen aus einer Palette von
   sieben, zufaellig gewaehlt: beide richtig zu raten liegt unter fuenf
   Prozent, und die Farben wechseln bei jedem Lauf.
"""

from __future__ import annotations

import random
import struct
import zlib
from dataclasses import dataclass

TEST_IMAGE_SIZE = 96
TEST_IMAGE_MIME = "image/png"

#: Farbname -> (RGB, akzeptierte Woerter in der Antwort).
#: Bewusst nur bunte Farben: "schwarz" und "weiss" sind Verlegenheitsantworten
#: und wuerden die Ratequote wieder hochtreiben.
PALETTE: dict[str, tuple[tuple[int, int, int], tuple[str, ...]]] = {
    "rot": ((208, 32, 32), ("rot", "red", "rouge", "rojo", "crimson", "scarlet")),
    "blau": ((32, 64, 208), ("blau", "blue", "bleu", "azul", "navy")),
    "gruen": ((32, 160, 64), ("grün", "gruen", "green", "vert", "verde")),
    "gelb": ((232, 208, 32), ("gelb", "yellow", "jaune", "amarillo")),
    "lila": ((136, 48, 176), ("lila", "violett", "purple", "violet", "morado", "magenta")),
    "orange": ((232, 128, 32), ("orange", "naranja")),
    "tuerkis": ((32, 176, 176), ("türkis", "tuerkis", "turquoise", "cyan", "teal")),
}

PROMPT_DE = (
    "Das Bild besteht aus zwei waagerechten Farbflaechen, oben und unten. "
    "Nenne beide Farben, durch Komma getrennt, je ein einzelnes deutsches Wort. "
    "Keine Erklaerung."
)


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def two_tone_png(
    oben: tuple[int, int, int],
    unten: tuple[int, int, int],
    size: int = TEST_IMAGE_SIZE,
) -> bytes:
    """Ein quadratisches PNG, oben die eine, unten die andere Farbe."""
    zeile_oben = bytes((0,)) + bytes(oben) * size
    zeile_unten = bytes((0,)) + bytes(unten) * size
    haelfte = size // 2
    raw = zeile_oben * haelfte + zeile_unten * (size - haelfte)
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8 bit, Truecolor
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def solid_png(
    rgb: tuple[int, int, int] = PALETTE["rot"][0], size: int = TEST_IMAGE_SIZE
) -> bytes:
    """Eine einfarbige Flaeche. Nur noch fuer Vergleichszwecke."""
    return two_tone_png(rgb, rgb, size)


@dataclass(frozen=True, slots=True)
class VisionChallenge:
    """Ein Testbild samt der Antwort, die dazu passt."""

    image: bytes
    expected: tuple[str, str]
    mime_type: str = TEST_IMAGE_MIME
    prompt: str = PROMPT_DE

    @property
    def expected_text(self) -> str:
        return " und ".join(self.expected)

    def solved(self, text: str) -> bool:
        """Nennt die Antwort beide Farben — und keine dritte?

        Grosszuegig bei Sprache und Formulierung, streng bei der Sache.

        Beide Farben zu verlangen ist die eine Haelfte: eine davon zu treffen
        ist Zufall. Die andere Haelfte ist, keine weitere zuzulassen. Der
        Prompt verlangt ausdruecklich zwei Woerter; wer stattdessen die halbe
        Palette aufzaehlt, deckt den Raum ab, statt hinzusehen — und bestand
        frueher genau damit.
        """
        lowered = (text or "").lower()
        beide = all(
            any(wort in lowered for wort in PALETTE[name][1]) for name in self.expected
        )
        return beide and not self.wrong_colors(text)

    def wrong_colors(self, text: str) -> list[str]:
        """Welche anderen Farben nennt die Antwort? Nur fuer die Fehlermeldung."""
        lowered = (text or "").lower()
        return [
            name
            for name, (_rgb, woerter) in PALETTE.items()
            if name not in self.expected and any(wort in lowered for wort in woerter)
        ]


def make_challenge(rng: random.Random | None = None) -> VisionChallenge:
    """Zwei zufaellige Farben aus der Palette, uebereinander."""
    rng = rng or random.Random()
    oben, unten = rng.sample(sorted(PALETTE), 2)
    return VisionChallenge(
        image=two_tone_png(PALETTE[oben][0], PALETTE[unten][0]),
        expected=(oben, unten),
    )


def looks_like_test_color(text: str, farbe: str = "rot") -> bool:
    """Nennt die Antwort diese eine Farbe? Fuer Einzelfarb-Vergleiche."""
    lowered = (text or "").lower()
    return any(wort in lowered for wort in PALETTE[farbe][1])
