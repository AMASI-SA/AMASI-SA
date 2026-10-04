"""Real isolated Mongo coverage for request-local dashboard cohort reuse."""
import asyncio
import os
import uuid

import pytest
import pytest_asyncio
import dashboard_order_reads as order_reads
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener

from dashboard_order_reads import (
    dashboard_order_read_scope, load_dashboard_orders, shared_dashboard_order_reads,
)
from order_currency import SALLA_RAW_CURRENCY_PROJECTION, hydrate_order_currency_fields
from salla_marketing_attribution import (
    SALLA_RAW_ATTRIBUTION_PROJECTION, attach_projected_salla_attribution,
)


class Reads(CommandListener):
    def __init__(self):
        self.finds = []
        self.batch_sizes = []

    def started(self, event):
        if event.command_name == "find" and event.command.get("find") == "unified_orders":
            self.finds.append(dict(event.command))

    def succeeded(self, event):
        cursor = event.reply.get("cursor", {})
        if "unified_orders" in cursor.get("ns", ""):
            self.batch_sizes.append(len(cursor.get("firstBatch", cursor.get("nextBatch", []))))

    def failed(self, event):
        pass


@pytest_asyncio.fixture
async def mongo():
    uri = os.environ["DASHBOARD_TEST_MONGO_URI"]
    assert uri.startswith("mongodb://127.0.0.1:")
    reads = Reads()
    client = AsyncIOMotorClient(uri, event_listeners=[reads], serverSelectionTimeoutMS=3000)
    db = client["dashboard_order_reads_test_" + uuid.uuid4().hex]
    await client.admin.command("ping")
    try:
        yield db, reads
    finally:
        await client.drop_database(db.name)
        client.close()


def order(number, **extra):
    return {
        "user_id": "owner", "order_number": str(number), "order_date": "2026-10-03",
        "order_status": "cancelled" if number % 2 else "completed",
        "payment_method": "mada", "shipping_company": "carrier", "currency": "USD",
        "total_amount": 10, "products": [{"name": "retained", "quantity": 1}],
        "raw_by_source": {"salla_direct": {
            "currency": "USD", "exchange_rate": 3.75, "total_amount": 10,
            "utm_source": "snapchat", "campaign_id": "campaign",
            "irrelevant_large_payload": "x" * 8192,
        }}, **extra,
    }


def query():
    return {"user_id": "owner", "order_date": {"$gte": "2026-10-01", "$lte": "2026-10-31"},
            "order_date_inferred": {"$ne": True}}


@pytest.mark.asyncio
async def test_hydration_matches_original_unfiltered_cohort_with_two_queries(mongo):
    db, reads = mongo
    await db.unified_orders.insert_many([order(i) for i in range(300)] + [
        order(301, user_id="other"), order(302, order_date_inferred=True),
        order(303, order_date="2026-09-01"),
    ])
    expected = await db.unified_orders.find(query(), {"_id": 0, "raw_by_source": 0}).to_list(100000)
    projection = dict(SALLA_RAW_CURRENCY_PROJECTION)
    projection.update(SALLA_RAW_ATTRIBUTION_PROJECTION)
    proofs = await db.unified_orders.find(query(), projection).to_list(100000)
    hydrate_order_currency_fields(expected, proofs)
    attach_projected_salla_attribution(expected, proofs)
    reads.finds.clear()
    reads.batch_sizes.clear()
    actual = await load_dashboard_orders(db, query())
    assert actual == expected
    assert len(actual) == 300
    assert {r["order_status"] for r in actual} == {"cancelled", "completed"}
    assert max(reads.batch_sizes) <= 128
    assert len(reads.finds) == 2  # one cohort + one narrow proof read
    assert all(f["filter"] == query() for f in reads.finds)
    assert await db.unified_orders.count_documents({}) == 303
    assert (await db.unified_orders.find_one({"order_number": "0"})).get("total_amount_sar") is None


@pytest.mark.asyncio
async def test_identical_parallel_reads_share_only_inside_scope(mongo):
    db, reads = mongo
    await db.unified_orders.insert_one(order(1))
    async with dashboard_order_read_scope():
        results = await asyncio.gather(*(load_dashboard_orders(db, query()) for _ in range(12)))
        assert all(r is results[0] for r in results)
        assert len(reads.finds) == 2
        reordered = dict(reversed(list(query().items())))
        assert await load_dashboard_orders(db, reordered) is results[0]
    assert await load_dashboard_orders(db, query()) == results[0]
    assert len(reads.finds) == 4
    async with dashboard_order_read_scope():
        assert await load_dashboard_orders(db, query()) is not results[0]
    assert len(reads.finds) == 6


@pytest.mark.asyncio
async def test_scope_owner_date_inferred_and_projection_isolation(mongo):
    db, reads = mongo
    await db.unified_orders.insert_many([
        order(1), order(2, user_id="other"), order(3, order_date="2026-09-01"),
        order(4, order_date_inferred=True),
    ])
    async with dashboard_order_read_scope():
        assert len(await load_dashboard_orders(db, query())) == 1
        assert len(await load_dashboard_orders(db, {"user_id": "owner"})) == 3
        assert (await load_dashboard_orders(db, {"user_id": "other"}))[0]["order_number"] == "2"
        without = await load_dashboard_orders(db, query(), include_marketing_attribution=False)
        assert "raw_by_source" not in without[0]
    assert len(reads.finds) == 8


