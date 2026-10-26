from copy import deepcopy
from datetime import datetime, timezone
import pytest
from fastapi import FastAPI, APIRouter
from httpx import ASGITransport, AsyncClient
from integrations_control_center.google_product_catalog_routes import attach_google_product_catalog_review_routes

class Cursor:
    def __init__(self, rows): self.rows=rows
    async def to_list(self,length): return self.rows[:length]
class Plans:
    def __init__(self, row, mutate=False): self.row=row; self.mutate=mutate; self.calls=0
    async def find_one(self, query, projection):
        self.calls+=1
        if self.mutate and self.calls==2: self.row['revision']+=1
        return deepcopy(self.row) if self.row and all(self.row.get(k)==v for k,v in query.items()) else None
class Products:
    def __init__(self,rows): self.rows=rows; self.query=None
    def find(self, query, projection):
        self.query=query
        return Cursor([deepcopy(r) for r in self.rows if r['user_id']==query['user_id'] and r['salla_product_id'] in query['salla_product_id']['$in']])
class Db:
    def __init__(self,owner='owner', mutate=False):
        self.plan=Plans({'user_id':owner,'plan_id':'winter','revision':1,'fields':{'instruction':'شتاء','pinned_product_ids':['123','456'],'excluded_product_ids':['456']}},mutate)
        self.products=Products([{'user_id':owner,'salla_product_id':'123','mezan_product_id':'mpv2_123','source':'salla','source_revision':'a'*64,'last_synced_at':datetime.now(timezone.utc),'status':'active','archived':False,'quantity':1,'variants_count':0}, {'user_id':'other','salla_product_id':'123','mezan_product_id':'mpv2_123','source':'salla','source_revision':'b'*64,'last_synced_at':datetime.now(timezone.utc),'status':'inactive','archived':False}])
    def __getitem__(self,key): return self.plan if key=='mezan_google_commercial_plans_v1' else self.products
async def call(db,user,path='/google_ads/commercial-plans/winter/catalog-review?expected_revision=1'):
    app=FastAPI(); router=APIRouter()
    async def current_user(): return user
    attach_google_product_catalog_review_routes(router,db,current_user);app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client: return await client.get(path)
@pytest.mark.asyncio
async def test_owner_pinned_and_excluded_scope_and_server_time():
    db=Db(); before=datetime.now(timezone.utc); r=await call(db,{'id':'owner','role':'owner'}); after=datetime.now(timezone.utc)
    assert r.status_code==200; data=r.json(); assert before <= datetime.fromisoformat(data['observed_at']) <= after
    assert db.products.query=={'user_id':'owner','salla_product_id':{'$in':['123']}}
    assert data['pinned_product_ids_reviewed']==['123'] and data['catalog']['products'][0]['status']=='unknown'
    assert data['execution_authorized'] is False and data['provider_writes']==0
@pytest.mark.asyncio
async def test_owner_cannot_read_another_owner_plan():
    assert (await call(Db(owner='other'),{'id':'owner','role':'owner'})).status_code==404
@pytest.mark.asyncio
async def test_revision_change_during_read_rejected():
    assert (await call(Db(mutate=True),{'id':'owner','role':'owner'})).status_code==409
@pytest.mark.asyncio
async def test_wrong_revision_and_invalid_product_are_rejected():
    db=Db(); assert (await call(db,{'id':'owner','role':'owner'},'/google_ads/commercial-plans/winter/catalog-review?expected_revision=2')).status_code==409
    db.plan.row['fields']['pinned_product_ids']=['mpv2_123']; assert (await call(db,{'id':'owner','role':'owner'})).status_code==422
@pytest.mark.asyncio
async def test_disabled_owner_rejected():
    assert (await call(Db(),{'id':'owner','role':'owner','disabled':True})).status_code==403
@pytest.mark.asyncio
async def test_same_revision_field_tampering_during_read_rejected():
    db=Db()
    original=db.plan.find_one
    async def changed(query, projection):
        if db.plan.calls==1: db.plan.row['fields']['instruction']='changed'
        return await original(query,projection)
    db.plan.find_one=changed
    assert (await call(db,{'id':'owner','role':'owner'})).status_code==409
@pytest.mark.asyncio
async def test_empty_selection_skips_catalog_and_extra_inputs_do_not_control_scope_or_time():
    db=Db();db.plan.row['fields']['pinned_product_ids']=[]
    result=await call(db,{'id':'owner','role':'owner'},'/google_ads/commercial-plans/winter/catalog-review?expected_revision=1&owner_id=other&as_of=2020-01-01')
    assert result.status_code==200 and result.json()['catalog']['products']==[] and db.products.query is None
    assert datetime.fromisoformat(result.json()['observed_at']).year != 2020
@pytest.mark.asyncio
async def test_non_owner_is_rejected_even_with_ad_permissions(monkeypatch):
    from integrations_control_center import google_product_catalog_routes as routes
    async def staff(*args,**kwargs): return {'owner':False,'user_id':'owner','actor_id':'staff'}
    monkeypatch.setattr(routes,'principal',staff)
    assert (await call(Db(),{'id':'staff','role':'viewer'})).status_code==403
