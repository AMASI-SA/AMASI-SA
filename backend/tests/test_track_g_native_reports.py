"""Track G native-only reporting tests; no production I/O or financial writes."""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

import accounting_mz2_reports as reports
import accounting_ledger_v2 as ledger
import accounting_writer_transition as transition
from accounting_module_contract import OPERATION_ID, EVIDENCE_SECTIONS


class NativeOnly:
    def __init__(self, raw):
        self.raw = raw
        self._session = None

    def __getattr__(self, name):
        if name in {"accounts", "general_ledger", "counterparties", "operating_salaries", "suppliers", "financial_provider_apps_legacy"}:
            raise AssertionError("Forbidden legacy read: " + name)
        return getattr(self.raw, name)

    def __getitem__(self, name):
        return getattr(self, name)


def leg(kind, key, sub, side, amount, **extra):
    return dict(entity_type=kind, entity_id=key, sub_account=sub, side=side,
                amount=str(amount), txn_group_id="opening", entry_type="opening_balance",
                effective_at="2026-01-01T00:00:00+00:00", entry_no=1, **extra)


@pytest_asyncio.fixture
async def native(monkeypatch):
    db = NativeOnly(AsyncMongoMockClient().db)
    state = dict(operation_id=OPERATION_ID, status="active", cutover_at="2026-01-01T00:00:00+00:00",
                 opening_active_txn_group_id="opening", opening_balance_txn_group_id="opening",
                 evidence_sheet_ref="signed", evidence_sections={s["id"]: "evidence" for s in EVIDENCE_SECTIONS},
                 opening_balance_preview_id="preview", opening_balance_preview_balanced=True,
                 opening_balance_approved_at="2026-01-01T00:00:00Z", opening_balance_approved_by="owner")
    await db.settings.insert_one({"user_id": "owner", "mezan2_financial_cutover": state})
    await db.mz2_financial_accounts.insert_one({"user_id": "owner", "id": "cash", "account_type": "cash", "status": "active"})
    rows = [leg("bank", "cash", "main", "debit", 100), leg("equity", "opening_balance_equity", "main", "credit", 100)]
    monkeypatch.setattr(transition, "transition_state", AsyncMock(return_value={"state": "v2_active"}))
    monkeypatch.setattr(ledger, "verify_active_opening_v2", AsyncMock(return_value=True))
    async def read(*args, **kwargs):
        assert kwargs["user_id"] == "owner"
        return deepcopy(rows)
    monkeypatch.setattr(ledger, "read_reporting_entries_v2", read)
    return db, rows


@pytest.mark.asyncio
async def test_no_legacy_read_and_native_reconciliation(native):
    db, rows = native
    result = await reports.mz2_financial_position(db, owner="owner")
    assert result["status"] == "available", result
    assert result["totals"] == {"total_assets": 100.0, "total_liabilities": 0.0, "net_position": 100.0}
    trial = await reports.mz2_trial_balance(db, owner="owner")
    assert sum(r["debits"] for r in trial["items"]) == sum(float(r["amount"]) for r in rows if r["side"] == "debit")
    assert sum(r["credits"] for r in trial["items"]) == 100
    statement = await reports.mz2_account_statement(db, owner="owner", entity_type="bank", entity_id="cash")
    assert statement["balance"] == 100


@pytest.mark.asyncio
async def test_legacy_state_has_no_fallback(native, monkeypatch):
    db, _ = native
    monkeypatch.setattr(transition, "transition_state", AsyncMock(return_value={"state": "legacy_active"}))
    result = await reports.mz2_financial_position(db, owner="owner")
    assert result["reason"] == "mz2_native_ledger_required"
    assert result["totals"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,key,sub", [("bank", "foreign", "main"), ("asset", "free-text", "deposit"),
                                            ("supplier", "legacy", "payable"), ("equity", "unknown", "main")])
async def test_unknown_identity_blocks_all_totals(native, kind, key, sub):
    db, rows = native
    rows.append(leg(kind, key, sub, "debit", 15))
    result = await reports.mz2_financial_position(db, owner="owner")
    assert result["reason"] == "unresolved_mz2_identity", result
    assert result["readiness_blockers"][0]["status"] == "UNRESOLVED"
    assert result["totals"] is None


@pytest.mark.asyncio
async def test_native_employee_supplier_external_and_tax_stay_separate(native):
    db, rows = native
    for collection, kind, sub in [("mezan_employees_v2", "employee", "salary_payable"),
                                  ("mezan_suppliers_v2", "supplier", "payable"),
                                  ("mz2_external_persons_v2", "external_person", "payable")]:
        row = {"user_id": "owner", "id": kind, "status": "active"}
        if kind == "employee":
            row["financial_entity_id"] = kind
        await db[collection].insert_one(row)
        rows.append(leg(kind, kind, sub, "credit", 10))
    rows.extend([leg("tax", "input_vat", "", "debit", 12), leg("tax", "sales_vat_payable", "", "credit", 20)])
    result = await reports.mz2_financial_position(db, owner="owner")
    assert result["status"] == "available", result
    assert result["assets"]["input_vat"] == 12
    assert result["liabilities"]["sales_vat_payable"] == 20
    tax = await reports.mz2_entity_report(db, owner="owner", entity_types=("tax",))
    assert {r["entity_id"]: r["net"] for r in tax["items"]} == {"input_vat": 12, "sales_vat_payable": -20}


