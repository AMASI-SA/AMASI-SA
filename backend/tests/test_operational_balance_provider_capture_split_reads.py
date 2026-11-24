"""Disjoint capture reads preserve legacy batches, one cap and owned cancellation."""
import asyncio
from copy import deepcopy
from datetime import datetime

import pytest

from operational_provider_capture_batches import FIELD, build_batch
from operational_provider_transaction_evidence import COLLECTION, load_transaction_rows
from test_operational_balance_provider_capture_cursor import BoundedMotorCursor, observation
from test_operational_read_runtime import ThreadedMotorCursor


def order(number="1", identity="100"):
    return {"id": identity, "reference_id": number, "created_at": "2026-11-21T00:00:00Z",
            "payment": {"method": "tabby"}}


def legacy(identity, *, number="1", order_id="100", reference=None):
    row = observation(identity)
    proof = row["native_transaction_evidence"]
    proof.update(provider_order_id=order_id, reference=reference or identity)
    row.update(order_number=number, provider_order_id=order_id, native_transaction_evidence=[proof])
    return row


def envelope(rows):
    values = [{**row, "native_transaction_evidence": row["native_transaction_evidence"][0]} for row in rows]
    return build_batch(values, owner="owner", order_number=rows[0]["order_number"],
                       provider_order_id=rows[0]["provider_order_id"])


class Cursor:
    def __init__(self, rows):
        self.rows, self.limit = rows, None

    async def to_list(self, count):
        self.limit = count
        return deepcopy(self.rows[:count])


class DB:
    def __init__(self, rows=(), *, cursors=None):
        self.rows, self.cursors, self.calls = rows, cursors, []

    def __getitem__(self, name):
        assert name == COLLECTION
        return self

    def find(self, query, projection):
        self.calls.append((deepcopy(query), deepcopy(projection)))
        assert query["user_id"] == "owner" and query["source"] == "salla_transactions"
        assert set(query) == {"user_id", "source", "order_number", FIELD}
        assert type(query[FIELD]["$exists"]) is bool
        has_batch = query[FIELD]["$exists"]
        assert (FIELD in projection) is has_batch
        if self.cursors:
            return self.cursors[has_batch]
        numbers = query["order_number"]["$in"]
        rows = [row for row in self.rows if row.get("user_id") == "owner"
                and row.get("source") == "salla_transactions" and row.get("order_number") in numbers
                and (FIELD in row) is has_batch]
        return Cursor(rows)


async def load(db, *, limit=20_000, orders=None):
    instant = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
    return await load_transaction_rows(db, "owner", orders or [order()],
        instant("2026-11-20T00:00:00Z"), instant("2026-11-24T00:00:00Z"),
        to_instant=instant, max_rows=limit)


@pytest.mark.asyncio
async def test_disjoint_queries_return_each_legacy_and_expanded_capture_once():
    rows = [legacy("legacy"), envelope([legacy("native-a"), legacy("native-b")]),
            {**legacy("foreign"), "user_id": "other"}]
    db = DB(rows)
    actual = await load(db, limit=3)
    assert [row["transaction_id"] for row in actual["1"]] == ["legacy", "native-a", "native-b"]
    assert [query[FIELD] for query, _ in db.calls] == [{"$exists": False}, {"$exists": True}]
    assert len(db.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_count,native_count,limit", [(3, 1, 2), (2, 3, 4), (2, 1, 2)])
async def test_one_expanded_budget_rejects_overflow_without_returning_legacy_prefix(legacy_count, native_count, limit):
    db = DB([*[legacy("legacy-"+str(i)) for i in range(legacy_count)],
             envelope([legacy("native-"+str(i)) for i in range(native_count)])])
    with pytest.raises(ValueError, match="operational_source_scope_too_large"):
        await load(db, limit=limit)
    assert len(db.calls) == (1 if legacy_count > limit else 2)


@pytest.mark.asyncio
async def test_reference_collision_is_quarantined_across_legacy_and_envelope_lanes():
    db = DB([legacy("legacy", reference="shared"),
             envelope([legacy("native", number="2", order_id="200", reference="shared")])])
    result = await load(db, orders=[order(), order("2", "200")])
    for number in ("1", "2"):
        assert len(result[number]) == 2
        assert result[number][-1]["native_transaction_evidence"] == []


@pytest.mark.asyncio
async def test_expanded_budget_does_not_reset_for_a_second_250_order_chunk():
    orders = [order(str(i), str(i+1000)) for i in range(251)]
    db = DB([legacy("row-"+str(i), number=str(i), order_id=str(i+1000)) for i in range(251)])
    with pytest.raises(ValueError, match="operational_source_scope_too_large"):
        await load(db, limit=250, orders=orders)
    assert len(db.calls) == 3
    assert [len(query["order_number"]["$in"]) for query, _ in db.calls] == [250, 250, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, {}, "unknown"])
async def test_present_but_invalid_envelope_stays_in_guarded_lane_and_blocks_its_order(value):
    db = DB([{**legacy("corrupt"), FIELD: value}])
    result = await load(db)
    assert len(result["1"]) == 1
    assert result["1"][0]["native_transaction_evidence"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_lane", [False, True], ids=["legacy", "envelope"])
async def test_cancellation_joins_active_lane_before_close_and_never_returns_prefix(cancel_lane):
    old = ThreadedMotorCursor([[legacy("old")]], block=not cancel_lane)
    new = BoundedMotorCursor([[envelope([legacy("new")])]], block=cancel_lane)
    db = DB(cursors={False: old, True: new})
    active = new if cancel_lane else old
    task = asyncio.create_task(load(db))
    try:
        await asyncio.wait_for(active.entered.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not active.worker_finished.is_set()
        active.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert active.close_calls == 1 and not active.closed_during_read
        assert len(db.calls) == (2 if cancel_lane else 1)
        assert all(future.done() for future in active.operations)
    finally:
        old.release.set(); new.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, *old.operations, *new.operations, return_exceptions=True)


class ServerBatchCursor(ThreadedMotorCursor):
    """Small legacy docs fit after the server's default first101-document batch."""
    def __init__(self, rows):
        self.rows, self.batch_request = rows, None
        super().__init__([rows[:101], rows[101:]] if len(rows) > 101 else [rows], block=False)

    def batch_size(self, size):
        self.batch_request = size
        self.batches = [self.rows[i:i+size] for i in range(0, len(self.rows), size)]
        return self


@pytest.mark.asyncio
async def test_large_legacy_history_keeps_server_batches_while_only_envelopes_use_four():
    old = ServerBatchCursor([legacy("old-"+str(i)) for i in range(6000)])
    new = ServerBatchCursor([envelope([legacy("new-"+str(i))]) for i in range(20)])
    result = await load(DB(cursors={False: old, True: new}))
    assert len(result["1"]) == 6020
    assert old.batch_request is None and new.batch_request == 4
    assert len(old.operations) == 2 and len(new.operations) == 5
    assert old.close_calls == new.close_calls == 1
