"""Die Markenbilder, die Home Assistant aus ``brand/`` laedt.

Seit HA 2026.3 liefert eine Custom Component ihre Bilder selbst aus, ohne
Eintrag im Repository ``home-assistant/brands``; die lokalen Bilder haben
Vorrang vor dem CDN. Home Assistant sucht nur feste Dateinamen. Ein Tippfehler
oder ein falsches Format faellt nirgends auf — die Integration steht dann
einfach ohne Symbol in der Liste.

Vorgaben aus dem Brands-Repository: PNG, quadratisches Icon in 256 und 512
Pixeln, Logo im Querformat mit 128 bis 256 (bzw. 256 bis 512) Pixeln an der
kurzen Seite, Transparenz. Die Quellen liegen als SVG unter ``assets/brand/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

BRAND = Path(__file__).resolve().parent.parent / "custom_components" / "free_ai_router" / "brand"

#: Dateiname -> (Mindest-, Hoechstmass der kurzen Seite), quadratisch ja/nein
ERWARTET = {
    "icon.png": ((256, 256), True),
    "icon@2x.png": ((512, 512), True),
    "logo.png": ((128, 256), False),
    "logo@2x.png": ((256, 512), False),
    "dark_logo.png": ((128, 256), False),
    "dark_logo@2x.png": ((256, 512), False),
}


def test_nur_bekannte_dateinamen() -> None:
    """Home Assistant kennt nur feste Namen; alles andere wird nie geladen."""
    bekannt = {
        f"{dunkel}{art}{hd}.png"
        for dunkel in ("", "dark_")
        for art in ("icon", "logo")
        for hd in ("", "@2x")
    }
    vorhanden = {p.name for p in BRAND.iterdir()}
    assert vorhanden <= bekannt, sorted(vorhanden - bekannt)
    assert set(ERWARTET) <= vorhanden, sorted(set(ERWARTET) - vorhanden)


@pytest.mark.parametrize(("name", "vorgabe"), sorted(ERWARTET.items()))
def test_format_und_groesse(name: str, vorgabe: tuple[tuple[int, int], bool]) -> None:
    (kleinste, groesste), quadratisch = vorgabe
    with Image.open(BRAND / name) as bild:
        assert bild.format == "PNG"
        assert bild.mode == "RGBA", "ohne Alphakanal keine Transparenz"
        breite, hoehe = bild.size
        if quadratisch:
            assert breite == hoehe == kleinste
        else:
            assert breite > hoehe, "Logo im Querformat"
            assert kleinste <= hoehe <= groesste
        # Beschnitten: die Ecken der Kachel sind durchsichtig, ein Rand aus
        # leeren Zeilen gibt es nicht.
        alpha = bild.getchannel("A")
        assert alpha.getpixel((0, 0)) == 0
        assert alpha.getbbox() == (0, 0, breite, hoehe)


def test_hell_und_dunkel_gleich_gross() -> None:
    for hd in ("", "@2x"):
        with Image.open(BRAND / f"logo{hd}.png") as hell, Image.open(
            BRAND / f"dark_logo{hd}.png"
        ) as dunkel:
            assert hell.size == dunkel.size
