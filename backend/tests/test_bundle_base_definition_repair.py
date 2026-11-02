"""Reviewed physical-base repair; real Mongo tests use disposable loopback DBs."""
import asyncio
from copy import deepcopy
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorClient
from mongomock_motor import AsyncMongoMockClient
import pytest
import pytest_asyncio

import bundle_base_definition_repair as repair
import bundle_catalog_v2 as catalog
from product_cost_revision import get_product_cost_revision
from product_v2_routes import PRODUCTS


OWNER = "synthetic-base-repair-owner"
POLICY = {"sourcing_mode": "auto"}
ROSE_YES = [{"option_id": "rose", "value_id": "yes"}]


async def seed(db, *, root="shawl", schema=2):
    options = [{"id": key, "type": "radio", "values": [{"id": "yes"}, {"id": "no"}]}
               for key in ("rose", "embroidery")]
    for key, cost in ((root, 15), ("flower", 40)):
        await db[PRODUCTS].update_one({"user_id": OWNER, "salla_product_id": key}, {"$set": {
            "mezan_product_id": "mpv2_" + key, "name": "Synthetic " + key,
            "cost_price_from_salla": 70 if key == root else None,
            "variants": [], "options": options if key == root else [],
        }}, upsert=True)
        await db[catalog.COST_PROFILES].update_one({"user_id": OWNER, "salla_product_id": key},
                                                  {"$set": {"base_cost": cost}}, upsert=True)
    await db[catalog.BINDINGS].insert_one({"user_id": OWNER, "salla_product_id": root,
        "id": "embroidery-" + root, "option_id": "embroidery", "value_id": "yes",
        "mode": "direct", "direct_amount": 10, "quantity": 1})
    return await catalog.save_bundle(db, OWNER, root, {"expected_revision": 0,
        "product_type": "composite", "schema_version": schema, "items": [{
            "kind": "product", "product_id": "flower", "quantity": "1", "sourcing_mode": "auto",
            "condition": {"option_id": "rose", "value_id": "yes"},
            **({"rule_id": "rose-rule", "component_overrides": [], "option_mappings": []} if schema == 2 else {}),
        }]}, "synthetic-seed")


async def state(db):
    return {name: await db[name].find({}).sort("_id", 1).to_list(None)
            for name in sorted(await db.list_collection_names())}


async def preview(db, ids=None, policy=None):
    return await repair.build_bundle_base_definition_repair_plan(
        db, OWNER, ids or ["shawl"], source_policy=POLICY if policy is None else policy)


async def apply(db, plan, *, owner=OWNER, sha=None):
    return await repair.apply_bundle_base_definition_repair(
        db, owner, plan, expected_manifest_sha=plan["manifest_sha"] if sha is None else sha,
        actor_id="synthetic-reviewed-operator")


def rehash(plan):
    plan["manifest_sha"] = hashlib.sha256(json.dumps({k: v for k, v in plan.items() if k != "manifest_sha"},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=str).encode()).hexdigest()


@pytest_asyncio.fixture
async def db(monkeypatch):
    # Fast preview/validation tests use the real catalog validator; only its
    # transaction boundary is replaced. Persistence/concurrency use Mongo below.
    async def local_owner(db, owner, callback, **kwargs):
        return await callback(db)
    monkeypatch.setattr(catalog, "operational_owner", local_owner)
    database = AsyncMongoMockClient().base_definition_repair
    await seed(database)
    return database


@pytest_asyncio.fixture
async def real_db():
    uri = os.environ.get("MZ2_TEST_MONGO_URI")
    if not uri:
        pytest.skip("requires an isolated loopback replica set")
    assert urlsplit(uri).hostname in {"127.0.0.1", "localhost", "::1"}
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    database = client["mz2_base_repair_test_" + uuid4().hex]
    try:
        assert (await database.command("hello")).get("setName")
        await database.mz2_atomic_owners.insert_one({"_id": OWNER, "revision": 0, "writes_paused": False})
        await seed(database)
        yield database
    finally:
        assert database.name.startswith("mz2_base_repair_test_")
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_preview_is_read_only_exact_and_deterministic(db):
    await seed(db, root="intentional-replacement-container")
    before = await state(db)
    plan = await preview(db)
    assert await state(db) == before
    assert plan == await preview(db)
    assert plan["ready"]
    row = plan["products"][0]
    assert row["status"] == "change"
    assert row["proposed_payload"]["items"][:-1] == row["before_definition"]["items"]
    assert row["proposed_payload"]["component_scopes"] == row["before_definition"]["component_scopes"]
    assert row["proposed_payload"]["items"][-1] == {
        "kind": "current_product", "quantity": "1", "unit": "unit", "required": True,
        "repeat_policy": "sum", "sourcing_mode": "auto",
        "rule_id": row["proposed_payload"]["items"][-1]["rule_id"],
    }
    assert Decimal(row["source_metadata"]["base_unit_cost"]) == 15
    assert row["source_metadata"]["inventory_leaf"] is True
    assert len(plan["products"]) == 1


