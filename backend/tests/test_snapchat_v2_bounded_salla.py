from __future__ import annotations

from datetime import date

import pytest

from snapchat_v2.salla_outcomes import (
    _match_order_campaign,
    load_salla_campaign_outcomes,
    load_salla_report_summary_aggregate,
)
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import BINDINGS, RESOURCES
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def limit(self, _value):
        return self

    async def to_list(self, length=None):
        return list(self.rows[:length])

    def __aiter__(self):
        self._iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class Settings:
    async def find_one(self, _query, _projection=None):
        return {"report_included_statuses": []}


class Collection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.find_queries = []

    def find(self, query, _projection=None):
        self.find_queries.append(query)
        return Cursor(self.rows)


class Orders(Collection):
    def __init__(self, aggregate_rows=None, cost_orders=None, rows=None):
        super().__init__(rows)
        self.aggregate_results = [
            list(aggregate_rows or []),
            list(cost_orders or []),
        ]
        self.find_queries = []
        self.aggregate_pipelines = []

    def find(self, query, _projection):
        self.find_queries.append(query)
        return Cursor(self.rows)

    def aggregate(self, pipeline, **_kwargs):
        self.aggregate_pipelines.append(pipeline)
        index = min(len(self.aggregate_pipelines) - 1, len(self.aggregate_results) - 1)
        return Cursor(self.aggregate_results[index])


class DB:
    def __init__(
        self,
        aggregate_rows=None,
        cost_orders=None,
        products=None,
        profiles=None,
        option_bindings=None,
        product_bindings=None,
        resources=None,
        orders=None,
    ):
        self.settings = Settings()
        self.unified_orders = Orders(aggregate_rows, cost_orders, orders)
        self.order_status_policy = Collection()
        self.collections = {
            PRODUCTS: Collection(products),
            COST_PROFILES: Collection(profiles),
            BINDINGS: Collection(option_bindings),
            PRODUCT_RESOURCE_BINDINGS: Collection(product_bindings),
            RESOURCES: Collection(resources),
        }
    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())


def test_salla_campaign_match_remains_provider_neutral():
    meta = _match_order_campaign(
        {"source": "Meta Ads", "utm_campaign": "Launch Campaign"},
        id_lookup={},
        name_lookup={"launch campaign": ("meta-account", "meta-campaign")},
        provider_key="meta",
    )
    snapchat = _match_order_campaign(
        {"source": "Snapchat Ads", "utm_campaign": "Launch Campaign"},
        id_lookup={},
        name_lookup={"launch campaign": ("snap-account", "snap-campaign")},
    )
    foreign = _match_order_campaign(
        {"source": "Snapchat Ads", "utm_campaign": "Launch Campaign"},
        id_lookup={},
        name_lookup={"launch campaign": ("meta-account", "meta-campaign")},
        provider_key="meta",
    )

    assert meta == (("meta-account", "meta-campaign"), "campaign_name")
    assert snapchat == (("snap-account", "snap-campaign"), "campaign_name")
    assert foreign == (None, "foreign_platform")


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
    db = DB(
        [{
            "orders": 1,
            "sales_sar": 100,
            "financial_orders": 1,
            "financial_sales_sar": 100,
            "source_labelled_orders": 1,
            "exact_campaign_id_orders": 1,
        }],
        cost_orders=[{
            "order_status": "delivered",
            "total_amount": 100,
            "total_product_cost": 999,
            "products": [{"product_id": "product-1", "quantity": 1, "total": 100}],
        }],
        products=[{
            "id": "product-1",
            "salla_product_id": "product-1",
            "cost_price_from_salla": 40,
            "variants": [],
        }],
    )
    result = await load_salla_report_summary_aggregate(
        db,
        "owner-1",
        account_id="account-1",
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 1),
        timezone_name="Asia/Riyadh",
        platform_purchases=2,
        spend_sar=20,
    )

    assert result["snapchat_attributed_orders"] == 1
    assert result["python_order_rows_materialized"] == 1
    assert result["mongo_summary_rows_materialized"] == 1
    assert result["profitability"]["product_cost_sar"] == 40
    assert result["profitability"]["contribution_profit_sar"] == 40
    assert result["profitability"]["profit_margin_pct"] == 40.0
    assert result["profitability"]["stored_total_product_cost_used"] is False
    assert result["profitability"]["stored_cost_mismatch_orders"] == 1
    assert len(db.unified_orders.aggregate_pipelines) == 2
    assert any("$lookup" in stage for stage in db.unified_orders.aggregate_pipelines[0])
    assert db.unified_orders.find_queries == []
    product_query = db[PRODUCTS].find_queries[0]
    assert product_query["$or"]
    assert product_query != {"user_id": "owner-1"}


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
    assert {"$ne": ["$missing_from_latest_sync", True]} in catalog_match["$expr"]["$and"]
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


