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


def compensation(**patch):
    return {**command(),
            'request_id': 'salla-compensation-document-1',
            'case_type': 'order_compensation',
            'status': 'salla_refund_recorded',
            'items': [],
            'amount': '20.00',
            'refund_source_type': None,
            'refund_source_id': None,
            'refund_reference': 'salla-refund-20-001',
            'already_refunded_in_salla': True,
            'note': 'تعويض 20 ريالًا بالاتفاق مع العميل دون إرجاع المنتج',
            'shipping_kind': 'none', 'shipping_id': None,
            'shipment_reference': '', 'shipment_completed': False,
            'shipping_quote_hash': None,
            **patch}


def test_salla_net_150_after_20_refund_stays_150_after_documentation():
    async def scenario(db):
        await fixture(db)
        # Salla already changed this order's displayed total, so our record
        # must not turn the 150 SAR net amount into 130 SAR.
        await db.unified_orders.update_one({'user_id':'owner'},
            {'$set':{'raw_by_source.salla_direct.total':'150',
                     'raw_by_source.salla_direct.payment.refunded_amount':'20'}})
        original = await order_view(db, 'owner', '1001')
        assert original['total'] == '150.00'
        assert original['items'][0]['remaining'] == 2
        before = report(await read(db, 'owner'))
        request = compensation()
        results = await asyncio.gather(*[save_case(db,'owner','staff',request,clock=NOW) for _ in range(4)])
        assert all(r['id'] == results[0]['id'] for r in results)
        case = results[0]
        assert case['amount'] == '20.00'
        assert case['financial_effect'] == 'none_already_refunded_in_salla'
        assert case['order_total_after_refund_snapshot'] == '150.00'
        assert case['items'] == [] and case['shipping']['kind'] == 'none'
        assert case['note'] == request['note']
        state = await read(db,'owner')
        assert len(state['customer_returns']) == 1
        assert not state['movements']
        assert report(state)['summaries'] == before['summaries']
        new_view = await order_view(db,'owner','1001')
        assert new_view['total'] == '150.00'
        assert new_view['salla_refunds_documented'] == '20.00'
        assert new_view['items'][0]['remaining'] == 2
        with pytest.raises(HTTPException) as error:
            await save_case(db,'owner','staff',compensation(request_id='new-request-id'),clock=NOW)
        assert error.value.detail['code'] == 'customer_compensation_duplicate'
        assert error.value.detail['not_applied'] is True
        assert len((await read(db,'owner'))['customer_returns']) == 1
        for collection in ('accounting_general_ledger_v2', 'accounting_journal_groups_v2', 'general_ledger'):
            assert await db[collection].count_documents({}) == 0
    run(scenario)


@pytest.mark.parametrize('patch',[
    {'items':[{'id':'a','quantity':1}]},
    {'status':'refunded'},
    {'status':'pending'},
    {'already_refunded_in_salla':False},
    {'refund_source_type':'bank','refund_source_id':'bank'},
    {'shipping_kind':'courier','shipping_id':'carrier'},
    {'shipment_completed':True},
    {'note':''},
    {'note':'abc'},
    {'refund_reference':''},
    {'amount':'0'},
    {'amount':'-0.01'},
    {'refunded_at':'2099-01-01T00:00:00+00:00'},
])
def test_record_only_salla_compensation_invalid_inputs_never_book_cash(patch):
    async def scenario(db):
        await fixture(db)
        with pytest.raises(HTTPException):
            await save_case(db,'owner','staff',compensation(**patch),clock=NOW)
        state = await read(db,'owner')
        assert not state.get('customer_returns',[])
        assert not state['movements']
        assert report(state)['summaries']['SAR']['actual_liquidity'] == '1000.00'
    run(scenario)


def test_compensation_and_product_return_coexist_without_reducing_item_quantities():
    async def scenario(db):
        await fixture(db)
        await save_case(db,'owner','staff',compensation(),clock=NOW)
        await save_case(db,'owner','staff',
            command(request_id='return-item-separately',status='pending'),clock=NOW)
        view = await order_view(db,'owner','1001')
        assert view['items'][0]['remaining'] == 1
        assert view['salla_refunds_documented'] == '20.00'
        assert len((await read(db,'owner')).get('customer_returns',[])) == 2
        assert report(await read(db,'owner'))['summaries']['SAR']['actual_liquidity'] == '1000.00'
    run(scenario)


