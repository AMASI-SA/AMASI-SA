"""Explicit projection repair: synthetic identities, real provider contracts."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlparse
from uuid import uuid4
from types import SimpleNamespace

from motor.motor_asyncio import AsyncIOMotorClient
from mongomock_motor import AsyncMongoMockClient
import pytest
import pytest_asyncio

from salla_products_projection_repair import (
    ProductsProjectionRepairRefused, apply_products_projection_repair,
    build_products_projection_repair_plan,
)


OWNER = "synthetic-owner"
VERSION = "2026-01-02T12:00:00+00:00"
SIDE_COLLECTIONS = (
    "mezan_component_order_lifecycle_v1", "mezan_component_consumption_plans_v1",
    "mezan_component_consumption_units_v1", "order_review_workflows", "warehouse_locations",
    "mezan_preparation_pieces_v1", "mezan_inventory_receipts_v2", "payment_transactions",
    "tamara_attribution_log", "qoyod_auto_orders", "order_adjustments", "products",
    "mezan_product_cost_revisions_v2", "mezan_order_item_cost_snapshots_v2",
    "mezan_order_cost_corrections_v1", "accounting_journals", "salla_integrations",
)


def source_line(identity="line-kept", *, quantity=1, option="yes"):
    return {"id": identity, "order_id": "internal-one", "quantity": quantity,
        "product_id": "product-one", "product": {"id": "product-one", "sku": "SYN-ONE", "name": "Synthetic product"},
        "variant_id": "variant-one", "amounts": {"total": {"amount": 100}, "price_without_tax": {"amount": 90}},
        "options": [{"id": "option-one", "product_option_id": "embroidery", "name": "Embroidery",
                     "type": "radio", "value": [{"id": option, "name": option}]},
                    {"id": "option-text", "product_option_id": "name", "name": "Name",
                     "type": "textarea", "value": "PRIVATE_SYNTHETIC_NAME"}]}


async def seed(db, *, number="order-one", lines=None, root_count=3):
    lines = deepcopy([source_line()] if lines is None else lines)
    raw = {"id": "internal-one", "reference_id": number, "updated_at": VERSION,
        "status": {"slug": "in_progress"}, "items": lines,
        "customer": {"name": "PRIVATE_CUSTOMER", "phone": "PRIVATE_PHONE"}}
    products = [{"order_item_id": "line-kept" if index == 0 else "line-removed-" + str(index),
        "product_id": "product-one", "sku": "SYN-ONE", "quantity": 1,
        "total": 100, "options": []} for index in range(root_count)]
    doc = {"user_id": OWNER, "order_number": number, "order_id": "internal-one", "products": products,
        "raw_by_source": {"salla_direct": raw}, "field_sources": {"products": "salla_direct", "subtotal": "make"},
        "g47_salla_snapshot": {"source_updated_at": VERSION, "revision": 4, "cancelled": False, "component_pending": False},
        "updated_at": VERSION, "subtotal": 300, "total_amount": 100, "qoyod_followup_dirty": False,
        "cost_computed_at": VERSION, "cost_items": [{"frozen": "unchanged"}], "total_product_cost": 0,
        "customer_name": "PRIVATE_CUSTOMER", "order_status_slug": "in_progress"}
    await db.unified_orders.insert_one(deepcopy(doc))
    for name in SIDE_COLLECTIONS:
        await db[name].update_one({"_id": "sentinel"}, {"$set": {"user_id": OWNER, "value": "untouched"}}, upsert=True)
    return raw


class Provider:
    def __init__(self, raw):
        self.raw, self.calls = deepcopy(raw), []
        self.after_details = None
        self.on_call = None

    async def __call__(self, db, user_id, method, path, **kwargs):
        assert user_id == OWNER and method == "GET"
        self.calls.append((path, deepcopy(kwargs)))
        if self.on_call:
            await self.on_call(db, len(self.calls))
        if path == "/orders/items":
            assert kwargs["params"]["order_id"] == "internal-one"
            return {"success": True, "data": deepcopy(self.raw["items"])}
        assert path == "/orders/internal-one"
        raw = self.after_details if self.after_details and len(self.calls) % 3 == 0 else self.raw
        return {"success": True, "data": {key: deepcopy(value) for key, value in raw.items() if key != "items"}}


@pytest.fixture
def db():
    return AsyncMongoMockClient().projection_repair


@pytest.fixture
def simple_owner(monkeypatch):
    # Unit tests exercise the explicit write contract; real atomicity is tested
    # separately with the existing production owner boundary and Mongo replica set.
    async def owner(db, user_id, callback, **kwargs):
        assert user_id == OWNER and not kwargs
        return await callback(db)
    monkeypatch.setattr("operational_atomic.operational_owner", owner)


async def snapshot(db):
    return {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}


@pytest.mark.asyncio
async def test_preview_is_read_only_and_contains_no_customer_or_option_text(db):
    raw = await seed(db)
    before, provider = await snapshot(db), Provider(raw)
    plan = await build_products_projection_repair_plan(db, OWNER, ["order-one"], request=provider)
    assert plan["ready"] and plan["mode"] == "dry_run" and plan["pending_orders"] == 1
    assert len(plan["orders"][0]["before"]) == 3 and len(plan["orders"][0]["after"]) == 1
    assert plan["orders"][0]["removed_item_ids"] == ["line-removed-1", "line-removed-2"]
    assert [call[0] for call in provider.calls] == ["/orders/internal-one", "/orders/items", "/orders/internal-one"]
    assert await snapshot(db) == before
    assert "PRIVATE" not in json.dumps(plan)


@pytest.mark.asyncio
async def test_apply_changes_only_projection_and_preserves_raw_option_ids(db, simple_owner):
    raw = await seed(db)
    provider = Provider(raw)
    plan = await build_products_projection_repair_plan(db, OWNER, ["order-one"], request=provider)
    before = await snapshot(db)
    result = await apply_products_projection_repair(db, OWNER, ["order-one"], request=provider,
        expected_manifest_sha=plan["manifest_sha"])
    assert result["mode"] == "applied" and result["applied_orders"] == 1
    after = await snapshot(db)
    assert set(after) == set(before)
    for name in SIDE_COLLECTIONS:
        assert after[name] == before[name], name
    old, new = before["unified_orders"][0], after["unified_orders"][0]
    assert {key: value for key, value in old.items() if key != "products"} == {key: value for key, value in new.items() if key != "products"}
    assert len(new["products"]) == 1 and new["products"][0]["order_item_id"] == "line-kept"
    assert "options_raw" not in new["products"][0]
    assert new["products"][0]["options"][0] == {"name": "Embroidery", "value": ["yes"]}
    assert new["raw_by_source"]["salla_direct"] == raw
    assert new["raw_by_source"]["salla_direct"]["items"][0]["options"][0]["product_option_id"] == "embroidery"
    fresh = await build_products_projection_repair_plan(db, OWNER, ["order-one"], request=provider)
    assert fresh["pending_orders"] == 0
    repeated = await apply_products_projection_repair(db, OWNER, ["order-one"], request=provider,
        expected_manifest_sha=fresh["manifest_sha"])
    assert repeated["mode"] == "no_op" and await snapshot(db) == after
    with pytest.raises(ProductsProjectionRepairRefused, match="repair_manifest_changed"):
        await apply_products_projection_repair(db, OWNER, ["order-one"], request=provider,
            expected_manifest_sha=plan["manifest_sha"])


@pytest.mark.asyncio
async def test_quantity_decrease_and_same_sku_distinct_options_survive(db, simple_owner):
    lines = [source_line("line-kept", option="yes"), source_line("line-second", option="no")]
    raw = await seed(db, lines=lines, root_count=1)
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {"products.0.quantity": 3}})
    provider = Provider(raw)
    plan = await build_products_projection_repair_plan(db, OWNER, ["order-one"], request=provider)
    assert plan["ready"] and plan["orders"][0]["before"][0]["quantity"] == "3"
    assert plan["orders"][0]["reduced_quantities"] == [{"order_item_id": "line-kept", "before": "3", "after": "1"}]
    await apply_products_projection_repair(db, OWNER, ["order-one"], request=provider, expected_manifest_sha=plan["manifest_sha"])
    repaired = await db.unified_orders.find_one({"user_id": OWNER})
    products = repaired["products"]
    assert [row["quantity"] for row in products] == [1, 1]
    assert [row["order_item_id"] for row in products] == ["line-kept", "line-second"]
    assert [row["options"][0]["value"] for row in products] == [["yes"], ["no"]]
    assert all("options_raw" not in row for row in products)
    assert repaired["raw_by_source"]["salla_direct"] == raw
    assert [row["options"][0]["value"][0]["id"] for row in raw["items"]] == ["yes", "no"]


@pytest.mark.asyncio
async def test_removed_lines_preserve_surviving_frozen_cost_and_all_history(db, simple_owner):
    from order_cost_authority import (
        SNAPSHOTS, calculate_catalog_line_cost, line_fingerprint,
        load_order_cost_authority, resolve_order_line_cost,
    )
    from salla_integration.sync import _salla_order_to_doc

    raw = await seed(db)
    legacy = _salla_order_to_doc(deepcopy(raw))["products"][0]
    assert "options_raw" not in legacy
    assert legacy["options"][0] == {"name": "Embroidery", "value": ["yes"]}
    old = await db.unified_orders.find_one({"user_id": OWNER})
    old["products"][0] = deepcopy(legacy)
    await db.unified_orders.update_one({"_id": old["_id"]}, {"$set": {"products": old["products"]}})
    for item in old["products"]:
        await db[SNAPSHOTS].insert_one({"user_id": OWNER, "order_number": "order-one",
            "order_item_id": item["order_item_id"], "product_id": item["product_id"],
            "quantity": item["quantity"], "line_fingerprint": line_fingerprint(item),
            "line_cost": 75.5, "base_unit_cost": 75.5, "currency": "SAR", "cost_complete": True})

    frozen_fingerprint = line_fingerprint(legacy)
    # Adding native option IDs to the legacy line would invalidate its frozen
    # evidence even though the customer's choice and quantity did not change.
    assert line_fingerprint({**legacy, "options_raw": raw["items"][0]["options"]}) != frozen_fingerprint
    current_catalog = calculate_catalog_line_cost(legacy, product={}, profile={"base_cost": 999},
        product_bindings=[], option_bindings=[], resources={})
    assert current_catalog["line_total"] == 999
    authority_before = await load_order_cost_authority(db, OWNER, [old], include_frozen_metadata=True)
    cost_before = resolve_order_line_cost(legacy, order_number="order-one",
        catalog_cost=current_catalog, authority=authority_before)
    assert cost_before["line_total"] == 75.5 and cost_before["historical_cost_verified"] is True

    before, provider = await snapshot(db), Provider(raw)
    plan = await build_products_projection_repair_plan(db, OWNER, ["order-one"], request=provider)
    await apply_products_projection_repair(db, OWNER, ["order-one"], request=provider,
        expected_manifest_sha=plan["manifest_sha"])
    after = await snapshot(db)
    repaired = after["unified_orders"][0]
    assert len(old["products"]) == 3 and repaired["products"] == [legacy]
    assert line_fingerprint(repaired["products"][0]) == frozen_fingerprint
    assert repaired["raw_by_source"]["salla_direct"] == raw
    assert {name: rows for name, rows in after.items() if name != "unified_orders"} == {
        name: rows for name, rows in before.items() if name != "unified_orders"}
    assert {key: value for key, value in repaired.items() if key != "products"} == {
        key: value for key, value in old.items() if key != "products"}
    authority_after = await load_order_cost_authority(db, OWNER, [repaired], include_frozen_metadata=True)
    cost_after = resolve_order_line_cost(repaired["products"][0], order_number="order-one",
        catalog_cost=current_catalog, authority=authority_after)
    assert cost_after == cost_before


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["owner", "duplicate_order", "missing", "duplicate_arguments", "over_limit"])
async def test_scope_refusal_precedes_provider_requests(db, mutation):
    raw = await seed(db)
    provider = Provider(raw)
    owner, numbers = OWNER, ["order-one"]
    if mutation == "owner":
        owner = "foreign-owner"
    elif mutation == "duplicate_order":
        row = await db.unified_orders.find_one({}, {"_id": 0})
        await db.unified_orders.insert_one(row)
    elif mutation == "missing":
        numbers = ["missing-order"]
    elif mutation == "duplicate_arguments":
        numbers *= 2
    else:
        numbers = ["order-" + str(index) for index in range(26)]
    before = await snapshot(db)
    with pytest.raises(ProductsProjectionRepairRefused):
        await build_products_projection_repair_plan(db, owner, numbers, request=provider)
    assert not provider.calls and await snapshot(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["identity", "version", "options", "quantity", "variant", "duplicate_items", "malformed", "amounts"])
async def test_changed_or_invalid_provider_evidence_never_applies(db, mutation):
    raw = await seed(db)
    provider = Provider(raw)
    if mutation == "identity":
        provider.raw["reference_id"] = "different-order"
    elif mutation == "version":
        provider.raw["updated_at"] = "2026-01-02T12:00:01+00:00"
    elif mutation == "options":
        provider.raw["items"][0]["options"][0]["value"][0]["id"] = "no"
    elif mutation == "quantity":
        provider.raw["items"][0]["quantity"] = 2
    elif mutation == "variant":
        provider.raw["items"][0]["variant_id"] = "other-variant"
    elif mutation == "duplicate_items":
        provider.raw["items"] *= 2
    elif mutation == "malformed":
        provider.raw["items"].append("invalid")
    else:
        provider.raw["items"][0]["amounts"]["total"]["amount"] = 120
    before = await snapshot(db)
    plan = await build_products_projection_repair_plan(db, OWNER, ["order-one"], request=provider)
    assert not plan["ready"] and plan["orders"][0]["reasons"]
    with pytest.raises(ProductsProjectionRepairRefused, match="repair_has_unresolved_orders"):
        await apply_products_projection_repair(db, OWNER, ["order-one"], request=provider, expected_manifest_sha=plan["manifest_sha"])
    assert await snapshot(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("g47_salla_snapshot.source_updated_at", "2026-01-02T12:00:02+00:00"),
    ("last_salla_direct_status_source_updated_at", "2026-01-02T12:00:02+00:00"),
    ("g47_salla_snapshot.requires_authoritative_refresh", True),
    ("raw_by_source.salla_direct.date.updated", "2026-01-02T12:00:02+00:00"),
    ("raw_by_source.salla_direct.id", "another-internal"),
    ("g47_salla_snapshot.cancelled", True),
])
async def test_stale_conflicting_or_unproven_stored_source_is_refused(db, field, value):
    raw = await seed(db)
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {field: value}})
    provider = Provider(raw)
    before = await snapshot(db)
    plan = await build_products_projection_repair_plan(db, OWNER, ["order-one"], request=provider)
    assert not plan["ready"] and not provider.calls
    assert await snapshot(db) == before


@pytest.mark.asyncio
async def test_provider_version_change_during_read_is_refused(db):
    raw = await seed(db)
    provider = Provider(raw)
    provider.after_details = deepcopy(raw)
    provider.after_details["updated_at"] = "2026-01-02T12:00:02+00:00"
    plan = await build_products_projection_repair_plan(db, OWNER, ["order-one"], request=provider)
    assert not plan["ready"]
    assert plan["orders"][0]["reasons"] == ["provider_source_version_changed"]


@pytest.mark.asyncio
async def test_equal_version_cancellation_conflict_is_not_a_projection_repair(db):
    raw = await seed(db)
    provider = Provider(raw)
    provider.raw["status"] = {"slug": "canceled"}
    plan = await build_products_projection_repair_plan(db, OWNER, ["order-one"], request=provider)
    assert not plan["ready"]
    assert plan["orders"][0]["reasons"] == ["provider_status_differs_from_stored_source"]


@pytest.mark.asyncio
async def test_verified_complete_empty_items_can_clear_the_stale_projection(db, simple_owner):
    raw = await seed(db, lines=[])
    provider = Provider(raw)
    plan = await build_products_projection_repair_plan(db, OWNER, ["order-one"], request=provider)
    assert plan["ready"] and plan["orders"][0]["after"] == []
    await apply_products_projection_repair(db, OWNER, ["order-one"], request=provider,
        expected_manifest_sha=plan["manifest_sha"])
    assert (await db.unified_orders.find_one({"user_id": OWNER}))["products"] == []


@pytest.mark.asyncio
async def test_already_current_normal_projection_without_options_is_no_op(db, simple_owner):
    from orders_db import _current_salla_products
    item = source_line(quantity=2)
    item.pop("options")
    raw = await seed(db, lines=[item], root_count=1)
    products = _current_salla_products(deepcopy(raw), "order-one")
    assert products[0]["quantity"] == 2 and "options_raw" not in products[0]
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {"products": products}})
    before, provider = await snapshot(db), Provider(raw)
    plan = await build_products_projection_repair_plan(db, OWNER, ["order-one"], request=provider)
    assert plan["ready"] and plan["pending_orders"] == 0
    result = await apply_products_projection_repair(db, OWNER, ["order-one"], request=provider,
        expected_manifest_sha=plan["manifest_sha"])
    assert result["mode"] == "no_op" and await snapshot(db) == before


@pytest.mark.asyncio
async def test_unavailable_shared_projection_fails_closed(db, monkeypatch):
    raw = await seed(db)
    monkeypatch.setattr("orders_db._current_salla_products", lambda raw, order_number, **kwargs: None)
    before = await snapshot(db)
    plan = await build_products_projection_repair_plan(db, OWNER, ["order-one"], request=Provider(raw))
    assert not plan["ready"] and plan["orders"][0]["reasons"] == ["source_products_projection_unavailable"]
    assert await snapshot(db) == before


@pytest.mark.asyncio
async def test_provider_cannot_write_credentials_even_during_preview(db):
    raw = await seed(db)
    provider = Provider(raw)
    async def write_credentials(scoped, count):
        await scoped.salla_integrations.update_one({"user_id": OWNER}, {"$set": {"token": "PRIVATE_TOKEN"}})
    provider.on_call = write_credentials
    before = await snapshot(db)
    plan = await build_products_projection_repair_plan(db, OWNER, ["order-one"], request=provider)
    assert not plan["ready"] and plan["orders"][0]["reasons"] == ["provider_credentials_write_forbidden"]
    assert await snapshot(db) == before and "PRIVATE_TOKEN" not in json.dumps(plan)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("products.0.quantity", 2), ("g47_salla_snapshot.revision", 5),
    ("raw_by_source.salla_direct.items.0.options.0.value.0.id", "no"), ("updated_at", "new-revision")])
async def test_preview_to_apply_source_race_refuses_without_writing(db, simple_owner, field, value):
    raw = await seed(db)
    provider = Provider(raw)
    plan = await build_products_projection_repair_plan(db, OWNER, ["order-one"], request=provider)
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {field: value}})
    after_race = await snapshot(db)
    with pytest.raises(ProductsProjectionRepairRefused):
        await apply_products_projection_repair(db, OWNER, ["order-one"], request=provider, expected_manifest_sha=plan["manifest_sha"])
    assert await snapshot(db) == after_race


@pytest.mark.asyncio
async def test_race_after_provider_reads_is_rechecked_inside_owner(db, monkeypatch):
    raw = await seed(db)
    provider = Provider(raw)
    plan = await build_products_projection_repair_plan(db, OWNER, ["order-one"], request=provider)
    async def owner(database, user_id, callback):
        await database.unified_orders.update_one({"user_id": user_id}, {"$inc": {"g47_salla_snapshot.revision": 1}})
        return await callback(database)
    monkeypatch.setattr("operational_atomic.operational_owner", owner)
    with pytest.raises(ProductsProjectionRepairRefused, match="repair_manifest_changed"):
        await apply_products_projection_repair(db, OWNER, ["order-one"], request=provider, expected_manifest_sha=plan["manifest_sha"])
    assert len((await db.unified_orders.find_one({"user_id": OWNER}))["products"]) == 3


@pytest_asyncio.fixture
async def mongo():
    uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
    if not uri:
        pytest.skip("MZ2_TEST_MONGO_URI must name a disposable loopback replica set")
    assert urlparse(uri).hostname in {"127.0.0.1", "localhost"}
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    database = client["salla_projection_repair_test_" + uuid4().hex]
    try:
        assert (await database.command("hello")).get("setName")
        await database.mz2_atomic_owners.insert_one({"_id": OWNER, "revision": 0, "writes_paused": True})
        yield database
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_owner_transaction_limits_writes_and_preserves_pause(mongo):
    raw = await seed(mongo)
    provider = Provider(raw)
    plan = await build_products_projection_repair_plan(mongo, OWNER, ["order-one"], request=provider)
    before = await snapshot(mongo)
    result = await apply_products_projection_repair(mongo, OWNER, ["order-one"], request=provider,
        expected_manifest_sha=plan["manifest_sha"])
    assert result["mode"] == "applied"
    after = await snapshot(mongo)
    assert set(after) == set(before)
    for name in SIDE_COLLECTIONS:
        assert after[name] == before[name], name
    old, new = before["unified_orders"][0], after["unified_orders"][0]
    assert {key: value for key, value in old.items() if key != "products"} == {key: value for key, value in new.items() if key != "products"}
    assert after["mz2_atomic_owners"][0]["writes_paused"] is True
    assert after["mz2_atomic_owners"][0]["revision"] == before["mz2_atomic_owners"][0]["revision"] + 1


@pytest.mark.asyncio
async def test_real_mongo_cas_failure_rolls_back_the_entire_batch(mongo, monkeypatch):
    import operational_atomic
    real_owner = operational_atomic.operational_owner
    first = await seed(mongo)
    second = await seed(mongo, number="order-two")
    second["id"] = "internal-two"
    second["items"][0]["order_id"] = "internal-two"
    await mongo.unified_orders.update_one({"user_id": OWNER, "order_number": "order-two"},
        {"$set": {"order_id": "internal-two", "raw_by_source.salla_direct": second}})
    sources = {"internal-one": first, "internal-two": second}

    async def provider(db, user_id, method, path, **kwargs):
        assert user_id == OWNER and method == "GET"
        if path == "/orders/items":
            return {"data": deepcopy(sources[kwargs["params"]["order_id"]]["items"])}
        return {"data": deepcopy(sources[path.removeprefix("/orders/")])}

    class Collection:
        def __init__(self, real):
            self.real, self.writes = real, 0

        def find(self, *args, **kwargs):
            return self.real.find(*args, **kwargs)

        async def update_one(self, query, update):
            self.writes += 1
            if self.writes == 2:
                return SimpleNamespace(matched_count=0)
            return await self.real.update_one(query, update)

    async def with_failed_second_cas(database, owner, callback):
        async def inside(scoped):
            return await callback(SimpleNamespace(unified_orders=Collection(scoped.unified_orders)))
        return await real_owner(database, owner, inside)

    plan = await build_products_projection_repair_plan(mongo, OWNER, ["order-one", "order-two"], request=provider)
    assert plan["ready"] and plan["pending_orders"] == 2
    before = await snapshot(mongo)
    monkeypatch.setattr(operational_atomic, "operational_owner", with_failed_second_cas)
    with pytest.raises(ProductsProjectionRepairRefused, match="repair_compare_and_set_failed"):
        await apply_products_projection_repair(mongo, OWNER, ["order-one", "order-two"], request=provider,
            expected_manifest_sha=plan["manifest_sha"])
    assert await snapshot(mongo) == before


@pytest.mark.asyncio
async def test_real_mongo_concurrent_owner_update_wins_and_repair_refuses(mongo, monkeypatch):
    import operational_atomic
    real_owner = operational_atomic.operational_owner
    raw = await seed(mongo)
    provider = Provider(raw)
    plan = await build_products_projection_repair_plan(mongo, OWNER, ["order-one"], request=provider)
    writer_entered, release_writer, repair_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def writer(scoped):
        await scoped.unified_orders.update_one({"user_id": OWNER, "order_number": "order-one"},
            {"$set": {"products.0.quantity": 2}, "$inc": {"g47_salla_snapshot.revision": 1}})
        writer_entered.set()
        await asyncio.wait_for(release_writer.wait(), 10)

    async def signal_repair(database, owner, callback):
        repair_entered.set()
        return await real_owner(database, owner, callback)

    writer_task = asyncio.create_task(real_owner(mongo, OWNER, writer))
    await asyncio.wait_for(writer_entered.wait(), 10)
    monkeypatch.setattr(operational_atomic, "operational_owner", signal_repair)
    repair_task = asyncio.create_task(apply_products_projection_repair(mongo, OWNER, ["order-one"],
        request=provider, expected_manifest_sha=plan["manifest_sha"]))
    try:
        await asyncio.wait_for(repair_entered.wait(), 10)
    finally:
        release_writer.set()
    await asyncio.wait_for(writer_task, 10)
    with pytest.raises(ProductsProjectionRepairRefused, match="repair_manifest_changed"):
        await asyncio.wait_for(repair_task, 20)
    current = await mongo.unified_orders.find_one({"user_id": OWNER})
    assert len(current["products"]) == 3 and current["products"][0]["quantity"] == 2
    assert current["g47_salla_snapshot"]["revision"] == 5
    assert (await mongo.mz2_atomic_owners.find_one({"_id": OWNER}))["revision"] == 1


def test_cli_requires_explicit_scope_connection_and_manifest_without_printing_values(tmp_path):
    root = Path(__file__).resolve().parents[2]
    command = [sys.executable, str(root / "scripts" / "repair_salla_products_projection.py")]
    env = {key: value for key, value in os.environ.items() if not key.startswith("MZ2_ITEMS_PROJECTION_REPAIR_")}
    result = subprocess.run(command + ["--user-id", OWNER, "--orders", "order-one"], capture_output=True, text=True, env=env)
    assert result.returncode == 2
    assert json.loads(result.stdout) == {"ok": False, "error": "repair_connection_environment_required"}
    result = subprocess.run(command + ["--user-id", OWNER, "--orders", "order-one", "--apply"], capture_output=True, text=True, env=env)
    assert result.returncode == 2 and "--apply requires --manifest-sha" in result.stderr
