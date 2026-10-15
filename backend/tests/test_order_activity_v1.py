from pathlib import Path

from order_activity_v1 import (
    normalize_history_row,
    normalize_transaction_row,
)


def test_product_add_history_normalizes_without_touching_order_state():
    event = normalize_history_row(
        {
            "id": 1001,
            "action": "تم إضافة منتج إلى الطلب",
            "created_at": {
                "date": "2026-08-08 16:26:16",
                "timezone": "Asia/Riyadh",
            },
            "employee": {
                "name": "موظف خدمة العملاء",
            },
        },
        order_number="276936126",
        provider_order_id="1090700313",
    )

    assert event["order_number"] == "276936126"
    assert event["provider_order_id"] == "1090700313"
    assert event["event_type"] == "item_added"
    assert event["source"] == "salla_history"
    assert event["fingerprint"]


def test_two_payment_transactions_remain_two_independent_facts():
    first = normalize_transaction_row(
        {
            "id": 1,
            "amount": {
                "amount": 239.84,
                "currency": "SAR",
            },
            "payment_method": "credit_card",
            "status": "paid",
            "created_at": "2026-08-08 13:05:55",
        },
        order_number="276936126",
        provider_order_id="1090700313",
    )

    second = normalize_transaction_row(
        {
            "id": 2,
            "amount": {
                "amount": 940.90,
                "currency": "SAR",
            },
            "payment_method": "credit_card",
            "status": "paid",
            "created_at": "2026-08-08 17:00:00",
        },
        order_number="276936126",
        provider_order_id="1090700313",
    )

    assert first["fingerprint"] != second["fingerprint"]
    assert round(first["amount"] + second["amount"], 2) == 1180.74


def test_new_module_has_no_qoyod_or_canonical_order_write():
    source = Path(__file__).parents[1] / "order_activity_v1.py"
    text = source.read_text(encoding="utf-8")

    assert "unified_orders.update_" not in text
    assert "unified_orders.insert_" not in text
    assert "unified_orders.replace_" not in text
    lowered = text.lower()
    assert "db.qoyod" not in lowered
    assert "integrations.qoyod" not in lowered
