import pytest
from pydantic import ValidationError

from product_inventory_receipt_routes import (
    PurchaseInventoryReceiptRequest,
    _public_receipt,
    _receipt_fingerprint,
    build_inventory_health_rows,
)


def _payload(**overrides):
    value = {
        "idempotency_key": "inventory-receipt:test-001",
        "purchase_invoice_id": "invoice-1",
        "purchase_invoice_line_id": "line-1",
        "product_id": "mpv2-1",
        "location_id": "location-1",
        "scanned_barcode": " RUH-WH001-1-001 ",
        "quantity": 2,
        "preparation_state": "ready_complete",
        "specifications": [
            {"name": "الاسم", "value": "عبير"},
            {"name": "اللون", "value": "ذهبي"},
        ],
    }
    value.update(overrides)
    return PurchaseInventoryReceiptRequest(**value)


def test_receipt_fingerprint_is_stable_for_specification_order():
    first = _payload()
    second = _payload(specifications=[
        {"name": " اللون ", "value": " ذهبي "},
        {"name": "الاسم", "value": " عبير "},
    ])

    assert _receipt_fingerprint(first) == _receipt_fingerprint(second)


def test_receipt_fingerprint_keeps_variant_identity():
    assert _receipt_fingerprint(
        _payload(variant_id="gold")
    ) != _receipt_fingerprint(
        _payload(variant_id="silver")
    )


def test_receipt_rejects_unknown_preparation_state():
    with pytest.raises(ValidationError):
        _payload(preparation_state="maybe_ready")


def test_public_receipt_hides_internal_tenant_and_fingerprint():
    result = _public_receipt({
        "id": "receipt-1",
        "user_id": "merchant-1",
        "payload_fingerprint": "secret-internal-value",
        "_id": "mongo-id",
        "status": "posted",
    })

    assert result == {"id": "receipt-1", "status": "posted"}


def test_inventory_health_uses_available_after_order_commitments():
    result = build_inventory_health_rows(
        products=[{
            "mezan_product_id": "mpv2-1",
            "salla_product_id": "1",
            "name": "سلسال عبير",
            "sku": "NAME-1",
        }],
        profiles=[{
            "salla_product_id": "1",
            "inventory_policy": "branch_stock_required",
            "stockout_policy": "allow_preorder",
            "low_stock_threshold": 3,
        }],
        stock_rows=[{
            "identifiers": {"1", "mpv2-1", "NAME-1"},
            "on_hand": 10,
            "remaining": 2,
            "reserved_quantity": 8,
        }],
    )

    assert result[0]["on_hand_quantity"] == 10
    assert result[0]["reserved_quantity"] == 8
    assert result[0]["available_quantity"] == 2
    assert result[0]["health_status"] == "low_stock"


def test_zero_available_uses_product_preorder_policy():
    result = build_inventory_health_rows(
        products=[{
            "mezan_product_id": "mpv2-1",
            "salla_product_id": "1",
            "name": "سلسال الاسم",
            "sku": "NAME-1",
        }],
        profiles=[{
            "salla_product_id": "1",
            "inventory_policy": "branch_stock_required",
            "stockout_policy": "allow_preorder",
            "low_stock_threshold": 3,
        }],
        stock_rows=[],
    )

    assert result[0]["health_status"] == "preorder"
    assert result[0]["catalog_action_required"] == "show_preorder"
    assert result[0]["external_catalog_write_performed"] is False
