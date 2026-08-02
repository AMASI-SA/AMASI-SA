from __future__ import annotations

import fitz

from preparation_pdf import ProductLine
from preparation_pdf_card_file_number import (
    preparation_card_file_label,
    preparation_file_sequence,
    stamp_preparation_card_file_numbers,
)
from preparation_pdf_wrapped_text import (
    generate_wrapped_reference_preparation_pdf,
)


def _line(order_number: str) -> ProductLine:
    return ProductLine(
        order_number=order_number,
        order_date="2026-08-02",
        product_name="Preview product",
        customer_name="Test",
        note=None,
        quantity=2,
        total_products_in_order=4,
        shipping_company="iMile",
    )


def test_preparation_file_sequence_uses_permanent_registry_suffix() -> None:
    assert preparation_file_sequence("PF-20260802-0017") == "17"
    assert preparation_card_file_label(5, "PF-20260802-0017") == "5-17"


def test_stamp_replaces_visible_card_serials_with_card_and_file_number() -> None:
    source = generate_wrapped_reference_preparation_pdf([
        _line("990820001"),
        _line("990820002"),
    ])

    stamped = stamp_preparation_card_file_numbers(
        source,
        file_number="PF-20260802-0017",
        card_count=2,
    )

    document = fitz.open(stream=stamped, filetype="pdf")
    try:
        text = "\n".join(page.get_text() for page in document)
    finally:
        document.close()

    assert "1-17" in text
    assert "2-17" in text


def test_stamp_is_noop_when_batch_has_no_registered_file_number() -> None:
    source = generate_wrapped_reference_preparation_pdf([_line("990820001")])
    assert stamp_preparation_card_file_numbers(
        source,
        file_number=None,
        card_count=1,
    ) == source