@pytest.mark.asyncio
async def test_explicit_scope_source_policy_and_owner_are_required(db):
    for ids in ([], ["shawl"] * 21):
        with pytest.raises(repair.BundleBaseDefinitionRepairRefused, match="explicit_bounded"):
            await repair.build_bundle_base_definition_repair_plan(db, OWNER, ids, source_policy=POLICY)
    with pytest.raises(repair.BundleBaseDefinitionRepairRefused, match="duplicate_product_alias"):
        await preview(db, ["shawl", "mpv2_shawl"])
    with pytest.raises(repair.BundleBaseDefinitionRepairRefused, match="source_policy"):
        await preview(db, policy={})
    with pytest.raises(repair.BundleBaseDefinitionRepairRefused, match="supplier_required"):
        await preview(db, policy={"sourcing_mode": "supplier"})
    with pytest.raises(repair.BundleBaseDefinitionRepairRefused, match="owner_or_version"):
        await apply(db, await preview(db), owner="another-owner")


@pytest.mark.asyncio
@pytest.mark.parametrize("current,code", [
    ([{"kind": "current_product", "quantity": "2"}], "quantity_or_condition"),
    ([{"kind": "current_product", "quantity": "1", "condition": {"option_id": "rose", "value_id": "yes"}}], "quantity_or_condition"),
    ([{"kind": "current_product", "quantity": "1"}] * 2, "multiple_current"),
    ([{"kind": "product", "product_id": "mpv2_shawl", "quantity": "1"}], "parent_product_reference"),
])
async def test_ambiguous_existing_physical_inclusion_is_refused(db, current, code):
    await db[catalog.BUNDLES].update_one({"user_id": OWNER, "product_id": "shawl"}, {"$push": {"items": {"$each": current}}})
    before = await state(db)
    plan = await preview(db)
    assert not plan["ready"] and code in plan["products"][0]["blockers"][0]
    with pytest.raises(repair.BundleBaseDefinitionRepairRefused, match="not_ready"):
        await apply(db, plan)
    assert await state(db) == before


@pytest.mark.asyncio
async def test_missing_resource_scope_is_not_guessed_and_existing_policy_is_preserved(db):
    await db[catalog.RESOURCES].insert_one({"user_id": OWNER, "id": "bag", "unit_cost": 2, "track_inventory": True})
    await db[catalog.PRODUCT_RESOURCE_BINDINGS].insert_one({"user_id": OWNER, "salla_product_id": "shawl",
        "id": "bag-binding", "resource_id": "bag", "quantity": 1})
    policy = {"cost_treatment": "independent", "assembly_origin": "inhouse", "evidence_ref": "synthetic-approved"}
    await db[catalog.COST_PROFILES].update_one({"user_id": OWNER, "salla_product_id": "shawl"},
        {"$set": {"composition_components.product_resource:bag-binding": policy}})
    before = await state(db)
    plan = await preview(db)
    assert plan["products"][0]["blockers"] == ["bundle_component_scope_required"]
    assert await state(db) == before
    await db[catalog.BUNDLES].update_one({"user_id": OWNER, "product_id": "shawl"},
        {"$set": {"component_scopes": {"bag": "per_bundle"}}})
    plan = await preview(db)
    assert plan["ready"] and plan["products"][0]["proposed_payload"]["component_scopes"] == {"bag": "per_bundle"}
    original_profile = await db[catalog.COST_PROFILES].find_one({"user_id": OWNER, "salla_product_id": "shawl"})
    await apply(db, plan)
    assert await db[catalog.COST_PROFILES].find_one({"user_id": OWNER, "salla_product_id": "shawl"}) == original_profile


