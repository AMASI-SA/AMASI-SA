"""Account-scoped paging must not rerun financial history or confer write access."""
import asyncio
from copy import deepcopy

import pytest
from fastapi import HTTPException

import operational_financial_activity as activity
from operational_app_banks import assigned_banks, require_assigned_bank, validate_assignment
from operational_balance_store import STATES
from test_operational_balance_daily_financial_movements import (
    OWNER, STAFF, database, feed, movement, state_fixture,
)


@pytest.fixture(autouse=True)
def isolated_activity_cache():
    activity.clear_page_cache()
    yield
    activity.clear_page_cache()


def test_next_page_reuses_prepared_rows_and_only_probes_current_revision(monkeypatch):
    state = state_fixture()
    state["movements"] = [movement(str(i), amount="10.10", allocations=[]) for i in range(21)]
    original = activity._activity_state
    reads = []
    async def counted(*args):
        reads.append(args[-1]["revision"])
        return await original(*args)
    monkeypatch.setattr(activity, "_activity_state", counted)
    async def scenario():
        db = await database(state)
        first = await feed(db)
        second = await feed(db, page=2, snapshot=first["snapshot"])
        third = await feed(db, page=3, snapshot=first["snapshot"])
        assert reads == [1]
        assert [len(result["items"]) for result in (first, second, third)] == [10, 10, 1]
        assert first["totals_by_currency"] == second["totals_by_currency"] == third["totals_by_currency"]
        assert first["totals_by_currency"][0]["outgoing"] == "212.10"
        assert len({row["id"] for result in (first, second, third) for row in result["items"]}) == 21
        # Explicit refresh is the one deliberate rebuild; page navigation is not.
        await feed(db)
        assert reads == [1, 1]
    asyncio.run(scenario())


def test_same_scope_concurrent_first_reads_build_once(monkeypatch):
    state = state_fixture()
    state["movements"] = [movement()]
    original = activity._activity_state
    reads = []
    async def counted(*args):
        reads.append(True)
        await asyncio.sleep(0)
        return await original(*args)
    monkeypatch.setattr(activity, "_activity_state", counted)
    async def scenario():
        db = await database(state)
        results = await asyncio.gather(*(feed(db) for _ in range(5)))
        assert len(reads) == 1
        assert len({row["snapshot"] for row in results}) == 1
    asyncio.run(scenario())


def test_projection_excludes_reconciliation_archive_and_request_history(monkeypatch):
    state = state_fixture()
    state["movements"] = [movement()]
    state["requests"] = {"large-unrelated": {"private": "ignored"}}
    state["engine"]["audit"] = {"tail": ["large-unrelated"]}
    state["engine_audit_archive"] = {"invalid-unrelated-manifest": True}
    seen = []
    original = activity._prepare_rows
    def inspect(candidate, *args):
        seen.append(deepcopy(candidate))
        return original(candidate, *args)
    monkeypatch.setattr(activity, "_prepare_rows", inspect)
    async def scenario():
        result = await feed(await database(state))
        assert result["total"] == 1
        assert "requests" not in seen[0]
        assert "engine_audit_archive" not in seen[0]
        assert "audit" not in seen[0].get("engine", {})
    asyncio.run(scenario())


def test_packed_financial_snapshot_preserves_narrow_daily_results(monkeypatch):
    from operational_balance_archive import compact_financial_snapshot
    monkeypatch.setattr(activity, 'now', lambda: '2026-11-07T12:00:00+00:00')
    state = state_fixture()
    state['movements'] = [movement()]
    state['engine']['facts'] = {'unrelated': {'private': 'not read by activity'}}
    state['engine']['audit'] = {'tail': ['unrelated audit']}
    state['engine_audit_archive'] = {'invalid-unrelated-manifest': True}
    seen = []
    original = activity._prepare_rows
    def inspect(candidate, *args):
        seen.append(deepcopy(candidate))
        return original(candidate, *args)
    monkeypatch.setattr(activity, '_prepare_rows', inspect)
    async def scenario():
        db = await database(state)
        plain = await feed(db)
        packed = compact_financial_snapshot(state, 2_000_000)
        await db[STATES].replace_one({'_id': 'owner'}, packed)
        activity.clear_page_cache()
        result = await feed(db)
        assert result == plain
        assert seen[-1]['engine'] == {'obligations': state['engine']['obligations']}
        assert 'engine_audit_archive' not in seen[-1]
        assert 'requests' not in seen[-1]
        assert 'private' not in str(seen[-1])
    asyncio.run(scenario())


def test_corrupt_packed_financial_snapshot_never_becomes_an_empty_ledger():
    from operational_balance_archive import SNAPSHOT, compact_financial_snapshot
    async def scenario():
        state = state_fixture()
        state['movements'] = [movement()]
        packed = compact_financial_snapshot(state, 2_000_000)
        packed[SNAPSHOT]['sha256'] = 'invalid'
        with pytest.raises(HTTPException) as error:
            await feed(await database(packed))
        assert error.value.status_code == 503
        assert error.value.detail['code'] == 'financial_movements_snapshot_invalid'
    asyncio.run(scenario())


def test_bank_filter_and_hidden_malformed_money_are_scoped_before_validation_or_totals():
    state = state_fixture()
    state["movements"] = [movement("visible", amount="12.34"), movement("other", bank_kind="cash", bank_id="cash", amount="5.00"),
                          movement("hidden", bank_id="secret", amount="not-money", currency=None)]
    async def scenario():
        db = await database(state, cash=("cash",))
        params = dict(owner="owner", actor=STAFF, from_date="2026-11-07", to_date="2026-11-07")
        result = await activity.daily_financial_movements(db, **params, account_type="bank", account_id="bank")
        assert result["total"] == 1
        assert result["items"][0]["amount"] == "12.34"
        assert result["totals_by_currency"][0]["outgoing"] == "12.34"
        assert {item["account_id"] for item in result["accounts"]} == {"bank", "cash"}
        assert "secret" not in str(result) and "not-money" not in str(result)
        with pytest.raises(HTTPException) as error:
            await activity.daily_financial_movements(db, **params, account_type="bank", account_id="secret")
        assert error.value.status_code == 403
    asyncio.run(scenario())


