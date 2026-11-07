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
async def test_current_configured_bundle_cost_applies_to_all_unpriced_sold_units(db):
    # Owner rule: every sold unit uses configured current selected costs when
    # no immutable order-line cost exists; never blindly use Salla parent 70.
    order = sale("no-history", sale_line("line", 4))
    readonly = ReadOnlyDB(db)
    result = await build_mezan_v2_product_cost(readonly, OWNER, [order])
    cooperative = await build_mezan_v2_product_cost_cooperative(ReadOnlyDB(db), OWNER, [order])
    sold = await load_sold_products(ReadOnlyDB(db), OWNER, [order])
    assert result == cooperative
    assert result["incomplete_orders_count"] == 0
    assert result["product_rows"][0]["cost_status"] == "complete"
    assert result["product_rows"][0]["total_cost"] == 220
    assert result["total"] == result["product_profit_summary"]["total_cost"] == 220
    assert result["product_rows"][0]["units_sold"] == 4
    assert result["product_rows"][0]["average_unit_cost"] == 55
    assert sold[0]["total_cost"] == 220
    # New composite estimate adds bounded catalog reads; never N+1 reads
    # per order. Legacy frozen-order read limits remain unchanged.
    assert sum(readonly.reads.values()) <= 20
    # No snapshot/revision/journal is written by a profitability report.
    assert await db["mezan_order_cost_corrections_v1"].count_documents({}) == 0
    assert await db["mezan_order_item_cost_snapshots_v2"].count_documents({}) == 0


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
@pytest.mark.parametrize("unresolved", [False, True, "known_salla_fallback"])
async def test_executive_http_replaces_old_cost_and_decreases_profit_by_140_once(db, monkeypatch, unresolved):
    """Exercise the real HTTP composition with only unrelated provider facts stubbed."""
    from auth import DEFAULT_PAYMENT_METHODS, PAYMENT_FEE_DEFAULTS_VERSION
    import dashboard_v2_routes as dashboard
    await db.settings.insert_one({"user_id": OWNER,
        "payment_methods": deepcopy(DEFAULT_PAYMENT_METHODS),
        "payment_fee_defaults_version": PAYMENT_FEE_DEFAULTS_VERSION})
    order = sale("executive-http", sale_line("line", 4))
    if unresolved == "known_salla_fallback":
        order = sale("executive-http", sale_line("line", 4, product="plain"))
        await db[COST_PROFILES].delete_one({"user_id": OWNER, "salla_product_id": "plain"})
        await db[PRODUCTS].update_one({"user_id": OWNER, "salla_product_id": "plain"}, {"$set": {"cost_price_from_salla": 9}})
        await db.unified_orders.insert_one(deepcopy(order))
    elif unresolved:
        order["products"][0]["options"] = []
        await db.unified_orders.insert_one(deepcopy(order))
    else:
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
            if unresolved == "known_salla_fallback":
                assert result["totals"]["product_cost_complete"] is True
                assert result["totals"]["total_product_cost"] == 36
                assert result["totals"]["net_profit"] == 344
                continue
            if unresolved:
                assert result["totals"]["product_cost_complete"] is False
                assert result["totals"]["net_profit"] is None
                assert result["totals"]["net_sales"] is None
                assert result["product_cost_v2"]["product_rows"][0]["cost_status"] == "missing"
                assert result["product_cost_v2"]["product_rows"][0]["net_profit"] is None
                continue
            assert result["totals"]["total_product_cost"] == 220
            assert result["product_cost_v2"]["product_rows"][0]["total_cost"] == 220
            assert result["totals"]["net_profit"] == 160
            assert result["totals"]["net_profit"] - baseline_response["totals"]["net_profit"] == -140
            assert result["accounting_write_reached"] is False
            assert result["qoyod_write_reached"] is False


