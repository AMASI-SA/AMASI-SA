"""Contract tests for the canonical Mezan Order DTO."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from order_engine.models import (
    OrderDTO,
    OrderItemDTO,
    OrderSourceDTO,
)


def test_canonical_order_accepts_minimum_valid_contract():
    created_at = datetime(2026, 7, 13, 12, 30, tzinfo=timezone.utc)

    order = OrderDTO(
        order_id="1065072654",
        order_number="272139435",
        created_at=created_at,
        source=OrderSourceDTO(
            source_order_id="1065072654",
            source_reference="272139435",
        ),
        items=[
            OrderItemDTO(
                order_item_id="oi_272139435_1",
                product_id="11912",
                variant_id="11912-gold",
                sku="AMS11912",
                name="اسوارة الفراشة الأكثر أناقة",
                quantity=1,
                color="ذهبي",
                size="18",
            )
        ],
    )

    assert order.schema_version == 1
    assert order.order_number == "272139435"
    assert order.created_at == created_at
    assert order.items[0].order_item_id == "oi_272139435_1"
    assert order.items[0].color == "ذهبي"


def test_order_item_requires_stable_order_item_id():
    with pytest.raises(ValidationError):
        OrderItemDTO(
            order_item_id="",
            name="منتج",
            quantity=1,
        )


def test_order_rejects_database_specific_extra_fields():
    with pytest.raises(ValidationError):
        OrderDTO(
            order_id="1",
            order_number="1",
            created_at=datetime.now(timezone.utc),
            source=OrderSourceDTO(),
            raw_by_source={},
        )


def test_quantity_must_be_positive():
    with pytest.raises(ValidationError):
        OrderItemDTO(
            order_item_id="oi_1",
            name="منتج",
            quantity=0,
        )


def test_order_created_at_is_separate_from_engine_updated_at():
    created_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    updated_at = datetime(2026, 7, 13, tzinfo=timezone.utc)

    order = OrderDTO(
        order_id="1",
        order_number="1",
        created_at=created_at,
        engine_updated_at=updated_at,
        source=OrderSourceDTO(),
    )

    assert order.created_at < order.engine_updated_at
