"""Exercise web grants through both actual authentication and independent legacy dependencies."""
from urllib.parse import urlsplit
from pathlib import Path
import re

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from auth import create_access_token, get_current_user_from_db
from recurring_obligations_routes import make_recurring_obligations_router, WAREHOUSES
from web_page_access import FIELD, PAGES, make_web_page_access_router, web_page_access_for_user


def test_every_visible_navigation_target_has_a_catalogue_decision():
    src = Path(__file__).resolve().parents[2] / "frontend/src/components"
    routes = {
        url for filename in ("Sidebar.jsx", "MezanV2NavigationShell.jsx", "MezanV2NavigationShellLegacy.jsx")
        for url in re.findall(r'\bto:\s*"(\/[^"]*)"', (src / filename).read_text())
    }
    mapped = {url for row in PAGES for url in [row["to"], *row.get("aliases", [])]}
    # Meta app's review-only credentials have their own restricted navigation.
    routes.discard("/integrations-v2?provider=meta_ads")
    assert routes <= mapped
    assert len({page["id"] for page in PAGES}) == len(PAGES)
    assert len({page["to"] for page in PAGES}) == len(PAGES)
    assert all(urlsplit(page["to"]).path.startswith("/") and page["label"] and page["section_label"] for page in PAGES)


@pytest.mark.asyncio
async def test_one_signed_browser_session_observes_grant_revocation_and_exact_tenant(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-only-page-boundary-secret-for-signed-session")
    store = AsyncMongoMockClient().page_grant_test
    await store.users.insert_many([
        {"id": "owner", "role": "owner"},
        {"id": "other", "role": "owner"},
        {"id": "staff", "created_by": "owner", "role": "employee", "_web_workspace_read": ["finance.obligations", "other"]},
    ])
    await store[WAREHOUSES].insert_many([
        {"id": "our-warehouse", "user_id": "owner", "status": "active"},
        {"id": "other-warehouse", "user_id": "other", "status": "active"},
    ])

    async def signed_actor(request: Request):
        return await get_current_user_from_db(request, store)

    app = FastAPI()
    router = APIRouter(prefix="/api")
    router.include_router(make_web_page_access_router(store, signed_actor))
    router.include_router(make_recurring_obligations_router(store, signed_actor))

    @router.get("/private-unregistered-workspace")
    async def private_workspace(actor=Depends(signed_actor)):
        return actor["id"]

    app.include_router(router)
    owner = {"Authorization": "Bearer " + create_access_token("owner", "owner@example.invalid", mfa_verified=True)}
    staff = {"Authorization": "Bearer " + create_access_token("staff", "staff@example.invalid", mfa_verified=True)}
    target = "/api/web-page-access-v1/staff"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.put(target, headers=owner, json={"page_modes": {"finance.obligations": "read"}, "expected_revision": 0})).status_code == 200
        options = await client.get("/api/recurring-obligations/options", headers=staff)
        assert options.status_code == 200, options.text
        assert [r["id"] for r in options.json()["locations"]] == ["our-warehouse"]
        assert (await client.get("/api/private-unregistered-workspace", headers=staff)).status_code == 403
        assert (await client.get("/api/web-page-access-v1", headers=staff)).status_code == 403
        assert (await client.post("/api/recurring-obligations", headers=staff, json={})).status_code == 403
        for invalid in ({"employees.permissions": "read"}, {"finance.obligations": "write"}):
            assert (await client.put(target, headers=owner, json={"page_modes": invalid, "expected_revision": 1})).status_code == 422
        assert (await client.put(target, headers=owner, json={"page_modes": {}, "expected_revision": 1})).status_code == 200
        assert (await client.get("/api/recurring-obligations/options", headers=staff)).status_code == 403
    bad_member = {"id": "staff", "created_by": "owner", "role": "employee", FIELD: {
        "owner_id": "owner", "grants": [{"id": "employees.permissions", "mode": "read"}]}}
    assert web_page_access_for_user(bad_member) == {"pages": []}


def test_self_service_is_actor_bound_even_without_store_pages():
    from fastapi import HTTPException
    from web_page_access import enforce_web_catalog_access
    user = {"id": "staff", "created_by": "owner", "role": "employee",
            FIELD: {"owner_id": "owner", "grants": []}}
    for field in ["name", "email", "password", "security-question"]:
        assert enforce_web_catalog_access(user, "/api/auth/profile/" + field, "PUT") is None
    for path, method in [
        ("/api/auth/me", "GET"), ("/api/auth/refresh", "POST"), ("/api/auth/logout", "POST")
    ]:
        assert enforce_web_catalog_access(user, path, method) is None
    for path, method in [
        ("/api/auth/profile/password", "GET"), ("/api/auth/profile/other/password", "PUT"),
        ("/api/team/users/owner", "PUT"), ("/api/auth/passkey/trust/options", "POST")
    ]:
        with pytest.raises(HTTPException) as caught:
            enforce_web_catalog_access(user, path, method)
        assert caught.value.status_code == 403
