"""Actual handover and standalone driver-app routes, using native collections."""
import os
import httpx
import pytest
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorCollection
import store_delivery_handover_routes as handover
import store_delivery_driver_app_routes as driver_app
import store_delivery_payment_evidence_routes as receipt_routes
import store_delivery_payment_resubmission_routes as resubmit_routes
import store_delivery_payment_review_routes as accountant_reviews
import order_review_routes as review
from mezan_special_orders.contracts import CreateOrder
from mezan_special_orders.integration import IntegratedOrders
from mezan_special_orders.tests.test_core import OWNER, request_data
from mezan_special_orders.tests.test_source_adapters import catalog_row, options
from mezan_special_orders.tests.test_financial_integration import run, gl_balance, movement
from mezan_special_orders.ledger_adapter import EVENTS

pytestmark=pytest.mark.skipif(not os.getenv('MEZAN_SPECIAL_TEST_REPLICA_URI'),reason='Dedicated replica set required')


def app_client(h,*,driver=False):
    user=({'id':'native-driver-user','role':'store_driver','created_by':OWNER.tenant_id,'name':'Synthetic driver'}
          if driver else {'id':OWNER.tenant_id,'role':'owner','name':'Synthetic owner'})
    app=FastAPI()
    for factory in (review.make_order_review_router,handover.make_store_delivery_handover_router,
                    driver_app.make_store_delivery_driver_app_router,receipt_routes.make_store_delivery_payment_evidence_router,
                    accountant_reviews.make_store_delivery_payment_review_router,
                    resubmit_routes.make_store_delivery_payment_resubmission_router):
        app.include_router(factory(h.db,lambda:user))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic.test')


async def prepared(h,*,partial=False):
    await h.raw.users.insert_many([{'id':OWNER.tenant_id,'role':'owner'},
        {'id':'native-driver-user','role':'store_driver','created_by':OWNER.tenant_id}])
    await h.raw.mezan_products_v2.insert_one(catalog_row())
    data=request_data('creator',partial=partial)
    data['fx']['evidence_id']='sar-fixed-1'
    data['items'][0]['options']=[v.model_dump(mode='json') for v in options(multi=True)]
    h.integrated=IntegratedOrders(h.db)
    order=await h.integrated.create(OWNER,CreateOrder.model_validate(data),'native-delivery-create')
    async with app_client(h) as client:
        response=await client.post(f'/order-reviews-v1/{order["order_number"]}/complete',json={'expected_revision':0})
        assert response.status_code==200,response.text
    await h.raw.order_review_workflows.update_one({'order_number':order['order_number']},{'$set':{
        'stage':'completed','assembly_status':'completed','carrier_label_type':'store_courier',
        'carrier_label_ready':True,'carrier_label_print_confirmed':True,'carrier_label_barcode':order['order_number']}})
    await h.raw.store_drivers.insert_one({'id':'native-driver','user_id':OWNER.tenant_id,'account_user_id':'native-driver-user',
        'status':'active','name':'Synthetic driver','city':data['recipient']['address']['city'],'delivery_fee':20})
    return await h.integrated.get(OWNER,order['order_id'])


async def assign(h,order):
    async with app_client(h) as client:
        response=await client.post('/store-delivery/handover/sessions',json={'driver_id':'native-driver'})
        assert response.status_code==201,response.text
        sid=response.json()['id']
        response=await client.post(f'/store-delivery/handover/sessions/{sid}/scan',json={'barcode':order['order_number']})
        assert response.status_code==200 and response.json()['accepted'],response.text
        response=await client.post(f'/store-delivery/handover/sessions/{sid}/confirm')
        assert response.status_code==200,response.text
        return response.json()['assignments'][0]


async def pick_up(h,order):
    async with app_client(h,driver=True) as client:
        response=await client.post('/store-delivery/app/deliveries/status',
            json={'barcode':order['order_number'],'target_status':'out_for_delivery'})
        assert response.status_code==200,response.text


