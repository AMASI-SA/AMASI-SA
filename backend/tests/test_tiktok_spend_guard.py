import httpx
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import pytest
import pytest_asyncio
from fastapi import HTTPException, FastAPI, APIRouter
from pydantic import ValidationError
from integrations_control_center import tiktok_spend_guard as guard
from integrations_control_center import tiktok_creator_api as transport
from tests.test_tiktok_campaign_management import environment
from tests.test_tiktok_management_routes import app_for


@pytest.mark.asyncio
async def test_owner_spend_policy_route_is_reachable_and_typed():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner", "role": "owner"})), base_url="http://test") as client:
        response = await client.put("/api/integrations-v2/tiktok_ads/spend-guard/policy", json={})
    assert response.status_code == 422


class SpendState:
    def __init__(self):
        self.spend, self.currency, self.zone = "101.00", "SAR", "Asia/Riyadh"
        self.calls, self.bad = [], None

    async def handle(self, request):
        assert request.method == "GET" and request.url.host == "business-api.tiktok.com"
        assert request.headers["Access-Token"] == "disposable-fixture-token"
        self.calls.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/advertiser/info/"):
            data = {"list": [{"advertiser_id": "70001", "currency": self.currency, "timezone": self.zone}]}
        else:
            assert request.url.path.endswith("/report/integrated/get/")
            assert json.loads(request.url.params["metrics"]) == ["spend"]
            filtering = json.loads(request.url.params["filtering"])
            assert filtering[0]["field_name"] == "campaign_ids"
            campaign = json.loads(filtering[0]["filter_value"])[0]
            day = request.url.params["start_date"]
            assert day == request.url.params["end_date"]
            row = {"dimensions": {"campaign_id": campaign, "stat_time_day": day + " 00:00:00"}, "metrics": {"spend": self.spend}}
            data = {"list": [row], "page_info": {"page": 1, "total_page": 1, "total_number": 1}}
            if self.bad == "campaign": row["dimensions"]["campaign_id"] = "9999"
            if self.bad == "day": row["dimensions"]["stat_time_day"] = "2020-01-01"
            if self.bad == "page": data["page_info"]["total_number"] = True
            if self.bad == "empty": data["list"] = []
            if self.bad == "nan": row["metrics"]["spend"] = "NaN"
            if self.bad == "bool": row["metrics"]["spend"] = True
        return httpx.Response(200, json={"code": 0, "data": data})

    def api(self):
        return transport.TikTokCreatorAPI(transport=httpx.MockTransport(self.handle))


@pytest_asyncio.fixture
async def workflow(environment, monkeypatch):
    db, native = environment
    state = SpendState()
    monkeypatch.setattr(transport.governor, "peek", lambda: ("normal", None))
    monkeypatch.setenv("TIKTOK_SPEND_GUARD_ENABLED", "true")
    return db, native, state


def policy(**changes):
    return guard.SpendPolicyInput(**{"account_id": "70001", "expected_revision": 0, "enabled": True, "rules": [{"campaign_id": "1111", "pause_at_daily_spend_native": 100.0}], "polling_and_report_delay_acknowledged": True, **changes})


async def configure(workflow, **changes):
    db, native, state = workflow
    return await guard.save_policy(db, "owner", policy(**changes), api_factory=state.api, provider_factory=native.factory)


async def cycle(workflow, identifier="cycle1", user="owner", current=None):
    from campaign_ai_monitor_legacy import RECOMMENDATION_COLLECTION
    db, native, state = workflow
    snapshot = {"user_id": user, "snapshot_id": identifier, "recommendations": [{"provider": "snapchat", "immutable": True}]}
    await db[RECOMMENDATION_COLLECTION].update_one({"user_id": user, "snapshot_id": identifier}, {"$setOnInsert": snapshot}, upsert=True)
    return await guard.run_spend_guard(db, user, snapshot, current=current, api_factory=state.api, provider_factory=native.factory)


@pytest.mark.asyncio
async def test_policy_default_is_off_and_no_configuration_never_calls_provider(workflow):
    db, native, state = workflow
    assert (await guard.current_policy(db, "owner", "70001"))["enabled"] is False
    result = await cycle(workflow)
    assert result["tiktok_spend_guard"]["checked_rules"] == 0
    assert native.writes == [] and state.calls == []


@pytest.mark.asyncio
async def test_owner_policy_pauses_exact_campaign_once_and_preserves_other_platform_snapshot(workflow):
    db, native, state = workflow
    saved = await configure(workflow)
    assert saved["currency"] == "SAR" and saved["timezone"] == "Asia/Riyadh" and saved["allowed_actions"] == ["pause"]
    result = await cycle(workflow)
    assert result["tiktok_spend_guard"]["checks"][0]["status"] == "paused_verified"
    assert result["writes_performed"] is True
    assert result["recommendations"] == [{"provider": "snapchat", "immutable": True}]
    assert result["tiktok_spend_guard"]["profit_sar"] is None and result["tiktok_spend_guard"]["financial_orders"] is None
    assert native.writes == [("/open_api/v1.3/smart_plus/campaign/status/update/", {"advertiser_id": "70001", "operation_status": "DISABLE", "campaign_ids": ["1111"]})]
    old_calls = len(state.calls)
    await cycle(workflow)
    assert len(native.writes) == 1 and len(state.calls) == old_calls
    audit = await db[guard.ACTIONS].find_one({"user_id": "owner"})
    assert Decimal(audit["reported_spend_native"]) == Decimal("101.00")
    assert Decimal(audit["pause_at_daily_spend_native"]) == Decimal("100.00")


