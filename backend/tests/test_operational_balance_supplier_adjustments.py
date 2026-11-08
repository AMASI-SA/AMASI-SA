import asyncio
from copy import deepcopy
import pytest
from fastapi import HTTPException
from test_operational_balance_integration import run, NOW, movement
from test_operational_balance_inventory import seed, command
from test_operational_balance_app_permissions import client, grant, WRITE, READ
from operational_balance_inventory import save_purchase
from operational_supplier_adjustments import save_adjustment, lookup
from operational_balance_store import read, digest
from operational_balance_service import report, refresh, create_movement


def adjustment(invoice,**patch):
    return dict(request_id='supplier-credit-1',expected_session_scope=digest(['owner','staff']),invoice_id=invoice['id'],kind='return',accepted=True,reference='CREDIT-1',business_date='2026-10-07',amount=None,lines=[dict(kind='product',item_id='product',quantity=1)],note='',**patch)


def test_return_concurrent_retry_tax_projection_refresh_no_bank_write():
    async def scenario(db):
        await seed(db);inv=await save_purchase(db,'owner','staff',command(),clock=NOW)
        source=await db.mezan_products_v2.find_one({})
        results=await asyncio.gather(*[save_adjustment(db,'owner','staff',adjustment(inv),clock=NOW) for _ in range(5)])
        assert all(r==results[0] for r in results)
        a=results[0]['adjustments'][0]
        assert (a['net'],a['tax'],a['gross'])==('20.00','3.00','23.00')
        await refresh(db,'owner',clock=NOW)
        state=await read(db,'owner')
        assert report(state)['summary']['payable']=='57.50'
        assert report(state)['summary']['actual_liquidity']=='1000.00'
        assert len(state['supplier_adjustments'])==1 and not state['movements']
        assert await db.mezan_products_v2.find_one({})==source
    run(scenario)


def test_paid_invoice_discount_is_credit_not_bank_and_full_remaining_return():
    async def scenario(db):
        await seed(db);inv=await save_purchase(db,'owner','staff',command(),clock=NOW)
        await create_movement(db,'owner','staff',{**movement(),'amount':'80.50','allocations':[{'obligation_id':inv['obligation_id'],'amount':'80.50'}]},clock=NOW)
        p=adjustment(inv);p.update(kind='discount',amount='11.50',lines=[])
        saved=await save_adjustment(db,'owner','staff',p,clock=NOW)
        assert saved['adjusted_gross']=='69.00' and saved['credit']=='11.50'
        p.update(request_id='supplier-return-full',reference='FULL-RETURN',kind='return',amount=None,lines=[dict(kind='product',item_id='product',quantity=3),dict(kind='component',item_id='component',quantity=2)])
        saved=await save_adjustment(db,'owner','staff',p,clock=NOW)
        assert saved['adjusted_gross']=='0.00' and saved['credit']=='80.50'
        state=await read(db,'owner');assert len(state['movements'])==1
        assert report(state)['summary']['actual_liquidity']=='919.50'
        assert report(state)['summary']['receivable']=='80.50'
        assert sum(float(a['tax']) for a in saved['adjustments'])==10.50
    run(scenario)


@pytest.mark.parametrize('patch',[{'accepted':False},{'invoice_id':'foreign'},{'reference':''},{'business_date':'2099-01-01'},{'lines':[dict(kind='product',item_id='product',quantity=4)]},{'lines':[dict(kind='product',item_id='legacy',quantity=1)]},{'kind':'discount','amount':'81','lines':[]},{'amount':'1'}])
def test_invalid_adjustment_is_atomic(patch):
    async def scenario(db):
        await seed(db);inv=await save_purchase(db,'owner','staff',command(),clock=NOW)
        before=await read(db,'owner');p=adjustment(inv);p.update(patch)
        with pytest.raises(HTTPException):await save_adjustment(db,'owner','staff',p,clock=NOW)
        assert await read(db,'owner')==before
    run(scenario)


def test_cumulative_and_business_reference_duplicate_rejected():
    async def scenario(db):
        await seed(db);inv=await save_purchase(db,'owner','staff',command(),clock=NOW)
        p=adjustment(inv)
        await save_adjustment(db,'owner','staff',p,clock=NOW)
        p.update(request_id='other-request-2')
        with pytest.raises(HTTPException):await save_adjustment(db,'owner','staff',p,clock=NOW)
        p.update(reference='SECOND',lines=[dict(kind='product',item_id='product',quantity=3)])
        with pytest.raises(HTTPException):await save_adjustment(db,'owner','staff',p,clock=NOW)
        assert len((await read(db,'owner'))['supplier_adjustments'])==1
    run(scenario)


@pytest.mark.parametrize('permissions',[[],[WRITE],[READ],[WRITE,READ]])
def test_api_permission_exact_lookup_no_report_grant(permissions):
    async def scenario(db):
        await seed(db);inv=await save_purchase(db,'owner','staff',command(),clock=NOW);await grant(db,permissions)
        async with client(db) as c:
            path='/api/operational-balances/supplier-adjustments'
            response=await c.get(path+'/entry/supplier/STOCK-1')
            assert response.status_code==(200 if WRITE in permissions else 403),response.text
            response=await c.post(path,json=adjustment(inv))
            assert response.status_code==(200 if WRITE in permissions else 403),response.text
            assert (await c.get('/api/operational-balances/reports')).status_code==(200 if READ in permissions else 403)
    run(scenario)


