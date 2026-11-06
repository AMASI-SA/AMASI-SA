from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient
from integrations.qoyod.header_summary import cache_report_summary, read_header_summary
from test_qoyod_verified_unsent_summary import NOW, SNAPSHOT, proof


@pytest.mark.asyncio
async def test_header_100_reads_never_access_orders_or_provider_and_payload_is_bounded():
    # Any access except settings raises AttributeError, even with a huge report.
    db = AsyncMongoMockClient().db
    await db.qoyod_settings.insert_one({'user_id': 'tenant', 'invoice_presence_snapshot': SNAPSHOT})
    await cache_report_summary(db, user_id='tenant', orders_user_id='owner',
        proofs=[proof(str(i)) for i in range(20000)], from_date='2026-07-01', now=NOW)
    settings = await db.qoyod_settings.find_one({'user_id': 'tenant'})
    reader = AsyncMock(return_value=settings)
    bounded_db = SimpleNamespace(qoyod_settings=SimpleNamespace(find_one=reader))
    for _ in range(100):
        result = await read_header_summary(bounded_db, user_id='tenant', orders_user_id='owner', now=NOW)
        assert result['invoice_presence']['count'] == 20000
        assert len(json.dumps(result)) < 300
        assert 'order_numbers' not in result['invoice_presence']
    assert reader.await_count == 100
    assert reader.call_args.args[0] == {'user_id': 'tenant'}
    assert reader.call_args.args[1]['invoice_presence_summary'] == 1


@pytest.mark.asyncio
async def test_last_success_survives_expiry_or_failed_sync_but_never_crosses_owner():
    db = AsyncMongoMockClient().db
    await db.qoyod_settings.insert_one({'user_id': 'tenant', 'invoice_presence_snapshot': SNAPSHOT})
    await cache_report_summary(db, user_id='tenant', orders_user_id='owner',
        proofs=[proof('1')], from_date='2026-07-01', now=NOW)
    for tenant, owner, now in [('other', 'owner', NOW), ('tenant', 'other', NOW)]:
        result = await read_header_summary(db, user_id=tenant, orders_user_id=owner, now=now)
        assert result['invoice_presence']['count'] is None
    expired = await read_header_summary(db, user_id='tenant', orders_user_id='owner', now=NOW + timedelta(minutes=15))
    assert expired['invoice_presence']['count'] == 1
    assert expired['invoice_presence']['stale'] is True
    await db.qoyod_settings.update_one({'user_id': 'tenant'}, {'$set': {'invoice_presence_snapshot.complete': False}})
    assert (await read_header_summary(db, user_id='tenant', orders_user_id='owner', now=NOW))['invoice_presence']['stale'] is True
    await db.qoyod_settings.update_one({'user_id': 'tenant'}, {'$set': {'invoice_presence_snapshot': {**SNAPSHOT, 'started_at': (NOW + timedelta(seconds=1)).isoformat()}}})
    await cache_report_summary(db, user_id='tenant', orders_user_id='owner', proofs=[proof('2')],
        from_date='2026-07-01', now=NOW, settings={'invoice_presence_snapshot': SNAPSHOT})
    retained = (await read_header_summary(db, user_id='tenant', orders_user_id='owner', now=NOW))['invoice_presence']
    assert retained['count'] == 1
    assert retained['stale'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('filters', [{'from_date':'2026-10-01'}, {'search':'abc'},
    {'salla_status':'completed'}, {'to_date':'2026-10-01'}])
async def test_filtered_reports_do_not_replace_global_summary(filters):
    db = SimpleNamespace()  # must return before any DB access
    args = dict(user_id='tenant', orders_user_id='owner', proofs=[], from_date='2026-07-01', now=NOW)
    args.update(filters)
    await cache_report_summary(db, **args)


@pytest.mark.asyncio
async def test_http_summary_denies_employee_actor_and_owner_uses_only_small_reads():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from integrations.qoyod.routes import make_qoyod_router
    db = SimpleNamespace(
        users=SimpleNamespace(find_one=AsyncMock(return_value={'id':'employee', 'role':'employee'})),
        qoyod_settings=SimpleNamespace(find_one=AsyncMock(return_value=None)),
    )
    async def session():
        return {'id':'owner', 'role':'owner', '_mobile_actor_id':'employee'}
    app = FastAPI()
    router = make_qoyod_router(db, session)
    route = next(r.path for r in router.routes if getattr(r, "path", "").endswith('/unsent-summary'))
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        denied = await client.get(route)
        assert denied.status_code == 403
        db.qoyod_settings.find_one.assert_not_awaited()
        db.users.find_one.return_value = {'id':'employee', 'role':'owner'}
        response = await client.get(route)
        assert response.status_code == 200
        assert response.json()['invoice_presence']['count'] is None
        assert db.qoyod_settings.find_one.await_count == 1
