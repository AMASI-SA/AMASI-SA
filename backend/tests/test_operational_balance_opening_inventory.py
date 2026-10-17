"""Real replica-set acceptance for independent opening quantities (no journals)."""
from copy import deepcopy
import asyncio
import pytest
from fastapi import HTTPException
from test_operational_balance_integration import run
from fastapi import FastAPI
import httpx
from operational_balance_routes import make_operational_balance_router


def client(db, actor, mobile=False):
    app = FastAPI()
    async def user():
        return await db.users.find_one({'id': actor}, {'_id': 0})
    app.include_router(make_operational_balance_router(db, user), prefix='/api')
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated')

from operational_opening_inventory import preview, approve, context, BATCHES, RECEIPTS
from operational_inventory_projection import projection
from operational_atomic import operational_owner

OWNER = {'id': 'owner'}


def request():
    return {'request_id': 'opening-stock-1', 'lines': [{'kind': 'product', 'item_id': 'product',
        'location_id': 'shelf', 'quantity': 5, 'unit_cost': '12.50',
        'purchase_configuration': {'state': 'ready', 'selections': [], 'inputs': []}}]}


async def seed(db, enabled=True):
    await db.mezan_products_v2.insert_one({'user_id': 'owner', 'mezan_product_id': 'product',
        'name': 'منتج افتتاحي', 'sku': 'SKU1', 'options': [], 'variants': [], 'variants_count': 0})
    await db.warehouse_locations_warehouses.insert_one({'user_id': 'owner', 'id': 'warehouse', 'name': 'Warehouse', 'status': 'active'})
    await db.warehouse_locations.insert_one({'user_id': 'owner', 'id': 'shelf', 'warehouse_id': 'warehouse',
        'name': 'Shelf', 'code': 'S1', 'state': 'empty', 'max_items': 20, 'occupancy': None})
    if enabled:
        await db.mz2_atomic_owners.insert_one({'_id': 'owner', 'physical_inventory_writes_paused': False,
                                              'inventory_control_revision': 1, 'writes_paused': True})


def test_owner_preview_approve_replay_and_no_financial_effect():
    async def scenario(db):
        await seed(db)
        before = await db.mz2_financial_accounts.find({}).to_list(100)
        for actor in ('staff','other'):
            async with client(db, actor, mobile=False) as c:
                result = await c.post('/api/operational-balances/inventory-opening/preview', json=request())
                assert result.status_code == (403 if actor == 'staff' else 422)
        async with client(db, 'owner', mobile=False) as c:
            p = await c.post('/api/operational-balances/inventory-opening/preview', json=request())
            assert p.status_code == 200, p.text
            assert await db[BATCHES].count_documents({}) == 0
            body = {**request(), 'preview_hash': p.json()['preview_hash']}
            first = await c.post('/api/operational-balances/inventory-opening/approve', json=body)
            assert first.status_code == 200, first.text
            again = await c.post('/api/operational-balances/inventory-opening/approve', json=body)
            assert again.status_code == 200 and again.json() == first.json(), again.text
        assert await db[RECEIPTS].count_documents({}) == 1
        location = await db.warehouse_locations.find_one({'id': 'shelf'})
        assert location['occupancy']['total_quantity'] == 5
        assert (await context(db, OWNER))['approved'] is True
        for collection in ('accounting_journal_groups_v2','accounting_general_ledger_v2','mz2_inventory_cost_states','mz2_opening_facts_v2','purchase_invoices','settings'):
            assert await db[collection].count_documents({}) == 0
        assert await db.mz2_financial_accounts.find({}).to_list(100) == before
        state = await db.mz2_atomic_owners.find_one({'_id': 'owner'})
        assert state['writes_paused'] is True
        stock = await projection(db, 'owner')
        assert stock['items'][0]['physical'] == 5
        assert stock['items'][0]['available'] == 0
        assert stock['items'][0]['unit_cost'] is None
        assert stock['items'][0]['operational_unit_cost'] == '12.50'
        assert stock['items'][0]['operational_inventory_value'] == '62.50'
    run(scenario)


