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
    # Fake values only; match actual account_client epoch projection contract.
    credential={'user_id':'owner','refresh_token_ciphertext':'fake-encrypted-test-value',
        'scope':'fake-test-scope','google_subject':'fake-test-subject'}
    await db[management.GOOGLE_CREDENTIALS_COLLECTION].insert_one(credential)
    for collection in ('mezan_integration_accounts_v2','mezan_integrations_v2'):
        query={'user_id':'owner','provider':'google_ads'}
        values={**query,'connection_status':'connected','connection_provenance':'api_connection','ad_account_id':'123'}
        if await db[collection].find_one(query):await db[collection].update_one(query,{'$set':values})
        else:await db[collection].insert_one(values)
    epoch=management.digest({key:value for key,value in credential.items() if key!='user_id'})
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
        return provider,epoch
    monkeypatch.setattr(management,'account_client',client)
    preview=await review(db,body);await save(db,body,preview['review_digest'])
    key=grants.grant_key('owner','123',request_key)
    policies=guard.BudgetGuardSnapshot('owner','123',{},
        await db[guard.AUTOMATION_POLICIES].find_one({'user_id':'owner','account_id':'123'},{'_id':0}),
        await db[guard.CREATION_POLICIES].find_one({'user_id':'owner','account_id':'123'},{'_id':0}))
    if active:
        # Test-only stand-in for a FUTURE authenticated activation producer;
        # no application function can activate configured grants in this slice.
        await db[grants.GRANTS].update_one({'grant_key':key},{'$set':{'state':'active','activation_version':1,
            'activation_revision':1,'activated_by':'owner','activated_at':NOW.isoformat(),
            'activation_review_digest':preview['review_digest'],'activation_policy_binding':authority.policy_binding(policies)}})
    intent=authority.pure.prepare_google_funded_creation_intent(grant=preview['grant'],draft=preview['draft'],current=NOW)
    row={'user_id':'owner','account_id':'123','proposal_id':'funded_proposal1','execution_claim_id':'funded_claim1',
        'actor_id':'mezan_google_automation_v1','provider':'google_ads','action':'create_campaign','campaign_id':None,
        'before_digest':None,'before_snapshot':None,'auth_epoch':epoch,'currency':'SAR','time_zone':'Asia/Riyadh',
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
    result=await authority.reserve_google_funded_creation(db,user_id='owner',account_id='123',grant_key=key,
        proposal_id=row['proposal_id'],execution_claim_id=row['execution_claim_id'])
    row['_reservation_ack']=result['_reservation_ack']
    return result

async def floor(db,key,row):
    return await authority.floor_google_funded_creation(db,user_id='owner',account_id='123',grant_key=key,
        proposal_id=row['proposal_id'],execution_claim_id=row['execution_claim_id'],reservation_ack=row.get('_reservation_ack'))

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
    await grants.revoke_google_funded_grant(db,user=await db.users.find_one({'id':'owner'}),account_id='123',request_key='reviewed_launch_1',expected_revision=1)
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

@pytest.mark.asyncio
@pytest.mark.parametrize('stage',['reserve','floor'])
async def test_unknown_commit_retains_one_shot_and_cannot_retry_or_recover_permission(monkeypatch,stage):
    db,provider,key,row=await setup(monkeypatch)
    if stage=='floor':await reserve(db,key,row)
    async def lost():raise TimeoutError('postcommit storage ACK lost')
    db.after_commit=lost
    with pytest.raises(HTTPException) as error:
        if stage=='reserve':await reserve(db,key,row)
        else:await floor(db,key,row)
    assert error.value.detail['commit_attempted'] is True and error.value.detail['automatic_retry_allowed'] is False
    db.after_commit=None
    run=await db[grants.RUNS].find_one({'grant_key':key})
    state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    assert run['consumed'] and run['phase']==('submitted' if stage=='floor' else 'reserved')
    assert state['active_claim'] and next(iter(state['allocations'].values()))['budget_native']=='15'
    reads=provider.previews
    with pytest.raises(HTTPException):await reserve(db,key,row)
    with pytest.raises(HTTPException):await floor(db,key,row)
    assert provider.previews==reads and provider.writes==0
    receipt=await guard.read_google_budget_guard_commit(db,user_id='owner',account_id='123',operation_id=state['last_commit']['operation_id'])
    assert receipt['provider_dispatch_authorized'] is False
    with pytest.raises(HTTPException):await authority.floor_google_funded_creation(db,user_id='owner',account_id='123',
        grant_key=key,proposal_id=row['proposal_id'],execution_claim_id=row['execution_claim_id'],reservation_ack=receipt)
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'

@pytest.mark.asyncio
@pytest.mark.parametrize('timing',['waiting','before_commit','during_ack'])
async def test_floor_expiry_keeps_hold_and_burned_continuation(monkeypatch,timing):
    db,provider,key,row=await setup(monkeypatch)
    record=await db[grants.GRANTS].find_one({'grant_key':key})
    # Controlled valid owner review: rebuild exact canonical review/hash after
    # shortening expiry before any reservation, not a caller expiry override.
    record['grant']['expires_at']=(NOW+timedelta(seconds=20)).isoformat()
    record['review']['expires_at']=record['grant']['expires_at']
    digest=grants.review_digest(record['review']);record['review_digest']=digest
    record['grant']['owner_review_digest']=digest;record['activation_review_digest']=digest
    await db[grants.GRANTS].update_one({'grant_key':key},{'$set':record})
    intent=authority.pure.prepare_google_funded_creation_intent(grant=record['grant'],draft=record['review']['draft'],current=NOW)
    row['idempotency_key']=intent['creation_identity'];row['automation_source']['creation_identity']=intent['creation_identity']
    # Label uses the funded identity, so the exact operation graph changes too.
    row['operations']=intent['request']['mutateOperations'];row['confirmation_digest']=management.digest(management.immutable(row))
    await db[management.PROPOSALS].update_one({'proposal_id':row['proposal_id']},{'$set':row})
    await reserve(db,key,row)
    if timing=='waiting':advance(monkeypatch,NOW+timedelta(seconds=21))
    elif timing=='before_commit':
        original=authority._guard_funded_command
        async def elapsed(*args,**kwargs):
            result=await original(*args,**kwargs)
            if isinstance(kwargs['command'],guard.FundedCreationDispatchFloor):advance(monkeypatch,NOW+timedelta(seconds=21))
            return result
        monkeypatch.setattr(authority,'_guard_funded_command',elapsed)
    else:
        async def elapsed():advance(monkeypatch,NOW+timedelta(seconds=21))
        db.after_commit=elapsed
    if timing=='during_ack':
        result=await floor(db,key,row)
        assert result['authority_expired_after_commit'] is True and result['funded_dispatch_authorized'] is False
    else:
        with pytest.raises(HTTPException):await floor(db,key,row)
    db.after_commit=None
    run=await db[grants.RUNS].find_one({'grant_key':key})
    assert run['phase']==('submitted' if timing=='during_ack' else 'reserved')
    assert (await db[grants.GRANTS].find_one({'grant_key':key}))['consumed'] is True
    with pytest.raises(HTTPException):await floor(db,key,row)
    assert provider.writes==0 and (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['operations','auth_epoch','currency','owner','product','draft','hard_spend','claim','allocation','native_drift'])
async def test_fresh_floor_refuses_changed_sources_without_releasing(monkeypatch,change):
    db,provider,key,row=await setup(monkeypatch);await reserve(db,key,row)
    if change in {'operations','auth_epoch','currency'}:
        update={'operations':[]} if change=='operations' else {change:'changed'}
        await db[management.PROPOSALS].update_one({'proposal_id':row['proposal_id']},{'$set':update})
    elif change=='owner':await db.users.update_one({'id':'owner'},{'$set':{'disabled':True}})
    elif change=='product':await db[authority.PRODUCT_COLLECTION].update_one({'user_id':'owner'},{'$set':{'quantity':0}})
    elif change=='draft':await db[management.DRAFTS].update_one({'draft_id':'reviewdraft'},{'$set':{'revision':2}})
    elif change=='hard_spend':await db[grants.GRANTS].update_one({'grant_key':key},{'$set':{'spend_semantics':'HARD_TOTAL_SPEND'}})
    elif change=='claim':await db[grants.GRANTS].update_one({'grant_key':key},{'$set':{'claim':None}})
    elif change=='allocation':
        state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'});allocations=deepcopy(state['allocations'])
        next(iter(allocations.values()))['budget_native']='1'
        await db[guard.BUDGET_GUARDS].update_one({'account_id':'123'},{'$set':{'allocations':allocations}})
    else:
        original=provider.search
        async def changed(query,**kwargs):
            rows,complete=await original(query,**kwargs)
            if 'FROM campaign 'in query:rows[0]['campaignBudget']['amountMicros']='16000000'
            return rows,complete
        monkeypatch.setattr(provider,'search',changed)
    with pytest.raises(HTTPException):await floor(db,key,row)
    assert provider.writes==0 and (await db[grants.RUNS].find_one({'grant_key':key}))['phase']=='reserved'
    assert (await db[grants.GRANTS].find_one({'grant_key':key}))['consumed'] is True
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'

@pytest.mark.asyncio
async def test_arbitrary_native_flags_cannot_mint_prepared_proof(monkeypatch):
    db,provider,key,row=await setup(monkeypatch)
    capability=await guard.probe_google_budget_guard_transactions(db)
    with pytest.raises(HTTPException) as error:
        await guard.run_google_budget_guard_transaction(db,user_id='owner',account_id='123',operation_id='forged_native_bundle',
            capability=capability,planner=lambda snapshot:guard.FundedCreationReservation(key,row['proposal_id'],row['execution_claim_id'],
                {'identity_verified':True,'complete':True,'provider_validated':True}))
    assert error.value.detail['code']=='google_funded_native_bundle_unproved'
    assert not await db[grants.RUNS].find({}).to_list(length=1) and provider.writes==0

@pytest.mark.asyncio
async def test_owner_marker_changes_only_approved_field_and_is_not_revocation_dependency(monkeypatch):
    db,provider,key,row=await setup(monkeypatch)
    await db.users.update_one({'id':'owner'},{'$set':{'email':'test.invalid','extra_permissions':['unchanged'],'password_hash':'test-only-not-a-secret'}})
    before=await db.users.find_one({'id':'owner'});await reserve(db,key,row);await floor(db,key,row)
    after=await db.users.find_one({'id':'owner'})
    assert {key:value for key,value in after.items() if key!='mezan_google_funded_authority_epoch'}==before
    assert after['mezan_google_funded_authority_epoch'].startswith('funded_floor_')
    from pymongo.errors import OperationFailure
    # Positive paths use users.update; safe revocation does not use the marker.
    async def denied(*args,**kwargs):raise OperationFailure('test-only denied marker',code=13)
    monkeypatch.setattr(authority,'_owner_write_fence',denied)
    result=await grants.revoke_google_funded_grant(db,user=await db.users.find_one({'id':'owner'}),account_id='123',request_key='reviewed_launch_1',expected_revision=1)
    assert result['state']=='revoked' and provider.writes==0

@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['before_capture','between_reservation_and_floor'])
async def test_stored_grant_auth_rejects_password_change_before_marker_snapshot(monkeypatch,phase):
    from tests.test_google_ads_funded_creation_grants import authenticated_user
    db,provider,key,row=await setup(monkeypatch)
    user=await authenticated_user(db,monkeypatch,issued_at=NOW-timedelta(minutes=1))
    record=await db[grants.GRANTS].find_one({'grant_key':key})
    assert record['review']['_owner_auth_binding']==(await grants.capture_owner_auth(db,user))[1]
    if phase=='between_reservation_and_floor':await reserve(db,key,row)
    await db.users.update_one({'id':'owner'},{'$set':{'password_updated_at':NOW-timedelta(seconds=1),
        'password_hash':'test-only-new-password-hash'}})
    with pytest.raises(HTTPException) as error:
        if phase=='before_capture':await reserve(db,key,row)
        else:await floor(db,key,row)
    assert error.value.detail['code']=='google_funded_owner_auth_changed'
    assert provider.writes==0
    if phase=='between_reservation_and_floor':
        fresh=await authenticated_user(db,monkeypatch,issued_at=NOW)
        result=await grants.revoke_google_funded_grant(db,user=fresh,account_id='123',request_key='reviewed_launch_1',expected_revision=1)
        assert result['state']=='revoked' and (await db[grants.GRANTS].find_one({'grant_key':key}))['claim']
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['relink','disconnect','account_removal'])
async def test_floor_snapshot_refuses_google_scope_drift_after_prepare(monkeypatch,change):
    db,provider,key,row=await setup(monkeypatch)
    await reserve(db,key,row)
    original=authority._prepare
    async def changed_after_native_reads(*args,**kwargs):
        prepared=await original(*args,**kwargs)
        if change=='relink':
            await db[management.GOOGLE_CREDENTIALS_COLLECTION].update_one({'user_id':'owner'},
                {'$set':{'google_subject':'fake-relinked-test-subject'}})
        elif change=='disconnect':
            await db.mezan_integrations_v2.update_one({'user_id':'owner','provider':'google_ads'},
                {'$set':{'connection_status':'error'}})
        else:
            await db.mezan_integration_accounts_v2.update_one({'user_id':'owner','provider':'google_ads'},
                {'$set':{'ad_account_id':'456'}})
        return prepared
    monkeypatch.setattr(authority,'_prepare',changed_after_native_reads)
    with pytest.raises(HTTPException) as error:await floor(db,key,row)
    assert error.value.detail['code']=='google_funded_google_scope_changed'
    state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    assert state['active_claim'] is not None
    assert next(iter(state['allocations'].values()))['state']=='reserved'
    assert (await db[grants.RUNS].find_one({'grant_key':key}))['phase']=='reserved'
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    assert provider.writes==0
    with pytest.raises(HTTPException):await floor(db,key,row)
