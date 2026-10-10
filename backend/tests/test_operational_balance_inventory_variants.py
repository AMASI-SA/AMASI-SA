"""Variant purchasing and accepted returns through isolated Mongo and HTTP."""
import asyncio
from copy import deepcopy
import pytest
from fastapi import HTTPException
from test_operational_balance_inventory import seed, command
from test_operational_balance_integration import run, NOW
from test_operational_balance_app_permissions import client, grant, WRITE, READ
from test_operational_balance_supplier_adjustments import adjustment
from operational_balance_inventory import catalog, save_purchase, inventory_view
from operational_supplier_adjustments import save_adjustment
from operational_balance_store import read
from operational_balance_service import report, refresh


async def seed_variants(db):
    await seed(db)
    await db.mezan_products_v2.update_one({'mezan_product_id':'product'}, {'$set':{
        'variants_count':2, 'variants':[
            {'id':'silver','display_name':'اللون: فضي','sku':'SILVER'},
            {'id':'gold','display_name':'اللون: ذهبي','sku':'GOLD'}]}})


def variant_command(**patch):
    return command(lines=[
        {'kind':'product','item_id':'product','variant_id':'silver','quantity':10,'unit_price':'10','tax':'15'},
        {'kind':'product','item_id':'product','variant_id':'gold','quantity':20,'unit_price':'20','tax':'60'}],**patch)


def test_silver10_gold20_concurrent_purchase_return_discount_and_readback():
    async def scenario(db):
        await seed_variants(db); await grant(db,[WRITE,READ])
        original=await db.mezan_products_v2.find_one({})
        async with client(db) as c:
            base='/api/operational-balances'
            cat=(await c.get(base+'/inventory-catalog')).json()['items']
            assert {(r.get('variant_id'),r['sku']) for r in cat if r['kind']=='product'}=={('silver','SILVER'),('gold','GOLD')}
            replies=await asyncio.gather(*[c.post(base+'/inventory-purchases',json=variant_command()) for _ in range(3)])
            assert all(r.status_code==200 for r in replies),[r.text for r in replies]
            invoice=replies[0].json()
            assert invoice['gross']=='575.00'
            p=adjustment(invoice);p['lines']=[{'kind':'product','item_id':'product','variant_id':'silver','quantity':2}]
            returned=await c.post(base+'/supplier-adjustments',json=p)
            assert returned.status_code==200,returned.text
            assert (await c.post(base+'/supplier-adjustments',json=p)).json()==returned.json()
            rows={l['variant_id']:l for l in returned.json()['lines']}
            assert (rows['silver']['remaining_quantity'],rows['gold']['remaining_quantity'])==(8,20)
            assert (rows['silver']['remaining_gross'],rows['gold']['remaining_gross'])==('92.00','460.00')
            view=(await c.get(base+'/inventory-purchases')).json()
            assert len(view['items'])==1
            assert {(l['variant_id'],l['quantity'],l['remaining_quantity']) for l in view['stock']}=={('silver',10,8),('gold',20,20)}
        # A whole-invoice credit must retain each variant identity as well.
        p.update(request_id='variant-discount',reference='DISCOUNT',kind='discount',amount='55.20',lines=[])
        credited=await save_adjustment(db,'owner','staff',p,clock=NOW)
        rows={l['variant_id']:l for l in credited['lines']}
        assert (rows['silver']['remaining_gross'],rows['gold']['remaining_gross'])==('82.80','414.00')
        await refresh(db,'owner',clock=NOW)
        state=await read(db,'owner')
        assert len(state['inventory_purchases'])==1 and len(state['supplier_adjustments'])==2
        assert not state['movements']
        assert report(state)['summary']['actual_liquidity']=='1000.00'
        assert report(state)['summary']['payable']=='496.80'
        assert await db.mezan_products_v2.find_one({})==original
    run(scenario)


