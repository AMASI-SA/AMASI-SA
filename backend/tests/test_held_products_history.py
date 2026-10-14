"""History is retained after release, without granting access to other staff."""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from held_products_history import make_held_products_history_router, hold_product_snapshot


@pytest.fixture
def history():
    db = AsyncMongoMockClient().db
    user = {"id": "alice"}
    contexts = {
        "alice": {"actor_id": "alice", "merchant_id": "shop", "permissions": {"preparation.assigned.stop"}},
        "bob": {"actor_id": "bob", "merchant_id": "shop", "permissions": {"preparation.assigned.stop"}},
        "owner": {"actor_id": "shop", "merchant_id": "shop", "is_owner": True},
        "outsider": {"actor_id": "alice", "merchant_id": "other", "permissions": {"preparation.assigned.stop"}},
        "denied": {"actor_id": "denied", "merchant_id": "shop", "permissions": set()},
    }
    async def context(_db, actor):
        return contexts[actor["id"]]
    app = FastAPI()
    app.include_router(make_held_products_history_router(db, lambda: user, context), prefix="/api/fulfillment-experiments-v1")
    return db, user, TestClient(app)


def piece(pid, employee):
    return {"piece_id": pid, "order_number": "123", "order_item_id": pid,
            "product_name": "Test product", "responsible_employee_id": employee,
            "supplier_id": "supplier", "supplier_name": "Test supplier",
            "product_options_snapshot": {"size": "M"}, "user_id": "shop"}


async def seed(db, hid="h1", **extra):
    row = {"id": hid, "user_id": "shop", "order_number": "123", "status": "active",
           "note": "Customer requested edit", "stop_type": "edit", "scope": "order",
           "piece_ids": ["p1", "p2"], "employee_ids": ["alice", "bob"],
           "created_at": datetime(2026, 10, 14, tzinfo=timezone.utc),
           "product_snapshots": [hold_product_snapshot(piece("p1", "alice")),
                                 hold_product_snapshot(piece("p2", "bob"))], **extra}
    await db.mezan_fulfillment_holds_v1.insert_one(row)


@pytest.mark.asyncio
async def test_release_remains_in_default_history_after_piece_removed(history):
    db, user, client = history
    await seed(db)
    path = "/api/fulfillment-experiments-v1/holds/history"
    first = client.get(path).json()
    assert first["total"] == 1
    assert [p["piece_id"] for p in first["items"][0]["products"]] == ["p1"]
    await db.mezan_fulfillment_holds_v1.update_one({"id": "h1"}, {"$set": {
        "status": "released", "released_at": datetime.now(timezone.utc),
        "released_by_name": "Customer service", "release_note": "Edit cancelled"}})
    result = client.get(path).json()
    entry = result["items"][0]
    assert result["total"] == 1 and entry["status"] == "released"
    assert entry["release_note"] == "Edit cancelled"
    assert entry["products"][0]["supplier_name"] == "Test supplier"
    assert entry["products"][0]["product_options_snapshot"] == {"size": "M"}
    assert client.get(path, params={"status": "active"}).json()["total"] == 0
    assert client.get(path, params={"status": "released"}).json()["total"] == 1


@pytest.mark.asyncio
async def test_employee_and_tenant_isolation_and_all_scope_permission(history):
    db, user, client = history
    await seed(db)
    path = "/api/fulfillment-experiments-v1/holds/history"
    assert client.get(path, params={"scope": "all"}).status_code == 403
    user["id"] = "bob"
    entry = client.get(path).json()["items"][0]
    assert [p["piece_id"] for p in entry["products"]] == ["p2"]
    assert "employee_ids" not in entry and "piece_ids" not in entry and "target_id" not in entry
    user["id"] = "outsider"
    assert client.get(path).json()["total"] == 0
    user["id"] = "denied"
    assert client.get(path).status_code == 403
    user["id"] = "owner"
    assert len(client.get(path, params={"scope": "all"}).json()["items"][0]["products"]) == 2


@pytest.mark.asyncio
async def test_legacy_hold_retained_and_details_never_leak_other_employee(history):
    db, user, client = history
    await seed(db, product_snapshots=None)
    await db.mezan_preparation_pieces_v1.insert_one(piece("p2", "bob"))
    response = client.get("/api/fulfillment-experiments-v1/holds/history").json()
    assert response["total"] == 1
    assert response["items"][0]["products"] == []
    assert response["items"][0]["snapshot_available"] is False
    await db.mezan_preparation_pieces_v1.insert_one(piece("p1", "alice"))
    entry = client.get("/api/fulfillment-experiments-v1/holds/history").json()["items"][0]
    assert [p["piece_id"] for p in entry["products"]] == ["p1"]


@pytest.mark.asyncio
async def test_stable_pagination_keeps_repeated_stops(history):
    db, user, client = history
    await seed(db, "h1", status="released")
    await seed(db, "h2")
    path = "/api/fulfillment-experiments-v1/holds/history"
    first = client.get(path, params={"page_size": 1}).json()
    second = client.get(path, params={"page_size": 1, "page": 2}).json()
    assert first["total"] == second["total"] == 2
    assert first["items"][0]["id"] == "h2"
    assert second["items"][0]["id"] == "h1"
    assert first["has_more"] is True and second["has_more"] is False
    assert client.get(path, params={"page_size": 101}).status_code == 422
    assert client.get(path, params={"status": "unknown"}).status_code == 422


def test_snapshot_is_allowlisted_and_detached():
    source = {**piece("p1", "alice"), "private_cost": 100, "active_hold_id": "old"}
    snapshot = hold_product_snapshot(source)
    source["product_options_snapshot"]["size"] = "L"
    assert snapshot["product_options_snapshot"]["size"] == "M"
    assert "private_cost" not in snapshot and "active_hold_id" not in snapshot
    assert "user_id" not in snapshot


@pytest.mark.asyncio
async def test_real_operational_routes_freeze_and_retain_after_release_and_reassignment():
    from fulfillment_experiment_routes import make_fulfillment_experiment_router
    db = AsyncMongoMockClient().db
    app = FastAPI()
    app.include_router(make_fulfillment_experiment_router(
        db, lambda: {"id": "shop", "role": "owner", "name": "Owner"}), prefix="/api")
    client = TestClient(app)
    await db.mezan_preparation_pieces_v1.insert_one({**piece("p1", "alice"), "status": "assigned"})
    root = "/api/fulfillment-experiments-v1"
    created = client.post(root + "/orders/123/holds", json={
        "scope": "item", "target_id": "p1", "stop_type": "edit", "note": "Customer request"})
    assert created.status_code == 200, created.text
    hid = created.json()["hold"]["id"]
    assert client.post(root + f"/holds/{hid}/release", json={"note": "Resolved"}).status_code == 200
    await db.mezan_preparation_pieces_v1.update_one({"piece_id": "p1"}, {"$set": {
        "responsible_employee_id": "bob", "supplier_name": "New supplier"}})
    entry = client.get(root + "/holds/history", params={"scope": "all"}).json()["items"][0]
    assert entry["status"] == "released" and entry["release_note"] == "Resolved"
    assert entry["products"][0]["responsible_employee_id"] == "alice"
    assert entry["products"][0]["supplier_name"] == "Test supplier"
