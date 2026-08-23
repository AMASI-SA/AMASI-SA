from datetime import date

from campaign_ai_gcc_market_expansion_planner import build_gcc_market_expansion_plan


def _opportunity_plan(**overrides):
    base = {
        "monthly_profit_gap_sar": 50000,
        "remaining_profit_gap_after_measured_candidates_sar": 40000,
    }
    base.update(overrides)
    return base


def _market(market="Saudi Arabia", **overrides):
    base = {
        "market": market,
        "evidence_status": "measured",
        "confidence": "high",
        "demand_score": 80,
        "competition_score": 50,
        "product_fit_score": 85,
        "price_sensitivity": "medium",
        "delivery_days": 2,
        "local_price_sar": 250,
        "landed_product_cost_sar": 80,
        "expected_cac_sar": 45,
        "shipping_cost_sar": 25,
        "payment_fee_sar": 8,
        "expected_return_rate": 0.05,
        "return_cost_per_return_sar": 30,
        "expected_monthly_orders": 200,
        "source_provenance": [{"source": "mezan", "status": "measured"}],
    }
    base.update(overrides)
    return base


def test_complete_measured_market_calculates_profit():
    plan = build_gcc_market_expansion_plan(
        as_of=date(2026, 8, 23),
        opportunity_plan=_opportunity_plan(),
        market_evidence=[_market()],
    )
    saudi = plan["saudi_baseline"]
    assert saudi["readiness"] == "analysis_ready"
    assert saudi["economics"]["expected_return_cost_per_order_sar"] == 1.5
    assert saudi["economics"]["expected_net_profit_per_order_sar"] == 90.5
    assert saudi["economics"]["expected_monthly_net_profit_sar"] == 18100


def test_unknown_cac_keeps_market_evidence_required():
    uae = _market("United Arab Emirates", expected_cac_sar=None)
    plan = build_gcc_market_expansion_plan(
        as_of=date(2026, 8, 23),
        opportunity_plan=_opportunity_plan(),
        market_evidence=[_market(), uae],
    )
    item = plan["evidence_required"][0]
    assert item["market"] == "United Arab Emirates"
    assert "unknown:expected_cac_sar" in item["blockers"]
    assert item["economics"]["expected_monthly_net_profit_sar"] is None
    assert item["priority_score"] == 0


def test_measured_gcc_market_can_beat_saudi_baseline():
    saudi = _market(expected_monthly_orders=150)
    qatar = _market(
        "Qatar",
        local_price_sar=280,
        expected_cac_sar=40,
        shipping_cost_sar=28,
        expected_monthly_orders=180,
    )
    plan = build_gcc_market_expansion_plan(
        as_of=date(2026, 8, 23),
        opportunity_plan=_opportunity_plan(),
        market_evidence=[saudi, qatar],
    )
    best = plan["best_measured_expansion_market"]
    assert best["market"] == "Qatar"
    assert best["better_than_saudi_on_measured_profit"] is True
    assert best["monthly_profit_delta_vs_saudi_sar"] > 0
    assert plan["strategy"] == "evaluate_best_gcc_market_against_saudi_growth"


def test_gcc_not_assumed_better_when_saudi_baseline_is_unknown():
    saudi = _market(expected_cac_sar=None)
    kuwait = _market("Kuwait")
    plan = build_gcc_market_expansion_plan(
        as_of=date(2026, 8, 23),
        opportunity_plan=_opportunity_plan(),
        market_evidence=[saudi, kuwait],
    )
    best = plan["best_measured_expansion_market"]
    assert best["market"] == "Kuwait"
    assert best["better_than_saudi_on_measured_profit"] is None
    assert plan["strategy"] == "prefer_saudi_until_gcc_measured_profit_is_superior"


def test_profit_gap_prefers_remaining_gap_from_opportunity_plan():
    plan = build_gcc_market_expansion_plan(
        as_of=date(2026, 8, 23),
        opportunity_plan=_opportunity_plan(
            monthly_profit_gap_sar=90000,
            remaining_profit_gap_after_measured_candidates_sar=25000,
        ),
        market_evidence=[_market(), _market("Bahrain")],
    )
    assert plan["monthly_profit_gap_sar"] == 25000


def test_missing_profit_goal_context_is_not_invented():
    plan = build_gcc_market_expansion_plan(
        as_of=date(2026, 8, 23),
        opportunity_plan={},
        market_evidence=[_market(), _market("Oman")],
    )
    assert plan["monthly_profit_gap_sar"] is None
    assert plan["strategy"] == "profit_gap_context_missing"


def test_unmeasured_popularity_cannot_become_ready():
    uae = _market(
        "United Arab Emirates",
        evidence_status="external_signal",
        demand_score=100,
        product_fit_score=100,
    )
    plan = build_gcc_market_expansion_plan(
        as_of=date(2026, 8, 23),
        opportunity_plan=_opportunity_plan(),
        market_evidence=[_market(), uae],
    )
    item = plan["evidence_required"][0]
    assert item["market"] == "United Arab Emirates"
    assert "market_evidence_not_measured" in item["blockers"]
    assert item["priority_score"] == 0


def test_contract_is_read_only_and_limits_markets_to_gcc():
    plan = build_gcc_market_expansion_plan(
        as_of=date(2026, 8, 23),
        opportunity_plan=_opportunity_plan(),
        market_evidence=[
            _market(),
            _market("Qatar"),
            _market("United States"),
        ],
    )
    assert plan["contract_version"] == "gcc_market_expansion_planner_v1"
    assert plan["read_only"] is True
    assert [item["market"] for item in plan["ranked_expansion_markets"]] == ["Qatar"]
    assert "action" not in plan
