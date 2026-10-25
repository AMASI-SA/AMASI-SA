"""Owner-scoped readiness loads trusted storage and unsegmented native inventory."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException

from integrations_control_center import google_ads_automation_plan as plan
from integrations_control_center import google_ads_automation_policy as policy
from integrations_control_center.google_ads_management import ANALYSES, PROPOSALS

NOW = datetime(2026, 10, 26, 1, tzinfo=timezone.utc)


class Cursor:
    def __init__(self, rows): self.rows = rows
    def sort(self, *args): return self
    async def to_list(self, length): return deepcopy(self.rows[:length])


class Collection:
    def __init__(self, rows=()): self.rows, self.reads = deepcopy(list(rows)), []
    async def find_one(self, scope, projection=None):
        self.reads.append((scope, projection))
        return next((deepcopy(row) for row in self.rows if all(row.get(k) == v for k, v in scope.items())), None)
    def find(self, scope, projection=None):
        self.reads.append((scope, projection))
        rows = [row for row in self.rows if all(row.get(k) == v for k, v in scope.items())]
        if projection and any(value == 1 for value in projection.values()):
            rows = [{key:value for key,value in row.items() if projection.get(key) == 1} for row in rows]
        return Cursor(rows)


class DB:
    def __init__(self):
        self.users = Collection([{"id": "owner", "role": "owner"}])
        self.collections = {}
    def __getitem__(self, name): return self.collections.setdefault(name, Collection())


def fixtures():
    period = dict(account_id="123", currency_code="SAR", time_zone="Asia/Riyadh", date_from="2026-10-25", date_to="2026-10-25")
    evidence = dict(evidence_id="google:campaign:123:456:spend_native", campaign_id="456", source="google_ads_api",
                    metric="spend_native", value="20", observed_at=NOW.isoformat(), **{k:v for k,v in period.items() if k != "account_id"})
    rec = dict(recommendation_id="rec1", provider="google_ads", account_id="123", campaign_id="456", action="PAUSE_CAMPAIGN",
               confidence="high", evidence=[evidence], proposed_budget_native=None)
    record = dict(user_id="owner", account_id="123", analysis_id="analysis1", created_at=NOW.isoformat(),
                  date_from=period["date_from"], date_to=period["date_to"],
                  result=dict(provider="google_ads", account_id="123", period=period, recommendations=[rec]))
    config = dict(user_id="owner", account_id="123", schema_version=1, revision=1, enabled=True,
                  currency_code="SAR", time_zone="Asia/Riyadh", campaign_ids=["456"], allowed_actions=["pause", "enable", "set_budget"],
                  max_campaign_daily_budget_native="40", max_account_daily_budget_native="55", max_budget_change_percent="10",
                  cooldown_seconds=3600, max_actions_per_day=3, expires_at=(NOW+timedelta(days=1)).isoformat())
    campaign = dict(campaign_id="456", account_id="123", source="google_ads_api", status="ENABLED", budget_native="20", budget_mode="DAILY",
                    currency_code="SAR", time_zone="Asia/Riyadh", observed_at=NOW.isoformat())
    inventory = dict(complete=True, account_id="123", currency_code="SAR", time_zone="Asia/Riyadh", observed_at=NOW.isoformat(), campaigns=[campaign])
    return record, config, inventory


def app(monkeypatch, *, owner=True, connected=True, enabled=True, reader_failure=False):
    db = DB(); record, config, inventory = fixtures(); config["enabled"] = enabled
    db[ANALYSES].rows.append(record); db[policy.AUTOMATION_POLICIES].rows.append(config)
    if not owner: db.users.rows[0]["role"] = "viewer"
    calls = []
    async def user(): return {"id": "owner", "role": "owner"}  # persisted role is authoritative
    async def accounts(database, user_id): return [{"account_id": "123"}] if connected else []
    async def reader(database, actor, **kwargs):
        calls.append((actor, kwargs))
        if reader_failure: raise HTTPException(503, detail={"code": "google_ads_read_failed"})
        return deepcopy(inventory)
    monkeypatch.setattr(policy, "available_accounts", accounts)
    monkeypatch.setattr(plan, "now", lambda: NOW)
    instance, router = FastAPI(), APIRouter()
    plan.attach_google_ads_automation_plan_routes(router, db, user, reader=reader)
    instance.include_router(router)
    return instance, db, calls, inventory


async def get(instance, **params):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        return await client.get("/google_ads/automation/plan/123", params=params)


@pytest.mark.asyncio
async def test_disabled_policy_short_circuits_analysis_history_and_native_reads(monkeypatch):
    instance, db, calls, _ = app(monkeypatch, enabled=False)
    response = await get(instance)
    assert response.status_code == 200
    result = response.json()
    assert result["blockers"] == ["policy_disabled"] and result["candidates"] == []
    assert result["executable"] is result["executor_available"] is False and result["provider_writes"] == 0
    assert not calls and not db[ANALYSES].reads and not db[PROPOSALS].reads


@pytest.mark.asyncio
@pytest.mark.parametrize("owner,connected,status", [(False, True,403), (True,False,404)])
async def test_persisted_owner_and_linked_account_are_required(monkeypatch, owner, connected, status):
    instance, db, calls, _ = app(monkeypatch, owner=owner, connected=connected)
    assert (await get(instance, analysis_id="analysis1")).status_code == status
    assert not calls and not db[ANALYSES].reads


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"user_id":"other"},{"account_id":"999"}])
async def test_saved_analysis_is_loaded_by_tenant_account_and_id(monkeypatch, change):
    instance, db, calls, _ = app(monkeypatch)
    db[ANALYSES].rows[0].update(change)
    assert (await get(instance, analysis_id="analysis1")).status_code == 404
    assert not calls
    assert db[ANALYSES].reads[0][0] == {"user_id":"owner","account_id":"123","analysis_id":"analysis1"}


@pytest.mark.asyncio
async def test_native_saved_analysis_and_inventory_are_adapted_without_mutation_or_model(monkeypatch):
    instance, db, calls, _ = app(monkeypatch)
    before = deepcopy({name:collection.rows for name,collection in db.collections.items()})
    db[ANALYSES].rows[0]["private_prompt"] = "must not escape"
    response = await get(instance, analysis_id="analysis1")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["candidates"][0]["decision"] == "eligible_for_preview"
    assert result["candidates"][0]["native_action"] == "pause"
    assert result["candidates"][0]["executed"] is False
    assert result["provider_writes"] == 0 and result["executable"] is False
    assert "must not escape" not in response.text and "operations" not in response.text
    assert calls[0][0]["actor_id"] == "owner" and calls[0][1]["account_id"] == "123"
    assert "from_date" not in calls[0][1] and "campaign_id" not in calls[0][1]
    assert db[PROPOSALS].reads[0][0] == {"user_id":"owner","account_id":"123"}
    assert db[policy.AUTOMATION_POLICIES].rows == before[policy.AUTOMATION_POLICIES]


@pytest.mark.asyncio
async def test_history_overflow_is_not_a_complete_history_and_does_not_read_provider(monkeypatch):
    instance, db, calls, _ = app(monkeypatch)
    db[PROPOSALS].rows = [{"user_id":"owner","account_id":"123","status":"failed"} for _ in range(1001)]
    response = await get(instance, analysis_id="analysis1")
    assert response.status_code == 200 and response.json()["blockers"] == ["history_incomplete"]
    assert not calls


@pytest.mark.asyncio
async def test_pending_operations_across_all_time_block_account_preview(monkeypatch):
    instance, db, calls, _ = app(monkeypatch)
    db[PROPOSALS].rows = [{"user_id":"owner","account_id":"123","status":"uncertain","created_at":"2020-01-01T00:00:00Z"}]
    response = await get(instance, analysis_id="analysis1")
    assert response.status_code == 200 and response.json()["blockers"] == ["account_execution_pending"]


@pytest.mark.asyncio
@pytest.mark.parametrize("action,campaign_id", [("create_campaign", None),("duplicate", "789")])
async def test_verified_native_creation_history_uses_new_campaign_for_cooldown(monkeypatch, action, campaign_id):
    instance, db, calls, _ = app(monkeypatch)
    db[PROPOSALS].rows = [{"user_id":"owner","account_id":"123","status":"completed", "action":action,
        "campaign_id":campaign_id,"created_campaign_id":"456","verified":True,
        "executed_at":(NOW-timedelta(minutes=30)).isoformat(),"operations":["private mutation body"]}]
    response = await get(instance, analysis_id="analysis1")
    assert response.status_code == 200, response.text
    assert response.json()["candidates"][0]["blockers"] == ["campaign_cooldown"]
    projection = db[PROPOSALS].reads[0][1]
    assert projection["action"] == projection["created_campaign_id"] == projection["verified"] == 1
    assert "operations" not in projection and "private mutation body" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["upload_image","attach_video","upload_html5"])
async def test_verified_account_asset_history_counts_toward_account_daily_limit(monkeypatch, action):
    instance, db, calls, _ = app(monkeypatch)
    db[policy.AUTOMATION_POLICIES].rows[0]["max_actions_per_day"] = 1
    db[PROPOSALS].rows = [{"user_id":"owner","account_id":"123","status":"completed", "action":action,
        "campaign_id":None,"created_campaign_id":None,"verified":True,
        "executed_at":(NOW-timedelta(minutes=30)).isoformat()}]
    response = await get(instance, analysis_id="analysis1")
    assert response.status_code == 200 and response.json()["blockers"] == ["daily_action_limit"]


@pytest.mark.asyncio
async def test_unknown_action_with_missing_campaign_stays_unproved(monkeypatch):
    instance, db, calls, _ = app(monkeypatch)
    db[PROPOSALS].rows = [{"user_id":"owner","account_id":"123","status":"completed", "action":"unknown",
        "campaign_id":None,"verified":True,"executed_at":(NOW-timedelta(minutes=30)).isoformat()}]
    response = await get(instance, analysis_id="analysis1")
    assert response.json()["blockers"] == ["history_unproved"]


@pytest.mark.asyncio
async def test_expired_policy_and_missing_analysis_do_not_read_provider(monkeypatch):
    instance, db, calls, _ = app(monkeypatch)
    response = await get(instance)
    assert response.json()["blockers"] == ["analysis_required"]
    db[policy.AUTOMATION_POLICIES].rows[0]["expires_at"] = NOW.isoformat()
    assert (await get(instance, analysis_id="analysis1")).json()["blockers"] == ["policy_expired"]
    assert not calls


@pytest.mark.asyncio
async def test_stale_or_unproved_saved_analysis_short_circuits_native_and_history_reads(monkeypatch):
    instance, db, calls, _ = app(monkeypatch)
    db[ANALYSES].rows[0]["created_at"] = (NOW-timedelta(hours=5)).isoformat()
    response = await get(instance, analysis_id="analysis1")
    assert response.json()["blockers"] == ["analysis_stale"]
    assert not calls and not db[PROPOSALS].reads


@pytest.mark.asyncio
async def test_provider_failure_propagates_safe_native_problem_without_creating_action(monkeypatch):
    instance, db, calls, _ = app(monkeypatch, reader_failure=True)
    response = await get(instance, analysis_id="analysis1")
    assert response.status_code == 503 and response.json()["detail"]["code"] == "google_ads_read_failed"
    assert db[PROPOSALS].rows == []


@pytest.mark.asyncio
async def test_storage_exception_has_constant_public_error_no_private_body(monkeypatch):
    instance, db, calls, _ = app(monkeypatch)
    async def fail(*args, **kwargs): raise RuntimeError("private database connection string")
    db[ANALYSES].find_one = fail
    response = await get(instance, analysis_id="analysis1")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "google_automation_plan_unavailable"
    assert "private" not in response.text and not calls


@pytest.mark.asyncio
async def test_incomplete_native_inventory_never_produces_a_candidate(monkeypatch):
    instance, db, calls, inventory = app(monkeypatch)
    inventory["complete"] = False
    response = await get(instance, analysis_id="analysis1")
    assert response.json()["blockers"] == ["workspace_incomplete"]


@pytest.mark.asyncio
async def test_native_inventory_uses_no_period_or_metrics_and_closes_client(monkeypatch):
    queries, closed = [], []
    class Client:
        async def account_metadata(self): return {"account_id":"123","identity_verified":True,"currency_code":"SAR","time_zone":"Asia/Riyadh"}
        async def field_metadata(self, fields): return {field:{"selectable":True} for field in fields}
        async def search(self, query):
            queries.append(query)
            return [{"campaign":{"id":"456","status":"ENABLED","name":"native"},"campaignBudget":{"period":"DAILY","amountMicros":"20000000"}}], True
        async def close(self): closed.append(True)
    async def account_client(*args, **kwargs): return Client(), "epoch"
    monkeypatch.setattr(plan,"account_client",account_client)
    result = await plan.read_google_automation_inventory(DB(), {"user_id":"owner"}, account_id="123")
    assert len(queries) == len(closed) == 1
    assert "metrics." not in queries[0] and "segments.date" not in queries[0] and "LIMIT 501" in queries[0]
    assert result["campaigns"][0]["budget_native"] == "20"
    assert result["campaigns"][0]["budget_mode"] == "DAILY" and result["complete"] is True
    assert result["campaigns"][0]["source"] == "google_ads_api"


@pytest.mark.asyncio
@pytest.mark.parametrize("count,complete,period", [(501,True,"DAILY"),(1,False,"DAILY"),(1,True,None)])
async def test_native_inventory_overflow_and_unknown_budget_period_fail_closed(monkeypatch, count, complete, period):
    class Client:
        async def account_metadata(self): return {"account_id":"123","identity_verified":True,"currency_code":"SAR","time_zone":"Asia/Riyadh"}
        async def field_metadata(self, fields): return {field:{"selectable":True} for field in fields}
        async def search(self, query):
            return [{"campaign":{"id":str(index),"status":"ENABLED"},"campaignBudget":{"period":period,"amountMicros":"20000000"}} for index in range(count)], complete
        async def close(self): pass
    async def account_client(*args, **kwargs): return Client(), "epoch"
    monkeypatch.setattr(plan,"account_client",account_client)
    result = await plan.read_google_automation_inventory(DB(), {"user_id":"owner"}, account_id="123")
    if period is None: assert result["campaigns"][0]["budget_mode"] is None
    else: assert result["complete"] is False
