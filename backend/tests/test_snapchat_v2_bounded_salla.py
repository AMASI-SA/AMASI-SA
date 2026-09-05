from __future__ import annotations

from datetime import date

import pytest

from snapchat_v2.salla_outcomes import (
    load_salla_campaign_outcomes,
    load_salla_report_summary_aggregate,
)


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def limit(self, _value):
        return self

    async def to_list(self, length=None):
        return list(self.rows[:length])


class Settings:
    async def find_one(self, _query, _projection=None):
        return {"report_included_statuses": []}


class Orders:
    def __init__(self, aggregate_rows=None):
        self.aggregate_rows = aggregate_rows or []
        self.find_queries = []
        self.aggregate_pipelines = []

    def find(self, query, _projection):
        self.find_queries.append(query)
        return Cursor([])

    def aggregate(self, pipeline, **_kwargs):
        self.aggregate_pipelines.append(pipeline)
        return Cursor(self.aggregate_rows)


class DB:
    def __init__(self, aggregate_rows=None):
        self.settings = Settings()
        self.unified_orders = Orders(aggregate_rows)


@pytest.mark.asyncio
async def test_page_detail_order_query_is_restricted_to_visible_campaign_identities():
    db = DB()
    await load_salla_campaign_outcomes(
        db,
        "owner-1",
        account_id="account-1",
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 1),
        timezone_name="Asia/Riyadh",
        identities=[{
            "account_id": "account-1",
            "campaign_id": "campaign-visible",
            "campaign_name": "Visible Campaign",
        }],
        restrict_to_identities=True,
    )

    query = db.unified_orders.find_queries[0]
    assert "$or" in query
    assert any(
        value.get("$in") == ["campaign-visible"]
        for clause in query["$or"]
        for value in clause.values()
        if isinstance(value, dict)
    )


@pytest.mark.asyncio
async def test_report_wide_salla_summary_materializes_one_scalar_not_orders():
    db = DB([{
        "orders": 120,
        "sales_sar": 12_000,
        "financial_orders": 100,
        "financial_sales_sar": 10_000,
        "known_product_cost_sar": 4_000,
        "missing_cost_orders": 0,
        "source_labelled_orders": 110,
        "exact_campaign_id_orders": 95,
    }])
    result = await load_salla_report_summary_aggregate(
        db,
        "owner-1",
        account_id="account-1",
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 1),
        timezone_name="Asia/Riyadh",
        platform_purchases=130,
        spend_sar=2_000,
    )

    assert result["snapchat_attributed_orders"] == 120
    assert result["python_order_rows_materialized"] == 0
    assert result["mongo_summary_rows_materialized"] == 1
    assert result["profitability"]["contribution_profit_sar"] == 4_000
    assert result["profitability"]["profit_margin_pct"] == 40.0
    assert len(db.unified_orders.aggregate_pipelines) == 1
    assert any("$lookup" in stage for stage in db.unified_orders.aggregate_pipelines[0])
    assert db.unified_orders.find_queries == []


@pytest.mark.asyncio
async def test_report_summary_pushes_entity_filters_into_campaign_lookup():
    db = DB([])
    result = await load_salla_report_summary_aggregate(
        db,
        "owner-1",
        account_id="account-1",
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 1),
        timezone_name="Asia/Riyadh",
        search="Winter [A]",
        active_only=True,
    )

    pipeline = db.unified_orders.aggregate_pipelines[0]
    lookup = next(stage["$lookup"] for stage in pipeline if "$lookup" in stage)
    catalog_match = lookup["pipeline"][0]["$match"]
    assert catalog_match["active"] is True
    assert catalog_match["$or"] == [
        {"name": {"$regex": "Winter\\ \\[A\\]", "$options": "i"}},
        {"external_id": {"$regex": "Winter\\ \\[A\\]", "$options": "i"}},
    ]
    attributed = next(
        stage["$set"]["_attributed"]
        for stage in pipeline
        if "$set" in stage and "_attributed" in stage["$set"]
    )
    assert attributed == {"$gt": [{"$size": "$_campaign_match"}, 0]}
    assert result["filters"] == {
        "search": "Winter [A]",
        "active_only": True,
        "source_only_orders_excluded": True,
    }
