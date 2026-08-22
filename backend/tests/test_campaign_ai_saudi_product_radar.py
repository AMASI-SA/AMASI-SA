from datetime import date, timedelta

import campaign_ai_saudi_product_radar as radar


def signal(day, score, **extra):
    return {
        "product_key": extra.pop("product_key", "p1"),
        "product_name": extra.pop("product_name", "منتج 1"),
        "market": extra.pop("market", "SA"),
        "source": extra.pop("source", "public_market"),
        "score": score,
        "observed_on": day.isoformat(),
        **extra,
    }


def test_lifecycle_rising():
    end = date(2026, 8, 22)
    rows = [(end - timedelta(days=14), 30), (end - timedelta(days=10), 35), (end - timedelta(days=2), 70), (end, 80)]
    assert radar.classify_lifecycle(rows, as_of=end)["state"] == "rising"


def test_lifecycle_falling():
    end = date(2026, 8, 22)
    rows = [(end - timedelta(days=14), 80), (end - timedelta(days=10), 75), (end - timedelta(days=2), 40), (end, 35)]
    assert radar.classify_lifecycle(rows, as_of=end)["state"] == "falling"


def test_saudi_first_filters_non_saudi_from_primary_ranking():
    end = date(2026, 8, 22)
    result = radar.build_saudi_product_radar(
        as_of=end,
        market_signals=[
            signal(end, 70, product_key="sa"),
            signal(end, 99, product_key="ae", market="AE"),
        ],
        goal_context={},
    )
    keys = [item["product_key"] for item in result["saudi_opportunities"]]
    assert keys == ["sa"]
    assert result["sales_market"] == "Saudi Arabia"


def test_external_discovery_only_when_saudi_options_limited():
    end = date(2026, 8, 22)
    result = radar.build_saudi_product_radar(
        as_of=end,
        market_signals=[signal(end - timedelta(days=1), 75), signal(end, 80)],
        goal_context={},
    )
    policy = result["external_discovery_policy"]
    assert policy["allowed"] is True
    assert policy["target_customer_market"] == "Saudi Arabia"
    assert "alibaba" in policy["sources"]


def test_external_discovery_stays_off_when_saudi_options_are_sufficient():
    end = date(2026, 8, 22)
    signals = []
    for idx in range(3):
        key = f"p{idx}"
        signals += [
            signal(end - timedelta(days=2), 70 + idx, product_key=key),
            signal(end, 75 + idx, product_key=key),
        ]
    result = radar.build_saudi_product_radar(
        as_of=end, market_signals=signals, goal_context={}
    )
    assert result["external_discovery_policy"]["allowed"] is False
    assert result["external_discovery_policy"]["sources"] == []


def test_profit_gap_can_trigger_external_discovery_even_with_three_options():
    end = date(2026, 8, 22)
    signals = []
    for idx in range(3):
        key = f"p{idx}"
        signals += [
            signal(end - timedelta(days=2), 75, product_key=key, estimated_net_profit_per_order_sar=20, estimated_monthly_orders=10),
            signal(end, 80, product_key=key, estimated_net_profit_per_order_sar=20, estimated_monthly_orders=10),
        ]
    result = radar.build_saudi_product_radar(
        as_of=end,
        market_signals=signals,
        goal_context={"remaining_to_target_sar": 5000},
    )
    assert result["measured_saudi_opportunity_profit_coverage_sar"] == 600
    assert result["external_discovery_policy"]["allowed"] is True
    assert result["external_discovery_policy"]["reason"] == "saudi_options_do_not_cover_profit_gap"


def test_existing_product_is_classified_separately():
    end = date(2026, 8, 22)
    result = radar.build_saudi_product_radar(
        as_of=end,
        market_signals=[
            signal(end - timedelta(days=1), 70, store_product_id="salla-1"),
            signal(end, 72, store_product_id="salla-1"),
        ],
        goal_context={},
    )
    assert len(result["existing_products"]) == 1
    assert result["saudi_opportunities"] == []
    assert result["existing_products"][0]["store_product_id"] == "salla-1"