@pytest.mark.parametrize('control', [None, {}, {'physical_inventory_writes_paused': False}])
def test_default_closed_preview_allowed_approval_has_no_stock_effect(control):
    async def scenario(db):
        await seed(db, False)
        if control is not None: await db.mz2_atomic_owners.insert_one({'_id': 'owner', **control})
        p = await preview(db, OWNER, request())
        assert p['writer_disabled'] is True
        with pytest.raises(HTTPException) as error:
            await approve(db, OWNER, {**request(), 'preview_hash': p['preview_hash']})
        assert error.value.status_code == 423
        assert await db[BATCHES].count_documents({}) == 0
        assert await db[RECEIPTS].count_documents({}) == 0
        assert (await db.warehouse_locations.find_one({'id': 'shelf'}))['occupancy'] is None
    run(scenario)


@pytest.mark.parametrize('change', ['catalog', 'capacity', 'stock', 'owner'])
def test_stale_preview_or_changed_owner_cannot_write(change):
    async def scenario(db):
        await seed(db)
        p = await preview(db, OWNER, request())
        if change == 'catalog': await db.mezan_products_v2.update_one({'mezan_product_id': 'product'}, {'$set': {'name': 'Changed'}})
        if change == 'capacity': await db.warehouse_locations.update_one({'id': 'shelf'}, {'$set': {'max_items': 2}})
        if change == 'stock': await db.warehouse_locations.update_one({'id': 'shelf'}, {'$set': {'occupancy': {'items': [{'quantity': 1}], 'total_quantity': 1}}})
        if change == 'owner': await db.users.update_one({'id': 'owner'}, {'$set': {'role': 'employee'}})
        with pytest.raises(HTTPException):
            await approve(db, OWNER, {**request(), 'preview_hash': p['preview_hash']})
        assert await db[BATCHES].count_documents({}) == 0
        assert await db[RECEIPTS].count_documents({}) == 0
    run(scenario)


@pytest.mark.parametrize('change', ['duplicate', 'cost', 'foreign_location', 'foreign_product'])
def test_invalid_line_fails_before_stock_write(change):
    async def scenario(db):
        await seed(db); body = request()
        if change == 'duplicate': body['lines'].append(deepcopy(body['lines'][0]))
        if change == 'cost': body['lines'][0]['unit_cost'] = 'NaN'
        if change == 'foreign_location': body['lines'][0]['location_id'] = 'foreign'
        if change == 'foreign_product': body['lines'][0]['item_id'] = 'foreign'
        with pytest.raises(HTTPException): await preview(db, OWNER, body)
        assert await db[RECEIPTS].count_documents({}) == 0
    run(scenario)


def test_restricted_profile_cannot_write_financial_or_control_records_even_if_caught():
    async def scenario(db):
        await seed(db)
        for name in ('general_ledger','mz2_inventory_cost_states','settings','mz2_atomic_owners'):
            async def forbidden(scoped):
                try: await scoped[name].insert_one({'user_id':'owner', 'amount':1})
                except HTTPException: pass
            with pytest.raises(HTTPException):
                from inventory_write_gate import admitted
                await admitted(db, 'owner', 'test-forbidden', lambda: operational_owner(db, 'owner', forbidden, profile='opening_inventory'))
    run(scenario)


def test_two_concurrent_initial_batches_cannot_duplicate_stock():
    async def scenario(db):
        await seed(db)
        p = await preview(db, OWNER, request())
        result = await asyncio.gather(*[approve(db, OWNER, {**request(), 'preview_hash': p['preview_hash']}) for _ in range(2)], return_exceptions=True)
        assert any(isinstance(r, dict) and r['status'] == 'approved' for r in result)
        assert all(isinstance(r, dict) or isinstance(r, HTTPException) and r.status_code == 429 for r in result)
        assert await db[BATCHES].count_documents({}) == 1
        assert (await db.warehouse_locations.find_one({'id':'shelf'}))['occupancy']['total_quantity'] == 5
    run(scenario)


