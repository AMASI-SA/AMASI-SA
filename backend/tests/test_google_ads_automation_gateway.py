"""Automation uses explicit standing authority, not a forged human approval."""
import pytest
from fastapi import HTTPException
from integrations_control_center import google_ads_management as management
from integrations_control_center import google_ads_automation_authority as authority
from tests.test_google_ads_automation_authority import DB, case_documents, NOW

@pytest.mark.asyncio
async def test_human_gateway_cannot_execute_automation_owned_proposal(monkeypatch):
    docs, _ = case_documents(); row = docs[management.PROPOSALS][0]
    row.update(status='previewed'); row['confirmation_digest'] = authority.automation_confirmation_digest(row)
    monkeypatch.setattr(management, 'now', lambda: NOW)
    with pytest.raises(HTTPException) as error:
        await management.execute_google_proposal(DB(docs), {'user_id':'owner','actor_id':'owner','owner':True}, row['proposal_id'], row['confirmation_digest'])
    assert error.value.detail['code'] == 'google_automation_gateway_required'


def test_manual_digest_is_unchanged_but_automation_binding_is_frozen():
    row={'proposal_id':'p','user_id':'owner','actor_id':'owner','action':'pause'}
    before=management.digest(management.immutable(row))
    expected={k:row.get(k) for k in ('proposal_id','provider','user_id','actor_id','action','account_id','currency','time_zone','auth_epoch','before_digest','draft_id','draft_revision','operations','reason','planned','financial_bound','expires_at','correlation_label')}
    assert before == management.digest(expected)
    row['automation_source']={'kind':'standing_policy','policy_revision':1}
    assert management.digest(management.immutable(row)) != before

from copy import deepcopy
from types import SimpleNamespace
import asyncio
from integrations_control_center import google_ads_automation_gateway as gateway
from tests.test_google_ads_automation_authority import Collection, matches
from pymongo.errors import DuplicateKeyError

class GatewayCollection(Collection):
    async def insert_one(self,row):
        if self is getattr(self,'fence_collection',None) and self.rows:
            raise DuplicateKeyError('test-only duplicate fence')
        self.rows.append(deepcopy(row));return SimpleNamespace(inserted_id='local')
    async def update_one(self,query,update):
        result=await super().update_one(query,update)
        if result.matched_count:
            row=next(r for r in self.rows if matches(r,query) or r.get('proposal_id')==query.get('proposal_id')) if '$push' in update else None
            if row is not None:
                for key,append in update.get('$push',{}).items():
                    row.setdefault(key,[]).extend(deepcopy(append.get('$each',[append])))
        return result

class GatewayDB(DB):
    def __init__(self,docs):
        self._tx_setup({k:GatewayCollection(v) for k,v in docs.items()});self.collections=self._tx_collections;self.users=self['users']
    def __getitem__(self,key):
        return self._tx_get(key)

class NativeProvider:
    def __init__(self,native):
        self.native=deepcopy(native);self.previews=0;self.writes=0;self.verified=True;self.response_lost=False
    async def account_metadata(self):return {**self.native['metadata'],'name':'حساب اختبار'}
    async def campaign_snapshot(self,*args,**kwargs):return deepcopy(self.native['campaign_snapshot'])
    async def field_metadata(self,fields):return {f:{'selectable':True} for f in fields}
    async def search(self,query):
        assert 'segments.date' not in query and 'metrics.' not in query
        snap=self.native['campaign_snapshot'];return [{'campaign':deepcopy(snap['campaign']),'campaignBudget':deepcopy(snap['campaign_budget'])}],True
    async def close(self):pass
    async def mutate(self,operations,*,validate_only=False):
        if validate_only:self.previews+=1;return {}
        self.writes+=1
        for operation in operations:
            spec=next(iter(operation.values()));update=spec.get('update',{})
            if 'status' in update:self.native['campaign_snapshot']['campaign']['status']=update['status']
        if self.response_lost:raise TimeoutError('test-only lost response')
        return {'mutateOperationResponses':[{'campaignResult':{'resourceName':'customers/123/campaigns/456'}}]}
    async def verify_operations(self,*args,**kwargs):return self.verified,{'verified':self.verified}

