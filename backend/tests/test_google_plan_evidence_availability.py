"""Owner-scoped saved-plan diagnostic availability, not sales or profitability."""
from copy import deepcopy
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from pymongo.errors import OperationFailure

from integrations_control_center import google_plan_evidence_availability as module

NOW = datetime(2026, 10, 27, 8, tzinfo=timezone.utc)
PLAN = {'user_id': 'owner', 'plan_id': 'winter', 'revision': 3,
        'fields': {'pinned_product_ids': ['123', '456'], 'excluded_product_ids': ['456'], 'instruction': 'winter'}}


def order(number='77', source='2026-10-26T21:30:00Z', normalized='2026-10-26', owner='owner'):
    return {'user_id': owner, 'order_id': '42', 'order_number': number, 'order_date': normalized,
            'raw_by_source': {'salla_direct': {'id': 42, 'reference_id': number, 'created_at': source}}}


class Cursor:
    def __init__(self, rows, collection): self.rows, self.collection = rows, collection
    def hint(self, value): self.collection.hint_value = value; return self
    def limit(self, value): self.bound = value; return self
    def max_time_ms(self, value): assert value == 3000; return self
    async def to_list(self, length):
        if self.collection.failure: raise self.collection.failure
        if self.collection.on_read: self.collection.on_read()
        return deepcopy(self.rows[:min(length, self.bound)])


class Orders:
    def __init__(self, rows=()): self.rows = list(rows); self.queries = []; self.hint_value = None; self.failure = None; self.on_read = None
    def find(self, query, projection):
        self.queries.append((deepcopy(query), deepcopy(projection)))
        return Cursor([r for r in self.rows if r['user_id'] == query['user_id'] and query['order_date']['$gte'] <= r['order_date'] <= query['order_date']['$lte']], self)


class Plans:
    def __init__(self): self.row = deepcopy(PLAN)
    async def find_one(self, query, projection=None, **kwargs):
        return deepcopy(self.row) if self.row and all(self.row.get(k) == v for k, v in query.items()) else None


class DB:
    def __init__(self, rows=()): self.plans = Plans(); self.orders = Orders(rows)
    def __getitem__(self, name):
        if name == module.PLANS: return self.plans
        assert name == 'unified_orders'
        return self.orders


def app(monkeypatch, db, actor=None):
    async def current_user(): return {'id': 'owner'}
    async def principal(*args, **kwargs): return actor or {'user_id': 'owner', 'actor_id': 'owner', 'owner': True}
    monkeypatch.setattr(module, 'principal', principal)
    target, router = FastAPI(), APIRouter()
    module.attach_google_plan_evidence_availability_routes(router, db, current_user, clock=lambda: NOW)
    target.include_router(router)
    return target


async def get(target, revision=3):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url='http://test') as client:
        return await client.get('/google_ads/commercial-plans/winter/evidence-availability', params={'expected_revision': revision})


@pytest.mark.asyncio
async def test_route_reads_once_with_declared_compound_index_and_server_closed_windows(monkeypatch):
    db = DB([order(source='2026-10-26T20:30:00Z'), order(number='78', source='2026-10-11T23:30:00Z', normalized='2026-10-12')])
    response = await get(app(monkeypatch, db)); assert response.status_code == 200
    out = response.json(); assert out['mode'] == 'source_date_availability_diagnostic'
    assert out['pinned_product_ids_reviewed'] == ['123']
    assert out['windows']['last_14_days']['period']['from'] == '2026-10-13'
    assert out['windows']['last_14_days']['observed_bound_source_dates'] == 1
    assert out['windows']['previous_14_days']['observed_bound_source_dates'] == 1
    assert out['windows']['last_90_days']['observed_bound_source_dates'] == 2
    assert len(db.orders.queries) == 1 and db.orders.hint_value == [('user_id', 1), ('order_date', -1)]
    query, projection = db.orders.queries[0]
    assert query['order_date'] == {'$gte': '2026-07-28', '$lte': '2026-10-27'}
    assert query['$expr']['$lte'][1] == 16384
    assert not {'products', 'customer_name', 'currency', 'total_amount'} & set(projection)
    assert out['coverage_complete'] is out['product_mapping_verified'] is out['execution_authorized'] is False
    assert all(p['sales'] is p['units_sold'] is p['profitability'] is None for p in out['products'])
    assert '77' not in str(out) and '78' not in str(out)


@pytest.mark.asyncio
async def test_empty_diagnostics_are_not_zero_product_sales(monkeypatch):
    out = (await get(app(monkeypatch, DB()))).json()
    assert out['windows']['last_14_days']['status'] == 'not_observed'
    assert out['windows']['last_14_days']['observed_bound_source_dates'] == 0
    assert out['products'][0]['sales'] is None and out['coverage_complete'] is False


@pytest.mark.asyncio
async def test_all_excluded_pins_do_not_read_orders(monkeypatch):
    db = DB(); db.plans.row['fields']['excluded_product_ids'] = ['123', '456']
    out = (await get(app(monkeypatch, db))).json()
    assert not db.orders.queries and out['pinned_product_ids_reviewed'] == []
    assert out['windows']['last_14_days']['status'] == 'not_requested'


@pytest.mark.asyncio
async def test_stale_revision_wrong_owner_and_staff_cannot_read(monkeypatch):
    db = DB(); target = app(monkeypatch, db)
    assert (await get(target, revision=2)).status_code == 409 and not db.orders.queries
    target = app(monkeypatch, db, {'user_id': 'other', 'actor_id': 'other', 'owner': True})
    assert (await get(target)).status_code == 404 and not db.orders.queries
    target = app(monkeypatch, db, {'user_id': 'owner', 'actor_id': 'staff', 'owner': False})
    assert (await get(target)).status_code == 403 and not db.orders.queries


@pytest.mark.asyncio
@pytest.mark.parametrize('change_revision', [True, False])
async def test_plan_revision_or_same_revision_field_change_invalidates_result(monkeypatch, change_revision):
    db = DB([order()])
    def change():
        if change_revision: db.plans.row['revision'] += 1
        else: db.plans.row['fields']['instruction'] = 'changed'
    db.orders.on_read = change
    assert (await get(app(monkeypatch, db))).status_code == 409


@pytest.mark.asyncio
async def test_missing_index_and_database_failure_are_unavailable_not_zero(monkeypatch):
    db = DB(); db.orders.failure = OperationFailure('private database/index detail', code=2)
    response = await get(app(monkeypatch, db)); assert response.status_code == 503
    assert 'private database' not in response.text and 'windows' not in response.json()


@pytest.mark.asyncio
async def test_unknown_timestamp_stays_candidate_subset_diagnostic(monkeypatch):
    out = (await get(app(monkeypatch, DB([order(source='2026-10-26')])))).json()
    assert out['read_diagnostic']['observed_unverified_source_dates'] == 1
    assert out['windows']['last_14_days']['status'] == 'not_observed'
    assert out['coverage_complete'] is False


@pytest.mark.asyncio
async def test_truncation_suppresses_all_partial_counts():
    db = DB([order(), order(number='78')])
    out = await module.read_plan_source_date_windows(db, owner_id='owner', as_of=NOW, limit=1)
    assert out['read_diagnostic']['truncated'] is True
    assert all(window['observed_bound_source_dates'] is None for window in out['windows'].values())
    assert out['read_diagnostic']['observed_unverified_source_dates'] is None
