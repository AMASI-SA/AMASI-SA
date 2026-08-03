from dashboard_v2_ads_executive import (
    build_salla_ads_executive_breakdown,
    resolve_salla_ad_platform,
)


def test_resolves_salla_platform_sources():
    assert resolve_salla_ad_platform({"source": "snapchat"}) == "snapchat"
    assert resolve_salla_ad_platform({"utm_source": "facebook"}) == "meta"
    assert resolve_salla_ad_platform({"source": {"channel": "Tik Tok"}}) == "tiktok"
    assert resolve_salla_ad_platform({"utm_source": "google_ads"}) == "google"
    assert resolve_salla_ad_platform({"source": "store"}) is None


def test_salla_outcomes_and_platform_costs_are_kept_separate():
    orders = [
        {"order_number": "1", "source": "snapchat", "total_amount": 250},
        {"order_number": "2", "utm_source": "snap", "total_amount": 300},
        {"order_number": "3", "utm_source": "facebook", "total_amount": 400},
        {"order_number": "4", "source": "store", "total_amount": 100},
    ]
    ads = {
        "breakdown": {
            "snapchat": 375.44,
            "tiktok": 0,
            "meta": 200,
            "google_transitional": 0,
        },
        "providers": {
            "snapchat": {"orders": 10, "revenue": 9999},
            "tiktok": {"orders": 0, "revenue": 0},
            "meta": {"orders": 4, "revenue": 8888},
        },
    }
    result = build_salla_ads_executive_breakdown(orders, ads)
    snap = result["providers"]["snapchat"]
    meta = result["providers"]["meta"]

    assert snap["salla_orders"] == 2
    assert snap["salla_sales_sar"] == 550
    assert snap["platform_reported_orders"] == 10
    assert snap["platform_cost_per_order_sar"] == 37.54
    assert snap["actual_roas"] == 1.46

    assert meta["salla_orders"] == 1
    assert meta["salla_sales_sar"] == 400
    assert meta["platform_cost_per_order_sar"] == 50

    assert result["total"]["salla_orders"] == 3
    assert result["total"]["salla_sales_sar"] == 950
    assert result["coverage"]["salla_unattributed_orders"] == 1
    assert result["source_contract"]["provider_conversion_sales_excluded"] is True


def test_google_spend_keeps_total_platform_cpa_unavailable_without_google_orders():
    result = build_salla_ads_executive_breakdown([], {
        "breakdown": {
            "snapchat": 100,
            "tiktok": 0,
            "meta": 0,
            "google_transitional": 50,
        },
        "providers": {
            "snapchat": {"orders": 2},
            "tiktok": {"orders": 0},
            "meta": {"orders": 0},
        },
    })

    assert result["providers"]["snapchat"]["platform_cost_per_order_sar"] == 50
    assert result["providers"]["google"]["platform_cost_per_order_sar"] is None
    assert result["total"]["platform_cost_per_order_sar"] is None
