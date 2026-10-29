"""Disposable replica-only funded storage races; never calls paid Google mutate."""
import asyncio
from copy import deepcopy
import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.write_concern import WriteConcern
from integrations_control_center import google_ads_funded_creation_authority as authority
from integrations_control_center import google_ads_funded_creation_grants as grants
from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center import google_ads_management as management
from tests.test_google_ads_paused_creation_budget_mongo import actual_db,clock,automation_mongo_uri,guard_mongo_contract
from tests.test_google_ads_funded_creation_authority import setup,reserve,floor,settle,NOW,seed_future_preparation
from tests.test_google_ads_funded_creation_grants import authenticated_user

async def held(db,key,*,phase='reserved'):
    state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    journal=await db[grants.RUNS].find_one({'grant_key':key})
    assert state['active_claim'] and journal['phase']==phase
    assert next(iter(state['allocations'].values()))['state']=='reserved'
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    return state,journal

@pytest.mark.asyncio
async def test_replica_actual_reserve_floor_consumes_continuation_once(monkeypatch,actual_db):
    db,capture=actual_db;db,provider,key,row=await setup(monkeypatch,db)
    baseline=capture.names.count('commitTransaction')
    await reserve(db,key,row);result=await floor(db,key,row)
    assert result['funded_dispatch_authorized'] is False
    await held(db,key,phase='submitted')
    assert capture.names.count('commitTransaction')-baseline==2
    assert provider.writes==0
    with pytest.raises(HTTPException):await floor(db,key,row)

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['disabled','role','password'])
@pytest.mark.parametrize('stage',['reserve','floor'])
async def test_replica_owner_change_before_capture_rejects_stale_grant(monkeypatch,actual_db,automation_mongo_uri,change,stage):
    db,capture=actual_db;db,provider,key,row=await setup(monkeypatch,db)
    if stage=='floor':await reserve(db,key,row)
    else:row['_initiation_ack']=await seed_future_preparation(db,key,row)
    other=AsyncIOMotorClient(automation_mongo_uri,serverSelectionTimeoutMS=2000,retryWrites=True)
    try:
        values={'disabled':True} if change=='disabled' else {'role':'staff'} if change=='role' else {'password_updated_at':NOW,'password_hash':'test-only-changed-value'}
        result=await other[db.name].users.with_options(write_concern=WriteConcern('majority',j=True)).update_one({'id':'owner'},{'$set':values})
        assert result.matched_count==1
        before=capture.names.count('commitTransaction')
        with pytest.raises(HTTPException):await (reserve(db,key,row) if stage=='reserve' else floor(db,key,row))
        assert capture.names.count('commitTransaction')==before and provider.writes==0
        if stage=='floor':await held(db,key)
        else:
            assert (await db[grants.RUNS].find_one({'grant_key':key}))['phase']=='prepared'
            assert (await db[grants.GRANTS].find_one({'grant_key':key}))['consumed'] is True
    finally:other.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['disabled','role','password'])
@pytest.mark.parametrize('stage',['reserve','floor'])
async def test_replica_owner_writer_between_snapshot_and_marker_conflicts(monkeypatch,actual_db,automation_mongo_uri,change,stage):
    db,capture=actual_db;db,provider,key,row=await setup(monkeypatch,db)
    if stage=='floor':await reserve(db,key,row)
    else:row['_initiation_ack']=await seed_future_preparation(db,key,row)
    other=AsyncIOMotorClient(automation_mongo_uri,serverSelectionTimeoutMS=2000,retryWrites=True)
    original=authority._owner_write_fence;writes=[]
    async def changed(view,**kwargs):
        values={'disabled':True} if change=='disabled' else {'role':'staff'} if change=='role' else {'password_updated_at':NOW,'password_hash':'test-only-changed-value'}
        # Different client writes after the transaction already read users.
        result=await other[db.name].users.with_options(write_concern=WriteConcern('majority',j=True)).update_one({'id':'owner'},{'$set':values})
        assert result.matched_count==1;writes.append(True)
        return await original(view,**kwargs)
    monkeypatch.setattr(authority,'_owner_write_fence',changed)
    before=capture.names.count('commitTransaction')
    try:
        with pytest.raises(HTTPException) as error:await (reserve(db,key,row) if stage=='reserve' else floor(db,key,row))
        assert error.value.detail['preserve_execution_fence'] is True
        assert writes==[True] and capture.names.count('commitTransaction')==before and provider.writes==0
        if stage=='floor':await held(db,key)
        else:assert (await db[grants.RUNS].find_one({'grant_key':key}))['phase']=='prepared'
    finally:other.close()