@pytest.mark.asyncio
async def test_current_cost_rose_and_external_name_service_four_units_without_operational_writes(db):
    """Independent supplier embroidery is an expense estimate, not receipt."""
    root = await db[PRODUCTS].find_one({"user_id": OWNER, "salla_product_id": "shawl"})
    await db[PRODUCTS].update_one(
        {"user_id": OWNER, "salla_product_id": "shawl"}, {"$set": {
            "options": [*root["options"], {
                "id": "name-choice", "name": "هل تريد اضافة الاسم ؟",
                "required": False, "values": [
                    {"id": "yes", "name": "نعم"}, {"id": "no", "name": "لا"},
                ],
            }],
        }})
    await db[RESOURCES].insert_one({"user_id": OWNER, "id": "external-name",
        "unit_cost": 10, "track_inventory": False, "kind": "service"})
    await db[BINDINGS].insert_one({"user_id": OWNER, "id": "name-supplier",
        "salla_product_id": "shawl", "option_id": "name-choice",
        "value_id": "yes", "mode": "resource", "resource_id": "external-name",
        "quantity": 1})
    line = sale_line("line", 4)
    line["options"] = [*line["options"], {
        "option_id": "name-choice", "value_id": "yes",
        "name": "هل تريد اضافة الاسم ؟", "value": "نعم",
    }]
    order = sale("unpriced-service", line)
    first = await build_mezan_v2_product_cost(ReadOnlyDB(db), OWNER, [order])
    sold = await load_sold_products(ReadOnlyDB(db), OWNER, [order])
    assert first["total"] == first["product_profit_summary"]["total_cost"] == 260
    assert first["product_rows"][0]["average_unit_cost"] == 65
    assert sold[0]["total_cost"] == 260
    await db[COST_PROFILES].update_one({"user_id": OWNER, "salla_product_id": "shawl"},
                                      {"$set": {"base_cost": 16}})
    await db[COST_PROFILES].update_one({"user_id": OWNER, "salla_product_id": "rose"},
                                      {"$set": {"base_cost": 45}})
    await db[RESOURCES].update_one({"user_id": OWNER, "id": "external-name"},
                                  {"$set": {"unit_cost": 11}})
    latest = await build_mezan_v2_product_cost_cooperative(ReadOnlyDB(db), OWNER, [order])
    # Owner policy uses *today's* costs for all four sold units: 16+45+11=72.
    assert latest["total"] == 288
    assert latest["product_rows"][0]["average_unit_cost"] == 72
    assert latest["product_profit_summary"]["total_cost"] == 288
    for coll in ("purchase_invoices", "mezan_component_consumption_units_v1",
                 "general_ledger", "supplier_invoices", "mezan_order_cost_corrections_v1"):
        assert await db[coll].count_documents({}) == 0


@pytest.mark.asyncio
async def test_unpriced_mixed_current_choices_not_all_charged_for_rose(db):
    yes = sale("yes", sale_line("yes-line", 2, rose="yes"))
    no = sale("no", sale_line("no-line", 3, rose="no"))
    result = await build_mezan_v2_product_cost(ReadOnlyDB(db), OWNER, [yes, no])
    coop = await build_mezan_v2_product_cost_cooperative(ReadOnlyDB(db), OWNER, [yes, no])
    sold = await load_sold_products(ReadOnlyDB(db), OWNER, [yes, no])
    assert coop == result
    assert result["total"] == (2 * 55) + (3 * 15) == 155
    assert result["product_rows"][0]["units_sold"] == 5
    assert result["product_rows"][0]["total_cost"] == 155
    assert result["product_profit_summary"]["total_cost"] == 155
    assert sold[0]["total_cost"] == 155


@pytest.mark.asyncio
async def test_unpriced_salla_order_row_id_uses_catalog_option_id_only_for_financial_estimate(db):
    order = sale("old-option-ids", sale_line("line", 1))
    order["products"][0]["options"] = [{
        "id": "928241773", "option": {"id": "rose", "name": "هل تريد تغليف ورد؟"},
        "values": [{"id": "yes", "name": "نعم"}],
    }]
    cost = await build_mezan_v2_product_cost(ReadOnlyDB(db), OWNER, [order])
    assert cost["total"] == 55
    assert cost["product_rows"][0]["cost_sources"] == ["current_bundle_catalog_estimate"]
    # Supplier/stock original token identities are not changed.
    assert order["products"][0]["options"][0]["id"] == "928241773"


@pytest.mark.asyncio
async def test_unpriced_ambiguous_or_unknown_choice_keeps_missing_not_zero(db):
    line = sale_line("unrecognized", 1)
    line["options"] = [{"name": "غير معروف", "value": "اختيار غير معروف"}]
    order = sale("ambiguous", line)
    result = await build_mezan_v2_product_cost_cooperative(ReadOnlyDB(db), OWNER, [order])
    sold = await load_sold_products(ReadOnlyDB(db), OWNER, [order])
    assert result["incomplete_orders_count"] == 1
    assert result["product_rows"][0]["total_cost"] is None
    assert result["product_rows"][0]["cost_status"] == "missing"
    assert sold[0]["total_cost"] is None


@pytest.mark.asyncio
async def test_frozen_bundle_still_wins_over_new_current_cost_policy(db):
    order = sale("frozen-priority", sale_line("line", 4))
    await freeze(db, order)
    await db[COST_PROFILES].update_many({"user_id": OWNER},
                                        {"$set": {"base_cost": 999}})
    report = await build_mezan_v2_product_cost(ReadOnlyDB(db), OWNER, [order])
    assert report["total"] == 220
    assert report["product_rows"][0]["total_cost"] == 220
    assert report["product_rows"][0]["cost_sources"] == ["bundle_snapshot"]


