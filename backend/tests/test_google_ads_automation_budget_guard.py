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
        rows.append(deepcopy(row));return SimpleNamespace(inserted_id=row.get('_id'), acknowledged=True)
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
    async def end_session(self):
        if self.in_transaction: await self.abort_transaction()
    async def commit_transaction(self):
        self.commit_calls+=1
        if self.db.commit_gate:await self.db.commit_gate()
        self.db.rows=deepcopy(self.rows);self.in_transaction=False
        if self.db.lose_commit_ack:raise ConnectionError('injected response loss after commit')


class DB:
    def __init__(self,rows=None):
        self.rows=deepcopy(rows or {});self.calls=[];self.sessions=[];self.client=self;self.admin=self
        self.commit_gate=None;self.lose_commit_ack=False;self.transactional=True
    def __getitem__(self,name):return Collection(self,name)
    async def command(self,name,*,session):
        assert session is not None
        if isinstance(name, dict):
            assert name['abortTransaction'] == 1
            await session.abort_transaction()
            return {'ok':1}
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


def allocation(snapshot):
    return guard.LaunchAllocation('launch1','google_create_'+'a'*64,'999','proposal1','claim1','SAR','15','b'*64)


def with_creation():
    return DB({CREATION_POLICIES:[{'user_id':'owner','account_id':'123','revision':1,'enabled':True,
        'max_account_daily_budget_native':'55','_creation_ledger':{'original':{'held':True}}}]})


@pytest.mark.asyncio
async def test_allocation_binds_server_loaded_absence_and_is_not_dispatch_authority():
    db=with_creation();capability=await supported(db)
    result=await run(db,allocation,capability=capability)
    assert result['committed'] and result['provider_dispatch_authorized'] is False
    stored=db.rows[guard.BUDGET_GUARDS][0];entry=stored['allocations']['launch1']
    assert entry['standing_policy_present'] is False and entry['standing_policy_revision'] is None
    assert entry['creation_policy_revision']==1 and entry['guard_epoch_before']==0
    assert stored['active_claim']['execution_claim_id']=='claim1'
    assert db.rows[CREATION_POLICIES][0]['_creation_ledger']=={'original':{'held':True}}


@pytest.mark.asyncio
@pytest.mark.parametrize('amount',['0','0.00000001','0.00000100','1.0000001','NaN','Infinity','-1','01','1000000000000'])
async def test_allocation_budget_is_positive_exact_six_digit_decimal(amount):
    db=with_creation();capability=await supported(db)
    def invalid(snapshot):
        return guard.LaunchAllocation('launch1','google_create_'+'a'*64,'999','proposal1','claim1','SAR',amount,'b'*64)
    with pytest.raises(HTTPException):await run(db,invalid,capability=capability)
    assert guard.BUDGET_GUARDS not in db.rows


@pytest.mark.asyncio
async def test_duplicate_allocation_or_creation_identity_never_reserves_again():
    db=with_creation();capability=await supported(db);await run(db,allocation,capability=capability)
    before=deepcopy(db.rows)
    with pytest.raises(HTTPException):await run(db,allocation,capability=capability,operation_id='op2')
    assert db.rows==before
    # Even after a future proved terminal settlement clears active_claim, the
    # permanently consumed creation identity must never auto-reenable.
    db.rows[guard.BUDGET_GUARDS][0]['active_claim']=None
    with pytest.raises(HTTPException):await run(db,allocation,capability=capability,operation_id='op3')


@pytest.mark.asyncio
async def test_capability_is_database_bound_and_expired_probe_cannot_be_used(monkeypatch):
    db=with_creation();other=with_creation();capability=await supported(db)
    with pytest.raises(HTTPException):await run(other,allocation,capability=capability)
    assert not other.sessions
    monkeypatch.setattr(guard.time,'monotonic',lambda:capability.probed_monotonic+61)
    with pytest.raises(HTTPException):await run(db,allocation,capability=capability)
    assert len(db.sessions)==1


