"""Contract tests for the Mezan Order Item identity."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from order_item_engine.models import (
    OrderItemIdentityDTO,
    OrderItemOptionDTO,
    OrderItemSourceDTO,
)


def make_identity(**overrides):
    payload = {
        "order_item_id": "salla:272008653:50001",
        "order_id": "1065000001",
        "order_number": "272008653",
        "order_created_at": datetime(
            2026,
            7,
            13,
            10,
            30,
            tzinfo=timezone.utc,
        ),
        "line_index": 0,
        "source": OrderItemSourceDTO(
            source_order_id="1065000001",
            source_order_item_id="50001",
            source_product_id="11912",
            source_variant_id="70001",
        ),
        "product_id": "11912",
        "variant_id": "70001",
        "sku": "AMS11912-GOLD-18",
        "name": "اسوارة الفراشة",
        "quantity": 1,
        "color": "ذهبي",
        "size": "18",
        "options": [
            OrderItemOptionDTO(
                name="اللون",
                value="ذهبي",
            ),
            OrderItemOptionDTO(
                name="المقاس",
                value="18",
            ),
        ],
        "currency": "SAR",
        "unit_price": 100,
        "total": 115,
    }
    payload.update(overrides)
    return OrderItemIdentityDTO(**payload)


def test_accepts_complete_identity_contract():
    item = make_identity()

    assert item.schema_version == 1
    assert item.order_item_id == "salla:272008653:50001"
    assert item.order_number == "272008653"
    assert item.source.provider == "salla"
    assert item.color == "ذهبي"
    assert item.size == "18"


def test_two_lines_of_same_product_remain_separate_identities():
    first = make_identity(
        order_item_id="salla:272008653:50001",
        line_index=0,
    )
    second = make_identity(
        order_item_id="salla:272008653:50002",
        line_index=1,
    )

    assert first.product_id == second.product_id
    assert first.order_item_id != second.order_item_id


def test_order_item_id_is_required():
    with pytest.raises(ValidationError):
        make_identity(order_item_id="")


def test_quantity_must_be_positive():
    with pytest.raises(ValidationError):
        make_identity(quantity=0)


def test_line_index_cannot_be_negative():
    with pytest.raises(ValidationError):
        make_identity(line_index=-1)


@pytest.mark.parametrize(
    "operational_field",
    [
        "supplier_id",
        "supplier_name",
        "preparation_status",
        "preparation_employee_id",
        "receiving_employee_id",
        "purchase_batch_id",
        "availability_status",
        "inventory_status",
        "marketing_cost",
        "ai_notes",
    ],
)
def test_operational_engine_fields_are_forbidden(
    operational_field,
):
    with pytest.raises(ValidationError):
        make_identity(**{operational_field: "forbidden"})


def test_database_specific_fields_are_forbidden():
    with pytest.raises(ValidationError):
        make_identity(
            raw_by_source={},
            mongo_id="abc",
        )
