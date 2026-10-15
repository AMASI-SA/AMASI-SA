"""Inventory purchases through real isolated Mongo and authenticated routes."""
import asyncio
from copy import deepcopy
import pytest
from fastapi import HTTPException
from test_operational_balance_integration import run, started, NOW, movement
from test_operational_balance_app_permissions import client, grant, WRITE, READ
from operational_balance_store import read, digest
from operational_balance_service import report, create_movement, refresh
from operational_balance_inventory import catalog, save_purchase, purchase_view, inventory_view


async def seed(db):
    await started(db, supplier='0')
    await db.mezan_products_v2.insert_one({'user_id':'owner','mezan_product_id':'product','name':'منتج مخزون','main_image':'https://example.invalid/product.png','status':'sale','archived':False})
    await db.mezan_cost_resources_v2.insert_one({'user_id':'owner','id':'component','name':'مكون','track_inventory':True,'unit':'piece','status':'active'})
    await db.products.insert_one({'user_id':'owner','id':'legacy','name':'Forbidden'})


def command(**patch):
    return {'request_id':'inventory-create-1','expected_session_scope':digest(['owner','staff']),
        'supplier_id':'supplier','invoice_number':'STOCK-1','invoice_date':'2026-10-07','note':'',
        'lines':[{'kind':'product','item_id':'product','quantity':3,'unit_price':'20','tax':'9'},
                 {'kind':'component','item_id':'component','quantity':2,'unit_price':'5','tax':'1.50'}],**patch}


def test_quantity_tax_debt_and_separate_payment_retry_refresh():
    async def scenario(db):
        await seed(db)
        before=await db.mezan_products_v2.find_one({})
        results=await asyncio.gather(*[save_purchase(db,'owner','staff',command(),clock=NOW) for _ in range(5)])
        assert all(r==results[0] for r in results)
        saved=results[0]
        assert (saved['net'],saved['tax'],saved['gross'])==('70.00','10.50','80.50')
        state=await read(db,'owner')
        assert len(state['inventory_purchases'])==1 and not state['movements']
        assert report(state)['summary']['actual_liquidity']=='1000.00'
        assert report(state)['summary']['payable']=='80.50'
        pay={**movement(),'amount':'30','allocations':[{'obligation_id':saved['obligation_id'],'amount':'30'}]}
        await asyncio.gather(*[create_movement(db,'owner','staff',pay,clock=NOW) for _ in range(3)])
        await refresh(db,'owner',clock=NOW);await refresh(db,'owner',clock=NOW)
        state=await read(db,'owner');view=purchase_view(state,state['inventory_purchases'][0])
        assert view['settled']=='30.00' and view['outstanding']=='50.50'
        assert report(state)['summary']['actual_liquidity']=='970.00'
        assert len(state['movements'])==1
        stock=inventory_view(state)['stock']
        assert {(s['kind'],s['quantity']) for s in stock}=={('product',3),('component',2)}
        assert await db.mezan_products_v2.find_one({})==before
        assert await db.mezan_supplier_invoices_v2.count_documents({})==0
    run(scenario)


@pytest.mark.parametrize('patch',[
    {'supplier_id':'foreign'}, {'invoice_date':'2099-01-01'},
    {'lines':[{'kind':'product','item_id':'legacy','quantity':1,'unit_price':'2','tax':'0'}]},
    {'lines':[{'kind':'component','item_id':'component','quantity':0,'unit_price':'2','tax':'0'}]},
    {'lines':[{'kind':'product','item_id':'product','quantity':1,'unit_price':'2','tax':'-1'}]},
    {'lines':[{'kind':'product','item_id':'product','quantity':1.5,'unit_price':'2','tax':'0'}]},
])
def test_invalid_purchase_has_no_partial_quantity_or_debt(patch):
    async def scenario(db):
        await seed(db);before=await read(db,'owner')
        with pytest.raises(HTTPException):await save_purchase(db,'owner','staff',command(**patch),clock=NOW)
        assert await read(db,'owner')==before
    run(scenario)


def test_invoice_business_identity_conflict_and_owner_change():
    async def scenario(db):
        await seed(db)
        result=await asyncio.gather(*[save_purchase(db,'owner','staff',command(request_id=f'concurrent-{i}'),clock=NOW) for i in range(3)],return_exceptions=True)
        assert sum(isinstance(r,HTTPException) for r in result)==2
        state=await read(db,'owner');assert len(state['inventory_purchases'])==1
        with pytest.raises(HTTPException):await save_purchase(db,'other','staff',command(request_id='concurrent-0'),clock=NOW)
        assert 'inventory_purchases' not in await read(db,'other')
    run(scenario)


@pytest.mark.parametrize('permissions',[[],[WRITE],[READ],[WRITE,READ]])
def test_api_permissions_and_write_only_entry(permissions):
    async def scenario(db):
        await seed(db);await grant(db,permissions)
        async with client(db) as c:
            base='/api/operational-balances'
            assert (await c.get(base+'/inventory-catalog')).status_code==(200 if permissions else 403)
            assert (await c.get(base+'/inventory-purchases')).status_code==(200 if READ in permissions else 403)
            r=await c.post(base+'/inventory-purchases',json=command())
            assert r.status_code==(200 if WRITE in permissions else 403),r.text
            lookup=await c.get(base+'/inventory-purchases/entry/supplier/STOCK-1')
            assert lookup.status_code==(200 if WRITE in permissions else 403),lookup.text
    run(scenario)


