"""Own disposable loopback replica only; no production/provider access."""
from copy import deepcopy
import os
from urllib.parse import urlsplit
import uuid
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from integrations_control_center.google_plan_store_observations import read_store_observations, PROJECTION
from tests.test_google_store_product_observations import NOW,order

@pytest_asyncio.fixture
async def mongo():
    uri=os.getenv('GOOGLE_STORE_OBSERVATIONS_TEST_MONGO_URI')
    if not uri:pytest.skip('explicit own disposable replica required')
    parsed=urlsplit(uri)
    assert parsed.scheme=='mongodb' and parsed.hostname in {'127.0.0.1','localhost'} and parsed.port==27475
    assert parsed.username is None and parsed.password is None and parsed.path in ('','/') and not parsed.query and not parsed.fragment and ',' not in parsed.netloc
    client=AsyncIOMotorClient(uri,serverSelectionTimeoutMS=3000)
    name='google_store_observations_test_'+uuid.uuid4().hex;db=client[name]
    try:
        hello=await client.admin.command('hello');assert hello.get('setName')=='gStoreObservations' and hello.get('isWritablePrimary') is True
        async with await client.start_session() as session:
            session.start_transaction();await db.probe.insert_one({'x':1},session=session);await session.abort_transaction()
        assert await db.probe.count_documents({})==0
        await db.unified_orders.create_index([('user_id',1),('order_date',-1)])
        yield db
    finally:
        await client.drop_database(name);client.close()

async def read(db):return await read_store_observations(db,owner_id='owner',product_ids=['123'],as_of=NOW)

@pytest.mark.asyncio
async def test_real_projection_scope_exact_source_midnight_and_no_customer_payload(mongo):
    a=order();a['customer_name']='private customer sentinel';a['raw_by_source']['salla_direct']['items'][0]['custom_fields']={'secret':'private customization'}
    b=order(user_id='other');await mongo.unified_orders.insert_many([a,b])
    out=await read(mongo);w=out['products'][0]['currency_groups'][0]['windows']['last_14_days'];assert w['observed_units']=='2'
    projected=await mongo.unified_orders.find_one({'user_id':'owner'},PROJECTION)
    assert 'customer_name' not in projected and 'custom_fields' not in str(projected)
    assert 'private' not in str(out) and out['coverage_complete'] is False
    assert await mongo.unified_orders.count_documents({})==2

@pytest.mark.asyncio
async def test_real_missing_index_is_error_without_unbounded_fallback(mongo):
    await mongo.unified_orders.drop_indexes()
    with pytest.raises(Exception):await read(mongo)

@pytest.mark.asyncio
async def test_real_document_sentinel_suppresses_counts(mongo):
    await mongo.unified_orders.insert_many([order() for _ in range(1001)])
    out=await read(mongo);assert out['truncated'] is True and out['diagnostics']['observed_documents'] is None
    assert out['products'][0]['currency_groups']==[]

@pytest.mark.asyncio
async def test_real_oversized_documents_are_omitted_without_zero_sales_claim(mongo):
    value=order();value['excluded_padding']='x'*40000;await mongo.unified_orders.insert_one(value)
    out=await read(mongo);assert out['products'][0]['currency_groups']==[] and out['coverage_complete'] is False
    assert out['products'][0]['sales_native'] is None

@pytest.mark.asyncio
async def test_real_duplicates_refunds_and_unmatched_ids_never_become_sales(mongo):
    a=order();b=deepcopy(a);await mongo.unified_orders.insert_many([a,b]);out=await read(mongo)
    assert out['diagnostics']['duplicate_order_documents']==2 and out['products'][0]['currency_groups']==[]
    await mongo.unified_orders.delete_many({})
    a=order();a['raw_by_source']['salla_direct']['items'][0]['product']['id']=999;await mongo.unified_orders.insert_one(a)
    out=await read(mongo);assert out['diagnostics']['unmatched_lines']==1 and out['products'][0]['currency_groups']==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('source',['refund_action','canonical_actual','line_amount'])
