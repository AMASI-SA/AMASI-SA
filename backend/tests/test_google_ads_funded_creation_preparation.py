"""Actual internal preparation producer; fake external/native/metadata transport."""
import asyncio
from copy import deepcopy
import pytest
from fastapi import HTTPException
from integrations_control_center import google_ads_funded_creation_preparation as preparation
from integrations_control_center import google_ads_funded_creation_grants as grants
from integrations_control_center import google_ads_funded_creation_authority as authority
from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center import google_ads_management as management
from tests.test_google_ads_funded_creation_authority import setup as old_setup
from tests.test_google_ads_funded_creation_grants import setup as grant_setup,review,save
from tests.test_google_ads_automation_creation_gateway import NativeCreationProvider
from tests.test_google_ads_standing_dispatch_floor import NOW,advance

@pytest.fixture(autouse=True)
def process_resources(monkeypatch):
    monkeypatch.setattr(preparation,'_admitted_slots',set())
    monkeypatch.setattr(preparation,'_metadata_unconfirmed',False)
    monkeypatch.setattr(preparation,'_metadata_lock',asyncio.Lock())


def acknowledgment():
    return {'semantics':grants.SEMANTICS,'version':grants.AVERAGE_ACK_VERSION,
        'text_digest':grants.AVERAGE_ACK_DIGEST,'acknowledged':True}

async def setup(monkeypatch,db=None,*,acknowledged=True,metadata=True):
    db,_,body=await grant_setup(monkeypatch,db)
    body['max_account_daily_budget_native']='100'
    await db[guard.AUTOMATION_POLICIES].update_one({'account_id':'123'},{'$set':{'max_account_daily_budget_native':'100'}})
    if acknowledged:body.update(average_daily_acknowledged=True,average_daily_ack_version=grants.AVERAGE_ACK_VERSION,
        average_daily_ack_digest=grants.AVERAGE_ACK_DIGEST)
    provider=NativeCreationProvider();original=provider.mutate
    async def validate(operations,*,validate_only=False):
        assert validate_only is True,'Preparation must NEVER send a paid provider mutate'
        return await original(operations,validate_only=True)
    monkeypatch.setattr(provider,'mutate',validate)
    credentials={'user_id':'owner','refresh_token_ciphertext':'fake-encrypted-test-value',
        'scope':'fake-scope','google_subject':'fake-subject'}
    await db[management.GOOGLE_CREDENTIALS_COLLECTION].insert_one(deepcopy(credentials))
    epoch=management.digest({key:credentials[key] for key in ('refresh_token_ciphertext','scope','google_subject')})
    async def client(*args,**kwargs):return provider,epoch
    monkeypatch.setattr(management,'account_client',client)
    preview=await review(db,body);await save(db,body,preview['review_digest'])
    if metadata:
        db.name='unit-preparation'
        original_command=db.command
        async def command(value,**kwargs):
            if kwargs:return await original_command(value,**kwargs)
            if 'connectionStatus'in value:
                return {'ok':1,'authInfo':{'authenticatedUsers':[{'user':'test-only','db':db.name}],
                    'authenticatedUserPrivileges':[{'resource':{'db':db.name,'collection':name},'actions':list(actions)}
                        for name,actions in preparation._namespaces().items()]}}
            if 'listCollections'in value:
                return {'ok':1,'cursor':{'id':0,'firstBatch':[{'name':name,'type':'collection','options':{},'info':{'uuid':name.encode()[:16].ljust(16,b'0')}}
                    for name in preparation._namespaces()]}}
            if 'listIndexes'in value:
                indexes=[{'name':name,'key':dict(keys),'unique':True} for name,keys in preparation._indexes()[value['listIndexes']]]
                return {'ok':1,'cursor':{'id':0,'firstBatch':indexes}}
            raise AssertionError('Unplanned database command')
        db.command=command
    key=grants.grant_key('owner','123',body['request_key'])
    return db,provider,key,preview

