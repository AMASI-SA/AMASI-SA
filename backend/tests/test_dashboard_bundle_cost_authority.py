"""Public reporting acceptance for immutable bundle/order costs (synthetic data)."""
from collections import Counter
from copy import deepcopy

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient

import bundle_catalog_v2 as catalog
from bundle_fulfillment_v2 import capture_intake
from dashboard_v2_routes import (
    build_mezan_v2_product_cost,
    build_mezan_v2_product_cost_cooperative,
    make_dashboard_v2_router,
)
from order_cost_authority import capture_reporting_costs
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import BINDINGS, RESOURCES
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS
from sold_products_report_v2 import load_sold_products, summarize

OWNER = "bundle-report-test-owner"
LIFECYCLES = "mezan_component_order_lifecycle_v1"


def sale_line(key, quantity=1, rose="yes", product="shawl"):
    line = {
        "order_item_id": key, "product_id": product,
        "quantity": quantity, "total": 100 * quantity,
        "sku": "SYNTHETIC-SHAWL" if product == "shawl" else "SYNTHETIC-PLAIN",
    }
    if product == "shawl":
        line["options"] = [{"option_id": "rose", "value_id": rose,
                            "name": "هل تريد تغليف ورد؟", "value": "نعم" if rose == "yes" else "لا"}]
    return line


def sale(number, *lines, **fields):
    return {
        "user_id": OWNER, "order_number": number, "order_date": "2026-11-02",
        "currency": "SAR", "order_status": "تم التنفيذ",
        "total_amount": sum(line["total"] for line in lines),
        "products": list(lines), **fields,
    }


async def freeze(db, order):
    lines = [{**line, "order_line_id": line["order_item_id"]} for line in order["products"]]
    bundles = await capture_intake(db, OWNER, lines, {})
    ordinary = await capture_reporting_costs(db, OWNER, lines, {}, bundles)
    await db[LIFECYCLES].insert_one({
        "user_id": OWNER, "order_number": order["order_number"],
        "bundle_intake_snapshots": bundles, "reporting_cost_snapshots": ordinary,
    })
    await db.unified_orders.insert_one(deepcopy(order))


@pytest_asyncio.fixture
async def db(monkeypatch):
    db = AsyncMongoMockClient().dashboard_bundle_authority

    async def fixture_transaction(db, owner, callback, **kwargs):
        return await callback(db)

    monkeypatch.setattr(catalog, "operational_owner", fixture_transaction)
    await db[PRODUCTS].insert_many([
        {"user_id": OWNER, "salla_product_id": "shawl", "mezan_product_id": "mpv2_shawl",
         "sku": "SYNTHETIC-SHAWL", "name": "Synthetic shawl", "cost_price_from_salla": 70,
         "options": [{"id": "rose", "name": "هل تريد تغليف ورد؟",
                      "values": [{"id": "yes", "name": "نعم"}, {"id": "no", "name": "لا"}]}]},
        {"user_id": OWNER, "salla_product_id": "rose", "name": "Synthetic bouquet"},
        {"user_id": OWNER, "salla_product_id": "plain", "sku": "SYNTHETIC-PLAIN", "name": "Synthetic ordinary product"},
    ])
    await db[COST_PROFILES].insert_many([
        {"user_id": OWNER, "salla_product_id": "shawl", "base_cost": 15},
        {"user_id": OWNER, "salla_product_id": "rose", "base_cost": 40},
        {"user_id": OWNER, "salla_product_id": "plain", "base_cost": 9},
    ])
    await catalog.save_bundle(db, OWNER, "shawl", {
        "product_type": "composite", "expected_revision": 0,
        "items": [
            {"kind": "current_product", "quantity": "1"},
            {"kind": "product", "product_id": "rose", "quantity": "1",
             "condition": {"option_id": "rose", "value_id": "yes"}},
        ],
    }, OWNER)
    return db


