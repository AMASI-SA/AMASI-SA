"""Actual Google router and auth dependency, synthetic JWT/DB/provider only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, FastAPI, Request
import httpx
import jwt
import pytest

import auth
from integrations_control_center.google_ads_routes import attach_google_ads_management_routes
from tests.test_google_merchant_observation_routes import DB, OWNER, ACCOUNT, MERCHANT, CREDS, providers

BASE = "/integrations-v2/google_ads/merchant-products"
SECRET = "synthetic-test-only-signing-key-with-thirty-two-characters"
REAL_CLIENT = httpx.AsyncClient


def app_for(monkeypatch, db, *, after_auth=None):
    monkeypatch.setattr(auth, "get_jwt_secret", lambda: SECRET)
    class AuthUsers:
        async def find_one(self, query, projection=None):
            row = next((row for row in db.rows["users"] if all(row.get(key) == value for key, value in query.items())), None)
            if row is None: return None
            return deepcopy(row) if projection is None else {key: deepcopy(value) for key, value in row.items() if projection.get(key) == 1}
    db.users = AuthUsers()
    async def current_user(request: Request):
        user = await auth.get_current_user_from_db(request, db)
        if after_auth: after_auth()
        return user
    router = APIRouter(prefix="/integrations-v2")
    attach_google_ads_management_routes(router, db, current_user)
    app = FastAPI(); app.include_router(router); app.state.google_router = router
    return app


async def request(app, suffix, *, params=None, token=True):
    now = datetime.now(timezone.utc)
    headers = {"Authorization": "Bearer " + jwt.encode({"sub": OWNER, "type": "access", "mfa": True,
        "iat": now, "exp": now + timedelta(minutes=5)}, SECRET, algorithm=auth.JWT_ALGORITHM)} if token else {}
    async with REAL_CLIENT(transport=httpx.ASGITransport(app=app), base_url="https://synthetic.invalid") as client:
        return await client.get(BASE + suffix, params=params, headers=headers)


@pytest.mark.asyncio
async def test_full_google_router_registers_both_paths_once_and_preserves_commercial_routes(monkeypatch):
    db = DB(); app = app_for(monkeypatch, db)
    paths = [route.path for route in app.state.google_router.routes]
    assert paths.count(BASE + "/choices") == paths.count(BASE + "/observation") == 1
    assert any("/commercial-plans/" in path for path in paths)
    assert any("prior-season" in path for path in paths)
    assert any("/management/readiness" in path for path in paths)
    before = deepcopy(db.rows)
    response = await request(app, "/choices")
    assert response.status_code == 200, response.text
    assert response.json()["owner_id"] == OWNER and response.json()["local_account_count"] == 2
    assert response.json()["merchant_access_verified"] is False
    assert CREDS not in {row[0] for row in db.reads}
    assert db.rows == before


@pytest.mark.asyncio
async def test_attached_observation_uses_actual_auth_and_reviewed_native_boundary(monkeypatch):
    db = DB(); app = app_for(monkeypatch, db); calls = providers(monkeypatch)
    before = deepcopy(db.rows)
    response = await request(app, "/observation", params={"account_id": ACCOUNT, "merchant_id": MERCHANT})
    assert response.status_code == 200, response.text
    assert response.json()["products"][0]["offer_id"] == "SKU1"
    assert response.json()["store_product_mapping_verified"] is False
    assert calls["merchant"] == 1 and calls["links"] == calls["metadata"] == 2
    assert db.rows == before


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix,params", [("/choices", {"account_id": ACCOUNT}), ("/observation", {})])
async def test_attached_routes_do_not_infer_or_adopt_caller_defaults(monkeypatch, suffix, params):
    db = DB(); app = app_for(monkeypatch, db); calls = providers(monkeypatch)
    response = await request(app, suffix, params=params)
    assert response.status_code == 422 and calls["constructed"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["/choices", "/observation"])
async def test_actual_auth_denies_revoked_password_before_any_provider_read(monkeypatch, suffix):
    db = DB(); db.rows["users"][0]["password_updated_at"] = datetime.now(timezone.utc) + timedelta(minutes=1)
    app = app_for(monkeypatch, db); calls = providers(monkeypatch)
    response = await request(app, suffix, params={"account_id": ACCOUNT, "merchant_id": MERCHANT} if suffix == "/observation" else None)
    assert response.status_code == 401 and calls["constructed"] == 0
    assert db.transactions == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["/choices", "/observation"])
async def test_owner_change_after_real_auth_before_snapshot_refuses_both_attached_paths(monkeypatch, suffix):
    db = DB()
    def change(): db.rows["users"][0]["password_updated_at"] = datetime.now(timezone.utc)
    app = app_for(monkeypatch, db, after_auth=change); calls = providers(monkeypatch)
    response = await request(app, suffix, params={"account_id": ACCOUNT, "merchant_id": MERCHANT} if suffix == "/observation" else None)
    assert response.status_code == 403 and calls["constructed"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["/choices", "/observation"])
async def test_attached_paths_require_actual_auth_not_a_frontend_owner_boolean(monkeypatch, suffix):
    app = app_for(monkeypatch, DB()); calls = providers(monkeypatch)
    response = await request(app, suffix, params={"account_id": ACCOUNT, "merchant_id": MERCHANT} if suffix == "/observation" else None, token=False)
    assert response.status_code == 401 and calls["constructed"] == 0
