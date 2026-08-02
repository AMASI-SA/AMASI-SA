"""Stamp each reviewed preparation card with card-number/file-number.

The approved PDF renderer numbers cards as ``1``, ``2``, ... .  Preparation
files already have a permanent registry number such as ``PF-20260802-0017``.
This preview-only support layer keeps the approved 3x5 layout unchanged and
replaces the visible serial with ``1-17``, ``2-17``, ... .
"""
from __future__ import annotations

import re
from functools import wraps
from typing import Any, Callable

import fitz

import preparation_pdf_reference_layout as reference


_MM = 72.0 / 25.4
_INSTALLED = False
_ORIGINAL_RENDERER: Callable[[dict[str, Any]], bytes] | None = None


def _text(value: Any) -> str:
    return str(value or "").strip()


def preparation_file_sequence(file_number: Any) -> str:
    """Return the human sequence suffix from a registry file number.

    ``PF-20260802-0017`` becomes ``17``.  The registry always emits this
    format, while the fallback preserves a non-standard value rather than
    hiding it.
    """
    raw = _text(file_number)
    if not raw:
        return ""
    match = re.search(r"(\d+)\s*$", raw)
    if not match:
        return raw
    return str(int(match.group(1)))


def preparation_card_file_label(card_number: int, file_number: Any) -> str:
    """Build the merchant-facing label, for example ``5-17``."""
    sequence = preparation_file_sequence(file_number)
    card = max(1, int(card_number))
    return f"{card}-{sequence}" if sequence else str(card)


def stamp_preparation_card_file_numbers(
    pdf_bytes: bytes,
    *,
    file_number: Any,
    card_count: int,
) -> bytes:
    """Replace the serial at the top of each approved 3x5 card.

    Only the small serial strip is redacted. Images, QR codes, quantities,
    product fields, and delivery counts remain byte-for-byte visually
    unchanged outside that strip.
    """
    sequence = preparation_file_sequence(file_number)
    count = max(0, int(card_count or 0))
    if not pdf_bytes or not sequence or count <= 0:
        return pdf_bytes

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        cards_per_page = reference.REFERENCE_CARDS_PER_PAGE
        columns = reference.REFERENCE_COLUMNS
        rows = reference.REFERENCE_ROWS

        for page_index, page in enumerate(document):
            first_card = page_index * cards_per_page
            remaining = count - first_card
            if remaining <= 0:
                break
            cards_on_page = min(cards_per_page, remaining)

            page_width = float(page.rect.width)
            page_height = float(page.rect.height)
            margin_x = 7 * _MM
            margin_y = 6 * _MM
            card_width = (page_width - 2 * margin_x) / columns
            card_height = (page_height - 2 * margin_y) / rows

            placements: list[tuple[fitz.Rect, float, float, str]] = []
            for card_index in range(cards_on_page):
                logical_column = card_index % columns
                column = columns - 1 - logical_column
                row = card_index // columns
                center_x = margin_x + column * card_width + card_width / 2
                card_top = margin_y + row * card_height

                # The approved renderer places the 8.5pt serial at this exact
                # centered baseline. Keep the redaction below the media area,
                # which starts 13pt from the card top.
                redact = fitz.Rect(
                    center_x - 38.0,
                    card_top + 0.2,
                    center_x + 38.0,
                    card_top + 12.4,
                )
                label = preparation_card_file_label(
                    first_card + card_index + 1,
                    file_number,
                )
                placements.append((redact, center_x, card_top + 8.5, label))
                page.add_redact_annot(redact, fill=(1, 1, 1))

            page.apply_redactions()
            for _rect, center_x, baseline_y, label in placements:
                font_size = 8.5
                text_width = fitz.get_text_length(
                    label,
                    fontname="helv",
                    fontsize=font_size,
                )
                page.insert_text(
                    (center_x - text_width / 2, baseline_y),
                    label,
                    fontname="helv",
                    fontsize=font_size,
                    color=(0.082, 0.082, 0.082),
                    overlay=True,
                )

        return document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()


def install_preparation_pdf_card_file_number() -> None:
    """Install after reference-layout and wrapped-text renderers."""
    global _INSTALLED, _ORIGINAL_RENDERER
    if _INSTALLED:
        return

    import reviewed_preparation_batches as batch

    _ORIGINAL_RENDERER = batch.render_preparation_batch_pdf

    @wraps(_ORIGINAL_RENDERER)
    def render_with_card_file_number(batch_row: dict[str, Any]) -> bytes:
        assert _ORIGINAL_RENDERER is not None
        rendered = _ORIGINAL_RENDERER(batch_row)
        card_count = sum(
            1
            for row in (batch_row.get("lines") or [])
            if isinstance(row, dict)
        )
        return stamp_preparation_card_file_numbers(
            rendered,
            file_number=batch_row.get("file_number"),
            card_count=card_count,
        )

    batch.render_preparation_batch_pdf = render_with_card_file_number
    _INSTALLED = True


__all__ = [
    "install_preparation_pdf_card_file_number",
    "preparation_card_file_label",
    "preparation_file_sequence",
    "stamp_preparation_card_file_numbers",
]
