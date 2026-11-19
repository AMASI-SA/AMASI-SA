import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from ads_manager import routes as ads_routes
from web_marketing_page_policy import enforce_marketing_web_pages, make_web_marketing_router


def staff(*grants):
    return {"id": "employee", "role": "user", "created_by": "store",
            "web_page_access": {"owner_id": "store", "grants": [{"id": p, "mode": "read"} for p in grants]}}


@pytest.mark.parametrize("method,path,query", [
    ("GET", "/api/marketing-reports", {"provider": "all"}),
    ("GET", "/api/marketing-reports", {"provider": "meta", "source": "salla"}),
    ("GET", "/api/marketing-reports", {"provider": "google"}),
    ("POST", "/api/integrations-v2/meta_ads/test-connection", {}),
    ("PUT", "/api/ads-manager/account-cost-settings/test", {}),
    ("GET", "/api/ads-manager/overview", {"provider": "meta"}),
    ("GET", "/api/web-page-access-v1/marketing-read/apps.advertising", {}),
])
def test_single_platform_grant_cannot_read_other_pages_or_run_commands(method, path, query):
    with pytest.raises(HTTPException) as error:
        enforce_marketing_web_pages({"marketing.meta": "read"}, path, method, query)
    assert error.value.status_code == 403
    assert enforce_marketing_web_pages({"marketing.meta": "read"}, "/api/marketing-reports", "GET",
                                      {"provider": "meta", "source": "platform"}) is True


@pytest.mark.asyncio
async def test_bound_staff_receives_only_own_merchant_and_own_instruction_history():
    db = AsyncMongoMockClient().db
    await db.users.insert_one({"id": "store", "role": "owner"})
    profiles = {"employee": staff("intelligence.tracking"), "denied": staff("apps.advertising")}
    collection = db.mezan_order_tracking_instructions_v1
    await collection.insert_many([
        {"id": str(i), "user_id": tenant, "created_by": actor, "order_number": number, "note": note,
         "created_at": "2026-11-19", "status": "active", "internal_secret": "hidden"}
        for i, (tenant, actor, number, note) in enumerate([
            ("store", "employee", "334", "visible"), ("store", "other", "334", "other staff"),
            ("other store", "employee", "334", "other store"), ("store", "employee", "335", "other order"),
        ])])
    async def current_user(request: Request):
        return profiles[request.headers["actor"]]
    app = FastAPI()
    app.include_router(make_web_marketing_router(db, current_user), prefix="/api/web-page-access-v1")
    url = "/api/web-page-access-v1/marketing-read/intelligence.tracking"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(url, params={"q": "334", "user_id": "other store"}, headers={"actor": "employee"})
        assert response.status_code == 200
        assert response.json()["rows"] == [["334", "visible", "2026-11-19", "active"]]
        assert response.headers["cache-control"] == "private, no-store"
        assert (await client.get(url, headers={"actor": "denied"})).status_code == 403
        await db.users.update_one({"id": "store"}, {"$set": {"disabled": True}})
        assert (await client.get(url, headers={"actor": "employee"})).status_code == 403


@pytest.mark.asyncio
async def test_platform_service_receives_scope_from_grant_not_employee_or_query(monkeypatch):
    db = AsyncMongoMockClient().db
    await db.users.insert_one({"id": "store", "role": "owner"})
    calls = []
    # The native route catches service range errors before serializing its full overview model.
    async def observe(self, owner, **kwargs):
        calls.append((owner, kwargs["provider"]))
        raise ValueError("invalid_date")
    monkeypatch.setattr(ads_routes.AdsManagerService, "overview", observe)
    app = FastAPI()
    session = {"user": staff("marketing.overview")}
    async def actor():
        return session["user"]
    app.include_router(ads_routes.make_ads_manager_router(db, actor), prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/ads-manager/overview?provider=meta&user_id=other")
        assert response.status_code == 400
        assert calls == [("store", "meta")]
        session["user"] = staff("marketing.meta")
        assert (await client.get("/api/ads-manager/overview?provider=all")).status_code == 403
        assert calls == [("store", "meta")]


@pytest.mark.asyncio
async def test_cohort_read_uses_merchant_defaults_without_creating_or_migrating_them(monkeypatch):
    from mezan_attribution_owner_routes import make_mezan_attribution_owner_router

    db = AsyncMongoMockClient().db
    await db.users.insert_one({"id": "store", "role": "owner"})
    calls = []

    async def cohort(database, tenant, **options):
        calls.append((tenant, options["included_statuses"]))
        return {"cohorts": []}

    monkeypatch.setattr("mezan_attribution_owner_routes.build_customer_cohort_report", cohort)
    async def actor():
        return staff("marketing.cohorts")
    app = FastAPI()
    app.include_router(make_mezan_attribution_owner_router(db, actor), prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.get("/api/mezan-attribution-v1/customer-cohorts?user_id=foreign")
        assert result.status_code == 200
        assert calls == [("store", [])]
        assert await db.settings.count_documents({}) == 0
