"""Public receive-session regression coverage; no live provider writes."""
import asyncio
import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

import store_delivery_driver_app_routes as app_routes
from store_delivery_handover_routes import ASSIGNMENTS, ORDERS, SESSIONS
from store_delivery_driver_routes import STORE_DRIVERS
from store_courier_domain import WORKFLOWS


ACTOR = {"id": "driver-account", "role": "store_driver", "created_by": "merchant", "_session_client": "amasi_mobile"}
DRIVER = {"user_id": "merchant", "id": "driver", "account_user_id": "driver-account", "name": "الموصل", "city": "الرياض", "status": "active", "delivery_fee": 20}


async def assignment_count(db):
    return await db[ASSIGNMENTS].count_documents({})


def setup(monkeypatch, *, city="الرياض", stage="completed", existing=None, provider_fails=False):
    mongo_uri = os.environ.get("MZ2_TEST_MONGO_URI")
    if mongo_uri:
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=5000)
        client.get_io_loop = asyncio.get_running_loop
        db = client[f"test_driver_self_claim_{uuid.uuid4().hex}"]
    else:
        db = AsyncMongoMockClient().test
    if not mongo_uri:
        # mongomock 4.3 looks up the updated row through the exclusion projection,
        # losing filter fields (status/user_id). Apply exclusion after the atomic
        # operation to retain MongoDB semantics; production code is not patched.
        collection_type = type(db[ASSIGNMENTS])
        original = collection_type.find_one_and_update
        async def find_and_update(self, *args, **kwargs):
            projection = kwargs.pop("projection", None)
            result = await original(self, *args, **kwargs)
            if result and projection:
                result = {k: v for k, v in result.items() if projection.get(k, 1) != 0}
            return result
        monkeypatch.setattr(collection_type, "find_one_and_update", find_and_update)
    async def seed():
        await db[STORE_DRIVERS].insert_one(dict(DRIVER))
        await db[ORDERS].insert_one({"user_id": "merchant", "order_id": "salla-1", "order_number": "101", "shipping_barcode": "LABEL-1", "shipping_city": city, "outstanding_amount": 0})
        await db[WORKFLOWS].insert_one({"user_id": "merchant", "order_number": "101", "carrier_label_type": "store_courier", "carrier_label_ready": True, "stage": stage, "assembly_status": stage})
        if existing:
            await db[ASSIGNMENTS].insert_one({"user_id": "merchant", "id": "prior", "order_id": "salla-1", "order_number": "101", "active": True, "assigned_at": "2026-10-25T10:00:00+00:00", **existing})
    asyncio.run(seed())
    calls = []
    async def push(*args, **kwargs):
        calls.append(kwargs["slug"])
        if provider_fails:
            from fastapi import HTTPException
            raise HTTPException(502, detail={"code": "salla_delivery_status_readback_mismatch"})
        return {"slug": kwargs["slug"], "verified_slug": kwargs["slug"]}
    monkeypatch.setattr(app_routes, "_push_salla_delivery_status", push)
    app = FastAPI()
    app.include_router(app_routes.make_store_delivery_driver_app_router(db, lambda: ACTOR))
    client = TestClient(app)
    session = client.post("/store-delivery/app/deliveries/receive-sessions").json()
    assert "id" in session, session
    def scan(value="LABEL-1"):
        return client.post(f'/store-delivery/app/deliveries/receive-sessions/{session["id"]}/scan', json={"barcode": value})
    return db, scan, calls


@pytest.mark.parametrize("value", ["LABEL-1", "101"])
def test_unassigned_ready_shipment_is_claimed_and_received(monkeypatch, value):
    db, scan, calls = setup(monkeypatch)
    response = scan(value)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] is True
    assert body["delivery"]["status"] == "out_for_delivery"
    assert body["session"]["accepted_count"] == 1
    assert calls == ["delivering"]
    async def verify():
        rows = await db[ASSIGNMENTS].find({}).to_list(None)
        assert len(rows) == 1
        assert rows[0]["driver_id"] == "driver"
        assert rows[0]["delivery_fee_snapshot"] == 20
        wf = await db[WORKFLOWS].find_one({})
        assert wf["stage"] == "delivering"
        assert wf["store_courier_driver_profile_id"] == "driver"
        assert "carrier_label_print_confirmed" not in wf
        assert await db[SESSIONS].count_documents({"status": "open"}) == 0
    asyncio.run(verify())
    repeat = scan(value).json()
    assert repeat["code"] == "driver_delivery_already_received"
    assert repeat["out_for_delivery_at"]
    assert calls == ["delivering"]


def test_other_driver_is_reported_without_transfer_or_customer_data(monkeypatch):
    db, scan, calls = setup(monkeypatch, existing={"driver_id": "other", "driver_name_snapshot": "موصل آخر", "status": "out_for_delivery", "out_for_delivery_at": "2026-10-25T12:00:00+00:00", "customer_mobile": "private"})
    body = scan("101").json()
    assert body["accepted"] is False
    assert body["code"] == "driver_shipment_assigned_to_another"
    assert body["current_driver_name"] == "موصل آخر"
    assert body["current_status"] == "out_for_delivery"
    assert body["out_for_delivery_at"] == "2026-10-25T12:00:00+00:00"
    assert not {"customer_mobile", "driver_id", "assignment_id"} & body.keys()
    assert not calls
    assert asyncio.run(assignment_count(db)) == 1


