import asyncio
import os
import uuid
from datetime import date

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from dashboard_recurring_reads import load_dashboard_recurring_inputs
from dashboard_spill import DashboardSpill
from recurring_obligations_routes import (
    INVOICES, OBLIGATIONS, compute_recurring_obligations_for_range,
)


def obligation(identity, **extra):
    return dict(id=identity, user_id="owner", status="active", start_date="2026-01-01",
                cycle="monthly", period_amount=310, expense_type="electricity",
                estimation_basis="last_3_invoices", **extra)


def invoice(identity, amount, start, end):
    return dict(user_id="owner", obligation_id=identity, amount=amount,
                period_start=start, period_end=end)


async def exercise(rows, invoices, callback):
    uri = os.environ.get("DASHBOARD_TEST_MONGO_URI")
    if not uri:
        pytest.skip("isolated Mongo URI required")
    assert uri.startswith("mongodb://127.0.0.1:")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    db = client["dashboard_recurring_" + uuid.uuid4().hex]
    store = DashboardSpill()
    try:
        if rows:
            await db[OBLIGATIONS].insert_many(rows)
        for offset in range(0, len(invoices), 128):
            await db[INVOICES].insert_many(invoices[offset:offset + 128])
        inputs = await load_dashboard_recurring_inputs(db, "owner", store)
        await callback(db, inputs)
    finally:
        store.close()
        await client.drop_database(db.name)
        client.close()


@pytest.mark.asyncio
async def test_rich_recurring_parity_preserves_first_actual_raw_sort_and_rounding():
    rows = [obligation("utility"), obligation("fixed")]
    rows[1].update(expense_type="rent", period_amount=19.995)
    rows += [obligation("stopped"), obligation("manual"), obligation("last")]
    rows[2].update(status="stopped", stopped_at="2026-10-02")
    rows[3].update(estimation_basis="manual")
    rows[4].update(estimation_basis="last_invoice")
    invoices = [
        invoice("utility", 3, "2026-10-01", "2026-10-01"),
        invoice("utility", 999, "2026-10-01", "2026-10-01"),
        invoice("utility", 31, "2026-09-01", "2026-09-30"),
        invoice("utility", 62, "2026-09-01", "2026-09-30"),
        invoice("utility", 93, "2026-09-01", "2026-09-30"),
        invoice("utility", 124, "2026-09-01", "2026-09-30"),
        invoice("utility", 0, "2026-08-01", "2026-08-31"),
        invoice("utility", -9, "2026-08-01", "2026-08-31"),
        invoice("utility", 999, "invalid", "invalid"),
        invoice("last", 20, "2026-09-01", " 2026-09-30 "),
        invoice("last", 40, "2026-09-01", "20260930"),
    ]
    async def check(db, inputs):
        start, end = date(2026, 10, 1), date(2026, 10, 4)
        expected = await compute_recurring_obligations_for_range(db, "owner", start, end)
        actual = await compute_recurring_obligations_for_range(
            db, "owner", start, end, dashboard_inputs=inputs)
        assert actual == expected
        assert inputs.invoices_for_day(rows[0], start)[0]["amount"] == 3
        assert [r["amount"] for r in inputs.invoices_for_day(rows[0], end)] == [3, 999, 31]
        assert inputs.invoices_for_day(rows[4], end)[0]["amount"] == 40
        assert inputs.metrics["max_mongo_batch"] <= 128
        assert inputs.metrics["max_selected_invoices"] <= 3
    await exercise(rows, invoices, check)


@pytest.mark.asyncio
async def test_empty_and_owner_isolation():
    other = obligation("other")
    other["user_id"] = "other"
    async def check(db, inputs):
        target = date(2026, 10, 1)
        assert len(inputs.obligations) == 0
        assert await compute_recurring_obligations_for_range(
            db, "owner", target, target, dashboard_inputs=inputs
        ) == await compute_recurring_obligations_for_range(db, "owner", target, target)
    await exercise([other], [], check)


@pytest.mark.asyncio
async def test_validation_errors_are_not_hidden_or_eagerly_raised():
    row = obligation("utility")
    invoices = [invoice("utility", "invalid-money", "2026-09-01", "2026-09-30"),
                invoice("utility", 31, "2026-10-01", "2026-10-01")]
    async def check(db, inputs):
        actual_day, historical_day = date(2026, 10, 1), date(2026, 10, 2)
        # Actual wins before malformed historical data is inspected.
        assert await compute_recurring_obligations_for_range(
            db, "owner", actual_day, actual_day, dashboard_inputs=inputs
        ) == await compute_recurring_obligations_for_range(db, "owner", actual_day, actual_day)
        for kwargs in ({}, {"dashboard_inputs": inputs}):
            with pytest.raises(ValueError, match="invalid-money"):
                await compute_recurring_obligations_for_range(
                    db, "owner", historical_day, historical_day, **kwargs)
    await exercise([row], invoices, check)


@pytest.mark.asyncio
async def test_zero_actual_wins_and_invalid_inactive_or_fixed_history_is_not_read():
    rows = [obligation("utility"), obligation("fixed"), obligation("inactive")]
    rows[1].update(expense_type="rent", period_amount=1.005)
    rows[2]["status"] = "cancelled"
    invoices = [invoice("utility", 0, "2026-10-01", "2026-10-01"),
                invoice("utility", 999, "2026-10-01", "2026-10-01"),
                invoice("fixed", "bad", "2026-09-01", "2026-09-30"),
                invoice("inactive", "bad", "2026-09-01", "2026-09-30")]
    async def check(db, inputs):
        target = date(2026, 10, 1)
        assert await compute_recurring_obligations_for_range(
            db, "owner", target, target, dashboard_inputs=inputs
        ) == await compute_recurring_obligations_for_range(db, "owner", target, target)
    await exercise(rows, invoices, check)


