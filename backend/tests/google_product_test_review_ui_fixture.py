"""Synthetic actual management HTTP plan/fit writer -> candidate UI contract."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from datetime import datetime
import httpx
from pytest import MonkeyPatch
from fastapi import APIRouter,FastAPI,Request
from integrations_control_center.google_ads_routes import attach_google_ads_management_routes
from integrations_control_center import google_commercial_plan as plans,google_native_inventory_reader as native_reader,google_live_category_reader as category_reader
from integrations_control_center.google_product_test_review_routes import attach_google_product_test_review_routes
from integrations_control_center.google_live_category_routes import attach_google_live_category_routes
from integrations_control_center.google_product_test_review_reader import ReviewAdmission
from tests.test_google_product_test_review_routes import DB,Native,payload,fit,NOW,Orders
from tests.test_google_product_test_review_mongo import source
from tests.test_google_native_inventory_reader import page,envelope
from tests.test_google_linked_campaign_reader import database,event
from tests.test_google_prior_season_context import context

TARGET=Path(__file__).resolve().parents[2]/'frontend/src/services/__fixtures__/googleProductTestReview.json'
class FixtureDB(DB):
    def __init__(self):
        super().__init__();self.evidence=database([event('1'),event('2'),event('2',provider='meta',account_id='meta-account',campaign_id='meta-campaign',event_id='meta-event',association_key='meta-key')])
    def __getitem__(self,name):return self.evidence[name] if name in self.evidence else super().__getitem__(name)

def app_for(patches,db,native):
    import auth
    class FixedDatetime(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    patches.setattr(plans,'datetime',FixedDatetime);patches.setattr(native_reader,'datetime',FixedDatetime);patches.setattr(category_reader,'datetime',FixedDatetime)
    patches.setitem(attach_google_product_test_review_routes.__kwdefaults__,'caller',native)
    patches.setitem(attach_google_product_test_review_routes.__kwdefaults__,'clock',lambda:NOW)
    patches.setitem(attach_google_product_test_review_routes.__kwdefaults__,'admission',ReviewAdmission())
    patches.setitem(attach_google_live_category_routes.__kwdefaults__,'caller',native)
    patches.setattr(auth,'get_jwt_secret',lambda:'synthetic-only')
    patches.setattr(auth.jwt,'decode',lambda *args,**kwargs:{'sub':'owner','type':'access','mfa':True,'iat':int(NOW.timestamp())})
    async def current(request:Request):return await auth.get_current_user_from_db(request,db)
    class NoAdsProvider:
        def __init__(self,*args,**kwargs):raise AssertionError('ads provider must not be constructed')
    app,router=FastAPI(),APIRouter(prefix='/integrations-v2')
    attach_google_ads_management_routes(router,db,current,client_factory=NoAdsProvider,ai_client_factory=NoAdsProvider)
    assert sum(r.path.endswith('/product-test-review') for r in router.routes)==1
    assert sum(r.path.endswith('/product-season-reviews') for r in router.routes)==1
    assert sum(r.path.endswith('/product-season-reviews/{product_id}') for r in router.routes)==1
    app.include_router(router);return app

async def generate():
    patches=MonkeyPatch()
    try:
        db,native=FixtureDB(),Native()
        native.responses['/categories/8']['data']['urls']={'customer':'https://store.test/winter'}
        native.responses['/categories/8/products']=page([{'id':1},{'id':2},{'id':3,'status':'hidden'}])
        native.responses['/products/2']=envelope({'id':2,'status':'sale','is_available':True,'quantity':0,'unlimited_quantity':True,'options':[],'skus':[],'urls':{'customer':'https://store.test/item-2'}})
        native.responses['/products/2/variants']=page([])
        first=source();second=source('USD',11);second['raw_by_source']['salla_direct']['items'][0]['quantity']='1'
        prior=source(identity=12);prior['order_date']='2025-11-01';prior['raw_by_source']['salla_direct']['date']='2025-11-01T10:00:00Z';prior['raw_by_source']['salla_direct']['items'][0]['product']['id']=2
        db.orders=Orders([first,second,prior]);app=app_for(patches,db,native)
        path='/integrations-v2/google_ads/commercial-plans/winter'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer synthetic-token'}) as client:
            async def okay(method,url,**kwargs):
                result=await getattr(client,method)(url,**kwargs);assert result.status_code==200,result.text;return result.json()
            plan=await okay('put',path,json=payload(excluded_product_ids=['3'],prior_season_context=context(calendar_reference_text=None)))
            assert 'acknowledge_review' not in plan['fields'] and 'expected_revision' not in plan['fields']
            assert plan['fields']==db.plans.rows[0]['fields'] and plan['fields']['category_url'] is None
            category=await okay('get',path+'/live-category-preview',params={'expected_revision':1,'category_id':'8'})
            query=[('expected_revision',1),('category_id','8'),('product_ids','1'),('product_ids','2')]
            initial=await okay('get',path+'/product-season-reviews',params=query)
            preview={'expected_revision':1,'category_id':'8','selected_product_ids':['1','2']}
            before=deepcopy(db.orders.rows)
            no_fit=await okay('post',path+'/product-test-review',json=preview)
            saved=await okay('put',path+'/product-season-reviews/1',json=fit())
            after=await okay('get',path+'/product-season-reviews',params=query)
            candidate=await okay('post',path+'/product-test-review',json=preview)
            blocked_save=await okay('put',path+'/product-season-reviews/2',json=fit(decision='not_suitable',reason='غير مناسب للتوجيه الحالي'))
            blocked_state=await okay('get',path+'/product-season-reviews',params=query)
            blocked_candidate=await okay('post',path+'/product-test-review',json=preview)
            db.users.rows[0]['password_updated_at']='2026-10-29T11:00:00Z'
            stale=await okay('get',path+'/product-season-reviews',params=query)
            rereview=await okay('put',path+'/product-season-reviews/1',json=fit(expected_review_revision=1))
            rereview_state=await okay('get',path+'/product-season-reviews',params=query)
            assert db.orders.rows==before
        return {'generated_by':'actual_authenticated_management_HTTP_planPUT_categoryGET_fitGET_CASPUT_candidatePOST','auth_user':{'id':'owner','role':'owner'},
            'plan':plan,'category':category,'selected_product_ids':['1','2'],'initial_fit_state':initial,'no_fit_candidate':no_fit,
            'fit_saved':saved,'fit_state':after,'candidate':candidate,'blocked_fit_saved':blocked_save,'blocked_fit_state':blocked_state,
            'blocked_candidate':blocked_candidate,'password_stale_fit_state':stale,'rereview_saved':rereview,'rereview_fit_state':rereview_state}
    finally:patches.undo()

if __name__=='__main__':TARGET.write_text(json.dumps(asyncio.run(generate()),ensure_ascii=False,separators=(',',':'))+'\n')
