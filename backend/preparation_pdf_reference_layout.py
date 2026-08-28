"""Reference-compatible preparation PDF layout and image loading.

The merchant's operational reference uses an A4 portrait page with three
right-to-left columns and five rows (15 preparation cards per page). Each card
has a serial number, product image next to a QR code, then a split detail area:
product specifications below the QR and order/date/quantity/delivery below the
image. Cards have no surrounding borders.

This module patches only reviewed preparation batches. The legacy Salla PDF
parser remains untouched.
"""
from __future__ import annotations

import base64
import io
import re
from types import SimpleNamespace
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
REFERENCE_QR_LOGO = "#7C173C"
MEZAN_IMAGE_COLLECTION = "order_review_mezan_images"
MEZAN_IMAGE_PREFIX = "/api/order-reviews-v1/mezan-images/"
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


def split_reference_card_rows(
    line: ProductLine,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Partition rows exactly like the reference: specs right, order data left."""
    rows = reference_card_rows(line)
    return rows[:-4], rows[-4:]


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
    detail_gap = 2.4 * mm

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

        value_right = right - label_width - 2.2
        value_width = max(8, width - label_width - 2.2)
        value_font = font_bold if bold_value else font_name
        value_visual = _fit_visual_line(clean_value, value_font, font_size, value_width)
        pdf.setFont(value_font, font_size)
        pdf.setFillColor(REFERENCE_TEXT)
        pdf.drawRightString(value_right, y, value_visual)

    def draw_card(card_index: int, line: ProductLine, serial: int) -> None:
        logical_column = card_index % REFERENCE_COLUMNS
        column = REFERENCE_COLUMNS - 1 - logical_column
        row = card_index // REFERENCE_COLUMNS
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

        qr_bytes = _qr_with_center_mark(line.barcode_payload or line.order_number)
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

        product_cursor = media_y - 4
        if line.product_name:
            product_lines = _wrap_arabic_lines(
                _text(line.product_name),
                font_bold,
                7.0,
                usable_width,
                max_lines=2,
            )
            pdf.setFont(font_bold, 7.0)
            pdf.setFillColor(REFERENCE_TEXT)
            for product_line in product_lines:
                pdf.drawCentredString(x + card_width / 2, product_cursor, product_line)
                product_cursor -= 8.0
            product_cursor -= 0.5

        half_width = (usable_width - detail_gap) / 2
        left_half_right = left + half_width
        right_half_right = right
        detail_top = product_cursor
        line_height = 8.15
        body_size = 6.6

        specification_rows, order_rows = split_reference_card_rows(line)
        max_rows = max(1, int((detail_top - (y + inner)) // line_height))
        specification_rows = specification_rows[:max_rows]
        order_rows = order_rows[:max_rows]

        spec_cursor = detail_top
        for label, value in specification_rows:
            if spec_cursor < y + inner:
                break
            draw_label_value(
                label,
                value,
                right=right_half_right,
                y=spec_cursor,
                width=half_width,
                font_size=body_size,
            )
            spec_cursor -= line_height

        order_cursor = detail_top
        for label, value in order_rows:
            if order_cursor < y + inner:
                break
            is_delivery = label == "للتوصيل"
            draw_label_value(
                label,
                value,
                right=left_half_right,
                y=order_cursor,
                width=half_width,
                font_size=6.8 if is_delivery else body_size,
                label_red=not is_delivery,
                bold_value=is_delivery,
            )
            order_cursor -= line_height

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


async def _mezan_image_from_context(
    context: dict[str, Any],
    candidate: str,
    batch_module: Any,
) -> tuple[bytes | None, str | None]:
    if not candidate.startswith(MEZAN_IMAGE_PREFIX):
        return None, None
    database = context.get("_database")
    user_id = _text(context.get("_user_id"))
    image_id = candidate.removeprefix(MEZAN_IMAGE_PREFIX).split("/", 1)[0]
    if database is None or not user_id or not image_id:
        return None, None
    try:
        row = await database[MEZAN_IMAGE_COLLECTION].find_one(
            {
                "user_id": user_id,
                "id": image_id,
                "deleted_at": {"$exists": False},
            },
            {"_id": 0, "data_base64": 1, "content_type": 1},
        )
        if not row or not row.get("data_base64"):
            return None, None
        raw = base64.b64decode(row["data_base64"], validate=True)
        return batch_module._compress_image_bytes(raw)
    except Exception:
        return None, None


async def _download_first_product_image(
    client: httpx.AsyncClient,
    candidates: list[str],
    cache: dict[str, tuple[bytes | None, str | None]],
    batch_module: Any,
    context: dict[str, Any],
) -> tuple[bytes | None, str | None, str | None]:
    for candidate in candidates:
        if candidate.startswith(MEZAN_IMAGE_PREFIX):
            raw, mime = await _mezan_image_from_context(context, candidate, batch_module)
            if raw:
                return raw, mime, candidate
            continue
        if candidate.startswith("data:image/") and "," in candidate:
            try:
                _header, encoded = candidate.split(",", 1)
                raw = base64.b64decode(encoded)
                compressed = batch_module._compress_image_bytes(raw)
                if compressed[0]:
                    return compressed[0], compressed[1], candidate
            except Exception:
                continue
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
    """Enrich canonical batch snapshots with reference-layout image fallbacks.

    Identity, customer options and stale-data validation belong exclusively to
    the canonical reviewed-preparation builder. The reference layout only
    improves image resolution on those already validated immutable rows.
    """
    import reviewed_preparation_batches as batch
    builder = _ORIGINAL_BATCH_LINE_BUILDER or batch._build_batch_lines
    if builder is build_reference_batch_lines:
        raise RuntimeError("canonical_preparation_batch_builder_unavailable")
    result = await builder(context, planned)

    workflows_by_number = {
        batch._text(workflow.get("order_number")): workflow
        for _order, workflow in context.get("pairs") or []
    }
    live_identities: dict[tuple[str, str], Any] = {}
    for order, _workflow in context.get("pairs") or []:
        order_number = batch._text(getattr(order, "order_number", None))
        try:
            for identity in batch.map_order_item_identities(order):
                live_identities[(
                    order_number,
                    batch._text(getattr(identity, "order_item_id", None)),
                )] = identity
        except Exception:
            # Live identity is optional here and is used only to discover
            # secondary image URLs. Canonical frozen identity already won.
            continue
    row_allocations = [
        (row, planned[index] if index < len(planned) else {})
        for index, row in enumerate(result)
    ]
    image_cache: dict[str, tuple[bytes | None, str | None]] = {}
    # Keep the optional fallback bounded. The canonical builder already tried
    # the primary image; this layer must never push file creation toward the
    # proxy timeout merely because several secondary images are unavailable.
    timeout = httpx.Timeout(3.0, connect=2.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=_IMAGE_REQUEST_HEADERS,
    ) as client:
        async def hydrate(
            pair: tuple[dict[str, Any], dict[str, Any]],
        ) -> tuple[list[str], bytes | None, str | None, str | None]:
            row, allocation = pair
            order_number = batch._text(row.get("order_number"))
            workflow = workflows_by_number.get(order_number) or {}
            states = {
                batch._text(state_row.get("order_item_id")): dict(state_row)
                for state_row in workflow.get("items") or []
                if isinstance(state_row, dict)
                and batch._text(state_row.get("order_item_id"))
            }
            state = states.get(batch._text(row.get("order_item_id")), {})
            source_line = allocation.get("line") or {}
            frozen = source_line.get("ready_item_identity") or source_line.get("review_snapshot_identity") or {}
            live_identity = live_identities.get((
                order_number,
                batch._text(row.get("order_item_id")),
            ))
            if live_identity is None:
                live_identity = SimpleNamespace(
                    image_url=(
                        frozen.get("selected_image_url")
                        if isinstance(frozen, dict)
                        else None
                    ),
                    image_urls=[],
                )
            candidates = image_candidate_urls(
                state,
                live_identity,
                source_line,
            )
            for value in (row.get("selected_image_url"), row.get("resolved_image_url")):
                url = batch._text(value)
                if url.startswith("//"):
                    url = f"https:{url}"
                if url and url not in candidates:
                    candidates.append(url)

            if row.get("image_b64"):
                return candidates, None, None, batch._text(row.get("resolved_image_url") or row.get("selected_image_url")) or None
            image_bytes, image_mime, resolved_image_url = await _download_first_product_image(
                client,
                candidates,
                image_cache,
                batch,
                context,
            )
            return candidates, image_bytes, image_mime, resolved_image_url

        hydrated = await batch.bounded_map_ordered(
            row_allocations,
            hydrate,
            concurrency=8,
        )

    for row, (candidates, image_bytes, image_mime, resolved_image_url) in zip(result, hydrated):
        row["image_candidates"] = candidates
        if row.get("image_b64"):
            row["image_missing"] = False
            row["resolved_image_url"] = resolved_image_url
            continue
        row["resolved_image_url"] = resolved_image_url
        row["image_b64"] = (
            base64.b64encode(image_bytes).decode("ascii")
            if image_bytes else None
        )
        row["image_mime"] = image_mime
        row["image_missing"] = image_bytes is None
    return result


_INSTALLED = False
_ORIGINAL_CONTEXT_LOADER = None
_ORIGINAL_BATCH_LINE_BUILDER = None


def install_preparation_pdf_reference_layout() -> None:
    global _INSTALLED, _ORIGINAL_CONTEXT_LOADER, _ORIGINAL_BATCH_LINE_BUILDER
    import reviewed_preparation_batches as batch

    if _INSTALLED:
        return
    _ORIGINAL_CONTEXT_LOADER = batch.load_reviewed_product_context
    _ORIGINAL_BATCH_LINE_BUILDER = batch._build_batch_lines

    async def load_context_with_image_access(
        database: Any,
        *,
        user_id: str,
        limit: int,
    ) -> dict[str, Any]:
        context = await _ORIGINAL_CONTEXT_LOADER(
            database,
            user_id=user_id,
            limit=limit,
        )
        context["_database"] = database
        context["_user_id"] = user_id
        return context

    batch.load_reviewed_product_context = load_context_with_image_access
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
    "split_reference_card_rows",
]
