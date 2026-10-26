"""Shared native gateway + standing authority against disposable real Mongo.

Only Google transport is fake. Real preview inserts, proposal/account fences,
policy claim CAS, submission floors and terminal settlement all use MongoDB.
"""
import asyncio
from copy import deepcopy
from datetime import timedelta

from fastapi import HTTPException
import pytest
from tests.test_google_ads_automation_policy_mongo import guard_mongo_contract, set_guard_time

from integrations_control_center import google_ads_automation_gateway as gateway
from integrations_control_center import google_ads_automation_budget_guard as guard
from motor.motor_asyncio import AsyncIOMotorClientSession
from integrations_control_center import google_ads_automation_authority as authority
from integrations_control_center import google_ads_management as management
from integrations_control_center.google_ads_campaign_contract import GoogleCampaignActionInput
from tests.test_google_ads_automation_authority import NOW, case_documents
from tests.test_google_ads_automation_authority_mongo import authority_db, seed
from tests.test_google_ads_automation_policy_mongo import automation_mongo_uri
from tests.test_google_ads_automation_gateway import NativeProvider


async def setup(monkeypatch,db):
    set_guard_time(NOW)
    docs,native=case_documents();docs[management.PROPOSALS]=[];docs[management.FENCES]=[]
    await seed(db,docs)
    provider=NativeProvider(native)
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','true')
    monkeypatch.setattr(management,'now',lambda:NOW)
    monkeypatch.setattr(authority,'_now',lambda current=None:NOW)
    async def client(database,user_id,account_id,**kwargs):
        assert database is db and user_id=='owner' and account_id=='123'
        return provider,'test-epoch'
    monkeypatch.setattr(management,'account_client',client)
    return provider


async def preview(db):
    return await gateway.preview_google_automation_recommendation(db,user_id='owner',account_id='123',
        policy_revision=3,analysis_id='analysis1',recommendation_id='rec1')


async def execute(db,row):
    return await gateway.execute_google_automation_proposal(db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])


@pytest.mark.asyncio
async def test_real_gateway_validation_claim_floor_and_readback_complete_once(monkeypatch,authority_db):
    provider=await setup(monkeypatch,authority_db);row=await preview(authority_db)
    assert provider.previews==1 and provider.writes==0
    result=await execute(authority_db,row)
    assert result['status']=='completed' and result['verified'] is True and provider.writes==1
    stored=await authority_db[management.PROPOSALS].find_one({'proposal_id':row['proposal_id']})
    assert stored['submission_started'] is True and stored['accepted_receipt'] is True
    configured=await authority_db[gateway.AUTOMATION_POLICIES].find_one({'user_id':'owner','account_id':'123'})
    assert configured['_dispatch_claim'] is None
    assert configured['_dispatch_days'][0]['proposal_ids']==[row['proposal_id']]
    assert (await execute(authority_db,row))['status']=='completed' and provider.writes==1


@pytest.mark.asyncio
async def test_real_shared_account_fence_serializes_manual_and_automation(monkeypatch,authority_db):
    provider=await setup(monkeypatch,authority_db);auto=await preview(authority_db)
    owner={'user_id':'owner','actor_id':'owner','owner':True,'can_draft':True}
    manual=await management.preview_google_action(authority_db,owner,GoogleCampaignActionInput(
        account_id='123',campaign_id='456',action='rename',changes={'name':'Manual test'},idempotency_key='manual-rival'))
    entered,resume=asyncio.Event(),asyncio.Event();original=provider.mutate
    async def blocked(operations,*,validate_only=False):
        if validate_only:return await original(operations,validate_only=True)
        entered.set();await resume.wait()
        return await original(operations)
    monkeypatch.setattr(provider,'mutate',blocked)
    task=asyncio.create_task(execute(authority_db,auto))
    try:
        await asyncio.wait_for(entered.wait(),5)
        with pytest.raises(HTTPException) as error:
            await management.execute_google_proposal(authority_db,owner,manual['proposal_id'],manual['confirmation_digest'])
        assert error.value.detail['code']=='google_ads_account_execution_pending'
        assert await authority_db[management.FENCES].count_documents({'status':'claimed'})==1
        assert (await authority_db[management.PROPOSALS].find_one({'proposal_id':manual['proposal_id']}))['submission_started'] is False
    finally:
        resume.set();result=await asyncio.wait_for(task,5)
    assert result['status']=='completed' and provider.writes==1


