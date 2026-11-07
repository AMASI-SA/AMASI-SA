"""Synthetic native Snap/Mongo workload for a real trial host; zero provider network.

Run inside Sync's existing resource envelope. No token store or live transport.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
import uuid
sys.path.insert(0, '/opt/mezan/backend')

class Tokens:
    async def get_access_token(self, *args, **kwargs): return 'synthetic-host-only-never-provider'

async def main(source, seconds):
    os.environ['MONGO_URL'] = Path('/run/secrets/mongo_uri').read_text().strip()
    os.environ['JWT_SECRET'] = Path('/run/secrets/jwt_secret').read_text().strip()
    from independent_staging import validate
    if validate() != 'staging:'+source: raise ValueError('reviewed synthetic source required')
    import httpx
    from motor.motor_asyncio import AsyncIOMotorClient
    from snapchat_v2.client import SnapchatV2Client
    from snapchat_v2.entities import sync_entities
    from snapchat_v2.facts import upsert_hourly_facts, SNAPCHAT_HOURLY_FACTS_COLLECTION
    from snapchat_v2.lease import ensure_lease_indexes, acquire_lease, heartbeat_lease, release_lease
    mongo = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = mongo['mezan_exit2d_staging']
    marker = await db.exit2d_host_trial_control.find_one({'_id':'seed-once'})
    if not marker or marker.get('state') != 'ready' or marker.get('source_sha') != source:
        raise ValueError('prepared fresh synthetic fixture required')
    user, account, owner = 'synthetic-host-snap-owner', 'synthetic-host-account', str(uuid.uuid4())
    async def transport(request):
        await asyncio.sleep(.05)
        if request.url.path.endswith('/campaigns'):
            payload = {'request_status':'SUCCESS','campaigns':[{'campaign':{'id':f'fixture-{i}','name':f'Synthetic{i}','status':'ACTIVE'}} for i in range(100)],'paging':{}}
        elif request.url.path.endswith('/stats'):
            start = datetime.fromisoformat(request.url.params['start_time']); end = datetime.fromisoformat(request.url.params['end_time'])
            points = []
            while start < end:
                points.append({'start_time':start.isoformat(),'end_time':(start+timedelta(hours=1)).isoformat(),'stats':{'spend':1000000,'impressions':100,'swipes':5}})
                start += timedelta(hours=1)
            payload = {'request_status':'SUCCESS','timeseries_stats':[{'sub_request_status':'SUCCESS','timeseries_stat':{'granularity':'HOUR','breakdown_stats':{'campaign':[{'id':f'fixture-{i}','timeseries':points} for i in range(100)]}}}],'paging':{}}
        else: raise ValueError('unreviewed synthetic transport path')
        return httpx.Response(200,json=payload)
    await ensure_lease_indexes(db)
    if not await acquire_lease(db,user,account,owner): raise ValueError('another synthetic workload owns lease')
    if await acquire_lease(db,user,account,'duplicate'): raise ValueError('duplicate lease accepted')
    client = SnapchatV2Client(db,user,token_store=Tokens(),client_factory=lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(transport),**kw))
    start = datetime(2026,9,6,tzinfo=timezone.utc); deadline=time.monotonic()+seconds; rounds=0
    try:
        while time.monotonic() < deadline:
            await heartbeat_lease(db,user,account,owner)
            rows = await client.fetch_entities(account,'campaign')
            if len(rows['rows']) != 100: raise ValueError('entity fixture differs')
            await sync_entities(db,user_id=user,ad_account_id=account,entity_type='campaign',rows=rows['rows'],sync_run_id='host-synthetic')
            facts = await client.fetch_hourly_facts({'ad_account_id':account,'timezone':'UTC','currency':'SAR'},start_utc=start,end_utc=start+timedelta(hours=24),sync_run_id='host-synthetic')
            await upsert_hourly_facts(db,facts['campaign_rows'])
            count = await db[SNAPCHAT_HOURLY_FACTS_COLLECTION].count_documents({'user_id':user})
            if count != 2400: raise ValueError('hourly facts duplicated or missing')
            rounds+=1
            progress={'rounds':rounds,'state':'running','updated_at':datetime.now(timezone.utc)}
            await db.exit2d_host_snap_progress.update_one({'_id':'snap'},{'$set':progress},upsert=True)
            print(json.dumps({'kind':'host_synthetic_snap','rounds':rounds,'unique_facts':count,'at':datetime.now(timezone.utc).isoformat(),'provider_network':False}),flush=True)
            await asyncio.sleep(.5)
    finally:
        await db.exit2d_host_snap_progress.update_one({'_id':'snap'},{'$set':{'state':'finished'}},upsert=True)
        await release_lease(db,user,account,owner); mongo.close()
    if rounds < 2: raise ValueError('insufficient progress')

if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('--source-sha',required=True)
    p.add_argument('--ack-synthetic-only',action='store_true'); p.add_argument('--seconds',type=int,default=180)
    a=p.parse_args()
    if not a.ack_synthetic_only or not 30 <= a.seconds <= 900: p.error('explicit synthetic acknowledgement and 30..900 seconds required')
    asyncio.run(main(a.source_sha,a.seconds))
