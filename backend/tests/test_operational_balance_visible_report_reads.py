"""Real ASGI disconnects own report reads and cancel only the last cache waiter."""
import asyncio
from copy import deepcopy
import json
from threading import Event
from urllib.parse import urlsplit

from fastapi import FastAPI
import pytest

from operational_balance_routes import make_operational_balance_router
from operational_balance_store import STATES
from operational_financial_activity_runtime import preparation_checkpoint
from operational_read_runtime import SnapshotReadCache
from test_operational_balance_supplier_workspace import ReadOnlyDB


BASE = "/api/operational-balances"


def app_for(db):
    app = FastAPI()

    async def principal():
        return {"id": "owner"}

    app.include_router(make_operational_balance_router(db, principal), prefix="/api")
    return app


def begin_get(app, path):
    queue, sent = asyncio.Queue(), []
    queue.put_nowait({"type": "http.request", "body": b"", "more_body": False})

    async def receive():
        return await queue.get()

    async def send(message):
        sent.append(message)

    url = urlsplit(BASE + path)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "GET", "scheme": "http", "path": url.path,
             "raw_path": url.path.encode(), "query_string": url.query.encode(), "headers": [],
             "client": ("127.0.0.1", 1234), "server": ("isolated", 80), "root_path": ""}
    return asyncio.create_task(app(scope, receive, send)), queue, sent


def response(sent):
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return start["status"], json.loads(body)


async def wait_for_waiters(cache, count):
    for _ in range(100):
        if cache.inflight and next(iter(cache.inflight.values()))["waiters"] == count:
            return
        await asyncio.sleep(0)
    raise AssertionError("report readers did not share the snapshot build")


@pytest.mark.parametrize("path", ["/reports", "/financial-position", "/cost-exposure", "/suppliers",
                                   "/obligations?party_type=supplier&party_id=supplier"])
def test_visible_get_keeps_normal_response_and_never_writes(monkeypatch, path):
    import operational_open_cost_exposure as exposure

    async def scenario():
        db = ReadOnlyDB()
        before = deepcopy(db.data)

        async def preview(_db, owner, state, *, as_of=None):
            return {"status": "preview", "as_of": as_of, "engine_as_of": as_of,
                    "started_at": state["started_at"], "currencies": {}, "groups": [], "issues": [], "complete": True}

        monkeypatch.setattr(exposure, "project", preview)
        task, _, sent = begin_get(app_for(db), path)
        await asyncio.wait_for(task, 2)
        status, body = response(sent)
        assert status == 200, body
        assert db.data == before
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["/reports", "/financial-position", "/suppliers",
                                   "/obligations?party_type=supplier&party_id=supplier"])
def test_disconnect_stops_and_joins_report_cpu_before_http_finishes(monkeypatch, path):
    import operational_balance_routes as routes
    import operational_financial_position as position
    import operational_open_cost_exposure as exposure
    import operational_supplier_workspace as suppliers

    async def scenario():
        db = ReadOnlyDB()
        before = deepcopy(db.data)
        started, stopped = Event(), Event()

        def cpu(*_args, **_kwargs):
            started.set()
            try:
                while True:
                    preparation_checkpoint()
            finally:
                stopped.set()

        async def no_cost(*_args, **_kwargs):
            return None

        target, name = {"/reports": (routes, "report"), "/financial-position": (position, "build_financial_position"),
                        "/suppliers": (suppliers, "supplier_workspace"),
                        "/obligations?party_type=supplier&party_id=supplier": (routes, "report")}[path]
        monkeypatch.setattr(target, name, cpu)
        monkeypatch.setattr(exposure, "project", no_cost)
        task, queue, sent = begin_get(app_for(db), path)
        assert await asyncio.to_thread(started.wait, 2)
        queue.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(task, 2)
        assert stopped.is_set()
        status, body = response(sent)
        assert status == 499 and body["detail"]["code"] == "operational_report_read_cancelled"
        assert db.data == before
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())


def test_disconnected_cost_reader_keeps_other_reader_and_completed_cache(monkeypatch):
    import operational_open_cost_exposure as exposure

    async def scenario():
        db = ReadOnlyDB()
        db.data[STATES][0]["revision"] = 1
        before = deepcopy(db.data)
        cache, entered, release, stopped = SnapshotReadCache(), asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = 0

        async def source(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            entered.set()
            try:
                await release.wait()
                preparation_checkpoint()
                return {"status": "preview", "amount": "20.00"}
            finally:
                stopped.set()

        monkeypatch.setattr(exposure, "_READ_CACHE", cache)
        monkeypatch.setattr(exposure, "_project", source)
        app = app_for(db)
        first, first_queue, first_sent = begin_get(app, "/cost-exposure")
        second, _, second_sent = begin_get(app, "/cost-exposure")
        await entered.wait()
        await wait_for_waiters(cache, 2)
        first_queue.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(first, 2)
        assert response(first_sent)[0] == 499
        assert not stopped.is_set() and not second.done() and calls == 1
        release.set()
        await asyncio.wait_for(second, 2)
        assert response(second_sent) == (200, {"status": "preview", "amount": "20.00"})
        third, _, third_sent = begin_get(app, "/cost-exposure")
        await asyncio.wait_for(third, 2)
        assert response(third_sent)[0] == 200 and calls == 1
        assert cache.entries and not cache.inflight
        assert db.data == before
    asyncio.run(scenario())


def test_last_cost_reader_cancels_all_sources_and_does_not_cache_partial_result(monkeypatch):
    import operational_open_cost_exposure as exposure

    async def scenario():
        db = ReadOnlyDB()
        db.data[STATES][0]["revision"] = 1
        before = deepcopy(db.data)
        cache, entered, stopped = SnapshotReadCache(), asyncio.Event(), asyncio.Event()

        async def source(*_args, **_kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        monkeypatch.setattr(exposure, "_READ_CACHE", cache)
        monkeypatch.setattr(exposure, "_project", source)
        task, queue, sent = begin_get(app_for(db), "/cost-exposure")
        await entered.wait()
        queue.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(task, 2)
        assert response(sent)[0] == 499 and stopped.is_set()
        assert not cache.entries and not cache.inflight
        assert db.data == before
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())
