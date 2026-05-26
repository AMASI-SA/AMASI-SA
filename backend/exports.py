"""Export reports to Excel and PDF (with Arabic support)."""
from __future__ import annotations

import io

import xlsxwriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    _HAS_AR = True
except Exception:
    _HAS_AR = False


# Register a Unicode font that supports Arabic. DejaVuSans is shipped with most systems.
_FONT_REGISTERED = False
_FONT_NAME = "Helvetica"


def _register_font() -> str:
    global _FONT_REGISTERED, _FONT_NAME
    if _FONT_REGISTERED:
        return _FONT_NAME
    candidates = [
        ("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ("NotoSansArabic", "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"),
        ("Amiri", "/usr/share/fonts/truetype/amiri/amiri-regular.ttf"),
    ]
    for name, path in candidates:
        try:
            pdfmetrics.registerFont(TTFont(name, path))
            _FONT_NAME = name
            _FONT_REGISTERED = True
            return _FONT_NAME
        except Exception:
            continue
    _FONT_REGISTERED = True  # use default
    return _FONT_NAME


def _ar(text: str) -> str:
    """Reshape Arabic for proper PDF rendering."""
    if not _HAS_AR or text is None:
        return str(text) if text is not None else ""
    try:
        reshaped = arabic_reshaper.reshape(str(text))
        return get_display(reshaped)
    except Exception:
        return str(text)


def export_report_excel(report: dict) -> bytes:
    """Build an .xlsx file in memory and return its bytes."""
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    wb.formats[0].set_font_name("Arial")

    title_fmt = wb.add_format({
        "bold": True, "font_size": 16, "align": "right", "valign": "vcenter",
        "bg_color": "#0A3622", "font_color": "#FFFFFF", "font_name": "Arial",
    })
    header_fmt = wb.add_format({
        "bold": True, "font_size": 11, "align": "right", "valign": "vcenter",
        "bg_color": "#F3F4F1", "font_color": "#0A3622", "border": 1, "font_name": "Arial",
    })
    cell_fmt = wb.add_format({"align": "right", "border": 1, "font_name": "Arial"})
    num_fmt = wb.add_format({"align": "right", "border": 1, "num_format": "#,##0.00", "font_name": "Arial"})
    kpi_fmt = wb.add_format({
        "bold": True, "font_size": 13, "align": "right", "valign": "vcenter",
        "bg_color": "#FFFFFF", "border": 1, "num_format": "#,##0.00", "font_name": "Arial",
    })
    label_fmt = wb.add_format({
        "bold": True, "font_size": 11, "align": "right", "valign": "vcenter",
        "bg_color": "#F3F4F1", "border": 1, "font_name": "Arial",
    })

    ws = wb.add_worksheet("ملخص التقرير")
    ws.right_to_left()
    ws.set_column("A:A", 35)
    ws.set_column("B:B", 25)
    ws.set_column("C:F", 18)

    ws.merge_range("A1:F1", "تقرير المحاسبة — تحليل ملف سلة", title_fmt)
    ws.set_row(0, 32)

    summary = report.get("summary", {})
    kpi_rows = [
        ("إجمالي المبيعات (ر.س)", summary.get("total_sales", 0)),
        ("إجمالي عدد الطلبات", summary.get("total_orders", 0)),
        ("إجمالي رسوم بوابات الدفع (ر.س)", summary.get("total_payment_fees", 0)),
        ("إجمالي تكاليف الشحن (ر.س)", summary.get("total_shipping_cost", 0)),
        ("إجمالي تكاليف الإعلانات (ر.س)", summary.get("total_ads_cost", 0)),
        ("تكلفة المنتجات (ر.س)", summary.get("total_product_cost", 0)),
        ("صافي الربح النهائي (ر.س)", summary.get("net_profit", 0)),
    ]
    row = 2
    for label, value in kpi_rows:
        ws.write(row, 0, label, label_fmt)
        ws.write_number(row, 1, float(value or 0), kpi_fmt)
        row += 1

    # Payment breakdown
    row += 2
    ws.merge_range(row, 0, row, 7, "تفاصيل طرق الدفع", title_fmt)
    ws.set_row(row, 28)
    row += 1
    headers = [
        "طريقة الدفع", "عدد الطلبات", "إجمالي المبيعات",
        "نسبة % ", "مبلغ ثابت", "العمولة الأساسية",
        "ضريبة % ", "إجمالي العمولة",
    ]
    for c, h in enumerate(headers):
        ws.write(row, c, h, header_fmt)
    row += 1
    for pm in report.get("payment_breakdown", []):
        ws.write(row, 0, pm.get("name", ""), cell_fmt)
        ws.write_number(row, 1, int(pm.get("orders_count", 0)), num_fmt)
        ws.write_number(row, 2, float(pm.get("total_sales", 0)), num_fmt)
        ws.write_number(row, 3, float(pm.get("commission_percent", 0)), num_fmt)
        ws.write_number(row, 4, float(pm.get("fixed_fee", 0)), num_fmt)
        ws.write_number(row, 5, float(pm.get("base_commission", pm.get("fee_amount", 0))), num_fmt)
        ws.write_number(row, 6, float(pm.get("vat_percent", 0)), num_fmt)
        ws.write_number(row, 7, float(pm.get("fee_amount", 0)), num_fmt)
        row += 1

    # Shipping breakdown
    row += 2
    ws.merge_range(row, 0, row, 5, "تفاصيل شركات الشحن", title_fmt)
    ws.set_row(row, 28)
    row += 1
    headers = ["شركة الشحن", "عدد الطلبات", "تكلفة الشحنة", "قبل الضريبة", "ضريبة %", "الإجمالي"]
    for c, h in enumerate(headers):
        ws.write(row, c, h, header_fmt)
    row += 1
    for sc in report.get("shipping_breakdown", []):
        ws.write(row, 0, sc.get("name", ""), cell_fmt)
        ws.write_number(row, 1, int(sc.get("orders_count", 0)), num_fmt)
        ws.write_number(row, 2, float(sc.get("cost_per_order", 0)), num_fmt)
        ws.write_number(row, 3, float(sc.get("base_cost", sc.get("total_cost", 0))), num_fmt)
        ws.write_number(row, 4, float(sc.get("vat_percent", 0)), num_fmt)
        ws.write_number(row, 5, float(sc.get("total_cost", 0)), num_fmt)
        row += 1

    # Daily costs
    daily_costs = report.get("daily_costs", {})
    if daily_costs:
        row += 2
        ws.merge_range(row, 0, row, 1, "التكاليف اليومية", title_fmt)
        ws.set_row(row, 28)
        row += 1
        cost_items = [
            ("إعلانات سناب شات", daily_costs.get("snapchat_ads", 0)),
            ("إعلانات تيك توك", daily_costs.get("tiktok_ads", 0)),
            ("إعلانات إنستقرام", daily_costs.get("instagram_ads", 0)),
            ("تكاليف المنتجات", daily_costs.get("product_costs", 0)),
        ]
        for label, value in cost_items:
            ws.write(row, 0, label, label_fmt)
            ws.write_number(row, 1, float(value or 0), num_fmt)
            row += 1

    wb.close()
    return buf.getvalue()


def export_report_pdf(report: dict) -> bytes:
    """Build a PDF in memory."""
    _register_font()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=2 * cm, leftMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title="تقرير محاسبي",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", parent=styles["Title"], fontName=_FONT_NAME, alignment=2, fontSize=18,
        textColor=colors.HexColor("#0A3622"),
    )
    h2_style = ParagraphStyle(
        "h2", parent=styles["Heading2"], fontName=_FONT_NAME, alignment=2, fontSize=13,
        textColor=colors.HexColor("#0A3622"), spaceBefore=12, spaceAfter=8,
    )
    body_style = ParagraphStyle(  # noqa: F841
        "body", parent=styles["BodyText"], fontName=_FONT_NAME, alignment=2, fontSize=11,
    )

    elements: list = []
    elements.append(Paragraph(_ar("تقرير المحاسبة — تحليل ملف سلة"), title_style))
    elements.append(Spacer(1, 0.4 * cm))

    summary = report.get("summary", {})
    kpi_rows = [
        ("إجمالي المبيعات (ر.س)", summary.get("total_sales", 0)),
        ("إجمالي عدد الطلبات", summary.get("total_orders", 0)),
        ("إجمالي رسوم بوابات الدفع (ر.س)", summary.get("total_payment_fees", 0)),
        ("إجمالي تكاليف الشحن (ر.س)", summary.get("total_shipping_cost", 0)),
        ("إجمالي تكاليف الإعلانات (ر.س)", summary.get("total_ads_cost", 0)),
        ("تكلفة المنتجات (ر.س)", summary.get("total_product_cost", 0)),
        ("صافي الربح النهائي (ر.س)", summary.get("net_profit", 0)),
    ]
    data = [[_ar(f"{v:,.2f}" if isinstance(v, (int, float)) else str(v)), _ar(k)] for k, v in kpi_rows]
    tbl = Table([[_ar("القيمة"), _ar("البند")]] + data, colWidths=[5 * cm, 10 * cm])
    tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0A3622")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8F9FA")]),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(tbl)

    # Payment breakdown
    elements.append(Paragraph(_ar("تفاصيل طرق الدفع"), h2_style))
    pm_data = [[
        _ar("إجمالي العمولة"), _ar("ضريبة %"), _ar("العمولة الأساسية"),
        _ar("مبلغ ثابت"), _ar("نسبة %"),
        _ar("إجمالي المبيعات"), _ar("عدد الطلبات"), _ar("طريقة الدفع"),
    ]]
    for pm in report.get("payment_breakdown", []):
        pm_data.append([
            _ar(f"{pm.get('fee_amount', 0):,.2f}"),
            _ar(f"{pm.get('vat_percent', 0):.2f}"),
            _ar(f"{pm.get('base_commission', pm.get('fee_amount', 0)):,.2f}"),
            _ar(f"{pm.get('fixed_fee', 0):,.2f}"),
            _ar(f"{pm.get('commission_percent', 0):.2f}"),
            _ar(f"{pm.get('total_sales', 0):,.2f}"),
            _ar(f"{pm.get('orders_count', 0)}"),
            _ar(pm.get("name", "")),
        ])
    if len(pm_data) > 1:
        t = Table(pm_data, colWidths=[2.2*cm, 1.6*cm, 2.2*cm, 1.7*cm, 1.5*cm, 2.4*cm, 1.8*cm, 3*cm])
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F1")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0A3622")),
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
        ]))
        elements.append(t)

    # Shipping breakdown
    elements.append(Paragraph(_ar("تفاصيل شركات الشحن"), h2_style))
    sh_data = [[_ar("الإجمالي"), _ar("ضريبة %"), _ar("قبل الضريبة"), _ar("تكلفة الشحنة"), _ar("عدد الطلبات"), _ar("شركة الشحن")]]
    for sc in report.get("shipping_breakdown", []):
        sh_data.append([
            _ar(f"{sc.get('total_cost', 0):,.2f}"),
            _ar(f"{sc.get('vat_percent', 0):.2f}"),
            _ar(f"{sc.get('base_cost', sc.get('total_cost', 0)):,.2f}"),
            _ar(f"{sc.get('cost_per_order', 0):,.2f}"),
            _ar(f"{sc.get('orders_count', 0)}"),
            _ar(sc.get("name", "")),
        ])
    if len(sh_data) > 1:
        t = Table(sh_data, colWidths=[2.6*cm, 1.8*cm, 2.4*cm, 2.4*cm, 2.2*cm, 3.6*cm])
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F1")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0A3622")),
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
        ]))
        elements.append(t)

    doc.build(elements)
    return buf.getvalue()
