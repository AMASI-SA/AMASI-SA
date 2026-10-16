"""A refresh must not remove the last completed closed-day reporting proof."""
from datetime import date, datetime, timezone

import mongomock
import pytest

from snapchat_v2.period_evidence import total_period_complete
from snapchat_v2.total_facts import load_total_facts, upsert_total_facts


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
async def test_refresh_write_keeps_last_completed_day_readable():
    db = Database()
    day = date(2026, 10, 15)
    for run_id, status in [('closed', 'complete'), ('refresh', 'running')]:
        db.raw.mezan_snapchat_sync_runs_v2.insert_one(dict(
            user_id='u', provider='snapchat_ads', ad_account_id='a',
            sync_run_id=run_id, status=status,
            financial_sync_status='complete', campaign_sync_status='complete',
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

    await upsert_total_facts(db, [{**fact, 'sync_run_id': 'closed'}])
    assert await proven(await read())
    await upsert_total_facts(db, [{**fact, 'sync_run_id': 'refresh', 'spend_native': 13}])
    rows = await read()
    assert await proven(rows), 'In-progress refresh erased the completed closed-day proof'
    assert rows[0]['spend_native'] == 12
