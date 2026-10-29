from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
import httpx
import pytest
from bson import BSON
from fastapi import APIRouter,FastAPI,Request
from pymongo.errors import DuplicateKeyError
from integrations_control_center.google_commercial_plan import PLANS,attach_google_commercial_plan_routes
from integrations_control_center.google_product_season_review import REVIEWS
from integrations_control_center.google_product_test_review_routes import attach_google_product_test_review_routes
from integrations_control_center.google_product_test_review_reader import ReviewAdmission
from tests.test_google_prior_season_routes import OwnerUsers
from tests.test_google_commercial_plan import Collection,body
from tests.test_google_native_inventory_reader import Native,Integration
from tests.test_google_plan_store_observations import Cursor as OrderCursor
from tests.test_google_store_product_observations import NOW,order
from tests.test_google_linked_campaign_reader import Collection as GraphCollection,LINKS

class Cursor:
    def __init__(self,rows):self.rows=rows
    def limit(self,value):return self
    def max_time_ms(self,value):return self
    async def to_list(self,length):return deepcopy(self.rows[:length])
class Reviews:
    def __init__(self):self.rows=[];self.race=False
    def find(self,query):return Cursor([r for r in self.rows if r['_id'] in query['_id']['$in']])
    async def insert_one(self,row):
        if any(r['_id']==row['_id'] for r in self.rows):raise DuplicateKeyError('synthetic')
        self.rows.append(deepcopy(row))
    async def find_one_and_replace(self,query,row,**kwargs):
        if self.race:return None
        for i,old in enumerate(self.rows):
            if all(old.get(k)==v for k,v in query.items() if k!='$expr') and type(old['revision']) is int:self.rows[i]=deepcopy(row);return deepcopy(row)
        return None
class Orders:
    def __init__(self,rows):self.rows=rows;self.queries=[]
    def find(self,query,projection):
        self.queries.append(deepcopy(query));period=query['order_date']
        return OrderCursor([r for r in self.rows if r.get('user_id')==query['user_id'] and period['$gte']<=r['order_date']<=period['$lte']])
class DB:
    def __init__(self):
        self.users=OwnerUsers();self.plans=Collection();self.reviews=Reviews();self.orders=Orders([]);self.salla_integrations=Integration();self.graph=GraphCollection([])
    def __getitem__(self,name):return {PLANS:self.plans,REVIEWS:self.reviews,'unified_orders':self.orders,LINKS:self.graph}[name]

