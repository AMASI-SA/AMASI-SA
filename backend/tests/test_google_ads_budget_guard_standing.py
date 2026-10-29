from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center.google_ads_automation_authority import policy_authorization_digest

NOW = datetime(2026, 10, 26, 20, tzinfo=timezone.utc)


def case():
    policy = {'user_id': 'owner', 'account_id': '123', 'revision': 1, 'enabled': True,
        'activation_version': 1, 'activation_revision': 1, 'activated_by': 'owner',
        'activated_at': NOW.isoformat(), 'expires_at': (NOW + timedelta(days=1)).isoformat(),
        'currency_code': 'SAR', 'time_zone': 'Asia/Riyadh', 'updated_by': 'owner', 'max_account_daily_budget_native': '100'}
    inventory = {'account_id': '123', 'complete': True, 'source': 'google_ads_api',
        'observed_at': NOW.isoformat(), 'currency_code': 'SAR', 'time_zone': 'Asia/Riyadh',
        'campaigns': [{'campaign_id': str(cid), 'status': status, 'budget_mode': 'DAILY',
            'budget_native': amount, 'shared_budget': False,
            'budget_resource': f'customers/123/campaignBudgets/{cid}'}
            for cid, status, amount in [(1, 'ENABLED', '60'), (2, 'PAUSED', '20'), (3, 'PAUSED', '10')]]}
    claim = {'claim_id': 'claim1', 'execution_claim_id': 'claim1', 'proposal_id': 'proposal1',
        'campaign_id': '2', 'action': 'enable', 'campaign_budget_before_native': '20',
        'campaign_budget_target_native': '20', 'policy_revision': 1, 'policy_digest': policy_authorization_digest(policy)}
    claim['native_inventory_digest'] = guard._digest(inventory)
    snapshot = guard.BudgetGuardSnapshot('owner', '123', {'allocations': {}, 'active_claim': None}, policy, None)
    return snapshot, guard.StandingBudgetReservation('allocation1', 1, claim, inventory)


def fixture_commitment(snapshot, command, current):
    # Each test models a newly read complete snapshot, not a mutation of the
    # already-bound claim. The separate mismatch test exercises that fence.
    command.claim['native_inventory_digest'] = guard._digest(command.inventory)
    from integrations_control_center.google_ads_paused_creation_budget import standing_lineage
    for key,entry in snapshot.guard['allocations'].items():
        if entry.get('kind')=='standing':
            entry.update(user_id='owner',account_id='123',allocation_id=key,operation_id=key,
                proposal_id='prior_'+key,execution_claim_id='claim_'+key,claim_digest='b'*64,created_at=NOW.isoformat())
            entry['reservation_lineage_digest']=standing_lineage(entry)
    return guard.standing_commitment(snapshot, command, current)


def test_inventory_fingerprint_cannot_be_replaced_after_claim_binding():
    snapshot, command = case()
    command.inventory['campaigns'][0]['budget_native'] = '61'
    with pytest.raises(HTTPException) as error:
        guard.standing_commitment(snapshot, command, NOW)
    assert error.value.detail['code'] == 'google_budget_guard_inventory_unproved'


def test_enable_counts_existing_and_proposed_once():
    snapshot, command = case()
    result = fixture_commitment(snapshot, command, NOW)
    assert result['resulting_average_commitment_native'] == '80'
    assert result['increase_native'] == '20'


@pytest.mark.parametrize('reflected', [False, True])
def test_pending_increase_counts_only_unreflected_delta(reflected):
    snapshot, command = case()
    snapshot.guard['allocations']['old'] = {'kind':'standing', 'budget_resource':'customers/123/campaignBudgets/1', 'settlement_lineage_digest':'a'*64, 'campaign_id': '1', 'state': 'reserved',
        'currency_code': 'SAR', 'budget_native': '75'}
    if reflected:
        command.inventory['campaigns'][0]['budget_native'] = '75'
    result = fixture_commitment(snapshot, command, NOW)
    assert result['resulting_average_commitment_native'] == '95'


def test_pending_enable_counts_full_amount():
    snapshot, command = case()
    snapshot.guard['allocations']['old'] = {'kind':'standing', 'budget_resource':'customers/123/campaignBudgets/3', 'campaign_id': '3', 'state': 'reserved',
        'currency_code': 'SAR', 'budget_native': '10'}
    assert fixture_commitment(snapshot, command, NOW)['resulting_average_commitment_native'] == '90'


def test_set_budget_counts_delta_not_full_target():
    snapshot, command = case()
    command.claim.update(campaign_id='1', action='set_budget', campaign_budget_before_native='60', campaign_budget_target_native='85')
    assert fixture_commitment(snapshot, command, NOW)['resulting_average_commitment_native'] == '85'


def test_existing_overcommitment_does_not_prevent_authorized_pause():
    snapshot, command = case()
    snapshot.standing_policy['max_account_daily_budget_native'] = '50'
    command.claim['policy_digest'] = policy_authorization_digest(snapshot.standing_policy)
    command.claim.update(campaign_id='1', action='pause', campaign_budget_before_native='60', campaign_budget_target_native='60')
    assert fixture_commitment(snapshot, command, NOW)['increase_native'] == '0'


def test_minimum_active_policy_cap_applies():
    snapshot, command = case()
    creation = {**snapshot.standing_policy, 'max_account_daily_budget_native': '70'}
    snapshot = guard.BudgetGuardSnapshot(snapshot.user_id, snapshot.account_id, snapshot.guard, snapshot.standing_policy, creation)
    with pytest.raises(HTTPException) as error:
        fixture_commitment(snapshot, command, NOW)
    assert error.value.detail['code'] == 'google_budget_guard_account_limit'


