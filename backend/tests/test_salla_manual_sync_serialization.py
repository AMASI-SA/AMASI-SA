"""Actual manual ASGI routes share one durable tenant sync lease.

Provider work is a controlled async collaborator; Mongo lease acquisition,
route admission, cancellation/timeout, and release are the actual code paths.
No server bootstrap, credentials, live API or production database is imported.
"""
import asyncio
from datetime import datetime, timezone
import sys
import types
from unittest.mock import AsyncMock

from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient
import pytest
import pytest_asyncio

import product_v2_routes as products
from salla_integration import routes, scheduled_reconciliation as scheduled
from salla_integration.service import SallaError


OWNER = "synthetic-sync-owner"
DUE = datetime(2026, 11, 15, 7, tzinfo=timezone.utc)
PRODUCT_PATHS = ("/salla/sync/products", "/products-v2/sync")


@pytest_asyncio.fixture
async def harness(monkeypatch):
    db = AsyncMongoMockClient(tz_aware=True).db
    monkeypatch.setattr(routes, "_MANUAL_ORDER_SYNC_TASKS", {})

    async def current_user():
        return {"id": OWNER, "role": "owner"}

    unexpected = AsyncMock(side_effect=AssertionError("Provider work was not admitted by this test"))
    monkeypatch.setattr(routes, "run_orders_sync", unexpected)
    monkeypatch.setattr(routes, "run_products_sync", unexpected)
    monkeypatch.setattr(products, "run_product_v2_sync", unexpected)
    monkeypatch.setattr(scheduled, "run_orders_sync", unexpected)
    fake_server = types.ModuleType("server")
    fake_server.current_user = current_user
    with monkeypatch.context() as context:
        context.setitem(sys.modules, "server", fake_server)
        router = APIRouter()
        routes.attach_salla_routes(router, db)
    app = FastAPI()
    app.include_router(router)
    app.include_router(products.make_product_v2_router(db, current_user))
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                           base_url="http://isolated.test") as client:
        try:
            yield db, client
        finally:
            tasks = list(routes._MANUAL_ORDER_SYNC_TASKS.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def assert_lease_available(db):
    token, active = await routes._acquire_salla_sync_lease(db, OWNER)
    assert token is not None, active
    for key in (OWNER, f"{OWNER}:catalogue"):
        held = await db.salla_sync_leases.find_one({"_id": f"salla-orders:{key}"})
        assert held["lease_token"] == token
    await routes._release_salla_sync_lease(db, OWNER, token)


def patch_product_work(monkeypatch, path, work):
    if path == "/salla/sync/products":
        monkeypatch.setattr(routes, "run_products_sync", work)
    else:
        monkeypatch.setattr(products, "run_product_v2_sync", work)


def test_manual_timeout_ends_before_shared_lease_can_expire():
    assert 0 < routes._MANUAL_SYNC_TIMEOUT_SECONDS < routes._ORDER_SYNC_LEASE_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PRODUCT_PATHS)
async def test_occupied_shared_lease_returns_409_without_starting_product_work(harness, monkeypatch, path):
    db, client = harness
    work = AsyncMock(side_effect=AssertionError("A held lease must reject the second import"))
    patch_product_work(monkeypatch, path, work)
    token, _ = await routes._acquire_order_sync_lease(db, OWNER)
    assert token is not None
    try:
        response = await client.post(path)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "SALLA_SYNC_ALREADY_RUNNING"
        work.assert_not_awaited()
        held = await db.salla_sync_leases.find_one({"_id": f"salla-orders:{OWNER}"})
        assert held["lease_token"] == token
        assert held["lease_until"] > datetime.now(timezone.utc)
    finally:
        await routes._release_order_sync_lease(db, OWNER, token)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", (*PRODUCT_PATHS, "/salla/sync/orders"))
async def test_old_catalogue_worker_excludes_every_manual_sync(harness, monkeypatch, path):
    db, client = harness
    work = AsyncMock(side_effect=AssertionError("Old catalogue work must not overlap manual work"))
    monkeypatch.setattr(routes, "run_products_sync", work)
    monkeypatch.setattr(products, "run_product_v2_sync", work)
    monkeypatch.setattr(routes, "run_orders_sync", work)
    token, _ = await routes._acquire_order_sync_lease(db, f"{OWNER}:catalogue")
    assert token is not None
    try:
        response = await client.post(path, json={})
        if path == "/salla/sync/orders":
            assert response.status_code == 202, response.text
            assert response.json()["already_running"] is True
        else:
            assert response.status_code == 409, response.text
            assert response.json()["detail"]["code"] == "SALLA_SYNC_ALREADY_RUNNING"
        work.assert_not_awaited()
        assert OWNER not in routes._MANUAL_ORDER_SYNC_TASKS
        old = await db.salla_sync_leases.find_one({"_id": f"salla-orders:{OWNER}:catalogue"})
        assert old["lease_token"] == token
        assert old["lease_until"] > datetime.now(timezone.utc)
        # Failure to claim the second key must release the first claim only.
        shared, active = await routes._acquire_order_sync_lease(db, OWNER)
        assert shared is not None, active
        await routes._release_order_sync_lease(db, OWNER, shared)
    finally:
        await routes._release_order_sync_lease(db, f"{OWNER}:catalogue", token)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_after", [1, 2])
