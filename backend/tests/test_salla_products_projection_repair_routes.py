"""Public one-order repair boundary, using the real engine and synthetic data."""
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient

# Exercise the actual Orders V2 factory before the package adds unrelated routers.
from order_engine import _original_make_order_engine_router as make_order_engine_router
from test_salla_products_projection_repair import OWNER, Provider, seed, snapshot


OTHER_OWNER = "synthetic-other-owner"
EMPLOYEE = "synthetic-employee"
BASE = "/api/orders-v2/order-one/products-projection-repair"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Live provider transport is forbidden")
    async def async_forbidden(*args, **kwargs):
        forbidden()
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", async_forbidden)


@pytest_asyncio.fixture
async def context(monkeypatch):
    db = AsyncMongoMockClient().projection_repair_routes
    raw = await seed(db)
    await db.users.insert_many([
        {"id": OWNER, "role": "owner", "is_active": True},
        {"id": OTHER_OWNER, "role": "owner", "is_active": True},
        {"id": EMPLOYEE, "role": "employee", "created_by": OWNER, "is_active": True},
    ])
    provider = Provider(raw)
    monkeypatch.setattr("salla_integration.service.call_salla", provider)
    transactions = []
    async def owner(database, user_id, callback, **kwargs):
        assert database is db and user_id == OWNER and not kwargs
        transactions.append(user_id)
        return await callback(database)
    # Real Mongo atomicity remains covered by the repair engine acceptance suite.
    monkeypatch.setattr("operational_atomic.operational_owner", owner)
    return SimpleNamespace(db=db, raw=raw, provider=provider, transactions=transactions)


def app_for(db, user=None):
    principal = {"id": OWNER, "role": "owner"} if user is None else deepcopy(user)
    async def current_user():
        return deepcopy(principal)
    app = FastAPI()
    app.include_router(make_order_engine_router(db, current_user), prefix="/api")
    return app


async def request(app, action="preview", **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test.example") as client:
        return await client.post(BASE + "/" + action, **kwargs)


@pytest.mark.asyncio
async def test_public_preview_and_apply_use_real_proof_and_only_projection_writes(context):
    app = app_for(context.db)
    before = await snapshot(context.db)
    preview = await request(app, json={})
    assert preview.status_code == 200
    assert preview.headers["cache-control"] == "no-store"
    plan = preview.json()
    assert plan["ready"] and plan["pending_orders"] == 1 and plan["mode"] == "dry_run"
    assert len(plan["orders"][0]["before"]) == 3 and len(plan["orders"][0]["after"]) == 1
    assert "user_id" not in plan and "PRIVATE" not in preview.text and OWNER not in preview.text
    assert await snapshot(context.db) == before and context.transactions == []
    assert len(context.provider.calls) == 3
    applied = await request(app, "apply", json={"manifest_sha": plan["manifest_sha"]})
    assert applied.status_code == 200 and applied.headers["cache-control"] == "no-store"
    assert applied.json()["mode"] == "applied" and applied.json()["applied_orders"] == 1
    assert len(context.provider.calls) == 6 and context.transactions == [OWNER]
    after = await snapshot(context.db)
    assert len(after["unified_orders"][0]["products"]) == 1
    after["unified_orders"][0]["products"] = before["unified_orders"][0]["products"]
    assert after == before


@pytest.mark.asyncio
async def test_native_employee_owner_shape_is_denied_before_provider(context):
    user = {"id": OWNER, "role": "owner", "_session_client": "amasi_mobile", "_mobile_actor_id": EMPLOYEE}
    before = await snapshot(context.db)
    response = await request(app_for(context.db, user), json={})
    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "products_projection_repair_owner_required"}}
    assert response.headers["cache-control"] == "no-store"
    assert context.provider.calls == [] and context.transactions == []
    assert await snapshot(context.db) == before


@pytest.mark.asyncio
async def test_apply_rejects_extra_owner_without_provider_or_writes(context):
    before = await snapshot(context.db)
    response = await request(app_for(context.db), "apply", json={"manifest_sha": "a" * 64, "user_id": OTHER_OWNER})
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_products_projection_repair_request"}}
    assert response.headers["cache-control"] == "no-store"
    assert context.provider.calls == [] and context.transactions == []
    assert await snapshot(context.db) == before
