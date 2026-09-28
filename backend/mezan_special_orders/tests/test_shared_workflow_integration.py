"""Actual existing review/HTTP/source pipeline on a disposable replica set."""
import asyncio
import os
import httpx
import pytest
from fastapi import FastAPI
from mezan_special_orders.contracts import CreateOrder
from mezan_special_orders.domain import DomainError
from mezan_special_orders.integration import IntegratedOrders
from mezan_special_orders.tests.test_core import OWNER, request_data
from mezan_special_orders.tests.test_source_adapters import catalog_row, options
from mezan_special_orders.tests.test_financial_integration import run

URI=os.environ.get('MEZAN_SPECIAL_TEST_REPLICA_URI')
pytestmark=pytest.mark.skipif(not URI,reason='Dedicated disposable replica set required')


async def setup(h,purpose='creator',key='actual-source-creation',*,partial=False):
    await h.raw.users.update_one({'id':OWNER.tenant_id},{'$setOnInsert':{'role':'owner','name':'Synthetic owner'}},upsert=True)
    row=catalog_row()
    await h.raw.mezan_products_v2.update_one({'user_id':OWNER.tenant_id,'salla_product_id':'p-demo'}, {'$set':row},upsert=True)
    data=request_data(purpose,partial=partial);data['fx']['evidence_id']='sar-fixed-1';data['items'][0]['options']=[v.model_dump(mode='json') for v in options(multi=True)]
    h.integrated=IntegratedOrders(h.db)
    return await h.integrated.create(OWNER,CreateOrder.model_validate(data),key)


def test_actual_creation_commits_source_and_existing_review_membership_together():
    async def scenario(h):
        o=await setup(h)
        w=await h.raw.order_review_workflows.find_one({'order_number':o['order_number']})
        assert w['source_provider']=='mezan' and w['stage']=='pending_review' and w['revision']==0
        assert w['special_source_digest']==o['snapshot_digest']
        assert await h.raw.unified_orders.count_documents({})==0
        assert await h.raw.general_ledger.count_documents({})==0
        again=await setup(h)
        assert again['order_id']==o['order_id']
        assert await h.raw.order_review_workflows.count_documents({})==1
    run(scenario)


def test_actual_shared_review_skips_salla_and_keeps_all_current_customer_options(monkeypatch):
    import order_review_routes as review
    async def forbidden(*a,**k): raise AssertionError('Local order must never call Salla')
    monkeypatch.setattr(review,'call_salla',forbidden)
    async def scenario(h):
        o=await setup(h)
        user={'id':OWNER.tenant_id,'role':'owner','name':'Synthetic owner'}
        app=FastAPI();app.include_router(review.make_order_review_router(h.db,lambda:user))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic.test') as c:
            route='/order-reviews-v1/{order_number}/complete'
            path=route.replace('{order_number}',o['order_number'])
            response=await c.post(path,json={'expected_revision':0})
            assert response.status_code==200,response.text
            assert response.json()['salla_status_sync']=='not_applicable'
        doc=await h.doc(o); w=await h.raw.order_review_workflows.find_one({'order_number':o['order_number']})
        assert doc['source_frozen'] is True and w['stage']=='reviewed'
        assert w['special_source_digest']==doc['snapshot_digest']
        assert len(w['items'][0]['options'])==3
        assert 'تغليف' in str(w['items'][0]['options']) and 'بطاقة' in str(w['items'][0]['options'])
        assert (await h.integrated.get(OWNER,o['order_id']))['stage']=='reviewed'
        assert await h.raw.unified_orders.count_documents({})==0
    run(scenario)


def test_pending_option_amend_updates_shared_source_proof_and_invalidates_old_review_revision():
    import order_review_routes as review
    async def scenario(h):
        o=await setup(h)
        o=await h.integrated.command(OWNER,o['order_id'],o['revision'],'amend-local-options','amend_options',
            {'line_key':'line-1','options':[v.model_dump(mode='json') for v in options(size='56',multi=True)]})
        w=await h.raw.order_review_workflows.find_one({'order_number':o['order_number']})
        assert w['revision']==1 and w['special_source_revision']==2 and w['special_source_digest']==o['snapshot_digest']
        app=FastAPI();app.include_router(review.make_order_review_router(h.db,lambda:{'id':OWNER.tenant_id,'role':'owner'}))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic.test') as c:
            path='/order-reviews-v1/{order_number}/complete'.replace('{order_number}',o['order_number'])
            failed=await c.post(path,json={'expected_revision':0});assert failed.status_code==409,failed.text
            good=await c.post(path,json={'expected_revision':1});assert good.status_code==200,good.text
        doc=await h.doc(o);assert doc['source_frozen'] is True
        with pytest.raises(DomainError,match='options_frozen'):
            await h.integrated.command(OWNER,o['order_id'],doc['revision'],'late-option-edit','amend_options',
                {'line_key':'line-1','options':[v.model_dump(mode='json') for v in options()]})
    run(scenario)


def test_transaction_context_is_task_local_and_failure_rolls_back_source_freeze(monkeypatch):
    import order_review_routes as review
    from motor.motor_asyncio import AsyncIOMotorCollection
    original=AsyncIOMotorCollection.replace_one
    async def fail(self,selector,replacement,*args,**kwargs):
        if self.name=='order_review_workflows' and replacement.get('stage')=='reviewed':
            raise RuntimeError('Synthetic persistence fault')
        return await original(self,selector,replacement,*args,**kwargs)
    monkeypatch.setattr(AsyncIOMotorCollection,'replace_one',fail)
    async def scenario(h):
        o=await setup(h)
        app=FastAPI();app.include_router(review.make_order_review_router(h.db,lambda:{'id':OWNER.tenant_id,'role':'owner'}))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic.test') as c:
            path='/order-reviews-v1/{order_number}/complete'.replace('{order_number}',o['order_number'])
            with pytest.raises(RuntimeError,match='Synthetic persistence fault'):
                await c.post(path,json={'expected_revision':0})
        assert h.db.session is None
        doc=await h.doc(o);assert doc['source_frozen'] is False and doc['revision']==1
        w=await h.raw.order_review_workflows.find_one({'order_number':o['order_number']});assert w['stage']=='pending_review'
    run(scenario)
