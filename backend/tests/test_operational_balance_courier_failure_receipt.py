"""A finished target receipt survives only an authorized aggregate failure."""
import asyncio
from copy import deepcopy

from fastapi import APIRouter, FastAPI, HTTPException
import httpx
import pytest
import pymongo
from pymongo.errors import NetworkTimeout

from operational_balance_store import STATES, digest
from operational_courier_transitions import WEBHOOK_PROOF
from operational_demand_refresh import LEASES, refresh_stage
from operational_demand_refresh_routes import VisibleRefreshInput, attach_demand_refresh_routes
from operational_source_diagnostics import source_failure_detail
from salla_shipping import CURRENT_SHIPPING
from test_operational_balance_courier_acquisition import native_provider
from test_operational_balance_courier_waybill_acquisition import binding_gap, seed
from test_operational_balance_delivery_confirmation_acquisition import LocalProofDB
from test_operational_balance_demand_refresh import DB, Request


TARGET = "8891"
NUMBER = "9891"
SCOPE = digest(["owner", "staff"])
PATH = "/api/operational-balances/refresh"
SOURCE_DETAIL = source_failure_detail("operational_source_read_timeout", "unified_orders")


def make_route(database, scope_override=None):
    async def principal():
        return {"id": "staff"}

    async def scope(db, user, permission):
        assert db is database and permission == "reports"
        return {"id": "staff"}, "owner", "mezan2"

    router = APIRouter(prefix="/operational-balances")
    attach_demand_refresh_routes(router, database, principal, scope_override or scope)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return app, router.routes[0].endpoint


async def post(database, *, target=TARGET, scope=None):
    app, _ = make_route(database, scope)
    payload = {"visible": True}
    if target is not None:
        payload.update(courier_order_id=target, expected_session_scope=SCOPE)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
        return await client.post(PATH, json=payload)


def returned_receipt(status="saved", reason=None):
    outcome = {"order_id": TARGET, "status": status}
    if reason is not None:
        outcome["reason"] = reason
    return {"status": "attempted", "courier": {
        "status": "deferred" if status == "unavailable" else "completed",
        "selected": 1, "outcomes": [outcome],
    }}


def stub_acquisition(monkeypatch, receipt):
    import operational_visible_evidence

    async def acquire(db, owner, **options):
        return deepcopy(receipt)

    monkeypatch.setattr(operational_visible_evidence, "acquire_visible_evidence", acquire)


def scoped_deadline(monkeypatch):
    """Arm the real outer timeout only at the behavior being exercised."""
    import operational_demand_refresh as runtime
    deadlines = []

    class AsyncioWithArmedDeadline:
        def __getattr__(self, name):
            return getattr(asyncio, name)

        def timeout(self, seconds):
            assert seconds == runtime.REQUEST_SECONDS
            deadline = asyncio.timeout(None)
            deadlines.append(deadline)
            return deadline

    monkeypatch.setattr(runtime, "asyncio", AsyncioWithArmedDeadline())

    async def arm():
        assert len(deadlines) == 1
        deadlines[0].reschedule(asyncio.get_running_loop().time())

    return arm


def failing_aggregate(monkeypatch, *, kind="source", before_failure=None):
    import operational_balance_service

    async def refresh(db, owner):
        if before_failure:
            await before_failure()
        if kind == "timeout":
            await asyncio.Event().wait()
        detail = SOURCE_DETAIL if kind == "source" else {
            "code": "operational_capacity_requires_archive", "message": "existing capacity failure",
        }
        with refresh_stage("source_collection"):
            with refresh_stage("source_primary_reads"):
                raise HTTPException(409, detail=deepcopy(detail), headers={"X-Refresh-Fixture": "retained"})

    monkeypatch.setattr(operational_balance_service, "refresh", refresh)


