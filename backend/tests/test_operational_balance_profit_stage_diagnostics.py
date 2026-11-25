"""Profit diagnostics preserve soft failures, financial output and owned cleanup."""
import asyncio
from copy import deepcopy
import json

from pymongo.errors import ExecutionTimeout
import pytest

import operational_profit_sources as source
from operational_balance_automation import safe_trace
from operational_balance_store import STATES
from operational_demand_refresh import (
    LEASES, TIMING_LIMIT, demand_context, refresh_stage, run_visible_refresh,
)
from test_operational_balance_demand_refresh import DB, Request, allowed
from test_operational_balance_profit_context_parallel import ContextBoundary, reached
from test_operational_balance_profit_sources import START, END, values


READERS = {
    "context": "profit_context", "sales": "profit_sales",
    "products": "profit_products", "order_costs": "profit_order_costs",
    "ads": "profit_ads", "payroll": "profit_payroll", "recurring": "profit_recurring",
}
CONTEXT_STAGES = (
    "profit_context_settings", "profit_context_orders", "profit_context_raw",
    "profit_context_prepare",
)


def install_readers(monkeypatch, overrides=None):
    overrides, calls = overrides or {}, []
    amounts = values()
    context = {"orders": [], "settings": {}, "coverage": {"orders": 1, "complete": True}}

    async def load(name):
        calls.append(name)
        if name in overrides:
            return await overrides[name]()
        return deepcopy(context if name == "context" else amounts[name])

    async def load_context(*_args):
        return await load("context")

    monkeypatch.setattr(source, "_load_context", load_context)
    monkeypatch.setattr(source, "_component_readers", lambda *_args: {
        name: (lambda name=name: load(name)) for name in amounts
    })
    return {"owner_id": "owner", "started_at": START}, calls


def settled(database):
    assert database.data.get(LEASES, {}) == {}
    assert all(name == LEASES for name, _, _ in database.writes)
    assert demand_context() is None
    assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]


def stages(context):
    rows = list(context.stage_timings)
    assert safe_trace({"stage_timings": rows})["stage_timings"] == rows
    assert all(set(row) == {"stage", "duration_ms", "outcome"} for row in rows)
    return [(row["stage"], row["outcome"]) for row in rows]


def test_logical_reader_stages_preserve_snapshot_and_only_exist_in_owned_refresh(monkeypatch, caplog):
    caplog.set_level("INFO", logger="operational_demand_refresh")

    async def exercise():
        state, calls = install_readers(monkeypatch)
        before = deepcopy(state)
        database, captured = DB(), []
        saved = deepcopy(database.data[STATES])
        ordinary = await source.load_profit_snapshot(database, "owner", state, as_of=END)
        ordinary_calls = list(calls)
        assert not caplog.records and demand_context() is None
        calls.clear()

        async def operation():
            captured.append(demand_context())
            return await source.load_profit_snapshot(database, "owner", state, as_of=END)

        owned = await run_visible_refresh(Request(), database, "owner", "staff", allowed, operation)
        assert owned == ordinary and state == before
        assert owned["components"]["net_sales"]["amount"] == "100.00"
        assert owned["components"]["cost_of_goods"]["known_subtotal"] == "20.00"
        assert owned["quality"]["source_errors"] == {}
        assert calls == ordinary_calls
        assert set(stages(captured[0])) == {(name, "complete") for name in READERS.values()}
        assert len(captured[0].stage_timings) == len(READERS)
        assert database.data[STATES] == saved
        settled(database)

    asyncio.run(exercise())


@pytest.mark.parametrize("reader", ["context", "products"])
@pytest.mark.parametrize("error_type,code", [
    (ValueError, "operational_profit_source_incomplete"),
    (ExecutionTimeout, "operational_source_read_timeout"),
])
def test_caught_reader_error_is_failed_without_changing_partial_result_or_leaking_details(
        monkeypatch, caplog, reader, error_type, code):
    caplog.set_level("INFO", logger="operational_demand_refresh")
    private = "private-owner order=confidential amount=9182.37"

    async def exercise():
        async def fail():
            raise error_type(private)

        state, calls = install_readers(monkeypatch, {reader: fail})
        database, captured = DB(), []
        ordinary = await source.load_profit_snapshot(database, "owner", state, as_of=END)
        calls.clear()

        async def operation():
            captured.append(demand_context())
            return await source.load_profit_snapshot(database, "owner", state, as_of=END)

        owned = await run_visible_refresh(Request(), database, "owner", "staff", allowed, operation)
        assert owned == ordinary
        assert owned["quality"]["source_errors"] == {reader: code}
        assert owned["quality"]["unknown_is_zero"] is False
        assert (READERS[reader], "failed") in stages(captured[0])
        if reader == "context":
            assert calls == ["context"]
            assert stages(captured[0]) == [("profit_context", "failed")]
        else:
            assert owned["components"]["net_sales"]["amount"] == "100.00"
            assert owned["components"]["cost_of_goods"]["amount"] is None
        assert private not in json.dumps(list(captured[0].stage_timings))
        assert private not in caplog.text
        settled(database)

    asyncio.run(exercise())