@pytest.mark.asyncio
async def test_below_trigger_and_already_paused_campaigns_never_write(workflow):
    db, native, state = workflow
    await configure(workflow)
    state.spend = "99.99"
    result = await cycle(workflow)
    assert result["tiktok_spend_guard"]["checks"][0]["status"] == "below_trigger"
    assert result["writes_performed"] is False
    state.spend = "101"
    native.row["operation_status"] = "DISABLE"
    result = await cycle(workflow, "cycle2")
    assert result["tiktok_spend_guard"]["checks"][0]["status"] == "already_paused"
    assert native.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["campaign", "day", "page", "empty", "nan", "bool"])
async def test_incomplete_or_invalid_native_spend_never_becomes_permission(workflow, bad):
    db, native, state = workflow
    await configure(workflow)
    state.bad = bad
    result = await cycle(workflow)
    assert result["tiktok_spend_guard"]["checks"][0]["status"] == "blocked"
    assert not native.writes


@pytest.mark.asyncio
async def test_disabled_or_stale_policy_cannot_overwrite_owner_authority(workflow):
    db, native, state = workflow
    await configure(workflow)
    with pytest.raises(HTTPException) as stale:
        await configure(workflow, enabled=False)
    assert stale.value.detail["code"] == "tiktok_guard_policy_changed"
    await configure(workflow, expected_revision=1, enabled=False)
    assert (await guard.current_policy(db, "owner", "70001"))["revision"] == 2
    before = len(state.calls)
    await cycle(workflow)
    assert len(state.calls) == before and not native.writes
    assert await db[guard.VERSIONS].count_documents({"user_id": "owner"}) == 2


@pytest.mark.asyncio
async def test_revocation_after_native_preview_blocks_at_final_submit_boundary(workflow, monkeypatch):
    db, native, state = workflow
    await configure(workflow)
    original = guard.preview_tiktok_campaign
    async def revoke_after_preview(*args, **kwargs):
        value = await original(*args, **kwargs)
        await configure(workflow, expected_revision=1, enabled=False)
        return value
    monkeypatch.setattr(guard, "preview_tiktok_campaign", revoke_after_preview)
    result = await cycle(workflow)
    assert result["tiktok_spend_guard"]["checks"][0]["status"] == "blocked"
    assert not native.writes
    from integrations_control_center import tiktok_campaign_management as management
    assert (await db[management.FENCE_COLLECTION].find_one({"user_id": "owner"}))["status"] == "released"
    assert (await db[management.COLLECTION].find_one({"user_id": "owner"}))["status"] == "previewed"


@pytest.mark.asyncio
async def test_uncertain_native_submission_is_not_repeated_in_a_later_cycle(workflow):
    db, native, state = workflow
    await configure(workflow)
    native.behavior = "timeout_after_write"
    result = await cycle(workflow)
    assert result["tiktok_spend_guard"]["checks"][0]["status"] == "uncertain"
    assert result["writes_performed"] is None
    await cycle(workflow, "cycle2")
    assert len(native.writes) == 1


@pytest.mark.asyncio
async def test_rotated_bounded_rules_use_account_native_day(workflow, monkeypatch):
    db, native, state = workflow
    for identifier in ("1112", "1113"):
        native.rows[identifier] = {**deepcopy(native.row), "campaign_id": identifier}
    await configure(workflow, rules=[{"campaign_id": value, "pause_at_daily_spend_native": 100.0} for value in ("1111", "1112", "1113")])
    monkeypatch.setattr(guard, "MAX_CHECKS", 1)
    state.spend = "0"
    clock = lambda: datetime(2026, 10, 25, 22, 30, tzinfo=timezone.utc)
    for identifier in ("cycle1", "cycle2"):
        await cycle(workflow, identifier, current=clock)
    reports = [params for path, params in state.calls if path.endswith("/report/integrated/get/")]
    assert [json.loads(json.loads(row["filtering"])[0]["filter_value"])[0] for row in reports] == ["1111", "1112"]
    assert all(row["start_date"] == "2026-10-26" for row in reports)
    assert not native.writes