class ReadOnlyCollection:
    def __init__(self, collection, reads):
        self.collection, self.reads = collection, reads

    def find(self, *args, **kwargs):
        self.reads[(self.collection.name, "find")] += 1
        return self.collection.find(*args, **kwargs)

    def aggregate(self, *args, **kwargs):
        self.reads[(self.collection.name, "aggregate")] += 1
        return self.collection.aggregate(*args, **kwargs)

    async def find_one(self, *args, **kwargs):
        self.reads[(self.collection.name, "find_one")] += 1
        return await self.collection.find_one(*args, **kwargs)

    def __getattr__(self, name):
        raise AssertionError("report attempted non-read operation: " + name)


class ReadOnlyDB:
    def __init__(self, db):
        self.db, self.reads = db, Counter()

    def __getitem__(self, name):
        return ReadOnlyCollection(self.db[name], self.reads)

    def __getattr__(self, name):
        return self[name]


@pytest.mark.asyncio
async def test_four_shawls_with_roses_are_220_in_both_builders_and_sold_report(db):
    order = sale("four-with-roses", sale_line("line", 4))
    await freeze(db, order)
    readonly = ReadOnlyDB(db)
    standard = await build_mezan_v2_product_cost(readonly, OWNER, [order])
    cooperative = await build_mezan_v2_product_cost_cooperative(readonly, OWNER, [order])
    sold = await load_sold_products(readonly, OWNER, [order])
    assert standard == cooperative
    assert standard["total"] == 220
    assert standard["product_profit_summary"]["total_cost"] == 220
    assert standard["product_rows"][0]["total_cost"] == 220
    assert standard["product_rows"][0]["average_unit_cost"] == 55
    assert standard["product_rows"][0]["units_sold"] == 4
    assert standard["product_rows"][0]["net_profit"] == 180
    assert sold[0]["total_cost"] == summarize(sold)["total_cost"] == 220
    assert sold[0]["unit_cost"] == 55
    assert sold[0]["cost_source"] == "bundle_expected"
    assert len(sold) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("rose, expected", [("yes", 220), ("no", 60)])
async def test_customer_option_selects_recipe_and_later_catalog_edits_do_not_reprice(db, rose, expected):
    order = sale("frozen-" + rose, sale_line("line", 4, rose=rose))
    await freeze(db, order)
    await db[COST_PROFILES].update_many({"user_id": OWNER}, {"$set": {"base_cost": 999}})
    await db[PRODUCTS].update_many({"user_id": OWNER}, {"$set": {"cost_price_from_salla": 777}})
    for builder in (build_mezan_v2_product_cost, build_mezan_v2_product_cost_cooperative):
        first = await builder(ReadOnlyDB(db), OWNER, [order])
        replay = await builder(ReadOnlyDB(db), OWNER, [order])
        assert first == replay
        assert first["total"] == expected
    assert (await load_sold_products(ReadOnlyDB(db), OWNER, [order]))[0]["total_cost"] == expected


@pytest.mark.asyncio
async def test_mixed_options_and_ordinary_quantities_preserve_one_sale_line_per_product(db):
    order = sale("mixed", sale_line("yes", 2), sale_line("no", 2, rose="no"),
                 sale_line("ordinary", 3, product="plain"))
    await freeze(db, order)
    result = await build_mezan_v2_product_cost_cooperative(ReadOnlyDB(db), OWNER, [order])
    sold = await load_sold_products(ReadOnlyDB(db), OWNER, [order])
    assert result["total"] == 167  # 2*55 + 2*15 + 3*9
    assert result["product_profit_summary"]["total_cost"] == 167
    assert sum(row["total_cost"] for row in sold) == 167
    by_product = {row["product_id"]: row for row in sold}
    assert set(by_product) == {"shawl", "plain"}
    assert by_product["shawl"]["units_sold"] == 4
    assert by_product["shawl"]["unit_cost"] == 35
    assert by_product["plain"]["total_cost"] == 27


