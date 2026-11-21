"""Isolated transactional acceptance for repeated stock entry and controls."""
import asyncio
from copy import deepcopy
import pytest
from fastapi import FastAPI, HTTPException
import httpx
from test_operational_balance_integration import run
from test_operational_balance_opening_inventory import seed, request, OWNER
from standalone_inventory_entry import context, preview, save, set_control, ENTRIES
from operational_opening_inventory import BATCHES, RECEIPTS
from operational_inventory_projection import projection
from inventory_write_gate import admitted
from operational_atomic import operational_owner


async def setup(db, enabled=True):
    await seed(db, enabled=False)
    await db.warehouse_locations_warehouses.update_one({'id':'warehouse'}, {'$set':{'inventory_scope':'standalone'}})
    await db.warehouse_locations.update_one({'id':'shelf'}, {'$set':{'display_code':'1-2-7'}})
    if enabled:
        await set_control(db, OWNER, {'revision':0,'paused':False})


async def command(db, suffix='one', quantity=5):
    body=request();body['request_id']='independent-'+suffix;body['lines'][0]['quantity']=quantity
    p=await preview(db, OWNER, body)
    return {**body,'preview_hash':p['preview_hash']}


def test_legacy_warehouse_without_scope_keeps_identity_and_supports_independent_entry():
    async def scenario(db):
        await setup(db)
        await db.warehouse_locations_warehouses.update_one(
            {'id': 'warehouse'}, {'$unset': {'inventory_scope': ''}})
        await db.warehouse_locations_warehouses.update_one(
            {'id': 'warehouse'}, {'$set': {'is_primary': True, 'code': 'TEST-WH001'}})
        await db.warehouse_locations.update_one({'id': 'shelf'}, {'$set': {
            'barcode_value': 'TEST-WH001-C1-001', 'row': 1, 'column': 1}})
        warehouse_before = await db.warehouse_locations_warehouses.find_one({'id': 'warehouse'})
        location_before = await db.warehouse_locations.find_one({'id': 'shelf'})
        control_before = await db.mz2_atomic_owners.find_one({'_id': 'owner'})
        financial_before = await db.mz2_financial_accounts.find({}).to_list(100)

        from operational_balance_routes import make_operational_balance_router
        app = FastAPI()
        async def principal(): return OWNER
        app.include_router(make_operational_balance_router(db, principal))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated') as client:
            base = '/operational-balances/standalone-inventory'
            response = await client.get(base + '/context')
            assert response.status_code == 200, response.text
            catalog = response.json()
            assert [location['id'] for location in catalog['locations']] == ['shelf']
            assert catalog['writer_disabled'] is False
            body = request(); body['request_id'] = 'legacy-warehouse'
            response = await client.post(base + '/preview', json=body)
            assert response.status_code == 200, response.text
            payload = {**body, 'preview_hash': response.json()['preview_hash']}
            response = await client.post(base + '/save', json=payload)
            assert response.status_code == 200, response.text
            saved = response.json()
            replay = await client.post(base + '/save', json=payload)
            assert replay.status_code == 200 and replay.json() == saved
        assert saved['status'] == 'approved'
        assert await db[ENTRIES].count_documents({}) == 1
        assert await db[RECEIPTS].count_documents({}) == 1
        assert await db.warehouse_locations_warehouses.find_one({'id': 'warehouse'}) == warehouse_before
        after = await db.warehouse_locations.find_one({'id': 'shelf'})
        for key in ('id', 'user_id', 'warehouse_id', 'name', 'code', 'display_code', 'barcode_value', 'row', 'column'):
            assert after.get(key) == location_before.get(key)
        assert after['occupancy']['total_quantity'] == 5
        control_after = await db.mz2_atomic_owners.find_one({'_id': 'owner'})
        assert {key: value for key, value in control_after.items() if key != 'revision'} == {
            key: value for key, value in control_before.items() if key != 'revision'}
        assert await db.mz2_financial_accounts.find({}).to_list(100) == financial_before
        stock = await projection(db, 'owner')
        assert sum(row['physical'] for row in stock['items']) == 5
        assert sum(row['available'] for row in stock['items']) == 0
        for name in ('accounting_journal_groups_v2', 'accounting_general_ledger_v2',
                     'mz2_inventory_cost_states', 'mezan_inventory_reservations_v2'):
            assert await db[name].count_documents({}) == 0
    run(scenario)


@pytest.mark.parametrize('scope', ['shared', None, '', False, 'unknown'])
def test_explicit_scope_never_inherits_legacy_compatibility(scope):
    async def scenario(db):
        await setup(db)
        await db.warehouse_locations_warehouses.update_one(
            {'id': 'warehouse'}, {'$set': {'inventory_scope': scope}})
        assert (await context(db, OWNER))['locations'] == []
        with pytest.raises(HTTPException):
            await command(db)
        assert await db[ENTRIES].count_documents({}) == 0
        assert await db[RECEIPTS].count_documents({}) == 0
    run(scenario)


