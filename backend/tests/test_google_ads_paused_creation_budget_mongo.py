"""Disposable real replica common claims/drift/faults; no live Google calls."""
import asyncio
from copy import deepcopy
import time
import uuid
import pytest
import pytest_asyncio
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener
from pymongo.write_concern import WriteConcern
from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center import google_ads_automation_creation_authority as creation
from integrations_control_center import google_ads_automation_authority as standing
from integrations_control_center import google_ads_management as management
from integrations_control_center.google_ads_automation_policy_mutation_adapter import mutate_google_automation_policy
from tests.test_google_ads_automation_policy_mongo import automation_mongo_uri,guard_mongo_contract
from tests.test_google_ads_automation_authority import freeze_guard_clock
from tests.test_google_ads_automation_creation_authority import documents
from tests.test_google_ads_automation_creation_contract import NOW
from tests.test_google_ads_automation_creation_gateway import setup,preview,execute

class Capture(CommandListener):
    def __init__(self):self.names=[]
    def started(self,event):self.names.append(event.command_name)
    def succeeded(self,event):pass
    def failed(self,event):pass

@pytest.fixture(autouse=True)
def clock(monkeypatch,guard_mongo_contract):
    freeze_guard_clock(monkeypatch,NOW)
    monkeypatch.setattr(creation,'now',lambda current=None:current or NOW)
    for key in ('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','GOOGLE_ADS_AUTOMATION_WORKER_ENABLED','GOOGLE_ADS_AUTOMATION_CREATION_ENABLED'):
        monkeypatch.setenv(key,'true')

@pytest_asyncio.fixture
async def actual_db(automation_mongo_uri):
    capture=Capture();client=AsyncIOMotorClient(automation_mongo_uri,serverSelectionTimeoutMS=2000,
        retryWrites=True,event_listeners=[capture]);name='g_paused_common_'+uuid.uuid4().hex;db=client[name]
    try:
        await client.admin.command('ping');await management.ensure_google_management_indexes(db)
        await db[guard.AUTOMATION_POLICIES].create_index([('user_id',1),('account_id',1)],unique=True,name='google_automation_tenant_account')
        yield db,capture
    finally:
        tasks=[state['task'] for key,state in guard._pending_operations.items() if key[0]==id(db)]
        if tasks:
            done,pending=await asyncio.wait(tasks,timeout=20)
            assert not pending,'Retained real driver work did not finish within fixture cleanup bound'
        await client.drop_database(name);client.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('target',['owner','canonical_product'])
@pytest.mark.parametrize('stage',['after_tx_reads_before_commit','after_commit'])
async def test_real_nonconflicting_metadata_writer_is_caught_before_native_floor(monkeypatch,actual_db,target,stage):
    db,capture=actual_db;db,provider,values=await setup(monkeypatch,db);row=await preview(db,values)
    async def drift():
        collection=(db.users if target=='owner' else db[creation.PRODUCT_COLLECTION]).with_options(
            write_concern=WriteConcern('majority',j=True))
        result=await collection.update_one({'id':'owner'} if target=='owner' else {'user_id':'owner'},
            {'$set':{'disabled':True} if target=='owner' else {'quantity':0}})
        assert result.matched_count==1
    if stage=='after_commit':
        original=guard._commit_once
        async def changed(*args):
            result=await original(*args);await drift();return result
        monkeypatch.setattr(guard,'_commit_once',changed)
    else:
        original=creation._linearize_creation_claim
        async def changed(*args):
            result=await original(*args);await drift();return result
        monkeypatch.setattr(creation,'_linearize_creation_claim',changed)
    result=await execute(db,row)
    assert result['status']=='uncertain' and result['submission_started'] is False and provider.writes==0
    policy=await db[guard.CREATION_POLICIES].find_one({'user_id':'owner','account_id':'123'})
    budget=await db[guard.BUDGET_GUARDS].find_one({'user_id':'owner','account_id':'123'})
    assert policy['_creation_claim'] and budget['active_claim'] and budget['epoch']==1
    assert next(iter(budget['allocations'].values()))['state']=='reserved'
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    assert capture.names.count('commitTransaction')==1