@pytest.mark.asyncio
@pytest.mark.parametrize("currency,rate", [("SAR", "1"), ("QAR", "1.03"), ("AED", "1.02"), ("KWD", "12.2")])
async def test_order_currency_does_not_convert_sar_component_cost_again(db, currency, rate):
    order = sale("fx-" + currency, sale_line("line", 4), currency=currency,
                 accounting_currency="SAR", currency_conversion_status="verified",
                 exchange_rate_to_sar=rate)
    await freeze(db, order)
    result = await build_mezan_v2_product_cost(ReadOnlyDB(db), OWNER, [order])
    assert result["total"] == 220
    assert result["product_rows"][0]["total_sales"] == round(400 * float(rate), 2)
    assert (await load_sold_products(ReadOnlyDB(db), OWNER, [order]))[0]["total_cost"] == 220


@pytest.mark.asyncio
async def test_refund_policy_is_applied_once_after_frozen_full_cost(db):
    order = sale("partial-return", sale_line("line", 4), actual_partial_refund_amount=100)
    await freeze(db, order)
    result = await build_mezan_v2_product_cost(ReadOnlyDB(db), OWNER, [order])
    assert result["total"] == 165  # 220 * (1 - 100/400), exactly once
    assert result["product_rows"][0]["total_cost"] == 165
    assert result["product_profit_summary"]["total_cost"] == 165


@pytest.mark.asyncio
async def test_catalog_components_and_options_are_shared_for_ordinary_products(db):
    await db[RESOURCES].insert_one({"user_id": OWNER, "id": "pack", "unit_cost": 3})
    await db[PRODUCT_RESOURCE_BINDINGS].insert_one({"user_id": OWNER,
        "salla_product_id": "plain", "id": "pack-binding", "resource_id": "pack", "quantity": 2})
    await db[BINDINGS].insert_one({"user_id": OWNER, "salla_product_id": "plain",
        "id": "personalized", "option_name": "إضافة الاسم", "value_name": "نعم",
        "mode": "direct", "direct_amount": 5})
    line = {**sale_line("plain-line", 2, product="plain"),
            "options": [{"name": "إضافة الاسم", "value": "نعم"}]}
    order = sale("ordinary-options", line)
    await freeze(db, order)
    await db[RESOURCES].update_one({"id": "pack"}, {"$set": {"unit_cost": 100}})
    await db[BINDINGS].update_one({"id": "personalized"}, {"$set": {"direct_amount": 100}})
    result = await build_mezan_v2_product_cost(ReadOnlyDB(db), OWNER, [order])
    sold = await load_sold_products(ReadOnlyDB(db), OWNER, [order])
    assert result["total"] == 40  # (9 + 2*3 + 5) * 2
    assert sold[0]["total_cost"] == 40
    assert sold[0]["unit_cost"] == 20


@pytest.mark.asyncio
async def test_unresolved_bundle_cost_is_not_replaced_with_current_parent_70(db):
    order = sale("no-history", sale_line("line", 4))
    result = await build_mezan_v2_product_cost(ReadOnlyDB(db), OWNER, [order])
    sold = await load_sold_products(ReadOnlyDB(db), OWNER, [order])
    assert result["incomplete_orders_count"] == 1
    assert result["product_rows"][0]["cost_status"] == "missing"
    assert result["product_rows"][0]["total_cost"] is None
    assert result["product_rows"][0]["net_profit"] is None
    assert sold[0]["total_cost"] is None


