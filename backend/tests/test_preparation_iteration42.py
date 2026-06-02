"""Iter-42 — Arabic glyph-coverage regression tests.

Merchant complaint: "نوع الخط يظهر الترميز حق الأحرف غلط" — Cairo TTFs
fetched from Google's Fonts CSS API were SUBSETTED (89/144 presentation
forms FE70-FEFF; 102/256 Arabic base). When arabic-reshaper emitted a
presentation form NOT in Cairo's cmap, ReportLab rendered a `.notdef`
box. Iter-42 introduces **Noto Sans Arabic** as the primary font (full
coverage: 256/256 base + 141/144 presentation-B + 631/688 presentation-A).
This file locks in the coverage guarantee so a future Cairo regression
or accidental subsetting would fail the build.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "/app/backend")


@pytest.fixture(autouse=True)
def _reset_font_registration():
    import preparation_pdf
    preparation_pdf._FONT_REGISTERED = False
    preparation_pdf._FONT_NAME = "Helvetica"
    preparation_pdf._FONT_BOLD_NAME = "Helvetica-Bold"
    preparation_pdf._FONT_CMAPS.clear()
    yield
    preparation_pdf._FONT_REGISTERED = False
    preparation_pdf._FONT_NAME = "Helvetica"
    preparation_pdf._FONT_BOLD_NAME = "Helvetica-Bold"
    preparation_pdf._FONT_CMAPS.clear()


def test_noto_sans_arabic_files_are_bundled():
    fonts_dir = Path("/app/backend/fonts")
    semi = fonts_dir / "NotoSansArabic-SemiBold.ttf"
    bold = fonts_dir / "NotoSansArabic-Bold.ttf"
    assert semi.exists(), f"missing {semi}"
    assert bold.exists(), f"missing {bold}"
    assert semi.stat().st_size > 100_000, "NotoSansArabic SemiBold suspiciously small"
    assert bold.stat().st_size > 100_000, "NotoSansArabic Bold suspiciously small"


def test_noto_sans_arabic_has_full_coverage():
    """Noto Sans Arabic must cover all the codepoints we know we use.
    If Google ever ships a subsetted build, this test will catch it
    BEFORE the merchant does."""
    from fontTools.ttLib import TTFont
    for name in ("NotoSansArabic-SemiBold", "NotoSansArabic-Bold"):
        path = Path("/app/backend/fonts") / f"{name}.ttf"
        cmap = TTFont(str(path)).getBestCmap()
        chars = set(cmap.keys())
        pres_b = sum(1 for k in chars if 0xFE70 <= k <= 0xFEFF)
        ar_base = sum(1 for k in chars if 0x0600 <= k <= 0x06FF)
        # Iter-42 measured 141/144 + 256/256 on the day we bundled.
        # Allow only a TINY downward drift (≤3 presentation glyphs).
        assert ar_base == 256, f"{name}: Arabic base coverage regressed: {ar_base}/256"
        assert pres_b >= 141, f"{name}: presentation forms regressed: {pres_b}/144"


def test_register_font_picks_noto_first():
    """Primary font in iter-42+ must be Noto Sans Arabic when bundled."""
    import preparation_pdf
    name, bold = preparation_pdf._register_font()
    assert name == "NotoSansArabic-SemiBold", f"expected Noto primary, got {name}"
    assert bold == "NotoSansArabic-Bold", f"expected Noto bold, got {bold}"
    # Cairo must STILL be registered as a fallback (per the merchant's
    # "أو إضافة أكثر من نوع خط عربي" request).
    assert "Cairo-SemiBold" in preparation_pdf._FONT_CMAPS
    assert "Cairo-Bold" in preparation_pdf._FONT_CMAPS


def test_primary_font_covers_real_merchant_product_names():
    """The exact product names from the merchant's June 2026 sample
    MUST be drawable in the primary font without any missing glyphs.
    Each of these was a confirmed Cairo failure pre-iter-42."""
    import preparation_pdf
    preparation_pdf._register_font()
    from preparation_pdf import _ar, _font_supports

    SAMPLES = [
        "تعليقة النصر",
        "بروش غزال",
        "قلادة روز",
        "بروش قبعة التخرج",
        "أبو عمر",
        "كف بالاسم للأطفال",
        "تغليف انيق آمايس",
        "طقم عباية نسائي",
        "ساعة بولغاري",
        "لوحة اللهم ألف",
        "أحمد",
        "حور عبدالعزيز",
        # And the labels we draw on every card:
        "الاسم",
        "المقاس",
        "اللون",
        "ملاحظة",
    ]
    failed: list[str] = []
    for s in SAMPLES:
        reshaped = _ar(s)
        if not _font_supports("NotoSansArabic-SemiBold", reshaped):
            failed.append(s)
    assert not failed, (
        "Noto Sans Arabic is missing glyphs for merchant strings: "
        f"{failed} — bundled TTF may be subsetted."
    )


def test_generated_pdf_embeds_noto_font():
    import fitz
    import preparation_pdf
    preparation_pdf._register_font()
    from preparation_pdf import parse_salla_orders_pdf, generate_preparation_pdf

    sample = Path("/tmp/compare/original_salla.pdf")
    if not sample.exists():
        pytest.skip("Fixture missing")
    lines = parse_salla_orders_pdf(sample.read_bytes())
    pdf_bytes = generate_preparation_pdf(lines)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        fonts = {f[3] for page in doc for f in page.get_fonts()}
    finally:
        doc.close()
    assert any("Noto" in n for n in fonts), (
        f"Noto font NOT embedded in output PDF; saw {fonts}"
    )


def test_font_supports_helper_behaviour():
    import preparation_pdf
    preparation_pdf._register_font()
    from preparation_pdf import _font_supports
    # Basic Latin always covered
    assert _font_supports("NotoSansArabic-SemiBold", "Hello 123")
    # Whitespace is ignored
    assert _font_supports("NotoSansArabic-SemiBold", "   ")
    # Unknown font name → False, not crash
    assert not _font_supports("DoesNotExist", "abc")