@pytest.mark.asyncio
async def test_variants_require_an_explicit_reviewed_mapping(db):
    await db[PRODUCTS].update_one({"user_id": OWNER, "salla_product_id": "shawl"},
        {"$set": {"variants": [{"id": "red", "sku": "SYNTHETIC-RED"}]}})
    plan = await preview(db)
    assert plan["products"][0]["blockers"] == ["current_product_variant_mapping_required"]
    mapped = await preview(db, policy={"sourcing_mode": "auto", "slots": [{"slot_id": "physical-shawl", "variant_id": "red"}]})
    assert mapped["ready"]
    assert mapped["products"][0]["proposed_payload"]["items"][-1]["slots"] == [{"slot_id": "physical-shawl", "variant_id": "red"}]


@pytest.mark.asyncio
async def test_manifest_hash_and_append_only_proposal_guards(db):
    plan = await preview(db)
    before = await state(db)
    with pytest.raises(repair.BundleBaseDefinitionRepairRefused, match="hash_mismatch"):
        await apply(db, plan, sha="0" * 64)
    changed = deepcopy(plan)
    changed["products"][0]["proposed_payload"]["items"][0]["quantity"] = "9"
    rehash(changed)
    with pytest.raises(repair.BundleBaseDefinitionRepairRefused, match="proposal_changed"):
        await apply(db, changed)
    assert await state(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"$unset": {"items.0.required": ""}},
    {"$set": {"items.0.quantity": 1}},
    {"$set": {"items.0.rule_id": ""}},
    {"$set": {"items.0.product_id": "mpv2_flower"}},
])
async def test_preview_refuses_implicit_normalization_of_existing_components(db, change):
    await db[catalog.BUNDLES].update_one({"user_id": OWNER, "product_id": "shawl"}, change)
    before = await state(db)
    plan = await preview(db)
    assert plan["products"][0]["blockers"] == ["existing_definition_requires_canonical_review"]
    assert not plan["ready"] and await state(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("collection,query,update", [
    (catalog.BUNDLES, {"product_id": "shawl"}, {"$inc": {"revision": 1}}),
    (catalog.BUNDLES, {"product_id": "shawl"}, {"$set": {"items.0.quantity": "2"}}),
    (catalog.COST_PROFILES, {"salla_product_id": "shawl"}, {"$set": {"base_cost": 70}}),
    (catalog.PRODUCT_OPERATION_PROFILES, {"salla_product_id": "shawl"}, {"$set": {"bundle_sourcing": {"allow_split": False}}}),
])
async def test_revision_hash_cost_and_source_policy_drift_refuse_before_apply(db, collection, query, update):
    plan = await preview(db)
    await db[collection].update_one({"user_id": OWNER, **query}, update, upsert=True)
    before = await state(db)
    with pytest.raises(repair.BundleBaseDefinitionRepairRefused, match="definition_or_catalog_changed"):
        await apply(db, plan)
    assert await state(db) == before


@pytest.mark.asyncio
async def test_batch_preflight_prevents_earlier_product_write_when_later_target_changed(db):
    await seed(db, root="second-shawl")
    plan = await preview(db, ["shawl", "second-shawl"])
    await db[catalog.BUNDLES].update_one({"user_id": OWNER, "product_id": "second-shawl"}, {"$inc": {"revision": 1}})
    before = await state(db)
    with pytest.raises(repair.BundleBaseDefinitionRepairRefused, match="definition_or_catalog_changed"):
        await apply(db, plan)
    assert await state(db) == before


@pytest.mark.asyncio
async def test_legacy_schema_and_existing_rules_remain_unchanged(db):
    await seed(db, root="legacy-shawl", schema=1)
    plan = await preview(db, ["legacy-shawl"])
    before_items = deepcopy(plan["products"][0]["before_definition"]["items"])
    await apply(db, plan)
    saved = await db[catalog.BUNDLES].find_one({"user_id": OWNER, "product_id": "legacy-shawl"})
    assert saved["schema_version"] == 1 and saved["items"][:-1] == before_items
    assert saved["items"][-1]["repeat_policy"] == "once"


@pytest.mark.asyncio
async def test_real_apply_220_options_no_duplicates_and_historical_records_unchanged(real_db):
    db = real_db
    old = await catalog.expand_bundle(db, OWNER, "shawl", selected_options=ROSE_YES)
    assert Decimal(old["estimated_cost"]) * 4 == 160
    for collection, fields in (
        ("mezan_component_order_lifecycle_v1", {"bundle_intake_snapshots": {"line": old}}),
        ("mezan_component_consumption_plans_v1", {"bundle_snapshots": {"line": old}}),
        ("mezan_component_consumption_units_v1", {"leaves": old["leaves"], "state": "consumed"}),
        ("mezan_order_item_cost_snapshots_v2", {"unit_cost": 40, "quantity": 4}),
        ("general_ledger", {"status": "posted", "amount": 160, "side": "debit"}),
    ):
        await db[collection].insert_one({"user_id": OWNER, "order_number": "synthetic-historical-order", **deepcopy(fields)})
    before = await state(db)
    plan = await preview(db)
    applied = await apply(db, plan)
    assert applied["products"][0]["status"] == "applied"
    assert await get_product_cost_revision(db, OWNER) == 1
    current = await catalog.expand_bundle(db, OWNER, "shawl", selected_options=ROSE_YES)
    assert Decimal(current["estimated_cost"]) * 4 == 220
    assert len([leaf for leaf in current["leaves"] if leaf.get("current_product_reference")]) == 1
    with_embroidery = await catalog.expand_bundle(db, OWNER, "shawl", selected_options=ROSE_YES + [{"option_id": "embroidery", "value_id": "yes"}])
    assert Decimal(with_embroidery["estimated_cost"]) * 4 == 260
    no_rose = await catalog.expand_bundle(db, OWNER, "shawl", selected_options=[{"option_id": "rose", "value_id": "no"}])
    assert Decimal(no_rose["estimated_cost"]) * 4 == 60
    frozen_replay = await catalog.expand_bundle(db, OWNER, "shawl", selected_options=ROSE_YES, frozen_definitions=old["frozen_definitions"])
    assert Decimal(frozen_replay["estimated_cost"]) * 4 == 160
    after = await state(db)
    mutable = {catalog.BUNDLES, "mz2_atomic_owners", "mezan_product_cost_revisions_v2"}
    assert {k: v for k, v in before.items() if k not in mutable} == {k: v for k, v in after.items() if k not in mutable}
    saved = await db[catalog.BUNDLES].find_one({"user_id": OWNER, "product_id": "shawl"})
    replay = await apply(db, plan)
    assert replay["products"][0]["status"] == "already_applied"
    assert await db[catalog.BUNDLES].find_one({"user_id": OWNER, "product_id": "shawl"}) == saved
    # A fresh preview of a pre-existing inclusion is a complete write-free no-op.
    no_op = await preview(db)
    assert no_op["products"][0]["status"] == "already_included"
    before_no_op = await state(db)
    assert (await apply(db, no_op))["products"][0]["status"] == "already_included"
    assert await state(db) == before_no_op


@pytest.mark.asyncio
async def test_real_concurrent_apply_adds_only_one_definition_revision(real_db):
    plan = await preview(real_db)
    results = await asyncio.gather(apply(real_db, plan), apply(real_db, plan))
    assert sorted(result["products"][0]["status"] for result in results) == ["already_applied", "applied"]
    saved = await real_db[catalog.BUNDLES].find_one({"user_id": OWNER, "product_id": "shawl"})
    assert saved["revision"] == 2 and len(saved["history"]) == 1
    assert len([row for row in saved["items"] if row["kind"] == "current_product"]) == 1


@pytest.mark.asyncio
async def test_real_commit_invalidation_interruption_is_recoverable_without_second_rule(real_db, monkeypatch):
    plan = await preview(real_db)
    original = repair.bump_product_cost_revision

    async def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic cache write interruption")

    monkeypatch.setattr(repair, "bump_product_cost_revision", unavailable)
    with pytest.raises(repair.BundleBaseDefinitionRepairRefused, match="invalidation_pending") as caught:
        await apply(real_db, plan)
    assert caught.value.results[0]["status"] == "applied"
    assert caught.value.results[0]["cache_invalidated"] is False
    monkeypatch.setattr(repair, "bump_product_cost_revision", original)
    result = await apply(real_db, plan)
    assert result["products"][0]["status"] == "already_applied"
    assert await get_product_cost_revision(real_db, OWNER) == 1
    saved = await real_db[catalog.BUNDLES].find_one({"user_id": OWNER, "product_id": "shawl"})
    assert saved["revision"] == 2


def test_cli_is_preview_by_default_and_requires_reviewed_apply_inputs():
    script = Path(__file__).resolve().parents[2] / "scripts" / "repair_bundle_base_definition.py"
    spec = importlib.util.spec_from_file_location("base_definition_repair_cli_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = module.parser().parse_args(["--user-id", OWNER, "--product-id", "shawl", "--sourcing-mode", "auto"])
    assert args.apply is False and module._inputs(args) == (None, POLICY)
    args = module.parser().parse_args(["--user-id", OWNER, "--apply"])
    with pytest.raises(ValueError, match="reviewed_manifest"):
        module._inputs(args)
