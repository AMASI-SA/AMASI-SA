"""Read-only projection against the real isolated eligibility replica set."""
from copy import deepcopy
from decimal import Decimal
import pytest
from test_operational_physical_stock_prerequisites import run
from test_stock_eligibility_acceptance import contract_stock, contract_adoption, read, available, reserve_product, consume
from operational_inventory_projection import projection

async def catalog(db):
    await db.mezan_products_v2.insert_one({'user_id':'owner','mezan_product_id':'product','name':'Product','sku':'SKU'})
    await db.mezan_cost_resources_v2.insert_one({'user_id':'owner','id':'component','name':'Component','track_inventory':True,'kind':'stock_component'})
    await db.warehouse_locations_warehouses.insert_one({'user_id':'owner','id':'wh','name':'Warehouse'})

async def snapshot(db):
    return {name:await db[name].find({}).to_list(30000) for name in sorted(await db.list_collection_names())}

@pytest.mark.parametrize('kind',['product','component'])
@pytest.mark.parametrize('state',['posted','pending','rejected','damaged','quarantine','pending_inspection','financial_pending'])
def test_projection_and_consumer_agree_with_zero_read_writes(kind,state):
    async def scenario(db):
        await catalog(db);await contract_stock(db,kind)
        if state in {'pending','rejected'}:await db.mezan_inventory_receipts_v2.update_one({'id':'lot'},{'$set':{'status':state}})
        elif state=='financial_pending':await db.mezan_inventory_receipts_v2.update_one({'id':'lot'},{'$set':{'valuation_status':'PENDING_FINANCIAL_VALUATION'}})
        elif state!='posted':await db.warehouse_locations.update_one({'id':'loc'},{'$set':{'occupancy.items.0.condition':state}})
        before=await snapshot(db)
        row=(await projection(db,'owner'))['items'][0]
        assert row['physical']==10
        assert row['available']==available(await read(db,kind))==(10 if state=='posted' else 0)
        assert row['held']==(0 if state=='posted' else 10)
        assert await snapshot(db)==before
    run(scenario)

@pytest.mark.parametrize('kind',['product','component'])
@pytest.mark.parametrize('contract',['opening','manufactured','missing_configuration','mismatched_configuration'])
def test_projection_preserves_original_identity_contracts(kind,contract):
    async def scenario(db):
        await catalog(db)
        if contract=='opening': await contract_adoption(db,kind)
        else: await contract_stock(db,kind,source='stock_preparation_order' if contract=='manufactured' else 'purchase_invoice')
        if contract in {'missing_configuration','mismatched_configuration'}:
            await db.warehouse_locations.update_one({'id':'loc'},{'$unset':{'occupancy.items.0.configuration_key':''}})
        if contract=='mismatched_configuration':
            await db.warehouse_locations.update_one({'id':'loc'},{'$set':{'occupancy.items.0.specifications':{'color':'silver'}}})
        before=await snapshot(db)
        row=(await projection(db,'owner'))['items'][0]
        assert row['physical']==10 and row['available']==available(await read(db,kind))==(0 if contract=='mismatched_configuration' else 10)
        assert await snapshot(db)==before
    run(scenario)


def test_projection_reservations_pending_then_consumption_no_double_count():
    async def scenario(db):
        await catalog(db);await contract_stock(db)
        await reserve_product(db)
        first=(await projection(db,'owner'))['items'][0]
        assert (first['physical'],first['reserved'],first['held'],first['available'])==(10,3,0,7)
        await db.mezan_inventory_receipts_v2.update_one({'id':'lot'},{'$set':{'status':'pending'}})
        blocked=(await projection(db,'owner'))['items'][0]
        assert (blocked['physical'],blocked['reserved'],blocked['held'],blocked['available'])==(10,3,7,0)
        from fastapi import HTTPException
        with pytest.raises(HTTPException):await consume(db,'product')
        await db.mezan_inventory_receipts_v2.update_one({'id':'lot'},{'$set':{'status':'posted'}})
        import asyncio
        await asyncio.gather(consume(db,'product'),consume(db,'product'))
        final=(await projection(db,'owner'))['items'][0]
        assert (final['physical'],final['reserved'],final['held'],final['available'])==(7,0,0,7)
    run(scenario)


def test_110_is_read_only_simulation_with_107_financially_held():
    async def scenario(db):
        await catalog(db);await contract_stock(db)
        loc=await db.warehouse_locations.find_one({'id':'loc'});old=loc['occupancy']['items'][0]
        from product_inventory_rules import build_inventory_configuration_key
        gold_fields=dict(preparation_state='requires_preparation',specifications={'color':'gold'})
        silver_fields=dict(preparation_state='ready_complete',specifications={'color':'silver','name':'Abeer'})
        for fields in (gold_fields,silver_fields):
            fields['configuration_key']=build_inventory_configuration_key(sku='SKU',**fields)
        items=[dict(old,quantity=3),dict(old,receipt_id='gold',lot_id='gold',quantity=8,**gold_fields),dict(old,receipt_id='silver',lot_id='silver',quantity=99,**silver_fields)]
        await db.warehouse_locations.update_one({'id':'loc'},{'$set':{'occupancy':{'items':items,'total_quantity':110}}})
        proof=await db.mezan_inventory_receipts_v2.find_one({'id':'lot'});proof.pop('_id')
        proofs=[]
        for receipt,quantity,fields in [('gold',8,gold_fields),('silver',99,silver_fields)]:
            current=dict(proof);current.update(fields)
            current.update(id=receipt,receipt_id=receipt,lot_id=receipt,quantity=quantity,valuation_status='PENDING_FINANCIAL_VALUATION')
            proofs.append(current)
        await db.mezan_inventory_receipts_v2.insert_many(proofs)
        before=await snapshot(db);result=await projection(db,'owner')
        assert {(r['preparation_state'],r['specifications']['color'],r['physical']) for r in result['items']}=={('ready_complete','gold',3),('requires_preparation','gold',8),('ready_complete','silver',99)}
        assert sum(r['physical'] for r in result['items'])==110
        assert sum(r['available'] for r in result['items'])==3
        assert sum(r['held'] for r in result['items'])==107
        assert await snapshot(db)==before
    run(scenario)


