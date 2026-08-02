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


def _line(order_number: str, *, quantity: int = 2) -> ProductLine:
    return ProductLine(
        order_number=order_number,
        order_date="2026-08-02",
        product_name="Preview product",
        customer_name="Test",
        note=None,
        quantity=quantity,
        total_products_in_order=10,
        shipping_company="iMile",
    )


def _pdf_text(pdf_bytes: bytes) -> str:
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return "\n".join(page.get_text() for page in document)
    finally:
        document.close()


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

    text = _pdf_text(stamped)
    assert "1-17" in text
    assert "2-17" in text


def test_quantity_three_remains_one_card_for_its_salla_product_line() -> None:
    """Ten pieces can be eight cards: one line qty=3 plus seven qty=1 lines."""
    lines = [
        _line("990820001", quantity=3),
        *[
            _line(f"99082000{index}", quantity=1)
            for index in range(2, 9)
        ],
    ]
    source = generate_wrapped_reference_preparation_pdf(lines)
    stamped = stamp_preparation_card_file_numbers(
        source,
        file_number="PF-20260802-0002",
        card_count=len(lines),
    )

    text = _pdf_text(stamped)
    for card_number in range(1, 9):
        assert f"{card_number}-2" in text
    assert "9-2" not in text


def test_stamp_is_noop_when_batch_has_no_registered_file_number() -> None:
    source = generate_wrapped_reference_preparation_pdf([_line("990820001")])
    assert stamp_preparation_card_file_numbers(
        source,
        file_number=None,
        card_count=1,
    ) == source
