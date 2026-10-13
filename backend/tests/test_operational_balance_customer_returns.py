import asyncio
from copy import deepcopy
import pytest
from fastapi import HTTPException
from operational_customer_returns import order_view, save_case, shipping_quote
from operational_balance_service import report, refresh, create_movement
from operational_balance_store import read, digest, mutate
from test_operational_balance_integration import run, started, movement, START, NOW
from test_operational_balance_app_permissions import client, grant, WRITE


async def fixture(db):
    await started(db)
    await db.unified_orders.insert_one({'user_id':'owner','order_number':'1001','raw_by_source':{'salla_direct':{
        'id':'order1','reference_id':'1001','created_at':'2026-10-06T09:00:00+00:00','updated_at':NOW,'currency':'SAR','status':'delivered',
        'shipping':{'delivered_at':'2026-10-06T12:00:00+00:00'},
        'total':'300','items':[{'id':'a','name':'منتج أ','quantity':2},{'id':'b','name':'منتج ب','quantity':1}],
        'payment':{'method':'tamara','status':'paid','reference':'capture1','paid_amount':'300','paid_at':'2026-10-06T09:01:00+00:00'}}}})
    await db.mz2_provider_fee_policies_v2.insert_one({'user_id':'owner','policies':[{'id':'fee','provider':'tamara','currency':'SAR','status':'active','effective_from':'2026-01-01',
        'percentage':'2','fixed_amount':'0','vat_treatment':'exempt','refund_fee_treatment':'retain','cancellation_fee_treatment':'retain'}]})
    await db.mz2_shipping_setup_v2.insert_one({'user_id':'owner','couriers':[{'courier_key':'carrier','name':'شركة اختبار','confirmed_by':'owner','confirmed_at':START}],
        'contracts':[{'id':'shipping-rate','status':'approved','party_type':'courier','party_id':'carrier','context':'delivery','effective_from':START,
                      'confirmed_by':'owner','confirmed_at':START,'delivery_fee':'25','vat_included':False,'vat_percent':'15','cod_fixed_fee':'7','cod_percent':'1'}]})
    await db.store_drivers.insert_one({'user_id':'owner','id':'driver','name':'مندوب مجاني','status':'active'})


def command(**patch):
    return {'request_id':'return-create-1','expected_session_scope':digest(['owner','staff']), 'order_number':'1001','items':[{'id':'a','quantity':1}],
            'status':'pending','amount':None,'refund_source_type':'bank','refund_source_id':'bank','refund_reference':'refund-1',
            'refunded_at':NOW,'shipping_kind':'none','shipping_id':None,'shipment_reference':'','shipment_completed':False,'note':'',**patch}


def test_pending_then_bank_refund_is_atomic_and_retry_safe():
    async def scenario(db):
        await fixture(db)
        c=await save_case(db,'owner','staff',command(),clock=NOW)
        assert report(await read(db,'owner'))['summary']['actual_liquidity']=='1000.00'
        p=command(request_id='confirm-1',status='refunded',amount='100')
        replies=await asyncio.gather(*[save_case(db,'owner','staff',p,case_id=c['id'],clock=NOW) for _ in range(5)])
        assert all(r['id']==c['id'] for r in replies)
        state=await read(db,'owner');assert len(state['customer_returns'])==1
        assert report(state)['summary']['actual_liquidity']=='900.00'
        await refresh(db,'owner',clock=NOW)
        assert report(await read(db,'owner'))['summary']['actual_liquidity']=='900.00'
        assert (await order_view(db,'owner','1001'))['items'][0]['remaining']==1
    run(scenario)


