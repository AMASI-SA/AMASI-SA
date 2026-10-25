import httpx
import json
import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import ValidationError

from integrations_control_center import tiktok_child_management as controls
from integrations_control_center import tiktok_creator_api as transport
from tests.test_tiktok_campaign_management import environment
from tests.test_tiktok_management_routes import app_for


@pytest.mark.asyncio
async def test_child_controls_are_reachable_through_owner_workspace():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner", "role": "owner"})), base_url="http://test") as client:
        response = await client.post("/api/integrations-v2/tiktok_ads/child-management/proposals", json={})
    assert response.status_code == 422


def payload(kind="adgroup", action="pause", **changes):
    value = {"kind": kind, "action": action, "account_id": "70001", "campaign_id": "1111", "adgroup_id": "2222", "reason": "Owner selected control", "idempotency_key": "child_control_1"}
    if kind == "ad":
        value["ad_id"] = "3333"
    if action == "rename":
        value["name"] = "New winter name"
    if action in {"set_budget", "set_conversion_bid"}:
        value["amount_native"] = 55.0 if action == "set_budget" else 8.0
    if action in {"enable", "set_budget", "set_conversion_bid"}:
        value["spend_ceiling_native"] = 60.0
    return controls.ChildInput(**{**value, **changes})


class State:
    def __init__(self):
        self.writes = []
        self.timeout = False
        self.bad_verification = False
        self.campaign = {"advertiser_id": "70001", "campaign_id": "1111", "operation_status": "ENABLE", "objective_type": "WEB_CONVERSIONS", "sales_destination": "WEBSITE", "budget_optimize_on": False, "budget": 50.0, "budget_mode": "BUDGET_MODE_DAY"}
        self.group = {"advertiser_id": "70001", "campaign_id": "1111", "adgroup_id": "2222", "adgroup_name": "Winter", "operation_status": "ENABLE", "budget": 50.0, "budget_mode": "BUDGET_MODE_DAY", "bid_type": "BID_TYPE_CUSTOM", "conversion_bid_price": 5.0}
        self.ad = {"advertiser_id": "70001", "campaign_id": "1111", "adgroup_id": "2222", "smart_plus_ad_id": "3333", "ad_name": "Winter ad", "operation_status": "ENABLE", "native_extra_secret": "never-project-this"}

    async def handle(self, request):
        assert request.headers["Access-Token"] == "disposable-fixture-token"
        path = request.url.path
        if path.endswith("/advertiser/info/"):
            data = {"list": [{"advertiser_id": "70001", "currency": "SAR"}]}
        elif request.method == "POST":
            body = json.loads(request.content)
            self.writes.append((path, body))
            kind = "adgroup" if "/adgroup/" in path else "ad"
            row = self.group if kind == "adgroup" else self.ad
            key = "adgroup_id" if kind == "adgroup" else "smart_plus_ad_id"
            assert body["advertiser_id"] == "70001"
            if "/status/update/" in path:
                assert body[key + "s"] == ["2222" if kind == "adgroup" else "3333"]
                assert set(body) == {"advertiser_id", key + "s", "operation_status"}
            else:
                assert body[key] == ("2222" if kind == "adgroup" else "3333")
                assert len(body) == 3
            if not self.bad_verification:
                row.update({k: v for k, v in body.items() if k not in {"advertiser_id", key, key + "s"}})
            if self.timeout:
                raise httpx.ReadTimeout("unknown native result", request=request)
            data = {}
        else:
            kind = "campaign" if "/campaign/" in path else "adgroup" if "/adgroup/" in path else "ad"
            row = self.campaign if kind == "campaign" else self.group if kind == "adgroup" else self.ad
            data = {"list": [dict(row)], "page_info": {"total_number": 1}}
        return httpx.Response(200, json={"code": 0, "data": data})

    def api(self):
        return transport.TikTokCreatorAPI(transport=httpx.MockTransport(self.handle))


@pytest_asyncio.fixture
async def workflow(environment, monkeypatch):
    db, _ = environment
    state = State()
    monkeypatch.setenv("TIKTOK_CHILD_MANAGEMENT_ENABLED", "true")
    monkeypatch.setattr(transport.governor, "peek", lambda: ("normal", None))
    monkeypatch.setattr(controls, "TikTokCreatorAPI", state.api)
    return db, state


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,action", [("adgroup", "pause"), ("ad", "pause"), ("adgroup", "enable"), ("ad", "enable"), ("adgroup", "rename"), ("ad", "rename"), ("adgroup", "set_budget"), ("adgroup", "set_conversion_bid")])
async def test_preview_then_one_verified_native_child_change(workflow, kind, action):
    db, state = workflow
    if action == "enable":
        (state.group if kind == "adgroup" else state.ad)["operation_status"] = "DISABLE"
    preview = await controls.preview_child(db, "owner", payload(kind, action))
    assert not state.writes and "never-project-this" not in str(preview)
    saved = await db[controls.PROPOSALS].find_one({"_id": preview["proposal_id"]})
    assert "plan" not in saved and saved["plan_encrypted"]
    result = await controls.execute_child(db, "owner", preview["proposal_id"], preview["confirmation_digest"])
    assert result["verified"] is True and result["status"] == "completed"
    assert await db[controls.FENCES].count_documents({}) == 0
    await controls.execute_child(db, "owner", preview["proposal_id"], preview["confirmation_digest"])
    assert len(state.writes) == 1


