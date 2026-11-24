"""Numeric, exact-generation owner traces cannot alter finance or authorization."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
import os
import time

import httpx
import pytest
from pymongo import _csot
from pymongo.errors import PyMongoError
from motor.motor_asyncio import AsyncIOMotorClient

import demand_read_diagnostics as diagnostics
import demand_read_jobs as jobs
import demand_read_store as store
from operational_balance_store import STATES
from operational_financial_activity_runtime import prepare_in_thread
from operational_financial_position import build_financial_position
from operational_workspace_read_builders import authorize_spec, build_projection
from test_operational_balance_demand_metadata_reads import read_db, read_mongo_uri
from test_operational_workspace_demand_http import BASE, HEADERS, application, seed_workspace


def test_phase_context_is_bounded_numeric_and_does_not_swallow_function_errors():
    outer, inner, disabled = {}, {}, {}
    with diagnostics.capture(outer):
        with diagnostics.phase("state_read_ms"):
            pass
        with diagnostics.capture(inner):
            with diagnostics.phase("report_build_ms"):
                pass
        with pytest.raises(ValueError, match="synthetic failure"):
            with diagnostics.phase("state_decode_ms"):
                raise ValueError("synthetic failure")
        with diagnostics.phase("not-a-metric"):
            pass
    with diagnostics.capture(disabled, enabled=False):
        with diagnostics.phase("state_read_ms"):
            pass
    assert set(outer) == {"state_read_ms", "state_decode_ms"}
    assert set(inner) == {"report_build_ms"} and disabled == {}
    assert diagnostics._CURRENT.get() is None
    assert all(type(value) is float and math.isfinite(value) and value >= 0
               for value in [*outer.values(), *inner.values()])


@pytest.mark.parametrize("value", [True, None, "1", float("nan"), float("inf"), -1, 10 ** 1000])
def test_invalid_or_unbounded_numeric_values_are_rejected(value):
    assert diagnostics._number(value) is False


def test_maximal_metric_record_fits_the_existing_bounded_child_stream():
    value = {key: 10 ** 12 for key in store._METRIC_NUMBERS}
    value.update(family="operational_statement", outcome="claim_lost")
    assert store.safe_metrics(value) == value
    assert len((store.METRIC_PREFIX + json.dumps(value) + "\n").encode()) < 2048
    assert store.safe_metrics({**value, "customer_name": "must not escape"}) is None


@pytest.mark.asyncio
async def test_thread_phase_keeps_result_identity_and_request_context():
    item, metrics = {"currency": "SAR", "amount": "12.34"}, {}
    with diagnostics.capture(metrics):
        result = await prepare_in_thread(diagnostics.thread_call("report_build_cpu_ms", lambda: item))
    assert result is item and set(metrics) == {"report_build_cpu_ms"}
    assert diagnostics._CURRENT.get() is None


async def prepared_owner_report(db):
    state = await seed_workspace(db, movements=0)
    state["started_at"] = "2030-04-01T00:00:00+00:00"
    state["engine"]["as_of"] = "2030-04-05T00:00:00+00:00"
    for opening in state["openings"].values():
        opening["created_at"] = state["started_at"]
    await db[STATES].replace_one({"_id": "owner"}, state)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(db)), base_url="http://isolated") as client:
        pending = await client.get(BASE + "/report", params={"view": "employees"}, headers=HEADERS)
        assert pending.status_code == 202, pending.text
        claim = await jobs.claim_next(db)
        assert claim["_id"] == pending.json()["key"]
        assert await jobs.execute_claim(db, claim["_id"], claim["lease_token"], claim["generation"],
            builder=build_projection, authorize=authorize_spec, startup_started=time.monotonic() - .01,
            runner_import_ms=4.0)
        ready = await client.get(BASE + "/report", params={"view": "employees"}, headers=HEADERS)
        assert ready.status_code == 200, ready.text
    return state, claim, ready.json()


def reference(body):
    value = body["read_diagnostics_ref"]
    return {"read_key": value["key"], "read_generation": value["generation"]}


@pytest.mark.asyncio
async def test_actual_http_trace_exposes_only_owner_numeric_phases_without_changing_finance(read_db):
    before, claim, body = await prepared_owner_report(read_db)
    expected = build_financial_position(deepcopy(before), view="employees")
    assert json.dumps(body["currencies"], sort_keys=True, ensure_ascii=False) == json.dumps(expected["currencies"], sort_keys=True, ensure_ascii=False)
    assert await read_db[STATES].find_one({"_id": "owner"}) == before
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(read_db)), base_url="http://isolated") as client:
        plain = await client.get("/api/operational-balances/automation")
        assert plain.status_code == 200 and "read_diagnostics" not in plain.json()
        response = await client.get("/api/operational-balances/automation", params=reference(body))
        assert response.status_code == 200, response.text
        trace = response.json()["read_diagnostics"]
        assert trace["available"] is True and trace["view"] == "employees" and trace["source_revision"] == 5
        required = {"startup_ms", "runner_import_ms", "state_read_ms", "state_decode_ms", "state_decode_cpu_ms",
            "report_build_ms", "report_build_cpu_ms", "report_copy_cpu_ms", "materialize_ms",
            "response_encode_ms", "response_encode_cpu_ms", "response_prepare_ms"}
        assert required <= trace["metrics"].keys()
        assert trace["metrics"]["runner_import_ms"] == 4.0
        assert trace["metrics"]["startup_ms"] >= 10
        assert "builder_import_ms" not in trace["metrics"]  # The pure test builder was injected.
        assert "forecast_sources_ms" not in trace["metrics"]  # Not run means unknown/absent, never invented zero.
        assert all(diagnostics._number(v) for k, v in trace["metrics"].items() if k not in {"family", "outcome"})
        assert "lease_token" not in response.text and claim["lease_token"] not in response.text
        assert "financial-source" not in response.text and "header" not in trace
        employee = await client.get("/api/operational-balances/automation", params=reference(body), headers={"x-test-user": "staff"})
        assert employee.status_code == 403
        for params, headers in [(reference(body), {"x-test-user": "other"}),
                ({**reference(body), "read_generation": "f" * 32}, {}),
                ({**reference(body), "read_key": "e" * 64}, {})]:
            hidden = await client.get("/api/operational-balances/automation", params=params, headers=headers)
            assert hidden.status_code == 200 and hidden.json()["read_diagnostics"] == {"available": False}
        assert (await client.get("/api/operational-balances/automation", params={"read_key": claim["_id"]})).status_code == 422
    assert await read_db[STATES].find_one({"_id": "owner"}) == before


@pytest.mark.asyncio
async def test_actual_child_persists_launch_import_and_build_phases(read_db, read_mongo_uri, monkeypatch):
    before = await seed_workspace(read_db, movements=0)
    before["started_at"] = "2030-04-01T00:00:00+00:00"
    before["engine"]["as_of"] = "2030-04-05T00:00:00+00:00"
    for opening in before["openings"].values():
        opening["created_at"] = before["started_at"]
    await read_db[STATES].replace_one({"_id": "owner"}, before)
    monkeypatch.setenv("MONGO_URL", read_mongo_uri)
    monkeypatch.setenv("DB_NAME", read_db.name)
    monkeypatch.setenv("MEZAN_BACKGROUND_WORKER_PROCESS", "1")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    metrics = []
    monkeypatch.setattr(jobs, "_emit_metrics", lambda value: metrics.append(value))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(read_db)), base_url="http://isolated") as client:
        pending = await client.get(BASE + "/report", params={"view": "employees"}, headers=HEADERS)
        assert pending.status_code == 202, pending.text
        claim = await jobs.claim_next(read_db)
        await asyncio.wait_for(jobs.run_claim(read_db, claim), 30)
        ready = await client.get(BASE + "/report", params={"view": "employees"}, headers=HEADERS)
        assert ready.status_code == 200, (ready.text, metrics)
        trace = (await client.get("/api/operational-balances/automation", params=reference(ready.json()))).json()["read_diagnostics"]
    assert trace["available"] is True and trace["metrics"]["outcome"] == "ready"
    measured = trace["metrics"]
    assert measured["pid"] != os.getpid()
    assert measured["startup_ms"] >= measured["runner_import_ms"] >= 0
    assert measured["builder_import_ms"] >= 0 and measured["report_build_cpu_ms"] >= 0
    assert len(metrics) == 1 and metrics[0]["pid"] == measured["pid"]
    assert await read_db[STATES].find_one({"_id": "owner"}) == before


@pytest.mark.asyncio
async def test_stale_generation_token_actor_and_retention_never_reuse_trace(read_db):
    before, claim, body = await prepared_owner_report(read_db)
    original = (await read_db[store.JOBS].find_one({"_id": claim["_id"]}))["read_diagnostics"]
    metrics = {"family": "operational_report", "outcome": "ready", "build_ms": 999}
    for changed in ({**claim, "generation": "f" * 32}, {**claim, "lease_token": "wrong-token"},
                    {**claim, "actor_id": "staff"}, {**claim, "owner_id": "other"}):
        assert not await diagnostics.save_owner_trace(read_db, changed, metrics)
    assert not await diagnostics.save_delivery_trace(read_db, claim["spec"], claim["_id"], "f" * 32,
                                                     {"response_encode_ms": 999})
    assert (await read_db[store.JOBS].find_one({"_id": claim["_id"]}))["read_diagnostics"] == original
    await read_db[store.JOBS].update_one({"_id": claim["_id"]}, {"$set": {"retire_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    assert await diagnostics.read_owner_trace(read_db, "owner", claim["_id"], claim["generation"]) == {"available": False}
    assert await read_db[STATES].find_one({"_id": "owner"}) == before


@pytest.mark.asyncio
async def test_telemetry_failure_cannot_fail_job_ready_delivery_or_automation_status(read_db, monkeypatch):
    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("private synthetic telemetry failure")
    monkeypatch.setattr(diagnostics, "_io", unavailable)
    before, claim, body = await prepared_owner_report(read_db)
    assert (await read_db[store.JOBS].find_one({"_id": claim["_id"]}))["status"] == "ready"
    monkeypatch.setattr(diagnostics, "read_owner_trace", unavailable)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(read_db)), base_url="http://isolated") as client:
        response = await client.get("/api/operational-balances/automation", params=reference(body))
        assert response.status_code == 200 and response.json()["read_diagnostics"] == {"available": False}
        assert "private synthetic" not in response.text
    assert await read_db[STATES].find_one({"_id": "owner"}) == before


@pytest.mark.asyncio
async def test_owner_authorization_is_rechecked_after_diagnostic_read(read_db, monkeypatch):
    _, _, body = await prepared_owner_report(read_db)
    original = diagnostics.read_owner_trace
    async def revoke(db, *args):
        value = await original(db, *args)
        await db.users.update_one({"id": "owner"}, {"$set": {"disabled": True}})
        return value
    monkeypatch.setattr(diagnostics, "read_owner_trace", revoke)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(read_db)), base_url="http://isolated") as client:
        response = await client.get("/api/operational-balances/automation", params=reference(body))
        assert response.status_code == 403 and "state_decode_ms" not in response.text


@pytest.mark.asyncio
async def test_driver_budget_precedes_dispatch_and_is_cleared_after_io(read_db):
    budgets = []
    def operation():
        budgets.append(_csot.remaining())
        return read_db[store.JOBS].find_one({"_id": "synthetic-missing"}, {"_id": 1})
    assert _csot.remaining() is None
    assert await diagnostics._io(operation) is None
    assert len(budgets) == 1 and 0 < budgets[0] <= diagnostics.IO_SECONDS
    assert _csot.remaining() is None


@pytest.mark.asyncio
async def test_cancelled_telemetry_joins_its_admitted_future_and_preserves_cancellation():
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    entered = asyncio.Event()
    def operation():
        entered.set()
        return future
    task = asyncio.create_task(diagnostics._io(operation))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and not future.cancelled()
    future.set_result(None)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert future.done() and _csot.remaining() is None


@pytest.mark.asyncio
async def test_opaque_or_session_wrappers_cannot_acquire_telemetry_capability(read_db):
    class Opaque:
        __slots__ = ()
        def __getitem__(self, _name):
            raise AssertionError("Unsupported database wrapper must not be accessed")
    assert diagnostics._collection(Opaque()) is None
    assert await diagnostics.read_owner_trace(Opaque(), "owner", "a" * 64, "b" * 32) == {"available": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_actual_stalled_local_socket_has_finite_budget_and_is_joined(cancel):
    connected = asyncio.Event()
    writers, handlers = set(), set()
    async def stalled(reader, writer):
        task = asyncio.current_task()
        writers.add(writer); handlers.add(task)
        connected.set()
        try:
            await reader.read()  # Own local server accepts bytes but never answers Mongo's handshake.
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            writers.discard(writer); handlers.discard(task)
    server = await asyncio.start_server(stalled, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = AsyncIOMotorClient(f"mongodb://127.0.0.1:{port}/?directConnection=true",
        serverSelectionTimeoutMS=5000, connectTimeoutMS=5000, socketTimeoutMS=5000)
    task = None
    try:
        started = time.monotonic()
        task = asyncio.create_task(diagnostics._io(lambda: client.synthetic_traces.jobs.find_one({"_id": "synthetic"})))
        await asyncio.wait_for(connected.wait(), 2)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else PyMongoError):
            await asyncio.wait_for(task, 2)
        assert task.done() and time.monotonic() - started < 2
        assert _csot.remaining() is None
    finally:
        client.close()
        server.close()
        await server.wait_closed()
        for writer in list(writers):
            writer.close()
        if handlers:
            await asyncio.gather(*list(handlers), return_exceptions=True)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_corrupt_or_non_numeric_saved_diagnostics_are_unknown_and_do_not_escape(read_db):
    before, claim, _ = await prepared_owner_report(read_db)
    saved = (await read_db[store.JOBS].find_one({"_id": claim["_id"]}))["read_diagnostics"]
    for bad in ({**saved, "schema_version": True}, {**saved, "source_revision": True},
            {**saved, "metrics": {**saved["metrics"], "customer_name": "private synthetic data"}},
            {**saved, "metrics": {**saved["metrics"], "build_ms": float("nan")}},
            {**saved, "family": "operational_home"}):
        await read_db[store.JOBS].update_one({"_id": claim["_id"]}, {"$set": {"read_diagnostics": bad}})
        assert await diagnostics.read_owner_trace(read_db, "owner", claim["_id"], claim["generation"]) == {"available": False}
    assert await read_db[STATES].find_one({"_id": "owner"}) == before