def payload(**changes):return body(category_url=None,category_ids=['8'],pinned_product_ids=['1'],**changes).model_dump(mode='json')
def fit(**changes):return dict(expected_plan_revision=1,expected_review_revision=0,category_id='8',decision='suitable_for_test',reason='ملائم لهدايا الشتاء',acknowledge_owner_review=True)|changes
def app_for(monkeypatch,db,caller=None,**extra):
    import auth
    from integrations_control_center import google_commercial_plan as plans,google_native_inventory_reader as native
    class FixedDatetime(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(plans,'datetime',FixedDatetime);monkeypatch.setattr(native,'datetime',FixedDatetime)
    monkeypatch.setattr(auth,'get_jwt_secret',lambda:'synthetic-only');monkeypatch.setattr(auth.jwt,'decode',lambda *args,**kwargs:{'sub':'owner','type':'access','mfa':True,'iat':int(NOW.timestamp())})
    app,router=FastAPI(),APIRouter()
    async def user(request:Request):return await auth.get_current_user_from_db(request,db)
    attach_google_commercial_plan_routes(router,db,user)
    attach_google_product_test_review_routes(router,db,user,caller=caller or Native(),clock=lambda:NOW,admission=ReviewAdmission(),**extra)
    app.include_router(router);return app

def client(app):return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer synthetic-token'})
async def preview(c,**changes):return await c.post('/google_ads/commercial-plans/winter/product-test-review',json={'expected_revision':1,'category_id':'8','selected_product_ids':['1'],**changes})
async def save_fit(c,**changes):return await c.put('/google_ads/commercial-plans/winter/product-season-reviews/1',json=fit(**changes))
async def read_fit(c):return await c.get('/google_ads/commercial-plans/winter/product-season-reviews',params=[('expected_revision',1),('category_id','8'),('product_ids','1')])

@pytest.mark.asyncio
async def test_actual_http_plan_write_fit_cas_then_trusted_native_and_store_review(monkeypatch):
    db,native=DB(),Native();stored=order();stored['raw_by_source']['salla_direct']['items'][0]['product']['id']=1;db.orders.rows=[stored]
    async with client(app_for(monkeypatch,db,native)) as c:
        saved=await c.put('/google_ads/commercial-plans/winter',json=payload());assert saved.status_code==200
        assert 'acknowledge_review' not in saved.json()['fields']
        first=await preview(c);assert first.status_code==200,first.text
        assert first.json()['products'][0]['decision']=='needs_owner_fit_review'
        reviewed=await save_fit(c);assert reviewed.status_code==200,reviewed.text
        assert reviewed.json()['review_revision']==1
        assert (await save_fit(c)).status_code==409
        final=await preview(c);assert final.status_code==200,final.text
        row=final.json()['products'][0];assert row['decision']=='supported_test_review'
        assert row['current_store_observations']['currency_groups'][0]['windows']['last_14_days']['observed_units']=='2'
        assert row['profitability'] is None and row['paid_launch_eligible'] is False
        assert len(native.calls)==16 and (await read_fit(c)).json()['products'][0]['review_revision']==1
    assert len(db.plans.rows)==1 and len(db.reviews.rows)==1

@pytest.mark.asyncio
async def test_no_history_still_allows_owner_reviewed_exploration_without_claiming_new(monkeypatch):
    db=DB()
    async with client(app_for(monkeypatch,db)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload())).status_code==200
        assert (await save_fit(c)).status_code==200
        response=await preview(c)
    assert response.status_code==200,response.text
    row=response.json()['products'][0];assert row['decision']=='exploration_test_review'
    assert 'not_absence_of_sales' in row['history_status'] and row['approved_test_budget'].startswith('not_checked')

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['ack_missing','ack_false','extra_metric','duplicate','six','fake_actor'])
async def test_invalid_review_or_proof_input_rejected_before_native(monkeypatch,kind):
    db,native=DB(),Native()
    async with client(app_for(monkeypatch,db,native)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload())).status_code==200
        value=fit()
        if kind=='ack_missing':value.pop('acknowledge_owner_review')
        if kind=='ack_false':value['acknowledge_owner_review']=False
        if kind=='extra_metric':value['profitability']=1
        if kind in {'ack_missing','ack_false','extra_metric'}:result=await c.put('/google_ads/commercial-plans/winter/product-season-reviews/1',json=value)
        else:result=await preview(c,**({'selected_product_ids':['1','1']} if kind=='duplicate' else {'selected_product_ids':[str(i) for i in range(1,7)]} if kind=='six' else {'actor':{'owner':True}}))
    assert result.status_code==422 and native.calls==[] and db.reviews.rows==[]

