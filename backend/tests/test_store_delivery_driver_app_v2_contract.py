import asyncio

import pytest
from fastapi import HTTPException

import store_delivery_driver_app_routes as module
from store_delivery_driver_app_routes import (
    DELIVERY_EXCEPTION_CODES,
    DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_CANCEL,
    DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_DELAY,
    DELIVERY_EXCEPTION_CUSTOMER_UNREACHABLE,
    _push_salla_delivery_status,
    _salla_order_id,
    _true_barcode_match,
)
from store_delivery_payment_evidence_routes import (
    CUSTOMER_CONVERSATION_EVIDENCE,
    DELIVERY_PROOFS,
)


def test_true_barcode_match_never_treats_order_number_as_barcode():
    clauses = _true_barcode_match("ABC-123")
    assert clauses == [
        {"barcode": "ABC-123"},
        {"shipping_barcode": "ABC-123"},
        {"tracking_number": "ABC-123"},
    ]
    assert all("order_number" not in row and "order_id" not in row for row in clauses)


def test_delivery_exception_contract_matches_courier_ui():
    assert DELIVERY_EXCEPTION_CODES == {
        DELIVERY_EXCEPTION_CUSTOMER_UNREACHABLE,
        DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_DELAY,
        DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_CANCEL,
    }


def test_salla_order_id_prefers_raw_direct_id():
    order = {
        "order_id": "stored-id",
        "raw_by_source": {"salla_direct": {"id": 987654321}},
    }
    assignment = {"order_id": "assignment-id"}
    assert _salla_order_id(order, assignment) == "987654321"


def test_salla_delivery_status_write_and_readback(monkeypatch):
    calls = []

    async def fake_call(db, user_id, method, path, **kwargs):
        calls.append((user_id, method, path, kwargs))
        if method == "GET":
            return {"data": {"status": {"slug": "delivering"}}}
        return {"success": True}

    monkeypatch.setattr(module, "_call_salla", fake_call)
    result = asyncio.run(_push_salla_delivery_status(
        object(),
        user_id="merchant-1",
        assignment={"order_id": "101"},
        order={"order_id": "101"},
        slug="delivering",
    ))
    assert result["verified_slug"] == "delivering"
    assert calls[0] == (
        "merchant-1",
        "POST",
        "/orders/101/status",
        {"json": {"slug": "delivering", "send_status_sms": False}},
    )
    assert calls[1][1:3] == ("GET", "/orders/101")
    assert calls[1][3]["params"] == {"format": "light"}


def test_salla_delivery_status_readback_mismatch_fails_closed(monkeypatch):
    async def fake_call(db, user_id, method, path, **kwargs):
        if method == "GET":
            return {"data": {"status": {"slug": "processing"}}}
        return {"success": True}

    monkeypatch.setattr(module, "_call_salla", fake_call)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(_push_salla_delivery_status(
            object(),
            user_id="merchant-1",
            assignment={"order_id": "101"},
            order={"order_id": "101"},
            slug="delivered",
        ))
    assert caught.value.status_code == 502
    assert caught.value.detail["code"] == "salla_delivery_status_readback_mismatch"


def test_delivery_evidence_collections_are_separate():
    assert DELIVERY_PROOFS != CUSTOMER_CONVERSATION_EVIDENCE