@pytest.mark.parametrize('case',['missing','unknown','duplicate','component','foreign','changed_retry'])
def test_invalid_variant_purchase_is_atomic(case):
    async def scenario(db):
        await seed_variants(db)
        p=variant_command()
        if case=='missing': p['lines'][0].pop('variant_id')
        if case=='unknown': p['lines'][0]['variant_id']='untrusted'
        if case=='duplicate': p['lines'][1]['variant_id']='silver'
        if case=='component': p['lines'][0].update(kind='component',item_id='component')
        if case=='foreign':
            await db.mezan_products_v2.insert_one({'user_id':'other','mezan_product_id':'other-product','name':'Other','variants':[{'id':'foreign','name':'Other'}]})
            p['lines'][0]['variant_id']='foreign'
        if case=='changed_retry':
            await save_purchase(db,'owner','staff',p,clock=NOW)
            p=deepcopy(p);p['lines'][0]['quantity']=11
        before=await read(db,'owner')
        with pytest.raises(HTTPException): await save_purchase(db,'owner','staff',p,clock=NOW)
        assert await read(db,'owner')==before
    run(scenario)


@pytest.mark.parametrize('case',['missing','unknown','duplicate','excess'])
def test_variant_return_cannot_consume_another_color(case):
    async def scenario(db):
        await seed_variants(db)
        invoice=await save_purchase(db,'owner','staff',variant_command(),clock=NOW)
        p=adjustment(invoice);p['lines']=[{'kind':'product','item_id':'product','variant_id':'silver','quantity':2}]
        if case=='missing':p['lines'][0].pop('variant_id')
        if case=='unknown':p['lines'][0]['variant_id']='other'
        if case=='duplicate':p['lines']*=2
        if case=='excess':p['lines'][0]['quantity']=11
        before=await read(db,'owner')
        with pytest.raises(HTTPException):await save_adjustment(db,'owner','staff',p,clock=NOW)
        assert await read(db,'owner')==before
    run(scenario)


@pytest.mark.parametrize('variants,count', [([],2),([{'id':'silver','name':'Silver'}],2),([{'id':'silver','name':'Silver'},{'id':'silver','name':'Gold'}],2),([{'name':'Silver'}],1),([{'id':'silver'}],1),({},0),('',0),(False,0),([],False),([], '')])
def test_incomplete_or_ambiguous_mz2_variants_fail_closed(variants,count):
    async def scenario(db):
        await seed(db)
        await db.mezan_products_v2.update_one({'mezan_product_id':'product'},{'$set':{'variants':variants,'variants_count':count}})
        with pytest.raises(HTTPException):await catalog(db,'owner')
    run(scenario)


def test_old_unallocated_stock_stays_separate_after_product_gets_variants():
    async def scenario(db):
        await seed(db);await save_purchase(db,'owner','staff',command(),clock=NOW)
        await db.mezan_products_v2.update_one({'mezan_product_id':'product'},{'$set':{'variants':[{'id':'silver','name':'فضي'},{'id':'gold','name':'ذهبي'}]}})
        await save_purchase(db,'owner','staff',variant_command(request_id='second-invoice',invoice_number='SECOND'),clock=NOW)
        stock=inventory_view(await read(db,'owner'))['stock']
        assert {(l.get('variant_id'),l['quantity']) for l in stock if l['kind']=='product'}=={(None,3),('silver',10),('gold',20)}
    run(scenario)


def test_pre_variant_saved_requests_replay_through_updated_api():
    async def scenario(db):
        await seed(db);await grant(db,[WRITE])
        old=command()
        invoice=await save_purchase(db,'owner','staff',old,clock=NOW)
        credit=adjustment(invoice)
        returned=await save_adjustment(db,'owner','staff',credit,clock=NOW)
        async with client(db) as c:
            r=await c.post('/api/operational-balances/inventory-purchases',json=old)
            assert r.status_code==200,r.text
            assert r.json()==invoice
            r=await c.post('/api/operational-balances/supplier-adjustments',json=credit)
            assert r.status_code==200,r.text
            assert r.json()==returned
        state=await read(db,'owner')
        assert len(state['inventory_purchases'])==1 and len(state['supplier_adjustments'])==1
    run(scenario)
