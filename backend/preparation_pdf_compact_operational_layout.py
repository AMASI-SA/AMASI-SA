"""Compact operational layout for reviewed preparation PDF cards.

This overlay keeps the approved 3x5 A4 layout, unit-card expansion, wrapped
specifications, selected product images, and card/file numbering while making
four merchant-approved presentation changes:

* print shipping company and order piece count without the ``للتوصيل`` label;
* omit the product title below the image and QR code;
* add visible horizontal and vertical space between cards;
* place the product image and QR code closer together.
"""
from __future__ import annotations

import io
from typing import Any

import fitz
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from preparation_pdf import ProductLine, _ar, _register_font
import preparation_pdf_reference_layout as reference
from preparation_pdf_wrapped_text import build_wrapped_specification_plan


REFERENCE_TEXT = HexColor("#151515")
REFERENCE_RED = HexColor("#D12B2B")
MARGIN_X = 7 * mm
MARGIN_Y = 6 * mm
COLUMN_GAP = 4 * mm
ROW_GAP = 4.5 * mm
INNER = 2.8 * mm
MEDIA_SIZE = 24 * mm
MEDIA_GAP = 1.5 * mm
# Keep the details on a clearly separate printed row below both the product
# image and QR. The previous 3.2-point offset was only ~1.1 mm and looked
# visually attached to the media blocks.
MEDIA_TO_DETAILS_GAP = 4.0 * mm
DETAIL_GAP = 1.8 * mm
_INSTALLED = False


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def compact_detail_top(media_bottom: float) -> float:
    """Return the first detail baseline below the image/QR media row."""
    return media_bottom - MEDIA_TO_DETAILS_GAP


def compact_card_dimensions(
    page_width: float = A4[0],
    page_height: float = A4[1],
) -> tuple[float, float]:
    """Return card width/height after explicit inter-card gaps are reserved."""
    card_width = (
        page_width
        - 2 * MARGIN_X
        - (reference.REFERENCE_COLUMNS - 1) * COLUMN_GAP
    ) / reference.REFERENCE_COLUMNS
    card_height = (
        page_height
        - 2 * MARGIN_Y
        - (reference.REFERENCE_ROWS - 1) * ROW_GAP
    ) / reference.REFERENCE_ROWS
    return card_width, card_height


def compact_card_pdf_origin(
    card_index: int,
    page_width: float = A4[0],
    page_height: float = A4[1],
) -> tuple[float, float, float, float]:
    """Return x/y/width/height in ReportLab's bottom-origin coordinates."""
    card_width, card_height = compact_card_dimensions(page_width, page_height)
    logical_column = card_index % reference.REFERENCE_COLUMNS
    column = reference.REFERENCE_COLUMNS - 1 - logical_column
    row = card_index // reference.REFERENCE_COLUMNS
    x = MARGIN_X + column * (card_width + COLUMN_GAP)
    y = (
        page_height
        - MARGIN_Y
        - (row + 1) * card_height
        - row * ROW_GAP
    )
    return x, y, card_width, card_height


def compact_media_positions(
    card_x: float,
    card_width: float,
) -> tuple[float, float]:
    """Return image and QR x positions with a fixed narrow media gap."""
    pair_width = 2 * MEDIA_SIZE + MEDIA_GAP
    pair_left = card_x + (card_width - pair_width) / 2
    return pair_left, pair_left + MEDIA_SIZE + MEDIA_GAP


