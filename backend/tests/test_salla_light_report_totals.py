"""Light list payloads must survive ingestion and report currency projection."""
import pytest
from mongomock import MongoClient

from dashboard_eligibility_filter import order_is_eligible
from order_currency import (
    SALLA_RAW_CURRENCY_PROJECTION, hydrate_order_currency_fields, order_total_sar,
)
from salla_integration.sync import _salla_order_to_doc


@pytest.mark.parametrize("total", [197.72, 326.26, 0])
def test_light_total_is_canonical_native_amount(total):
    doc = _salla_order_to_doc({"id": 1, "reference_id": "test-1",
                              "total": {"amount": total, "currency": "SAR"}})
    assert doc["total_amount"] == total
    assert doc["original_total_amount"] == total


def test_full_explicit_zero_wins_over_light_total():
    doc = _salla_order_to_doc({"id": 1, "reference_id": "test-1",
                              "amounts": {"total": {"amount": 0, "currency": "SAR"}},
                              "total": {"amount": 197.72, "currency": "SAR"}})
    assert doc["total_amount"] == 0


def test_existing_light_orders_survive_real_report_projection_and_filter():
    db = MongoClient().db
    rows = []
    for number, amount in [("test-1", 197.72), ("test-2", 326.26)]:
        row = {"order_number": number, "total_amount": 0,
               "original_total_amount": amount, "total_amount_sar": amount,
               "currency": "SAR", "original_currency": "SAR",
               "currency_conversion_status": "native_sar"}
        rows.append(row)
        db.orders.insert_one({**row, "raw_by_source": {"salla_direct": {
            "total": {"amount": amount, "currency": "SAR"}, "currency": "SAR"}}})
    projected = list(db.orders.find({}, SALLA_RAW_CURRENCY_PROJECTION))
    hydrate_order_currency_fields(rows, projected)
    eligible = [row for row in rows if order_is_eligible(row)]
    assert len(eligible) == 2
    assert round(sum(order_total_sar(row) for row in eligible), 2) == 523.98


def test_partial_raw_currency_does_not_erase_promoted_native_total():
    row = {"order_number": "test-1", "total_amount": 0,
           "original_total_amount": 197.72, "total_amount_sar": 197.72,
           "currency": "SAR", "currency_conversion_status": "native_sar"}
    hydrate_order_currency_fields([row], [{"order_number": "test-1",
        "raw_by_source": {"salla_direct": {"currency": "SAR"}}}])
    assert order_total_sar(row) == 197.72
