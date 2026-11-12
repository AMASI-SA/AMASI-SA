"""Bank refresh keeps recorded money while bounding request-owned preparation."""
import asyncio
from collections import Counter
from copy import deepcopy
from threading import Event
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest

import operational_balance_service as service
import operational_balance_store as store
from operational_balance_routes import scope
from operational_demand_refresh import LEASES, run_visible_refresh
from test_operational_balance_demand_refresh import Collection, DB, Request


NOW = "2026-11-12T12:00:00+00:00"


class CountedCollection(Collection):
    async def find_one(self, query, projection=None):
        self.db.reads[self.name] += 1
        if self.name == store.STATES and projection and projection.get("status"):
            state = self.db.data[store.STATES]["owner"]
            return {key: state[key] for key in ("status", "revision")}
        return await super().find_one(query, projection)


class CountedDB(DB):
    def __init__(self):
        super().__init__()
        self.reads = Counter()
        self.checks = 0
        self.data["users"] = {"owner": {"id": "owner", "role": "owner"}}

    def __getitem__(self, name):
        return CountedCollection(self, name)


class CountedMovement(dict):
    visits = 0

    def get(self, key, *args):
        if key == "request_id":
            type(self).visits += 1
        return super().get(key, *args)


def movement(identity="a", number="A", **changes):
    return {"request_id": "order-bank:" + identity, "order_number": number,
            "direction": "incoming", "bank_id": "bank", "amount": "10.00", **changes}


def order(identity="a", number="A", *, amount="10.00", bank="bank"):
    return {"id": identity, "order_number": number, "currency": "SAR",
            "bank_credit": {"bank_id": bank, "amount": amount, "bank_name": "Bank"}}


def isolate_sources(monkeypatch, orders):
    import operational_balance_engine
    import operational_balance_inventory
    import operational_balance_sources
    import operational_customer_returns
    import operational_profit_sources

    def reconcile(state, *_):
        result = deepcopy(state)
        result["engine"].update(obligations={}, issues=[])
        return result

    monkeypatch.setattr(operational_balance_sources, "collect_sources", AsyncMock(return_value={"orders": orders}))
    monkeypatch.setattr(operational_balance_inventory, "assert_no_source_collision", AsyncMock())
    monkeypatch.setattr(operational_customer_returns, "attach_returns", lambda *_: None)
    monkeypatch.setattr(operational_balance_engine, "reconcile", reconcile)
    monkeypatch.setattr(operational_profit_sources, "load_profit_snapshot", AsyncMock(return_value={"source": "isolated_bank_refresh"}))


async def visible_refresh(db, request=None):
    async def verify():
        db.checks += 1
        actor, owner, _ = await scope(db, {"id": "owner"}, "reports")
        assert (actor["id"], owner) == ("owner", "owner")
        metadata = await db[store.STATES].find_one(
            {"_id": owner, "owner_id": owner}, {"status": 1, "revision": 1, "engine.as_of": 1})
        await service.active_gate(db, owner, metadata)

    return await run_visible_refresh(request or Request(), db, "owner", "owner", verify,
                                     lambda: service.refresh(db, "owner", clock=NOW))


def claim_rows(db):
    return list(db.data.get(store.OPERATION_CLAIMS, {}).values())


def test_recorded_bank_cohort_does_not_add_per_order_authorization_io(monkeypatch):
    async def measured(count):
        db = CountedDB()
        saved = [CountedMovement(movement(str(i), str(i))) for i in range(400)]
        db.data[store.STATES]["owner"]["movements"] = deepcopy(saved)
        isolate_sources(monkeypatch, [order(str(i), str(i)) for i in range(count)])
        CountedMovement.visits = 0
        result = await visible_refresh(db)
        assert result["movements"] == saved
        assert result["audit"] == [] and result["engine"]["issues"] == []
        assert claim_rows(db) == []
        assert db.data[LEASES] == {}
        return db.checks, db.reads, Counter((name, action) for name, action, _ in db.writes), CountedMovement.visits

    async def scenario():
        empty = await measured(0)
        full = await measured(200)
        assert full[:3] == empty[:3], "Recorded bank orders must not add authorization reads or lease renewals"
        assert full[3] <= 400 * 2, "Prepare the recorded movement identities once, not once per order"
    asyncio.run(scenario())


