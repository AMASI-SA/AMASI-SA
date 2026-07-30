import pytest
from pydantic import ValidationError

from stock_preparation_order_routes import (
    STOCK_PREPARATION_STATUS_CANCELLED,
    STOCK_PREPARATION_STATUS_IN_PROGRESS,
    STOCK_PREPARATION_STATUS_READY,
    StockPreparationOrderCreateRequest,
    apply_received_quantities,
    next_stock_preparation_status,
    stock_preparation_order_fingerprint,
)


def _payload(**overrides):
    value = {
        "idempotency_key": "stock-preparation:test-001",
        "supplier_id": "supplier-1",
        "assigned_employee_id": "employee-1",
        "destination_warehouse_id": "warehouse-1",
        "items": [{
            "product_id": "mpv2-1",
            "quantity": 30,
            "specifications": [
                {"name": "الاسم", "value": "عبير"},
                {"name": "اللون", "value": "ذهبي"},
            ],
        }],
        "note": "تجهيز دفعة جاهزة للاحتفاظ بالمخزون",
    }
    value.update(overrides)
    return StockPreparationOrderCreateRequest(**value)


def test_stock_preparation_fingerprint_is_stable_for_spec_order():
    first = _payload()
    second = _payload(items=[{
        "product_id": "mpv2-1",
        "quantity": 30,
        "specifications": [
            {"name": " اللون ", "value": " ذهبي "},
            {"name": "الاسم", "value": " عبير "},
        ],
    }])

    assert (
        stock_preparation_order_fingerprint(first)
        == stock_preparation_order_fingerprint(second)
    )


def test_stock_preparation_fingerprint_keeps_variant_identity():
    gold = _payload(items=[{
        "product_id": "mpv2-1",
        "variant_id": "gold",
        "quantity": 30,
        "specifications": [{"name": "الاسم", "value": "عبير"}],
    }])
    silver = _payload(items=[{
        "product_id": "mpv2-1",
        "variant_id": "silver",
        "quantity": 30,
        "specifications": [{"name": "الاسم", "value": "عبير"}],
    }])

    assert (
        stock_preparation_order_fingerprint(gold)
        != stock_preparation_order_fingerprint(silver)
    )


def test_stock_preparation_requires_at_least_one_item():
    with pytest.raises(ValidationError):
        _payload(items=[])


def test_received_quantities_are_derived_per_work_item():
    result = apply_received_quantities(
        {
            "id": "stock-1",
            "items": [
                {"id": "item-1", "quantity": 30},
                {"id": "item-2", "quantity": 5},
            ],
        },
        {"item-1": 20, "item-2": 5},
    )

    assert result["requested_quantity"] == 35
    assert result["received_quantity"] == 25
    assert result["remaining_quantity"] == 10
    assert result["retention_complete"] is False
    assert result["items"][0]["remaining_quantity"] == 10
    assert result["items"][1]["remaining_quantity"] == 0


def test_received_quantities_are_capped_at_requested_amount():
    result = apply_received_quantities(
        {"items": [{"id": "item-1", "quantity": 30}]},
        {"item-1": 500},
    )

    assert result["received_quantity"] == 30
    assert result["remaining_quantity"] == 0
    assert result["retention_complete"] is True


def test_stock_preparation_uses_governed_stage_transitions():
    assert next_stock_preparation_status(
        current_status="reviewed",
        action="start_preparation",
    ) == STOCK_PREPARATION_STATUS_IN_PROGRESS
    assert next_stock_preparation_status(
        current_status="in_progress",
        action="mark_ready_for_receipt",
    ) == STOCK_PREPARATION_STATUS_READY
    assert next_stock_preparation_status(
        current_status="ready_for_receipt",
        action="return_to_preparation",
    ) == STOCK_PREPARATION_STATUS_IN_PROGRESS
    assert next_stock_preparation_status(
        current_status="reviewed",
        action="cancel",
    ) == STOCK_PREPARATION_STATUS_CANCELLED


def test_cannot_cancel_after_any_inventory_was_retained():
    with pytest.raises(
        ValueError,
        match="stock_preparation_cancel_forbidden",
    ):
        next_stock_preparation_status(
            current_status="ready_for_receipt",
            action="cancel",
            has_received_quantity=True,
        )


def test_cannot_skip_from_reviewed_to_ready_for_receipt():
    with pytest.raises(
        ValueError,
        match="stock_preparation_transition_invalid",
    ):
        next_stock_preparation_status(
            current_status="reviewed",
            action="mark_ready_for_receipt",
        )
