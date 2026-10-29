"""Disposable real Mongo and synthetic fixed-GET provider responses only."""
import asyncio
from copy import deepcopy
from datetime import datetime,timezone
import os
from urllib.parse import urlsplit
import uuid
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from integrations_control_center.google_commercial_plan import PLANS
from integrations_control_center.google_product_season_review import REVIEWS,ReviewInput,save_product_season_review
from integrations_control_center import google_product_test_review_reader as reader
from tests.test_google_product_test_review_routes import app_for,client,payload,preview,save_fit,read_fit,fit,NOW,Native
from tests.test_google_store_product_observations import order
from tests.test_google_native_inventory_reader import page,envelope

@pytest_asyncio.fixture
async def mongo():
    uri=os.getenv('GOOGLE_PRODUCT_TEST_REVIEW_MONGO_URI')
    if not uri:pytest.skip('explicit disposable loopback replica required')
    parsed=urlsplit(uri)
    assert parsed.scheme=='mongodb' and parsed.hostname=='127.0.0.1' and parsed.port==27482
    assert parsed.path in ('','/') and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    connection=AsyncIOMotorClient(uri,serverSelectionTimeoutMS=3000);name='google_product_test_review_'+uuid.uuid4().hex;db=connection[name]
    try:
        hello=await connection.admin.command('hello');assert hello.get('setName')=='gProductTestReview' and hello.get('isWritablePrimary') is True
        async with await connection.start_session() as session:
            session.start_transaction();await db.probe.insert_one({'x':1},session=session);await session.abort_transaction()
        assert await db.probe.count_documents({})==0
        await db.users.insert_one({'id':'owner','role':'owner'})
        await db.salla_integrations.insert_one({'user_id':'owner','store_id':9,'store_domain':'https://store.test','status':'connected','token_revision':'opaque'})
        await db.unified_orders.create_index([('user_id',1),('order_date',-1)])
        yield db
    finally:
        await connection.drop_database(name);connection.close()

def source(currency='SAR',identity=10):
    row=order(order_id=str(identity),order_number=str(identity+10),currency=currency)
    raw=row['raw_by_source']['salla_direct'];raw.update(id=identity,reference_id=identity+10,currency=currency)
    raw['items'][0].update(id=identity+20);raw['items'][0]['product']['id']=1
    return row