@pytest.mark.parametrize("reader", ["context", "products"])
def test_source_deadline_records_cancelled_only_after_reader_cleanup_and_keeps_finished_values(
        monkeypatch, reader):
    async def exercise():
        entered, cancelled, cleanup, finished = (asyncio.Event() for _ in range(4))
        database, captured, task = DB(), [], None

        async def pending():
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await cleanup.wait()
                raise
            finally:
                finished.set()

        state, calls = install_readers(monkeypatch, {reader: pending})

        async def operation():
            captured.append(demand_context())
            with refresh_stage("profit_snapshot"):
                return await source.load_profit_snapshot(database, "owner", state, as_of=END, budget_seconds=.1)

        try:
            task = asyncio.create_task(run_visible_refresh(
                Request(), database, "owner", "staff", allowed, operation))
            await reached(entered)
            await reached(cancelled)
            assert not task.done() and not finished.is_set()
            assert database.data[LEASES]
            assert not any(name == READERS[reader] for name, _ in stages(captured[0]))
            cleanup.set()
            result = await task
            assert finished.is_set()
            assert result["quality"]["source_errors"] == {reader: "operational_source_read_timeout"}
            assert (READERS[reader], "cancelled") in stages(captured[0])
            assert stages(captured[0])[-1] == ("profit_snapshot", "complete")
            if reader == "products":
                assert result["components"]["net_sales"]["amount"] == "100.00"
                assert result["components"]["cost_of_goods"]["amount"] is None
            else:
                assert calls == ["context"]
            settled(database)
        finally:
            cleanup.set()
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


def test_real_context_phase_diagnostics_preserve_queries_native_cohort_and_serial_output(monkeypatch, caplog):
    caplog.set_level("INFO", logger="operational_demand_refresh")

    async def exercise():
        boundary = ContextBoundary(monkeypatch)
        before = deepcopy(boundary.rows)
        try:
            ordinary = await boundary.load()
            ordinary_calls = deepcopy(boundary.calls)
            assert not caplog.records and demand_context() is None
            boundary.calls.clear()
            owned = await boundary.visible(boundary.load)
            assert owned == ordinary and boundary.calls == ordinary_calls
            assert boundary.rows == before and owned["coverage"]["orders"] == 1
            assert stages(boundary.context) == [(stage, "complete") for stage in CONTEXT_STAGES]
            boundary.settled()
        finally:
            await boundary.close_task(None)

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["settings", "orders", "raw", "prepare"])
def test_real_context_failure_preserves_exception_and_identifies_phase(monkeypatch, kind):
    async def exercise():
        boundary = ContextBoundary(monkeypatch)
        error = ValueError("private context evidence")
        if kind == "prepare":
            async def fail_history(*_args, **_kwargs):
                raise error
            monkeypatch.setattr(source, "guard_financial_cohort_history", fail_history)
        else:
            boundary.errors[kind] = error
        try:
            with pytest.raises(ValueError) as failure:
                await boundary.visible(boundary.load)
            assert failure.value is error
            assert ("profit_context_" + kind, "failed") in stages(boundary.context)
            assert "private context evidence" not in json.dumps(list(boundary.context.stage_timings))
            boundary.settled()
        finally:
            await boundary.close_task(None)

    asyncio.run(exercise())


def test_bounded_tail_retains_latest_inner_failure_and_soft_success_wrappers(monkeypatch):
    async def exercise():
        async def fail_authority():
            with refresh_stage("profit_product_authority"):
                raise ValueError("private authority evidence")

        state, _ = install_readers(monkeypatch, {"products": fail_authority})
        database, captured = DB(), []

        async def operation():
            context = demand_context()
            captured.append(context)
            for _ in range(TIMING_LIMIT):
                context.record_stage("source_orders", 1, "complete")
            with refresh_stage("aggregate"):
                with refresh_stage("profit_snapshot"):
                    return await source.load_profit_snapshot(database, "owner", state, as_of=END)

        result = await run_visible_refresh(Request(), database, "owner", "staff", allowed, operation)
        trace = stages(captured[0])
        assert len(trace) == TIMING_LIMIT
        assert ("profit_context", "complete") in trace
        assert ("profit_product_authority", "failed") in trace
        assert ("profit_products", "failed") in trace
        assert trace[-2:] == [("profit_snapshot", "complete"), ("aggregate", "complete")]
        assert result["quality"]["source_errors"] == {"products": "operational_profit_source_incomplete"}
        settled(database)

    asyncio.run(exercise())


def test_unknown_future_reader_name_is_never_used_as_a_diagnostic_label(monkeypatch, caplog):
    caplog.set_level("INFO", logger="operational_demand_refresh")

    async def exercise():
        state, _ = install_readers(monkeypatch)
        database, captured = DB(), []

        async def future_reader():
            return {}

        monkeypatch.setattr(source, "_component_readers", lambda *_: {"private-owner-fixture": future_reader})

        async def operation():
            captured.append(demand_context())
            return await source.load_profit_snapshot(database, "owner", state, as_of=END)

        result = await run_visible_refresh(Request(), database, "owner", "staff", allowed, operation)
        assert result["quality"]["source_errors"] == {}
        assert stages(captured[0]) == [("profit_context", "complete")]
        assert "private-owner-fixture" not in caplog.text
        settled(database)

    asyncio.run(exercise())