def test_platform_partial_and_full_refunds_retain_fee_without_bank_debit():
    async def scenario(db):
        await fixture(db)
        a=command(status='refunded',amount='100',refund_source_type='provider',refund_source_id='tamara')
        await save_case(db,'owner','staff',a,clock=NOW)
        await refresh(db,'owner',clock=NOW);await refresh(db,'owner',clock=NOW)
        result=report(await read(db,'owner'));projection=result['details']['provider_reports']['provider:order1']
        assert projection['refunded']=='100.00' and projection['estimated_fees']=='6.00'
        assert result['summary']['actual_liquidity']=='1000.00'
        b={**a,'request_id':'return-create-2','refund_reference':'refund-2','items':[{'id':'a','quantity':1},{'id':'b','quantity':1}],'amount':'200'}
        await save_case(db,'owner','staff',b,clock=NOW);await refresh(db,'owner',clock=NOW)
        projection=report(await read(db,'owner'))['details']['provider_reports']['provider:order1']
        assert projection['refunded']=='300.00' and projection['estimated_fees']=='6.00'
    run(scenario)


@pytest.mark.parametrize('kind,fee',[('courier','28.75'),('store_driver','0.00')])
def test_return_shipping_matches_contract_not_example_price_and_driver_is_free(kind,fee):
    async def scenario(db):
        await fixture(db);identity='carrier' if kind=='courier' else 'driver'
        quote=await shipping_quote(db,'owner',kind,identity,NOW,'1001');assert quote['amount']==fee
        p=command(shipping_kind=kind,shipping_id=identity,shipment_reference='awb1',shipping_quote_hash=digest(quote))
        c=await save_case(db,'owner','staff',p,clock=NOW)
        state=await read(db,'owner');key='customer-return-shipping:'+c['id']
        if kind=='courier':assert state['engine']['obligations'][key]['expected']==fee
        else:assert key not in state['engine']['obligations']
        await save_case(db,'owner','staff',{**p,'request_id':'ship-complete','shipment_completed':True},case_id=c['id'],clock=NOW)
        await refresh(db,'owner',clock=NOW);state=await read(db,'owner')
        if kind=='courier':assert state['engine']['obligations'][key]['confirmed']==fee and state['engine']['obligations'][key]['expected']=='0.00'
        else:assert key not in state['engine']['obligations']
        assert report(state)['summary']['actual_liquidity']=='1000.00'
    run(scenario)


@pytest.mark.parametrize('patch',[{'items':[{'id':'a','quantity':3}]},{'items':[{'id':'foreign','quantity':1}]},{'amount':'301','status':'refunded'},
 {'amount':'10','status':'refunded','refund_source_type':'provider','refund_source_id':'tabby'}, {'amount':'10','status':'refunded','refund_source_id':'foreign'}])
def test_invalid_return_has_no_partial_financial_or_case_write(patch):
    async def scenario(db):
        await fixture(db)
        with pytest.raises(HTTPException):await save_case(db,'owner','staff',command(**patch),clock=NOW)
        state=await read(db,'owner');assert state.get('customer_returns',[])==[]
        assert report(state)['summary']['actual_liquidity']=='1000.00'
    run(scenario)


def test_concurrent_quantity_and_reference_rejection():
    async def scenario(db):
        await fixture(db)
        replies=await asyncio.gather(*[save_case(db,'owner','staff',command(request_id=f'compete-{i}',items=[{'id':'b','quantity':1}]),clock=NOW) for i in range(2)],return_exceptions=True)
        assert sum(isinstance(r,HTTPException) for r in replies)==1
        p=command(request_id='actual-refund',status='refunded',amount='10')
        await save_case(db,'owner','staff',p,clock=NOW)
        with pytest.raises(HTTPException):await save_case(db,'owner','staff',{**p,'request_id':'same-ref-new-id'},clock=NOW)
        assert report(await read(db,'owner'))['summary']['actual_liquidity']=='990.00'
    run(scenario)


def test_missing_retain_policy_blocks_confirmation_but_native_write_can_save_pending():
    async def scenario(db):
        await fixture(db);await grant(db,[WRITE])
        await db.mz2_provider_fee_policies_v2.update_one({'user_id':'owner'},{'$unset':{'policies.0.refund_fee_treatment':''}})
        with pytest.raises(HTTPException):await save_case(db,'owner','staff',command(status='refunded',amount='10',refund_source_type='provider',refund_source_id='tamara'),clock=NOW)
        async with client(db) as c:
            assert (await c.get('/api/operational-balances/customer-returns')).status_code==403
            response=await c.post('/api/operational-balances/customer-returns',json=command(request_id='pending-after-policy-rejection'))
            assert response.status_code==200,response.text
        assert (await read(db,'owner'))['customer_returns'][0]['status']=='pending'
        assert report(await read(db,'owner'))['summary']['actual_liquidity']=='1000.00'
    run(scenario)