def test_approval_rolls_back_entire_batch_on_event_failure(monkeypatch):
    import operational_opening_inventory as service
    original = service.validate_insert
    monkeypatch.setattr(service, 'validate_insert', lambda name, doc: name != 'warehouse_location_events' and original(name, doc))
    async def scenario(db):
        await seed(db); p = await preview(db, OWNER, request())
        with pytest.raises(HTTPException):
            await approve(db, OWNER, {**request(), 'preview_hash': p['preview_hash']})
        assert await db[BATCHES].count_documents({}) == 0
        assert await db[RECEIPTS].count_documents({}) == 0
        assert (await db.warehouse_locations.find_one({'id':'shelf'}))['occupancy'] is None
    run(scenario)


@pytest.mark.parametrize('bad', [float('nan'), True, -1])
def test_malformed_capacity_is_rejected(bad):
    async def scenario(db):
        await seed(db)
        await db.warehouse_locations.update_one({'id':'shelf'}, {'$set': {'max_items': bad}})
        with pytest.raises(HTTPException): await preview(db, OWNER, request())
    run(scenario)


def test_exact_variant_and_customization_identity_is_required():
    async def scenario(db):
        await seed(db)
        option = {'id':'color', 'name':'اللون', 'type':'select', 'required':True, 'values':[{'id':'red','name':'أحمر'},{'id':'blue','name':'أزرق'}]}
        variant = {'id':'red-variant','name':'Red', 'selections':[{'option_id':'color','value_id':'red'}]}
        await db.mezan_products_v2.update_one({'mezan_product_id':'product'}, {'$set': {'options':[option], 'variants':[variant], 'variants_count':1}})
        body = request()
        with pytest.raises(HTTPException): await preview(db, OWNER, body)
        body['lines'][0]['purchase_configuration']['selections']=[{'option_id':'color','value_id':'red'}]
        p = await preview(db, OWNER, body)
        assert p['lines'][0]['inventory_identity']['salla_variant_id'] == 'red-variant'
        await approve(db, OWNER, {**body, 'preview_hash':p['preview_hash']})
        proof = await db[RECEIPTS].find_one({})
        assert proof['salla_variant_id'] == 'red-variant'
        assert proof['specifications'] == {'اللون':'أحمر'}
    run(scenario)


def test_preview_then_emergency_pause_and_hash_tampering_fail_closed():
    async def scenario(db):
        await seed(db)
        p = await preview(db, OWNER, request())
        with pytest.raises(HTTPException) as error:
            await approve(db, OWNER, {**request(), 'preview_hash':'0'*64})
        assert error.value.detail['code'] == 'opening_inventory_preview_changed'
        await db.mz2_atomic_owners.update_one({'_id':'owner'}, {'$set': {'physical_inventory_writes_paused':True, 'inventory_control_revision':2}})
        with pytest.raises(HTTPException) as error:
            await approve(db, OWNER, {**request(), 'preview_hash':p['preview_hash']})
        assert error.value.status_code == 423
        assert await db[BATCHES].count_documents({}) == 0
        assert await db[RECEIPTS].count_documents({}) == 0
    run(scenario)


