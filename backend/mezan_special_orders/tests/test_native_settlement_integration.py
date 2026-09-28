"""Actual native settlement routes against one disposable Mongo replica set."""
import os
import httpx
import pytest
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorCollection
from mezan_special_orders.tests.test_core import OWNER
from mezan_special_orders.tests.test_financial_integration import run, gl_balance, movement
from mezan_special_orders.tests.test_native_delivery_integration import prepared, assign, pick_up, app_client
from mezan_special_orders.ledger_adapter import EVENTS
from store_delivery_settlement_routes import SETTLEMENTS, make_store_delivery_settlement_router

pytestmark=pytest.mark.skipif(not os.getenv('MEZAN_SPECIAL_TEST_REPLICA_URI'),reason='Dedicated replica set required')


def client(h):
    app=FastAPI()
    app.include_router(make_store_delivery_settlement_router(h.db,lambda:{'id':OWNER.tenant_id,'role':'owner'}))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic.test')


async def delivered_cash(h):
    order=await prepared(h,partial=True)
    order,ev=await h.claim(order)
    order=await h.fin_command(order,'bank_collection',
        {'receipt_claim_id':order['receipt_claims'][0]['claim_id'],'movement':movement(ev)},'initial-partial-bank')
    assignment=await assign(h,order)
    await pick_up(h,order)
    async with app_client(h,driver=True) as c:
        result=await c.post('/store-delivery/app/deliveries/status',json={
            'barcode':order['order_number'],'target_status':'delivered','payment_method':'cash'})
        assert result.status_code==200,result.text
    doc=await h.doc(order)
    parent=next(p['movement_id'] for p in doc['payments'] if p['kind']=='cod_collection')
    return order,parent


async def payload(h,order,parent,*,amount=10,offset=0,reference='REMIT-001',allocated=1000):
    ev=await h.evidence.upload(OWNER.tenant_id,OWNER.actor_id,kind='bank_receipt' if amount else 'cost_document',
        content_type='image/png',data=h.png)
    out={'amount':amount,'earning_offset':offset,'idempotency_key':'native-settlement-'+reference,
        'special_allocations':[{'order_id':order['order_id'],'parent_movement_id':parent,'amount_minor':allocated}]
            if allocated else []}
    if amount:
        out.update(account_id='bank-demo',reference=reference,movement=movement(ev,reference=reference))
    else:
        out['evidence']=ev.model_dump(mode='json')
    return out


def test_partial_native_remittance_one_bank_leg_and_no_second_customer_payment():
    async def scenario(h):
        order,parent=await delivered_cash(h)
        data=await payload(h,order,parent)
        async with client(h) as c:
            result=await c.post('/store-delivery/settlements/driver/native-driver/cod-remittance',json=data)
            assert result.status_code==201,result.text
            count=await h.raw.general_ledger.count_documents({})
            replay=await c.post('/store-delivery/settlements/driver/native-driver/cod-remittance',json=data)
            assert replay.status_code==201,replay.text
            assert replay.json()['settlement']['id']==result.json()['settlement']['id']
            assert await h.raw.general_ledger.count_documents({})==count
            totals=(await c.get('/store-delivery/settlements/driver/native-driver/summary')).json()
            assert totals['cod_cash_custody']==10 and totals['delivery_earnings_due']==20,totals
        state=await h.integrated.get(OWNER,order['order_id'])
        assert state['balances']['custody_minor']==1000
        assert state['balances']['collected_minor']==6000 and state['balances']['remaining_minor']==0
        assert await gl_balance(h,'bank','bank-demo')==5000
        assert await gl_balance(h,'store_driver','native-driver','cod_receivable')==1000
        assert await h.raw.account_transactions.count_documents({})==2
        assert await h.raw[SETTLEMENTS].count_documents({})==1
        assert await h.raw.general_ledger.count_documents({'entity_type':'revenue'})==0
        async with app_client(h,driver=True) as c:
            summary=await c.get('/store-delivery/app/accounts/summary')
            assert summary.status_code==200,summary.text
            assert summary.json()['cod_cash_custody']==10
    run(scenario)


