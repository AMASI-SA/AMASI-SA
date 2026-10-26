"""Actual replica Mongo transactions serialize policy absence and allocations.

Explicit disposable loopback URI only. No provider/model/credential access. All
races schedule genuine Mongo writes and commit; matching is never substituted.
"""
import asyncio
from copy import deepcopy
import uuid

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorClientSession
import pytest
import pytest_asyncio

from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center.google_ads_automation_creation_policy import CREATION_POLICIES
from integrations_control_center.google_ads_automation_policy import AUTOMATION_POLICIES
from tests.test_google_ads_automation_policy_mongo import automation_mongo_uri


class GatedCollection:
    def __init__(self,owner,name):self.owner=owner;self.collection=owner.raw[name];self.name=name
    def __getattr__(self,key):return getattr(self.collection,key)
    async def update_one(self,query,update,**kwargs):
        assert kwargs.get('session') is not None
        operation=(update.get('$set',{}).get('last_commit') or {}).get('operation_id')
        if self.name==guard.BUDGET_GUARDS and operation==self.owner.block_operation:
            self.owner.entered.set();await self.owner.resume.wait()
        return await self.collection.update_one(query,update,**kwargs)


class GatedDB:
    def __init__(self,raw):
        self.raw=raw;self.client=raw.client;self.block_operation=None
        self.entered=asyncio.Event();self.resume=asyncio.Event()
    def __getitem__(self,name):return GatedCollection(self,name)
    async def command(self,*args,**kwargs):return await self.raw.command(*args,**kwargs)


@pytest_asyncio.fixture
async def mongo_guard(automation_mongo_uri):
    client=AsyncIOMotorClient(automation_mongo_uri,serverSelectionTimeoutMS=1000)
    name='g_budget_tx_'+uuid.uuid4().hex
    assert len(name.encode('ascii'))<=63
    raw=client[name];db=GatedDB(raw)
    try:
        hello=await client.admin.command('hello')
        assert hello.get('setName'), 'Configured budget guard tests require replica Mongo transactions'
        await raw[AUTOMATION_POLICIES].create_index([('user_id',1),('account_id',1)],unique=True)
        await raw[CREATION_POLICIES].create_index([('user_id',1),('account_id',1)],unique=True)
        capability=await guard.probe_google_budget_guard_transactions(db)
        yield db,capability
    finally:
        await client.drop_database(name);client.close()


async def run(db,capability,planner,operation_id,*,owner='owner',account='123'):
    return await guard.run_google_budget_guard_transaction(db,user_id=owner,account_id=account,operation_id=operation_id,
        capability=capability,planner=planner)


async def seed(db,capability,*,standing=True):
    await run(db,capability,lambda snapshot:guard.PolicyMutation('creation',0,
        {'enabled':True,'auto_enable_new':True,'max_account_daily_budget_native':'55'}),'seed_creation')
    if standing:
        await run(db,capability,lambda snapshot:guard.PolicyMutation('standing',0,
            {'enabled':True,'max_account_daily_budget_native':'55'}),'seed_standing')


def allocation(snapshot):
    from decimal import Decimal
    # Future authority must perform all remaining ownership/native/activation
    # checks. This internal pure decision isolates the actual cap race here.
    caps=[Decimal(snapshot.creation_policy['max_account_daily_budget_native'])]
    if snapshot.standing_policy_present and snapshot.standing_policy['enabled']:
        caps.append(Decimal(snapshot.standing_policy['max_account_daily_budget_native']))
    if Decimal('40')+Decimal('15')>min(caps):raise ValueError('insufficient owner-approved headroom')
    return guard.LaunchAllocation('launch1','google_create_'+'a'*64,'999','proposal1','claim1','SAR','15','b'*64)


