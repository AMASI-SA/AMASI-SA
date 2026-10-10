"""Operational purchases never prove or mutate physical warehouse stock."""
import asyncio
from copy import deepcopy
import pytest
from fastapi import HTTPException
from test_operational_balance_inventory import seed,command
from test_operational_balance_integration import run,NOW
from test_operational_balance_app_permissions import client,grant,WRITE,READ
from test_operational_balance_supplier_adjustments import adjustment
from operational_balance_inventory import save_purchase
from operational_balance_store import read
from operational_balance_service import report
from operational_inventory_projection import projection

OPTIONS=[{'id':'color','name':'اللون','type':'select','required':True,'values':[{'id':'gold','name':'ذهبي'},{'id':'silver','name':'فضي'}]}, {'id':'size','name':'المقاس','type':'select','required':False,'values':[{'id':'s','name':'صغير'},{'id':'m','name':'وسط'}]}, {'id':'name','name':'الاسم','type':'text','required':True}]
RAW={'state':'raw','selections':[{'option_id':'color','value_id':'gold'}],'inputs':[]}
READY={'state':'ready','selections':[{'option_id':'color','value_id':'silver'}],'inputs':[{'option_id':'name','value':'عبير'}]}
PHYSICAL=['warehouse_locations','warehouse_locations_warehouses','mezan_inventory_receipts_v2','mz2_inventory_cost_states','mezan_inventory_reservations_v2']

async def setup(db):
 await seed(db);await grant(db,[WRITE,READ])
 await db.mezan_products_v2.update_many({}, {'$set':{'options':OPTIONS,'options_count':3}})
 await db.warehouse_locations_warehouses.insert_one({'id':'wh','user_id':'owner','name':'المستودع'})
 await db.warehouse_locations.insert_one({'id':'location','user_id':'owner','warehouse_id':'wh','name':'موقع اختبار','code':'W1','purpose':'permanent_storage','state':'occupied','occupancy':{'total_quantity':3,'items':[{'product_id':'product','quantity':3,'receipt_id':'physical-old','preparation_state':'requires_preparation','specifications':{'اللون':'ذهبي'}}]}})

def purchase():return command(lines=[{'kind':'product','item_id':'product','quantity':10,'unit_price':'15','tax':'0','location_id':'location','purchase_configuration':deepcopy(RAW)}, {'kind':'product','item_id':'product','quantity':100,'unit_price':'20','tax':'0','location_id':'location','purchase_configuration':deepcopy(READY)}])
async def snapshot(db):return {name:await db[name].find({}).to_list(1000) for name in PHYSICAL}

def test_raw10_ready100_cost_retry_returns_and_physical_separation():
 async def scenario(db):
  await setup(db);before=await snapshot(db)
  async with client(db) as c:
   url='/api/operational-balances/inventory-purchases'
   replies=await asyncio.gather(*[c.post(url,json=purchase()) for _ in range(4)])
   assert all(r.status_code==200 for r in replies),[r.text for r in replies]
   invoice=replies[0].json();assert all(r.json()==invoice for r in replies)
   assert invoice['net']=='2150.00' and invoice['gross']=='2150.00'
   assert invoice['physical_stock_status']=='unproven'
   raw,ready=invoice['lines'];assert raw['quantity']==10 and ready['quantity']==100
   assert raw['purchase_line_key']!=ready['purchase_line_key']
   assert raw['purchase_configuration']==RAW and ready['purchase_configuration']==READY
   assert raw['location_name']=='موقع اختبار' and raw['purchase_option_labels'][0]['value']=='ذهبي'
   assert (await c.get(url)).json()['items']==[invoice]
   p=adjustment(invoice);p['lines']=[{'item_id':'product','kind':'product','purchase_line_key':raw['purchase_line_key'],'quantity':2}]
   r=await c.post('/api/operational-balances/supplier-adjustments',json=p);assert r.status_code==200,r.text
   assert [l['remaining_quantity'] for l in r.json()['lines']]==[8,100]
   p['request_id']='ready-return-0002';p['reference']='return-ready';p['lines'][0].update(purchase_line_key=ready['purchase_line_key'],quantity=1)
   r=await c.post('/api/operational-balances/supplier-adjustments',json=p);assert r.status_code==200,r.text
   assert [l['remaining_quantity'] for l in r.json()['lines']]==[8,99]
   physical=(await c.get('/api/operational-balances/inventory')).json()
   assert sum(r['physical'] for r in physical['items'])==3
  state=await read(db,'owner');assert not state['movements'] and len(state['inventory_purchases'])==1
  assert report(state)['summary']['payable']=='2100.00' and report(state)['summary']['actual_liquidity']=='1000.00'
  assert await snapshot(db)==before
 run(scenario)

