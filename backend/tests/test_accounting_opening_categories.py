"""Economic opening classifications through the production compile contract.

All database objects in this module are in-memory test doubles. No live ledger,
opening approval, or writer transition is performed.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
from pydantic import ValidationError

from accounting_financial_accounts import (
    EVIDENCE_SECTION_IDS,
    OpeningDraftCreate,
    OpeningLine,
    _compile_opening,
)
import accounting_mz2_reports as reports


OWNER = "synthetic-opening-owner"


def line(category: str, *, entity: str = "synthetic-entity", **overrides) -> dict:
    section = "suppliers" if category in {"supplier_advance", "supplier_payable"} else "equity"
    payload = {
        "category": category,
        "entity_id": entity,
        "meaning": "owed_by_us" if category in {"supplier_payable", "accrued_expense"} else "available_to_us",
        "original_amount": "120.00",
        "original_currency": "SAR",
        "fx_rate_to_sar": "1",
        "evidence_file_id": f"evidence-{section}",
    }
    return {**payload, **overrides}


def draft(*lines: dict) -> OpeningDraftCreate:
    return OpeningDraftCreate.model_validate({
        "idempotency_key": "economic-opening-draft-v1",
        "cutover_at": "2026-10-01T00:00:00+03:00",
        "cutover_timezone": "Asia/Riyadh",
        "cutover_evidence_file_id": "evidence-cutover",
        "section_evidence_file_ids": {
            section: f"evidence-{section}" for section in EVIDENCE_SECTION_IDS
        },
        "lines": list(lines),
    })


@pytest_asyncio.fixture
async def db():
    database = AsyncMongoMockClient()["isolated_opening_categories"]
    await database.mezan_suppliers_v2.insert_many([
        {"user_id": OWNER, "id": identity, "status": "active"}
        for identity in ("supplier-1", "synthetic-entity")
    ])
    return database


@pytest.mark.asyncio
async def test_supplier_advance_and_payable_are_separate_without_netting(db):
    compiled = await _compile_opening(db, owner=OWNER, payload=draft(
        line("supplier_advance", entity="supplier-1", original_amount="300.00"),
        line("supplier_payable", entity="supplier-1", original_amount="900.00"),
    ))
    supplier = [row for row in compiled["preview_entries"] if row["entity_type"] == "supplier"]
    assert [(row["entity_id"], row["sub_account"], row["side"], row["sar_amount"]) for row in supplier] == [
        ("supplier-1", "advance", "debit", "300.00"),
        ("supplier-1", "payable", "credit", "900.00"),
    ]
    assert compiled["debit_total"] == compiled["credit_total"] == "900.00"
    assert await db.list_collection_names() == ["mezan_suppliers_v2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["supplier_payable", "supplier_advance"])
@pytest.mark.parametrize("zero", [False, True])
async def test_direct_supplier_opening_including_zero_rejects_legacy_identity(db, category, zero):
    await db.suppliers.insert_one({"user_id": OWNER, "id": "legacy-only"})
    await db.counterparties.insert_one({"user_id": OWNER, "id": "legacy-only", "kind": "supplier"})
    overrides = {"meaning": "zero", "original_amount": "0.00"} if zero else {}
    with pytest.raises(HTTPException) as error:
        await _compile_opening(db, owner=OWNER, payload=draft(line(category, entity="legacy-only", **overrides)))
    assert error.value.detail["code"] == "supplier_v2_identity_required"
    compiled = await _compile_opening(db, owner=OWNER, payload=draft(line(category, entity="supplier-1", **overrides)))
    assert compiled["lines"][0]["entity_id"] == "supplier-1"
    assert bool(compiled["zero_accounts"]) == zero


@pytest.mark.asyncio
async def test_prepaid_asset_and_accrued_liability_are_not_current_expense(db):
    compiled = await _compile_opening(db, owner=OWNER, payload=draft(
        line("prepaid_expense", entity="annual-rent", original_amount="1200.00"),
        line("accrued_expense", entity="unpaid-utilities", original_amount="450.00"),
    ))
    assert [(row["category"], row["entity_type"], row["sub_account"], row["side"], row["sar_amount"])
            for row in compiled["lines"]] == [
        ("prepaid_expense", "asset", "prepaid_expense", "debit", "1200.00"),
        ("accrued_expense", "liability", "accrued_expense", "credit", "450.00"),
    ]
    assert all(row["entity_type"] != "expense" for row in compiled["preview_entries"])
    assert compiled["debit_total"] == compiled["credit_total"] == "1200.00"
    assert await db.list_collection_names() == ["mezan_suppliers_v2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["supplier_advance", "prepaid_expense", "accrued_expense"])
async def test_new_categories_require_explicit_zero_and_create_no_zero_legs(db, category):
    compiled = await _compile_opening(db, owner=OWNER, payload=draft(
        line(category, meaning="zero", original_amount="0.00"),
    ))
    assert compiled["zero_only"] is True
    assert compiled["preview_entries"] == []
    assert len(compiled["zero_accounts"]) == 1
    assert compiled["lines"][0]["meaning"] == "zero"
    assert compiled["lines"][0]["original_amount"] == "0.00"

    missing = line(category)
    missing.pop("original_amount")
    with pytest.raises(ValidationError):
        OpeningLine.model_validate(missing)
    with pytest.raises(ValidationError):
        OpeningLine.model_validate(line(category, original_amount="0.00"))
    with pytest.raises(ValidationError):
        OpeningLine.model_validate(line(category, meaning="zero", original_amount="1.00"))


@pytest.mark.asyncio
@pytest.mark.parametrize("category,wrong_meaning", [
    ("supplier_advance", "owed_by_us"),
    ("prepaid_expense", "owed_by_us"),
    ("accrued_expense", "available_to_us"),
])
async def test_new_category_debit_credit_meaning_mismatch_is_rejected(db, category, wrong_meaning):
    with pytest.raises(HTTPException) as error:
        await _compile_opening(db, owner=OWNER, payload=draft(line(category, meaning=wrong_meaning)))
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "opening_accounting_meaning_mismatch"
    assert await db.list_collection_names() == ["mezan_suppliers_v2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["supplier_advance", "prepaid_expense", "accrued_expense"])
async def test_duplicate_economic_identity_is_rejected(db, category):
    with pytest.raises(HTTPException) as error:
        await _compile_opening(db, owner=OWNER, payload=draft(line(category), line(category)))
    assert error.value.detail["code"] == "opening_duplicate_account"


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["supplier_advance", "prepaid_expense", "accrued_expense"])
async def test_new_categories_require_matching_section_evidence(db, category):
    with pytest.raises(HTTPException) as error:
        await _compile_opening(db, owner=OWNER, payload=draft(
            line(category, evidence_file_id="evidence-unrelated"),
        ))
    assert error.value.detail["code"] == "opening_line_evidence_section_mismatch"


@pytest.mark.asyncio
async def test_supplier_advance_preserves_fx_snapshot_without_netting(db):
    compiled = await _compile_opening(db, owner=OWNER, payload=draft(
        line("supplier_advance", entity="supplier-1", original_amount="80.00",
             original_currency="USD", fx_rate_to_sar="3.75",
             fx_at="2026-09-30T21:00:00Z", fx_source="reviewed synthetic FX evidence"),
        line("supplier_payable", entity="supplier-1", original_amount="300.00"),
    ))
    advance = compiled["lines"][0]
    assert advance["original_amount"] == "80.00"
    assert advance["original_currency"] == "USD"
    assert advance["sar_amount"] == "300.00"
    assert advance["fx_snapshot"]["rate_to_sar"] == "3.75"
    assert advance["fx_snapshot"]["source"] == "reviewed synthetic FX evidence"
    assert len(compiled["preview_entries"]) == 2
    assert compiled["debit_total"] == compiled["credit_total"] == "300.00"


@pytest.mark.asyncio
async def test_financial_position_exposes_supplier_asset_and_liability_separately(db, monkeypatch):
    compiled = await _compile_opening(db, owner=OWNER, payload=draft(
        line("supplier_advance", entity="supplier-1", original_amount="300.00"),
        line("supplier_payable", entity="supplier-1", original_amount="900.00"),
        line("prepaid_expense", entity="annual-rent", original_amount="1200.00"),
        line("accrued_expense", entity="unpaid-utilities", original_amount="450.00"),
    ))
    await db.mezan_suppliers_v2.insert_one({"user_id": OWNER, "id": "supplier-1", "status": "active"})
    await db.mz2_prepaid_selections_v2.insert_one({"user_id": OWNER, "id": "annual-rent",
        "entity_id": "annual-rent", "entity_type": "asset", "sub_account": "prepaid_expense", "status": "active"})
    await db.mz2_opening_facts_v2.insert_one({"user_id": OWNER, "id": "unpaid-utilities",
        "entity_id": "unpaid-utilities", "entity_type": "liability", "sub_account": "accrued_expense", "status": "active"})
    # The trusted ledger reader is the I/O boundary; exercise the report's real
    # account classifiers using the real compiler's output, without posting.
    reader = AsyncMock(return_value={
        "status": "available",
        "legacy_financial_data_included": False,
        "opening_balance_txn_group_id": "synthetic-native-opening",
        "items": [{**row, "amount": row["sar_amount"]} for row in compiled["preview_entries"]],
    })
    monkeypatch.setattr(reports, "read_mz2_ledger", reader)
    result = await reports.mz2_financial_position(db, owner=OWNER)
    reader.assert_awaited_once_with(db, owner=OWNER, as_of=None)
    assert result["assets"]["supplier_advance"] == 300.0
    assert result["assets"]["prepaid_expense"] == 1200.0
    assert result["liabilities"]["supplier_payable"] == 900.0
    assert result["liabilities"]["accrued_expense"] == 450.0
    assert result["totals"] == {
        "total_assets": 1500.0, "total_liabilities": 1350.0, "net_position": 150.0,
    }