@pytest.mark.asyncio
async def test_current_bundle_without_original_choice_rows_preserves_known_cost_but_stays_incomplete(db):
    order = sale("no-selection-source", sale_line("line", 4))
    order["products"][0]["options"] = []
    result = await build_mezan_v2_product_cost(ReadOnlyDB(db), OWNER, [order])
    assert result["product_rows"][0]["cost_status"] == "missing"
    assert result["product_rows"][0]["total_cost"] == 60  # known base, never a complete cost
    assert result["incomplete_orders_count"] == 1
    sold = await load_sold_products(ReadOnlyDB(db), OWNER, [order])
    assert sold[0]["cost_status"] == "incomplete"
    assert sold[0]["total_cost"] is None
    assert sold[0]["known_cost_total"] == 60
    assert any(cell["status"] == "unresolved" for cell in sold[0]["cost_groups"][0]["cells"])


@pytest.mark.asyncio
async def test_current_bundle_conflicting_yes_and_no_keeps_cost_missing(db):
    order = sale("conflicting-source", sale_line("line", 1))
    order["products"][0]["options"].append({
        "option_id": "rose", "value_id": "no",
        "name": "هل تريد تغليف ورد؟", "value": "لا",
    })
    result = await build_mezan_v2_product_cost_cooperative(ReadOnlyDB(db), OWNER, [order])
    assert result["product_rows"][0]["cost_status"] == "missing"
    assert result["product_rows"][0]["total_cost"] is None


@pytest.mark.asyncio
async def test_unpriced_bundle_duplicate_sale_line_identity_cannot_reprice(db):
    # A duplicate canonical line ID must not be collapsed into one last-wins
    # estimate, nor cause the component cost to be counted twice.
    order = sale("dup-line", sale_line("line", 1), sale_line("line", 1, rose="no"))
    result = await build_mezan_v2_product_cost(ReadOnlyDB(db), OWNER, [order])
    assert result["incomplete_orders_count"] == 1
    assert result["product_rows"][0]["total_cost"] is None
    assert result["product_rows"][0]["cost_status"] == "missing"


@pytest.mark.asyncio
async def test_unpriced_bundle_stale_current_recipe_option_condition_fails_closed(db):
    definition = await db["mezan_bundle_definitions_v2"].find_one({
        "user_id": OWNER, "product_id": "shawl",
    })
    amended = deepcopy(definition["items"])
    for row in amended:
        if row.get("condition"):
            row["condition"]["value_id"] = "old-value-no-longer-in-catalog"
    await db["mezan_bundle_definitions_v2"].update_one({
        "user_id": OWNER, "product_id": "shawl",
    }, {"$set": {"items": amended}})
    order = sale("stale-condition", sale_line("line", 3))
    result = await build_mezan_v2_product_cost_cooperative(ReadOnlyDB(db), OWNER, [order])
    assert result["product_rows"][0]["total_cost"] is None
    assert result["product_rows"][0]["cost_status"] == "missing"


@pytest.mark.asyncio
async def test_salla_distinct_order_row_ids_with_rose_name_and_private_text_cost_65(db):
    """Order row id differs from catalog option id; private text has no price."""
    await db[PRODUCTS].update_one({"user_id": OWNER, "salla_product_id": "shawl"},
        {"$push": {"options": {"$each": [
            {"id": "name-choice", "name": "هل تريد اضافة اسم على الشال ؟",
             "type": "radio", "values": [{"id": "yes", "name": "نعم"}, {"id": "no", "name": "لا"}]},
            {"id": "embroidery-text", "name": "التطريز على الشال", "type": "text", "values": []},
            {"id": "gift-message", "name": "كرت اهداء", "type": "text", "values": []},
        ]}}})
    await db[RESOURCES].insert_one({"user_id": OWNER, "id": "name-external",
        "kind": "service", "unit_cost": 10, "track_inventory": False})
    await db[BINDINGS].insert_one({"user_id": OWNER, "id": "name-external-binding",
        "salla_product_id": "shawl", "option_id": "name-choice",
        "value_id": "yes", "mode": "resource", "resource_id": "name-external", "quantity": 1})
    line = sale_line("salla-row", 1)
    line["options"] = [
        {"id": "928241773", "option": {"id": "rose", "name": "هل تريد تغليف ورد؟",
         "type": "radio"}, "values": [{"id": "yes", "name": "نعم"}]},
        {"id": "1952424308", "option": {"id": "name-choice", "name": "هل تريد اضافة اسم على الشال ؟",
         "type": "radio"}, "values": [{"id": "yes", "name": "نعم"}]},
        {"id": "1176813685", "option": {"id": "embroidery-text", "name": "التطريز على الشال",
         "type": "text"}, "value": {"text": "PRIVATE_NAME"}},
        {"id": "268562294", "option": {"id": "gift-message", "name": "كرت اهداء",
         "type": "text"}, "value": {"text": "PRIVATE_GIFT"}},
    ]
    order = sale("salla-real-row-ids", line)
    executive = await build_mezan_v2_product_cost_cooperative(ReadOnlyDB(db), OWNER, [order])
    products = await load_sold_products(ReadOnlyDB(db), OWNER, [order])
    assert executive["total"] == executive["product_profit_summary"]["total_cost"] == 65
    assert executive["product_rows"][0]["average_unit_cost"] == 65
    assert executive["product_rows"][0]["cost_sources"] == ["current_bundle_catalog_estimate"]
    assert products[0]["total_cost"] == 65
    assert await db["general_ledger"].count_documents({}) == 0
    assert await db["mezan_component_consumption_units_v1"].count_documents({}) == 0


