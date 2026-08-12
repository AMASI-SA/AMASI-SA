from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from integrations_control_center import (
    snapchat_account_timezone_manager as manager,
)
from integrations_control_center import (
    snapchat_campaign_created_order_semantics as module,
)
from integrations_control_center import (
    snapchat_campaign_profitability as profitability,
)
from product_cost_revision import (
    PRODUCT_COST_REVISIONS,
    get_product_cost_revision,
)
from product_option_cost_routes import BINDINGS, make_product_option_cost_router
from product_v2_details_routes import COST_PROFILES, make_product_v2_details_router
from product_v2_routes import PRODUCTS


def _cost_result(order, _context):
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


@pytest.mark.asyncio
async def test_paginated_and_filtered_rows_keep_report_wide_totals(monkeypatch):
    module._PROFIT_CACHE.clear()

    async def cost_revision(_db, _user_id):
        return 0

    async def cost_context(_db, _user_id):
        return {"loaded": True}

    financial_matched = {
        ("account-1", "campaign-1"): [
            {"total_amount": 100.0, "product_cost": 40.0},
        ],
        ("account-1", "campaign-2"): [
            {"total_amount": 200.0, "product_cost": 60.0},
        ],
    }
    campaign_rows = {
        "campaign-1": {
            "account_id": "account-1",
            "campaign_id": "campaign-1",
            "campaign_name": "Campaign one",
            "spend_sar": 30.0,
            "salla_results": {
                "orders": 1,
                "created_orders": 1,
                "financial_orders": 1,
            },
        },
        "campaign-2": {
            "account_id": "account-1",
            "campaign_id": "campaign-2",
            "campaign_name": "Campaign two",
            "spend_sar": 70.0,
            "salla_results": {
                "orders": 1,
                "created_orders": 1,
                "financial_orders": 1,
            },
        },
    }

    async def base_report(*_args, **kwargs):
        module._FINANCIAL_MATCHED.set(deepcopy(financial_matched))
        campaign_id = (
            "campaign-2"
            if kwargs.get("campaign_query") == "two" or kwargs.get("page") == 2
            else "campaign-1"
        )
        return {
            "result_source": "salla",
            "selected_account_id": "account-1",
            "date_from": "2026-08-01",
            "date_to": "2026-08-12",
            "campaigns": [deepcopy(campaign_rows[campaign_id])],
            "totals": {
                "orders": 2,
                "spend_sar": 100.0,
                "sales_sar": 300.0,
            },
            "source": {
                "salla_attribution": {
                    "created_orders_matched": 2,
                    "financial_orders_matched": 2,
                    "cancelled_orders_matched": 0,
                    "excluded_orders_matched": 0,
                },
            },
        }

    async def base_outcomes(*_args, **_kwargs):
        return {}, {}, {}

    monkeypatch.setattr(module, "get_product_cost_revision", cost_revision)
    monkeypatch.setattr(profitability, "_load_cost_context", cost_context)
    monkeypatch.setattr(profitability, "_order_cost_and_products", _cost_result)
    monkeypatch.setattr(manager, "_salla_account_outcomes", base_outcomes)
    monkeypatch.setattr(manager, "build_account_timezone_campaign_report", base_report)
    module.install_fixed_created_order_semantics()
    report = manager.build_account_timezone_campaign_report

    common = {
        "result_source": "salla",
        "account_id": "account-1",
        "from_date": "2026-08-01",
        "to_date": "2026-08-12",
    }
    page_one = await report(object(), "owner-1", page=1, **common)
    page_two = await report(object(), "owner-1", page=2, **common)
    filtered = await report(
        object(),
        "owner-1",
        page=1,
        campaign_query="two",
        **common,
    )

    assert page_one["campaigns"][0]["campaign_id"] == "campaign-1"
    assert page_two["campaigns"][0]["campaign_id"] == "campaign-2"
    assert filtered["campaigns"][0]["campaign_id"] == "campaign-2"
    assert page_one["totals"]["orders"] == 2
    assert page_two["totals"]["orders"] == 2
    assert filtered["totals"]["orders"] == 2
    assert page_one["totals"]["cpa_sar"] == 50.0
    assert page_two["totals"]["cpa_sar"] == 50.0

    first_profit = page_one["campaigns"][0]["profitability"]
    second_profit = page_two["campaigns"][0]["profitability"]
    assert first_profit["ad_spend_sar"] == 30.0
    assert second_profit["ad_spend_sar"] == 70.0
    for payload in (page_one, page_two, filtered):
        totals = payload["totals"]["profitability"]
        assert totals["orders"] == 2
        assert totals["sales_sar"] == 300.0
        assert totals["product_cost_sar"] == 100.0
        assert totals["ad_spend_sar"] == 100.0
        assert totals["contribution_profit_sar"] == 100.0


def _matches(row, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(row, {**clause}) for clause in expected):
                return False
            continue
        elif isinstance(expected, dict) and "$ne" in expected:
            if row.get(key) == expected["$ne"]:
                return False
        elif row.get(key) != expected:
            return False
    return True