@pytest.mark.asyncio
async def test_replica_users_validator_rejects_marker_without_partial_claim(monkeypatch,actual_db):
    db,capture=actual_db;db,provider,key,row=await setup(monkeypatch,db)
    # Validator permits the existing owner and disallows only the new marker.
    await db.command({'collMod':'users','validator':{'mezan_google_funded_authority_epoch':{'$exists':False}},'validationLevel':'strict'})
    with pytest.raises(HTTPException):await reserve(db,key,row)
    assert (await db[grants.RUNS].find_one({'grant_key':key}))['phase']=='prepared'
    assert (await db[grants.GRANTS].find_one({'grant_key':key}))['consumed'] is True
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    assert provider.writes==0

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['server91','connection_close','ack_loss_after_commit'])
async def test_replica_unknown_floor_retains_and_restart_has_no_continuation(monkeypatch,actual_db,automation_mongo_uri,fault):
    import time
    db,capture=actual_db;db,provider,key,row=await setup(monkeypatch,db)
    await reserve(db,key,row)
    before=capture.names.count('commitTransaction')
    if fault=='ack_loss_after_commit':
        original=guard._commit_once
        async def lost(*args):
            await original(*args)
            raise TimeoutError('test-only simulated ACK loss')
        monkeypatch.setattr(guard,'_commit_once',lost)
    else:
        data={'failCommands':['commitTransaction']}
        data.update({'errorCode':91} if fault=='server91' else {'closeConnection':True})
        await db.client.admin.command({'configureFailPoint':'failCommand','mode':{'times':1},'data':data})
    started=time.monotonic()
    try:
        with pytest.raises(HTTPException) as error:await floor(db,key,row)
        elapsed=time.monotonic()-started
    finally:await db.client.admin.command({'configureFailPoint':'failCommand','mode':'off'})
    assert error.value.detail['commit_attempted'] is True
    assert error.value.detail['preserve_execution_fence'] is True
    assert error.value.detail['automatic_retry_allowed'] is False
    assert elapsed<=20 and provider.writes==0
    assert capture.names.count('commitTransaction')==before+1
    await held(db,key,phase='submitted' if fault=='ack_loss_after_commit' else 'reserved')
    # Both a burned local ACK and a restarted client without the private ACK fail
    # before any native preparation or another wire commit.
    with pytest.raises(HTTPException):await floor(db,key,row)
    other=AsyncIOMotorClient(automation_mongo_uri,serverSelectionTimeoutMS=2000,retryWrites=True)
    try:
        with pytest.raises(HTTPException) as replay:
            await authority.floor_google_funded_creation(other[db.name],user_id='owner',account_id='123',grant_key=key,
                proposal_id=row['proposal_id'],execution_claim_id=row['execution_claim_id'])
        assert replay.value.detail['code']=='google_funded_reservation_ack_unproved'
    finally:other.close()
    assert capture.names.count('commitTransaction')==before+1 and provider.writes==0
    print({'fault':fault,'http_helper_seconds':round(elapsed,3),'floor_wire_attempts':1,'hold_retained':True})

