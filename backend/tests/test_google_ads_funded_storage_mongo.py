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
from tests.test_google_ads_funded_creation_authority import setup,reserve,floor,settle,NOW
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
            assert await db[grants.RUNS].find_one({'grant_key':key}) is None
            assert (await db[grants.GRANTS].find_one({'grant_key':key}))['consumed'] is False
    finally:other.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['disabled','role','password'])
@pytest.mark.parametrize('stage',['reserve','floor'])
async def test_replica_owner_writer_between_snapshot_and_marker_conflicts(monkeypatch,actual_db,automation_mongo_uri,change,stage):
    db,capture=actual_db;db,provider,key,row=await setup(monkeypatch,db)
    if stage=='floor':await reserve(db,key,row)
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
        else:assert await db[grants.RUNS].find_one({'grant_key':key}) is None
    finally:other.close()

@pytest.mark.asyncio
async def test_replica_users_validator_rejects_marker_without_partial_claim(monkeypatch,actual_db):
    db,capture=actual_db;db,provider,key,row=await setup(monkeypatch,db)
    # Validator permits the existing owner and disallows only the new marker.
    await db.command({'collMod':'users','validator':{'mezan_google_funded_authority_epoch':{'$exists':False}},'validationLevel':'strict'})
    with pytest.raises(HTTPException):await reserve(db,key,row)
    assert await db[grants.RUNS].find_one({'grant_key':key}) is None
    assert (await db[grants.GRANTS].find_one({'grant_key':key}))['consumed'] is False
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    assert provider.writes==0
