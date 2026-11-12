"""Supplier preview requests own and join their CPU work; approvals are separate."""
import asyncio
from copy import deepcopy
import json
from threading import Event
from urllib.parse import urlsplit, urlencode

from fastapi import FastAPI
import pytest

from operational_balance_routes import make_operational_balance_router
from operational_balance_store import initial
from operational_demand_refresh import calculation_checkpoint
from operational_financial_activity_runtime import preparation_checkpoint, preparation_scope


STAMP = "2026-11-01T00:00:00+00:00"
BASE = "/api/operational-balances"


def begin_request(app, path, *, method="GET", payload=None):
    queue, sent = asyncio.Queue(), []
    body = json.dumps(payload).encode() if payload is not None else b""
    queue.put_nowait({"type": "http.request", "body": body, "more_body": False})

    async def receive():
        return await queue.get()

    async def send(message):
        sent.append(message)

    url = urlsplit(BASE + path)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": url.path, "raw_path": url.path.encode(),
             "query_string": url.query.encode(), "headers": [(b"content-type", b"application/json")],
             "client": ("127.0.0.1", 1234), "server": ("isolated", 80), "root_path": ""}
    return asyncio.create_task(app(scope, receive, send)), queue, sent


@pytest.mark.parametrize("stage", ["proposal", "comparison", "inventory", "manifest", "native_batch",
                                  "receiving", "correction", "native_plan", "native_candidates"])