def test_one_batch_multiple_products_locations_and_changed_replay_conflict():
    async def scenario(db):
        await seed(db)
        await db.mezan_products_v2.insert_one({'user_id':'owner','mezan_product_id':'second','name':'Second', 'options':[], 'variants':[], 'variants_count':0})
        await db.warehouse_locations.insert_one({'user_id':'owner','id':'shelf2','warehouse_id':'warehouse','state':'empty','occupancy':None,'max_items':10})
        body=request()
        body['lines'] += [dict(deepcopy(body['lines'][0]), item_id='second', quantity=3),
                          dict(deepcopy(body['lines'][0]), location_id='shelf2',quantity=2)]
        p=await preview(db, OWNER, body)
        result=await approve(db, OWNER, {**body,'preview_hash':p['preview_hash']})
        assert result['total_quantity'] == 10 and result['total_cost'] == '125.00'
        assert (await db.warehouse_locations.find_one({'id':'shelf'}))['occupancy']['total_quantity'] == 8
        assert (await db.warehouse_locations.find_one({'id':'shelf2'}))['occupancy']['total_quantity'] == 2
        body['lines'][0]['quantity']=9
        with pytest.raises(HTTPException) as error:
            await approve(db, OWNER, {**body,'preview_hash':p['preview_hash']})
        assert error.value.status_code == 409
        assert await db[RECEIPTS].count_documents({}) == 3
        assert await db[BATCHES].count_documents({}) == 1
    run(scenario)


def test_multirow_capacity_failure_creates_no_batch():
    async def scenario(db):
        await seed(db)
        await db.mezan_products_v2.insert_one({'user_id':'owner','mezan_product_id':'second','name':'Second','options':[],'variants':[]})
        body=request();body['lines'] += [dict(deepcopy(body['lines'][0]),item_id='second',quantity=16)]
        with pytest.raises(HTTPException): await preview(db,OWNER,body)
        assert await db[BATCHES].count_documents({}) == 0
        assert (await db.warehouse_locations.find_one({'id':'shelf'}))['occupancy'] is None
    run(scenario)


def test_projection_never_uses_unproven_operational_cost():
    async def scenario(db):
        await seed(db)
        p=await preview(db,OWNER,request())
        await approve(db,OWNER,{**request(),'preview_hash':p['preview_hash']})
        await db[RECEIPTS].update_one({}, {'$set': {'operational_total_cost':'999.00'}})
        result=await projection(db,'owner')
        assert result['items'][0]['unit_cost'] is None
        assert result['items'][0].get('operational_unit_cost') is None
        assert result['items'][0]['available'] == 0
    run(scenario)


def test_mobile_shaped_principal_cannot_impersonate_owner():
    async def scenario(db):
        await seed(db)
        app=FastAPI()
        async def principal():
            return {'id':'owner','role':'owner','_mobile_actor_id':'staff','_session_client':'amasi_mobile'}
        app.include_router(make_operational_balance_router(db,principal),prefix='/api')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://isolated') as c:
            for path in ('preview','approve'):
                body = request() if path == 'preview' else {**request(),'preview_hash':'a'*64}
                assert (await c.post('/api/operational-balances/inventory-opening/'+path,json=body)).status_code == 403
        with pytest.raises(HTTPException) as error:
            await preview(db,await principal(),request())
        assert error.value.status_code == 403
        assert await db[BATCHES].count_documents({}) == 0
    run(scenario)


def test_context_and_preview_leave_all_collections_unchanged():
    async def scenario(db):
        await seed(db)
        async def snapshot():
            return {name: await db[name].find({}).sort('_id',1).to_list(1000)
                    for name in await db.list_collection_names()}
        before=await snapshot()
        await context(db,OWNER)
        await preview(db,OWNER,request())
        assert await snapshot() == before
    run(scenario)


@pytest.mark.parametrize('duplicate_kind', ['archived_product', 'disabled_location'])
@pytest.mark.parametrize('duplicate_first', [False, True])
def test_hidden_duplicate_identity_cannot_bind_opening_stock(duplicate_kind, duplicate_first):
    async def scenario(db):
        hidden_product = {'user_id':'owner', 'mezan_product_id':'product', 'name':'Archived duplicate',
                          'sku':'WRONG-SKU', 'archived':True, 'options':[], 'variants':[]}
        hidden_location = {'user_id':'owner', 'id':'shelf', 'warehouse_id':'warehouse',
                           'state':'disabled', 'occupancy':None, 'max_items':20}
        collection = db.mezan_products_v2 if duplicate_kind == 'archived_product' else db.warehouse_locations
        duplicate = hidden_product if duplicate_kind == 'archived_product' else hidden_location
        if duplicate_first:
            await collection.insert_one(deepcopy(duplicate))
        await seed(db)
        if not duplicate_first:
            await collection.insert_one(deepcopy(duplicate))
        with pytest.raises(HTTPException) as error:
            await preview(db, OWNER, request())
        assert error.value.status_code == 409
        assert error.value.detail['code'] == 'opening_inventory_identity_ambiguous'
        assert await db[BATCHES].count_documents({}) == 0
        assert await db[RECEIPTS].count_documents({}) == 0
        assert all(row['occupancy'] is None for row in await db.warehouse_locations.find({}).to_list(20))
    run(scenario)


