"""Actual diagnostic/ASGI contract with isolated transaction lifecycle fake."""
import asyncio
import time
from types import SimpleNamespace

from fastapi import FastAPI, APIRouter, HTTPException
import httpx
import pytest

from integrations_control_center import google_ads_budget_storage_readiness as readiness
from tests.test_google_ads_automation_policy import DB as OwnerDB


class Session:
    def __init__(self, db): self.db = db; self.active = False
    async def __aenter__(self): raise AssertionError("Motor async context must not be used")
    async def __aexit__(self, *args): raise AssertionError("synchronous delegate cleanup forbidden")
    async def end_session(self):
        self.db.ended += 1
        if self.active: await self.abort_transaction()
    def start_transaction(self, **kwargs):
        assert kwargs['read_concern'].level == 'snapshot'
        assert kwargs['write_concern'].document == {'w': 'majority'}
        self.active = True; self.db.started += 1
    async def abort_transaction(self):
        self.db.aborted += 1
        if self.db.failure == 'abort_timeout':
            self.db.abort_entered.set()
            while not self.db.release_abort.is_set():
                try:
                    await self.db.release_abort.wait()
                except asyncio.CancelledError:
                    self.db.cancellation_seen.set()
        if self.db.failure == 'abort': raise RuntimeError('private-db-name')
        self.active = False


class ProbeDB:
    def __init__(self, failure=None):
        self.owner = OwnerDB(); self.users = self.owner.users
        self.users.rows.append({"id":"staff", "role":"staff", "owner_id":"owner"})
        self.failure = failure; self.started = self.aborted = self.writes = self.ended = 0
        self.abort_entered = asyncio.Event(); self.release_abort = asyncio.Event(); self.cancellation_seen = asyncio.Event()
        self.raw_aborts = 0
        self.documents = []; self.client = SimpleNamespace(start_session=self.session, admin=SimpleNamespace(command=self.abort_command))
    async def abort_command(self, command, *, session):
        assert session.active
        assert command == {'abortTransaction':1, 'writeConcern':{'w':'majority', 'wtimeout':4000}}
        self.raw_aborts += 1
        if self.failure == 'abort_network': raise ConnectionError('private-db-name')
        if self.failure == 'abort_reply': return {'ok':0}
        if self.failure == 'abort_wc': return {'ok':1, 'writeConcernError':{'code':64}}
        if self.failure == 'abort_unknown': return None
        # Command failure/unknown cases must remain failure even when end_session succeeds.
        if self.failure not in ('abort_timeout','abort'): session.active = False
        return {'ok':1}

    async def session(self): return Session(self)
    async def command(self, name, *, session):
        assert name == 'hello'
        return {'logicalSessionTimeoutMinutes': 30, **({} if self.failure == 'standalone' else {'setName': 'rs'})}
    def __getitem__(self, name):
        assert name == readiness.PROBE_COLLECTION, 'must not touch policy/budget/provider collections'
        return self
    async def insert_one(self, document, *, session):
        assert session.active
        self.writes += 1; self.documents.append(document)
        if self.failure == 'write': raise RuntimeError('mongodb://secret-host/private-db')
        if self.failure == 'timeout':
            await asyncio.sleep(.04)
            raise TimeoutError('simulated driver deadline')
        return SimpleNamespace(acknowledged=self.failure != 'unacknowledged')


def mounted(db, user):
    app, router = FastAPI(), APIRouter()
    async def current_user(): return user
    readiness.attach_google_ads_budget_storage_readiness_routes(router, db, current_user)
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_owner_success_proves_write_then_abort_without_grant_or_saved_data():
    db = ProbeDB()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(mounted(db, {'id':'owner'})), base_url='http://test') as client:
        result = await client.post('/google_ads/automation/storage-readiness')
    assert result.status_code == 200
    assert result.json() == {'scope':'budget_storage_transaction_only', 'storage_verified':True,
        'snapshot_majority_write_aborted':True, 'execution_authorized':False, 'funding_ready':False,
        'policy_changed':False, 'provider_writes':0}
    assert (db.started, db.writes, db.raw_aborts, db.ended) == (1, 1, 1, 1)
    assert set(db.documents[0]) == {'_id', 'probe_only'}


