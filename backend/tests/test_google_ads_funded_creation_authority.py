"""Real storage/native-reader helpers; external Google transport only is fake."""
from copy import deepcopy
from datetime import timedelta
import pytest
from fastapi import HTTPException
from integrations_control_center import google_ads_funded_creation_authority as authority
from integrations_control_center import google_ads_funded_creation_grants as grants
from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center import google_ads_management as management
from integrations_control_center.google_ads_paused_creation_budget import account_exposure
from tests.test_google_ads_funded_creation_grants import setup as grant_setup,review,save
from tests.test_google_ads_automation_creation_gateway import NativeCreationProvider
from tests.test_google_ads_standing_dispatch_floor import NOW,advance

async def setup(monkeypatch,db=None,*,active=True,request_key='reviewed_launch_1',cap='100'):
    db,_,body=await grant_setup(monkeypatch,db)
    body.update(request_key=request_key,max_account_daily_budget_native=cap)
    await db[guard.AUTOMATION_POLICIES].update_one({'account_id':'123'},{'$set':{'max_account_daily_budget_native':'100'}})
    provider=NativeCreationProvider()
    original=provider.mutate
    async def validate(operations,*,validate_only=False):
        if not validate_only:raise AssertionError('storage helpers must NEVER send a funded mutation')
        assert next(op['campaignOperation']['create']['status'] for op in operations if 'campaignOperation'in op)=='ENABLED'
        return await original(operations,validate_only=True)
    provider.accept_original=lambda operations:original(operations,validate_only=False)
    monkeypatch.setattr(provider,'mutate',validate)
    async def client(database,user_id,account_id,**kwargs):
        assert user_id=='owner' and account_id=='123'
        return provider,'test-epoch'
    monkeypatch.setattr(management,'account_client',client)
    preview=await review(db,body);await save(db,body,preview['review_digest'])
    key=grants.grant_key('owner','123',request_key)
    if active:
        # Test-only stand-in for a FUTURE authenticated activation producer;
        # no application function can activate configured grants in this slice.
        await db[grants.GRANTS].update_one({'grant_key':key},{'$set':{'state':'active','activation_version':1,
            'activation_revision':1,'activated_by':'owner','activated_at':NOW.isoformat(),
            'activation_review_digest':preview['review_digest']}})
    intent=authority.pure.prepare_google_funded_creation_intent(grant=preview['grant'],draft=preview['draft'],current=NOW)
    row={'user_id':'owner','account_id':'123','proposal_id':'funded_proposal1','execution_claim_id':'funded_claim1',
        'actor_id':'mezan_google_automation_v1','provider':'google_ads','action':'create_campaign','campaign_id':None,
        'before_digest':None,'before_snapshot':None,'auth_epoch':'test-epoch','currency':'SAR','time_zone':'Asia/Riyadh',
        'draft_id':'reviewdraft','draft_revision':1,'planned':preview['draft'],'status':'approved','submission_started':False,
        'operations':intent['request']['mutateOperations'],'expires_at':(NOW+timedelta(minutes=30)).isoformat(),
        'created_at':NOW.isoformat(),'idempotency_key':intent['creation_identity'],
        'automation_source':{'kind':'funded_new_creation','grant_key':key,'creation_identity':intent['creation_identity']}}
    row['confirmation_digest']=management.digest(management.immutable(row))
    await db[management.PROPOSALS].insert_one(row)
    await db[management.FENCES].insert_one({'user_id':'owner','account_id':'123','proposal_id':row['proposal_id'],
        'execution_claim_id':row['execution_claim_id'],'status':'claimed','claimed_at':NOW.isoformat()})
    return db,provider,key,row

async def reserve(db,key,row):
    return await authority.reserve_google_funded_creation(db,user_id='owner',account_id='123',grant_key=key,
        proposal_id=row['proposal_id'],execution_claim_id=row['execution_claim_id'])

async def floor(db,key,row):
    return await authority.floor_google_funded_creation(db,user_id='owner',account_id='123',grant_key=key,
        proposal_id=row['proposal_id'],execution_claim_id=row['execution_claim_id'])

