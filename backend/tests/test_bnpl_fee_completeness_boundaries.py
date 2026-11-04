"""Unresolved fee terms remain visible across BNPL API and generation consumers."""
from copy import deepcopy
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from mongomock_motor import AsyncMongoMockClient

from bnpl import matching_service, settlements_routes
import settlement_engine_generation as generation


def period_row(*, complete=False, invoice_no=1, gross=40):
    return {
        "invoice_no": invoice_no,
        "from": f"2026-06-{1 + (invoice_no - 1) * 7:02d}",
        "to": f"2026-06-{invoice_no * 7:02d}",
        "transactions_count": 1,
        "refunds_count": 0,
        "gross_sales": gross,
        "total_refunds": 0,
        "net_sales": gross,
        "commission": 2 if complete else None,
        "commission_vat": 0.34 if complete else None,
        "settlement_fee": 0,
        "settlement_fee_vat": 0,
        "net_payable": round(gross - 2.34, 2) if complete else None,
        "transferred_amount": 5,
        "remaining_with_provider": round(gross - 7.34, 2) if complete else None,
        "fees_complete": complete,
        "fee_review_reasons": [] if complete else ["provider_payment_plan_required"],
    }


async def request(db, path):
    async def owner():
        return {"id": "owner"}

    app, router = FastAPI(), APIRouter()
    settlements_routes.attach_bnpl_settlements_routes(router, db=db, get_current_user=owner)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline.test") as client:
        return await client.get(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [False, True])
async def test_weekly_endpoint_preserves_unknown_totals_and_known_amounts(monkeypatch, complete):
    rows = [period_row(complete=complete), period_row(complete=True, invoice_no=2, gross=50)]
    monkeypatch.setattr(settlements_routes, "compute_weekly_settlements", AsyncMock(return_value=rows))
    response = await request(object(), "/bnpl/settlements/weekly/tabby")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["rows"] == rows
    assert data["totals"]["gross_sales"] == 90
    assert data["totals"]["transferred_amount"] == 10
    assert data["totals"]["invoices_count"] == 2
    assert data["totals"]["commission"] == (4 if complete else None)
    assert data["totals"]["net_payable"] == (85.32 if complete else None)
    assert data["fees_complete"] is complete


@pytest.mark.asyncio
async def test_registration_overview_keeps_unknown_estimate_and_actual_receipts(monkeypatch):
    db = AsyncMongoMockClient().boundaries
    row = period_row()
    monkeypatch.setattr(settlements_routes, "compute_all_settlements", AsyncMock(return_value={"providers": [{
        "provider": "tabby", "totals": row, "fees_complete": False,
        "fee_review_reasons": row["fee_review_reasons"],
    }]}))
    monkeypatch.setattr("ledger_core.compute_balance", AsyncMock(return_value={"net_balance": 7}))
    await db.general_ledger.insert_one({"user_id": "owner", "entry_type": "bnpl_settlement", "status": "posted",
        "side": "credit", "entity_type": "payment_gateway", "entity_id": "tabby", "amount": 5})
    before = await db.general_ledger.find({}).to_list(10)
    response = await request(db, "/bnpl/settlements/registration-overview")
    assert response.status_code == 200
    result = next(item for item in response.json()["providers"] if item["provider"] == "tabby")
    assert result["expected_total"] is None and result["difference"] is None
    assert result["match_status"] == "review" and result["fees_complete"] is False
    assert (result["gross_sales"], result["current_receivable"], result["received_total"]) == (40, 7, 5)
    assert await db.general_ledger.find({}).to_list(10) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [False, True])
async def test_import_preview_blocks_unknown_fees_without_zero_prefill(monkeypatch, complete):
    db = AsyncMongoMockClient().boundaries
    row = period_row(complete=complete)
    source = {"provider": "tabby", "totals": row, "fees_complete": complete,
        "fee_review_reasons": row["fee_review_reasons"], "bank": {"transferred_amount": 5}}
    monkeypatch.setattr(settlements_routes, "compute_settlement_for_provider", AsyncMock(return_value=source))
    response = await request(db, "/bnpl/settlements/import-preview/tabby?date_from=2026-06-01&date_to=2026-06-07")
    if complete:
        assert response.status_code == 200
        assert response.json()["prefill"]["transferred_amount"] == 37.66
        assert response.json()["prefill"]["commission"] == 2
    else:
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "provider_fees_incomplete"
        assert detail["breakdown"]["gross_sales"] == 40
        assert detail["breakdown"]["net_payable"] is None
        assert detail["bank_reconciliation"]["transferred_amount"] == 5
        assert "prefill" not in detail


