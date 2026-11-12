"""Daily report totals and browser/native authority remain independently scoped."""
import asyncio

import pytest
from fastapi import HTTPException

import operational_financial_activity as activity
from test_operational_balance_daily_financial_movements import state_fixture, movement, database, feed, STAFF


def test_currency_totals_cover_all_authorized_rows_before_paging_and_date_filter():
    state = state_fixture()
    state["movements"] = [movement(str(i), amount="10.10", allocations=[]) for i in range(12)]
    state["movements"] += [
        movement("usd-in", amount="7.25", currency="USD", direction="incoming", allocations=[]),
        movement("usd-out", amount="2.15", currency="USD", allocations=[]),
        movement("hidden", amount="9999.00", bank_id="secret", allocations=[]),
        movement("outside", amount="8888.00", occurred_at="2026-11-06T20:59:59Z", allocations=[]),
    ]
    async def scenario():
        db = await database(state)
        kwargs = dict(owner="owner", actor=STAFF, from_date="2026-11-07", to_date="2026-11-07")
        first = await activity.daily_financial_movements(db, **kwargs)
        second = await activity.daily_financial_movements(db, **kwargs, page=2, snapshot=first["snapshot"])
        expected = [
            {"currency": "SAR", "count": 12, "incoming": "0.00", "outgoing": "121.20", "noncash": "0.00", "net_cash": "-121.20"},
            {"currency": "USD", "count": 2, "incoming": "7.25", "outgoing": "2.15", "noncash": "0.00", "net_cash": "5.10"},
        ]
        assert first["total"] == second["total"] == 14
        assert len(first["items"]) == 10 and len(second["items"]) == 4
        assert first["totals_by_currency"] == second["totals_by_currency"] == expected
        assert first["totals_complete"] and not first["coverage"]["complete"]
        assert first["totals_basis"] == "recorded_scoped_movements"
        # Missing historical balances do not obscure totals of actual rows.
        assert all(row["account_balance_after"] is None for row in first["items"])
    asyncio.run(scenario())


def test_noncash_is_separate_from_bank_money_and_transfer_legs_are_scoped():
    assert activity.scoped_currency_totals([
        {"currency": "SAR", "direction": "noncash", "amount": "200.00"},
        {"currency": "SAR", "direction": "incoming", "amount": "150.00"},
    ]) == [{"currency": "SAR", "count": 2, "incoming": "150.00", "outgoing": "0.00", "noncash": "200.00", "net_cash": "150.00"}]
    state = state_fixture()
    state["movements"] = [movement("transfer", kind="transfer", party_type="bank", party_id="secret", allocations=[])]
    async def scenario():
        db = await database(state)
        staff = await activity.daily_financial_movements(db, owner="owner", actor=STAFF,
            from_date="2026-11-07", to_date="2026-11-07")
        owner = await feed(db)
        assert staff["totals_by_currency"][0] == {"currency": "SAR", "count": 1,
            "incoming": "0.00", "outgoing": "100.00", "noncash": "0.00", "net_cash": "-100.00"}
        assert owner["totals_by_currency"][0]["net_cash"] == "0.00"
        assert owner["totals_by_currency"][0]["count"] == 2
    asyncio.run(scenario())


def test_unknown_source_and_known_empty_scope_do_not_fabricate_currency_totals():
    async def scenario():
        db = await database(state_fixture(), assigned=())
        empty = await activity.daily_financial_movements(db, owner="owner", actor=STAFF,
            from_date="2026-11-07", to_date="2026-11-07")
        assert empty["totals_by_currency"] == [] and empty["totals_complete"]
        await db.mz2_atomic_owners.insert_one({"_id": "owner", "ledger_backend_state": "v2_active"})
        unavailable = await feed(db)
        assert unavailable["items"] == [] and unavailable["totals_by_currency"] == []
        assert not unavailable["totals_complete"] and unavailable["financial_source"] == "unavailable"
        inactive = await feed(await database())
        assert inactive["items"] == [] and inactive["totals_by_currency"] == []
        assert not inactive["totals_complete"]
    asyncio.run(scenario())


def test_undated_authorized_rows_leave_date_totals_partial():
    state = state_fixture()
    state["movements"] = [movement("dated"), movement("undated", occurred_at=None)]
    async def scenario():
        result = await feed(await database(state))
        assert result["total"] == 1 and result["totals_by_currency"][0]["outgoing"] == "100.00"
        assert not result["totals_complete"] and result["coverage"]["undated_count"] == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("direction,currency", [("unknown", "SAR"), ("incoming", None)])
def test_invalid_direction_or_currency_does_not_fabricate_totals(direction, currency):
    with pytest.raises(HTTPException) as exc:
        activity.scoped_currency_totals([{"direction": direction, "currency": currency, "amount": "10.00"}])
    assert exc.value.status_code == 503
