"""A refresh must not remove the last completed closed-day reporting proof."""
from datetime import date, datetime, timezone

import mongomock
import pytest

from snapchat_v2.period_evidence import total_period_complete
from snapchat_v2.total_facts import load_total_facts, upsert_total_facts
from snapchat_v2.committed_totals import publish_committed_totals, COLLECTION


class Cursor:
    def __init__(self, raw):
        self.raw = raw

    def sort(self, *args):
        self.raw = self.raw.sort(*args)
        return self

    def limit(self, count):
        self.raw = self.raw.limit(count)
        return self

    async def to_list(self, length):
        return list(self.raw)[:length]


class Collection:
    def __init__(self, raw):
        self.raw = raw

    async def create_index(self, *args, **kwargs):
        return self.raw.create_index(*args, **kwargs)

    async def update_one(self, *args, **kwargs):
        return self.raw.update_one(*args, **kwargs)

    async def find_one(self, *args, **kwargs):
        return self.raw.find_one(*args, **kwargs)

    def find(self, *args, **kwargs):
        return Cursor(self.raw.find(*args, **kwargs))


class Database:
    def __init__(self):
        self.raw = mongomock.MongoClient(tz_aware=True).db

    def __getitem__(self, name):
        return Collection(self.raw[name])


@pytest.mark.asyncio
@pytest.mark.parametrize('refresh_status', ['running', 'partial', 'failed'])
async def test_refresh_write_keeps_last_completed_day_readable(refresh_status, monkeypatch):
    db = Database()
    day = date(2026, 10, 15)
    for run_id, status in [('closed', 'complete'), ('refresh', refresh_status)]:
        db.raw.mezan_snapchat_sync_runs_v2.insert_one(dict(
            user_id='u', provider='snapchat_ads', ad_account_id='a',
            sync_run_id=run_id, status=status,
            financial_sync_status='complete', campaign_sync_status='complete',
            ad_squad_sync_status='complete', ad_sync_status='complete',
            finished_at=datetime(2026, 10, 16, 8, tzinfo=timezone.utc),
            request_window={'date_from': day.isoformat(), 'date_to': day.isoformat(),
                            'action_report_time': 'conversion'}))
    fact = dict(user_id='u', ad_account_id='a', entity_type='campaign',
                external_id='campaign', campaign_id='campaign',
                report_date=day.isoformat(), account_timezone='UTC', currency='USD',
                window_start_utc=datetime(2026, 10, 15, tzinfo=timezone.utc),
                window_end_utc=datetime(2026, 10, 16, tzinfo=timezone.utc),
                coverage={'status': 'complete'}, spend_native=12)

    async def read():
        return await load_total_facts(db, user_id='u', ad_account_id='a',
                                     entity_type='campaign', date_from=day,
                                     date_to=day, account_timezone='UTC')

    async def proven(rows):
        return await total_period_complete(db, user_id='u', account_id='a',
                                           entity_type='campaign', date_from=day,
                                           date_to=day, action_report_time='conversion',
                                           facts=rows)

    def hierarchy(run, spend):
        return [{**fact, 'sync_run_id': run, 'spend_native': spend, 'entity_type': level}
                for level in ['campaign', 'ad_squad', 'ad']]

    await upsert_total_facts(db, hierarchy('closed', 12))
    assert await proven(await read())
    assert await publish_committed_totals(db, 'closed') == 1
    await upsert_total_facts(db, hierarchy('refresh', 13))
    assert await publish_committed_totals(db, 'refresh') == 0
    rows = await read()
    assert await proven(rows), 'In-progress refresh erased the completed closed-day proof'
    assert rows[0]['spend_native'] == 12
    from unified_marketing.readers import snapchat_v2 as reader
    async def selected(*args, **kwargs):
        return {'ad_account_id': 'a', 'timezone': 'UTC', 'currency': 'USD'}
    async def cost(*args, **kwargs):
        return {'exchange_rate_to_sar': 3.75}
    monkeypatch.setattr(reader, 'get_selected_account', selected)
    monkeypatch.setattr(reader, 'resolve_account', selected)
    monkeypatch.setattr(reader, 'calculate_cost_components', cost)
    evidence = await reader.load_snapchat_v2_entity_readiness_evidence(
        db, 'u', entity_level='campaign', date_from=day, date_to=day, timezone_name='UTC')
    assert evidence['complete'] is True
    series = await reader.load_snapchat_v2_entity_daily_series(
        db, 'u', entity_level='campaign', entity_ids=['campaign'], date_from=day,
        date_to=day, timezone_name='UTC')
    assert series['rows'][0]['delivery']['spend']['amount'] == 12
    db.raw.mezan_snapchat_sync_runs_v2.update_one({'sync_run_id': 'refresh'},
                                                {'$set': {'status': 'complete'}})
    assert await publish_committed_totals(db, 'refresh') == 1
    rows = await read()
    assert await proven(rows)
    assert rows[0]['spend_native'] == 13
    assert db.raw[COLLECTION].count_documents({}) == 1
    assert await load_total_facts(db, user_id='another-owner', ad_account_id='a',
                                 entity_type='campaign', date_from=day,
                                 date_to=day, account_timezone='UTC') == []
    import snapchat_v2.committed_totals as committed
    monkeypatch.setattr(committed, 'MAX_DOCUMENT_BYTES', 1)
    with pytest.raises(ValueError, match='document_limit'):
        await publish_committed_totals(db, 'refresh')
    assert (await read())[0]['spend_native'] == 13


@pytest.mark.asyncio
async def test_first_running_source_cannot_publish_a_committed_snapshot():
    db = Database()
    db.raw.mezan_snapchat_sync_runs_v2.insert_one({
        'sync_run_id': 'first', 'provider': 'snapchat_ads', 'status': 'running',
        'financial_sync_status': 'complete', 'campaign_sync_status': 'complete',
        'ad_squad_sync_status': 'complete', 'ad_sync_status': 'complete',
    })
    assert await publish_committed_totals(db, 'first') == 0
    assert db.raw[COLLECTION].count_documents({}) == 0
