"""Repair endpoint for Salla integration user_id mismatch (2026-07-09).

Contract:
    • POST /api/salla/webhook-diagnose/repair-integration
    • Body: {"source_user_id": <str>, "target_user_id": <str>}
    • Auth required.
    • target_user_id MUST equal the authenticated user's id → 403 otherwise.
    • source_user_id != target_user_id → 400 otherwise.
    • source doc must exist → 404 otherwise.
    • On success: doc moved atomically. Tokens preserved verbatim.

REMOVE this test alongside the repair route once connection state
stabilises across all merchants.
"""
from __future__ import annotations

import mongomock_motor  # noqa: F401
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport


TARGET_UID = "5aee091a-cc47-42cd-b14c-a14e32f169cc"
SOURCE_UID = "ae93e5c3-8c34-4f02-841c-e27772103555"


@pytest.fixture
def db():
    client = mongomock_motor.AsyncMongoMockClient()
    return client["test_salla_repair"]


def _make_app(db, user_id):
    from fastapi import APIRouter
    from salla_integration.routes import attach_salla_routes
    # Monkey-patch server.current_user BEFORE attach_salla_routes
    # imports it at call time.
    import server as _srv
    async def _fake_user():
        return {"id": user_id, "email": "amasi.jewelery@gmail.com"}
    _srv.current_user = _fake_user  # type: ignore
    app = FastAPI()
    api_router = APIRouter()
    attach_salla_routes(api_router, db)
    app.include_router(api_router, prefix="/api")
    return app


async def _post(app, url, body):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport,
                            base_url="http://test") as c:
        return await c.post(url, json=body)


@pytest.mark.asyncio
async def test_target_must_be_self(db):
    app = _make_app(db, TARGET_UID)
    r = await _post(
        app, "/api/salla/webhook-diagnose/repair-integration",
        {"source_user_id": SOURCE_UID,
         "target_user_id": "some-other-user"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "target_not_self"


@pytest.mark.asyncio
async def test_same_user_rejected(db):
    app = _make_app(db, TARGET_UID)
    r = await _post(
        app, "/api/salla/webhook-diagnose/repair-integration",
        {"source_user_id": TARGET_UID, "target_user_id": TARGET_UID})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "same_user"


@pytest.mark.asyncio
async def test_source_not_found(db):
    app = _make_app(db, TARGET_UID)
    r = await _post(
        app, "/api/salla/webhook-diagnose/repair-integration",
        {"source_user_id": SOURCE_UID, "target_user_id": TARGET_UID})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "source_not_found"


@pytest.mark.asyncio
async def test_successful_move(db):
    # Seed integration under the source (admin) user.
    await db.salla_integrations.insert_one({
        "user_id":            SOURCE_UID,
        "status":             "connected",
        "store_id":           1673234356,
        "install_mode":       "easy_mode",
        "access_token_enc":   "opaque-access-token",
        "refresh_token_enc":  "opaque-refresh-token",
        "easy_mode_owner_email": "admin@hesab.app",
    })
    app = _make_app(db, TARGET_UID)
    r = await _post(
        app, "/api/salla/webhook-diagnose/repair-integration",
        {"source_user_id": SOURCE_UID, "target_user_id": TARGET_UID})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["moved"] is True
    assert body["store_id"] == 1673234356
    # Source is deleted, target holds the exact same tokens.
    src = await db.salla_integrations.find_one({"user_id": SOURCE_UID})
    tgt = await db.salla_integrations.find_one({"user_id": TARGET_UID})
    assert src is None
    assert tgt is not None
    assert tgt["store_id"]          == 1673234356
    assert tgt["status"]             == "connected"
    assert tgt["access_token_enc"]  == "opaque-access-token"
    assert tgt["refresh_token_enc"] == "opaque-refresh-token"
    assert tgt["moved_from_user_id"] == SOURCE_UID


@pytest.mark.asyncio
async def test_move_overwrites_existing_target(db):
    """If target already has an old integration, it gets replaced by
    the incoming (fresher) one."""
    await db.salla_integrations.insert_one({
        "user_id":  SOURCE_UID, "status": "connected",
        "store_id": 111, "access_token_enc": "new-tok",
    })
    await db.salla_integrations.insert_one({
        "user_id":  TARGET_UID, "status": "disconnected",
        "store_id": 999, "access_token_enc": "stale-tok",
    })
    app = _make_app(db, TARGET_UID)
    r = await _post(
        app, "/api/salla/webhook-diagnose/repair-integration",
        {"source_user_id": SOURCE_UID, "target_user_id": TARGET_UID})
    assert r.status_code == 200
    assert r.json()["target_replaced_existing"] is True
    tgt = await db.salla_integrations.find_one({"user_id": TARGET_UID})
    assert tgt["store_id"]         == 111        # from source
    assert tgt["status"]           == "connected"
    assert tgt["access_token_enc"] == "new-tok"
