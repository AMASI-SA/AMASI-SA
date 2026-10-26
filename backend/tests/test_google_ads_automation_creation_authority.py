"""Creation reservations are durable authority, never implicit provider writes."""
import asyncio
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from integrations_control_center import google_ads_automation_creation_authority as authority
from integrations_control_center import google_ads_automation_creation_contract as contract
from integrations_control_center import google_ads_management as management
from integrations_control_center.google_ads_ai import PRODUCT_COLLECTION
from integrations_control_center.google_ads_automation_creation_policy import CREATION_POLICIES
from tests.test_google_ads_automation_creation_contract import NOW, prepared_case, valid_proposal
from tests.test_google_ads_automation_authority import case_documents


def matches(row, query):
    for key, value in query.items():
        if key == "$or":
            if not any(matches(row, part) for part in value): return False
            continue
        if key == "$and":
            if not all(matches(row, part) for part in value): return False
            continue
        actual = row.get(key)
        if isinstance(value, dict) and any(k.startswith("$") for k in value):
            for op, expected in value.items():
                if op == "$exists" and (key in row) != expected: return False
                if op == "$eq" and actual != expected: return False
                if op == "$gt" and (actual is None or actual <= expected): return False
                if op == "$in" and actual not in expected: return False
        elif actual != value: return False
    return True


class Cursor:
    def __init__(self, rows): self.rows = deepcopy(rows)
    def sort(self, *args): return self
    async def to_list(self, length=None): return self.rows[:length]


class Collection:
    def __init__(self, rows=()): self.rows=deepcopy(list(rows)); self.before_update=None; self.updates=[]; self.after_read=None
    async def find_one(self, query, projection=None):
        result = next((deepcopy(row) for row in self.rows if matches(row, query)), None)
        if result is not None and projection and any(value == 1 for value in projection.values()):
            result={key:value for key,value in result.items() if projection.get(key)==1}
        if self.after_read: self.after_read()
        return result
    def find(self, query, projection=None):
        rows=[deepcopy(row) for row in self.rows if matches(row,query)]
        if projection and any(value==1 for value in projection.values()):
            rows=[{key:value for key,value in row.items() if projection.get(key)==1} for row in rows]
        return Cursor(rows)
    async def create_index(self,*args,**kwargs): return 'index'
    async def update_one(self, query, update):
        if self.before_update: self.before_update(self)
        self.updates.append((deepcopy(query),deepcopy(update)))
        row=next((row for row in self.rows if matches(row,query)),None)
        if row is None:return SimpleNamespace(matched_count=0)
        row.update(deepcopy(update.get('$set',{})))
        for key,value in update.get('$inc',{}).items(): row[key]=row.get(key,0)+value
        return SimpleNamespace(matched_count=1)


class DB:
    def __init__(self, docs): self.collections={key:Collection(rows) for key,rows in docs.items()}
    def __getitem__(self,key): return self.collections.setdefault(key,Collection())
    def __getattr__(self,key): return self[key]


def documents():
    values=prepared_case(); intent=contract.prepare_google_creation_intent(**values)
    docs,_=case_documents(current=NOW)
    policy={**values['policy'],'template':values['template'],'updated_by':'owner'}
    row=valid_proposal(intent)
    row.update(status='approved',verified=False,submission_started=False,created_campaign_id=None,resource_names=[],
        execution_claim_id='claim123',auth_epoch='epoch',currency='SAR',time_zone='Asia/Riyadh',
        campaign_id=None,before_digest=None,draft_id=intent['creation_identity'],draft_revision=1,
        idempotency_key=intent['creation_identity'],created_at=NOW.isoformat(),expires_at=(NOW+timedelta(minutes=30)).isoformat())
    row['confirmation_digest']=management.digest(management.immutable(row))
    docs.update({CREATION_POLICIES:[policy],PRODUCT_COLLECTION:[values['product']],
        authority.CREATION_INTENTS:[{'user_id':'owner','account_id':'123','creation_identity':intent['creation_identity'],
            'intent':intent,'choice':values['choice'],'created_at':NOW.isoformat()}],
        management.PROPOSALS:[row],management.DRAFTS:[{'user_id':'owner','draft_id':intent['creation_identity'],
            'revision':1,'draft':intent['draft']}],management.FENCES:[{'user_id':'owner','account_id':'123','proposal_id':'proposal123',
                'execution_claim_id':'claim123','status':'claimed'}]})
    native={'metadata':{'account_id':'123','identity_verified':True,'currency_code':'SAR','time_zone':'Asia/Riyadh'},
        'auth_epoch':'epoch','observed_at':NOW.isoformat(),'native_context':values['native_context']}
    return docs,native,intent


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    for key in ('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','GOOGLE_ADS_AUTOMATION_WORKER_ENABLED','GOOGLE_ADS_AUTOMATION_CREATION_ENABLED'):
        monkeypatch.setenv(key,'true')
    monkeypatch.setattr(authority,'now',lambda current=None: current or NOW)


