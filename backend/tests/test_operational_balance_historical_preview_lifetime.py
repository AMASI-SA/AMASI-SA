"""Historical review reads end with their visible request, without approval writes."""
import asyncio
from copy import deepcopy
import json
from threading import Event

from fastapi import FastAPI
import pytest

from operational_balance_routes import make_operational_balance_router
from operational_balance_store import initial
from operational_financial_activity_runtime import preparation_checkpoint, preparation_scope


STAMP = "2026-11-01T00:00:00+00:00"


def request(app, path, *, payload=None):
    queue, sent = asyncio.Queue(), []
    queue.put_nowait({"type": "http.request", "body": json.dumps(payload).encode() if payload else b"",
                     "more_body": False})

    async def receive():
        return await queue.get()

    async def send(message):
        sent.append(message)

    url = "/api/operational-balances" + path
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST" if payload else "GET", "scheme": "http", "path": url,
        "raw_path": url.encode(), "query_string": b"", "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1234), "server": ("isolated", 80), "root_path": ""}
    return asyncio.create_task(app(scope, receive, send)), queue, sent


@pytest.mark.parametrize("kind", ["shipping", "provider"])
@pytest.mark.parametrize("stage", ["sources", "calculation"])
def test_disconnect_stops_historical_review_before_snapshot_closes(monkeypatch, kind, stage):
    import operational_balance_routes as routes
    import operational_historical_shipping_service as shipping
    import operational_provider_history_service as providers
    from operational_provider_history import HISTORICAL

    async def scenario():
        state = initial("owner")
        state.update(status="active", started_at=STAMP, revision=7)
        state["engine"] = {"as_of": STAMP, "facts": {}, "obligations": {}, "issues": [{
            "code": HISTORICAL, "source_id": "provider:order", "history_proof_hash": "proof",
            "history_created_at": STAMP, "order_page_balance_review": {"order_id": "order"}}]}
        before = deepcopy(state)
        entered, stopped, release = Event(), Event(), Event()
        lifecycle = []

        def calculation(*_args, **_kwargs):
            entered.set()
            try:
                while not release.wait(.001):
                    preparation_checkpoint()
                return ([], [], []) if kind == "shipping" else {"preview_hash": "hash"}
            finally:
                stopped.set()
                lifecycle.append("work_stopped")

        async def blocked_source():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
                lifecycle.append("work_stopped")

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                lifecycle.append("session_closed")

            def start_transaction(self, **_kwargs):
                class Snapshot:
                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *_args):
                        lifecycle.append("snapshot_closed")
                return Snapshot()

        class Database:
            def __init__(self):
                self.client = self

            async def start_session(self):
                return Session()

        async def noop(*_args, **_kwargs):
            return None

        async def scope(*_args, **_kwargs):
            return {"id": "owner"}, "owner", None

        async def read(*_args, **_kwargs):
            if kind == "provider" and stage == "sources":
                await blocked_source()
            return deepcopy(state)

        async def sources(*_args, **_kwargs):
            if stage == "sources":
                await blocked_source()
            return {"historical_fee_candidates": [{"order_number": "10", "order_id": "order"}], "issues": []}

        async def unexpected_write(*_args, **_kwargs):
            raise AssertionError("a historical preview must not write")

        monkeypatch.setattr(routes, "scope", scope)
        service = shipping if kind == "shipping" else providers
        monkeypatch.setattr(service, "read", read)
        monkeypatch.setattr(service, "active_gate", noop)
        monkeypatch.setattr(service, "mutate", unexpected_write)
        monkeypatch.setattr(shipping, "_owner", noop)
        monkeypatch.setattr(shipping, "SnapshotReads", lambda db, owner, session: db)
        monkeypatch.setattr(shipping, "collect_sources", sources)
        monkeypatch.setattr(shipping, "review", calculation)
        monkeypatch.setattr(providers, "preview_row", calculation)

        async def principal():
            return {"id": "owner"}

        app = FastAPI()
        app.include_router(make_operational_balance_router(Database(), principal), prefix="/api")
        path = "/historical-shipping-fees/preview" if kind == "shipping" else "/provider-history-reviews"
        task, queue, sent = request(app, path, payload={"order_number": "10"} if kind == "shipping" else None)
        try:
            assert await asyncio.to_thread(entered.wait, 2), sent
            queue.put_nowait({"type": "http.disconnect"})
            done, _ = await asyncio.wait({task}, timeout=.5)
            assert task in done, "historical review continued after page disconnect"
            await task
            assert stopped.is_set()
            assert next(m["status"] for m in sent if m["type"] == "http.response.start") == 499
            if kind == "shipping":
                assert lifecycle.index("work_stopped") < lifecycle.index("snapshot_closed") < lifecycle.index("session_closed")
            assert state == before
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["shipping", "provider"])
def test_historical_review_loops_observe_stop_without_mutation(kind):
    from operational_historical_shipping_fee import review
    from operational_provider_history_service import _candidates

    stop = Event()

    class StoppedRows(list):
        def __iter__(self):
            stop.set()
            return super().__iter__()

    state = initial("owner")
    state.update(status="active", started_at=STAMP)
    state["engine"] = {"as_of": STAMP, "facts": {}, "obligations": {}, "issues": StoppedRows([{}])}
    state["movements"] = StoppedRows([{}])
    before = deepcopy(state)
    candidate = {"order_id": "order", "obligation_id": "shipping:order:base_shipping",
        "party_id": "courier", "party_type": "courier", "currency": "SAR", "delivery_hash": "proof"}
    with preparation_scope(stop), pytest.raises(asyncio.CancelledError):
        if kind == "shipping":
            review(state, candidate)
        else:
            _candidates(state)
    assert state == before
