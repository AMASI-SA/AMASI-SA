"""Cohort catalog hydration must preserve costs without unrelated raw payloads."""
from copy import deepcopy

from bson import BSON
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
import pytest

import dashboard_v2_routes as dashboard
from product_v2_routes import PRODUCTS


OWNER = "catalog-cohort-owner"
BUILDERS = [
    pytest.param(dashboard.build_mezan_v2_product_cost, {}, id="standard"),
    pytest.param(dashboard.build_mezan_v2_product_cost_cooperative, {}, id="cooperative"),
    pytest.param(dashboard.build_mezan_v2_product_cost_cooperative,
                 {"include_product_rows": False}, id="card"),
]


class NoReads:
    def __getitem__(self, name):
        raise AssertionError("empty cohort queried " + name)

    def __getattr__(self, name):
        return self[name]


class RecordedCursor:
    def __init__(self, cursor, record):
        self.cursor, self.record = cursor, record

    async def to_list(self, length):
        rows = await self.cursor.to_list(length=length)
        self.record["rows"] = deepcopy(rows)
        self.record["bytes"] = sum(len(BSON.encode(row)) for row in rows)
        self.record["length"] = length
        return rows

    def __aiter__(self):
        return self.cursor.__aiter__()

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class RecordedCollection:
    def __init__(self, collection, reads):
        self.collection, self.reads = collection, reads

    def find(self, query, projection=None, *args, **kwargs):
        record = {"collection": self.collection.name, "query": deepcopy(query),
                  "projection": deepcopy(projection)}
        self.reads.append(record)
        return RecordedCursor(self.collection.find(query, projection, *args, **kwargs), record)

    def __getattr__(self, name):
        return getattr(self.collection, name)


class RecordedDB:
    def __init__(self, db):
        self.db, self.reads = db, []

    def __getitem__(self, name):
        return RecordedCollection(self.db[name], self.reads)

    def __getattr__(self, name):
        return self[name]


def order(*lines):
    return {"user_id": OWNER, "order_number": "cohort-order", "currency": "SAR",
            "order_date": "2026-11-22", "order_status": "تم التنفيذ",
            "total_amount": 200, "products": list(lines)}


@pytest.mark.asyncio
@pytest.mark.parametrize("builder,kwargs", BUILDERS)
async def test_empty_cohort_does_not_read_catalog_or_any_authority(builder, kwargs):
    result = await builder(NoReads(), OWNER, [], **kwargs)
    assert result["total"] == 0
    assert result["breakdown"] == {
        "mezan_v2_base": 0, "mezan_v2_variant": 0,
        "salla_product_fallback": 0, "salla_variant_fallback": 0,
        "product_components": 0, "selected_options": 0,
    }
    assert result["source_lines"] == {}
    assert result["missing_products"] == []
    assert result["incomplete_orders_count"] == result["no_products_orders_count"] == 0
    row_keys = ["product_rows", "product_profit_summary"] if kwargs.get("include_product_rows", True) else []
    assert list(result) == [
        "total", "breakdown", "source_lines", "linked_products_count", "missing_products_count",
        "missing_product_cost_count", "missing_all_cost_products_count", "missing_all_cost_lines_count",
        "salla_fallback_products_count", "missing_products", *row_keys,
        "no_products_orders_count", "incomplete_orders_count", "source_contract",
    ]
    if kwargs.get("include_product_rows", True):
        assert result["product_rows"] == []
        assert result["product_profit_summary"] == dashboard._finalize_product_profit_rows({})[1]
    else:
        assert "product_rows" not in result
        assert "product_profit_summary" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("builder,kwargs", BUILDERS)
async def test_one_product_cohort_does_not_materialize_unrelated_raw_catalog(builder, kwargs):
    db = AsyncMongoMockClient().catalog_cohort
    products = [{
        "user_id": OWNER, "salla_product_id": f"p-{index}", "name": f"Product {index}",
        "raw_salla_details": {"cost_price": {"amount": "37.50"}, "description": "x" * 16384},
        "raw_salla": {"images": ["image-payload" * 2048]},
    } for index in range(25)]
    await db[PRODUCTS].insert_many(deepcopy(products))
    recorded = RecordedDB(db)
    result = await builder(recorded, OWNER, [order({
        "order_item_id": "line", "product_id": "p-0", "quantity": 2, "total": 200,
    })], **kwargs)
    assert result["total"] == 75
    catalog_reads = [read for read in recorded.reads if read["collection"] == PRODUCTS]
    hydrated = [row for read in catalog_reads for row in read.get("rows", [])
                if "description" in row.get("raw_salla_details", {})]
    assert [row["salla_product_id"] for row in hydrated] == ["p-0"]
    baseline_bytes = sum(len(BSON.encode(row)) for row in products)
    assert sum(read.get("bytes", 0) for read in catalog_reads) < baseline_bytes / 4


