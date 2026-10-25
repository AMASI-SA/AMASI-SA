from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import campaign_ai_snapchat_autonomy as autonomy
import campaign_ai_monitor_legacy as legacy

NOW = datetime.now(timezone.utc)
ACCOUNT = {
    "user_id": "owner", "provider": "snapchat_ads", "ad_account_id": "account-a",
    "external_account_id": "account-a", "mezan_selected": True,
    "connection_status": "connected", "connection_provenance": "api_connection",
    "currency": "USD", "timezone": "America/Los_Angeles", "display_name": "Account A",
}


def payload(mode="enabled", revision=0, **changes):
    values = {
        "expected_revision": revision, "mode": mode, "allowed_actions": ["pause", "reduce", "scale"],
        "max_entity_daily_budget_micro": 200_000_000, "max_daily_budget_increase_micro": 20_000_000,
        "max_change_percent": 15, "min_interval_minutes": 300, "max_actions_per_cycle": 2,
        "expires_at": (NOW + timedelta(days=1)).isoformat(),
        "acknowledgement_version": autonomy.FENCE_VERSION, "accept_autonomous_execution": True,
    }
    values.update(changes)
    return autonomy.SnapchatAutonomyPolicyInput(**values)


def checked(recommendation_id="rec-a", *, account_id="account-a", action="reduce", budget=100):
    recommendation = {"recommendation_id": recommendation_id, "provider": "snapchat",
                      "account_id": account_id, "entity_id": recommendation_id,
                      "entity_level": "campaign", "action": action, "change_percent": 15,
                      "approval_available": True}
    target = {"provider": "snapchat", "account_id": account_id, "entity_id": recommendation_id,
              "entity_level": "campaign", "current_daily_budget_native": budget, "currency_native": "USD"}
    return {"snapshot": {"snapshot_id": "snapshot-a"}, "recommendation": recommendation,
            "target": target, "snapshot_digest": "digest-a", "execution_quality": {"status": "complete"}}


@pytest.fixture
def selected(monkeypatch):
    rows = [deepcopy(ACCOUNT)]

    async def load(_db, user_id):
        return [deepcopy(row) for row in rows if row["user_id"] == user_id]

    monkeypatch.setattr(autonomy, "_load_selected_accounts", load)
    return rows


@pytest.mark.parametrize("changes", [
    {"allowed_actions": []}, {"allowed_actions": ["pause", "pause"]}, {"allowed_actions": ["resume"]},
    {"max_entity_daily_budget_micro": None}, {"max_entity_daily_budget_micro": True},
    {"max_entity_daily_budget_micro": 4_999_999}, {"max_daily_budget_increase_micro": -1},
    {"max_daily_budget_increase_micro": 0}, {"max_change_percent": 4}, {"max_change_percent": 31},
    {"min_interval_minutes": 299}, {"max_actions_per_cycle": 0}, {"expires_at": None},
    {"expires_at": "2030-01-01T12:00:00"}, {"accept_autonomous_execution": False},
    {"acknowledgement_version": "old"}, {"user_id": "other"}, {"currency": "SAR"},
])
def test_grant_requires_explicit_typed_limits_and_current_acknowledgement(changes):
    with pytest.raises(ValidationError):
        payload(**changes)


def test_new_policy_default_is_disabled_without_budget_authority():
    value = autonomy.SnapchatAutonomyPolicyInput(expected_revision=0)
    assert value.mode == "disabled" and value.allowed_actions == []
    assert value.max_entity_daily_budget_micro is None
    assert value.accept_autonomous_execution is False


@pytest.mark.asyncio
async def test_cannot_store_dormant_enabled_grant_before_fence_is_installed(monkeypatch, selected):
    monkeypatch.setattr(autonomy, "executor_ready", lambda: False)
    with pytest.raises(HTTPException) as caught:
        await autonomy.save_snapchat_autonomy_policy(
            None, "owner", "owner", "account-a", payload(), now=lambda: NOW,
        )
    assert caught.value.detail["code"] == "snapchat_autonomy_executor_unavailable"


@pytest.mark.parametrize("account_id,currency,action,budget,expected", [
    ("account-a", "USD", "reduce", 100, 85_000_000),
    ("account-a", "USD", "scale", 100, 115_000_000),
    ("account-a", "USD", "reduce", 5.01, 5_000_000),
])
def test_policy_uses_same_native_payload_as_real_executor(account_id, currency, action, budget, expected):
    saved = {**payload().model_dump(), "account_id": account_id, "currency": currency}
    proof = checked(action=action, budget=budget)
    change, increase = autonomy._validate_change(saved, proof["recommendation"], proof["target"])
    assert change == {"daily_budget_micro": expected}
    assert increase == max(0, expected - round(budget * 1_000_000))


@pytest.mark.parametrize("change", [
    {"account_id": "foreign"}, {"currency_native": "SAR"}, {"currency_native": None},
    {"entity_level": "ad"}, {"current_daily_budget_native": 5},
])
def test_policy_rejects_unknown_scope_currency_level_or_minimum_noop(change):
    saved = {**payload().model_dump(), "account_id": "account-a", "currency": "USD"}
    proof = checked()
    proof["target"].update(change)
    if "entity_level" in change:
        proof["recommendation"].update(change)
    with pytest.raises(HTTPException):
        autonomy._validate_change(saved, proof["recommendation"], proof["target"])


def test_actual_minimum_rounded_scale_cannot_exceed_percentage_grant():
    saved = {**payload().model_dump(), "account_id": "account-a", "currency": "USD"}
    proof = checked(action="scale", budget=4)
    with pytest.raises(HTTPException) as caught:
        autonomy._validate_change(saved, proof["recommendation"], proof["target"])
    assert caught.value.detail["code"] == "snapchat_autonomy_actual_change_limit_exceeded"


@pytest.mark.asyncio
async def test_no_server_binding_does_not_invent_policy_authority():
    collection = type("Collection", (), {"find_one": AsyncMock(return_value=None)})()
    db = {autonomy.BINDING_COLLECTION: collection}
    await autonomy.require_snapchat_autonomy_write_authorization(
        db, user_id="owner", proposal_id="manual", account_id="account-a",
        target_id="campaign-a", action="campaign.update", changes={"status": "PAUSED"},
    )
    collection.find_one.assert_awaited_once()


def test_policy_routes_require_owner_before_reading_accounts(monkeypatch):
    read = AsyncMock()
    monkeypatch.setattr(autonomy, "_load_selected_accounts", read)
    router = APIRouter(prefix="/ads-manager")
    async def current_user():
        return {"id": "staff", "role": "staff"}
    def require_owner(user):
        raise HTTPException(status_code=403)
    autonomy.attach_snapchat_autonomy_routes(router, None, current_user, require_owner)
    app = FastAPI(); app.include_router(router)
    assert TestClient(app).get("/ads-manager/ai-monitor/snapchat/autonomy").status_code == 403
    read.assert_not_awaited()


def test_final_fence_marker_must_be_present_in_real_management():
    # Root integration makes the capability test truthful. Until then this
    # test blocks release rather than treating configuration-only as complete.
    assert autonomy.executor_ready() is True