async def authorize(db,native):
    return await authority.authorize_google_creation_claim(db,user_id='owner',account_id='123',proposal_id='proposal123',
        execution_claim_id='claim123',native_evidence=native,current=NOW)


async def settle(db):
    return await authority.settle_google_creation_claim(db,user_id='owner',account_id='123',claim_id='claim123',current=NOW)


@pytest.mark.asyncio
async def test_claim_is_atomic_paused_reservation_and_not_enable_funding():
    docs,native,intent=documents(); db=DB(docs)
    # Existing active commitments may fully consume55; paused creation spends0.
    native['native_context']['campaigns'][0]['budget_native']='55'
    reply=await authorize(db,native)
    assert reply['authorized'] and reply['submission_floor_required'] and reply['provider_writes']==0
    policy=db[CREATION_POLICIES].rows[0]; entry=policy['_creation_ledger'][intent['creation_identity']]
    assert policy['_creation_claim']['claim_id']=='claim123' and policy['_creation_generation']==1
    assert entry['reservation_native']=='15' and entry['reservation_state']=='paused_reserved'
    assert entry['reserved_enable_budget_native']=='0' and entry['reservation_currency_code']=='SAR'
    state=await authority.read_google_creation_state(db,user_id='owner',account_id='123',native_context=native['native_context'],current=NOW)
    assert state['total_created']==state['created_today']==state['paused_created']==1
    assert state['reserved_creation_budget_native']=='15' and state['reserved_enable_budget_native']=='0'
    assert state['pending_write'] and db[management.PROPOSALS].rows[0]['submission_started'] is False
    assert db[management.FENCES].rows[0]['status']=='claimed'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['disabled','revision','template','product','alias','owner','account','auth','native_age','proposal_expired','draft','operations','manual','floor'])
async def test_changed_or_unproved_authority_never_creates_claim(change):
    docs,native,intent=documents(); db=DB(docs); policy=db[CREATION_POLICIES].rows[0];row=db[management.PROPOSALS].rows[0]
    if change=='disabled':policy['enabled']=False
    elif change=='revision':policy['revision']+=1
    elif change=='template':policy['template']['headlines'][0]='Changed'
    elif change=='product':db[PRODUCT_COLLECTION].rows[0]['source_revision']='b'*64
    elif change=='alias':db[PRODUCT_COLLECTION].rows.append({**deepcopy(db[PRODUCT_COLLECTION].rows[0]),'salla_product_id':'2','mezan_product_id':'1'})
    elif change=='owner':db.users.rows[0]['role']='reviewer'
    elif change=='account':db.mezan_integrations_v2.rows=[]
    elif change=='auth':native['auth_epoch']='other'
    elif change=='native_age':native['observed_at']=(NOW-timedelta(seconds=91)).isoformat()
    elif change=='proposal_expired':row['expires_at']=NOW.isoformat();row['confirmation_digest']=management.digest(management.immutable(row))
    elif change=='draft':db[management.DRAFTS].rows[0]['draft']['name']='Altered'
    elif change=='operations':row['operations'].append({'campaignOperation':{'update':{'status':'ENABLED'}}});row['confirmation_digest']=management.digest(management.immutable(row))
    elif change=='manual':row['actor_id']='owner';row['confirmation_digest']=management.digest(management.immutable(row))
    elif change=='floor':row['submission_started']=True
    with pytest.raises(HTTPException) as caught: await authorize(db,native)
    assert caught.value.detail['preserve_execution_fence'] is True
    assert caught.value.detail['automatic_retry_allowed'] is False
    assert '_creation_claim' not in policy and not db[CREATION_POLICIES].updates


@pytest.mark.asyncio
async def test_revoke_winning_same_document_cas_blocks_claim():
    docs,native,_=documents();db=DB(docs)
    def revoke(collection):collection.rows[0].update(enabled=False,revision=3)
    db[CREATION_POLICIES].before_update=revoke
    with pytest.raises(HTTPException):await authorize(db,native)
    assert '_creation_claim' not in db[CREATION_POLICIES].rows[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[ConnectionError,asyncio.CancelledError])
