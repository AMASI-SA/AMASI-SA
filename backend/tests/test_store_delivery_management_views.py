from store_delivery_driver_routes import _change_history_payload, _driver_delivery_counts
from store_delivery_reassignment_routes import (
    _assignment_status_filter,
    _assignment_totals,
    _enrich_assignment,
    _is_cash_on_delivery,
)


def test_driver_list_counts_current_deliveries_without_counting_delivered():
    counts = _driver_delivery_counts([
        {"driver_id": "d1", "status": "assigned"},
        {"driver_id": "d1", "status": "out_for_delivery"},
        {"driver_id": "d1", "status": "delivered"},
        {"driver_id": "d2", "status": "assigned"},
    ])
    assert counts["d1"] == {
        "assigned_count": 1,
        "out_for_delivery_count": 1,
        "current_delivery_count": 2,
        "delivered_count": 1,
    }
    assert counts["d2"]["current_delivery_count"] == 1


def test_driver_update_event_preserves_before_and_after_values():
    history = _change_history_payload(
        {"name": "أحمد", "phone": "050", "delivery_fee": 15.0, "version": 1},
        {"phone": "051", "delivery_fee": 20.0, "version": 2, "updated_at": "now"},
    )
    assert history == {
        "changed_fields": ["delivery_fee", "phone"],
        "before": {"delivery_fee": 15.0, "phone": "050"},
        "after": {"delivery_fee": 20.0, "phone": "051"},
    }


def test_current_status_is_assigned_plus_out_for_delivery():
    assert _assignment_status_filter("current") == {"$in": ["assigned", "out_for_delivery"]}
    assert _assignment_status_filter("delivered") == "delivered"


def test_assignment_totals_use_snapshotted_fee_and_cod_outstanding_only():
    assignments = [
        {"order_id": "o1", "delivery_fee_snapshot": 10},
        {"order_id": "o2", "delivery_fee_snapshot": 15},
    ]
    orders = {
        "o1": {"order_id": "o1", "payment_method": "الدفع عند الاستلام", "remaining_amount": 120},
        "o2": {"order_id": "o2", "payment_method": "mada", "remaining_amount": 50},
    }
    assert _assignment_totals(assignments, orders) == {
        "cod_total": 120.0,
        "delivery_fee_total": 25.0,
        "cod_unavailable_count": 0,
    }


def test_assignment_item_exposes_cod_and_customer_details():
    order = {
        "customer_name": "سارة",
        "customer_mobile": "0500000000",
        "shipping_city": "الرياض",
        "payment_method": "cash_on_delivery",
        "remaining_amount": 80,
    }
    row = _enrich_assignment({"id": "a1", "order_id": "o1"}, order)
    assert _is_cash_on_delivery(order) is True
    assert row["customer_name"] == "سارة"
    assert row["is_cash_on_delivery"] is True
    assert row["cod_outstanding"] == 80.0