@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['other_account', 'different_resource', 'missing_shared', 'missing_reference_count'])
async def test_rest_inventory_budget_identity_never_authorizes_mismatched_evidence(monkeypatch, change):
    docs, evidence = case_documents('CHANGE_BUDGET')
    provider = NativeProvider(evidence)
    raw = deepcopy(evidence['campaign_snapshot']['campaign_budget'])
    if change == 'other_account': raw['resourceName'] = 'customers/777/campaignBudgets/999'
    elif change == 'different_resource': raw['resourceName'] = 'customers/123/campaignBudgets/1000'
    elif change == 'missing_shared': raw.pop('explicitlyShared')
    else: raw.pop('referenceCount')
    async def rest_rows(*args, **kwargs):
        return [{'campaign': deepcopy(evidence['campaign_snapshot']['campaign']), 'campaignBudget': raw}], True, None
    monkeypatch.setattr(gateway, 'compatible_read', rest_rows)
    monkeypatch.setattr(management, 'now', lambda: NOW)
    evidence['inventory'] = await gateway._inventory(provider, evidence['metadata'])
    with pytest.raises(HTTPException) as error:
        authority._native_proof(docs[management.PROPOSALS][0], evidence,
            docs[gateway.AUTOMATION_POLICIES][0], NOW)
    assert error.value.detail['code'] == 'google_automation_native_evidence_invalid'
    assert provider.writes == 0

@pytest.fixture
def integration(monkeypatch):
    docs,native=case_documents();docs[management.PROPOSALS]=[];docs[management.FENCES]=[]
    db=GatewayDB(docs);provider=NativeProvider(native)
    from integrations_control_center import google_ads_automation_budget_guard as guard
    from datetime import datetime
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(guard,'datetime',Clock)
    monkeypatch.setattr(guard,'budget_guard_runtime_available',lambda:True)
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','true')
    monkeypatch.setattr(management,'now',lambda:NOW)
    monkeypatch.setattr(authority,'_now',lambda current=None:NOW)
    async def client(db_arg,user_id,account_id,**kwargs):
        assert db_arg is db and user_id=='owner' and account_id=='123'
        return provider,'test-epoch'
    monkeypatch.setattr(management,'account_client',client)
    return db,provider

async def preview(db):
    return await gateway.preview_google_automation_recommendation(db,user_id='owner',account_id='123',policy_revision=3,analysis_id='analysis1',recommendation_id='rec1')

async def execute(db,row):
    return await gateway.execute_google_automation_proposal(db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])

@pytest.mark.asyncio
async def test_native_validate_then_policy_claim_submit_and_verified_completion_once(integration):
    db,provider=integration;row=await preview(db)
    assert provider.previews==1 and provider.writes==0
    assert row['actor_id']==authority.SERVICE_ACTOR_ID and row['automation_source']['kind']=='standing_policy'
    result=await execute(db,row)
    assert result['status']=='completed' and result['verified'] is True and provider.writes==1
    assert db[management.FENCES].rows[0]['status']=='released'
    assert db[gateway.AUTOMATION_POLICIES].rows[0]['_dispatch_claim'] is None
    again=await execute(db,row)
    assert again['status']=='completed' and provider.writes==1

@pytest.mark.asyncio
async def test_disabled_execution_never_reads_provider_or_submits(integration,monkeypatch):
    db,provider=integration;monkeypatch.delenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED')
    with pytest.raises(HTTPException):await preview(db)
    assert provider.previews==provider.writes==0

@pytest.mark.asyncio
async def test_policy_revoked_after_preview_cannot_send(integration):
    db,provider=integration;row=await preview(db);db[gateway.AUTOMATION_POLICIES].rows[0]['enabled']=False
    with pytest.raises(HTTPException):await execute(db,row)
    assert provider.writes==0 and not db[management.FENCES].rows

@pytest.mark.asyncio
async def test_forged_automation_binding_is_rejected_before_provider(integration):
    db,provider=integration;row=await preview(db);db[management.PROPOSALS].rows[0]['automation_source']['policy_revision']=2
    with pytest.raises(HTTPException):await execute(db,row)
    assert provider.writes==0

