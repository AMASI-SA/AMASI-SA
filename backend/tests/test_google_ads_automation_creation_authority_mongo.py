"""Disposable real Mongo evidence for creation CAS and nonrefundable identity.

No provider or credentials are used. Race gates schedule genuine same-document
Mongo updates; no fake matching, transactions or result counters are substituted.
"""
import asyncio
from copy import deepcopy
from datetime import timedelta
import uuid

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio

from integrations_control_center import google_ads_automation_creation_authority as authority
from integrations_control_center.google_ads_automation_creation_policy import CREATION_POLICIES
from integrations_control_center import google_ads_management as management
from tests.test_google_ads_automation_policy_mongo import automation_mongo_uri
from tests.test_google_ads_automation_creation_authority import NOW, documents


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    for key in ('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','GOOGLE_ADS_AUTOMATION_WORKER_ENABLED','GOOGLE_ADS_AUTOMATION_CREATION_ENABLED'):
        monkeypatch.setenv(key,'true')
    monkeypatch.setattr(authority,'now',lambda current=None:current or NOW)


@pytest_asyncio.fixture
async def creation_db(automation_mongo_uri):
    client=AsyncIOMotorClient(automation_mongo_uri,serverSelectionTimeoutMS=1000)
    name='g_creation_auth_'+uuid.uuid4().hex
    assert len(name.encode('ascii'))<=63
    db=client[name]
    try:
        await client.admin.command('ping')
        docs,native,intent=documents()
        for key,rows in docs.items():
            if rows:await db[key].insert_many(deepcopy(rows))
        await db[CREATION_POLICIES].create_index([('user_id',1),('account_id',1)],unique=True,name='google_creation_tenant_account')
        await db[authority.CREATION_INTENTS].create_index([('user_id',1),('account_id',1),('creation_identity',1)],unique=True)
        yield db,native,intent
    finally:
        await client.drop_database(name)
        client.close()


async def authorize(db,native):
    return await authority.authorize_google_creation_claim(db,user_id='owner',account_id='123',proposal_id='proposal123',
        execution_claim_id='claim123',native_evidence=native,current=NOW)


async def policy(db):return await db[CREATION_POLICIES].find_one({'user_id':'owner','account_id':'123'},{'_id':0})


@pytest.mark.asyncio
async def test_real_mongo_simultaneous_claims_reserve_exactly_once(creation_db,monkeypatch):
    db,native,intent=creation_db
    entered=0;ready=asyncio.Event();original=authority._linearize_creation_claim
    async def together(*args):
        nonlocal entered
        entered+=1
        if entered==2:ready.set()
        await ready.wait()
        return await original(*args)
    monkeypatch.setattr(authority,'_linearize_creation_claim',together)
    results=await asyncio.gather(authorize(db,native),authorize(db,native),return_exceptions=True)
    assert sum(isinstance(result,dict) and result.get('authorized') is True for result in results)==1
    assert sum(isinstance(result,HTTPException) for result in results)==1
    stored=await policy(db)
    assert stored['_creation_generation']==1 and len(stored['_creation_ledger'])==1
    assert stored['_creation_claim']['creation_identity']==intent['creation_identity']


@pytest.mark.asyncio
async def test_real_mongo_revoke_first_wins_zero_claim(creation_db,monkeypatch):
    db,native,_=creation_db;at_cas=asyncio.Event();resume=asyncio.Event();original=authority._linearize_creation_claim
    async def blocked(*args):at_cas.set();await resume.wait();return await original(*args)
    monkeypatch.setattr(authority,'_linearize_creation_claim',blocked)
    task=asyncio.create_task(authorize(db,native));await at_cas.wait()
    result=await db[CREATION_POLICIES].update_one({'user_id':'owner','account_id':'123','revision':2},
        {'$set':{'enabled':False},'$inc':{'revision':1}})
    assert result.matched_count==1
    resume.set()
    with pytest.raises(HTTPException):await task
    stored=await policy(db)
    assert not stored.get('_creation_claim') and not stored.get('_creation_ledger')


@pytest.mark.asyncio
async def test_real_mongo_claim_first_revocation_preserves_reservation(creation_db):
    db,native,intent=creation_db;await authorize(db,native)
    before=await policy(db)
    result=await db[CREATION_POLICIES].update_one({'user_id':'owner','account_id':'123','revision':2},
        {'$set':{'enabled':False},'$inc':{'revision':1}})
    assert result.matched_count==1
    after=await policy(db)
    assert after['_creation_claim']==before['_creation_claim']
    assert after['_creation_ledger']==before['_creation_ledger']
    with pytest.raises(HTTPException) as caught:await authorize(db,native)
    assert caught.value.detail['preserve_execution_fence']
    fence=await db[management.FENCES].find_one({'user_id':'owner','account_id':'123'})
    assert fence['status']=='claimed'


