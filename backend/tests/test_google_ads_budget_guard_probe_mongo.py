"""Actual disposable replica capability probe; no native/provider activity."""
import asyncio
import json
import os
import time
import uuid

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
import pytest

from integrations_control_center import google_ads_automation_budget_guard as guard


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',[None,{'errorCode':91},{'closeConnection':True}])
async def test_real_probe_requires_abort_ack_preserves_foreign_data_and_bounds_waiter(fault):
    uri=os.environ.get('GOOGLE_ADS_TEST_MONGO_URI')
    assert uri and uri.startswith('mongodb://127.0.0.1:')
    client=AsyncIOMotorClient(uri,serverSelectionTimeoutMS=3000,retryWrites=False)
    name='google_guard_probe_test_'+uuid.uuid4().hex;db=client[name]
    try:
        await db[guard.BUDGET_GUARDS].insert_one({'_id':'foreign-record'})
        if fault:
            await client.admin.command({'configureFailPoint':'failCommand','mode':{'times':1},
                'data':{'failCommands':['abortTransaction'],**fault}})
        ticks=[time.monotonic()];stop=asyncio.Event()
        async def heartbeat():
            while not stop.is_set():
                await asyncio.sleep(.05);ticks.append(time.monotonic())
        pulse=asyncio.create_task(heartbeat());started=time.monotonic()
        try:
            if fault:
                with pytest.raises(HTTPException) as error:await guard.probe_google_budget_guard_transactions(db)
                assert error.value.status_code==503
                assert error.value.detail['code']=='google_budget_guard_transactions_unavailable'
            else:
                capability=await guard.probe_google_budget_guard_transactions(db)
                assert capability.supported and capability.database is db
            elapsed=time.monotonic()-started
            assert elapsed<=14
        finally:stop.set();await pulse
        gap=max(b-a for a,b in zip(ticks,ticks[1:]));assert gap<1.5
        cleanup_started=time.monotonic();pending=guard._pending_probe
        if pending:
            await asyncio.wait_for(asyncio.gather(asyncio.shield(pending['task']),return_exceptions=True),timeout=30)
        await asyncio.sleep(0);assert guard._pending_probe is None
        print(json.dumps({'fault':str(fault),'response_seconds':round(elapsed,3),
            'cleanup_seconds':round(time.monotonic()-cleanup_started,3),'event_loop_gap':round(gap,3)}))
        assert await db[guard.BUDGET_GUARDS].find({}).to_list(None)==[{'_id':'foreign-record'}]
        assert guard.budget_guard_runtime_available() is False
    finally:
        await client.admin.command({'configureFailPoint':'failCommand','mode':'off'})
        await client.drop_database(name);client.close()


@pytest.mark.asyncio
async def test_strict_business_validator_cannot_break_engine_probe_or_be_bypassed_by_real_guard_write():
    uri=os.environ.get('GOOGLE_ADS_TEST_MONGO_URI')
    assert uri and uri.startswith('mongodb://127.0.0.1:')
    client=AsyncIOMotorClient(uri,serverSelectionTimeoutMS=3000,retryWrites=True)
    name='google_guard_validator_test_'+uuid.uuid4().hex;db=client[name]
    schema={'$jsonSchema':{'bsonType':'object','required':['user_id','account_id','protocol_version','epoch','allocations'],
        'additionalProperties':False,'properties':{
            '_id':{'bsonType':'string'},'user_id':{'bsonType':'string'},'account_id':{'bsonType':'string'},
            'protocol_version':{'bsonType':'int'},'epoch':{'bsonType':['int','long']},'allocations':{'bsonType':'object'},
            'active_claim':{'bsonType':['object','null']},'last_commit':{'bsonType':['object','null']}}}}
    try:
        await db.create_collection(guard.BUDGET_GUARDS,validator=schema,validationLevel='strict',validationAction='error')
        foreign=guard._new_guard({'user_id':'foreign','account_id':'999'})
        await db[guard.BUDGET_GUARDS].insert_one(foreign)
        capability=await guard.probe_google_budget_guard_transactions(db)
        assert capability.supported
        assert await db[guard.TECHNICAL_PROBES].count_documents({})==0
        assert await db[guard.BUDGET_GUARDS].find({}).to_list(None)==[foreign]
        result=await guard.run_google_budget_guard_transaction(db,user_id='owner',account_id='123',
            operation_id='valid_policy',capability=capability,
            planner=lambda snapshot:guard.PolicyMutation('standing',0,{'enabled':False}))
        assert result['committed'] and not result['provider_dispatch_authorized']
        before=await db[guard.BUDGET_GUARDS].find_one({'user_id':'owner','account_id':'123'})
        # Tighten actual collection rules. Engine capability must neither claim
        # business validation nor bypass it during a subsequent policy+guard TX.
        await db.command({'collMod':guard.BUDGET_GUARDS,'validator':{'$and':[schema,{'requires_unavailable_field':True}]},
            'validationLevel':'strict','validationAction':'error'})
        capability=await guard.probe_google_budget_guard_transactions(db)
        with pytest.raises(HTTPException) as error:
            await guard.run_google_budget_guard_transaction(db,user_id='owner',account_id='123',
                operation_id='validator_reject',capability=capability,
                planner=lambda snapshot:guard.PolicyMutation('standing',1,{'enabled':False}))
        assert error.value.detail['commit_attempted'] is False
        assert error.value.detail['preserve_execution_fence'] is True
        assert error.value.detail['automatic_retry_allowed'] is False
        assert await db[guard.BUDGET_GUARDS].find_one({'user_id':'owner','account_id':'123'})==before
        policy=await db[guard.AUTOMATION_POLICIES].find_one({'user_id':'owner','account_id':'123'})
        assert policy['revision']==1
        assert await db[guard.BUDGET_GUARDS].find_one({'_id':foreign['_id']})==foreign
    finally:await client.drop_database(name);client.close()
