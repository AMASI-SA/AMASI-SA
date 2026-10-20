"""Owner payment choice: wallet funding never silently settles existing debt."""
import asyncio

import pytest
from fastapi import HTTPException
from operational_balance_service import create_movement, report, save_opening
from operational_balance_store import read
from test_operational_balance_integration import START, movement, opening, run, seed_ad_account


@pytest.mark.parametrize("payment_kind", ["payment", "settlement"])
def test_tiktok_wallet_funding_and_debt_settlement_are_distinct_and_replay_safe(payment_kind):
    async def scenario(db):
        await seed_ad_account(db)
        await db.mezan_integration_accounts_v2.update_one(
            {"mezan_integration_account_id": "ia"},
            {"$set": {"provider": "tiktok_ads"}})
        await db.mz2_ad_account_bindings_v2.update_one(
            {"_id": "ad"},
            {"$set": {"platform": "tiktok", "funding_mode": "hybrid",
                      "hybrid_policy": "explicit_split", "version": 2}})
        # Existing source contract is retained for this payment-only test.
        # This does not claim wallet-depletion allocation is implemented.
        await db.mz2_ad_automation_policies_v2.insert_one({
            "user_id": "owner", "id": "synthetic-payment-policy",
            "platform": "tiktok", "integration_account_id": "ia",
            "version": 1, "binding_version": 2, "status": "active",
            "confirmed_by": "owner", "confirmed_at": START,
            "start_date": "2026-10-01", "wallet_fraction": "0"})
        await save_opening(db, "owner", "owner", opening(), clock=START)
        await save_opening(db, "owner", "owner",
                          opening("ad_account", "ad", "100", "for_party", "ad-debt"),
                          clock=START)
        await save_opening(db, "owner", "owner", {"request_id": "start-hybrid"},
                          finish=True, clock=START)
        overpayment = {**movement(), "kind": "payment", "party_type": "ad_account",
                       "party_id": "ad", "amount": "200", "request_id": "not-wallet-funding"}
        for _ in range(2):
            with pytest.raises(HTTPException) as error:
                await create_movement(db, "owner", "owner", overpayment)
            assert error.value.detail["code"] == "operational_ad_payment_exceeds_payable"
            assert error.value.detail["not_applied"] is True
        assert (await read(db, "owner"))["movements"] == []
        funding = {**movement(), "kind": "wallet_funding", "party_type": "ad_account",
                   "party_id": "ad", "amount": "200", "request_id": "fund-choice"}
        replies = await asyncio.gather(*[
            create_movement(db, "owner", "owner", funding) for _ in range(2)])
        assert replies[0] == replies[1]
        state = await read(db, "owner")
        after_funding = report(state)
        ad = next(row for row in after_funding["parties"] if row["party_type"] == "ad_account")
        assert (ad["ad_wallet_balance"], ad["ad_payable"]) == ("200.00", "100.00")
        assert after_funding["summary"]["actual_liquidity"] == "800.00"
        assert len(state["movements"]) == 1 and state["movements"][0]["allocations"] == []

        debt_id = next(key for key, row in state["openings"].items()
                       if row["party_type"] == "ad_account")
        settlement = {**movement(), "kind": payment_kind, "party_type": "ad_account",
                      "party_id": "ad", "amount": "100", "request_id": "settle-choice",
                      "allocations": [{"obligation_id": debt_id, "amount": "100"}]}
        if payment_kind == "payment":
            settlement["allocations"] = []
        first = await create_movement(db, "owner", "owner", settlement)
        assert await create_movement(db, "owner", "owner", settlement) == first
        final_state = await read(db, "owner")
        final = report(final_state)
        ad = next(row for row in final["parties"] if row["party_type"] == "ad_account")
        assert (ad["ad_wallet_balance"], ad["ad_payable"]) == ("200.00", "0.00")
        assert final["summary"]["actual_liquidity"] == "700.00"
        assert len(final_state["movements"]) == 2
        with pytest.raises(HTTPException) as no_debt:
            await create_movement(db, "owner", "owner",
                                  {**settlement, "request_id": "no-debt-left",
                                   "amount": "1", "allocations": []})
        assert no_debt.value.detail["code"] == "operational_ad_payment_exceeds_payable"
        unchanged = report(await read(db, "owner"))
        assert unchanged["summary"]["actual_liquidity"] == "700.00"
        assert next(row for row in unchanged["parties"] if row["party_type"] == "ad_account")["ad_wallet_balance"] == "200.00"
        for collection in ("accounting_journal_groups_v2", "accounting_general_ledger_v2",
                           "general_ledger", "mz2_inventory_cost_states"):
            assert await db[collection].count_documents({}) == 0
    run(scenario)