@pytest.mark.asyncio
async def test_owner_review_password_change_invalidates_fit_but_exposes_revision_for_explicit_rereview(monkeypatch):
    db=DB()
    async with client(app_for(monkeypatch,db)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload())).status_code==200
        assert (await save_fit(c)).status_code==200
        db.users.rows[0]['password_updated_at']='2026-10-28T00:00:00Z'
        read=await read_fit(c);assert read.status_code==200,read.text
        row=read.json()['products'][0];assert row['decision']=='unknown' and row['review_revision']==1
        assert (await save_fit(c,expected_review_revision=1)).status_code==200
        assert (await read_fit(c)).json()['products'][0]['current_owner_review'] is True

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['disabled','role','deleted','password','bool_revision','float_revision','fields','review','connection'])
async def test_actual_auth_writer_preview_rejects_owner_plan_review_or_connection_race(monkeypatch,kind):
    from integrations_control_center import google_product_test_review_reader as reader
    from integrations_control_center.google_product_season_review import save_product_season_review,ReviewInput
    db=DB();original=reader.read_store_observations
    async def changing(*args,**kwargs):
        result=await original(*args,**kwargs)
        if kind=='disabled':db.users.rows[0]['disabled']=True
        if kind=='role':db.users.rows[0]['role']='staff'
        if kind=='deleted':db.users.rows.clear()
        if kind=='password':db.users.rows[0]['password_updated_at']='2026-10-29T12:01:00Z'
        if kind=='bool_revision':db.plans.rows[0]['revision']=True
        if kind=='float_revision':db.plans.rows[0]['revision']=1.0
        if kind=='fields':db.plans.rows[0]['fields']['message']='changed'
        if kind=='connection':db.salla_integrations.row['token_revision']='changed'
        if kind=='review':await save_product_season_review(db,authenticated_user={'id':'owner','role':'owner'},plan_id='winter',product_id='1',body=ReviewInput.model_validate(fit(expected_review_revision=1)),clock=lambda:NOW)
        return result
    monkeypatch.setattr(reader,'read_store_observations',changing)
    async with client(app_for(monkeypatch,db)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload())).status_code==200
        assert (await save_fit(c)).status_code==200
        result=await preview(c)
    assert result.status_code==({'disabled':403,'role':403,'deleted':403,'password':403,'bool_revision':409,'float_revision':409,'fields':409}.get(kind,503)),result.text
    assert 'products' not in result.json()

@pytest.mark.asyncio
async def test_review_write_may_persist_before_final_owner_denial_and_never_auto_retries(monkeypatch):
    db=DB();original=db.reviews.insert_one
    async def lost(row):
        await original(row);db.users.rows[0]['disabled']=True
    db.reviews.insert_one=lost
    async with client(app_for(monkeypatch,db)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload())).status_code==200
        result=await save_fit(c)
    assert result.status_code==403 and result.json()['detail']['review_may_have_persisted'] is True
    assert len(db.reviews.rows)==1 and db.reviews.rows[0]['revision']==1

@pytest.mark.asyncio
async def test_prior_query_is_separate_and_four_current_windows_are_unchanged(monkeypatch):
    from tests.test_google_prior_season_context import context
    from tests.test_google_prior_season_observations import prior_order
    db=DB();past=prior_order();past['raw_by_source']['salla_direct']['items'][0]['product']['id']=1;db.orders.rows=[past]
    async with client(app_for(monkeypatch,db)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload(prior_season_context=context()))).status_code==200
        assert (await save_fit(c)).status_code==200
        response=await preview(c)
    assert response.status_code==200,response.text
    data=response.json();assert len(data['current_windows'])==4 and len(data['prior_windows'])==1
    assert db.orders.queries[0]['order_date']!=db.orders.queries[1]['order_date']
    assert db.orders.queries[1]['order_date']=={'$gte':'2025-10-31','$lte':'2026-01-01'}
    assert data['products'][0]['evidence_reasons']==['qualifying_saved_prior_gross_lines_alignment_unknown']

@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['preview','state','save'])
@pytest.mark.parametrize('mutation',['disabled','password'])
async def test_owner_drift_during_last_review_read_is_caught_before_response(monkeypatch,operation,mutation):
    from integrations_control_center import google_product_season_review as fits,google_product_test_review_reader as reader
    db=DB()
    async with client(app_for(monkeypatch,db)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload())).status_code==200
        assert (await save_fit(c)).status_code==200
        target=reader if operation=='preview' else fits;original=target.read_reviews;calls=0
        async def changing(*args,**kwargs):
            nonlocal calls
            rows=await original(*args,**kwargs);calls+=1
            if calls==2:db.users.rows[0].update({'disabled':True} if mutation=='disabled' else {'password_updated_at':'2026-10-29T12:01:00Z'})
            return rows
        monkeypatch.setattr(target,'read_reviews',changing)
        response=await preview(c) if operation=='preview' else await read_fit(c) if operation=='state' else await save_fit(c,expected_review_revision=1)
    assert response.status_code==403,response.text
    if operation=='save':assert response.json()['detail']['review_may_have_persisted'] is True and db.reviews.rows[0]['revision']==2
