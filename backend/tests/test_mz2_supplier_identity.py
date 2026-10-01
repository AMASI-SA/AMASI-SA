"""Canonical supplier identity and native journal boundary regressions."""
import pytest
from fastapi import HTTPException

from accounting_onboarding_identities import identities, verify_mappings
from accounting_ledger_v2 import post_journal_v2, reverse_journal_v2
from supplier_identity_service import require_supplier_v2
from tests.test_accounting_ledger_v2 import _DB, _open, _atomic


def database():
    return _DB(mezan_suppliers_v2=[
        {"user_id": "owner-1", "id": "canonical", "company_name": "Supplier", "status": "active"},
        {"user_id": "other-owner", "id": "other", "status": "active"},
        {"user_id": "owner-1", "id": "archived", "status": "inactive"},
    ], suppliers=[{"user_id": "owner-1", "id": "legacy"}],
        counterparties=[{"user_id": "owner-1", "id": "legacy", "kind": "supplier"}])


@pytest.mark.asyncio
async def test_opening_picker_only_canonical_active_suppliers():
    db = database()
    assert [r["id"] for r in await identities(db, "owner-1", "supplier")] == ["canonical"]
    supplier = await require_supplier_v2(db, "owner-1", "canonical")
    assert supplier["source"] == "mezan_suppliers_v2"
    assert supplier["entity_id"] == "canonical"


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["legacy", "other", "archived", " canonical", "missing"])
async def test_identity_rejects_legacy_wrong_owner_inactive_and_aliases(key):
    with pytest.raises(HTTPException) as error:
        await require_supplier_v2(database(), "owner-1", key)
    assert error.value.detail["code"] == "supplier_v2_identity_required"


@pytest.mark.asyncio
async def test_opening_coverage_requires_payable_and_advance_independently():
    db = database()
    def line(category):
        return {"entity_type": "supplier", "entity_id": "canonical", "financial_account_id": None,
                "category": category}
    compiled = {"lines": [line("supplier_payable")], "section_evidence_file_ids": {}}
    with pytest.raises(HTTPException) as error:
        await verify_mappings(db, "owner-1", compiled, [])
    assert error.value.detail["code"] == "onboarding_entity_balance_required"
    compiled["lines"].append(line("supplier_advance"))
    mappings = await verify_mappings(db, "owner-1", compiled, [])
    assert all(row["id"] == "canonical" for row in mappings)


async def payment(db, supplier_id):
    return await _atomic(db, lambda session: post_journal_v2(
        db, user_id="owner-1", actor_id="owner-1", actor_name="Owner",
        idempotency_key="supplier-payment", txn_type="supplier_payment_v2", source="supplier_payments_v2",
        effective_at="2026-09-13T12:00:00Z", notes="Payment", metadata={},
        entries=[
            {"leg_key": "supplier", "entity_type": "supplier", "entity_id": supplier_id, "sub_account": "payable",
             "side": "debit", "amount": "10.00", "entry_type": "supplier_payment"},
            {"leg_key": "bank", "entity_type": "bank", "entity_id": "canonical-bank", "sub_account": "main",
             "side": "credit", "amount": "10.00", "entry_type": "supplier_payment"},
        ], mongo_session=session))


@pytest.mark.asyncio
async def test_native_sink_rejects_legacy_supplier_without_any_payment_write():
    db = database()
    await _open(db)
    before = len(db.rows["accounting_general_ledger_v2"])
    with pytest.raises(HTTPException) as error:
        await payment(db, "legacy")
    assert error.value.detail["code"] == "supplier_v2_identity_required"
    assert len(db.rows["accounting_general_ledger_v2"]) == before


@pytest.mark.asyncio
async def test_native_supplier_payment_and_archived_canonical_reversal():
    db = database()
    await _open(db)
    posted = await payment(db, "canonical")
    db.rows["mezan_suppliers_v2"][0]["status"] = "inactive"
    result = await _atomic(db, lambda session: reverse_journal_v2(
        db, user_id="owner-1", actor_id="owner-1", actor_name="Owner",
        original_txn_group_id=posted["group"]["txn_group_id"],
        reason="Correct payment", effective_at="2026-09-14T12:00:00Z", mongo_session=session))
    assert result["group"]["txn_type"] == "reversal"
    assert {r["entity_id"] for r in result["entries"] if r["entity_type"] == "supplier"} == {"canonical"}