@pytest.mark.parametrize("recorded,expected_new,expected_conflicts", [
    ([movement()], 0, 0),
    ([movement(), movement()], 0, 1),
    ([movement(number="other"), movement(identity="other")], 0, 1),
    ([movement(identity="other")], 0, 0),
    ([movement(number="other")], 0, 0),
    ([movement(identity="other", direction="outgoing")], 1, 0),
    ([movement(direction="outgoing")], 0, 0),
    ([movement(bank_id="another-bank")], 0, 1),
    ([movement(amount="11.00")], 0, 1),
    ([movement(amount="10")], 0, 0),
])
def test_refresh_matches_union_of_movement_positions(monkeypatch, recorded, expected_new, expected_conflicts):
    async def scenario():
        db = CountedDB()
        db.data[store.STATES]["owner"]["movements"] = deepcopy(recorded)
        isolate_sources(monkeypatch, [order()])
        result = await visible_refresh(db)
        assert result["movements"][:len(recorded)] == recorded
        assert len(result["movements"]) == len(recorded) + expected_new
        assert len(claim_rows(db)) == len(result["audit"]) == expected_new
        assert result["engine"]["issues"] == [
            {"code": "operational_order_bank_credit_conflict", "source_id": "a"}
        ] * expected_conflicts
    asyncio.run(scenario())


@pytest.mark.parametrize("legacy", [
    {"request_id": {"legacy": "unrelated"}},
    {"order_number": ["unrelated"]},
])
def test_unrelated_legacy_identity_shape_does_not_block_saved_bank_refresh(monkeypatch, legacy):
    async def scenario():
        db = CountedDB()
        recorded = [movement(), movement("unrelated", "U", **legacy)]
        db.data[store.STATES]["owner"]["movements"] = deepcopy(recorded)
        isolate_sources(monkeypatch, [order()])
        result = await visible_refresh(db)
        assert result["movements"] == recorded
        assert result["engine"]["issues"] == []
        assert claim_rows(db) == [] and result["audit"] == []
        assert db.data[LEASES] == {}
    asyncio.run(scenario())


@pytest.mark.parametrize("legacy", [
    {"request_id": {"legacy": "unrelated"}},
    {"order_number": ["unrelated"]},
])
def test_legacy_unhashable_key_keeps_the_same_movements_other_conflict_key(monkeypatch, legacy):
    async def scenario():
        db = CountedDB()
        recorded = [movement(bank_id="another-bank", **legacy)]
        db.data[store.STATES]["owner"]["movements"] = deepcopy(recorded)
        isolate_sources(monkeypatch, [order()])
        result = await visible_refresh(db)
        assert result["movements"] == recorded
        assert result["engine"]["issues"] == [
            {"code": "operational_order_bank_credit_conflict", "source_id": "a"}]
        assert claim_rows(db) == [] and result["audit"] == []
        assert db.data[LEASES] == {}
    asyncio.run(scenario())


def test_same_batch_candidates_preserve_claim_identity_and_conflicts(monkeypatch):
    async def scenario():
        db = CountedDB()
        orders = [order("a", "X"), order("a", "X"), order("b", "X"),
                  order("a", "Y"), order("b", "Y"), order("a", "Y"),
                  order("b", "Y", amount="11.00"), order("c", "Z"), order("b", "Z")]
        isolate_sources(monkeypatch, orders)
        result = await visible_refresh(db)
        assert [(m["request_id"], m["order_number"], m["amount"]) for m in result["movements"]] == [
            ("order-bank:a", "X", "10.00"), ("order-bank:b", "Y", "10.00"), ("order-bank:c", "Z", "10.00")]
        assert len(claim_rows(db)) == len(result["audit"]) == 3
        assert [row["source_id"] for row in result["engine"]["issues"]] == ["a", "b", "b"]
        assert {row["actor_id"] for row in claim_rows(db)} == {"operational_order_worker"}
        assert {row["owner_id"] for row in claim_rows(db)} == {"owner"}
    asyncio.run(scenario())


