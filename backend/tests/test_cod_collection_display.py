"""COD amount shown in review, store-courier labels and driver collection.

All fixtures are synthetic; no network or business records are touched.
"""
from copy import deepcopy

import pytest

import fulfillment_v2_routes as fulfillment
from cod_collection import cod_expected_due
from order_engine.mapper import map_salla_order
from order_engine.shipping_label_service import _store_courier_print_data
from salla_integration.sync import _salla_order_to_doc
from store_delivery_payment_evidence_routes import authoritative_outstanding_amount


def cod_order(*, total=180.52, paid=0, remaining=None, method="cod", status=None):
    return {
        "id": 12345,
        "reference_id": "90012345",
        "date": "2026-09-27T02:17:04+03:00",
        "status": {"slug": "under_review", "name": "بإنتظار المراجعة"},
        "payment_method": method,
        "payment": {"status": status} if status else {},
        "amounts": {"total": {"amount": total, "currency": "SAR"}},
        "payment_actions": {
            "remaining_action": {
                "paid_amount": {"amount": paid, "currency": "SAR"},
                "remaining_amount": remaining,
                "has_remaining_amount": False,
            },
        },
        "customer": {"name": "عميل اختبار"},
        "items": [],
    }


def test_salla_cod_null_checkout_balance_is_not_zero_cash_due():
    raw = cod_order()
    assert cod_expected_due(
        raw["payment_method"],
        total=raw["amounts"]["total"],
        paid=raw["payment_actions"]["remaining_action"]["paid_amount"],
        remaining=raw["payment_actions"]["remaining_action"]["remaining_amount"],
    ) == 180.52
    doc = _salla_order_to_doc(raw)
    assert doc["remaining_amount"] == 180.52
    assert doc["has_remaining_amount"] is True
    assert doc["payment_collection_status"] == "unpaid"
    dto = map_salla_order(raw)
    assert dto.payment.remaining_amount == 180.52
    assert dto.payment.has_remaining_amount is True
    assert dto.payment.collection_status == "unpaid"


@pytest.mark.parametrize("paid, expected", [(50, 130.52), (180.52, 0)])
def test_partial_and_fully_paid_cod(paid, expected):
    raw = cod_order(paid=paid)
    assert _salla_order_to_doc(raw)["remaining_amount"] == expected
    assert map_salla_order(raw).payment.remaining_amount == expected


def test_explicit_positive_remaining_and_prepaid_zero_are_preserved():
    assert _salla_order_to_doc(cod_order(remaining={"amount": 25, "currency": "SAR"}))["remaining_amount"] == 25
    assert _salla_order_to_doc(cod_order(method="mada"))["remaining_amount"] == 0
    assert cod_expected_due(
        "cod", total={"amount": 180.52, "currency": "SAR"},
        paid={"amount": 0, "currency": "SAR"},
        remaining={"amount": 0, "currency": "QAR"},
    ) is None
    assert cod_expected_due("cod", total=180.52, paid=None) is None
    assert cod_expected_due("cod", total=180.52, paid=0, payment_status="paid") is None


def test_legacy_zero_canonical_order_uses_cod_due_at_driver_handover():
    stale = {
        "payment_method": "cod", "payment_status": "pending",
        "payment_collection_status": "unknown",
        "total_amount": 180.52, "paid_amount": 0,
        "remaining_amount": 0, "has_remaining_amount": False,
    }
    assert authoritative_outstanding_amount(stale) == 180.52
    assert authoritative_outstanding_amount({**stale, "payment_method": "mada"}) == 0
    assert authoritative_outstanding_amount({**stale, "payment_status": "paid"}) == 0
    assert authoritative_outstanding_amount({**stale, "paid_amount": 50}) == 130.52


def test_store_courier_label_uses_total_minus_paid_not_cod_fee(monkeypatch):
    monkeypatch.setattr(
        "order_engine.shipping_label_service._qr_data_uri",
        lambda value: "data:image/svg+xml;base64,QR",
    )
    raw = cod_order()
    shipment = {
        "payment_method": "cod",
        "courier_name": "مندوب المتجر",
        "cash_on_delivery": {"amount": 4.63, "currency": "SAR"},
        "total": {"amount": 180.52, "currency": "SAR"},
        "ship_to": {}, "packages": [],
    }
    print_data = _store_courier_print_data("90012345", raw, shipment, {})
    assert print_data["remaining_amount"] == {"amount": 180.52, "currency": "SAR"}
    assert print_data["payment_method"] == "cod"
    assert print_data["paid_amount"] == {"amount": 0, "currency": "SAR"}
    print_data = _store_courier_print_data(
        "90012345", cod_order(paid=50), shipment, {},
    )
    assert print_data["remaining_amount"]["amount"] == 130.52
    print_data = _store_courier_print_data(
        "90012345", cod_order(method="mada"), shipment, {},
    )
    assert print_data["remaining_amount"]["amount"] == 0


@pytest.mark.asyncio
async def test_existing_label_snapshot_is_corrected_read_only(monkeypatch):
    order = map_salla_order(cod_order())
    old_workflow = {
        "order_number": "90012345",
        "carrier_label_type": "store_courier",
        "carrier_label_print_data": {
            "order_number": "90012345",
            "qr_code": "data:image/svg+xml;base64,QR",
            "remaining_amount": {"amount": 0, "currency": "SAR"},
        },
    }
    original = deepcopy(old_workflow)

    async def local_order(*args, **kwargs):
        return order

    monkeypatch.setattr(fulfillment, "get_order", local_order)
    result = await fulfillment._order_view(
        None, user_id="synthetic", workflow=old_workflow,
    )
    assert result["carrier_label_print_data"]["remaining_amount"]["amount"] == 180.52
    assert old_workflow == original