def _attributed_order(**overrides):
    row = {
        "order_number": "order-1",
        "created_at": "2026-09-01T10:00:00+03:00",
        "order_date": "2026-09-01",
        "order_status": "delivered",
        "total_amount": 100,
        "products": [
            {
                "product_id": "product-1",
                "quantity": 2,
                "total": 100,
                "options": [{"name": "Wrap", "value": "Premium"}],
            }
        ],
        "raw_by_source": {
            "salla_direct": {
                "ad_platform_source": "snapchat",
                "campaign_id": "campaign-1",
            }
        },
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_row_and_summary_use_same_targeted_canonical_cost_engine():
    order = _attributed_order(total_product_cost=999)
    products = [{
        "id": "product-1",
        "salla_product_id": "product-1",
        "cost_price_from_salla": 90,
        "variants": [],
    }]
    profiles = [{"salla_product_id": "product-1", "base_cost": 10}]
    product_bindings = [{
        "id": "component-1",
        "salla_product_id": "product-1",
        "resource_id": "resource-1",
        "quantity": 2,
    }]
    option_bindings = [{
        "id": "option-1",
        "salla_product_id": "product-1",
        "option_name": "Wrap",
        "value_name": "Premium",
        "mode": "direct",
        "direct_amount": 3,
    }]
    resources = [{"id": "resource-1", "unit_cost": 5}]
    kwargs = {
        "products": products,
        "profiles": profiles,
        "product_bindings": product_bindings,
        "option_bindings": option_bindings,
        "resources": resources,
    }
    row_db = DB(orders=[order], **kwargs)
    row_result = await load_salla_campaign_outcomes(
        row_db,
        "owner-1",
        account_id="account-1",
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 1),
        timezone_name="Asia/Riyadh",
        identities=[{
            "account_id": "account-1",
            "campaign_id": "campaign-1",
            "campaign_name": "Campaign 1",
        }],
        campaign_spend_sar={"campaign-1": 20},
        restrict_to_identities=True,
    )
    summary_db = DB(
        aggregate_rows=[{
            "orders": 1,
            "sales_sar": 100,
            "financial_orders": 1,
            "financial_sales_sar": 100,
            "source_labelled_orders": 1,
            "exact_campaign_id_orders": 1,
        }],
        cost_orders=[order],
        **kwargs,
    )
    summary = await load_salla_report_summary_aggregate(
        summary_db,
        "owner-1",
        account_id="account-1",
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 1),
        timezone_name="Asia/Riyadh",
        spend_sar=20,
    )

    row_profit = row_result["by_campaign"]["campaign-1"]["profitability"]
    summary_profit = summary["profitability"]
    # Per unit: 10 base + (5*2) component + 3 option = 23; quantity 2 => 46.
    assert row_profit["product_cost_sar"] == 46
    assert summary_profit["product_cost_sar"] == 46
    assert row_profit["contribution_profit_sar"] == 34
    assert summary_profit["contribution_profit_sar"] == 34
    assert summary_profit["stored_cost_mismatch_orders"] == 1
    assert summary["cost_read_diagnostics"]["products_materialized"] == 1
    assert row_result["cost_read_diagnostics"]["products_materialized"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("order_changes", "expected_cost"),
    [
        ({"total_product_cost": None}, 40),
        ({"total_product_cost": 999}, 40),
        ({"order_status": "refunded", "total_product_cost": 999}, 0),
        ({"order_status": "cancelled", "total_product_cost": 999}, 0),
        ({"actual_partial_refund_amount": 50, "total_product_cost": 999}, 20),
    ],
)
async def test_summary_cost_uses_current_catalog_and_existing_refund_policy(
    order_changes,
    expected_cost,
):
    order = _attributed_order(**order_changes)
    db = DB(
        aggregate_rows=[{
            "orders": 1,
            "sales_sar": 100,
            "financial_orders": 1,
            "financial_sales_sar": 100,
            "source_labelled_orders": 1,
            "exact_campaign_id_orders": 1,
        }],
        cost_orders=[order],
        products=[{
            "id": "product-1",
            "salla_product_id": "product-1",
            "cost_price_from_salla": 90,
            "variants": [],
        }],
        profiles=[{"salla_product_id": "product-1", "base_cost": 20}],
    )
    result = await load_salla_report_summary_aggregate(
        db,
        "owner-1",
        account_id="account-1",
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 1),
        timezone_name="Asia/Riyadh",
        spend_sar=0,
    )

    assert result["profitability"]["product_cost_sar"] == expected_cost
    assert result["profitability"]["contribution_profit_sar"] == 100 - expected_cost
    product_query = db[PRODUCTS].find_queries[0]
    assert product_query["$or"]
    assert product_query != {"user_id": "owner-1"}


