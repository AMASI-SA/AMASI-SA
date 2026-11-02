"""Transactional operator unassignment: no driver custody, money or historical reassignment."""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from store_delivery_reassignment_routes import (
    UnassignPayload, _assert_unassignment_safe, make_store_delivery_reassignment_router,
)
from store_delivery_handover_routes import ASSIGNMENTS, EVENTS, ORDERS
from store_courier_domain import WORKFLOWS, ASSIGNED_WAITING_PICKUP
from store_delivery_driver_app_routes import driver_receive_session_views

ACTOR = {"id": "merchant", "role": "owner"}
DENIED = {"id": "driver-user", "role": "store_driver", "created_by": "merchant"}


def _route(db):
    router = make_store_delivery_reassignment_router(db, lambda: ACTOR)
    return next(row.endpoint for row in router.routes if row.path.endswith("/{assignment_id}/unassign"))


async def _fixture():
    uri = os.environ.get("MZ2_TEST_MONGO_URI")
    if not uri:
        pytest.skip("Transaction-capable disposable Mongo configured by Store Delivery CI only")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    db = client["mezan_delivery_unassign_test_" + uuid.uuid4().hex]
    assignment = {
        "id": "assignment-A", "user_id": "merchant", "driver_id": "driver-R",
        "order_id": "salla-101", "order_number": "101",
        "active": True, "status": "assigned", "delivery_fee_snapshot": 20.0,
        "assigned_at": "2026-11-02T10:00:00+00:00",
    }
    order = {
        "user_id": "merchant", "order_id": "salla-101", "order_number": "101",
        "store_delivery_assignment_id": "assignment-A", "store_delivery_driver_id": "driver-R",
        "store_delivery_driver_name": "R", "store_delivery_status": "assigned",
        "store_delivery_fee_snapshot": 20.0,
    }
    workflow = {
        "user_id": "merchant", "order_number": "101",
        "stage": "completed", "assembly_status": "completed",
        "carrier_label_type": "store_courier", "carrier_label_ready": True,
        "store_delivery_assignment_id": "assignment-A",
        "store_courier_driver_profile_id": "driver-R",
        "store_courier_assignee_id": "store-driver:driver-R",
        "store_courier_assignee_name": "R",
        "store_courier_assignment_state": ASSIGNED_WAITING_PICKUP,
        "carrier_label_print_confirmed": True,
    }
    await db[ASSIGNMENTS].insert_one(assignment)
    await db[ORDERS].insert_one(order)
    await db[WORKFLOWS].insert_one(workflow)
    return client, db


async def _shutdown(client, db):
    await client.drop_database(db.name)
    client.close()


@pytest.mark.asyncio
async def test_operator_unassign_removes_only_active_driver_projection():
    client, db = await _fixture()
    try:
        await db["store_delivery_instructions"].insert_one({
            "user_id": "merchant", "id": "instruction-1",
            "order_id": "salla-101", "driver_id": "driver-R",
            "status": "active", "acknowledged_at": None,
            "driver_name_snapshot": "R",
        })
        result = await _route(db)(
            "assignment-A", UnassignPayload(expected_driver_id="driver-R"), user=ACTOR,
        )
        assert result["ok"] is True
        old = await db[ASSIGNMENTS].find_one({"id": "assignment-A"})
        assert old["active"] is False and old["status"] == "unassigned"
        assert old["driver_id"] == "driver-R" and old["delivery_fee_snapshot"] == 20.0
        assert await db[ASSIGNMENTS].count_documents({"driver_id": "driver-R", "active": True}) == 0
        order = await db[ORDERS].find_one({"order_number": "101"})
        assert "store_delivery_assignment_id" not in order
        assert "store_delivery_fee_snapshot" not in order
        assert order["store_delivery_status"] == "unassigned"
        workflow = await db[WORKFLOWS].find_one({"order_number": "101"})
        assert "store_courier_assignee_id" not in workflow
        assert workflow["carrier_label_ready"] is True
        assert workflow["carrier_label_print_confirmed"] is True
        assert workflow["stage"] == "completed"
        instruction = await db["store_delivery_instructions"].find_one({"id": "instruction-1"})
        assert instruction["driver_id"] is None
        assert await db[EVENTS].count_documents({"event_type": "store_delivery_unassigned"}) == 1
        with pytest.raises(HTTPException) as error:
            await _route(db)("assignment-A", UnassignPayload(expected_driver_id="driver-R"), user=ACTOR)
        assert error.value.status_code == 404
    finally:
        await _shutdown(client, db)


@pytest.mark.asyncio
async def test_reject_different_driver_without_modifying_old_assignment():
    client, db = await _fixture()
    try:
        with pytest.raises(HTTPException) as error:
            await _route(db)("assignment-A", UnassignPayload(expected_driver_id="driver-J"), user=ACTOR)
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "assignment_driver_changed"
        assert (await db[ASSIGNMENTS].find_one({"id": "assignment-A"}))["active"] is True
    finally:
        await _shutdown(client, db)


