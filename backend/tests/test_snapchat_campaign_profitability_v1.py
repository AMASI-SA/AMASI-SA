from __future__ import annotations

import pytest

from integrations_control_center import snapchat_campaign_profitability as module


def _raw_campaign(*, missing=False, fallback=False):
    return {
        "orders": 2,
        "sales_sar": 1000.0,
        "product_cost_sar": 400.0,
        "allocated_product_sales_sar": 1000.0,
        "unallocated_sales_sar": 0.0,
        "missing_cost_orders": 1 if missing else 0,
        "fallback_cost_orders": 1 if fallback else 0,
        "no_products_orders": 0,
        "products": {
            "product-a": {
                "identity": "product-a",
                "salla_product_id": "101",
                "mezan_product_id": "mpv2_101",
                "name": "منتج أ",
                "sku": "A-1",
                "image_url": "",
                "units": 2,
                "orders": 2,
                "sales_sar": 600.0,
                "cost_sar": 220.0,
                "missing_everywhere": missing,
                "uses_salla_fallback": fallback,
                "cost_sources": {"mezan_v2_base"},
            },
            "product-b": {
                "identity": "product-b",
                "salla_product_id": "102",
                "mezan_product_id": "mpv2_102",
                "name": "منتج ب",
                "sku": "B-1",
                "image_url": "",
                "units": 1,
                "orders": 1,
                "sales_sar": 400.0,
                "cost_sar": 180.0,
                "missing_everywhere": False,
                "uses_salla_fallback": False,
                "cost_sources": {"mezan_v2_variant"},
            },
        },
    }


def test_complete_campaign_profit_is_sales_minus_product_cost_and_ads():
    result = module._finalize_campaign(
        _raw_campaign(),
        spend_sar=200.0,
    )

    assert result["sales_sar"] == 1000.0
    assert result["product_cost_sar"] == 400.0
    assert result["ad_spend_sar"] == 200.0
    assert result["gross_profit_before_ads_sar"] == 600.0
    assert result["contribution_profit_sar"] == 400.0
    assert result["profit_margin_pct"] == 40.0
    assert result["gross_margin_pct"] == 60.0
    assert result["break_even_roas"] == pytest.approx(1.666667)
    assert result["cost_status"] == "complete"

    products = {row["identity"]: row for row in result["products"]}
    assert products["product-a"]["allocated_ad_spend_sar"] == 120.0
    assert products["product-a"]["contribution_profit_sar"] == 260.0
    assert products["product-b"]["allocated_ad_spend_sar"] == 80.0
    assert products["product-b"]["contribution_profit_sar"] == 140.0
    assert sum(row["allocated_ad_spend_sar"] for row in products.values()) == 200.0


def test_missing_product_cost_prevents_false_campaign_profit():
    result = module._finalize_campaign(
        _raw_campaign(missing=True),
        spend_sar=200.0,
    )

    assert result["known_product_cost_sar"] == 400.0
    assert result["product_cost_sar"] is None
    assert result["gross_profit_before_ads_sar"] is None
    assert result["contribution_profit_sar"] is None
    assert result["profit_margin_pct"] is None
    assert result["cost_status"] == "missing"
    assert result["products"][0]["product_cost_sar"] is None


def test_salla_fallback_is_reported_but_profit_remains_calculable():
    result = module._finalize_campaign(
        _raw_campaign(fallback=True),
        spend_sar=200.0,
    )

    assert result["product_cost_sar"] == 400.0
    assert result["contribution_profit_sar"] == 400.0
    assert result["cost_status"] == "salla_fallback"
    assert result["products"][0]["cost_status"] == "salla_fallback"


def test_order_addition_preserves_product_grain_and_missing_coverage():
    bucket = module._new_campaign_bucket()
    module._add_order_to_campaign(bucket, {
        "order_sales_sar": 250.0,
        "product_cost_sar": 100.0,
        "allocated_product_sales_sar": 250.0,
        "unallocated_sales_sar": 0.0,
        "missing_everywhere": False,
        "uses_salla_fallback": False,
        "no_products": False,
        "lines": [
            {
                "identity": "product-a",
                "salla_product_id": "101",
                "mezan_product_id": "mpv2_101",
                "name": "منتج أ",
                "sku": "A-1",
                "image_url": "",
                "units": 2,
                "allocated_sales_sar": 250.0,
                "cost_sar": 100.0,
                "base_complete": True,
                "uses_salla_fallback": False,
                "base_cost_source": "mezan_v2_base",
            },
        ],
    })

    assert bucket["orders"] == 1
    assert bucket["sales_sar"] == 250.0
    assert bucket["product_cost_sar"] == 100.0
    assert bucket["products"]["product-a"]["units"] == 2
    assert bucket["products"]["product-a"]["orders"] == 1
    assert bucket["products"]["product-a"]["cost_sources"] == {"mezan_v2_base"}


def test_profit_scope_is_conservative_and_read_only():
    result = module._finalize_campaign(_raw_campaign(), spend_sar=200.0)
    assert "before_payment_shipping_bnpl" in result["profit_scope"]
    assert result["allocation_method"] == (
        module.CAMPAIGN_PROFITABILITY_ALLOCATION_METHOD
    )
    assert module.CAMPAIGN_PROFITABILITY_CACHE_TTL_SECONDS == 300
