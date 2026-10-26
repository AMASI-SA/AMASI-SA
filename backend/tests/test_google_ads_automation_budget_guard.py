"""Budget guard uses one explicit transaction, never a retrying callback."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center.google_ads_automation_policy import AUTOMATION_POLICIES
from integrations_control_center.google_ads_automation_creation_policy import CREATION_POLICIES


class Cursor:
    def __init__(self,rows):self.rows=rows
    async def to_list(self,length):return deepcopy(self.rows[:length])


def matches(row,query):return all(row.get(key)==value for key,value in query.items())


class Collection:
    def __init__(self,db,name):self.db=db;self.name=name
    def rows(self,session):
        assert session is not None, 'Every collection operation must receive session'
        self.db.calls.append((self.name,session))
        return session.rows.setdefault(self.name,[])
    async def find_one(self,query,projection=None,*,session):
        return next((deepcopy(row) for row in self.rows(session) if matches(row,query)),None)
    def find(self,query,projection=None,*,session):
        return Cursor([row for row in self.rows(session) if matches(row,query)])
    async def insert_one(self,row,*,session):
        rows=self.rows(session)
        assert not any(old.get('_id')==row.get('_id') for old in rows if '_id' in old)
        rows.append(deepcopy(row));return SimpleNamespace(inserted_id=row.get('_id'))
    async def update_one(self,query,update,*,session):
        rows=self.rows(session);row=next((row for row in rows if matches(row,query)),None)
        if row is None:return SimpleNamespace(matched_count=0)
        row.update(deepcopy(update.get('$set',{})))
        for key,value in update.get('$inc',{}).items():row[key]=row.get(key,0)+value
        for key,value in update.get('$push',{}).items():
            row[key]=[*row.get(key,[]),*value['$each']][value['$slice']:]
        return SimpleNamespace(matched_count=1)


class Session:
    def __init__(self,db):self.db=db;self.rows=None;self.in_transaction=False;self.aborted=False;self.commit_calls=0
    async def __aenter__(self):return self
    async def __aexit__(self,*args):
        if self.in_transaction:await self.abort_transaction()
    def start_transaction(self,**kwargs):
        assert not self.in_transaction
        self.rows=deepcopy(self.db.rows);self.in_transaction=True;self.options=kwargs
    async def abort_transaction(self):self.aborted=True;self.in_transaction=False
    async def commit_transaction(self):
        self.commit_calls+=1
        if self.db.commit_gate:await self.db.commit_gate()
        self.db.rows=deepcopy(self.rows);self.in_transaction=False
        if self.db.lose_commit_ack:raise ConnectionError('injected response loss after commit')


class DB:
    def __init__(self,rows=None):
        self.rows=deepcopy(rows or {});self.calls=[];self.sessions=[];self.client=self
        self.commit_gate=None;self.lose_commit_ack=False;self.transactional=True
    def __getitem__(self,name):return Collection(self,name)
    async def command(self,name,*,session):
        assert session is not None
        return {'setName':'test','logicalSessionTimeoutMinutes':30} if self.transactional else {}
    async def start_session(self):
        session=Session(self);self.sessions.append(session);return session


async def supported(db):return await guard.probe_google_budget_guard_transactions(db)


def mutation(snapshot):
    return guard.PolicyMutation('standing',0,{'enabled':False,'max_account_daily_budget_native':'55'},event={'kind':'owner_save'})


async def run(db,planner=mutation,*,capability=None,user_id='owner',account_id='123',operation_id='op1'):
    return await guard.run_google_budget_guard_transaction(db,user_id=user_id,account_id=account_id,
        operation_id=operation_id,capability=capability,planner=planner)


@pytest.mark.asyncio
async def test_default_runtime_unavailable_and_no_implicit_probe_or_write():
    db=DB()
    assert guard.budget_guard_runtime_available() is False
    with pytest.raises(HTTPException):await run(db)
    assert not db.sessions and not db.rows


@pytest.mark.asyncio
async def test_probe_requires_real_transaction_and_rolls_back_probe_write():
    db=DB();capability=await supported(db)
    assert capability.supported and not db.rows and db.sessions[0].aborted
    assert guard.budget_guard_runtime_available() is False
    db.transactional=False
    with pytest.raises(HTTPException):await supported(db)


@pytest.mark.asyncio
async def test_policy_and_guard_commit_in_same_explicit_session():
    db=DB();capability=await supported(db);reply=await run(db,capability=capability)
    assert reply['committed'] and reply['provider_dispatch_authorized'] is False
    assert len(db.rows[AUTOMATION_POLICIES])==1
    policy=db.rows[AUTOMATION_POLICIES][0]
    assert policy['user_id']=='owner' and policy['account_id']=='123' and policy['revision']==1
    stored=db.rows[guard.BUDGET_GUARDS][0]
    assert stored['epoch']==1 and stored['last_commit']['operation_id']=='op1'
    session=db.sessions[-1]
    assert all(call_session is session for _,call_session in db.calls[1:])
    assert session.commit_calls==1 and session.options['read_concern'].level=='snapshot'


@pytest.mark.asyncio
async def test_snapshot_is_deeply_immutable_and_absence_is_server_loaded():
    db=DB();capability=await supported(db)
    def decide(snapshot):
        assert snapshot.standing_policy is None and snapshot.creation_policy is None
        assert snapshot.standing_policy_present is False
        with pytest.raises(TypeError):snapshot.guard['epoch']=999
        return mutation(snapshot)
    await run(db,decide,capability=capability)


@pytest.mark.asyncio
async def test_async_planner_is_rejected_and_transaction_rolls_back():
    db=DB();capability=await supported(db)
    async def forbidden(snapshot):return mutation(snapshot)
    with pytest.raises(HTTPException):await run(db,forbidden,capability=capability)
    assert not db.rows and db.sessions[-1].aborted


@pytest.mark.asyncio
@pytest.mark.parametrize('values',[{'_creation_ledger':{}},{'user_id':'other'},{'revision':100},{'$set':{}},{'unknown':True}])
async def test_policy_mutation_cannot_write_private_or_arbitrary_fields(values):
    db=DB();capability=await supported(db)
    with pytest.raises(HTTPException):
        await run(db,lambda snapshot:guard.PolicyMutation('creation',0,values),capability=capability)
    assert not db.rows


@pytest.mark.asyncio
async def test_policy_revision_failure_rolls_back_guard_and_preserves_private_keys():
    db=DB({CREATION_POLICIES:[{'user_id':'owner','account_id':'123','revision':2,'_creation_claim':{'held':True}}]})
    capability=await supported(db);before=deepcopy(db.rows)
    with pytest.raises(HTTPException):await run(db,lambda snapshot:guard.PolicyMutation('creation',1,{'enabled':False}),capability=capability)
    assert db.rows==before
    await run(db,lambda snapshot:guard.PolicyMutation('creation',2,{'enabled':False}),capability=capability)
    assert db.rows[CREATION_POLICIES][0]['_creation_claim']=={'held':True}


@pytest.mark.asyncio
async def test_commit_ack_loss_never_returns_positive_permission_or_retries():
    db=DB();capability=await supported(db);db.lose_commit_ack=True
    with pytest.raises(HTTPException) as caught:await run(db,capability=capability)
    assert caught.value.detail['commit_attempted'] and caught.value.detail['preserve_execution_fence']
    assert caught.value.detail['automatic_retry_allowed'] is False
    assert db.sessions[-1].commit_calls==1 and db.rows[guard.BUDGET_GUARDS][0]['epoch']==1


@pytest.mark.asyncio
async def test_timeout_during_commit_preserves_unknown_and_never_retries(monkeypatch):
    db=DB();capability=await supported(db)
    async def blocked():await asyncio.Event().wait()
    db.commit_gate=blocked
    with pytest.raises(HTTPException) as caught:
        await guard.run_google_budget_guard_transaction(db,user_id='owner',account_id='123',operation_id='op1',
            capability=capability,planner=mutation,timeout_seconds=.01)
    assert caught.value.detail['commit_attempted'] and caught.value.detail['preserve_execution_fence']
    assert db.sessions[-1].commit_calls==1
