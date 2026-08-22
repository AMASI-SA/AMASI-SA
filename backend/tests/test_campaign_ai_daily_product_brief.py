from datetime import date

from campaign_ai_daily_product_brief import build_daily_product_brief


def _item(name, *, state="rising", stage="accelerating", risk="normal", confidence="high",
          trend_score=80, opportunity_score=75, measured=True, store_product_id=None,
          monthly_profit=5000):
    return {
        "product_key": name.lower().replace(" ", "-"),
        "product_name": name,
        "store_product_id": store_product_id,
        "saudi_trend_score": trend_score,
        "saudi_opportunity_score": opportunity_score,
        "trend_lifecycle": {
            "state": state,
            "estimated_wave_stage": stage,
            "risk": risk,
            "confidence": confidence,
            "momentum": 14,
            "acceleration": 5,
        },
        "lifecycle": {"state": state, "confidence": confidence},
        "estimated_monthly_net_profit_sar": monthly_profit,
        "estimated_net_profit_per_order_sar": 50,
        "estimated_monthly_orders": 100,
        "evidence_status": "measured" if measured else "evidence_required",
        "evidence_count": 6 if measured else 1,
        "sources": ["saudi_search", "saudi_competitor"] if measured else ["saudi_search"],
    }


def test_brief_prioritizes_accelerating_measured_opportunity():
    radar = {
        "saudi_opportunities": [
            _item("Stable Item", state="stable", stage="developing", trend_score=70),
            _item("Fast Item", state="rising", stage="accelerating", trend_score=90),
        ],
        "existing_products": [],
        "monthly_profit_gap_sar": 20000,
        "measured_saudi_opportunity_profit_coverage_sar": 10000,
    }
    brief = build_daily_product_brief(as_of=date(2026, 8, 22), radar_snapshot=radar)
    assert brief["top_new_saudi_opportunities"][0]["product_name"] == "Fast Item"


def test_brief_surfaces_existing_decay_risk():
    radar = {
        "existing_products": [
            _item("Cooling Existing", state="falling", stage="cooling", risk="trend_decay", store_product_id="p1"),
        ],
        "saudi_opportunities": [],
    }
    brief = build_daily_product_brief(as_of=date(2026, 8, 22), radar_snapshot=radar)
    assert brief["products_to_watch"][0]["risk"] == "trend_decay"


def test_brief_keeps_low_confidence_opportunity_in_evidence_required_lane():
    radar = {
        "existing_products": [],
        "saudi_opportunities": [
            _item("One Signal", confidence="low", measured=False, trend_score=88),
        ],
    }
    brief = build_daily_product_brief(as_of=date(2026, 8, 22), radar_snapshot=radar)
    assert brief["headline"]["evidence_required_count"] == 1
    assert brief["evidence_required"][0]["product_name"] == "One Signal"


def test_brief_connects_measured_coverage_to_monthly_profit_gap():
    radar = {
        "existing_products": [],
        "saudi_opportunities": [],
        "monthly_profit_gap_sar": 30000,
        "measured_saudi_opportunity_profit_coverage_sar": 12000,
    }
    brief = build_daily_product_brief(as_of=date(2026, 8, 22), radar_snapshot=radar)
    assert brief["headline"]["goal_status"] == "profit_gap_still_open"
    assert brief["headline"]["profit_gap_after_measured_coverage_sar"] == 18000


def test_brief_marks_saudi_coverage_sufficient_when_gap_is_covered():
    radar = {
        "existing_products": [],
        "saudi_opportunities": [],
        "monthly_profit_gap_sar": 10000,
        "measured_saudi_opportunity_profit_coverage_sar": 14000,
    }
    brief = build_daily_product_brief(as_of=date(2026, 8, 22), radar_snapshot=radar)
    assert brief["headline"]["goal_status"] == "saudi_opportunity_coverage_sufficient"
    assert brief["headline"]["profit_gap_after_measured_coverage_sar"] == 0


def test_external_discovery_stays_saudi_customer_oriented():
    radar = {
        "existing_products": [],
        "saudi_opportunities": [],
        "external_discovery_policy": {
            "allowed": True,
            "reason": "saudi_options_limited",
            "sources": ["alibaba", "shein_public_market"],
            "target_customer_market": "Saudi Arabia",
        },
    }
    brief = build_daily_product_brief(as_of=date(2026, 8, 22), radar_snapshot=radar)
    assert brief["external_discovery"]["allowed"] is True
    assert brief["external_discovery"]["target_customer_market"] == "Saudi Arabia"


def test_contract_is_read_only_and_does_not_emit_actions():
    brief = build_daily_product_brief(as_of=date(2026, 8, 22), radar_snapshot={})
    assert brief["contract_version"] == "daily_product_brief_v1"
    assert brief["read_only"] is True
    assert "actions" not in brief