@pytest.mark.asyncio
async def test_reject_received_or_delivered_assignments_and_keep_financials():
    for status in ("out_for_delivery", "delivered"):
        client, db = await _fixture()
        try:
            await db[ASSIGNMENTS].update_one(
                {"id": "assignment-A"}, {"$set": {"status": status, "out_for_delivery_at": "2026-11-02T12:00:00+00:00"}},
            )
            with pytest.raises(HTTPException) as error:
                await _route(db)("assignment-A", UnassignPayload(expected_driver_id="driver-R"), user=ACTOR)
            assert error.value.status_code == 409
            assert (await db[ASSIGNMENTS].find_one({"id": "assignment-A"}))["active"] is True
        finally:
            await _shutdown(client, db)


@pytest.mark.asyncio
async def test_reject_any_financial_or_receipt_evidence():
    client, db = await _fixture()
    try:
        await db["store_delivery_collections"].insert_one({
            "user_id": "merchant", "assignment_id": "assignment-A", "amount": 100,
        })
        with pytest.raises(HTTPException) as error:
            await _route(db)("assignment-A", UnassignPayload(expected_driver_id="driver-R"), user=ACTOR)
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "assignment_financial_evidence_exists"
        assert (await db[ASSIGNMENTS].find_one({"id": "assignment-A"}))["active"] is True
    finally:
        await _shutdown(client, db)


@pytest.mark.asyncio
async def test_reject_acknowledged_instruction_and_missing_projection():
    client, db = await _fixture()
    try:
        await db["store_delivery_instructions"].insert_one({
            "user_id": "merchant", "id": "instruction-2",
            "order_id": "salla-101", "driver_id": "driver-R",
            "status": "active", "acknowledged_at": "2026-11-02T11:00:00Z",
        })
        with pytest.raises(HTTPException) as error:
            await _route(db)("assignment-A", UnassignPayload(expected_driver_id="driver-R"), user=ACTOR)
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "assignment_in_custody_cannot_unassign"
    finally:
        await _shutdown(client, db)


@pytest.mark.asyncio
async def test_driver_role_cannot_call_unassignment():
    client, db = await _fixture()
    try:
        with pytest.raises(HTTPException) as error:
            await _route(db)("assignment-A", UnassignPayload(expected_driver_id="driver-R"), user=DENIED)
        assert error.value.status_code == 403
        assert await db[EVENTS].count_documents({}) == 0
    finally:
        await _shutdown(client, db)


def test_pure_gate_rejects_completed_and_inconsistent_workflows():
    assignment = {"id":"a","driver_id":"d","status":"assigned","active":True}
    order = {"store_delivery_assignment_id":"a","store_delivery_driver_id":"d","store_delivery_status":"assigned"}
    workflow = {"store_delivery_assignment_id":"a","store_courier_driver_profile_id":"d",
                "store_courier_assignment_state":ASSIGNED_WAITING_PICKUP,"stage":"completed","assembly_status":"completed"}
    _assert_unassignment_safe(assignment, order, workflow, expected_driver_id="d")
    for changed in ({"store_courier_picked_up_at":"2026-11-02"}, {"stage":"delivering"},
                    {"store_courier_assignment_state":"delivering"}):
        with pytest.raises(HTTPException):
            _assert_unassignment_safe(assignment,order,{**workflow,**changed},expected_driver_id="d")