def test_evidence_query_ignores_unrelated_history_but_preserves_current_veto():
    async def scenario(db):
        from fulfillment_v2_routes import _load_inventory_evidence
        await contract_adoption(db)
        await db.mezan_inventory_receipts_v2.insert_many([{'user_id':'owner','id':f'history-{i}','location_id':'loc','status':'posted'} for i in range(20001)])
        loc=await db.warehouse_locations.find_one({'id':'loc'})
        proof=await _load_inventory_evidence(db,'owner',[loc])
        assert {r['id'] for r in proof[('loc','lot')]}=={'lot','opening'}
        await db.mezan_inventory_receipts_v2.update_one({'id':'lot'},{'$set':{'status':'rejected'}})
        assert available(await read(db,'product'))==0
    run(scenario)


def test_evidence_bound_is_retained_for_relevant_documents():
    async def scenario(db):
        from fulfillment_v2_routes import _load_inventory_evidence
        from fastapi import HTTPException
        await db.mezan_inventory_receipts_v2.insert_many([{'user_id':'owner','id':'same','location_id':'loc'} for _ in range(20001)])
        with pytest.raises(HTTPException) as error:
            await _load_inventory_evidence(db,'owner',[{'id':'loc','occupancy':{'items':[{'receipt_id':'same'}]}}])
        assert error.value.detail['code']=='inventory_evidence_limit_reconciliation_required'
    run(scenario)


def test_adoption_witness_is_deduplicated_across_reference_chunks():
    async def scenario(db):
        from fulfillment_v2_routes import _load_inventory_evidence
        refs=[f'lot-{i}' for i in range(501)]
        await db.mezan_inventory_receipts_v2.insert_one({'id':'opening','user_id':'owner','location_id':'loc','source_type':'opening_inventory','adopted_receipt_ids':refs})
        proof=await _load_inventory_evidence(db,'owner',[{'id':'loc','occupancy':{'items':[{'receipt_id':r} for r in refs]}}])
        assert all(len(proof[('loc',r)])==1 for r in refs)
    run(scenario)


def test_projection_snapshot_does_not_mix_receipt_status_commits(monkeypatch):
    async def scenario(db):
        import asyncio
        import operational_inventory_projection as module
        await catalog(db);await contract_stock(db)
        read_locations,release=asyncio.Event(),asyncio.Event()
        original=module.records
        async def paused(database,owner,collection):
            rows=await original(database,owner,collection)
            if collection=='warehouse_locations':
                read_locations.set();await release.wait()
            return rows
        monkeypatch.setattr(module,'records',paused)
        task=asyncio.create_task(projection(db,'owner'))
        await asyncio.wait_for(read_locations.wait(),10)
        try:
            await db.mezan_inventory_receipts_v2.update_one({'id':'lot'},{'$set':{'status':'pending'}})
        finally:release.set()
        prior=await task
        assert prior['items'][0]['available']==10
        current=await projection(db,'owner')
        assert current['items'][0]['physical']==10 and current['items'][0]['available']==0
    run(scenario)


@pytest.mark.parametrize('field,value',[('purpose','returns'),('purpose','damaged'),('condition','quarantine'),('inspection_status','pending_inspection'),('valuation_status','PENDING_FINANCIAL_VALUATION'),('receipt_confirmed',False)])
@pytest.mark.parametrize('kind',['product','component'])
def test_location_evidence_reaches_reader_and_final_consumer(kind,field,value):
    async def scenario(db):
        await catalog(db);await contract_stock(db,kind)
        if kind=='product':await reserve_product(db)
        await db.warehouse_locations.update_one({'id':'loc'},{'$set':{field:value}})
        before=await snapshot(db)
        row=(await projection(db,'owner'))['items'][0]
        assert row['physical']==10 and row['available']==available(await read(db,kind))==0
        from fastapi import HTTPException
        with pytest.raises(HTTPException):await consume(db,kind)
        assert (await db.warehouse_locations.find_one({'id':'loc'}))['occupancy']['total_quantity']==10
        # Reads do not mutate; rejected consumption leaves inventory/cost sources intact.
        after=await snapshot(db)
        for name in ('warehouse_locations','mz2_inventory_cost_states','mezan_inventory_receipts_v2'):
            assert after.get(name)==before.get(name)
    run(scenario)


def test_pending_valuation_is_not_displayed_when_other_condition_also_blocks():
    async def scenario(db):
        await catalog(db);await contract_stock(db)
        await db.warehouse_locations.update_one({'id':'loc'},{'$set':{'purpose':'damaged'}})
        await db.mezan_inventory_receipts_v2.update_one({'id':'lot'},{'$set':{'valuation_status':'PENDING_FINANCIAL_VALUATION'}})
        await db.mz2_inventory_cost_states.insert_one({'user_id':'owner','inventory_identity':{'item_type':'product','product_id':'product'},'authoritative':True,'cost_policy_version':'moving-weighted-average-v1','average_cost':'15'})
        before=await snapshot(db)
        row=(await projection(db,'owner'))['items'][0]
        assert row['physical']==10 and row['available']==0
        assert row['unit_cost'] is None and row['inventory_value'] is None
        assert await snapshot(db)==before
    from test_operational_balance_integration import run as run_with_existing_cost_fixture
    run_with_existing_cost_fixture(scenario)
