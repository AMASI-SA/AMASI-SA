import io
from pathlib import Path

import fitz
import pytest
from PIL import Image
from reportlab.lib.units import mm

from preparation_pdf import ProductLine
from preparation_pdf_compact_operational_layout import (
    MEDIA_TO_DETAILS_GAP,
    compact_detail_top,
    generate_compact_operational_preparation_pdf,
)
from preparation_pdf_wrapped_text import MEDIA_TO_TEXT_GAP, media_text_start


def _sample_image_bytes() -> bytes:
    image = Image.new("RGB", (320, 320), (210, 230, 210))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _sample_line() -> ProductLine:
    return ProductLine(
        order_number="275923147",
        order_date="2026-08-02",
        product_name="كوب مطبوع",
        customer_name="Princess Ahlam",
        note="اترك مسافة واضحة أسفل الصورة والباركود قبل الملاحظة",
        quantity=1,
        total_products_in_order=3,
        image_bytes=_sample_image_bytes(),
        image_mime="image/png",
        shipping_company="سمسا - شحن عادي",
        barcode_payload="order:275923147:item:1",
    )


def test_compact_details_start_four_mm_below_media() -> None:
    assert MEDIA_TO_DETAILS_GAP == pytest.approx(4.0 * mm)
    media_bottom = 100.0
    assert compact_detail_top(media_bottom) == pytest.approx(
        media_bottom - 4.0 * mm
    )


def test_wrapped_fallback_uses_the_same_printed_gap() -> None:
    assert MEDIA_TO_TEXT_GAP == pytest.approx(4.0 * mm)
    assert media_text_start(100.0) == pytest.approx(100.0 - 4.0 * mm)


def test_final_compact_overlay_uses_gap_helper() -> None:
    backend = Path(__file__).parents[1]
    source = (
        backend / "preparation_pdf_compact_operational_layout.py"
    ).read_text(encoding="utf-8")
    install_source = (
        backend / "order_review_forward_stage_guard.py"
    ).read_text(encoding="utf-8")

    assert "detail_top = compact_detail_top(media_y)" in source
    assert "detail_top = media_y - 3.2" not in source
    assert install_source.index(
        "install_preparation_pdf_compact_operational_layout()"
    ) > install_source.index("install_preparation_pdf_wrapped_text()")


def test_compact_renderer_builds_a_real_pdf_with_both_media_blocks() -> None:
    pdf_bytes = generate_compact_operational_preparation_pdf([_sample_line()])
    assert pdf_bytes.startswith(b"%PDF")

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        assert document.page_count == 1
        images = document[0].get_images(full=True)
        assert len(images) >= 2  # product image + QR
    finally:
        document.close()
