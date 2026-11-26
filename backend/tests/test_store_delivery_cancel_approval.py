"""Courier-requested cancellation: Salla confirmation, no courier fee or COD.

Uses the disposable Mongo replica set provisioned by Store Delivery CI. Does
not call Salla or perform any production order writes.
"""
import os
import uuid

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

import store_delivery_cancel_approval as cancellation
from store_delivery_handover_routes import ASSIGNMENTS, ORDERS, EVENTS
from store_courier_domain import WORKFLOWS
from store_delivery_reassignment_routes import make_store_delivery_reassignment_router

OWNER = {"id": "merchant", "role": "owner"}
DRIVER = {"id": "driver-account", "role": "store_driver", "created_by": "merchant"}
ASSIGNMENT_ID = "a-101"
ORDER_ID = "3155923424"
ORDER_NUMBER = "900"
REQUEST_ID = "courier-cancel-test-900"


async def scenario():
    uri = os.environ.get("MZ2_TEST_MONGO_URI")
    if not uri:
        pytest.skip("Requires disposable transaction-capable Mongo replica set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    db = client["courier_cancel_test_" + uuid.uuid4().hex]
    await db[ASSIGNMENTS].insert_one({
        "user_id": "merchant", "id": ASSIGNMENT_ID, "driver_id": "driver-1",
        "order_id": ORDER_ID, "order_number": ORDER_NUMBER,
        "status": "out_for_delivery", "active": True, "delivery_fee_snapshot": 25,
        "delivery_exception_code": "customer_requested_cancel",
        "delivery_exception_note": "العميل رفض استلام الطلب",
        "assigned_at": "2026-11-26T10:00:00Z",
    })
    await db[ORDERS].insert_one({
        "user_id": "merchant", "order_id": ORDER_ID, "order_number": ORDER_NUMBER,
        "order_status_slug": "delivering", "store_delivery_assignment_id": ASSIGNMENT_ID,
        "store_delivery_driver_id": "driver-1",
        "store_delivery_status": "out_for_delivery",
        "remaining_amount": 150, "payment_method": "cash_on_delivery",
        "store_delivery_fee_snapshot": 25,
    })
    await db[WORKFLOWS].insert_one({
        "user_id": "merchant", "order_number": ORDER_NUMBER, "stage": "delivering",
        "store_delivery_assignment_id": ASSIGNMENT_ID,
        "store_courier_driver_profile_id": "driver-1",
        "store_courier_assignment_state": "delivering",
    })
    return client, db


def endpoint(db):
    router = make_store_delivery_reassignment_router(db, lambda: OWNER)
    return next(r.endpoint for r in router.routes
                if r.path.endswith("/{assignment_id}/approve-cancellation"))


def payload():
    return cancellation.ApproveCourierCancellation(
        expected_driver_id="driver-1", request_id=REQUEST_ID,
    )


async def cleanup(client, db):
    await client.drop_database(db.name)
    client.close()


def fake_salla(*, post_behavior="ok", initial_status="delivering"):
    state = {"status": initial_status, "posts": 0}
    async def call(db, owner, method, path, **kwargs):
        assert owner == "merchant" and ORDER_ID in path
        if method == "POST":
            state["posts"] += 1
            assert kwargs["single_post_attempt"] is True
            assert kwargs["json"]["slug"] == "canceled"
            if post_behavior == "timeout_after_commit":
                state["status"] = "canceled"
                raise TimeoutError("unknown result")
            if post_behavior == "no_confirm":
                return {"success": True}
            state["status"] = "canceled"
            return {"success": True}
        return {"data": {
            "id": ORDER_ID, "reference_id": ORDER_NUMBER,
            "status": {"slug": state["status"]},
        }}
    return state, call


@pytest.mark.asyncio
async def test_cancel_is_zero_driver_fee_and_cod_and_keeps_audit(monkeypatch):
    monkeypatch.setenv("AMASI_COURIER_CANCELLATION_APPROVAL_ENABLED", "1")
    client, db = await scenario()
    try:
        state, call = fake_salla()
        monkeypatch.setattr(cancellation, "_call_salla", call)
        approve = endpoint(db)
        first = await approve(ASSIGNMENT_ID, payload(), user=OWNER)
        assert first["ok"] is True and first["status"] == "completed"
        assert first["courier_fee_due"] == first["courier_cod_due"] == 0
        row = await db[ASSIGNMENTS].find_one({"id": ASSIGNMENT_ID})
        assert row["active"] is False and row["status"] == "canceled"
        assert row["physical_return_pending"] is True
        assert row["delivery_fee_snapshot"] == 25  # historical snapshot preserved
        assert row["delivery_exception_note"] == "العميل رفض استلام الطلب"
        order = await db[ORDERS].find_one({"order_number": ORDER_NUMBER})
        assert order["store_delivery_status"] == "canceled"
        assert order["store_delivery_courier_fee_due"] == 0
        assert order["store_delivery_courier_cod_due"] == 0
        assert order["remaining_amount"] == 150  # don't fabricate a cash receipt
        assert await db["store_delivery_driver_earnings"].count_documents({}) == 0
        assert await db["store_delivery_collections"].count_documents({}) == 0
        assert await db[EVENTS].count_documents({
            "event_type": "store_delivery_customer_cancellation_approved",
        }) == 1
        second = await approve(ASSIGNMENT_ID, payload(), user=OWNER)
        assert second["ok"] is True and state["posts"] == 1
        assert await db[EVENTS].count_documents({
            "event_type": "store_delivery_customer_cancellation_approved",
        }) == 1
    finally:
        await cleanup(client, db)


@pytest.mark.asyncio
async def test_ambiguous_post_does_not_auto_post_twice(monkeypatch):
    monkeypatch.setenv("AMASI_COURIER_CANCELLATION_APPROVAL_ENABLED", "1")
    client, db = await scenario()
    try:
        state, call = fake_salla(post_behavior="timeout_after_commit")
        monkeypatch.setattr(cancellation, "_call_salla", call)
        approve = endpoint(db)
        pending = await approve(ASSIGNMENT_ID, payload(), user=OWNER)
        assert pending["status"] == "awaiting_salla_confirmation"
        assert await db[ASSIGNMENTS].count_documents({
            "id": ASSIGNMENT_ID, "active": True, "status": "cancel_syncing",
        }) == 1
        done = await approve(ASSIGNMENT_ID, payload(), user=OWNER)
        assert done["status"] == "completed"
        assert state["posts"] == 1
    finally:
        await cleanup(client, db)


@pytest.mark.asyncio
async def test_unconfirmed_salla_never_removes_assignment_or_posts_money(monkeypatch):
    monkeypatch.setenv("AMASI_COURIER_CANCELLATION_APPROVAL_ENABLED", "1")
    client, db = await scenario()
    try:
        state, call = fake_salla(post_behavior="no_confirm")
        monkeypatch.setattr(cancellation, "_call_salla", call)
        approve = endpoint(db)
        response = await approve(ASSIGNMENT_ID, payload(), user=OWNER)
        assert response["status"] == "awaiting_salla_confirmation"
        assert await db[ASSIGNMENTS].count_documents({
            "id": ASSIGNMENT_ID, "active": True, "status": "cancel_syncing",
        }) == 1
        await approve(ASSIGNMENT_ID, payload(), user=OWNER)
        assert state["posts"] == 1
        assert await db[EVENTS].count_documents({
            "event_type": "store_delivery_customer_cancellation_approved",
        }) == 0
        assert await db["store_delivery_driver_earnings"].count_documents({}) == 0
    finally:
        await cleanup(client, db)


@pytest.mark.asyncio
async def test_money_proof_blocks_salla_before_post(monkeypatch):
    monkeypatch.setenv("AMASI_COURIER_CANCELLATION_APPROVAL_ENABLED", "1")
    client, db = await scenario()
    try:
        await db["store_delivery_driver_earnings"].insert_one({
            "user_id": "merchant", "assignment_id": ASSIGNMENT_ID, "amount": 25,
        })
        state, call = fake_salla()
        monkeypatch.setattr(cancellation, "_call_salla", call)
        with pytest.raises(HTTPException) as exc:
            await endpoint(db)(ASSIGNMENT_ID, payload(), user=OWNER)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "cancel_financial_evidence_requires_review"
        assert state["posts"] == 0
        assert await db[ASSIGNMENTS].count_documents({
            "id": ASSIGNMENT_ID, "active": True, "status": "out_for_delivery",
        }) == 1
    finally:
        await cleanup(client, db)


@pytest.mark.asyncio
async def test_requires_driver_request_and_operator_permission(monkeypatch):
    monkeypatch.setenv("AMASI_COURIER_CANCELLATION_APPROVAL_ENABLED", "1")
    client, db = await scenario()
    try:
        await db[ASSIGNMENTS].update_one(
            {"id": ASSIGNMENT_ID}, {"$unset": {"delivery_exception_code": ""}},
        )
        with pytest.raises(HTTPException) as exc:
            await endpoint(db)(ASSIGNMENT_ID, payload(), user=OWNER)
        assert exc.value.detail["code"] == "cancel_driver_request_missing"
        with pytest.raises(HTTPException) as denied:
            await endpoint(db)(ASSIGNMENT_ID, payload(), user=DRIVER)
        assert denied.value.status_code == 403
    finally:
        await cleanup(client, db)


@pytest.mark.asyncio
async def test_release_gate_defaults_closed(monkeypatch):
    monkeypatch.delenv("AMASI_COURIER_CANCELLATION_APPROVAL_ENABLED", raising=False)
    client, db = await scenario()
    try:
        with pytest.raises(HTTPException) as err:
            await endpoint(db)(ASSIGNMENT_ID, payload(), user=OWNER)
        assert err.value.detail["code"] == "cancel_approval_release_gate_closed"
    finally:
        await cleanup(client, db)


@pytest.mark.asyncio
async def test_pending_cancellation_blocks_driver_delivery_and_exception_replacement():
    """A courier cannot accrue delivery income or erase a pending customer refusal."""
    from store_delivery_driver_app_routes import (
        DriverDeliveryException, DriverStatusUpdate, MOBILE_APP_CLIENT,
        make_store_delivery_driver_app_router,
    )

    client, db = await scenario()
    try:
        await db["store_drivers"].insert_one({
            "user_id": "merchant", "id": "driver-1",
            "account_user_id": "driver-account", "status": "active",
        })
        actor = {
            "id": "driver-account", "role": "store_driver",
            "created_by": "merchant", "_session_client": MOBILE_APP_CLIENT,
        }
        router = make_store_delivery_driver_app_router(db, lambda: actor)
        status = next(
            row.endpoint for row in router.routes
            if row.path.endswith("/deliveries/status")
        )
        exception = next(
            row.endpoint for row in router.routes
            if row.path.endswith("/deliveries/exception")
        )
        with pytest.raises(HTTPException) as status_error:
            await status(
                DriverStatusUpdate(
                    barcode=ORDER_NUMBER, target_status="delivered",
                ),
                user=actor,
            )
        assert status_error.value.detail["code"] == "driver_cancellation_pending_staff_approval"
        with pytest.raises(HTTPException) as exception_error:
            await exception(
                DriverDeliveryException(
                    barcode=ORDER_NUMBER,
                    exception_code="customer_unreachable",
                ),
                user=actor,
            )
        assert exception_error.value.detail["code"] == "driver_cancellation_pending_staff_approval"
        assert await db["store_delivery_collections"].count_documents({}) == 0
        assert await db["store_delivery_driver_earnings"].count_documents({}) == 0
    finally:
        await cleanup(client, db)