@pytest.mark.parametrize("kind", ["source", "capacity", "timeout"])
def test_saved_native_proof_remains_reported_when_real_visible_aggregate_fails(monkeypatch, kind):
    _, source, shipment, histories = binding_gap(TARGET, NUMBER)
    calls = []
    native_provider(monkeypatch, {TARGET: (shipment, histories)}, calls)
    arm = scoped_deadline(monkeypatch) if kind == "timeout" else None
    failing_aggregate(monkeypatch, kind=kind, before_failure=arm)

    async def scenario():
        db = LocalProofDB()
        await seed(db, source)
        before = deepcopy(db.data[STATES])
        response = await post(db)

        assert response.status_code == (408 if kind == "timeout" else 409), response.text
        saved = await db.sources.unified_orders.find_one({"user_id": "owner", "order_id": TARGET})
        assert saved[CURRENT_SHIPPING][WEBHOOK_PROOF]["order_id"] == TARGET
        detail = response.json()["detail"]
        assert detail["session_scope"] == SCOPE
        assert detail["native_evidence"] == {"courier": {
            "status": "completed", "selected": 1,
            "outcomes": [{"order_id": TARGET, "status": "saved"}],
        }}
        if kind == "source":
            assert {key: detail[key] for key in SOURCE_DETAIL} == SOURCE_DETAIL
        if kind != "timeout":
            assert response.headers["X-Refresh-Fixture"] == "retained"
        if kind != "capacity":
            assert any(row["stage"] == "native_evidence" and row["outcome"] == "complete"
                       for row in detail["stage_timings"])
            assert detail["stage_timings"][-1]["stage"] == "aggregate"
        assert db.data[STATES] == before and db.data[LEASES] == {}
        assert calls == [(TARGET, "/orders/" + TARGET + "/histories"), (TARGET, "/shipments")]

    asyncio.run(scenario())


@pytest.mark.parametrize("fault,status,reason", [
    ("blocked", "blocked", "courier_native_shipment_mismatch"),
    ("unavailable", "unavailable", "native_evidence_unavailable"),
])
def test_finished_native_failure_is_not_lost_or_claimed_as_saved(monkeypatch, fault, status, reason):
    from salla_integration import courier_delivery_sync

    _, source, shipment, histories = binding_gap(TARGET, NUMBER)
    if fault == "blocked":
        shipment["id"] = "SYNTHETIC-WRONG-SHIPMENT"
    native_provider(monkeypatch, {TARGET: (shipment, histories)}, [])
    if fault == "unavailable":
        async def unavailable(*args, **kwargs):
            raise httpx.RequestError("SYNTHETIC-PRIVATE-PROVIDER-BODY")
        monkeypatch.setattr(courier_delivery_sync, "call_salla", unavailable)
    failing_aggregate(monkeypatch)

    async def scenario():
        db = LocalProofDB()
        await seed(db, source)
        before = deepcopy(db.data[STATES])
        response = await post(db)
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert detail["native_evidence"] == {"courier": {
            "status": "deferred" if fault == "unavailable" else "completed", "selected": 1,
            "outcomes": [{"order_id": TARGET, "status": status, "reason": reason}],
        }}
        saved = await db.sources.unified_orders.find_one({"user_id": "owner", "order_id": TARGET})
        assert WEBHOOK_PROOF not in saved[CURRENT_SHIPPING]
        assert "SYNTHETIC-PRIVATE" not in response.text
        assert db.data[STATES] == before and db.data[LEASES] == {}

    asyncio.run(scenario())


def test_failure_receipt_reconstructs_only_one_bounded_target_and_fixed_reason(monkeypatch):
    receipt = returned_receipt("blocked", "SYNTHETIC-PRIVATE-REASON")
    receipt.update(private="SYNTHETIC-PRIVATE-TOP", bank={"owner": "SYNTHETIC-PRIVATE-OTHER"})
    receipt["courier"].update(saved=999, private="SYNTHETIC-PRIVATE-COURIER")
    receipt["courier"]["outcomes"][0]["provider_payload"] = {"secret": "SYNTHETIC-PRIVATE-PAYLOAD"}
    stub_acquisition(monkeypatch, receipt)
    failing_aggregate(monkeypatch)

    response = asyncio.run(post(DB()))
    assert response.status_code == 409
    assert response.json()["detail"]["native_evidence"] == {"courier": {
        "status": "completed", "selected": 1,
        "outcomes": [{"order_id": TARGET, "status": "blocked"}],
    }}
    assert "SYNTHETIC-PRIVATE" not in response.text


@pytest.mark.parametrize("receipt", [
    None,
    {"status": "deferred", "courier": {"status": "deferred", "reason": "native_evidence_budget_exhausted"}},
    {"courier": {"status": "completed", "selected": True, "outcomes": []}},
    {"courier": {"status": "completed", "selected": 1, "outcomes": []}},
    {"courier": {"status": "completed", "selected": 1,
                 "outcomes": [{"order_id": "another-order", "status": "saved"}]}},
    {"courier": {"status": "completed", "selected": 1,
                 "outcomes": [{"order_id": TARGET, "status": "pending"}]}},
    {"courier": {"status": "completed", "selected": 1,
                 "outcomes": [{"order_id": TARGET, "status": "saved"},
                              {"order_id": "another-order", "status": "saved"}]}},
])
def test_incomplete_malformed_or_other_order_receipt_is_omitted(monkeypatch, receipt):
    stub_acquisition(monkeypatch, receipt)
    failing_aggregate(monkeypatch)
    response = asyncio.run(post(DB()))
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "native_evidence" not in detail and "session_scope" not in detail


