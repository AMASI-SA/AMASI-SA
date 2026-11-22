"""Exercise web grants through both actual authentication and independent legacy dependencies."""
from urllib.parse import urlsplit
from pathlib import Path
import re

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from auth import create_access_token, get_current_user_from_db
from operational_web_page_policy import OPERATIONAL_OWNER_REVIEW_PAGES
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


def test_all_authenticated_app_routes_have_a_grant_replacement_or_owner_review_policy():
    app = Path(__file__).resolve().parents[2] / "frontend/src/App.js"
    source = app.read_text()
    routes = set(re.findall(r'\bpath="([^"]+)"', source))
    visible_paths = {urlsplit(address).path for page in PAGES for address in (page["to"], *page.get("aliases", []))}
    visible_paths.update(page["detail_route"] for page in PAGES if "detail_route" in page)
    routing_only = {"/login", "/register", "/", "*", "/employee-detail/:pageId", "/integrations/qoyod/*"}
    owner_reviews = {page["to"] for page in OPERATIONAL_OWNER_REVIEW_PAGES}
    assert owner_reviews == {"/operational-balances/shipping-review", "/operational-balances/provider-history"}
    assert {page["to"]: page["api_paths"] for page in OPERATIONAL_OWNER_REVIEW_PAGES} == {
        "/operational-balances/shipping-review": ("/completed-shipping-fees/preview", "/completed-shipping-fees/approve"),
        "/operational-balances/provider-history": ("/provider-history-reviews", "/provider-history-reviews/approve"),
    }
    assert owner_reviews <= routes and owner_reviews.isdisjoint(visible_paths)
    assert routes <= visible_paths | routing_only | owner_reviews
    for page in OPERATIONAL_OWNER_REVIEW_PAGES:
        assert page["owner_only"] is True and page["owner_permission"] == "bank_manage"
        assert page["id"] not in {grant["id"] for grant in PAGES}
        assert re.search(r'<Route\s+path="' + re.escape(page["to"]) +
                         r'"\s+element=\{\s*<ProtectedRoute>\s*<OwnerOnlyRoute>', source)
    assert all(not page.get("owner_only") for page in PAGES)


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
        for invalid in ({"employees.permissions": "write"}, {"finance.obligations": "write"},
                        {"balances.shipping_review": "read"}, {"balances.provider_history": "write"}):
            assert (await client.put(target, headers=owner, json={"page_modes": invalid, "expected_revision": 1})).status_code == 422
        catalogue = (await client.get("/api/web-page-access-v1", headers=owner)).json()["pages"]
        all_pages = {page["id"]: "read" for page in catalogue}
        assigned = await client.put(target, headers=owner, json={"page_modes": all_pages, "expected_revision": 1})
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["member"]["page_modes"] == all_pages
        stored = await store.users.find_one({"id": "staff"})
        assert {r["id"] for r in web_page_access_for_user(stored)["pages"]} == set(all_pages)
        assert (await client.get("/api/web-page-access-v1", headers=staff)).status_code == 403
        assert (await client.put(target, headers=staff, json={"page_modes": {}, "expected_revision": 2})).status_code == 403
        assert (await client.post("/api/recurring-obligations", headers=staff, json={})).status_code == 403
        assert (await client.put(target, headers=owner, json={"page_modes": {}, "expected_revision": 2})).status_code == 200
        assert (await client.get("/api/recurring-obligations/options", headers=staff)).status_code == 403
    invalid_grant = {"id": "staff", "created_by": "owner", "role": "employee", FIELD: {
        "owner_id": "other", "grants": [{"id": "employees.permissions", "mode": "read"}]}}
    assert web_page_access_for_user(invalid_grant) == {"pages": []}