@pytest.mark.parametrize('unavailable', [{'status': 'disabled'}, {'status': 'archived'}, {'deleted_at': '2026-01-01'}])
def test_legacy_warehouse_must_still_be_active_and_not_deleted(unavailable):
    async def scenario(db):
        await setup(db)
        await db.warehouse_locations_warehouses.update_one(
            {'id': 'warehouse'}, {'$unset': {'inventory_scope': ''}, '$set': unavailable})
        assert (await context(db, OWNER))['locations'] == []
        with pytest.raises(HTTPException):
            await command(db)
        assert await db[ENTRIES].count_documents({}) == 0
    run(scenario)


@pytest.mark.parametrize('occupancy', [
    {'items': [{'source_type': 'old-path', 'quantity': 2}], 'total_quantity': 2},
    {'items': [], 'total_quantity': 2},
    {'items': 'broken', 'total_quantity': 0},
])
def test_legacy_compatibility_cannot_relabel_or_overwrite_other_stock(occupancy):
    async def scenario(db):
        await setup(db)
        await db.warehouse_locations_warehouses.update_one(
            {'id': 'warehouse'}, {'$unset': {'inventory_scope': ''}})
        await db.warehouse_locations.update_one({'id': 'shelf'}, {'$set': {'occupancy': occupancy}})
        before = await db.warehouse_locations.find_one({'id': 'shelf'})
        with pytest.raises(HTTPException):
            await command(db)
        assert await db.warehouse_locations.find_one({'id': 'shelf'}) == before
        assert await db[ENTRIES].count_documents({}) == 0
        assert await db[RECEIPTS].count_documents({}) == 0
    run(scenario)


def test_legacy_warehouse_keeps_owner_authorization_and_default_closed_control():
    async def scenario(db):
        await setup(db, False)
        await db.warehouse_locations_warehouses.update_one(
            {'id': 'warehouse'}, {'$unset': {'inventory_scope': ''}})
        catalog = await context(db, OWNER)
        assert catalog['writer_disabled'] is True and len(catalog['locations']) == 1
        payload = await command(db)
        with pytest.raises(HTTPException) as exc:
            await save(db, OWNER, payload)
        assert exc.value.status_code == 423
        with pytest.raises(HTTPException) as exc:
            await context(db, {'id': 'staff'})
        assert exc.value.status_code == 403
        assert await db[ENTRIES].count_documents({}) == 0
        assert (await db.warehouse_locations.find_one({'id': 'shelf'}))['occupancy'] is None
    run(scenario)


@pytest.mark.parametrize('scope', ['shared', None])
def test_scope_changed_after_legacy_preview_cannot_commit_stock(scope):
    async def scenario(db):
        await setup(db)
        await db.warehouse_locations_warehouses.update_one(
            {'id': 'warehouse'}, {'$unset': {'inventory_scope': ''}})
        payload = await command(db)
        await db.warehouse_locations_warehouses.update_one(
            {'id': 'warehouse'}, {'$set': {'inventory_scope': scope}})
        with pytest.raises(HTTPException):
            await save(db, OWNER, payload)
        assert await db[ENTRIES].count_documents({}) == 0
        assert await db[RECEIPTS].count_documents({}) == 0
        assert (await db.warehouse_locations.find_one({'id': 'shelf'}))['occupancy'] is None
    run(scenario)


def test_repeated_immediate_saves_replay_and_unchanged_operating_writers():
    async def scenario(db):
        await setup(db)
        a=await command(db)
        first=await save(db,OWNER,a)
        assert await save(db,OWNER,a)==first
        b=await command(db,'two',3)
        await save(db,OWNER,b)
        assert await db[ENTRIES].count_documents({})==2
        assert await db[RECEIPTS].count_documents({})==2
        assert await db[BATCHES].count_documents({})==0
        assert (await db.warehouse_locations.find_one({'id':'shelf'}))['occupancy']['total_quantity']==8
        c=await context(db,OWNER)
        assert len(c['entries'])==2 and c['locations'][0]['name']=='1-2-7'
        stock=await projection(db,'owner')
        assert sum(r['physical'] for r in stock['items'])==8
        assert sum(r['available'] for r in stock['items'])==0
        assert {r['operational_unit_cost'] for r in stock['items']}=={'12.50'}
        assert {r['location_code'] for r in stock['items']}=={'1-2-7'}
        control=await db.mz2_atomic_owners.find_one({'_id':'owner'})
        assert control.get('physical_inventory_writes_paused') is not False
        assert control.get('writes_paused') is not False
        for name in ('accounting_journal_groups_v2','accounting_general_ledger_v2','mz2_inventory_cost_states','mezan_inventory_reservations_v2','mezan_fulfillment_events_v2','operational_balance_states_v1'):
            assert await db[name].count_documents({})==0
    run(scenario)


