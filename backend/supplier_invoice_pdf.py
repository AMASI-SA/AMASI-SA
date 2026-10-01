"""Arabic supplier invoice PDF for Mezan supplier receiving."""
from __future__ import annotations

import base64
import io
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except Exception:  # pragma: no cover
    arabic_reshaper = None
    get_display = None


_FONT_REGISTERED = False
_FONT_REGULAR = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"


def _register_font() -> tuple[str, str]:
    global _FONT_REGISTERED, _FONT_REGULAR, _FONT_BOLD
    if _FONT_REGISTERED:
        return _FONT_REGULAR, _FONT_BOLD
    fonts = Path(__file__).resolve().parent / "fonts"
    regular = fonts / "NotoSansArabic-SemiBold.ttf"
    bold = fonts / "NotoSansArabic-Bold.ttf"
    if regular.exists() and bold.exists():
        try:
            pdfmetrics.registerFont(TTFont("MezanSupplierArabic", str(regular)))
            pdfmetrics.registerFont(TTFont("MezanSupplierArabicBold", str(bold)))
            _FONT_REGULAR = "MezanSupplierArabic"
            _FONT_BOLD = "MezanSupplierArabicBold"
        except Exception:
            pass
    _FONT_REGISTERED = True
    return _FONT_REGULAR, _FONT_BOLD


def _ar(value: Any) -> str:
    text = str(value or "")
    if not arabic_reshaper or not get_display:
        return text
    try:
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def _text(value: Any) -> str:
    return str(value or "").strip()


def _money(halalas: Any) -> str:
    try:
        amount = int(halalas or 0) / 100
    except (TypeError, ValueError, OverflowError):
        amount = 0
    return f"{amount:,.2f} ر.س"


def _date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y/%m/%d")
    return _text(value)[:10] or "—"


def _amasi_logo() -> ImageReader | None:
    path = Path(__file__).resolve().parent / "assets" / "amasi_logo_transparent.b64"
    try:
        raw = base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
        return ImageReader(io.BytesIO(raw))
    except Exception:
        return None


def _product_image(value: Any) -> ImageReader | None:
    url = _text(value)
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        trusted = (
            host in {"salla.sa", "salla.network"}
            or host.endswith(".salla.sa")
            or host.endswith(".salla.network")
        )
        if parsed.scheme != "https" or not trusted:
            return None
        request = Request(url, headers={"User-Agent": "AMASI-Supplier-Invoice/1.0"})
        with urlopen(request, timeout=4) as response:
            content_type = _text(response.headers.get("Content-Type")).casefold()
            if not content_type.startswith("image/"):
                return None
            raw = response.read(5 * 1024 * 1024 + 1)
        if not raw or len(raw) > 5 * 1024 * 1024:
            return None
        return ImageReader(io.BytesIO(raw))
    except Exception:
        return None


def _service_quantity_per_piece(service: dict[str, Any], line_quantity: int) -> float:
    raw = service.get("quantity_per_piece")
    if raw not in (None, ""):
        try:
            return float(raw)
        except (TypeError, ValueError, OverflowError):
            return 0.0
    try:
        total = float(service.get("total_quantity") or 0)
    except (TypeError, ValueError, OverflowError):
        total = 0.0
    return total / max(1, line_quantity)


def _supplier_invoice_print_group_key(line: dict[str, Any]) -> tuple[Any, ...]:
    quantity = max(1, int(line.get("quantity") or 1))
    services = tuple(sorted(
        (
            _text(service.get("service_id")),
            _text(service.get("service_code")),
            _text(service.get("service_name")).casefold(),
            _text(service.get("unit")),
            int(service.get("unit_price_halalas") or 0),
            round(_service_quantity_per_piece(service, quantity), 6),
        )
        for service in (line.get("services") or [])
        if isinstance(service, dict)
    ))
    return (
        _text(line.get("product_id")),
        _text(line.get("sku")),
        _text(line.get("variant_id")),
        _text(line.get("product_name")).casefold(),
        int(line.get("product_unit_price_halalas") or 0),
        bool(line.get("product_charge_eligible", True)),
        services,
    )


