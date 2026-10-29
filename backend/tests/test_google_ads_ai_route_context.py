"""Typed draft context stays within the selected campaign family."""
from types import SimpleNamespace
import asyncio

import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException

from integrations_control_center import google_ads_routes as routes
from integrations_control_center import google_ads_ai


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["SHOPPING", "PERFORMANCE_MAX"])
@pytest.mark.parametrize("case", ["unique", "collision", "language", "unknown", "merchant", "account", "incomplete", "model_feed_change"])
async def test_retail_ai_route_binds_unique_native_offer_and_generated_feed(monkeypatch, family, case):
    from tests.test_google_ads_workspace import retail_inventory, CUSTOMER
    inventory = retail_inventory()
    if case == "collision": inventory["items"] += retail_inventory(feed="QA", status="NOT_ELIGIBLE", availability="OUT_OF_STOCK")["items"]
    if case == "language": inventory["items"] += retail_inventory(language="en")["items"]
    if case == "unknown": inventory["items"][0]["availability"] = None
    if case == "merchant": inventory["items"][0]["merchant_id"] = "999"
    if case == "account": inventory["items"][0]["resource_name"] = inventory["items"][0]["resource_name"].replace(CUSTOMER, "777")
    if case == "incomplete": inventory["complete"] = False
    contexts = []
    client = SimpleNamespace(closed=False)
    async def metadata(): return {"account_id": CUSTOMER, "currency_code": "SAR", "time_zone": "Asia/Riyadh"}
    async def assets(): return [], True
    async def close(): client.closed = True
    client.account_metadata, client.assets, client.close = metadata, assets, close
    async def principal(*args, **kwargs): return {"user_id": "owner", "actor_id": "owner", "owner": True, "can_draft": True}
    async def account_client(*args, **kwargs): return client, "epoch"
    async def lookup(provider, kind, **kwargs):
        assert provider is client
        if kind == "products": return inventory
        if kind == "merchants": return {"items": [{"id": "123"}], "complete": True}
        return {"items": [], "complete": True}
    async def generate(*args, **kwargs):
        contexts.append(kwargs["account_context"])
        return {"draft": {"shopping": {"merchant_id": "123", "feed_label": "QA" if case == "model_feed_change" else "SA"},
                          "groups": [{"product_offer_ids": ["SKU1"]}]}, "provider_writes": 0}
    monkeypatch.setattr(routes, "principal", principal)
    monkeypatch.setattr(routes, "account_client", account_client)
    monkeypatch.setattr(routes, "google_lookup", lookup)
    monkeypatch.setattr(google_ads_ai, "generate_google_campaign_draft", generate)
    app, router = FastAPI(), APIRouter()
    async def user(): return {"id": "owner"}
    routes.attach_google_ads_management_routes(router, object(), user)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as http:
        response = await http.post("/google_ads/ai/draft", json={"account_id": CUSTOMER, "campaign_type": family,
            "prompt": "مسودة اختبار", "budget_ceiling_native": "1", "product_ids": ["1"],
            "product_offer_selections": [{"product_id": "1", "merchant_id": "123", "offer_id": "SKU1"}]})
    assert client.closed is True
    if case == "unique":
        assert response.status_code == 200, response.text
        association = contexts[0]["product_offer_associations"][0]
        assert association["verification_status"] == "provider_eligibility_verified"
        assert association["mapping_status"] == "owner_selected_unverified" and association["feed_label"] == "SA"
    else:
        assert response.status_code in {409, 422}, response.text
        assert len(contexts) == (1 if case == "model_feed_change" else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("required_failure", [False, True])
async def test_search_context_skips_unrelated_permissions_and_drains_failed_required_reads(monkeypatch, required_failure):
    calls, model_contexts = [], []
    client = SimpleNamespace(closed=False)
    pending, started = set(), asyncio.Event()

    async def metadata():
        return {"account_id": "123", "currency_code": "SAR", "time_zone": "Asia/Riyadh"}

    async def assets():
        return [], True

    async def close():
        assert not pending, "a provider read outlived its request/client"
        client.closed = True

    client.account_metadata, client.assets, client.close = metadata, assets, close

    async def principal(*args, **kwargs):
        return {"user_id": "owner", "actor_id": "owner", "owner": True, "can_draft": True}

    async def account_client(db, user_id, account_id, **kwargs):
        assert user_id == "owner" and account_id == "123"
        return client, "epoch"

    async def lookup(provider, kind, **kwargs):
        assert provider is client
        calls.append(kind)
        if kind in {"hotels", "apps", "merchants"}:
            raise HTTPException(status_code=403, detail={"code": "unrelated_account_permission"})
        if required_failure and kind == "languages":
            await asyncio.wait_for(started.wait(), timeout=1)
            raise HTTPException(status_code=403, detail={"code": "required_language_permission"})
        if required_failure:
            pending.add(kind); started.set()
            try:
                await asyncio.sleep(10)
            finally:
                pending.remove(kind)
        return {"items": [], "complete": True}

    async def generate(db, user_id, request, **kwargs):
        assert user_id == "owner" and request.campaign_type == "SEARCH"
        model_contexts.append(kwargs["account_context"])
        return {"provider_writes": 0, "campaign_type": "SEARCH"}

    monkeypatch.setattr(routes, "principal", principal)
    monkeypatch.setattr(routes, "account_client", account_client)
    monkeypatch.setattr(routes, "google_lookup", lookup)
    monkeypatch.setattr(google_ads_ai, "generate_google_campaign_draft", generate)
    app, router = FastAPI(), APIRouter()

    async def current_user():
        return {"id": "owner"}

    routes.attach_google_ads_management_routes(router, object(), current_user)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as http:
        response = await http.post("/google_ads/ai/draft", json={"account_id": "123", "campaign_type": "SEARCH", "prompt": "مسودة اختبار تقنية", "budget_ceiling_native": "1"})
    if required_failure:
        assert response.status_code == 403 and response.json()["detail"]["code"] == "required_language_permission"
        assert model_contexts == [] and client.closed is True and not pending
        return
    assert response.status_code == 200, response.text
    assert set(calls) == {"languages", "audiences", "user_lists", "conversion_actions", "conversion_goals"}
    assert len(model_contexts) == 1 and client.closed is True
    assert model_contexts[0]["merchant_ids"] == model_contexts[0]["apps"] == model_contexts[0]["hotel_center_ids"] == []


class AttemptCursor:
    def __init__(self, rows): self.rows = rows
    def sort(self, key, direction):
        self.rows = sorted(self.rows, key=lambda row: row.get(key, ""), reverse=direction < 0)
        return self
    async def to_list(self, length): return self.rows[:length]


class AttemptCollection:
    def __init__(self): self.rows, self.indexes, self.fail_insert, self.fail_update, self.fail_find = [], [], False, False, False
    async def create_index(self, *args, **kwargs): self.indexes.append((args, kwargs))
    async def insert_one(self, row):
        from copy import deepcopy
        if self.fail_insert: raise RuntimeError("private storage connection details")
        self.rows.append(deepcopy(row))
    async def update_one(self, scope, update):
        if self.fail_update: raise RuntimeError("private diagnostic storage details")
        for row in self.rows:
            if all(row.get(key) == value for key, value in scope.items()): row.update(update["$set"])
    async def find_one(self, scope, projection=None):
        if self.fail_find: raise RuntimeError("private policy storage details")
        return next((row for row in self.rows if all(row.get(k) == v for k, v in scope.items())), None)
    def find(self, scope, projection=None):
        return AttemptCursor([row.copy() for row in self.rows if all(row.get(k) == v for k, v in scope.items())])


class AttemptDB:
    def __init__(self): self.collections = {}
    def __getitem__(self, name): return self.collections.setdefault(name, AttemptCollection())


def analysis_app(monkeypatch, db, *, failure=None):
    from integrations_control_center.google_ads_management import ANALYSES, POLICIES
    async def actor(*args, **kwargs):
        return {"user_id": "owner", "actor_id": "owner", "owner": True, "can_read": True}
    async def reader(*args, **kwargs):
        if failure == "native": raise HTTPException(503, detail={"code": "native_read_failed", "private": "provider secret"})
        if failure == "cancel": raise asyncio.CancelledError()
        return {"complete": True, "campaigns": []}
    async def analyzer(*args, **kwargs):
        if failure == "model": raise HTTPException(504, detail={"code": "google_ai_timeout", "private": "model prompt"})
        assert set(kwargs["facts"]["permitted_actions"]) == {"PAUSE_CAMPAIGN", "RESUME_CAMPAIGN"}
        return {"summary": "private analysis narrative", "recommendations": [], "provider_writes": 0}
    if failure == "save": db[ANALYSES].fail_insert = True
    if failure == "policy": db[POLICIES].fail_find = True
    monkeypatch.setattr(routes, "principal", actor)
    monkeypatch.setattr(routes, "read_google_workspace", reader)
    monkeypatch.setattr(google_ads_ai, "analyze_google_campaigns", analyzer)
    app, router = FastAPI(), APIRouter()
    async def user(): return {"id": "owner"}
    routes.attach_google_ads_management_routes(router, db, user)
    app.include_router(router)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status,phase,code", [
    (None, 200, "save_analysis", None),
    ("native", 503, "native_workspace", "native_read_failed"),
    ("policy", 502, "policy_read", "google_ai_analysis_failed"),
    ("model", 504, "structured_analysis", "google_ai_timeout"),
    ("save", 502, "save_analysis", "google_ai_analysis_failed"),
])
async def test_analysis_attempts_distinguish_native_model_and_storage_failures_without_private_data(monkeypatch, failure, status, phase, code):
    from integrations_control_center.google_ads_ai_runs import ATTEMPTS
    db = AttemptDB()
    app = analysis_app(monkeypatch, db, failure=failure)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as http:
        response = await http.post("/google_ads/ai/analyze", json={"account_id": "123", "from_date": "2026-10-25", "to_date": "2026-10-25"})
        assert response.status_code == status
        history = await http.get("/google_ads/ai/analysis-attempts", params={"account_id": "123"})
    assert history.status_code == 200
    row = history.json()["items"][0]
    assert row["phase"] == phase and row.get("error_code") == code
    assert row["status"] == ("complete" if failure is None else "failed")
    assert row["provider_writes"] == 0 and row["duration_ms"] >= 0
    assert "private" not in str(row) and "secret" not in str(row) and "prompt" not in str(row)
    assert "owner" not in str(row)
    if failure is None: assert row["analysis_id"] == response.json()["analysis_id"]
    assert len(db[ATTEMPTS].rows) == 1