@pytest.mark.asyncio
async def test_unknown_provider_response_never_resubmits_and_recovery_only_reads(integration,monkeypatch):
    db,provider=integration;row=await preview(db);provider.response_lost=True
    result=await execute(db,row)
    assert result['status']=='uncertain' and result['submission_started'] is True and provider.writes==1
    with pytest.raises(HTTPException):await execute(db,row)
    monkeypatch.setattr(management,'now',lambda:NOW+__import__('datetime').timedelta(minutes=6))
    monkeypatch.delenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED')
    recovered=await gateway.reconcile_google_automation_proposal(db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])
    assert recovered['status']=='completed' and provider.writes==1

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['lost_ack','cancel','policy_revoke'])
async def test_authority_failure_retains_fence_without_native_submission(integration,monkeypatch,kind):
    db,provider=integration;row=await preview(db)
    original=authority.authorize_google_automation_claim
    async def fail(*args,**kwargs):
        if kind=='policy_revoke':
            db[gateway.AUTOMATION_POLICIES].rows[0]['enabled']=False
            raise HTTPException(409,{'code':'google_automation_policy_changed','preserve_execution_fence':True})
        await original(*args,**kwargs)
        if kind=='cancel':raise asyncio.CancelledError()
        raise TimeoutError('test-only acknowledgement lost')
    monkeypatch.setattr(authority,'authorize_google_automation_claim',fail)
    if kind=='cancel':
        with pytest.raises(asyncio.CancelledError):await execute(db,row)
    else:
        result=await execute(db,row);assert result['status']=='uncertain'
    stored=db[management.PROPOSALS].rows[0]
    assert stored['submission_started'] is False and provider.writes==0
    assert db[management.FENCES].rows[0]['status']=='claimed'
    monkeypatch.setattr(management,'now',lambda:NOW+__import__('datetime').timedelta(minutes=6))
    settled=await gateway.reconcile_google_automation_proposal(db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])
    assert settled['status']=='failed' and settled['safe_error']=='execution_cancelled_before_dispatch'
    assert db[management.FENCES].rows[0]['status']=='released' and provider.writes==0

@pytest.mark.asyncio
async def test_native_drift_before_authority_never_sends_and_leaves_no_authorization(integration):
    db,provider=integration;row=await preview(db)
    provider.native['campaign_snapshot']['campaign']['name']='changed externally'
    with pytest.raises(HTTPException) as error:await execute(db,row)
    assert error.value.detail['code']=='google_ads_provider_drift'
    assert provider.writes==0 and db[management.FENCES].rows[0]['status']=='released'
    assert '_dispatch_claim' not in db[gateway.AUTOMATION_POLICIES].rows[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['other_account', 'different_resource', 'missing_shared', 'missing_reference_count'])
async def test_execute_never_mutates_when_rest_inventory_budget_proof_changes(integration, change):
    db, provider = integration
    row = await preview(db)
    async def changed_rest_row(query):
        snap = provider.native['campaign_snapshot']
        budget = deepcopy(snap['campaign_budget'])
        if change == 'other_account': budget['resourceName'] = 'customers/777/campaignBudgets/999'
        elif change == 'different_resource': budget['resourceName'] = 'customers/123/campaignBudgets/1000'
        elif change == 'missing_shared': budget.pop('explicitlyShared')
        else: budget.pop('referenceCount')
        return [{'campaign': deepcopy(snap['campaign']), 'campaignBudget': budget}], True
    provider.search = changed_rest_row
    try:
        result = await execute(db, row)
        assert result['status'] != 'completed'
    except HTTPException:
        pass
    assert provider.writes == 0
    assert db[management.PROPOSALS].rows[0].get('submission_started') is not True

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['not_acknowledged','cancel_after_claim','kill_after_claim'])
async def test_submission_floor_failure_never_calls_google_and_holds_claim(integration,monkeypatch,kind):
    db,provider=integration;row=await preview(db)
    original_event=management.record_event
    async def fail_event(db_arg,proposal,status,**kwargs):
        if status=='submitted':
            if kind=='cancel_after_claim':raise asyncio.CancelledError()
            raise TimeoutError('test-only floor acknowledgement lost')
        return await original_event(db_arg,proposal,status,**kwargs)
    monkeypatch.setattr(management,'record_event',fail_event)
    if kind=='cancel_after_claim':
        with pytest.raises(asyncio.CancelledError):await execute(db,row)
    else:
        result=await execute(db,row);assert result['status']=='uncertain'
    assert provider.writes==0 and db[management.FENCES].rows[0]['status']=='claimed'
    assert db[gateway.AUTOMATION_POLICIES].rows[0]['_dispatch_claim']['proposal_id']==row['proposal_id']

