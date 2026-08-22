from datetime import date

from campaign_ai_opportunity_to_profit_planner import build_opportunity_to_profit_plan


def _item(**overrides):
    base = {
        "product_key": "p1",
        "product_name": "منتج سعودي واعد",
        "saudi_trend_score": 84,
        "saudi_opportunity_score": 80,
        "state": "rising",
        "stage": "accelerating",
        "confidence": "high",
        "risk": "normal",
        "estimated_monthly_net_profit_sar": 30000,
        "estimated_net_profit_per_order_sar": 75,
        "estimated_monthly_orders": 400,
        "evidence_status": "measured",
    }
    base.update(overrides)
    return base


def _brief(**overrides):
    base = {
        "headline": {"monthly_profit_gap_sar": 50000},
        "measured_new_opportunities": [_item()],
        "evidence_required": [],
        "top_rising_existing_products": [],
        "products_to_watch": [],
        "external_discovery": {"allowed": False, "target_customer_market": "Saudi Arabia"},
    }
    base.update(overrides)
    return base


def test_measured_profitable_opportunity_is_analysis_ready():
    plan = build_opportunity_to_profit_plan(as_of=date(2026, 8, 22), daily_brief=_brief())
    item = plan["analysis_ready"][0]
    assert item["readiness"] == "analysis_ready"
    assert item["economics_status"] == "measured"
    assert item["estimated_profit_gap_coverage_ratio"] == 0.6


def test_unknown_economics_never_become_measured_profit():
    lead = _item(
        product_key="lead",
        evidence_status="evidence_required",
        estimated_monthly_net_profit_sar=None,
        estimated_net_profit_per_order_sar=None,
        estimated_monthly_orders=None,
    )
    plan = build_opportunity_to_profit_plan(
        as_of=date(2026, 8, 22),
        daily_brief=_brief(measured_new_opportunities=[], evidence_required=[lead]),
    )
    item = plan["evidence_required"][0]
    assert item["economics_status"] == "unknown"
    assert "monthly_profit_contribution_unknown" in item["blockers"]
    assert plan["measured_candidate_profit_sar"] == 0


def test_late_entry_is_penalized_and_blocked():
    late = _item(product_key="late", risk="late_entry", stage="peak_or_plateau")
    plan = build_opportunity_to_profit_plan(
        as_of=date(2026, 8, 22),
        daily_brief=_brief(measured_new_opportunities=[late]),
    )
    item = plan["evidence_required"][0]
    assert item["readiness"] == "evidence_required"
    assert "late_entry" in item["blockers"]


def test_measured_candidates_reduce_profit_gap():
    first = _item(product_key="a", estimated_monthly_net_profit_sar=30000)
    second = _item(product_key="b", estimated_monthly_net_profit_sar=25000)
    plan = build_opportunity_to_profit_plan(
        as_of=date(2026, 8, 22),
        daily_brief=_brief(measured_new_opportunities=[first, second]),
    )
    assert plan["measured_candidate_profit_sar"] == 55000
    assert plan["remaining_profit_gap_after_measured_candidates_sar"] == 0
    assert plan["strategy"] == "protect_and_sequence_measured_profit"


def test_no_goal_context_does_not_invent_gap():
    brief = _brief()
    brief["headline"] = {}
    plan = build_opportunity_to_profit_plan(as_of=date(2026, 8, 22), daily_brief=brief)
    assert plan["monthly_profit_gap_sar"] is None
    assert plan["remaining_profit_gap_after_measured_candidates_sar"] is None
    assert plan["strategy"] == "goal_context_missing"


def test_external_discovery_remains_saudi_targeted():
    brief = _brief(external_discovery={
        "allowed": True,
        "reason": "saudi_options_limited",
        "target_customer_market": "Saudi Arabia",
    })
    plan = build_opportunity_to_profit_plan(as_of=date(2026, 8, 22), daily_brief=brief)
    assert plan["external_discovery"]["allowed"] is True
    assert plan["external_discovery"]["target_customer_market"] == "Saudi Arabia"


def test_contract_is_read_only():
    plan = build_opportunity_to_profit_plan(as_of=date(2026, 8, 22), daily_brief=_brief())
    assert plan["contract_version"] == "opportunity_to_profit_planner_v1"
    assert plan["read_only"] is True
    assert "action" not in plan