@pytest.mark.asyncio
async def test_large_full_cohort_removes_legacy_20000_invoice_cap():
    invoices = [invoice("utility", 60, "2026-09-01", "2026-09-30") for _ in range(20000)]
    invoices.append(invoice("utility", 31, "2026-10-01", "2026-10-31"))
    async def check(db, inputs):
        target = date(2026, 10, 10)
        legacy = await compute_recurring_obligations_for_range(db, "owner", target, target)
        actual = await compute_recurring_obligations_for_range(
            db, "owner", target, target, dashboard_inputs=inputs)
        assert legacy["total"] == 2.0  # Truncated loader misses the actual invoice.
        assert actual["total"] == 1.0
        assert inputs.metrics["invoices_loaded"] == 20001
        assert inputs.metrics["max_mongo_batch"] <= 128
        assert inputs.metrics["max_selected_invoices"] <= 3
    await exercise([obligation("utility")], invoices, check)


@pytest.mark.asyncio
async def test_full_cohort_removes_legacy_5000_obligation_cap():
    rows = [obligation(str(index)) for index in range(5001)]
    for row in rows:
        row.update(expense_type="rent", period_amount=31)
    async def check(db, inputs):
        target = date(2026, 10, 10)
        legacy = await compute_recurring_obligations_for_range(db, "owner", target, target)
        actual = await compute_recurring_obligations_for_range(
            db, "owner", target, target, dashboard_inputs=inputs)
        assert legacy["total"] == legacy["rentals_total"] == 5000.0
        assert actual["total"] == actual["rentals_total"] == 5001.0
        assert actual["by_type"] == {"rent": 5001.0}
        assert inputs.metrics["obligations_loaded"] == len(inputs.obligations) == 5001
        assert inputs.metrics["max_mongo_batch"] <= 128
        assert inputs.metrics["invoices_loaded"] == 0
    await exercise(rows, [], check)


class ControlledCursor:
    """Controlled I/O boundary; actual loader, spill and calculator stay real."""
    def __init__(self, rows, *, fail=False, wait=False):
        self.rows, self.fail, self.wait = rows, fail, wait
        self.calls = self.close_calls = 0
        self.waiting = asyncio.Event()

    def batch_size(self, size):
        assert size == 128
        return self

    async def to_list(self, length):
        assert length == 128
        self.calls += 1
        if self.calls == 1:
            return self.rows
        if self.fail:
            raise RuntimeError("injected cursor read failure")
        if self.wait:
            self.waiting.set()
            await asyncio.Future()
        return []

    async def close(self):
        self.close_calls += 1


class ControlledCollection:
    def __init__(self, cursor):
        self.cursor = cursor
    def find(self, query, projection):
        assert query == {"user_id": "owner"}
        assert projection["_id"] == 0
        return self.cursor


def controlled_db(stage, **options):
    cursors = {
        OBLIGATIONS: ControlledCursor([obligation("utility")], **(options if stage == OBLIGATIONS else {})),
        INVOICES: ControlledCursor([invoice("utility", 31, "2026-10-01", "2026-10-31")],
                                   **(options if stage == INVOICES else {})),
    }
    return {key: ControlledCollection(cursor) for key, cursor in cursors.items()}, cursors


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [OBLIGATIONS, INVOICES])
async def test_cursor_load_failure_propagates_and_closes_open_cursors(stage):
    db, cursors = controlled_db(stage, fail=True)
    store = DashboardSpill()
    directory = store.directory
    try:
        with pytest.raises(RuntimeError, match="injected cursor read failure"):
            await load_dashboard_recurring_inputs(db, "owner", store)
        assert cursors[stage].calls == 2  # Failure after a successfully spilled batch.
        assert cursors[stage].close_calls == 1
        assert cursors[OBLIGATIONS].close_calls == 1
        if stage == OBLIGATIONS:
            assert cursors[INVOICES].calls == cursors[INVOICES].close_calls == 0
    finally:
        store.close(flush=False)
    assert not directory.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [OBLIGATIONS, INVOICES])
async def test_load_cancellation_propagates_and_closes_open_cursors(stage):
    db, cursors = controlled_db(stage, wait=True)
    store = DashboardSpill()
    directory = store.directory
    task = asyncio.create_task(load_dashboard_recurring_inputs(db, "owner", store))
    try:
        await asyncio.wait_for(cursors[stage].waiting.wait(), timeout=3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cursors[stage].close_calls == 1
        assert cursors[OBLIGATIONS].close_calls == 1
        if stage == OBLIGATIONS:
            assert cursors[INVOICES].calls == cursors[INVOICES].close_calls == 0
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        store.close(flush=False)
    assert not directory.exists()


@pytest.mark.asyncio
async def test_spill_encoding_failure_closes_invoice_cursor_without_partial_result():
    db, cursors = controlled_db(INVOICES)
    cursors[INVOICES].rows[0]["amount"] = object()
    store = DashboardSpill()
    directory = store.directory
    try:
        with pytest.raises(TypeError, match="unsupported spill value type"):
            await load_dashboard_recurring_inputs(db, "owner", store)
        assert cursors[OBLIGATIONS].close_calls == cursors[INVOICES].close_calls == 1
    finally:
        store.close(flush=False)
    assert not directory.exists()
