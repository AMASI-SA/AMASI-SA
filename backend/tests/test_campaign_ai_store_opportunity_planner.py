from campaign_ai_store_opportunity_planner import build_store_opportunity_plan


def _goal(**overrides):
    base = {
        "status": "behind_target",
        "phase": "recover_profit_gap",
        "minimum_net_profit_sar": 100000,
        "remaining_to_target_sar": 42000,
        "required_daily_net_profit_sar": 4200,
        "projected_month_end_net_profit_sar": 78000,
        "profit_accounting_quality_known": True,
        "profit_accounting_complete": True,
    }
    base.update(overrides)
    return base


def test_behind_target_opens_store_wide_evidence_lanes():
    plan = build_store_opportunity_plan(
        goal_context=_goal(),
        business_profit={"available": True},
        monthly_memory={},
        candidates=[],
    )
    lanes = {item["lane"]: item for item in plan["opportunity_lanes"]}
    assert lanes["conversion_friction"]["state"] == "evidence_required"
    assert lanes["product_margin_and_offer"]["state"] == "evidence_required"
    assert lanes["inventory_readiness"]["state"] == "evidence_required"
    assert lanes["new_growth_engine"]["state"] == "evidence_required"


def test_unknown_accounting_blocks_financial_planning():
    plan = build_store_opportunity_plan(
        goal_context=_goal(profit_accounting_quality_known=False, profit_accounting_complete=False),
        business_profit={"available": True},
        monthly_memory={},
        candidates=[],
    )
    first = plan["opportunity_lanes"][0]
    assert first["lane"] == "profit_data_quality"
    assert first["state"] == "blocked"


def test_zero_purchase_spend_is_observed_not_fabricated():
    plan = build_store_opportunity_plan(
        goal_context=_goal(),
        business_profit={"available": True},
        monthly_memory={},
        candidates=[
            {"spend_sar": 120, "purchases": 0, "data_complete": True},
            {"spend_sar": 30, "purchases": 1, "data_complete": True},
        ],
    )
    assert plan["diagnosis"]["zero_purchase_spend_sar"] == 120.0
    assert any(x["lane"] == "stop_verified_ad_waste" for x in plan["opportunity_lanes"])


def test_proven_demand_requires_complete_accounting_and_campaign_evidence():
    plan = build_store_opportunity_plan(
        goal_context=_goal(status="on_track", phase="protect_target_path", remaining_to_target_sar=10000),
        business_profit={"available": True},
        monthly_memory={},
        candidates=[{"spend_sar": 300, "purchases": 5, "roas": 3.1, "cpa_sar": 60, "data_complete": True}],
    )
    assert plan["diagnosis"]["strong_complete_campaign_signals"] == 1
    assert any(x["lane"] == "protect_and_scale_proven_demand" for x in plan["opportunity_lanes"])


def test_incomplete_campaign_signal_does_not_become_scale_evidence():
    plan = build_store_opportunity_plan(
        goal_context=_goal(),
        business_profit={"available": True},
        monthly_memory={},
        candidates=[{"spend_sar": 300, "purchases": 5, "roas": 4, "data_complete": False}],
    )
    assert plan["diagnosis"]["strong_complete_campaign_signals"] == 0


def test_repeated_failed_actions_are_counted_from_monthly_memory():
    plan = build_store_opportunity_plan(
        goal_context=_goal(),
        business_profit={"available": True},
        monthly_memory={"repeated_patterns": {"repeated_failed_or_uncertain": [{"recommendation_id": "a", "failed_or_uncertain_executions": 2}]}},
        candidates=[],
    )
    assert plan["diagnosis"]["repeated_failed_or_uncertain_actions"] == 1


def test_plan_is_read_only_and_marks_evidence_gaps():
    plan = build_store_opportunity_plan(
        goal_context=_goal(),
        business_profit={"available": True},
        monthly_memory={},
        candidates=[],
    )
    assert plan["read_only"] is True
    assert "product-level Mezan profit" in plan["evidence_gaps"]
    assert any("performs no" in item for item in plan["guardrails"])
