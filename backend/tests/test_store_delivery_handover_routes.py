from store_delivery_handover_routes import (
    ORDERS,
    _assignment_order_filter,
    _barcode_candidates,
    _order_city,
    _order_id,
    _order_number,
)
from store_delivery_reassignment_routes import (
    _assignment_status_filter,
    _delivery_duration_report,
)
from store_delivery_domain import assignment_snapshot, StoreDeliveryRuleError
from store_courier_domain import store_courier_assignment_blocker


def test_handover_reads_canonical_unified_orders_collection():
    assert ORDERS == "unified_orders"


def test_store_courier_assignment_requires_completed_printed_shipment():
    ready = {
        "carrier_label_type": "store_courier",
        "carrier_label_ready": True,
        "carrier_label_print_confirmed": True,
        "stage": "completed",
        "assembly_status": "completed",
    }
    assert store_courier_assignment_blocker(ready) is None
    assert store_courier_assignment_blocker({**ready, "carrier_label_print_confirmed": False}) == (
        "store_courier_label_not_confirmed"
    )
    assert store_courier_assignment_blocker({**ready, "assembly_status": "in_progress"}) == (
        "store_courier_order_not_completed"
    )


def test_order_city_prefers_shipping_fields():
    order = {
        "city": "fallback",
        "shipping_address": {"city": "جدة"},
        "shipping_city": "الرياض",
    }
    assert _order_city(order) == "الرياض"


def test_order_city_supports_nested_shipping_address():
    assert _order_city({"shipping_address": {"city": "جدة"}}) == "جدة"


def test_order_number_uses_canonical_order_fields():
    assert _order_number({"order_id": "abc"}) == "abc"
    assert _order_number({"order_number": "2339", "order_id": "abc"}) == "2339"


def test_order_id_never_depends_on_mongo_document_id():
    assert _order_id({"order_id": "salla-77", "order_number": "2339"}) == "salla-77"
    assert _order_id({"order_number": "2339"}) == "2339"
    assert _order_id({"id": "legacy-only"}) == ""


def test_barcode_candidates_cover_shipping_and_canonical_order_identifiers():
    rows = _barcode_candidates("ZX-10")
    assert {tuple(row.items())[0][0] for row in rows} == {
        "shipping_barcode",
        "tracking_number",
        "barcode",
        "order_number",
        "order_id",
        "reference_id",
    }


def test_assignment_snapshot_freezes_driver_fee_and_city():
    driver = {
        "id": "d1",
        "name": "سامي",
        "city": "الرياض",
        "coverage_mode": "city",
        "delivery_fee": 20,
        "status": "active",
    }
    first = assignment_snapshot(driver=driver, shipping_city="الرياض")
    driver["delivery_fee"] = 25
    second = assignment_snapshot(driver=driver, shipping_city="الرياض")
    assert first["delivery_fee_snapshot"] == 20.0
    assert second["delivery_fee_snapshot"] == 25.0


def test_assignment_order_filter_never_adds_null_identity_clause():
    assert _assignment_order_filter("merchant-1", {"order_id": "salla-1", "order_number": None}) == {
        "user_id": "merchant-1",
        "$or": [{"order_id": "salla-1"}, {"order_number": "salla-1"}],
    }


def test_city_mismatch_is_rejected_before_assignment():
    driver = {
        "id": "d1",
        "name": "سامي",
        "city": "الرياض",
        "coverage_mode": "city",
        "delivery_fee": 20,
        "status": "active",
    }
    try:
        assignment_snapshot(driver=driver, shipping_city="جدة")
    except StoreDeliveryRuleError as exc:
        assert str(exc) == "driver_city_mismatch"
    else:
        raise AssertionError("city mismatch must fail closed")


def test_current_assignment_status_covers_assigned_and_out_for_delivery():
    assert _assignment_status_filter("current") == {
        "$in": ["assigned", "out_for_delivery"]
    }


def test_delivery_duration_report_uses_assignment_to_delivered_timestamp():
    report = _delivery_duration_report([
        {
            "assigned_at": "2026-08-22T00:00:00+00:00",
            "delivered_at": "2026-08-22T01:00:00+00:00",
        },
        {
            "assigned_at": "2026-08-22T00:00:00Z",
            "delivered_at": "2026-08-22T03:00:00Z",
        },
        {
            "assigned_at": "2026-08-22T03:00:00Z",
            "delivered_at": "2026-08-22T02:00:00Z",
        },
        {"assigned_at": None, "delivered_at": "2026-08-22T02:00:00Z"},
    ])
    assert report == {
        "measured_count": 2,
        "average_delivery_seconds": 7200.0,
        "fastest_delivery_seconds": 3600.0,
        "longest_delivery_seconds": 10800.0,
    }