@pytest.mark.asyncio
async def test_tenant_and_account_scope_cannot_read_or_modify_other_policies():
    db=with_creation();capability=await supported(db)
    def other(snapshot):
        assert snapshot.creation_policy is None and snapshot.user_id=='other'
        return mutation(snapshot)
    await run(db,other,capability=capability,user_id='other')
    assert db.rows[CREATION_POLICIES][0]['user_id']=='owner'
    assert db.rows[AUTOMATION_POLICIES][0]['user_id']=='other'


@pytest.mark.asyncio
async def test_policy_lowering_does_not_remove_or_refund_existing_allocation():
    db=with_creation();capability=await supported(db);await run(db,allocation,capability=capability)
    before=deepcopy(db.rows[guard.BUDGET_GUARDS][0])
    await run(db,lambda snapshot:guard.PolicyMutation('creation',1,{'enabled':False,'max_account_daily_budget_native':'1'}),
        capability=capability,operation_id='owner_revoke')
    after=db.rows[guard.BUDGET_GUARDS][0]
    assert before['allocations']==after['allocations'] and before['active_claim']==after['active_claim']
    assert after['epoch']==before['epoch']+1


@pytest.mark.asyncio
async def test_external_cancellation_after_commit_holds_fence_and_never_retries(monkeypatch):
    db=with_creation();capability=await supported(db)
    original=Session.commit_transaction
    async def cancelled(session):await original(session);raise asyncio.CancelledError()
    monkeypatch.setattr(Session,'commit_transaction',cancelled)
    with pytest.raises(asyncio.CancelledError) as caught:await run(db,allocation,capability=capability)
    assert caught.value.commit_attempted and caught.value.preserve_execution_fence
    assert db.rows[guard.BUDGET_GUARDS][0]['active_claim'] and db.sessions[-1].commit_calls==1


@pytest.mark.asyncio
async def test_read_based_recovery_proves_storage_only_and_never_grants_dispatch():
    db=with_creation();capability=await supported(db);db.lose_commit_ack=True
    with pytest.raises(HTTPException):await run(db,allocation,capability=capability)
    before=deepcopy(db.rows)
    proof=await guard.read_google_budget_guard_commit(db,user_id='owner',account_id='123',operation_id='op1')
    assert proof['storage_outcome']=='committed' and proof['allocation']['state']=='reserved'
    assert proof['provider_dispatch_authorized'] is False and proof['preserve_execution_fence']
    assert proof['automatic_retry_allowed'] is False and db.rows==before
    missing=await guard.read_google_budget_guard_commit(db,user_id='owner',account_id='123',operation_id='unknown')
    assert missing['storage_outcome']=='not_proven' and missing['preserve_execution_fence']
    foreign=await guard.read_google_budget_guard_commit(db,user_id='other',account_id='123',operation_id='op1')
    assert foreign['storage_outcome']=='not_proven' and foreign['allocation'] is None


@pytest.mark.asyncio
async def test_overwritten_policy_commit_record_is_not_proof_of_historical_absence():
    db=DB();capability=await supported(db);await run(db,capability=capability)
    await run(db,lambda snapshot:guard.PolicyMutation('standing',1,{'enabled':False}),capability=capability,operation_id='op2')
    proof=await guard.read_google_budget_guard_commit(db,user_id='owner',account_id='123',operation_id='op1')
    assert proof['storage_outcome']=='not_proven' and proof['preserve_execution_fence']


@pytest.mark.asyncio
async def test_duplicate_policy_rows_never_establish_absence_or_reserve():
    db=with_creation();db.rows[CREATION_POLICIES].append(deepcopy(db.rows[CREATION_POLICIES][0]))
    capability=await supported(db)
    with pytest.raises(HTTPException):await run(db,allocation,capability=capability)
    assert guard.BUDGET_GUARDS not in db.rows
