"""Regression contract for preserving Salla customer-selected product options."""
from pathlib import Path


def test_order_mapper_keeps_extended_salla_customer_option_shapes():
    source = Path("backend/order_engine/mapper.py").read_text(encoding="utf-8")

    for token in (
        'option.get("title")',
        'option.get("question")',
        'option.get("answer")',
        'option.get("option_value")',
        'item.get("customer_options")',
        'item.get("selected_options")',
        'item.get("product_options")',
    ):
        assert token in source


def test_order_details_renders_customer_options_as_rows():
    source = Path("frontend/src/pages/OrderDetailsV2.jsx").read_text(encoding="utf-8")

    assert 'data-testid="order-v2-customer-options"' in source
    assert "خيارات العميل" in source
    assert "option?.title" in source
    assert "option?.question" in source
    assert "option?.answer" in source
    assert "option?.option_value" in source