async def test_postcommit_lost_ack_never_grants_or_refunds(monkeypatch,failure):
    docs,native,intent=documents();db=DB(docs); original=authority._linearize_creation_claim
    async def lost(*args):await original(*args);raise failure()
    monkeypatch.setattr(authority,'_linearize_creation_claim',lost)
    with pytest.raises((HTTPException,asyncio.CancelledError)) as caught:await authorize(db,native)
    exc=caught.value
    assert (exc.detail if isinstance(exc,HTTPException) else vars(exc))['preserve_execution_fence'] is True
    policy=db[CREATION_POLICIES].rows[0]
    assert policy['_creation_claim'] and policy['_creation_ledger'][intent['creation_identity']]['state']=='reserved'
    with pytest.raises(HTTPException):await settle(db)
    assert db[management.FENCES].rows[0]['status']=='claimed'


def completed(db,intent,*,receipt=False):
    row=db[management.PROPOSALS].rows[0]; final=valid_proposal(intent)
    row.update({key:final[key] for key in ('status','verified','submission_started','created_campaign_id','resource_names')})
    row['verification']={'readback_independent':True,'verified_resources':deepcopy(row['resource_names']),'mismatches':[],'coverage_gaps':[]}
    if receipt:row.update(accepted_receipt=True,accepted_operations_digest=management.digest(row['operations']))
    return row


@pytest.mark.asyncio
@pytest.mark.parametrize('receipt',[False,True])
async def test_original_independent_receipt_or_unique_correlation_owns_without_resend(receipt):
    docs,native,intent=documents();db=DB(docs);await authorize(db,native)
    policy=db[CREATION_POLICIES].rows[0];policy.update(enabled=False,revision=3)
    completed(db,intent,receipt=receipt)
    reply=await settle(db)
    assert reply['settled'] and reply['released'] and reply['verified_ownership'] and reply['quota_refunded'] is False
    entry=policy['_creation_ledger'][intent['creation_identity']]
    assert contract.creation_campaign_ownership_verified(entry,intent=intent,user_id='owner',account_id='123',campaign_id='999',proposal=db[management.PROPOSALS].rows[0])
    assert policy['_creation_claim'] is None and entry['reservation_state']=='paused_created'
    assert db[management.FENCES].rows[0]['status']=='claimed'
    assert (await settle(db))['settled']


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['manual','ambiguous','incomplete','unknown','wrong_digest'])
async def test_unproved_or_manual_results_stay_reserved_and_unowned(change):
    docs,native,intent=documents();db=DB(docs);await authorize(db,native);row=completed(db,intent)
    if change=='manual':row['actor_id']='owner'
    elif change=='ambiguous':row['verification']['mismatches']=['non_unique_label']
    elif change=='incomplete':row['verification']['verified_resources'].pop()
    elif change=='unknown':row.update(status='uncertain',verified=False)
    elif change=='wrong_digest':row.update(accepted_receipt=True,accepted_operations_digest='bad')
    with pytest.raises(HTTPException):await settle(db)
    entry=db[CREATION_POLICIES].rows[0]['_creation_ledger'][intent['creation_identity']]
    assert entry['state']=='reserved' and not entry.get('verified')


def test_worker_flag_uses_existing_authoritative_lexicon(monkeypatch):
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_WORKER_ENABLED','enabled')
    assert authority.creation_execution_enabled()


@pytest.mark.asyncio
async def test_foreign_or_stale_native_status_cannot_release_owned_paused_slot():
    docs,native,intent=documents();db=DB(docs);await authorize(db,native);completed(db,intent);await settle(db)
    # Top-level proof is fresh, but this campaign row belongs to another account.
    native['native_context']['campaigns'].append({'campaign_id':'999','status':'ENABLED','account_id':'other'})
    state=await authority.read_google_creation_state(db,user_id='owner',account_id='123',native_context=native['native_context'],current=NOW)
    assert state['paused_created']==1 and state['reserved_creation_budget_native']=='15'


@pytest.mark.asyncio
async def test_final_owner_recheck_follows_last_fence_read():
    docs,native,_=documents();db=DB(docs);reads=0
    def revoke():
        nonlocal reads
        reads+=1
        if reads>=2:db.users.rows[0]['role']='reviewer'
    db[management.FENCES].after_read=revoke
    with pytest.raises(HTTPException):await authorize(db,native)
    assert not db[CREATION_POLICIES].updates


