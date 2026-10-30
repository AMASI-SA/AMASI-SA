"""Auth-enabled disposable replica preparation/privilege/race proof; no Google writes."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import os
from urllib.parse import urlsplit
import uuid
import pytest
import pytest_asyncio
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.write_concern import WriteConcern
from tests.test_google_ads_funded_creation_preparation import setup,admission,process_resources,NOW
from tests.test_google_ads_paused_creation_budget_mongo import Capture
from tests.test_google_ads_automation_authority import freeze_guard_clock
from integrations_control_center import google_ads_funded_creation_preparation as preparation
from integrations_control_center import google_ads_funded_creation_authority as authority
from integrations_control_center import google_ads_funded_creation_grants as grants
from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center import google_ads_management as management

ROOT='funded_test_root'
PASSWORD='DisposablePreparationTestsOnly123'

@pytest.fixture(scope='session')
def test_uri():
    uri=os.getenv('GOOGLE_FUNDED_PREPARATION_TEST_MONGO_URI')
    if not uri:pytest.skip('Disposable auth-enabled loopback replica URI required')
    parsed=urlsplit(uri)
    if (parsed.scheme!='mongodb' or parsed.hostname not in {'127.0.0.1','localhost'} or parsed.port is None
        or parsed.username is not None or parsed.password is not None):raise ValueError('Disposable loopback URI only')
    return uri

@pytest.fixture(autouse=True)
def clock(monkeypatch):
    freeze_guard_clock(monkeypatch,NOW)
    monkeypatch.setattr(management,'now',lambda:NOW)

@pytest_asyncio.fixture
async def actual_db(test_uri):
    capture=Capture();client=AsyncIOMotorClient(test_uri,username=ROOT,password=PASSWORD,authSource='admin',
        serverSelectionTimeoutMS=2000,retryWrites=True,event_listeners=[capture])
    db=client['g_funded_preparation_'+uuid.uuid4().hex]
    clients=[client];databases=[db]
    try:
        await client.admin.command('ping');await management.ensure_google_management_indexes(db)
        yield db,capture,clients,databases
    finally:
        tasks=[s['task'] for k,s in guard._pending_operations.items() if k[0] in {id(x) for x in databases}]
        if tasks:
            _,pending=await asyncio.wait(tasks,timeout=20);assert not pending,'Own retained driver work unfinished'
        await db.command({'dropAllUsersFromDatabase':1});await db.command({'dropAllRolesFromDatabase':1})
        await client.drop_database(db.name)
        for client in clients:client.close()

async def provision_test_namespaces(db):
    # EXCLUSIVELY random disposable test database. Runtime performs no DDL.
    existing=set(await db.list_collection_names())
    for name in preparation._namespaces():
        if name not in existing:await db.create_collection(name)
    for name,contracts in preparation._indexes().items():
        for index_name,keys in contracts:await db[name].create_index(keys,unique=True,name=index_name)

async def case(monkeypatch,actual_db):
    db,capture,_,_=actual_db;db,provider,key,preview=await setup(monkeypatch,db,metadata=False)
    await provision_test_namespaces(db)
    return db,provider,key,preview

async def other_client(actual_db,test_uri,*,user=ROOT,password=PASSWORD,auth_source='admin'):
    db,capture,clients,databases=actual_db
    client=AsyncIOMotorClient(test_uri,username=user,password=password,authSource=auth_source,
        serverSelectionTimeoutMS=2000,retryWrites=True,event_listeners=[capture])
    clients.append(client);other=client[db.name];databases.append(other)
    await client.admin.command('ping')
    return other

@pytest.mark.asyncio
async def test_auth_replica_prepare_positive_ack_binds_unique_identity_and_reserves(monkeypatch,actual_db):
    db,provider,key,preview=await case(monkeypatch,actual_db);cap=await admission(db,preview)
    baseline=actual_db[1].names.count('commitTransaction')
    result=await preparation.prepare_google_funded_creation(db,admission=cap)
    assert actual_db[1].names.count('commitTransaction')==baseline+1
    run=await db[grants.RUNS].find_one({'grant_key':key})
    fence=await db[management.FENCES].find_one({'account_id':'123'},{'_id':0})
    assert run['phase']=='prepared' and run['creation_identity']==result['creation_identity']
    assert run['preparation_binding']['fence']==fence and run['preparation_binding']['guard_epoch']==2
    assert (await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'}))['allocations']=={}
    reserved=await authority.reserve_google_funded_creation(db,user_id='owner',account_id='123',grant_key=key,
        proposal_id=result['proposal_id'],execution_claim_id=result['execution_claim_id'],initiation_ack=result['_initiation_ack'])
    assert reserved['committed'] is True and provider.writes==0

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['read','update_without_insert','full'])
async def test_actual_effective_read_update_full_privileges_are_not_noop_proof(monkeypatch,actual_db,test_uri,mode):
    db,provider,key,preview=await case(monkeypatch,actual_db)
    username='test_'+mode;password='DisposableRoleTestOnly123'
    if mode=='update_without_insert':
        privileges=[{'resource':{'db':db.name,'collection':name},'actions':['update']}
            for name,actions in preparation._namespaces().items() if 'update'in actions]
        await db.command({'createRole':'test_update','privileges':privileges,'roles':[{'role':'read','db':db.name}]})
        roles=['test_update']
    else:roles=[{'role':'readWrite' if mode=='full' else 'read','db':db.name}]
    await db.command({'createUser':username,'pwd':password,'roles':roles})
    worker=await other_client(actual_db,test_uri,user=username,password=password,auth_source=db.name)
    cap=await admission(worker,preview)
    if mode=='full':
        result=await preparation.prepare_google_funded_creation(worker,admission=cap)
        assert result['committed'] is True
    else:
        with pytest.raises(HTTPException) as error:await preparation.prepare_google_funded_creation(worker,admission=cap)
        assert error.value.detail['code']=='google_funded_storage_privileges_unproved'
        assert await db[grants.RUNS].find_one({'grant_key':key}) is None
    assert provider.writes==0

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['absent_collection','missing_index','custom_validator'])
async def test_actual_namespace_and_index_unknowns_block_without_auto_provision(monkeypatch,actual_db,fault):
    db,provider,key,preview=await case(monkeypatch,actual_db)
    if fault=='absent_collection':await db.drop_collection(guard.TECHNICAL_PROBES)
    elif fault=='missing_index':await db[grants.RUNS].drop_index('google_funded_run_identity')
    else:await db.command({'collMod':grants.RUNS,'validator':{'unrecognized_contract':{'$exists':False}}})
    with pytest.raises(HTTPException):await preparation.prepare_google_funded_creation(db,admission=await admission(db,preview))
    assert await db[grants.RUNS].find_one({'grant_key':key}) is None
    assert (await db[grants.GRANTS].find_one({'grant_key':key}))['state']=='configured' and provider.writes==0
    if fault=='absent_collection':assert guard.TECHNICAL_PROBES not in await db.list_collection_names()
    elif fault=='missing_index':assert 'google_funded_run_identity' not in await db[grants.RUNS].index_information()

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['server91','connection_close','ack_loss_after_commit'])
async def test_actual_preparation_commit_uncertainty_never_issues_or_retries(monkeypatch,actual_db,test_uri,fault):
    import time
    db,provider,key,preview=await case(monkeypatch,actual_db);cap=await admission(db,preview)
    baseline=actual_db[1].names.count('commitTransaction')
    if fault=='ack_loss_after_commit':
        original=guard._commit_once
        async def lost(*args):await original(*args);raise TimeoutError('test-only ACK loss after real commit')
        monkeypatch.setattr(guard,'_commit_once',lost)
    else:
        data={'failCommands':['commitTransaction']}
        data.update({'errorCode':91} if fault=='server91' else {'closeConnection':True})
        await db.client.admin.command({'configureFailPoint':'failCommand','mode':{'times':1},'data':data})
    started=time.monotonic()
    try:
        with pytest.raises(HTTPException) as error:await preparation.prepare_google_funded_creation(db,admission=cap)
        elapsed=time.monotonic()-started
    finally:await db.client.admin.command({'configureFailPoint':'failCommand','mode':'off'})
    assert error.value.detail['commit_attempted'] is True and error.value.detail['automatic_retry_allowed'] is False
    assert elapsed<=25 and cap.used and provider.writes==0
    assert actual_db[1].names.count('commitTransaction')==baseline+1
    run=await db[grants.RUNS].find_one({'grant_key':key})
    assert (run is not None) is (fault=='ack_loss_after_commit')
    if run:assert run['phase']=='prepared' and run['consumed'] and run['creation_identity']
    with pytest.raises(HTTPException):await preparation.prepare_google_funded_creation(db,admission=cap)
    # A new client can read evidence but lacks an original private owner action.
    worker=await other_client(actual_db,test_uri)
    with pytest.raises(HTTPException):await preparation.prepare_google_funded_creation(worker,admission=run)
    assert actual_db[1].names.count('commitTransaction')==baseline+1
    print({'preparation_fault':fault,'helper_seconds':round(elapsed,3),'commit_wire_attempts':1,'prepared':bool(run),'provider_writes':0})

@pytest.mark.asyncio
async def test_actual_released_fence_cas_preserves_binding_and_unresolved_claim_blocks(monkeypatch,actual_db):
    db,provider,key,preview=await case(monkeypatch,actual_db)
    await db[management.FENCES].insert_one({'user_id':'owner','account_id':'123','status':'released',
        'proposal_id':'prior','execution_claim_id':'prior','released_at':NOW.isoformat()})
    result=await preparation.prepare_google_funded_creation(db,admission=await admission(db,preview))
    fence=await db[management.FENCES].find_one({'account_id':'123'},{'_id':0})
    run=await db[grants.RUNS].find_one({'grant_key':key})
    assert fence==run['preparation_binding']['fence'] and fence['proposal_id']==result['proposal_id']
    assert fence['released_at']==NOW.isoformat() and provider.writes==0

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['disabled','role','password'])
async def test_actual_owner_write_after_snapshot_before_marker_aborts(monkeypatch,actual_db,test_uri,change):
    db,provider,key,preview=await case(monkeypatch,actual_db);cap=await admission(db,preview)
    worker=await other_client(actual_db,test_uri);original=authority._owner_write_fence
    async def changed(view,**kwargs):
        value={'disabled':True} if change=='disabled' else {'role':'staff'} if change=='role' else {'password_updated_at':NOW,'password_hash':'test-only-change'}
        written=await worker.users.with_options(write_concern=WriteConcern('majority',j=True)).update_one({'id':'owner'},{'$set':value})
        assert written.matched_count==1
        return await original(view,**kwargs)
    monkeypatch.setattr(authority,'_owner_write_fence',changed)
    with pytest.raises(HTTPException) as error:await preparation.prepare_google_funded_creation(db,admission=cap)
    assert error.value.detail['commit_attempted'] is False and error.value.detail['preserve_execution_fence'] is True
    assert await db[grants.RUNS].find_one({'grant_key':key}) is None and provider.writes==0

@pytest.mark.asyncio
async def test_actual_two_clients_same_slot_contend_in_mongo_not_local_registry(monkeypatch,actual_db,test_uri):
    db,provider,key,preview=await case(monkeypatch,actual_db);worker=await other_client(actual_db,test_uri)
    first=await admission(db,preview);second=await admission(worker,preview)
    assert first.db is not second.db
    original=guard._read_policy;readers=set();both=asyncio.Event()
    original_run=guard._run;ready=set();first_ready=asyncio.Event();both_ready=asyncio.Event();start=asyncio.Event()
    async def delayed(*args,**kwargs):
        ready.add(id(args[0]))
        first_ready.set()
        if len(ready)==2:both_ready.set()
        await start.wait();return await original_run(*args,**kwargs)
    monkeypatch.setattr(guard,'_run',delayed)
    async def overlap(database,scope,kind,session):
        result=await original(database,scope,kind,session)
        if session is not None and kind=='creation':
            readers.add(id(database))
            if len(readers)==2:both.set()
            await asyncio.wait_for(both.wait(),5)
        return result
    monkeypatch.setattr(guard,'_read_policy',overlap)
    baseline=actual_db[1].names.count('commitTransaction')
    first_task=asyncio.create_task(preparation.prepare_google_funded_creation(db,admission=first))
    await asyncio.wait_for(first_ready.wait(),5)
    second_task=asyncio.create_task(preparation.prepare_google_funded_creation(worker,admission=second))
    try:
        await asyncio.wait_for(both_ready.wait(),5)
        pending=[k for k,v in guard._pending_operations.items() if v['kind']=='write' and k[0] in {id(db),id(worker)}]
        assert len(pending)==2 and len({k[0] for k in pending})==2
    finally:start.set()
    results=await asyncio.gather(first_task,second_task,return_exceptions=True)
    assert len(readers)==2 and sum(isinstance(r,dict) and r.get('committed') is True for r in results)==1
    failed=[r for r in results if isinstance(r,HTTPException)]
    assert len(failed)==1 and failed[0].detail['commit_attempted'] is False
    assert failed[0].detail['code']=='google_budget_guard_policy_changed'
    assert actual_db[1].names.count('commitTransaction')==baseline+1
    assert await db[grants.RUNS].count_documents({'grant_key':key})==1
    assert await db[management.PROPOSALS].count_documents({'account_id':'123'})==1
    assert (await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'}))['epoch']==2
    assert first.used and second.used and provider.writes==0

@pytest.mark.asyncio
async def test_actual_missing_account_guard_row_is_not_synthesized(monkeypatch,actual_db):
    db,provider,key,preview=await case(monkeypatch,actual_db)
    await db[guard.BUDGET_GUARDS].delete_many({'user_id':'owner','account_id':'123'})
    with pytest.raises(HTTPException) as error:await preparation.prepare_google_funded_creation(db,admission=await admission(db,preview))
    assert error.value.detail['code']=='google_budget_guard_existing_state_unproved'
    assert await db[guard.BUDGET_GUARDS].count_documents({})==0
    assert await db[grants.RUNS].find_one({'grant_key':key}) is None and provider.writes==0

@pytest.mark.asyncio
@pytest.mark.parametrize('collection',[grants.RUNS,management.PROPOSALS,management.FENCES,guard.BUDGET_GUARDS])
async def test_actual_namespace_removed_after_probe_is_not_recreated(monkeypatch,actual_db,collection):
    db,provider,key,preview=await case(monkeypatch,actual_db);original=guard.probe_google_budget_guard_transactions
    async def removed(*args):
        result=await original(*args);await db.drop_collection(collection);return result
    monkeypatch.setattr(guard,'probe_google_budget_guard_transactions',removed)
    with pytest.raises(HTTPException):await preparation.prepare_google_funded_creation(db,admission=await admission(db,preview))
    assert collection not in await db.list_collection_names()
    assert (await db[grants.GRANTS].find_one({'grant_key':key}))['state']=='configured' and provider.writes==0

@pytest.mark.asyncio
@pytest.mark.parametrize('drop_collection',[False,True])
async def test_actual_reserve_missing_guard_cannot_upsert_or_recreate(monkeypatch,actual_db,drop_collection):
    db,provider,key,preview=await case(monkeypatch,actual_db)
    prepared=await preparation.prepare_google_funded_creation(db,admission=await admission(db,preview))
    if drop_collection:await db.drop_collection(guard.BUDGET_GUARDS)
    else:await db[guard.BUDGET_GUARDS].delete_many({'user_id':'owner','account_id':'123'})
    with pytest.raises(HTTPException) as error:
        await authority.reserve_google_funded_creation(db,user_id='owner',account_id='123',grant_key=key,
            proposal_id=prepared['proposal_id'],execution_claim_id=prepared['execution_claim_id'],
            initiation_ack=prepared['_initiation_ack'])
    assert error.value.detail['code']=='google_budget_guard_existing_state_unproved'
    assert await db[guard.BUDGET_GUARDS].count_documents({})==0
    if drop_collection:assert guard.BUDGET_GUARDS not in await db.list_collection_names()
    assert (await db[grants.RUNS].find_one({'grant_key':key}))['phase']=='prepared'
    assert prepared['_initiation_ack'].used and provider.writes==0

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['recreated_namespace','missing_index'])
async def test_actual_changed_namespace_generation_or_index_after_probe_blocks(monkeypatch,actual_db,change):
    db,provider,key,preview=await case(monkeypatch,actual_db);original=guard.probe_google_budget_guard_transactions
    async def changed(*args):
        result=await original(*args)
        if change=='recreated_namespace':
            await db.drop_collection(grants.RUNS);await provision_test_namespaces(db)
        else:await db[grants.RUNS].drop_index('google_funded_run_identity')
        return result
    monkeypatch.setattr(guard,'probe_google_budget_guard_transactions',changed)
    with pytest.raises(HTTPException):await preparation.prepare_google_funded_creation(db,admission=await admission(db,preview))
    assert await db[grants.RUNS].find_one({'grant_key':key}) is None and provider.writes==0
    assert (await db[grants.GRANTS].find_one({'grant_key':key}))['state']=='configured'