@pytest.mark.asyncio
async def test_currency_change_foreign_owner_and_feature_disable_never_write(workflow, monkeypatch):
    db, native, state = workflow
    await configure(workflow)
    state.currency = "USD"
    result = await cycle(workflow)
    assert result["tiktok_spend_guard"]["checks"][0]["status"] == "blocked"
    assert (await guard.current_policy(db, "foreign", "70001"))["enabled"] is False
    monkeypatch.setenv("TIKTOK_SPEND_GUARD_ENABLED", "false")
    await guard.run_spend_guard(object(), "owner", {"snapshot_id": "skip"})
    assert not native.writes


@pytest.mark.asyncio
async def test_native_day_rollover_during_preflight_retires_old_spend_authority(workflow):
    db, native, state = workflow
    await configure(workflow)
    moments = iter([datetime(2026, 10, 25, 20, 59, tzinfo=timezone.utc), datetime(2026, 10, 25, 21, 0, tzinfo=timezone.utc)])
    result = await cycle(workflow, current=lambda: next(moments))
    assert result["tiktok_spend_guard"]["checks"][0]["status"] == "blocked"
    assert not native.writes


@pytest.mark.asyncio
async def test_concurrent_cycle_calls_do_not_repeat_native_pause(workflow):
    db, native, state = workflow
    await configure(workflow)
    await asyncio.gather(*(cycle(workflow) for _ in range(3)))
    assert len(native.writes) == 1


@pytest.mark.asyncio
async def test_concurrent_policy_saves_activate_only_one_exact_owner_version(workflow):
    db, native, state = workflow
    results = await asyncio.gather(configure(workflow), configure(workflow), return_exceptions=True)
    assert sum(isinstance(value, dict) for value in results) == 1
    active = await guard.current_policy(db, "owner", "70001")
    assert active["revision"] == 1 and active["rules"] == [{"campaign_id": "1111", "pause_at_daily_spend_native": 100.0}]
    assert not native.writes


@pytest.mark.asyncio
async def test_owner_can_disable_a_saved_policy_even_when_feature_is_off(workflow, monkeypatch):
    db, native, state = workflow
    await configure(workflow)
    old_calls = len(state.calls)
    monkeypatch.setenv("TIKTOK_SPEND_GUARD_ENABLED", "false")
    saved = await configure(workflow, expected_revision=1, enabled=False, rules=[], polling_and_report_delay_acknowledged=False)
    assert saved["enabled"] is False and len(state.calls) == old_calls and not native.writes


@pytest.mark.asyncio
async def test_action_history_filters_owner_account_and_campaign(workflow):
    db, _, _ = workflow
    await guard.indexes(db)
    for identifier, owner, account, campaign in [("mine", "owner", "70001", "1111"), ("foreign", "foreign", "70001", "1111"), ("other-account", "owner", "70002", "1111"), ("other-campaign", "owner", "70001", "2222")]:
        await db[guard.ACTIONS].insert_one({"_id": identifier, "user_id": owner, "snapshot_id": "snapshot-" + identifier, "account_id": account, "campaign_id": campaign, "created_at": guard.now(), "status": "blocked"})
    async def actor(): return {"id": "owner", "role": "owner"}
    app, router = FastAPI(), APIRouter(prefix="/api/integrations-v2")
    guard.attach_spend_guard_routes(router, db, actor, lambda user: user)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/integrations-v2/tiktok_ads/spend-guard/actions", params={"account_id": "70001", "campaign_id": "1111"})
        missing_account = await client.get("/api/integrations-v2/tiktok_ads/spend-guard/actions", params={"campaign_id": "1111"})
    assert response.status_code == 200 and [row["_id"] for row in response.json()["items"]] == ["mine"]
    assert missing_account.status_code == 422


@pytest.mark.asyncio
async def test_broken_policy_inventory_is_unknown_never_complete_or_zero(workflow):
    db, native, state = workflow
    await guard.indexes(db)
    await db[guard.POLICIES].insert_one({"user_id": "owner", "account_id": "70001", "revision": 1, "enabled": True, "version_id": "missing-immutable-version"})
    result = await cycle(workflow)
    summary = result["tiktok_spend_guard"]
    assert summary["status"] == "partial" and summary["coverage_exhaustive"] is False
    assert summary["configured_rules"] is None and summary["checked_rules"] == 0
    assert not native.writes and not state.calls


@pytest.mark.parametrize("changes", [{"rules": []}, {"polling_and_report_delay_acknowledged": False}, {"rules": [{"campaign_id": "1111", "pause_at_daily_spend_native": True}]}, {"enabled": "true"}])
def test_enabled_policy_requires_exact_typed_owner_authority(changes):
    with pytest.raises(ValidationError):
        policy(**changes)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", [("GET", "/policy?account_id=70001", None), ("GET", "/actions", None), ("PUT", "/policy", {"account_id": "70001", "expected_revision": 0, "enabled": False})])
async def test_staff_cannot_read_or_change_owner_policy(method, path, body):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "staff", "role": "staff"})), base_url="http://test") as client:
        response = await client.request(method, "/api/integrations-v2/tiktok_ads/spend-guard" + path, **({"json": body} if body is not None else {}))
    assert response.status_code == 403
