"""Readability overlay for the merchant-approved Amasi A4 product card.

This keeps the locked 3x5/A4 geometry while fixing three visual defects found
in the real PDF: customer options were shown in the opposite operational order,
long option values were ellipsized instead of continuing on the next line, and
the image/QR pair was too small inside each card.
"""
from __future__ import annotations

from typing import Any

from reportlab.lib.units import mm


_INSTALLED = False


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _wrap_value(value: Any, *, first_limit: int = 22, continuation_limit: int = 27) -> list[str]:
    """Wrap Arabic/customer option text on word boundaries, at most two rows."""
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
        if len(lines) == 1:
            # Keep the remaining text intact on the second visual row. The
            # renderer still performs a width safety-fit, but normal customer
            # choices now receive a real continuation line instead of ellipsis.
            continue
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

    original_spec_rows = layout._spec_rows

    def readable_spec_rows(line):
        # The operational reading order requested by the merchant is the
        # reverse of the source projection: the final source field (for
        # example an accessory choice) is shown first, then name, with size
        # last. This makes "الاسم" immediately precede "المقاس".
        rows = list(reversed(original_spec_rows(line)))
        rendered: list[tuple[str, str]] = []
        for label, value in rows:
            wrapped = _wrap_value(value)
            if not wrapped:
                continue
            rendered.append((label, wrapped[0]))
            for continuation in wrapped[1:]:
                rendered.append(("", continuation))
        return rendered

    # Increase both product image and QR equally. 24 mm still fits safely in
    # each 3-column A4 card and materially improves scanning/visual inspection.
    layout.MEDIA_SIZE = 24.0 * mm
    layout.MEDIA_GAP = 1.4 * mm
    layout._spec_rows = readable_spec_rows
    _INSTALLED = True


__all__ = [
    "install_preparation_pdf_card_readability_overlay",
]