@pytest.mark.parametrize('partial',[False,True])
def test_native_driver_flow_preserves_bank_cod_fee_and_replay_without_salla_sale(partial):
    async def scenario(h):
        order=await prepared(h,partial=partial)
        if partial:
            # Existing integrated bank command, not a test-double proof.
            order,ev=await h.claim(order)
            order=await h.fin_command(order,'bank_collection',
                {'receipt_claim_id':order['receipt_claims'][0]['claim_id'],'movement':movement(ev)},'native-bank-001')
        assignment=await assign(h,order)
        assert assignment['source_provider']=='mezan'
        assert await h.raw.unified_orders.count_documents({})==0
        await pick_up(h,order)
        payload={'barcode':order['order_number'],'target_status':'delivered','payment_method':'cash','outstanding_amount':999999}
        async with app_client(h,driver=True) as client:
            response=await client.post('/store-delivery/app/deliveries/status',json=payload)
            assert response.status_code==200,response.text
            assert response.json()['collection_amount']==(20 if partial else 0)
            count=await h.raw.general_ledger.count_documents({})
            replay=await client.post('/store-delivery/app/deliveries/status',json=payload)
            assert replay.status_code==200,replay.text
            assert await h.raw.general_ledger.count_documents({})==count
            listed=await client.get('/store-delivery/app/deliveries')
            assert listed.status_code==200,listed.text
            assert listed.json()['items'][0]['outstanding_amount']==0
        doc=await h.doc(order)
        assert len(doc['costs'])==1 and doc['costs'][0]['cost_sar_minor']==2000
        assert await gl_balance(h,'store_driver','native-driver','cod_receivable')==(2000 if partial else 0)
        assert await gl_balance(h,'store_driver','native-driver','delivery_fee_payable')==-2000
        assert await h.raw.general_ledger.count_documents({'entity_type':'revenue'})==0
        assert await h.raw[driver_app.DRIVER_EARNINGS].count_documents({})==1
        assert await h.raw[driver_app.DRIVER_COLLECTIONS].count_documents({})==1
        assert (await h.integrated.get(OWNER,order['order_id']))['stage']=='delivered'
    run(scenario)


def test_native_delivery_failure_rolls_back_native_custody_earning_ledger_and_stage(monkeypatch):
    original=AsyncIOMotorCollection.insert_one
    async def fail(self,*args,**kwargs):
        if self.name==EVENTS:
            raise RuntimeError('synthetic failure after native driver journal')
        return await original(self,*args,**kwargs)
    async def scenario(h):
        order=await prepared(h);assignment=await assign(h,order);await pick_up(h,order)
        before=await h.doc(order)
        monkeypatch.setattr(AsyncIOMotorCollection,'insert_one',fail)
        async with app_client(h,driver=True) as client:
            with pytest.raises(RuntimeError,match='synthetic failure after native driver journal'):
                await client.post('/store-delivery/app/deliveries/status',json={
                    'barcode':order['order_number'],'target_status':'delivered'})
        assert await h.doc(order)==before
        assert await h.raw.general_ledger.count_documents({})==0
        assert await h.raw[driver_app.DRIVER_EARNINGS].count_documents({})==0
        assert await h.raw[driver_app.DRIVER_COLLECTIONS].count_documents({})==0
        assert (await h.raw[handover.ASSIGNMENTS].find_one({'id':assignment['id']}))['status']=='out_for_delivery'
        assert (await h.raw.order_review_workflows.find_one({'order_number':order['order_number']}))['stage']=='delivering'
    run(scenario)


def test_native_fee_snapshot_tamper_is_blocked_without_financial_writes():
    async def scenario(h):
        order=await prepared(h);assignment=await assign(h,order);await pick_up(h,order)
        await h.raw[handover.ASSIGNMENTS].update_one({'id':assignment['id']},{'$set':{'delivery_fee_snapshot':999}})
        async with app_client(h,driver=True) as client:
            response=await client.post('/store-delivery/app/deliveries/status',json={
                'barcode':order['order_number'],'target_status':'delivered'})
            assert response.status_code==403,response.text
        assert await h.raw.general_ledger.count_documents({})==0
    run(scenario)