@pytest.mark.asyncio
async def test_unpriced_current_bundle_reads_are_constant_across_order_count(db):
    one = [sale("unpriced-001", sale_line("line-001", 1))]
    many = [
        sale(f"unpriced-{i:03}", sale_line(f"line-{i:03}", 1))
        for i in range(1, 31)
    ]
    first_db, many_db = ReadOnlyDB(db), ReadOnlyDB(db)
    one_cost = await build_mezan_v2_product_cost(first_db, OWNER, one)
    many_cost = await build_mezan_v2_product_cost(many_db, OWNER, many)
    assert one_cost["total"] == 55
    assert many_cost["total"] == 1650
    assert first_db.reads == many_db.reads
    assert sum(many_db.reads.values()) <= 20


@pytest.mark.asyncio
async def test_current_estimate_is_opt_in_to_profitability_not_marketing_or_historical_authority(db):
    from dashboard_cpu_budget import CPUWorkBudget
    from order_cost_authority import (
        _unknown, load_order_cost_authority, resolve_order_line_cost,
    )

    order = sale("scope-isolation", sale_line("scope-line", 1))
    line = order["products"][0]

    async def read(budget=None, **kwargs):
        a = await load_order_cost_authority(
            ReadOnlyDB(db), OWNER, [order], budget=budget, **kwargs)
        return resolve_order_line_cost(
            line, order_number=order["order_number"],
            authority=a, catalog_cost=_unknown(1, "missing"),
            product_id="shawl",
        )

    historical = await read()
    assert historical["calculation_cost_available"] is False
    assert "bundle_snapshot_missing" in historical["missing_cost_reasons"]
    dashboard = await read(CPUWorkBudget(5))
    assert dashboard["calculation_cost_available"] is True
    assert dashboard["line_total"] == 55
    assert dashboard["historical_cost_verified"] is False
    disabled = await read(CPUWorkBudget(5), current_profitability_estimate=False)
    assert disabled["calculation_cost_available"] is False
    assert disabled["cost_authority"] == "unresolved_snapshot"


@pytest.mark.asyncio
async def test_option_id_costs_agree_across_dashboard_executive_and_sold_products(db):
    from tests.test_report_option_identity import PRODUCT, BINDING
    await db[PRODUCTS].update_one({"salla_product_id": "plain"}, {"$set": deepcopy(PRODUCT)})
    await db[COST_PROFILES].update_one({"salla_product_id": "plain"}, {"$set": {"base_cost": 15}})
    await db[RESOURCES].insert_many([
        {"user_id": OWNER, "id": "bag", "unit_cost": 1.2},
        {"user_id": OWNER, "id": "emb", "unit_cost": 8},
    ])
    await db[BINDINGS].insert_one({**BINDING, "user_id": OWNER, "salla_product_id": "plain"})
    await db[PRODUCT_RESOURCE_BINDINGS].insert_one({"id": "bag", "resource_id": "bag", "user_id": OWNER, "salla_product_id": "plain"})
    orders = []
    for index in range(21):
        count = 4 if index == 19 else 5 if index == 20 else 1
        lines = []
        for position in range(count):
            option = {"name": "Name embroidery" if 15 <= index < 19 else "تطريز الاسم",
                      "values": [{"name": "Yes" if 15 <= index < 19 else "نعم"}]}
            if count > 1:
                option["id"] = f"checkout-row-{index}-{position}"
            lines.append({**sale_line(f"line-{index}-{position}", product="plain"), "options": [option]})
        orders.append(sale(f"order-{index}", *lines))
    result = await build_mezan_v2_product_cost(ReadOnlyDB(db), OWNER, orders)
    cooperative = await build_mezan_v2_product_cost_cooperative(ReadOnlyDB(db), OWNER, orders)
    sold = await load_sold_products(ReadOnlyDB(db), OWNER, orders)
    assert result == cooperative
    assert result["total"] == result["product_profit_summary"]["total_cost"] == 677.6
    assert result["incomplete_orders_count"] == 0
    assert result["product_rows"][0]["units_sold"] == 28
    assert sold[0]["total_cost"] == 677.6
