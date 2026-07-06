"""Iter-2026-02.rev34 — DRY-mapping Purge/Repair tool (P0).

Runs against the REAL local Mongo (MONGO_URL from backend/.env, loaded
by conftest.py) under an ISOLATED throw-away tenant, so the query
semantics ($regex, $not, $nin) are validated for real — no stubs.

Covers:
  1. plan counts DRY:/PREVIEW: deletes + real-id dry_run_only repairs.
  2. execute refuses a wrong confirm_token and mutates NOTHING.
  3. execute deletes ONLY DRY docs (archived first), repairs flags
     with audit fields, and leaves every real-id doc untouched.
  4. integration_inbox rows (orders + raw payload) are NEVER touched.
  5. verify returns all_pass=True post-purge / False pre-purge.
  6. The rev34 pending-orders ledger filter excludes DRY invoices
     from existing_invoice detection (Mongo-level semantics).
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.dry_purge import (
    ARCHIVE_COLLECTION,
    CONFIRM_TOKEN,
    RUNS_COLLECTION,
    DryPurgeRefused,
    build_dry_purge_plan,
    execute_dry_purge,
    verify_dry_state,
)

TENANT = f"test-drypurge-{uuid4().hex[:8]}"

_ALL_COLLECTIONS = (
    "qoyod_products_mapping", "qoyod_customers_mapping",
    "qoyod_invoices", "qoyod_invoice_payments",
    "integration_inbox", ARCHIVE_COLLECTION, RUNS_COLLECTION,
)


async def _seed(db):
    await db.qoyod_products_mapping.insert_many([
        # DELETE bucket
        {"user_id": TENANT, "sku": "SKU-DRY-1",
         "qoyod_product_id": "DRY:product:aaaa1111"},
        {"user_id": TENANT, "sku": "SKU-PREV-1",
         "qoyod_product_id": "PREVIEW:product:bbbb2222"},
        # REPAIR bucket — REAL id + legacy flag
        {"user_id": TENANT, "sku": "SKU-REAL-FLAG",
         "qoyod_product_id": "7149911", "dry_run_only": True},
        # UNTOUCHED — clean real mapping
        {"user_id": TENANT, "sku": "SKU-REAL-OK",
         "qoyod_product_id": "21", "dry_run_only": False},
    ])
    await db.qoyod_customers_mapping.insert_many([
        {"user_id": TENANT, "lookup_key": "+966500000001",
         "qoyod_customer_id": "DRY:contact:cccc3333"},
        {"user_id": TENANT, "lookup_key": "+966500000002",
         "qoyod_customer_id": "88", "dry_run_only": True},
        {"user_id": TENANT, "lookup_key": "+966500000003",
         "qoyod_customer_id": "99"},
    ])
    await db.qoyod_invoices.insert_many([
        {"user_id": TENANT, "salla_order_id": "ORD-DRY",
         "salla_order_number": "ORD-DRY",
         "qoyod_invoice_id": "DRY:invoice:dddd4444", "dry_run": True,
         "status": "pending"},
        {"user_id": TENANT, "salla_order_id": "ORD-REAL",
         "salla_order_number": "ORD-REAL",
         "qoyod_invoice_id": "188", "status": "sent"},
    ])
    await db.qoyod_invoice_payments.insert_many([
        # DRY via payment id
        {"user_id": TENANT, "salla_order_number": "ORD-DRY",
         "qoyod_invoice_payment_id": "DRY:invoice_payment:eeee5555",
         "qoyod_invoice_id": "DRY:invoice:dddd4444"},
        # DRY via invoice id only
        {"user_id": TENANT, "salla_order_number": "ORD-DRY2",
         "qoyod_invoice_payment_id": None,
         "qoyod_invoice_id": "PREVIEW:invoice:ffff6666"},
        # REAL — frozen forensic style, must survive
        {"user_id": TENANT, "salla_order_number": "ORD-REAL",
         "qoyod_invoice_payment_id": "160", "qoyod_invoice_id": "188"},
    ])
    # An ORDER row carrying DRY sentinels + a raw payload — must be
    # byte-for-byte untouched by the purge.
    await db.integration_inbox.insert_one({
        "user_id": TENANT, "id": "inbox-row-1",
        "salla_order_number": "ORD-DRY",
        "pipeline_stage": "INVOICE_CREATED",
        "qoyod_invoice_id": "DRY:invoice:dddd4444",
        "raw_payload": {"order": {"id": 1, "amounts": {"total": 115}}},
        "qoyod_payloads": {"invoice": {
            "invoice": {"contact_id": "DRY:contact:cccc3333",
                        "line_items": [{"product_id": None}]}}},
    })


@pytest_asyncio.fixture()
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[os.environ["DB_NAME"]]
    # Clean slate for the isolated tenant.
    for coll in _ALL_COLLECTIONS:
        await database[coll].delete_many({"user_id": TENANT})
    await _seed(database)
    yield database
    for coll in _ALL_COLLECTIONS:
        await database[coll].delete_many({"user_id": TENANT})
    client.close()


# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_plan_counts_and_buckets(db):
    plan = await build_dry_purge_plan(db, user_id=TENANT)
    assert plan["ok"] is True
    d = plan["delete"]
    assert d["qoyod_products_mapping"]["count"] == 2   # DRY + PREVIEW
    assert d["qoyod_customers_mapping"]["count"] == 1
    assert d["qoyod_invoices"]["count"] == 1
    assert d["qoyod_invoice_payments"]["count"] == 2   # pid + inv-id
    assert plan["total_delete"] == 6
    r = plan["repair"]
    assert r["qoyod_products_mapping"]["count"] == 1
    assert r["qoyod_customers_mapping"]["count"] == 1
    assert plan["total_repair"] == 2
    assert plan["expected_confirm_token"] == CONFIRM_TOKEN
    # Real ids never appear in the delete samples.
    for bucket in d.values():
        for s in bucket["samples"]:
            for f in ("qoyod_product_id", "qoyod_customer_id",
                      "qoyod_invoice_id", "qoyod_invoice_payment_id"):
                v = s.get(f)
                if v not in (None, ""):
                    assert str(v).upper().startswith(("DRY:", "PREVIEW:")), s


@pytest.mark.asyncio
async def test_execute_refuses_wrong_token_and_mutates_nothing(db):
    with pytest.raises(DryPurgeRefused) as exc:
        await execute_dry_purge(
            db, user_id=TENANT, confirm_token="WRONG", actor="t")
    assert exc.value.code == "confirm_token_mismatch"
    # Nothing deleted / archived.
    assert await db.qoyod_products_mapping.count_documents(
        {"user_id": TENANT}) == 4
    assert await db[ARCHIVE_COLLECTION].count_documents(
        {"user_id": TENANT}) == 0


@pytest.mark.asyncio
async def test_execute_deletes_dry_only_archives_and_repairs(db):
    summary = await execute_dry_purge(
        db, user_id=TENANT, confirm_token=CONFIRM_TOKEN,
        actor="operator:test")
    assert summary["total_deleted"] == 6
    assert summary["deleted"] == {
        "qoyod_products_mapping":  2,
        "qoyod_customers_mapping": 1,
        "qoyod_invoices":          1,
        "qoyod_invoice_payments":  2,
    }
    assert summary["archived"] == summary["deleted"]
    assert summary["total_repaired"] == 2

    # REAL docs survived.
    assert await db.qoyod_products_mapping.count_documents(
        {"user_id": TENANT}) == 2   # SKU-REAL-FLAG + SKU-REAL-OK
    assert await db.qoyod_customers_mapping.count_documents(
        {"user_id": TENANT}) == 2
    real_inv = await db.qoyod_invoices.find_one(
        {"user_id": TENANT, "salla_order_id": "ORD-REAL"})
    assert real_inv and real_inv["qoyod_invoice_id"] == "188"
    real_pay = await db.qoyod_invoice_payments.find_one(
        {"user_id": TENANT, "qoyod_invoice_payment_id": "160"})
    assert real_pay is not None

    # REPAIR carries audit fields, id untouched.
    fixed = await db.qoyod_products_mapping.find_one(
        {"user_id": TENANT, "sku": "SKU-REAL-FLAG"})
    assert fixed["dry_run_only"] is False
    assert fixed["qoyod_product_id"] == "7149911"
    assert fixed["dry_flag_cleared_reason"] == "dry_purge_repair"
    assert fixed["dry_flag_cleared_by"] == "operator:test"
    assert fixed["dry_flag_cleared_run_id"] == summary["run_id"]

    # ARCHIVE holds every deleted doc under this run_id.
    archived = await db[ARCHIVE_COLLECTION].count_documents(
        {"user_id": TENANT, "run_id": summary["run_id"]})
    assert archived == 6
    arch = await db[ARCHIVE_COLLECTION].find_one(
        {"user_id": TENANT, "source_collection": "qoyod_invoices"})
    assert arch["doc"]["qoyod_invoice_id"] == "DRY:invoice:dddd4444"

    # Run summary persisted.
    run = await db[RUNS_COLLECTION].find_one(
        {"user_id": TENANT, "run_id": summary["run_id"]})
    assert run and run["total_deleted"] == 6


@pytest.mark.asyncio
async def test_inbox_orders_and_raw_payload_never_touched(db):
    before = await db.integration_inbox.find_one(
        {"user_id": TENANT, "id": "inbox-row-1"}, {"_id": 0})
    await execute_dry_purge(
        db, user_id=TENANT, confirm_token=CONFIRM_TOKEN, actor="t")
    after = await db.integration_inbox.find_one(
        {"user_id": TENANT, "id": "inbox-row-1"}, {"_id": 0})
    assert after == before          # byte-identical — order + raw payload
    assert after["qoyod_invoice_id"] == "DRY:invoice:dddd4444"


@pytest.mark.asyncio
async def test_verify_fails_before_and_passes_after_purge(db):
    v0 = await verify_dry_state(db, user_id=TENANT)
    assert v0["all_pass"] is False
    assert v0["checks"]["products_dry_mappings"]["count"] == 3   # 2 DRY + 1 flag
    assert v0["checks"]["ledger_dry_invoices"]["count"] == 1
    # The seeded inbox row holds a DRY request_body at a sendable stage.
    assert v0["checks"]["sendable_rows_with_dry_request_body"]["count"] == 1

    await execute_dry_purge(
        db, user_id=TENANT, confirm_token=CONFIRM_TOKEN, actor="t")
    # Simulate the operator reprocessing the stuck row (payload rebuilt)
    # — mark it terminal so it leaves the sendable scope.
    await db.integration_inbox.update_one(
        {"user_id": TENANT, "id": "inbox-row-1"},
        {"$set": {"pipeline_stage": "SKIPPED"}})

    v1 = await verify_dry_state(db, user_id=TENANT)
    assert v1["all_pass"] is True, v1
    for name in ("products_dry_mappings", "customers_dry_mappings",
                 "ledger_dry_invoices", "ledger_dry_payments"):
        assert v1["checks"][name]["count"] == 0


@pytest.mark.asyncio
async def test_pending_orders_ledger_filter_excludes_dry(db):
    """The exact filter shipped in routes.py (rev34) — a DRY ledger
    row must NOT match as an existing invoice; a real one must."""
    ledger_filter = {
        "user_id": TENANT,
        "qoyod_invoice_id": {
            "$exists": True, "$nin": [None, ""],
            "$not": {"$regex": "^(DRY:|PREVIEW:)"},
        },
    }
    matches = [r async for r in db.qoyod_invoices.find(ledger_filter)]
    assert len(matches) == 1
    assert matches[0]["qoyod_invoice_id"] == "188"
