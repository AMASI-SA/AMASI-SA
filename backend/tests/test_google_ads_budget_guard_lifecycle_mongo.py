"""One actual commit wire attempt; unknown ACK never releases account fence."""
import asyncio
import json
import os
import time
import uuid

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener
import pytest

from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center.google_ads_management import FENCES
from tests.test_google_ads_automation_budget_guard_mongo import GatedDB, seed, allocation


class Capture(CommandListener):
    def __init__(self):self.commits=[]
    def started(self,event):
        if event.command_name=='commitTransaction':self.commits.append(dict(event.command))
    def succeeded(self,event):pass
    def failed(self,event):pass


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',[{'errorCode':91},{'closeConnection':True},
    {'writeConcernError':{'code':64,'errmsg':'injected test uncertainty'}}])
async def test_actual_single_wire_commit_unknown_ack_keeps_fence_and_reconciles_storage_only(fault):
    uri=os.environ.get('GOOGLE_ADS_TEST_MONGO_URI');assert uri and uri.startswith('mongodb://127.0.0.1:')
    capture=Capture();client=AsyncIOMotorClient(uri,serverSelectionTimeoutMS=2000,retryWrites=True,event_listeners=[capture])
    name='g_lifecycle_'+uuid.uuid4().hex;raw=client[name];db=GatedDB(raw)
    try:
        capability=await guard.probe_google_budget_guard_transactions(db)
        await seed(db,capability)
        fence={'user_id':'owner','account_id':'123','status':'claimed','execution_claim_id':'held'}
        await raw[FENCES].insert_one(dict(fence));capture.commits.clear()
        await client.admin.command({'configureFailPoint':'failCommand','mode':{'times':1},
            'data':{'failCommands':['commitTransaction'],**fault}})
        stop=asyncio.Event();ticks=[time.monotonic()]
        async def heartbeat():
            while not stop.is_set():await asyncio.sleep(.05);ticks.append(time.monotonic())
        pulse=asyncio.create_task(heartbeat());started=time.monotonic()
        try:
            with pytest.raises(HTTPException) as error:
                await guard.run_google_budget_guard_transaction(db,user_id='owner',account_id='123',
                    operation_id='uncertain_commit',capability=capability,planner=allocation)
            elapsed=time.monotonic()-started
            assert error.value.status_code==503
            assert error.value.detail['commit_attempted'] is True
            assert error.value.detail['preserve_execution_fence'] is True
            assert error.value.detail['automatic_retry_allowed'] is False
            assert elapsed<=19
        finally:stop.set();await pulse
        gap=max(b-a for a,b in zip(ticks,ticks[1:]));assert gap<1.5
        cleanup_started=time.monotonic()
        pending=list(guard._pending_operations.values())
        if pending:await asyncio.wait_for(asyncio.gather(*(asyncio.shield(item['task']) for item in pending),return_exceptions=True),30)
        await asyncio.sleep(0)
        assert len(capture.commits)==1, 'commit helper/automatic retry must not send a second wire commit'
        command=capture.commits[0]
        assert command['$db']=='admin' and command['autocommit'] is False
        assert command['writeConcern']['w']=='majority' and command['writeConcern']['j'] is True
        assert 'lsid' in command and 'txnNumber' in command
        assert await raw[FENCES].find_one({'execution_claim_id':'held'},{'_id':0})==fence
        proof=await guard.read_google_budget_guard_commit(db,user_id='owner',account_id='123',operation_id='uncertain_commit')
        assert proof['preserve_execution_fence'] is True and proof['automatic_retry_allowed'] is False
        assert proof['provider_dispatch_authorized'] is False and proof['provider_writes']==0
        if 'writeConcernError' in fault:
            assert proof['storage_outcome']=='committed'
            assert proof['allocation']['state']=='reserved'
        else:assert proof['storage_outcome']=='not_proven'
        print(json.dumps({'fault':next(iter(fault)),'response_seconds':round(elapsed,3),
            'cleanup_after_response_seconds':round(time.monotonic()-cleanup_started,3),
            'event_loop_gap':round(gap,3),'wire_commit_attempts':len(capture.commits),'storage_outcome':proof['storage_outcome']}))
    finally:
        await client.admin.command({'configureFailPoint':'failCommand','mode':'off'})
        await client.drop_database(name);client.close()
