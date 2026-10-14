"""Actual isolated Mongo evidence for the existing physical stock projection."""
from copy import deepcopy
import pytest
from test_operational_balance_inventory import seed
from test_operational_balance_integration import run
from test_operational_balance_app_permissions import client,grant,WRITE,READ
from operational_inventory_projection import projection,SOURCES

async def setup(db):
 await seed(db);await grant(db,[WRITE,READ])
 await db.warehouse_locations_warehouses.insert_one({'id':'wh','user_id':'owner','name':'Test warehouse'})
 await db.warehouse_locations.insert_one({'id':'loc','user_id':'owner','warehouse_id':'wh','code':'W-1','purpose':'permanent_storage','state':'occupied','occupancy':{'total_quantity':110,'items':[
  {'product_id':'product','item_type':'product','quantity':10,'receipt_id':'raw','preparation_state':'requires_preparation','specifications':{'اللون':'ذهبي'}},
  {'product_id':'product','item_type':'product','quantity':100,'receipt_id':'ready','preparation_state':'ready_complete','specifications':{'اللون':'فضي','الاسم':'عبير'}}]}})
 await db.mezan_inventory_reservations_v2.insert_one({'id':'hold','user_id':'owner','order_number':'synthetic-only','status':'active','allocations':[{'inventory_row_key':'receipt:raw','location_id':'loc','quantity':1}]})
 await db.mz2_inventory_cost_states.insert_one({'user_id':'owner','inventory_identity':{'item_type':'product','product_id':'product','variant_id':None},'authoritative':True,'cost_policy_version':'moving-weighted-average-v1','average_cost':'12.50'})

 # Fixtures now include actual receipt evidence; purchase-report rows alone
 # must never imply physical availability.
 from product_inventory_rules import build_inventory_configuration_key
 loc=await db.warehouse_locations.find_one({'id':'loc'})
 for index,item in enumerate(loc['occupancy']['items']):
  key=build_inventory_configuration_key(sku='product',preparation_state=item['preparation_state'],specifications=item['specifications'])
  await db.warehouse_locations.update_one({'id':'loc'},{'$set':{f'occupancy.items.{index}.configuration_key':key}})
  await db.mezan_inventory_receipts_v2.insert_one({**item,'id':item['receipt_id'],'configuration_key':key,'user_id':'owner','location_id':'loc','warehouse_id':'wh','source_type':'purchase_invoice','source_id':'invoice','source_line_id':str(index),'status':'posted'})

async def snapshot(db):return {name:await db[name].find({}).to_list(1000) for name in SOURCES}

def test_physical_authority_raw_customized_cost_reservations_and_zero_writes():
 async def scenario(db):
  await setup(db);before=await snapshot(db)
  async with client(db) as c:
   r=await c.get('/api/operational-balances/inventory');assert r.status_code==200,r.text
   result=r.json();assert result['source']=='warehouse_locations' and result['read_only'] and result['schema_version']==1
   raw,ready=result['items'];assert (raw['physical'],raw['reserved'],raw['available'])==(10,1,9)
   assert ready['physical']==100 and ready['available']==100 and ready['specifications']['الاسم']=='عبير'
   assert raw['unit_cost']=='12.50' and raw['inventory_value']=='125.00'
   assert sum(l['physical'] for l in result['items'])==110
  assert await snapshot(db)==before
 run(scenario)

@pytest.mark.parametrize('condition',['quarantine','pending_inspection','damaged','not_sellable'])
def test_held_stock_never_available(condition):
 async def scenario(db):
  await setup(db)
  await db.warehouse_locations.update_one({'id':'loc'},{'$set':{'occupancy.items.0.condition':condition}})
  row=(await projection(db,'owner'))['items'][0]
  assert row['physical']==10 and row['held']==9 and row['reserved']==1 and row['available']==0
 run(scenario)

@pytest.mark.parametrize('problem',['duplicate','excess','missing','location'])
def test_ambiguous_reservation_fail_closed(problem):
 async def scenario(db):
  await setup(db)
  if problem=='duplicate':await db.mezan_inventory_reservations_v2.insert_one({'id':'hold','user_id':'owner','status':'active','allocations':[{'inventory_row_key':'receipt:raw','quantity':1}]})
  if problem=='excess':await db.mezan_inventory_reservations_v2.update_one({}, {'$set':{'allocations.0.quantity':11}})
  if problem=='missing':await db.mezan_inventory_reservations_v2.update_one({}, {'$set':{'allocations.0.inventory_row_key':'unknown'}})
  if problem=='location':await db.mezan_inventory_reservations_v2.update_one({}, {'$set':{'allocations.0.location_id':'other'}})
  row=(await projection(db,'owner'))['items'][0]
  assert row['available'] is None and row['reserved'] is None and row['availability_issues']
 run(scenario)

def test_component_without_sku_exact_unit_reservations_no_service_or_legacy_fallback():
 async def scenario(db):
  await setup(db)
  await db.mezan_cost_resources_v2.update_one({'id':'component'},{'$set':{'track_inventory':True}})
  await db.warehouse_locations.update_one({'id':'loc'},{'$push':{'occupancy.items':{'item_type':'stock_component','resource_id':'component','receipt_id':'component-lot','quantity':4,'configuration_key':'component-config'}}})
  await db.mezan_inventory_receipts_v2.insert_one({'id':'component-lot','user_id':'owner','location_id':'loc','warehouse_id':'wh','source_type':'purchase_invoice','source_id':'invoice','source_line_id':'component','status':'posted','item_type':'stock_component','resource_id':'component','quantity':4,'configuration_key':'component-config'})
  for index in range(2):
   await db.mezan_component_consumption_units_v1.insert_one({'_id':'unit-'+str(index),'user_id':'owner','state':'reserved','order_line_id':'same-line','allocations':[{'id':'allocation-'+str(index),'location_id':'loc','lot_id':'component-lot','resource_id':'component','quantity':'1'}]})
  await db.products.insert_one({'id':'legacy-product','user_id':'owner','name':'Legacy must not appear'})
  await db.warehouse_locations.update_one({'id':'loc'},{'$push':{'occupancy.items':{'product_id':'legacy-product','receipt_id':'legacy','quantity':999}}})
  result=await projection(db,'owner');component=next(r for r in result['items'] if r['kind']=='component')
  assert component['sku'] is None and component['physical']==4 and component['reserved']==2 and component['available']==2
  assert component['unit_cost'] is None and component['inventory_value'] is None
  assert len(result['items'])==3 and result['warnings']
 run(scenario)

@pytest.mark.parametrize('permissions,status',[([],403),([WRITE],403),([READ],200),([READ,WRITE],200)])
def test_reports_permission(permissions,status):
 async def scenario(db):
  await setup(db);await grant(db,permissions)
  async with client(db) as c:
   r=await c.get('/api/operational-balances/inventory');assert r.status_code==status,r.text
 run(scenario)

def test_unknown_cost_and_other_owner_do_not_leak():
 async def scenario(db):
  await setup(db);await db.mz2_inventory_cost_states.delete_many({})
  await db.mz2_inventory_cost_states.insert_one({'user_id':'other','inventory_identity':{'item_type':'product','product_id':'product'},'authoritative':True,'cost_policy_version':'moving-weighted-average-v1','average_cost':'999'})
  result=await projection(db,'owner');assert all(r['unit_cost'] is None and r['inventory_value'] is None for r in result['items'])
  assert (await projection(db,'other'))['items']==[]
 run(scenario)