def test_write_only_refunded_shipment_continuation_keeps_refund_immutable():
    from test_operational_balance_customer_returns import fixture, command as return_command
    async def scenario(db):
        await fixture(db);await grant(db,[WRITE])
        async with client(db) as c:
            base='/api/operational-balances/customer-returns'
            quote=(await c.get(base+'/shipping-quote/courier/carrier?order_number=1001')).json()
            p=return_command(status='refunded',amount='50',shipping_kind='courier',shipping_id='carrier',shipping_quote_hash=quote['quote_hash'],shipment_reference='RETURN-SHIP-1')
            r=await c.post(base,json=p);assert r.status_code==200,r.text
            entry=(await c.get(base+'/entry/1001')).json()['items'][0]
            assert entry['amount']=='50.00' and entry['refund_source_id']=='bank'
            assert not {'summary','purchases','contributions'} & entry.keys()
            before=report(await read(db,'owner'))['summary']['actual_liquidity']
            p.update({k:entry[k] for k in ('amount','refund_source_type','refund_source_id','refund_reference','refunded_at')})
            p.update(request_id='ship-confirm-1',shipment_completed=True)
            path=base+'/'+entry['id']+'/confirm'
            replies=await asyncio.gather(*[c.post(path,json=p) for _ in range(3)])
            assert all(r.status_code==200 for r in replies),[r.text for r in replies]
            assert (await c.get(base+'/entry/1001')).json()['items']==[]
            assert report(await read(db,'owner'))['summary']['actual_liquidity']==before
            assert (await c.get('/api/operational-balances/reports')).status_code==403
    run(scenario)


def test_exchange_invoice_credit_survives_refresh():
    from test_operational_balance_exchanges import seed as exchange_seed, create, purchase
    from operational_balance_exchanges import save_exchange
    async def scenario(db):
        await exchange_seed(db);case=await create(db)
        saved=await save_exchange(db,'owner','staff',purchase(case),case_id=case['id'],clock=NOW)
        inv=saved['purchases'][0]
        p=adjustment(inv);p.update(lines=[dict(kind='product',item_id=inv['lines'][0]['item_id'],quantity=1)])
        result=await save_adjustment(db,'owner','staff',p,clock=NOW)
        await refresh(db,'owner',clock=NOW)
        state=await read(db,'owner')
        assert state['engine']['obligations']['exchange-purchase:'+inv['id']]['confirmed']==result['adjusted_gross']
        assert not state['movements']
    run(scenario)


def test_rounding_cumulative_returns_preserve_exact_invoice_totals():
    async def scenario(db):
        await seed(db)
        inv=await save_purchase(db,'owner','staff',command(lines=[dict(kind='product',item_id='product',quantity=3,unit_price='0.01',tax='0.01')]),clock=NOW)
        for n in range(3):
            p=adjustment(inv);p.update(request_id='rounding-return-'+str(n),reference='R-'+str(n))
            result=await save_adjustment(db,'owner','staff',p,clock=NOW)
        assert result['adjusted_gross']=='0.00'
        from decimal import Decimal
        assert sum(Decimal(a['net']) for a in result['adjustments'])==Decimal('.03')
        assert sum(Decimal(a['tax']) for a in result['adjustments'])==Decimal('.01')
        assert result['lines'][0]['remaining_quantity']==0
    run(scenario)


def test_concurrent_payment_and_credit_never_overpay_adjusted_invoice():
    async def scenario(db):
        await seed(db);inv=await save_purchase(db,'owner','staff',command(),clock=NOW)
        p=adjustment(inv);p.update(kind='discount',amount='11.50',lines=[])
        payment={**movement(),'amount':'80.50','allocations':[{'obligation_id':inv['obligation_id'],'amount':'80.50'}]}
        results=await asyncio.gather(save_adjustment(db,'owner','staff',p,clock=NOW),create_movement(db,'owner','staff',payment,clock=NOW),return_exceptions=True)
        state=await read(db,'owner');view=lookup(state,'supplier','STOCK-1')
        assert view['adjusted_gross']=='69.00' and len(state['supplier_adjustments'])==1
        if state['movements']:
            assert view['credit']=='11.50' and report(state)['summary']['actual_liquidity']=='919.50'
        else:
            assert isinstance(results[1],HTTPException) and view['outstanding']=='69.00'
            assert report(state)['summary']['actual_liquidity']=='1000.00'
    run(scenario)


def test_exchange_invoice_lines_use_exact_parent_snapshots_without_guessing():
    from operational_supplier_adjustments import invoice_view
    invoice={'id':'invoice','supplier_id':'supplier','invoice_number':'EX-1','gross':'23.00','lines':[
        {'item_id':'a','quantity':1,'net':'10.00','tax':'1.50','gross':'11.50'},
        {'item_id':'b','quantity':1,'net':'10.00','tax':'1.50','gross':'11.50'}]}
    state={'movements':[],'customer_exchanges':[{'purchases':[invoice],'items':[
        {'id':'a','name':'منتج أول','product_id':'p1','image_url':'https://example.invalid/first.png'},
        {'id':'b','name':'منتج ثان','product_id':'p2'}]}]}
    original=deepcopy(state)
    lines=invoice_view(state,'exchange',invoice)['lines']
    assert [(l['name'],l['product_id']) for l in lines]==[('منتج أول','p1'),('منتج ثان','p2')]
    assert lines[0]['image_url']=='https://example.invalid/first.png' and lines[1]['image_url'] is None
    assert state==original
    state['customer_exchanges'][0]['items']=[]
    lines=invoice_view(state,'exchange',invoice)['lines']
    assert all(l['name'] is None and l['product_id'] is None and l['image_url'] is None for l in lines)