@pytest.mark.asyncio
async def test_missing_is_not_zero_and_explicit_zero_is_preserved(native):
    db, _ = native
    missing = await reports.mz2_account_statement(db, owner="owner", entity_type="employee", entity_id="employee")
    assert missing["balance"] is None
    assert missing["reason"] == "account_evidence_missing"
    await db.mezan_employees_v2.insert_one({"user_id": "owner", "id": "employee", "financial_entity_id": "employee", "status": "active"})
    zero = dict(entity_type="employee", entity_id="employee", sub_account="advance", evidence_ref="approved-zero",
                opening_balance_txn_group_id="opening", accounting_at="2026-01-01T00:00:00+00:00")
    await db.settings.update_one({"user_id": "owner"}, {"$set": {"mezan2_financial_cutover.opening_balance_zero_accounts": [zero]}})
    result = await reports.mz2_account_statement(db, owner="owner", entity_type="employee", entity_id="employee")
    assert result["status"] == "available", result
    assert result["balance"] == 0
    assert result["balances"][0]["explicit_zero"] is True


@pytest.mark.asyncio
async def test_typed_fact_identity_required_not_just_generic_subaccount(native):
    db, rows = native
    rows.append(leg("liability", "accrual-1", "accrued_expense", "credit", 40))
    assert (await reports.mz2_financial_position(db, owner="owner"))["totals"] is None
    await db.mz2_opening_facts_v2.insert_one(dict(user_id="owner", id="accrual-1", entity_id="accrual-1",
        entity_type="liability", sub_account="accrued_expense", status="active"))
    assert (await reports.mz2_financial_position(db, owner="owner"))["liabilities"]["accrued_expense"] == 40


@pytest.mark.asyncio
async def test_real_mongo_native_integrity_and_reconciliation():
    """Synthetic isolated database only; production APIs are never invoked."""
    import os
    from uuid import uuid4
    from motor.motor_asyncio import AsyncIOMotorClient
    uri = os.environ.get("MZ2_TEST_MONGO_URI")
    if not uri:
        pytest.skip("MZ2_TEST_MONGO_URI required")
    assert uri.startswith("mongodb://127.0.0.1:"), "This fixture is disposable-local only"
    client = AsyncIOMotorClient(uri)
    raw = client["track_g_reports_" + uuid4().hex]
    try:
        await ledger.ensure_accounting_ledger_v2_indexes(raw)
        await raw.mz2_atomic_owners.insert_one(dict(_id="owner", ledger_backend_state="v2_active",
            ledger_backend_revision=1, ledger_backend_contract_revision=1, ledger_backend_activation_ref="synthetic"))
        # Construct immutable synthetic fixtures through the ledger's serializer.
        # This is test setup in an isolated DB, not an Opening HTTP action.
        async with await client.start_session() as session:
            async with session.start_transaction():
                fixture = await ledger.post_opening_journal_v2(raw, user_id="owner", actor_id="synthetic",
                    actor_name="Synthetic fixture", opening_operation_id="synthetic", approved_preview_hash="a" * 64,
                    effective_at="2026-01-01T00:00:00Z", mongo_session=session,
                    entries=[dict(leg_key="cash", entity_type="bank", entity_id="cash", sub_account="main",
                                  side="debit", amount="100.00", entry_type="opening_balance"),
                             dict(leg_key="equity", entity_type="equity", entity_id="opening_balance_equity", sub_account="main",
                                  side="credit", amount="100.00", entry_type="opening_balance")])
        group = fixture["group"]["txn_group_id"]
        state = dict(operation_id=OPERATION_ID, status="active", cutover_at="2026-01-01T00:00:00+00:00",
            opening_active_txn_group_id=group, opening_root_txn_group_id=group, opening_balance_txn_group_id=group,
            evidence_sheet_ref="signed", evidence_sections={s["id"]: "evidence" for s in EVIDENCE_SECTIONS},
            opening_balance_preview_id="preview", opening_balance_preview_balanced=True,
            opening_balance_approved_at="2026-01-01T00:00:00Z", opening_balance_approved_by="owner")
        await raw.settings.insert_one({"user_id": "owner", "mezan2_financial_cutover": state})
        await raw.mz2_financial_accounts.insert_one(dict(user_id="owner", id="cash", account_type="cash", status="active"))
        db = NativeOnly(raw)
        position = await reports.mz2_financial_position(db, owner="owner")
        assert position["status"] == "available", position
        native_balance = await ledger.compute_balance_v2(raw, user_id="owner", entity_type="bank", entity_id="cash")
        assert position["assets"]["banks"] == float(native_balance["net_balance"]) == 100
        trial = await reports.mz2_trial_balance(db, owner="owner")
        assert sum(r["debits"] for r in trial["items"]) == sum(r["credits"] for r in trial["items"]) == 100
        # A mutated native leg cannot leak a partial or fabricated zero balance.
        await raw.accounting_general_ledger_v2.update_one({"user_id": "owner", "entity_type": "bank"}, {"$set": {"amount": "999.00"}})
        broken = await reports.mz2_financial_position(db, owner="owner")
        assert broken["totals"] is None
        assert broken["reason"] == "approved_opening_group_invalid"
    finally:
        await client.drop_database(raw.name)
        client.close()



