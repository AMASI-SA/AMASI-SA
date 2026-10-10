"""Real isolated Mongo exchange lifecycle; no production/runtime imports."""
import asyncio
import pytest
from fastapi import HTTPException
from operational_balance_exchanges import exchange_order, save_exchange, project_exchanges
from operational_balance_service import report, refresh, create_movement
from operational_balance_store import read, digest
from operational_customer_returns import shipping_quote
from test_operational_balance_customer_returns import fixture
from test_operational_balance_integration import run, NOW, movement
from test_operational_balance_app_permissions import client, grant, WRITE


async def seed(db):
    await fixture(db)
    await db.unified_orders.update_one({'user_id':'owner'},{'$set':{
        'raw_by_source.salla_direct.created_at':'2026-09-01T09:00:00+00:00',
        'raw_by_source.salla_direct.items.0.product_id':'p1',
        'raw_by_source.salla_direct.items.1.product_id':'p2'}})
    await db.mezan_product_cost_profiles_v2.insert_many([
        {'user_id':'owner','salla_product_id':'p1','base_cost':'50'},
        {'user_id':'owner','salla_product_id':'p2','base_cost':'20'}])


def command(**patch):
    return {'request_id':'exchange-create-1','expected_session_scope':digest(['owner','staff']),
            'order_number':'1001','items':[{'id':'a','quantity':2}], 'shipping_id':'carrier',
            'shipping_quote_hash':None,'shipment_reference':'', 'contribution':None, **patch}


async def create(db,**patch):
    q=await shipping_quote(db,'owner','courier','carrier',NOW,'1001')
    return await save_exchange(db,'owner','staff',command(shipping_quote_hash=digest(q),**patch),clock=NOW)


def action(case,kind,**patch):
    return {'request_id':'exchange-'+kind+'-1','expected_session_scope':digest(['owner','staff']),
            'action':kind,**patch}


def test_old_order_expected_then_partial_invoice_gross_supplier_payment():
    async def scenario(db):
        await seed(db)
        before=await db.unified_orders.count_documents({})
        case=await create(db)
        assert case['order_number']=='1001' and case['summary']['expected_products']=='100.00'
        assert case['summary']['shipping']=='28.75'
        assert report(await read(db,'owner'))['summary']['actual_liquidity']=='1000.00'
        p=action(case,'purchase',supplier_id='supplier',invoice_number='real-invoice-1',invoice_date='2026-10-06',
                 lines=[{'item_id':'a','quantity':1,'net':'80','tax':'12','gross':'92'}],net='80',tax='12',gross='92')
        results=await asyncio.gather(*[save_exchange(db,'owner','staff',p,case_id=case['id'],clock=NOW) for _ in range(4)])
        assert all(c['summary']['confirmed_products']=='92.00' for c in results)
        assert results[0]['summary']['expected_products']=='50.00'
        state=await read(db,'owner');invoice=state['customer_exchanges'][0]['purchases'][0]
        oid='exchange-purchase:'+invoice['id']
        await create_movement(db,'owner','staff',{**movement(),'amount':'92','allocations':[{'obligation_id':oid,'amount':'92'}]},clock=NOW)
        await refresh(db,'owner',clock=NOW);await refresh(db,'owner',clock=NOW)
        result=report(await read(db,'owner'))
        assert result['summary']['actual_liquidity']=='908.00'
        assert await db.unified_orders.count_documents({})==before
        assert await db.mezan_supplier_invoices_v2.count_documents({})==0
    run(scenario)


def test_customer_contribution_is_one_bank_credit_without_reducing_liabilities():
    async def scenario(db):
        await seed(db)
        case=await create(db,contribution={'bank_id':'bank','amount':'30','reference':'customer-paid-1','paid_at':'2026-10-06T12:00:00+00:00','existing_movement_id':None})
        assert case['summary']['customer_contribution']=='30.00'
        assert case['summary']['net_cost']=='98.75'
        assert report(await read(db,'owner'))['summary']['actual_liquidity']=='1030.00'
        await refresh(db,'owner',clock=NOW);await refresh(db,'owner',clock=NOW)
        state=await read(db,'owner');assert len(state['movements'])==1
        assert state['movements'][0]['order_number'] is None
        assert state['engine']['obligations']['exchange-shipping:'+case['id']]['expected']=='28.75'
    run(scenario)


def purchase(case,**patch):
    return action(case,'purchase',supplier_id='supplier',invoice_number='invoice-1',invoice_date='2026-10-06',
        lines=[{'item_id':'a','quantity':1,'net':'40','tax':'6','gross':'46'}],net='40',tax='6',gross='46',**patch)


