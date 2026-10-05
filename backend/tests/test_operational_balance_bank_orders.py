"""Latest operational-only decision: order bank credits without receipts."""
import asyncio
import hashlib
import json
import pytest
from fastapi import HTTPException
from test_operational_balance_integration import run, started, refresh, read, report, movement, create_movement, NOW


async def seed(db, status='reviewed', method='تحويل بنكي مصرف تجريبي'):
    await db.mz2_bank_transfer_bindings.insert_one({
        '_id':hashlib.sha256(json.dumps(['owner','salla.payment_method_bank','مصرف تجريبي'],ensure_ascii=False).encode()).hexdigest(),
        'user_id':'owner','upstream_source':'salla.payment_method_bank','upstream_value':'مصرف تجريبي',
        'financial_account_id':'bank','status':'active','confirmed':True,'identity_contract_version':1,
        'bank_account_source':'mz2_financial_accounts'})
    await db.unified_orders.insert_one({'user_id':'owner','order_number':'new-bank-order','raw_by_source':{'salla_direct':{
        'id':'salla-bank-id','reference_id':'new-bank-order','created_at':'2026-10-06T10:00:00+00:00',
        'updated_at':'2026-10-06T10:00:00+00:00','status':status,'payment_method':method,'currency':'SAR',
        'total':{'amount':'300','currency':'SAR'},'items':[]}}})


@pytest.mark.parametrize('status',['reviewed','in_progress','تم المراجعة','قيد التنفيذ'])
def test_bank_order_credit_without_receipt_once_across_retries_and_states(status):
    async def scenario(db):
        await started(db);await seed(db,status)
        await asyncio.gather(refresh(db,'owner',clock=NOW),refresh(db,'owner',clock=NOW))
        await db.unified_orders.update_one({'user_id':'owner'},{'$set':{'raw_by_source.salla_direct.status':'in_progress'}})
        await refresh(db,'owner',clock=NOW)
        state=await read(db,'owner');assert len(state['movements'])==1
        assert state['movements'][0]['receipt_id'] is None
        assert report(state)['summary']['actual_liquidity']=='1300.00'
        assert not any(o['kind']=='cod' for o in state['engine']['obligations'].values())
        with pytest.raises(HTTPException):
            payload=movement();payload.update(request_id='manual-bank-duplicate',direction='incoming',order_number='new-bank-order')
            await create_movement(db,'owner','owner',payload)
        assert len((await read(db,'owner'))['movements'])==1
    run(scenario)


def test_missing_binding_and_ineligible_status_never_guess_bank():
    async def scenario(db):
        await started(db);await seed(db,'pending')
        await refresh(db,'owner',clock=NOW);assert not (await read(db,'owner'))['movements']
        await db.unified_orders.update_one({'user_id':'owner'},{'$set':{'raw_by_source.salla_direct.status':'reviewed'}})
        await db.mz2_bank_transfer_bindings.update_one({'user_id':'owner'},{'$set':{'confirmed':False}})
        await refresh(db,'owner',clock=NOW);state=await read(db,'owner')
        assert not state['movements'];assert any(i['code']=='operational_order_bank_binding_incomplete' for i in state['engine']['issues'])
    run(scenario)


def test_later_delivery_or_cancellation_does_not_repeat_or_erase_bank_credit():
    async def scenario(db):
        await started(db);await seed(db);await refresh(db,'owner',clock=NOW)
        for status in ('delivered','cancelled'):
            await db.unified_orders.update_one({'user_id':'owner'},{'$set':{'raw_by_source.salla_direct.status':status}})
            await refresh(db,'owner',clock=NOW)
            state=await read(db,'owner');assert len(state['movements'])==1
            assert report(state)['summary']['actual_liquidity']=='1300.00'
    run(scenario)


def test_prestart_order_and_changed_amount_do_not_create_extra_money():
    async def scenario(db):
        await started(db);await seed(db)
        await db.unified_orders.update_one({'user_id':'owner'},{'$set':{'raw_by_source.salla_direct.created_at':'2026-10-01T10:00:00+00:00'}})
        await refresh(db,'owner',clock=NOW);assert not (await read(db,'owner'))['movements']
        await db.unified_orders.update_one({'user_id':'owner'},{'$set':{'raw_by_source.salla_direct.created_at':'2026-10-06T10:00:00+00:00'}})
        await refresh(db,'owner',clock=NOW)
        await db.unified_orders.update_one({'user_id':'owner'},{'$set':{'raw_by_source.salla_direct.total.amount':'350'}})
        await refresh(db,'owner',clock=NOW);state=await read(db,'owner')
        assert report(state)['summary']['actual_liquidity']=='1300.00'
        assert any(i['code']=='operational_order_bank_credit_conflict' for i in state['engine']['issues'])
    run(scenario)


def test_same_order_cannot_credit_again_after_owner_change():
    from operational_balance_service import save_opening
    from test_operational_balance_integration import START
    async def scenario(db):
        await started(db);await seed(db);await refresh(db,'owner',clock=NOW)
        await db.mz2_financial_accounts.insert_one({'id':'bank','user_id':'other','name':'Other bank','account_type':'bank','currency':'SAR','status':'active'})
        await save_opening(db,'other','other',{'request_id':'other-baseline','party_type':'bank','party_id':'bank','direction':'for_us','amount':'1','currency':'SAR'},clock=START)
        await save_opening(db,'other','other',{'request_id':'start-other-owner'},finish=True,clock=START)
        binding=await db.mz2_bank_transfer_bindings.find_one({'user_id':'owner'})
        binding.update(user_id='other',_id=hashlib.sha256(json.dumps(['other','salla.payment_method_bank','مصرف تجريبي'],ensure_ascii=False).encode()).hexdigest())
        await db.mz2_bank_transfer_bindings.insert_one(binding)
        await db.unified_orders.update_one({'user_id':'owner'},{'$set':{'user_id':'other'}})
        with pytest.raises(HTTPException) as error:
            await refresh(db,'other',clock=NOW)
        assert error.value.detail['code']=='operational_request_scope_conflict'
        assert len((await read(db,'owner'))['movements'])==1
        assert not (await read(db,'other'))['movements']
    run(scenario)