@pytest.mark.asyncio
async def test_empty_verified_scope_cannot_fabricate_zero(native):
    db, rows = native
    rows.clear()
    await db.mz2_financial_accounts.delete_many({})
    result = await reports.mz2_financial_position(db, owner="owner")
    assert result["totals"] is None
    assert result["reason"] == "opening_balance_evidence_missing"


@pytest.mark.asyncio
@pytest.mark.parametrize("collection,kind,sub", [("mezan_employees_v2", "employee", "advance"),
    ("mezan_suppliers_v2", "supplier", "payable"), ("mz2_external_persons_v2", "external_person", "receivable")])
@pytest.mark.parametrize("owner,status", [("other-owner", "active"), ("owner", "inactive")])
async def test_foreign_and_inactive_native_identity_never_resolves(native, collection, kind, sub, owner, status):
    db, rows = native
    rows.append(leg(kind, "target", sub, "debit", 10))
    await db[collection].insert_one(dict(user_id=owner, id="target", status=status))
    result = await reports.mz2_financial_position(db, owner="owner")
    assert result["totals"] is None
    assert result["readiness_blockers"][0]["entity_id"] == "target"


@pytest.mark.asyncio
async def test_readiness_port_reports_exact_identity_and_unopened_book(native):
    db, rows = native
    rows.append(leg("bank", "unknown", "main", "debit", 10))
    readiness = await reports.mz2_report_readiness(db, owner="owner")
    assert readiness["applicable"] is True
    assert readiness["blockers"][0]["entity_id"] == "unknown"
    await db.settings.delete_many({})
    assert await reports.mz2_report_readiness(db, owner="owner") == {
        "applicable": False, "status": "not_opened", "blockers": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("mapping", [None, "legacy-employee-id"])
async def test_native_employee_id_is_reportable_without_historical_financial_alias(native, mapping):
    db, rows = native
    rows.append(leg("employee", "employee", "salary_payable", "credit", 20))
    await db.mezan_employees_v2.insert_one(dict(user_id="owner", id="employee", status="active",
                                               financial_entity_id=mapping))
    result = await reports.mz2_financial_position(db, owner="owner")
    # Employee OS creates a native id without financial_entity_id. Native
    # writers and reports share that exact id; a historical alias is not an FK.
    assert result["status"] == "available", result
    assert result["totals"] == {"total_assets": 100.0, "total_liabilities": 20.0, "net_position": 80.0}
    assert result["liabilities"]["salaries_unpaid"] == 20.0
    assert result["legacy_financial_data_included"] is False
    trial = await reports.mz2_trial_balance(db, owner="owner")
    assert {(row["entity_type"], row["entity_id"], row["sub_account"]): row["net"] for row in trial["items"]} == {
        ("bank", "cash", "main"): 100.0, ("equity", "opening_balance_equity", "main"): -100.0,
        ("employee", "employee", "salary_payable"): -20.0}
    readiness = await reports.mz2_report_readiness(db, owner="owner")
    assert readiness == {"applicable": True, "status": "available", "blockers": []}
    # NativeOnly rejects every Legacy collection access in all of these calls.


@pytest.mark.asyncio
@pytest.mark.parametrize("record", [None,
    {"user_id": "other-owner", "id": "employee", "status": "active"},
    {"user_id": "owner", "id": "employee", "status": "active", "archived": True},
    {"user_id": "owner", "id": "different-native-id", "status": "active", "financial_entity_id": "employee"},
])
async def test_missing_foreign_archived_or_alias_only_employee_blocks_reports(native, record):
    db, rows = native
    rows.append(leg("employee", "employee", "salary_payable", "credit", 20))
    if record:
        await db.mezan_employees_v2.insert_one(deepcopy(record))
    blocker = {"entity_type": "employee", "entity_id": "employee", "sub_account": "salary_payable",
               "status": "UNRESOLVED", "reason": "native_identity_missing"}
    position = await reports.mz2_financial_position(db, owner="owner")
    assert position["status"] == "not_ready"
    assert position["totals"] is None
    assert position["readiness_blockers"] == [blocker]
    trial = await reports.mz2_trial_balance(db, owner="owner")
    assert trial["status"] == "not_ready"
    assert trial["items"] == []
    assert trial["readiness_blockers"] == [blocker]
    assert await reports.mz2_report_readiness(db, owner="owner") == {
        "applicable": True, "status": "not_ready", "blockers": [blocker]}