@pytest.mark.asyncio
async def test_post_completion_crash_is_settled_by_read_only_terminal_recovery(integration,monkeypatch):
    db,provider=integration;row=await preview(db)
    original=gateway._settle_if_present
    async def interrupted(*args,**kwargs):raise TimeoutError('test-only finalization interruption')
    monkeypatch.setattr(gateway,'_settle_if_present',interrupted)
    with pytest.raises(TimeoutError):await execute(db,row)
    assert db[management.PROPOSALS].rows[0]['status']=='completed' and provider.writes==1
    assert db[gateway.AUTOMATION_POLICIES].rows[0]['_dispatch_claim']
    monkeypatch.setattr(gateway,'_settle_if_present',original)
    monkeypatch.delenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED')
    result=await gateway.reconcile_google_automation_proposal(db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])
    assert result['status']=='completed' and provider.writes==1
    assert db[gateway.AUTOMATION_POLICIES].rows[0]['_dispatch_claim'] is None

@pytest.mark.asyncio
async def test_automation_service_cannot_use_human_preview(integration):
    from integrations_control_center.google_ads_campaign_contract import GoogleCampaignActionInput
    db,provider=integration
    with pytest.raises(HTTPException) as error:
        await management.preview_google_action(db,gateway._actor('owner'),GoogleCampaignActionInput(action='pause',account_id='123',campaign_id='456',idempotency_key='test'))
    assert error.value.detail['code']=='google_automation_gateway_required' and provider.previews==0

@pytest.mark.asyncio
async def test_manual_proposal_cannot_enter_automation_execution(integration):
    db,provider=integration;row=await preview(db)
    stored=db[management.PROPOSALS].rows[0];stored.pop('automation_source');stored['actor_id']='owner';stored['confirmation_digest']=management.digest(management.immutable(stored))
    with pytest.raises(HTTPException) as error:await execute(db,row)
    assert error.value.detail['code']=='google_automation_proposal_binding_invalid' and provider.writes==0

@pytest.mark.asyncio
async def test_operator_kill_after_policy_claim_never_reaches_submission_floor(integration,monkeypatch):
    db,provider=integration;row=await preview(db)
    async def kill_after_ack():
        monkeypatch.delenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED')
    db.after_commit=kill_after_ack
    result=await execute(db,row)
    assert result['status']=='uncertain' and result['submission_started'] is False
    assert provider.writes==0 and db[management.FENCES].rows[0]['status']=='claimed'
    assert db[gateway.AUTOMATION_POLICIES].rows[0]['_dispatch_claim']

@pytest.mark.asyncio
async def test_floor_commit_with_lost_ack_never_dispatches_and_cannot_be_cancelled_as_unsubmitted(integration,monkeypatch):
    db,provider=integration;row=await preview(db);provider.verified=False
    original=management.record_event
    async def lose_ack(db_arg,proposal,status,**kwargs):
        result=await original(db_arg,proposal,status,**kwargs)
        if status=='submitted':raise TimeoutError('test-only postcommit floor response lost')
        return result
    monkeypatch.setattr(management,'record_event',lose_ack)
    result=await execute(db,row)
    assert result['submission_started'] is True and result['status']=='uncertain' and provider.writes==0
    monkeypatch.setattr(management,'now',lambda:NOW+__import__('datetime').timedelta(minutes=6))
    recovered=await gateway.reconcile_google_automation_proposal(db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])
    assert recovered['status']=='uncertain' and provider.writes==0
    assert db[management.FENCES].rows[0]['status']=='claimed'

