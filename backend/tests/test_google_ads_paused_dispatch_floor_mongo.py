"""Disposable real replica serialization and one-wire floor ACK faults."""
import asyncio
import time
import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from integrations_control_center import google_ads_automation_creation_authority as creation
from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center import google_ads_management as management
from integrations_control_center.google_ads_automation_policy_mutation_adapter import mutate_google_automation_policy
from tests.test_google_ads_paused_creation_budget_mongo import actual_db,clock,automation_mongo_uri,guard_mongo_contract
from tests.test_google_ads_automation_creation_gateway import setup,preview,execute

@pytest.mark.asyncio
@pytest.mark.parametrize('timing',['before_floor_write','after_floor_ack'])
async def test_real_independent_client_revoke_serializes_with_floor(monkeypatch,actual_db,automation_mongo_uri,timing):
    db,capture=actual_db;db,provider,values=await setup(monkeypatch,db);row=await preview(db,values)
    other=AsyncIOMotorClient(automation_mongo_uri,serverSelectionTimeoutMS=2000,retryWrites=True);worker=other[db.name]
    assert worker is not db and worker.client is not db.client
    async def revoke():
        result=await mutate_google_automation_policy(worker,policy_kind='creation',scope={'user_id':'owner','account_id':'123'},
            expected_revision=2,values={'enabled':False,'updated_by':'owner'})
        assert result['committed'] is True
    if timing=='before_floor_write':
        original=creation._guard_paused_creation_command
        async def before(*args,**kwargs):
            if isinstance(kwargs['command'],guard.PausedCreationDispatchFloor):
                # _run has loaded original policy/guard snapshot but has not
                # written either; a different real client commits revocation.
                await revoke()
            return await original(*args,**kwargs)
        monkeypatch.setattr(creation,'_guard_paused_creation_command',before)
    else:
        original=creation.acknowledge_paused_creation_dispatch_floor
        async def after(database,proposal):
            result=await original(database,proposal);await revoke();return result
        monkeypatch.setattr(creation,'acknowledge_paused_creation_dispatch_floor',after)
    try:
        result=await execute(db,row)
        state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
        if timing=='before_floor_write':
            assert result['submission_started'] is False and provider.writes==0
            assert state['epoch']==2 and state['active_claim']
            assert next(iter(state['allocations'].values())).get('dispatch_marker') is None
            assert capture.names.count('commitTransaction')==1 # only first client's reservation is monitored
            assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
        else:
            assert result['submission_started'] is True and result['status']=='completed' and provider.writes==1
            assert state['epoch']==4 and capture.names.count('commitTransaction')==3 # second client not monitored
            assert next(iter(state['allocations'].values()))['dispatch_marker']['policy_revision']==2
    finally:
        retained=[state['task'] for key,state in guard._pending_operations.items() if key[0]==id(worker)]
        if retained:
            _,pending=await asyncio.wait(retained,timeout=20);assert not pending
        other.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['server91','connection_close'])
async def test_real_floor_commit_unknown_never_http_retry_or_release(monkeypatch,actual_db,fault):
    db,capture=actual_db;db,provider,values=await setup(monkeypatch,db);row=await preview(db,values)
    original=creation.acknowledge_paused_creation_dispatch_floor
    async def faulted(database,proposal):
        data={'failCommands':['commitTransaction']}
        data.update({'errorCode':91} if fault=='server91' else {'closeConnection':True})
        await db.client.admin.command({'configureFailPoint':'failCommand','mode':{'times':1},'data':data})
        return await original(database,proposal)
    monkeypatch.setattr(creation,'acknowledge_paused_creation_dispatch_floor',faulted)
    started=time.monotonic()
    try:result=await execute(db,row);duration=time.monotonic()-started
    finally:await db.client.admin.command({'configureFailPoint':'failCommand','mode':'off'})
    assert duration<=20 and result['status']=='uncertain' and provider.writes==0
    assert capture.names.count('commitTransaction')==2 # one claim + one floor attempt
    state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    assert state['active_claim'] and next(iter(state['allocations'].values()))['state']=='reserved'
    assert (await db[guard.CREATION_POLICIES].find_one({'account_id':'123'}))['_creation_claim']
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    with pytest.raises(HTTPException):await execute(db,row)
    assert provider.writes==0 and capture.names.count('commitTransaction')==2
    print({'case':fault,'floor_helper_seconds':round(duration,3),'floor_wire_attempts':1,'provider_writes':0,'hold_retained':True})
