"""Actual transactions recover terminal legacy claims and retain unknown ACKs."""
from copy import deepcopy
from datetime import datetime, timezone
import asyncio

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClientSession
from motor.motor_asyncio import AsyncIOMotorCollection
import pytest
import pytest_asyncio

from integrations_control_center import google_ads_automation_authority as authority
from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center.google_ads_management import PROPOSALS, FENCES
from tests.test_google_ads_automation_budget_guard_mongo import mongo_guard
from tests.test_google_ads_automation_policy_mongo import automation_mongo_uri
from tests.test_google_ads_automation_authority_mongo import rebased_case, seed


@pytest_asyncio.fixture
async def legacy_mongo(monkeypatch,mongo_guard):
    db=mongo_guard[0].raw
    current=datetime.now(timezone.utc)
    docs,native=rebased_case(current)
    await seed(db,docs)
    # The generic guard fixture uses Mongo's default index name; the native
    # authority requires the production name for the identical unique keys.
    await db[guard.AUTOMATION_POLICIES].drop_index('user_id_1_account_id_1')
    await db[guard.AUTOMATION_POLICIES].create_index([('user_id',1),('account_id',1)],
        unique=True,name='google_automation_tenant_account')
    monkeypatch.setattr(guard,'budget_guard_runtime_available',lambda:True)
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','true')
    await authority.authorize_google_automation_claim(db,user_id='owner',account_id='123',
        proposal_id='proposal1',execution_claim_id='claim1',native_evidence=native,current=current)
    await db[guard.BUDGET_GUARDS].delete_many({'user_id':'owner','account_id':'123'})
    await db[guard.AUTOMATION_POLICIES].update_one({'user_id':'owner','account_id':'123'},
        {'$set':{'enabled':False,'revision':4}})
    await db.mezan_integrations_v2.delete_many({'user_id':'owner'})
    await db.mezan_integration_accounts_v2.delete_many({'user_id':'owner'})
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','false')
    monkeypatch.setattr(guard,'budget_guard_runtime_available',lambda:False)
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal',['cancelled','completed'])
async def test_actual_replica_terminal_legacy_recovery_preserves_quota_and_reflection(legacy_mongo,terminal):
    db=legacy_mongo
    changes=({'status':'completed','verified':True,'submission_started':True} if terminal=='completed' else
        {'status':'failed','submission_started':False,'safe_error':'execution_cancelled_before_dispatch'})
    await db[PROPOSALS].update_one({'proposal_id':'proposal1'},{'$set':changes})
    prior=await db[guard.AUTOMATION_POLICIES].find_one({'user_id':'owner','account_id':'123'})
    result=await authority.settle_google_automation_claim(db,user_id='owner',account_id='123',claim_id='claim1')
    stored=await db[guard.AUTOMATION_POLICIES].find_one({'user_id':'owner','account_id':'123'})
    shared=await db[guard.BUDGET_GUARDS].find_one({'user_id':'owner','account_id':'123'})
    assert result['released'] is True and result['quota_refunded'] is False
    assert stored['_dispatch_claim'] is None and stored['_dispatch_days']==prior['_dispatch_days']
    entry=shared['allocations']['standing_claim1']
    assert entry['state']==('native_enabled' if terminal=='completed' else 'settled')
    assert entry['origin']=='legacy_terminal_recovery' and shared['epoch']==1
    assert (await db[FENCES].find_one({'execution_claim_id':'claim1'}))['status']=='claimed'


@pytest.mark.asyncio
async def test_actual_replica_unknown_legacy_outcome_rolls_back_new_guard(legacy_mongo):
    db=legacy_mongo
    await db[PROPOSALS].update_one({'proposal_id':'proposal1'},{'$set':{'status':'uncertain','submission_started':True}})
    held=await db[guard.AUTOMATION_POLICIES].find_one({'user_id':'owner','account_id':'123'})
    with pytest.raises(HTTPException):
        await authority.settle_google_automation_claim(db,user_id='owner',account_id='123',claim_id='claim1')
    assert await db[guard.BUDGET_GUARDS].count_documents({})==0
    assert await db[guard.AUTOMATION_POLICIES].find_one({'user_id':'owner','account_id':'123'})==held


@pytest.mark.asyncio
async def test_actual_replica_lost_terminal_recovery_ack_keeps_conservative_committed_hold(monkeypatch,legacy_mongo):
    db=legacy_mongo
    await db[PROPOSALS].update_one({'proposal_id':'proposal1'},
        {'$set':{'status':'completed','verified':True,'submission_started':True}})
    original=AsyncIOMotorClientSession.commit_transaction
    calls=[]
    async def lost_ack(session):
        calls.append(True)
        await original(session)
        raise ConnectionError('test-only lost recovery acknowledgement')
    monkeypatch.setattr(AsyncIOMotorClientSession,'commit_transaction',lost_ack)
    with pytest.raises(HTTPException) as error:
        await authority.settle_google_automation_claim(db,user_id='owner',account_id='123',claim_id='claim1')
    assert error.value.detail['code']=='google_budget_guard_commit_unconfirmed'
    assert error.value.detail['automatic_retry_allowed'] is False
    assert error.value.detail['preserve_execution_fence'] is True and len(calls)==1
    shared=await db[guard.BUDGET_GUARDS].find_one({'user_id':'owner','account_id':'123'})
    assert shared['allocations']['standing_claim1']['state']=='native_enabled'
    assert (await db[FENCES].find_one({'execution_claim_id':'claim1'}))['status']=='claimed'


@pytest.mark.asyncio
async def test_actual_replica_fence_release_after_snapshot_read_aborts_legacy_recovery(monkeypatch,legacy_mongo):
    db=legacy_mongo
    await db[PROPOSALS].update_one({'proposal_id':'proposal1'},
        {'$set':{'status':'failed','submission_started':False,'safe_error':'execution_cancelled_before_dispatch'}})
    held=await db[guard.AUTOMATION_POLICIES].find_one({'user_id':'owner','account_id':'123'})
    read,proceed=asyncio.Event(),asyncio.Event()
    original=AsyncIOMotorCollection.find_one
    async def read_then_pause(collection,*args,**kwargs):
        result=await original(collection,*args,**kwargs)
        if collection.database.name==db.name and collection.name==FENCES and kwargs.get('session') is not None:
            read.set();await proceed.wait()
        return result
    monkeypatch.setattr(AsyncIOMotorCollection,'find_one',read_then_pause)
    task=asyncio.create_task(authority.settle_google_automation_claim(db,user_id='owner',account_id='123',claim_id='claim1'))
    try:
        await asyncio.wait_for(read.wait(),3)
        await db[FENCES].update_one({'execution_claim_id':'claim1'},{'$set':{'status':'released'}})
        proceed.set()
        with pytest.raises(HTTPException) as error:await task
        assert error.value.detail['preserve_execution_fence'] is True
        assert error.value.detail['automatic_retry_allowed'] is False
        assert await db[guard.BUDGET_GUARDS].count_documents({})==0
        assert await db[guard.AUTOMATION_POLICIES].find_one({'user_id':'owner','account_id':'123'})==held
        assert (await db[FENCES].find_one({'execution_claim_id':'claim1'}))['status']=='released'
    finally:
        proceed.set()
        if not task.done():task.cancel()
        await asyncio.gather(task,return_exceptions=True)
