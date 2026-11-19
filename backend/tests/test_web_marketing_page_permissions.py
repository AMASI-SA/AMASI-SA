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


def test_native_customer_and_marketing_tab_routes_cover_independent_grants():
    from pathlib import Path
    import re
    from web_marketing_page_policy import MARKETING_WEB_PAGES, PROVIDERS
    pages = {p["id"]: p for p in MARKETING_WEB_PAGES}
    base = Path(__file__).resolve().parents[2] / "frontend/src/pages"
    customer_src = (base / "CustomerIntelligenceCenter.jsx").read_text().split("export const CUSTOMER_INTELLIGENCE_TABS = [")[1].split("];")[0]
    provider_src = (base / "MarketingPlatformWorkspace.jsx").read_text().split("const TABS = [")[1].split("];")[0]
    addresses = {url: p["id"] for p in pages.values() for url in [p["to"], *p.get("aliases", [])]}
    for tab in re.findall(r'id: "(\w+)"', customer_src):
        routes_to = addresses["/customer-intelligence?tab=" + tab]
        assert routes_to == ("intelligence.inbox" if tab == "conversations" else "intelligence." + tab)
    for provider in PROVIDERS:
        for tab in re.findall(r'id: "(\w+)"', provider_src):
            if tab == "content" and provider != "tiktok":
                assert f"/ads-manager?provider={provider}&tab={tab}" not in addresses
                continue
            routes_to = addresses[f"/ads-manager?provider={provider}&tab={tab}"]
            page = "marketing." + provider
            if tab == "overview":
                assert routes_to == addresses["/ads-manager?provider=" + provider]
                continue
            assert routes_to == f"{page}.{tab}"
            uri = "/api/web-page-access-v1/marketing-read/" + routes_to
            assert enforce_marketing_web_pages({routes_to: "read"}, uri, "GET") is True
            for grants, method in [({page: "read"}, "GET"), ({routes_to: "read"}, "POST")]:
                with pytest.raises(HTTPException):
                    enforce_marketing_web_pages(grants, uri, method)


@pytest.mark.asyncio
async def test_signal_threads_are_merchant_channel_and_assignment_scoped():
    from web_marketing_workspace_reads import customer_workspace
    from customer_intelligence.foundation import CHANNELS_COLLECTION, CONVERSATIONS_COLLECTION
    from customer_intelligence.learning_contract import CUSTOMER_SIGNALS_COLLECTION
    from customer_intelligence.reply_suggestions import REPLY_SUGGESTIONS_COLLECTION
    db = AsyncMongoMockClient().db
    for actor, merchant, ch, owner, cid, status in [
        ("employee", "one", "channel", "store", "mine", "follow_up_due"),
        (None, "one", "channel", "store", "shared", "open"),
        ("other", "one", "channel", "store", "private", "needs_human"),
        ("employee", "two", "channel", "unrelated", "mine", "follow_up_due"),
        ("employee", "three", "off", "store", "inactive", "needs_human"),
    ]:
        tenant = {"user_id": owner, "merchant_id": merchant, "channel_id": ch}
        await db[CHANNELS_COLLECTION].update_one(tenant, {"$set": {**tenant, "provider": "whatsapp", "status": "connected",
                "ingress_enabled": ch != "off", "egress_mode": "disabled", "send_allowed": False, "ai_auto_reply_allowed": False}}, upsert=True)
        thread = {**tenant, "conversation_id": cid}
        await db[CONVERSATIONS_COLLECTION].insert_one({**thread, "customer_id": cid, "assigned_employee_id": actor,
                                                        "last_message_at": cid, "status": status})
        for signal in ("product_request", "objection", "response_quality", "intent"):
            await db[CUSTOMER_SIGNALS_COLLECTION].insert_one({**thread, "signal_type": signal, "commercial_impact": "sales_opportunity" if signal == "intent" else "none",
                        "source_role": "employee" if signal == "response_quality" else "customer",
                        "provider": "whatsapp", "created_at": cid, "review_status": "proposed",
                        "analysis_ciphertext": "must-never-appear"})
        for reviewer in ("employee", "other"):
            await db[REPLY_SUGGESTIONS_COLLECTION].insert_one({**thread, "reviewed_by": reviewer,
                           "review_decision": "approve", "status": "reviewed", "reviewed_at": cid})
    for tab in ("sales", "market", "objections", "quality", "audit", "customers"):
        result = await customer_workspace(db, "store", "employee", tab, 0)
        assert sorted(row[-1] for row in result["rows"]) == ["mine", "shared"]
        assert "must-never-appear" not in str(result)
    assert (await customer_workspace(db, "store", "employee", "overview", 0))["rows"] == [["follow_up_due", 1], ["open", 1]]
    assert [row[-1] for row in (await customer_workspace(db, "store", "employee", "followups", 0))["rows"]] == ["mine"]
    assert (await customer_workspace(db, "store", "employee", "integrations", 0))["rows"][0] == ["whatsapp", "connected", 1]


