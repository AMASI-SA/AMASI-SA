"""Only a visible request may acquire source proof; cancellation never posts money."""
import asyncio
from copy import deepcopy

from fastapi import FastAPI, HTTPException
import httpx
import pytest

from operational_balance_archive import compact_financial_snapshot, compact_issues
from operational_balance_store import STATES
from operational_demand_refresh import LEASES, run_visible_refresh
import operational_visible_evidence as acquisition
from salla_integration import bank_payment_sync, courier_delivery_sync, paid_sar_sync
from test_operational_balance_demand_refresh import DB, Request, allowed


async def visible_refresh(db):
    """Exercise the real user action, permission gate, acquisition and final CAS."""
    from operational_balance_routes import make_operational_balance_router

    async def current_user():
        return {"id": "owner", "_session_client": "web"}

    app = FastAPI()
    app.include_router(make_operational_balance_router(db, current_user), prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
        response = await client.post("/api/operational-balances/refresh", json={"visible": True})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "refreshed"
    return response.json()


def with_gaps(*, packed=False):
    db = DB()
    state = db.data[STATES]["owner"]
    state["engine"] = {"issues": [
        {"code": "operational_order_bank_binding_incomplete", "source_id": "bank-order"},
        {"code": "operational_delivery_timestamp_missing", "source_id": "courier-order"}],
        "customer_tender_coverage": {"sar": {"proof_lookups": [{"order_id": "sar-order", "order_number": "17"}]}}}
    if packed:
        state = compact_financial_snapshot(state, 100000)
        db.data[STATES]["owner"] = compact_issues(state, 100000)
    return db


def test_acquisition_and_stale_worker_have_no_capability_outside_a_visible_request():
    from operational_balance_worker import tick, run

    class NoReads:
        def __getitem__(self, key):
            raise AssertionError("idle worker accessed a collection")

    async def scenario():
        db = NoReads()
        await tick(db)
        await run(db)
        with pytest.raises(HTTPException) as failure:
            await acquisition.acquire_visible_evidence(db, "owner")
        assert failure.value.detail["code"] == "operational_visible_refresh_required"
    asyncio.run(scenario())


@pytest.mark.parametrize("packed", [False, True])
def test_saved_source_gaps_include_paid_sar_and_share_one_bounded_visible_stage(monkeypatch, packed):
    async def scenario():
        db, request, calls = with_gaps(packed=packed), Request(), []
        before = deepcopy(db.data[STATES])

        def adapter(name):
            async def acquire(guarded, owner, gaps, **options):
                calls.append((name, owner, deepcopy(gaps), options))
                await guarded.unified_orders.find_one({"user_id": owner})
            return acquire

        monkeypatch.setattr(bank_payment_sync, "acquire_missing", adapter("bank"))
        monkeypatch.setattr(courier_delivery_sync, "acquire_missing", adapter("courier"))
        monkeypatch.setattr(paid_sar_sync, "acquire_missing", adapter("sar"))
        result = await run_visible_refresh(request, db, "owner", "owner", allowed,
            lambda: acquisition.acquire_visible_evidence(db, "owner"))
        assert result == {"status": "attempted", "bank_candidates": 1, "courier_candidates": 1, "sar_candidates": 1}
        assert [name for name, *_ in calls] == ["bank", "courier", "sar"]
        assert all(owner == "owner" and options["limit"] == 4 for _, owner, _, options in calls)
        assert calls[2][2] == [{"order_id": "sar-order", "order_number": "17"}]
        assert db.data[STATES] == before and db.data[LEASES] == {}
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())


def test_permission_revoked_after_native_read_prevents_proof_write_and_financial_refresh(monkeypatch):
    async def scenario():
        db, request, revoked, refreshed = with_gaps(), Request(), False, False
        before = deepcopy(db.data[STATES])

        async def verify():
            if revoked:
                raise HTTPException(403, detail={"code": "operational_actor_inactive"})

        async def acquire(guarded, owner, issues, **options):
            nonlocal revoked
            await guarded.unified_orders.find_one({"user_id": owner})
            revoked = True  # User or owner binding changed while native proof was read.
            await guarded.unified_orders.update_one({"user_id": owner}, {"$set": {"proof": True}})

        async def operation():
            nonlocal refreshed
            await acquisition.acquire_visible_evidence(db, "owner")
            refreshed = True

        monkeypatch.setattr(bank_payment_sync, "acquire_missing", acquire)
        with pytest.raises(HTTPException) as failure:
            await run_visible_refresh(request, db, "owner", "owner", verify, operation)
        assert failure.value.status_code == 403
        assert not refreshed and db.data[STATES] == before
        assert not [write for write in db.writes if write[0] == "unified_orders"]
        assert db.data[LEASES] == {}
    asyncio.run(scenario())


def test_disconnect_cancels_and_joins_native_acquisition_before_refresh(monkeypatch):
    async def scenario():
        db, request = with_gaps(), Request()
        entered, stopped = asyncio.Event(), asyncio.Event()
        refreshed = False

        async def acquire(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async def operation():
            nonlocal refreshed
            await acquisition.acquire_visible_evidence(db, "owner")
            refreshed = True

        monkeypatch.setattr(bank_payment_sync, "acquire_missing", acquire)
        work = asyncio.create_task(run_visible_refresh(request, db, "owner", "owner", allowed, operation))
        await entered.wait()
        request.disconnect()
        with pytest.raises(HTTPException) as failure:
            await work
        assert failure.value.status_code == 499 and stopped.is_set() and not refreshed
        assert db.data[LEASES] == {} and db.data[STATES]["owner"]["revision"] == 1
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())


def test_budget_joins_native_read_and_leaves_saved_gaps_for_the_visible_refresh(monkeypatch):
    monkeypatch.setattr(acquisition, "ACQUISITION_SECONDS", .01)

    async def scenario():
        db, request, stopped = with_gaps(), Request(), asyncio.Event()
        before = deepcopy(db.data[STATES])

        async def acquire(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        monkeypatch.setattr(bank_payment_sync, "acquire_missing", acquire)
        result = await run_visible_refresh(request, db, "owner", "owner", allowed,
            lambda: acquisition.acquire_visible_evidence(db, "owner"))
        assert result["status"] == "deferred" and result["reason"] == "native_evidence_budget_exhausted"
        assert stopped.is_set() and db.data[STATES] == before and db.data[LEASES] == {}
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())