async def test_real_refund_projection_rejects_known_or_ambiguous_refund(mongo,source):
    value=order();raw=value['raw_by_source']['salla_direct']
    if source=='refund_action':raw['payment_actions']={'refund_action':{'has_refund_amount':True,'pending_refund_amount':1,'refund_amount':{'amount':20,'currency':'SAR'}}}
    elif source=='canonical_actual':value['actual_partial_refund_amount']=20
    else:raw['items'][0]['refunded_amount']=20
    await mongo.unified_orders.insert_one(value)
    out=await read(mongo);assert out['products'][0]['currency_groups']==[]
    assert out['refunds_reconciled'] is False

@pytest.mark.asyncio
async def test_real_leading_zero_alias_cannot_add_second_observation(mongo):
    a=order();b=deepcopy(a);b.update(order_id='010',order_number='020');b['raw_by_source']['salla_direct'].update(id='010',reference_id='020')
    await mongo.unified_orders.insert_many([a,b]);out=await read(mongo)
    assert out['products'][0]['currency_groups'][0]['windows']['last_14_days']['observed_units']=='2'

@pytest.mark.asyncio
@pytest.mark.parametrize('actions',['bad',None,[],{'refund_action':'bad'}])
async def test_real_malformed_refund_containers_cannot_disappear_into_eligible_projection(mongo,actions):
    value=order();value['raw_by_source']['salla_direct']['payment_actions']=actions
    await mongo.unified_orders.insert_one(value)
    out=await read(mongo)
    assert out['products'][0]['currency_groups']==[] and out['coverage_complete'] is False
    assert 'malformed_refund_containers_excluded_before_projection' in out['limitations']

@pytest.mark.asyncio
async def test_real_management_router_owner_saved_plan_scopes_actual_observations(mongo,monkeypatch):
    from datetime import datetime
    from fastapi import FastAPI,APIRouter
    from httpx import ASGITransport,AsyncClient
    from integrations_control_center.google_ads_routes import attach_google_ads_management_routes
    from integrations_control_center import google_plan_store_observations as module
    class FixedDatetime(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(module,'datetime',FixedDatetime)
    class NoProvider:
        def __init__(self,*args,**kwargs):raise AssertionError('no provider calls allowed')
    await mongo.mezan_google_commercial_plans_v1.insert_one({'user_id':'owner','plan_id':'winter','revision':1,'fields':{'pinned_product_ids':['123','456'],'excluded_product_ids':['456']}})
    await mongo.mezan_products_v2.insert_one({'user_id':'owner','salla_product_id':'123','mezan_product_id':'mpv2_123','source':'salla','source_revision':'a'*64,'last_synced_at':NOW,'status':'active','archived':False})
    await mongo.unified_orders.insert_many([order(),order(user_id='foreign')])
    app=FastAPI();router=APIRouter(prefix='/integrations-v2')
    async def owner():return {'id':'owner','role':'owner'}
    attach_google_ads_management_routes(router,mongo,owner,client_factory=NoProvider);app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        response=await client.get('/integrations-v2/google_ads/commercial-plans/winter/store-observations?expected_revision=1')
        stale=await client.get('/integrations-v2/google_ads/commercial-plans/winter/store-observations?expected_revision=2')
    assert response.status_code==200 and stale.status_code==409
    data=response.json();assert data['product_ids_reviewed']==['123']
    assert data['observations']['products'][0]['currency_groups'][0]['windows']['last_14_days']['observed_units']=='2'
    assert data['evidence']['products'][0]['windows']['last_14_days']['availability']=='unproven'
    assert data['provider_writes']==0 and data['execution_authorized'] is False
    assert await mongo.unified_orders.count_documents({})==2
    assert await mongo.mezan_google_commercial_plans_v1.count_documents({})==1
