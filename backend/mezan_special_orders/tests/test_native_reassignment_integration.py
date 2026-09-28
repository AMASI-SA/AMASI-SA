"""Real native reassignment preserves local source and per-driver fee ownership."""
import os
import httpx
import pytest
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorCollection
from mezan_special_orders.tests.test_core import OWNER
from mezan_special_orders.tests.test_financial_integration import run,gl_balance
from mezan_special_orders.tests.test_native_delivery_integration import prepared,assign,pick_up
import store_delivery_driver_app_routes as driver_app
import store_delivery_reassignment_routes as reassign
import store_delivery_handover_routes as handover

pytestmark=pytest.mark.skipif(not os.getenv('MEZAN_SPECIAL_TEST_REPLICA_URI'),reason='Dedicated replica set required')


def client(h,*,new_driver=False):
    user=({'id':'new-driver-user','role':'store_driver','created_by':OWNER.tenant_id} if new_driver
        else {'id':OWNER.tenant_id,'role':'owner'})
    app=FastAPI()
    app.include_router(reassign.make_store_delivery_reassignment_router(h.db,lambda:user))
    app.include_router(driver_app.make_store_delivery_driver_app_router(h.db,lambda:user))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic.test')


async def second_driver(h):
    row=await h.raw.store_drivers.find_one({'id':'native-driver'},{'_id':0})
    row.update(id='new-driver',account_user_id='new-driver-user',delivery_fee=25,name='New synthetic driver')
    await h.raw.users.insert_one({'id':'new-driver-user','role':'store_driver','created_by':OWNER.tenant_id})
    await h.raw.store_drivers.insert_one(row)


@pytest.mark.parametrize('picked_up',[False,True])
def test_local_reassignment_before_delivery_uses_new_driver_fee_and_keeps_old_history(picked_up):
    async def scenario(h):
        order=await prepared(h);old=await assign(h,order);await second_driver(h)
        if picked_up:await pick_up(h,order)
        source=await h.doc(order)
        path=f'/store-delivery/assignments/{old["id"]}/reassign'
        payload={'driver_id':'new-driver','reason':'Operator approved route handover'}
        async with client(h) as c:
            response=await c.post(path,json=payload)
            assert response.status_code==200,response.text
            new=response.json()['assignment']
            again=await c.post(path,json=payload)
            assert again.status_code==200,again.text
            assert again.json()['assignment']['id']==new['id']
            listed=await c.get('/store-delivery/assignments')
            assert listed.status_code==200,listed.text
            assert listed.json()['items'][0]['order_found'] is True
        assert (await h.raw[handover.ASSIGNMENTS].find_one({'id':old['id']}))['active'] is False
        assert (await h.raw[handover.ASSIGNMENTS].find_one({'id':old['id']}))['delivery_fee_snapshot']==20
        assert new['delivery_fee_snapshot']==25
        assert (await h.doc(order))['snapshot_digest']==source['snapshot_digest']
        async with client(h,new_driver=True) as c:
            for stage in ['out_for_delivery','delivered']:
                response=await c.post('/store-delivery/app/deliveries/status',json={'barcode':order['order_number'],'target_status':stage})
                assert response.status_code==200,response.text
        assert await gl_balance(h,'store_driver','native-driver','delivery_fee_payable')==0
        assert await gl_balance(h,'store_driver','new-driver','delivery_fee_payable')==-2500
        assert await h.raw[driver_app.DRIVER_EARNINGS].count_documents({})==1
        assert await h.raw.unified_orders.count_documents({})==0
    run(scenario)


def test_reassignment_audit_failure_restores_original_driver_and_workflow(monkeypatch):
    original=AsyncIOMotorCollection.insert_one
    async def fail(self,*args,**kwargs):
        if self.name==handover.EVENTS:raise RuntimeError('synthetic reassignment audit failure')
        return await original(self,*args,**kwargs)
    async def scenario(h):
        order=await prepared(h);old=await assign(h,order);await second_driver(h);await pick_up(h,order)
        before=await h.raw.order_review_workflows.find_one({'order_number':order['order_number']},{'_id':0})
        monkeypatch.setattr(AsyncIOMotorCollection,'insert_one',fail)
        async with client(h) as c:
            with pytest.raises(RuntimeError,match='synthetic reassignment audit failure'):
                await c.post(f'/store-delivery/assignments/{old["id"]}/reassign',json={'driver_id':'new-driver'})
        after=await h.raw.order_review_workflows.find_one({'order_number':order['order_number']},{'_id':0})
        assert after==before
        assert await h.raw[handover.ASSIGNMENTS].count_documents({})==1
        assert (await h.raw[handover.ASSIGNMENTS].find_one({}))['active'] is True
        assert await h.raw.general_ledger.count_documents({})==0
    run(scenario)
