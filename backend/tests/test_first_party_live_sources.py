from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from first_party_attribution.live_sources import classify_entry, summarize, read_live_sources
from first_party_attribution.routes import make_first_party_attribution_router

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)

@pytest.mark.parametrize('url,ref,expected', [
    ('https://amasi-sa.com/?utm_source=snapchat&utm_medium=paid_social', '', ('snapchat', True)),
    ('https://amasi-sa.com/?utm_source=ig', '', ('meta', False)),
    ('https://amasi-sa.com/?ttclid=abc', '', ('tiktok', True)),
    ('https://amasi-sa.com/?gclid=abc', '', ('google_ads', True)),
    ('https://amasi-sa.com/?utm_source=google', '', ('google', False)),
    ('https://amasi-sa.com/', 'https://www.google.com/search?q=a', ('google_organic', False)),
    ('https://amasi-sa.com/', 'https://l.facebook.com/', ('meta', False)),
    ('https://amasi-sa.com/', 'https://evilfacebook.com/', ('referral', False)),
    ('https://amasi-sa.com/', '', ('direct', False)),
    ('https://amasi-sa.com/cart', 'https://amasi-sa.com/product', ('unknown', False)),
    ('', '', ('unknown', False)),
    ('https://amasi-sa.com/?utm_source=newsletter', '', ('other_campaign', False)),
])
def test_entry_evidence(url, ref, expected):
    # A 90-day acquisition touch MUST NOT turn today's direct visit into Snapchat.
    assert classify_entry({'page_url': url, 'referrer': ref, 'source': 'snapchat'}) == expected


def test_sessions_count_once_with_separate_store_and_visitor_and_no_identity_leak():
    event = {'store_id': 'one', 'visitor_id': 'v1', 'session_id': 's1',
             'page_url': 'https://amasi-sa.com/?utm_source=snapchat', 'received_at': NOW.isoformat()}
    result = summarize([event, event, {**event, 'page_url': 'https://amasi-sa.com/cart'},
                        {**event, 'visitor_id': 'v2'}, {**event, 'store_id': 'two'}], NOW)
    assert result['total_sessions'] == 3
    assert sum(row['sessions'] for row in result['sources']) == 3
    assert result['sources'][0]['sessions'] == 3
    assert 'visitor_id' not in str(result)
    assert summarize([], NOW)['known_source_percent'] is None


class Cursor:
    def __init__(self, rows): self.rows = rows
    def sort(self, *args): self.sort_args = args; return self
    def limit(self, value): self.bound = value; return self
    def max_time_ms(self, value): self.time_bound = value; return self
    async def to_list(self, value): return self.rows

class Collection:
    def __init__(self, rows): self.cursor = Cursor(rows)
    def find(self, query, projection): self.query = query; self.projection = projection; return self.cursor

@pytest.mark.asyncio
async def test_window_is_tenant_scoped_production_only_bounded_and_server_timed():
    collection = Collection([])
    result = await read_live_sources({'mezan_first_party_events_v1': collection}, 'owner', NOW)
    assert collection.query == {'user_id': 'owner', 'store_scope': 'production',
        'received_at': {'$gte': '2026-11-18T11:30:00+00:00', '$lte': NOW.isoformat()}}
    assert collection.cursor.bound == 20001
    assert collection.cursor.time_bound == 2500
    assert result['total_sessions'] == 0
    collection.cursor.rows = [{}] * 20001
    with pytest.raises(ValueError):
        await read_live_sources({'mezan_first_party_events_v1': collection}, 'owner', NOW)

@pytest.mark.asyncio
async def test_route_cached_per_owner_and_failure_is_not_zero(monkeypatch):
    current = {'id': 'owner'}
    async def user(): return current
    reader = AsyncMock(return_value=summarize([], NOW))
    monkeypatch.setattr('first_party_attribution.routes.read_live_sources', reader)
    app = FastAPI(); app.include_router(make_first_party_attribution_router({}, user))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        for _ in range(2):
            assert (await client.get('/first-party-attribution/v1/live-sources')).status_code == 200
        assert reader.await_count == 1
        current['id'] = 'another-owner'
        reader.side_effect = RuntimeError('database down')
        response = await client.get('/first-party-attribution/v1/live-sources')
        assert response.status_code == 503
        assert 'total_sessions' not in response.text

@pytest.mark.asyncio
async def test_route_requires_authentication():
    async def deny(): raise HTTPException(status_code=401)
    app = FastAPI(); app.include_router(make_first_party_attribution_router({}, deny))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/first-party-attribution/v1/live-sources')).status_code == 401