async def pending_noncash(h,method='bank_transfer'):
    order=await prepared(h,partial=True)
    order,ev=await h.claim(order)
    order=await h.fin_command(order,'bank_collection',
        {'receipt_claim_id':order['receipt_claims'][0]['claim_id'],'movement':movement(ev)},'native-bank-001')
    assignment=await assign(h,order);await pick_up(h,order)
    async with app_client(h,driver=True) as client:
        response=await client.post('/store-delivery/evidence/receipt',data={'assignment_id':assignment['id']},
            files={'file':('synthetic.png',h.png,'image/png')})
        assert response.status_code==200,response.text
        token=response.json()['receipt_reference']
        response=await client.post('/store-delivery/app/deliveries/status',json={
            'barcode':order['order_number'],'target_status':'delivered','payment_method':method,
            'receipt_reference':token,'bank_account_id':'bank-demo' if method=='bank_transfer' else None})
        assert response.status_code==200,response.text
    return order,assignment


@pytest.mark.parametrize('method',['bank_transfer','card_terminal'])
def test_native_noncash_approval_requires_real_bank_movement_and_posts_once(method):
    async def scenario(h):
        order,assignment=await pending_noncash(h,method)
        assert (await h.integrated.get(OWNER,order['order_id']))['balances']['remaining_minor']==2000
        assert await gl_balance(h,'bank','bank-demo')==4000
        assert await gl_balance(h,'store_driver','native-driver','cod_receivable')==0
        review_path=f'/store-delivery/payment-review/{assignment["id"]}'
        async with app_client(h) as client:
            response=await client.post(review_path,json={'decision':'approved'})
            assert response.status_code==422,response.text
            assert (await h.raw[driver_app.DRIVER_PAYMENT_REVIEWS].find_one({}))['status']=='pending'
            bank_ev=await h.evidence.upload(OWNER.tenant_id,OWNER.actor_id,kind='bank_receipt',
                content_type='image/png',data=h.png)
            data={'decision':'approved','note':'Matched real synthetic bank evidence',
                  'movement':movement(bank_ev,reference='NATIVE-APPROVAL-001')}
            response=await client.post(review_path,json=data)
            assert response.status_code==200,response.text
            count=await h.raw.general_ledger.count_documents({})
            again=await client.post(review_path,json=data)
            assert again.status_code==200,again.text
            assert await h.raw.general_ledger.count_documents({})==count
        assert (await h.integrated.get(OWNER,order['order_id']))['balances']['remaining_minor']==0
        assert await gl_balance(h,'bank','bank-demo')==6000
        assert await gl_balance(h,'store_driver','native-driver','cod_receivable')==0
        assert await gl_balance(h,'store_driver','native-driver','delivery_fee_payable')==-2000
        assert await h.raw.account_transactions.count_documents({})==2
        assert await h.raw.unified_orders.count_documents({})==0
    run(scenario)


def test_native_noncash_rejection_never_posts_bank_and_revoked_permission_fails():
    async def scenario(h):
        order,assignment=await pending_noncash(h)
        count=await h.raw.general_ledger.count_documents({})
        path=f'/store-delivery/payment-review/{assignment["id"]}'
        async with app_client(h) as client:
            await h.raw.users.update_one({'id':OWNER.tenant_id},{'$set':{'denied_permissions':['store_delivery.payments.review']}})
            denied=await client.post(path,json={'decision':'rejected','note':'Does not match'})
            assert denied.status_code==403,denied.text
            await h.raw.users.update_one({'id':OWNER.tenant_id},{'$unset':{'denied_permissions':''}})
            result=await client.post(path,json={'decision':'rejected','note':'Does not match'})
            assert result.status_code==200,result.text
        assert await h.raw.general_ledger.count_documents({})==count
        assert (await h.integrated.get(OWNER,order['order_id']))['balances']['remaining_minor']==2000
        assert (await h.raw[driver_app.DRIVER_PAYMENT_REVIEWS].find_one({}))['status']=='rejected'
    run(scenario)