@pytest.mark.asyncio
@pytest.mark.parametrize('age',['native','proposal','catalog'])
async def test_final_cas_time_revalidates_earliest_proof_and_expiry(monkeypatch,age):
    docs,native,_=documents();db=DB(docs);elapsed=91 if age=='native' else 61
    if age=='proposal':
        row=db[management.PROPOSALS].rows[0];row['expires_at']=(NOW+timedelta(seconds=60)).isoformat()
        row['confirmation_digest']=management.digest(management.immutable(row))
    if age=='catalog':db[CREATION_POLICIES].rows[0]['catalog_max_age_seconds']=60
    # For catalog, make its policy binding correspond to the configured lifetime.
    if age=='catalog':
        values=prepared_case();values['policy']['catalog_max_age_seconds']=60
        intent=contract.prepare_google_creation_intent(**values)
        saved=db[authority.CREATION_INTENTS].rows[0];saved['intent']=intent
        row=db[management.PROPOSALS].rows[0];row['automation_source']=authority.creation_source(intent)
        row['confirmation_digest']=management.digest(management.immutable(row))
    def advance():monkeypatch.setattr(authority,'now',lambda current=None:current or NOW+timedelta(seconds=elapsed))
    db[management.FENCES].after_read=advance
    with pytest.raises(HTTPException):await authorize(db,native)
    assert not db[CREATION_POLICIES].updates


@pytest.mark.asyncio
async def test_default_off_with_existing_claim_never_allows_cleanup(monkeypatch):
    docs,native,_=documents();db=DB(docs);await authorize(db,native)
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_CREATION_ENABLED','false')
    with pytest.raises(HTTPException) as caught:await authorize(db,native)
    assert caught.value.detail['preserve_execution_fence'] is True
    assert db[CREATION_POLICIES].rows[0]['_creation_claim']


def stop_row(row,*,stamp=NOW):
    row.update(status='failed',submission_started=True,submitted_at=NOW.isoformat(),provider_write_reached=False,
        safe_error='automation_stopped_before_provider_dispatch',dispatch_cancelled_before_http=True,
        dispatch_cancelled_before_http_claim_id='claim123',dispatch_cancelled_before_http_at=stamp.isoformat())


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['valid','before_claim','wrong_claim','uncertain','unknown_floor'])
async def test_only_exact_known_no_http_marker_can_settle_without_resetting_floor(case):
    docs,native,intent=documents();db=DB(docs);await authorize(db,native);row=db[management.PROPOSALS].rows[0];stop_row(row)
    if case=='before_claim':
        row['submitted_at']=(NOW-timedelta(seconds=1)).isoformat();row['dispatch_cancelled_before_http_at']=row['submitted_at']
    elif case=='wrong_claim':row['dispatch_cancelled_before_http_claim_id']='wrong'
    elif case=='uncertain':row['status']='uncertain'
    elif case=='unknown_floor':row['dispatch_cancelled_before_http']=False
    if case=='valid':
        assert (await settle(db))['verified_ownership'] is False
        policy=db[CREATION_POLICIES].rows[0]
        assert policy['_creation_claim'] is None and len(policy['_creation_ledger'])==1
        assert row['submission_started'] is True
    else:
        with pytest.raises(HTTPException):await settle(db)
        assert db[CREATION_POLICIES].rows[0]['_creation_claim']


@pytest.mark.asyncio
async def test_quota_survives_cancellation_revision_and_local_midnight():
    docs,native,intent=documents();db=DB(docs);await authorize(db,native)
    row=db[management.PROPOSALS].rows[0];row.update(status='failed',safe_error='execution_cancelled_before_dispatch')
    await settle(db)
    policy=db[CREATION_POLICIES].rows[0];policy.update(revision=3,enabled=False,owner_generation=2)
    before=await authority.read_google_creation_state(db,user_id='owner',account_id='123',native_context=native['native_context'],current=NOW)
    assert before['created_today']==before['total_created']==1 and before['paused_created']==0
    # Account-local day, not UTC date, controls rollover. Reserved identity remains consumed.
    later=NOW.replace(hour=21,minute=0)
    native['native_context']['observed_at']=later.isoformat()
    after=await authority.read_google_creation_state(db,user_id='owner',account_id='123',native_context=native['native_context'],current=later)
    assert later.date()==NOW.date() and after['created_today']==0 and after['total_created']==1