@pytest.mark.parametrize('duplicate_kind', ['archived_product', 'disabled_location'])
def test_hidden_duplicate_added_after_preview_blocks_approval(duplicate_kind):
    async def scenario(db):
        await seed(db)
        p=await preview(db, OWNER, request())
        if duplicate_kind == 'archived_product':
            await db.mezan_products_v2.insert_one({'user_id':'owner','mezan_product_id':'product',
                'name':'Old duplicate','sku':'WRONG','archived':True,'options':[],'variants':[]})
        else:
            await db.warehouse_locations.insert_one({'user_id':'owner','id':'shelf',
                'warehouse_id':'warehouse','state':'disabled','occupancy':None,'max_items':20})
        with pytest.raises(HTTPException) as error:
            await approve(db, OWNER, {**request(),'preview_hash':p['preview_hash']})
        assert error.value.detail['code'] == 'opening_inventory_identity_ambiguous'
        assert await db[BATCHES].count_documents({}) == 0
        assert await db[RECEIPTS].count_documents({}) == 0
    run(scenario)


def test_disabled_duplicate_warehouse_blocks_opening():
    async def scenario(db):
        await seed(db)
        p=await preview(db,OWNER,request())
        await db.warehouse_locations_warehouses.insert_one({'user_id':'owner','id':'warehouse','name':'Old duplicate','status':'disabled'})
        for action in ('preview','approve'):
            with pytest.raises(HTTPException) as error:
                if action == 'preview': await preview(db,OWNER,request())
                else: await approve(db,OWNER,{**request(),'preview_hash':p['preview_hash']})
            assert error.value.detail['code'] == 'opening_inventory_identity_ambiguous'
        assert await db[BATCHES].count_documents({}) == 0
        assert await db[RECEIPTS].count_documents({}) == 0
    run(scenario)


@pytest.mark.parametrize('change', [{'name':'Renamed'}, {'active':False}, {'archived':True}])
def test_warehouse_snapshot_changes_reject_approval(change):
    async def scenario(db):
        await seed(db)
        p=await preview(db,OWNER,request())
        await db.warehouse_locations_warehouses.update_one({'id':'warehouse'}, {'$set':change})
        with pytest.raises(HTTPException):
            await approve(db,OWNER,{**request(),'preview_hash':p['preview_hash']})
        assert await db[BATCHES].count_documents({}) == 0
        assert await db[RECEIPTS].count_documents({}) == 0
    run(scenario)


async def seed_component(db, identity='fabric', unit='m', **overrides):
    await db.mezan_cost_resources_v2.insert_one({'user_id':'owner','id':identity,'name':'Fabric',
        'kind':'stock_component','track_inventory':True,'status':'active','unit':unit,'code':identity.upper(),
        'image_url':'https://example.com/fabric.png',**overrides})


def component_line(identity='fabric', quantity=2.5, cost='8.00'):
    return {'kind':'component','item_id':identity,'location_id':'shelf','quantity':quantity,'unit_cost':cost,
            'purchase_configuration':{'state':'raw','selections':[],'inputs':[]}}