@pytest.mark.asyncio
async def test_in_transit_can_unassign_only_after_confirmed_physical_return_and_reassign_later():
    """Out-for-delivery case: physical return -> inactive former driver -> ready queue."""
    client, db = await _fixture()
    try:
        await db[ASSIGNMENTS].update_one(
            {"id": "assignment-A"},
            {"$set": {"status": "out_for_delivery", "out_for_delivery_at": "2026-11-02T12:00:00+00:00"}},
        )
        await db[ORDERS].update_one(
            {"order_number": "101"},
            {"$set": {"store_delivery_status": "out_for_delivery"}},
        )
        await db[WORKFLOWS].update_one(
            {"order_number": "101"},
            {"$set": {"stage": "delivering", "store_courier_assignment_state": "delivering",
                      "store_courier_picked_up_at": "2026-11-02T12:00:00+00:00"}},
        )
        await db["store_delivery_driver_receive_sessions"].insert_one({
            "user_id": "merchant", "driver_id": "driver-R", "id": "session-1",
            "status": "open", "accepted": [{
                "assignment_id": "assignment-A", "order_number": "101",
                "outstanding_amount": 110, "outstanding_amount_available": True,
            }], "accepted_count": 1, "collection_total": 110,
        })
        with pytest.raises(HTTPException) as error:
            await _route(db)("assignment-A", UnassignPayload(expected_driver_id="driver-R"), user=ACTOR)
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "assignment_physical_return_confirmation_required"
        assert (await db[ASSIGNMENTS].find_one({"id": "assignment-A"}))["active"] is True

        result = await _route(db)(
            "assignment-A", UnassignPayload(expected_driver_id="driver-R", physical_return_confirmed=True),
            user=ACTOR,
        )
        assert result["ok"] and result["reassignment_eligible"] and result["physical_return_confirmed"]
        previous = await db[ASSIGNMENTS].find_one({"id": "assignment-A"})
        assert previous["status"] == "unassigned" and previous["active"] is False
        assert previous["prior_assignment_status"] == "out_for_delivery"
        assert previous["delivery_fee_snapshot"] == 20.0
        order = await db[ORDERS].find_one({"order_number": "101"})
        workflow = await db[WORKFLOWS].find_one({"order_number": "101"})
        assert order["store_delivery_status"] == "unassigned"
        assert workflow["stage"] == "completed"
        assert workflow["store_courier_assignment_state"] is None if "store_courier_assignment_state" in workflow else True
        assert workflow.get("store_courier_assignee_id") is None
        assert workflow.get("carrier_label_print_confirmed") is True
        assert await db[ASSIGNMENTS].count_documents({"order_id": "salla-101", "active": True}) == 0
        views = await driver_receive_session_views(
            db, user_id="merchant", driver_id="driver-R",
            sessions=[await db["store_delivery_driver_receive_sessions"].find_one({"id": "session-1"})],
        )
        assert views[0]["accepted_count"] == 0 and views[0]["collection_total"] == 0
        # The audit source is retained and never zeroed or erased.
        stored = await db["store_delivery_driver_receive_sessions"].find_one({"id": "session-1"})
        assert stored["accepted_count"] == 1 and stored["collection_total"] == 110

        # Actual handover API must accept the SAME canonical order for a new
        # driver after the old physical custody is returned, not a synthetic
        # direct database insertion that would mask a workflow blocker.
        from store_courier_domain import store_courier_assignment_blocker
        from store_delivery_handover_routes import (
            SessionCreate, ScanIn, create_session_service, scan_shipment_service,
            confirm_session_service,
        )
        assert store_courier_assignment_blocker(workflow) is None
        await db[ORDERS].update_one(
            {"order_number": "101"}, {"$set": {"shipping_company": "مندوب الرياض"}},
        )
        await db["store_drivers"].insert_one({
            "user_id": "merchant", "id": "driver-J", "name": "مندوب جديد",
            "status": "active", "shipping_company": "مندوب الرياض",
            "coverage_mode": "shipping_company", "delivery_fee": 25.0,
        })
        session = await create_session_service(
            db, actor=ACTOR, user_id="merchant",
            payload=SessionCreate(driver_id="driver-J"),
        )
        scanned = await scan_shipment_service(
            db, actor=ACTOR, user_id="merchant",
            session_id=session["id"], payload=ScanIn(barcode="101"),
        )
        assert scanned["accepted"] is True, scanned
        confirmation = await confirm_session_service(
            db, actor=ACTOR, user_id="merchant", session_id=session["id"],
        )
        assert confirmation["assigned_count"] == 1
        assert await db[ASSIGNMENTS].count_documents(
            {"order_id": "salla-101", "active": True, "driver_id": "driver-J"},
        ) == 1
        assert (await db[ASSIGNMENTS].find_one({"id": "assignment-A"}))["delivery_fee_snapshot"] == 20.0
    finally:
        await _shutdown(client, db)


@pytest.mark.asyncio
async def test_out_for_delivery_rejects_money_and_completed_workflows_even_with_return_confirmed():
    client, db = await _fixture()
    try:
        await db[ASSIGNMENTS].update_one(
            {"id": "assignment-A"},
            {"$set": {"status": "out_for_delivery", "out_for_delivery_at": "now"}},
        )
        await db[ORDERS].update_one(
            {"order_number": "101"}, {"$set": {"store_delivery_status": "out_for_delivery"}},
        )
        await db[WORKFLOWS].update_one(
            {"order_number": "101"},
            {"$set": {"stage": "delivering", "store_courier_assignment_state": "delivering",
                      "store_courier_picked_up_at": "now"}},
        )
        await db["store_delivery_collections"].insert_one({
            "user_id": "merchant", "assignment_id": "assignment-A", "amount": 30,
        })
        with pytest.raises(HTTPException) as e:
            await _route(db)(
                "assignment-A",
                UnassignPayload(expected_driver_id="driver-R", physical_return_confirmed=True),
                user=ACTOR,
            )
        assert e.value.status_code == 409
        assert e.value.detail["code"] == "assignment_financial_evidence_exists"
        assert (await db[ASSIGNMENTS].find_one({"id": "assignment-A"}))["active"] is True
    finally:
        await _shutdown(client, db)