@pytest.mark.asyncio
async def test_sold_http_route_preserves_order_identity_for_snapshot_lookup(db):
    from auth import DEFAULT_PAYMENT_METHODS, PAYMENT_FEE_DEFAULTS_VERSION
    await db.settings.insert_one({"user_id": OWNER,
        "payment_methods": deepcopy(DEFAULT_PAYMENT_METHODS),
        "payment_fee_defaults_version": PAYMENT_FEE_DEFAULTS_VERSION})
    order = sale("http-identity", sale_line("line", 4))
    await freeze(db, order)

    async def user():
        return {"id": OWNER, "role": "owner"}

    async def unused_legacy(**kwargs):
        raise AssertionError("sold-products route must not call legacy dashboard")

    app = FastAPI()
    app.include_router(make_dashboard_v2_router(ReadOnlyDB(db), user, unused_legacy, lambda current: None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard-v2/sold-products", params={
            "from_date": "2026-11-02", "to_date": "2026-11-02"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["totals"]["total_cost"] == 220
    assert payload["items"][0]["total_cost"] == 220
    assert payload["incomplete_count"] == 0


@pytest.mark.asyncio
async def test_reporting_reads_are_batched_instead_of_growing_per_bundle(db):
    orders = [sale(f"batch-{i:03}", sale_line("line", 4)) for i in range(80)]
    # Freeze the true selected composition once, then replicate identical
    # synthetic sale evidence with distinct order identities for load testing.
    await freeze(db, orders[0])
    frozen = await db[LIFECYCLES].find_one({"order_number": orders[0]["order_number"]}, {"_id": 0})
    await db[LIFECYCLES].insert_many([{**deepcopy(frozen), "order_number": order["order_number"]} for order in orders[1:]])
    for builder in (build_mezan_v2_product_cost, build_mezan_v2_product_cost_cooperative, load_sold_products):
        single_db, many_db = ReadOnlyDB(db), ReadOnlyDB(db)
        single = await builder(single_db, OWNER, orders[:1])
        many = await builder(many_db, OWNER, orders)
        assert single_db.reads == many_db.reads
        assert sum(many_db.reads.values()) <= 15
        if isinstance(many, list):
            assert many[0]["total_cost"] == 17600
        else:
            assert single["total"] == 220
            assert many["total"] == 17600


@pytest.mark.asyncio
async def test_executive_http_replaces_old_cost_and_decreases_profit_by_140_once(db, monkeypatch):
    """Exercise the real HTTP composition with only unrelated provider facts stubbed."""
    from auth import DEFAULT_PAYMENT_METHODS, PAYMENT_FEE_DEFAULTS_VERSION
    import dashboard_v2_routes as dashboard
    await db.settings.insert_one({"user_id": OWNER,
        "payment_methods": deepcopy(DEFAULT_PAYMENT_METHODS),
        "payment_fee_defaults_version": PAYMENT_FEE_DEFAULTS_VERSION})
    order = sale("executive-http", sale_line("line", 4))
    await freeze(db, order)
    baseline_response = {"totals": {
        "total_orders": 1, "total_sales": 400, "total_product_cost": 80,
        "total_ads_cost": 0, "operating_expenses_total": 0,
        "operating_salaries_total": 0, "total_payment_fees": 20,
        "net_profit": 300, "net_sales": 300,
    }, "payment_breakdown": [], "net_sales_config": {}}

    async def legacy(**kwargs):
        return deepcopy(baseline_response)

    async def empty_ads(*args, **kwargs):
        return {
            "total": 0, "breakdown": {"snapchat": 0, "tiktok": 0, "meta": 0, "google_transitional": 0},
            "spend_quality": {"amount_available": True, "amount_complete": True},
            "bank_commissions": {"total_fee_sar": 0}, "source_contract": {},
            "providers": {key: {"spend": 0, "orders": 0, "revenue": 0, "roas": 0}
                          for key in ("snapchat", "tiktok", "meta", "google")},
        }

    async def user():
        return {"id": OWNER, "role": "owner"}

    monkeypatch.setattr(dashboard, "build_mezan_v2_ads", empty_ads)
    app = FastAPI()
    app.include_router(make_dashboard_v2_router(ReadOnlyDB(db), user, legacy, lambda current: None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(2):
            response = await client.get("/dashboard-v2", params={"from_date": "2026-11-02", "to_date": "2026-11-02"})
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["totals"]["total_product_cost"] == 220
            assert result["product_cost_v2"]["product_rows"][0]["total_cost"] == 220
            assert result["totals"]["net_profit"] == 160
            assert result["totals"]["net_profit"] - baseline_response["totals"]["net_profit"] == -140
            assert result["accounting_write_reached"] is False
            assert result["qoyod_write_reached"] is False
