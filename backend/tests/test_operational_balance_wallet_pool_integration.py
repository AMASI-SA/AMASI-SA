"""Real loopback Mongo payment/CAS acceptance for the operational wallet pool."""
import asyncio

import pytest
from fastapi import HTTPException

from operational_ad_funding import save
from operational_balance_engine import reconcile
from operational_balance_service import create_movement, report, save_opening
from operational_balance_store import mutate, read
from test_operational_balance_integration import run, opening, movement
from test_operational_balance_wallet_pool import START, NOW, snapshot


async def setup(db, wallet="50", debt="0"):
    await db.mezan_integration_accounts_v2.insert_one({"user_id": "owner",
        "mezan_integration_account_id": "wallet-ia", "provider": "meta_ads",
        "external_account_id": "wallet-external", "display_name": "محفظة تشغيلية",
        "connection_status": "connected", "currency": "SAR", "timezone": "UTC", "mezan_selected": True})
    configured = await save(db, "owner", "owner", {"request_id": "wallet-config", "version": 0,
        "integration_account_id": "wallet-ia", "mode": "wallet_first", "currency": "SAR",
        "evidence": "Owner-approved wallet-first operational contract"})
    account = configured["party_id"]
    await save_opening(db, "owner", "owner", opening(), clock=START)
    await save_opening(db, "owner", "owner", opening("ad_account", account, wallet, "for_us", "wallet-opening"), clock=START)
    await save_opening(db, "owner", "owner", opening("ad_account", account, debt, "for_party", "debt-opening"), clock=START)
    await save_opening(db, "owner", "owner", {"request_id": "wallet-start"}, finish=True, clock=START)
    return account


async def spend(db, account, amount):
    async def apply(state):
        source = snapshot(amount)
        source["account_id"] = account
        result = reconcile(state, {"ad_snapshots": [source]}, NOW)
        state.clear()
        state.update(result)
        return None
    await mutate(db, "owner", apply)


def payment(account, amount="100", request="wallet-payment", **changes):
    return {**movement(), "party_type": "ad_account", "party_id": account,
            "amount": amount, "request_id": request, **changes}


async def assert_result(db, account, wallet, debt, cash, count):
    state = await read(db, "owner")
    result = report(state)
    row = next(r for r in result["parties"] if r["party_id"] == account)
    assert (row["ad_wallet_balance"], row["ad_payable"]) == (wallet, debt)
    assert result["summary"]["actual_liquidity"] == cash
    assert len(state["movements"]) == count
    for collection in ("accounting_journal_groups_v2", "accounting_general_ledger_v2",
                       "general_ledger", "mz2_inventory_cost_states"):
        assert await db[collection].count_documents({}) == 0
    return state


@pytest.mark.parametrize("kind", ["payment", "wallet_funding"])
def test_payment_replay_debt_first_and_single_bank_effect(kind):
    async def scenario(db):
        account = await setup(db)
        await spend(db, account, "80")
        payload = payment(account, kind=kind)
        replies = await asyncio.gather(*[create_movement(db, "owner", "owner", payload, clock=NOW) for _ in range(2)])
        assert replies[0] == replies[1]
        assert await create_movement(db, "owner", "owner", payload, clock=NOW) == replies[0]
        result = await assert_result(db, account, "70.00", "0.00", "900.00", 1)
        assert result["movements"][0]["wallet_first_allocation"] == {
            "debt_settled": "30.00", "wallet_funded": "70.00", "basis": "confirmed_balance_at_recording"}
        assert result["movements"][0]["allocations"] == []
    run(scenario)


def test_late_spend_and_concurrent_distinct_payments_conserve_cash():
    async def scenario(db):
        account = await setup(db, "0", "100")
        await asyncio.gather(*[create_movement(db, "owner", "owner", payment(account, request=key), clock=NOW)
                               for key in ("first", "second")])
        before = await assert_result(db, account, "100.00", "0.00", "800.00", 2)
        await spend(db, account, "180")
        after = await assert_result(db, account, "0.00", "80.00", "800.00", 2)
        assert before["movements"] == after["movements"]
        assert sorted(m["wallet_first_allocation"]["debt_settled"] for m in after["movements"]) == ["0.00", "100.00"]
    run(scenario)


@pytest.mark.parametrize("change,code", [
    ({"kind": "correction", "bank_id": None}, "operational_wallet_movement_unsupported"),
    ({"kind": "refund", "direction": "incoming"}, "operational_wallet_movement_unsupported"),
    ({"kind": "settlement"}, "operational_wallet_movement_unsupported"),
    ({"allocations": [{"obligation_id": "old-day", "amount": "1"}]}, "operational_wallet_pool_allocation_unsupported"),
])
def test_unsupported_wallet_changes_are_definitively_rejected_without_cash(change, code):
    async def scenario(db):
        account = await setup(db)
        payload = payment(account, **change)
        for _ in range(2):
            with pytest.raises(HTTPException) as error:
                await create_movement(db, "owner", "owner", payload, clock=NOW)
            assert error.value.detail["code"] == code and error.value.detail["not_applied"] is True
        await assert_result(db, account, "50.00", "0.00", "1000.00", 0)
    run(scenario)