async def admission(db,preview,**changes):
    values={'authenticated_user':await db.users.find_one({'id':'owner'}),'account_id':'123',
        'request_key':preview['request_key'],'expected_review_digest':preview['review_digest'],
        'expected_revision':preview['revision'],'expected_owner_generation':preview['owner_generation'],
        'spend_acknowledgment':acknowledgment()}
    values.update(changes)
    return preparation.begin_google_funded_owner_admission(db,**values)

@pytest.mark.asyncio
async def test_actual_prepare_ack_then_reserve_uses_existing_consumer_without_provider_send(monkeypatch):
    db,provider,key,preview=await setup(monkeypatch)
    cap=await admission(db,preview)
    result=await preparation.prepare_google_funded_creation(db,admission=cap)
    assert result['kind']=='funded_preparation' and result['committed'] is True
    assert result['funded_dispatch_authorized'] is False and provider.writes==0 and cap.used
    journal=await db[grants.RUNS].find_one({'grant_key':key})
    state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    assert journal['phase']=='prepared' and journal['creation_identity'] and state['allocations']=={}
    assert journal['preparation_binding']['guard_epoch']==state['epoch']==2
    reserve=await authority.reserve_google_funded_creation(db,user_id='owner',account_id='123',grant_key=key,
        proposal_id=result['proposal_id'],execution_claim_id=result['execution_claim_id'],initiation_ack=result['_initiation_ack'])
    assert reserve['committed'] is True and provider.writes==0
    assert (await db[grants.RUNS].find_one({'grant_key':key}))['phase']=='reserved'
    with pytest.raises(HTTPException):await preparation.prepare_google_funded_creation(db,admission=cap)

@pytest.mark.asyncio
async def test_missing_reviewed_ack_cannot_activate_legacy_configured_grant(monkeypatch):
    db,provider,key,preview=await setup(monkeypatch,acknowledged=False)
    with pytest.raises(HTTPException) as error:await preparation.prepare_google_funded_creation(db,admission=await admission(db,preview))
    assert error.value.detail['code']=='google_funded_preparation_review_changed'
    assert (await db[grants.GRANTS].find_one({'grant_key':key}))['state']=='configured' and provider.writes==0

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['version','text_digest','acknowledged','semantics'])
async def test_owner_spend_ack_text_and_semantics_are_exact(monkeypatch,change):
    db,provider,key,preview=await setup(monkeypatch)
    ack=acknowledgment();ack[change]='wrong'
    with pytest.raises(HTTPException):await admission(db,preview,spend_acknowledgment=ack)
    assert provider.writes==0

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['noauth','denied','absent','validator','wrong_index','cursor_unknown'])
async def test_actual_metadata_contract_failclosed_without_journal_or_activation(monkeypatch,fault):
    db,provider,key,preview=await setup(monkeypatch);original=db.command
    async def changed(value):
        result=await original(value)
        if 'connectionStatus'in value:
            if fault=='noauth':result['authInfo']['authenticatedUsers']=[]
            elif fault=='denied':result['authInfo']['authenticatedUserPrivileges']=[]
        if 'listCollections'in value:
            if fault=='absent':result['cursor']['firstBatch'].pop()
            elif fault=='validator':result['cursor']['firstBatch'][0]['options']={'validator':{'unknown':True}}
            elif fault=='cursor_unknown':raise TimeoutError('test-only cursor response lost')
        if 'listIndexes'in value and fault=='wrong_index':result['cursor']['firstBatch'][0]['unique']=False
        return result
    db.command=changed
    with pytest.raises(HTTPException):await preparation.prepare_google_funded_creation(db,admission=await admission(db,preview))
    assert await db[grants.RUNS].find_one({'grant_key':key}) is None
    assert (await db[grants.GRANTS].find_one({'grant_key':key}))['state']=='configured' and provider.writes==0
    assert preparation._metadata_unconfirmed is (fault=='cursor_unknown')