class _Collection:
    def __init__(self, rows):
        self.rows = rows

    async def create_index(self, *_args, **_kwargs):
        return "test-index"

    async def find_one(self, query, projection=None):
        row = next((row for row in self.rows if _matches(row, query)), None)
        if row is None:
            return None
        if not projection:
            return deepcopy(row)
        included = {key for key, value in projection.items() if value and key != "_id"}
        if not included:
            return {
                key: deepcopy(value)
                for key, value in row.items()
                if projection.get(key, 1)
            }
        return {key: deepcopy(value) for key, value in row.items() if key in included}

    async def update_one(self, query, update, upsert=False):
        row = next((row for row in self.rows if _matches(row, query)), None)
        inserted = row is None
        if inserted:
            if not upsert:
                return None
            row = {
                key: deepcopy(value)
                for key, value in query.items()
                if not key.startswith("$") and not isinstance(value, dict)
            }
            self.rows.append(row)
        if inserted:
            row.update(deepcopy(update.get("$setOnInsert") or {}))
        for key, amount in (update.get("$inc") or {}).items():
            row[key] = row.get(key, 0) + amount
        row.update(deepcopy(update.get("$set") or {}))
        return None

    async def delete_one(self, query):
        row = next((row for row in self.rows if _matches(row, query)), None)
        if row is not None:
            self.rows.remove(row)
        return SimpleNamespace(deleted_count=int(row is not None))


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def __getitem__(self, name):
        return _Collection(self.rows.setdefault(name, []))


@pytest.mark.asyncio
async def test_product_cost_save_invalidates_profitability_across_workers(monkeypatch):
    module._PROFIT_CACHE.clear()
    db = _DB({
        PRODUCTS: [{
            "user_id": "owner-1",
            "id": "mpv2_p-1",
            "mezan_product_id": "mpv2_p-1",
            "salla_product_id": "p-1",
            "variants": [],
        }],
        COST_PROFILES: [{
            "user_id": "owner-1",
            "salla_product_id": "p-1",
            "base_cost": 30.0,
            "variant_costs": {},
        }],
        PRODUCT_COST_REVISIONS: [],
    })
    context_loads = 0

    async def cost_context(source_db, user_id):
        nonlocal context_loads
        context_loads += 1
        profile = await source_db[COST_PROFILES].find_one({
            "user_id": user_id,
            "salla_product_id": "p-1",
        })
        return {"base_cost": profile["base_cost"]}

    def order_cost(order, context):
        return _cost_result(
            {**order, "product_cost": context["base_cost"]},
            context,
        )

    monkeypatch.setattr(profitability, "_load_cost_context", cost_context)
    monkeypatch.setattr(profitability, "_order_cost_and_products", order_cost)
    kwargs = {
        "account_id": "account-1",
        "date_from": "2026-08-01",
        "date_to": "2026-08-12",
        "financial_matched": {
            ("account-1", "campaign-1"): [{"total_amount": 100.0}],
        },
        "campaign_spend": {("account-1", "campaign-1"): 20.0},
        "total_spend_sar": 20.0,
    }

    before, _ = await module.calculate_financial_profitability(
        db,
        "owner-1",
        **kwargs,
    )
    assert before[("account-1", "campaign-1")]["product_cost_sar"] == 30.0
    assert context_loads == 1

    async def current_user():
        return {"id": "owner-1"}

    router = make_product_v2_details_router(db, current_user)
    save_route = next(
        route
        for route in router.routes
        if route.path == "/products-v2/{product_id}/costs"
        and "PUT" in route.methods
    )
    await save_route.endpoint(
        "mpv2_p-1",
        {"base_cost": 10.0, "variant_costs": {}, "notes": ""},
        {"id": "owner-1"},
    )
    assert await get_product_cost_revision(db, "owner-1") == 1

    after, _ = await module.calculate_financial_profitability(
        db,
        "owner-1",
        **kwargs,
    )
    assert after[("account-1", "campaign-1")]["product_cost_sar"] == 10.0
    assert after[("account-1", "campaign-1")]["contribution_profit_sar"] == 70.0
    assert context_loads == 2


@pytest.mark.asyncio
async def test_option_cost_save_and_delete_bump_shared_revision():
    db = _DB({
        PRODUCTS: [{
            "user_id": "owner-1",
            "id": "mpv2_p-1",
            "mezan_product_id": "mpv2_p-1",
            "salla_product_id": "p-1",
            "options": [{
                "id": "option-1",
                "name": "Size",
                "values": [{"id": "value-1", "name": "Large"}],
            }],
        }],
        BINDINGS: [],
        PRODUCT_COST_REVISIONS: [],
    })
    router = make_product_option_cost_router(db, lambda: {"id": "owner-1"})
    path = "/products-v2/{product_id}/option-costs/{option_id}/{value_id}"
    save_route = next(
        route for route in router.routes
        if route.path == path and "PUT" in route.methods
    )
    delete_route = next(
        route for route in router.routes
        if route.path == path and "DELETE" in route.methods
    )

    await save_route.endpoint(
        "mpv2_p-1",
        "option-1",
        "value-1",
        {"mode": "direct", "direct_amount": 7.5, "quantity": 1},
        {"id": "owner-1"},
    )
    assert await get_product_cost_revision(db, "owner-1") == 1

    await delete_route.endpoint(
        "mpv2_p-1",
        "option-1",
        "value-1",
        {"id": "owner-1"},
    )
    assert await get_product_cost_revision(db, "owner-1") == 2

    # A no-op delete does not evict otherwise valid profitability entries.
    await delete_route.endpoint(
        "mpv2_p-1",
        "option-1",
        "value-1",
        {"id": "owner-1"},
    )
    assert await get_product_cost_revision(db, "owner-1") == 2