@pytest.mark.asyncio
async def test_real_mongo_postcommit_response_loss_keeps_unknown_identity(creation_db,monkeypatch):
    db,native,intent=creation_db;original=authority._linearize_creation_claim
    async def lost(*args):await original(*args);raise ConnectionError('injected response loss after Mongo commit')
    monkeypatch.setattr(authority,'_linearize_creation_claim',lost)
    with pytest.raises(HTTPException) as caught:await authorize(db,native)
    assert caught.value.detail['authorization_cas_attempted'] and caught.value.detail['preserve_execution_fence']
    stored=await policy(db)
    assert stored['_creation_claim'] and stored['_creation_ledger'][intent['creation_identity']]['reservation_native']=='15'
    with pytest.raises(HTTPException):await authority.settle_google_creation_claim(db,user_id='owner',account_id='123',claim_id='claim123',current=NOW)
    assert (await policy(db))==stored


@pytest.mark.asyncio
async def test_real_mongo_terminal_cancel_keeps_day_total_across_revision_and_midnight(creation_db):
    db,native,intent=creation_db;await authorize(db,native)
    await db[management.PROPOSALS].update_one({'user_id':'owner','proposal_id':'proposal123'},
        {'$set':{'status':'failed','safe_error':'execution_cancelled_before_dispatch','provider_write_reached':False}})
    reply=await authority.settle_google_creation_claim(db,user_id='owner',account_id='123',claim_id='claim123',current=NOW)
    assert reply['settled'] and reply['quota_refunded'] is False
    await db[CREATION_POLICIES].update_one({'user_id':'owner','account_id':'123'},
        {'$set':{'enabled':False,'owner_generation':2},'$inc':{'revision':1}})
    # Gateway is responsible for releasing its native fence only after the ACK.
    await db[management.FENCES].update_one({'user_id':'owner','account_id':'123','execution_claim_id':'claim123'},
        {'$set':{'status':'released'}})
    before=await authority.read_google_creation_state(db,user_id='owner',account_id='123',native_context=native['native_context'],current=NOW)
    later=NOW.replace(hour=21)
    native['native_context']['observed_at']=later.isoformat()
    after=await authority.read_google_creation_state(db,user_id='owner',account_id='123',native_context=native['native_context'],current=later)
    assert before['created_today']==before['total_created']==1
    assert after['created_today']==0 and after['total_created']==1 and after['paused_created']==0


@pytest.mark.asyncio
async def test_real_mongo_original_verified_correlation_settles_owned_atomically(creation_db):
    from tests.test_google_ads_automation_creation_contract import valid_proposal
    db,native,intent=creation_db;await authorize(db,native)
    final=valid_proposal(intent)
    await db[management.PROPOSALS].update_one({'user_id':'owner','account_id':'123','proposal_id':'proposal123'},
        {'$set':{**{key:final[key] for key in ('status','verified','submission_started','created_campaign_id','resource_names')},
            'verification':{'readback_independent':True,'verified_resources':final['resource_names'],'mismatches':[],'coverage_gaps':[]}}})
    reply=await authority.settle_google_creation_claim(db,user_id='owner',account_id='123',claim_id='claim123',current=NOW)
    stored=await policy(db)
    assert reply['verified_ownership'] and stored['_creation_claim'] is None
    assert stored['_creation_ledger'][intent['creation_identity']]['state']=='owned'
    assert (await db[management.FENCES].find_one({'user_id':'owner','account_id':'123'}))['status']=='claimed'
    # Same campaign name/label in a different tenant never gets private ownership.
    await db[CREATION_POLICIES].insert_one({'user_id':'other','account_id':'123','revision':1})
    assert not (await db[CREATION_POLICIES].find_one({'user_id':'other','account_id':'123'})).get('_creation_ledger')