@pytest.mark.asyncio
async def test_real_authority_postcommit_ack_loss_holds_then_terminally_cancels_without_http(monkeypatch,authority_db):
    provider=await setup(monkeypatch,authority_db);row=await preview(authority_db)
    original=AsyncIOMotorClientSession.commit_transaction
    async def lose_ack(session):
        await original(session)
        raise TimeoutError('test-only budget transaction committed but response lost')
    monkeypatch.setattr(AsyncIOMotorClientSession,'commit_transaction',lose_ack)
    result=await execute(authority_db,row)
    assert result['status']=='uncertain' and result['submission_started'] is False and provider.writes==0
    configured=await authority_db[gateway.AUTOMATION_POLICIES].find_one({'user_id':'owner','account_id':'123'})
    assert configured['_dispatch_claim']['proposal_id']==row['proposal_id']
    budget=await authority_db[guard.BUDGET_GUARDS].find_one({'user_id':'owner','account_id':'123'})
    allocation=budget['allocations'][budget['active_claim']['allocation_id']]
    assert allocation['state']=='reserved' and allocation['proposal_id']==row['proposal_id']
    assert (await authority_db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    monkeypatch.setattr(AsyncIOMotorClientSession,'commit_transaction',original)
    monkeypatch.setattr(management,'now',lambda:NOW+timedelta(minutes=6))
    monkeypatch.delenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED')
    result=await gateway.reconcile_google_automation_proposal(authority_db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])
    assert result['status']=='failed' and result['safe_error']=='execution_cancelled_before_dispatch' and provider.writes==0
    configured=await authority_db[gateway.AUTOMATION_POLICIES].find_one({'user_id':'owner','account_id':'123'})
    assert configured['_dispatch_claim'] is None and configured['_dispatch_days'][0]['proposal_ids']==[row['proposal_id']]
    assert (await authority_db[management.FENCES].find_one({'account_id':'123'}))['status']=='released'


