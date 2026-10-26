"""Real replica tests for owner-scoped pinned product route."""
from datetime import datetime, timezone
import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from backend.tests.test_google_product_catalog_reader_mongo import replica_db
from integrations_control_center import google_product_catalog_routes as routes

async def setup_data(db):
    await db.mezan_google_commercial_plans_v1.insert_one({'user_id':'owner','plan_id':'winter','revision':1,
        'fields':{'instruction':'winter','pinned_product_ids':['123'],'excluded_product_ids':[]}})
    await db.mezan_products_v2.insert_many([
        {'user_id':'owner','salla_product_id':'123','mezan_product_id':'mpv2_123','source':'salla',
         'source_revision':'a'*64,'last_synced_at':datetime.now(timezone.utc),'status':'active','archived':False},
        {'user_id':'other','salla_product_id':'123','mezan_product_id':'mpv2_123','source':'salla',
         'source_revision':'b'*64,'last_synced_at':datetime.now(timezone.utc),'status':'inactive','archived':False}])
    await db.users.insert_many([{'id':'owner','role':'owner'}, {'id':'staff','role':'viewer','created_by':'owner'}])

async def call(db,user):
    async def current_user(): return user
    app=FastAPI();router=APIRouter();routes.attach_google_product_catalog_review_routes(router,db,current_user);app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        return await client.get('/google_ads/commercial-plans/winter/catalog-review?expected_revision=1')

@pytest.mark.asyncio
async def test_real_replica_revision_changes_during_catalog_read_fail_closed(replica_db,monkeypatch):
    await setup_data(replica_db)
    original=routes.read_catalog_products
    async def race(db,**kwargs):
        result=await original(db,**kwargs)
        changed=await db.mezan_google_commercial_plans_v1.update_one({'user_id':'owner','plan_id':'winter','revision':1},{'$set':{'revision':2}})
        assert changed.modified_count==1
        return result
    monkeypatch.setattr(routes,'read_catalog_products',race)
    response=await call(replica_db,{'id':'owner','role':'owner'})
    assert response.status_code==409 and response.json()['detail']['code']=='google_commercial_plan_revision_conflict'

@pytest.mark.asyncio
async def test_real_replica_tenant_and_staff_do_not_read_owner_plan(replica_db):
    await setup_data(replica_db)
    assert (await call(replica_db,{'id':'other','role':'owner'})).status_code==404
    assert (await call(replica_db,{'id':'staff','role':'viewer'})).status_code==403
    response=await call(replica_db,{'id':'owner','role':'owner'})
    assert response.status_code==200
    data=response.json(); assert len(data['catalog']['products'])==1
    assert data['catalog']['products'][0]['status']=='unknown'
    assert data['execution_authorized'] is False and data['provider_writes']==0
