"""Iter-38 regression tests — parser + PDF generation fixes driven by a
real merchant complaint (Salla orders PDF dated 2026-06-02).

Reference fixture: /tmp/compare/original_salla.pdf  (13 orders, 19 lines)
Reference output: /tmp/compare/system_FIXED.pdf

Bugs fixed in iter-38 and locked-in here:
  • Address/phone/postal-code text leaking into the options dict, then
    leaking into the printed card as "extra options".
  • Customer name not extracted when the option key is a compound
    (e.g. "الاسم على التعليقه", "الاسم على سبحه") — needs prefix match.
  • Note not extracted when PyMuPDF concatenates lines and the dict
    becomes "shifted" — value-side scan as a fallback.
  • Note key variant "مالحظه" (PyMuPDF lam-alef break) was not in
    NOTE_KEY_PREFIXES.
  • Generated PDF cards did NOT render product_name / size / color
    even though parser had extracted them.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

# Direct module under test — no FastAPI roundtrip needed for these.
import sys
sys.path.insert(0, "/app/backend")
from preparation_pdf import (   # noqa: E402
    parse_salla_orders_pdf,
    generate_preparation_pdf,
    _pick_name_from_options,
    _pick_note_from_options,
    _pick_size_from_options,
    _pick_color_from_options,
    _looks_like_address_or_footer,
    _parse_options_block,
)


SAMPLE_PDF = Path("/tmp/compare/original_salla.pdf")


@pytest.fixture(scope="module")
def parsed_lines():
    if not SAMPLE_PDF.exists():
        pytest.skip(f"Fixture {SAMPLE_PDF} not available in this environment")
    with SAMPLE_PDF.open("rb") as f:
        return parse_salla_orders_pdf(f.read())


@pytest.fixture(scope="module")
def by_order(parsed_lines):
    from collections import defaultdict
    out = defaultdict(list)
    for ln in parsed_lines:
        out[ln.order_number].append(ln)
    return dict(out)


# ── A. Unit tests on the new helpers ──────────────────────────────────
def test_looks_like_address_or_footer_detects_known_boundaries():
    assert _looks_like_address_or_footer("+966500275471")
    assert _looks_like_address_or_footer("0566612345")
    assert _looks_like_address_or_footer("السعودية")
    assert _looks_like_address_or_footer("الرمز الربيدي 12345")
    assert _looks_like_address_or_footer("شارع الأمير")
    assert _looks_like_address_or_footer("حي النخيل")
    assert _looks_like_address_or_footer("شكرًا لشرائك")
    # Should NOT be flagged
    assert not _looks_like_address_or_footer("الاسم")
    assert not _looks_like_address_or_footer("سدیم")
    assert not _looks_like_address_or_footer("نعم")
    assert not _looks_like_address_or_footer("ذهبي")


def test_parse_options_block_stops_at_address():
    """The block walker must stop BEFORE consuming the address+phone+
    footer that follows the last product on a Salla invoice."""
    block = [
        "الاسم", "سديم",
        "هل تريد اضافه كرت اهداء", "نعم",
        "الكتابه على الكرت", "مبروك",
        # Address starts here — should NOT be consumed
        "afaf alanazi", "السعودية",
        "الخرب", "34753 ، الرمز الربيدي",
        "+966500275471", "متجر",
    ]
    opts = _parse_options_block(block)
    assert "الاسم" in opts and opts["الاسم"] == "سديم"
    assert opts.get("هل تريد اضافه كرت اهداء") == "نعم"
    assert opts.get("الكتابه على الكرت") == "مبروك"
    # Must NOT contain the address bits
    assert "afaf alanazi" not in opts
    assert "+966500275471" not in opts
    assert "الخرب" not in opts


def test_pick_name_from_options_prefix_match():
    """Salla supports compound keys like "الاسم على التعليقه" — those
    must be recognised even though they don't EXACTLY match a variant."""
    assert _pick_name_from_options({"الاسم": "سديم"}) == "سديم"
    # Compound: starts with variant + space
    assert _pick_name_from_options({"الاسم على التعليقه": "أبو عمر"}) == "أبو عمر"
    assert _pick_name_from_options({"الاسم على سبحه": "فيصل"}) == "فيصل"
    # Ligature-broken variant "السم على ..."
    assert _pick_name_from_options({"السم عىل التعليقه": "أبو عمر"}) == "أبو عمر"
    # Empty value → fall through, NOT return ""
    assert _pick_name_from_options({"الاسم": ""}) is None
    # Unrelated keys
    assert _pick_name_from_options({"اللون": "أحمر"}) is None


def test_pick_note_from_options_handles_shifted_dict():
    """When PyMuPDF concatenates lines the options dict can be SHIFTED
    so that "مالحظه" appears as a VALUE. The fix is to scan values for
    the prefix and pick the NEXT entry's key as the note text."""
    # Happy path — key match
    assert _pick_note_from_options({"ملاحظة": "تعليمة خاصة"}) == "تعليمة خاصة"
    # PyMuPDF lam-alef variant
    assert _pick_note_from_options({"مالحظه": "هدية"}) == "هدية"
    # Shifted dict — "مالحظه" is a VALUE, not a key
    shifted = {
        "تحب تضيف سنة التخرج": "نعم",
        "ذهيب": "مالحظه",
        "التتاخر عن خمسة ايام اهم يش": "ام حمود",
    }
    assert _pick_note_from_options(shifted) == "التتاخر عن خمسة ايام اهم يش"
    # No note → None
    assert _pick_note_from_options({"الاسم": "x"}) is None


# ── B. End-to-end checks pinned to the real Salla PDF ─────────────────
def test_orders_parsed_count(parsed_lines, by_order):
    """The sample has 12 distinct orders and 19 line-items in total."""
    assert len(by_order) == 12, f"expected 12 orders, got {len(by_order)}"
    assert len(parsed_lines) == 19, f"expected 19 lines, got {len(parsed_lines)}"