async def legacy_catalog_loader(db, user_id, orders):
    """The pre-change owner-wide read/index, with no new filtering logic."""
    rows = await db[PRODUCTS].find(
        {"user_id": user_id}, dashboard.PRODUCT_COST_CATALOG_PROJECTION,
    ).to_list(length=100000)
    ids = [str(row.get("salla_product_id") or "").strip() for row in rows
           if str(row.get("salla_product_id") or "").strip()]
    return ids, dashboard._index_products(rows)


def resolve(line, indexes):
    return dashboard._line_product(line, products_by_id=indexes[0],
                                   products_by_variant=indexes[1], products_by_sku=indexes[2])


async def alias_fixture(db):
    products = [
        {"salla_product_id": "a", "mezan_product_id": "legacy-a", "id": "internal-a",
         "name": "Unique_A!", "sku": "duplicate-sku", "cost_price_from_salla": 10,
         "variants": [{"id": "canonical-var", "sku": "merge-sku"}],
         "raw_salla_details": {"variants": [{"id": "old-var", "sku": "MERGE-SKU", "cost_price": 7}]}},
        {"salla_product_id": "b", "sku": "DUPLICATE-SKU", "cost_price_from_salla": 20,
         "variants": [{"id": "variant-b", "sku": "variant-sku-b", "cost_price_from_salla": 3}]},
        {"salla_product_id": "c", "name": "Ambiguous", "cost_price_from_salla": 30},
        {"salla_product_id": "d", "name": "Ambiguous", "cost_price_from_salla": 40},
        {"_id": "same-first", "salla_product_id": "same", "name": "Same identity", "cost_price_from_salla": 50},
        {"_id": "same-last", "salla_product_id": "same", "name": "Same identity", "cost_price_from_salla": 60},
        {"salla_product_id": "malformed", "raw_salla_details": {
            "variants": {"bad": True}, "skus": [{"id": "suppressed-malformed"}]}},
        {"salla_product_id": "cost-only", "raw_salla_details": {
            "variants": [{"cost": 1}], "skus": [{"id": "suppressed-cost-only"}]}},
        {"salla_product_id": "details-skus", "raw_salla_details": {
            "variants": [], "skus": [{"id": "raw-sku", "cost_price": 9}]}},
        {"salla_product_id": "details-products", "raw_salla_details": {
            "product_variants": [{"id": "product-variant", "cost_price": 11}]}},
        {"salla_product_id": "light-variants", "raw_salla": {
            "variants": [{"id": "light-variant", "cost_price": 12}],
            "skus": [{"id": "suppressed-light-sku"}]}},
        {"salla_product_id": "light-skus", "raw_salla": {
            "skus": [{"id": "light-sku", "cost_price": 13}]}},
    ]
    await db[PRODUCTS].insert_many([{**row, "user_id": OWNER} for row in products])
    await db[PRODUCTS].insert_one({"user_id": "foreign-owner", "salla_product_id": "foreign",
                                  "name": "Unique A!", "sku": "duplicate-sku", "cost_price_from_salla": 999})
    selectors = [
        {"product_id": "a"}, {"source_product": {"id": "legacy-a"}},
        {"source_variant": {"product_id": "internal-a"}}, {"sku": " duplicate-SKU "},
        {"variant_id": "canonical-var"}, {"variant_id": "old-var"}, {"name": "unique a"},
        {"product_id": "c"}, {"name": "Ambiguous"}, {"product_id": "same"},
        {"name": "same identity"}, {"variant_id": "variant-b"},
        {"variant_id": "suppressed-malformed"}, {"variant_id": "suppressed-cost-only"},
        {"variant_id": "raw-sku"}, {"variant_id": "product-variant"},
        {"variant_id": "light-variant"}, {"variant_id": "light-sku"},
        {"variant_id": "suppressed-light-sku"},
    ]
    lines = [{"order_item_id": f"line-{i}", "quantity": 1, "total": 100, **row}
             for i, row in enumerate(selectors)]
    return [order(*lines)]


