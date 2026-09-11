"""Bildvorbereitung und Tokenschaetzung.

Die Tests halten vor allem eine Messung fest, die einer Annahme des Konzepts
widerspricht: Skalieren spart keine Token. Gemessen am 11.09.2026 kostete
dasselbe Motiv bei gemini-3.5-flash-lite 1110 Prompt-Token — bei 256x144
ebenso wie bei 4096x2304. Wer das nicht weiss, baut eine Verkleinerung ein,
die nur Bildqualitaet kostet.
"""

from __future__ import annotations

import io

import pytest

from custom_components.free_ai_router.imaging import (
    MAX_EDGE,
    MAX_UPLOAD_BYTES,
    TOKENS_PER_IMAGE,
    PreparedImage,
    estimate_text_tokens,
    prepare,
)

Image = pytest.importorskip("PIL.Image")


def jpeg(breite: int, hoehe: int, *, rauschen: bool = False, quality: int = 90) -> bytes:
    """Ein JPEG. Mit Rauschen wird es gross genug, um die Byte-Grenze zu reissen."""
    if rauschen:
        # Echtes Rauschen aus dem Zufallsgenerator des Systems: unkomprimierbar
        # und um Groessenordnungen schneller als Pixel einzeln zu setzen.
        import os

        bild = Image.frombytes("RGB", (breite, hoehe), os.urandom(breite * hoehe * 3))
    else:
        bild = Image.new("RGB", (breite, hoehe), (120, 80, 40))
    puffer = io.BytesIO()
    bild.save(puffer, format="JPEG", quality=quality)
    return puffer.getvalue()


# --------------------------------------------------------------------------
# Der Befund: Groesse aendert den Preis nicht
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("breite", "hoehe"),
    [(256, 144), (768, 432), (1280, 720), (2048, 1152), (4096, 2304)],
)
def test_token_haengen_nicht_an_der_bildgroesse(breite: int, hoehe: int) -> None:
    """Gemessen bei Google und Groq: derselbe Preis ueber den ganzen Bereich."""
    bild = PreparedImage(b"", "image/jpeg", breite, hoehe, 0, False)
    assert bild.estimated_tokens == TOKENS_PER_IMAGE


def test_schaetzung_liegt_nicht_unter_dem_gemessenen() -> None:
    """Zu niedrig zu schaetzen laeuft in den 429; zu hoch bremst nur.

    Gemessen: Google 1110, Groq 792.
    """
    assert TOKENS_PER_IMAGE >= 1000


# --------------------------------------------------------------------------
# Verkleinern: nur wenn es Bandbreite spart
# --------------------------------------------------------------------------


def test_kamerabild_bleibt_unangetastet() -> None:
    """Der Normalfall. 1280x720 mit rund 80 KB — verkleinern braechte nichts
    ausser Qualitaetsverlust."""
    original = jpeg(1280, 720)
    assert len(original) < MAX_UPLOAD_BYTES

    nachher = prepare(original, "image/jpeg")
    assert not nachher.scaled
    assert nachher.data is original
    assert nachher.width == 1280


def test_sehr_grosses_bild_wird_verkleinert() -> None:
    """Ein Handyfoto mit mehreren Megabyte kostet Uploadzeit — das ist der
    einzige Grund, ueberhaupt zu verkleinern."""
    original = jpeg(4000, 3000, rauschen=True, quality=95)
    assert len(original) > MAX_UPLOAD_BYTES, "Testbild nicht gross genug"

    nachher = prepare(original, "image/jpeg")
    assert nachher.scaled
    assert max(nachher.width, nachher.height) == MAX_EDGE
    assert len(nachher.data) < len(original)
    assert nachher.estimated_tokens == TOKENS_PER_IMAGE  # aendert sich trotzdem nicht


def test_seitenverhaeltnis_bleibt() -> None:
    nachher = prepare(jpeg(4000, 3000, rauschen=True, quality=95), "image/jpeg")
    assert nachher.width == MAX_EDGE
    assert nachher.height == round(3000 * MAX_EDGE / 4000)


def test_grosse_datei_mit_kleinen_abmessungen_wird_neu_kodiert() -> None:
    """Auch ohne Verkleinern lohnt die Neukodierung, wenn die Datei riesig ist."""
    original = jpeg(1200, 900, rauschen=True, quality=100)
    if len(original) <= MAX_UPLOAD_BYTES:
        pytest.skip("Testbild nicht gross genug")
    nachher = prepare(original, "image/jpeg")
    assert nachher.scaled
    assert (nachher.width, nachher.height) == (1200, 900)
    assert len(nachher.data) < len(original)


def test_eigene_grenzwerte() -> None:
    nachher = prepare(jpeg(1280, 720), "image/jpeg", max_edge=512, max_bytes=1)
    assert nachher.scaled
    assert max(nachher.width, nachher.height) == 512


# --------------------------------------------------------------------------
# Robustheit
# --------------------------------------------------------------------------


def test_unlesbares_bild_geht_unveraendert_hinaus() -> None:
    """Ein Bild nicht zu verkleinern kostet Bandbreite. Es gar nicht zu
    schicken kostet die Analyse."""
    muell = b"das ist kein Bild"
    nachher = prepare(muell, "image/jpeg")
    assert nachher.data == muell
    assert not nachher.scaled
    assert nachher.estimated_tokens == TOKENS_PER_IMAGE


def test_unbekannter_mime_typ_wird_zu_jpeg_erklaert() -> None:
    assert prepare(b"kaputt", "image/tiff").mime_type == "image/jpeg"


def test_png_bleibt_png_solange_es_klein_ist() -> None:
    puffer = io.BytesIO()
    Image.new("RGB", (320, 240), (10, 200, 90)).save(puffer, format="PNG")
    nachher = prepare(puffer.getvalue(), "image/png")
    assert nachher.mime_type == "image/png"
    assert not nachher.scaled


def test_beschreibung_nennt_das_wesentliche() -> None:
    text = prepare(jpeg(1280, 720), "image/jpeg").describe()
    assert "1280x720" in text
    assert "unveraendert" in text
    assert "Token" in text


def test_textschaetzung() -> None:
    assert estimate_text_tokens("") == 1
    assert estimate_text_tokens("x" * 400) == 100
