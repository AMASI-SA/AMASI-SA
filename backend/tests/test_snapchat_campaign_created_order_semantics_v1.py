from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

import auth
from integrations_control_center import (
    snapchat_campaign_created_order_semantics as module,
)
from integrations_control_center import (
    snapchat_campaign_profitability as profitability,
)


def _matches(row, query):
    for key, condition in query.items():
        value = row.get(key)
        if isinstance(condition, dict):
            for operator, expected in condition.items():
                if operator == "$gte" and not (value is not None and value >= expected):
                    return False
                if operator == "$lte" and not (value is not None and value <= expected):
                    return False
                if operator == "$ne" and value == expected:
                    return False
        elif value != condition:
            return False
    return True


class FakeCursor:
    def __init__(self, rows):
        self.rows = deepcopy(list(rows))

    async def to_list(self, length):
        return deepcopy(self.rows[:length])


class FakeCollection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query, projection=None):
        return FakeCursor(row for row in self.rows if _matches(row, query))


class FakeDB:
    def __init__(self, orders):
        self.unified_orders = FakeCollection(deepcopy(orders))


@pytest.mark.asyncio
async def test_created_orders_remain_counted_when_cancelled(monkeypatch):
    async def settings(db, user_id):
        return {
            "report_included_statuses": ["completed", "delivered"],
            "hide_inferred_date_orders": False,
        }

    monkeypatch.setattr(auth, "ensure_user_settings", settings)
    db = FakeDB([
        {
            "user_id": "owner-1",
            "id": "order-1",
            "order_date": "2026-08-04",
            "created_at": "2026-08-04T01:00:00+03:00",
            "utm_campaign_id": "campaign-1",
            "order_status": "completed",
            "total_amount": 100.0,
        },
        {
            "user_id": "owner-1",
            "id": "order-2",
            "order_date": "2026-08-04",
            "created_at": "2026-08-04T02:00:00+03:00",
            "utm_campaign_id": "campaign-1",
            "order_status": "delivered",
            "total_amount": 50.0,
        },
        {
            "user_id": "owner-1",
            "id": "order-3",
            "order_date": "2026-08-04",
            "created_at": "2026-08-04T03:00:00+03:00",
            "utm_campaign_id": "campaign-1",
            "order_status": "cancelled",
            "total_amount": 75.0,
        },
    ])
    identities = [{
        "account_id": "account-1",
        "campaign_id": "campaign-1",
        "campaign_name": "حملة 1",
    }]

    by_campaign, by_date, coverage, financial = (
        await module.build_created_and_financial_outcomes(
            db,
            "owner-1",
            date_from="2026-08-04",
            date_to="2026-08-04",
            timezone_name="Asia/Riyadh",
            identities=identities,
        )
    )

    row = by_campaign[("account-1", "campaign-1")]
    assert row["orders"] == 3
    assert row["created_orders"] == 3
    assert row["financial_orders"] == 2
    assert row["cancelled_orders"] == 1
    assert row["excluded_orders"] == 1
    assert row["sales_sar"] == 150.0
    assert by_date["2026-08-04"]["orders"] == 3
    assert [order["id"] for order in financial[("account-1", "campaign-1")]] == [
        "order-1",
        "order-2",
    ]
    assert coverage["created_orders_matched"] == 3
    assert coverage["financial_orders_matched"] == 2
    assert coverage["cancelled_orders_matched"] == 1
    assert coverage["order_count_semantics"] == (
        "created_orders_all_statuses_fixed_by_creation_time"
    )


@pytest.mark.asyncio
async def test_riyadh_business_timezone_controls_salla_order_day(monkeypatch):
    async def settings(db, user_id):
        return {
            "report_included_statuses": ["completed"],
            "hide_inferred_date_orders": False,
        }

    monkeypatch.setattr(auth, "ensure_user_settings", settings)
    db = FakeDB([
        {
            "user_id": "owner-1",
            "id": "order-la",
            "order_date": "2026-08-04",
            "created_at": "2026-08-04T06:30:00+00:00",
            "utm_campaign_id": "campaign-1",
            "order_status": "completed",
            "total_amount": 100.0,
        },
    ])
    identities = [{
        "account_id": "account-1",
        "campaign_id": "campaign-1",
        "campaign_name": "Campaign 1",
    }]

    by_campaign, by_date, _, _ = await module.build_created_and_financial_outcomes(
        db,
        "owner-1",
        date_from="2026-08-04",
        date_to="2026-08-04",
        timezone_name="America/Los_Angeles",
        identities=identities,
    )

    assert by_campaign[("account-1", "campaign-1")]["orders"] == 1
    assert by_date["2026-08-04"]["orders"] == 1


