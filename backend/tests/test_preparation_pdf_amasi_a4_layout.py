from __future__ import annotations

import io

import fitz
from PIL import Image

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