@pytest.mark.asyncio
async def test_actual_http_writer_fit_and_real_order_projection_support_review_not_profit(mongo,monkeypatch):
    await mongo.unified_orders.insert_one(source());before=await mongo.unified_orders.find_one({});native=Native()
    async with client(app_for(monkeypatch,mongo,native)) as c:
        saved=await c.put('/google_ads/commercial-plans/winter',json=payload());assert saved.status_code==200
        assert 'acknowledge_review' not in saved.json()['fields']
        assert (await preview(c)).json()['products'][0]['decision']=='needs_owner_fit_review'
        assert (await save_fit(c)).status_code==200
        response=await preview(c)
    assert response.status_code==200,response.text
    row=response.json()['products'][0];assert row['decision']=='supported_test_review'
    assert row['current_store_observations']['currency_groups'][0]['windows']['last_14_days']=={'observed_orders':1,'observed_lines':1,'observed_units':'2'}
    assert row['profitability'] is None and row['paid_launch_eligible'] is False
    assert await mongo.unified_orders.find_one({})==before and await mongo[REVIEWS].count_documents({})==1
    assert all(method[0].startswith('/') for method in native.calls)

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['disabled','role','deleted','password','bool_revision','float_revision','fields','review','connection'])
async def test_actual_owner_plan_review_connection_races_reject_output(mongo,monkeypatch,kind):
    original=reader.read_store_observations
    async def changing(*args,**kwargs):
        result=await original(*args,**kwargs)
        if kind=='deleted':await mongo.users.delete_many({'id':'owner'})
        elif kind in {'disabled','role','password'}:
            change={'disabled':True} if kind=='disabled' else {'role':'staff'} if kind=='role' else {'password_updated_at':'2026-10-29T12:01:00Z'}
            await mongo.users.update_one({'id':'owner'},{'$set':change})
        elif kind in {'bool_revision','float_revision','fields'}:
            change={'revision':True} if kind=='bool_revision' else {'revision':1.0} if kind=='float_revision' else {'fields.message':'changed'}
            await mongo[PLANS].update_one({'plan_id':'winter'},{'$set':change})
        elif kind=='connection':await mongo.salla_integrations.update_one({'user_id':'owner'},{'$set':{'token_revision':'changed'}})
        else:await save_product_season_review(mongo,authenticated_user={'id':'owner','role':'owner'},plan_id='winter',product_id='1',body=ReviewInput.model_validate(fit(expected_review_revision=1)),clock=lambda:NOW)
        return result
    monkeypatch.setattr(reader,'read_store_observations',changing)
    async with client(app_for(monkeypatch,mongo)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload())).status_code==200
        assert (await save_fit(c)).status_code==200
        response=await preview(c)
    assert response.status_code==({'disabled':403,'role':403,'deleted':403,'password':403,'bool_revision':409,'float_revision':409,'fields':409}.get(kind,503)),response.text
    assert 'products' not in response.json()

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['refund','malformed_refund','duplicate'])
async def test_actual_projection_rejected_orders_never_become_positive_demand(mongo,monkeypatch,kind):
    row=source()
    if kind=='duplicate':await mongo.unified_orders.insert_many([row,deepcopy(row)])
    else:
        row['raw_by_source']['salla_direct']['payment_actions']='unproved' if kind=='malformed_refund' else {'refund_action':{'has_refund_amount':True}}
        await mongo.unified_orders.insert_one(row)
    async with client(app_for(monkeypatch,mongo)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload())).status_code==200
        assert (await save_fit(c)).status_code==200
        response=await preview(c)
    assert response.status_code==200,response.text
    row=response.json()['products'][0];assert row['decision']=='exploration_test_review' and row['current_store_observations']['currency_groups']==[]
    assert 'not_absence_of_sales' in row['history_status']

@pytest.mark.asyncio
async def test_actual_mixed_currencies_remain_separate_without_conversion_or_profit(mongo,monkeypatch):
    await mongo.unified_orders.insert_many([source(),source('USD',11)])
    async with client(app_for(monkeypatch,mongo)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload())).status_code==200
        assert (await save_fit(c)).status_code==200
        response=await preview(c)
    assert response.status_code==200,response.text
    row=response.json()['products'][0];assert {g['currency_code'] for g in row['current_store_observations']['currency_groups']}=={'SAR','USD'}
    assert row['profitability'] is None and response.json()['platform_conversions_added'] is False

@pytest.mark.asyncio
async def test_actual_prior_band_and_current_four_windows_remain_separate(mongo,monkeypatch):
    from tests.test_google_prior_season_context import context
    past=source();past['order_date']='2025-11-01';past['raw_by_source']['salla_direct']['date']='2025-11-01T10:00:00Z'
    await mongo.unified_orders.insert_one(past)
    async with client(app_for(monkeypatch,mongo)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload(prior_season_context=context()))).status_code==200
        assert (await save_fit(c)).status_code==200
        response=await preview(c)
    assert response.status_code==200,response.text
    data=response.json();row=data['products'][0]
    assert len(data['current_windows'])==4 and len(data['prior_windows'])==1 and row['current_store_observations']['currency_groups']==[]
    assert row['prior_store_observations']['currency_groups'][0]['windows']['owner_reviewed_prior_season']['observed_units']=='2'
    assert data['calendar_alignment_verification']=='unknown' and data['growth'] is None