@pytest.mark.asyncio
async def test_analysis_attempt_history_is_tenant_account_period_scoped_and_bounded(monkeypatch):
    from integrations_control_center.google_ads_ai_runs import ATTEMPTS
    db = AttemptDB()
    app = analysis_app(monkeypatch, db)
    for index in range(25):
        db[ATTEMPTS].rows.append({"user_id": "owner", "account_id": "123", "analysis_id": str(index),
            "date_from": "2026-10-25", "date_to": "2026-10-25", "created_at": f"{index:03}",
            "status": "complete", "phase": "save_analysis", "provider_writes": 0,
            "raw_body": "private secret", "actor_id": "owner"})
    db[ATTEMPTS].rows.extend([
        {"user_id": "other", "account_id": "123", "created_at": "999", "analysis_id": "foreign"},
        {"user_id": "owner", "account_id": "999", "created_at": "999", "analysis_id": "other-account"},
    ])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as http:
        response = await http.get("/google_ads/ai/analysis-attempts", params={"account_id": "123", "date_from": "2026-10-25", "date_to": "2026-10-25"})
        invalid = await http.get("/google_ads/ai/analysis-attempts", params={"account_id": "123", "date_from": "2026-10-25"})
    assert invalid.status_code == 422
    rows = response.json()["items"]
    assert len(rows) == 20 and rows[0]["analysis_id"] == "24" and rows[-1]["analysis_id"] == "5"
    assert "private" not in response.text and "actor_id" not in response.text


