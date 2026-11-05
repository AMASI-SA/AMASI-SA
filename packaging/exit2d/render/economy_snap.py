"""Synthetic Snap transport + real client/lease/entity/fact Mongo paths.

CI injects this into the running sync container, so its additional process and
the unchanged scheduler share the SAME512MiB/0.5CPU envelope. No governor patched.
This is a bounded component sync workload, not live OAuth/full scheduler proof.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import sys
import time

sys.path.insert(0, '/opt/mezan/backend')
from independent_staging import validate
validate()
assert os.environ['MONGO_URL'] == 'mongodb://fixture:synthetic-only-password@staging-mongo:27017'
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from snapchat_v2.client import SnapchatV2Client
from snapchat_v2.entities import sync_entities
from snapchat_v2.facts import upsert_hourly_facts, SNAPCHAT_HOURLY_FACTS_COLLECTION
from snapchat_v2.lease import ensure_lease_indexes, acquire_lease, release_lease

class Tokens:
    async def get_access_token(self, user_id, **kwargs):
        return 'public-synthetic-not-a-provider-token'

async def transport(request):
    await asyncio.sleep(.05)
    if request.url.path.endswith('/campaigns'):
        payload = {'request_status': 'SUCCESS', 'campaigns': [
            {'campaign': {'id': f'fixture-{i}', 'name': f'Synthetic{i}', 'status': 'ACTIVE'}}
            for i in range(100)], 'paging': {}}
    else:
        assert request.url.path.endswith('/stats'), 'unreviewed transport path'
        params = request.url.params
        start = datetime.fromisoformat(params['start_time'])
        end = datetime.fromisoformat(params['end_time'])
        points = []
        while start < end:
            points.append({'start_time': start.isoformat(),
                'end_time': (start+timedelta(hours=1)).isoformat(),
                'stats': {'spend': 1000000, 'impressions': 100, 'swipes': 5}})
            start += timedelta(hours=1)
        payload = {'request_status': 'SUCCESS', 'timeseries_stats': [
            {'sub_request_status': 'SUCCESS', 'timeseries_stat': {'granularity': 'HOUR',
             'breakdown_stats': {'campaign': [{'id': f'fixture-{i}', 'timeseries': points} for i in range(100)]}}}], 'paging': {}}
    return httpx.Response(200, json=payload)

async def main():
    mongo = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = mongo['mezan_exit2d_staging']
    await ensure_lease_indexes(db)
    assert await acquire_lease(db, 'synthetic-snap-owner', 'synthetic-account', 'first')
    assert not await acquire_lease(db, 'synthetic-snap-owner', 'synthetic-account', 'duplicate')
    client = SnapchatV2Client(db, 'synthetic-snap-owner', token_store=Tokens(),
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(transport), **kwargs))
    start = datetime(2026, 9, 6, tzinfo=timezone.utc)
    rounds = 0
    deadline = time.monotonic()+90
    try:
        while time.monotonic() < deadline:
            entities = await client.fetch_entities('synthetic-account', 'campaign')
            assert len(entities['rows']) == 100
            await sync_entities(db, user_id='synthetic-snap-owner', ad_account_id='synthetic-account',
                entity_type='campaign', rows=entities['rows'], sync_run_id='economy-synthetic')
            facts = await client.fetch_hourly_facts({'ad_account_id': 'synthetic-account', 'timezone': 'UTC', 'currency': 'SAR'},
                start_utc=start, end_utc=start+timedelta(hours=24), sync_run_id='economy-synthetic')
            assert len(facts['campaign_rows']) == 2400
            await upsert_hourly_facts(db, facts['campaign_rows'])
            assert await db[SNAPCHAT_HOURLY_FACTS_COLLECTION].count_documents({'user_id': 'synthetic-snap-owner'}) == 2400
            rounds += 1
            print(json.dumps({'kind': 'snap_real_client_real_mongo_simulated_transport', 'rounds': rounds,
                'entities_per_round': 100, 'hourly_facts_unique': 2400, 'duplicate_lease_denied': True}), flush=True)
            await asyncio.sleep(.5)
    finally:
        assert await release_lease(db, 'synthetic-snap-owner', 'synthetic-account', 'first')
        mongo.close()
    assert rounds >= 2

asyncio.run(main())
