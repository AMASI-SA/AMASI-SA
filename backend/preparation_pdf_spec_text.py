"""Print customer specifications with Arabic shaping and symbol fallback."""
from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime
from functools import lru_cache
from itertools import groupby
from pathlib import Path
from unicodedata import bidirectional, mirrored

from reportlab.lib.colors import Color, HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont, TTFError
from reportlab.pdfgen.canvas import Canvas

from preparation_pdf import _ar


SPEC_SYMBOLS_FROM = "PREPARATION_PDF_SYMBOLS_FROM"
_SYMBOL_FONT = "NotoSansSymbols2-Print"
_PRESENTATION_SELECTORS = str.maketrans("", "", "\ufe0e\ufe0f")
_HEART_COLORS = {
    "❤": HexColor("#B51A37"),
    "🩷": HexColor("#C84276"),
    "🩶": HexColor("#64686F"),
    "🩵": HexColor("#4B93B9"),
    "🧡": HexColor("#D56328"),
    "💛": HexColor("#BC8D08"),
    "💚": HexColor("#397C46"),
    "💙": HexColor("#3E65A9"),
    "💜": HexColor("#7952A3"),
    "🤎": HexColor("#775043"),
    "🖤": HexColor("#111111"),
}
_HEART_GLYPHS = {char: "♥" for char in _HEART_COLORS if char != "❤"}
_HEART_GLYPHS["🤍"] = "♡"
_LOG = logging.getLogger(__name__)


def aware_instant(value: str | None) -> datetime | None:
    """Treat missing or date-only snapshots as ineligible for the rollout."""
    if not value:
        return None
    try:
        instant = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return instant if instant.utcoffset() is not None else None


def _visual_order(value: str) -> str:
    value = value.translate(_PRESENTATION_SELECTORS)
    # Older Unicode tables classify new emoji as unassigned. The legacy bidi
    # implementation rejects that class and _ar would return unshaped Arabic.
    # Give those code points the same neutral direction as other print symbols
    # during shaping and reordering, then restore their original identities.
    unknown = dict.fromkeys(ch for ch in value if not bidirectional(ch))
    if not unknown:
        return _ar(value)
    original = set(value)
    markers = (
        ch for ch in map(chr, range(0x2600, 0x1FB00))
        if ch not in original and bidirectional(ch) == "ON" and not mirrored(ch)
    )
    swaps = dict(zip(unknown, markers))
    visual = _ar(value.translate(str.maketrans(swaps)))
    return visual.translate(str.maketrans({marker: ch for ch, marker in swaps.items()}))


@lru_cache(maxsize=1)
def _symbol_coverage() -> dict[int, int]:
    # Embed the actual glyphs instead of relying on a viewer or printer's
    # locally installed Symbol/ZapfDingbats fonts.
    path = Path(__file__).with_name("fonts") / f"{_SYMBOL_FONT}.ttf"
    try:
        font = TTFont(_SYMBOL_FONT, str(path))
        pdfmetrics.registerFont(font)
        return font.face.charToGlyph
    except (OSError, TTFError):
        _LOG.exception("Unable to load the embedded preparation PDF symbol font")
        return {}


def _runs(value: str, font: str) -> Iterator[tuple[str, Color | None, str]]:
    visual = _visual_order(value)
    coverage = getattr(pdfmetrics.getFont(font).face, "charToGlyph", {})

    def style(char: str) -> tuple[str, Color | None]:
        glyph = ord(_HEART_GLYPHS.get(char, char))
        face = font
        if not coverage.get(glyph) and _symbol_coverage().get(glyph):
            face = _SYMBOL_FONT
        return face, _HEART_COLORS.get(char)

    for (run_font, color), chars in groupby(visual, key=style):
        yield run_font, color, "".join(_HEART_GLYPHS.get(ch, ch) for ch in chars)


def specification_width(value: str, font: str, size: float) -> float:
    return sum(pdfmetrics.stringWidth(text, face, size) for face, _, text in _runs(value, font))


def draw_specification_text(
    pdf: Canvas, value: str, font: str, size: float, color: Color, right: float, y: float,
) -> None:
    """Draw the same glyph runs the wrapping planner measured."""
    runs = list(_runs(value, font))
    width = sum(pdfmetrics.stringWidth(text, face, size) for face, _, text in runs)
    text_object = pdf.beginText(right - width, y)
    for face, symbol_color, text in runs:
        text_object.setFont(face, size)
        text_object.setFillColor(symbol_color if symbol_color is not None else color)
        text_object.textOut(text)
    pdf.drawText(text_object)
