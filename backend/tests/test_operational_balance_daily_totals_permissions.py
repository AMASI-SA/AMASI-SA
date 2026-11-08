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


def test_browser_daily_grant_is_explicit_scoped_and_independent_of_native_read():
    from test_operational_balance_app_permissions import client
    state = state_fixture()
    state["movements"] = [movement("visible"), movement("hidden", bank_id="secret")]
    async def scenario():
        db = await database(state)
        endpoint = "/api/operational-balances/daily-financial-movements"
        params = {"from_date": "2026-11-07", "to_date": "2026-11-07"}
        async with client(db, mobile=False) as browser:
            assert (await browser.get(endpoint, params=params)).status_code == 403
            await db.users.update_one({"id": "staff"}, {"$set": {"accounting_permissions": ["accounting.movements.view"]}})
            result = await browser.get(endpoint, params=params)
            assert result.status_code == 200, result.text
            assert result.json()["total"] == 1 and result.json()["totals_by_currency"][0]["outgoing"] == "100.00"
            await db.users.update_one({"id": "staff"}, {"$set": {"denied_permissions": ["accounting.movements.view"]}})
            assert (await browser.get(endpoint, params=params)).status_code == 403
            # Native report-read remains independent from the browser grant.
            async with client(db) as mobile:
                assert (await mobile.get(endpoint, params=params)).status_code == 200
            await db.users.update_one({"id": "staff"}, {"$set": {"denied_permissions": [], "accounting_permissions": []}})
            assert (await browser.get(endpoint, params=params)).status_code == 403
    asyncio.run(scenario())


def test_browser_read_permission_revocation_before_response_blocks_result(monkeypatch):
    from test_operational_balance_app_permissions import client
    original = activity._actor_names
    async def revoke(db, owner, rows):
        await db.users.update_one({"id": "staff"}, {"$set": {"accounting_permissions": []}})
        await original(db, owner, rows)
    monkeypatch.setattr(activity, "_actor_names", revoke)
    async def scenario():
        db = await database(state_fixture())
        await db.users.update_one({"id": "staff"}, {"$set": {"accounting_permissions": ["accounting.movements.view"]}})
        async with client(db, mobile=False) as browser:
            result = await browser.get("/api/operational-balances/daily-financial-movements",
                params={"from_date": "2026-11-07", "to_date": "2026-11-07"})
            assert result.status_code == 403
    asyncio.run(scenario())


def test_real_mongo_browser_write_grant_and_account_scope_rechecked_before_commit(monkeypatch):
    from test_operational_balance_integration import run, started, movement as command
    from test_operational_balance_app_permissions import grant, client, READ
    from operational_balance_store import read, digest
    import operational_balance_routes as routes
    original = routes.create_movement
    revoke = {"field": "accounting_permissions"}
    async def change_authority(db, *args, **kwargs):
        if revoke["field"] == "accounting_permissions":
            await db.users.update_one({"id": "staff"}, {"$set": {"accounting_permissions": ["accounting.movements.view"]}})
        else:
            await db.mezan_mobile_app_access_v1.update_one({"user_id": "staff"}, {"$set": {"operational_banks": {}}})
        return await original(db, *args, **kwargs)
    monkeypatch.setattr(routes, "create_movement", change_authority)
    async def scenario(db):
        await started(db)
        for field in ("accounting_permissions", "operational_banks"):
            revoke["field"] = field
            await grant(db, [READ])
            await db.users.update_one({"id": "staff"}, {"$set": {
                "accounting_permissions": ["accounting.movements.view", "accounting.movements.import"]}})
            async with client(db, mobile=False) as browser:
                result = await browser.post("/api/operational-balances/movements",
                    json={**command(), "expected_session_scope": digest(["owner", "staff"])})
                assert result.status_code == 403, result.text
            assert (await read(db, "owner"))["movements"] == []
    run(scenario)
