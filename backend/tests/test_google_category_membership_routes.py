from copy import deepcopy
from datetime import datetime
import pytest
from fastapi import APIRouter,FastAPI
from httpx import ASGITransport,AsyncClient
from integrations_control_center.google_category_membership_routes import attach_google_category_membership_routes
from integrations_control_center.google_category_membership_reader import read_category_memberships
from tests.test_google_category_membership import NOW,product

class Cursor:
    def __init__(self,rows):self.rows=rows
    def hint(self,key):assert key==[('user_id',1),('salla_product_id',1)];return self
    def limit(self,n):assert 1<=n<=101;return self
    def max_time_ms(self,n):assert n==2000;return self
    async def to_list(self,length):return deepcopy(self.rows[:length])
class Products:
    def __init__(self):self.rows=[product()];self.query=None;self.fail=False
    def find(self,query,projection):
        self.query=query
        if self.fail:raise RuntimeError('private sentinel')
        return Cursor(self.rows)
class Plans:
    def __init__(self):self.calls=0;self.mutate=None;self.row={'user_id':'owner','plan_id':'winter','revision':1,'fields':{'pinned_product_ids':['123','456'],'excluded_product_ids':['456'],'category_ids':['8'],'category_url':None}}
    async def find_one(self,query,projection):
        self.calls+=1
        if self.calls==2 and self.mutate:self.mutate(self.row)
        return deepcopy(self.row) if all(self.row.get(k)==v for k,v in query.items()) else None
class Users:
    async def find_one(self,*args,**kwargs):return None
class Db:
    def __init__(self):self.products=Products();self.plans=Plans();self.users=Users()
    def __getitem__(self,key):return self.plans if key=='mezan_google_commercial_plans_v1' else self.products
async def call(db,user=None,path='/google_ads/commercial-plans/winter/category-review?expected_revision=1',actual=False,monkeypatch=None):
    app=FastAPI();router=APIRouter()
    async def current():return user or {'id':'owner','role':'owner'}
    if actual:
        from integrations_control_center import google_category_membership_routes as module
        from integrations_control_center.google_ads_routes import attach_google_ads_management_routes
        class FixedDatetime(datetime):
            @classmethod
            def now(cls,tz=None):return NOW
        monkeypatch.setattr(module,'datetime',FixedDatetime)
        class NoProvider:
            def __init__(self,*a,**kw):raise AssertionError('provider must not be called')
        attach_google_ads_management_routes(router,db,current,client_factory=NoProvider)
        assert sum(r.path=='/google_ads/commercial-plans/{plan_id}/category-review' for r in router.routes)==1
        assert any(r.path.endswith('/store-observations') for r in router.routes)
    else:attach_google_category_membership_routes(router,db,current,clock=lambda:NOW)
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:return await client.get(path)

@pytest.mark.asyncio
async def test_actual_router_binds_owner_revision_categories_and_excludes_products(monkeypatch):
    db=Db();r=await call(db,actual=True,monkeypatch=monkeypatch);assert r.status_code==200
    data=r.json();assert data['product_ids_reviewed']==['123'] and data['review']['products'][0]['matched_category_ids']==['8']
    assert db.products.query['user_id']=='owner' and db.products.query['salla_product_id']=={'$in':['123']}
    assert data['execution_authorized'] is False and data['provider_writes']==0

@pytest.mark.asyncio
@pytest.mark.parametrize('mutation',[lambda r:r.update(revision=2),lambda r:r['fields'].update(category_ids=['9'])])
async def test_revision_and_same_revision_scope_race_rejected(mutation):
    db=Db();db.plans.mutate=mutation;assert (await call(db)).status_code==409

@pytest.mark.asyncio
async def test_foreign_plan_staff_and_disabled_owner_rejected():
    assert (await call(Db(),{'id':'foreign','role':'owner'})).status_code==404
    assert (await call(Db(),{'id':'staff','role':'staff'})).status_code==403
    assert (await call(Db(),{'id':'owner','role':'owner','disabled':True})).status_code==403

@pytest.mark.asyncio
async def test_missing_index_or_duplicate_identity_fails_closed_without_private_error():
    db=Db();db.products.fail=True;r=await call(db);assert r.status_code==503 and 'sentinel' not in r.text
    db=Db();db.products.rows.append(deepcopy(db.products.rows[0]));assert (await call(db)).status_code==503

@pytest.mark.asyncio
async def test_empty_category_scope_or_pins_skips_source_read_and_does_not_infer_url():
    for key in ('category_ids','pinned_product_ids'):
        db=Db();db.plans.row['fields'][key]=[];db.plans.row['fields']['category_url']='https://store.example/c/winter'
        r=await call(db);assert r.status_code==200 and db.products.query is None
        assert all(p['membership']=='unproved' for p in r.json()['review']['products'])

@pytest.mark.asyncio
@pytest.mark.parametrize('field,value',[('category_ids',['008']),('category_ids',['8','8']),('pinned_product_ids',['bad']),('category_url','http://bad.example')])
async def test_invalid_saved_scope_never_reads_catalog(field,value):
    db=Db();db.plans.row['fields'][field]=value;r=await call(db)
    assert r.status_code in (422,503) and db.products.query is None

@pytest.mark.asyncio
async def test_client_owner_and_clock_cannot_change_bound_read():
    r=await call(Db(),path='/google_ads/commercial-plans/winter/category-review?expected_revision=1&owner_id=foreign&as_of=2000-01-01')
    assert r.status_code==200 and r.json()['observed_at']==NOW.isoformat()
