"""Cross-currency advertising from a SAR bank is one evidenced money movement.

Uses only the isolated loopback Mongo fixture. No production secrets or writes.
"""
import asyncio
from copy import deepcopy

import pytest
from fastapi import HTTPException

from operational_balance_service import create_movement, report, account_statement, save_opening
from operational_balance_store import read, digest
from operational_balance_sources import entities
from test_operational_balance_integration import run, opening, movement, seed_ad_account, START
from test_operational_balance_app_permissions import client, grant, WRITE, READ


async def setup(db):
    await seed_ad_account(db)
    await db.mezan_integration_accounts_v2.update_one(
        {"mezan_integration_account_id": "ia"}, {"$set": {"currency": "USD"}})
    await save_opening(db, "owner", "owner", opening(), clock=START)
    await save_opening(db, "owner", "owner", {"request_id": "fx-start"}, finish=True, clock=START)


def fx_payment(**changes):
    base = movement()
    base.update(party_type="ad_account", party_id="ad", kind="wallet_funding",
                amount="100.00", currency="USD", direction="outgoing",
                bank_id="bank", source_account_type="bank",
                bank_currency="SAR", bank_amount="380.00",
                bank_fee_amount="5.00", reference="BANK-FX-001",
                request_id="fx-payment-001", allocations=[])
    base.update(changes)
    return base


def test_sar_bank_debit_usd_ad_credit_and_separate_fee_are_atomic():
    async def scenario(db):
        await setup(db)
        payload = fx_payment()
        responses = await asyncio.gather(*[
            create_movement(db, "owner", "owner", payload) for _ in range(3)])
        assert all(r == responses[0] for r in responses)
        assert responses[0]["bank_amount"] == "380.00"
        assert responses[0]["bank_net_amount"] == "375.00"
        assert responses[0]["bank_fee_amount"] == "5.00"
        assert responses[0]["effective_fx_rate_to_sar"] == "3.750000"
        state = await read(db, "owner")
        assert len(state["movements"]) == 1
        snapshot = report(state)
        bank = next(p for p in snapshot["parties"] if p["party_type"] == "bank")
        ad = next(p for p in snapshot["parties"] if p["party_type"] == "ad_account")
        fee = next(p for p in snapshot["parties"]
                   if p["party_id"] == "advertising_bank_fx_fee")
        assert bank["currency"] == "SAR" and bank["actual"] == "620.00"
        assert ad["currency"] == "USD" and ad["outstanding_receivable"] == "100.00"
        assert fee["currency"] == "SAR" and fee["expense_paid"] == "5.00"
        assert snapshot["summaries"]["SAR"]["actual_liquidity"] == "620.00"
        assert snapshot["summaries"]["SAR"]["operating_expenses_paid"] == "5.00"
        statement = account_statement(state, next(r for r in await entities(db, "owner", "bank") if r["id"] == "bank"))
        assert len(statement["items"]) == 1
        assert statement["items"][0]["amount"] == "380.00"
        assert statement["items"][0]["currency"] == "SAR"
        assert statement["items"][0]["ad_amount"] == "100.00"
        assert statement["items"][0]["ad_currency"] == "USD"
        for collection in ("accounting_general_ledger_v2", "accounting_journal_groups_v2", "general_ledger"):
            assert await db[collection].count_documents({}) == 0
    run(scenario)



@pytest.mark.parametrize("usd,gross,fee,net,bank_left,rate", [
    ("100.50", "381.25", "4.38", "376.87", "618.75", "3.749950"),
    ("0.01", "0.05", "0.01", "0.04", "999.95", "4.000000"),
])
def test_fractional_usd_and_sar_fees_are_exact_to_cent(usd, gross, fee, net, bank_left, rate):
    async def scenario(db):
        await setup(db)
        payload = fx_payment(amount=usd, bank_amount=gross, bank_fee_amount=fee)
        result = await create_movement(db, "owner", "owner", payload)
        assert result["amount"] == usd
        assert result["bank_amount"] == gross
        assert result["bank_fee_amount"] == fee
        assert result["bank_net_amount"] == net
        assert result["effective_fx_rate_to_sar"] == rate
        await create_movement(db, "owner", "owner", payload)
        data = await read(db, "owner")
        assert len(data["movements"]) == 1
        report_data = report(data)
        bank = next(row for row in report_data["parties"] if row["party_type"] == "bank")
        ad = next(row for row in report_data["parties"] if row["party_type"] == "ad_account")
        fees = next(row for row in report_data["parties"] if row["party_id"] == "advertising_bank_fx_fee")
        assert bank["actual"] == bank_left
        assert ad["outstanding_receivable"] == usd
        assert fees["expense_paid"] == fee
        assert report_data["summaries"]["SAR"]["actual_liquidity"] == bank_left
    run(scenario)


@pytest.mark.parametrize("update", [
    {"bank_currency": None},
    {"bank_amount": None},
    {"bank_fee_amount": None},
    {"bank_amount": "4.00", "bank_fee_amount": "5.00"},
    {"bank_amount": "999.00"},
    {"currency": "SAR"},
    {"party_type": "supplier", "party_id": "supplier"},
    {"source_account_type": "cash", "bank_id": "cash"},
    {"direction": "incoming", "kind": "collection"},
    {"reference": ""},
    {"bank_fee_amount": "-1.00"},
])
def test_invalid_fx_request_never_moves_any_balance(update):
    async def scenario(db):
        await setup(db)
        payload = fx_payment(**update)
        with pytest.raises(HTTPException):
            await create_movement(db, "owner", "owner", payload)
        assert (await read(db, "owner"))["movements"] == []
        assert report(await read(db, "owner"))["summaries"]["SAR"]["actual_liquidity"] == "1000.00"
    run(scenario)


def test_duplicate_bank_reference_cannot_register_same_debit_twice():
    async def scenario(db):
        await setup(db)
        await create_movement(db, "owner", "owner", fx_payment())
        with pytest.raises(HTTPException):
            await create_movement(db, "owner", "owner",
                fx_payment(request_id="new-mobile-request", amount="100.00"))
        assert len((await read(db, "owner"))["movements"]) == 1
    run(scenario)


def test_owner_mobile_endpoint_enforces_assigned_sar_bank_and_fx_fields():
    async def scenario(db):
        await setup(db)
        await grant(db, [WRITE, READ])
        payload = {**fx_payment(), "expected_session_scope": digest(["owner", "staff"])}
        async with client(db, "staff") as c:
            response = await c.post("/api/operational-balances/movements", json=payload)
            assert response.status_code == 200, response.text
            again = await c.post("/api/operational-balances/movements", json=payload)
            assert again.status_code == 200 and again.json() == response.json()
            rejected = await c.post("/api/operational-balances/movements",
                json={**payload, "request_id": "same-charge-other-bank", "bank_id": "not-assigned",
                      "reference": "BANK-FX-002"})
            assert rejected.status_code in (403, 409), rejected.text
        assert len((await read(db, "owner"))["movements"]) == 1
    run(scenario)