def test_revocation_after_preparation_prevents_new_claim_and_save(monkeypatch):
    async def scenario():
        db = CountedDB()
        isolate_sources(monkeypatch, [order()])
        original = service._bank_credit_actions

        def prepare(*args):
            actions = original(*args)
            db.data["users"]["owner"]["disabled"] = True
            return actions

        monkeypatch.setattr(service, "_bank_credit_actions", prepare)
        with pytest.raises(HTTPException) as error:
            await visible_refresh(db)
        assert error.value.detail["code"] == "operational_actor_inactive"
        assert claim_rows(db) == []
        assert db.data[store.STATES]["owner"]["revision"] == 1
        assert not [write for write in db.writes if write[0] == store.STATES]
        assert db.data[LEASES] == {}
    asyncio.run(scenario())


def test_revocation_at_commit_preserves_saved_money_and_claim_intent(monkeypatch):
    async def scenario():
        db = CountedDB()
        isolate_sources(monkeypatch, [order()])
        before = deepcopy(db.data[store.STATES]["owner"])
        original = store.stage_pages

        async def stage(database, pages):
            await original(database, pages)
            db.data["users"]["owner"]["disabled"] = True

        monkeypatch.setattr(store, "stage_pages", stage)
        with pytest.raises(HTTPException) as error:
            await visible_refresh(db)
        assert error.value.detail["code"] == "operational_actor_inactive"
        assert len(claim_rows(db)) == 1  # A pinned intent does not move money.
        assert db.data[store.STATES]["owner"] == before
        assert not [write for write in db.writes if write[0] == store.STATES]
        assert db.data[LEASES] == {}
    asyncio.run(scenario())


def test_disconnect_after_first_claim_stops_later_claims_and_save(monkeypatch):
    async def scenario():
        db, request = CountedDB(), Request()
        isolate_sources(monkeypatch, [order("a", "A"), order("b", "B")])
        before = deepcopy(db.data[store.STATES]["owner"])
        original = service.claim_movement

        async def claim(*args):
            result = await original(*args)
            request.disconnect()
            return result

        monkeypatch.setattr(service, "claim_movement", claim)
        with pytest.raises(HTTPException) as error:
            await visible_refresh(db, request)
        assert error.value.detail["code"] == "operational_refresh_cancelled"
        assert len(claim_rows(db)) == 1
        assert db.data[store.STATES]["owner"] == before
        assert not [write for write in db.writes if write[0] == store.STATES]
        assert db.data[LEASES] == {}
    asyncio.run(scenario())


def test_disconnect_during_bank_preparation_joins_thread_before_return(monkeypatch):
    async def scenario():
        db, request = CountedDB(), Request()
        entered, release, finished = asyncio.Event(), Event(), Event()
        loop = asyncio.get_running_loop()

        class BlockingMovement(dict):
            def get(self, key, *args):
                if key == "request_id":
                    loop.call_soon_threadsafe(entered.set)
                    assert release.wait(2), "Test failed to release the owned preparation thread"
                return super().get(key, *args)

        db.data[store.STATES]["owner"]["movements"] = [BlockingMovement(movement())]
        before = deepcopy(db.data[store.STATES]["owner"])
        isolate_sources(monkeypatch, [order()])
        original = service._bank_credit_actions

        def prepare(*args):
            try:
                return original(*args)
            finally:
                finished.set()

        monkeypatch.setattr(service, "_bank_credit_actions", prepare)
        work = asyncio.create_task(visible_refresh(db, request))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            request.disconnect()
            await asyncio.sleep(0)
            assert not work.done()
        finally:
            release.set()
        with pytest.raises(HTTPException) as error:
            await work
        assert error.value.detail["code"] == "operational_refresh_cancelled"
        assert finished.is_set()
        assert claim_rows(db) == []
        assert db.data[store.STATES]["owner"] == before
        assert not [write for write in db.writes if write[0] == store.STATES]
        assert db.data[LEASES] == {}
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())