def test_native_approval_failure_rolls_back_bank_customer_balance_and_review(monkeypatch):
    original=AsyncIOMotorCollection.insert_one
    async def fail(self,*args,**kwargs):
        if self.name==accountant_reviews.PAYMENT_EVENTS:
            raise RuntimeError('synthetic review audit failure')
        return await original(self,*args,**kwargs)
    async def scenario(h):
        order,assignment=await pending_noncash(h)
        before=await h.doc(order);count=await h.raw.general_ledger.count_documents({})
        ev=await h.evidence.upload(OWNER.tenant_id,OWNER.actor_id,kind='bank_receipt',content_type='image/png',data=h.png)
        monkeypatch.setattr(AsyncIOMotorCollection,'insert_one',fail)
        async with app_client(h) as client:
            with pytest.raises(RuntimeError,match='synthetic review audit failure'):
                await client.post(f'/store-delivery/payment-review/{assignment["id"]}',json={
                    'decision':'approved','movement':movement(ev,reference='FAIL-NATIVE-001')})
        assert await h.doc(order)==before
        assert await h.raw.general_ledger.count_documents({})==count
        assert await h.raw.account_transactions.count_documents({})==1
        assert (await h.raw[driver_app.DRIVER_PAYMENT_REVIEWS].find_one({}))['status']=='pending'
    run(scenario)


def test_native_rejected_receipt_resubmission_preserves_initial_proof_and_bank_replay():
    async def scenario(h):
        order,assignment=await pending_noncash(h)
        initial=await h.raw[driver_app.DRIVER_COLLECTIONS].find_one({})
        async with app_client(h) as client:
            rejected=await client.post(f'/store-delivery/payment-review/{assignment["id"]}',json={'decision':'rejected','note':'Wrong original slip'})
            assert rejected.status_code==200,rejected.text
        count=await h.raw.general_ledger.count_documents({})
        async with app_client(h,driver=True) as client:
            upload=await client.post('/store-delivery/evidence/receipt',data={'assignment_id':assignment['id']},
                files={'file':('replacement.png',h.png,'image/png')})
            assert upload.status_code==200,upload.text
            payload={'receipt_reference':upload.json()['receipt_reference'],'bank_account_id':'bank-demo'}
            path=f'/store-delivery/app/payment-review/{assignment["id"]}/resubmit'
            result=await client.post(path,json=payload)
            assert result.status_code==200,result.text
            replay=await client.post(path,json=payload)
            assert replay.status_code==200,replay.text
            assert replay.json()['revision']==result.json()['revision']==2
        assert await h.raw.general_ledger.count_documents({})==count
        assert (await h.integrated.get(OWNER,order['order_id']))['balances']['remaining_minor']==2000
        current=await h.raw[driver_app.DRIVER_COLLECTIONS].find_one({})
        assert current['original_receipt_reference']==initial['original_receipt_reference']
        assert current['receipt_reference']!=initial['receipt_reference']
        ev=await h.evidence.upload(OWNER.tenant_id,OWNER.actor_id,kind='bank_receipt',content_type='image/png',data=h.png)
        async with app_client(h) as client:
            approval=await client.post(f'/store-delivery/payment-review/{assignment["id"]}',json={
                'decision':'approved','movement':movement(ev,reference='RESUBMITTED-001')})
            assert approval.status_code==200,approval.text
        assert (await h.integrated.get(OWNER,order['order_id']))['balances']['remaining_minor']==0
        assert await gl_balance(h,'bank','bank-demo')==6000
    run(scenario)


def test_native_driver_permission_revoked_mid_request_is_rechecked(monkeypatch):
    from mezan_special_orders import delivery_bridge
    original=delivery_bridge.verify_local_assignment
    async def scenario(h):
        order=await prepared(h);assignment=await assign(h,order)
        # Model a stale authenticated session while persisted user is revoked.
        await h.raw.users.update_one({'id':'native-driver-user'},{'$set':{'disabled':True}})
        async with app_client(h,driver=True) as client:
            result=await client.post('/store-delivery/app/deliveries/status',json={
                'barcode':order['order_number'],'target_status':'out_for_delivery'})
            assert result.status_code==403,result.text
        assert (await h.raw[handover.ASSIGNMENTS].find_one({'id':assignment['id']}))['status']=='assigned'
        assert await h.raw.general_ledger.count_documents({})==0
    run(scenario)
