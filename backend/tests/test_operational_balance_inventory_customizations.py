"""Canonical product options remain distinct from stock quantity and money."""
from copy import deepcopy
import pytest
from test_operational_balance_inventory_personalizations import seed_variants, personalized
from test_operational_balance_integration import run, NOW
from test_operational_balance_app_permissions import client, grant, WRITE, READ
from operational_balance_inventory import save_purchase, catalog
from operational_balance_store import read

FIELDS = [
 {'id':'name','name':'الاسم','type':'text','required':True},
 {'id':'letter','name':'الحرف','type':'string','required':True},
 {'id':'note','name':'ملاحظة','type':'textarea','required':False}]

async def setup(db):
 await seed_variants(db); await grant(db,[WRITE,READ])
 await db.mezan_products_v2.update_many({}, {'$set':{'options':FIELDS,'options_count':3}})

def allocation(name='عبير',letter='ع',quantity=1):
 return {'quantity':quantity,'values':[{'option_id':'name','value':name},{'option_id':'letter','value':letter}]}

def payload():
 p=personalized()
 p['lines'][0]['personalizations']=[allocation(quantity=4),allocation(letter='ب',quantity=2)]
 p['lines'][1]['personalizations']=[allocation(quantity=1),allocation('روان','ر',3)]
 return p

def test_canonical_fields_two_fields_same_name_different_letter_variants_and_replay():
 async def scenario(db):
  await setup(db)
  products=[r for r in await catalog(db,'owner') if r['kind']=='product']
  assert all(r['customization_fields']==FIELDS and r['customization_issues']==[] for r in products)
  p=payload();p['lines'][0]['personalizations'][0]['values'].append({'option_id':'note','value':' ملاحظة '*30})
  async with client(db) as c:
   url='/api/operational-balances/inventory-purchases'
   response=await c.post(url,json=p); assert response.status_code==200,response.text
   saved=response.json(); assert saved['gross']=='172.50'
   assert [r['unallocated_quantity'] for r in saved['lines']]==[4,1]
   value=saved['lines'][0]['personalizations'][0]['values'][0]
   assert value=={'option_id':'name','option_name':'الاسم','type':'text','value':'عبير'}
   assert (await c.post(url,json=p)).json()==saved
   assert (await c.get(url)).json()['items']==[saved]
  state=await read(db,'owner');assert not state['movements'] and len(state['inventory_purchases'])==1
  assert sum(r['quantity'] for r in saved['lines'])==15
 run(scenario)

@pytest.mark.parametrize('kind',['unknown','duplicate_id','missing_required','blank','too_long','mixed','over_quantity','duplicate_combination','forged_label'])
def test_invalid_canonical_allocations_atomic(kind):
 async def scenario(db):
  await setup(db);p=payload();a=p['lines'][0]['personalizations'][0]
  if kind=='unknown':a['values'][0]['option_id']='not-mz2'
  if kind=='duplicate_id':a['values'].append(deepcopy(a['values'][0]))
  if kind=='missing_required':a['values'].pop()
  if kind=='blank':a['values'][0]['value']=' '
  if kind=='too_long':a['values'][0]['value']='a'*101
  if kind=='mixed':a['name']='عبير'
  if kind=='over_quantity':a['quantity']=11
  if kind=='duplicate_combination':p['lines'][0]['personalizations'].append(deepcopy(a))
  if kind=='forged_label':a['values'][0]['option_name']='forged'
  before=await read(db,'owner')
  async with client(db) as c:
   r=await c.post('/api/operational-balances/inventory-purchases',json=p)
   assert r.status_code==422,r.text
  assert await read(db,'owner')==before
 run(scenario)

@pytest.mark.parametrize('options',[[],[{'id':'name','name':'اسم','type':'file','required':True}],[{'id':'name','name':'اسم','type':'text'}]])
def test_missing_unsupported_incomplete_fields_never_invent_name(options):
 async def scenario(db):
  await setup(db)
  await db.mezan_products_v2.update_many({}, {'$set':{'options':options,'options_count':len(options)}})
  async with client(db) as c:
   r=await c.post('/api/operational-balances/inventory-purchases',json=personalized())
   assert r.status_code==422,r.text
  assert not (await read(db,'owner')).get('inventory_purchases')
 run(scenario)

def test_old_name_request_replays_before_changed_catalog_validation():
 async def scenario(db):
  await seed_variants(db);await grant(db,[WRITE])
  old=personalized();saved=await save_purchase(db,'owner','staff',old,clock=NOW)
  await db.mezan_products_v2.update_many({}, {'$set':{'options':[],'options_count':0}})
  async with client(db) as c:
   r=await c.post('/api/operational-balances/inventory-purchases',json=old)
   assert r.status_code==200 and r.json()==saved,r.text
 run(scenario)

@pytest.mark.parametrize('mode',['names','ids','missing','wrong','ambiguous'])
def test_select_is_proven_by_canonical_variant_not_display_label(mode):
 async def scenario(db):
  await setup(db)
  color={'id':'color','name':'اللون','type':'select','required':True,'values':[{'id':'g','name':'ذهبي'},{'id':'s','name':'فضي'}]}
  product=await db.mezan_products_v2.find_one({})
  variants=product['variants']
  for variant in variants:
   value='ذهبي' if variant['id']=='gold' else 'فضي'
   selection={'name':'اللون','value':value} if mode!='ids' else {'option_id':'color','value_id':'g' if variant['id']=='gold' else 's'}
   if mode=='wrong':selection['value']='غير موجود'
   variant['selections']=[] if mode=='missing' else [selection,selection] if mode=='ambiguous' else [selection]
  await db.mezan_products_v2.update_many({}, {'$set':{'options':FIELDS+[color],'options_count':4,'variants':variants}})
  items=[r for r in await catalog(db,'owner') if r['kind']=='product']
  allowed=mode in {'names','ids'}
  assert all(bool(r['customization_issues'])!=allowed for r in items)
  async with client(db) as c:
   r=await c.post('/api/operational-balances/inventory-purchases',json=payload())
   assert r.status_code==(200 if allowed else 422),r.text
 run(scenario)
