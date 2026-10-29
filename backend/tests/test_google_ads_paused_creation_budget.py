"""Shared PAUSED reservations use actual internal authority helpers, never ads."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import pytest
from fastapi import HTTPException
from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center import google_ads_automation_creation_authority as authority
from integrations_control_center.google_ads_paused_creation_budget import account_exposure
from integrations_control_center.google_ads_management import PROPOSALS
from tests.test_google_ads_automation_creation_authority import DB, documents, authorize, settle, completed
from tests.test_google_ads_automation_creation_contract import NOW
from tests.test_google_ads_automation_authority import freeze_guard_clock

@pytest.fixture(autouse=True)
def fixture_runtime_only(monkeypatch):
    for key in ('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','GOOGLE_ADS_AUTOMATION_WORKER_ENABLED','GOOGLE_ADS_AUTOMATION_CREATION_ENABLED'):
        monkeypatch.setenv(key,'true')
    monkeypatch.setattr(guard,'budget_guard_runtime_available',lambda:True)
    monkeypatch.setattr(authority,'now',lambda current=None:current or NOW)
    freeze_guard_clock(monkeypatch,NOW)

async def owned():
    docs,native,intent=documents(budget_native='20',native_amount='30');db=DB(docs)
    await authorize(db,native);completed(db,intent);await settle(db)
    row=db[guard.BUDGET_GUARDS].rows[0]
    allocation=next(value for value in row['allocations'].values() if value['kind']=='paused_creation')
    inventory=deepcopy(native['native_context'])
    inventory['campaigns'].append({'campaign_id':'999','account_id':'123','source':'google_ads_api',
        'currency_code':'SAR','time_zone':'Asia/Riyadh','observed_at':NOW.isoformat(),
        'budget_resource':allocation['budget_resource'],'shared_budget':False,'budget_mode':'DAILY',
        'budget_native':'20','status':'PAUSED'})
    def snapshot():
        return guard.BudgetGuardSnapshot('owner','123',deepcopy(db[guard.BUDGET_GUARDS].rows[0]),
            deepcopy(db[guard.AUTOMATION_POLICIES].rows[0]),deepcopy(db[guard.CREATION_POLICIES].rows[0]))
    return db,inventory,intent,snapshot

@pytest.mark.asyncio
async def test_common_reservation_is_durable_and_unknown_receipt_keeps_epoch_allocation_and_fence():
    docs,native,intent=documents();db=DB(docs);reply=await authorize(db,native)
    state=db[guard.BUDGET_GUARDS].rows[0];before=deepcopy(state)
    assert reply['authorized'] and reply['provider_dispatch_authorized'] is False
    assert state['epoch']==1 and state['active_claim']['execution_claim_id']=='claim123'
    allocation=next(iter(state['allocations'].values()))
    assert allocation['original_creation_budget_native']=='15' and allocation['native_inventory_digest']
    db[PROPOSALS].rows[0].update(status='uncertain',submission_started=True,accepted_receipt=True)
    with pytest.raises(HTTPException):await settle(db)
    assert db[guard.BUDGET_GUARDS].rows[0]==before
    assert db[guard.CREATION_POLICIES].rows[0]['_creation_claim']
    assert db[authority.management.FENCES].rows[0]['status']=='claimed'

@pytest.mark.asyncio
async def test_creation_only_owner_policy_is_valid_but_missing_explicit_cap_is_not():
    docs,native,_=documents();docs.pop(guard.AUTOMATION_POLICIES)
    db=DB(docs);assert (await authorize(db,native))['authorized']
    docs,native,_=documents();docs[guard.AUTOMATION_POLICIES][0].pop('max_account_daily_budget_native')
    db=DB(docs)
    with pytest.raises(HTTPException):await authorize(db,native)
    assert not db[guard.BUDGET_GUARDS].rows and '_creation_claim' not in db[guard.CREATION_POLICIES].rows[0]

@pytest.mark.asyncio
async def test_paused_creation_reservation_cannot_ignore_existing_enabled_exposure():
    docs,native,_=documents(native_amount='55');db=DB(docs)
    with pytest.raises(HTTPException) as error:await authorize(db,native)
    assert error.value.detail['code']=='google_budget_guard_account_limit'
    assert not db[guard.BUDGET_GUARDS].rows and not db[guard.CREATION_POLICIES].updates

@pytest.mark.asyncio
async def test_original20_enabled22_then15_never_releases_original_hold_or_double_counts():
    db,inventory,intent,snapshot=await owned();row=inventory['campaigns'][-1]
    for status,amount,expected in [('PAUSED','20','20'),('ENABLED','22','22'),('ENABLED','15','20'),('PAUSED','22','22')]:
        row.update(status=status,budget_native=amount)
        state=snapshot();proof=account_exposure(state,inventory,state.creation_policy,NOW)
        assert proof['exposure_by_campaign']['999']==Decimal(expected)
        assert proof['total']==Decimal('30')+Decimal(expected)
        entry=db[guard.CREATION_POLICIES].rows[0]['_creation_ledger'][intent['creation_identity']]
        assert entry['reservation_native']=='20'
        assert next(iter(db[guard.BUDGET_GUARDS].rows[0]['allocations'].values()))['original_creation_budget_native']=='20'

@pytest.mark.asyncio
@pytest.mark.parametrize('status,amount',[('PAUSED','20'),('ENABLED','22'),('ENABLED','15')])
async def test_pending25_same_resource_is_one_max_with_native_and_creation_hold(status,amount):
    _,inventory,_,snapshot=await owned();row=inventory['campaigns'][-1];row.update(status=status,budget_native=amount)
    state=snapshot();state.guard['allocations']['pending']={'kind':'standing','campaign_id':'999','state':'reserved',
        'budget_resource':row['budget_resource'],'currency_code':'SAR','budget_native':'25'}
    from integrations_control_center.google_ads_paused_creation_budget import standing_lineage
    pending=state.guard['allocations']['pending']
    pending.update(user_id='owner',account_id='123',allocation_id='pending',operation_id='prior',
        proposal_id='prior_proposal',execution_claim_id='prior_claim',claim_digest='b'*64,created_at=NOW.isoformat(),action='set_budget')
    pending['reservation_lineage_digest']=standing_lineage(pending)
    proof=account_exposure(state,inventory,state.creation_policy,NOW)
    assert proof['exposure_by_campaign']['999']==Decimal('25') and proof['total']==Decimal('55')

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['missing_native','removed_without_supported_proof','different_resource','shared_collision','legacy_unmapped','mixed_unknown','unbound_standing','currency','old_native'])
async def test_ambiguous_mapping_never_releases_existing_owned_hold_even_revoked(mode):
    db,inventory,_,snapshot=await owned();state=snapshot();state.creation_policy['enabled']=False
    before=deepcopy(db[guard.BUDGET_GUARDS].rows)
    if mode=='missing_native':inventory['campaigns'].pop()
    elif mode=='removed_without_supported_proof':inventory['campaigns'][-1]['status']='REMOVED'
    elif mode=='different_resource':inventory['campaigns'][-1]['budget_resource']='customers/123/campaignBudgets/777'
    elif mode=='shared_collision':inventory['campaigns'][0]['budget_resource']=inventory['campaigns'][-1]['budget_resource']
    elif mode=='legacy_unmapped':state.guard['allocations']={}
    elif mode=='mixed_unknown':state.guard['allocations']['unknown']={'campaign_id':'999','budget_native':'25','state':'reserved'}
    elif mode=='unbound_standing':state.guard['allocations']['unknown']={'kind':'standing','campaign_id':'999','budget_resource':inventory['campaigns'][-1]['budget_resource'],'currency_code':'SAR','budget_native':'25','state':'reserved'}
    elif mode=='currency':inventory['currency_code']='USD'
    else:inventory['observed_at']=(NOW-timedelta(seconds=90)).isoformat()
    with pytest.raises(HTTPException):account_exposure(state,inventory,state.creation_policy,NOW)
    assert db[guard.BUDGET_GUARDS].rows==before
