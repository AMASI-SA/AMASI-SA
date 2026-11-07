from __future__ import annotations

import io
import unicodedata
from datetime import datetime, timezone

import fitz
import pytest
from PIL import Image
import preparation_pdf_amasi_a4_layout as pdf_layout

from preparation_pdf import ProductLine
from preparation_pdf_amasi_a4_layout import (
    CARDS_PER_PAGE,
    generate_amasi_product_file_pdf,
)


def _image_bytes() -> bytes:
    image = Image.new("RGB", (80, 80), "#f5f5f5")
    output = io.BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


def _line(index: int) -> ProductLine:
    return ProductLine(
        order_number=f"1000{index}",
        order_date="2026-08-20T02:00:00+03:00",
        product_name=f"منتج {index}",
        customer_name="ماما حنان",
        note=None,
        quantity=1,
        total_products_in_order=5,
        item_index=index,
        image_bytes=_image_bytes(),
        image_mime="image/jpeg",
        shipping_company="سمسا",
        size="1 سنة",
        color="أزرق",
        product_id=f"p-{index}",
        sku=f"sku-{index}",
        product_options={"نوع الجهاز": "iPhone 14"},
        barcode_payload=f"MEZAN:{index}",
    )


def test_a4_contract_is_three_by_five() -> None:
    assert CARDS_PER_PAGE == 15


def test_a4_pdf_has_fifteen_cards_on_one_page_and_header_names() -> None:
    pdf = generate_amasi_product_file_pdf(
        [_line(index) for index in range(1, 16)],
        supplier_name="أبو جبل",
        responsible_employee_name="عرفات",
        file_number="PF-20260820-0017",
        file_date="2026/08/20",
    )
    assert pdf.startswith(b"%PDF")
    document = fitz.open(stream=pdf, filetype="pdf")
    try:
        assert len(document) == 1
        text = "\n".join(page.get_text() for page in document)
        assert "PF-20260820-0017" in text
        assert "15" in text
    finally:
        document.close()


def test_sixteen_cards_start_second_a4_page() -> None:
    pdf = generate_amasi_product_file_pdf([_line(index) for index in range(1, 17)])
    document = fitz.open(stream=pdf, filetype="pdf")
    try:
        assert len(document) == 2
    finally:
        document.close()


def _symbols_pdf(order_date: str | None) -> bytes:
    line = _line(1)
    line.order_date = order_date
    line.file_spec_fields = [
        {"name": "الاسم على الشال", "value": "نجمة...🩶"},
        {"name": "رسالة الكرت", "value": "نورة🩷"},
        {"name": "ملاحظة", "value": "أحبك (SKU-17) ★ • ✓"},
        {"name": "شكل التطريز", "value": "اسم مع غزال"},
    ]
    return generate_amasi_product_file_pdf([line], file_date="2026/11/08")


def test_new_order_pdf_keeps_arabic_punctuation_and_symbols() -> None:
    with fitz.open(stream=_symbols_pdf("2026-11-07T21:00:00Z"), filetype="pdf") as doc:
        page = doc[0]
        printed = unicodedata.normalize("NFKC", page.get_text())
        assert "نجمة" in printed and "نورة" in printed
        assert all(text in printed for text in ("...", "(SKU-17)", "★", "•", "✓"))
        assert "اسم مع غزال" in printed
        spans = page.get_texttrace()
        hearts = [s for s in spans if any(c[0] == ord("♥") and c[1] > 0 for c in s["chars"])]
        assert len(hearts) == 2
        assert hearts[0]["color"] != hearts[1]["color"]
        dots = next(s for s in spans if "".join(chr(c[0]) for c in s["chars"]) == "...")
        assert hearts[0]["bbox"][2] <= dots["bbox"][0] + 0.1
        symbol_xref = next(font[0] for font in page.get_fonts() if "NotoSansSymbols2" in font[3])
        assert doc.extract_font(symbol_xref)[3]  # The viewer need not have this font installed.


@pytest.mark.parametrize("order_date", ["2026-11-07T23:59:59+03:00", "2026-11-08", None])
def test_reprinting_older_or_undated_orders_keeps_the_original_appearance(
    monkeypatch, order_date,
) -> None:
    with monkeypatch.context() as disabled:
        disabled.setattr(pdf_layout, "SPEC_SYMBOLS_FROM", datetime.max.replace(tzinfo=timezone.utc))
        baseline = _symbols_pdf(order_date)
    with (
        fitz.open(stream=baseline, filetype="pdf") as before,
        fitz.open(stream=_symbols_pdf(order_date), filetype="pdf") as after,
    ):
        assert before[0].get_pixmap().samples == after[0].get_pixmap().samples