@pytest.mark.asyncio
async def test_real_standing_increase_and_actual_paused_authorizer_share_one_epoch(monkeypatch,actual_db):
    db,capture=actual_db;docs,native,intent=documents()
    # Explicit controlled owner fixture: standing permits40→44; creation15
    # permits40+15. Both together59 would exceed the common explicit55 limit.
    docs[guard.AUTOMATION_POLICIES][0]['max_campaign_daily_budget_native']='60'
    for name,rows in docs.items():
        if rows:await db[name].insert_many(deepcopy(rows))
    policy=docs[guard.AUTOMATION_POLICIES][0]
    inventory=deepcopy(native['native_context'])
    claim={'claim_id':'stand_claim','execution_claim_id':'stand_claim','proposal_id':'stand_proposal',
        'campaign_id':'456','action':'set_budget','policy_revision':policy['revision'],
        'policy_digest':standing.policy_authorization_digest(policy),'native_inventory_digest':management.digest(inventory),
        'campaign_budget_before_native':'40','campaign_budget_target_native':'44','budget_increase_reserved_native':'4',
        'budget_semantics':'average_daily_budget','authorized_at':NOW.isoformat(),'time_zone':'Asia/Riyadh',
        'confirmation_digest':'a'*64,'operations_digest':'b'*64}
    capability=await guard.probe_google_budget_guard_transactions(db)
    entered=asyncio.Event();release=asyncio.Event();original=guard._commit_once
    async def blocked(*args):entered.set();await release.wait();return await original(*args)
    monkeypatch.setattr(guard,'_commit_once',blocked)
    first=asyncio.create_task(guard.run_google_budget_guard_transaction(db,user_id='owner',account_id='123',
        operation_id='standing_competitor',capability=capability,planner=lambda snapshot:guard.StandingBudgetReservation('standing_stand_claim',policy['revision'],claim,inventory)))
    second=asyncio.create_task(creation.authorize_google_creation_claim(db,user_id='owner',account_id='123',
        proposal_id='proposal123',execution_claim_id='claim123',native_evidence=native,current=NOW))
    try:await asyncio.wait_for(entered.wait(),5)
    finally:release.set()
    results=await asyncio.gather(first,second,return_exceptions=True)
    assert sum(isinstance(result,dict) and result.get('committed') is True for result in results)==1
    assert sum(isinstance(result,HTTPException) for result in results)==1
    state=await db[guard.BUDGET_GUARDS].find_one({'user_id':'owner','account_id':'123'})
    assert state['epoch']==1 and len(state['allocations'])==1 and state['active_claim']
    assert capture.names.count('commitTransaction')==1
    assert all(not isinstance(result,dict) or result['provider_dispatch_authorized'] is False for result in results)

@pytest.mark.asyncio
async def test_real_owner_revoke_after_reservation_needs_no_oauth_and_blocks_floor(monkeypatch,actual_db):
    db,capture=actual_db;db,provider,values=await setup(monkeypatch,db);row=await preview(db,values)
    original=creation.authorize_google_creation_claim
    async def revoke(*args,**kwargs):
        result=await original(*args,**kwargs)
        await db.mezan_integration_accounts_v2.delete_many({'user_id':'owner'})
        changed=await mutate_google_automation_policy(db,policy_kind='creation',scope={'user_id':'owner','account_id':'123'},
            expected_revision=2,values={'enabled':False,'updated_by':'owner','updated_at':NOW.isoformat()})
        assert changed['committed'] is True
        return result
    monkeypatch.setattr(creation,'authorize_google_creation_claim',revoke)
    result=await execute(db,row)
    assert result['status']=='uncertain' and result['submission_started'] is False and provider.writes==0
    policy=await db[guard.CREATION_POLICIES].find_one({'user_id':'owner','account_id':'123'})
    state=await db[guard.BUDGET_GUARDS].find_one({'user_id':'owner','account_id':'123'})
    assert policy['enabled'] is False and policy['revision']==3 and policy['_creation_claim']
    assert state['epoch']==2 and state['active_claim']
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    assert capture.names.count('commitTransaction')==2

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['server91','connection_close'])
async def test_real_unknown_commit_wire_has_no_floor_send_retry_or_native_fence_release(monkeypatch,actual_db,fault):
    db,capture=actual_db;db,provider,values=await setup(monkeypatch,db);row=await preview(db,values)
    data={'failCommands':['commitTransaction']}
    data.update({'errorCode':91} if fault=='server91' else {'closeConnection':True})
    await db.client.admin.command({'configureFailPoint':'failCommand','mode':{'times':1},'data':data})
    started=time.monotonic()
    try:
        result=await execute(db,row)
        duration=time.monotonic()-started
    finally:await db.client.admin.command({'configureFailPoint':'failCommand','mode':'off'})
    assert duration<=20 and result['status']=='uncertain' and result['submission_started'] is False
    assert provider.writes==0 and capture.names.count('commitTransaction')==1
    fence=await db[management.FENCES].find_one({'account_id':'123'})
    assert fence['status']=='claimed'
    # An unknown transaction can have applied or aborted. Absence is not a
    # durable reservation proof; the original native account fence and uncertain
    # proposal prevent another execution. No refund/release is inferred.
    with pytest.raises(HTTPException):await execute(db,row)
    assert provider.writes==0 and (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    print({'case':fault,'helper_request_seconds':round(duration,3),'commit_wire_attempts':1,'provider_writes':0,'native_fence_retained':True})