@pytest.mark.asyncio
async def test_disconnected_analysis_caller_is_distinct_from_provider_failure(monkeypatch):
    from integrations_control_center.google_ads_ai_runs import ATTEMPTS
    db = AttemptDB()
    app = analysis_app(monkeypatch, db, failure="cancel")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as http:
        with pytest.raises(asyncio.CancelledError):
            await http.post("/google_ads/ai/analyze", json={"account_id": "123", "date_from": "2026-10-25", "date_to": "2026-10-25"})
    row = db[ATTEMPTS].rows[0]
    assert row["status"] == "cancelled" and row["phase"] == "native_workspace"
    assert row["error_code"] == "google_ai_request_cancelled" and row["provider_writes"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["insert", "update"])
async def test_optional_diagnostics_cannot_turn_successful_analysis_into_failure(monkeypatch, caplog, failure):
    from integrations_control_center.google_ads_ai_runs import ATTEMPTS
    from integrations_control_center.google_ads_management import ANALYSES
    db = AttemptDB()
    setattr(db[ATTEMPTS], "fail_" + failure, True)
    app = analysis_app(monkeypatch, db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as http:
        response = await http.post("/google_ads/ai/analyze", json={"account_id": "123", "date_from": "2026-10-25", "date_to": "2026-10-25"})
    assert response.status_code == 200
    assert len(db[ANALYSES].rows) == 1
    assert "google_ai_attempt_record_unavailable" in caplog.text
    assert "private" not in caplog.text


@pytest.mark.asyncio
async def test_attempt_history_still_requires_current_ads_read_permission(monkeypatch):
    db = AttemptDB()
    app = analysis_app(monkeypatch, db)
    async def denied(*args, **kwargs): raise HTTPException(403, detail={"code": "ads_read_denied"})
    monkeypatch.setattr(routes, "principal", denied)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as http:
        response = await http.get("/google_ads/ai/analysis-attempts", params={"account_id": "123"})
        post = await http.post("/google_ads/ai/analyze", json={"account_id": "123", "date_from": "2026-10-25", "date_to": "2026-10-25"})
    assert response.status_code == post.status_code == 403
    assert not db.collections
