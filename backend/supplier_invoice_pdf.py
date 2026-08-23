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
    path = Path(__file__).resolve().parent / "assets" / "amasi_qr_logo.b64"
    try:
        raw = base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
        return ImageReader(io.BytesIO(raw))
    except Exception:
        return None


def _short_text(value: Any, limit: int = 52) -> str:
    text = _text(value)
    if len(text) <= limit:
        return text
    return f"{text[: max(1, limit - 1)].rstrip()}…"


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


def generate_supplier_invoice_pdf(invoice: dict[str, Any]) -> bytes:
    """Render one durable supplier invoice with selected services only."""
    regular_font, bold_font = _register_font()
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    right = width - 15 * mm
    left = 15 * mm
    y = height - 18 * mm

    def new_page() -> None:
        nonlocal y
        page.showPage()
        y = height - 18 * mm

    def ensure_space(required_mm: float) -> None:
        if y < required_mm * mm:
            new_page()

    supplier = dict(invoice.get("supplier_snapshot") or {})
    page.setFillColor(HexColor("#74102F"))
    page.roundRect(left, height - 42 * mm, width - 30 * mm, 28 * mm, 4 * mm, fill=1, stroke=0)
    page.setFillColor(HexColor("#FFFFFF"))
    page.setFont(bold_font, 18)
    page.drawRightString(right - 5 * mm, height - 25 * mm, _ar("فاتورة مورد — أماسي"))
    logo = _amasi_logo()
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
        page.setFillColor(HexColor("#64748B"))
        page.setFont(regular_font, 9)
        page.drawRightString(right, y, _ar(label))
        page.setFillColor(HexColor("#0F172A"))
        page.setFont(bold_font, 11)
        page.drawRightString(right - 36 * mm, y, _ar(value))
        y -= 8 * mm

    y -= 3 * mm
    for line in invoice.get("lines") or []:
        ensure_space(62)
        line_top = y
        page.setFillColor(HexColor("#F8FAFC"))
        page.setStrokeColor(HexColor("#E2E8F0"))
        page.roundRect(left, y - 17 * mm, width - 30 * mm, 20 * mm, 3 * mm, fill=1, stroke=1)
        image = _product_image(line.get("selected_image_url"))
        text_right = right - 4 * mm
        if image is not None:
            page.drawImage(
                image,
                right - 19 * mm,
                y - 15 * mm,
                width=14 * mm,
                height=14 * mm,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )
            text_right = right - 23 * mm
        page.setFillColor(HexColor("#0F172A"))
        page.setFont(bold_font, 10)
        product_name = _short_text(line.get("product_name") or "منتج")
        page.drawRightString(text_right, y - 4 * mm, _ar(product_name))
        page.setFont(regular_font, 8)
        facts = (
            f"الكمية: {int(line.get('quantity') or 0)}  |  "
            f"سعر المنتج: {_money(line.get('product_unit_price_halalas'))}  |  "
            f"إجمالي المنتج: {_money(line.get('product_total_halalas'))}"
        )
        page.drawRightString(text_right, y - 11 * mm, _ar(facts))
        y -= 23 * mm

        services = list(line.get("services") or [])
        if services:
            page.setFillColor(HexColor("#CDA14A"))
            page.setFont(bold_font, 9)
            page.drawRightString(right - 4 * mm, y, _ar("الخدمات المختارة"))
            y -= 6 * mm
            for service in services:
                ensure_space(28)
                page.setFillColor(HexColor("#334155"))
                page.setFont(regular_font, 8.5)
                text = (
                    f"• {_text(service.get('service_name')) or 'خدمة'} — "
                    f"{service.get('total_quantity') or 0:g} × "
                    f"{_money(service.get('unit_price_halalas'))} = "
                    f"{_money(service.get('total_halalas'))}"
                )
                page.drawRightString(right - 8 * mm, y, _ar(text))
                y -= 6 * mm

        page.setFillColor(HexColor("#74102F"))
        page.setFont(bold_font, 10)
        page.drawRightString(right - 4 * mm, y, _ar(f"إجمالي السطر: {_money(line.get('total_halalas'))}"))
        y -= 11 * mm
        if line_top == y:
            y -= 5 * mm

    ensure_space(32)
    page.setFillColor(HexColor("#FFF8E7"))
    page.setStrokeColor(HexColor("#CDA14A"))
    page.roundRect(left, y - 17 * mm, width - 30 * mm, 20 * mm, 4 * mm, fill=1, stroke=1)
    page.setFillColor(HexColor("#74102F"))
    page.setFont(bold_font, 15)
    page.drawCentredString(width / 2, y - 9 * mm, _ar(f"الإجمالي النهائي: {_money(invoice.get('total_halalas'))}"))

    page.save()
    return buffer.getvalue()


__all__ = ["generate_supplier_invoice_pdf"]
