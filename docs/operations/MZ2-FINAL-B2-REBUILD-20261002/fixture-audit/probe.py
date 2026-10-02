"""Retained exact successful independent probe; no source/test changes.

Run through run_probe.py, which strips inherited service credentials.
This is attribution-semantics evidence, not transaction/atomicity evidence.
"""
import asyncio,json,socket
from unittest.mock import AsyncMock,patch
from mongomock_motor import AsyncMongoMockClient
import salla_integration.webhook_order_sync as sync
import fulfillment_v2_routes as fulfillment
import operational_atomic,accounting_shipping_native_observer as observer
import mezan_attribution_ledger_sync as ledger,first_party_attribution as first

async def local_owner(db,owner,callback,**kw): return await callback(db)

async def main():
 def deny(*a,**kw): raise AssertionError('Network forbidden')
 socket.socket.connect=deny;socket.create_connection=deny
 result=[]
 for explode in (False,True):
  db=AsyncMongoMockClient(tz_aware=True).fixture_audit
  await db.salla_integrations.insert_one({'user_id':'owner','store_id':'fixture-store'})
  mock=AsyncMock(side_effect=RuntimeError('isolated ledger unavailable')) if explode else AsyncMock(return_value={'synced':True,'attribution_quality':'confirmed'})
  payload={'id':'123','reference_id':'123','date':'2026-10-01T10:00:00Z','updated_at':'2026-10-01T11:00:00Z','status':{'slug':'under_review','name':'under review'},'payment_method':'cod','amounts':{'total':{'amount':100,'currency':'SAR'}},'source_details':{'source':'snapchat','campaign_id':'synthetic-cmp'}}
  with patch.object(operational_atomic,'operational_owner',local_owner),patch.object(fulfillment,'operational_owner',local_owner),patch.object(observer,'observe_delivery',AsyncMock(return_value={})),patch.object(ledger,'safe_sync_order_to_attribution_ledger',mock),patch.object(first,'link_order_attribution',AsyncMock(return_value={'linked':False})):
   current=await sync.sync_order_from_verified_webhook(db,{'event':'order.created','merchant':'fixture-store','data':payload})
  assert current['synced'] is True,current
  assert mock.await_count==1
  stored=await db.unified_orders.find_one({'user_id':'owner','order_number':'123'})
  assert stored['raw_by_source']['salla_direct']==payload
  assert stored['g47_salla_snapshot']['revision']==1
  assert current['attribution_ledger']['synced'] is (not explode)
  if explode: assert current['attribution_ledger']['reason']=='ledger_bridge_unavailable'
  result.append({'ledger_raises':explode,'order_synced':current['synced'],'attribution':current['attribution_ledger'],'canonical_raw_retained':True,'snapshot_revision':1,'attribution_calls':mock.await_count,'actual_persist_wrapper':True,'transaction_capability':'mocked local callback; not atomicity evidence'})
 print(json.dumps(result))

asyncio.run(main())