@pytest.mark.asyncio
@pytest.mark.parametrize('standing_exists',[False,True])
async def test_real_transaction_new_standing_after_absence_or_lower_cap_defeats_old_launch(mongo_guard,standing_exists):
    db,capability=mongo_guard;await seed(db,capability,standing=standing_exists)
    db.block_operation='launch_old'
    task=asyncio.create_task(run(db,capability,allocation,'launch_old'));await asyncio.wait_for(db.entered.wait(),5)
    expected=1 if standing_exists else 0
    await run(db,capability,lambda snapshot:guard.PolicyMutation('standing',expected,
        {'enabled':True,'max_account_daily_budget_native':'10'}),'owner_lower_cap')
    db.resume.set()
    with pytest.raises(HTTPException) as caught:await task
    assert caught.value.detail['preserve_execution_fence'] is True
    stored=await db.raw[guard.BUDGET_GUARDS].find_one({'user_id':'owner','account_id':'123'})
    assert stored['allocations']=={} and stored['active_claim'] is None
    assert (await db.raw[AUTOMATION_POLICIES].find_one({'user_id':'owner','account_id':'123'}))['max_account_daily_budget_native']=='10'


@pytest.mark.asyncio
async def test_real_transaction_claim_first_preserves_allocation_when_owner_lowers_cap(mongo_guard):
    db,capability=mongo_guard;await seed(db,capability)
    reply=await run(db,capability,allocation,'launch_first')
    assert reply['committed'] and reply['provider_dispatch_authorized'] is False
    before=await db.raw[guard.BUDGET_GUARDS].find_one({'user_id':'owner','account_id':'123'})
    await run(db,capability,lambda snapshot:guard.PolicyMutation('standing',1,
        {'enabled':True,'max_account_daily_budget_native':'10'}),'later_lower')
    after=await db.raw[guard.BUDGET_GUARDS].find_one({'user_id':'owner','account_id':'123'})
    assert before['allocations']==after['allocations'] and before['active_claim']==after['active_claim']
    assert after['epoch']==before['epoch']+1


@pytest.mark.asyncio
async def test_real_transaction_abort_does_not_leave_guard_or_policy(mongo_guard):
    db,capability=mongo_guard
    def invalid(snapshot):return guard.PolicyMutation('creation',5,{'enabled':False})
    with pytest.raises(HTTPException):await run(db,capability,invalid,'wrong_revision')
    assert await db.raw[guard.BUDGET_GUARDS].count_documents({})==0
    assert await db.raw[CREATION_POLICIES].count_documents({})==0


@pytest.mark.asyncio
async def test_real_commit_response_loss_retains_allocation_and_never_returns_permission(mongo_guard,monkeypatch):
    db,capability=mongo_guard;await seed(db,capability)
    original=AsyncIOMotorClientSession.commit_transaction;calls=0
    async def lost(session):
        nonlocal calls
        calls+=1;await original(session);raise ConnectionError('injected loss after genuine Mongo commit')
    monkeypatch.setattr(AsyncIOMotorClientSession,'commit_transaction',lost)
    with pytest.raises(HTTPException) as caught:await run(db,capability,allocation,'unknown_commit')
    assert caught.value.detail['commit_attempted'] and caught.value.detail['preserve_execution_fence']
    assert caught.value.detail['automatic_retry_allowed'] is False and calls==1
    stored=await db.raw[guard.BUDGET_GUARDS].find_one({'user_id':'owner','account_id':'123'})
    assert stored['active_claim']['operation_id']=='unknown_commit' and stored['allocations']['launch1']['state']=='reserved'


@pytest.mark.asyncio
async def test_real_transaction_tenant_scope_and_policy_absence_are_independent(mongo_guard):
    db,capability=mongo_guard;await seed(db,capability,standing=True)
    await run(db,capability,lambda snapshot:guard.PolicyMutation('creation',0,
        {'enabled':True,'max_account_daily_budget_native':'55'}),'other_creation',owner='other')
    def other(snapshot):
        assert snapshot.user_id=='other' and snapshot.standing_policy is None
        return guard.PolicyMutation('standing',0,{'enabled':False,'max_account_daily_budget_native':'1'})
    await run(db,capability,other,'other_policy',owner='other')
    assert await db.raw[guard.BUDGET_GUARDS].count_documents({})==2
    assert (await db.raw[AUTOMATION_POLICIES].find_one({'user_id':'owner','account_id':'123'}))['max_account_daily_budget_native']=='55'
    assert (await db.raw[AUTOMATION_POLICIES].find_one({'user_id':'other','account_id':'123'}))['max_account_daily_budget_native']=='1'