@pytest.mark.asyncio
async def test_actual_review_cas_concurrent_writers_keep_one_winner_no_overwrite(mongo,monkeypatch):
    app=app_for(monkeypatch,mongo)
    async with client(app) as c:assert (await c.put('/google_ads/commercial-plans/winter',json=payload())).status_code==200
    async def save():
        try:return await save_product_season_review(mongo,authenticated_user={'id':'owner','role':'owner'},plan_id='winter',product_id='1',body=ReviewInput.model_validate(fit()),clock=lambda:NOW)
        except Exception as exc:return exc
    results=await asyncio.gather(save(),save())
    assert sum(isinstance(r,dict) for r in results)==1
    rejected=next(r for r in results if not isinstance(r,dict));assert rejected.status_code==409
    assert await mongo[REVIEWS].count_documents({})==1
    assert (await mongo[REVIEWS].find_one({}))['revision']==1

@pytest.mark.asyncio
async def test_actual_password_change_after_authenticated_dependency_rejects_before_native(mongo,monkeypatch):
    from integrations_control_center import google_product_season_review as module
    native=Native();original=module.principal
    async def changing(*args,**kwargs):
        actor=await original(*args,**kwargs)
        await mongo.users.update_one({'id':'owner'},{'$set':{'password_updated_at':'2026-10-29T12:01:00Z'}})
        return actor
    monkeypatch.setattr(module,'principal',changing)
    async with client(app_for(monkeypatch,mongo,native)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload())).status_code==200
        response=await preview(c)
    assert response.status_code==403 and native.calls==[]

@pytest.mark.asyncio
async def test_actual_missing_order_index_fails_without_unbounded_fallback(mongo,monkeypatch):
    await mongo.unified_orders.drop_indexes()
    async with client(app_for(monkeypatch,mongo)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload())).status_code==200
        response=await preview(c)
    assert response.status_code==503 and 'unified_orders' not in response.text

@pytest.mark.asyncio
async def test_actual_committed_campaign_writers_keep_two_products_one_context(mongo,monkeypatch):
    from tests.test_google_linked_campaign_mongo import seed
    from integrations_control_center import campaign_product_associations as graph
    await seed(mongo,monkeypatch)
    recorded=datetime(2026,10,1,tzinfo=timezone.utc)
    for product in ['1','2']:
        await graph.attach_campaign_product(mongo,'owner',{'provider':'snapchat_ads','account_id':'account','campaign_id':'campaign','product_id':product,
            'valid_from':recorded,'evidence':{'source':'manual','verification_status':'verified','observed_at':recorded}},idempotency_key='candidate-'+product)
    native=Native();native.responses['/categories/8/products']=page([{'id':1},{'id':2}])
    native.responses['/products/2']=deepcopy(native.responses['/products/1']);native.responses['/products/2']['data']['id']=2
    native.responses['/products/2/variants']=page([{'id':8,'stock_quantity':3}])
    async with client(app_for(monkeypatch,mongo,native)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload())).status_code==200
        assert (await save_fit(c)).status_code==200
        assert (await c.put('/google_ads/commercial-plans/winter/product-season-reviews/2',json=fit())).status_code==200
        response=await preview(c,selected_product_ids=['1','2'])
    assert response.status_code==200,response.text
    data=response.json();assert len(data['campaign_contexts'])==1 and all(len(p['campaign_context_refs'])==1 for p in data['products'])
    assert all('metrics' not in p for p in data['products']) and data['platform_conversions_added'] is False

@pytest.mark.asyncio
async def test_actual_review_password_revision_requires_explicit_cas_rereview(mongo,monkeypatch):
    async with client(app_for(monkeypatch,mongo)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload())).status_code==200
        assert (await save_fit(c)).status_code==200
        await mongo.users.update_one({'id':'owner'},{'$set':{'password_updated_at':'2026-10-28T00:00:00Z'}})
        row=(await read_fit(c)).json()['products'][0];assert row['review_revision']==1 and row['current_owner_review'] is False
        assert (await save_fit(c,expected_review_revision=1)).status_code==200
        assert (await read_fit(c)).json()['products'][0]['current_owner_review'] is True