@pytest.mark.asyncio
async def test_released_fence_is_conditionally_reclaimed_and_full_actual_binding_matches(monkeypatch):
    db,provider,key,preview=await setup(monkeypatch)
    await db[management.FENCES].insert_one({'user_id':'owner','account_id':'123','status':'released',
        'proposal_id':'old-proposal','execution_claim_id':'old-claim','released_at':NOW.isoformat(),'old_marker':'preserved'})
    result=await preparation.prepare_google_funded_creation(db,admission=await admission(db,preview))
    fence=await db[management.FENCES].find_one({'account_id':'123'},{'_id':0})
    run=await db[grants.RUNS].find_one({'grant_key':key})
    assert fence==run['preparation_binding']['fence'] and fence['proposal_id']==result['proposal_id']
    assert fence['released_at']==NOW.isoformat() and fence['old_marker']=='preserved'
    reserve=await authority.reserve_google_funded_creation(db,user_id='owner',account_id='123',grant_key=key,
        proposal_id=result['proposal_id'],execution_claim_id=result['execution_claim_id'],initiation_ack=result['_initiation_ack'])
    assert reserve['committed'] is True and provider.writes==0

@pytest.mark.asyncio
@pytest.mark.parametrize('status',['claimed','unknown'])
async def test_existing_unresolved_account_fence_is_never_adopted(monkeypatch,status):
    db,provider,key,preview=await setup(monkeypatch)
    old={'user_id':'owner','account_id':'123','status':status,'proposal_id':'foreign','execution_claim_id':'foreign'}
    await db[management.FENCES].insert_one(old)
    with pytest.raises(HTTPException) as error:await preparation.prepare_google_funded_creation(db,admission=await admission(db,preview))
    assert error.value.detail['code']=='google_funded_account_fence_pending'
    assert await db[management.FENCES].find_one({'account_id':'123'})==old and provider.writes==0

@pytest.mark.asyncio
@pytest.mark.parametrize('applied',[False,True])
async def test_unknown_preparation_never_issues_or_reconstructs_ack(monkeypatch,applied):
    db,provider,key,preview=await setup(monkeypatch);cap=await admission(db,preview)
    if applied:
        async def lost():raise TimeoutError('test-only ACK lost after commit')
        db.after_commit=lost
    else:
        async def lost(*args):raise TimeoutError('test-only unknown commit not applied')
        monkeypatch.setattr(guard,'_commit_once',lost)
    with pytest.raises(HTTPException) as error:await preparation.prepare_google_funded_creation(db,admission=cap)
    assert cap.used and error.value.detail['commit_attempted'] is True
    assert error.value.detail['preserve_execution_fence'] is True
    run=await db[grants.RUNS].find_one({'grant_key':key})
    assert (run is not None) is applied
    if applied:assert run['phase']=='prepared' and run['consumed'] is True
    with pytest.raises(HTTPException):await preparation.prepare_google_funded_creation(db,admission=cap)
    with pytest.raises(HTTPException) as retry:await admission(db,preview)
    assert retry.value.detail['code']=='google_funded_owner_admission_consumed'
    assert provider.writes==0

@pytest.mark.asyncio
@pytest.mark.parametrize('timing',['after_probe','before_commit','after_ack'])
async def test_absolute_original_deadline_cannot_be_renewed(monkeypatch,timing):
    db,provider,key,preview=await setup(monkeypatch);clock=[100.0]
    monkeypatch.setattr(guard,'_operation_clock',lambda:clock[0])
    if timing=='after_probe':
        original=guard.probe_google_budget_guard_transactions
        async def elapsed(*args):
            result=await original(*args);clock[0]=121.0;return result
        monkeypatch.setattr(guard,'probe_google_budget_guard_transactions',elapsed)
    elif timing=='before_commit':
        original=preparation._write_preparation
        async def elapsed(*args,**kwargs):
            result=await original(*args,**kwargs);clock[0]=121.0;return result
        monkeypatch.setattr(preparation,'_write_preparation',elapsed)
    else:
        async def elapsed():clock[0]=121.0
        db.after_commit=elapsed
    cap=await admission(db,preview)
    with pytest.raises(HTTPException):await preparation.prepare_google_funded_creation(db,admission=cap)
    run=await db[grants.RUNS].find_one({'grant_key':key})
    assert (run is not None) is (timing=='after_ack')
    assert cap.used and provider.writes==0
    if run:assert run['phase']=='prepared'

