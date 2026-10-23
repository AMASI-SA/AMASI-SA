"""Latest owner rule: hybrid payments settle debt, then fund the wallet once."""
import asyncio

import pytest
from fastapi import HTTPException

from operational_balance_service import create_movement, report, save_opening
from operational_balance_store import read
from test_operational_balance_integration import START, movement, opening, run, seed_ad_account


async def setup(db, debt):
    await seed_ad_account(db)
    await db.mezan_integration_accounts_v2.update_one(
        {"mezan_integration_account_id": "ia"}, {"$set": {"provider": "tiktok_ads"}})
    await db.mz2_ad_account_bindings_v2.update_one(
        {"_id": "ad"}, {"$set": {"platform": "tiktok", "funding_mode": "hybrid",
                               "hybrid_policy": "explicit_split", "version": 2}})
    # Payment semantics only; no claim of automatic wallet-depletion support.
    await db.mz2_ad_automation_policies_v2.insert_one({
        "user_id": "owner", "id": "synthetic-payment-policy", "platform": "tiktok",
        "integration_account_id": "ia", "version": 1, "binding_version": 2,
        "status": "active", "confirmed_by": "owner", "confirmed_at": START,
        "start_date": "2026-10-01", "wallet_fraction": "0"})
    await save_opening(db, "owner", "owner", opening(), clock=START)
    await save_opening(db, "owner", "owner",
                      opening("ad_account", "ad", debt, "for_party", "ad-debt"), clock=START)
    await save_opening(db, "owner", "owner", {"request_id": "start-hybrid"},
                      finish=True, clock=START)


def payment(amount, request_id="ad-payment", **changes):
    return {**movement(), "kind": "payment", "party_type": "ad_account",
            "party_id": "ad", "amount": amount, "request_id": request_id, **changes}


async def assert_balances(db, wallet, debt, bank, count):
    state = await read(db, "owner")
    result = report(state)
    ad = next(row for row in result["parties"] if row["party_type"] == "ad_account")
    assert (ad["ad_wallet_balance"], ad["ad_payable"]) == (wallet, debt)
    assert result["summary"]["actual_liquidity"] == bank
    assert len(state["movements"]) == count
    for collection in ("accounting_journal_groups_v2", "accounting_general_ledger_v2",
                       "general_ledger", "mz2_inventory_cost_states"):
        assert await db[collection].count_documents({}) == 0
    return state


@pytest.mark.parametrize("debt,amount,wallet,remaining,bank", [
    ("100", "200", "100.00", "0.00", "800.00"),
    ("100", "100", "0.00", "0.00", "900.00"),
    ("100", "50", "0.00", "50.00", "950.00"),
    ("0", "200", "200.00", "0.00", "800.00"),
])
def test_hybrid_payment_settles_debt_then_funds_excess_once(debt, amount, wallet, remaining, bank):
    async def scenario(db):
        await setup(db, debt)
        payload = payment(amount)
        replies = await asyncio.gather(*[
            create_movement(db, "owner", "owner", payload) for _ in range(2)])
        assert replies[0] == replies[1]
        assert await create_movement(db, "owner", "owner", payload) == replies[0]
        await assert_balances(db, wallet, remaining, bank, 1)
    run(scenario)


def test_partial_explicit_allocation_cannot_fund_wallet_before_debt_is_paid():
    async def scenario(db):
        await setup(db, "100")
        state = await read(db, "owner")
        debt_id = next(key for key, row in state["openings"].items()
                       if row["party_type"] == "ad_account")
        wrong = payment("200", allocations=[{"obligation_id": debt_id, "amount": "50"}])
        for _ in range(2):
            with pytest.raises(HTTPException) as error:
                await create_movement(db, "owner", "owner", wrong)
            assert error.value.detail["code"] == "operational_ad_debt_priority_required"
            assert error.value.detail["not_applied"] is True
        await assert_balances(db, "0.00", "100.00", "1000.00", 0)
        correct = payment("200", "corrected-payment",
                          allocations=[{"obligation_id": debt_id, "amount": "100"}])
        await create_movement(db, "owner", "owner", correct)
        await assert_balances(db, "100.00", "0.00", "800.00", 1)
    run(scenario)


def test_two_concurrent_payments_settle_same_debt_only_once():
    async def scenario(db):
        await setup(db, "100")
        await asyncio.gather(*[create_movement(db, "owner", "owner", payment("100", key))
                               for key in ("payment-one", "payment-two")])
        await assert_balances(db, "100.00", "0.00", "800.00", 2)
    run(scenario)