def test_read_only_assignment_cannot_authorize_payment_settlement_or_transfer():
    from test_operational_balance_app_permissions import READ
    async def scenario():
        state = state_fixture()
        state["movements"] = [movement("visible"), movement("hidden", bank_id="secret")]
        db = await database(state)
        stored = await validate_assignment(db, "owner", [READ], True, {
            "bank_ids": ["secret"], "default_bank_id": "secret", "cash_ids": ["cash"],
            "read_bank_ids": ["bank"], "read_cash_ids": [],
        })
        assert stored == {"bank_ids": [], "default_bank_id": None, "read_bank_ids": ["bank"], "read_cash_ids": []}
        await db.mezan_mobile_app_access_v1.update_one({"user_id": "staff"}, {"$set": {"operational_banks": stored}})
        assert (await assigned_banks(db, "owner", STAFF))["bank_ids"] == []
        assert await activity.account_scope(db, "owner", STAFF) == {("bank", "bank")}
        result = await activity.daily_financial_movements(db, owner="owner", actor=STAFF,
            from_date="2026-11-07", to_date="2026-11-07")
        assert result["total"] == 1 and result["totals_by_currency"][0]["outgoing"] == "100.00"
        for kind in ("payment", "settlement", "transfer"):
            with pytest.raises(HTTPException) as error:
                await require_assigned_bank(db, "owner", STAFF, {
                    "source_account_type": "bank", "bank_id": "bank", "kind": kind,
                    "party_type": "supplier", "party_id": "supplier",
                })
            assert error.value.status_code == 403
    asyncio.run(scenario())


@pytest.mark.parametrize("payload", [
    {"read_bank_ids": ["foreign"]}, {"read_cash_ids": ["bank"]},
    {"read_bank_ids": ["bank", "bank"]}, {"read_cash_ids": "cash"},
])
def test_read_scope_rejects_foreign_wrong_kind_and_malformed_assignments(payload):
    from test_operational_balance_app_permissions import READ
    async def scenario():
        db = await database()
        with pytest.raises(HTTPException) as error:
            await validate_assignment(db, "owner", [READ], True, payload)
        assert error.value.status_code == 422
    asyncio.run(scenario())


def test_empty_read_assignments_never_expand_to_all_and_revocation_blocks_cached_page():
    state = state_fixture()
    state["movements"] = [movement(str(i)) for i in range(12)]
    async def scenario():
        db = await database(state)
        params = dict(owner="owner", actor=STAFF, from_date="2026-11-07", to_date="2026-11-07")
        first = await activity.daily_financial_movements(db, **params)
        await db.mezan_mobile_app_access_v1.update_one({"user_id": "staff"},
            {"$set": {"operational_banks": {"bank_ids": [], "read_bank_ids": [], "read_cash_ids": []}}})
        with pytest.raises(HTTPException) as error:
            await activity.daily_financial_movements(db, **params, page=2, snapshot=first["snapshot"])
        assert error.value.status_code in {403, 409}
        empty = await activity.daily_financial_movements(db, **params)
        assert empty["items"] == [] and empty["total"] == 0 and empty["accounts"] == []
        assert empty["totals_by_currency"] == []
    asyncio.run(scenario())


def test_http_checks_current_page_grant_even_for_prepared_page():
    from test_operational_balance_app_permissions import client
    state = state_fixture()
    state["movements"] = [movement(str(i)) for i in range(12)]
    async def scenario():
        db = await database(state)
        await db.users.update_one({"id": "staff"}, {"$set": {"accounting_permissions": ["accounting.movements.view"]}})
        async with client(db, mobile=False) as browser:
            path = "/api/operational-balances/daily-financial-movements"
            params = {"from_date": "2026-11-07", "to_date": "2026-11-07"}
            first = await browser.get(path, params=params)
            assert first.status_code == 200, first.text
            await db.users.update_one({"id": "staff"}, {"$set": {"accounting_permissions": []}})
            denied = await browser.get(path, params={**params, "page": 2, "snapshot": first.json()["snapshot"]})
            assert denied.status_code == 403 and "items" not in denied.json()
    asyncio.run(scenario())


def test_legacy_feed_cannot_disclose_transfer_counteraccount_or_hidden_rows():
    state = state_fixture()
    state["movements"] = [
        movement("out", kind="transfer", party_type="bank", party_id="secret", name="PRIVATE BANK",
                 note="PRIVATE NOTE", reference="PRIVATE REF", allocations=[]),
        movement("in", kind="transfer", bank_id="secret", bank_name="PRIVATE BANK",
                 party_type="bank", party_id="bank", name="Visible bank", allocations=[]),
        movement("hidden", bank_id="secret"),
    ]
    rows = activity.scoped_legacy_movements(state, {("bank", "bank")})
    assert len(rows) == 2
    assert {row["direction"] for row in rows} == {"incoming", "outgoing"}
    assert all(row["bank_id"] == "bank" and row["party_id"] is None for row in rows)
    assert "secret" not in str(rows) and "PRIVATE" not in str(rows)
    assert activity.scoped_legacy_movements(state, set()) == []
    assert activity.scoped_legacy_movements(state, None) == state["movements"]
