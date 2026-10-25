import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock

import campaign_ai_monitor_legacy as legacy


def _rec(action="scale", level="campaign"):
    return {"action": action, "entity_level": level}


def _target(budget=10.0):
    return {"current_daily_budget_native": budget}


def test_p0_5_exact_fresh_budget_basis_is_allowed():
    legacy._require_snapchat_budget_basis_unchanged(
        _rec(), _target(10.0), {"daily_budget_micro": 10_000_000}
    )


def test_p0_5_budget_drift_blocks_stale_absolute_write():
    with pytest.raises(HTTPException) as caught:
        legacy._require_snapchat_budget_basis_unchanged(
            _rec(), _target(10.0), {"daily_budget_micro": 13_000_000}
        )
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "snapchat_recommendation_budget_drift"
    assert caught.value.detail["recovery_action"] == "create_fresh_campaign_ai_recommendation"


def test_p0_5_missing_fresh_budget_basis_fails_closed():
    with pytest.raises(HTTPException) as caught:
        legacy._require_snapchat_budget_basis_unchanged(_rec(), _target(10.0), {})
    assert caught.value.detail["code"] == "snapchat_recommendation_budget_basis_unavailable"


def test_p0_5_pause_does_not_require_budget_basis():
    legacy._require_snapchat_budget_basis_unchanged(_rec("pause"), _target(None), {})


def test_p0_5_ad_level_does_not_invent_budget_semantics():
    legacy._require_snapchat_budget_basis_unchanged(_rec("scale", "ad"), _target(None), {})


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["campaign", "ad_group"])
@pytest.mark.parametrize("budget", [4.0, 5.0])
async def test_reduce_never_increases_or_keeps_budget_at_provider_minimum(monkeypatch, level, budget):
    from integrations_control_center import snapchat_campaign_management as management
    import campaign_ai_profit_accounting_gate as profit

    monkeypatch.setattr(legacy._execution_quality, "preflight_approved_execution", AsyncMock())
    monkeypatch.setattr(profit, "require_profit_accounting_complete_for_scale", AsyncMock())
    create = AsyncMock(return_value={"status": "completed", "proposal_id": "done"})
    monkeypatch.setattr(management, "create_snapchat_management_proposal", create)
    with pytest.raises(HTTPException) as caught:
        await legacy._execute_snapchat_approval(
            None, "owner", {"action": "reduce", "entity_level": level, "change_percent": 15,
                            "rationale": "Reduce the daily budget after the current review."},
            {"account_id": "account-1", "entity_id": "entity-1", "current_daily_budget_native": budget,
             "parent_id": "campaign-1" if level == "ad_group" else None},
            idempotency_key="direction-check-001", snapshot_id="snapshot-1",
            recommendation_id="recommendation-1", snapshot_digest="digest-1",
        )
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "recommendation_budget_change_not_effective"
    create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["campaign", "ad_group"])
@pytest.mark.parametrize("action,budget,expected", [
    ("reduce", 100.0, 85_000_000),
    ("scale", 100.0, 115_000_000),
    ("reduce", 5.01, 5_000_000),
])
async def test_effective_budget_change_preserves_requested_direction(monkeypatch, level, action, budget, expected):
    from integrations_control_center import snapchat_campaign_management as management
    import campaign_ai_profit_accounting_gate as profit

    monkeypatch.setattr(legacy._execution_quality, "preflight_approved_execution", AsyncMock())
    monkeypatch.setattr(profit, "require_profit_accounting_complete_for_scale", AsyncMock())
    create = AsyncMock(return_value={"status": "completed", "proposal_id": "done"})
    monkeypatch.setattr(management, "create_snapchat_management_proposal", create)
    result = await legacy._execute_snapchat_approval(
        None, "owner", {"action": action, "entity_level": level, "change_percent": 15,
                        "rationale": "Change the daily budget after the current review."},
        {"account_id": "account-1", "entity_id": "entity-1", "current_daily_budget_native": budget,
         "parent_id": "campaign-1" if level == "ad_group" else None},
        idempotency_key="direction-check-002", snapshot_id="snapshot-1",
        recommendation_id="recommendation-1", snapshot_digest="digest-1",
    )
    assert result["status"] == "completed"
    assert create.await_args.args[3].payload == {"daily_budget_micro": expected}
    assert create.await_args.args[3].expected_outcome["action"] == action