def test_native_fee_payment_is_not_a_second_shipping_expense():
    async def scenario(h):
        order,parent=await delivered_cash(h)
        before=await h.doc(order)
        data=await payload(h,order,parent,amount=20,allocated=0,reference='FEE-001')
        async with client(h) as c:
            result=await c.post('/store-delivery/settlements/driver/native-driver/earning-payment',json=data)
            assert result.status_code==201,result.text
            assert result.json()['summary']['delivery_earnings_due']==0,result.text
        assert await gl_balance(h,'store_driver','native-driver','delivery_fee_payable')==0
        assert await gl_balance(h,'store_driver','native-driver','cod_receivable')==2000
        assert await gl_balance(h,'bank','bank-demo')==2000
        assert (await h.doc(order))['costs']==before['costs']
    run(scenario)


def test_native_explicit_netting_clears_fee_and_custody_without_bank_movement():
    async def scenario(h):
        order,parent=await delivered_cash(h)
        data=await payload(h,order,parent,amount=0,offset=20,allocated=2000,reference='NET-001')
        async with client(h) as c:
            result=await c.post('/store-delivery/settlements/driver/native-driver/net-settlement',json=data)
            assert result.status_code==201,result.text
            summary=result.json()['summary']
            assert summary['cod_cash_custody']==summary['delivery_earnings_due']==0,summary
            assert summary['cod_cash_remitted']==summary['delivery_earnings_paid']==20,summary
        state=await h.integrated.get(OWNER,order['order_id'])
        assert state['balances']['custody_minor']==0 and state['balances']['collected_minor']==6000
        assert await h.raw.account_transactions.count_documents({})==1
        assert await gl_balance(h,'store_driver','native-driver','cod_receivable')==0
        assert await gl_balance(h,'store_driver','native-driver','delivery_fee_payable')==0
        async with app_client(h,driver=True) as c:
            summary=(await c.get('/store-delivery/app/accounts/summary')).json()
            assert summary['cod_cash_custody']==summary['earnings_due']==0,summary
            assert summary['earnings_paid']==20,summary
    run(scenario)


def test_local_custody_cannot_be_silently_settled_without_allocation_or_evidence():
    async def scenario(h):
        order,parent=await delivered_cash(h)
        data=await payload(h,order,parent)
        count=await h.raw.general_ledger.count_documents({})
        async with client(h) as c:
            for bad in ({'amount':10,'account_id':'bank-demo'}, {**data,'special_allocations':[]},
                        {**data,'movement':None}, {**data,'amount':30}):
                result=await c.post('/store-delivery/settlements/driver/native-driver/cod-remittance',json=bad)
                assert result.status_code in (403,409,422),result.text
                assert await h.raw.general_ledger.count_documents({})==count
        assert await h.raw[SETTLEMENTS].count_documents({})==0
        assert (await h.integrated.get(OWNER,order['order_id']))['balances']['custody_minor']==2000
    run(scenario)


def test_native_settlement_failure_rolls_back_bank_ledger_order_and_settlement(monkeypatch):
    original=AsyncIOMotorCollection.insert_one
    async def fail(self,*args,**kwargs):
        if self.name==EVENTS:raise RuntimeError('synthetic settlement audit failure')
        return await original(self,*args,**kwargs)
    async def scenario(h):
        order,parent=await delivered_cash(h)
        before=await h.doc(order);count=await h.raw.general_ledger.count_documents({})
        data=await payload(h,order,parent)
        monkeypatch.setattr(AsyncIOMotorCollection,'insert_one',fail)
        async with client(h) as c:
            with pytest.raises(RuntimeError,match='synthetic settlement audit failure'):
                await c.post('/store-delivery/settlements/driver/native-driver/cod-remittance',json=data)
        assert await h.doc(order)==before
        assert await h.raw.general_ledger.count_documents({})==count
        assert await h.raw.account_transactions.count_documents({})==1
        assert await h.raw[SETTLEMENTS].count_documents({})==0
    run(scenario)