def test_completed_increase_rejects_still_fresh_but_pre_settlement_inventory():
    snapshot, command = case()
    snapshot.guard['allocations']['old'] = {'kind':'standing', 'budget_resource':'customers/123/campaignBudgets/1', 'settlement_lineage_digest':'a'*64, 'campaign_id': '1', 'state': 'native_enabled',
        'currency_code': 'SAR', 'budget_native': '75', 'action': 'set_budget',
        'settled_at': (NOW + timedelta(seconds=1)).isoformat()}
    with pytest.raises(HTTPException) as error:
        fixture_commitment(snapshot, command, NOW + timedelta(seconds=2))
    assert error.value.detail['code'] == 'google_budget_guard_reflection_unproved'


def test_completed_increase_counts_reflected_native_once():
    snapshot, command = case()
    snapshot.guard['allocations']['old'] = {'kind':'standing', 'budget_resource':'customers/123/campaignBudgets/1', 'settlement_lineage_digest':'a'*64, 'campaign_id': '1', 'state': 'native_enabled',
        'currency_code': 'SAR', 'budget_native': '75', 'action': 'set_budget', 'settled_at': NOW.isoformat()}
    command.inventory['campaigns'][0]['budget_native'] = '75'
    assert fixture_commitment(snapshot, command, NOW)['resulting_average_commitment_native'] == '95'


@pytest.mark.parametrize('mutation', ['stale', 'currency', 'shared', 'duplicate_budget', 'unknown_pending', 'expired'])
def test_ambiguous_evidence_fails_closed(mutation):
    snapshot, command = case()
    if mutation == 'stale': command.inventory['observed_at'] = (NOW - timedelta(seconds=90)).isoformat()
    if mutation == 'currency': command.inventory['currency_code'] = 'USD'
    if mutation == 'shared': command.inventory['campaigns'][0]['shared_budget'] = True
    if mutation == 'duplicate_budget': command.inventory['campaigns'][1]['budget_resource'] = command.inventory['campaigns'][0]['budget_resource']
    if mutation == 'unknown_pending': snapshot.guard['allocations']['old'] = {'campaign_id': '999', 'state': 'reserved'}
    if mutation == 'expired': snapshot.standing_policy['expires_at'] = NOW.isoformat()
    with pytest.raises(HTTPException):
        fixture_commitment(snapshot, command, NOW)


def test_storage_never_declares_runtime_available():
    assert guard.budget_guard_runtime_available() is False


@pytest.mark.asyncio
async def test_transaction_reserves_quota_and_budget_then_settles_known_no_http(monkeypatch):
    from tests.test_google_ads_automation_budget_guard import DB, matches as simple_matches
    from tests import test_google_ads_automation_budget_guard as fake
    from integrations_control_center import google_ads_automation_authority as authority
    from integrations_control_center.google_ads_management import PROPOSALS, digest
    # The fake models transaction/session plumbing only; Mongo concurrency is
    # covered by the separate replica suite, never claimed by this check.
    def matches(row, query):
        return all(row.get(key) in value['$in'] if isinstance(value, dict) and '$in' in value
            else simple_matches(row, {key: value}) for key, value in query.items())
    monkeypatch.setattr(fake, 'matches', matches)
    snapshot, command = case()
    current = datetime.now(timezone.utc)
    snapshot.standing_policy.update(activated_at=current.isoformat(), expires_at=(current+timedelta(days=1)).isoformat(), max_actions_per_day=1)
    command.inventory['observed_at'] = current.isoformat()
    command.claim.update(confirmation_digest='confirmation', operations_digest=digest([]), policy_revision=1, policy_digest=policy_authorization_digest(snapshot.standing_policy))
    command.claim['native_inventory_digest'] = digest(command.inventory)
    db = DB({guard.AUTOMATION_POLICIES: [snapshot.standing_policy]})
    capability = await guard.probe_google_budget_guard_transactions(db)
    args = dict(user_id='owner', account_id='123', capability=capability)
    reply = await guard.run_google_budget_guard_transaction(db, operation_id='reserve', planner=lambda state: command, **args)
    assert reply['committed'] and reply['provider_dispatch_authorized'] is False
    assert db.rows[guard.AUTOMATION_POLICIES][0]['_dispatch_days'][0]['proposal_ids'] == ['proposal1']
    before={'campaign':{'id':'2','campaignBudget':'customers/123/campaignBudgets/2'},
        'campaign_budget':{'resourceName':'customers/123/campaignBudgets/2','period':'DAILY','amountMicros':'20000000'}}
    db.rows[PROPOSALS] = [{'before_snapshot':before,'before_digest':digest(before),'user_id':'owner', 'account_id':'123', 'proposal_id':'proposal1', 'execution_claim_id':'claim1',
        'confirmation_digest':'confirmation', 'operations':[], 'status':'failed', 'submission_started':False,
        'safe_error':'execution_cancelled_before_dispatch'}]
    monkeypatch.setattr(authority, '_bound_proposal', lambda proposal: {'policy_revision':1, 'policy_digest':command.claim['policy_digest']})
    settled = await guard.run_google_budget_guard_transaction(db, operation_id='settle',
        planner=lambda state: guard.BudgetReservationSettlement('allocation1','claim1'), **args)
    assert settled['committed'] and settled['quota_refunded'] is False
    assert db.rows[guard.BUDGET_GUARDS][0]['epoch'] == 2
    assert db.rows[guard.BUDGET_GUARDS][0]['active_claim'] is None
    assert db.rows[guard.AUTOMATION_POLICIES][0]['_dispatch_claim'] is None
    assert db.rows[guard.AUTOMATION_POLICIES][0]['_dispatch_days'][0]['proposal_ids'] == ['proposal1']
