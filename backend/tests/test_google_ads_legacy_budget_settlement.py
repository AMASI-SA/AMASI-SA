"""Terminal legacy claims recover atomically; unknown outcomes keep their hold."""
from copy import deepcopy

from fastapi import HTTPException
import pytest
import pytest_asyncio

from integrations_control_center import google_ads_automation_authority as authority
from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center.google_ads_management import PROPOSALS, FENCES
from tests.test_google_ads_automation_authority import DB, NOW, case_documents, authorize, freeze_guard_clock


@pytest_asyncio.fixture
async def legacy_case(monkeypatch):
    freeze_guard_clock(monkeypatch,NOW)
    monkeypatch.setattr(guard,'budget_guard_runtime_available',lambda:True)
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','true')
    docs,native=case_documents();db=DB(docs)
    await authorize(db,native)
    db[guard.BUDGET_GUARDS].rows=[]
    db['mezan_integrations_v2'].rows=[]
    db['mezan_integration_accounts_v2'].rows=[]
    db[guard.AUTOMATION_POLICIES].rows[0].update(enabled=False,revision=4)
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','false')
    monkeypatch.setattr(guard,'budget_guard_runtime_available',lambda:False)
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal',['cancelled','rejected','completed'])
async def test_known_terminal_legacy_claim_recovers_without_oauth_or_quota_refund(legacy_case,terminal):
    db=legacy_case
    row=db[PROPOSALS].rows[0]
    if terminal=='cancelled':row.update(status='failed',submission_started=False,safe_error='execution_cancelled_before_dispatch')
    elif terminal=='rejected':row.update(status='failed',submission_started=True,provider_write_reached=False,rejection={'provider_status':400})
    else:row.update(status='completed',verified=True,submission_started=True)
    quota=deepcopy(db[guard.AUTOMATION_POLICIES].rows[0]['_dispatch_days'])
    result=await authority.settle_google_automation_claim(db,user_id='owner',account_id='123',claim_id='claim1',current=NOW)
    assert result['released'] is True and result['quota_refunded'] is False
    stored=db[guard.AUTOMATION_POLICIES].rows[0]
    assert stored['_dispatch_claim'] is None and stored['_dispatch_days']==quota and stored['enabled'] is False
    allocation=db[guard.BUDGET_GUARDS].rows[0]['allocations']['standing_claim1']
    assert allocation['origin']=='legacy_terminal_recovery'
    assert allocation['state']==('native_enabled' if terminal=='completed' else 'settled')
    assert db[FENCES].rows[0]['status']=='claimed'  # only gateway releases fence
    assert guard.budget_guard_runtime_available() is False


@pytest.mark.asyncio
@pytest.mark.parametrize('state',['uncertain','verifying','approved'])
async def test_unknown_legacy_claim_never_creates_a_tombstone_or_releases_hold(legacy_case,state):
    db=legacy_case;db[PROPOSALS].rows[0].update(status=state,submission_started=state!='approved')
    held=deepcopy(db[guard.AUTOMATION_POLICIES].rows[0])
    with pytest.raises(HTTPException) as error:
        await authority.settle_google_automation_claim(db,user_id='owner',account_id='123',claim_id='claim1',current=NOW)
    assert error.value.detail['preserve_execution_fence'] is True
    assert error.value.detail['automatic_retry_allowed'] is False
    assert db[guard.AUTOMATION_POLICIES].rows[0]==held
    assert db[guard.BUDGET_GUARDS].rows==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption',['fence_missing','amount','campaign','before_snapshot','proposal_digest'])
async def test_legacy_recovery_requires_original_claim_and_native_proposal_binding(legacy_case,corruption):
    db=legacy_case;row=db[PROPOSALS].rows[0]
    row.update(status='failed',submission_started=False,safe_error='execution_cancelled_before_dispatch')
    if corruption=='fence_missing':db[FENCES].rows=[]
    if corruption=='amount':db[guard.AUTOMATION_POLICIES].rows[0]['_dispatch_claim']['campaign_budget_target_native']='1'
    if corruption=='campaign':db[guard.AUTOMATION_POLICIES].rows[0]['_dispatch_claim']['campaign_id']='999'
    if corruption=='before_snapshot':row['before_snapshot']['campaign_budget']['amountMicros']='1000000'
    if corruption=='proposal_digest':row['confirmation_digest']='f'*64
    held=deepcopy(db[guard.AUTOMATION_POLICIES].rows[0])
    with pytest.raises(HTTPException):
        await authority.settle_google_automation_claim(db,user_id='owner',account_id='123',claim_id='claim1',current=NOW)
    assert db[guard.AUTOMATION_POLICIES].rows[0]==held
    assert db[guard.BUDGET_GUARDS].rows==[]