@pytest.mark.asyncio
async def test_reconciliation_never_calls_missing_fees_a_zero_matched_balance(monkeypatch):
    db = AsyncMongoMockClient().boundaries
    row = period_row()
    monkeypatch.setattr(settlements_routes, "compute_settlement_for_provider", AsyncMock(return_value={
        "totals": row, "fees_complete": False, "fee_review_reasons": row["fee_review_reasons"],
    }))
    response = await request(db, "/bnpl/settlements/reconciliation")
    assert response.status_code == 200
    for result in response.json()["rows"]:
        assert result["expected"] is None and result["difference"] is None
        assert result["gross_sales"] == 40
        assert result["actual"] == 0 and result["count"] == 0
        assert result["fees_complete"] is False and result["match_status"] == "review"


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [False, True])
async def test_matching_does_not_match_unknown_net_or_consume_its_transfers(monkeypatch, linked):
    rows = [period_row(), period_row(complete=True, invoice_no=2, gross=50)]
    transfers = [
        {"id": "transfer-a", "amount": 47.66, "transaction_date": "2026-06-15"},
        {"id": "transfer-b", "amount": 9, "transaction_date": "2026-06-15"},
    ]
    before = deepcopy(transfers)
    monkeypatch.setattr(matching_service, "compute_weekly_settlements", AsyncMock(return_value=rows))
    monkeypatch.setattr(matching_service, "_find_provider_account", AsyncMock(return_value={"id": "wallet"} if linked else None))
    fetch = AsyncMock(return_value=transfers)
    monkeypatch.setattr(matching_service, "_fetch_provider_out_transfers", fetch)
    result = await matching_service.compute_matches_for_provider(object(), "owner", "tabby")
    assert result["fees_complete"] is False
    assert result["totals"]["unmatched_invoice_total"] is None
    if linked:
        assert result["invoices"][0]["net_payable"] is None
        assert result["invoices"][0]["match_status"] == "review"
        assert result["invoices"][0]["matched_transfer"] is None
        assert result["invoices"][1]["matched_transfer"]["id"] == "transfer-a"
        assert result["totals"]["matched_amount"] == 47.66
        assert result["totals"]["unmatched_transfer_total"] == 9
    else:
        fetch.assert_not_awaited()
    assert transfers == before


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [False, True])
async def test_generation_skips_unresolved_period_before_any_invoice_write(monkeypatch, dry_run):
    db = AsyncMongoMockClient().boundaries
    rows = [period_row(), period_row(complete=True, invoice_no=2, gross=50)]
    monkeypatch.setattr(generation, "_build_bnpl_periods", AsyncMock(return_value=(rows, {})))
    monkeypatch.setattr(generation, "_resolve_default_bank", AsyncMock(return_value=(None, None)))
    result = await generation.generate_for_provider(db, "owner", {"id": "owner"}, "tabby", None, None, dry_run=dry_run)
    assert result["fees_complete"] is False
    assert result["counts"]["skipped_incomplete_fees"] == 1
    assert result["counts"]["invoices_new"] == 1
    assert result["review_periods"][0]["gross_sales"] == 40
    assert result["review_periods"][0]["net_payable"] is None
    for collection in ("settlement_periods", "settlement_invoices", "expected_transfers"):
        assert await db[collection].count_documents({}) == (0 if dry_run else 1)
        assert await db[collection].count_documents({"period_from": "2026-06-01"}) == 0
    assert await db.general_ledger.count_documents({}) == 0
    assert await db.account_transactions.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["salla", "emkan"])
async def test_generation_accepts_complete_official_salla_and_emkan_amounts(provider):
    db = AsyncMongoMockClient().boundaries
    if provider == "salla":
        await db.settlement_entries.insert_one({"user_id": "owner", "provider": provider,
            "settlement_reference": "report-example", "settlement_date": "2026-06-07",
            "actual_gross_amount": 40, "actual_refund_amount": 0, "actual_payment_fee": 2,
            "actual_payment_vat": 0.34, "actual_net_amount": 37.66})
    else:
        await db.settlement_files.insert_one({"id": "report-example", "user_id": "owner", "provider": provider,
            "header": {"statement_id": "statement-example", "statement_date": "2026-06-07"},
            "totals": {"transactions_count": 1, "gross": 40, "fees": 2, "fees_vat": 0.34, "net": 37.66}})
    result = await generation.generate_for_provider(db, "owner", {"id": "owner"}, provider, "2026-06-01", "2026-06-30")
    assert result["fees_complete"] is True and result["review_periods"] == []
    assert result["counts"]["invoices_new"] == 1
    invoice = await db.settlement_invoices.find_one({"user_id": "owner"})
    assert invoice["gross_sales"] == 40 and invoice["commission_vat"] == 0.34
    assert invoice["expected_transfer_amount"] == 37.66
