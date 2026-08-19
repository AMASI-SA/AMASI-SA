from store_delivery_handover_routes import _barcode_candidates, _order_city, _order_number
from store_delivery_domain import assignment_snapshot, StoreDeliveryRuleError


def test_order_city_prefers_shipping_fields():
    order = {
        "city": "fallback",
        "shipping_address": {"city": "جدة"},
        "shipping_city": "الرياض",
    }
    assert _order_city(order) == "الرياض"


def test_order_city_supports_nested_shipping_address():
    assert _order_city({"shipping_address": {"city": "جدة"}}) == "جدة"


def test_order_number_falls_back_to_id():
    assert _order_number({"id": "abc"}) == "abc"
    assert _order_number({"order_number": "2339", "id": "abc"}) == "2339"


def test_barcode_candidates_cover_shipping_and_order_identifiers():
    rows = _barcode_candidates("ZX-10")
    assert {tuple(row.items())[0][0] for row in rows} == {
        "shipping_barcode",
        "tracking_number",
        "barcode",
        "order_number",
        "reference_id",
        "id",
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