@pytest.mark.asyncio
async def test_real_submission_floor_postcommit_ack_loss_never_dispatches_or_auto_retries(monkeypatch,authority_db):
    provider=await setup(monkeypatch,authority_db);row=await preview(authority_db);provider.verified=False
    original=management.record_event
    async def lose_floor_ack(db,proposal,status,**kwargs):
        result=await original(db,proposal,status,**kwargs)
        if status=='submitted':raise TimeoutError('test-only committed submission floor response lost')
        return result
    monkeypatch.setattr(management,'record_event',lose_floor_ack)
    result=await execute(authority_db,row)
    assert result['status']=='uncertain' and result['submission_started'] is True and provider.writes==0
    with pytest.raises(HTTPException):await execute(authority_db,row)
    monkeypatch.setattr(management,'now',lambda:NOW+timedelta(minutes=6))
    recovered=await gateway.reconcile_google_automation_proposal(authority_db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])
    assert recovered['status']=='uncertain' and provider.writes==0
    assert (await authority_db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'

@pytest.mark.asyncio
async def test_real_flag_off_after_floor_ack_stops_http_and_settles_bound_no_http_proof(monkeypatch,authority_db):
    provider=await setup(monkeypatch,authority_db);row=await preview(authority_db)
    original=management.record_event
    async def kill_after_ack(db,proposal,status,**kwargs):
        result=await original(db,proposal,status,**kwargs)
        if status=='submitted':monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','false')
        return result
    monkeypatch.setattr(management,'record_event',kill_after_ack)
    result=await execute(authority_db,row)
    assert result['status']=='failed' and result['submission_started'] is True and provider.writes==0
    stored=await authority_db[management.PROPOSALS].find_one({'proposal_id':row['proposal_id']})
    assert stored['dispatch_cancelled_before_http'] is True
    assert stored['dispatch_cancelled_before_http_claim_id']==stored['execution_claim_id']
    configured=await authority_db[gateway.AUTOMATION_POLICIES].find_one({'user_id':'owner','account_id':'123'})
    assert configured['_dispatch_claim'] is None and configured['_dispatch_days'][0]['proposal_ids']==[row['proposal_id']]
    assert (await authority_db[management.FENCES].find_one({'account_id':'123'}))['status']=='released'


@pytest.mark.asyncio
async def test_real_terminal_cancel_before_old_floor_is_never_resurrected(monkeypatch,authority_db):
    provider=await setup(monkeypatch,authority_db);row=await preview(authority_db)
    original=management.record_event
    async def cancel_before_floor(db,proposal,status,**kwargs):
        if status=='submitted':
            cancelled=await db[management.PROPOSALS].update_one({'user_id':'owner','account_id':'123',
                'proposal_id':proposal['proposal_id'],'execution_claim_id':proposal['execution_claim_id'],
                'status':'approved','submission_started':False}, {'$set':{'status':'failed',
                'safe_error':'execution_cancelled_before_dispatch','provider_write_reached':False}})
            assert cancelled.matched_count==1
            await authority.settle_google_automation_claim(db,user_id='owner',account_id='123',claim_id=proposal['execution_claim_id'])
            await management._release_fence(db,proposal)
        return await original(db,proposal,status,**kwargs)
    monkeypatch.setattr(management,'record_event',cancel_before_floor)
    result=await execute(authority_db,row)
    assert result['status']=='failed' and result['safe_error']=='execution_cancelled_before_dispatch'
    stored=await authority_db[management.PROPOSALS].find_one({'proposal_id':row['proposal_id']})
    assert stored['status']=='failed' and stored['submission_started'] is False and provider.writes==0
    assert (await authority_db[management.FENCES].find_one({'account_id':'123'}))['status']=='released'
    assert (await authority_db[gateway.AUTOMATION_POLICIES].find_one({'user_id':'owner','account_id':'123'}))['_dispatch_claim'] is None

@pytest.mark.asyncio
@pytest.mark.parametrize('outcome',['completed','provider_rejected'])
async def test_real_settlement_ack_loss_keeps_native_fence_until_read_based_release(monkeypatch,authority_db,outcome):
    provider=await setup(monkeypatch,authority_db);row=await preview(authority_db)
    if outcome=='provider_rejected':
        original_mutate=provider.mutate
        async def reject(operations,*,validate_only=False):
            if validate_only:return await original_mutate(operations,validate_only=True)
            raise HTTPException(400,{'code':'native_rejected','provider_status':400})
        monkeypatch.setattr(provider,'mutate',reject)
    original_settle=authority.settle_google_automation_claim
    async def lose_settlement_ack(*args,**kwargs):
        await original_settle(*args,**kwargs)
        raise TimeoutError('test-only policy settlement committed but response lost')
    monkeypatch.setattr(authority,'settle_google_automation_claim',lose_settlement_ack)
    with pytest.raises(TimeoutError):await execute(authority_db,row)
    assert (await authority_db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    configured=await authority_db[gateway.AUTOMATION_POLICIES].find_one({'user_id':'owner','account_id':'123'})
    assert configured['_dispatch_claim'] is None
    assert configured['_dispatch_days'][0]['proposal_ids']==[row['proposal_id']]
    monkeypatch.setattr(authority,'settle_google_automation_claim',original_settle)
    monkeypatch.delenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED')
    recovered=await gateway.reconcile_google_automation_proposal(authority_db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])
    assert recovered['status']==('completed' if outcome=='completed' else 'failed')
    assert (await authority_db[management.FENCES].find_one({'account_id':'123'}))['status']=='released'
    assert provider.writes==(1 if outcome=='completed' else 0)