async def test_ambiguous_group_acquisition_failure_releases_its_own_claims(harness, monkeypatch, fail_after):
    db, client = harness
    acquire = routes._acquire_order_sync_lease
    count = 0

    async def uncertain(db, user_id, **kwargs):
        nonlocal count
        result = await acquire(db, user_id, **kwargs)
        count += 1
        if count == fail_after:
            raise RuntimeError("Synthetic write acknowledged locally but connection failed")
        return result

    monkeypatch.setattr(routes, "_acquire_order_sync_lease", uncertain)
    response = await client.post("/salla/sync/products")
    assert response.status_code == 500, response.text
    monkeypatch.setattr(routes, "_acquire_order_sync_lease", acquire)
    await assert_lease_available(db)


@pytest.mark.asyncio
async def test_group_release_preserves_a_replacement_claim(harness):
    db, _ = harness
    token, _ = await routes._acquire_salla_sync_lease(db, OWNER)
    assert token is not None
    await db.salla_sync_leases.update_one({"_id": f"salla-orders:{OWNER}"},
                                         {"$set": {"lease_token": "replacement-owner-token"}})
    await routes._release_salla_sync_lease(db, OWNER, token)
    token_after, active = await routes._acquire_order_sync_lease(db, OWNER)
    assert token_after is None
    assert active["lease_until"] > datetime.now(timezone.utc)
    legacy_token, _ = await routes._acquire_order_sync_lease(db, f"{OWNER}:catalogue")
    assert legacy_token is not None
    await routes._release_order_sync_lease(db, f"{OWNER}:catalogue", legacy_token)
    await routes._release_order_sync_lease(db, OWNER, "replacement-owner-token")


@pytest.mark.asyncio
@pytest.mark.parametrize("first_path", PRODUCT_PATHS)
async def test_running_product_route_excludes_both_manual_routes_and_scheduler(harness, monkeypatch, first_path):
    db, client = harness
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(scoped, user_id):
        assert scoped is db and user_id == OWNER
        entered.set()
        await release.wait()
        return {"updated": 1}

    work = AsyncMock(side_effect=blocked)
    monkeypatch.setattr(routes, "run_products_sync", work)
    monkeypatch.setattr(products, "run_product_v2_sync", work)
    orders = AsyncMock(side_effect=AssertionError("Shared lease must reject order processing"))
    monkeypatch.setattr(routes, "run_orders_sync", orders)
    monkeypatch.setattr(scheduled, "run_orders_sync", orders)
    request = asyncio.create_task(client.post(first_path))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        for path in PRODUCT_PATHS:
            response = await client.post(path)
            assert response.status_code == 409, response.text
        order_response = await client.post("/salla/sync/orders", json={})
        assert order_response.status_code == 202, order_response.text
        assert order_response.json()["already_running"] is True
        for catalogue in (False, True):
            assert not await scheduled.run_due(db, OWNER, now=DUE, catalogue=catalogue)
        orders.assert_not_awaited()
        assert work.await_count == 1
        assert OWNER not in routes._MANUAL_ORDER_SYNC_TASKS
        release.set()
        completed = await request
        assert completed.status_code == 200, completed.text
        await assert_lease_available(db)
    finally:
        release.set()
        await request


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PRODUCT_PATHS)
@pytest.mark.parametrize("outcome,expected_status", [
    ("success", 200), ("provider_error", 502), ("unexpected_error", 500), ("timeout", 504),
])
async def test_product_route_releases_shared_lease_on_every_terminal_path(
    harness, monkeypatch, path, outcome, expected_status,
):
    db, client = harness
    cancelled = asyncio.Event()

    async def work(scoped, user_id):
        assert scoped is db and user_id == OWNER
        if outcome == "provider_error":
            raise SallaError("Synthetic provider unavailable", status_code=502)
        if outcome == "unexpected_error":
            raise RuntimeError("Synthetic processing interruption")
        if outcome == "timeout":
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return {"updated": 1}

    patch_product_work(monkeypatch, path, work)
    if outcome == "timeout":
        monkeypatch.setattr(routes, "_MANUAL_SYNC_TIMEOUT_SECONDS", 0.01)
    response = await client.post(path)
    assert response.status_code == expected_status, response.text
    if outcome == "timeout":
        assert response.json()["detail"]["code"] == "SALLA_SYNC_TIMEOUT"
        assert cancelled.is_set(), "Provider task must be stopped before releasing its lease"
    await assert_lease_available(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "unexpected_error", "timeout", "cancelled"])
async def test_background_manual_order_sync_releases_shared_lease(harness, monkeypatch, outcome):
    db, client = harness
    entered, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def work(scoped, user_id, **kwargs):
        assert scoped is db and user_id == OWNER
        assert kwargs["log_id"]
        entered.set()
        try:
            await release.wait()
            if outcome == "unexpected_error":
                raise RuntimeError("Synthetic order sync interrupted")
            return {"updated": 1}
        finally:
            stopped.set()

    monkeypatch.setattr(routes, "run_orders_sync", work)
    if outcome == "timeout":
        monkeypatch.setattr(routes, "_MANUAL_SYNC_TIMEOUT_SECONDS", 0.05)
    response = await client.post("/salla/sync/orders", json={})
    assert response.status_code == 202, response.text
    assert response.json()["accepted"] is True
    task = routes._MANUAL_ORDER_SYNC_TASKS[OWNER]
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        occupied, active = await routes._acquire_order_sync_lease(db, OWNER)
        assert occupied is None
        assert active["log_id"] == response.json()["log_id"]
        if outcome == "cancelled":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            if outcome != "timeout":
                release.set()
            await asyncio.wait_for(task, timeout=1)
        assert stopped.is_set()
        assert OWNER not in routes._MANUAL_ORDER_SYNC_TASKS
        await assert_lease_available(db)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