@pytest.mark.asyncio
async def test_provider_rejection_settles_claim_without_retry(integration,monkeypatch):
    db,provider=integration;row=await preview(db)
    original=provider.mutate
    async def reject(operations,*,validate_only=False):
        if validate_only:return await original(operations,validate_only=True)
        raise HTTPException(400,{'code':'native_rejected','provider_status':400,'provider_codes':['INVALID_ARGUMENT']})
    monkeypatch.setattr(provider,'mutate',reject)
    result=await execute(db,row)
    assert result['status']=='failed' and result['provider_write_reached'] is False
    assert db[gateway.AUTOMATION_POLICIES].rows[0]['_dispatch_claim'] is None
    assert db[management.FENCES].rows[0]['status']=='released'
    with pytest.raises(HTTPException):await execute(db,row)

@pytest.mark.asyncio
async def test_non_authorizing_reply_never_crosses_native_submission_floor(integration,monkeypatch):
    db,provider=integration;row=await preview(db)
    async def not_authorized(*args,**kwargs):return {'authorized':False}
    monkeypatch.setattr(authority,'authorize_google_automation_claim',not_authorized)
    result=await execute(db,row)
    assert result['status']=='uncertain' and result['submission_started'] is False and provider.writes==0
    assert db[management.FENCES].rows[0]['status']=='claimed'

@pytest.mark.asyncio
async def test_expired_policy_blocks_before_provider_read_or_claim(integration):
    db,provider=integration;db[gateway.AUTOMATION_POLICIES].rows[0]['expires_at']=NOW.isoformat()
    with pytest.raises(HTTPException):await preview(db)
    assert provider.previews==provider.writes==0 and not db[management.FENCES].rows

@pytest.mark.asyncio
async def test_operator_kill_after_submission_ack_prevents_http_and_preserves_floor(integration,monkeypatch):
    db,provider=integration;row=await preview(db);original=management.record_event
    async def kill_after_floor(db_arg,proposal,status,**kwargs):
        result=await original(db_arg,proposal,status,**kwargs)
        if status=='submitted':monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','false')
        return result
    monkeypatch.setattr(management,'record_event',kill_after_floor)
    result=await execute(db,row)
    assert provider.writes==0
    assert result['status']=='failed' and result['submission_started'] is True
    assert result['safe_error']=='automation_stopped_before_provider_dispatch'
    stored=db[management.PROPOSALS].rows[0]
    assert stored['dispatch_cancelled_before_http'] is True
    assert stored['dispatch_cancelled_before_http_claim_id']==stored['execution_claim_id']
    assert db[management.FENCES].rows[0]['status']=='released'
    assert db[gateway.AUTOMATION_POLICIES].rows[0]['_dispatch_claim'] is None
    assert db[gateway.AUTOMATION_POLICIES].rows[0]['_dispatch_days'][0]['proposal_ids']==[row['proposal_id']]

@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['before_floor','during_authority_failure'])
async def test_terminal_cancellation_winning_old_executor_is_never_resurrected(integration,monkeypatch,phase):
    db,provider=integration;row=await preview(db)
    async def cancel_terminal():
        stored=db[management.PROPOSALS].rows[0]
        stored.update(status='failed',submission_started=False,safe_error='execution_cancelled_before_dispatch',provider_write_reached=False)
        await authority.settle_google_automation_claim(db,user_id='owner',account_id='123',claim_id=stored['execution_claim_id'])
        await management._release_fence(db,stored)
    if phase=='before_floor':
        original=management.record_event
        async def cancel_before_floor(db_arg,proposal,status,**kwargs):
            if status=='submitted':await cancel_terminal()
            return await original(db_arg,proposal,status,**kwargs)
        monkeypatch.setattr(management,'record_event',cancel_before_floor)
    else:
        original=authority.authorize_google_automation_claim
        async def cancel_then_lose_ack(*args,**kwargs):
            await original(*args,**kwargs);await cancel_terminal()
            raise TimeoutError('test-only old authority response lost after cancellation')
        monkeypatch.setattr(authority,'authorize_google_automation_claim',cancel_then_lose_ack)
    result=await execute(db,row)
    assert result['status']=='failed' and result['safe_error']=='execution_cancelled_before_dispatch'
    assert db[management.PROPOSALS].rows[0]['status']=='failed' and provider.writes==0
    assert db[management.FENCES].rows[0]['status']=='released'
    assert db[gateway.AUTOMATION_POLICIES].rows[0]['_dispatch_claim'] is None

