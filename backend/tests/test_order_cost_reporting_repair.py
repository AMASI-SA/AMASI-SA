"""Historical reporting repair against synthetic, frozen sale evidence only."""
import asyncio
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlparse
import uuid

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from mongomock_motor import AsyncMongoMockClient

from order_cost_authority import CORRECTIONS, LIFECYCLES, line_fingerprint
from order_cost_reporting_repair import (
    ReportingRepairRefused, apply_reporting_repair, build_reporting_repair_plan,
    source_authority_evidence_hash,
)
from product_cost_revision import PRODUCT_COST_REVISIONS
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS
from stock_component_consumption_service import PLANS, UNITS

OWNER = "synthetic-repair-owner"


async def seed(db, number="order-one", *, quantity=4, embroidery=0, included_base=False):
    """Frozen parent15 + selected rose40; live parent20 is deliberately different."""
    item = {"order_item_id": "line-one", "product_id": "shawl", "quantity": quantity,
            "sku": "SYNTHETIC-SHAWL", "total": 100 * quantity,
            "options": [{"option_id": "rose", "value_id": "yes"}]}
    order = {"user_id": OWNER, "order_number": number, "products": [item],
             "currency": "SAR", "order_status": "completed", "total_amount": 100 * quantity}
    leaves = [{"kind": "product", "product_id": "rose", "quantity": "1",
               "unit_cost": "40", "cost_source": "mezan_v2_base"}]
    if included_base:
        leaves.append({"kind": "product", "product_id": "shawl", "quantity": "1",
                       "unit_cost": "15", "cost_source": "mezan_v2_base",
                       "current_product_reference": True})
    if embroidery:
        leaves.append({"kind": "direct_cost", "binding_id": "embroidery", "quantity": "1",
                       "unit_cost": str(embroidery), "cost_source": "mezan_option_binding"})
    snapshot = {
        "product_id": "shawl", "revision": 3, "schema_version": 2,
        "estimated_cost": str(40 + embroidery + (15 if included_base else 0)),
        "cost_complete": True, "leaves": leaves,
        "selected_options": [{"option_id": "rose", "value_id": "yes"}],
        "sale_quantity": quantity, "line_fingerprint": line_fingerprint(item),
        "frozen_definitions": {"shawl": {
            "product_id": "shawl", "product_type": "composite", "revision": 3,
            "items": [{"kind": "product", "product_id": "rose", "quantity": "1"}],
            "catalog_metadata": {"profile": {"user_id": OWNER, "salla_product_id": "shawl", "base_cost": 15}},
            "catalog_product": {"user_id": OWNER, "salla_product_id": "shawl", "cost_price_from_salla": 70},
        }},
    }
    await db[PRODUCTS].update_one({"user_id": OWNER, "salla_product_id": "shawl"},
                                {"$set": {"sku": "SYNTHETIC-SHAWL", "cost_price_from_salla": 70}}, upsert=True)
    await db[COST_PROFILES].update_one({"user_id": OWNER, "salla_product_id": "shawl"},
                                     {"$set": {"base_cost": 20}}, upsert=True)
    await db["mezan_bundle_definitions_v2"].update_one(
        {"user_id": OWNER, "product_id": "shawl"}, {"$set": {"product_type": "composite"}}, upsert=True)
    await db["unified_orders"].insert_one(deepcopy(order))
    await db[LIFECYCLES].insert_one({"user_id": OWNER, "order_number": number,
                                    "bundle_intake_snapshots": {"line-one": snapshot}})
    return order


@pytest.fixture
def db():
    return AsyncMongoMockClient().reporting_repair