@pytest.mark.parametrize('patch',[
    {'supplier_id':'foreign'}, {'gross':'47'}, {'tax':'-1'},
    {'lines':[{'item_id':'foreign','quantity':1,'net':'40','tax':'6','gross':'46'}]},
    {'lines':[{'item_id':'a','quantity':3,'net':'40','tax':'6','gross':'46'}]},
    {'invoice_date':'2030-01-01'}])
def test_invalid_invoice_has_no_partial_debt(patch):
    async def scenario(db):
        await seed(db);case=await create(db)
        before=await read(db,'owner')
        with pytest.raises(HTTPException):await save_exchange(db,'owner','staff',{**purchase(case),**patch},case_id=case['id'],clock=NOW)
        after=await read(db,'owner');assert before==after
    run(scenario)


def test_competing_purchases_and_duplicate_invoice_other_case_rejected():
    async def scenario(db):
        await seed(db);case=await create(db,items=[{'id':'a','quantity':1}])
        p=purchase(case)
        results=await asyncio.gather(*[save_exchange(db,'owner','staff',{**p,'request_id':f'invoice-race-{i}','invoice_number':f'inv-{i}'},case_id=case['id'],clock=NOW) for i in range(2)],return_exceptions=True)
        assert sum(isinstance(r,HTTPException) for r in results)==1
        record=(await read(db,'owner'))['customer_exchanges'][0]['purchases'][0]
        other=await create(db,request_id='exchange-other-1',items=[{'id':'b','quantity':1}])
        with pytest.raises(HTTPException):await save_exchange(db,'owner','staff',{**p,'request_id':'duplicate-invoice','invoice_number':record['invoice_number'],'lines':[{'item_id':'b','quantity':1,'net':'40','tax':'6','gross':'46'}]},case_id=other['id'],clock=NOW)
    run(scenario)


def test_link_existing_bank_credit_then_retry_and_late_contribution():
    async def scenario(db):
        await seed(db);case=await create(db)
        m=await create_movement(db,'owner','staff',{**movement(),'party_type':'bank','party_id':'bank','direction':'incoming','kind':'collection','amount':'30','reference':'existing-paid'},clock=NOW)
        p=action(case,'contribution',contribution={'bank_id':'bank','amount':'30','reference':'existing-paid','paid_at':NOW,'existing_movement_id':m['id']})
        await asyncio.gather(*[save_exchange(db,'owner','staff',p,case_id=case['id'],clock=NOW) for _ in range(4)])
        state=await read(db,'owner');assert len(state['movements'])==1
        assert report(state)['summary']['actual_liquidity']=='1030.00'
        with pytest.raises(HTTPException):await save_exchange(db,'owner','staff',{**p,'request_id':'reuse-existing'},case_id=case['id'],clock=NOW)
    run(scenario)


def test_atomic_create_bad_contribution_and_concurrent_creation():
    async def scenario(db):
        await seed(db)
        with pytest.raises(HTTPException):await create(db,contribution={'bank_id':'foreign','amount':'30','reference':'bank-ref','paid_at':NOW})
        assert (await read(db,'owner')).get('customer_exchanges',[])==[]
        results=await asyncio.gather(*[create(db,request_id=f'new-exchange-{i}') for i in range(3)],return_exceptions=True)
        assert sum(isinstance(r,HTTPException) for r in results)==2
        assert len((await read(db,'owner'))['customer_exchanges'])==1
    run(scenario)


def test_shipping_confirmation_does_not_debit_bank_and_survives_refresh():
    async def scenario(db):
        await seed(db);case=await create(db)
        p=action(case,'shipping_completed',shipment_reference='awb-exchange-1')
        await save_exchange(db,'owner','staff',p,case_id=case['id'],clock=NOW)
        await save_exchange(db,'owner','staff',p,case_id=case['id'],clock=NOW)
        await refresh(db,'owner',clock=NOW)
        state=await read(db,'owner');ob=state['engine']['obligations']['exchange-shipping:'+case['id']]
        assert ob['expected']=='0.00' and ob['confirmed']=='28.75'
        assert report(state)['summary']['actual_liquidity']=='1000.00'
    run(scenario)