@pytest.mark.asyncio
async def test_namespace_drift_during_native_read_fails_before_transaction(monkeypatch):
    db,provider,key,preview=await setup(monkeypatch);native=authority._read_native_bundle;command=db.command;drift=[False]
    async def changed(*args,**kwargs):
        result=await native(*args,**kwargs);drift[0]=True;return result
    async def metadata(value,**kwargs):
        result=await command(value,**kwargs)
        if drift[0] and isinstance(value,dict) and 'listIndexes'in value:result['cursor']['firstBatch'][0]['unique']=False
        return result
    monkeypatch.setattr(authority,'_read_native_bundle',changed);db.command=metadata
    with pytest.raises(HTTPException):await preparation.prepare_google_funded_creation(db,admission=await admission(db,preview))
    assert await db[grants.RUNS].find_one({'grant_key':key}) is None and provider.writes==0

@pytest.mark.asyncio
async def test_metadata_unknown_ack_closes_concurrent_and_later_probes(monkeypatch):
    db,provider,key,preview=await setup(monkeypatch);command=db.command;entered=asyncio.Event();release=asyncio.Event();calls=[]
    async def lost(value,**kwargs):
        if isinstance(value,dict) and 'listCollections'in value:
            calls.append(True);entered.set();await release.wait();raise TimeoutError('test-only cursor ACK lost')
        return await command(value,**kwargs)
    db.command=lost
    first=asyncio.create_task(preparation._storage_contract(db));await entered.wait()
    second=asyncio.create_task(preparation._storage_contract(db));release.set()
    results=await asyncio.gather(first,second,return_exceptions=True)
    assert all(isinstance(r,HTTPException) for r in results) and calls==[True]
    assert preparation._metadata_unconfirmed
    with pytest.raises(HTTPException) as error:await preparation._storage_contract(db)
    assert error.value.detail['code']=='google_funded_storage_metadata_pending'

@pytest.mark.asyncio
async def test_cancelled_cursor_preserves_latch_without_swallowing_cancel(monkeypatch):
    db,provider,key,preview=await setup(monkeypatch)
    async def cancelled(*args,**kwargs):raise asyncio.CancelledError()
    db.command=cancelled
    with pytest.raises(asyncio.CancelledError):await preparation._cursor_command(db,{'listCollections':1},maximum=2)
    assert preparation._metadata_unconfirmed

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['policy','collection_generation','privileges'])
async def test_pre_native_snapshot_drift_is_rejected_after_native(monkeypatch,change):
    db,provider,key,preview=await setup(monkeypatch);native=authority._read_native_bundle;command=db.command;drift=[False]
    async def changed(*args,**kwargs):
        result=await native(*args,**kwargs)
        if change=='policy':await db[guard.AUTOMATION_POLICIES].update_one({'account_id':'123'},{'$inc':{'revision':1}})
        drift[0]=True;return result
    async def metadata(value,**kwargs):
        result=await command(value,**kwargs)
        if drift[0] and isinstance(value,dict):
            if change=='collection_generation' and 'listCollections'in value:result['cursor']['firstBatch'][0]['info']['uuid']=b'changed-uuid-0001'
            if change=='privileges' and 'connectionStatus'in value:result['authInfo']['authenticatedUserPrivileges']=[]
        return result
    monkeypatch.setattr(authority,'_read_native_bundle',changed);db.command=metadata
    with pytest.raises(HTTPException):await preparation.prepare_google_funded_creation(db,admission=await admission(db,preview))
    assert await db[grants.RUNS].find_one({'grant_key':key}) is None and provider.writes==0

