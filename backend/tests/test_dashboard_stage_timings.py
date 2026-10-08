"""Dashboard timing separates admission from execution without changing admission."""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
import dashboard_v2_routes as routes


def metrics(caplog):
    return [json.loads(r.getMessage().removeprefix("resource_stage ")) for r in caplog.records
            if r.name == "resource_governor" and r.getMessage().startswith("resource_stage ")]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["complete", "blocked", "exception", "cancel_admission", "cancel_execution"])
async def test_stage_timing_outcomes_preserve_errors_and_governor_contract(monkeypatch, caplog, outcome):
    clock = [100.0]
    calls = []
    monkeypatch.setattr(routes, "monotonic", lambda: clock[0])
    caplog.set_level(logging.INFO, logger="resource_governor")

    @asynccontextmanager
    async def heavy(kind, *, task_name):
        calls.append((kind, task_name))
        clock[0] += .25
        if outcome == "blocked":
            raise routes.ResourcePressure("resource_pressure")
        if outcome == "cancel_admission":
            raise asyncio.CancelledError()
        try:
            yield None
        finally:
            calls.append("released")
    monkeypatch.setattr(routes, "governor", SimpleNamespace(heavy=heavy))
    error = ValueError("same error identity")
    result = {"unchanged": True}
    executed = []

    @routes._heavy_dashboard_stage("test-stage")
    async def stage(value):
        executed.append(value)
        clock[0] += .5
        if outcome == "exception":
            raise error
        if outcome == "cancel_execution":
            raise asyncio.CancelledError()
        return result

    if outcome == "complete":
        assert await stage("argument") is result
    elif outcome == "blocked":
        with pytest.raises(HTTPException) as failure:
            await stage("argument")
        assert failure.value.status_code == 503
        assert failure.value.detail == {"code": "resource_pressure", "retryable": True, "data_complete": False}
    elif outcome == "exception":
        with pytest.raises(ValueError) as failure:
            await stage("argument")
        assert failure.value is error
    else:
        with pytest.raises(asyncio.CancelledError):
            await stage("argument")
    entered = outcome not in {"blocked", "cancel_admission"}
    assert calls == [("dashboard", "test-stage")] + (["released"] if entered else [])
    assert executed == (["argument"] if entered else [])
    rows = metrics(caplog)
    assert len(rows) == 1
    row = rows[0]
    assert row["admission_wait_ms"] == 250.0
    assert row["execution_ms"] == (500.0 if entered else 0.0)
    assert row["total_ms"] == (750.0 if entered else 250.0)
    assert row["duration_ms"] >= 0  # Original StageMetric field remains present.
    assert row["concurrency"] == 1
    assert row["status"] == ("complete" if outcome == "complete" else "blocked" if outcome == "blocked" else "failed")
    assert row["reason"] == ({"complete": None, "blocked": "resource_pressure", "exception": "ValueError"}.get(outcome, "CancelledError"))


@pytest.mark.asyncio
async def test_actual_task_cancellation_while_waiting_does_not_execute(monkeypatch, caplog):
    waiting = asyncio.Event()
    caplog.set_level(logging.INFO, logger="resource_governor")
    @asynccontextmanager
    async def heavy(kind, *, task_name):
        waiting.set()
        await asyncio.Event().wait()
        yield None
    monkeypatch.setattr(routes, "governor", SimpleNamespace(heavy=heavy))
    executed = []
    @routes._heavy_dashboard_stage("waiting-stage")
    async def stage():
        executed.append(True)
    task = asyncio.create_task(stage())
    await waiting.wait()
    await asyncio.sleep(.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not executed
    row, = metrics(caplog)
    assert row["admission_wait_ms"] > 0
    assert row["execution_ms"] == 0
    assert row["total_ms"] == row["admission_wait_ms"]
    assert row["status"] == "failed" and row["reason"] == "CancelledError"