async def settle(db,key,row):
    return await authority.settle_google_funded_creation(db,user_id='owner',account_id='123',grant_key=key,
        execution_claim_id=row['execution_claim_id'])

@pytest.mark.asyncio
async def test_actual_reader_reservation_floor_original_graph_settlement_one_shot(monkeypatch):
    db,provider,key,row=await setup(monkeypatch)
    result=await reserve(db,key,row)
    assert result['committed'] is True and result['funded_dispatch_authorized'] is False and provider.writes==0
    result=await floor(db,key,row)
    assert result['authority_expired_after_commit'] is False and result['funded_dispatch_authorized'] is False
    receipt=await provider.accept_original(row['operations'])
    names=authority.mutation_resource_names(receipt,row['operations'],'123')
    await db[management.PROPOSALS].update_one({'proposal_id':row['proposal_id']},{'$set':{
        'provider_receipt':receipt,'resource_names':names,'status':'verifying'}})
    result=await settle(db,key,row)
    assert result['settled'] is True and result['quota_refunded'] is False and result['native_fence_released'] is False
    state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    assert state['epoch']==4 and state['active_claim'] is None
    entry=next(iter(state['allocations'].values()))
    assert entry['campaign_id']=='777' and entry['budget_resource']=='customers/123/campaignBudgets/1000'
    assert entry['state']=='native_enabled' and entry['budget_native']=='15'
    assert (await db[grants.GRANTS].find_one({'grant_key':key}))['consumed'] is True
    with pytest.raises(HTTPException):await reserve(db,key,row)
    with pytest.raises(HTTPException):await floor(db,key,row)
    assert provider.writes==1 # one simulated ORIGINAL external acceptance, zero helper sends

@pytest.mark.asyncio
async def test_configured_grant_cannot_activate_when_runtime_changes(monkeypatch):
    db,provider,key,row=await setup(monkeypatch,active=False)
    with pytest.raises(HTTPException) as error:await reserve(db,key,row)
    assert error.value.detail['code']=='google_funded_grant_inactive_or_consumed' and provider.writes==0
    assert not await db[grants.RUNS].find({}).to_list(length=1)

@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['before_reservation','after_reservation'])
async def test_owner_revoke_has_no_oauth_and_blocks_next_boundary(monkeypatch,phase):
    db,provider,key,row=await setup(monkeypatch)
    if phase=='after_reservation':await reserve(db,key,row)
    async def unavailable(*args,**kwargs):raise AssertionError('OAuth unavailable')
    monkeypatch.setattr(management,'account_client',unavailable)
    await grants.revoke_google_funded_grant(db,user={'id':'owner'},account_id='123',request_key='reviewed_launch_1',expected_revision=1)
    with pytest.raises(HTTPException):
        if phase=='after_reservation':await floor(db,key,row)
        else:await reserve(db,key,row)
    assert provider.writes==0 and (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    record=await db[grants.GRANTS].find_one({'grant_key':key})
    assert record['state']=='revoked' and record['consumed'] is (phase=='after_reservation')

@pytest.mark.asyncio
async def test_known_cancel_settles_without_oauth_but_never_refunds_slot(monkeypatch):
    db,provider,key,row=await setup(monkeypatch);await reserve(db,key,row)
    await db[management.PROPOSALS].update_one({'proposal_id':row['proposal_id']},{'$set':{'status':'failed',
        'submission_started':False,'provider_write_reached':False,'safe_error':'execution_cancelled_before_dispatch'}})
    async def unavailable(*args,**kwargs):raise AssertionError('OAuth unavailable')
    monkeypatch.setattr(management,'account_client',unavailable)
    monkeypatch.setattr(guard,'budget_guard_runtime_available',lambda:False)
    result=await settle(db,key,row)
    assert result['settled'] is True and result['quota_refunded'] is False
    entry=next(iter((await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'}))['allocations'].values()))
    assert entry['outcome']=='cancelled_before_http' and entry['state']=='settled'
    assert (await db[grants.GRANTS].find_one({'grant_key':key}))['consumed'] is True and provider.writes==0
