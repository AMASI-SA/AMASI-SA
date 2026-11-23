"""Assignment status delivery is separate from the immutable physical file."""
from copy import deepcopy
import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient

import reviewed_preparation_batches as batches
import preparation_file_status_recovery as recovery
from preparation_file_registry import REGISTRY, preparation_file_view
from review_local_policy import LOCAL_COMPLETION_MODE


@pytest.fixture
def db():
    return AsyncMongoMockClient()["status_recovery"]


async def seed(db, *, owner="owner", number="ORDER-1", batch_id="batch-1"):
    await db.users.insert_one({"id": owner, "role": "owner", "is_active": True})
    batch = {"id": batch_id, "user_id": owner, "status": "ready", "allocated_quantity": 1,
             "lines": [{"order_number": number, "order_item_id": "line-1", "quantity": 1}],
             "order_count": 1, "pdf_sha256": "unchanged-pdf"}
    await db[batches.BATCHES].insert_one(deepcopy(batch))
    await db[REGISTRY].insert_one({"user_id": owner, "batch_id": batch_id, "status": "ready",
                                 "responsible_employee_id": "employee-1", "allocated_quantity": 1})
    await db[batches.WORKFLOWS].insert_one({"user_id": owner, "order_number": number,
        "stage": "reviewed", "completion_mode": LOCAL_COMPLETION_MODE})
    await db[batches.PREPARATION_UNIT_ALLOCATIONS].insert_one({"user_id": owner,
        "order_number": number, "order_item_id": "line-1", "unit_index": 1,
        "batch_id": batch_id, "status": "committed"})
    return batch


def client_for(db, *, user=None):
    actor = user or {"id": "owner", "role": "owner"}
    async def current_user():
        return actor
    app = FastAPI()
    app.include_router(batches.make_reviewed_preparation_batches_router(db, current_user), prefix="/api")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_silent_unresolved_order_is_pending_not_lost(db, monkeypatch):
    batch = await seed(db)
    monkeypatch.setattr(recovery, "_reconcile_order_stage", AsyncMock(return_value=(False, 0)))
    result = await recovery.reconcile_file_orders(db, user_id="owner", batch=batch, actor={"id": "owner"})
    assert result["reconciliation_required"] == ["ORDER-1"]
    assert result["order_status_sync"]["state"] == "pending"
    assert result["order_status_sync"]["orders"] == [{"order_number": "ORDER-1", "state": "pending",
        "reason": "preparation_status_not_confirmed"}]
    registry = await db[REGISTRY].find_one({"batch_id": "batch-1"})
    assert preparation_file_view(registry)["order_status_sync"] == result["order_status_sync"]


@pytest.mark.asyncio
async def test_mixed_results_are_complete_and_errors_are_sanitized(db, monkeypatch):
    batch = await seed(db)
    batch["lines"] += [{"order_number": "ORDER-2"}, {"order_number": "ORDER-3"}]
    await db[batches.WORKFLOWS].insert_many([
        {"user_id": "owner", "order_number": number, "stage": "reviewed",
         "completion_mode": LOCAL_COMPLETION_MODE}
        for number in ("ORDER-2", "ORDER-3")
    ])
    async def reconcile(_db, **kwargs):
        if kwargs["order_number"] == "ORDER-1":
            return True, 0
        if kwargs["order_number"] == "ORDER-2":
            return False, 2
        raise RuntimeError("secret-token-and-provider-response")
    monkeypatch.setattr(recovery, "_reconcile_order_stage", reconcile)
    result = await recovery.reconcile_file_orders(db, user_id="owner", batch=batch, actor={"id": "owner"})
    summary = result["order_status_sync"]
    assert (summary["confirmed_count"], summary["partial_count"], summary["pending_count"]) == (1, 1, 1)
    assert summary["orders"][1] == {"order_number": "ORDER-2", "state": "partial", "remaining_quantity": 2}
    assert "secret-token" not in repr(result)
    assert summary["orders"][2]["reason"] == "preparation_status_check_failed"