def test_component_fractional_opening_metadata_cost_projection_and_replay():
    async def scenario(db):
        await seed(db);await seed_component(db)
        ctx=await context(db,OWNER)
        assert ctx['components'][0]['quantity_precision'] == 6
        assert ctx['components'][0]['component_type'] == 'stock_component'
        assert ctx['components'][0]['sku'] == 'FABRIC'
        body={'request_id':'components-opening','lines':[component_line()]}
        p=await preview(db,OWNER,body)
        assert p['total_cost']=='20.00'
        assert p['quantity_totals']==[{'unit':'m','quantity':2.5}]
        a=await approve(db,OWNER,{**body,'preview_hash':p['preview_hash']})
        assert await approve(db,OWNER,{**body,'preview_hash':p['preview_hash']}) == a
        stock=await projection(db,'owner');row=stock['items'][0]
        assert row['kind']=='component' and row['resource_id']=='fabric' and row['physical']==2.5
        assert row['available']==0 and row['unit_cost'] is None
        assert row['operational_unit_cost']=='8.00' and row['operational_inventory_value']=='20.00'
        assert await db.mz2_inventory_cost_states.count_documents({})==0
        assert await db.accounting_general_ledger_v2.count_documents({})==0
    run(scenario)


def test_mixed_units_have_separate_quantity_totals_and_half_up_line_rounding():
    async def scenario(db):
        await seed(db);await seed_component(db)
        body=request();body['lines'].append(component_line(quantity=.125,cost='1.00'))
        p=await preview(db,OWNER,body)
        assert p['total_quantity'] is None
        assert p['quantity_totals']==[{'unit':'m','quantity':.125},{'unit':'piece','quantity':5}]
        assert p['lines'][1]['total_cost']=='0.13' and p['total_cost']=='62.63'
        await approve(db,OWNER,{**body,'preview_hash':p['preview_hash']})
        stock=await projection(db,'owner')
        component=next(r for r in stock['items'] if r['kind']=='component')
        assert component['operational_inventory_value']=='0.13'
    run(scenario)


@pytest.mark.parametrize('unit,quantity', [('piece',2.5),('m',.0000001),('kg',-1),('m',100001)])
def test_fractional_component_quantity_boundaries(unit,quantity):
    async def scenario(db):
        await seed(db);await seed_component(db,unit=unit)
        body={'request_id':'components-invalid','lines':[component_line(quantity=quantity)]}
        from pydantic import ValidationError
        with pytest.raises((HTTPException,ValidationError)):await preview(db,OWNER,body)
        assert await db[RECEIPTS].count_documents({})==0
    run(scenario)


@pytest.mark.parametrize('quantity',[True,'2.5',float('nan'),float('inf')])
def test_nonnumeric_component_quantity_rejected_at_boundary(quantity):
    from operational_opening_inventory import OpeningRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        OpeningRequest.model_validate({'request_id':'components-invalid','lines':[component_line(quantity=quantity)]})


def test_fractional_product_quantity_stays_rejected():
    async def scenario(db):
        await seed(db);body=request();body['lines'][0]['quantity']=1.5
        with pytest.raises(HTTPException):await preview(db,OWNER,body)
        assert await db[RECEIPTS].count_documents({})==0
    run(scenario)


@pytest.mark.parametrize('override',[{'track_inventory':False},{'kind':'service'},{'status':'inactive'},{'archived':True},{'user_id':'other'}])
def test_nonstock_inactive_or_foreign_components_not_eligible(override):
    async def scenario(db):
        await seed(db);await seed_component(db,**override)
        assert (await context(db,OWNER))['components']==[]
        with pytest.raises(HTTPException):
            await preview(db,OWNER,{'request_id':'components-invalid','lines':[component_line()]})
    run(scenario)


def test_archived_duplicate_component_and_stale_catalog_fail_closed():
    async def scenario(db):
        await seed(db);await seed_component(db)
        body={'request_id':'components-invalid','lines':[component_line()]}
        p=await preview(db,OWNER,body)
        await seed_component(db,archived=True)
        with pytest.raises(HTTPException):await approve(db,OWNER,{**body,'preview_hash':p['preview_hash']})
        assert await db[RECEIPTS].count_documents({})==0
    run(scenario)