@pytest.mark.asyncio
@pytest.mark.parametrize('when',['before_commit','after_ack'])
async def test_metadata_latch_prevents_ack_even_after_prior_preflight(monkeypatch,when):
    db,provider,key,preview=await setup(monkeypatch)
    if when=='before_commit':
        original=preparation._write_preparation
        async def changed(*args,**kwargs):
            result=await original(*args,**kwargs);preparation._metadata_unconfirmed=True;return result
        monkeypatch.setattr(preparation,'_write_preparation',changed)
    else:
        async def changed():preparation._metadata_unconfirmed=True
        db.after_commit=changed
    with pytest.raises(HTTPException) as error:await preparation.prepare_google_funded_creation(db,admission=await admission(db,preview))
    assert error.value.detail['code']=='google_funded_storage_metadata_pending'
    assert (await db[grants.RUNS].find_one({'grant_key':key}) is not None) is (when=='after_ack')
    assert provider.writes==0

@pytest.mark.asyncio
async def test_metadata_inflight_resources_are_retained_and_process_bounded(monkeypatch):
    db,provider,key,preview=await setup(monkeypatch);command=db.command;entered=asyncio.Event();release=asyncio.Event();calls=[]
    async def blocked(value,**kwargs):
        if isinstance(value,dict) and 'listCollections'in value:
            calls.append(True);entered.set();await release.wait();raise TimeoutError('test-only unknown cursor response')
        return await command(value,**kwargs)
    db.command=blocked;scope={'user_id':'owner','account_id':'123'}
    tasks=[asyncio.create_task(guard._retained_operation(db,scope,'metadata_resource_'+str(i),'read',
        lambda:preparation._storage_contract(db),5,{})) for i in range(guard.MAX_PENDING_READ_TASKS)]
    try:
        await entered.wait()
        assert len([v for v in guard._pending_operations.values() if v['kind']=='read'])==guard.MAX_PENDING_READ_TASKS
        with pytest.raises(HTTPException) as error:
            await guard._retained_operation(db,scope,'metadata_extra','read',lambda:preparation._storage_contract(db),5,{})
        assert error.value.detail['code']=='google_budget_guard_operation_pending' and calls==[True]
    finally:
        release.set();results=await asyncio.gather(*tasks,return_exceptions=True)
    assert all(isinstance(r,HTTPException) for r in results) and preparation._metadata_unconfirmed
    assert not [v for v in guard._pending_operations.values() if v['kind']=='read']

@pytest.mark.asyncio
async def test_actual_bson_cursor_id_and_uuid_types_are_supported(monkeypatch):
    from bson.int64 import Int64
    import uuid
    db,provider,key,preview=await setup(monkeypatch);command=db.command
    async def bson_types(value,**kwargs):
        result=await command(value,**kwargs)
        if isinstance(value,dict) and 'cursor'in result:
            result['cursor']['id']=Int64(0)
            for row in result['cursor']['firstBatch']:
                if 'info'in row:row['info']['uuid']=uuid.UUID(bytes=row['info']['uuid'])
        return result
    db.command=bson_types
    result=await preparation.prepare_google_funded_creation(db,admission=await admission(db,preview))
    assert result['committed'] is True and provider.writes==0

@pytest.mark.asyncio
@pytest.mark.parametrize('option,value',[('expireAfterSeconds',0),('partialFilterExpression',{}),('sparse',True),('hidden',True)])
async def test_unsupported_index_options_fail_even_when_value_is_falsey(monkeypatch,option,value):
    db,provider,key,preview=await setup(monkeypatch);command=db.command
    async def changed(request,**kwargs):
        result=await command(request,**kwargs)
        if isinstance(request,dict) and 'listIndexes'in request:result['cursor']['firstBatch'][0][option]=value
        return result
    db.command=changed
    with pytest.raises(HTTPException):await preparation.prepare_google_funded_creation(db,admission=await admission(db,preview))
    assert await db[grants.RUNS].find_one({'grant_key':key}) is None and provider.writes==0