@pytest.mark.asyncio
async def test_live_and_original_preview_are_explicit_and_existing_feature_flags_hold(monkeypatch):
    from web_marketing_workspace_reads import customer_workspace
    from customer_intelligence.routes import FEATURE_FLAG_ENV, LIVE_INBOX_FEATURE_FLAG_ENV
    from ai_store_access_contract import ROLE_ASSIGNMENT_OWNER_FIELD, ROLE_ASSIGNMENTS
    db = AsyncMongoMockClient().db
    for tab in ("drafts", "campaigns", "knowledge"):
        result = await customer_workspace(db, "store", "employee", tab, 0)
        assert result["rows"] and "بيانات تجريبية" in result["notice"]
        assert "demo-" not in str(result)
    monkeypatch.setenv(FEATURE_FLAG_ENV, "false")
    monkeypatch.setenv(LIVE_INBOX_FEATURE_FLAG_ENV, "false")
    for tab in ("drafts", "overview"):
        with pytest.raises(HTTPException) as exc:
            await customer_workspace(db, "store", "employee", tab, 0)
        assert exc.value.status_code == 404
    await db[ROLE_ASSIGNMENTS].insert_one({ROLE_ASSIGNMENT_OWNER_FIELD: "store", "user_id": "employee",
                                 "denied_permissions": ["customer_intelligence.inbox.read"]})
    with pytest.raises(HTTPException) as exc:
        await customer_workspace(db, "store", "employee", "customers", 0)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_tiktok_content_history_has_own_grant_paginated_projection_and_no_other_tenant():
    from integrations_control_center.tiktok_content_publishing import PROPOSALS
    db = AsyncMongoMockClient().db
    await db.users.insert_one({"id": "store", "role": "owner"})
    for i in range(22):
        await db[PROPOSALS].insert_one({"user_id": "store", "account_label": "حساب", "kind": "video", "status": "processing",
                "delivery": "direct", "created_at": i, "plan_encrypted": "secret"})
    await db[PROPOSALS].insert_one({"user_id": "other", "account_label": "FOREIGN", "created_at": 1000})
    async def actor():
        return staff("marketing.tiktok.content")
    app = FastAPI()
    app.include_router(make_web_marketing_router(db, actor), prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/api/marketing-read/marketing.tiktok.content?user_id=other")
        assert first.status_code == 200
        assert first.json()["has_more"] and len(first.json()["rows"]) == 20
        assert "FOREIGN" not in first.text and "secret" not in first.text
        final = (await client.get("/api/marketing-read/marketing.tiktok.content?offset=20")).json()
        assert not final["has_more"] and [r[-1] for r in final["rows"]] == [1, 0]
        assert (await client.get("/api/marketing-read/marketing.tiktok.accounts")).status_code == 403
        assert (await client.post("/api/marketing-read/marketing.tiktok.content")).status_code == 405


@pytest.mark.asyncio
async def test_platform_campaigns_and_ai_read_saved_metrics_with_no_cross_platform_rows(monkeypatch):
    from web_marketing_workspace_reads import provider_workspace
    calls = []
    async def read(self, tenant, **kwargs):
        calls.append((tenant, kwargs["provider"], kwargs["page"], kwargs["limit"]))
        return {"campaigns": [{"provider": "meta", "campaign_name": "محلي", "spend_sar_equivalent": 0},
                              {"provider": "other", "campaign_name": "FOREIGN"}],
                "campaign_pagination": {"pages": 2}, "providers": [{"provider": "meta",
                        "metrics": {"provider_reported_spend_sar": 0}, "campaign_coverage": {"status": "available"}}]}
    monkeypatch.setattr("ads_manager.service.AdsManagerService.overview", read)
    assert (await provider_workspace(None, "tenant", "meta", "campaigns", offset=0))["rows"] == [["محلي", None, None, 0]]
    readiness = dict((await provider_workspace(None, "tenant", "meta", "ai", offset=0))["rows"])
    assert readiness["الصرف"] == "متاح" and readiness["الطلبات المنسوبة"] == "غير مكتمل"
    assert calls == [("tenant", "meta", 1, 20)] * 2


@pytest.mark.asyncio
async def test_empty_provider_workspaces_use_native_missing_account_state_without_bootstrapping():
    from web_marketing_page_policy import PROVIDER_WORKSPACES
    from web_marketing_workspace_reads import provider_workspace
    db = AsyncMongoMockClient().db
    for provider, tab in PROVIDER_WORKSPACES.values():
        result = await provider_workspace(db, "unconnected", provider, tab, offset=0)
        assert isinstance(result["rows"], list) and not result["has_more"]
        if provider in {"snapchat", "tiktok"} and tab in ("ai", "campaigns"):
            assert result["notice"] and not result["rows"]
    assert not await db.list_collection_names()