@pytest_asyncio.fixture
async def mongo():
    uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
    if not uri:
        pytest.skip("MZ2_TEST_MONGO_URI must name a disposable loopback replica set")
    assert urlparse(uri).hostname in {"127.0.0.1", "localhost"}
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    database = client["mz2_reporting_repair_test_" + uuid.uuid4().hex]
    try:
        await client.admin.command("ping")
        yield database
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_dryrun_exposes_three_bases_and_uses_frozen_15_never_live_20(db):
    await seed(db)
    before = {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}
    plan = await build_reporting_repair_plan(db, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    row = plan["lines"][0]
    assert row["before_catalog_report"] == 80
    assert row["before_frozen_authority"] == 160
    assert row["after_reporting_correction"] == 220
    assert row["delta_cost_from_catalog"] == 140
    assert row["delta_cost_from_frozen"] == 60
    assert plan["totals"]["profit_change_from_catalog"] == -140
    assert plan["totals"]["profit_change_from_frozen"] == -60
    assert plan["ready"] and len(plan["corrections"]) == 1
    assert plan["corrections"][0]["cost"]["base_total"] == 60
    assert plan["corrections"][0]["cost"]["components_total"] == 160
    assert {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()} == before


@pytest.mark.asyncio
async def test_catalog_edits_only_change_explicit_comparison_not_manifest_or_correction(db):
    await seed(db)
    first = await build_reporting_repair_plan(db, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    await db[COST_PROFILES].update_one({"user_id": OWNER}, {"$set": {"base_cost": 999}})
    second = await build_reporting_repair_plan(db, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    assert first["manifest_sha"] == second["manifest_sha"]
    assert second["lines"][0]["before_catalog_report"] == 3996
    assert second["lines"][0]["after_reporting_correction"] == 220
    assert first["corrections"][0]["evidence_hash"] == second["corrections"][0]["evidence_hash"]


@pytest.mark.asyncio
@pytest.mark.parametrize("included_base,embroidery,expected,count", [(False, 10, 260, 1), (True, 10, 260, 0)])
async def test_existing_options_and_current_product_are_never_added_twice(db, included_base, embroidery, expected, count):
    await seed(db, included_base=included_base, embroidery=embroidery)
    plan = await build_reporting_repair_plan(db, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    assert plan["lines"][0]["after_reporting_correction"] == expected
    assert len(plan["corrections"]) == count


@pytest.mark.asyncio
async def test_no_explicit_base_decision_is_read_only_frozen_reaggregation(db):
    await seed(db)
    plan = await build_reporting_repair_plan(db, OWNER, ["order-one"])
    assert plan["lines"][0]["after_reporting_correction"] == 160
    assert plan["corrections"] == []
    assert await db[CORRECTIONS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_return_policy_applies_once_per_order_after_whole_line_repair(db):
    await seed(db)
    await db["unified_orders"].update_one({"order_number": "order-one"},
                                        {"$set": {"actual_partial_refund_amount": 100}})
    plan = await build_reporting_repair_plan(db, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    assert plan["totals"]["gross_line_cost_sar"]["after_reporting_correction"] == 220
    assert plan["totals"]["report_cost_sar"]["after_reporting_correction"] == 165
    assert plan["totals"]["profit_change_from_catalog"] == -105
    assert plan["totals"]["profit_change_from_frozen"] == -45


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["frozen_definitions", "frozen_definitions.shawl.catalog_metadata",
                                  "frozen_definitions.shawl.catalog_product"])
async def test_missing_frozen_base_metadata_is_unresolved_despite_complete_current_catalog(db, path):
    await seed(db)
    await db[LIFECYCLES].update_one({"user_id": OWNER}, {"$unset": {"bundle_intake_snapshots.line-one." + path: ""}})
    plan = await build_reporting_repair_plan(db, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    assert not plan["ready"]
    assert "frozen_base_metadata_missing" in plan["lines"][0]["reasons"]
    assert plan["lines"][0]["after_reporting_correction"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["quantity", "options", "unproven_identity", "foreign_frozen_owner"])
async def test_stale_or_unproven_sale_evidence_cannot_be_repaired(db, change):
    await seed(db)
    if change == "quantity":
        await db["unified_orders"].update_one({"user_id": OWNER}, {"$set": {"products.0.quantity": 5}})
    elif change == "options":
        await db["unified_orders"].update_one({"user_id": OWNER}, {"$set": {"products.0.options.0.value_id": "no"}})
    elif change == "unproven_identity":
        await db[LIFECYCLES].update_one({"user_id": OWNER}, {"$unset": {
            "bundle_intake_snapshots.line-one.line_fingerprint": "", "bundle_intake_snapshots.line-one.sale_quantity": ""}})
    else:
        await db[LIFECYCLES].update_one({"user_id": OWNER}, {"$set": {
            "bundle_intake_snapshots.line-one.frozen_definitions.shawl.catalog_metadata.profile.user_id": "another-owner"}})
    plan = await build_reporting_repair_plan(db, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    assert not plan["ready"]
    assert plan["lines"][0]["after_reporting_correction"] is None
    assert plan["corrections"] == []


@pytest.mark.asyncio
async def test_snapshot_quantity_and_option_ids_prove_legacy_identity_without_new_fingerprint(db):
    await seed(db)
    await db[LIFECYCLES].update_one({"user_id": OWNER}, {"$unset": {"bundle_intake_snapshots.line-one.line_fingerprint": ""}})
    plan = await build_reporting_repair_plan(db, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    assert plan["ready"]
    assert plan["lines"][0]["after_reporting_correction"] == 220


@pytest.mark.asyncio
async def test_foreign_owner_and_duplicate_order_or_line_are_rejected(db):
    order = await seed(db)
    with pytest.raises(ReportingRepairRefused, match="order_not_found_for_owner"):
        await build_reporting_repair_plan(db, "another-owner", ["order-one"])
    with pytest.raises(ReportingRepairRefused, match="duplicate_identity"):
        await build_reporting_repair_plan(db, OWNER, ["order-one", "order-one"])
    await db["unified_orders"].insert_one(deepcopy(order))
    with pytest.raises(ReportingRepairRefused, match="duplicate_identity"):
        await build_reporting_repair_plan(db, OWNER, ["order-one"])
    await db["unified_orders"].delete_one({"order_number": "order-one"})
    await db["unified_orders"].update_one({"order_number": "order-one"}, {"$push": {"products": order["products"][0]}})
    with pytest.raises(ReportingRepairRefused, match="duplicate_identity"):
        await build_reporting_repair_plan(db, OWNER, ["order-one"])


@pytest.mark.asyncio
async def test_duplicate_source_lifecycles_are_not_last_write_wins(db):
    await seed(db)
    lifecycle = await db[LIFECYCLES].find_one({"user_id": OWNER}, {"_id": 0})
    await db[LIFECYCLES].insert_one(lifecycle)
    with pytest.raises(ReportingRepairRefused, match="duplicate_identity"):
        await build_reporting_repair_plan(db, OWNER, ["order-one"], include_base_product_ids=["shawl"])


@pytest.mark.asyncio
async def test_actual_component_receipts_cannot_acquire_unproven_expected_parent_cost(db):
    order = await seed(db)
    snapshot = (await db[LIFECYCLES].find_one({"user_id": OWNER}))["bundle_intake_snapshots"]["line-one"]
    await db[PLANS].insert_one({"_id": "plan-one", "user_id": OWNER, "order_id": "order-one",
        "bundle_snapshots": {"line-one": snapshot},
        "lines": [{**order["products"][0], "order_line_id": "line-one"}]})
    await db[UNITS].insert_many([{"user_id": OWNER, "plan_id": "plan-one", "order_line_id": "line-one",
        "unit_index": index, "state": "consumed", "bundle_leaves": [{**snapshot["leaves"][0],
            "actual_cost": "40", "receipt_id": "receipt-rose", "state": "consumed", "consumed_quantity": "1"}]}
        for index in range(4)])
    plan = await build_reporting_repair_plan(db, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    assert plan["lines"][0]["before_frozen_authority"] == 160
    assert plan["lines"][0]["reasons"] == ["actual_cost_missing_base_provenance_required"]
    assert not plan["ready"]


@pytest.mark.asyncio
async def test_mixed_ordinary_and_bundle_lines_preserve_frozen_ordinary_cost(db):
    from order_cost_authority import capture_reporting_costs
    await seed(db)
    ordinary = {"order_item_id": "plain-line", "product_id": "plain", "quantity": 3, "total": 60}
    await db[PRODUCTS].insert_one({"user_id": OWNER, "salla_product_id": "plain"})
    await db[COST_PROFILES].insert_one({"user_id": OWNER, "salla_product_id": "plain", "base_cost": 9})
    costs = await capture_reporting_costs(db, OWNER, [ordinary], {})
    await db[LIFECYCLES].update_one({"user_id": OWNER}, {"$set": {"reporting_cost_snapshots": costs}})
    await db["unified_orders"].update_one({"user_id": OWNER}, {"$push": {"products": ordinary}, "$set": {"total_amount": 460}})
    await db[COST_PROFILES].update_one({"user_id": OWNER, "salla_product_id": "plain"}, {"$set": {"base_cost": 100}})
    plan = await build_reporting_repair_plan(db, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    assert plan["totals"]["gross_line_cost_sar"]["after_reporting_correction"] == 247
    assert len(plan["corrections"]) == 1
    assert next(row for row in plan["lines"] if row["order_item_id"] == "plain-line")["after_reporting_correction"] == 27


@pytest.mark.asyncio
async def test_missing_historical_cost_never_becomes_verified_from_catalog(db):
    await seed(db)
    await db[LIFECYCLES].delete_many({"user_id": OWNER})
    plan = await build_reporting_repair_plan(db, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    assert plan["lines"][0]["before_catalog_report"] == 80
    assert plan["lines"][0]["before_frozen_authority"] is None
    assert plan["lines"][0]["after_reporting_correction"] is None
    assert not plan["ready"]


@pytest.mark.asyncio
async def test_frozen_cost_currency_uses_its_own_rate_not_sales_exchange_rate(db):
    await seed(db)
    prefix = "bundle_intake_snapshots.line-one.frozen_definitions.shawl.catalog_metadata.profile."
    await db[LIFECYCLES].update_one({"user_id": OWNER}, {"$set": {
        prefix + "cost_currency": "USD", prefix + "cost_fx_rate_to_sar": "3.75"}})
    await db["unified_orders"].update_one({"user_id": OWNER}, {"$set": {
        "currency": "KWD", "exchange_rate_to_sar": "12.2", "currency_conversion_status": "verified", "accounting_currency": "SAR"}})
    plan = await build_reporting_repair_plan(db, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    assert plan["lines"][0]["after_reporting_correction"] == 385  # 160 + 4*15*3.75


@pytest.mark.asyncio
async def test_source_fence_is_equal_for_full_and_compact_snapshot_projection(db):
    from order_cost_authority import load_order_cost_authority
    order = await seed(db)
    record = (await load_order_cost_authority(db, OWNER, [order], include_corrections=False,
                                            include_frozen_metadata=True))["lines"][("order-one", "line-one")]
    compact = deepcopy(record)
    compact["bundle_snapshot"].pop("frozen_definitions")
    assert source_authority_evidence_hash(record) == source_authority_evidence_hash(compact)
    compact["cost"]["cost_stage"] = "actual"
    assert source_authority_evidence_hash(record) != source_authority_evidence_hash(compact)


async def apply_plan(db, plan):
    return await apply_reporting_repair(db, OWNER, plan["order_numbers"],
        include_base_product_ids=plan["include_base_product_ids"], expected_manifest_sha=plan["manifest_sha"])


@pytest.mark.asyncio
async def test_real_transaction_applies_once_and_leaves_orders_inventory_and_journals_untouched(mongo):
    from order_cost_authority import load_order_cost_authority, resolve_order_line_cost
    order = await seed(mongo)
    for name in ("accounting_journal_groups_v2", "accounting_general_ledger_v2", "general_ledger"):
        await mongo[name].insert_one({"user_id": OWNER, "id": "approved-entry", "status": "posted", "amount": 80})
    await mongo[UNITS].insert_one({"user_id": OWNER, "plan_id": "untouched-plan", "state": "reserved", "quantity": 1})
    original = {name: await mongo[name].find({}).to_list(None) for name in await mongo.list_collection_names()}
    plan = await build_reporting_repair_plan(mongo, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    result = await apply_plan(mongo, plan)
    assert result["applied_corrections"] == 1
    assert result["product_cost_revision_bumped"]
    authority = await load_order_cost_authority(mongo, OWNER, [order])
    resolved = resolve_order_line_cost(order["products"][0], order_number="order-one", catalog_cost={}, authority=authority)
    assert resolved["line_total"] == 220
    await mongo[COST_PROFILES].update_one({"user_id": OWNER}, {"$set": {"base_cost": 999}})
    updated_authority = await load_order_cost_authority(mongo, OWNER, [order])
    assert resolve_order_line_cost(order["products"][0], order_number="order-one", catalog_cost={},
                                   authority=updated_authority)["line_total"] == 220
    replay = await apply_plan(mongo, plan)
    assert replay["mode"] == "no_op" and replay["applied_corrections"] == 0
    assert not replay["product_cost_revision_bumped"]
    assert await mongo[CORRECTIONS].count_documents({}) == 1
    assert (await mongo[PRODUCT_COST_REVISIONS].find_one({"user_id": OWNER}))["revision"] == 1
    for name, documents in original.items():
        if name != COST_PROFILES:  # The test itself deliberately edited today's catalog.
            assert await mongo[name].find({}).to_list(None) == documents


@pytest.mark.asyncio
async def test_real_transaction_partial_failure_rolls_back_entire_batch_and_cache_revision(mongo):
    from pymongo.errors import OperationFailure
    await seed(mongo, "order-one")
    await seed(mongo, "order-two")
    await mongo.create_collection(CORRECTIONS, validator={"order_number": {"$ne": "order-two"}})
    plan = await build_reporting_repair_plan(mongo, OWNER, ["order-one", "order-two"], include_base_product_ids=["shawl"])
    with pytest.raises(OperationFailure):
        await apply_plan(mongo, plan)
    assert await mongo[CORRECTIONS].count_documents({}) == 0
    assert await mongo[PRODUCT_COST_REVISIONS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_real_concurrent_identical_apply_commits_exactly_one_correction_and_revision(mongo):
    await seed(mongo)
    plan = await build_reporting_repair_plan(mongo, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    # Precreate both collections so the test races first writes, not Mongo's
    # collection-creation DDL. The production path also supports absent ones.
    await mongo.create_collection(CORRECTIONS)
    await mongo.create_collection(PRODUCT_COST_REVISIONS)
    results = await asyncio.gather(apply_plan(mongo, plan), apply_plan(mongo, plan))
    assert sorted(row["applied_corrections"] for row in results) == [0, 1]
    assert await mongo[CORRECTIONS].count_documents({}) == 1
    assert (await mongo[PRODUCT_COST_REVISIONS].find_one({"user_id": OWNER}))["revision"] == 1


@pytest.mark.asyncio
async def test_real_apply_rechecks_order_line_and_rejects_changed_manifest(mongo):
    await seed(mongo)
    plan = await build_reporting_repair_plan(mongo, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    await mongo["unified_orders"].update_one({"user_id": OWNER}, {"$set": {"products.0.quantity": 5}})
    with pytest.raises(ReportingRepairRefused, match="repair_manifest_changed"):
        await apply_plan(mongo, plan)
    assert await mongo[CORRECTIONS].count_documents({}) == 0
    assert await mongo[PRODUCT_COST_REVISIONS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_real_conflicting_first_write_cannot_be_replaced(mongo):
    await seed(mongo)
    plan = await build_reporting_repair_plan(mongo, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    conflict = deepcopy(plan["corrections"][0])
    conflict["cost"]["line_total"] = 999
    await mongo[CORRECTIONS].insert_one(conflict)
    with pytest.raises(ReportingRepairRefused, match="repair_manifest_changed"):
        await apply_plan(mongo, plan)
    assert (await mongo[CORRECTIONS].find_one({"user_id": OWNER}))["cost"]["line_total"] == 999
    assert await mongo[PRODUCT_COST_REVISIONS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_real_no_change_reaggregation_does_not_bump_revision(mongo):
    await seed(mongo, included_base=True)
    plan = await build_reporting_repair_plan(mongo, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    result = await apply_plan(mongo, plan)
    assert result["mode"] == "no_op"
    assert await mongo[CORRECTIONS].count_documents({}) == 0
    assert await mongo[PRODUCT_COST_REVISIONS].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expected_price", "actual_receipt_stage"])
async def test_real_applied_correction_is_fenced_when_underlying_authority_changes(mongo, change):
    from order_cost_authority import load_order_cost_authority, resolve_order_line_cost
    order = await seed(mongo)
    plan = await build_reporting_repair_plan(mongo, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    await apply_plan(mongo, plan)
    before = await mongo[CORRECTIONS].find_one({"user_id": OWNER})
    if change == "expected_price":
        await mongo[LIFECYCLES].update_one({"user_id": OWNER}, {"$set": {"bundle_intake_snapshots.line-one.estimated_cost": "41"}})
    else:
        snapshot = (await mongo[LIFECYCLES].find_one({"user_id": OWNER}))["bundle_intake_snapshots"]["line-one"]
        await mongo[PLANS].insert_one({"_id": "later-plan", "user_id": OWNER, "order_id": "order-one",
            "bundle_snapshots": {"line-one": snapshot}, "lines": [{**order["products"][0], "order_line_id": "line-one"}]})
        await mongo[UNITS].insert_many([{"user_id": OWNER, "plan_id": "later-plan", "order_line_id": "line-one",
            "unit_index": index, "state": "consumed", "bundle_leaves": [{**snapshot["leaves"][0],
                "actual_cost": "42", "receipt_id": "actual-rose", "state": "consumed", "consumed_quantity": "1"}]}
            for index in range(4)])
    authority = await load_order_cost_authority(mongo, OWNER, [order])
    result = resolve_order_line_cost(order["products"][0], order_number="order-one", catalog_cost={}, authority=authority)
    assert not result["calculation_cost_available"]
    assert result["missing_cost_reasons"] == ["cost_correction_source_changed"]
    assert await mongo[CORRECTIONS].find_one({"user_id": OWNER}) == before
    assert (await mongo[PRODUCT_COST_REVISIONS].find_one({"user_id": OWNER}))["revision"] == 1


@pytest.mark.asyncio
async def test_real_cli_defaults_to_dryrun_and_requires_manifest_for_apply(mongo):
    import json
    await seed(mongo)
    script = Path(__file__).resolve().parents[2] / "scripts" / "repair_order_cost_reporting.py"
    args = [sys.executable, str(script), "--user-id", OWNER, "--orders", "order-one", "--include-base-product-id", "shawl"]
    environment = {**os.environ, "MZ2_REPORTING_REPAIR_MONGO_URI": os.environ["MZ2_TEST_MONGO_URI"],
                   "MZ2_REPORTING_REPAIR_DB_NAME": mongo.name}
    dryrun = subprocess.run(args, env=environment, capture_output=True, text=True, timeout=20)
    assert dryrun.returncode == 0, dryrun.stderr
    result = json.loads(dryrun.stdout)
    assert result["mode"] == "dry_run" and result["lines"][0]["after_reporting_correction"] == 220
    assert environment["MZ2_REPORTING_REPAIR_MONGO_URI"] not in dryrun.stdout + dryrun.stderr
    assert mongo.name not in dryrun.stdout + dryrun.stderr
    assert await mongo[CORRECTIONS].count_documents({}) == 0
    rejected = subprocess.run([*args, "--apply"], env=environment, capture_output=True, text=True, timeout=20)
    assert rejected.returncode == 2
    assert await mongo[CORRECTIONS].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["cost_incomplete", "leaf_cost_unknown", "total_mismatch"])
async def test_inconsistent_frozen_recipe_cannot_be_repaired(db, change):
    await seed(db)
    path, value = {"cost_incomplete": ("cost_complete", False),
                   "leaf_cost_unknown": ("leaves.0.unit_cost", None),
                   "total_mismatch": ("estimated_cost", "41")}[change]
    await db[LIFECYCLES].update_one({"user_id": OWNER}, {"$set": {"bundle_intake_snapshots.line-one." + path: value}})
    plan = await build_reporting_repair_plan(db, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    assert not plan["ready"]
    assert plan["lines"][0]["after_reporting_correction"] is None


@pytest.mark.asyncio
async def test_correction_first_write_identity_survives_canonical_line_alias(db):
    await seed(db)
    original = await build_reporting_repair_plan(db, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    await db["unified_orders"].update_one({"user_id": OWNER}, {"$set": {
        "products.0.canonical_order_item_id": "salla:synthetic-store:line-one", "products.0.source_item_id": "line-one"}})
    canonical = await build_reporting_repair_plan(db, OWNER, ["order-one"], include_base_product_ids=["shawl"])
    assert original["corrections"][0]["_id"] == canonical["corrections"][0]["_id"]
    assert original["corrections"][0]["line_fingerprint"] == canonical["corrections"][0]["line_fingerprint"]
