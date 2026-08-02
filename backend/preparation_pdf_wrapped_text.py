"""Lossless text wrapping for the approved 3x5 preparation PDF layout.

The reference renderer originally treated every specification and note as a
single physical line. Long values were shortened with an ellipsis, which hid
operational instructions. This overlay preserves the approved page geometry,
images, QR codes, and field partition while rendering specification values over
as many lines as the card permits. The font shrinks adaptively before any text
can be lost.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from preparation_pdf import ProductLine, _ar, _register_font
import preparation_pdf_reference_layout as reference


MIN_BODY_FONT_SIZE = 3.8
MAX_BODY_FONT_SIZE = 6.6
BODY_FONT_STEP = 0.3
REFERENCE_TEXT = HexColor("#151515")
REFERENCE_RED = HexColor("#D12B2B")


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _visual_width(value: str, font_name: str, font_size: float) -> float:
    return pdfmetrics.stringWidth(_ar(value), font_name, font_size)


def _split_long_token(
    token: str,
    *,
    font_name: str,
    font_size: float,
    max_width: float,
) -> tuple[str, str]:
    """Split one unbroken token without dropping characters."""
    if not token:
        return "", ""
    last_fit = 0
    for index in range(1, len(token) + 1):
        if _visual_width(token[:index], font_name, font_size) <= max_width:
            last_fit = index
        else:
            break
    if last_fit <= 0:
        last_fit = 1
    return token[:last_fit], token[last_fit:]


def wrap_reference_text(
    value: Any,
    *,
    font_name: str,
    font_size: float,
    first_width: float,
    continuation_width: float | None = None,
) -> list[str]:
    """Wrap logical text completely, without ellipsis or discarded words."""
    clean = _text(value)
    if not clean:
        return []
    continuation_width = continuation_width or first_width
    words = clean.split(" ")
    lines: list[str] = []
    pending = list(words)

    while pending:
        max_width = first_width if not lines else continuation_width
        current: list[str] = []
        while pending:
            candidate = " ".join([*current, pending[0]])
            if _visual_width(candidate, font_name, font_size) <= max_width:
                current.append(pending.pop(0))
                continue
            if current:
                break
            head, tail = _split_long_token(
                pending.pop(0),
                font_name=font_name,
                font_size=font_size,
                max_width=max_width,
            )
            current.append(head)
            if tail:
                pending.insert(0, tail)
            break
        lines.append(" ".join(current))
    return lines


@dataclass(frozen=True)
class WrappedField:
    label: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class WrappedSpecificationPlan:
    font_size: float
    line_height: float
    fields: tuple[WrappedField, ...]

    @property
    def physical_line_count(self) -> int:
        return sum(len(field.lines) for field in self.fields)


def build_wrapped_specification_plan(
    rows: list[tuple[str, str]],
    *,
    font_name: str,
    font_bold: str,
    width: float,
    available_height: float,
) -> WrappedSpecificationPlan:
    """Choose the largest font that preserves every specification character."""
    sizes: list[float] = []
    size = MAX_BODY_FONT_SIZE
    while size >= MIN_BODY_FONT_SIZE - 0.001:
        sizes.append(round(size, 2))
        size -= BODY_FONT_STEP

    chosen: WrappedSpecificationPlan | None = None
    for font_size in sizes:
        fields: list[WrappedField] = []
        for label, value in rows:
            label_visual = _ar(f"{label} :")
            label_width = pdfmetrics.stringWidth(label_visual, font_bold, font_size)
            first_width = max(8.0, width - label_width - 2.2)
            wrapped = wrap_reference_text(
                value,
                font_name=font_name,
                font_size=font_size,
                first_width=first_width,
                continuation_width=width,
            )
            fields.append(WrappedField(label=label, lines=tuple(wrapped or [""])))
        line_height = font_size + 1.35
        plan = WrappedSpecificationPlan(
            font_size=font_size,
            line_height=line_height,
            fields=tuple(fields),
        )
        chosen = plan
        if plan.physical_line_count * line_height <= available_height:
            return plan

    assert chosen is not None
    # Extremely long operational text is still preserved. Use the exact
    # available line height rather than discarding the final words.
    count = max(1, chosen.physical_line_count)
    fitted_line_height = max(3.15, available_height / count)
    fitted_font = max(2.8, fitted_line_height - 0.75)
    fields = []
    for label, value in rows:
        label_width = pdfmetrics.stringWidth(_ar(f"{label} :"), font_bold, fitted_font)
        wrapped = wrap_reference_text(
            value,
            font_name=font_name,
            font_size=fitted_font,
            first_width=max(6.0, width - label_width - 1.6),
            continuation_width=width,
        )
        fields.append(WrappedField(label=label, lines=tuple(wrapped or [""])))
    return WrappedSpecificationPlan(
        font_size=fitted_font,
        line_height=fitted_line_height,
        fields=tuple(fields),
    )


def _wrap_product_name(
    value: Any,
    *,
    font_name: str,
    max_width: float,
) -> tuple[float, list[str]]:
    clean = _text(value)
    if not clean:
        return 7.0, []
    for size in (7.0, 6.6, 6.2, 5.8, 5.4):
        lines = wrap_reference_text(
            clean,
            font_name=font_name,
            font_size=size,
            first_width=max_width,
            continuation_width=max_width,
        )
        if len(lines) <= 3:
            return size, lines
    return 5.0, wrap_reference_text(
        clean,
        font_name=font_name,
        font_size=5.0,
        first_width=max_width,
        continuation_width=max_width,
    )


def generate_wrapped_reference_preparation_pdf(
    lines: list[ProductLine],
    *,
    serial_start: int = 1,
    title: str = "تجهيز المنتجات",
) -> bytes:
    """Render the approved 3x5 layout while preserving long text in full."""
    if not lines:
        raise ValueError("No product lines to render")

    font_name, font_bold = _register_font()
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    page_width, page_height = A4

    margin_x = 7 * mm
    margin_y = 6 * mm
    card_width = (page_width - 2 * margin_x) / reference.REFERENCE_COLUMNS
    card_height = (page_height - 2 * margin_y) / reference.REFERENCE_ROWS
    inner = 3.2 * mm
    media_size = 24 * mm
    detail_gap = 2.4 * mm

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
        label_visual = _ar(f"{label} :")
        pdf.setFont(font_bold, font_size)
        label_width = pdfmetrics.stringWidth(label_visual, font_bold, font_size)
        pdf.setFillColor(REFERENCE_RED if label_red else REFERENCE_TEXT)
        pdf.drawRightString(right, y, label_visual)
        value_right = right - label_width - 2.2
        value_width = max(8, width - label_width - 2.2)
        value_font = font_bold if bold_value else font_name
        value_visual = reference._fit_visual_line(clean, value_font, font_size, value_width)
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
        logical_column = card_index % reference.REFERENCE_COLUMNS
        column = reference.REFERENCE_COLUMNS - 1 - logical_column
        row = card_index // reference.REFERENCE_COLUMNS
        x = margin_x + column * card_width
        y = page_height - margin_y - (row + 1) * card_height
        left = x + inner
        right = x + card_width - inner
        usable_width = right - left

        pdf.setFillColor(REFERENCE_TEXT)
        pdf.setFont(font_name, 8.5)
        pdf.drawCentredString(x + card_width / 2, y + card_height - 8.5, str(serial))

        media_top = y + card_height - 13
        media_y = media_top - media_size
        qr_x = right - media_size
        image_x = left
        qr_bytes = reference._qr_with_center_mark(line.order_number)
        pdf.drawImage(
            ImageReader(io.BytesIO(qr_bytes)),
            qr_x,
            media_y,
            width=media_size,
            height=media_size,
            mask="auto",
        )

        product_image = reference._square_product_image(line.image_bytes)
        if product_image is not None:
            image_buffer = io.BytesIO()
            product_image.save(image_buffer, format="JPEG", quality=88, optimize=True)
            image_buffer.seek(0)
            pdf.drawImage(
                ImageReader(image_buffer),
                image_x,
                media_y,
                width=media_size,
                height=media_size,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )

        product_cursor = media_y - 4
        product_size, product_lines = _wrap_product_name(
            line.product_name,
            font_name=font_bold,
            max_width=usable_width,
        )
        if product_lines:
            pdf.setFont(font_bold, product_size)
            pdf.setFillColor(REFERENCE_TEXT)
            product_line_height = product_size + 1.0
            for product_line in product_lines:
                pdf.drawCentredString(
                    x + card_width / 2,
                    product_cursor,
                    _ar(product_line),
                )
                product_cursor -= product_line_height
            product_cursor -= 0.5

        half_width = (usable_width - detail_gap) / 2
        left_half_right = left + half_width
        right_half_right = right
        detail_top = product_cursor
        detail_bottom = y + inner
        specification_rows, order_rows = reference.split_reference_card_rows(line)

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
            is_delivery = label == "للتوصيل"
            draw_single_line_field(
                label,
                value,
                right=left_half_right,
                y=order_cursor,
                width=half_width,
                font_size=6.8 if is_delivery else 6.6,
                label_red=not is_delivery,
                bold_value=is_delivery,
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


_INSTALLED = False


def install_preparation_pdf_wrapped_text() -> None:
    """Install after the reference layout so all future PDFs use wrapping."""
    global _INSTALLED
    if _INSTALLED:
        return
    import reviewed_preparation_batches as batch

    batch.generate_preparation_pdf = generate_wrapped_reference_preparation_pdf
    _INSTALLED = True


__all__ = [
    "WrappedField",
    "WrappedSpecificationPlan",
    "build_wrapped_specification_plan",
    "generate_wrapped_reference_preparation_pdf",
    "install_preparation_pdf_wrapped_text",
    "wrap_reference_text",
]
