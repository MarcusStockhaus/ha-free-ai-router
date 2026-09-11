"""Bilder fuer den Versand vorbereiten — und ehrlich schaetzen, was sie kosten.

**Gemessen am 11.09.2026, gegen die Annahme des Konzepts.** Dort stand, Bilder
vor dem Versand auf 768 Pixel zu skalieren spare Kontingent. Das stimmt nicht:

    Kante     Groesse       KB   Prompt-Token (gemini-3.5-flash-lite)
      256   256x144          9   1110
      768   768x432         67   1110
     1280   1280x720       116   1110
     2048   2048x1152      290   1110
     4096   4096x2304      749   1110

Ueber 30-fache Pixelzahl und 80-fache Dateigroesse: derselbe Preis. Google
normalisiert das Bild vor der Abrechnung. Groqs qwen3.8-27b verhaelt sich
genauso (792 Token, unabhaengig von der Groesse).

Zwei Folgerungen, beide unangenehm:

1. **Skalieren spart keine Token.** Es spart nur Bytes auf der Leitung. Deshalb
   greift es hier erst ab :data:`MAX_UPLOAD_BYTES` — ein Kamerabild mit 80 KB
   bleibt unangetastet, ein 12-Megapixel-Foto mit 4 MB nicht.
2. **Ein Bild kostet rund 1.100 Token, nicht 258.** Die Kachelrechnung des
   Konzepts (258 je 768er-Kachel) unterschaetzt um das Vierfache. Bei Groqs
   8.000 Token je Minute ist das der Unterschied zwischen gedachten 30 und
   tatsaechlichen 10 Analysen pro Minute — und genau diese Zahl bucht der
   Ledger vor dem Absenden vor.

Kein ``homeassistant``-Import, damit die Rechnung lokal pruefbar bleibt.
Pillow wird erst in der Funktion importiert: es liegt in jeder
HA-Installation, aber das Probe-CLI soll auch ohne laufen.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

#: Ab dieser Dateigroesse wird verkleinert. Darunter lohnt es nicht: Token
#: spart es ohnehin nicht, und jede Neukodierung kostet Bildqualitaet.
MAX_UPLOAD_BYTES = 1_500_000

#: Zielkante beim Verkleinern. Grosszuegig, weil nur die Leitung entlastet
#: werden soll — nicht die Abrechnung.
MAX_EDGE = 2048

#: Was ein Bild tatsaechlich kostet. Gemessen: Google 1.110, Groq 792. Der
#: hoehere Wert ist der richtige: eine zu hohe Vorbuchung bremst hoechstens
#: eine Anfrage zu frueh, eine zu niedrige laeuft in den 429.
TOKENS_PER_IMAGE = 1100

JPEG_QUALITY = 88

#: Formate, die sich unveraendert weiterreichen lassen.
_KEEP_FORMAT = {"image/jpeg", "image/png", "image/webp"}


@dataclass(frozen=True, slots=True)
class PreparedImage:
    """Ein versandfertiges Bild samt der Rechnung dahinter."""

    data: bytes
    mime_type: str
    width: int
    height: int
    original_bytes: int
    scaled: bool

    @property
    def estimated_tokens(self) -> int:
        """Was dieses Bild den Anbieter kostet.

        Bewusst eine Konstante: die Messung zeigt, dass die Abmessungen den
        Preis nicht beeinflussen. Eine Formel ueber Pixel oder Kacheln waere
        praeziser *aussehender* Unsinn.
        """
        return TOKENS_PER_IMAGE

    def describe(self) -> str:
        """Eine Zeile fuers Log."""
        wie = "verkleinert" if self.scaled else "unveraendert"
        return (
            f"{self.width}x{self.height}, {len(self.data) / 1024:.0f} KB "
            f"({wie}, vorher {self.original_bytes / 1024:.0f} KB), "
            f"~{self.estimated_tokens} Token"
        )


def prepare(
    data: bytes,
    mime_type: str,
    *,
    max_edge: int = MAX_EDGE,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> PreparedImage:
    """Bringe ein Bild auf Versandgroesse.

    Verkleinert nur, was ueber ``max_bytes`` liegt. Schlaegt nie fehl: laesst
    sich das Bild nicht oeffnen oder fehlt Pillow, gehen die Originaldaten
    hinaus. Ein Bild nicht zu verkleinern kostet Bandbreite, es gar nicht zu
    schicken kostet die Analyse.
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - in HA immer vorhanden
        _LOGGER.debug("Pillow fehlt, Bild geht unveraendert hinaus")
        return _unveraendert(data, mime_type)

    try:
        with Image.open(io.BytesIO(data)) as bild:
            bild.load()
            breite, hoehe = bild.size

            if len(data) <= max_bytes and max(breite, hoehe) <= max_edge:
                return PreparedImage(
                    data=data,
                    mime_type=mime_type,
                    width=breite,
                    height=hoehe,
                    original_bytes=len(data),
                    scaled=False,
                )

            faktor = min(1.0, max_edge / max(breite, hoehe))
            neu = (max(1, round(breite * faktor)), max(1, round(hoehe * faktor)))
            verkleinert = bild.convert("RGB")
            if neu != (breite, hoehe):
                verkleinert = verkleinert.resize(neu, Image.LANCZOS)

            puffer = io.BytesIO()
            verkleinert.save(puffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return PreparedImage(
                data=puffer.getvalue(),
                mime_type="image/jpeg",
                width=neu[0],
                height=neu[1],
                original_bytes=len(data),
                scaled=True,
            )
    except Exception as err:  # noqa: BLE001 - jedes Bildformat kann eigen sein
        _LOGGER.warning("Bild nicht aufbereitbar (%r), gehe unveraendert hinaus", err)
        return _unveraendert(data, mime_type)


def _unveraendert(data: bytes, mime_type: str) -> PreparedImage:
    """Rueckfall, wenn das Bild nicht lesbar war. Abmessungen unbekannt."""
    return PreparedImage(
        data=data,
        mime_type=mime_type if mime_type in _KEEP_FORMAT else "image/jpeg",
        width=0,
        height=0,
        original_bytes=len(data),
        scaled=False,
    )


def estimate_text_tokens(text: str) -> int:
    """Vier Zeichen je Token, die uebliche Faustregel."""
    return max(1, len(text) // 4)