@pytest.mark.asyncio
@pytest.mark.parametrize('user,status', [(None,401), ({'id':'unknown'},401), ({'id':'staff'},403)])
async def test_auth_failure_never_starts_database_probe(user,status):
    db = ProbeDB()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(mounted(db,user)), base_url='http://test') as client:
        result = await client.post('/google_ads/automation/storage-readiness')
    assert result.status_code == status
    assert db.started == db.writes == db.aborted == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['standalone','write','abort','unacknowledged','timeout','abort_network','abort_reply','abort_wc','abort_unknown'])
async def test_fail_closed_sanitized_bounded_probe(monkeypatch,failure):
    db = ProbeDB(failure)
    monkeypatch.setattr(readiness, 'TIMEOUT_SECONDS', .02)
    with pytest.raises(HTTPException) as error:
        await readiness.verify_budget_storage(db)
    assert error.value.status_code == 503
    assert error.value.detail['code'] == 'google_budget_storage_unverified'
    assert 'private-db' not in str(error.value.detail) and 'secret-host' not in str(error.value.detail)
    if failure == 'standalone': assert db.started == db.writes == db.aborted == 0
    else:
        task = readiness._active_probe
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        assert db.ended == 1
        if failure != "abort_reply": assert db.aborted >= 1


@pytest.mark.asyncio
async def test_probes_never_reuse_document_or_session():
    db = ProbeDB()
    await readiness.verify_budget_storage(db); await readiness.verify_budget_storage(db)
    assert db.documents[0]['_id'] != db.documents[1]['_id']
    assert db.started == db.writes == db.raw_aborts == db.ended == 2


@pytest.mark.asyncio
async def test_delayed_abort_is_retained_bounds_concurrency_and_finishes_in_own_context(monkeypatch):
    monkeypatch.setattr(readiness, 'TIMEOUT_SECONDS', .02)
    db = ProbeDB('abort_timeout')
    started = time.monotonic()
    with pytest.raises(HTTPException) as error:
        await readiness.verify_budget_storage(db)
    assert error.value.detail['code'] == 'google_budget_storage_unverified'
    assert time.monotonic() - started < .5
    task = readiness._active_probe
    assert task is not None and not task.done()
    assert db.abort_entered.is_set()
    second = ProbeDB()
    try:
        with pytest.raises(HTTPException) as busy:
            await readiness.verify_budget_storage(second)
        assert busy.value.status_code == 503
        assert busy.value.detail['code'] == 'google_budget_storage_probe_busy'
        assert second.started == second.writes == second.aborted == 0
        assert readiness._active_probe is task and not task.done()
    finally:
        db.release_abort.set()
        result = await asyncio.wait_for(asyncio.gather(asyncio.shield(task), return_exceptions=True), timeout=.5)
        assert result[0]['storage_verified'] is True  # late result never upgrades expired HTTP response
        await asyncio.sleep(0)  # consume callback releases the slot
    assert readiness._active_probe is None
    assert (db.started, db.writes, db.raw_aborts, db.ended) == (1, 1, 1, 1)
    assert (await readiness.verify_budget_storage(second))['storage_verified'] is True
    await asyncio.sleep(0)
    assert readiness._active_probe is None


@pytest.mark.asyncio
async def test_http_timeout_does_not_cancel_executor_backed_async_end_session_future(monkeypatch):
    monkeypatch.setattr(readiness, 'TIMEOUT_SECONDS', .02)
    db = ProbeDB()
    cleanup_future = asyncio.get_running_loop().create_future()
    original_session = db.session
    async def session():
        value = await original_session()
        # Motor's public async end_session returns an executor-backed Future;
        # cancelling its waiter cannot stop underlying synchronous driver work.
        def end_session():
            db.ended += 1
            return cleanup_future
        value.end_session = end_session
        return value
    db.client.start_session = session
    with pytest.raises(HTTPException):
        await readiness.verify_budget_storage(db)
    try:
        readiness._active_probe.cancel()  # cancellation while executor cleanup is in flight
        await asyncio.sleep(0)
        assert not cleanup_future.cancelled(), 'driver cleanup Future must remain awaitable after HTTP deadline'
        assert readiness._active_probe is not None and not readiness._active_probe.done()
    finally:
        if not cleanup_future.done(): cleanup_future.set_result(None)
        if readiness._active_probe is not None:
            await asyncio.gather(readiness._active_probe, return_exceptions=True)
        await asyncio.sleep(0)
    assert readiness._active_probe is None
