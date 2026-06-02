"""Iter-40 regression tests — product image/name alignment fixes.

Two bugs discovered on the merchant's June 2026 sample:

  ① IMAGE/NAME SWAP on multi-item orders where Salla declared images
    out of visual order. The parser zipped images positionally by their
    XREF-declaration order, so when xref N was visually BELOW xref N-1
    the products got the wrong images. Reproduces on page 11 of the
    fixture (order #263839904 — "تغليف" and "قلادة" had their images
    swapped).

  ② MISSING IMAGE for repeated products in the same order. The parser
    de-duped image xrefs with a `seen_xrefs` set — so when an order
    contained the same product TWICE (e.g. order #263822478 has قلادة
    روز ×2), only ONE of the two product slots received an image; the
    second was left empty.

Both fixed in iter-40 by sorting candidates by visual `rect.y0` and
treating each rectangle as a separate slot.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

import pytest

import sys
sys.path.insert(0, "/app/backend")
from preparation_pdf import parse_salla_orders_pdf  # noqa: E402


SAMPLE_PDF = Path("/tmp/compare/original_salla.pdf")


@pytest.fixture(scope="module")
def parsed_lines():
    if not SAMPLE_PDF.exists():
        pytest.skip(f"Fixture {SAMPLE_PDF} missing")
    return parse_salla_orders_pdf(SAMPLE_PDF.read_bytes())


@pytest.fixture(scope="module")
def by_order(parsed_lines):
    out = defaultdict(list)
    for ln in parsed_lines:
        out[ln.order_number].append(ln)
    return dict(out)


def _hash(b: bytes | None) -> str:
    return hashlib.md5(b or b"").hexdigest()


def test_all_items_in_repeated_product_orders_have_images(by_order):
    """Bug ②: order #263822478 contains قلادة روز TWICE + تغليف once.
    Before iter-40 only 2/3 had image bytes; after fix all 3 must."""
    items = by_order["263822478"]
    assert len(items) == 3, items
    for ln in items:
        assert ln.image_bytes, (
            f"item idx={ln.item_index} ({ln.product_name!r}) has no image; "
            "xref-dedup bug (iter-40) regressed"
        )


def test_repeated_product_uses_same_image_bytes(by_order):
    """Bug ②: the TWO قلادة روز slots in order #263822478 must share
    the same image (Salla reuses the xref, but each rectangle is a
    distinct product slot). The hash MUST be identical for both."""
    items = [ln for ln in by_order["263822478"] if "قالدة" in (ln.product_name or "")]
    assert len(items) == 2, items
    h0 = _hash(items[0].image_bytes)
    h1 = _hash(items[1].image_bytes)
    assert h0 == h1, f"qaladah twins should have the same image bytes; {h0} vs {h1}"


def test_same_product_across_orders_has_same_image_hash(by_order):
    """Bug ①: تغليف آمايس appears in 4 distinct orders. Its image
    must be byte-identical every time. If the swap bug were still
    present, page 11 (order #263839904) would deliver a DIFFERENT
    hash (the قلادة image)."""
    targets = [
        ("تغليف", "آمايس image must be identical across all 4 orders"),
        ("قالدة روز", "قلادة روز image must be identical across all occurrences"),
    ]
    for keyword, msg in targets:
        hashes = set()
        for items in by_order.values():
            for ln in items:
                if keyword in (ln.product_name or "") and ln.image_bytes:
                    hashes.add(_hash(ln.image_bytes))
        assert len(hashes) == 1, f"{msg}; saw hashes: {hashes}"


def test_order_263839904_image_not_swapped(by_order):
    """Bug ①: the specific page where the swap was originally observed.
    تغليف should have the same image as the OTHER تغليف rows in OTHER
    orders; قلادة same logic."""
    items = by_order["263839904"]
    assert len(items) == 2
    taghleef = next((ln for ln in items if "تغليف" in (ln.product_name or "")), None)
    qaladah = next((ln for ln in items if "قالدة" in (ln.product_name or "")), None)
    assert taghleef and qaladah
    # The hashes must NOT be swapped: pick another order that has only
    # ONE of them and use it as a reference.
    ref_qaladah = next(
        ln for ln in by_order["263831603"]
        if "قالدة" in (ln.product_name or "")
    )
    assert _hash(qaladah.image_bytes) == _hash(ref_qaladah.image_bytes), (
        "قلادة on order #263839904 has the WRONG image (swap bug regressed)"
    )


def test_no_product_has_wrong_image_via_cross_order_consistency(by_order):
    """Stronger version of the cross-order check — looks at EVERY
    multi-occurrence product. If any product family has >1 distinct
    image hash, it's a smoking-gun for the swap bug."""
    hashes_per_product: dict[str, set[str]] = defaultdict(set)
    for items in by_order.values():
        for ln in items:
            if not ln.image_bytes or not ln.product_name:
                continue
            # Normalise the product name (whitespace) — Salla sometimes
            # has trailing spaces or ligature variants.
            key = " ".join(ln.product_name.lower().split())
            hashes_per_product[key].add(_hash(ln.image_bytes))
    multi = {k: v for k, v in hashes_per_product.items() if len(v) > 1}
    assert not multi, (
        "Some products have multiple image hashes across orders — "
        f"image/name alignment bug: {multi}"
    )
