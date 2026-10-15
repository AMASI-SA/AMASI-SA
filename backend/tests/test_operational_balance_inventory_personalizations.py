"""Name allocations are immutable purchase annotations, never additional stock/debt."""
import asyncio
from copy import deepcopy
import pytest
from fastapi import HTTPException
from test_operational_balance_inventory import seed as base_seed, command
from test_operational_balance_inventory_variants import seed_variants as base_seed_variants
from test_operational_balance_integration import run, NOW
from test_operational_balance_app_permissions import client, grant, WRITE, READ
from test_operational_balance_supplier_adjustments import adjustment
from operational_balance_inventory import save_purchase, inventory_view
from operational_supplier_adjustments import save_adjustment
from operational_balance_store import read
from operational_balance_service import report


async def name_option(db):
    await db.mezan_products_v2.update_many({}, {'$set':{'options':[{'id':'name','name':'الاسم','type':'text','required':True}], 'options_count':1}})


async def seed(db):
    await base_seed(db); await name_option(db)


async def seed_variants(db):
    await base_seed_variants(db); await name_option(db)


def personalized():
    return command(lines=[
        {'kind':'product','item_id':'product','variant_id':'gold','quantity':10,'unit_price':'10','tax':'15',
         'personalizations':[{'name':'عبير','quantity':4}]},
        {'kind':'product','item_id':'product','variant_id':'silver','quantity':5,'unit_price':'10','tax':'7.50',
         'personalizations':[{'name':'عبير','quantity':1},{'name':'روان','quantity':3}]}])


def test_exact_user_example_concurrent_retry_readback_money_and_stock_parity():
    async def scenario(db):
        await seed_variants(db); await grant(db,[WRITE,READ])
        original=await db.mezan_products_v2.find_one({})
        async with client(db) as c:
            base='/api/operational-balances'
            results=await asyncio.gather(*[c.post(base+'/inventory-purchases',json=personalized()) for _ in range(4)])
            assert all(r.status_code==200 for r in results),[r.text for r in results]
            invoice=results[0].json(); assert all(r.json()==invoice for r in results)
            assert (invoice['net'],invoice['tax'],invoice['gross'])==('150.00','22.50','172.50')
            rows={l['variant_id']:l for l in invoice['lines']}
            assert rows['gold']['personalizations']==[{'name':'عبير','quantity':4}]
            assert rows['silver']['personalizations']==[{'name':'عبير','quantity':1},{'name':'روان','quantity':3}]
            assert (rows['gold']['unallocated_quantity'],rows['silver']['unallocated_quantity'])==(6,1)
            view=(await c.get(base+'/inventory-purchases')).json()
            assert view['items']==[invoice]
            assert sum(l['quantity'] for l in view['stock'])==15
            changed=personalized();changed['lines'][0]['personalizations'][0]['quantity']=5
            assert (await c.post(base+'/inventory-purchases',json=changed)).status_code==409
        state=await read(db,'owner')
        assert len(state['inventory_purchases'])==1 and not state['movements']
        assert report(state)['summary']['actual_liquidity']=='1000.00'
        assert report(state)['summary']['payable']=='172.50'
        assert await db.mezan_products_v2.find_one({})==original
        assert await db.mezan_supplier_invoices_v2.count_documents({})==0
    run(scenario)


@pytest.mark.parametrize('allocation',[
    [{'name':'عبير','quantity':11}], [{'name':'عبير','quantity':1},{'name':' عبير ','quantity':1}],
    [{'name':'','quantity':1}], [{'name':'  ','quantity':1}], [{'name':'x'*101,'quantity':1}],
    [{'name':'عبير','quantity':True}], [{'name':'عبير','quantity':1.5}],
    [{'name':'عبير','quantity':0}], [{'name':'عبير','quantity':-1}],
    [{'name':'ABeer','quantity':1},{'name':'abeer','quantity':1}],
    [{'name':'x\u0000y','quantity':1}],
    [{'name':'a  b','quantity':1},{'name':'a b','quantity':1}],
    [{'name':'عبير','quantity':'1'}], [{'name':42,'quantity':1}],
    [{'name':'عبير','quantity':1,'variant_id':'silver'}], None,
    [{'name':str(i),'quantity':1} for i in range(101)],
])
def test_invalid_allocations_reject_atomically_at_service_and_http(allocation):
    async def scenario(db):
        await seed_variants(db); await grant(db,[WRITE])
        p=personalized();p['lines'][0]['personalizations']=allocation
        before=await read(db,'owner')
        with pytest.raises(HTTPException): await save_purchase(db,'owner','staff',p,clock=NOW)
        assert await read(db,'owner')==before
        p['request_id']='http-validation-separate'
        async with client(db) as c:
            r=await c.post('/api/operational-balances/inventory-purchases',json=p)
            assert r.status_code==422,r.text
        assert await read(db,'owner')==before
    run(scenario)


def test_component_rejects_names_and_names_trim_on_product():
    async def scenario(db):
        await seed(db)
        p=command();p['lines'][1]['personalizations']=[{'name':'عبير','quantity':1}]
        before=await read(db,'owner')
        with pytest.raises(HTTPException):await save_purchase(db,'owner','staff',p,clock=NOW)
        assert await read(db,'owner')==before
        p=command(request_id='product-trim-new');p['lines'][0]['personalizations']=[{'name':' عبير ','quantity':1}]
        invoice=await save_purchase(db,'owner','staff',p,clock=NOW)
        assert invoice['lines'][0]['personalizations']==[{'name':'عبير','quantity':1}]
    run(scenario)


def test_return_keeps_original_distribution_without_guessing_returned_names():
    async def scenario(db):
        await seed_variants(db)
        saved=await save_purchase(db,'owner','staff',personalized(),clock=NOW)
        p=adjustment(saved);p['lines']=[{'kind':'product','item_id':'product','variant_id':'gold','quantity':2}]
        await save_adjustment(db,'owner','staff',p,clock=NOW)
        row=inventory_view(await read(db,'owner'))['items'][0]['lines'][0]
        assert row['quantity']==10 and row['remaining_quantity']==8
        assert row['personalizations']==[{'name':'عبير','quantity':4}] and row['unallocated_quantity']==6
    run(scenario)


def test_omitted_and_empty_allocations_keep_original_fingerprint_and_legacy_readback():
    async def scenario(db):
        await seed(db);await grant(db,[WRITE])
        old=command(); saved=await save_purchase(db,'owner','staff',old,clock=NOW)
        empty=deepcopy(old)
        for line in empty['lines']:line['personalizations']=[]
        async with client(db) as c:
            for payload in [old,empty]:
                response=await c.post('/api/operational-balances/inventory-purchases',json=payload)
                assert response.status_code==200,response.text
                assert response.json()==saved
        state=await read(db,'owner')
        for line in state['inventory_purchases'][0]['lines']:
            line.pop('personalizations');line.pop('unallocated_quantity')
        rows=inventory_view(state)['items'][0]['lines']
        assert all(l['personalizations']==[] and l['unallocated_quantity']==l['quantity'] for l in rows)
    run(scenario)
