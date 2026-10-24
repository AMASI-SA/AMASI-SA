import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from test_operational_instruction_notifications import DB
from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS
from store_delivery_handover_routes import enforce_assignment_instructions
import store_delivery_driver_app_routes as driver
from store_delivery_customer_instruction_routes import make_store_delivery_customer_instruction_router, STORE_DELIVERY_INSTRUCTIONS


def instruction(**changes):
    return {"id": "n", "user_id": "owner", "order_number": "1", "status": "active",
        "action_type": "cancel_order", "scope": "order", "target_stages": ["store_courier"],
        "task_protocol": 1, "activation_state": "scheduled", "enforcement": "completion_required", **changes}


def test_assignment_creates_courier_responsibility_but_pickup_stays_blocked():
    db = DB(); db.raw[ORDER_TRACKING_INSTRUCTIONS].insert_one(instruction())
    asyncio.run(enforce_assignment_instructions(db, user_id="owner", order_number="1", actor_id="handoff"))
    with pytest.raises(HTTPException) as error:
        asyncio.run(driver.enforce_driver_instruction_gate(db, merchant_id="owner", actor_id="driver", assignment={"order_number": "1"}))
    assert error.value.status_code == 409


@pytest.mark.parametrize("row", [instruction(operational_hold=True), instruction(task_protocol=None), instruction(target_stages=["preparation"], activation_state="active", operational_hold=True)])
def test_assignment_preserves_existing_immediate_and_nontracking_stops(row):
    db = DB(); db.raw[ORDER_TRACKING_INSTRUCTIONS].insert_one(row)
    with pytest.raises(HTTPException):
        asyncio.run(enforce_assignment_instructions(db, user_id="owner", order_number="1", actor_id="handoff"))


@pytest.mark.parametrize("kind", ["general", "urgent_preparation", "delivery_instruction"])
def test_informational_notes_never_block_courier_even_legacy_enforcement(kind):
    db = DB(); db.raw[ORDER_TRACKING_INSTRUCTIONS].insert_one(instruction(action_type=kind, task_protocol=None, status="waiting_customer_service_approval"))
    asyncio.run(driver.enforce_driver_instruction_gate(db, merchant_id="owner", actor_id="driver", assignment={"order_number": "1"}))


@pytest.mark.parametrize("target", ["out_for_delivery", "delivered"])
def test_status_stop_precedes_salla_proof_and_financial_writes(target):
    db = DB(); db.raw[ORDER_TRACKING_INSTRUCTIONS].insert_one(instruction())
    db.raw.store_drivers.insert_one({"id": "d", "user_id": "owner", "account_user_id": "actor", "status": "active"})
    db.raw.store_delivery_assignments.insert_one({"id": "assignment", "user_id": "owner", "driver_id": "d", "order_number": "1", "barcode": "barcode", "active": True, "status": "assigned"})
    async def current(): return {"id": "actor", "created_by": "owner", "role": "store_driver", "_session_client": "amasi_mobile"}
    app = FastAPI(); app.include_router(driver.make_store_delivery_driver_app_router(db, current))
    with patch.object(driver, "ensure_store_delivery_driver_app_indexes", AsyncMock()), patch.object(driver, "_push_salla_delivery_status", AsyncMock()) as push:
        with TestClient(app) as client:
            response = client.post("/store-delivery/app/deliveries/status", json={"barcode": "barcode", "target_status": target})
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "customer_service_instruction_action_required"
        push.assert_not_awaited()
    assert db.raw.store_delivery_assignments.find_one({})["status"] == "assigned"
    assert db.raw.store_delivery_collections.count_documents({}) == 0


def test_linked_legacy_cards_and_ack_are_not_a_second_task_path():
    db = DB(); db.raw.store_drivers.insert_one({"id": "d", "user_id": "owner", "account_user_id": "actor", "status": "active"})
    db.raw[STORE_DELIVERY_INSTRUCTIONS].insert_one({"id": "legacy", "user_id": "owner", "driver_id": "d", "status": "active", "source_tracking_instruction_id": "n"})
    async def current(): return {"id": "actor", "created_by": "owner", "role": "store_driver", "_session_client": "amasi_mobile"}
    app = FastAPI(); app.include_router(make_store_delivery_customer_instruction_router(db, current))
    with TestClient(app) as client:
        response = client.get("/store-delivery/instructions/driver/me")
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []
        response = client.post("/store-delivery/instructions/legacy/acknowledge")
        assert response.status_code == 409, response.text
    assert not db.raw[STORE_DELIVERY_INSTRUCTIONS].find_one({}).get("acknowledged_at")


def test_receive_barcode_stop_does_not_move_delivery_or_accept_scan():
    db = DB(); db.raw[ORDER_TRACKING_INSTRUCTIONS].insert_one(instruction())
    db.raw.store_drivers.insert_one({"id": "d", "user_id": "owner", "account_user_id": "actor", "status": "active"})
    db.raw.store_delivery_assignments.insert_one({"id": "assignment", "user_id": "owner", "driver_id": "d", "order_number": "1", "barcode": "barcode", "active": True, "status": "assigned"})
    db.raw.store_delivery_driver_receive_sessions.insert_one({"id": "session", "user_id": "owner", "driver_id": "d", "status": "open", "accepted": []})
    async def current(): return {"id": "actor", "created_by": "owner", "role": "store_driver", "_session_client": "amasi_mobile"}
    app = FastAPI(); app.include_router(driver.make_store_delivery_driver_app_router(db, current))
    with patch.object(driver, "canonical_order_for_assignment", AsyncMock(return_value={})), patch.object(driver, "authoritative_outstanding_amount", return_value=0), patch.object(driver, "_push_salla_delivery_status", AsyncMock()) as push:
        with TestClient(app) as client:
            response = client.post("/store-delivery/app/deliveries/receive-sessions/session/scan", json={"barcode": "barcode"})
        assert response.status_code == 409, response.text
        push.assert_not_awaited()
    assert db.raw.store_delivery_assignments.find_one({})["status"] == "assigned"
    assert db.raw.store_delivery_driver_receive_sessions.find_one({})["accepted"] == []
