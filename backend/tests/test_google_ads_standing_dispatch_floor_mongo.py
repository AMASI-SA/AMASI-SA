"""Disposable real replica: standing budget floor policy/cancel/ACK boundaries."""
import asyncio
from copy import deepcopy
import time
import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.write_concern import WriteConcern
from integrations_control_center import google_ads_automation_authority as authority
from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center import google_ads_management as management
from integrations_control_center.google_ads_automation_policy_mutation_adapter import mutate_google_automation_policy
from integrations_control_center.google_ads_paused_creation_budget import SessionCollection
from tests.test_google_ads_paused_creation_budget_mongo import actual_db,clock,automation_mongo_uri,guard_mongo_contract
from tests.test_google_ads_standing_dispatch_floor import setup,preview,execute,advance,NOW,timedelta

async def close_worker(worker):
    tasks=[state['task'] for key,state in guard._pending_operations.items() if key[0]==id(worker)]
    if tasks:
        _,pending=await asyncio.wait(tasks,timeout=20);assert not pending
    worker.client.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('timing',['before_floor_write','after_floor_ack'])
async def test_real_two_client_revoke_serializes_with_budget_increase(monkeypatch,actual_db,automation_mongo_uri,timing):
    db,capture=actual_db;db,provider=await setup(monkeypatch,db);row=await preview(db)
    worker=AsyncIOMotorClient(automation_mongo_uri,serverSelectionTimeoutMS=2000,retryWrites=True)[db.name]
    assert worker is not db and worker.client is not db.client
    async def revoke():
        result=await mutate_google_automation_policy(worker,policy_kind='standing',scope={'user_id':'owner','account_id':'123'},
            expected_revision=3,values={'enabled':False,'updated_by':'owner'})
        assert result['committed'] is True
    if timing=='before_floor_write':
        original=authority._guard_standing_dispatch_floor
        async def changed(*args,**kwargs):
            await revoke() # _run already read epoch/policy; independent client wins before writes
            return await original(*args,**kwargs)
        monkeypatch.setattr(authority,'_guard_standing_dispatch_floor',changed)
    else:
        original=authority.acknowledge_standing_dispatch_floor
        async def changed(database,proposal,**kwargs):
            result=await original(database,proposal,**kwargs);await revoke();return result
        monkeypatch.setattr(authority,'acknowledge_standing_dispatch_floor',changed)
    try:
        result=await execute(db,row);state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
        if timing=='before_floor_write':
            assert result['submission_started'] is False and provider.writes==0 and state['epoch']==2
            assert state['active_claim'] and next(iter(state['allocations'].values())).get('dispatch_marker') is None
            assert capture.names.count('commitTransaction')==1 # other client not monitored
            assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
        else:
            assert result['submission_started'] is True and provider.writes==1 and result['status']=='completed'
            assert state['epoch']==4 and capture.names.count('commitTransaction')==3
            allocation=next(iter(state['allocations'].values()))
            assert allocation['dispatch_marker']['policy_revision']==3 and allocation['budget_native']=='22'
    finally:await close_worker(worker)

@pytest.mark.asyncio
async def test_real_owner_cancel_races_proposal_floor_write(monkeypatch,actual_db,automation_mongo_uri):
    db,capture=actual_db;db,provider=await setup(monkeypatch,db);row=await preview(db)
    worker=AsyncIOMotorClient(automation_mongo_uri,serverSelectionTimeoutMS=2000,retryWrites=True)[db.name]
    original=SessionCollection.update_one;cancelled=[]
    async def changed(collection,query,update,**kwargs):
        if update.get('$set',{}).get('standing_dispatch_marker'):
            result=await worker[management.PROPOSALS].with_options(write_concern=WriteConcern('majority',j=True)).update_one(
                {'proposal_id':row['proposal_id'],'status':'approved','submission_started':False},
                {'$set':{'status':'failed','submission_started':False,'provider_write_reached':False,
                    'safe_error':'execution_cancelled_before_dispatch'}})
            assert result.matched_count==1;cancelled.append(True)
        return await original(collection,query,update,**kwargs)
    monkeypatch.setattr(SessionCollection,'update_one',changed)
    try:
        result=await execute(db,row);state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
        assert cancelled==[True] and result['status']=='failed' and result['submission_started'] is False and provider.writes==0
        allocation=next(iter(state['allocations'].values()))
        assert state['epoch']==2 and state['active_claim'] is None
        assert allocation.get('dispatch_marker') is None and allocation['state']=='settled' and allocation['outcome']=='cancelled_before_http'
        policy=await db[guard.AUTOMATION_POLICIES].find_one({'account_id':'123'})
        assert policy['_dispatch_claim'] is None and policy['_dispatch_days'][0]['proposal_ids']==[row['proposal_id']]
        assert capture.names.count('commitTransaction')==2
    finally:await close_worker(worker)