def test_complete_zero_selection_retains_no_attempt_without_claiming_a_save(monkeypatch):
    stub_acquisition(monkeypatch, {"courier": {"status": "completed", "selected": 0, "outcomes": []}})
    failing_aggregate(monkeypatch)
    response = asyncio.run(post(DB()))
    assert response.json()["detail"]["native_evidence"] == {"courier": {
        "status": "completed", "selected": 0, "outcomes": [],
    }}


def test_ordinary_refresh_failure_never_adds_an_order_receipt(monkeypatch):
    stub_acquisition(monkeypatch, returned_receipt())
    failing_aggregate(monkeypatch)
    response = asyncio.run(post(DB(), target=None))
    assert response.status_code == 409
    assert "native_evidence" not in response.json()["detail"]


@pytest.mark.parametrize("status,code", [(400, "invalid"), (401, "unauthorized"), (403, "denied"),
    (499, "operational_refresh_cancelled"), (409, "operational_refresh_in_progress"),
    (409, "operational_courier_target_not_pending"), (408, "unrelated_timeout"), (500, "unexpected")])
def test_unrelated_or_authentication_failure_keeps_original_contract(monkeypatch, status, code):
    import operational_balance_service

    stub_acquisition(monkeypatch, returned_receipt())
    detail = {"code": code, "message": "existing failure"}

    async def fail(db, owner):
        raise HTTPException(status, detail=detail, headers={"X-Refresh-Fixture": "retained"})
    monkeypatch.setattr(operational_balance_service, "refresh", fail)
    response = asyncio.run(post(DB()))
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert response.headers["X-Refresh-Fixture"] == "retained"


@pytest.mark.parametrize("change", ["owner", "actor", "revoked", "unauthenticated"])
def test_scope_is_rechecked_after_failed_aggregate_before_exposing_a_receipt(monkeypatch, change):
    stub_acquisition(monkeypatch, returned_receipt())
    failed = False

    async def change_scope():
        nonlocal failed
        failed = True

    async def scope(db, user, permission):
        if failed and change == "revoked":
            raise HTTPException(403, detail={"code": "operational_actor_inactive"})
        if failed and change == "unauthenticated":
            raise HTTPException(401, detail={"code": "operational_session_expired"})
        return {"id": "another-actor" if failed and change == "actor" else "staff"}, (
            "another-owner" if failed and change == "owner" else "owner"), "mezan2"

    failing_aggregate(monkeypatch, before_failure=change_scope)
    db = DB()
    response = asyncio.run(post(db, scope=scope))
    assert response.status_code == (401 if change == "unauthenticated" else 403)
    assert "native_evidence" not in response.json()["detail"]
    assert "session_scope" not in response.json()["detail"]
    assert db.data[STATES]["owner"]["revision"] == 1 and db.data[LEASES] == {}


def test_disconnect_after_finished_native_read_exposes_no_receipt(monkeypatch):
    import operational_balance_service

    stub_acquisition(monkeypatch, returned_receipt())

    async def scenario():
        db, request = DB(), Request()
        _, endpoint = make_route(db)

        async def refresh(database, owner):
            request.disconnect()
            await asyncio.Event().wait()
        monkeypatch.setattr(operational_balance_service, "refresh", refresh)
        with pytest.raises(HTTPException) as failure:
            await endpoint(VisibleRefreshInput(visible=True, courier_order_id=TARGET,
                expected_session_scope=SCOPE), request, {"id": "staff"})
        assert failure.value.status_code == 499
        assert "native_evidence" not in failure.value.detail
        assert db.data[LEASES] == {} and db.data[STATES]["owner"]["revision"] == 1
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    asyncio.run(scenario())


@pytest.mark.parametrize("unavailable", [NetworkTimeout("SYNTHETIC-PRIVATE-DATABASE"),
    RuntimeError("SYNTHETIC-PRIVATE-UNAVAILABLE"), HTTPException(503, detail="SYNTHETIC-PRIVATE-RECHECK")])
