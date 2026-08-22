from datetime import date

from campaign_ai_monthly_causal_memory import derive_monthly_causal_memory


def _goal(**overrides):
    base = {
        "minimum_net_profit_sar": 100000.0,
        "net_profit_to_date_sar": 42000.0,
        "remaining_to_target_sar": 58000.0,
        "projected_month_end_net_profit_sar": 65000.0,
        "status": "behind_target",
        "phase": "recover_profit_gap",
        "profit_accounting_quality_known": True,
        "profit_accounting_complete": True,
        "month_to_date": {"profit_contract_version": "mezan_profit_envelope_v1"},
    }
    base.update(overrides)
    return base


def test_memory_separates_operational_success_from_profit_causality():
    memory = derive_monthly_causal_memory(
        month_end=date(2026, 8, 22),
        goal_context=_goal(),
        recommendation_snapshots=[],
        executions=[{"execution_id": "e1", "recommendation_id": "r1", "status": "completed"}],
    )
    assert memory["what_worked"]["profit_effect_proven"] is False
    assert memory["causal_inference"]["claims"] == []
    assert memory["causal_inference"]["confidence"] == "not_established"


def test_memory_counts_recommendations_and_providers():
    snapshots = [{
        "generated_at": "2026-08-10T00:00:00+00:00",
        "recommendations": [
            {"recommendation_id": "meta:campaign:a:1", "provider": "meta", "action": "scale"},
            {"recommendation_id": "snapchat:campaign:b:2", "provider": "snapchat", "action": "reduce"},
        ],
    }]
    memory = derive_monthly_causal_memory(
        month_end=date(2026, 8, 22), goal_context=_goal(),
        recommendation_snapshots=snapshots, executions=[],
    )
    assert memory["observed_facts"]["recommendations"]["by_action"] == {"scale": 1, "reduce": 1}
    assert memory["observed_facts"]["recommendations"]["by_provider"] == {"meta": 1, "snapchat": 1}


def test_memory_detects_repeated_failed_or_uncertain_actions():
    executions = [
        {"execution_id": "e1", "recommendation_id": "r1", "status": "failed"},
        {"execution_id": "e2", "recommendation_id": "r1", "status": "verification_required"},
    ]
    memory = derive_monthly_causal_memory(
        month_end=date(2026, 8, 22), goal_context=_goal(),
        recommendation_snapshots=[], executions=executions,
    )
    assert memory["repeated_mistakes"] == [
        {"recommendation_id": "r1", "failed_or_uncertain_executions": 2}
    ]
    assert any("Do not repeat" in item for item in memory["next_month_plan"]["carry_forward_guardrails"])


def test_memory_keeps_unknown_accounting_as_constraint():
    memory = derive_monthly_causal_memory(
        month_end=date(2026, 8, 22),
        goal_context=_goal(profit_accounting_quality_known=False, profit_accounting_complete=False),
        recommendation_snapshots=[], executions=[],
    )
    goal = memory["observed_facts"]["goal"]
    assert goal["accounting_quality_known"] is False
    assert goal["accounting_complete"] is False
    assert any("incomplete/unknown" in item for item in memory["next_month_plan"]["carry_forward_guardrails"])


def test_memory_finalizes_only_on_calendar_month_end():
    current = derive_monthly_causal_memory(
        month_end=date(2026, 8, 22), goal_context=_goal(),
        recommendation_snapshots=[], executions=[],
    )
    closed = derive_monthly_causal_memory(
        month_end=date(2026, 8, 31), goal_context=_goal(),
        recommendation_snapshots=[], executions=[],
    )
    assert current["finalized"] is False
    assert closed["finalized"] is True


def test_memory_preserves_profit_target_and_gap():
    memory = derive_monthly_causal_memory(
        month_end=date(2026, 8, 22), goal_context=_goal(),
        recommendation_snapshots=[], executions=[],
    )
    goal = memory["observed_facts"]["goal"]
    assert goal["target_net_profit_sar"] == 100000.0
    assert goal["net_profit_sar"] == 42000.0
    assert goal["remaining_gap_sar"] == 58000.0
    assert memory["next_month_plan"]["profit_gap_to_revisit_sar"] == 58000.0
