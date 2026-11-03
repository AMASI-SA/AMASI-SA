"""Historical *reporting only* bundle corrections with scoped synthetic Salla data.

Real Mongo acceptance runs solely against disposable G47 loopback replica set.
No test connects to, resyncs, or modifies actual merchant records.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import os
from urllib.parse import urlparse
import uuid

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from mongomock_motor import AsyncMongoMockClient

from bundle_historical_reporting_audit import BundleHistoricalEvidenceUnavailable
from bundle_historical_reporting_repair import (
    APPLY_FLAG, APPROVAL_PHRASE, _read_evidence,
    apply_missing_bundle_repair, build_missing_bundle_repair_plan,
)
from dashboard_v2_routes import build_mezan_v2_product_cost
from order_cost_authority import (
    CORRECTIONS, _unknown, line_id, load_order_cost_authority,
    resolve_order_line_cost,
)
from product_cost_revision import PRODUCT_COST_REVISIONS
from product_v2_routes import PRODUCTS

OWNER = "synthetic-attested-owner"
NUMBER = "297657209"
PRODUCT = "735616589"


def raw_salla():
    return {
        "id": 180243420, "reference_id": NUMBER,
        "date": "2026-11-02T20:07:33Z",
        "status": {"slug": "in_progress", "name": "قيد التنفيذ"},
        "amounts": {
            "sub_total": {"amount": 225.32, "currency": "SAR"},
            "total": {"amount": 225.32, "currency": "SAR"},
        },
        "items": [{
            "id": 4281, "quantity": 1,
            "product": {"id": int(PRODUCT), "sku": "AMS13281",
                        "name": "Synthetic winter shawl"},
            "amounts": {"price_without_tax": {"amount": 225.32, "currency": "SAR"},
                        "total": {"amount": 225.32, "currency": "SAR"}},
            "options": [
                {"id": 928241773,
                 "option": {"id": 890565818, "name": "Rose?", "type": "radio"},
                 "values": [{"id": 1493279869, "name": "Yes rose"}]},
                {"id": 1952424308,
                 "option": {"id": 116462523, "name": "Embroidery?", "type": "radio"},
                 "values": [{"id": 720290686, "name": "Yes"}]},
                {"id": 1176813685,
                 "option": {"id": 1356739204, "type": "text", "name": "Embroidery text"},
                 "value": {"text": "CUSTOMER_TEXT_NOT_FOR_OUTPUT"}},
                {"id": 268562294,
                 "option": {"id": 582701445, "type": "text", "name": "Gift note"},
                 "value": {"text": "PRIVATE_GIFT_NOTE"}},
            ],
        }],
    }


async def seed(db, *, owner=OWNER, number=NUMBER):
    from order_engine.mapper import map_salla_order
    raw = raw_salla()
    raw["reference_id"] = number
    dto = map_salla_order(raw)
    rows = [item.model_dump(mode="python") for item in dto.items]
    saved = {"user_id": owner, "order_number": number,
             "order_date": "2026-11-02", "order_status": "قيد التنفيذ",
             "total_amount": 225.32, "currency": "SAR",
             "products": deepcopy(rows),
             "raw_by_source": {"salla_direct": raw}}
    await db.unified_orders.insert_one(saved)
    await db["mezan_bundle_definitions_v2"].insert_one(
        {"user_id": owner, "product_id": PRODUCT, "product_type": "composite"})
    await db[PRODUCTS].insert_one(
        {"user_id": owner, "salla_product_id": PRODUCT, "sku": "AMS13281",
         "name": "Synthetic shawl", "cost_price_from_salla": 99})
    return rows[0]["order_item_id"], saved


async def make_attestation(db, owner=OWNER, number=NUMBER, item_id=None):
    if item_id is None:
        from order_engine.mapper import map_salla_order
        item_id = map_salla_order(raw_salla()).items[0].order_item_id
    _, _, evidence = await _read_evidence(
        db if hasattr(db, "_owner") else __import__(
            "order_cost_reporting_repair")._RepairDB(db, owner),
        owner, number, item_id)
    return {
        "version": 1,
        "source_choice_evidence_hash": evidence["source_choice_evidence_hash"],
        "effective_from": "2026-11-01T00:00:00Z",
        "basis_reference": "owner-approved-2026-11-01-source-pricing",
        "base_unit_cost_sar": "15",
        "selected_costs": [
            {"source_option_id": "890565818", "value_id": "1493279869",
             "cost_kind": "component", "unit_cost_sar": "40"},
            {"source_option_id": "116462523", "value_id": "720290686",
             "cost_kind": "external_service", "unit_cost_sar": "10"},
        ],
    }


@pytest.fixture
def db():
    return AsyncMongoMockClient().bundle_owner_reporting


@pytest_asyncio.fixture
async def replica():
    uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
    if not uri:
        pytest.skip("G47 requires disposable MZ2_TEST_MONGO_URI")
    assert urlparse(uri).hostname in ("127.0.0.1", "localhost")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    database = client["bundle_reporting_repair_" + uuid.uuid4().hex]
    try:
        await client.admin.command("ping")
        yield database
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_dry_run_proposes_only_one_expected_cost_and_does_not_write(db):
    item_id, saved = await seed(db)
    attestation = await make_attestation(db, item_id=item_id)
    before = {name: await db[name].count_documents({}) for name in await db.list_collection_names()}
    plan = await build_missing_bundle_repair_plan(db, OWNER, NUMBER, item_id, attestation)
    after = {name: await db[name].count_documents({}) for name in await db.list_collection_names()}
    assert before == after
    assert plan["mode"] == "dry_run" and plan["ready"]
    assert plan["before_line_cost_sar"] is None
    assert plan["after_line_cost_sar"] == 65
    assert plan["application_state"] == "pending"
    assert plan["correction"]["cost"]["components_total"] == 50
    assert plan["correction"]["cost"]["base_total"] == 15
    assert plan["correction"]["cost"]["selected_options_total"] == 10
    assert plan["correction"]["base_provenance"]["provider_invoice_verified"] is False
    assert plan["correction"]["base_provenance"]["immutable_first_ingestion_proven"] is False
    assert plan["correction"]["cost"]["cost_stage"] == "expected"


@pytest.mark.asyncio
async def test_foreign_tenant_cannot_plan_and_does_not_guess_legacy_catalog(db):
    item_id, _ = await seed(db)
    attestation = await make_attestation(db, item_id=item_id)
    with pytest.raises(BundleHistoricalEvidenceUnavailable, match="historical_repair_order_not_found"):
        await build_missing_bundle_repair_plan(db, "another-owner", NUMBER, item_id, attestation)


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ["cost_snapshot", "intake", "reporting", "plan", "same_line_diff_cost"])
async def test_existing_frozen_or_operational_authority_blocks_parallel_write(db, conflict):
    item_id, saved = await seed(db)
    attestation = await make_attestation(db, item_id=item_id)
    if conflict == "cost_snapshot":
        await db["mezan_order_item_cost_snapshots_v2"].insert_one(
            {"user_id": OWNER, "order_number": NUMBER, "order_item_id": item_id})
    elif conflict in ("intake", "reporting"):
        await db["mezan_component_order_lifecycle_v1"].insert_one(
            {"user_id": OWNER, "order_number": NUMBER,
             "bundle_intake_snapshots" if conflict == "intake"
             else "reporting_cost_snapshots": {item_id: {"cost": 3}}})
    elif conflict == "plan":
        await db["mezan_component_consumption_plans_v1"].insert_one(
            {"user_id": OWNER, "order_id": NUMBER, "lines": [{"order_line_id": item_id}]})
    else:
        await db[CORRECTIONS].insert_one({
            "user_id": OWNER, "order_number": NUMBER, "order_item_id": item_id,
            "repair_version": "other", "state": "applied"})
    with pytest.raises(BundleHistoricalEvidenceUnavailable):
        await build_missing_bundle_repair_plan(db, OWNER, NUMBER, item_id, attestation)


@pytest.mark.asyncio
async def test_changed_source_hash_and_customization_do_not_reprice(db):
    item_id, _ = await seed(db)
    attestation = await make_attestation(db, item_id=item_id)
    await db.unified_orders.update_one(
        {"user_id": OWNER, "order_number": NUMBER},
        {"$set": {"raw_by_source.salla_direct.items.0.options.0.values.0.name": "Different"}})
    with pytest.raises(BundleHistoricalEvidenceUnavailable):
        await build_missing_bundle_repair_plan(db, OWNER, NUMBER, item_id, attestation)


@pytest.mark.asyncio
async def test_apply_disabled_by_default_even_with_approved_manifest(db, monkeypatch):
    item_id, _ = await seed(db)
    attestation = await make_attestation(db, item_id=item_id)
    plan = await build_missing_bundle_repair_plan(db, OWNER, NUMBER, item_id, attestation)
    monkeypatch.delenv(APPLY_FLAG, raising=False)
    with pytest.raises(BundleHistoricalEvidenceUnavailable, match="historical_repair_apply_disabled"):
        await apply_missing_bundle_repair(
            db, OWNER, NUMBER, item_id, attestation,
            expected_manifest_sha=plan["manifest_sha"],
            approval_phrase=APPROVAL_PHRASE)
    assert await db[CORRECTIONS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_real_mongo_transaction_first_write_replay_and_dashboard_consistency(replica, monkeypatch):
    db = replica
    item_id, saved = await seed(db)
    attestation = await make_attestation(db, item_id=item_id)
    plan = await build_missing_bundle_repair_plan(db, OWNER, NUMBER, item_id, attestation)
    await db[CORRECTIONS].create_index(
        [("user_id", 1), ("order_number", 1), ("order_item_id", 1), ("state", 1)])
    monkeypatch.setenv(APPLY_FLAG, "1")
    with pytest.raises(BundleHistoricalEvidenceUnavailable, match="historical_repair_explicit_approval_required"):
        await apply_missing_bundle_repair(
            db, OWNER, NUMBER, item_id, attestation, expected_manifest_sha=plan["manifest_sha"],
            approval_phrase="NO")
    with pytest.raises(BundleHistoricalEvidenceUnavailable, match="historical_repair_manifest_changed"):
        await apply_missing_bundle_repair(
            db, OWNER, NUMBER, item_id, attestation, expected_manifest_sha="f" * 64,
            approval_phrase=APPROVAL_PHRASE)
    applied = await apply_missing_bundle_repair(
        db, OWNER, NUMBER, item_id, attestation, expected_manifest_sha=plan["manifest_sha"],
        approval_phrase=APPROVAL_PHRASE)
    assert applied["mode"] == "applied"
    assert applied["changed_reporting_corrections"] == 1
    assert await db[CORRECTIONS].count_documents({"user_id": OWNER}) == 1
    assert (await db[PRODUCT_COST_REVISIONS].find_one({"_id": "product-cost:" + OWNER}))["revision"] == 1
    repeat = await apply_missing_bundle_repair(
        db, OWNER, NUMBER, item_id, attestation, expected_manifest_sha=plan["manifest_sha"],
        approval_phrase=APPROVAL_PHRASE)
    assert repeat["mode"] == "no_op"
    assert await db[CORRECTIONS].count_documents({"user_id": OWNER}) == 1
    assert (await db[PRODUCT_COST_REVISIONS].find_one({"_id": "product-cost:" + OWNER}))["revision"] == 1

    report = await build_mezan_v2_product_cost(db, OWNER, [saved])
    assert report["total"] == report["product_profit_summary"]["total_cost"] == 65
    product = next(row for row in report["product_rows"] if row["sku"] == "AMS13281")
    assert product["total_cost"] == 65 and product["cost_status"] == "complete"
    assert product["net_profit"] is not None
    authority = await load_order_cost_authority(db, OWNER, [saved])
    frozen = resolve_order_line_cost(
        saved["products"][0], order_number=NUMBER,
        catalog_cost=_unknown(1, "probe"), authority=authority, product_id=PRODUCT)
    assert frozen["line_total"] == 65 and frozen["calculation_cost_available"]
    # The correction must never create executable operational records.
    for name in (
        "mezan_component_consumption_plans_v1",
        "mezan_component_consumption_units_v1",
        "purchase_invoices", "general_ledger", "supplier_invoices",
    ):
        assert await db[name].count_documents({}) == 0


@pytest.mark.asyncio
async def test_reporting_correction_detects_tampering_and_preserves_missing(replica, monkeypatch):
    item_id, saved = await seed(replica)
    attestation = await make_attestation(replica, item_id=item_id)
    plan = await build_missing_bundle_repair_plan(replica, OWNER, NUMBER, item_id, attestation)
    monkeypatch.setenv(APPLY_FLAG, "1")
    await apply_missing_bundle_repair(
        replica, OWNER, NUMBER, item_id, attestation,
        expected_manifest_sha=plan["manifest_sha"], approval_phrase=APPROVAL_PHRASE)
    await replica[CORRECTIONS].update_one(
        {"user_id": OWNER, "order_number": NUMBER},
        {"$set": {"cost.line_total": 650}})
    report = await build_mezan_v2_product_cost(replica, OWNER, [saved])
    assert report["product_rows"][0]["total_cost"] is None
    assert report["product_rows"][0]["cost_status"] == "missing"
    assert report["missing_products_count"] == 1