@pytest.mark.asyncio
@pytest.mark.parametrize('action',['revoke','cancel'])
@pytest.mark.parametrize('timing',['after_tx_reads','after_floor_ack'])
async def test_replica_independent_owner_revoke_cancel_orders_floor(monkeypatch,actual_db,automation_mongo_uri,action,timing):
    db,capture=actual_db;db,provider,key,row=await setup(monkeypatch,db);await reserve(db,key,row)
    other=AsyncIOMotorClient(automation_mongo_uri,serverSelectionTimeoutMS=2000,retryWrites=True)
    worker=other[db.name]
    async def owner_write():
        if action=='revoke':
            # Prove safety revocation has no dependency on an OAuth/provider read.
            original_client=management.account_client
            async def unavailable(*args,**kwargs):raise AssertionError('OAuth offline during revoke')
            monkeypatch.setattr(management,'account_client',unavailable)
            try:
                await grants.revoke_google_funded_grant(worker,user=await worker.users.find_one({'id':'owner'}),
                    account_id='123',request_key='reviewed_launch_1',expected_revision=1)
            finally:monkeypatch.setattr(management,'account_client',original_client)
        else:
            changed=await worker[management.PROPOSALS].with_options(write_concern=WriteConcern('majority',j=True)).update_one(
                {'proposal_id':row['proposal_id']},{'$set':{'status':'failed','safe_error':'execution_cancelled_before_dispatch'}})
            assert changed.matched_count==1
    before=capture.names.count('commitTransaction')
    if timing=='after_tx_reads':
        original=authority._bound
        async def changed(*args,**kwargs):
            result=await original(*args,**kwargs);await owner_write();return result
        monkeypatch.setattr(authority,'_bound',changed)
    try:
        if timing=='after_tx_reads':
            with pytest.raises(HTTPException) as error:await floor(db,key,row)
            assert error.value.detail['commit_attempted'] is False
            await held(db,key)
        else:
            result=await floor(db,key,row)
            assert result['committed'] is True and result['funded_dispatch_authorized'] is False
            await owner_write();await held(db,key,phase='submitted')
        assert provider.writes==0
        # Other client commits are deliberately absent from this client's counter.
        assert capture.names.count('commitTransaction')==before+(timing=='after_floor_ack')
        with pytest.raises(HTTPException):await floor(db,key,row)
    finally:other.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('timing',['waiting','before_commit','during_ack'])
async def test_replica_floor_expiry_in_all_three_phases(monkeypatch,actual_db,timing):
    from datetime import timedelta
    from tests.test_google_ads_standing_dispatch_floor import advance
    db,capture=actual_db;db,provider,key,row=await setup(monkeypatch,db)
    record=await db[grants.GRANTS].find_one({'grant_key':key},{'_id':0})
    record['grant']['expires_at']=(NOW+timedelta(seconds=20)).isoformat()
    record['review']['expires_at']=record['grant']['expires_at']
    digest=grants.review_digest(record['review']);record['review_digest']=digest
    record['grant']['owner_review_digest']=digest;record['activation_review_digest']=digest
    await db[grants.GRANTS].update_one({'grant_key':key},{'$set':record})
    intent=authority.pure.prepare_google_funded_creation_intent(grant=record['grant'],draft=record['review']['draft'],current=NOW)
    row['idempotency_key']=intent['creation_identity'];row['automation_source']['creation_identity']=intent['creation_identity']
    row['operations']=intent['request']['mutateOperations'];row['confirmation_digest']=management.digest(management.immutable(row))
    await db[management.PROPOSALS].update_one({'proposal_id':row['proposal_id']},{'$set':row})
    await reserve(db,key,row);before=capture.names.count('commitTransaction')
    if timing=='waiting':advance(monkeypatch,NOW+timedelta(seconds=21))
    elif timing=='before_commit':
        original=authority._guard_funded_command
        async def elapsed(*args,**kwargs):
            result=await original(*args,**kwargs)
            if isinstance(kwargs['command'],guard.FundedCreationDispatchFloor):advance(monkeypatch,NOW+timedelta(seconds=21))
            return result
        monkeypatch.setattr(authority,'_guard_funded_command',elapsed)
    else:
        original=guard._commit_once
        async def elapsed(*args):
            result=await original(*args);advance(monkeypatch,NOW+timedelta(seconds=21));return result
        monkeypatch.setattr(guard,'_commit_once',elapsed)
    if timing=='during_ack':
        result=await floor(db,key,row)
        assert result['authority_expired_after_commit'] is True and result['funded_dispatch_authorized'] is False
    else:
        with pytest.raises(HTTPException):await floor(db,key,row)
    await held(db,key,phase='submitted' if timing=='during_ack' else 'reserved')
    assert provider.writes==0 and capture.names.count('commitTransaction')==before+(timing=='during_ack')
    with pytest.raises(HTTPException):await floor(db,key,row)

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['relink','disconnect','account_removal'])
async def test_replica_google_scope_writer_after_prepare_is_seen_by_floor(monkeypatch,actual_db,automation_mongo_uri,change):
    db,capture=actual_db;db,provider,key,row=await setup(monkeypatch,db);await reserve(db,key,row)
    other=AsyncIOMotorClient(automation_mongo_uri,serverSelectionTimeoutMS=2000,retryWrites=True)
    original=authority._prepare
    async def changed(*args,**kwargs):
        prepared=await original(*args,**kwargs)
        name=(management.GOOGLE_CREDENTIALS_COLLECTION if change=='relink' else
              'mezan_integrations_v2' if change=='disconnect' else 'mezan_integration_accounts_v2')
        values=({'google_subject':'test-only-relinked-subject'} if change=='relink' else
                {'connection_status':'error'} if change=='disconnect' else {'ad_account_id':'456','external_account_id':'456'})
        result=await other[db.name][name].with_options(write_concern=WriteConcern('majority',j=True)).update_one(
            {'user_id':'owner'},{'$set':values})
        assert result.matched_count==1
        return prepared
    monkeypatch.setattr(authority,'_prepare',changed)
    before=capture.names.count('commitTransaction')
    try:
        with pytest.raises(HTTPException) as error:await floor(db,key,row)
        assert error.value.detail['code']=='google_funded_google_scope_changed'
        await held(db,key)
        assert capture.names.count('commitTransaction')==before and provider.writes==0
    finally:other.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['server91','connection_close','ack_loss_after_commit'])