@pytest.mark.asyncio
@pytest.mark.parametrize('outcome',['completed','provider_rejected'])
async def test_unknown_policy_settlement_keeps_native_account_fence(integration,monkeypatch,outcome):
    db,provider=integration;row=await preview(db)
    if outcome=='provider_rejected':
        original_mutate=provider.mutate
        async def reject(operations,*,validate_only=False):
            if validate_only:return await original_mutate(operations,validate_only=True)
            raise HTTPException(400,{'code':'native_rejected','provider_status':400})
        monkeypatch.setattr(provider,'mutate',reject)
    original_settle=gateway._settle_if_present
    async def interrupted(*args,**kwargs):raise TimeoutError('test-only settlement response unavailable')
    monkeypatch.setattr(gateway,'_settle_if_present',interrupted)
    with pytest.raises(TimeoutError):await execute(db,row)
    assert db[management.FENCES].rows[0]['status']=='claimed'
    assert db[gateway.AUTOMATION_POLICIES].rows[0]['_dispatch_claim']
    monkeypatch.setattr(gateway,'_settle_if_present',original_settle)
    monkeypatch.delenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED')
    result=await gateway.reconcile_google_automation_proposal(db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])
    assert result['status']==('completed' if outcome=='completed' else 'failed')
    assert db[management.FENCES].rows[0]['status']=='released'
    assert db[gateway.AUTOMATION_POLICIES].rows[0]['_dispatch_claim'] is None

@pytest.mark.asyncio
async def test_missing_policy_at_settlement_is_unproved_and_cannot_release_fence(integration,monkeypatch):
    db,provider=integration;row=await preview(db);original=gateway._settle_if_present
    async def missing(db_arg,proposal):
        db[gateway.AUTOMATION_POLICIES].rows.clear()
        return await original(db_arg,proposal)
    monkeypatch.setattr(gateway,'_settle_if_present',missing)
    with pytest.raises(HTTPException) as error:await execute(db,row)
    assert error.value.detail['code']=='google_automation_settlement_unconfirmed'
    assert provider.writes==1 and db[management.FENCES].rows[0]['status']=='claimed'

@pytest.mark.asyncio
@pytest.mark.parametrize('slow_phase',['credential','metadata','snapshot','inventory'])
async def test_oldest_native_read_time_is_not_reset_by_later_slow_reads(integration,monkeypatch,slow_phase):
    from datetime import timedelta
    db,provider=integration;row=await preview(db)
    clock={'current':NOW}
    monkeypatch.setattr(management,'now',lambda:clock['current'])
    monkeypatch.setattr(authority,'_now',lambda current=None:current or clock['current'])
    if slow_phase=='credential':
        original=management.account_client
        async def slow(*args,**kwargs):
            result=await original(*args,**kwargs);clock['current']+=timedelta(seconds=91);return result
        monkeypatch.setattr(management,'account_client',slow)
    elif slow_phase=='inventory':
        original=gateway._inventory
        async def slow(*args,**kwargs):
            result=await original(*args,**kwargs);clock['current']+=timedelta(seconds=91);return result
        monkeypatch.setattr(gateway,'_inventory',slow)
    else:
        name='account_metadata' if slow_phase=='metadata' else 'campaign_snapshot'
        original=getattr(provider,name)
        async def slow(*args,**kwargs):
            result=await original(*args,**kwargs);clock['current']+=timedelta(seconds=91);return result
        monkeypatch.setattr(provider,name,slow)
    result=await execute(db,row)
    assert provider.writes==0
    assert result['status']=='uncertain' and result['submission_started'] is False
    configured=db[gateway.AUTOMATION_POLICIES].rows[0]
    assert configured.get('_dispatch_claim') is None and not configured.get('_dispatch_days')
    assert not any('_dispatch_claim' in update.get('$set',{}) for _,update in db[gateway.AUTOMATION_POLICIES].updates)
    assert db[management.FENCES].rows[0]['status']=='claimed'
