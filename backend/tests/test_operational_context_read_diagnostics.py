"""Context timing observes the existing read; it cannot change its contract."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import json
import logging
from threading import Event
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from mongomock_motor import AsyncMongoMockClient

import operational_balance_workspace as workspace
import operational_financial_position_state as financial_state
from operational_balance_routes import make_operational_balance_router
from operational_balance_store import STATES, initial
from operational_financial_activity_runtime import preparation_checkpoint
from test_operational_balance_visible_report_reads import begin_get, response


PATH = "/api/operational-balances/workspace/context"


class Observer:
    """Integration observer for the helper's public API, without sampling clocks.

    The helper's timing, bounds and log sanitization have their own tests. This
    observer makes every requested sample deterministic while exercising the
    real route, database selectors and cooperative worker cancellation.
    """

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.records = []
        self._current = ContextVar("test_context_read_diagnostics", default=None)

    def current(self):
        return self._current.get()

    async def sample(self, scope, awaitable, *, mode=None):
        if not self.enabled:
            return await awaitable
        record = {"scope": scope, "mode": mode, "phases": [], "outcome": "complete"}
        token = self._current.set(record)
        try:
            return await awaitable
        except asyncio.CancelledError:
            record["outcome"] = "cancelled"
            raise
        except BaseException:
            record["outcome"] = "error"
            raise
        finally:
            self._current.reset(token)
            self.records.append(record)

    @contextmanager
    def phase(self, name, *, cpu=False):
        record = self.current()
        if record is not None:
            record["phases"].append((name, cpu))
        yield

    def thread_call(self, name, function):
        if self.current() is None:
            return function

        @wraps(function)
        def measured(*args, **kwargs):
            with self.phase(name, cpu=True):
                return function(*args, **kwargs)

        return measured


class ReadCollection:
    def __init__(self, db, name):
        self.db, self.name = db, name

    async def find_one(self, query, projection=None):
        self.db.calls.append((self.name, "find_one", deepcopy(query), deepcopy(projection)))
        if self.name == STATES and self.db.state_error is not None:
            raise self.db.state_error
        return await self.db.raw[self.name].find_one(query, projection)

    def find(self, query, projection=None):
        self.db.calls.append((self.name, "find", deepcopy(query), deepcopy(projection)))
        return self.db.raw[self.name].find(query, projection)

    def __getattr__(self, name):
        raise AssertionError(f"Read diagnostics attempted {self.name}.{name}")


class ReadDB:
    def __init__(self, raw):
        self.raw, self.calls, self.state_error = raw, [], None

    def __getitem__(self, name):
        return ReadCollection(self, name)

    def __getattr__(self, name):
        return self[name]


async def fixture():
    client = AsyncMongoMockClient()
    raw = client["context_diagnostics_" + uuid4().hex]
    await raw.users.insert_one({"id": "owner", "role": "owner", "name": "Synthetic owner", "is_active": True})
    await raw.mz2_financial_accounts.insert_many([
        {"id": "bank", "user_id": "owner", "name": "Synthetic bank", "account_type": "bank", "currency": "SAR", "status": "active"},
        {"id": "cash", "user_id": "owner", "name": "Synthetic cash", "account_type": "cash", "currency": "SAR", "status": "active"},
    ])
    state = initial("owner")
    state.update(status="active", started_at="2026-11-01T00:00:00+00:00", revision=1)
    state["openings"]["bank:bank"] = {
        "id": "bank:bank", "party_type": "bank", "party_id": "bank", "name": "Synthetic bank",
        "currency": "SAR", "direction": "for_us", "amount": "100.00",
        "created_at": "2026-11-01T00:00:00+00:00", "actor_id": "owner",
    }
    state["requests"] = {"private-receipt": {"result": "must remain private"}}
    state["audit"] = [{"action": "private-history"}]
    await raw[STATES].insert_one(state)
    return client, ReadDB(raw)


def app_for(db):
    app = FastAPI()

    async def principal():
        return {"id": "owner"}

    app.include_router(make_operational_balance_router(db, principal), prefix="/api")
    return app


async def get(db, *, params=None, path=PATH):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(db)), base_url="http://isolated") as http:
        return await http.get(path, params=params)


def install(monkeypatch, observer):
    # raising=False also lets the red baseline exercise the existing route;
    # it fails on absent observations, not on a missing instrumentation module.
    monkeypatch.setattr(workspace, "read_diagnostics", observer, raising=False)
    monkeypatch.setattr(financial_state, "read_diagnostics", observer, raising=False)


@pytest.mark.parametrize("params,mode", [
    ({}, "full"),
    ({"home_preview": True, "provider_positions": True, "ad_bank_fx": True}, "home"),
    ({"directory_only": True, "home_preview": True}, "directory"),
])
def test_sampled_context_has_identical_response_queries_and_financial_state(monkeypatch, params, mode):
    async def scenario():
        client, db = await fixture()
        try:
            before = await db.raw[STATES].find_one({"_id": "owner"})
            off = Observer(enabled=False)
            install(monkeypatch, off)
            expected = await get(db, params=params)
            assert expected.status_code == 200, expected.text
            expected_reads = deepcopy(db.calls)
            db.calls.clear()

            on = Observer()
            install(monkeypatch, on)
            actual = await get(db, params=params)

            assert actual.status_code == 200, actual.text
            assert actual.content == expected.content
            assert db.calls == expected_reads
            assert await db.raw[STATES].find_one({"_id": "owner"}) == before
            assert len(on.records) == 1 and off.records == []
            record = on.records[0]
            assert (record["scope"], record["mode"], record["outcome"]) == ("operational_context", mode, "complete")
            phases = record["phases"]
            assert ("access_initial", False) in phases and ("access_final", False) in phases
            assert ("project", True) in phases
            assert {name for name, _ in phases if name.startswith("catalog.")} == {
                "catalog." + kind for kind in workspace.RESOURCE_KINDS
            }
            state_reads = [call for call in db.calls if call[0] == STATES]
            assert len(state_reads) == 1
            assert state_reads[0][2] == {"_id": "owner", "owner_id": "owner"}
            if mode == "directory":
                assert ("directory_state_read", False) in phases
                assert not any(name in {"state_read", "state_decode", "report"} for name, _ in phases)
                assert ("navigation", True) in phases
                assert state_reads[0][3] == dict.fromkeys((
                    "owner_id", "status", "revision", "started_at", "pos_receivable_binding", "ad_funding",
                    "courier_tariffs.payment_mode", "courier_tariffs.cash_account.id", "engine.ad_balance_sar_basis",
                ), 1)
            else:
                assert all(pair in phases for pair in (
                    ("state_read", False), ("state_decode", True), ("report", True), ("navigation", True),
                ))
                assert state_reads[0][3] == {"requests": 0, "audit": 0}
        finally:
            client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("corrupt_snapshot", [False, True])
def test_sample_does_not_reclassify_read_errors_or_continue_to_catalog(monkeypatch, corrupt_snapshot):
    async def scenario():
        client, db = await fixture()
        try:
            if corrupt_snapshot:
                await db.raw[STATES].update_one({"_id": "owner"}, {"$set": {"revision": "invalid"}})
            else:
                db.state_error = HTTPException(503, detail={"code": "synthetic_storage_unavailable"})
            off = Observer(enabled=False)
            install(monkeypatch, off)
            expected = await get(db)
            expected_reads = deepcopy(db.calls)
            db.calls.clear()
            on = Observer()
            install(monkeypatch, on)

            actual = await get(db)

            assert actual.status_code == expected.status_code == 503
            assert actual.content == expected.content
            assert db.calls == expected_reads
            assert len(on.records) == 1 and on.records[0]["outcome"] == "error"
            assert not any(name.startswith("catalog.") or name == "access_final"
                           for name, _ in on.records[0]["phases"])
        finally:
            client.close()

    asyncio.run(scenario())


def test_context_sample_keeps_fresh_access_recheck_after_computation(monkeypatch):
    original_catalog = workspace.resource_catalog

    async def revoke_after_catalog(db, *args, **kwargs):
        result = await original_catalog(db, *args, **kwargs)
        # A concurrent revocation is simulated in the fixture, not by the read.
        await db.raw.users.update_one({"id": "owner"}, {"$set": {"disabled": True}})
        return result

    monkeypatch.setattr(workspace, "resource_catalog", revoke_after_catalog)

    async def scenario():
        client, db = await fixture()
        try:
            on = Observer()
            install(monkeypatch, on)
            actual = await get(db)
            assert actual.status_code == 403
            assert actual.json()["detail"]["code"] == "operational_actor_inactive"
            assert len(on.records) == 1 and on.records[0]["outcome"] == "error"
            assert ("access_final", False) in on.records[0]["phases"]
            assert [call for call in db.calls if call[0] == STATES] == [
                (STATES, "find_one", {"_id": "owner", "owner_id": "owner"}, {"requests": 0, "audit": 0}),
            ]
        finally:
            client.close()

    asyncio.run(scenario())


def test_legacy_context_does_not_start_workspace_diagnostics(monkeypatch):
    async def scenario():
        client, db = await fixture()
        try:
            on = Observer()
            install(monkeypatch, on)
            actual = await get(db, path="/api/operational-balances/context")
            assert actual.status_code == 200, actual.text
            assert on.records == []
            assert len([call for call in db.calls if call[0] == STATES]) == 1
        finally:
            client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["state_decode", "report"])
@pytest.mark.parametrize("sampled", [False, True])
def test_disconnect_still_cancels_and_joins_the_same_cpu_worker(monkeypatch, phase, sampled):
    async def scenario():
        client, db = await fixture()
        started, stopped = Event(), Event()

        def cpu(*_args, **_kwargs):
            started.set()
            try:
                while True:
                    preparation_checkpoint()
            finally:
                stopped.set()

        target, name = (financial_state, "_report_state") if phase == "state_decode" else (workspace, "report")
        monkeypatch.setattr(target, name, cpu)
        observer = Observer(enabled=sampled)
        install(monkeypatch, observer)
        try:
            before = await db.raw[STATES].find_one({"_id": "owner"})
            task, queue, sent = begin_get(app_for(db), "/workspace/context?home_preview=true")
            assert await asyncio.to_thread(started.wait, 2)
            queue.put_nowait({"type": "http.disconnect"})
            await asyncio.wait_for(task, 2)

            assert stopped.is_set()
            status, body = response(sent)
            assert status == 499 and body["detail"]["code"] == "operational_report_read_cancelled"
            assert await db.raw[STATES].find_one({"_id": "owner"}) == before
            assert len([call for call in db.calls if call[0] == STATES]) == 1
            assert not [other for other in asyncio.all_tasks() if other is not asyncio.current_task()]
            if sampled:
                assert len(observer.records) == 1 and observer.records[0]["outcome"] == "cancelled"
                assert (phase, True) in observer.records[0]["phases"]
                assert ("access_final", False) not in observer.records[0]["phases"]
            else:
                assert observer.records == []
        finally:
            client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("params,mode", [
    ({"home_preview": True}, "home"),
    ({"directory_only": True}, "directory"),
])
def test_real_helper_emits_numeric_stage_evidence_without_payloads(monkeypatch, caplog, params, mode):
    import read_path_diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "_last", {})
    monkeypatch.setattr(diagnostics, "_active", set())
    monkeypatch.setattr(diagnostics, "_current", ContextVar("real_context_read_diagnostics", default=None))

    async def scenario():
        client, db = await fixture()
        try:
            actual = await get(db, params=params)
            assert actual.status_code == 200, actual.text
            assert len([call for call in db.calls if call[0] == STATES]) == 1
        finally:
            client.close()

    with caplog.at_level(logging.INFO, logger="mezan.read_path_diagnostics"):
        asyncio.run(scenario())
    logs = [record.getMessage() for record in caplog.records if record.name == "mezan.read_path_diagnostics"]
    assert len(logs) == 1
    payload = json.loads(logs[0].removeprefix("read_path_sample "))
    assert (payload["scope"], payload["mode"], payload["outcome"]) == ("operational_context", mode, "complete")
    expected_phases = {"access_initial", "access_final", "navigation", "project"} | {
        "catalog." + kind for kind in workspace.RESOURCE_KINDS
    }
    expected_phases |= {"directory_state_read"} if mode == "directory" else {"state_read", "state_decode", "report"}
    assert set(payload["phases"]) == expected_phases
    assert all(type(value) in {int, float} and value >= 0
               for phase in payload["phases"].values() for value in phase.values())
    for phase in ("navigation", "project") if mode == "directory" else ("state_decode", "report", "navigation", "project"):
        assert "sum_thread_cpu_ms" in payload["phases"][phase]
    for private in ("Synthetic owner", "Synthetic bank", "bank:bank", "private-receipt", "must remain private", "private-history"):
        assert private not in logs[0]
    assert diagnostics.current() is None and diagnostics._active == set()


def test_real_helper_does_not_leave_a_sample_or_worker_after_disconnect(monkeypatch, caplog):
    import read_path_diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "_last", {})
    monkeypatch.setattr(diagnostics, "_active", set())
    monkeypatch.setattr(diagnostics, "_current", ContextVar("real_cancel_diagnostics", default=None))

    async def scenario():
        client, db = await fixture()
        started, stopped = Event(), Event()

        def cpu(*_args, **_kwargs):
            started.set()
            try:
                while True:
                    preparation_checkpoint()
            finally:
                stopped.set()

        monkeypatch.setattr(workspace, "report", cpu)
        try:
            task, queue, sent = begin_get(app_for(db), "/workspace/context?home_preview=true")
            assert await asyncio.to_thread(started.wait, 2)
            queue.put_nowait({"type": "http.disconnect"})
            await asyncio.wait_for(task, 2)
            assert response(sent)[0] == 499 and stopped.is_set()
            assert diagnostics.current() is None and diagnostics._active == set()
            assert not [other for other in asyncio.all_tasks() if other is not asyncio.current_task()]
        finally:
            client.close()

    with caplog.at_level(logging.INFO, logger="mezan.read_path_diagnostics"):
        asyncio.run(scenario())
    logs = [record.getMessage() for record in caplog.records if record.name == "mezan.read_path_diagnostics"]
    assert len(logs) == 1
    payload = json.loads(logs[0].removeprefix("read_path_sample "))
    assert payload["outcome"] == "cancelled"
    assert "report" in payload["phases"] and "sum_thread_cpu_ms" in payload["phases"]["report"]
    assert "access_final" not in payload["phases"]