async def test_replica_unknown_reservation_prepared_or_reserved_cannot_reinitiate(monkeypatch,actual_db,automation_mongo_uri,fault):
    import time
    db,capture=actual_db;db,provider,key,row=await setup(monkeypatch,db)
    row['_initiation_ack']=await seed_future_preparation(db,key,row)
    before=capture.names.count('commitTransaction')
    if fault=='ack_loss_after_commit':
        original=guard._commit_once
        async def lost(*args):await original(*args);raise TimeoutError('test-only ACK lost after real commit')
        monkeypatch.setattr(guard,'_commit_once',lost)
    else:
        data={'failCommands':['commitTransaction']}
        data.update({'errorCode':91} if fault=='server91' else {'closeConnection':True})
        await db.client.admin.command({'configureFailPoint':'failCommand','mode':{'times':1},'data':data})
    started=time.monotonic()
    try:
        with pytest.raises(HTTPException) as error:await reserve(db,key,row)
        elapsed=time.monotonic()-started
    finally:await db.client.admin.command({'configureFailPoint':'failCommand','mode':'off'})
    assert error.value.detail['commit_attempted'] is True and error.value.detail['preserve_execution_fence'] is True
    assert error.value.detail['automatic_retry_allowed'] is False and elapsed<=20
    run=await db[grants.RUNS].find_one({'grant_key':key})
    assert run['phase']==('reserved' if fault=='ack_loss_after_commit' else 'prepared')
    assert (await db[grants.GRANTS].find_one({'grant_key':key}))['consumed'] is True
    state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    if fault=='ack_loss_after_commit':await held(db,key)
    else:assert state['active_claim'] is None and state['allocations']=={}
    assert row['_initiation_ack'].used and provider.writes==0
    with pytest.raises(HTTPException):await reserve(db,key,row)
    other=AsyncIOMotorClient(automation_mongo_uri,serverSelectionTimeoutMS=2000,retryWrites=True)
    try:
        with pytest.raises(HTTPException) as restarted:
            await authority.reserve_google_funded_creation(other[db.name],user_id='owner',account_id='123',grant_key=key,
                proposal_id=row['proposal_id'],execution_claim_id=row['execution_claim_id'])
        assert restarted.value.detail['code']=='google_funded_initiation_ack_unproved'
    finally:other.close()
    assert capture.names.count('commitTransaction')==before+1
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    print({'reserve_fault':fault,'helper_seconds':round(elapsed,3),'wire_commit_attempts':1,'journal_phase':run['phase']})

@pytest.mark.asyncio
async def test_replica_revoke_and_known_cancel_ignore_oauth_and_users_update_validator(monkeypatch,actual_db):
    db,capture=actual_db;db,provider,key,row=await setup(monkeypatch,db);await reserve(db,key,row)
    before=await db.users.find_one({'id':'owner'})
    # Existing document may retain its marker; any future users write fails.
    await db.command({'collMod':'users','validator':{'id':{'$eq':'impossible-test-id'}},'validationLevel':'strict'})
    async def unavailable(*args,**kwargs):raise AssertionError('No OAuth/provider reader permitted on safety path')
    monkeypatch.setattr(management,'account_client',unavailable)
    monkeypatch.setattr(guard,'budget_guard_runtime_available',lambda:False)
    result=await grants.revoke_google_funded_grant(db,user=before,account_id='123',request_key='reviewed_launch_1',expected_revision=1)
    assert result['state']=='revoked'
    await db[management.PROPOSALS].update_one({'proposal_id':row['proposal_id']},{'$set':{'status':'failed',
        'submission_started':False,'provider_write_reached':False,'safe_error':'execution_cancelled_before_dispatch'}})
    result=await settle(db,key,row)
    assert result['settled'] is True and result['quota_refunded'] is False and result['native_fence_released'] is False
    assert await db.users.find_one({'id':'owner'})==before
    assert (await db[grants.GRANTS].find_one({'grant_key':key}))['consumed'] is True
    assert (await db[grants.RUNS].find_one({'grant_key':key}))['phase']=='settled'
    assert provider.writes==0

