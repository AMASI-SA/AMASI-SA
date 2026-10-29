"""Actual diagnostic/ASGI contract with isolated transaction lifecycle fake."""
import asyncio
from types import SimpleNamespace

from fastapi import FastAPI, APIRouter, HTTPException
import httpx
import pytest

from integrations_control_center import google_ads_budget_storage_readiness as readiness
from tests.test_google_ads_automation_policy import DB as OwnerDB


class Session:
    def __init__(self, db): self.db = db; self.active = False
    async def __aenter__(self): return self
    async def __aexit__(self, *args):
        if self.active: await self.abort_transaction()
    def start_transaction(self, **kwargs):
        assert kwargs['read_concern'].level == 'snapshot'
        assert kwargs['write_concern'].document == {'w': 'majority'}
        self.active = True; self.db.started += 1
    async def abort_transaction(self):
        self.db.aborted += 1
        if self.db.failure == 'abort_timeout': await asyncio.Event().wait()
        if self.db.failure == 'abort': raise RuntimeError('private-db-name')
        self.active = False


class ProbeDB:
    def __init__(self, failure=None):
        self.owner = OwnerDB(); self.users = self.owner.users
        self.users.rows.append({"id":"staff", "role":"staff", "owner_id":"owner"})
        self.failure = failure; self.started = self.aborted = self.writes = 0
        self.documents = []; self.client = SimpleNamespace(start_session=self.session)
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
        if self.failure == 'timeout': await asyncio.Event().wait()
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
    assert (db.started, db.writes, db.aborted) == (1, 1, 1)
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
@pytest.mark.parametrize('failure', ['standalone','write','abort','unacknowledged','timeout','abort_timeout'])
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
        await asyncio.sleep(0)  # cancellation delivers the owned-session abort
        assert db.aborted >= 1


@pytest.mark.asyncio
async def test_probes_never_reuse_document_or_session():
    db = ProbeDB()
    await readiness.verify_budget_storage(db); await readiness.verify_budget_storage(db)
    assert db.documents[0]['_id'] != db.documents[1]['_id']
    assert db.started == db.writes == db.aborted == 2