def test_compensation_record_via_mobile_native_endpoint_never_posts_bank():
    async def scenario(db):
        await fixture(db)
        await grant(db,[WRITE])
        async with client(db) as c:
            response = await c.post('/api/operational-balances/customer-returns',json=compensation())
            assert response.status_code == 200,response.text
            assert response.json()['status'] == 'salla_refund_recorded'
            assert response.json()['case_type'] == 'order_compensation'
            # Native writers receive the scoped receipt result, including
            # whether this was documentary only or a real source debit.
            assert response.json()['financial_effect']=='none_already_refunded_in_salla'
            again = await c.post('/api/operational-balances/customer-returns',json=compensation())
            assert again.status_code == 200 and again.json() == response.json()
        assert len((await read(db,'owner'))['customer_returns']) == 1
        assert not (await read(db,'owner'))['movements']
    run(scenario)


def test_salla_refund_observation_is_read_only_without_unique_execution_identity():
    async def scenario(db):
        await fixture(db)
        await db.unified_orders.update_one({'user_id':'owner'},{'$set':{
            'raw_by_source.salla_direct.payment_actions.refund_action.refunded_transactions':[
                {'payment_method':'mada','amount':{'amount':20,'currency':'SAR'}}]}})
        before = report(await read(db,'owner'))
        view = await order_view(db,'owner','1001')
        assert view['salla_refund_observation'] == {
            'amount':'20.00','count':1,'status':'observed_requires_execution_evidence'}
        assert view['refunded'] == '0.00'
        assert view['salla_refunds_documented'] == '0.00'
        assert report(await read(db,'owner')) == before
        # Generic Salla evidence alone never creates a customer return,
        # bank debit or provider movement.
        assert not (await read(db,'owner')).get('customer_returns',[])
        assert not (await read(db,'owner'))['movements']
    run(scenario)


@pytest.mark.parametrize('value',[{'currency':'USD','amount':20},
                                  {'currency':'SAR','amount':'3.141'},
                                  {'currency':'SAR','amount':-3}])
def test_invalid_salla_refund_observation_is_not_financial_evidence(value):
    async def scenario(db):
        await fixture(db)
        await db.unified_orders.update_one({'user_id':'owner'},{'$set':{
            'raw_by_source.salla_direct.payment_actions.refund_action.refunded_transactions':[
                {'amount':value}]}})
        view = await order_view(db,'owner','1001')
        assert view['salla_refund_observation']['status'] == 'invalid_evidence'
        assert view['salla_refund_observation']['amount'] is None
        assert report(await read(db,'owner'))['summary']['actual_liquidity'] == '1000.00'
    run(scenario)


def confirmed_compensation(**patch):
    values = {'status':'salla_refund_confirmed','refund_source_type':'provider',
              'refund_source_id':'salla'}
    values.update(patch)
    return compensation(**values)


async def add_refund_evidence(db, amount='20.00', new_total='150'):
    await db.unified_orders.update_one({'user_id':'owner'}, {'$set':{
        'raw_by_source.salla_direct.total':new_total,
        'raw_by_source.salla_direct.payment_actions.refund_action.refunded_transactions':[
            {'payment_method':'mada','amount':{'amount':amount,'currency':'SAR'}}
        ]}})


def test_manual_salla_refund_source_debits_once_without_reducing_net_sale():
    async def scenario(db):
        await fixture(db)
        await db.unified_orders.update_one({'user_id':'owner'},
            {'$set':{'raw_by_source.salla_direct.total':'170',
                     'raw_by_source.salla_direct.payment.method':'mada'}})
        assert (await order_view(db,'owner','1001'))['total']=='170.00'
        await add_refund_evidence(db)
        before=report(await read(db,'owner'))
        p=confirmed_compensation()
        saved=await save_case(db,'owner','staff',p,clock=NOW)
        again=await save_case(db,'owner','staff',p,clock=NOW)
        assert saved==again and saved['financial_effect']=='confirmed_provider_refund_debit'
        state=await read(db,'owner')
        assert len(state['customer_returns'])==1 and len(state['movements'])==0
        after=report(state)
        source=next(r for r in after['parties']
                    if r['party_type']=='provider' and r['party_id']=='salla')
        assert source['actual']=='-20.00'
        assert source['customer_refunds_manual_debited']=='20.00'
        assert after['summaries']['SAR']['actual_liquidity']==before['summaries']['SAR']['actual_liquidity']
        assert after['details']['manual_customer_refunds'][0]['source_id']=='salla'
        assert after['details']['manual_customer_refunds'][0]['amount']=='20.00'
        view=await order_view(db,'owner','1001')
        assert view['total']=='150.00'
        assert view['salla_refunds_documented']=='20.00'
        assert view['salla_refunds_confirmed_source']=='20.00'
        assert view['items'][0]['remaining']==2
        with pytest.raises(HTTPException) as duplicate:
            await save_case(db,'owner','staff',confirmed_compensation(request_id='second-request'),clock=NOW)
        assert duplicate.value.detail['code']=='customer_compensation_duplicate'
        assert duplicate.value.detail.get('not_applied') is True
        assert len((await read(db,'owner'))['customer_returns'])==1
    run(scenario)


