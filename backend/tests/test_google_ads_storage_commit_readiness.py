"""Owner route and raw protocol without business/provider collaborators."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

from fastapi import FastAPI, APIRouter, HTTPException
import httpx
import pytest

from integrations_control_center import google_ads_storage_commit_readiness as probe
from tests.test_google_ads_automation_policy import DB as OwnerDB


class Session:
    def __init__(self,db):self.db=db;self.tx=False;self.rows=None;self.committed=False;self.ended=False
    def start_transaction(self,**kw):
        assert kw['read_concern'].level=='snapshot' and kw['write_concern'].document=={'w':'majority','j':True}
        self.tx=True;self.rows=deepcopy(self.db.rows)
    async def end_session(self):self.ended=True;self.tx=False
    async def __aenter__(self):raise AssertionError('no synchronous delegate cleanup')


class DB:
    def __init__(self,fault=None):
        self.rows={probe.SLOTS:[],probe.PAYLOADS:[]};self.fault=fault;self.commits=0;self.deletes=0;self.sessions=[]
        self.client=self;self.admin=self;self.users=OwnerDB().users
        self.users.rows.append({'id':'staff','role':'staff','owner_id':'owner'})
    async def start_session(self):
        value=Session(self);self.sessions.append(value);return value
    async def command(self,command,*,session,**kw):
        assert not session.ended
        if command=='hello':
            return {'logicalSessionTimeoutMinutes':30,**({'msg':'isdbgrid'} if self.fault=='mongos' else {} if self.fault=='standalone' else {'setName':'rs'})}
        key=next(iter(command));name=command[key]
        rows=session.rows if session.tx else self.rows
        if key=='commitTransaction':
            self.commits+=1;assert command['writeConcern']==probe.WC
            self.rows=deepcopy(session.rows);session.committed=True
            if self.fault=='commit_lost':raise ConnectionError('private-host')
            if self.fault=='commit_wce':return {'ok':1,'writeConcernError':{'code':64}}
            return {'ok':1}
        assert name in (probe.SLOTS,probe.PAYLOADS), 'business/provider collections forbidden'
        if key=='find':
            # Fresh reads MUST NOT reuse the public driver's post-raw-commit txn.
            assert not session.committed
            if not session.tx:assert command['readConcern']=={'level':'majority'}
            found=[deepcopy(r) for r in rows[name] if all(r.get(k)==v for k,v in command['filter'].items())]
            if self.fault=='readback' and name==probe.PAYLOADS and self.commits:found=[]
            return {'ok':1,'cursor':{'firstBatch':found}}
        if key=='insert':
            for row in command['documents']:
                if any(r['_id']==row['_id'] for r in rows[name]):return {'ok':1,'writeErrors':[{'code':11000}]}
                rows[name].append(deepcopy(row))
            if self.fault=='reserve_lost' and name==probe.SLOTS:raise ConnectionError('private-host')
            return {'ok':1,'n':1}
        if key=='update':
            update=command['updates'][0]
            for row in rows[name]:
                if all(row.get(k)==v for k,v in update['q'].items()):
                    row.update(deepcopy(update['u']['$set']))
                    for field in update['u'].get('$unset',{}):row.pop(field,None)
                    return {'ok':1,'n':1,'nModified':1}
            return {'ok':1,'n':0,'nModified':0}
        if key=='delete':
            self.deletes+=1;query=command['deletes'][0]['q']
            found=[row for row in rows[name] if all(row.get(k)==v for k,v in query.items())]
            rows[name][:]=[row for row in rows[name] if row not in found]
            if self.fault=='delete_lost':raise ConnectionError('private-host')
            return {'ok':1,'n':len(found)}
        raise AssertionError(command)


def mounted(db,user):
    app,router=FastAPI(),APIRouter()
    async def current():return user
    probe.attach_google_ads_storage_commit_readiness_routes(router,db,current)
    app.include_router(router);return app


async def request(db,user={'id':'owner'},method='post',body=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(mounted(db,user)),base_url='http://test') as client:
        return await client.request(method,'/google_ads/automation/storage-commit-readiness',**({'json':{} if body is None else body} if method=='post' else {}))


@pytest.mark.asyncio
async def test_owner_success_cross_document_commit_fresh_majority_read_cleanup_receipt_and_repeat():
    db=DB();foreign={'_id':'foreign','keep':True};db.rows[probe.PAYLOADS].append(foreign)
    response=await request(db)
    assert response.status_code==200 and response.json()==probe._result('verified',True)
    assert db.commits==db.deletes==1 and db.rows[probe.PAYLOADS]==[foreign]
    assert db.rows[probe.SLOTS][0]['state']=='completed' and len(db.sessions)==2
    second=await request(db)
    assert second.status_code==200 and db.commits==2 and db.rows[probe.SLOTS][0]['version']==2
    receipt=await request(db,method='get')
    assert receipt.json()['status']=='historical_completed_receipt'
    assert receipt.json()['commit_majority_j_ack_verified'] is False and receipt.json()['funding_ready'] is False
    assert db.commits==2


@pytest.mark.asyncio
@pytest.mark.parametrize('user,status',[(None,401),({'id':'missing'},401),({'id':'staff'},403)])
async def test_authentication_denies_before_probe_or_writes(user,status):
    db=DB();response=await request(db,user)
    assert response.status_code==status and not db.sessions and not db.rows[probe.SLOTS]


@pytest.mark.asyncio
async def test_caller_cannot_supply_owner_or_collection():
    db=DB();response=await request(db,body={'owner_id':'other','collection':'policies'})
    assert response.status_code==422 and not db.sessions


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['standalone','mongos'])
async def test_unsupported_topology_creates_no_receipt_or_payload(fault):
    db=DB(fault);response=await request(db)
    assert response.status_code==503 and response.json()['detail']['code']=='google_storage_commit_unsupported_topology'
    assert db.rows=={probe.SLOTS:[],probe.PAYLOADS:[]} and db.commits==db.deletes==0


@pytest.mark.asyncio
@pytest.mark.parametrize('fault,state,commits,deletes',[('reserve_lost','reserved',0,0),('commit_lost','committed',1,0),
    ('commit_wce','committed',1,0),('readback','committed',1,0),('delete_lost','committed',1,1)])
async def test_ambiguity_keeps_durable_slot_and_no_recommit_or_delete_retry(fault,state,commits,deletes):
    db=DB(fault);response=await request(db)
    assert response.status_code==503 and 'private-host' not in response.text
    assert db.rows[probe.SLOTS][0]['state']==state
    assert (db.commits,db.deletes)==(commits,deletes)
    db.fault=None
    again=await request(db)
    assert again.status_code==503 and again.json()['detail']['code']=='google_storage_commit_reconciliation_required'
    assert (db.commits,db.deletes)==(commits,deletes)


@pytest.mark.asyncio
async def test_deadline_keeps_executor_future_slot_and_blocks_second_probe(monkeypatch):
    monkeypatch.setattr(probe,'TIMEOUT_SECONDS',.02)
    db=DB();future=asyncio.get_running_loop().create_future();original=db.command
    async def command(value,**kw):
        if isinstance(value,dict) and 'commitTransaction' in value:return await future
        return await original(value,**kw)
    db.command=command
    response=await request(db)
    assert response.status_code==503 and not future.cancelled() and probe._active is not None
    active=probe._active
    try:
        second=await request(db)
        assert second.json()['detail']['code']=='google_storage_commit_busy'
        assert not active['task'].done()
    finally:
        future.set_exception(ConnectionError('unknown commit'))
        await asyncio.gather(active['task'],return_exceptions=True);await asyncio.sleep(0)
    assert probe._active is None and db.rows[probe.SLOTS][0]['state']=='reserved'


@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['before_commit','after_commit'])
async def test_original_deadline_prevents_late_commit_or_cleanup_and_success(monkeypatch,phase):
    db=DB();clock=[100.0];original=db.command
    monkeypatch.setattr(probe,'_clock',lambda:clock[0])
    async def command(value,**kw):
        reply=await original(value,**kw)
        if isinstance(value,dict) and (phase=='before_commit' and value.get('insert')==probe.PAYLOADS
            or phase=='after_commit' and 'commitTransaction' in value):clock[0]+=30
        return reply
    db.command=command
    response=await request(db)
    assert response.status_code==503 and not db.deletes
    assert db.commits==(0 if phase=='before_commit' else 1)
    assert db.rows[probe.SLOTS][0]['state']==('reserved' if phase=='before_commit' else 'committed')


@pytest.mark.asyncio
async def test_cleanup_future_keeps_original_context_and_resource_slot_until_completion(monkeypatch):
    monkeypatch.setattr(probe,'TIMEOUT_SECONDS',.02)
    db=DB();future=asyncio.get_running_loop().create_future();original=db.start_session
    async def session():
        result=await original()
        if len(db.sessions)==1:
            def end_session():return future
            result.end_session=end_session
        return result
    db.start_session=session
    response=await request(db);active=probe._active
    assert response.status_code==503 and active and not active['task'].done() and len(db.sessions)==1
    try:
        active['task'].cancel();await asyncio.sleep(0)
        assert not future.cancelled() and not active['task'].done()
    finally:
        future.set_result(None)
        await asyncio.gather(active['task'],return_exceptions=True);await asyncio.sleep(0)
    assert probe._active is None and not db.deletes and db.rows[probe.SLOTS][0]['state']=='committed'