def test_catalog_is_mz2_only_and_excludes_service_foreign_and_archived():
    async def scenario(db):
        await seed(db)
        await db.mezan_products_v2.insert_many([{'user_id':'other','mezan_product_id':'foreign','name':'Private'}, {'user_id':'owner','mezan_product_id':'archived','name':'Archived','archived':True}])
        await db.mezan_cost_resources_v2.insert_one({'user_id':'owner','id':'service','name':'Service','track_inventory':False})
        assert {r['id'] for r in await catalog(db,'owner')}=={'product','component'}
    run(scenario)


@pytest.mark.parametrize('source_kind',['bank','cash','employee_custody'])
def test_inventory_payment_source_and_overpayment_atomic(source_kind):
    async def scenario(db):
        await seed(db);await grant(db,[WRITE])
        await db.mezan_employees_v2.insert_one({'user_id':'owner','id':'employee','account_user_id':'staff','name':'Staff','status':'active'})
        await db.mezan_mobile_app_access_v1.update_one({'user_id':'staff'},{'$set':{'operational_banks.cash_ids':['cash']}})
        if source_kind=='employee_custody':
            await create_movement(db,'owner','owner',{**movement(),'request_id':'fund-custody','party_type':'employee_custody','party_id':'employee','amount':'100'},clock=NOW)
        saved=await save_purchase(db,'owner','staff',command(),clock=NOW)
        account={'bank':'bank','cash':'cash','employee_custody':'employee'}[source_kind]
        pay={**movement(),'request_id':'pay-inventory','source_account_type':source_kind,'bank_id':account,'amount':'80.50',
             'expected_session_scope':digest(['owner','staff']),'allocations':[{'obligation_id':saved['obligation_id'],'amount':'80.50'}]}
        async with client(db) as c:
            replies=await asyncio.gather(*[c.post('/api/operational-balances/movements',json=pay) for _ in range(3)])
            assert all(r.status_code==200 for r in replies),[r.text for r in replies]
            assert (await c.post('/api/operational-balances/movements',json={**pay,'request_id':'overpayment-1'})).status_code==409
        state=await read(db,'owner');records={r['party_id']:r for r in report(state)['parties']}
        assert purchase_view(state,state['inventory_purchases'][0])['outstanding']=='0.00'
        assert records['bank']['actual']=={'bank':'919.50','cash':'1000.00','employee_custody':'900.00'}[source_kind]
        if source_kind=='cash':assert records['cash']['actual']=='-80.50'
        if source_kind=='employee_custody':assert records['employee']['custody_remaining']=='19.50'
        assert report(state)['summary']['operating_expenses_paid']=='0.00'
    run(scenario)


def test_mz2_and_replacement_invoice_collision_and_payload_conflict():
    async def scenario(db):
        await seed(db)
        await db.mezan_supplier_invoices_v2.insert_one({'user_id':'owner','supplier_id':'supplier','invoice_number':'STOCK-1'})
        with pytest.raises(HTTPException):await save_purchase(db,'owner','staff',command(),clock=NOW)
        assert not (await read(db,'owner')).get('inventory_purchases')
        await db.mezan_supplier_invoices_v2.delete_many({})
        saved=await save_purchase(db,'owner','staff',command(invoice_number='STOCK-2',request_id='inventory-second'),clock=NOW)
        with pytest.raises(HTTPException):await save_purchase(db,'owner','staff',command(invoice_number='STOCK-3',request_id='inventory-second'),clock=NOW)
        assert len((await read(db,'owner'))['inventory_purchases'])==1
        # Separate synthetic scope avoids resetting an active baseline.
        state=await read(db,'owner')
        state['customer_exchanges']=[{'purchases':[{'supplier_id':'supplier','invoice_number':'EXISTING-EXCHANGE'}]}]
        await db.operational_balance_states_v1.replace_one({'_id':'owner'},state)
        with pytest.raises(HTTPException):await save_purchase(db,'owner','staff',command(invoice_number='EXISTING-EXCHANGE',request_id='cross-invoice'),clock=NOW)
        assert len((await read(db,'owner'))['inventory_purchases'])==1
    run(scenario)


def test_later_mz2_invoice_stops_refresh_without_duplicate_debt():
    async def scenario(db):
        await seed(db);await save_purchase(db,'owner','staff',command(),clock=NOW)
        before=await read(db,'owner')
        await db.mezan_supplier_invoices_v2.insert_one({'user_id':'owner','supplier_id':'supplier','invoice_number':'STOCK-1'})
        with pytest.raises(HTTPException) as error:await refresh(db,'owner',clock=NOW)
        assert error.value.detail['code']=='inventory_invoice_source_conflict'
        assert await read(db,'owner')==before
        assert report(before)['summary']['payable']=='80.50'
    run(scenario)


def test_invoice_number_with_slash_and_live_grant_revocation(monkeypatch):
    import operational_balance_inventory as inventory
    original=inventory.save_purchase
    async def scenario(db):
        await seed(db);await grant(db,[WRITE])
        async with client(db) as c:
            base='/api/operational-balances'
            payload=command(invoice_number='INV/2026/001')
            assert (await c.post(base+'/inventory-purchases',json=payload)).status_code==200
            assert (await c.get(base+'/inventory-purchases/entry/supplier/INV%2F2026%2F001')).status_code==200
            async def revoke(*args,**kwargs):
                await grant(db,[READ])
                return await original(*args,**kwargs)
            monkeypatch.setattr(inventory,'save_purchase',revoke)
            before=await read(db,'owner')
            response=await c.post(base+'/inventory-purchases',json=command(invoice_number='REVOKED',request_id='revoked-inventory'))
            assert response.status_code==403
            assert await read(db,'owner')==before
    run(scenario)
