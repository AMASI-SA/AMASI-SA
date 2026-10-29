"""Actual disposable replica commit/readback/cleanup and uncertainty proof."""
import asyncio
import importlib.util
import json
import os
import time
import uuid

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener
import pytest

from integrations_control_center import google_ads_storage_commit_readiness as probe


class Capture(CommandListener):
    def __init__(self):self.commits=[];self.deletes=[]
    def started(self,event):
        if event.command_name=='commitTransaction':self.commits.append(dict(event.command))
        if event.command_name=='delete':self.deletes.append(dict(event.command))
    def succeeded(self,event):pass
    def failed(self,event):pass


def client_and_db():
    uri=os.environ.get('GOOGLE_ADS_TEST_MONGO_URI');assert uri and uri.startswith('mongodb://127.0.0.1:')
    capture=Capture();client=AsyncIOMotorClient(uri,serverSelectionTimeoutMS=2000,retryWrites=True,event_listeners=[capture])
    return client,client['g_commit_preflight_'+uuid.uuid4().hex],capture


@pytest.mark.asyncio
async def test_real_commit_fresh_readback_cleanup_repeat_receipt_and_foreign_record():
    client,db,capture=client_and_db()
    try:
        foreign={'_id':'foreign-existing','keep':True}
        await db[probe.PAYLOADS].insert_one(foreign)
        result=await probe._run(db,'owner',probe._probe)
        assert result==probe._result('verified',True)
        assert len(capture.commits)==1 and len(capture.deletes)==1
        commit=capture.commits[0]
        assert commit['writeConcern']['w']=='majority' and commit['writeConcern']['j'] is True
        assert commit['autocommit'] is False and commit['$db']=='admin'
        assert await db[probe.PAYLOADS].find({}).to_list(None)==[foreign]
        receipt=await db[probe.SLOTS].find_one({'owner_id':'owner'})
        assert receipt['state']=='completed' and receipt['version']==1
        assert (await probe._run(db,'owner',probe._probe))['status']=='verified'
        assert len(capture.commits)==2 and len(capture.deletes)==2
        assert (await db[probe.SLOTS].find_one({'owner_id':'owner'}))['version']==2
        historical=await probe._run(db,'owner',probe._latest)
        assert historical['status']=='historical_completed_receipt' and not historical['commit_majority_j_ack_verified']
    finally:await client.drop_database(db.name);client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',[{'errorCode':91},{'closeConnection':True},
    {'writeConcernError':{'code':64,'errmsg':'injected test uncertainty'}}])
async def test_real_commit_fault_keeps_slot_and_never_recommits_or_deletes(fault):
    client,db,capture=client_and_db()
    try:
        await client.admin.command({'configureFailPoint':'failCommand','mode':{'times':1},
            'data':{'failCommands':['commitTransaction'],**fault}})
        ticks=[time.monotonic()];stop=asyncio.Event()
        async def pulse():
            while not stop.is_set():await asyncio.sleep(.05);ticks.append(time.monotonic())
        beat=asyncio.create_task(pulse());started=time.monotonic()
        try:
            with pytest.raises(HTTPException):await probe._run(db,'owner',probe._probe)
            elapsed=time.monotonic()-started;assert elapsed<=24
        finally:stop.set();await beat
        gap=max(b-a for a,b in zip(ticks,ticks[1:]));assert gap<1.5
        active=probe._active;cleanup=time.monotonic()
        if active:await asyncio.wait_for(asyncio.gather(asyncio.shield(active['task']),return_exceptions=True),30)
        await asyncio.sleep(0)
        assert len(capture.commits)==1 and not capture.deletes
        receipt=await db[probe.SLOTS].find_one({'owner_id':'owner'})
        assert receipt['state'] in ('reserved','committed')
        if 'writeConcernError' in fault:
            assert receipt['state']=='committed' and await db[probe.PAYLOADS].count_documents({'owner_id':'owner'})==1
        with pytest.raises(HTTPException) as again:await probe._run(db,'owner',probe._probe)
        assert again.value.detail['code']=='google_storage_commit_reconciliation_required'
        assert len(capture.commits)==1 and not capture.deletes
        print(json.dumps({'fault':next(iter(fault)),'response_seconds':round(elapsed,3),
            'cleanup_seconds':round(time.monotonic()-cleanup,3),'event_loop_gap':round(gap,3),'wire_commits':len(capture.commits)}))
    finally:
        await client.admin.command({'configureFailPoint':'failCommand','mode':'off'})
        await client.drop_database(db.name);client.close()


@pytest.mark.asyncio
async def test_real_uncertain_cleanup_keeps_receipt_without_second_delete():
    client,db,capture=client_and_db()
    try:
        await client.admin.command({'configureFailPoint':'failCommand','mode':{'times':1},
            'data':{'failCommands':['delete'],'closeConnection':True}})
        with pytest.raises(HTTPException):await probe._run(db,'owner',probe._probe)
        active=probe._active
        if active:await asyncio.gather(active['task'],return_exceptions=True)
        await asyncio.sleep(0)
        assert len(capture.commits)==1 and len(capture.deletes)==1
        assert (await db[probe.SLOTS].find_one({'owner_id':'owner'}))['state']=='committed'
        with pytest.raises(HTTPException):await probe._run(db,'owner',probe._probe)
        assert len(capture.commits)==1 and len(capture.deletes)==1
    finally:
        await client.admin.command({'configureFailPoint':'failCommand','mode':'off'})
        await client.drop_database(db.name);client.close()


@pytest.mark.asyncio
async def test_independent_worker_slots_race_only_one_can_commit(monkeypatch):
    client,db,capture=client_and_db()
    spec=importlib.util.spec_from_file_location('integrations_control_center.commit_probe_worker2',probe.__file__)
    other=importlib.util.module_from_spec(spec);spec.loader.exec_module(other)
    barrier=asyncio.Barrier(2)
    def gate(original):
        async def find(database,collection,query,session,majority=True):
            row=await original(database,collection,query,session,majority)
            if collection==probe.SLOTS and row is None:await barrier.wait()
            return row
        return find
    monkeypatch.setattr(probe,'_find',gate(probe._find));monkeypatch.setattr(other,'_find',gate(other._find))
    try:
        results=await asyncio.gather(probe._run(db,'owner',probe._probe),other._run(db,'owner',other._probe),return_exceptions=True)
        assert sum(isinstance(result,dict) and result.get('status')=='verified' for result in results)==1
        assert sum(isinstance(result,HTTPException) for result in results)==1
        assert len(capture.commits)==1
        assert await db[probe.SLOTS].count_documents({'owner_id':'owner'})==1
    finally:await client.drop_database(db.name);client.close()