def test_manual_bank_refund_debits_one_bank_account_and_appears_in_statement():
    async def scenario(db):
        from operational_balance_service import account_statement
        from operational_balance_sources import entities
        await fixture(db)
        await add_refund_evidence(db)
        p=compensation(status='salla_refund_confirmed',refund_source_type='bank',refund_source_id='bank')
        saved=await save_case(db,'owner','staff',p,clock=NOW)
        assert saved['financial_effect']=='confirmed_bank_refund_debit'
        state=await read(db,'owner')
        summary=report(state)
        bank=next(r for r in summary['parties'] if r['party_type']=='bank' and r['party_id']=='bank')
        assert bank['actual']=='980.00'
        assert bank['customer_refunds_manual_debited']=='20.00'
        assert summary['summaries']['SAR']['actual_liquidity']=='980.00'
        account=next(a for a in await entities(db,'owner','bank') if a['id']=='bank')
        statement=account_statement(state,account)
        assert len(statement['items'])==1
        assert statement['items'][0]['amount']=='20.00'
        assert statement['items'][0]['direction']=='outgoing'
        assert statement['items'][0]['reference']=='salla-refund-20-001'
        assert (await order_view(db,'owner','1001'))['total']=='150.00'
        assert not state['movements']  # no second payment API call or automatic bank transfer
    run(scenario)


@pytest.mark.parametrize('patch', [
    {'refund_source_type':None,'refund_source_id':None},
    {'refund_source_id':'foreign'},
    {'refund_source_type':'cash','refund_source_id':'bank'},
    {'already_refunded_in_salla':False},
    {'items':[{'id':'a','quantity':1}]},
    {'status':'pending'},
    {'amount':'21.00'},
    {'amount':'0.001'},
    {'note':'no'},
    {'refund_reference':''},
    {'shipping_kind':'courier','shipping_id':'carrier'},
])
def test_confirmed_source_refund_invalid_fields_do_not_debit_any_balance(patch):
    async def scenario(db):
        await fixture(db)
        await add_refund_evidence(db)
        with pytest.raises(HTTPException):
            await save_case(db,'owner','staff',confirmed_compensation(**patch),clock=NOW)
        state=await read(db,'owner')
        assert not state.get('customer_returns',[])
        assert report(state)['summaries']['SAR']['actual_liquidity']=='1000.00'
    run(scenario)


def test_salla_order_value_change_alone_never_triggers_refund_source_debit():
    async def scenario(db):
        await fixture(db)
        await db.unified_orders.update_one({'user_id':'owner'},{
            '$set':{'raw_by_source.salla_direct.total':'150'}})
        with pytest.raises(HTTPException) as exc:
            await save_case(db,'owner','staff',confirmed_compensation(),clock=NOW)
        assert exc.value.detail['code']=='customer_compensation_salla_evidence_missing'
        state=await read(db,'owner')
        assert not state.get('customer_returns',[])
        assert report(state)['summaries']['SAR']['actual_liquidity']=='1000.00'
        assert (await order_view(db,'owner','1001'))['total']=='150.00'
    run(scenario)


def test_record_only_compensation_cannot_be_reconfirmed_and_debited_later():
    async def scenario(db):
        await fixture(db)
        await add_refund_evidence(db)
        await save_case(db,'owner','staff',compensation(),clock=NOW)
        with pytest.raises(HTTPException):
            await save_case(db,'owner','staff',confirmed_compensation(request_id='new-intent'),clock=NOW)
        state=await read(db,'owner')
        assert len(state.get('customer_returns',[]))==1
        assert report(state)['summaries']['SAR']['actual_liquidity']=='1000.00'
    run(scenario)