def test_fractional_capacity_exact_sum_and_distinct_component_costs():
    async def scenario(db):
        await seed(db)
        await db.warehouse_locations.update_one({'id':'shelf'},{'$set':{'max_items':.6}})
        for identity in ('a','b','c'):await seed_component(db,identity)
        body={'request_id':'components-capacity','lines':[component_line('a',.1),component_line('b',.2),component_line('c',.3)]}
        p=await preview(db,OWNER,body)
        assert p['total_quantity']==.6
        await approve(db,OWNER,{**body,'preview_hash':p['preview_hash']})
        assert await db[RECEIPTS].count_documents({})==3
    run(scenario)


def test_gold_silver_and_named_embroidery_remain_separate_costed_stock_rows():
    async def scenario(db):
        await seed(db)
        await db.warehouse_locations.update_one({'id':'shelf'},{'$set':{'max_items':100}})
        color={'id':'color','name':'اللون','type':'select','required':True,'values':[{'id':'gold','name':'ذهبي'},{'id':'silver','name':'فضي'}]}
        service={'id':'service','name':'التخصيص','type':'select','required':True,'values':[{'id':'embroidery','name':'تطريز'},{'id':'none','name':'بدون'}]}
        name={'id':'name','name':'الاسم','type':'text','required':False,'values':[]}
        variants=[{'id':v,'name':v,'selections':[{'option_id':'color','value_id':v}]} for v in ('gold','silver')]
        await db.mezan_products_v2.update_one({'mezan_product_id':'product'},{'$set':{'options':[color,service,name],'variants':variants,'variants_count':2}})
        body={'request_id':'named-opening','lines':[]}
        for color_id,service_id,personalization,quantity,cost in [('gold','none','',10,'7.00'),('silver','none','',12,'10.00'),('gold','embroidery','عبير',20,'60.00'),('gold','embroidery','فاطمة',12,'60.00')]:
            line=deepcopy(request()['lines'][0]);line.update(quantity=quantity,unit_cost=cost)
            line['purchase_configuration']={'state':'ready','selections':[{'option_id':'color','value_id':color_id},{'option_id':'service','value_id':service_id}],
                'inputs':[{'option_id':'name','value':personalization}] if personalization else []}
            body['lines'].append(line)
        p=await preview(db,OWNER,body)
        assert [r['total_cost'] for r in p['lines']]==['70.00','120.00','1200.00','720.00']
        assert p['total_cost']=='2110.00'
        assert len({r['inventory_identity']['configuration_key'] for r in p['lines']})==4
        assert [r['inventory_identity']['salla_variant_id'] for r in p['lines']]==['gold','silver','gold','gold']
        await approve(db,OWNER,{**body,'preview_hash':p['preview_hash']})
        assert await db[RECEIPTS].count_documents({})==4
    run(scenario)


def test_null_capacity_is_native_unlimited_and_fractional_small_cost_rounds_zero():
    async def scenario(db):
        await seed(db);await seed_component(db,unit='kg')
        await db.warehouse_locations.update_one({'id':'shelf'},{'$set':{'max_items':None}})
        body={'request_id':'component-subcent','lines':[component_line(quantity=.001,cost='0.01')]}
        p=await preview(db,OWNER,body)
        assert p['lines'][0]['total_cost']=='0.00' and p['total_cost']=='0.00'
        await approve(db,OWNER,{**body,'preview_hash':p['preview_hash']})
        row=(await projection(db,'owner'))['items'][0]
        assert row['physical']==.001 and row['operational_inventory_value']=='0.00'
        assert row['available']==0 and row['unit_cost'] is None
    run(scenario)