@pytest.mark.asyncio
async def test_missing_canonical_cost_is_partial_and_never_zero_filled():
    order = _attributed_order(total_product_cost=77)
    db = DB(
        aggregate_rows=[{
            "orders": 1,
            "sales_sar": 100,
            "financial_orders": 1,
            "financial_sales_sar": 100,
            "source_labelled_orders": 1,
            "exact_campaign_id_orders": 1,
        }],
        cost_orders=[order],
        products=[],
    )

    result = await load_salla_report_summary_aggregate(
        db,
        "owner-1",
        account_id="account-1",
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 1),
        timezone_name="Asia/Riyadh",
        spend_sar=20,
    )

    assert result["coverage_status"] == "partial"
    assert result["profitability"]["cost_status"] == "partial"
    assert result["profitability"]["product_cost_sar"] is None
    assert result["profitability"]["contribution_profit_sar"] is None
    assert result["profitability"]["known_product_cost_sar"] == 0
    assert result["profitability"]["stored_total_product_cost_used"] is False


@pytest.mark.asyncio
async def test_twenty_five_campaign_page_never_requests_full_product_catalog():
    orders = [
        _attributed_order(
            order_number=f"order-{index}",
            products=[{
                "product_id": f"product-{index}",
                "quantity": 1,
                "total": 10,
            }],
        )
        for index in range(25)
    ]
    products = [
        {
            "id": f"product-{index}",
            "salla_product_id": f"product-{index}",
            "cost_price_from_salla": 4,
            "variants": [],
        }
        for index in range(25)
    ]
    db = DB(orders=orders, products=products)

    result = await load_salla_campaign_outcomes(
        db,
        "owner-1",
        account_id="account-1",
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 1),
        timezone_name="Asia/Riyadh",
        identities=[{
            "account_id": "account-1",
            "campaign_id": "campaign-1",
            "campaign_name": "Campaign 1",
        }],
        restrict_to_identities=True,
    )

    query = db[PRODUCTS].find_queries[0]
    requested_ids = {
        value
        for clause in query["$or"]
        for condition in clause.values()
        for value in condition.get("$in", [])
    }
    assert requested_ids == {f"product-{index}" for index in range(25)}
    assert query != {"user_id": "owner-1"}
    assert result["cost_read_diagnostics"]["products_materialized"] == 25