@pytest.mark.parametrize('bad',['raw_name','required_name','unknown_color','foreign_location','missing_location','duplicated_config','image_file','mixed_variant'])
def test_rejected_metadata_atomic(bad):
 async def scenario(db):
  await setup(db);p=purchase()
  if bad=='raw_name':p['lines'][0]['purchase_configuration']['inputs']=[{'option_id':'name','value':'نورة'}]
  if bad=='required_name':p['lines'][1]['purchase_configuration']['inputs']=[]
  if bad=='unknown_color':p['lines'][0]['purchase_configuration']['selections'][0]['value_id']='invented'
  if bad=='foreign_location':p['lines'][0]['location_id']='foreign'
  if bad=='missing_location':p['lines'][0].pop('location_id')
  if bad=='duplicated_config':p['lines'].append(deepcopy(p['lines'][0]))
  if bad=='image_file':await db.mezan_products_v2.update_many({}, {'$set':{'options':OPTIONS+[{'id':'photo','name':'صورة','type':'file','required':True}],'options_count':4}})
  if bad=='mixed_variant':p['lines'][0]['variant_id']='gold'
  before=await read(db,'owner');physical=await snapshot(db)
  async with client(db) as c:
   r=await c.post('/api/operational-balances/inventory-purchases',json=p);assert r.status_code in (409,422),r.text
  assert await read(db,'owner')==before and await snapshot(db)==physical
 run(scenario)

def test_mid_save_failure_rolls_back_operational_document_and_never_touches_warehouse(monkeypatch):
 async def scenario(db):
  await setup(db);before=await read(db,'owner');physical=await snapshot(db)
  def broken(state):raise RuntimeError('synthetic failure after in-memory invoice append')
  with monkeypatch.context() as m:
   m.setattr('operational_balance_inventory.project_inventory',broken)
   with pytest.raises(RuntimeError):await save_purchase(db,'owner','staff',purchase(),clock=NOW)
  assert await read(db,'owner')==before and await snapshot(db)==physical
  saved=await save_purchase(db,'owner','staff',purchase(),clock=NOW);assert saved['gross']=='2150.00'
 run(scenario)

def test_component_no_sku_location_and_no_phantom_custom_name():
 async def scenario(db):
  await setup(db);p=purchase();p['lines']=[{'kind':'component','item_id':'component','quantity':4,'unit_price':'3','tax':'0','location_id':'location','purchase_configuration':{'state':'raw','selections':[],'inputs':[]}}]
  async with client(db) as c:
   r=await c.post('/api/operational-balances/inventory-purchases',json=p);assert r.status_code==200,r.text
   assert r.json()['gross']=='12.00'
   catalog=(await c.get('/api/operational-balances/inventory-catalog')).json()
   assert len(catalog['products'])==1 and catalog['locations'][0]['id']=='location'
  assert sum(row['physical'] for row in (await projection(db,'owner'))['items'])==3
  assert await db.mezan_products_v2.count_documents({})==1
 run(scenario)


def test_free_ready_name_remains_purchase_metadata_without_catalog_or_physical_creation():
 async def scenario(db):
  await setup(db);p=purchase();p['lines']=[p['lines'][1]];p['lines'][0]['quantity']=1
  p['lines'][0]['purchase_configuration']['inputs'][0]['value']='نورة'
  before=await snapshot(db);product=await db.mezan_products_v2.find_one({})
  saved=await save_purchase(db,'owner','staff',p,clock=NOW)
  assert saved['gross']=='20.00' and saved['lines'][0]['purchase_option_labels'][-1]['value']=='نورة'
  assert await snapshot(db)==before and await db.mezan_products_v2.find_one({})==product
  assert await db.mezan_products_v2.count_documents({})==1
 run(scenario)
