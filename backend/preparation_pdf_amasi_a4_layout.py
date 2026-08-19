"""Merchant-approved Amasi A4 product file layout.

Exact operational contract:
- A4 portrait.
- 3 columns x 5 rows = 15 cards/page.
- product image and QR are equal square sizes.
- customer-selected specifications sit below the QR.
- order/date/quantity/shipping summary sit below the product image.
- a vertical divider separates both detail columns.
- supplier and responsible employee are shown together in the page header.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

import qrcode
from PIL import Image, ImageDraw, ImageOps
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from preparation_pdf import ProductLine, _ar, _register_font
import preparation_pdf_card_file_number as numbering
import preparation_pdf_reference_layout as reference

AMASI_MAROON = HexColor("#74102F")
AMASI_GOLD = HexColor("#CDA14A")
TEXT = HexColor("#111111")
MUTED = HexColor("#666666")
BORDER = HexColor("#D8BCC7")
RED = HexColor("#D12B2B")
COLUMNS = 3
ROWS = 5
CARDS_PER_PAGE = 15
PAGE_MARGIN_X = 5.5 * mm
PAGE_MARGIN_BOTTOM = 5.5 * mm
HEADER_HEIGHT = 27 * mm
COLUMN_GAP = 3.2 * mm
ROW_GAP = 2.7 * mm
CARD_INNER = 2.4 * mm
MEDIA_SIZE = 20.5 * mm
MEDIA_GAP = 1.7 * mm
DETAIL_GAP = 2.0 * mm
_INSTALLED = False


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _date(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return "—"
    return raw[:10].replace("-", "/")


def _asset_logo_bytes() -> bytes | None:
    path = Path(__file__).with_name("assets") / "amasi_qr_logo.b64"
    try:
        return base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
    except Exception:
        return None


def _logo_image(size: int = 128) -> Image.Image | None:
    raw = _asset_logo_bytes()
    if not raw:
        return None
    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        image = ImageOps.fit(image, (size, size), method=Image.Resampling.LANCZOS)
        return image
    except Exception:
        return None


def _qr_with_amasi_logo(payload: str) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=5,
        border=1,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    logo = _logo_image()
    if logo is not None:
        side = image.size[0]
        logo_side = max(18, int(side * 0.20))
        logo = ImageOps.fit(logo, (logo_side, logo_side), method=Image.Resampling.LANCZOS)
        pad = max(2, logo_side // 14)
        plate = Image.new("RGB", (logo_side + pad * 2, logo_side + pad * 2), "white")
        plate.paste(logo, (pad, pad))
        left = (side - plate.size[0]) // 2
        top = (side - plate.size[1]) // 2
        image.paste(plate, (left, top))
    else:
        side = image.size[0]
        mark = max(18, int(side * 0.18))
        draw = ImageDraw.Draw(image)
        left = (side - mark) // 2
        top = (side - mark) // 2
        draw.rounded_rectangle((left, top, left + mark, top + mark), radius=3, fill="#74102F")
        draw.text((side // 2, side // 2), "A", fill="white", anchor="mm")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _card_dimensions(page_width: float, page_height: float) -> tuple[float, float]:
    usable_height = page_height - HEADER_HEIGHT - PAGE_MARGIN_BOTTOM
    card_width = (page_width - 2 * PAGE_MARGIN_X - (COLUMNS - 1) * COLUMN_GAP) / COLUMNS
    card_height = (usable_height - (ROWS - 1) * ROW_GAP) / ROWS
    return card_width, card_height


def _card_origin(index: int, page_width: float, page_height: float) -> tuple[float, float, float, float]:
    card_width, card_height = _card_dimensions(page_width, page_height)
    logical_column = index % COLUMNS
    column = COLUMNS - 1 - logical_column
    row = index // COLUMNS
    x = PAGE_MARGIN_X + column * (card_width + COLUMN_GAP)
    top = page_height - HEADER_HEIGHT - row * (card_height + ROW_GAP)
    y = top - card_height
    return x, y, card_width, card_height


def _fit(raw: str, font_name: str, font_size: float, max_width: float) -> str:
    return reference._fit_visual_line(_text(raw), font_name, font_size, max_width)


def _spec_rows(line: ProductLine) -> list[tuple[str, str]]:
    specs, _orders = reference.split_reference_card_rows(line)
    return specs


def _order_rows(line: ProductLine) -> list[tuple[str, str]]:
    carrier = _text(line.shipping_company) or "—"
    pieces = max(1, int(line.total_products_in_order or 1))
    return [
        ("ط", _text(line.order_number) or "—"),
        ("تاريخ", _date(line.order_date)),
        ("الكمية", str(max(1, int(line.quantity or 1)))),
        ("", f"{carrier} - {pieces}"),
    ]


def generate_amasi_product_file_pdf(
    lines: list[ProductLine],
    *,
    serial_start: int = 1,
    title: str = "ملف المنتجات",
    supplier_name: str = "",
    responsible_employee_name: str = "",
    file_number: str = "",
    file_date: str = "",
) -> bytes:
    if not lines:
        raise ValueError("No product lines to render")
    font_name, font_bold = _register_font()
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=A4)
    page_width, page_height = A4

    def draw_header(page_lines: list[ProductLine]) -> None:
        pdf.setFillColor(AMASI_MAROON)
        pdf.setFont(font_bold, 20)
        pdf.drawCentredString(page_width / 2, page_height - 13 * mm, _ar("ملف المنتجات"))
        sub_parts = []
        if _text(supplier_name):
            sub_parts.append(f"المورد: {_text(supplier_name)}")
        if _text(responsible_employee_name):
            sub_parts.append(f"الموظف المسؤول: {_text(responsible_employee_name)}")
        if sub_parts:
            pdf.setFillColor(TEXT)
            pdf.setFont(font_bold, 9)
            pdf.drawCentredString(page_width / 2, page_height - 19 * mm, _ar("   |   ".join(sub_parts)))
        pdf.setStrokeColor(AMASI_GOLD)
        pdf.setLineWidth(0.8)
        pdf.line(59 * mm, page_height - 23 * mm, 151 * mm, page_height - 23 * mm)

        right = page_width - PAGE_MARGIN_X
        pdf.setFont(font_bold, 7.3)
        pdf.setFillColor(AMASI_MAROON)
        if _text(file_number):
            pdf.drawRightString(right, page_height - 8.5 * mm, _ar(f"رقم الملف: {_text(file_number)}"))
        effective_date = _text(file_date) or _date(page_lines[0].order_date if page_lines else "")
        pdf.drawRightString(right, page_height - 12.5 * mm, _ar(f"تاريخ الرفع: {effective_date}"))

        left = PAGE_MARGIN_X
        pdf.setFillColor(TEXT)
        pdf.setFont(font_name, 7.3)
        pdf.drawString(left, page_height - 8.5 * mm, f"{len(page_lines)} / {CARDS_PER_PAGE}")

    def label_value(label: str, value: str, right: float, y: float, width: float, size: float = 5.7) -> None:
        clean = _text(value)
        if not clean:
            return
        if not label:
            pdf.setFillColor(TEXT)
            pdf.setFont(font_bold, size)
            pdf.drawRightString(right, y, _fit(clean, font_bold, size, width))
            return
        label_visual = _ar(f"{label} :")
        pdf.setFont(font_bold, size)
        label_width = pdfmetrics.stringWidth(label_visual, font_bold, size)
        pdf.setFillColor(RED)
        pdf.drawRightString(right, y, label_visual)
        value_right = right - label_width - 1.8
        pdf.setFillColor(TEXT)
        pdf.setFont(font_name, size)
        pdf.drawRightString(value_right, y, _fit(clean, font_name, size, max(8, width - label_width - 2)))

    def draw_card(index: int, line: ProductLine, serial: int) -> None:
        x, y, card_width, card_height = _card_origin(index, page_width, page_height)
        pdf.setStrokeColor(BORDER)
        pdf.setLineWidth(0.45)
        pdf.roundRect(x, y, card_width, card_height, 2.8 * mm, stroke=1, fill=0)

        label = numbering.preparation_card_file_label(serial, file_number)
        pdf.setFillColor(AMASI_MAROON)
        pill_w = max(13 * mm, pdfmetrics.stringWidth(label, font_bold, 7.4) + 5 * mm)
        pill_h = 5.0 * mm
        pill_x = x + (card_width - pill_w) / 2
        pill_y = y + card_height - pill_h / 2
        pdf.roundRect(pill_x, pill_y, pill_w, pill_h, 2.2 * mm, stroke=0, fill=1)
        pdf.setFillColor(HexColor("#FFFFFF"))
        pdf.setFont(font_bold, 7.4)
        pdf.drawCentredString(x + card_width / 2, pill_y + 1.45 * mm, label)

        inner_left = x + CARD_INNER
        inner_right = x + card_width - CARD_INNER
        pair_width = MEDIA_SIZE * 2 + MEDIA_GAP
        pair_left = x + (card_width - pair_width) / 2
        image_x = pair_left
        qr_x = pair_left + MEDIA_SIZE + MEDIA_GAP
        media_top = y + card_height - 7.4 * mm
        media_y = media_top - MEDIA_SIZE

        product_image = reference._square_product_image(line.image_bytes)
        if product_image is not None:
            image_buffer = io.BytesIO()
            product_image.save(image_buffer, format="JPEG", quality=88, optimize=True)
            image_buffer.seek(0)
            pdf.drawImage(ImageReader(image_buffer), image_x, media_y, MEDIA_SIZE, MEDIA_SIZE, mask="auto")
        qr = _qr_with_amasi_logo(line.barcode_payload or line.order_number)
        pdf.drawImage(ImageReader(io.BytesIO(qr)), qr_x, media_y, MEDIA_SIZE, MEDIA_SIZE, mask="auto")

        detail_top = media_y - 2.8 * mm
        detail_bottom = y + 2.0 * mm
        half = (inner_right - inner_left - DETAIL_GAP) / 2
        left_half_right = inner_left + half
        right_half_left = inner_left + half + DETAIL_GAP
        right_half_right = inner_right
        divider_x = inner_left + half + DETAIL_GAP / 2
        pdf.setStrokeColor(BORDER)
        pdf.setLineWidth(0.45)
        pdf.line(divider_x, detail_bottom, divider_x, detail_top + 1.2 * mm)

        order_y = detail_top
        for label_name, value in _order_rows(line):
            if order_y < detail_bottom:
                break
            label_value(label_name, value, left_half_right, order_y, half, 5.6 if label_name else 5.8)
            order_y -= 6.7

        spec_y = detail_top
        for label_name, value in _spec_rows(line):
            if spec_y < detail_bottom:
                break
            label_value(label_name, value, right_half_right, spec_y, half, 5.45)
            spec_y -= 6.5

    for page_start in range(0, len(lines), CARDS_PER_PAGE):
        page_lines = lines[page_start:page_start + CARDS_PER_PAGE]
        draw_header(page_lines)
        for index, line in enumerate(page_lines):
            draw_card(index, line, serial_start + page_start + index)
        pdf.showPage()
    pdf.save()
    out.seek(0)
    return out.getvalue()


def render_amasi_batch_pdf(batch_row: dict[str, Any]) -> bytes:
    import reviewed_preparation_batches as batch
    lines = [
        batch._line_from_batch_storage(row, batch_row)
        for row in (batch_row.get("lines") or [])
        if isinstance(row, dict)
    ]
    return generate_amasi_product_file_pdf(
        lines,
        serial_start=1,
        title="ملف المنتجات",
        supplier_name=_text(batch_row.get("supplier_name")),
        responsible_employee_name=(
            _text(batch_row.get("responsible_employee_name"))
            or _text(batch_row.get("created_by_name"))
        ),
        file_number=_text(batch_row.get("file_number")),
        file_date=_text(batch_row.get("file_date_display") or batch_row.get("file_date")),
    )


def install_preparation_pdf_amasi_a4_layout() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    import reviewed_preparation_batches as batch
    batch.generate_preparation_pdf = generate_amasi_product_file_pdf
    batch.render_preparation_batch_pdf = render_amasi_batch_pdf
    _INSTALLED = True


__all__ = [
    "CARDS_PER_PAGE",
    "generate_amasi_product_file_pdf",
    "install_preparation_pdf_amasi_a4_layout",
    "render_amasi_batch_pdf",
]
