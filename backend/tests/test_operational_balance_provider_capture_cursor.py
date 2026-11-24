"""Bound expanded native proofs and join real Motor executor work on cancellation."""
import asyncio

import pytest

from operational_provider_capture_batches import build_batch
from operational_provider_capture_cursor import read_capture_rows
from test_operational_read_runtime import ThreadedMotorCursor


def observation(identity):
    return {"user_id": "owner", "source": "salla_transactions", "order_number": "1",
        "provider_order_id": "100", "transaction_id": identity, "fingerprint": identity,
        "native_transaction_evidence": {"schema": "salla_transaction_evidence_v1",
            "reference": identity, "transaction_id": identity, "provider_order_id": "100",
            "provider": "tabby", "status": "captured", "paid_at": "2026-11-21T12:00:00Z",
            "currency": "SAR", "amount": "1.00"}}


def batch(count):
    return build_batch([observation(str(index)) for index in range(count)],
        owner="owner", order_number="1", provider_order_id="100")


class BoundedMotorCursor(ThreadedMotorCursor):
    def batch_size(self, count):
        self.batch_size_requested = count
        return self


@pytest.mark.asyncio
async def test_motor_streams_small_batches_and_preserves_every_complete_observation():
    cursor = BoundedMotorCursor([[batch(3)], [observation("legacy")]], block=False)
    rows = await read_capture_rows(cursor, max_rows=4)
    assert [row["transaction_id"] for row in rows] == ["0", "1", "2", "legacy"]
    assert cursor.batch_size_requested == 4
    assert cursor.close_calls == 1 and not cursor.closed_during_read
    assert len(cursor.operations) == 2


@pytest.mark.asyncio
async def test_envelope_overflow_stops_before_fetching_another_source_batch():
    cursor = BoundedMotorCursor([[batch(3)], [batch(1)]], block=False)
    with pytest.raises(ValueError, match="operational_source_scope_too_large"):
        await read_capture_rows(cursor, max_rows=2)
    assert cursor.consumed == 1 and len(cursor.operations) == 1
    assert cursor.close_calls == 1 and not cursor.closed_during_read


@pytest.mark.asyncio
async def test_normalized_limit_still_probes_overflow_in_the_next_document():
    cursor = BoundedMotorCursor([[batch(2)], [observation("overflow")]], block=False)
    with pytest.raises(ValueError, match="operational_source_scope_too_large"):
        await read_capture_rows(cursor, max_rows=2)
    assert cursor.consumed == 2 and cursor.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["cancel", "deadline"])
async def test_cancel_or_deadline_joins_active_motor_read_and_sequential_close(cause):
    cursor = BoundedMotorCursor([[batch(2)], [batch(2)]], block_close=True)

    async def read():
        async with asyncio.timeout(.02 if cause == "deadline" else 1):
            return await read_capture_rows(cursor, max_rows=4)

    task = asyncio.create_task(read())
    try:
        await asyncio.wait_for(cursor.entered.wait(), 1)
        if cause == "deadline":
            await asyncio.sleep(.03)
        else:
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
        assert not cursor.worker_finished.is_set() and not task.done()
        cursor.release.set()
        await asyncio.wait_for(cursor.close_entered.wait(), 1)
        assert not task.done() and not cursor.closed_during_read
        assert all(operation.done() for operation in cursor.operations)
        cursor.close_release.set()
        with pytest.raises(TimeoutError if cause == "deadline" else asyncio.CancelledError):
            await task
        assert len(cursor.operations) == cursor.close_calls == 1
    finally:
        cursor.release.set()
        cursor.close_release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, *cursor.operations, return_exceptions=True)


@pytest.mark.asyncio
async def test_coroutine_fake_fallback_is_bounded_in_documents_and_expanded_rows():
    class Cursor:
        async def to_list(self, count):
            self.count = count
            return [batch(3)]
    cursor = Cursor()
    with pytest.raises(ValueError, match="operational_source_scope_too_large"):
        await read_capture_rows(cursor, max_rows=2)
    assert cursor.count == 3


@pytest.mark.asyncio
async def test_future_returning_proxy_is_rejected_before_starting_unowned_work():
    class Proxy:
        def to_list(self, count):
            raise AssertionError("a Future-returning source proxy was invoked")
    with pytest.raises(ValueError, match="operational_source_cursor_unsupported"):
        await read_capture_rows(Proxy(), max_rows=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, {}, ()])
async def test_missing_cursor_result_cannot_be_reported_as_complete_empty_evidence(response):
    class Cursor:
        async def to_list(self, count):
            return response
    with pytest.raises(ValueError, match="operational_source_read_invalid"):
        await read_capture_rows(Cursor(), max_rows=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [-1, True, 20_001])
async def test_original_source_ceiling_cannot_be_raised_by_the_batch_helper(limit):
    with pytest.raises(ValueError, match="operational_source_scope_too_large"):
        await read_capture_rows(object(), max_rows=limit)
