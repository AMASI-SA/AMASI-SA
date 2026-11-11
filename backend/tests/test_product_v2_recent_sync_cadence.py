"""The catalogue's automatic provider work is admitted once per owner/hour."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from unittest.mock import AsyncMock

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

import product_v2_recent_sync_routes as recent


@pytest_asyncio.fixture(params=["memory", "mongo"])
async def context(monkeypatch, request):
    if request.param == "mongo":
        uri = os.environ.get("PRODUCT_RECENT_SYNC_TEST_MONGO_URI")
        if not uri:
            pytest.skip("explicit disposable localhost Mongo required")
        assert urlsplit(uri).hostname in {"127.0.0.1", "localhost", "::1"}
        client = AsyncIOMotorClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000)
        await client.admin.command("ping")
    else:
        client = AsyncMongoMockClient(tz_aware=True)
    name = "recent_products_test_" + uuid.uuid4().hex
    db = client[name]
    clock = [datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(recent, "_now", lambda: clock[0])
    provider = AsyncMock(return_value={"data": []})
    monkeypatch.setattr(recent, "call_salla", provider)
    try:
        yield db, clock, provider
    finally:
        await client.drop_database(name)
        client.close()


@pytest.mark.asyncio
async def test_automatic_calls_share_the_hourly_owner_cadence(context):
    db, clock, provider = context
    first = await recent.sync_recent_products(db, user_id="owner-a")
    assert first["ok"] is True
    clock[0] += timedelta(minutes=59, seconds=59)
    second = await recent.sync_recent_products(db, user_id="owner-a")
    assert provider.await_count == 1
    assert second["skipped"] is True
    assert second["reason"] == "up_to_date"
    clock[0] += timedelta(seconds=1)
    third = await recent.sync_recent_products(db, user_id="owner-a")
    assert third["ok"] is True
    assert provider.await_count == 2


@pytest.mark.asyncio
async def test_owner_scope_and_manual_force_bypass_the_hourly_cooldown(context):
    db, clock, provider = context
    await recent.sync_recent_products(db, user_id="owner-a")
    clock[0] += timedelta(minutes=1)
    await recent.sync_recent_products(db, user_id="owner-b")
    manual = await recent.sync_recent_products(db, user_id="owner-a", force=True)
    assert manual["ok"] is True and manual["skipped"] is False
    assert provider.await_count == 3
    assert [call.args[1] for call in provider.await_args_list] == ["owner-a", "owner-b", "owner-a"]


@pytest.mark.asyncio
async def test_parallel_tabs_and_force_do_not_overlap_an_active_provider_call(context):
    db, _, provider = context
    entered, finish = asyncio.Event(), asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await finish.wait()
        return {"data": []}

    provider.side_effect = blocked
    running = [asyncio.create_task(recent.sync_recent_products(
        db, user_id="owner-a", force=index % 2 == 0,
    )) for index in range(8)]
    completed = iter(asyncio.as_completed(running))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        others = [await asyncio.wait_for(next(completed), 2) for _ in range(7)]
        assert all(result["skipped"] and result["reason"] == "in_progress" and not result["ok"] for result in others)
        assert provider.await_count == 1
    finally:
        finish.set()
        results = await asyncio.gather(*running)
    assert sum(result["ok"] is True for result in results) == 1


@pytest.mark.asyncio
async def test_failed_provider_has_short_retry_and_does_not_claim_success(context):
    db, clock, provider = context
    provider.side_effect = HTTPException(status_code=503, detail="test provider unavailable")
    with pytest.raises(HTTPException) as failure:
        await recent.sync_recent_products(db, user_id="owner-a")
    assert failure.value.status_code == 503
    state = await db[recent.RECENT_SYNC_STATE].find_one({"_id": "owner-a"})
    assert "last_success_at" not in state and "lease_token" not in state
    skipped = await recent.sync_recent_products(db, user_id="owner-a")
    assert skipped["reason"] == "retry_later" and skipped["ok"] is False
    assert provider.await_count == 1
    clock[0] += timedelta(seconds=60)
    provider.side_effect = None
    assert (await recent.sync_recent_products(db, user_id="owner-a"))["ok"] is True
    assert provider.await_count == 2


@pytest.mark.asyncio
async def test_partial_run_does_not_extend_the_previous_success_timestamp(context):
    db, clock, provider = context
    await recent.sync_recent_products(db, user_id="owner-a")
    successful_at = clock[0]
    clock[0] += timedelta(minutes=10)
    provider.return_value = {"data": ["malformed product"]}
    partial = await recent.sync_recent_products(db, user_id="owner-a", force=True)
    assert partial["ok"] is False and partial["errors_count"] == 1
    assert partial["next_sync_at"] is None
    state = await db[recent.RECENT_SYNC_STATE].find_one({"_id": "owner-a"})
    assert state["last_success_at"] == successful_at
    clock[0] = successful_at + timedelta(hours=1)
    provider.return_value = {"data": []}
    assert (await recent.sync_recent_products(db, user_id="owner-a"))["skipped"] is False
    assert provider.await_count == 3


@pytest.mark.asyncio
async def test_crashed_expired_lease_can_be_recovered_without_forcing(context):
    db, clock, provider = context
    await db[recent.RECENT_SYNC_STATE].insert_one({
        "_id": "owner-a", "lease_token": "abandoned",
        "lease_until": clock[0] - timedelta(seconds=1),
    })
    assert (await recent.sync_recent_products(db, user_id="owner-a"))["ok"] is True
    assert provider.await_count == 1


@pytest.mark.asyncio
async def test_late_admission_response_cannot_start_work_after_safe_lease_budget(context, monkeypatch):
    db, clock, provider = context
    original = clock[0]
    readings = iter([original, original + timedelta(seconds=115)])
    monkeypatch.setattr(recent, "_now", lambda: next(readings, original + timedelta(seconds=115)))
    with pytest.raises(HTTPException) as failure:
        await recent.sync_recent_products(db, user_id="owner-a")
    assert failure.value.status_code == 503
    assert failure.value.detail["code"] == "salla_recent_products_sync_lease_expired"
    assert provider.await_count == 0
    state = await db[recent.RECENT_SYNC_STATE].find_one({"_id": "owner-a"})
    assert "last_success_at" not in state and "lease_token" not in state


@pytest.mark.asyncio
async def test_operation_timeout_cancels_provider_before_releasing_lease(context, monkeypatch):
    db, _, provider = context
    cancelled = asyncio.Event()

    async def blocked(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    provider.side_effect = blocked
    # This test exercises provider cancellation; index creation is covered by
    # the other real-Mongo cases and must not consume this tiny test deadline.
    monkeypatch.setattr(recent, "ensure_product_v2_indexes", AsyncMock())
    monkeypatch.setattr(recent, "SYNC_TIMEOUT_SECONDS", 0.05)
    with pytest.raises(HTTPException) as failure:
        await recent.sync_recent_products(db, user_id="owner-a")
    assert failure.value.status_code == 504
    assert cancelled.is_set()
    state = await db[recent.RECENT_SYNC_STATE].find_one({"_id": "owner-a"})
    assert "lease_token" not in state and "last_success_at" not in state
    provider.side_effect = None
    assert (await recent.sync_recent_products(db, user_id="owner-a", force=True))["ok"] is True


@pytest.mark.asyncio
async def test_superseded_worker_cannot_release_a_newer_lease_or_record_success(context):
    db, clock, provider = context

    async def supersede(*args, **kwargs):
        await db[recent.RECENT_SYNC_STATE].update_one({"_id": "owner-a"}, {"$set": {
            "lease_token": "new-owner-token", "lease_until": clock[0] + timedelta(minutes=2),
        }})
        return {"data": []}

    provider.side_effect = supersede
    with pytest.raises(HTTPException) as failure:
        await recent.sync_recent_products(db, user_id="owner-a")
    assert failure.value.status_code == 409
    state = await db[recent.RECENT_SYNC_STATE].find_one({"_id": "owner-a"})
    assert state["lease_token"] == "new-owner-token"
    assert "last_success_at" not in state


@pytest.mark.asyncio
async def test_cancellation_after_product_write_still_invalidates_changed_costs(context, monkeypatch):
    db, _, provider = context
    provider.return_value = {"data": [{"id": 1, "name": "First", "cost_price": 10}, {"id": 2}]}
    normalize = recent.normalize_salla_product

    def cancel_second(raw, **kwargs):
        if raw["id"] == 2:
            raise asyncio.CancelledError()
        return normalize(raw, **kwargs)

    monkeypatch.setattr(recent, "normalize_salla_product", cancel_second)
    bump = AsyncMock()
    monkeypatch.setattr(recent, "bump_product_cost_revision", bump)
    with pytest.raises(asyncio.CancelledError):
        await recent.sync_recent_products(db, user_id="owner-a")
    assert await db[recent.PRODUCTS].count_documents({"user_id": "owner-a"}) == 1
    bump.assert_awaited_once_with(db, "owner-a")
    state = await db[recent.RECENT_SYNC_STATE].find_one({"_id": "owner-a"})
    assert "last_success_at" not in state and "lease_token" not in state
    assert state.get("cost_revision_pending") is not True


@pytest.mark.asyncio
async def test_failed_cost_revision_is_durable_and_retried_on_unchanged_products(context, monkeypatch):
    db, clock, provider = context
    provider.return_value = {"data": [{"id": 1, "name": "Synthetic", "cost_price": 10}]}
    bump = AsyncMock(side_effect=[RuntimeError("revision write failed"), None])
    monkeypatch.setattr(recent, "bump_product_cost_revision", bump)
    app = FastAPI()

    async def user():
        return {"id": "owner-a"}

    app.include_router(recent.make_product_v2_recent_sync_router(db, user))
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        first = await client.post("/products-v2/sync-recent")
        assert first.status_code == 500
        assert await db[recent.PRODUCTS].count_documents({"user_id": "owner-a"}) == 1
        state = await db[recent.RECENT_SYNC_STATE].find_one({"_id": "owner-a"})
        assert state["cost_revision_pending"] is True
        assert "last_success_at" not in state
        clock[0] += timedelta(seconds=60)
        second = await client.post("/products-v2/sync-recent")
        assert second.status_code == 200, second.text
        assert second.json()["ok"] is True and second.json()["unchanged"] == 1
    assert bump.await_count == 2
    state = await db[recent.RECENT_SYNC_STATE].find_one({"_id": "owner-a"})
    assert state.get("cost_revision_pending") is not True
    assert state["last_success_at"] == clock[0]


@pytest.mark.asyncio
async def test_pending_cost_revision_can_recover_before_an_older_success_hour_expires(context, monkeypatch):
    db, clock, provider = context
    provider.return_value = {"data": [{"id": 1, "name": "Synthetic", "cost_price": 10}]}
    bump = AsyncMock(side_effect=[None, RuntimeError("revision write failed"), None])
    monkeypatch.setattr(recent, "bump_product_cost_revision", bump)
    await recent.sync_recent_products(db, user_id="owner-a")
    clock[0] += timedelta(minutes=5)
    provider.return_value = {"data": [{"id": 1, "name": "Synthetic", "cost_price": 20}]}
    with pytest.raises(RuntimeError, match="revision write failed"):
        await recent.sync_recent_products(db, user_id="owner-a", force=True)
    backoff = await recent.sync_recent_products(db, user_id="owner-a")
    assert backoff["reason"] == "retry_later" and backoff["ok"] is False
    assert datetime.fromisoformat(backoff["next_retry_at"]) == clock[0] + timedelta(seconds=60)
    clock[0] += timedelta(seconds=60)
    recovered = await recent.sync_recent_products(db, user_id="owner-a")
    assert recovered["ok"] is True and recovered["unchanged"] == 1
    assert bump.await_count == 3


@pytest.mark.asyncio
async def test_revision_acknowledgement_cannot_clear_another_workers_pending_intent(context, monkeypatch):
    db, clock, provider = context
    provider.return_value = {"data": [{"id": 1, "name": "Synthetic", "cost_price": 10}]}

    async def supersede_after_bump(*args):
        await db[recent.RECENT_SYNC_STATE].update_one({"_id": "owner-a"}, {"$set": {
            "lease_token": "new-owner-token", "lease_until": clock[0] + timedelta(minutes=2),
        }})

    monkeypatch.setattr(recent, "bump_product_cost_revision", supersede_after_bump)
    with pytest.raises(HTTPException) as failure:
        await recent.sync_recent_products(db, user_id="owner-a")
    assert failure.value.status_code == 409
    state = await db[recent.RECENT_SYNC_STATE].find_one({"_id": "owner-a"})
    assert state["cost_revision_pending"] is True
    assert state["lease_token"] == "new-owner-token"
    assert "last_success_at" not in state


@pytest.mark.asyncio
async def test_route_uses_authenticated_owner_and_validates_force(context):
    db, _, provider = context
    app = FastAPI()

    async def current_user():
        return {"id": "authenticated-owner"}

    app.include_router(recent.make_product_v2_recent_sync_router(db, current_user))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/products-v2/sync-recent?user_id=someone-else")
        assert response.status_code == 200 and response.json()["ok"] is True
        response = await client.post("/products-v2/sync-recent?force=true")
        assert response.status_code == 200 and response.json()["skipped"] is False
        response = await client.post("/products-v2/sync-recent?force=invalid")
        assert response.status_code == 422
    assert provider.await_count == 2
    assert all(call.args[1] == "authenticated-owner" for call in provider.await_args_list)
