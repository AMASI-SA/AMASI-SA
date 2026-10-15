import pytest
from fastapi import HTTPException

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