@pytest.mark.asyncio
async def test_explicit_reconcile_keeps_file_and_allocations(db, monkeypatch):
    await seed(db)
    before = await db[batches.PREPARATION_UNIT_ALLOCATIONS].find({}).to_list(None)
    reconcile = AsyncMock(return_value=(True, 0))
    monkeypatch.setattr(recovery, "_reconcile_order_stage", reconcile)
    async with client_for(db) as client:
        response = await client.post("/api/reviewed-preparation-batches-v1/batches/batch-1/reconcile-status", json={})
    assert response.status_code == 200, response.text
    assert response.json()["order_status_sync"]["confirmed_count"] == 1
    assert await db[batches.PREPARATION_UNIT_ALLOCATIONS].find({}).to_list(None) == before
    assert (await db[batches.BATCHES].find_one({"id": "batch-1"}))["pdf_sha256"] == "unchanged-pdf"
    assert await db[batches.BATCHES].count_documents({}) == 1
    assert reconcile.await_count == 1


@pytest.mark.asyncio
async def test_reconcile_does_not_cross_tenant_or_accept_unassigned_file(db, monkeypatch):
    await seed(db, owner="other")
    reconcile = AsyncMock()
    monkeypatch.setattr(recovery, "_reconcile_order_stage", reconcile)
    async with client_for(db) as client:
        response = await client.post("/api/reviewed-preparation-batches-v1/batches/batch-1/reconcile-status", json={})
    assert response.status_code == 404
    assert reconcile.await_count == 0
    await db[batches.BATCHES].update_one({"id": "batch-1"}, {"$set": {"user_id": "owner"}})
    async with client_for(db) as client:
        response = await client.post("/api/reviewed-preparation-batches-v1/batches/batch-1/reconcile-status", json={})
    assert response.status_code == 409
    assert reconcile.await_count == 0


@pytest.mark.asyncio
async def test_read_only_workspace_cannot_dispatch(db, monkeypatch):
    await seed(db)
    reconcile = AsyncMock()
    monkeypatch.setattr(recovery, "_reconcile_order_stage", reconcile)
    actor = {"id": "viewer", "created_by": "owner", "role": "employee",
             "_web_workspace_read": ["fulfillment.reviewed_files", "read"]}
    async with client_for(db, user=actor) as client:
        response = await client.post("/api/reviewed-preparation-batches-v1/batches/batch-1/reconcile-status", json={})
    assert response.status_code == 403
    assert reconcile.await_count == 0


@pytest.mark.asyncio
async def test_existing_legacy_receipt_is_not_redispatched(db, monkeypatch):
    await seed(db)
    await db[batches.WORKFLOWS].update_one({}, {"$unset": {"completion_mode": ""}})
    reconcile = AsyncMock()
    monkeypatch.setattr(recovery, "_reconcile_order_stage", reconcile)
    async with client_for(db) as client:
        response = await client.post("/api/reviewed-preparation-batches-v1/batches/batch-1/reconcile-status", json={})
    assert response.status_code == 200, response.text
    assert response.json()["order_status_sync"]["orders"][0]["reason"] == "legacy_assignment_status_requires_verification"
    assert reconcile.await_count == 0


@pytest.mark.asyncio
async def test_status_plan_is_read_only_and_each_post_handles_one_member(db, monkeypatch):
    batch = await seed(db)
    batch["lines"].append({"order_number": "ORDER-2", "order_item_id": "line-2", "quantity": 1})
    await db[batches.BATCHES].update_one({"id": "batch-1"}, {"$set": {"lines": batch["lines"]}})
    await db[batches.WORKFLOWS].insert_one({"user_id": "owner", "order_number": "ORDER-2",
        "stage": "reviewed", "completion_mode": LOCAL_COMPLETION_MODE})
    reconcile = AsyncMock(return_value=(True, 0))
    monkeypatch.setattr(recovery, "_reconcile_order_stage", reconcile)
    before = await db[batches.BATCHES].find_one({"id": "batch-1"})
    async with client_for(db) as client:
        plan = await client.get("/api/reviewed-preparation-batches-v1/batches/batch-1/status-plan")
        assert plan.status_code == 200
        assert plan.json()["order_numbers"] == ["ORDER-1", "ORDER-2"]
        assert await db[batches.BATCHES].find_one({"id": "batch-1"}) == before
        assert reconcile.await_count == 0
        missing = await client.post("/api/reviewed-preparation-batches-v1/batches/batch-1/reconcile-status", json={})
        foreign = await client.post("/api/reviewed-preparation-batches-v1/batches/batch-1/reconcile-status",
                                    json={"order_number": "OTHER-ORDER"})
        assert (missing.status_code, foreign.status_code) == (422, 422)
        assert reconcile.await_count == 0
        first = await client.post("/api/reviewed-preparation-batches-v1/batches/batch-1/reconcile-status",
                                 json={"order_number": "ORDER-1"})
        assert first.status_code == 200, first.text
        assert first.json()["order_status_sync"]["confirmed_count"] == 1
        assert first.json()["order_status_sync"]["pending_count"] == 1
        assert reconcile.await_count == 1
        second = await client.post("/api/reviewed-preparation-batches-v1/batches/batch-1/reconcile-status",
                                  json={"order_number": "ORDER-2"})
    assert second.json()["order_status_sync"]["state"] == "confirmed"
    assert second.json()["order_status_sync"]["confirmed_count"] == 2
    assert reconcile.await_count == 2
    stored = await db[REGISTRY].find_one({"batch_id": "batch-1"})
    assert preparation_file_view(stored)["order_status_sync"]["confirmed_count"] == 2