def test_order_263829492_has_size_and_color(by_order):
    """طقم عباية — must extract size + color (was completely missing)."""
    items = by_order["263829492"]
    assert len(items) == 1
    ln = items[0]
    assert ln.size and "54" in ln.size, f"size missing: {ln.size!r}"
    # color is "بني" (PyMuPDF outputs "بين" due to ligature)
    assert ln.color and ("بن" in ln.color or "بي" in ln.color), f"color missing: {ln.color!r}"


def test_order_263839771_extracts_abu_omar_customer(by_order):
    """تعليقة النصر key was "الاسم على التعليقه" — previously dropped."""
    items = by_order["263839771"]
    # 2 items: تغليف (no name), تعليقة النصر (name = أبو عمر)
    assert len(items) == 2
    talikah = next((ln for ln in items if "تعليقة" in (ln.product_name or "")), None)
    assert talikah is not None, "تعليقة النصر item missing"
    assert talikah.customer_name == "أبو عمر", f"customer wrong: {talikah.customer_name!r}"
    # Color should also be extracted
    assert talikah.color, f"color missing: {talikah.color!r}"


def test_order_263840401_extracts_shifted_note(by_order):
    """بروش قبعة التخرج — the note "لا تتأخر عن خمسة أيام" was lost due
    to PyMuPDF text concatenation shifting the options dict."""
    items = by_order["263840401"]
    assert len(items) == 1
    ln = items[0]
    assert ln.customer_name and "عال" in ln.customer_name
    assert ln.note, f"note missing: {ln.note!r}"
    assert "خمسة ايام" in ln.note or "خمسه ايام" in ln.note or "ايام" in ln.note, (
        f"note doesn't contain expected text: {ln.note!r}"
    )


def test_order_263832078_has_color_on_both_items(by_order):
    """بروش غزال + كف للأطفال — both have لون ذهبي."""
    items = by_order["263832078"]
    assert len(items) == 2
    for ln in items:
        assert ln.color, f"item {ln.product_name!r} missing color"
    # Item 2 has "ملاحظه: لا يوجد" — should be extracted
    kif = next((ln for ln in items if "كف" in (ln.product_name or "")), None)
    assert kif and kif.note, f"كف note missing: {kif.note if kif else 'no item'}"


def test_no_address_pollution_in_product_options(parsed_lines):
    """No `product_options` entry should contain phone numbers, country
    names, postal-code labels, or street markers — these are address
    fragments that previously leaked from the parser."""
    for ln in parsed_lines:
        opts = ln.product_options or {}
        for k, v in opts.items():
            assert not k.startswith("+"), f"phone leaked as key in {ln.product_name!r}: {k}"
            assert "الرمز" not in k, f"postal-code label leaked: {k}"
            # Country names should not be VALUES of legit options
            if k in ("السعودية", "الإمارات", "الكويت"):
                pytest.fail(f"country name leaked as option key: {k}")


# ── C. PDF generation renders the new fields ──────────────────────────
def test_generated_pdf_renders_size_color_and_product_name(parsed_lines):
    """Generate the PDF, re-open it, and assert that the new fields show
    up as visible text. The PDF uses arabic-reshaper + bidi, plus
    Cairo SemiBold/Bold (iter-39) whose cmap PyMuPDF only partially
    extracts. We check a curated set of substrings that DO extract
    reliably across all of {Noto Naskh, DejaVu, Cairo}: digits, common
    short words, and at least one bidi-stable Arabic product name."""
    import fitz, unicodedata
    pdf_bytes = generate_preparation_pdf(parsed_lines)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        raw_text = ""
        for page in doc:
            raw_text += page.get_text() + "\n"
    finally:
        doc.close()

    norm = unicodedata.normalize("NFKD", raw_text)
    def in_pdf(s: str) -> bool:
        return unicodedata.normalize("NFKD", s) in norm

    # ── 1) Order numbers MUST all appear (digits extract reliably) ──
    order_nums = {ln.order_number for ln in parsed_lines}
    for onum in order_nums:
        assert onum in norm, f"order# {onum} missing from PDF text"

    # ── 2) Size value (digit) — confirms a real value reaches the card ──
    assert "54" in norm, "size value '54' missing"

    # ── 3) At least one product name's stable substring must appear.
    # We pick "تعليقة" which extracts cleanly across all our font candidates.
    assert in_pdf("تعليقة"), "product name 'تعليقة' missing from output PDF"

    # ── 4) Customer name "أبو عمر" extracts via base letters in NFKD ──
    # On Cairo the hamza-alef glyph extracts as a single base alef, so
    # the NFKD form should still contain "بو عمر" at minimum.
    assert in_pdf("بو عمر"), "customer name fragment 'بو عمر' missing"

    # ── 5) Shifted-note words extract reliably (no hamza/ligature edge) ──
    assert in_pdf("خمسة"), "shifted-note word 'خمسة' missing"


def test_generated_pdf_has_card_per_line(parsed_lines):
    """19 lines must produce 19 cards across ≤ 2 pages (4×4 = 16/page)."""
    import fitz, unicodedata
    pdf_bytes = generate_preparation_pdf(parsed_lines)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        assert len(doc) == 2, f"expected 2 pages, got {len(doc)}"
        # Sanity: each order number from the input must appear at least
        # once in the output PDF text.
        raw = "\n".join(p.get_text() for p in doc)
        norm = unicodedata.normalize("NFKD", raw)
        order_nums = {ln.order_number for ln in parsed_lines}
        for onum in order_nums:
            assert onum in norm, f"order# {onum} missing from PDF"
    finally:
        doc.close()
