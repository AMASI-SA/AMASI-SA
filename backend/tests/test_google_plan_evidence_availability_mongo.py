"""Disposable-loopback real Mongo proofs for the unattached owner diagnostic."""
from datetime import datetime, timezone

import httpx
import pytest
from bson.son import SON
from fastapi import APIRouter, FastAPI

from tests.test_google_product_sales_evidence_reader_mongo import replica_db
from integrations_control_center import google_plan_evidence_availability as module

NOW = datetime(2026, 10, 27, 8, tzinfo=timezone.utc)
PLAN = {'user_id': 'owner', 'plan_id': 'winter', 'revision': 3,
        'fields': {'pinned_product_ids': ['123'], 'excluded_product_ids': [], 'instruction': 'winter'}}


def source(number='77', owner='owner', instant='2026-10-26T20:30:00Z'):
    return {'user_id': owner, 'order_id': '42', 'order_number': number, 'order_date': '2026-10-26',
        'customer_name': 'synthetic-private-canary', 'products': [{'customer_choice': 'synthetic-item-canary'}],
        'raw_by_source': {'salla_direct': {'id': 42, 'reference_id': number, 'created_at': instant}}}


async def target(db, monkeypatch):
    await db[module.PLANS].insert_one(PLAN.copy())
    async def current_user(): return {'id': 'owner'}
    async def principal(*args, **kwargs): return {'user_id': 'owner', 'actor_id': 'owner', 'owner': True}
    monkeypatch.setattr(module, 'principal', principal)
    app, router = FastAPI(), APIRouter()
    module.attach_google_plan_evidence_availability_routes(router, db, current_user, clock=lambda: NOW)
    app.include_router(router)
    return app


async def get(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        return await client.get('/google_ads/commercial-plans/winter/evidence-availability', params={'expected_revision': 3})


@pytest.mark.asyncio
async def test_real_hinted_owner_read_and_explain_use_compound_index_without_sales(replica_db, monkeypatch):
    await replica_db.unified_orders.create_index(module.SOURCE_INDEX)
    await replica_db.unified_orders.insert_many([source(), source('78', owner='other')])
    app = await target(replica_db, monkeypatch)
    out = (await get(app)).json()
    assert out['windows']['last_14_days']['observed_bound_source_dates'] == 1
    assert out['coverage_complete'] is False and out['products'][0]['sales'] is None
    assert 'synthetic-private-canary' not in str(out) and 'synthetic-item-canary' not in str(out)
    assert await replica_db.unified_orders.count_documents({}) == 2
    explain = await replica_db.command('explain', {'find': 'unified_orders',
        'filter': {'user_id': 'owner', 'order_date': {'$gte': '2026-07-28', '$lte': '2026-10-27'},
            '$expr': {'$lte': [{'$bsonSize': '$$ROOT'}, 16384]}},
        'projection': module.SOURCE_PROJECTION, 'hint': SON(module.SOURCE_INDEX), 'limit': 1001, 'maxTimeMS': 3000}, verbosity='executionStats')
    plan = str(explain['queryPlanner']['winningPlan'])
    assert 'IXSCAN' in plan and 'COLLSCAN' not in plan
    assert explain['executionStats']['totalDocsExamined'] == 1


@pytest.mark.asyncio
async def test_real_missing_compound_index_is_503_not_false_empty_coverage(replica_db, monkeypatch):
    await replica_db.unified_orders.insert_one(source())
    app = await target(replica_db, monkeypatch)
    response = await get(app)
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'google_plan_evidence_read_unavailable'
    assert 'windows' not in response.json() and await replica_db.unified_orders.count_documents({}) == 1


@pytest.mark.asyncio
async def test_real_oversized_source_is_explicitly_excluded_without_absence_claim(replica_db, monkeypatch):
    await replica_db.unified_orders.create_index(module.SOURCE_INDEX)
    row = source(); row['raw_by_source']['salla_direct']['padding'] = 'x' * 20000
    await replica_db.unified_orders.insert_one(row)
    out = (await get(await target(replica_db, monkeypatch))).json()
    assert out['read_diagnostic']['observed_subset_documents'] == 0
    assert out['windows']['last_14_days']['status'] == 'not_observed'
    assert 'oversized_documents_excluded_without_count' in out['limitations']
    assert out['coverage_complete'] is False and out['products'][0]['units_sold'] is None


@pytest.mark.asyncio
async def test_real_source_midnight_shift_and_open_day_remain_diagnostics(replica_db):
    await replica_db.unified_orders.create_index(module.SOURCE_INDEX)
    await replica_db.unified_orders.insert_many([
        source(instant='2026-10-26T20:59:59Z'), source('78', instant='2026-10-26T21:00:00Z'),
        source('79', instant='2026-10-26')])
    out = await module.read_plan_source_date_windows(replica_db, owner_id='owner', as_of=NOW)
    assert out['windows']['last_14_days']['observed_bound_source_dates'] == 1
    assert out['read_diagnostic']['observed_unverified_source_dates'] == 1
    assert out['coverage_complete'] is False


@pytest.mark.asyncio
async def test_real_sentinel_suppresses_partial_counts(replica_db):
    await replica_db.unified_orders.create_index(module.SOURCE_INDEX)
    await replica_db.unified_orders.insert_many([source('77'), source('78')])
    out = await module.read_plan_source_date_windows(replica_db, owner_id='owner', as_of=NOW, limit=1)
    assert out['read_diagnostic']['truncated'] is True
    assert all(value['observed_bound_source_dates'] is None for value in out['windows'].values())
    assert out['read_diagnostic']['observed_subset_documents'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('revision_change', [True, False])
async def test_real_plan_change_after_source_read_rejects_old_result(replica_db, monkeypatch, revision_change):
    await replica_db.unified_orders.create_index(module.SOURCE_INDEX)
    await replica_db.unified_orders.insert_one(source())
    app = await target(replica_db, monkeypatch)
    original = module.read_plan_source_date_windows
    async def concurrent_change(*args, **kwargs):
        result = await original(*args, **kwargs)
        change = {'$inc': {'revision': 1}} if revision_change else {'$set': {'fields.instruction': 'summer'}}
        await replica_db[module.PLANS].update_one({'user_id': 'owner', 'plan_id': 'winter'}, change)
        return result
    monkeypatch.setattr(module, 'read_plan_source_date_windows', concurrent_change)
    response = await get(app)
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'google_commercial_plan_revision_conflict'
