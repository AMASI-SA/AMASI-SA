from copy import deepcopy
import pytest
from fastapi import APIRouter,FastAPI
from httpx import ASGITransport,AsyncClient
from integrations_control_center.google_plan_store_observations import attach_google_plan_store_observation_routes
from tests.test_google_store_product_observations import NOW,order

class Cursor:
    def __init__(self,rows,fail=False):self.rows=rows;self.fail=fail
    def hint(self,value):
        assert value==[('user_id',1),('order_date',-1)]
        if self.fail:raise RuntimeError('private sentinel token')
        return self
    def limit(self,value):assert value==1001;return self
    def max_time_ms(self,value):assert value==3000;return self
    async def to_list(self,length):return deepcopy(self.rows[:length])
class Orders:
    def __init__(self,rows):self.rows=rows;self.fail=False;self.query=None
    def find(self,query,projection):self.query=query;assert 'customer_name' not in projection;return Cursor(self.rows,self.fail)
class Catalog:
    def find(self,query,projection):return Cursor([{'user_id':'owner','salla_product_id':'123','mezan_product_id':'mpv2_123','source':'salla','source_revision':'a'*64,'last_synced_at':NOW,'status':'active','archived':False}])
class Plans:
    def __init__(self):self.row={'user_id':'owner','plan_id':'winter','revision':1,'fields':{'pinned_product_ids':['123','456'],'excluded_product_ids':['456']}};self.calls=0;self.mutate=None
    async def find_one(self,query,projection):
        self.calls+=1
        if self.calls==2 and self.mutate:self.mutate(self.row)
        return deepcopy(self.row) if all(self.row.get(k)==v for k,v in query.items()) else None
class Users:
    async def find_one(self,*args,**kwargs):return None
class Db:
    def __init__(self,rows=None):self.users=Users();self.plans=Plans();self.orders=Orders([order()] if rows is None else rows)
    def __getitem__(self,key):return {'mezan_google_commercial_plans_v1':self.plans,'unified_orders':self.orders,'mezan_products_v2':Catalog()}[key]
async def call(db,user=None,path='/google_ads/commercial-plans/winter/store-observations?expected_revision=1'):
    app=FastAPI();router=APIRouter()
    async def current():return user or {'id':'owner','role':'owner'}
    attach_google_plan_store_observation_routes(router,db,current,clock=lambda:NOW);app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:return await client.get(path)

@pytest.mark.asyncio
async def test_explicit_owner_review_reports_partial_units_and_pure_builder_unproven():
    db=Db();r=await call(db);assert r.status_code==200
    data=r.json();assert data['product_ids_reviewed']==['123']
    assert data['observations']['products'][0]['currency_groups'][0]['windows']['last_14_days']['observed_units']=='2'
    p=data['evidence']['products'][0]
    assert p['windows']['last_14_days']['availability']=='unproven' and p['windows']['last_14_days']['rows']==[]
    assert p['growth_14_days_percent'] is None and p['profitability'] is None
    assert db.orders.query['order_date']=={'$gte':'2026-07-30','$lte':'2026-10-29'}
    assert data['execution_authorized'] is False

@pytest.mark.asyncio
@pytest.mark.parametrize('mutate',[lambda r:r.update(revision=2),lambda r:r['fields'].update(pinned_product_ids=[])])
async def test_plan_changed_even_without_revision_bump_rejects(mutate):
    db=Db();db.plans.mutate=mutate;assert (await call(db)).status_code==409

@pytest.mark.asyncio
async def test_foreign_plan_and_staff_and_disabled_owner_cannot_read():
    assert (await call(Db(),{'id':'foreign','role':'owner'})).status_code==404
    assert (await call(Db(),{'id':'owner','role':'staff'})).status_code==403
    assert (await call(Db(),{'id':'owner','role':'owner','disabled':True})).status_code==403

@pytest.mark.asyncio
async def test_missing_index_fails_closed_and_sanitized():
    db=Db();db.orders.fail=True;r=await call(db);assert r.status_code==503
    assert 'sentinel' not in r.text

@pytest.mark.asyncio
async def test_empty_pins_skip_orders_and_client_scope_time_are_ignored():
    db=Db();db.plans.row['fields']['pinned_product_ids']=[]
    r=await call(db,path='/google_ads/commercial-plans/winter/store-observations?expected_revision=1&owner_id=foreign&as_of=2000-01-01')
    assert r.status_code==200 and db.orders.query is None
    assert r.json()['observations']['as_of']==NOW.isoformat()

@pytest.mark.asyncio
async def test_document_sentinel_suppresses_all_partial_counts():
    r=await call(Db([order() for _ in range(1001)]));assert r.status_code==200
    out=r.json()['observations'];assert out['truncated'] is True and out['products'][0]['currency_groups']==[]
    assert all(v is None for v in out['diagnostics'].values())

@pytest.mark.asyncio
async def test_reader_validates_scope_before_any_database_read():
    from integrations_control_center.google_plan_store_observations import read_store_observations
    for owner,ids in (({'$ne':None},['123']),('owner',['bad']),('owner',['123','123'])):
        db=Db()
        with pytest.raises(ValueError):await read_store_observations(db,owner_id=owner,product_ids=ids,as_of=NOW)
        assert db.orders.query is None

@pytest.mark.asyncio
async def test_actual_management_router_wires_owner_observations_once_without_provider_calls(monkeypatch):
    from datetime import datetime
    from integrations_control_center import google_plan_store_observations as module
    from integrations_control_center.google_ads_routes import attach_google_ads_management_routes
    class FixedDatetime(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(module,'datetime',FixedDatetime)
    class NoProvider:
        def __init__(self,*args,**kwargs):raise AssertionError('provider constructor must not be called')
    db=Db();app=FastAPI();router=APIRouter(prefix='/integrations-v2')
    async def owner():return {'id':'owner','role':'owner'}
    attach_google_ads_management_routes(router,db,owner,client_factory=NoProvider)
    path='/integrations-v2/google_ads/commercial-plans/{plan_id}/store-observations'
    assert sum(r.path==path for r in router.routes)==1
    assert any(r.path.endswith('/commercial-plan-suggestions') for r in router.routes)
    assert any(r.path.endswith('/catalog-review') for r in router.routes)
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        r=await client.get('/integrations-v2/google_ads/commercial-plans/winter/store-observations?expected_revision=1')
    assert r.status_code==200
    out=r.json()['observations'];assert out['products'][0]['currency_groups'][0]['windows']['last_14_days']['observed_units']=='2'
    assert out['execution_authorized'] is False and out['provider_writes']==0
