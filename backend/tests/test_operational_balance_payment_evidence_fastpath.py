"""Empty payment evidence adds no refresh I/O; real evidence keeps admission."""
import asyncio
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from threading import Event, get_ident

from fastapi import HTTPException
import pytest

import operational_balance_store as store
import operational_demand_refresh as demand
import operational_payment_cas_reconciliation as evidence
from test_operational_balance_bank_refresh_plan import (
    CountedCollection, CountedDB, Request, claim_rows, isolate_sources, visible_refresh,
)


COVERAGE = "payment_event_evidence_coverage"
ZERO_COVERAGE = {"recognized": 0, "pending": 0, "conflict": 0}


def payment(**changes):
    return {"kind": "payment", "native_ref": "tx-1", "order_id": "order-1",
            "provider": "tabby", "amount": "300.00", "status": "evidence_received",
            **changes}


def financial_state(state):
    return deepcopy({**{key: state[key] for key in ("openings", "movements", "requests", "audit")},
                     "facts": state["engine"]["facts"]})


@pytest.fixture
def measured_refresh(monkeypatch):
    # Isolate source/profit preparation, retaining the actual refresh service,
    # evidence calculation, authorization, lease, archive preparation and CAS.
    isolate_sources(monkeypatch, [])
    db = CountedDB()
    state = db.data[store.STATES]["owner"]
    state["openings"] = {"bank": {"amount": "2000.00", "currency": "SAR"}}
    state["movements"] = [{"id": "saved-bank-receipt", "request_id": "saved-request",
                           "amount": "50.00", "currency": "SAR", "bank_id": "bank",
                           "direction": "incoming", "kind": "collection"}]
    state["engine"]["facts"] = {
        "capture:tx-1": {"order_id": "order-1", "provider": "tabby", "amount": "300.00"}}
    state["engine"][COVERAGE] = {"recognized": 9, "pending": 8, "conflict": 7}

    samples, dispatches = [], []
    active = False
    original_stage, original_thread = demand.refresh_stage, asyncio.to_thread

    @contextmanager
    def observe_stage(name):
        nonlocal active
        with original_stage(name):
            if name != "payment_evidence_link":
                yield
                return
            reads, checks = db.reads.copy(), db.checks
            writes, threads = len(db.writes), len(dispatches)
            active = True
            try:
                yield
            finally:
                active = False
                samples.append({
                    "checks": db.checks - checks, "reads": db.reads - reads,
                    "writes": Counter((collection, action) for collection, action, _ in db.writes[writes:]),
                    "threads": dispatches[threads:],
                })

    async def observe_thread(function, *args, **kwargs):
        if active:
            dispatches.append(function.__name__)
        return await original_thread(function, *args, **kwargs)

    monkeypatch.setattr(demand, "refresh_stage", observe_stage)
    monkeypatch.setattr(asyncio, "to_thread", observe_thread)
    return db, samples


def assert_empty_stage(sample):
    assert sample == {"checks": 0, "reads": Counter(), "writes": Counter(), "threads": []}


def assert_guarded_stage(sample):
    assert sample == {
        "checks": 2,
        "reads": Counter({"users": 2, store.STATES: 2, "mz2_atomic_owners": 2, "settings": 2}),
        "writes": Counter({(demand.LEASES, "renew"): 2}),
        "threads": ["reconcile_staged_payment_evidence"],
    }


@pytest.mark.parametrize("present", [False, True], ids=["absent", "empty"])
def test_empty_evidence_has_no_extra_io_and_resets_coverage(measured_refresh, present):
    async def scenario():
        db, samples = measured_refresh
        state = db.data[store.STATES]["owner"]
        if present:
            state[evidence.INBOX] = {}
        before = financial_state(state)
        for _ in range(2):
            result = await visible_refresh(db)
            assert result["engine"][COVERAGE] == ZERO_COVERAGE
            assert financial_state(result) == before
            assert (evidence.INBOX in result) is present
            assert_empty_stage(samples[-1])
        assert claim_rows(db) == [] and db.data[demand.LEASES] == {}
    asyncio.run(scenario())


@pytest.mark.parametrize("record,counts,status", [
    (payment(), {"recognized": 1, "pending": 0, "conflict": 0}, "already_recognized"),
    (payment(native_ref="missing"), {"recognized": 0, "pending": 1, "conflict": 0}, "evidence_received"),
    (payment(status="conflict"), {"recognized": 0, "pending": 0, "conflict": 1}, "conflict"),
], ids=["recognized", "pending", "conflict"])
def test_nonempty_evidence_keeps_admission_and_replay(measured_refresh, record, counts, status):
    async def scenario():
        db, samples = measured_refresh
        state = db.data[store.STATES]["owner"]
        state[evidence.INBOX] = {"proof": deepcopy(record)}
        before = financial_state(state)
        for _ in range(2):
            result = await visible_refresh(db)
            assert result["engine"][COVERAGE] == counts
            assert result[evidence.INBOX]["proof"]["status"] == status
            assert financial_state(result) == before
            assert_guarded_stage(samples[-1])
        assert claim_rows(db) == [] and db.data[demand.LEASES] == {}
    asyncio.run(scenario())