def test_default_closed_control_is_owner_only_and_does_not_open_old_writer():
    async def scenario(db):
        await setup(db,False)
        a=await command(db)
        assert (await context(db,OWNER))['writer_disabled'] is True
        with pytest.raises(HTTPException) as exc: await save(db,OWNER,a)
        assert exc.value.status_code==423
        with pytest.raises(HTTPException): await set_control(db,{'id':'staff'},{'revision':0,'paused':False})
        await set_control(db,OWNER,{'revision':0,'paused':False})
        with pytest.raises(HTTPException): await set_control(db,OWNER,{'revision':0,'paused':False})
        async def callback(): raise AssertionError('old writer opened')
        with pytest.raises(HTTPException) as exc: await admitted(db,'owner','old',callback)
        assert exc.value.status_code==423
        await set_control(db,OWNER,{'revision':1,'paused':True})
        with pytest.raises(HTTPException): await save(db,OWNER,a)
        assert await db[RECEIPTS].count_documents({})==0
    run(scenario)


@pytest.mark.parametrize('invalid',['shared','foreign','capacity','conflict','stale','staff','mixed'])
def test_rejected_entries_cannot_add_quantities(invalid):
    async def scenario(db):
        await setup(db)
        if invalid=='shared':
            await db.warehouse_locations_warehouses.update_one({'id':'warehouse'},{'$set':{'inventory_scope':'shared'}})
        elif invalid=='foreign':
            await db.warehouse_locations.update_one({'id':'shelf'},{'$set':{'user_id':'other'}})
        elif invalid=='mixed':
            await db.warehouse_locations.update_one({'id':'shelf'},{'$set':{'occupancy':{'items':[{'quantity':2,'product_id':'product'}],'total_quantity':2}}})
        if invalid in {'shared','foreign','capacity','mixed'}:
            with pytest.raises(HTTPException): await command(db,quantity=21 if invalid=='capacity' else 5)
            assert await db[ENTRIES].count_documents({})==0
            return
        a=await command(db)
        if invalid=='conflict':
            await save(db,OWNER,a);a['lines'][0]['quantity']=6
        elif invalid=='stale':
            await db.warehouse_locations.update_one({'id':'shelf'},{'$set':{'max_items':3}})
        with pytest.raises(HTTPException): await save(db,{'id':'staff'} if invalid=='staff' else OWNER,a)
        assert await db[ENTRIES].count_documents({})==(1 if invalid=='conflict' else 0)
    run(scenario)


@pytest.mark.parametrize('legacy', [False, True])
def test_concurrent_same_command_once_and_capacity_includes_previous_entries(legacy):
    async def scenario(db):
        await setup(db)
        if legacy:
            await db.warehouse_locations_warehouses.update_one(
                {'id': 'warehouse'}, {'$unset': {'inventory_scope': ''}})
        a=await command(db,quantity=12)
        outcomes=await asyncio.gather(save(db,OWNER,a),save(db,OWNER,a),return_exceptions=True)
        assert any(isinstance(r,dict) for r in outcomes)
        assert await db[ENTRIES].count_documents({})==1
        assert (await save(db,OWNER,a))['status']=='approved'
        with pytest.raises(HTTPException): await command(db,'over-capacity',9)
        await save(db,OWNER,await command(db,'exact-capacity',8))
        assert (await db.warehouse_locations.find_one({'id':'shelf'}))['occupancy']['total_quantity']==20
    run(scenario)


def test_separate_variants_and_fractional_components():
    async def scenario(db):
        await setup(db)
        await db.warehouse_locations.update_one({'id':'shelf'},{'$set':{'max_items':None}})
        await db.mezan_products_v2.update_one({'mezan_product_id':'product'},{'$set':{'options':[
            {'id':'color','name':'اللون','type':'select','required':True,'values':[{'id':'silver','name':'فضي'},{'id':'gold','name':'ذهبي'}]}]}})
        for color,quantity in [('silver',50),('gold',30)]:
            b=request();b['request_id']='color-'+color;b['lines'][0]['quantity']=quantity
            b['lines'][0]['purchase_configuration']['selections']=[{'option_id':'color','value_id':color}]
            p=await preview(db,OWNER,b);await save(db,OWNER,{**b,'preview_hash':p['preview_hash']})
        await db.mezan_cost_resources_v2.insert_one({'id':'cloth','user_id':'owner','name':'قماش','track_inventory':True,'unit':'m','status':'active'})
        b=request();b['request_id']='fractional-component';b['lines'][0].update(kind='component',item_id='cloth',quantity=2.5,purchase_configuration={'state':'raw','selections':[],'inputs':[]})
        p=await preview(db,OWNER,b);await save(db,OWNER,{**b,'preview_hash':p['preview_hash']})
        stock=await projection(db,'owner');assert len(stock['items'])==3
        assert sorted(i['physical'] for i in stock['items'])==[2.5,30,50]
        assert len({i['configuration_key'] for i in stock['items']})==3
    run(scenario)


