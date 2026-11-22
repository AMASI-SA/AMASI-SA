"""Read-only accepted fee indexes stay bound to their financial snapshot."""
from copy import deepcopy

import pytest
from mongomock_motor import AsyncMongoMockClient
from pymongo.errors import ExecutionTimeout

import provider_fee_statement_actual_sources as sources
from operational_balance_archive import SNAPSHOT, compact_financial_snapshot
from operational_balance_store import STATES
from tests.test_dashboard_operational_statement_fees import OWNER, NOW, accept, accepted_event, tabby_state
from tests.test_provider_fee_weekly_sources import ReadOnlyDB


def projected(state):
    return {**{key: deepcopy(state[key]) for key in ("_id", "owner_id", "revision", "status", "started_at")},
            "engine": {"as_of": state["engine"]["as_of"]}}


async def seeded(*, accepted=True, packed=False):
    db = AsyncMongoMockClient().statement_actual_sources
    state, _ = tabby_state()
    if accepted:
        event = accepted_event(state)
        event["source"]["plan_id"] = "Installments3Months"
        accept(state, event)
    if packed:
        state = compact_financial_snapshot(state, 12 * 1024 * 1024)
    await db[STATES].insert_one(deepcopy(state))
    return db, state


async def load(db, **changes):
    return await sources.load_operational_statement_fee_index(db, OWNER,
        changes.pop("order_numbers", ["order-one"]), accepted_as_of=NOW, **changes)


@pytest.mark.asyncio
@pytest.mark.parametrize("packed", [False, True])
async def test_actual_fees_use_one_owner_snapshot_without_writes_or_audit_history(packed):
    db, state = await seeded(packed=packed)
    before = deepcopy(state)
    readonly = ReadOnlyDB(db)
    result = await load(readonly, source_state=projected(state))
    assert result["order-one"]["available"] is True
    assert result["order-one"]["fee_total"] == "8.05"
    assert result["order-one"]["plan_id"] == "Installments3Months"
    assert all(collection == STATES and query["_id"] == OWNER and query["owner_id"] == OWNER
               for collection, query, _ in readonly.reads)
    assert sum(projection == {"requests": 0, "audit": 0} for _, _, projection in readonly.reads) == 1
    assert await db[STATES].find_one({"_id": OWNER}) == before
    if packed:
        assert SNAPSHOT in before


@pytest.mark.asyncio
async def test_average_only_source_never_unpacks_the_financial_snapshot():
    db, state = await seeded(accepted=False, packed=True)
    readonly = ReadOnlyDB(db)
    assert await load(readonly, source_state=projected(state)) == {}
    assert len(readonly.reads) == 2
    assert all(projection == sources.HEADER_PROJECTION for _, _, projection in readonly.reads)


@pytest.mark.asyncio
async def test_absent_source_and_empty_cohort_do_not_read_a_full_snapshot():
    db = AsyncMongoMockClient().statement_actual_absent
    readonly = ReadOnlyDB(db)
    assert await load(readonly) == {}
    assert len(readonly.reads) == 1 and readonly.reads[0][2] == sources.HEADER_PROJECTION
    readonly.reads.clear()
    assert await load(readonly, order_numbers=[]) == {}
    assert await load(readonly, source_state={}) == {}
    assert readonly.reads == []


@pytest.mark.asyncio
async def test_stale_cache_revision_does_not_silently_drop_new_actual_proof():
    db, state = await seeded()
    expected = projected(state)
    await db[STATES].update_one({"_id": OWNER}, {"$inc": {"revision": 1}})
    readonly = ReadOnlyDB(db)
    row = (await load(readonly, source_state=expected))["order-one"]
    assert row["available"] is False and row["reason_code"] == "provider_statement_fee_source_changed"
    assert "fee_total" not in row
    assert all(projection == sources.HEADER_PROJECTION for _, _, projection in readonly.reads)


@pytest.mark.asyncio
async def test_change_after_snapshot_read_is_rejected_before_returning_actual_fee(monkeypatch):
    db, state = await seeded()
    original = sources.read_financial_position_state

    async def racing(database, owner):
        result = await original(database, owner)
        await db[STATES].update_one({"_id": OWNER}, {"$inc": {"revision": 1}})
        return result

    monkeypatch.setattr(sources, "read_financial_position_state", racing)
    row = (await load(ReadOnlyDB(db), source_state=projected(state)))["order-one"]
    assert row["reason_code"] == "provider_statement_fee_source_changed" and "fee_total" not in row


@pytest.mark.asyncio
async def test_corrupt_packet_and_owner_mismatch_never_supply_actual_or_plan():
    db, state = await seeded(packed=True)
    await db[STATES].update_one({"_id": OWNER}, {"$set": {SNAPSHOT + ".sha256": "f" * 64}})
    row = (await load(ReadOnlyDB(db), source_state=projected(state)))["order-one"]
    assert row["reason_code"] == "provider_statement_fee_source_invalid" and row["plan_id"] is None
    wrong_owner = projected(state)
    wrong_owner["owner_id"] = "foreign"
    readonly = ReadOnlyDB(db)
    row = (await load(readonly, source_state=wrong_owner))["order-one"]
    assert row["reason_code"] == "provider_statement_fee_source_invalid" and readonly.reads == []


@pytest.mark.asyncio
async def test_read_failure_is_explicit_instead_of_becoming_no_accepted_evidence():
    class Unavailable:
        def __getitem__(self, name):
            assert name == STATES
            return self

        async def find_one(self, *args, **kwargs):
            raise ExecutionTimeout("synthetic source failure")

    row = (await load(Unavailable()))["order-one"]
    assert row["reason_code"] == "provider_statement_fee_source_unavailable" and row["plan_id"] is None


@pytest.mark.asyncio
async def test_actual_source_reader_enforces_cohort_bound_before_database_access():
    class NoRead:
        def __getitem__(self, name):
            raise AssertionError("unbounded cohort reached database")

    result = await load(NoRead(), order_numbers=[str(index) for index in range(20001)])
    assert all(row["reason_code"] == "provider_statement_fee_scope_limit" for row in result.values())