@pytest.mark.parametrize("kwargs,code", [({"city": "جدة"}, "driver_city_mismatch"), ({"stage": "in_progress"}, "store_courier_order_not_completed")])
def test_not_eligible_has_no_assignment_or_provider_write(monkeypatch, kwargs, code):
    db, scan, calls = setup(monkeypatch, **kwargs)
    body = scan().json()
    assert body["accepted"] is False
    assert body["code"] == code
    assert not calls
    assert asyncio.run(assignment_count(db)) == 0


def test_provider_failure_remains_assigned_and_retryable(monkeypatch):
    db, scan, calls = setup(monkeypatch, provider_fails=True)
    response = scan()
    assert response.status_code == 502
    async def verify():
        assignment = await db[ASSIGNMENTS].find_one({})
        assert assignment["status"] == "assigned"
        assert await db[ASSIGNMENTS].count_documents({}) == 1
    asyncio.run(verify())
    async def push(*args, **kwargs):
        return {"slug": "delivering"}
    monkeypatch.setattr(app_routes, "_push_salla_delivery_status", push)
    assert scan().json()["accepted"] is True
    assert asyncio.run(assignment_count(db)) == 1


def test_self_assigned_same_driver_can_receive_by_order_number(monkeypatch):
    db, scan, calls = setup(monkeypatch, existing={"driver_id": "driver", "status": "assigned"})
    assert scan("101").json()["accepted"] is True
    assert calls == ["delivering"]
    assert asyncio.run(assignment_count(db)) == 1


def test_unknown_shipment_has_no_effect(monkeypatch):
    db, scan, calls = setup(monkeypatch)
    assert scan("missing").json()["code"] == "shipment_not_found"
    assert not calls
    assert asyncio.run(assignment_count(db)) == 0


def test_missing_courier_label_snapshot_recovers_before_self_assignment(monkeypatch):
    import fulfillment_carrier_label as labels
    db, scan, calls = setup(monkeypatch)
    asyncio.run(db[WORKFLOWS].update_one({}, {"$unset": {"carrier_label_type": "", "carrier_label_ready": ""}}))
    async def recover(db, *, user_id, order_number):
        await db[WORKFLOWS].update_one({"user_id": user_id, "order_number": order_number},
            {"$set": {"carrier_label_type": "store_courier", "carrier_label_ready": True}})
        return True
    monkeypatch.setattr(labels, "recover_store_courier_label_for_handover", recover)
    assert scan("101").json()["accepted"] is True
    assert calls == ["delivering"]
    assert asyncio.run(assignment_count(db)) == 1


def test_two_couriers_racing_cannot_overwrite_winning_order(monkeypatch):
    from fastapi import HTTPException
    from store_delivery_handover_routes import (
        SessionCreate, ScanIn, create_session_service,
        scan_shipment_service, confirm_session_service,
    )
    db, _, _ = setup(monkeypatch)
    async def race():
        await db[STORE_DRIVERS].insert_one({**DRIVER, "id": "second", "account_user_id": "second-account"})
        sessions = []
        for driver_id in ["driver", "second"]:
            session = await create_session_service(db, actor=ACTOR, user_id="merchant", payload=SessionCreate(driver_id=driver_id))
            assert (await scan_shipment_service(db, actor=ACTOR, user_id="merchant", session_id=session["id"], payload=ScanIn(barcode="LABEL-1")))["accepted"]
            sessions.append(session)
        cls = type(db[ASSIGNMENTS])
        original_find, original_update = cls.find_one, cls.update_one
        found, claiming = [], []
        both_found, both_claiming = asyncio.Event(), asyncio.Event()
        async def find(self, query, *args, **kwargs):
            result = await original_find(self, query, *args, **kwargs)
            if self.name == ASSIGNMENTS and isinstance(query.get("order_id"), dict) and "$in" in query["order_id"]:
                found.append(True)
                if len(found) == 2: both_found.set()
                await asyncio.wait_for(both_found.wait(), 2)
            return result
        async def update(self, query, changes, *args, **kwargs):
            if self.name == WORKFLOWS and "store_courier_assignee_id" in changes.get("$set", {}):
                claiming.append(True)
                if len(claiming) == 2: both_claiming.set()
                await asyncio.wait_for(both_claiming.wait(), 2)
            return await original_update(self, query, changes, *args, **kwargs)
        monkeypatch.setattr(cls, "find_one", find)
        monkeypatch.setattr(cls, "update_one", update)
        results = await asyncio.gather(*[
            confirm_session_service(db, actor=ACTOR, user_id="merchant", session_id=s["id"], physical_label_scanned=False)
            for s in sessions
        ], return_exceptions=True)
        assert len(found) == len(claiming) == 2, (found, claiming, results)
        assert sum(isinstance(r, dict) and r.get("confirmed") is True for r in results) == 1, results
        assert sum(isinstance(r, HTTPException) and r.status_code == 409 for r in results) == 1, results
        rows = await db[ASSIGNMENTS].find({}).to_list(None)
        assert len(rows) == 1
        order = await db[ORDERS].find_one({})
        workflow = await db[WORKFLOWS].find_one({})
        assert order["store_delivery_assignment_id"] == workflow["store_delivery_assignment_id"] == rows[0]["id"]
        assert order["store_delivery_driver_id"] == workflow["store_courier_driver_profile_id"] == rows[0]["driver_id"]
    asyncio.run(race())
