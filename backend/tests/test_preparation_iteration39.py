"""Iter-39 — Cairo SemiBold font + spacing + field-order tests.

The merchant asked to change the printable PDF font to Cairo SemiBold
(with Cairo Bold for accent rows) and increase spacing between data
rows on each card. This file verifies the change is in effect.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import sys
sys.path.insert(0, "/app/backend")
import preparation_pdf  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_font_registration():
    """Force `_register_font()` to re-pick the font on each test run.
    Without this the global flag survives across tests, masking
    regressions."""
    preparation_pdf._FONT_REGISTERED = False
    preparation_pdf._FONT_NAME = "Helvetica"
    preparation_pdf._FONT_BOLD_NAME = "Helvetica-Bold"
    yield
    preparation_pdf._FONT_REGISTERED = False
    preparation_pdf._FONT_NAME = "Helvetica"
    preparation_pdf._FONT_BOLD_NAME = "Helvetica-Bold"


def test_cairo_ttf_files_are_bundled():
    """Without the bundled TTFs, deployments would fall back to Noto
    Naskh — that's an SLO miss for the merchant's font request."""
    fonts_dir = Path("/app/backend/fonts")
    assert (fonts_dir / "Cairo-SemiBold.ttf").exists(), \
        "Cairo-SemiBold.ttf must be bundled under backend/fonts/"
    assert (fonts_dir / "Cairo-Bold.ttf").exists(), \
        "Cairo-Bold.ttf must be bundled under backend/fonts/"
    # Sanity: ≥10 KB each — guards against accidental empty/HTML files
    assert (fonts_dir / "Cairo-SemiBold.ttf").stat().st_size > 50_000
    assert (fonts_dir / "Cairo-Bold.ttf").stat().st_size > 50_000


def test_register_font_picks_arabic_capable_primary():
    """Iter-39 originally required Cairo as the primary. Iter-42 promoted
    Noto Sans Arabic because Cairo's CSS-API TTF only covered 89/144
    Arabic Presentation Forms-B — broken glyphs for merchant product
    names. Both fonts are still REGISTERED (for fallback / future
    per-glyph picker), but the primary must be Arabic-complete."""
    name, bold = preparation_pdf._register_font()
    # In any reasonable bundle, primary must NOT be Helvetica.
    assert name != "Helvetica"
    # Cairo or Noto are both acceptable PRIMARY values; whichever one
    # _register_font() picks must have BOTH its SemiBold + Bold faces.
    assert name.endswith("SemiBold"), name
    assert bold.endswith("Bold"), bold
    # idempotent — second call must return the same tuple
    assert preparation_pdf._register_font() == (name, bold)
    # Both bundled font families should be registered as fallbacks.
    assert "Cairo-SemiBold" in preparation_pdf._FONT_CMAPS or \
        "NotoSansArabic-SemiBold" in preparation_pdf._FONT_CMAPS


def test_generated_pdf_embeds_arabic_font():
    """The output PDF must embed at least one Arabic-capable font
    (Cairo OR Noto Sans Arabic — iter-42 made Noto the primary)."""
    import fitz
    from preparation_pdf import parse_salla_orders_pdf, generate_preparation_pdf

    sample = Path("/tmp/compare/original_salla.pdf")
    if not sample.exists():
        pytest.skip("Fixture missing")

    lines = parse_salla_orders_pdf(sample.read_bytes())
    pdf_bytes = generate_preparation_pdf(lines)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        fonts_seen: set[str] = set()
        for page in doc:
            for f in page.get_fonts():
                # Each font tuple: (xref, ext, type, basefont, refname, encoding, ...)
                fonts_seen.add(f[3])
        # PDF subset prefixes like "AAAAAA+" — strip them for the check.
        bare = {n.split("+", 1)[-1] for n in fonts_seen}
        assert any("Cairo" in n or "NotoSansArabic" in n for n in bare), (
            f"No Arabic-capable font embedded; saw {fonts_seen}"
        )
    finally:
        doc.close()


def test_line_gap_increased_for_iter39():
    """Iter-39 raised the inter-line gap from 2.0 → 3.5 pt. We don't
    have a direct API for this constant, so we parse the source as
    text — crude but resilient.
    """
    src = Path("/app/backend/preparation_pdf.py").read_text(encoding="utf-8")
    # The current literal lives on a single line inside generate_preparation_pdf
    import re
    m = re.search(r"line_gap\s*=\s*([\d.]+)", src)
    assert m, "line_gap constant not found in preparation_pdf.py"
    val = float(m.group(1))
    assert val >= 3.0, f"line_gap should be ≥ 3.0 pt (iter-39 spacing), got {val}"


def test_field_order_in_build_text_lines_matches_spec():
    """The merchant locked the field order in iter-39:
       Order # → Product → الاسم → المقاس/اللون → ملاحظة → Date+Qty → Shipping

    We assert the spec by inspecting `_build_text_lines` source. (We
    could call it dynamically with a hand-crafted ProductLine, but the
    function is defined as a closure inside `generate_preparation_pdf`
    so source-level inspection is the simplest assertion.)
    """
    src = Path("/app/backend/preparation_pdf.py").read_text(encoding="utf-8")
    # The block must contain the documented order — find the docstring
    # of _build_text_lines and check the field markers appear in order.
    import re
    m = re.search(r"def _build_text_lines.*?return block", src, re.DOTALL)
    assert m, "_build_text_lines closure not found"
    body = m.group(0)
    # Look for the field keys in declared order.
    markers = [
        "Order# (with",        # comment above order_visual
        "Product name —",      # comment above product_name block
        'الاسم: {line.customer_name}',
        "المقاس:",
        "اللون:",
        "f\"ملاحظة:",
        "tail = qty_str",
        "ship_line = f",
    ]
    last_pos = -1
    for marker in markers:
        pos = body.find(marker)
        assert pos > last_pos, (
            f"field '{marker}' is out of order — expected after position {last_pos}, found at {pos}"
        )
        last_pos = pos


def test_pdf_uses_bold_font_for_accent_rows():
    """When Cairo Bold is registered, the bold rows (Order#, Product,
    Shipping) must use Cairo-Bold while the rest use Cairo-SemiBold.
    We verify both fonts end up embedded — Cairo Bold's presence proves
    at least one row triggered the bold branch."""
    import fitz
    from preparation_pdf import parse_salla_orders_pdf, generate_preparation_pdf
    sample = Path("/tmp/compare/original_salla.pdf")
    if not sample.exists():
        pytest.skip("Fixture missing")
    lines = parse_salla_orders_pdf(sample.read_bytes())
    pdf = generate_preparation_pdf(lines)
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        basefonts: set[str] = set()
        for page in doc:
            for f in page.get_fonts():
                basefonts.add(f[3])
    finally:
        doc.close()
    has_semi = any("SemiBold" in n for n in basefonts)
    has_bold = any("Bold" in n and "SemiBold" not in n for n in basefonts)
    assert has_semi, f"Cairo-SemiBold missing from {basefonts}"
    assert has_bold, f"Cairo-Bold missing from {basefonts}"