@pytest.mark.asyncio
async def test_disconnect_keeps_completed_status_and_unprocessed_orders_visible(db, monkeypatch):
    batch = await seed(db)
    batch["lines"].append({"order_number": "ORDER-2"})
    await db[batches.WORKFLOWS].insert_one({"user_id": "owner", "order_number": "ORDER-2",
        "stage": "reviewed", "completion_mode": LOCAL_COMPLETION_MODE})
    async def reconcile(_db, **kwargs):
        if kwargs["order_number"] == "ORDER-1":
            return True, 0
        raise asyncio.CancelledError()
    monkeypatch.setattr(recovery, "_reconcile_order_stage", reconcile)
    with pytest.raises(asyncio.CancelledError):
        await recovery.reconcile_file_orders(db, user_id="owner", batch=batch, actor={"id": "owner"})
    stored = await db[REGISTRY].find_one({"batch_id": "batch-1"})
    view = preparation_file_view(stored)["order_status_sync"]
    assert (view["confirmed_count"], view["pending_count"]) == (1, 1)
    assert view["orders"][1]["reason"] == "preparation_status_not_checked"
    assert await db[batches.PREPARATION_UNIT_ALLOCATIONS].count_documents({"status": "committed"}) == 1


@pytest.mark.asyncio
async def test_revoked_actor_cannot_continue_status(db, monkeypatch):
    await seed(db)
    await db.users.update_one({"id": "owner"}, {"$set": {"disabled": True}})
    reconcile = AsyncMock()
    monkeypatch.setattr(recovery, "_reconcile_order_stage", reconcile)
    async with client_for(db) as client:
        response = await client.post("/api/reviewed-preparation-batches-v1/batches/batch-1/reconcile-status", json={})
    assert response.status_code == 403
    assert reconcile.await_count == 0


@pytest.mark.asyncio
async def test_existing_file_with_more_than_fifty_orders_can_be_checked(db, monkeypatch):
    batch = await seed(db)
    numbers = [f"ORDER-{index}" for index in range(1, 52)]
    batch["lines"] = [{"order_number": number} for number in numbers]
    await db[batches.BATCHES].update_one({"id": "batch-1"}, {"$set": {"lines": batch["lines"]}})
    reconcile = AsyncMock(return_value=(True, 0))
    monkeypatch.setattr(recovery, "_reconcile_order_stage", reconcile)
    async with client_for(db) as client:
        response = await client.get("/api/reviewed-preparation-batches-v1/batches/batch-1/status-plan")
        assert response.status_code == 200, response.text
        assert set(response.json()["order_numbers"]) == set(numbers)
        assert reconcile.await_count == 0
        first = await client.post("/api/reviewed-preparation-batches-v1/batches/batch-1/reconcile-status",
                                  json={"order_number": "ORDER-1"})
    assert first.status_code == 200, first.text
    assert first.json()["order_status_sync"]["confirmed_count"] == 1
    assert first.json()["order_status_sync"]["pending_count"] == 50
    assert reconcile.await_count == 1