@pytest.mark.parametrize('original_method,chosen_provider', [
    ('mada', 'salla'), ('tamara', 'tamara'),
    ('tabby', 'tabby'), ('emkan', 'emkan'),
])
def test_manual_provider_selection_affects_only_chosen_payment_account(original_method, chosen_provider):
    async def scenario(db):
        await fixture(db)
        await db.unified_orders.update_one({'user_id':'owner'},
            {'$set':{'raw_by_source.salla_direct.payment.method':original_method}})
        await add_refund_evidence(db)
        assert (await order_view(db,'owner','1001'))['provider']==chosen_provider
        result = await save_case(db,'owner','staff',
            confirmed_compensation(refund_source_id=chosen_provider),clock=NOW)
        assert result['refund_source_id'] == chosen_provider
        state = await read(db,'owner')
        parties = report(state)['parties']
        adjustments = {r['party_id']:r['customer_refunds_manual_debited']
                       for r in parties if r['party_type']=='provider'}
        assert adjustments[chosen_provider]=='20.00'
        assert all(value=='0.00' for provider,value in adjustments.items()
                   if provider!=chosen_provider)
        assert (await order_view(db,'owner','1001'))['total']=='150.00'
        assert (await order_view(db,'owner','1001'))['salla_refunds_confirmed_source']=='20.00'
    run(scenario)


def test_refresh_does_not_post_the_manual_refund_twice():
    async def scenario(db):
        await fixture(db)
        await add_refund_evidence(db)
        await save_case(db,'owner','staff',
            confirmed_compensation(refund_source_id='tamara'),clock=NOW)
        before=report(await read(db,'owner'))
        account=next(r for r in before['parties']
                     if r['party_type']=='provider' and r['party_id']=='tamara')
        await refresh(db,'owner',clock=NOW)
        after=report(await read(db,'owner'))
        result=next(r for r in after['parties']
                    if r['party_type']=='provider' and r['party_id']=='tamara')
        assert account['customer_refunds_manual_debited']=='20.00'
        assert result['customer_refunds_manual_debited']=='20.00'
        assert result['actual']==account['actual']
        assert len((await read(db,'owner'))['customer_returns'])==1
    run(scenario)


@pytest.mark.parametrize('original_method,wrong_provider', [
    ('mada', 'tamara'),
    ('tamara', 'tabby'),
    ('tabby', 'salla'),
    ('emkan', 'tamara'),
    ('unknown_payment_method', 'salla'),
])
def test_manual_platform_refund_must_match_original_payment_provider(original_method, wrong_provider):
    async def scenario(db):
        await fixture(db)
        await db.unified_orders.update_one({'user_id':'owner'}, {'$set':{
            'raw_by_source.salla_direct.payment.method': original_method}})
        await add_refund_evidence(db)
        before = report(await read(db,'owner'))
        original = await order_view(db,'owner','1001')
        assert original['provider'] != wrong_provider
        with pytest.raises(HTTPException) as failure:
            await save_case(db,'owner','staff',
                confirmed_compensation(refund_source_id=wrong_provider),clock=NOW)
        assert failure.value.detail['code'] == 'customer_compensation_provider_mismatch'
        assert failure.value.detail.get('not_applied') is True
        state = await read(db,'owner')
        assert state.get('customer_returns', []) == []
        assert state['movements'] == []
        assert report(state)['summaries'] == before['summaries']
        assert (await order_view(db,'owner','1001'))['total'] == '150.00'
    run(scenario)


def test_refund_from_bank_still_allowed_when_original_order_was_paid_by_tamara():
    async def scenario(db):
        await fixture(db)
        await add_refund_evidence(db)
        order=await order_view(db,'owner','1001')
        assert order['provider']=='tamara'
        case=await save_case(db,'owner','staff',
            compensation(status='salla_refund_confirmed',
                         refund_source_type='bank', refund_source_id='bank'),clock=NOW)
        assert case['refund_source_type']=='bank'
        assert case['financial_effect']=='confirmed_bank_refund_debit'
        bank=next(row for row in report(await read(db,'owner'))['parties']
                  if row['party_type']=='bank' and row['party_id']=='bank')
        assert bank['actual']=='980.00'
        assert (await order_view(db,'owner','1001'))['total']=='150.00'
    run(scenario)