def test_disconnect_joins_supplier_preview_stage_before_closing_snapshot(monkeypatch, stage):
    import fulfillment_v2_routes as fulfillment
    import operational_balance_routes as routes
    import operational_balance_service as service
    import operational_native_purchase_link_service as links
    import operational_native_purchase_review as native
    import operational_receiving_coverage as receiving
    import operational_supplier_invoice_preview as preview

    async def scenario():
        state = initial("owner")
        state.update(status="active", started_at=STAMP, revision=7)
        state["engine"] = {"as_of": STAMP, "facts": {}, "obligations": {}}
        before = deepcopy(state)
        started, stopped = Event(), Event()
        lifecycle = []

        def cpu(*_args, **_kwargs):
            started.set()
            try:
                while True:
                    preparation_checkpoint()
            finally:
                stopped.set()
                lifecycle.append("cpu_stopped")

        class Session:
            in_transaction = False

            async def __aenter__(self):
                lifecycle.append("session_open")
                return self

            async def __aexit__(self, *_args):
                lifecycle.append("session_closed")

            def start_transaction(self, **_kwargs):
                outer = self

                class Transaction:
                    async def __aenter__(self):
                        outer.in_transaction = True

                    async def __aexit__(self, *_args):
                        outer.in_transaction = False
                        lifecycle.append("snapshot_closed")
                return Transaction()

        class Collection:
            def __init__(self, name):
                self.name = name

            def find(self, *_args, **_kwargs):
                return self

            async def to_list(self, *args, **kwargs):
                return [{"id": "invoice", "user_id": "owner"}] if self.name == "purchase_invoices" and stage == "native_batch" else []

            async def find_one(self, *_args, **_kwargs):
                return deepcopy(state)

        class Database:
            def __init__(self):
                self.client = self

            async def start_session(self):
                return Session()

            def __getitem__(self, name):
                return Collection(name)

        db = Database()

        class Scoped:
            def __init__(self, _db, owner, session):
                assert owner == "owner"
                self.db, self.session = _db, session

            def __getitem__(self, name):
                return db[name]

        async def noop(*_args, **_kwargs):
            return None

        async def scope(*_args, **_kwargs):
            return {"id": "owner"}, "owner", None

        async def actor(*_args, **_kwargs):
            return {"merchant_id": "owner"}

        async def read(*_args, **_kwargs):
            return deepcopy(state)

        async def sources(*_args, **_kwargs):
            return {"supplier_receipts": [], "issues": []}

        async def empty(*_args, **_kwargs):
            return []

        monkeypatch.setattr(routes, "scope", scope)
        monkeypatch.setattr(fulfillment, "_actor_context", actor)
        monkeypatch.setattr(fulfillment, "_require_permission", lambda *_args: None)
        monkeypatch.setattr(service, "accounting_inactive", noop)
        monkeypatch.setattr(preview, "SnapshotReads", Scoped)
        monkeypatch.setattr(preview, "read", read)
        monkeypatch.setattr(preview, "collect_sources", sources)
        monkeypatch.setattr(preview, "rows", empty)
        monkeypatch.setattr(preview, "supplier_proposal", lambda *_args: (deepcopy(state), []))
        monkeypatch.setattr(preview, "compare_supplier_invoices", lambda *_args, **_kwargs: {"issues": [], "rows": []})
        monkeypatch.setattr(preview, "native_purchase_inventory", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(native, "read_reporting_entries_v2", empty)

        path, method, payload = "/supplier-invoice-review", "GET", None
        if stage in {"proposal", "comparison", "inventory", "manifest"}:
            target = {"proposal": "supplier_proposal", "comparison": "compare_supplier_invoices",
                      "inventory": "native_purchase_inventory", "manifest": "_complete_preview"}[stage]
            monkeypatch.setattr(preview, target, cpu)
        elif stage == "native_batch":
            monkeypatch.setattr(native, "_review_native_batch", cpu)
        elif stage in {"receiving", "correction"}:
            monkeypatch.setattr(receiving, "compare_receiving", cpu)
            path += ("?receiving_only=true" if stage == "receiving" else
                     "/receiving/invoice/correction-preview?" + urlencode({"expected_as_of": STAMP, "expected_revision": 7}))
        else:
            async def context(*_args, **_kwargs):
                return deepcopy(state), {"id": "invoice"}, {}, {"id": "supplier"}, [], []

            monkeypatch.setattr(links, "_owner", noop)
            monkeypatch.setattr(links, "_read_context", context)
            monkeypatch.setattr(links, "active_gate", noop)
            if stage == "native_plan":
                monkeypatch.setattr(links, "build_link_plan", cpu)
                path, method = "/native-purchase-links/preview", "POST"
                payload = {"invoice_id": "invoice", "evidence_ref": "review-proof"}
            else:
                monkeypatch.setattr(links, "_candidate_result", cpu)
                path = "/native-purchase-links/invoice/candidates"

        app = FastAPI()

        async def principal():
            return {"id": "owner"}

        app.include_router(make_operational_balance_router(db, principal), prefix="/api")
        task, queue, sent = begin_request(app, path, method=method, payload=payload)
        assert await asyncio.to_thread(started.wait, 2), sent
        queue.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(task, 2)
        assert stopped.is_set()
        status = next(message["status"] for message in sent if message["type"] == "http.response.start")
        body = json.loads(b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body"))
        assert status == 499 and body["detail"]["code"] == "operational_report_read_cancelled"
        if "session_open" in lifecycle:
            assert lifecycle.index("cpu_stopped") < lifecycle.index("snapshot_closed") < lifecycle.index("session_closed")
        assert state == before
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["supplier", "receiving", "native_inventory"])
def test_supplier_loops_observe_stop_during_iteration_without_financial_mutation(kind):
    from operational_supplier_reconciliation import compare_supplier_invoices
    from operational_receiving_coverage import compare_receiving
    from operational_supplier_invoice_preview import native_purchase_inventory

    stop = Event()

    class StoppedRows(list):
        def __iter__(self):
            stop.set()
            return super().__iter__()

    state = initial("owner")
    state.update(status="active", started_at=STAMP)
    state["engine"] = {"as_of": STAMP, "obligations": {}, "facts": {}}
    before = deepcopy(state)
    rows = StoppedRows([{"id": "invoice", "user_id": "owner"}])
    with preparation_scope(stop), pytest.raises(asyncio.CancelledError):
        if kind == "supplier":
            compare_supplier_invoices(state, deepcopy(state), rows, owner_id="owner", as_of=STAMP)
        elif kind == "receiving":
            compare_receiving(state, rows, owner="owner", observed_at=STAMP)
        else:
            native_purchase_inventory(state, [], rows, owner="owner", as_of=STAMP)
    assert state == before


def test_reconcile_checkpoint_observes_read_stop_without_a_refresh_context():
    stop = Event()
    with preparation_scope(stop):
        calculation_checkpoint()
        stop.set()
        with pytest.raises(asyncio.CancelledError):
            calculation_checkpoint()