@pytest.mark.parametrize("method,path,payload", [
    ("POST", "/completed-shipping-fees/preview", {"order_number": "123"}),
    ("POST", "/completed-shipping-fees/approve", {
        "order_number": "123", "request_id": "owner-review-test",
        "expected_preview_hash": "a" * 64, "expected_session_scope": "b" * 64,
        "evidence_ref": "reviewed source", "confirm_opening_and_payouts": True,
    }),
    ("GET", "/provider-history-reviews", None),
    ("POST", "/provider-history-reviews/approve", {
        "order_id": "123", "request_id": "owner-review-test",
        "expected_preview_hash": "a" * 64, "expected_session_scope": "b" * 64,
        "evidence_id": "c" * 64, "statement_reference": "source-reference",
        "confirmed_outstanding": True,
    }),
])
def test_owner_review_routes_cannot_be_delegated_by_all_or_forged_page_grants(monkeypatch, method, path, payload):
    from operational_balance_access import OPERATION_LABELS
    from operational_balance_store import STATES
    import operational_completed_shipping_service as shipping
    import operational_provider_history_service as provider
    from test_operational_balance_access import BASE, headers, run, save_policy
    from web_page_access import configured_page_modes

    # Exercise the actual router/actor scope while replacing only the financial
    # service body: authorization must happen before any preview or approval.
    calls = []

    async def authorized_service(db, owner, actor, *args):
        calls.append((owner, actor))
        return {"owner": owner, "actor": actor}

    for module, functions in ((shipping, ("preview", "approve")), (provider, ("candidates", "approve"))):
        for name in functions:
            monkeypatch.setattr(module, name, authorized_service)

    async def scenario(db, client):
        before = await db[STATES].find_one({"_id": "owner"})
        modes = {page["id"]: "write" if "write_label" in page else "read" for page in PAGES}
        forged = {"balances.shipping_review": "write", "balances.provider_history": "write"}
        await save_policy(db, OPERATION_LABELS)
        await db.users.update_one({"id": "staff"}, {"$set": {FIELD: {
            "owner_id": "owner", "revision": 1,
            "grants": [{"id": key, "mode": mode} for key, mode in {**modes, **forged}.items()],
        }}})
        staff = await db.users.find_one({"id": "staff"})
        assert configured_page_modes(staff) == modes
        request = {"json": payload} if payload is not None else {}
        denied = await client.request(method, BASE + path, headers=headers(), **request)
        assert denied.status_code == 403, denied.text
        assert calls == []
        # Legacy non-managed employees cannot inherit the owner's authority.
        await db.users.update_one({"id": "staff"}, {"$unset": {FIELD: ""}})
        legacy = await client.request(method, BASE + path, headers=headers(), **request)
        assert legacy.status_code == 403, legacy.text
        assert calls == []
        allowed = await client.request(method, BASE + path, headers=headers("owner"), **request)
        assert allowed.status_code == 200, allowed.text
        assert allowed.json() == {"owner": "owner", "actor": "owner"}
        assert calls == [("owner", "owner")]
        assert await db[STATES].find_one({"_id": "owner"}) == before

    run(scenario)


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


@pytest.mark.asyncio
async def test_chart_page_gets_saved_spend_quality_without_administration_or_sync(monkeypatch):
    from integrations_control_center import dashboard_ads_platform_spend_routes as spend
    from web_page_access import bind_workspace_reader, enforce_web_catalog_access

    db = AsyncMongoMockClient().chart_pages
    await db.users.insert_one({"id": "shop", "role": "owner"})
    user = {"id": "viewer", "role": "employee", "created_by": "shop",
            FIELD: {"owner_id": "shop", "grants": [{"id": "home.charts", "mode": "read"}]}}
    async def viewer(request: Request):
        actor = user.copy()
        enforce_web_catalog_access(actor, request.url.path, request.method, request.query_params)
        await bind_workspace_reader(db, actor, request.url.path, request.method, request.query_params)
        return actor
    async def snapshot(db, owner_id, **dates):
        assert owner_id == "shop"
        return {"total_sar": None, "known_total_sar": 75, "spend_quality": {"amount_complete": False, "accounts": ["sensitive"]},
                "daily_spend": [], "providers": {"accounts": ["sensitive"]}}
    monkeypatch.setattr(spend, "build_dashboard_platform_spend", snapshot)
    app = FastAPI()
    router = APIRouter(prefix="/api/integrations-v2")
    def owner_only(_):
        raise AssertionError("a chart grant must not receive an owner principal")
    spend.attach_dashboard_ads_platform_spend_routes(router, db, viewer, owner_only)
    app.include_router(router)
    path = "/api/integrations-v2/dashboard/ads-platform-spend"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path, params={"date_from": "2026-04-01", "date_to": "2026-04-01"})
        assert response.status_code == 200, response.text
        assert response.json() == {"total_sar": None, "known_total_sar": 75, "daily_spend": [],
                                   "spend_quality": {"amount_complete": False}}
        assert (await client.post(path + "/refresh", json={"date_from": "2026-04-01", "date_to": "2026-04-01"})).status_code == 403
