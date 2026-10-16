from datetime import date

import mongomock
import pytest

import snapchat_v2.routes as routes
from snapchat_v2.period_evidence import total_period_complete


class AsyncCollection:
    def __init__(self, collection):
        self.collection = collection

    async def find_one(self, *args, **kwargs):
        return self.collection.find_one(*args, **kwargs)


class Database:
    def __init__(self):
        self.raw = mongomock.MongoClient().db

    def __getitem__(self, name):
        return AsyncCollection(self.raw[name])


@pytest.mark.asyncio
@pytest.mark.parametrize('level,field', [('campaign', 'campaign_sync_status'), ('ad_squad', 'ad_squad_sync_status'), ('ad', 'ad_sync_status')])
async def test_closed_period_keeps_its_proof_while_new_run_is_pending(monkeypatch, level, field):
    db = Database()
    db.raw.mezan_snapchat_sync_runs_v2.insert_many([
        dict(user_id='u', provider='snapchat_ads', ad_account_id='a', sync_run_id='closed',
             status='complete', started_at='2026-10-16T07:30:00Z', financial_sync_status='complete',
             request_window={'date_from': '2026-10-15', 'date_to': '2026-10-15', 'action_report_time': 'conversion'},
             **{field: 'complete'}),
        dict(user_id='u', provider='snapchat_ads', ad_account_id='a', sync_run_id='new',
             status='running', started_at='2026-10-16T08:00:00Z', **{field: 'pending'}),
    ])
    async def hourly(*args, **kwargs):
        return []
    async def total(*args, **kwargs):
        return [dict(external_id='entity', report_date='2026-10-15', sync_run_id='closed',
                     coverage={'status': 'complete'}, spend_native=12, impressions=100)]
    async def entities(*args, **kwargs):
        return []
    monkeypatch.setattr(routes, 'load_hourly_facts', hourly)
    monkeypatch.setattr(routes, 'load_total_facts', total)
    monkeypatch.setattr(routes, 'list_entities', entities)
    result = await routes._entity_performance_report(
        db, user_id='u', account={'ad_account_id': 'a', 'timezone': 'UTC'},
        date_from=date(2026, 10, 15), date_to=date(2026, 10, 15),
        timezone_name='UTC', action_report_time='conversion', entity_type=level)
    assert result['performance_sync_status'] == 'complete'
    assert result['source_collection'] == 'mezan_snapchat_daily_total_facts_v2'
    assert result['totals']['spend_native'] == 12
    assert result['rows'][0]['performance_sync_status'] == 'complete'


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['owner', 'account', 'provider', 'action', 'window', 'status', 'level', 'coverage', 'missing_run', 'missing_day'])
async def test_period_proof_rejects_wrong_or_missing_evidence(bad):
    db = Database()
    run = dict(user_id='u', provider='snapchat_ads', ad_account_id='a', sync_run_id='r',
               status='complete', financial_sync_status='complete', ad_sync_status='complete',
               request_window={'date_from': '2026-10-14', 'date_to': '2026-10-15', 'action_report_time': 'conversion'})
    facts = [dict(report_date=d, sync_run_id='r', coverage={'status': 'complete'})
             for d in ['2026-10-14', '2026-10-15']]
    if bad in {'owner', 'account', 'provider'}:
        run[{'owner': 'user_id', 'account': 'ad_account_id', 'provider': 'provider'}[bad]] = 'other'
    if bad == 'action': run['request_window']['action_report_time'] = 'impression'
    if bad == 'window': run['request_window']['date_from'] = '2026-10-15'
    if bad == 'status': run['status'] = 'running'
    if bad == 'level': run['ad_sync_status'] = 'partial'
    if bad == 'coverage': facts[0]['coverage']['status'] = 'partial'
    if bad == 'missing_run': facts[0]['sync_run_id'] = 'unknown'
    if bad == 'missing_day': facts.pop()
    db.raw.mezan_snapchat_sync_runs_v2.insert_one(run)
    assert not await total_period_complete(
        db, user_id='u', account_id='a', entity_type='ad',
        date_from=date(2026, 10, 14), date_to=date(2026, 10, 15),
        action_report_time='conversion', facts=facts)


@pytest.mark.asyncio
async def test_range_accepts_different_successful_source_run_for_each_day():
    db = Database()
    facts = []
    for day in ['2026-10-14', '2026-10-15']:
        db.raw.mezan_snapchat_sync_runs_v2.insert_one(dict(
            user_id='u', provider='snapchat_ads', ad_account_id='a', sync_run_id=day,
            status='complete', financial_sync_status='complete', ad_sync_status='complete',
            request_window={'date_from': day, 'date_to': day, 'action_report_time': 'conversion'}))
        facts.append(dict(report_date=day, sync_run_id=day, coverage={'status': 'complete'}))
    assert await total_period_complete(db, user_id='u', account_id='a', entity_type='ad',
        date_from=date(2026, 10, 14), date_to=date(2026, 10, 15),
        action_report_time='conversion', facts=facts)
