from datetime import datetime, timedelta, timezone

import campaign_ai_observation_window_guard as guard
import campaign_ai_monitor_legacy as legacy

NOW = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)
RID = "snapchat:campaign:a1:c1"


def prior(*, action="scale", hours=5, execution_status="awaiting_approval"):
    generated = NOW - timedelta(hours=1)
    return {
        "recent_recommendations": [{
            "generated_at": generated.isoformat(),
            "recommendations": [{
                "recommendation_id": RID,
                "action": action,
                "recommended_wait_hours": hours,
                "next_check_at": (generated + timedelta(hours=hours)).isoformat(),
                "execution_status": execution_status,
            }],
        }],
        "recent_executions": [],
    }


def recommendation(action):
    return {"recommendation_id": RID, "action": action}


def row(spend=100.0, purchases=2):
    return {"spend_sar": spend, "purchases": purchases}


def test_p1_4_duplicate_action_is_suppressed_inside_observation_window():
    result = guard.observation_window_decision(
        recommendation("scale"), row(), prior(action="scale"),
        now=NOW, target_cpa_sar=56.25,
    )
    assert result["blocked"] is True
    assert result["replacement_action"] == "monitor"
    assert result["prior_action"] == "scale"


def test_p1_4_conflicting_action_is_suppressed_inside_observation_window():
    result = guard.observation_window_decision(
        recommendation("reduce"), row(), prior(action="scale"),
        now=NOW, target_cpa_sar=56.25,
    )
    assert result["status"] == "observation_window_active"
    assert result["blocked"] is True


def test_p1_4_new_action_allowed_after_observation_window_elapsed():
    old_generated = NOW - timedelta(hours=7)
    context = {
        "recent_recommendations": [{
            "generated_at": old_generated.isoformat(),
            "recommendations": [{
                "recommendation_id": RID,
                "action": "scale",
                "recommended_wait_hours": 5,
                "next_check_at": (old_generated + timedelta(hours=5)).isoformat(),
            }],
        }]
    }
    result = guard.observation_window_decision(
        recommendation("reduce"), row(), context, now=NOW, target_cpa_sar=56.25
    )
    assert result["status"] == "clear"
    assert result["blocked"] is False


def test_p1_4_only_critical_no_purchase_pause_breaks_wait_window():
    result = guard.observation_window_decision(
        recommendation("pause"),
        row(spend=168.75, purchases=0),
        prior(action="monitor"),
        now=NOW,
        target_cpa_sar=56.25,
    )
    assert result["blocked"] is False
    assert result["emergency_override"] is True
    assert result["reason"] == "critical_no_purchase_spend"


def test_p1_4_noncritical_pause_does_not_bypass_wait_window():
    result = guard.observation_window_decision(
        recommendation("pause"),
        row(spend=160.0, purchases=0),
        prior(action="monitor"),
        now=NOW,
        target_cpa_sar=56.25,
    )
    assert result["blocked"] is True
    assert result["emergency_override"] is False


def test_p1_4_monitor_integration_rewrites_financial_action_and_keeps_audit_metadata():
    item = legacy.RecommendationItem(
        recommendation_id=RID, provider="snapchat", entity_level="campaign",
        entity_id="c1", entity_name="C1", account_id="a1", account_name="A1",
        parent_name=None, action="reduce", change_percent=15, priority="high",
        confidence="high", title="خفض", rationale="r", evidence=["e"], why_now="w",
        recommended_wait_hours=5, observation_plan="o", success_criteria=["s"],
        risk_if_ignored="risk", guardrail="g", next_check_at=(NOW + timedelta(hours=5)).isoformat(),
    )
    output = legacy.RecommendationOutput(summary="s", recommendations=[item], limitations=[])
    governed, decisions = legacy._apply_observation_window_guard(
        output,
        [{
            "provider": "snapchat", "entity_level": "campaign", "account_id": "a1",
            "entity_id": "c1", "spend_sar": 100.0, "purchases": 2,
        }],
        prior(action="scale"),
        now=NOW,
    )
    assert governed.recommendations[0].action == "monitor"
    assert governed.recommendations[0].change_percent is None
    assert decisions[RID]["status"] == "observation_window_active"