@pytest.mark.asyncio
async def test_replica_original_provider_graph_settlement_keeps_positive_native_hold(monkeypatch,actual_db):
    db,capture=actual_db;db,provider,key,row=await setup(monkeypatch,db);await reserve(db,key,row);await floor(db,key,row)
    assert provider.writes==0
    # Simulated ORIGINAL acceptance only; storage helpers never send paid mutate.
    receipt=await provider.accept_original(row['operations'])
    names=authority.mutation_resource_names(receipt,row['operations'],'123')
    await db[management.PROPOSALS].update_one({'proposal_id':row['proposal_id']},{'$set':{
        'provider_receipt':receipt,'resource_names':names,'status':'verifying'}})
    result=await settle(db,key,row)
    assert result['settled'] is True and result['quota_refunded'] is False
    state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    allocation=next(iter(state['allocations'].values()))
    assert state['active_claim'] is None and allocation['state']=='native_enabled'
    assert allocation['campaign_id']=='777' and allocation['budget_resource']=='customers/123/campaignBudgets/1000'
    assert allocation['budget_native']=='15' and allocation['settlement_proof_digest']
    assert (await db[grants.RUNS].find_one({'grant_key':key}))['phase']=='settled'
    assert (await db[grants.GRANTS].find_one({'grant_key':key}))['consumed'] is True
    with pytest.raises(HTTPException):await reserve(db,key,row)
    assert provider.writes==1 # seeded original outcome; zero helper-funded sends

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['disabled','role','password'])
async def test_replica_owner_writer_after_floor_user_marker_orders_after_transaction(monkeypatch,actual_db,automation_mongo_uri,change):
    from pymongo.monitoring import CommandListener
    db,capture=actual_db;db,provider,key,row=await setup(monkeypatch,db);await reserve(db,key,row)
    before=await db.users.find_one({'id':'owner'});loop=asyncio.get_running_loop();sent=asyncio.Event();writers=[]
    class Started(CommandListener):
        def started(self,event):
            if event.command_name=='update':loop.call_soon_threadsafe(sent.set)
        def succeeded(self,event):pass
        def failed(self,event):pass
    other=AsyncIOMotorClient(automation_mongo_uri,serverSelectionTimeoutMS=2000,retryWrites=True,event_listeners=[Started()])
    await other.admin.command('ping')
    original=authority._owner_write_fence
    async def after_marker(view,**kwargs):
        result=await original(view,**kwargs)
        values={'disabled':True} if change=='disabled' else {'role':'staff'} if change=='role' else {
            'password_updated_at':NOW,'password_hash':'test-only-after-marker-value'}
        async def write():
            return await other[db.name].users.with_options(write_concern=WriteConcern('majority',j=True)).update_one(
                {'id':'owner'},{'$set':values})
        writers.append(asyncio.create_task(write()))
        await asyncio.wait_for(sent.wait(),timeout=3)
        # Independent update is on the wire while this transaction owns users.
        # It cannot commit that same document ahead of our floor commit.
        assert not writers[0].done()
        return result
    monkeypatch.setattr(authority,'_owner_write_fence',after_marker)
    try:
        result=await floor(db,key,row)
        assert result['committed'] is True and result['funded_dispatch_authorized'] is False
        changed=await asyncio.wait_for(asyncio.shield(writers[0]),timeout=5)
        assert changed.matched_count==1
        await held(db,key,phase='submitted')
        after=await db.users.find_one({'id':'owner'})
        mutable={'mezan_google_funded_authority_epoch',*({'disabled'} if change=='disabled' else {'role'} if change=='role' else {'password_updated_at','password_hash'})}
        assert {k:v for k,v in after.items() if k not in mutable}=={k:v for k,v in before.items() if k not in mutable}
        with pytest.raises(HTTPException):await floor(db,key,row)
        assert provider.writes==0
    finally:
        if writers:await asyncio.wait_for(asyncio.gather(*writers,return_exceptions=True),timeout=10)
        other.close()