@pytest.mark.asyncio
@pytest.mark.parametrize('timing',['waiting','before_commit','during_ack'])
async def test_real_expiry_all_three_floor_phases(monkeypatch,actual_db,timing):
    db,capture=actual_db;db,provider=await setup(monkeypatch,db)
    await db[guard.AUTOMATION_POLICIES].update_one({'account_id':'123'},
        {'$set':{'expires_at':(NOW+timedelta(seconds=20)).isoformat()}})
    row=await preview(db)
    if timing=='waiting':
        original=authority.acknowledge_standing_dispatch_floor
        async def elapsed(database,proposal,**kwargs):
            advance(monkeypatch,NOW+timedelta(seconds=21));return await original(database,proposal,**kwargs)
        monkeypatch.setattr(authority,'acknowledge_standing_dispatch_floor',elapsed)
    elif timing=='before_commit':
        original=authority._guard_standing_dispatch_floor
        async def elapsed(*args,**kwargs):
            result=await original(*args,**kwargs);advance(monkeypatch,NOW+timedelta(seconds=21));return result
        monkeypatch.setattr(authority,'_guard_standing_dispatch_floor',elapsed)
    else:
        original=guard._commit_once;calls=0
        async def elapsed(*args):
            nonlocal calls
            calls+=1;result=await original(*args)
            if calls==2:advance(monkeypatch,NOW+timedelta(seconds=21))
            return result
        monkeypatch.setattr(guard,'_commit_once',elapsed)
    result=await execute(db,row)
    assert result['status']=='uncertain' and provider.writes==0 and result['submission_started'] is (timing=='during_ack')
    state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    assert state['epoch']==(2 if timing=='during_ack' else 1) and state['active_claim']
    assert next(iter(state['allocations'].values()))['budget_native']=='22'
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    assert capture.names.count('commitTransaction')==(2 if timing=='during_ack' else 1)

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['server91','connection_close','ack_loss_after_commit'])
async def test_real_unknown_floor_no_http_and_new_client_cannot_replay(monkeypatch,actual_db,automation_mongo_uri,fault):
    db,capture=actual_db;db,provider=await setup(monkeypatch,db);row=await preview(db)
    original=authority.acknowledge_standing_dispatch_floor;bundles=[]
    async def changed(database,proposal,**kwargs):
        bundles.append(deepcopy(kwargs['native_evidence']))
        if fault=='ack_loss_after_commit':
            raw=guard._commit_once
            async def lost(*args):await raw(*args);raise TimeoutError('standing floor ACK lost after real commit')
            monkeypatch.setattr(guard,'_commit_once',lost)
        else:
            data={'failCommands':['commitTransaction']}
            data.update({'errorCode':91} if fault=='server91' else {'closeConnection':True})
            await db.client.admin.command({'configureFailPoint':'failCommand','mode':{'times':1},'data':data})
        return await original(database,proposal,**kwargs)
    monkeypatch.setattr(authority,'acknowledge_standing_dispatch_floor',changed)
    started=time.monotonic()
    try:result=await execute(db,row);duration=time.monotonic()-started
    finally:await db.client.admin.command({'configureFailPoint':'failCommand','mode':'off'})
    assert duration<=20 and result['status']=='uncertain' and provider.writes==0
    assert capture.names.count('commitTransaction')==2
    state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    assert state['active_claim'] and next(iter(state['allocations'].values()))['budget_native']=='22'
    assert (await db[guard.AUTOMATION_POLICIES].find_one({'account_id':'123'}))['_dispatch_claim']
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    with pytest.raises(HTTPException):await execute(db,row)
    if fault=='ack_loss_after_commit':
        worker=AsyncIOMotorClient(automation_mongo_uri,serverSelectionTimeoutMS=2000,retryWrites=True)[db.name]
        try:
            stored=await db[management.PROPOSALS].find_one({'proposal_id':row['proposal_id']},{'_id':0})
            with pytest.raises(HTTPException) as error:await original(worker,stored,native_evidence=bundles[0])
            assert error.value.detail['code']=='google_budget_guard_operation_consumed'
        finally:await close_worker(worker)
    assert provider.writes==0 and capture.names.count('commitTransaction')==2
    print({'case':fault,'helper_seconds':round(duration,3),'floor_wire_attempts':1,'provider_writes':0,'hold_retained':True})