@pytest.mark.parametrize("inbox", [None, [], "", False, 0, {"proof": None}])
def test_malformed_inbox_cannot_enter_empty_fastpath(measured_refresh, inbox):
    async def scenario():
        db, samples = measured_refresh
        state = db.data[store.STATES]["owner"]
        state[evidence.INBOX] = inbox
        before = deepcopy(state)
        with pytest.raises(ValueError, match="operational_payment_evidence_(scope|record)_invalid"):
            await visible_refresh(db)
        assert samples[-1]["threads"] == ["reconcile_staged_payment_evidence"]
        assert samples[-1]["checks"] == 1  # Admission still runs before rejection.
        assert db.data[store.STATES]["owner"] == before
        assert not [write for write in db.writes if write[0] == store.STATES]
        assert db.data[demand.LEASES] == {}
    asyncio.run(scenario())


@pytest.mark.parametrize("populated", [False, True], ids=["empty", "nonempty"])
def test_revoked_owner_cannot_save_evidence_result(measured_refresh, monkeypatch, populated):
    async def scenario():
        db, _ = measured_refresh
        state = db.data[store.STATES]["owner"]
        state[evidence.INBOX] = {"proof": payment()} if populated else {}
        before = deepcopy(state)
        original = evidence.reconcile_staged_payment_evidence

        def reconcile(candidate):
            result = original(candidate)
            db.data["users"]["owner"]["disabled"] = True
            return result

        monkeypatch.setattr(evidence, "reconcile_staged_payment_evidence", reconcile)
        with pytest.raises(HTTPException) as error:
            await visible_refresh(db)
        assert error.value.detail["code"] == "operational_actor_inactive"
        assert db.data[store.STATES]["owner"] == before
        assert not [write for write in db.writes if write[0] == store.STATES]
        assert db.data[demand.LEASES] == {}
    asyncio.run(scenario())


def test_nonempty_calculation_is_joined_on_disconnect(measured_refresh, monkeypatch):
    async def scenario():
        db, _ = measured_refresh
        request = Request()
        state = db.data[store.STATES]["owner"]
        state[evidence.INBOX] = {"proof": payment()}
        before = deepcopy(state)
        loop, main_thread = asyncio.get_running_loop(), get_ident()
        entered, release, finished = asyncio.Event(), Event(), Event()
        original = evidence.reconcile_staged_payment_evidence

        def reconcile(candidate):
            assert get_ident() != main_thread, "Nonempty evidence must not block the request loop"
            loop.call_soon_threadsafe(entered.set)
            try:
                assert release.wait(2), "Test failed to release the calculation"
                return original(candidate)
            finally:
                finished.set()

        monkeypatch.setattr(evidence, "reconcile_staged_payment_evidence", reconcile)
        work = asyncio.create_task(visible_refresh(db, request))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            request.disconnect()
            await asyncio.sleep(0)
            assert not work.done()
        finally:
            release.set()
        with pytest.raises(HTTPException) as error:
            await work
        assert error.value.detail["code"] == "operational_refresh_cancelled"
        assert finished.is_set()
        assert db.data[store.STATES]["owner"] == before
        assert not [write for write in db.writes if write[0] == store.STATES]
        assert db.data[demand.LEASES] == {}
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())


def test_cas_retry_admits_evidence_added_after_empty_attempt(measured_refresh, monkeypatch):
    async def scenario():
        db, samples = measured_refresh
        state = db.data[store.STATES]["owner"]
        before = financial_state(state)
        attempts = []
        original_replace = CountedCollection.replace_one

        async def replace(collection, query, row):
            if collection.name == store.STATES:
                attempts.append(query["revision"])
                if len(attempts) == 1:
                    db.reject_cas = True
                    rejected = await original_replace(collection, query, row)
                    db.reject_cas = False
                    saved = db.data[store.STATES]["owner"]
                    saved["revision"] += 1
                    saved[evidence.INBOX] = {"proof": payment()}
                    return rejected
            return await original_replace(collection, query, row)

        monkeypatch.setattr(CountedCollection, "replace_one", replace)
        result = await visible_refresh(db)
        assert attempts == [1, 2] and result["revision"] == 3
        assert_empty_stage(samples[0])
        assert_guarded_stage(samples[1])
        assert result[evidence.INBOX]["proof"]["status"] == "already_recognized"
        assert result["engine"][COVERAGE] == {"recognized": 1, "pending": 0, "conflict": 0}
        assert financial_state(result) == before
        assert claim_rows(db) == [] and db.data[demand.LEASES] == {}
    asyncio.run(scenario())
