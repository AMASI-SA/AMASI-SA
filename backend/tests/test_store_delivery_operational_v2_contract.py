import asyncio
import inspect
from unittest.mock import AsyncMock

import pytest

import store_delivery_driver_app_routes as driver_routes
from store_delivery_driver_app_routes import (
    DELIVERY_EXCEPTION_CODES,
    DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_CANCEL,
    DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_DELAY,
    DELIVERY_EXCEPTION_CUSTOMER_UNREACHABLE,
    DRIVER_STATUS_TRANSITIONS,
    DriverReceiveScan,
    DriverStatusUpdate,
    _push_salla_delivery_status,
    _true_barcode_match,
)
from store_delivery_domain import (
    DELIVERY_STATUS_ASSIGNED,
    DELIVERY_STATUS_DELIVERED,
    DELIVERY_STATUS_OUT_FOR_DELIVERY,
)
from store_delivery_payment_evidence_routes import (
    CUSTOMER_CONVERSATION_EVIDENCE,
    DELIVERY_PROOFS,
    RECEIPTS,
)


def test_operational_v2_true_barcode_match_excludes_order_numbers():
    assert _true_barcode_match("ABC") == [
        {"barcode": "ABC"},
        {"shipping_barcode": "ABC"},
        {"tracking_number": "ABC"},
    ]


def test_operational_v2_receive_session_payload_is_barcode_only():
    payload = DriverReceiveScan(barcode="SHIP-123")
    assert payload.barcode == "SHIP-123"


def test_operational_v2_assigned_can_be_received_or_delivered_in_one_flow():
    allowed = DRIVER_STATUS_TRANSITIONS[DELIVERY_STATUS_ASSIGNED]
    assert DELIVERY_STATUS_OUT_FOR_DELIVERY in allowed
    assert DELIVERY_STATUS_DELIVERED in allowed


def test_operational_v2_exception_codes_are_internal_customer_outcomes():
    assert DELIVERY_EXCEPTION_CODES == {
        DELIVERY_EXCEPTION_CUSTOMER_UNREACHABLE,
        DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_DELAY,
        DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_CANCEL,
    }


def test_operational_v2_evidence_channels_are_distinct():
    assert len({RECEIPTS, DELIVERY_PROOFS, CUSTOMER_CONVERSATION_EVIDENCE}) == 3


def test_operational_v2_delivery_requires_independent_proof_field():
    payload = DriverStatusUpdate(
        barcode="SHIP-1",
        target_status=DELIVERY_STATUS_DELIVERED,
        payment_method="cash",
        delivery_proof_reference="proof-1",
        conversation_evidence_reference="conversation-1",
    )
    assert payload.delivery_proof_reference == "proof-1"
    assert payload.conversation_evidence_reference == "conversation-1"


def test_operational_v2_status_changes_can_bind_optional_conversation_evidence():
    source = inspect.getsource(driver_routes.make_store_delivery_driver_app_router)
    status_block = source.split('@router.post("/deliveries/status")', 1)[1]
    assert "conversation_evidence_reference" in status_block
    assert "_bind_status_conversation" in status_block
    assert "store_delivery_status_evidence_reference" in status_block


@pytest.mark.parametrize(
    ("slug", "actual"),
    [("delivering", "delivering"), ("delivered", "delivered")],
)
def test_operational_v2_salla_write_is_verified_by_readback(monkeypatch, slug, actual):
    call = AsyncMock(side_effect=[
        {"success": True},
        {"data": {"status": {"slug": actual}}},
    ])
    monkeypatch.setattr(driver_routes, "_call_salla", call)

    result = asyncio.run(_push_salla_delivery_status(
        object(),
        user_id="merchant-1",
        assignment={"order_id": "100"},
        order={"order_id": "100"},
        slug=slug,
    ))

    assert result["slug"] == slug
    assert result["verified_slug"] == actual
    assert call.await_count == 2
    first = call.await_args_list[0]
    second = call.await_args_list[1]
    assert first.args[2:4] == ("POST", "/orders/100/status")
    assert first.kwargs["json"]["slug"] == slug
    assert second.args[2:4] == ("GET", "/orders/100")


def test_operational_v2_router_exposes_simplified_courier_paths():
    source = inspect.getsource(driver_routes.make_store_delivery_driver_app_router)
    for path in (
        '/deliveries/home',
        '/deliveries/search/{query}',
        '/deliveries/report',
        '/deliveries/receive-sessions',
        '/deliveries/receive-sessions/{session_id}/scan',
        '/deliveries/receive-sessions/{session_id}/close',
        '/deliveries/exception',
        '/deliveries/status',
    ):
        assert path in source


def test_operational_v2_internal_exceptions_never_call_salla():
    source = inspect.getsource(driver_routes.make_store_delivery_driver_app_router)
    block = source.split('@router.post("/deliveries/exception")', 1)[1].split(
        '@router.post("/deliveries/status")', 1
    )[0]
    assert "_push_salla_delivery_status" not in block
    assert "_call_salla" not in block