def _group_supplier_invoice_lines_for_print(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse commercially identical invoice rows for the PDF only.

    The persisted invoice/ledger remains piece-auditable.  Grouping is a pure
    presentation step: product identity, unit price and the per-piece service
    recipe must all match before quantities and totals are summed.
    """
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    service_maps: dict[tuple[Any, ...], dict[tuple[Any, ...], dict[str, Any]]] = {}

    for raw in lines:
        if not isinstance(raw, dict):
            continue
        line = dict(raw)
        quantity = max(1, int(line.get("quantity") or 1))
        key = _supplier_invoice_print_group_key(line)
        existing = grouped.get(key)
        if existing is None:
            cloned_services = [dict(service) for service in (line.get("services") or []) if isinstance(service, dict)]
            line["quantity"] = quantity
            line["services"] = cloned_services
            line["piece_ids"] = list(dict.fromkeys(
                _text(piece_id) for piece_id in (line.get("piece_ids") or []) if _text(piece_id)
            ))
            grouped[key] = line
            service_maps[key] = {
                (
                    _text(service.get("service_id")),
                    _text(service.get("service_code")),
                    _text(service.get("service_name")).casefold(),
                    _text(service.get("unit")),
                    int(service.get("unit_price_halalas") or 0),
                    round(_service_quantity_per_piece(service, quantity), 6),
                ): service
                for service in cloned_services
            }
            continue

        existing["quantity"] = int(existing.get("quantity") or 0) + quantity
        for field in (
            "product_total_halalas",
            "services_total_halalas",
            "total_halalas",
        ):
            existing[field] = int(existing.get(field) or 0) + int(line.get(field) or 0)
        existing["piece_ids"] = list(dict.fromkeys([
            *[value for value in (existing.get("piece_ids") or []) if _text(value)],
            *[_text(value) for value in (line.get("piece_ids") or []) if _text(value)],
        ]))
        if not _text(existing.get("selected_image_url")) and _text(line.get("selected_image_url")):
            existing["selected_image_url"] = line.get("selected_image_url")

        by_service = service_maps[key]
        for service in (line.get("services") or []):
            if not isinstance(service, dict):
                continue
            service_key = (
                _text(service.get("service_id")),
                _text(service.get("service_code")),
                _text(service.get("service_name")).casefold(),
                _text(service.get("unit")),
                int(service.get("unit_price_halalas") or 0),
                round(_service_quantity_per_piece(service, quantity), 6),
            )
            target = by_service.get(service_key)
            if target is None:
                # The group key should make this unreachable, but fail closed
                # into a separate visual service row instead of losing money.
                target = dict(service)
                existing.setdefault("services", []).append(target)
                by_service[service_key] = target
                continue
            try:
                target["total_quantity"] = float(target.get("total_quantity") or 0) + float(service.get("total_quantity") or 0)
            except (TypeError, ValueError, OverflowError):
                pass
            target["total_halalas"] = int(target.get("total_halalas") or 0) + int(service.get("total_halalas") or 0)

    return list(grouped.values())


def generate_supplier_invoice_pdf(invoice: dict[str, Any]) -> bytes:
    """Render a compact RTL AMASI supplier invoice table."""
    regular_font, bold_font = _register_font()
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    right = width - 15 * mm
    left = 15 * mm
    content_width = right - left
    supplier = dict(invoice.get("supplier_snapshot") or {})
    logo = _amasi_logo()
    y = height - 18 * mm

    burgundy = HexColor("#74102F")
    gold = HexColor("#CDA14A")
    ink = HexColor("#0F172A")
    muted = HexColor("#64748B")
    grid = HexColor("#CBD5E1")
    soft = HexColor("#F8FAFC")
    cream = HexColor("#FFF8E7")

    # RTL order from the right edge: image, product, quantity, unit price,
    # services, line total. Widths add up to the full printable width (180 mm).
    column_widths = [18, 42, 18, 32, 42, 28]
    column_labels = ["صورة", "اسم المنتج", "الكمية", "سعر القطعة", "الخدمات", "الإجمالي"]
    boundaries = [right]
    for column_width in column_widths:
        boundaries.append(boundaries[-1] - column_width * mm)

    def product_name(value: Any) -> str:
        words = [word for word in _text(value).split() if word]
        return " ".join(words[:2]) or "منتج"

    def draw_brand_header(*, compact: bool = False) -> None:
        nonlocal y
        if compact:
            page.setFillColor(burgundy)
            page.roundRect(left, height - 27 * mm, content_width, 15 * mm, 3 * mm, fill=1, stroke=0)
            page.setFillColor(HexColor("#FFFFFF"))
            page.setFont(bold_font, 12)
            page.drawRightString(right - 4 * mm, height - 21 * mm, _ar("فاتورة مورد — أماسي"))
            page.setFont(regular_font, 8)
            page.drawString(left + 4 * mm, height - 21 * mm, _text(invoice.get("invoice_number")))
            y = height - 34 * mm
            return

        page.setFillColor(burgundy)
        page.roundRect(left, height - 42 * mm, content_width, 28 * mm, 4 * mm, fill=1, stroke=0)
        page.setFillColor(HexColor("#FFFFFF"))
        page.setFont(bold_font, 18)
        page.drawRightString(right - 5 * mm, height - 25 * mm, _ar("فاتورة مورد — أماسي"))
        if logo is not None:
            page.drawImage(
                logo,
                left + 5 * mm,
                height - 37 * mm,
                width=18 * mm,
                height=18 * mm,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )
        page.setFont(regular_font, 9)
        page.drawString(left + 26 * mm, height - 25 * mm, _text(invoice.get("invoice_number")))
        y = height - 52 * mm

        header_rows = [
            ("المورد", _text(supplier.get("company_name")) or "—"),
            ("رقم الفاتورة", _text(invoice.get("invoice_number")) or "—"),
            ("التاريخ", _date(invoice.get("approved_at") or invoice.get("created_at"))),
            ("أصدرها", _text(invoice.get("supplier_approved_by_name")) or "—"),
        ]
        for label, value in header_rows:
            page.setFillColor(muted)
            page.setFont(regular_font, 9)
            page.drawRightString(right, y, _ar(label))
            page.setFillColor(ink)
            page.setFont(bold_font, 11)
            page.drawRightString(right - 36 * mm, y, _ar(value))
            y -= 8 * mm
        y -= 5 * mm

    def draw_table_header() -> None:
        nonlocal y
        header_height = 10 * mm
        page.setFillColor(soft)
        page.setStrokeColor(grid)
        page.setLineWidth(0.5)
        page.rect(left, y - header_height, content_width, header_height, fill=1, stroke=1)
        for boundary in boundaries[1:-1]:
            page.line(boundary, y, boundary, y - header_height)
        page.setFillColor(burgundy)
        page.setFont(bold_font, 8.5)
        for index, label in enumerate(column_labels):
            center_x = (boundaries[index] + boundaries[index + 1]) / 2
            page.drawCentredString(center_x, y - 6.5 * mm, _ar(label))
        y -= header_height

    def new_table_page() -> None:
        page.showPage()
        draw_brand_header(compact=True)
        draw_table_header()

    draw_brand_header()
    draw_table_header()

    for line in _group_supplier_invoice_lines_for_print(list(invoice.get("lines") or [])):
        services = list(line.get("services") or [])
        row_height_mm = max(18.0, 8.0 + (7.0 * len(services)))
        row_height = row_height_mm * mm
        if y - row_height < 30 * mm:
            new_table_page()

        row_top = y
        row_bottom = y - row_height
        page.setFillColor(HexColor("#FFFFFF"))
        page.setStrokeColor(grid)
        page.setLineWidth(0.45)
        page.rect(left, row_bottom, content_width, row_height, fill=1, stroke=1)
        for boundary in boundaries[1:-1]:
            page.line(boundary, row_top, boundary, row_bottom)

        center_y = (row_top + row_bottom) / 2
        image = _product_image(line.get("selected_image_url"))
        if image is not None:
            image_center_x = (boundaries[0] + boundaries[1]) / 2
            page.drawImage(
                image,
                image_center_x - 4 * mm,
                center_y - 4 * mm,
                width=8 * mm,
                height=8 * mm,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )

        page.setFillColor(ink)
        page.setFont(bold_font, 9)
        product_center_x = (boundaries[1] + boundaries[2]) / 2
        page.drawCentredString(product_center_x, center_y - 1.5 * mm, _ar(product_name(line.get("product_name"))))

        page.setFont(bold_font, 9)
        quantity_center_x = (boundaries[2] + boundaries[3]) / 2
        page.drawCentredString(quantity_center_x, center_y - 1.5 * mm, str(int(line.get("quantity") or 0)))

        page.setFont(regular_font, 8.5)
        unit_center_x = (boundaries[3] + boundaries[4]) / 2
        page.drawCentredString(unit_center_x, center_y - 1.5 * mm, _ar(_money(line.get("product_unit_price_halalas"))))

        services_center_x = (boundaries[4] + boundaries[5]) / 2
        if not services:
            page.setFont(regular_font, 9)
            page.drawCentredString(services_center_x, center_y - 1.5 * mm, "—")
        else:
            service_line_height = 6.5 * mm
            service_y = center_y + ((len(services) - 1) * service_line_height / 2)
            for service in services:
                service_name = product_name(service.get("service_name") or "خدمة")
                service_amount = _money(service.get("total_halalas"))
                page.setFont(bold_font, 7.5)
                page.drawCentredString(services_center_x, service_y, _ar(service_name))
                page.setFont(regular_font, 7)
                page.drawCentredString(services_center_x, service_y - 3.5 * mm, _ar(service_amount))
                service_y -= service_line_height

        total_center_x = (boundaries[5] + boundaries[6]) / 2
        page.setFillColor(burgundy)
        page.setFont(bold_font, 8.5)
        page.drawCentredString(total_center_x, center_y - 1.5 * mm, _ar(_money(line.get("total_halalas"))))
        y = row_bottom

    if y - 24 * mm < 18 * mm:
        page.showPage()
        draw_brand_header(compact=True)

    y -= 8 * mm
    page.setFillColor(cream)
    page.setStrokeColor(gold)
    page.setLineWidth(0.8)
    page.roundRect(left, y - 17 * mm, content_width, 20 * mm, 4 * mm, fill=1, stroke=1)
    page.setFillColor(burgundy)
    page.setFont(bold_font, 15)
    page.drawCentredString(
        width / 2,
        y - 9 * mm,
        _ar(f"الإجمالي النهائي: {_money(invoice.get('total_halalas'))}"),
    )

    page.save()
    return buffer.getvalue()


__all__ = ["generate_supplier_invoice_pdf"]