def test_browser_api_and_native_write_without_reports_and_source_unchanged():
    async def scenario(db):
        await seed(db);await grant(db,[WRITE])
        source_before=await db.unified_orders.find_one({'user_id':'owner'})
        async with client(db,mobile=False) as c:
            order=(await c.get('/api/operational-balances/customer-exchanges/order/1001')).json()
            assert order['items'][0]['unit_estimate']=='50.00'
            q=(await c.get('/api/operational-balances/customer-returns/shipping-quote/courier/carrier?order_number=1001')).json()
            result=await c.post('/api/operational-balances/customer-exchanges',json=command(shipping_quote_hash=q['quote_hash']))
            assert result.status_code==200,result.text
            case=result.json()
            p=purchase(case)
            result=await c.post(f'/api/operational-balances/customer-exchanges/{case["id"]}/actions',json=p)
            assert result.status_code==200,result.text
        async with client(db) as c:
            assert (await c.get('/api/operational-balances/customer-exchanges')).status_code==403
            assert (await c.post('/api/operational-balances/customer-exchanges',json={})).status_code==422
        async with client(db,actor='other',mobile=False) as c:
            assert (await c.get('/api/operational-balances/customer-exchanges')).json()['items']==[]
            assert (await c.post(f'/api/operational-balances/customer-exchanges/{case["id"]}/actions',json=p)).status_code in (403,409)
        assert await db.unified_orders.find_one({'user_id':'owner'})==source_before
        assert await db.mezan_supplier_invoices_v2.count_documents({})==0
    run(scenario)


def test_missing_cost_and_existing_native_invoice_are_not_guessed():
    async def scenario(db):
        await seed(db)
        await db.mezan_product_cost_profiles_v2.update_one({'salla_product_id':'p1'},{'$unset':{'base_cost':''}})
        with pytest.raises(HTTPException):await create(db)
        await db.mezan_product_cost_profiles_v2.update_one({'salla_product_id':'p1'},{'$set':{'base_cost':'50'}})
        case=await create(db,request_id='valid-after-cost')
        await db.mezan_supplier_invoices_v2.insert_one({'user_id':'owner','supplier_id':'supplier','invoice_number':'invoice-1'})
        with pytest.raises(HTTPException):await save_exchange(db,'owner','staff',purchase(case),case_id=case['id'],clock=NOW)
        assert (await read(db,'owner'))['customer_exchanges'][0]['purchases']==[]
    run(scenario)


def test_contribution_does_not_replace_original_order_bank_credit():
    async def scenario(db):
        await seed(db)
        from test_operational_balance_bank_orders import seed as bank_order
        await bank_order(db)
        await db.unified_orders.update_one({'order_number':'new-bank-order'},{'$set':{
            'raw_by_source.salla_direct.items':[{'id':'x','product_id':'p1','name':'منتج جديد','quantity':1}],
            'raw_by_source.salla_direct.shipping':{'delivered_at':'2026-10-06T12:00:00+00:00'}}})
        q=await shipping_quote(db,'owner','courier','carrier',NOW,'new-bank-order')
        await save_exchange(db,'owner','staff',command(order_number='new-bank-order',items=[{'id':'x','quantity':1}],shipping_quote_hash=digest(q),
            contribution={'bank_id':'bank','amount':'30','reference':'extra-contribution','paid_at':NOW,'existing_movement_id':None}),clock=NOW)
        await asyncio.gather(*[refresh(db,'owner',clock=NOW) for _ in range(3)])
        state=await read(db,'owner');assert len(state['movements'])==2
        assert report(state)['summary']['actual_liquidity']=='1330.00'
        assert sum(bool(m.get('automatic_order_bank')) for m in state['movements'])==1
        assert not any(i['code']=='operational_order_bank_credit_conflict' for i in state['engine']['issues'])
    run(scenario)


def test_runtime_read_allowlist_and_no_source_or_accounting_writes():
    async def scenario(db):
        await seed(db)
        from test_operational_balance_sources import HARD_SOURCE_ALLOWLIST
        from operational_balance_store import STATES, OPERATION_CLAIMS
        touched=set()
        class Source:
            def __init__(self,collection):self.collection=collection
            def __getattr__(self,name):
                assert name in {'find','find_one'},f'Forbidden source operation {name}'
                return getattr(self.collection,name)
        class Guard:
            def __getitem__(self,name):
                touched.add(name)
                if name in {STATES,OPERATION_CLAIMS}:return db[name]
                assert name in HARD_SOURCE_ALLOWLIST|{'settings','mz2_atomic_owners'},f'Forbidden source {name}'
                return Source(db[name])
        safe=Guard();case=await create(safe)
        await save_exchange(safe,'owner','staff',purchase(case),case_id=case['id'],clock=NOW)
        await save_exchange(safe,'owner','staff',action(case,'contribution',contribution={'bank_id':'bank','amount':'30','reference':'guard-ref','paid_at':NOW}),case_id=case['id'],clock=NOW)
        assert not any('journal' in n or 'ledger' in n for n in touched)
    run(scenario)
