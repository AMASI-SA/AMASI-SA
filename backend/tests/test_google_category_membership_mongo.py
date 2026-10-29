"""Actual ProductV2 writers/BSON dates and own disposable loopback replica."""
from copy import deepcopy
from datetime import datetime,timedelta
import os
from urllib.parse import urlsplit
import uuid
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure
from integrations_control_center.google_category_membership_reader import read_category_memberships
from tests.test_google_category_membership import NOW,product

@pytest_asyncio.fixture
async def mongo():
    uri=os.getenv('GOOGLE_CATEGORY_TEST_MONGO_URI')
    if not uri:pytest.skip('explicit own disposable replica required')
    p=urlsplit(uri);assert p.scheme=='mongodb' and p.hostname=='127.0.0.1' and p.port==27476 and p.path in ('','/') and p.username is None and p.password is None and not p.query and not p.fragment and ',' not in p.netloc
    client=AsyncIOMotorClient(uri,serverSelectionTimeoutMS=3000);name='google_category_test_'+uuid.uuid4().hex;db=client[name]
    try:
        hello=await client.admin.command('hello');assert hello.get('setName')=='gCategoryReview' and hello.get('isWritablePrimary') is True
        async with await client.start_session() as session:
            session.start_transaction();await db.probe.insert_one({'x':1},session=session);await session.abort_transaction()
        assert await db.probe.count_documents({})==0
        await db.mezan_products_v2.create_index([('user_id',1),('salla_product_id',1)])
        yield db
    finally:await client.drop_database(name);client.close()
async def read(db):return await read_category_memberships(db,owner_id='owner',product_ids=['123'],category_ids=['8'],category_url=None,as_of=NOW)

@pytest.mark.asyncio
@pytest.mark.parametrize('writer',['list','details'])
async def test_actual_normalizer_and_details_writer_bson_snapshot_positive(mongo,monkeypatch,writer):
    value=product()
    if writer=='details':
        import product_v2_details_routes as module
        monkeypatch.setattr(module,'_now',lambda:NOW)
        value=module._details_patch(value['raw_salla'],user_id='owner')
    value['private_sentinel']='private product metadata';await mongo.mezan_products_v2.insert_one(value)
    result=await read(mongo);row=result['products'][0]
    assert row['membership']=='observed_in_stored_source' and row['matched_category_ids']==['8'] and row['observed_at']==NOW.isoformat()
    assert 'private' not in str(result) and result['platform_catalog_mapping_verified'] is False
    assert await mongo.mezan_products_v2.count_documents({})==1

@pytest.mark.asyncio
async def test_current_missing_categories_never_reuses_retained_old_details(mongo):
    value=product();value['raw_salla_details']=deepcopy(value['raw_salla']);value['raw_salla'].pop('categories')
    await mongo.mezan_products_v2.insert_one(value);assert (await read(mongo))['products'][0]['membership']=='unproved'

@pytest.mark.asyncio
async def test_owner_isolation_and_duplicate_identity_fail_closed(mongo):
    value=product();foreign=product();foreign['user_id']='foreign';await mongo.mezan_products_v2.insert_many([value,foreign])
    assert (await read(mongo))['products'][0]['membership']=='observed_in_stored_source'
    await mongo.mezan_products_v2.insert_one(product())
    with pytest.raises(ValueError):await read(mongo)

@pytest.mark.asyncio
@pytest.mark.parametrize('stamp',[NOW-timedelta(days=2),NOW+timedelta(seconds=1)])
async def test_bson_stale_future_observation_is_not_proof(mongo,stamp):
    value=product();value['last_synced_at']=stamp;await mongo.mezan_products_v2.insert_one(value)
    assert (await read(mongo))['products'][0]['membership']=='unproved'

@pytest.mark.asyncio
async def test_oversized_document_is_unknown_not_remote_absence(mongo):
    value=product();value['oversize']='x'*40000;await mongo.mezan_products_v2.insert_one(value)
    result=await read(mongo);assert result['products'][0]['membership']=='unproved' and result['remote_category_coverage_complete'] is False

@pytest.mark.asyncio
async def test_missing_index_has_no_unbounded_fallback(mongo):
    await mongo.mezan_products_v2.drop_indexes()
    with pytest.raises(OperationFailure):await read(mongo)

@pytest.mark.asyncio
async def test_actual_management_route_reads_normalized_snapshot_and_rejects_stale_revision(mongo,monkeypatch):
    from fastapi import FastAPI,APIRouter
    from httpx import ASGITransport,AsyncClient
    from integrations_control_center.google_ads_routes import attach_google_ads_management_routes
    from integrations_control_center import google_category_membership_routes as module
    class FixedDatetime(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(module,'datetime',FixedDatetime)
    await mongo.mezan_google_commercial_plans_v1.insert_one({'user_id':'owner','plan_id':'winter','revision':1,'fields':{'pinned_product_ids':['123','456'],'excluded_product_ids':['456'],'category_ids':['8'],'category_url':None}})
    await mongo.mezan_products_v2.insert_one(product())
    app=FastAPI();router=APIRouter()
    async def owner():return {'id':'owner','role':'owner'}
    class NoProvider:
        def __init__(self,*a,**kw):raise AssertionError('provider must not be called')
    attach_google_ads_management_routes(router,mongo,owner,client_factory=NoProvider);app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        r=await client.get('/google_ads/commercial-plans/winter/category-review?expected_revision=1')
        stale=await client.get('/google_ads/commercial-plans/winter/category-review?expected_revision=2')
    assert r.status_code==200 and stale.status_code==409
    assert r.json()['product_ids_reviewed']==['123'] and r.json()['review']['products'][0]['matched_category_ids']==['8']
    assert await mongo.mezan_google_commercial_plans_v1.count_documents({})==1