def test_unavailable_optional_scope_recheck_preserves_original_source_failure(monkeypatch, unavailable):
    stub_acquisition(monkeypatch, returned_receipt())
    failed = False

    async def failed_aggregate():
        nonlocal failed
        failed = True

    async def scope(db, user, permission):
        if failed:
            raise unavailable
        return {"id": "staff"}, "owner", "mezan2"

    failing_aggregate(monkeypatch, before_failure=failed_aggregate)
    response = asyncio.run(post(DB(), scope=scope))
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert {key: detail[key] for key in SOURCE_DETAIL} == SOURCE_DETAIL
    assert detail["stage_timings"][-1]["stage"] == "aggregate"
    assert response.headers["X-Refresh-Fixture"] == "retained"
    assert "native_evidence" not in detail and "session_scope" not in detail
    assert "SYNTHETIC-PRIVATE" not in response.text


def test_stalled_optional_scope_recheck_is_bounded_and_joined(monkeypatch):
    import operational_demand_refresh_routes as routes

    stub_acquisition(monkeypatch, returned_receipt())
    monkeypatch.setattr(routes, "_RECEIPT_VERIFY_SECONDS", 0.02, raising=False)

    async def scenario():
        db = DB()
        failed = False
        stopped = asyncio.Event()
        budgets = []

        async def failed_aggregate():
            nonlocal failed
            failed = True

        async def scope(db, user, permission):
            if failed:
                budgets.append(pymongo._csot.remaining())
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()
            return {"id": "staff"}, "owner", "mezan2"

        failing_aggregate(monkeypatch, before_failure=failed_aggregate)
        # The watchdog diagnoses an unbounded recheck; it is not the tested
        # timeout. The small recheck budget begins only after aggregate failure.
        response = await asyncio.wait_for(post(db, scope=scope), timeout=5)
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert {key: detail[key] for key in SOURCE_DETAIL} == SOURCE_DETAIL
        assert "native_evidence" not in detail and "session_scope" not in detail
        assert stopped.is_set() and len(budgets) == 1 and 0 < budgets[0] <= 0.02
        assert db.data[LEASES] == {} and db.data[STATES]["owner"]["revision"] == 1
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    asyncio.run(scenario())


def test_disconnect_during_optional_recheck_preserves_error_without_receipt(monkeypatch):
    stub_acquisition(monkeypatch, returned_receipt())

    async def scenario():
        db, request = DB(), Request()
        failed = False

        async def failed_aggregate():
            nonlocal failed
            failed = True

        async def scope(db, user, permission):
            if failed:
                request.disconnect()
                await asyncio.sleep(0)
            return {"id": "staff"}, "owner", "mezan2"

        failing_aggregate(monkeypatch, before_failure=failed_aggregate)
        _, endpoint = make_route(db, scope)
        with pytest.raises(HTTPException) as failure:
            await endpoint(VisibleRefreshInput(visible=True, courier_order_id=TARGET,
                expected_session_scope=SCOPE), request, {"id": "staff"})
        assert failure.value.status_code == 409
        assert {key: failure.value.detail[key] for key in SOURCE_DETAIL} == SOURCE_DETAIL
        assert "native_evidence" not in failure.value.detail
        assert db.data[LEASES] == {} and db.data[STATES]["owner"]["revision"] == 1

    asyncio.run(scenario())


def test_cancellation_during_optional_recheck_propagates_and_stops_the_recheck(monkeypatch):
    stub_acquisition(monkeypatch, returned_receipt())

    async def scenario():
        db, request = DB(), Request()
        failed = False
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def failed_aggregate():
            nonlocal failed
            failed = True

        async def scope(db, user, permission):
            if failed:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()
            return {"id": "staff"}, "owner", "mezan2"

        failing_aggregate(monkeypatch, before_failure=failed_aggregate)
        _, endpoint = make_route(db, scope)
        task = asyncio.create_task(endpoint(VisibleRefreshInput(visible=True, courier_order_id=TARGET,
            expected_session_scope=SCOPE), request, {"id": "staff"}))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert stopped.is_set() and db.data[LEASES] == {}
            assert not [pending for pending in asyncio.all_tasks() if pending is not asyncio.current_task()]
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_native_timeout_before_receipt_returns_keeps_outcome_unknown(monkeypatch):
    import operational_visible_evidence
    arm = scoped_deadline(monkeypatch)

    async def acquire(db, owner, **options):
        await arm()
        await asyncio.Event().wait()
    monkeypatch.setattr(operational_visible_evidence, "acquire_visible_evidence", acquire)
    db = DB()
    response = asyncio.run(post(db))
    assert response.status_code == 408
    detail = response.json()["detail"]
    assert detail["code"] == "operational_refresh_timeout"
    assert "native_evidence" not in detail and "session_scope" not in detail
    assert db.data[LEASES] == {} and db.data[STATES]["owner"]["revision"] == 1
