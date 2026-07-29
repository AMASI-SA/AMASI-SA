"""Printable Mezan V2 shipping/labeling batch cards."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except Exception:  # pragma: no cover - production requirements install both
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
            pdfmetrics.registerFont(TTFont("MezanShippingArabic", str(regular)))
            pdfmetrics.registerFont(TTFont("MezanShippingArabicBold", str(bold)))
            _FONT_REGULAR = "MezanShippingArabic"
            _FONT_BOLD = "MezanShippingArabicBold"
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


def _address(order: Any) -> str:
    shipping = getattr(order, "shipping", None)
    address = getattr(shipping, "address", None)
    if address is None:
        return ""
    parts = []
    for value in (
        getattr(address, "city", None),
        getattr(address, "district", None),
        getattr(address, "street", None),
        getattr(address, "building_number", None),
        getattr(address, "postal_code", None),
    ):
        normalized = _text(value)
        if normalized and normalized not in parts:
            parts.append(normalized)
    return "، ".join(parts) or _text(getattr(address, "formatted", None))


def generate_shipping_batch_pdf(
    *,
    batch: dict[str, Any],
    orders: list[Any],
) -> bytes:
    """Generate one reviewable packing/address card per order.

    This is a Mezan operational batch, not a carrier-issued waybill.  Carrier
    labels remain an explicit later action after the employee reviews the
    recipient and address.
    """
    regular_font, bold_font = _register_font()
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    for index, order in enumerate(orders, start=1):
        page.setFillColor(HexColor("#5B21B6"))
        page.rect(0, height - 31 * mm, width, 31 * mm, fill=1, stroke=0)
        page.setFillColor(HexColor("#FFFFFF"))
        page.setFont(bold_font, 17)
        page.drawRightString(
            width - 15 * mm,
            height - 14 * mm,
            _ar("دفعة الشحن والعنونة — Mezan OS V2"),
        )
        page.setFont(regular_font, 9)
        page.drawRightString(
            width - 15 * mm,
            height - 23 * mm,
            _ar(
                f"الدفعة: {_text(batch.get('id'))}  |  "
                f"البطاقة: {index}/{len(orders)}"
            ),
        )

        y = height - 45 * mm
        customer = getattr(order, "customer", None)
        shipping = getattr(order, "shipping", None)
        rows = [
            ("رقم الطلب", f"#{_text(getattr(order, 'order_number', None))}"),
            ("المستلم", _text(getattr(customer, "name", None))),
            ("الجوال", _text(getattr(customer, "mobile", None))),
            ("العنوان", _address(order)),
            ("شركة الشحن", _text(getattr(shipping, "company", None))),
        ]
        for label, value in rows:
            page.setFillColor(HexColor("#64748B"))
            page.setFont(regular_font, 9)
            page.drawRightString(width - 15 * mm, y, _ar(label))
            page.setFillColor(HexColor("#0F172A"))
            page.setFont(bold_font, 12)
            page.drawRightString(width - 48 * mm, y, _ar(value or "—"))
            y -= 12 * mm

        page.setStrokeColor(HexColor("#E2E8F0"))
        page.line(15 * mm, y + 5 * mm, width - 15 * mm, y + 5 * mm)
        page.setFillColor(HexColor("#0F172A"))
        page.setFont(bold_font, 12)
        page.drawRightString(width - 15 * mm, y - 3 * mm, _ar("محتويات الطلب"))
        y -= 14 * mm

        for item in getattr(order, "items", None) or []:
            quantity = getattr(item, "quantity", 1) or 1
            name = _text(getattr(item, "name", None))
            sku = _text(getattr(item, "sku", None))
            line = f"{quantity:g} × {name}"
            if sku:
                line += f" — {sku}"
            page.setFont(regular_font, 10)
            page.drawRightString(width - 19 * mm, y, _ar(f"• {line}"))
            y -= 8 * mm
            if y < 35 * mm:
                break

        page.setFillColor(HexColor("#FEF3C7"))
        page.roundRect(
            15 * mm,
            13 * mm,
            width - 30 * mm,
            16 * mm,
            3 * mm,
            fill=1,
            stroke=0,
        )
        page.setFillColor(HexColor("#92400E"))
        page.setFont(bold_font, 9)
        page.drawCentredString(
            width / 2,
            20 * mm,
            _ar(
                "ملف تشغيلي للمراجعة والتغليف — لا يُعد بوليصة شحن صادرة من الناقل"
            ),
        )
        page.showPage()

    page.save()
    return buffer.getvalue()


__all__ = ["generate_shipping_batch_pdf"]
