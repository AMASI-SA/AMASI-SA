"""Read-only Preview FX and review/ledger preflight; no financial writes."""
import asyncio,sys,os,json
from pathlib import Path
sys.path.insert(0,'/app/backend');sys.path.insert(0,'/opt/mezan-preview-excel-20260917')
os.environ['MEZAN_PREVIEW_EXCEL_ORDERS']='1'
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import dotenv_values
from bson import json_util
from order_engine.repository import MongoOrderRepository
from order_engine.service import _map_discovery_row
async def main():
 e=dotenv_values('/app/backend/.env');c=AsyncIOMotorClient(e['MONGO_URL']);db=c[e['DB_NAME']]
 draft=await db.accounting_settlements_v2.find_one({'provider':'salla','statement_reference':'6743152'})
 uid=draft['user_id'];repo=MongoOrderRepository(db)
 expected={'286054213':351.0,'286153601':311.01,'285970825':351.0,'286296558':311.01,'286153452':311.01,'286228043':311.01,'285965584':351.0}
 for n,amount in expected.items():
  row=await repo.get_salla_order(user_id=uid,order_number=n);dto=_map_discovery_row(row)
  assert dto.totals.total_sar==amount,(n,dto.totals)
  assert dto.totals.conversion_status=='verified'
 b=json_util.loads(Path('/opt/mezan-preview-excel-20260917/before-salla-verification.json').read_text())
 for name,records in b['financial'].items():
  if name != 'accounting_settlements_v2': assert await db[name].find({'user_id':uid}).to_list(None)==records,name
 print('FX_REPOSITORY_PASS',len(expected),'FINANCIAL_UNCHANGED',True,'DRAFT_STATUS',draft['status'])
 from ledger_core import compute_balance
 balance=await compute_balance(db,user_id=uid,entity_type='payment_gateway',entity_id='salla',sub_account='receivable')
 print('RECEIVABLE_AVAILABLE_SAR',max(float(balance.get('net_balance') or 0),0),'REQUIRED_SAR',58538.94)
 c.close()
asyncio.run(main())