def compact_reference_card_rows(
    line: ProductLine,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Remove only the delivery label while preserving its operational value."""
    specifications, order_rows = reference.split_reference_card_rows(line)
    compact_orders = [
        ("", value) if label == "للتوصيل" else (label, value)
        for label, value in order_rows
    ]
    return specifications, compact_orders


def generate_compact_operational_preparation_pdf(
    lines: list[ProductLine],
    *,
    serial_start: int = 1,
    title: str = "تجهيز المنتجات",
) -> bytes:
    """Render 15 operational cards per A4 page with compact media/details."""
    if not lines:
        raise ValueError("No product lines to render")

    font_name, font_bold = _register_font()
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    page_width, page_height = A4

    def draw_single_line_field(
        label: str,
        value: str,
        *,
        right: float,
        y: float,
        width: float,
        font_size: float,
        label_red: bool = True,
        bold_value: bool = False,
    ) -> None:
        clean = _text(value)
        if not clean:
            return
        value_font = font_bold if bold_value else font_name
        if not _text(label):
            value_visual = reference._fit_visual_line(
                clean,
                value_font,
                font_size,
                width,
            )
            pdf.setFont(value_font, font_size)
            pdf.setFillColor(REFERENCE_TEXT)
            pdf.drawRightString(right, y, value_visual)
            return

        label_visual = _ar(f"{label} :")
        pdf.setFont(font_bold, font_size)
        label_width = pdfmetrics.stringWidth(label_visual, font_bold, font_size)
        pdf.setFillColor(REFERENCE_RED if label_red else REFERENCE_TEXT)
        pdf.drawRightString(right, y, label_visual)
        value_right = right - label_width - 2.2
        value_width = max(8, width - label_width - 2.2)
        value_visual = reference._fit_visual_line(
            clean,
            value_font,
            font_size,
            value_width,
        )
        pdf.setFont(value_font, font_size)
        pdf.setFillColor(REFERENCE_TEXT)
        pdf.drawRightString(value_right, y, value_visual)

    def draw_wrapped_specifications(
        rows: list[tuple[str, str]],
        *,
        right: float,
        top: float,
        width: float,
        bottom: float,
    ) -> None:
        if not rows:
            return
        plan = build_wrapped_specification_plan(
            rows,
            font_name=font_name,
            font_bold=font_bold,
            width=width,
            available_height=max(3.0, top - bottom),
        )
        cursor = top
        for field in plan.fields:
            if not field.lines:
                continue
            label_visual = _ar(f"{field.label} :")
            pdf.setFont(font_bold, plan.font_size)
            label_width = pdfmetrics.stringWidth(
                label_visual,
                font_bold,
                plan.font_size,
            )
            pdf.setFillColor(REFERENCE_RED)
            pdf.drawRightString(right, cursor, label_visual)

            value_right = right - label_width - 2.0
            pdf.setFont(font_name, plan.font_size)
            pdf.setFillColor(REFERENCE_TEXT)
            pdf.drawRightString(value_right, cursor, _ar(field.lines[0]))
            cursor -= plan.line_height
            for continuation in field.lines[1:]:
                pdf.setFont(font_name, plan.font_size)
                pdf.setFillColor(REFERENCE_TEXT)
                pdf.drawRightString(right, cursor, _ar(continuation))
                cursor -= plan.line_height

    def draw_card(card_index: int, line: ProductLine, serial: int) -> None:
        x, y, card_width, card_height = compact_card_pdf_origin(
            card_index,
            page_width,
            page_height,
        )
        left = x + INNER
        right = x + card_width - INNER
        usable_width = right - left

        pdf.setFillColor(REFERENCE_TEXT)
        pdf.setFont(font_name, 8.5)
        pdf.drawCentredString(
            x + card_width / 2,
            y + card_height - 8.5,
            str(serial),
        )

        media_top = y + card_height - 13
        media_y = media_top - MEDIA_SIZE
        image_x, qr_x = compact_media_positions(x, card_width)

        qr_bytes = reference._qr_with_center_mark(
            line.barcode_payload or line.order_number
        )
        pdf.drawImage(
            ImageReader(io.BytesIO(qr_bytes)),
            qr_x,
            media_y,
            width=MEDIA_SIZE,
            height=MEDIA_SIZE,
            mask="auto",
        )

        product_image = reference._square_product_image(line.image_bytes)
        if product_image is not None:
            image_buffer = io.BytesIO()
            product_image.save(
                image_buffer,
                format="JPEG",
                quality=88,
                optimize=True,
            )
            image_buffer.seek(0)
            pdf.drawImage(
                ImageReader(image_buffer),
                image_x,
                media_y,
                width=MEDIA_SIZE,
                height=MEDIA_SIZE,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )

        # Product title is intentionally omitted. Details now start on a
        # separate visual row with a fixed 4 mm gap below both media blocks.
        detail_top = compact_detail_top(media_y)
        detail_bottom = y + INNER
        half_width = (usable_width - DETAIL_GAP) / 2
        left_half_right = left + half_width
        right_half_right = right
        specification_rows, order_rows = compact_reference_card_rows(line)

        draw_wrapped_specifications(
            specification_rows,
            right=right_half_right,
            top=detail_top,
            width=half_width,
            bottom=detail_bottom,
        )

        order_cursor = detail_top
        order_line_height = 8.15
        for label, value in order_rows:
            if order_cursor < detail_bottom:
                break
            is_shipping = not _text(label)
            draw_single_line_field(
                label,
                value,
                right=left_half_right,
                y=order_cursor,
                width=half_width,
                font_size=7.0 if is_shipping else 6.6,
                label_red=not is_shipping,
                bold_value=is_shipping,
            )
            order_cursor -= order_line_height

    for page_start in range(0, len(lines), reference.REFERENCE_CARDS_PER_PAGE):
        page_lines = lines[
            page_start:page_start + reference.REFERENCE_CARDS_PER_PAGE
        ]
        for index, line in enumerate(page_lines):
            draw_card(index, line, serial_start + page_start + index)
        pdf.showPage()

    pdf.save()
    output.seek(0)
    return output.getvalue()


def stamp_compact_preparation_card_file_numbers(
    pdf_bytes: bytes,
    *,
    file_number: Any,
    card_count: int,
) -> bytes:
    """Stamp card/file labels at positions matching the compact card grid."""
    import preparation_pdf_card_file_number as numbering

    sequence = numbering.preparation_file_sequence(file_number)
    count = max(0, int(card_count or 0))
    if not pdf_bytes or not sequence or count <= 0:
        return pdf_bytes

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        cards_per_page = reference.REFERENCE_CARDS_PER_PAGE
        for page_index, page in enumerate(document):
            first_card = page_index * cards_per_page
            remaining = count - first_card
            if remaining <= 0:
                break
            cards_on_page = min(cards_per_page, remaining)
            card_width, card_height = compact_card_dimensions(
                float(page.rect.width),
                float(page.rect.height),
            )

            placements: list[tuple[float, float, str]] = []
            for card_index in range(cards_on_page):
                logical_column = card_index % reference.REFERENCE_COLUMNS
                column = reference.REFERENCE_COLUMNS - 1 - logical_column
                row = card_index // reference.REFERENCE_COLUMNS
                center_x = (
                    MARGIN_X
                    + column * (card_width + COLUMN_GAP)
                    + card_width / 2
                )
                card_top = MARGIN_Y + row * (card_height + ROW_GAP)
                redact = fitz.Rect(
                    center_x - 38.0,
                    card_top + 0.2,
                    center_x + 38.0,
                    card_top + 12.4,
                )
                label = numbering.preparation_card_file_label(
                    first_card + card_index + 1,
                    file_number,
                )
                placements.append((center_x, card_top + 8.5, label))
                page.add_redact_annot(redact, fill=(1, 1, 1))

            page.apply_redactions()
            for center_x, baseline_y, label in placements:
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


def install_preparation_pdf_compact_operational_layout() -> None:
    """Install after wrapped text and card/file numbering overlays."""
    global _INSTALLED
    if _INSTALLED:
        return

    import reviewed_preparation_batches as batch
    import preparation_pdf_card_file_number as numbering

    batch.generate_preparation_pdf = generate_compact_operational_preparation_pdf
    # The existing numbering wrapper resolves this module-global function at
    # render time, so replacing it preserves its durable file-number flow.
    numbering.stamp_preparation_card_file_numbers = (
        stamp_compact_preparation_card_file_numbers
    )
    _INSTALLED = True


__all__ = [
    "COLUMN_GAP",
    "MEDIA_GAP",
    "MEDIA_TO_DETAILS_GAP",
    "ROW_GAP",
    "compact_card_dimensions",
    "compact_card_pdf_origin",
    "compact_detail_top",
    "compact_media_positions",
    "compact_reference_card_rows",
    "generate_compact_operational_preparation_pdf",
    "install_preparation_pdf_compact_operational_layout",
    "stamp_compact_preparation_card_file_numbers",
]
