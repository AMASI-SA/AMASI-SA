"""Reference-compatible preparation PDF layout and image loading.

The merchant's operational reference uses an A4 portrait page with three
right-to-left columns and five rows (15 preparation cards per page). Each card
has a serial number, product image next to a QR code, then product/spec fields,
order number, full date, quantity, and delivery information. Cards have no
large surrounding borders.

This module patches only reviewed preparation batches. The legacy Salla PDF
parser remains untouched.
"""
from __future__ import annotations

import base64
import io
import re
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx
import qrcode
from PIL import Image, ImageDraw, ImageOps
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from preparation_pdf import (
    ProductLine,
    _ar,
    _register_font,
    _wrap_arabic_lines,
)


REFERENCE_COLUMNS = 3
REFERENCE_ROWS = 5
REFERENCE_CARDS_PER_PAGE = REFERENCE_COLUMNS * REFERENCE_ROWS
REFERENCE_RED = HexColor("#D12B2B")
REFERENCE_TEXT = HexColor("#151515")
REFERENCE_MUTED = HexColor("#555555")
REFERENCE_QR_LOGO = "#7C173C"
_IMAGE_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.7",
    "Cache-Control": "no-cache",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _full_date(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return ""
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return raw[:24]


def reference_card_rows(line: ProductLine) -> list[tuple[str, str]]:
    """Return fields in the exact operational order printed below media."""
    rows: list[tuple[str, str]] = []
    if line.customer_name:
        rows.append(("الاسم", _text(line.customer_name)))
    if line.size:
        rows.append(("المقاس", _text(line.size)))
    if line.color:
        rows.append(("اللون", _text(line.color)))

    reserved = {
        "الاسم", "اسم", "المقاس", "مقاس", "اللون", "لون",
        "name", "size", "color", "colour",
    }
    for name, value in (line.product_options or {}).items():
        label = _text(name)
        field_value = _text(value)
        if not label or not field_value or label.casefold() in reserved:
            continue
        rows.append((label, field_value))
    if line.note:
        rows.append(("ملاحظة", _text(line.note)))

    rows.extend([
        ("ط", _text(line.order_number)),
        ("تاريخ", _full_date(line.order_date)),
        ("الكمية", str(max(1, int(line.quantity or 1)))),
    ])
    carrier = _text(line.shipping_company) or "—"
    delivery_count = max(1, int(line.total_products_in_order or 1))
    rows.append(("للتوصيل", f"{delivery_count} - {carrier}"))
    return rows


def _qr_with_center_mark(payload: str) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=5,
        border=1,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    side = image.size[0]
    mark_size = max(18, int(side * 0.19))
    left = (side - mark_size) // 2
    top = (side - mark_size) // 2
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (left, top, left + mark_size, top + mark_size),
        radius=max(2, mark_size // 10),
        fill=REFERENCE_QR_LOGO,
    )
    # A simple white brand mark keeps the QR reference appearance without
    # depending on a bundled external logo file.
    draw.text(
        (side // 2, side // 2),
        "A",
        fill="white",
        anchor="mm",
        stroke_width=0,
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _square_product_image(raw: bytes | None, size_px: int = 420) -> Image.Image | None:
    if not raw:
        return None
    try:
        image = Image.open(io.BytesIO(raw))
        if image.mode != "RGB":
            image = image.convert("RGB")
        return ImageOps.fit(
            image,
            (size_px, size_px),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    except Exception:
        return None


def _fit_visual_line(raw: str, font_name: str, font_size: float, max_width: float) -> str:
    visual = _ar(_text(raw))
    if pdfmetrics.stringWidth(visual, font_name, font_size) <= max_width:
        return visual
    logical = _text(raw)
    while logical:
        candidate = _ar(logical + "…")
        if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width:
            return candidate
        logical = logical[:-1]
    return "…"


def generate_reference_preparation_pdf(
    lines: list[ProductLine],
    *,
    serial_start: int = 1,
    title: str = "تجهيز المنتجات",
) -> bytes:
    """Generate the merchant-confirmed 3×5 A4 preparation layout."""
    if not lines:
        raise ValueError("No product lines to render")

    font_name, font_bold = _register_font()
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    page_width, page_height = A4

    margin_x = 7 * mm
    margin_y = 6 * mm
    card_width = (page_width - 2 * margin_x) / REFERENCE_COLUMNS
    card_height = (page_height - 2 * margin_y) / REFERENCE_ROWS
    inner = 3.2 * mm
    media_size = 24 * mm
    media_gap = 3.2 * mm

    def draw_label_value(
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
        clean_value = _text(value)
        if not clean_value:
            return
        label_visual = _ar(f"{label} :")
        label_font = font_bold
        pdf.setFont(label_font, font_size)
        label_width = pdfmetrics.stringWidth(label_visual, label_font, font_size)
        pdf.setFillColor(REFERENCE_RED if label_red else REFERENCE_TEXT)
        pdf.drawRightString(right, y, label_visual)

        value_right = right - label_width - 2.5
        value_width = max(10, width - label_width - 2.5)
        value_font = font_bold if bold_value else font_name
        value_visual = _fit_visual_line(clean_value, value_font, font_size, value_width)
        pdf.setFont(value_font, font_size)
        pdf.setFillColor(REFERENCE_TEXT)
        pdf.drawRightString(value_right, y, value_visual)

    def draw_card(card_index: int, line: ProductLine, serial: int) -> None:
        logical_column = card_index % REFERENCE_COLUMNS
        column = REFERENCE_COLUMNS - 1 - logical_column  # right-to-left
        row = card_index // REFERENCE_COLUMNS
        x = margin_x + column * card_width
        y = page_height - margin_y - (row + 1) * card_height
        left = x + inner
        right = x + card_width - inner
        usable_width = right - left

        # Serial number centered above the media, matching the reference file.
        pdf.setFillColor(REFERENCE_TEXT)
        pdf.setFont(font_name, 8.5)
        pdf.drawCentredString(x + card_width / 2, y + card_height - 8.5, str(serial))

        media_top = y + card_height - 13
        media_y = media_top - media_size
        qr_x = right - media_size
        image_x = left

        qr_bytes = _qr_with_center_mark(line.order_number)
        pdf.drawImage(
            ImageReader(io.BytesIO(qr_bytes)),
            qr_x,
            media_y,
            width=media_size,
            height=media_size,
            mask="auto",
        )

        product_image = _square_product_image(line.image_bytes)
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
        # Intentionally no grey placeholder. A missing image must look missing,
        # not like a successfully loaded grey product.

        text_top = media_y - 4
        cursor = text_top
        body_size = 6.8
        line_height = 8.25

        # Product name is required by the merchant and appears first below media.
        if line.product_name:
            product_lines = _wrap_arabic_lines(
                _text(line.product_name),
                font_bold,
                7.2,
                usable_width,
                max_lines=2,
            )
            pdf.setFont(font_bold, 7.2)
            pdf.setFillColor(REFERENCE_TEXT)
            for product_line in product_lines:
                pdf.drawCentredString(x + card_width / 2, cursor, product_line)
                cursor -= 8.4
            cursor -= 0.8

        fields = reference_card_rows(line)
        # Keep the operational tail visible even with many specifications.
        fixed_tail_count = 4
        dynamic_rows = fields[:-fixed_tail_count]
        tail_rows = fields[-fixed_tail_count:]
        available_dynamic = max(0, int((cursor - (y + inner) - fixed_tail_count * line_height) // line_height))
        if len(dynamic_rows) > available_dynamic:
            dynamic_rows = dynamic_rows[:available_dynamic]
        rows_to_draw = [*dynamic_rows, *tail_rows]

        for label, value in rows_to_draw:
            if cursor < y + inner + 2:
                break
            is_delivery = label == "للتوصيل"
            draw_label_value(
                label,
                value,
                right=right,
                y=cursor,
                width=usable_width,
                font_size=7.0 if is_delivery else body_size,
                label_red=not is_delivery,
                bold_value=is_delivery,
            )
            cursor -= line_height

    for page_start in range(0, len(lines), REFERENCE_CARDS_PER_PAGE):
        page_lines = lines[page_start:page_start + REFERENCE_CARDS_PER_PAGE]
        for index, line in enumerate(page_lines):
            draw_card(index, line, serial_start + page_start + index)
        pdf.showPage()

    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()


def image_candidate_urls(state: dict[str, Any], identity: Any, source_line: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    values: Iterable[Any] = (
        state.get("selected_image_url"),
        getattr(identity, "image_url", None),
        *((getattr(identity, "image_urls", None) or [])),
        source_line.get("image_url"),
        source_line.get("selected_image_url"),
    )
    for value in values:
        url = _text(value)
        if url.startswith("//"):
            url = f"https:{url}"
        if url and url not in candidates:
            candidates.append(url)
    return candidates


async def _download_first_product_image(
    client: httpx.AsyncClient,
    candidates: list[str],
    cache: dict[str, tuple[bytes | None, str | None]],
    batch_module: Any,
) -> tuple[bytes | None, str | None, str | None]:
    for candidate in candidates:
        if candidate.startswith("data:image/") and "," in candidate:
            try:
                header, encoded = candidate.split(",", 1)
                raw = base64.b64decode(encoded)
                compressed = batch_module._compress_image_bytes(raw)
                if compressed[0]:
                    return compressed[0], compressed[1], candidate
            except Exception:
                continue

        # Mezan image URLs are authenticated relative endpoints. They cannot be
        # fetched by an unauthenticated external HTTP client here, so continue
        # to the original Salla image candidates instead of stopping on them.
        if candidate.startswith("/"):
            continue
        safe_url = batch_module._safe_image_url(candidate)
        if not safe_url:
            continue
        if safe_url in cache:
            raw, mime = cache[safe_url]
            if raw:
                return raw, mime, safe_url
            continue

        parsed = urlparse(safe_url)
        headers = dict(_IMAGE_REQUEST_HEADERS)
        if parsed.scheme and parsed.netloc:
            headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
        result: tuple[bytes | None, str | None] = (None, None)
        try:
            response = await client.get(safe_url, headers=headers)
            final_host = response.url.host or ""
            if (
                response.status_code == 200
                and not batch_module._is_private_literal_host(final_host)
                and len(response.content) <= batch_module.MAX_IMAGE_BYTES
            ):
                result = batch_module._compress_image_bytes(response.content)
        except Exception:
            result = (None, None)
        cache[safe_url] = result
        if result[0]:
            return result[0], result[1], safe_url
    return None, None, None


async def build_reference_batch_lines(
    context: dict[str, Any],
    planned: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build batch snapshots while falling back across every product image."""
    import reviewed_preparation_batches as batch

    orders_by_number = {
        batch._text(order.order_number): order
        for order, _workflow in context.get("pairs") or []
    }
    workflows_by_number = {
        batch._text(workflow.get("order_number")): workflow
        for _order, workflow in context.get("pairs") or []
    }
    identities_by_order = {
        order_number: {
            batch._text(identity.order_item_id): identity
            for identity in batch.map_order_item_identities(order)
        }
        for order_number, order in orders_by_number.items()
    }

    result: list[dict[str, Any]] = []
    image_cache: dict[str, tuple[bytes | None, str | None]] = {}
    timeout = httpx.Timeout(16.0, connect=6.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=_IMAGE_REQUEST_HEADERS,
    ) as client:
        for index, allocation in enumerate(planned, start=1):
            order_number = batch._text(allocation.get("order_number"))
            order_item_id = batch._text(allocation.get("order_item_id"))
            order = orders_by_number.get(order_number)
            workflow = workflows_by_number.get(order_number) or {}
            identity = (identities_by_order.get(order_number) or {}).get(order_item_id)
            if order is None or identity is None:
                raise batch.HTTPException(
                    status_code=409,
                    detail={
                        "code": "reviewed_line_changed_reload_required",
                        "order_number": order_number,
                    },
                )
            states = {
                batch._text(row.get("order_item_id")): dict(row)
                for row in workflow.get("items") or []
                if isinstance(row, dict) and batch._text(row.get("order_item_id"))
            }
            state = states.get(order_item_id, {})
            if state.get("supplier_export") is False:
                raise batch.HTTPException(
                    status_code=409,
                    detail={
                        "code": "reviewed_line_no_longer_exportable",
                        "order_number": order_number,
                    },
                )

            spec_fields = batch.supplier_file_spec_fields(identity, state)
            card_fields = batch._card_field_projection(
                spec_fields,
                state.get("preparation_note"),
            )
            source_line = allocation.get("line") or {}
            candidates = image_candidate_urls(state, identity, source_line)
            image_bytes, image_mime, resolved_image_url = await _download_first_product_image(
                client,
                candidates,
                image_cache,
                batch,
            )
            total_products = sum(
                batch._unit_quantity(getattr(item, "quantity", 0))
                for item in getattr(order, "items", None) or []
            ) or 1
            result.append({
                "line_number": index,
                "group_key": batch._text(allocation.get("group_key")),
                "order_number": order_number,
                "order_item_id": order_item_id,
                "unit_indices": list(allocation.get("unit_indices") or []),
                "quantity": int(allocation.get("quantity") or 0),
                "product_name": (
                    batch._text(getattr(identity, "name", None))
                    or batch._text(allocation.get("product_name"))
                ),
                "product_id": batch._text(getattr(identity, "product_id", None)) or None,
                "sku": batch._text(getattr(identity, "sku", None)) or None,
                "line_index": int(getattr(identity, "line_index", 0) or 0),
                "order_date": str(getattr(order, "created_at", "") or ""),
                "shipping_company": (
                    batch._text(getattr(getattr(order, "shipping", None), "company", None))
                    or None
                ),
                "total_products_in_order": total_products,
                "selected_image_url": batch._text(state.get("selected_image_url")) or None,
                "resolved_image_url": resolved_image_url,
                "image_b64": (
                    base64.b64encode(image_bytes).decode("ascii")
                    if image_bytes else None
                ),
                "image_mime": image_mime,
                "image_missing": image_bytes is None,
                "image_candidates": candidates,
                "customer_name": card_fields["customer_name"],
                "size": card_fields["size"],
                "color": card_fields["color"],
                "note": card_fields["note"],
                "product_options": card_fields["product_options"],
                "file_spec_fields": spec_fields,
                "preparation_note": batch._text(state.get("preparation_note")) or None,
            })
    return result


_INSTALLED = False


def install_preparation_pdf_reference_layout() -> None:
    global _INSTALLED
    import reviewed_preparation_batches as batch

    batch.generate_preparation_pdf = generate_reference_preparation_pdf
    batch._build_batch_lines = build_reference_batch_lines
    _INSTALLED = True


__all__ = [
    "REFERENCE_CARDS_PER_PAGE",
    "REFERENCE_COLUMNS",
    "REFERENCE_ROWS",
    "build_reference_batch_lines",
    "generate_reference_preparation_pdf",
    "image_candidate_urls",
    "install_preparation_pdf_reference_layout",
    "reference_card_rows",
]