@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['preview','state','save'])
@pytest.mark.parametrize('mutation',['disabled','password'])
async def test_actual_owner_drift_during_final_review_read_never_escapes(mongo,monkeypatch,operation,mutation):
    from integrations_control_center import google_product_season_review as fits
    async with client(app_for(monkeypatch,mongo)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload())).status_code==200
        assert (await save_fit(c)).status_code==200
        target=reader if operation=='preview' else fits;original=target.read_reviews;calls=0
        async def changing(*args,**kwargs):
            nonlocal calls
            rows=await original(*args,**kwargs);calls+=1
            if calls==2:await mongo.users.update_one({'id':'owner'},{'$set':{'disabled':True} if mutation=='disabled' else {'password_updated_at':'2026-10-29T12:01:00Z'}})
            return rows
        monkeypatch.setattr(target,'read_reviews',changing)
        response=await preview(c) if operation=='preview' else await read_fit(c) if operation=='state' else await save_fit(c,expected_review_revision=1)
    assert response.status_code==403,response.text
    if operation=='save':assert response.json()['detail']['review_may_have_persisted'] is True and (await mongo[REVIEWS].find_one({}))['revision']==2

@pytest.mark.asyncio
async def test_actual_meta_tiktok_association_writers_preserve_native_field_gaps_without_performance_claim(mongo,monkeypatch):
    from integrations_control_center import campaign_product_associations as graph
    recorded=datetime(2026,10,1,tzinfo=timezone.utc)
    monkeypatch.setattr(graph,'_utcnow',lambda:recorded)
    await graph.ensure_campaign_product_association_indexes(mongo)
    for provider in ['meta','tiktok']:
        await graph.attach_campaign_product(mongo,'owner',{'provider':provider,'account_id':provider+'-account','campaign_id':provider+'-campaign','product_id':'1',
            'valid_from':recorded,'evidence':{'source':'manual','verification_status':'verified','observed_at':recorded}},idempotency_key='missing-source-'+provider)
    async with client(app_for(monkeypatch,mongo)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload())).status_code==200
        assert (await save_fit(c)).status_code==200
        response=await preview(c)
    assert response.status_code==200,response.text
    data=response.json();row=data['products'][0];associations={r['provider']:r for r in row['campaign_associations']}
    assert set(associations)=={'meta','tiktok'}
    assert associations['meta']['missing']==['persisted_native_timezone','atomic_campaign_snapshot_completeness']
    assert associations['tiktok']['missing']==['persisted_native_currency','persisted_native_timezone']
    assert all(r['feed_mapping_verified'] is False and r['meaning']=='intended_product_association_only' for r in associations.values())
    assert row['decision']=='exploration_test_review' and row['evidence_reasons']==[] and data['campaign_contexts']==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['preview','state','save'])
async def test_actual_duplicate_plan_without_unique_index_fails_closed_before_provider_or_review_write(mongo,monkeypatch,operation):
    native=Native()
    async with client(app_for(monkeypatch,mongo,native)) as c:
        assert (await c.put('/google_ads/commercial-plans/winter',json=payload())).status_code==200
        await mongo[PLANS].drop_index('google_commercial_plan_owner_id')
        duplicate=await mongo[PLANS].find_one({});duplicate.pop('_id')
        await mongo[PLANS].insert_one(duplicate)
        assert await mongo[PLANS].count_documents({'user_id':'owner','plan_id':'winter'})==2
        response=await preview(c) if operation=='preview' else await read_fit(c) if operation=='state' else await save_fit(c)
    assert response.status_code==409,response.text
    assert response.json()['detail']['code']=='google_commercial_plan_scope_unproved'
    assert native.calls==[] and await mongo[REVIEWS].count_documents({})==0
