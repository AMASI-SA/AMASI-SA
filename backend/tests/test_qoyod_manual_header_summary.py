import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from integrations.qoyod import manual_header_summary as subject

@pytest.mark.asyncio
async def test_snapshot_reads_are_bounded_and_owner_scoped():
    reader = AsyncMock(return_value={'summary': {'count': 17}})
    db = SimpleNamespace(qoyod_header_counts=SimpleNamespace(find_one=reader))
    for _ in range(100):
        assert (await subject.read_manual_summary(db, tenant='main', owner='a'))['local_summary']['count'] == 17
    reader.assert_awaited_with({'_id': 'main:a'}, {'_id': 0, 'summary': 1})

@pytest.mark.asyncio
async def test_count_matches_real_exact_references_and_canonical_eligibility():
    db = AsyncMongoMockClient().db
    for ref, status in [('1','completed'), ('2','delivering'), ('3','delivered'), ('4','completed'),
                        ('5','completed'), ('6','completed'), ('7','pending'), ('8','cancelled')]:
        await db.unified_orders.insert_one({'user_id':'a', 'order_number':ref,
            'order_status':status, 'order_date':'2026-08-01'})
    await db.unified_orders.insert_many([
        {'user_id':'a','order_number':'2','order_status':'delivering','order_date':'2026-08-01'},
        {'user_id':'a','order_number':'9','order_status':'completed','order_date':'2026-08-01','order_date_inferred':True},
        {'user_id':'other','order_number':'10','order_status':'completed','order_date':'2026-08-01'},
        {'user_id':'a','order_number':'11','order_status':'completed','order_date':'2026-06-01'},
    ])
    await db.qoyod_invoices.insert_many([
        {'user_id':'main','reference':'1','qoyod_invoice_id':'101','issue_date':'2026-06-01','last_sync_at':'2026-11-01T00:00:00Z'},
        {'user_id':'main','reference':'1','qoyod_invoice_id':'102'},
        {'user_id':'main','reference':'ORDER-2','qoyod_invoice_id':'103'},
        {'user_id':'main','reference':'3','qoyod_invoice_id':'DRY:104'},
        {'user_id':'other','reference':'4','qoyod_invoice_id':'105'},
        {'user_id':'main','reference':'5','qoyod_invoice_id':'106'},
    ])
    result = (await subject.refresh_manual_summary(db, tenant='main', owner='a'))['local_summary']
    assert (result['eligible'], result['matched'], result['count']) == (6,2,4)
    assert result['source'] == 'local_invoice_mirror'
    assert 'complete' not in result and 'invoice_presence' not in result
    assert result['latest_invoice_sync_at'] == '2026-11-01T00:00:00Z'
    assert (await subject.read_manual_summary(db, tenant='main', owner='a'))['local_summary'] == result
    assert (await subject.read_manual_summary(db, tenant='main', owner='other'))['local_summary'] is None

@pytest.mark.asyncio
async def test_failure_preserves_snapshot_and_concurrent_click_is_rejected(monkeypatch):
    db = AsyncMongoMockClient().db
    await db.qoyod_header_counts.insert_one({'_id':'main:a','summary':{'count':19}})
    started, finish = asyncio.Event(), asyncio.Event()
    async def failed(*args):
        started.set()
        await finish.wait()
        raise RuntimeError('read failed')
    monkeypatch.setattr(subject, '_count_local', failed)
    task = asyncio.create_task(subject.refresh_manual_summary(db, tenant='main', owner='a'))
    await started.wait()
    with pytest.raises(HTTPException) as error:
        await subject.refresh_manual_summary(db, tenant='main', owner='a')
    assert error.value.status_code == 429
    finish.set()
    with pytest.raises(RuntimeError):
        await task
    assert (await subject.read_manual_summary(db, tenant='main', owner='a'))['local_summary']['count'] == 19

@pytest.mark.asyncio
async def test_no_mirror_does_not_claim_verified_absence():
    db = AsyncMongoMockClient().db
    result = (await subject.refresh_manual_summary(db, tenant='main', owner='a'))['local_summary']
    assert result['count'] == 0
    assert result['latest_invoice_sync_at'] is None
    assert result['source'] == 'local_invoice_mirror'
    assert 'complete' not in result

@pytest.mark.asyncio
async def test_http_checks_actual_actor_without_provider_owner_binding():
    from integrations.qoyod.routes import make_qoyod_router
    db = AsyncMongoMockClient().db
    await db.users.insert_one({'id':'a','role':'owner'})
    session = {'id':'a','role':'owner'}
    async def current_user(): return session
    app = FastAPI()
    app.include_router(make_qoyod_router(db, current_user))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/integrations/qoyod/header-count')).status_code == 200
        assert (await client.post('/integrations/qoyod/header-count/refresh')).status_code == 200
        assert await db.qoyod_settings.count_documents({}) == 0
        await db.users.insert_one({'id':'employee','role':'employee'})
        session['_mobile_actor_id'] = 'employee'
        assert (await client.get('/integrations/qoyod/header-count')).status_code == 403
        assert (await client.post('/integrations/qoyod/header-count/refresh')).status_code == 403

@pytest.mark.asyncio
async def test_timeout_preserves_previous_count(monkeypatch):
    db = AsyncMongoMockClient().db
    await db.qoyod_header_counts.insert_one({'_id':'main:a','summary':{'count':19}})
    async def timeout(*args): raise asyncio.TimeoutError()
    monkeypatch.setattr(subject, '_count_local', timeout)
    with pytest.raises(HTTPException) as error:
        await subject.refresh_manual_summary(db, tenant='main', owner='a')
    assert error.value.status_code == 503
    assert (await subject.read_manual_summary(db, tenant='main', owner='a'))['local_summary']['count'] == 19

@pytest.mark.asyncio
async def test_superseded_worker_cannot_publish_or_clear_newer_claim(monkeypatch):
    db = AsyncMongoMockClient().db
    await db.qoyod_header_counts.insert_one({'_id':'main:a','summary':{'count':19}})
    async def superseded(*args):
        await db.qoyod_header_counts.update_one({'_id':'main:a'}, {'$set':{'token':'newer'}})
        return {'count':2}
    monkeypatch.setattr(subject, '_count_local', superseded)
    with pytest.raises(HTTPException) as error:
        await subject.refresh_manual_summary(db, tenant='main', owner='a')
    assert error.value.status_code == 409
    row = await db.qoyod_header_counts.find_one({'_id':'main:a'})
    assert row['summary']['count'] == 19 and row['token'] == 'newer' and 'lease_until' in row
