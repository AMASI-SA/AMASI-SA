import inspect
from unittest.mock import AsyncMock

import pytest

import store_delivery_driver_app_routes as driver_routes
from store_delivery_driver_app_routes import (
    DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_CANCEL,
    DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_DELAY,
    DELIVERY_EXCEPTION_CUSTOMER_UNREACHABLE,
    DriverDeliveryException,
    DriverStatusUpdate,
    SALLA_STATUS_SLUGS,
    _sync_salla_delivery_status,
)
from store_delivery_domain import (
    DELIVERY_STATUS_DELIVERED,
    DELIVERY_STATUS_OUT_FOR_DELIVERY,
)
from store_delivery_payment_evidence_routes import (
    CUSTOMER_CONVERSATION_EVIDENCE,
    DELIVERY_PROOFS,
    RECEIPTS,
)


def test_build7_status_contract_has_required_delivery_proof():
    payload = DriverStatusUpdate(
        barcode="SHIP-1",
        target_status=DELIVERY_STATUS_DELIVERED,
        payment_method="cash",
        delivery_proof_reference="proof-token",
    )
    assert payload.delivery_proof_reference == "proof-token"


def test_build7_operational_exceptions_allow_optional_note_and_image():
    for code in (
        DELIVERY_EXCEPTION_CUSTOMER_UNREACHABLE,
        DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_DELAY,
        DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_CANCEL,
    ):
        payload = DriverDeliveryException(barcode="SHIP-1", exception_code=code)
        assert payload.note is None
        assert payload.evidence_reference is None


def test_build7_evidence_kinds_are_separate():
    assert len({RECEIPTS, DELIVERY_PROOFS, CUSTOMER_CONVERSATION_EVIDENCE}) == 3


def test_build7_salla_status_slugs():
    assert SALLA_STATUS_SLUGS == {
        DELIVERY_STATUS_OUT_FOR_DELIVERY: "delivering",
        DELIVERY_STATUS_DELIVERED: "delivered",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "expected_slug"),
    [
        (DELIVERY_STATUS_OUT_FOR_DELIVERY, "delivering"),
        (DELIVERY_STATUS_DELIVERED, "delivered"),
    ],
)
async def test_build7_salla_status_write_uses_official_order_status_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    expected_slug: str,
):
    call = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(driver_routes, "call_salla", call)

    result = await _sync_salla_delivery_status(
        object(),
        user_id="merchant-1",
        order_id="123456",
        target_status=target,
    )

    assert result == {"success": True}
    call.assert_awaited_once_with(
        object(),
        "merchant-1",
        "POST",
        "/orders/123456/status",
        json={"slug": expected_slug, "send_status_sms": False},
    )


def test_build7_exception_path_is_mezan_only_not_salla_mutation():
    source = inspect.getsource(driver_routes.make_store_delivery_driver_app_router)
    exception_block = source.split('@router.post("/deliveries/exception")', 1)[1].split(
        '@router.post("/deliveries/status")', 1
    )[0]
    assert "_sync_salla_delivery_status" not in exception_block
    assert "call_salla" not in exception_block
