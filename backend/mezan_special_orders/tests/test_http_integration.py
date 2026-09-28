"""Concrete API: fresh principals and private files on an actual replica set."""
import os
from contextlib import asynccontextmanager
import httpx
import pytest
from fastapi import FastAPI
from mezan_special_orders.http import make_integrated_special_orders_router
from mezan_special_orders.binding import SpecialOrdersDatabase,Enablement
from mezan_special_orders.tests.test_financial_integration import run
from mezan_special_orders.tests.test_core import OWNER,request_data
from mezan_special_orders.tests.test_source_adapters import catalog_row,options

pytestmark=pytest.mark.skipif(not os.environ.get('MEZAN_SPECIAL_TEST_REPLICA_URI'),reason='Dedicated replica set required')

@asynccontextmanager
async def api(h,user=None):
    await h.raw.users.update_one({'id':OWNER.tenant_id},{'$set':{'id':OWNER.tenant_id,'role':'owner','is_active':True}},upsert=True)
    app=FastAPI();app.include_router(make_integrated_special_orders_router(h.db,lambda:user or {'id':OWNER.tenant_id}),prefix='/api')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic.test') as c:yield c

async def create(c,h):
    await h.raw.mezan_products_v2.update_one({'user_id':OWNER.tenant_id,'salla_product_id':'p-demo'},{'$set':catalog_row()},upsert=True)
    data=request_data('creator');data['fx']['evidence_id']='sar-fixed-1';data['items'][0]['options']=[v.model_dump(mode='json') for v in options(multi=True)]
    response=await c.post('/api/special-orders-v1',json=data,headers={'Idempotency-Key':'real-http-create'})
    assert response.status_code==201,response.text
    return response.json()


def test_concrete_http_creation_replay_and_provisional_costs():
    async def scenario(h):
        async with api(h) as c:
            o=await create(c,h);again=await create(c,h);assert again['order_id']==o['order_id']
            r=await c.get('/api/special-orders-v1');assert r.status_code==200,r.text
            assert r.json()['items'][0]['stage']=='pending_review' and 'receipt_claims' not in r.json()['items'][0]
            detail=await c.get('/api/special-orders-v1/'+o['order_id']);assert detail.status_code==200,detail.text
            assert detail.json()['cost_report']['provisional'] is True
        assert await h.raw.unified_orders.count_documents({})==0 and await h.raw.general_ledger.count_documents({})==0
    run(scenario)


def policy(h):
    return {'effective_at':'2026-09-27T00:00:00+00:00','classification':'expense_recovery','tax_basis_points':0,
        'tax_treatment':'out_of_scope','evidence':h.policy_ev.model_dump(mode='json'),'reason':'Synthetic declared classification'}


def test_forged_stale_owner_session_cannot_override_persisted_employee():
    async def scenario(h):
        await h.raw.users.insert_one({'id':'staff-test','role':'employee','created_by':OWNER.tenant_id,'is_active':True})
        async with api(h,{'id':'staff-test','role':'owner','is_owner':True}) as c:
            r=await c.post('/api/special-orders-v1/accounting-policies',json=policy(h));assert r.status_code==403,r.text
        assert await h.raw.mezan_special_order_accounting_policies_v1.count_documents({})==1
    run(scenario)


def test_uploaded_receipt_is_private_and_never_posts_cash():
    async def scenario(h):
        for staff in ('staff-one','staff-two'):
            await h.raw.users.insert_one({'id':staff,'role':'employee','created_by':OWNER.tenant_id,'is_active':True})
            await h.raw.mezan_special_order_access_v1.insert_one({'tenant_id':OWNER.tenant_id,'actor_id':staff,'status':'active','permissions':['special_orders.receipt_upload','special_orders.read']})
        async with api(h,{'id':'staff-one'}) as c:
            r=await c.post('/api/special-orders-v1/evidence',data={'kind':'bank_receipt'},files={'file':('receipt.png',h.png,'image/png')})
            assert r.status_code==201,r.text
            e=r.json();r=await c.get('/api/special-orders-v1/evidence/'+e['object_id'])
            assert r.status_code==200 and r.content==h.png and r.headers['cache-control']=='no-store, private'
        async with api(h,{'id':'staff-two'}) as c:
            r=await c.get('/api/special-orders-v1/evidence/'+e['object_id']);assert r.status_code==403,r.text
        assert await h.raw.general_ledger.count_documents({})==0 and await h.raw.account_transactions.count_documents({})==0
    run(scenario)


def test_owner_policy_and_fx_approval_are_immutable():
    async def scenario(h):
        async with api(h) as c:
            body=policy(h)
            for _ in range(2):
                r=await c.post('/api/special-orders-v1/accounting-policies',json=body);assert r.status_code==201,r.text
            body['classification']='other_income';r=await c.post('/api/special-orders-v1/accounting-policies',json=body);assert r.status_code==409,r.text
            fx={'snapshot':{'currency':'QAR','rate_to_sar':'1.03','captured_at':'2026-09-27T00:00:00+00:00','evidence_id':'fx-proof-http'},
                'evidence':h.policy_ev.model_dump(mode='json'),'reason':'Synthetic FX approval'}
            r=await c.post('/api/special-orders-v1/fx-snapshots',json=fx);assert r.status_code==201,r.text
            fx['snapshot']['rate_to_sar']='1.05';r=await c.post('/api/special-orders-v1/fx-snapshots',json=fx);assert r.status_code==409,r.text
    run(scenario)


def test_default_factory_mounts_nothing_and_merchant_allowlist_is_enforced():
    async def scenario(h):
        assert not make_integrated_special_orders_router(h.raw,lambda:{}).routes
        assert not make_integrated_special_orders_router(SpecialOrdersDatabase(h.raw,Enablement()),lambda:{}).routes
        db=SpecialOrdersDatabase(h.raw,Enablement(True,True,True,True,tenants=frozenset({'another-store'})))
        await h.raw.users.insert_one({'id':OWNER.tenant_id,'role':'owner','is_active':True})
        app=FastAPI();app.include_router(make_integrated_special_orders_router(db,lambda:{'id':OWNER.tenant_id}))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic.test') as c:
            r=await c.get('/special-orders-v1');assert r.status_code==403,r.text
    run(scenario)


def test_http_cannot_inject_posted_proof_through_generic_observe_command():
    async def scenario(h):
        async with api(h) as c:
            o=await create(c,h)
            r=await c.post('/api/special-orders-v1/'+o['order_id']+'/commands',headers={'Idempotency-Key':'forged-ledger-proof'},
                json={'expected_revision':1,'operation':'observe_payment','payload':{'movement_id':'fake-posted'}})
            assert r.status_code==422,r.text
        assert await h.raw.general_ledger.count_documents({})==0
    run(scenario)
