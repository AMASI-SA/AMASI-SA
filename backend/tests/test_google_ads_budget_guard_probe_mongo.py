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
