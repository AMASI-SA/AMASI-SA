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
@pytest.mark.parametrize('fault',['server91','connection_close','ack_loss_after_commit'])
async def test_real_floor_commit_unknown_never_http_retry_or_release(monkeypatch,actual_db,automation_mongo_uri,fault):
    db,capture=actual_db;db,provider,values=await setup(monkeypatch,db);row=await preview(db,values)
    original=creation.acknowledge_paused_creation_dispatch_floor
    async def faulted(database,proposal):
        if fault!='ack_loss_after_commit':
            data={'failCommands':['commitTransaction']}
            data.update({'errorCode':91} if fault=='server91' else {'closeConnection':True})
            await db.client.admin.command({'configureFailPoint':'failCommand','mode':{'times':1},'data':data})
        else:
            raw=guard._commit_once
            async def lost(*args):
                await raw(*args);raise TimeoutError('floor ACK lost after real commit')
            monkeypatch.setattr(guard,'_commit_once',lost)
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
    if fault=='ack_loss_after_commit':
        other=AsyncIOMotorClient(automation_mongo_uri,serverSelectionTimeoutMS=2000,retryWrites=True)
        try:
            stored=await db[management.PROPOSALS].find_one({'proposal_id':row['proposal_id']},{'_id':0})
            with pytest.raises(HTTPException) as error:await original(other[db.name],stored)
            assert error.value.detail['code']=='google_budget_guard_operation_consumed'
            assert provider.writes==0 and capture.names.count('commitTransaction')==2
        finally:other.close()
    print({'case':fault,'floor_helper_seconds':round(duration,3),'floor_wire_attempts':1,'provider_writes':0,'hold_retained':True})

@pytest.mark.asyncio
async def test_real_owner_cancel_conflicts_with_floor_proposal_write(monkeypatch,actual_db,automation_mongo_uri):
    from integrations_control_center.google_ads_paused_creation_budget import SessionCollection
    from pymongo.write_concern import WriteConcern
    db,capture=actual_db;db,provider,values=await setup(monkeypatch,db);row=await preview(db,values)
    other=AsyncIOMotorClient(automation_mongo_uri,serverSelectionTimeoutMS=2000,retryWrites=True);worker=other[db.name]
    original=SessionCollection.update_one;cancelled=[]
    async def cancel_first(collection,query,update,**kwargs):
        if update.get('$set',{}).get('creation_dispatch_marker'):
            changed=await worker[management.PROPOSALS].with_options(write_concern=WriteConcern('majority',j=True)).update_one(
                {'proposal_id':row['proposal_id'],'status':'approved','submission_started':False},
                {'$set':{'status':'failed','submission_started':False,'provider_write_reached':False,
                    'safe_error':'execution_cancelled_before_dispatch'}})
            assert changed.matched_count==1;cancelled.append(True)
        return await original(collection,query,update,**kwargs)
    monkeypatch.setattr(SessionCollection,'update_one',cancel_first)
    try:
        result=await execute(db,row)
        assert cancelled==[True] and result['status']=='failed' and result['submission_started'] is False and provider.writes==0
        state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
        assert state['epoch']==2 and state['active_claim'] is None
        allocation=next(iter(state['allocations'].values()))
        assert allocation.get('dispatch_marker') is None and allocation['state']=='cancelled_before_http'
        assert capture.names.count('commitTransaction')==2 # original claim + known no-HTTP settlement
    finally:other.close()

@pytest.mark.asyncio
async def test_real_expiry_crossed_during_floor_ack_keeps_inflight_hold(monkeypatch,actual_db):
    from datetime import timedelta
    from tests.test_google_ads_automation_creation_gateway import advance,NOW
    db,capture=actual_db;db,provider,values=await setup(monkeypatch,db)
    await db[guard.CREATION_POLICIES].update_one({'account_id':'123'},
        {'$set':{'expires_at':(NOW+timedelta(seconds=20)).isoformat()}})
    row=await preview(db,values);original=guard._commit_once;calls=0
    async def elapsed(*args):
        nonlocal calls
        calls+=1;result=await original(*args)
        if calls==2:advance(monkeypatch,NOW+timedelta(seconds=21))
        return result
    monkeypatch.setattr(guard,'_commit_once',elapsed)
    result=await execute(db,row)
    assert result['status']=='uncertain' and result['submission_started'] is True and provider.writes==0
    assert result['safe_error']=='creation_authority_expired_after_floor'
    state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    assert state['epoch']==2 and state['active_claim'] and next(iter(state['allocations'].values()))['dispatch_marker']
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    assert capture.names.count('commitTransaction')==2
