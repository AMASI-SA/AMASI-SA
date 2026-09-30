"""C2 native invoice contracts against disposable loopback replica-set Mongo.

Each test owns a UUID database. Opening rows are synthetic local test setup;
no production credentials, data, migrations, or financial endpoints are used.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from urllib.parse import urlparse
import uuid

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import monitoring
import pytest
import pytest_asyncio

from accounting_module_contract import EVIDENCE_SECTIONS
from accounting_atomic import atomic_owner
from accounting_ledger_v2 import (
    AccountingLedgerV2Error, ensure_accounting_ledger_v2_indexes,
    post_opening_journal_v2,
)
import supplier_native_invoice_v2 as native

OWNER = "c2-test-owner"
ACTOR = {"id": OWNER, "role": "owner", "is_active": True,
         "accounting_permissions": ["accounting.purchases.post", "accounting.settlements.view"]}
NOW = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
LEGACY = {"general_ledger", "suppliers", "counterparties", "accounts", "purchase_invoices", "liabilities"}


OPENING_LINES = [{"category": "inventory_asset", "entity_type": "asset", "entity_id": identity,
                  "sub_account": "inventory", "ledger_currency": "SAR", "amount": 0}
                 for identity in ("inventory-main", "inventory-variant")] + [
                {"category": "input_vat", "entity_type": "tax", "entity_id": "input-tax",
                 "sub_account": "input_vat", "ledger_currency": "SAR", "amount": 0}]
OPENING_MANIFEST = {"draft_id": "synthetic-opening-draft", "cutover_at": "2026-09-01T00:00:00Z", "lines": OPENING_LINES}
OPENING_HASH = hashlib.sha256(json.dumps(OPENING_MANIFEST, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class Commands(monitoring.CommandListener):
    def __init__(self): self.collections = []
    def started(self, event):
        if event.command_name in {"find", "aggregate", "insert", "update", "delete", "count", "distinct", "findAndModify"}:
            self.collections.append(event.command.get(event.command_name))
    def succeeded(self, event): pass
    def failed(self, event): pass


@pytest_asyncio.fixture
async def env():
    uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
    if not uri: pytest.skip("MZ2_TEST_MONGO_URI required")
    assert urlparse(uri).hostname in {"localhost", "127.0.0.1"}, "Disposable loopback Mongo required"
    commands = Commands()
    client = AsyncIOMotorClient(uri, event_listeners=[commands], serverSelectionTimeoutMS=5000, tz_aware=True)
    db = client["mz2_c2_native_" + uuid.uuid4().hex]
    try:
        await client.admin.command("ping")
        await ensure_accounting_ledger_v2_indexes(db)
        await db.users.insert_one(deepcopy(ACTOR))
        await db.mz2_atomic_owners.insert_one({
            "_id": OWNER, "revision": 0, "writes_paused": False,
            "ledger_backend_state": "v2_active", "ledger_backend_revision": 1,
            "ledger_backend_contract_revision": 1, "ledger_backend_activation_ref": "synthetic-c2",
        })
        async def opening(scoped):
            return await post_opening_journal_v2(db, user_id=OWNER, actor_id=OWNER,
                actor_name="Synthetic owner", opening_operation_id="synthetic-opening",
                approved_preview_hash=OPENING_HASH, effective_at="2026-09-01T00:00:00Z",
                entries=[
                    {"leg_key": "control", "entity_type": "opening_control", "entity_id": "clean-start",
                     "entry_type": "opening_balance", "side": "debit", "amount": "0.01"},
                    {"leg_key": "equity", "entity_type": "equity", "entity_id": "opening-equity",
                     "entry_type": "opening_balance", "side": "credit", "amount": "0.01"}],
                mongo_session=scoped._session)
        result = await atomic_owner(db, OWNER, opening)
        group = result["group"]["txn_group_id"]
        await db.settings.insert_one({"user_id": OWNER, "mezan2_financial_cutover": {
            "operation_id": "MZ2-FIN-CUTOVER-001", "ledger_source": "accounting_v2_operation_scoped",
            "status": "active", "cutover_at": "2026-09-01T00:00:00Z",
            "evidence_sheet_ref": "synthetic-local-only",
            "evidence_sections": {x["id"]: "synthetic-local-only" for x in EVIDENCE_SECTIONS},
            "opening_balance_preview_id": "synthetic-preview", "opening_balance_preview_balanced": True,
            "opening_balance_approved_at": "2026-09-01T00:00:00Z", "opening_balance_approved_by": OWNER,
            "opening_balance_txn_group_id": group, "opening_active_txn_group_id": group,
            "opening_root_txn_group_id": group}})
        await db.mezan_suppliers_v2.insert_one({"id": "supplier", "user_id": OWNER, "status": "active"})
        await db.mezan_products_v2.insert_one({"id": "product", "user_id": OWNER, "status": "active"})
        await db.mezan_cost_resources_v2.insert_one({"id": "service", "user_id": OWNER, "status": "active", "kind": "service"})
        commands.collections.clear()
        yield db, commands
    finally:
        await client.drop_database(db.name)
        client.close()


def invoice(*, service=False, variant=None, tax=0):
    line = {"product_id": "product", "variant_id": variant, "piece_ids": ["piece"], "quantity": 1,
            "product_charge_eligible": True, "product_unit_price_halalas": 10000,
            "product_total_halalas": 10000, "services_total_halalas": 2500 if service else 0,
            "total_halalas": 12500 if service else 10000,
            "services": [{"service_id": "service", "quantity_per_piece": 1, "total_quantity": 1,
                          "unit_price_halalas": 2500, "total_halalas": 2500}] if service else []}
    return {"id": "invoice", "user_id": OWNER, "session_id": "session", "supplier_id": "supplier",
            "approved_by": OWNER, "approved_at": NOW, "currency": "SAR", "experiment_mode": False,
            "subtotal_halalas": line["total_halalas"], "total_halalas": line["total_halalas"] + tax,
            "lines": [line], "line_count": 1, "piece_count": 1}


async def post(db, row, *, after=None):
    async def commit(scoped):
        result = await native.post_native_invoice(db, user_id=OWNER, actor=ACTOR,
            invoice=row, mongo_session=scoped._session)
        if after: await after(scoped)
        await scoped.mezan_supplier_invoices_v2.update_one({"user_id": OWNER, "id": row["id"]},
            {"$setOnInsert": deepcopy(row)}, upsert=True)
        return result
    return await atomic_owner(db, OWNER, commit)


async def legs(db):
    return await db.accounting_general_ledger_v2.find({"user_id": OWNER, "entry_type": "supplier_invoice"}).to_list(100)


async def assert_rolled_back(db):
    assert not await legs(db)
    assert await db.mezan_supplier_invoices_v2.count_documents({}) == 0
    assert await db.accounting_journal_groups_v2.count_documents({"txn_type": "supplier_invoice"}) == 0


def code(exc):
    value = exc.value
    return value.code if isinstance(value, AccountingLedgerV2Error) else (value.detail.get("code") if isinstance(value.detail, dict) else value.detail)

async def mapping(db, *, kind="product", source_id=None, variant=None, treatment=None, entity="inventory-main"):
    from supplier_debit_identity_v2 import MappingPut, save_mapping
    source_id = source_id or ("product" if kind == "product" else "service")
    treatment = treatment or ("INVENTORY_ASSET" if kind in {"product", "default"} else "CAPITALIZE_TO_INVENTORY")
    payload = MappingPut(confirmed=True, reason="Synthetic test explicit owner selection",
        source_kind=kind, source_id=source_id, variant_id=variant, financial_treatment=treatment,
        entity_type="expense" if treatment == "EXPENSE" else "asset", entity_id=entity,
        sub_account=None if treatment == "EXPENSE" else "inventory", currency="SAR", status="active", version=0)
    return await atomic_owner(db, OWNER, lambda scoped: save_mapping(scoped, OWNER, ACTOR, payload))


async def identities(db):
    from supplier_debit_identity_v2 import CONTRACT, EXPENSES
    settings = await db.settings.find_one({"user_id": OWNER})
    await db.mz2_opening_balance_drafts.insert_one({"user_id": OWNER, "status": "posted",
        "id": "synthetic-opening-draft", "cutover_at": "2026-09-01T00:00:00Z",
        "preview_manifest": deepcopy(OPENING_MANIFEST), "preview_hash": OPENING_HASH,
        "txn_group_id": settings["mezan2_financial_cutover"]["opening_active_txn_group_id"],
        "lines": deepcopy(OPENING_LINES)})
    await db.mezan_products_v2.update_one({"user_id": OWNER}, {"$set": {
        "mezan_product_id": "product", "variants": [{"id": "variant", "status": "active"}]}})
    await db[EXPENSES].insert_one({"_id": "expense-main", "id": "expense-main", "user_id": OWNER,
        "contract": CONTRACT, "status": "active", "currency": "SAR", "entity_type": "expense", "sub_account": None,
        "confirmed_at": NOW.isoformat(), "confirmed_by": OWNER, "version": 1})


@pytest.mark.asyncio
async def test_product_mapping_exact_balanced_native_legs_and_zero_legacy_access(env):
    db, commands = env
    await identities(db); await mapping(db)
    commands.collections.clear()
    row = invoice(); await post(db, row)
    rows = await legs(db)
    assert len(rows) == 2
    debit = next(x for x in rows if x["side"] == "debit")
    credit = next(x for x in rows if x["side"] == "credit")
    assert (debit["entity_type"], debit["entity_id"], debit["sub_account"], debit["amount_minor"]) == ("asset", "inventory-main", "inventory", 10000)
    assert (credit["entity_type"], credit["entity_id"], credit["sub_account"], credit["amount_minor"]) == ("supplier", "supplier", "payable", 10000)
    assert credit["entry_type"] == "supplier_invoice"
    assert credit["metadata"]["supplier_invoice_id"] == "invoice"
    assert credit["metadata"]["supplier_source"] == "mezan_suppliers_v2"
    assert credit["metadata"]["invoice_contract"] == "mz2_supplier_invoice_v1"
    saved = await db.mezan_supplier_invoices_v2.find_one({"id": "invoice"})
    assert saved["mz2_financial_contract"] == "mz2_supplier_invoice_v1"
    assert saved["mz2_txn_group_id"] == credit["txn_group_id"]
    assert not LEGACY.intersection(commands.collections)


@pytest.mark.asyncio
async def test_variant_specific_overrides_product_mapping(env):
    db, _ = env; await identities(db)
    await mapping(db); await mapping(db, variant="variant", entity="inventory-variant")
    await post(db, invoice(variant="variant"))
    assert next(x for x in await legs(db) if x["side"] == "debit")["entity_id"] == "inventory-variant"


@pytest.mark.asyncio
@pytest.mark.parametrize("treatment,entity,entity_type,sub", [
    ("CAPITALIZE_TO_INVENTORY", "inventory-variant", "asset", "inventory"),
    ("EXPENSE", "expense-main", "expense", None)])
async def test_service_explicit_financial_treatment(env, treatment, entity, entity_type, sub):
    db, _ = env; await identities(db); await mapping(db)
    await mapping(db, kind="service", treatment=treatment, entity=entity)
    await post(db, invoice(service=True))
    rows = await legs(db)
    service = next(x for x in rows if x["side"] == "debit" and x["amount_minor"] == 2500)
    assert (service["entity_type"], service["entity_id"], service.get("sub_account")) == (entity_type, entity, sub)
    assert sum(x["amount_minor"] for x in rows if x["side"] == "debit") == 12500
    assert sum(x["amount_minor"] for x in rows if x["side"] == "credit") == 12500


@pytest.mark.asyncio
@pytest.mark.parametrize("service,expected", [(False, "MZ2_SUPPLIER_PRODUCT_DEBIT_IDENTITY_REQUIRED"),
    (True, "MZ2_SUPPLIER_SERVICE_DEBIT_IDENTITY_REQUIRED")])
async def test_missing_mapping_fails_closed(env, service, expected):
    db, _ = env; await identities(db)
    if service: await mapping(db)
    with pytest.raises(HTTPException) as exc: await post(db, invoice(service=service))
    assert code(exc) == expected
    await assert_rolled_back(db)


@pytest.mark.asyncio
async def test_foreign_mapping_not_accepted(env):
    from supplier_debit_identity_v2 import MAPPINGS
    db, _ = env; await identities(db); row = await mapping(db)
    await db[MAPPINGS].update_one({"id": row["id"]}, {"$set": {"user_id": "other-owner"}})
    with pytest.raises(HTTPException): await post(db, invoice())
    await assert_rolled_back(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted", [False, True])
async def test_inactive_or_deleted_referenced_expense_rejected_at_posting(env, deleted):
    from supplier_debit_identity_v2 import EXPENSES
    db, _ = env; await identities(db); await mapping(db)
    await mapping(db, kind="service", treatment="EXPENSE", entity="expense-main")
    if deleted: await db[EXPENSES].delete_one({"id": "expense-main"})
    else: await db[EXPENSES].update_one({"id": "expense-main"}, {"$set": {"status": "inactive"}})
    with pytest.raises(HTTPException) as exc: await post(db, invoice(service=True))
    assert code(exc) == "MZ2_SUPPLIER_EXPENSE_IDENTITY_REQUIRED"
    await assert_rolled_back(db)


@pytest.mark.asyncio
async def test_invoice_total_must_equal_exact_line_debits(env):
    db, _ = env; await identities(db); await mapping(db)
    row = invoice(); row["total_halalas"] += 1
    with pytest.raises(HTTPException): await post(db, row)
    await assert_rolled_back(db)


@pytest.mark.asyncio
async def test_tax_without_purchase_contract_fails_closed(env):
    db, _ = env; await identities(db); await mapping(db)
    row = invoice(tax=1500); row["tax_halalas"] = 1500
    with pytest.raises(HTTPException) as exc: await post(db, row)
    assert code(exc) == "MZ2_SUPPLIER_TAX_IDENTITY_REQUIRED"
    await assert_rolled_back(db)


@pytest.mark.asyncio
async def test_same_retry_and_concurrent_retry_single_native_journal(env):
    db, _ = env; await identities(db); await mapping(db)
    await post(db, invoice()); await post(db, invoice())
    await asyncio.gather(post(db, invoice()), post(db, invoice()))
    assert len(await legs(db)) == 2
    assert await db.accounting_journal_groups_v2.count_documents({"txn_type": "supplier_invoice"}) == 1
    assert await db.mezan_supplier_invoices_v2.count_documents({}) == 1


@pytest.mark.asyncio
async def test_same_operation_changed_economics_conflict(env):
    db, _ = env; await identities(db); await mapping(db); await post(db, invoice())
    row = invoice(); row["lines"][0].update(product_unit_price_halalas=10100, product_total_halalas=10100, total_halalas=10100)
    row.update(subtotal_halalas=10100, total_halalas=10100)
    with pytest.raises((HTTPException, AccountingLedgerV2Error)): await post(db, row)
    assert len(await legs(db)) == 2
    assert (await db.mezan_supplier_invoices_v2.find_one({"id": "invoice"}))["total_halalas"] == 10000


@pytest.mark.asyncio
async def test_legacy_only_supplier_is_rejected(env):
    db, commands = env; await identities(db); await mapping(db)
    await db.mezan_suppliers_v2.delete_many({})
    await db.suppliers.insert_one({"id": "supplier", "user_id": OWNER, "status": "active"})
    commands.collections.clear()
    with pytest.raises(HTTPException) as exc: await post(db, invoice())
    assert code(exc) == "supplier_v2_identity_required"
    assert not LEGACY.intersection(commands.collections)
    await assert_rolled_back(db)


@pytest.mark.asyncio
async def test_pause_blocks_all_native_effects(env):
    db, _ = env; await identities(db); await mapping(db)
    await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    with pytest.raises(HTTPException) as exc: await post(db, invoice())
    assert code(exc) == "mz2_writes_paused"
    await assert_rolled_back(db)


@pytest.mark.asyncio
async def test_failure_after_journal_rolls_back_invoice_journal_and_audit(env):
    db, _ = env; await identities(db); await mapping(db)
    audit_before = await db.accounting_audit_log_v2.count_documents({})
    async def fail_after_post(scoped): raise RuntimeError("synthetic post-insert transaction failure")
    with pytest.raises(RuntimeError): await post(db, invoice(), after=fail_after_post)
    await assert_rolled_back(db)
    assert await db.accounting_audit_log_v2.count_documents({}) == audit_before


@pytest.mark.asyncio
async def test_closed_period_is_not_moved_to_another_date(env):
    db, _ = env; await identities(db); await mapping(db)
    await db.mz2_accounting_periods.insert_one({"user_id": OWNER, "month": "2026-09", "closed": True})
    with pytest.raises((HTTPException, AccountingLedgerV2Error)) as exc: await post(db, invoice())
    assert code(exc) == "accounting_period_closed"
    await assert_rolled_back(db)

@pytest.mark.asyncio
async def test_explicit_input_vat_requires_evidence_and_posts_exact_tax_identity(env):
    from accounting_source_files import preserve_original
    db, _ = env; await identities(db); await mapping(db)
    row = invoice(tax=1500)
    digest = hashlib.sha256(b"synthetic purchase tax invoice").hexdigest()
    row["purchase_tax"] = {"treatment": "INPUT_VAT", "amount_halalas": 1500, "entity_id": "input-tax",
        "evidence_file_id": "tax-evidence", "evidence_sha256": digest, "confirmed": True}
    with pytest.raises(HTTPException) as exc: await post(db, deepcopy(row))
    assert code(exc) == "supplier_native_purchase_tax_evidence_required"
    await assert_rolled_back(db)
    await preserve_original(db, OWNER, "tax-evidence", b"synthetic purchase tax invoice")
    await post(db, row)
    rows = await legs(db)
    tax = next(x for x in rows if x["entity_type"] == "tax")
    assert (tax["entity_id"], tax["sub_account"], tax["side"], tax["amount_minor"]) == ("input-tax", "input_vat", "debit", 1500)
    assert sum(x["amount_minor"] for x in rows if x["side"] == "debit") == 11500
    assert next(x for x in rows if x["side"] == "credit")["amount_minor"] == 11500


@pytest.mark.asyncio
async def test_changed_opening_manifest_does_not_authorize_invented_inventory_account(env):
    db, _ = env; await identities(db); await mapping(db)
    await db.mz2_opening_balance_drafts.update_one({"user_id": OWNER}, {"$set": {"lines.0.entity_id": "invented"}})
    with pytest.raises(HTTPException) as exc: await post(db, invoice())
    assert code(exc) == "MZ2_SUPPLIER_OPENING_IDENTITY_MANIFEST_INVALID"
    await assert_rolled_back(db)

async def receiving_session(db):
    import supplier_receiving_routes as receiving
    snapshot = {"id": "supplier", "company_name": "Synthetic supplier", "service_links": []}
    session = {"id": "receiving-session", "user_id": OWNER, "client_request_id": "synthetic-request",
        "reference": "SR-C2-TEST", "status": "open", "supplier_id": "supplier", "supplier_snapshot": snapshot,
        "opened_by": OWNER, "opened_by_name": "Synthetic owner", "opened_at": NOW, "scan_count": 1,
        "order_numbers": ["123456789"], "file_numbers": ["file"]}
    await db[receiving.SESSIONS].insert_one(session)
    await db[receiving.PRODUCTS].update_one({"user_id": OWNER}, {"$set": {
        "salla_product_id": "product", "name": "Synthetic product", "sku": "product"}})
    await db[receiving.COST_PROFILES].insert_one({"user_id": OWNER, "salla_product_id": "product",
        "base_cost": 100, "mezan_product_id": "product"})
    piece = {"user_id": OWNER, "piece_id": "piece", "product_id": "product", "product_name": "Synthetic product",
        "sku": "product", "order_number": "123456789", "order_item_id": "1", "unit_index": 1,
        "status": receiving.PIECE_STATUS_IN_PROGRESS, "supplier_receiving_session_id": session["id"],
        "receipt_event_id": "scan-event", "services": []}
    await db[receiving.PIECES].insert_one(deepcopy(piece))
    event = {**piece, "id": "scan-event", "session_id": session["id"], "event_type": "supplier_piece_scanned",
        "occurred_at": NOW, "product_charge_eligible": True, "invoice_services": [],
        "reference_product_unit_price_halalas": 10000, "reference_product_price_source": "mezan_v2_base",
        "reference_product_price_complete": True}
    await db[receiving.RECEIVING_EVENTS].insert_one(deepcopy(event))
    await db[receiving.PIECE_EVENTS].insert_one(deepcopy(event))
    return session, {"expected_supplier_id": "supplier", "confirmed_total_halalas": 10000,
        "invoice_lines": [{"piece_ids": ["piece"], "product_unit_price_halalas": 10000, "services": []}]}


@pytest_asyncio.fixture
async def http(env):
    import httpx
    from fastapi import FastAPI
    import supplier_receiving_routes as receiving
    db, _ = env
    async def current(): return deepcopy(ACTOR)
    app = FastAPI(); app.include_router(receiving.make_supplier_receiving_router(db, current))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as client:
        yield client


@pytest.mark.asyncio
async def test_real_concurrent_close_and_replay_commit_one_invoice_no_legacy(env, http):
    db, commands = env; await identities(db); await mapping(db)
    session, payload = await receiving_session(db)
    commands.collections.clear()
    path = f'/supplier-receiving-v1/sessions/{session["id"]}/close'
    first, retry = await asyncio.gather(http.post(path, json=payload), http.post(path, json=payload))
    assert first.status_code == 200, first.text
    assert retry.status_code == 200, retry.text
    assert sorted([first.json().get("idempotent", False), retry.json().get("idempotent", False)]) == [False, True]
    invoice_id = first.json()["supplier_invoice"]["id"]
    assert invoice_id == retry.json()["supplier_invoice"]["id"]
    assert await db.mezan_supplier_invoices_v2.count_documents({}) == 1
    assert len(await legs(db)) == 2
    for suffix in ("", "/pdf"):
        response = await http.get('/supplier-receiving-v1/invoices/' + invoice_id + suffix)
        assert response.status_code == 200, response.text
    assert not LEGACY.intersection(commands.collections)
    changed = deepcopy(payload); changed["invoice_lines"][0]["product_unit_price_halalas"] += 1
    changed["confirmed_total_halalas"] += 1
    response = await http.post(path, json=changed)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "supplier_native_close_payload_conflict"
    assert len(await legs(db)) == 2


@pytest.mark.asyncio
async def test_real_close_missing_mapping_rolls_back_receiving_effects(env, http):
    import supplier_receiving_routes as receiving
    db, commands = env; await identities(db)
    session, payload = await receiving_session(db)
    before_piece = await db[receiving.PIECES].find_one({"piece_id": "piece"})
    before_profile = await db[receiving.COST_PROFILES].find_one({"user_id": OWNER})
    commands.collections.clear()
    response = await http.post(f'/supplier-receiving-v1/sessions/{session["id"]}/close', json=payload)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "MZ2_SUPPLIER_PRODUCT_DEBIT_IDENTITY_REQUIRED"
    await assert_rolled_back(db)
    assert await db[receiving.PIECES].find_one({"piece_id": "piece"}) == before_piece
    assert await db[receiving.COST_PROFILES].find_one({"user_id": OWNER}) == before_profile
    assert (await db[receiving.SESSIONS].find_one({"id": session["id"]}))["status"] == "open"
    assert await db[receiving.RECEIVING_EVENTS].count_documents({"event_type": "supplier_receiving_session_closed"}) == 0
    assert not LEGACY.intersection(commands.collections)

@pytest.mark.asyncio
async def test_explicit_audited_store_default_inventory_mapping(env):
    from supplier_debit_identity_v2 import AUDIT
    db, _ = env; await identities(db)
    await mapping(db, kind="default", source_id=OWNER)
    assert await db[AUDIT].count_documents({"user_id": OWNER, "action": "mapping_confirmed"}) == 1
    await post(db, invoice())
    assert next(x for x in await legs(db) if x["side"] == "debit")["entity_id"] == "inventory-main"


@pytest.mark.asyncio
async def test_cutover_readiness_cannot_be_bypassed_by_valid_mappings(env):
    db, _ = env; await identities(db); await mapping(db)
    await db.settings.update_one({"user_id": OWNER}, {"$unset": {"mezan2_financial_cutover.evidence_sheet_ref": ""}})
    with pytest.raises(HTTPException) as exc: await post(db, invoice())
    assert code(exc) == "supplier_native_accounting_not_safe_active"
    await assert_rolled_back(db)


@pytest.mark.asyncio
async def test_post_reloads_permission_in_transaction(env):
    db, _ = env; await identities(db); await mapping(db)
    await db.users.update_one({"id": OWNER}, {"$set": {"role": "employee", "created_by": OWNER,
        "accounting_permissions": ["accounting.settlements.view"]}})
    with pytest.raises(HTTPException) as exc: await post(db, invoice())
    assert code(exc) == "accounting_permission_required"
    await assert_rolled_back(db)


@pytest.mark.asyncio
async def test_reversal_endpoint_fail_closed_keeps_original_invoice_immutable(env, http):
    db, _ = env; await identities(db); await mapping(db)
    session, payload = await receiving_session(db)
    response = await http.post(f'/supplier-receiving-v1/sessions/{session["id"]}/close', json=payload)
    assert response.status_code == 200, response.text
    iid = response.json()["supplier_invoice"]["id"]
    before = await db.mezan_supplier_invoices_v2.find_one({"id": iid})
    rows = await legs(db)
    response = await http.post(f'/supplier-receiving-v1/invoices/{iid}/reverse')
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "supplier_native_reversal_reconciliation_required"
    assert await db.mezan_supplier_invoices_v2.find_one({"id": iid}) == before
    assert await legs(db) == rows