@pytest.mark.asyncio
async def test_decorator_request_isolation_and_empty_cohort(mongo):
    db, reads = mongo

    @shared_dashboard_order_reads
    async def summary():
        return await asyncio.gather(load_dashboard_orders(db, query()), load_dashboard_orders(db, query()))

    results = await asyncio.gather(summary(), summary())
    assert results == [[[], []], [[], []]]
    assert len(reads.finds) == 2
    assert results[0][0] is results[0][1]
    assert results[0][0] is not results[1][0]


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_read_and_scope_closes_tasks(mongo, monkeypatch):
    db, reads = mongo
    await db.unified_orders.insert_one(order(1))
    original = order_reads._load
    entered = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def gated(*args):
        entered.set()
        try:
            await release.wait()
            return await original(*args)
        finally:
            finished.set()

    monkeypatch.setattr(order_reads, "_load", gated)
    async with dashboard_order_read_scope():
        cancelled = asyncio.create_task(load_dashboard_orders(db, query()))
        await entered.wait()
        survivor = asyncio.create_task(load_dashboard_orders(db, query()))
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        release.set()
        assert len(await survivor) == 1
        assert len(reads.finds) == 2
    entered.clear()
    release.clear()
    finished.clear()
    async with dashboard_order_read_scope():
        orphan = asyncio.create_task(load_dashboard_orders(db, query()))
        await entered.wait()
    assert finished.is_set()
    with pytest.raises(asyncio.CancelledError):
        await orphan


@pytest.mark.asyncio
async def test_dashboard_narrow_product_projection_preserves_cost_resolution(mongo):
    from dashboard_v2_routes import (PRODUCT_COST_CATALOG_PROJECTION, _index_products,
                                     _line_product, calculate_mezan_v2_line_cost)
    db, _ = mongo
    products = [
        {"id": "p1", "salla_product_id": "s1", "name": "Unique historical", "sku": "SKU1",
         "raw_salla_details": {"cost_price": 17.5, "description": "x" * 100000,
                               "skus": [{"id": "v1", "sku": "VSKU", "cost": 23}]}},
        {"id": "p2", "salla_product_id": "s2", "name": "Zero", "cost_price_from_salla": 0,
         "raw_salla": {"cost": 99, "description": "x" * 100000}},
        {"id": "p3", "salla_product_id": "s3", "name": "duplicate", "raw_salla": {"cost": 9}},
        {"id": "p4", "salla_product_id": "s4", "name": "duplicate", "raw_salla": {"cost": 12}},
    ]
    await db.products.insert_many(products)
    full = await db.products.find({}, {"_id": 0}).to_list(10)
    projected = await db.products.find({}, PRODUCT_COST_CATALOG_PROJECTION).to_list(10)
    assert all("description" not in (p.get("raw_salla_details") or {}) and
               "description" not in (p.get("raw_salla") or {}) for p in projected)
    before = _index_products(full)
    after = _index_products(projected)
    for item in [{"product_id": "s1", "quantity": 2}, {"sku": "VSKU", "variant_id": "v1"},
                 {"name": "Unique historical", "product_id": "legacy"},
                 {"product_id": "s2"}, {"name": "duplicate", "product_id": "legacy"}]:
        costs = []
        for indices in (before, after):
            product = _line_product(item, products_by_id=indices[0],
                                    products_by_variant=indices[1], products_by_sku=indices[2])
            costs.append(None if product is None else calculate_mezan_v2_line_cost(
                item, product=product, profile=None, product_bindings=[], option_bindings=[], resources={}))
        assert costs[0] == costs[1]


@pytest.mark.asyncio
async def test_historical_identity_aliases_keep_global_last_proof_semantics(mongo):
    db, _ = mongo
    rows = [order(i) for i in range(260)]
    rows[0]["order_number"] = " 777 "
    rows[130]["order_number"] = 777
    rows[-1]["order_number"] = "777"
    rows[-1]["raw_by_source"]["salla_direct"]["exchange_rate"] = 4.0
    await db.unified_orders.insert_many(rows)
    expected = await db.unified_orders.find(query(), {"_id": 0, "raw_by_source": 0}).to_list(100000)
    projection = dict(SALLA_RAW_CURRENCY_PROJECTION)
    projection.update(SALLA_RAW_ATTRIBUTION_PROJECTION)
    proofs = await db.unified_orders.find(query(), projection).to_list(100000)
    hydrate_order_currency_fields(expected, proofs)
    attach_projected_salla_attribution(expected, proofs)
    assert await load_dashboard_orders(db, query()) == expected


@pytest.mark.asyncio
async def test_v2_skips_unused_analysis_payloads_and_legacy_projects_display_fields(mongo):
    from dashboard_order_reads import read_recent_dashboard_analyses
    db, _ = mongo
    await db.analyses.insert_one({"user_id": "owner", "id": "analysis", "name": "report",
        "created_at": "2026-10-01", "orders_imported": 12, "report": {
            "summary": {"total_sales": 120, "net_profit": 20, "total_orders": 12},
            "huge_detail": ["x" * 1024] * 1000}})
    assert await read_recent_dashboard_analyses(db, "owner", include=False) == []
    rows = await read_recent_dashboard_analyses(db, "owner", include=True)
    assert len(rows) == 1
    assert rows[0]["report"] == {"summary": {"total_sales": 120, "net_profit": 20, "total_orders": 12}}
    assert rows[0]["id"] == "analysis"
    assert await read_recent_dashboard_analyses(db, "other", include=True) == []