@pytest.mark.asyncio
async def test_ambiguous_write_fences_other_keys_until_readonly_reconciliation(workflow):
    db, state = workflow
    value = payload()
    preview = await controls.preview_child(db, "owner", value)
    state.timeout = True
    with pytest.raises(HTTPException):
        await controls.execute_child(db, "owner", preview["proposal_id"], preview["confirmation_digest"])
    saved = await db[controls.PROPOSALS].find_one({"_id": preview["proposal_id"]})
    assert saved["status"] == "uncertain" and await db[controls.FENCES].count_documents({}) == 1
    other = await controls.preview_child(db, "owner", payload(idempotency_key="child_control_2"))
    with pytest.raises(HTTPException) as blocked:
        await controls.execute_child(db, "owner", other["proposal_id"], other["confirmation_digest"])
    assert blocked.value.detail["code"] == "tiktok_child_pending_write" and len(state.writes) == 1
    result = await controls.reconcile_child(db, "owner", preview["proposal_id"])
    assert result["verified"] is True and len(state.writes) == 1
    assert await db[controls.FENCES].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["parent", "budget", "digest", "tenant"])
async def test_changed_or_foreign_approval_never_writes(workflow, change):
    db, state = workflow
    preview = await controls.preview_child(db, "owner", payload("ad", "enable"))
    if change == "parent":
        state.campaign["operation_status"] = "DISABLE"
    if change == "budget":
        state.group["budget"] = 500.0
    with pytest.raises(HTTPException):
        await controls.execute_child(db, "foreign" if change == "tenant" else "owner", preview["proposal_id"], "0" * 64 if change == "digest" else preview["confirmation_digest"])
    assert not state.writes


@pytest.mark.asyncio
async def test_native_mismatch_stays_uncertain_and_cannot_repeat(workflow):
    db, state = workflow
    preview = await controls.preview_child(db, "owner", payload())
    state.bad_verification = True
    with pytest.raises(HTTPException):
        await controls.execute_child(db, "owner", preview["proposal_id"], preview["confirmation_digest"])
    with pytest.raises(HTTPException):
        await controls.execute_child(db, "owner", preview["proposal_id"], preview["confirmation_digest"])
    assert len(state.writes) == 1


@pytest.mark.asyncio
async def test_feature_off_does_not_reach_db_or_provider(monkeypatch):
    monkeypatch.delenv("TIKTOK_CHILD_MANAGEMENT_ENABLED", raising=False)
    with pytest.raises(HTTPException) as blocked:
        await controls.preview_child(object(), "owner", payload())
    assert blocked.value.status_code == 503


@pytest.mark.asyncio
async def test_dynamic_parent_budget_requires_125_percent_headroom(workflow):
    db, state = workflow
    state.campaign.update({"budget_optimize_on": True, "budget_mode": "BUDGET_MODE_DYNAMIC_DAILY_BUDGET", "budget_auto_adjust_strategy": "UNSET"})
    with pytest.raises(HTTPException) as blocked:
        await controls.preview_child(db, "owner", payload(action="enable"))
    assert blocked.value.detail["code"] == "tiktok_management_spend_ceiling_exceeded"
    preview = await controls.preview_child(db, "owner", payload(action="enable", spend_ceiling_native=62.5))
    assert preview["financial_bound"]["maximum_native"] == "62.50" and not state.writes
    state.campaign["budget_auto_adjust_strategy"] = "AUTO"
    with pytest.raises(HTTPException):
        await controls.execute_child(db, "owner", preview["proposal_id"], preview["confirmation_digest"])
    assert not state.writes


@pytest.mark.parametrize("changes", [{"kind": "ad", "ad_id": None}, {"action": "enable"}, {"action": "set_budget", "amount_native": 50.0}, {"amount_native": 50.0}, {"name": "unexpected"}])
def test_typed_controls_require_exact_action_fields(changes):
    with pytest.raises(ValidationError):
        controls.ChildInput(**{**payload().model_dump(mode="json"), **changes})
