"""Disposable real Mongo; only the external Google transport is fake."""
import asyncio
import pytest
from tests.test_google_ads_automation_policy_mongo import guard_mongo_contract, set_guard_time
from fastapi import HTTPException
from integrations_control_center import google_ads_management as management
from integrations_control_center import google_ads_automation_creation_gateway as gateway
from integrations_control_center.google_ads_campaign_contract import GoogleCampaignActionInput
from tests.test_google_ads_automation_authority_mongo import authority_db
from tests.test_google_ads_automation_policy_mongo import automation_mongo_uri
from tests.test_google_ads_automation_creation_gateway import setup,preview,execute,advance,NOW,timedelta


@pytest.fixture(autouse=True)
def creation_guard_clock():
    set_guard_time(NOW)


@pytest.mark.asyncio
async def test_real_mongo_floor_original_readback_ownership_and_no_resend(monkeypatch,authority_db):
    db,provider,values=await setup(monkeypatch,authority_db);row=await preview(db,values);result=await execute(db,row)
    assert result['status']=='completed' and result['after']['status']=='PAUSED' and provider.writes==1
    stored=await db[management.PROPOSALS].find_one({'proposal_id':row['proposal_id']})
    assert stored['submission_started'] is True and stored['accepted_receipt'] is True
    policy=await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'})
    ledger=policy['_creation_ledger'][row['automation_source']['creation_identity']]
    assert ledger['state']=='owned' and ledger['verified'] is True and ledger['created_campaign_id']=='777'
    assert ledger['reservation_native']=='15' and ledger['reserved_enable_budget_native']=='0'
    assert policy['_creation_claim'] is None
    assert (await execute(db,row))['status']=='completed' and provider.writes==1


@pytest.mark.asyncio
async def test_real_mongo_lost_floor_ack_no_http_and_reservation_held(monkeypatch,authority_db):
    db,provider,values=await setup(monkeypatch,authority_db);row=await preview(db,values);original=management.record_event
    async def lose(database,proposal,status,**kwargs):
        result=await original(database,proposal,status,**kwargs)
        if status=='submitted':raise TimeoutError('lost floor ACK')
        return result
    monkeypatch.setattr(management,'record_event',lose)
    result=await execute(db,row)
    assert result['status']=='uncertain' and result['submission_started'] is True and provider.writes==0
    policy=await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'})
    assert policy['_creation_claim']['proposal_id']==row['proposal_id']
    assert policy['_creation_ledger'][row['automation_source']['creation_identity']]['state']=='reserved'
    stored=await db[management.PROPOSALS].find_one({'proposal_id':row['proposal_id']})
    assert stored.get('dispatch_cancelled_before_http') is not True
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    with pytest.raises(HTTPException):await execute(db,row)
    assert provider.writes==0


@pytest.mark.asyncio
async def test_real_mongo_stop_after_acked_floor_strict_settlement_preserves_floor_quota(monkeypatch,authority_db):
    db,provider,values=await setup(monkeypatch,authority_db);row=await preview(db,values);original=management.record_event
    async def stop(database,proposal,status,**kwargs):
        result=await original(database,proposal,status,**kwargs)
        if status=='submitted':monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_CREATION_ENABLED','false')
        return result
    monkeypatch.setattr(management,'record_event',stop)
    result=await execute(db,row)
    assert result['status']=='failed' and result['submission_started'] is True and provider.writes==0
    stored=await db[management.PROPOSALS].find_one({'proposal_id':row['proposal_id']})
    assert stored['dispatch_cancelled_before_http'] is True
    policy=await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'})
    assert policy['_creation_claim'] is None
    assert policy['_creation_ledger'][row['automation_source']['creation_identity']]['state']=='no_campaign'
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='released'


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome',['completed','rejected'])
async def test_real_mongo_settlement_ack_loss_holds_native_fence_until_off_recovery(monkeypatch,authority_db,outcome):
    from integrations_control_center import google_ads_automation_creation_authority as authority
    db,provider,values=await setup(monkeypatch,authority_db);row=await preview(db,values);original=authority.settle_google_creation_claim
    if outcome=='rejected':
        mutate=provider.mutate
        async def reject(operations,*,validate_only=False):
            if validate_only:return await mutate(operations,validate_only=True)
            provider.writes+=1
            raise HTTPException(502,detail={'code':'google_rejected','provider_status':400,'provider_codes':['INVALID_ARGUMENT']})
        monkeypatch.setattr(provider,'mutate',reject)
    async def lose(*args,**kwargs):
        await original(*args,**kwargs);raise TimeoutError('lost settlement ACK')
    monkeypatch.setattr(authority,'settle_google_creation_claim',lose)
    with pytest.raises(TimeoutError):await execute(db,row)
    assert provider.writes==1 and (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    monkeypatch.setattr(authority,'settle_google_creation_claim',original)
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_CREATION_ENABLED','false')
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','false')
    result=await gateway.reconcile_google_automation_creation(db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])
    assert result['status']==('completed' if outcome=='completed' else 'failed') and provider.writes==1
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='released'


@pytest.mark.asyncio
async def test_real_mongo_common_fence_serializes_manual_and_standing_creation(monkeypatch,authority_db):
    db,provider,values=await setup(monkeypatch,authority_db);row=await preview(db,values)
    owner={'user_id':'owner','actor_id':'owner','owner':True,'can_draft':True}
    manual=await management.preview_google_action(db,owner,GoogleCampaignActionInput(account_id='123',
        action='create_campaign',draft_id=row['draft_id'],draft_revision=1,idempotency_key='manual-rival'))
    entered,resume=asyncio.Event(),asyncio.Event();original=provider.mutate
    async def blocked(operations,*,validate_only=False):
        if validate_only:return await original(operations,validate_only=True)
        entered.set();await resume.wait();return await original(operations)
    monkeypatch.setattr(provider,'mutate',blocked)
    task=asyncio.create_task(execute(db,row))
    try:
        await asyncio.wait_for(entered.wait(),5)
        with pytest.raises(HTTPException) as error:
            await management.execute_google_proposal(db,owner,manual['proposal_id'],manual['confirmation_digest'])
        assert error.value.detail['code']=='google_ads_account_execution_pending'
    finally:
        resume.set();result=await task
    assert result['status']=='completed' and provider.writes==1


@pytest.mark.asyncio
async def test_real_mongo_original_correlation_recovers_without_receipt_and_never_mutates_again(monkeypatch,authority_db):
    db,provider,values=await setup(monkeypatch,authority_db);row=await preview(db,values);provider.loss=True
    result=await execute(db,row)
    assert result['status']=='uncertain' and provider.writes==1
    advance(monkeypatch,NOW+timedelta(seconds=31))
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_CREATION_ENABLED','false')
    result=await gateway.reconcile_google_automation_creation(db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])
    assert result['status']=='completed' and provider.writes==1
    stored=await db[management.PROPOSALS].find_one({'proposal_id':row['proposal_id']})
    assert stored.get('accepted_receipt') is not True and stored['verification']['readback_independent'] is True
    policy=await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'})
    assert policy['_creation_ledger'][row['automation_source']['creation_identity']]['state']=='owned'