def test_strict_profile_rolls_back_if_financial_write_is_attempted():
    async def scenario(db):
        await setup(db)
        async def work(scoped):
            try: await scoped.mz2_financial_accounts.insert_one({'id':'illegal','user_id':'owner'})
            except HTTPException: pass
        with pytest.raises(HTTPException):
            await admitted(db,'owner','standalone',lambda:operational_owner(db,'owner',work,profile='standalone_inventory'),standalone=True)
        assert await db.mz2_financial_accounts.count_documents({'id':'illegal'})==0
    run(scenario)


def test_new_warehouse_locations_use_short_labels_but_scan_codes_remain_unique():
    from warehouse_location_v2_routes import make_warehouse_location_v2_router
    from warehouse_room_routes import make_warehouse_room_router
    async def scenario(db):
        app=FastAPI()
        async def principal():return {'id':'owner','role':'owner'}
        app.include_router(make_warehouse_location_v2_router(db,principal))
        app.include_router(make_warehouse_room_router(db,principal))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
            w=await c.post('/warehouse-locations-v2/warehouses',json={'name':'New','district':'حي','street':'شارع','inventory_scope':'standalone','location_numbering':'cabinet-row-slot'})
            assert w.status_code==201,w.text
            ids=[]
            for i in range(2):
                sec=await c.post('/warehouse-locations-v2/sections',json={'warehouse_id':w.json()['id'],'name':str(i),'capabilities':['cabinets']})
                cab=await c.post('/warehouse-locations-v2/sections/'+sec.json()['id']+'/cabinets',json={'length':2,'width':12})
                assert cab.status_code==201,cab.text
                assert cab.json()['cabinet']['cabinet_number']==i+1
                ids.append(cab.json()['cabinet']['id'])
            locs=await db.warehouse_locations.find({'cabinet_id':{'$in':ids}}).to_list(100)
            assert len({l['display_code'] for l in locs})==48
            target=next(l for l in locs if l['display_code']=='1-2-7')
            assert target['row']==2 and target['column']==7
            scan=await c.get('/warehouse-locations-v2/locations/scan/'+target['barcode_value'])
            assert scan.status_code==200 and scan.json()['location']['id']==target['id']
            assert target['barcode_value']!=target['display_code']
    run(scenario)


@pytest.mark.parametrize('legacy', [False, True])
def test_event_failure_rolls_back_entry_receipt_and_occupancy(monkeypatch, legacy):
    import standalone_inventory_entry as service
    original=service.validate_insert
    monkeypatch.setattr(service,'validate_insert',lambda name,doc:name!='warehouse_location_events' and original(name,doc))
    async def scenario(db):
        await setup(db)
        if legacy:
            await db.warehouse_locations_warehouses.update_one(
                {'id': 'warehouse'}, {'$unset': {'inventory_scope': ''}})
        with pytest.raises(HTTPException): await save(db,OWNER,await command(db))
        assert await db[ENTRIES].count_documents({})==0
        assert await db[RECEIPTS].count_documents({})==0
        assert (await db.warehouse_locations.find_one({'id':'shelf'}))['occupancy'] is None
    run(scenario)


def test_standalone_admission_cannot_enter_existing_fulfillment_profile():
    async def scenario(db):
        await setup(db)
        async def callback(scoped): raise AssertionError('entered existing fulfillment')
        with pytest.raises(HTTPException):
            await admitted(db,'owner','standalone',lambda:operational_owner(db,'owner',callback,profile='fulfillment'),standalone=True)
    run(scenario)


def test_configured_catalog_ignores_unused_old_variant_labels_and_http_control_is_strict():
    from operational_balance_routes import make_operational_balance_router
    async def scenario(db):
        await setup(db)
        await db.mezan_products_v2.update_one({'mezan_product_id':'product'}, {'$set':{'variants':[{'id':'variant-no-label'}],'variants_count':1}})
        app=FastAPI()
        async def principal():return OWNER
        app.include_router(make_operational_balance_router(db,principal))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
            configured=await c.get('/operational-balances/inventory-catalog?configured=true')
            assert configured.status_code==200,configured.text
            assert configured.json()['products'][0]['id']=='product'
            legacy=await c.get('/operational-balances/inventory-catalog')
            assert legacy.status_code!=200
            invalid=await c.post('/operational-balances/standalone-inventory/control',json={'revision':1,'paused':'false'})
            assert invalid.status_code==422
        # A broken variant is not silently admitted as a stocked ready product.
        with pytest.raises(HTTPException): await command(db)
    run(scenario)