def test_recorded_component_unit_cannot_be_reinterpreted_after_catalog_edit():
    async def scenario(db):
        await seed(db);await seed_component(db,unit='m')
        body={'request_id':'component-original-unit','lines':[component_line()]}
        p=await preview(db,OWNER,body)
        await approve(db,OWNER,{**body,'preview_hash':p['preview_hash']})
        await db.mezan_cost_resources_v2.update_one({'id':'fabric'},{'$set':{'unit':'kg'}})
        row=(await projection(db,'owner'))['items'][0]
        assert row['unit']=='m' and row['physical']==2.5
        assert row['operational_inventory_value']=='20.00'
        assert row['unit_cost'] is None and row['available']==0
    run(scenario)


def test_age_ten_dresses_with_embroidery_names_are_distinct_physical_rows():
    async def scenario(db):
        await seed(db)
        await db.warehouse_locations.update_one({'id':'shelf'},{'$set':{'max_items':100}})
        size={'id':'size','name':'المقاس','type':'select','required':True,'values':[{'id':'age10','name':'عمر 10'}]}
        service={'id':'service','name':'الخدمة','type':'select','required':True,'values':[{'id':'embroidery','name':'تطريز الاسم'}]}
        name={'id':'name','name':'الاسم','type':'text','required':True,'values':[]}
        await db.mezan_products_v2.insert_one({'user_id':'owner','mezan_product_id':'dress','name':'فستان',
            'sku':'DRESS','options':[size,service,name], 'variants_count':1,
            'variants':[{'id':'dress-age10','name':'عمر 10','sku':'DRESS-10','selections':[{'option_id':'size','value_id':'age10'}]}]})
        body={'request_id':'dresses-age-ten','lines':[]}
        for personal,qty in [('عبير',20),('فاطمة',12)]:
            line=deepcopy(request()['lines'][0]);line.update(item_id='dress',quantity=qty,unit_cost='60.00')
            line['purchase_configuration']={'state':'ready','selections':[{'option_id':'size','value_id':'age10'},{'option_id':'service','value_id':'embroidery'}],
                'inputs':[{'option_id':'name','value':personal}]}
            body['lines'].append(line)
        p=await preview(db,OWNER,body)
        assert [r['total_cost'] for r in p['lines']]==['1200.00','720.00']
        assert {r['sku'] for r in p['lines']}=={'DRESS-10'}
        assert {r['inventory_identity']['salla_variant_id'] for r in p['lines']}=={'dress-age10'}
        assert len({r['inventory_identity']['configuration_key'] for r in p['lines']})==2
        await approve(db,OWNER,{**body,'preview_hash':p['preview_hash']})
        projected=(await projection(db,'owner'))['items']
        assert {(r['specifications']['الاسم'],r['physical']) for r in projected}=={('عبير',20),('فاطمة',12)}
        assert all(r['specifications']['المقاس']=='عمر 10' and r['operational_unit_cost']=='60.00' for r in projected)
    run(scenario)


@pytest.mark.parametrize('legacy_kind',['missing',None,''])
def test_legacy_component_kind_uses_explicit_inventory_flag(legacy_kind):
    async def scenario(db):
        await seed(db);await seed_component(db,kind=legacy_kind)
        if legacy_kind == 'missing':
            await db.mezan_cost_resources_v2.update_one({'id':'fabric'},{'$unset':{'kind':''}})
        row=(await context(db,OWNER))['components'][0]
        assert row['component_type']=='stock_component'
        body={'request_id':'legacy-stock-component','lines':[component_line()]}
        p=await preview(db,OWNER,body)
        await approve(db,OWNER,{**body,'preview_hash':p['preview_hash']})
        assert (await projection(db,'owner'))['items'][0]['physical']==2.5
    run(scenario)


def test_unknown_component_kind_is_not_assumed_stock():
    async def scenario(db):
        await seed(db);await seed_component(db,kind='unknown_kind')
        assert (await context(db,OWNER))['components']==[]
        with pytest.raises(HTTPException):
            await preview(db,OWNER,{'request_id':'unknown-component','lines':[component_line()]})
        assert await db[RECEIPTS].count_documents({})==0
    run(scenario)