def test_original_shipping_price_survives_new_tariff_and_frozen_evidence():
    async def scenario(db):
        await fixture(db)
        setup = await db.mz2_shipping_setup_v2.find_one({'user_id':'owner'})
        old = setup['contracts'][0]
        old['effective_to'] = '2026-10-07T00:00:00+00:00'
        newer = {**old, 'id':'new-tariff', 'delivery_fee':'40', 'effective_from':old['effective_to'], 'effective_to':None}
        await db.mz2_shipping_setup_v2.update_one({'user_id':'owner'},{'$set':{'contracts':[old,newer]}})
        q = await shipping_quote(db,'owner','courier','carrier',NOW,'1001')
        assert q['amount']=='28.75' and q['contract_id']=='shipping-rate'
        async def frozen(state):
            state.setdefault('engine',{}).setdefault('facts',{}).update({
                'shipping:order1':{'id':'carrier','party_type':'courier','delivered_at':'2026-10-06T12:00:00+00:00'},
                'shipping:order1:base_shipping':{'cost':'23.00'}})
        await mutate(db,'owner',frozen)
        q = await shipping_quote(db,'owner','courier','carrier',NOW,'1001')
        assert q['amount']=='23.00' and q['source']=='confirmed_original_shipping'
    run(scenario)


def test_missing_shipping_date_and_changed_quote_fail_without_record():
    async def scenario(db):
        await fixture(db)
        with pytest.raises(HTTPException):
            await save_case(db,'owner','staff',command(shipping_kind='courier',shipping_id='carrier',shipping_quote_hash='old'),clock=NOW)
        await db.unified_orders.update_one({'user_id':'owner'},{'$unset':{'raw_by_source.salla_direct.shipping.delivered_at':''}})
        with pytest.raises(HTTPException):
            await shipping_quote(db,'owner','courier','carrier',NOW,'1001')
        assert (await read(db,'owner')).get('customer_returns',[])==[]
    run(scenario)


def test_browser_api_create_read_confirm_and_foreign_owner_denial():
    async def scenario(db):
        await fixture(db)
        async with client(db,mobile=False) as c:
            assert (await c.get('/api/operational-balances/customer-returns/order/1001')).status_code==200
            response=await c.post('/api/operational-balances/customer-returns',json=command())
            assert response.status_code==200,response.text
            case=response.json()
            payload=command(request_id='http-confirm',status='refunded',amount='75',refunded_at='2026-10-06T13:00:00+00:00')
            response=await c.post(f'/api/operational-balances/customer-returns/{case["id"]}/confirm',json=payload)
            assert response.status_code==200,response.text
            assert (await c.post(f'/api/operational-balances/customer-returns/{case["id"]}/confirm',json=payload)).json()==response.json()
            assert len((await c.get('/api/operational-balances/customer-returns')).json()['items'])==1
        async with client(db,actor='other',mobile=False) as c:
            assert (await c.get('/api/operational-balances/customer-returns/order/1001')).status_code==409
            assert (await c.get('/api/operational-balances/customer-returns')).json()['items']==[]
        assert report(await read(db,'owner'))['summary']['actual_liquidity']=='925.00'
    run(scenario)


@pytest.mark.parametrize('return_first',[True,False])
def test_refund_and_manual_bank_movement_cannot_reuse_reference(return_first):
    async def scenario(db):
        await fixture(db)
        async def refund():
            return await save_case(db,'owner','staff',command(status='refunded',amount='10'),clock=NOW)
        async def manual():
            return await create_movement(db,'owner','staff',{**movement(),'amount':'10','reference':'refund-1'},clock=NOW)
        await (refund() if return_first else manual())
        with pytest.raises(HTTPException):await (manual() if return_first else refund())
        assert report(await read(db,'owner'))['summary']['actual_liquidity']=='990.00'
    run(scenario)