@pytest.mark.asyncio
async def test_cohort_resolution_preserves_global_alias_winners_and_ambiguity():
    db = AsyncMongoMockClient().alias_parity
    orders = await alias_fixture(db)
    expected_ids, original = await legacy_catalog_loader(db, OWNER, orders)
    ids, scoped = await dashboard._load_cohort_catalog_products(db, OWNER, orders)
    assert ids == expected_ids
    lines = orders[0]["products"]
    for line in lines:
        assert resolve(line, scoped) == resolve(line, original), line
    assert resolve({"sku": "duplicate-sku"}, scoped)["salla_product_id"] == "b"
    assert resolve({"name": "Ambiguous"}, scoped) is None
    assert resolve({"variant_id": "old-var"}, scoped) is None
    assert resolve({"product_id": "same"}, scoped)["cost_price_from_salla"] == 60
    assert resolve({"name": "same identity"}, scoped)["cost_price_from_salla"] == 50
    for variant in ("suppressed-malformed", "suppressed-cost-only", "suppressed-light-sku"):
        assert resolve({"variant_id": variant}, scoped) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("builder,kwargs", BUILDERS)
async def test_full_cost_response_and_companion_reads_equal_legacy_catalog(builder, kwargs, monkeypatch):
    db = AsyncMongoMockClient().cost_parity
    orders = await alias_fixture(db)
    current_reads = RecordedDB(db)
    actual = await builder(current_reads, OWNER, deepcopy(orders), **kwargs)
    monkeypatch.setattr(dashboard, "_load_cohort_catalog_products", legacy_catalog_loader)
    original_reads = RecordedDB(db)
    expected = await builder(original_reads, OWNER, deepcopy(orders), **kwargs)
    assert actual == expected
    assert actual["total"] == 255
    assert actual["missing_all_cost_lines_count"] >= 5
    # Preserve existing capped companion subsets, including duplicate IDs.
    companions = lambda reads: [(read["collection"], read["query"], read["projection"], read.get("length"))
                                for read in reads if read["collection"] != PRODUCTS]
    assert companions(current_reads.reads) == companions(original_reads.reads)


@pytest.mark.asyncio
async def test_nonempty_orders_without_products_preserve_companion_scopes(monkeypatch):
    db = AsyncMongoMockClient().no_product_lines
    await db[PRODUCTS].insert_one({"user_id": OWNER, "salla_product_id": "a", "cost_price_from_salla": 10})
    actual_reads, original_reads = RecordedDB(db), RecordedDB(db)
    actual = await dashboard.build_mezan_v2_product_cost_cooperative(actual_reads, OWNER, [order()])
    monkeypatch.setattr(dashboard, "_load_cohort_catalog_products", legacy_catalog_loader)
    expected = await dashboard.build_mezan_v2_product_cost_cooperative(original_reads, OWNER, [order()])
    assert actual == expected
    assert actual["no_products_orders_count"] == actual["incomplete_orders_count"] == 1
    companions = lambda reads: [read for read in reads if read["collection"] != PRODUCTS]
    assert companions(actual_reads.reads) == companions(original_reads.reads)
    assert len([read for read in actual_reads.reads if read["collection"] == PRODUCTS]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["delete", "reidentify", "rename", "price"])
async def test_selected_document_changed_between_alias_and_hydration(mutation, monkeypatch):
    db = AsyncMongoMockClient().catalog_race
    await db[PRODUCTS].insert_one({"user_id": OWNER, "salla_product_id": "a", "name": "Original",
                                  "cost_price_from_salla": 10})
    original_index = dashboard.index_current_catalog_products_cooperative

    async def index_then_change(rows):
        indexes = await original_index(rows)
        if mutation == "delete":
            await db[PRODUCTS].delete_many({"user_id": OWNER})
        else:
            change = {"reidentify": {"salla_product_id": "b"}, "rename": {"name": "Renamed"},
                      "price": {"cost_price_from_salla": 42}}[mutation]
            await db[PRODUCTS].update_one({"user_id": OWNER}, {"$set": change})
        return indexes

    monkeypatch.setattr(dashboard, "index_current_catalog_products_cooperative", index_then_change)
    orders = [order({"product_id": "a", "quantity": 1, "total": 100})]
    if mutation == "price":
        _, indexes = await dashboard._load_cohort_catalog_products(db, OWNER, orders)
        assert resolve(orders[0]["products"][0], indexes)["cost_price_from_salla"] == 42
    else:
        with pytest.raises(HTTPException) as caught:
            await dashboard._load_cohort_catalog_products(db, OWNER, orders)
        assert caught.value.status_code == 503
        assert caught.value.detail == {
            "code": "catalog_changed_during_cost_read", "retryable": True, "data_complete": False,
        }
        monkeypatch.setattr(dashboard, "index_current_catalog_products_cooperative", original_index)
        fresh = await dashboard.build_mezan_v2_product_cost_cooperative(db, OWNER, orders)
        if mutation == "rename":
            assert fresh["total"] == 10
            assert fresh["product_rows"][0]["name"] == "Renamed"
        else:
            # A truly missing identity remains explicitly incomplete on retry.
            assert fresh["missing_all_cost_lines_count"] == 1
            assert fresh["incomplete_orders_count"] == 1
