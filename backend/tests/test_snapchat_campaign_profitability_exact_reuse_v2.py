from __future__ import annotations

from copy import deepcopy

import pytest

from integrations_control_center import (
    snapchat_campaign_profitability_exact_reuse as module,
)


@pytest.mark.asyncio
async def test_capture_reuses_the_same_campaign_matcher(monkeypatch):
    orders = [
        {"id": "order-1", "campaign_id": "campaign-1", "total_amount": 100},
        {"id": "order-2", "campaign_id": "campaign-2", "total_amount": 200},
        {"id": "order-3", "campaign_id": "unknown", "total_amount": 300},
    ]

    async def filtered_orders(*args, **kwargs):
        return deepcopy(orders)

    monkeypatch.setattr(module, "_filtered_orders", filtered_orders)

    identities = [
        {
            "account_id": "account-1",
            "campaign_id": "campaign-1",
            "campaign_name": "حملة 1",
        },
        {
            "account_id": "account-1",
            "campaign_id": "campaign-2",
            "campaign_name": "حملة 2",
        },
    ]

    matched = await module.capture_exact_matched_orders(
        object(),
        "owner-1",
        date_from="2026-08-04",
        date_to="2026-08-04",
        identities=identities,
    )

    assert [order["id"] for order in matched[("account-1", "campaign-1")]] == [
        "order-1"
    ]
    assert [order["id"] for order in matched[("account-1", "campaign-2")]] == [
        "order-2"
    ]
    assert sum(len(rows) for rows in matched.values()) == 2


@pytest.mark.asyncio
async def test_profitability_uses_exact_orders_and_campaign_spend(monkeypatch):
    module._CACHE.clear()

    async def cost_context(db, user_id):
        return {"loaded": True}

    def order_cost(order, context):
        return {
            "order_sales_sar": float(order["total_amount"]),
            "product_cost_sar": float(order["product_cost"]),
            "allocated_product_sales_sar": float(order["total_amount"]),
            "unallocated_sales_sar": 0.0,
            "missing_everywhere": False,
            "uses_salla_fallback": False,
            "mezan_cost_complete": True,
            "no_products": False,
            "lines": [],
        }

    monkeypatch.setattr(module.profitability, "_load_cost_context", cost_context)
    monkeypatch.setattr(module.profitability, "_order_cost_and_products", order_cost)

    by_campaign, totals = await module.calculate_profitability_from_exact_matches(
        object(),
        "owner-1",
        date_from="2026-08-04",
        date_to="2026-08-04",
        matched_orders={
            ("account-1", "campaign-1"): [
                {"total_amount": 100.0, "product_cost": 40.0},
                {"total_amount": 50.0, "product_cost": 20.0},
            ]
        },
        campaign_spend={("account-1", "campaign-1"): 30.0},
    )

    row = by_campaign[("account-1", "campaign-1")]
    assert row["orders"] == 2
    assert row["sales_sar"] == 150.0
    assert row["product_cost_sar"] == 60.0
    assert row["ad_spend_sar"] == 30.0
    assert row["contribution_profit_sar"] == 60.0
    assert row["profit_margin_pct"] == 40.0
    assert totals["orders"] == 2
    assert totals["product_cost_sar"] == 60.0
    assert totals["contribution_profit_sar"] == 60.0


def test_source_policy_is_exact_and_read_only():
    assert module.SOURCE_MODE == "snapchat_salla_visible_matches_profitability_v2"
    assert module.CACHE_TTL_SECONDS == 5 * 60