@pytest.mark.asyncio
async def test_profitability_uses_financial_orders_only(monkeypatch):
    module._PROFIT_CACHE.clear()

    async def cost_revision(db, user_id):
        return 0

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

    monkeypatch.setattr(profitability, "_load_cost_context", cost_context)
    monkeypatch.setattr(profitability, "_order_cost_and_products", order_cost)
    monkeypatch.setattr(module, "get_product_cost_revision", cost_revision)

    by_campaign, totals = await module.calculate_financial_profitability(
        object(),
        "owner-1",
        account_id="account-1",
        date_from="2026-08-04",
        date_to="2026-08-04",
        financial_matched={
            ("account-1", "campaign-1"): [
                {"total_amount": 150.0, "product_cost": 60.0},
            ],
        },
        campaign_spend={
            ("account-1", "campaign-1"): 30.0,
            ("account-1", "campaign-without-orders"): 40.0,
        },
    )

    row = by_campaign[("account-1", "campaign-1")]
    assert row["orders"] == 1
    assert row["sales_sar"] == 150.0
    assert row["product_cost_sar"] == 60.0
    assert row["contribution_profit_sar"] == 60.0
    assert totals["orders"] == 1
    assert totals["ad_spend_sar"] == 70.0
    assert totals["contribution_profit_sar"] == 20.0
    assert totals["profit_margin_pct"] == pytest.approx(13.33)
    assert totals["total_ad_spend_scope"] == "all_campaigns_in_report"


def test_cancelled_status_detection_handles_arabic_and_english():
    assert module.is_cancelled_order({"order_status": "cancelled"}) is True
    assert module.is_cancelled_order({"order_status_native": "ملغي"}) is True
    assert module.is_cancelled_order({"order_status": "completed"}) is False


@pytest.mark.asyncio
async def test_duplicate_order_ids_are_counted_once(monkeypatch):
    async def settings(db, user_id):
        return {
            "report_included_statuses": ["completed"],
            "hide_inferred_date_orders": False,
        }

    monkeypatch.setattr(auth, "ensure_user_settings", settings)
    order = {
        "user_id": "owner-1",
        "id": "duplicate-order",
        "order_date": "2026-09-03",
        "created_at": "2026-09-03T10:00:00+03:00",
        "utm_campaign_id": "campaign-1",
        "order_status": "completed",
        "total_amount": 50.0,
    }
    by_campaign, _, coverage, financial = (
        await module.build_created_and_financial_outcomes(
            FakeDB([order, deepcopy(order)]),
            "owner-1",
            date_from="2026-09-03",
            date_to="2026-09-03",
            timezone_name="America/Los_Angeles",
            identities=[{
                "account_id": "account-1",
                "campaign_id": "campaign-1",
                "campaign_name": "Campaign 1",
            }],
        )
    )
    assert by_campaign[("account-1", "campaign-1")]["orders"] == 1
    assert len(financial[("account-1", "campaign-1")]) == 1
    assert coverage["duplicate_orders_excluded"] == 1


@pytest.mark.asyncio
async def test_september_four_salla_order_does_not_enter_september_three(monkeypatch):
    async def settings(db, user_id):
        return {
            "report_included_statuses": ["completed"],
            "hide_inferred_date_orders": False,
        }

    monkeypatch.setattr(auth, "ensure_user_settings", settings)
    order = {
        "user_id": "owner-1",
        "id": "riyadh-september-four",
        "order_date": "2026-09-04",
        "created_at": "2026-09-04T00:15:00+03:00",
        "utm_campaign_id": "campaign-1",
        "order_status": "completed",
        "total_amount": 50.0,
    }
    by_campaign, by_date, coverage, _ = (
        await module.build_created_and_financial_outcomes(
            FakeDB([order]),
            "owner-1",
            date_from="2026-09-03",
            date_to="2026-09-03",
            timezone_name="America/Los_Angeles",
            identities=[{
                "account_id": "account-1",
                "campaign_id": "campaign-1",
                "campaign_name": "Campaign 1",
            }],
        )
    )
    assert by_campaign == {}
    assert by_date == {}
    assert coverage["salla_total_orders"] == 0