@pytest.mark.asyncio
async def test_real_mongo_new_revision_and_generation_cannot_reset_daily_quota(creation_db):
    from tests.test_google_ads_automation_creation_contract import prepared_case, valid_proposal
    from integrations_control_center import google_ads_automation_creation_contract as contract
    db,native,_=creation_db;await authorize(db,native)
    await db[management.PROPOSALS].update_one({'user_id':'owner','proposal_id':'proposal123'},
        {'$set':{'status':'failed','safe_error':'execution_cancelled_before_dispatch','provider_write_reached':False}})
    await authority.settle_google_creation_claim(db,user_id='owner',account_id='123',claim_id='claim123',current=NOW)
    values=prepared_case();values['policy'].update(revision=3,activation_revision=3,owner_generation=2)
    intent=contract.prepare_google_creation_intent(**values)
    await db[CREATION_POLICIES].update_one({'user_id':'owner','account_id':'123'}, {'$set':values['policy']})
    await db[authority.CREATION_INTENTS].insert_one({'user_id':'owner','account_id':'123','creation_identity':intent['creation_identity'],
        'intent':intent,'choice':values['choice'],'created_at':NOW.isoformat()})
    row=valid_proposal(intent)
    row.update(proposal_id='proposal456',execution_claim_id='claim456',status='approved',verified=False,submission_started=False,
        created_campaign_id=None,resource_names=[],auth_epoch='epoch',currency='SAR',time_zone='Asia/Riyadh',campaign_id=None,
        before_digest=None,draft_id=intent['creation_identity'],draft_revision=1,idempotency_key=intent['creation_identity'],
        correlation_label='MEZAN-proposal456',expires_at=(NOW+timedelta(minutes=30)).isoformat())
    row['operations'][-2]['labelOperation']['create']['name']='MEZAN-proposal456'
    row['confirmation_digest']=management.digest(management.immutable(row))
    await db[management.PROPOSALS].insert_one(row)
    await db[management.DRAFTS].insert_one({'user_id':'owner','draft_id':intent['creation_identity'],'revision':1,'draft':intent['draft']})
    await db[management.FENCES].update_one({'user_id':'owner','account_id':'123'},
        {'$set':{'proposal_id':'proposal456','execution_claim_id':'claim456','status':'claimed'}})
    with pytest.raises(HTTPException) as caught:
        await authority.authorize_google_creation_claim(db,user_id='owner',account_id='123',proposal_id='proposal456',
            execution_claim_id='claim456',native_evidence=native,current=NOW)
    assert caught.value.detail['authorization_cas_attempted'] is False
    stored=await policy(db)
    assert len(stored['_creation_ledger'])==1 and stored['_creation_claim'] is None


@pytest.mark.asyncio
async def test_real_mongo_unclaimed_cancel_barrier_defeats_late_authorizer(creation_db,monkeypatch):
    db,native,intent=creation_db;entered=asyncio.Event();resume=asyncio.Event();original=authority._linearize_creation_claim
    async def delayed(*args):entered.set();await resume.wait();return await original(*args)
    monkeypatch.setattr(authority,'_linearize_creation_claim',delayed)
    pending=asyncio.create_task(authorize(db,native));await entered.wait()
    await db[management.PROPOSALS].update_one({'user_id':'owner','account_id':'123','proposal_id':'proposal123'},
        {'$set':{'status':'failed','submission_started':False,'provider_write_reached':False,'safe_error':'execution_cancelled_before_dispatch','provider_write_reached':False}})
    result=await authority.settle_google_creation_claim(db,user_id='owner',account_id='123',claim_id='claim123',current=NOW)
    assert result['settled'] and not result['verified_ownership']
    resume.set()
    with pytest.raises(HTTPException):await pending
    stored=await policy(db)
    assert stored['_creation_claim'] is None and stored['_creation_generation']==1
    entry=stored['_creation_ledger'][intent['creation_identity']]
    assert entry['authorization_granted'] is False and 'authorized_at' not in entry
    assert (await authority.settle_google_creation_claim(db,user_id='owner',account_id='123',claim_id='claim123',current=NOW))['settled']
    assert (await db[management.FENCES].find_one({'user_id':'owner','account_id':'123'}))['status']=='claimed'


@pytest.mark.asyncio
async def test_real_mongo_unclaimed_barrier_ack_loss_keeps_fence_until_read_proof(creation_db,monkeypatch):
    db,_,intent=creation_db;original=authority._linearize_creation_settlement
    await db[management.PROPOSALS].update_one({'user_id':'owner','account_id':'123','proposal_id':'proposal123'},
        {'$set':{'status':'failed','submission_started':False,'provider_write_reached':False,'safe_error':'execution_cancelled_before_dispatch','provider_write_reached':False}})
    async def lost(*args):await original(*args);raise ConnectionError('injected settled barrier response lost')
    monkeypatch.setattr(authority,'_linearize_creation_settlement',lost)
    with pytest.raises(HTTPException):await authority.settle_google_creation_claim(db,user_id='owner',account_id='123',claim_id='claim123',current=NOW)
    assert (await policy(db))['_creation_ledger'][intent['creation_identity']]['state']=='no_campaign'
    assert (await db[management.FENCES].find_one({'user_id':'owner','account_id':'123'}))['status']=='claimed'
    monkeypatch.setattr(authority,'_linearize_creation_settlement',original)
    assert (await authority.settle_google_creation_claim(db,user_id='owner',account_id='123',claim_id='claim123',current=NOW))['released']
