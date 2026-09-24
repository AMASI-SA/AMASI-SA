"""Readability overlay for the merchant-approved Amasi A4 product card.

Keeps the locked A4 / 3x5 geometry and enlarges the product image / QR pair
equally. The A4 renderer handles original option order and full text wrapping.
"""
from __future__ import annotations

from typing import Any

from reportlab.lib.units import mm


_INSTALLED = False


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalized(value: Any) -> str:
    return _text(value).casefold().replace("ـ", "")


def _compact_label(label: Any) -> str:
    """Keep verbose Salla questions from pushing values across the divider."""
    raw = _text(label)
    normalized = _normalized(raw)
    if not raw:
        return ""
    if "شماغ" in normalized:
        return "الشماغ المجاني"
    if "تطريز" in normalized and "اسم" in normalized:
        return "تطريز الاسم"
    if len(raw) <= 17:
        return raw
    words = raw.split()
    compact = ""
    for word in words:
        candidate = word if not compact else f"{compact} {word}"
        if len(candidate) > 17:
            break
        compact = candidate
    return compact or raw[:17]


def _wrap_value(value: Any, *, first_limit: int = 18, continuation_limit: int = 22) -> list[str]:
    """Wrap customer option text on word boundaries, at most two visual rows."""
    raw = _text(value)
    if not raw:
        return []
    words = raw.split()
    lines: list[str] = []
    current = ""
    limit = first_limit
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= limit or not current:
            current = candidate
            continue
        lines.append(current)
        current = word
        limit = continuation_limit
    if current:
        lines.append(current)
    if len(lines) <= 2:
        return lines
    return [lines[0], " ".join(lines[1:])]


def install_preparation_pdf_card_readability_overlay() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    import preparation_pdf_amasi_a4_layout as layout

    # Both media blocks remain identical for visual balance and QR scanning.
    # The A4 renderer wraps full labels and values itself. Preserve the source
    # snapshot order here; reversing and abbreviating it loses Salla's order.
    layout.MEDIA_SIZE = 24.0 * mm
    layout.MEDIA_GAP = 1.4 * mm
    _INSTALLED = True


__all__ = [
    "_compact_label",
    "_wrap_value",
    "install_preparation_pdf_card_readability_overlay",
]
