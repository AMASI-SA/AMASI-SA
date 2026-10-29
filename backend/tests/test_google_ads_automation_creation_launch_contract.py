"""Funded launch preview is pure, provenance scoped, and never execution authority."""
from copy import deepcopy
from datetime import timedelta

import pytest

from integrations_control_center import google_ads_automation_creation_contract as creation
from integrations_control_center import google_ads_automation_creation_launch_contract as launch
from tests.test_google_ads_automation_creation_contract import NOW, prepared_case, valid_ledger, valid_proposal


def case():
    inputs = prepared_case()
    intent = creation.prepare_google_creation_intent(**inputs)
    policy = deepcopy(inputs['policy'])
    policy.update(auto_enable_new=True, revision=3, activation_revision=3)
    ledger = valid_ledger(intent)
    ledger.update(state='owned', reservation_state='paused_created', reservation_native='15', reservation_currency_code='SAR')
    native = deepcopy(inputs['native_context'])
    native.update(segmented=False, inventory_scope='ALL_CAMPAIGNS', has_more=False)
    native['campaigns'].append({**deepcopy(native['campaigns'][0]), 'campaign_id': '999', 'status': 'PAUSED',
        'budget_native': '15', 'campaign_type': 'SEARCH', 'correlation_label': ledger['correlation_label'],
        'budget_reference_count': 1, 'creation_configuration_verified': True, 'creation_draft_digest': intent['binding']['draft_digest']})
    for row in native['campaigns']:
        row.update(shared_budget=False, budget_resource=f"customers/123/campaignBudgets/{row['campaign_id']}",
            resource_name=f"customers/123/campaigns/{row['campaign_id']}")
    return dict(policy=policy, creation_intent=intent, ledger_entry=ledger, original_proposal={**valid_proposal(intent), 'created_at': NOW.isoformat()},
        campaign_id='999', native_context=native,
        launch_state={'user_id': 'owner', 'account_id': '123', 'complete': True, 'observed_at': NOW.isoformat(),
            'standing_policy_present': False, 'guard_epoch': 0, 'pending_write': False, 'consumed_creation_identities': [], 'allocations': []}, current=NOW)


def blocked(values, code):
    with pytest.raises(launch.CreationLaunchContractError) as error:
        launch.prepare_google_creation_launch_intent(**values)
    assert error.value.code == code


def test_exact_headroom_prepares_readonly_enable_preview_without_budget_change():
    values = case()
    result = launch.prepare_google_creation_launch_intent(**values)
    assert result['ready_for_enable_preview'] is False
    assert result['executable'] is False and result['runtime_supported'] is False and result['provider_writes'] == 0
    assert result['planned'] == {'action': 'enable', 'campaign_id': '999', 'before_status': 'PAUSED', 'after_status': 'ENABLED'}
    assert result['funding']['resulting_average_commitment_native'] == '55'
    assert result['funding']['account_commitment_limit_native'] == '55'
    assert result['authority_required'] is True


@pytest.mark.parametrize('change,code', [
    ({'enabled': False}, 'launch_policy_not_authorized'),
    ({'auto_enable_new': False}, 'launch_policy_not_authorized'),
    ({'activation_revision': 2}, 'launch_policy_not_authorized'),
    ({'activation_version': True}, 'launch_policy_invalid'),
    ({'activated_by': 'viewer'}, 'launch_policy_not_authorized'),
    ({'expires_at': NOW.isoformat()}, 'launch_policy_expired'),
    ({'owner_generation': 2}, 'launch_creation_scope_changed'),
    ({'template_version': 2}, 'launch_creation_scope_changed'),
    ({'budget_native': '14'}, 'launch_creation_ownership_unproved'),
])
def test_current_explicit_owner_permission_and_immutable_creation_scope(change, code):
    values = case(); values['policy'].update(change)
    blocked(values, code)


@pytest.mark.parametrize('change', [
    {'complete': False}, {'segmented': True}, {'has_more': True}, {'inventory_scope': 'SELECTED'},
    {'observed_at': (NOW - timedelta(seconds=90)).isoformat()}, {'account_id': 'other'},
])
def test_partial_stale_or_wrong_native_inventory_blocks(change):
    values = case(); values['native_context'].update(change)
    blocked(values, 'launch_native_inventory_unproved')


@pytest.mark.parametrize('change', [
    {'status': 'UNKNOWN'}, {'shared_budget': True}, {'budget_mode': 'TOTAL'}, {'budget_native': 'NaN'},
    {'budget_native': 40}, {'currency_code': 'USD'}, {'shared_budget': None},
])
def test_any_unknown_campaign_budget_status_or_shared_budget_blocks(change):
    values = case(); values['native_context']['campaigns'][0].update(change)
    blocked(values, 'launch_native_inventory_unproved')


def test_no_manual_campaign_adoption_even_matching_names_labels_and_product():
    values = case(); values['original_proposal']['actor_id'] = 'owner'
    blocked(values, 'launch_creation_ownership_unproved')
    values = case(); values['ledger_entry'] = None
    blocked(values, 'launch_creation_ownership_unproved')
    values = case(); values['campaign_id'] = '24390429267'
    blocked(values, 'launch_creation_ownership_unproved')


def allocation(values, campaign_id='777', state='reserved', amount='1'):
    return {'allocation_id': 'allocation_1', 'user_id': 'owner', 'account_id': '123', 'currency_code': 'SAR',
        'campaign_id': campaign_id, 'state': state, 'budget_native': amount}


def test_allocations_count_once_and_funding_never_assumes_pending_savings():
    values = case()
    values['launch_state']['allocations'] = [allocation(values, '456', 'native_enabled', '40')]
    assert launch.prepare_google_creation_launch_intent(**values)['funding']['resulting_average_commitment_native'] == '55'
    pending = {**deepcopy(values['native_context']['campaigns'][0]), 'campaign_id': '777', 'status': 'PAUSED', 'budget_native': '1',
        'budget_resource': 'customers/123/campaignBudgets/777', 'resource_name': 'customers/123/campaigns/777'}
    values['native_context']['campaigns'].append(pending)
    values['launch_state']['allocations'].append({**allocation(values), 'allocation_id': 'allocation_2'})
    blocked(values, 'launch_account_commitment_limit')


@pytest.mark.parametrize('change', [{'state': 'unknown'}, {'campaign_id': 'missing'}, {'currency_code': 'USD'},
    {'state': 'reserved', 'campaign_id': '456', 'budget_native': '40'}, {'campaign_id': '999', 'budget_native': '15'}])
def test_unknown_or_ambiguous_allocations_block(change):
    values = case(); item = allocation(values); item.update(change)
    values['launch_state']['allocations'] = [item]
    blocked(values, 'launch_allocations_unproved')


def test_lower_active_standing_cap_wins_and_absence_must_be_explicit():
    values = case()
    values['standing_policy'] = {'user_id': 'owner', 'account_id': '123', 'enabled': True, 'revision': 7,
        'activation_version': 1, 'activation_revision': 7, 'activated_by': 'owner', 'activated_at': NOW.isoformat(),
        'expires_at': (NOW + timedelta(days=1)).isoformat(), 'currency_code': 'SAR', 'time_zone': 'Asia/Riyadh',
        'max_account_daily_budget_native': '54.999999'}
    values['launch_state']['standing_policy_present'] = True
    blocked(values, 'launch_account_commitment_limit')
    values['standing_policy']['max_account_daily_budget_native'] = '100'
    assert launch.prepare_google_creation_launch_intent(**values)['funding']['account_commitment_limit_native'] == '55'
    values['launch_state']['standing_policy_present'] = False
    blocked(values, 'launch_standing_policy_unproved')


def test_revalidation_binds_native_before_policy_and_allocations_without_mutating_inputs():
    values = case(); untouched = deepcopy(values)
    intent = launch.prepare_google_creation_launch_intent(**values)
    assert values == untouched
    assert launch.revalidate_google_creation_launch_intent(intent, **values) == intent
    values['native_context']['campaigns'][0]['budget_native'] = '39'
    with pytest.raises(launch.CreationLaunchContractError, match='launch_intent_changed'):
        launch.revalidate_google_creation_launch_intent(intent, **values)


@pytest.mark.parametrize('path,value,code', [
    ('pending_write', True, 'launch_state_unproved'),
    ('complete', False, 'launch_state_unproved'),
    ('standing_policy_present', None, 'launch_standing_policy_unproved'),
])
def test_missing_state_never_means_no_claims_or_no_other_policy(path, value, code):
    values = case(); values['launch_state'][path] = value
    blocked(values, code)


@pytest.mark.parametrize('change', [{'status': 'ENABLED'}, {'campaign_type': 'PERFORMANCE_MAX'},
    {'creation_configuration_verified': False}, {'creation_draft_digest': 'b' * 64},
    {'correlation_label': 'manual'}, {'budget_native': '14'}])
def test_launch_never_changes_or_relaunches_drifted_native_campaign(change):
    values = case(); values['native_context']['campaigns'][1].update(change)
    blocked(values, 'launch_native_target_changed')


def test_existing_fifty_five_plus_new_fifteen_does_not_fit_fifty_five():
    values = case(); values['native_context']['campaigns'][0]['budget_native'] = '55'
    blocked(values, 'launch_account_commitment_limit')
    values['native_context']['campaigns'][0]['budget_native'] = '40.000001'
    blocked(values, 'launch_account_commitment_limit')


@pytest.mark.parametrize('duplicate', ['campaign', 'budget', 'allocation'])
def test_duplicates_cannot_hide_or_double_count_commitments(duplicate):
    values = case()
    if duplicate == 'campaign':
        values['native_context']['campaigns'].append(deepcopy(values['native_context']['campaigns'][0]))
    elif duplicate == 'budget':
        values['native_context']['campaigns'][1]['budget_resource'] = values['native_context']['campaigns'][0]['budget_resource']
    else:
        values['launch_state']['allocations'] = [allocation(values, '456', 'native_enabled', '40')] * 2
    blocked(values, 'launch_allocations_unproved' if duplicate == 'allocation' else 'launch_native_inventory_unproved')


@pytest.mark.parametrize('key,value,code', [
    ('current', NOW.replace(tzinfo=None), 'launch_clock_unproved'),
    ('policy', None, 'launch_policy_invalid'),
    ('native_context', None, 'launch_native_inventory_unproved'),
    ('launch_state', None, 'launch_state_unproved'),
    ('original_proposal', None, 'launch_creation_ownership_unproved'),
    ('creation_intent', None, 'launch_creation_ownership_unproved'),
])
def test_malformed_snapshots_fail_with_bounded_codes(key, value, code):
    values = case(); values[key] = value
    blocked(values, code)


def test_cap_change_revision_even_equal_money_invalidates_preview():
    values = case(); intent = launch.prepare_google_creation_launch_intent(**values)
    values['policy'].update(revision=4, activation_revision=4)
    with pytest.raises(launch.CreationLaunchContractError, match='launch_intent_changed'):
        launch.revalidate_google_creation_launch_intent(intent, **values)


@pytest.mark.parametrize('epoch', [None, True, -1, 2**63-2])
def test_future_account_guard_snapshot_must_be_explicit_bounded_integer(epoch):
    values = case(); values['launch_state']['guard_epoch'] = epoch
    blocked(values, 'launch_state_unproved')


def test_account_guard_epoch_and_standing_absence_are_bound_but_not_authority():
    values = case(); intent = launch.prepare_google_creation_launch_intent(**values)
    cap = intent['binding']['cap_binding']
    assert cap == {'creation_cap_native': '55', 'standing_policy_present': False,
        'standing_policy_digest': None, 'standing_policy_revision': None, 'account_guard_epoch': 0}
    values['launch_state']['guard_epoch'] = 1
    with pytest.raises(launch.CreationLaunchContractError, match='launch_intent_changed'):
        launch.revalidate_google_creation_launch_intent(intent, **values)


def test_manual_pause_never_triggers_second_initial_launch_and_identity_survives_policy_revision():
    values = case(); first = launch.prepare_google_creation_launch_intent(**values)
    values['policy'].update(revision=4, activation_revision=4)
    assert launch.prepare_google_creation_launch_intent(**values)['launch_identity'] == first['launch_identity']
    values['launch_state']['consumed_creation_identities'] = [values['creation_intent']['creation_identity']]
    blocked(values, 'launch_creation_already_consumed')


@pytest.mark.parametrize('history', [None, ['manual'], [True]])
def test_missing_or_unknown_history_does_not_mean_never_launched(history):
    values = case(); values['launch_state']['consumed_creation_identities'] = history
    blocked(values, 'launch_history_unproved')


@pytest.mark.parametrize('references', [None, True, 2])
def test_target_budget_must_have_single_native_reference(references):
    values = case(); values['native_context']['campaigns'][1]['budget_reference_count'] = references
    blocked(values, 'launch_native_target_changed')


def existing_scope_ack(values):
    policy = values['policy']; intent = values['creation_intent']
    return {'acknowledged_by': policy['user_id'], 'acknowledged_at': NOW.isoformat(),
        'policy_revision': policy['revision'], 'owner_generation': policy['owner_generation'],
        'template_id': policy['template_id'], 'template_version': policy['template_version'],
        'product_id': intent['binding']['identity']['product_id'], 'final_url': intent['binding']['product']['final_url'],
        'campaign_id': values['campaign_id'], 'creation_identity': intent['creation_identity']}


def old_owned_case():
    values = case(); values['original_proposal']['created_at'] = (NOW - timedelta(minutes=1)).isoformat()
    return values


def test_old_owned_paused_campaign_is_not_adopted_by_flipping_auto_enable():
    values = old_owned_case()
    blocked(values, 'launch_existing_owned_not_authorized')
    values['launch_state']['include_existing_owned'] = False
    values['launch_state']['existing_owned_scope_ack'] = existing_scope_ack(values)
    blocked(values, 'launch_existing_owned_not_authorized')


def test_explicit_existing_owned_scope_ack_can_prepare_only_its_bound_campaign():
    values = old_owned_case()
    values['launch_state'].update(include_existing_owned=True, existing_owned_scope_ack=existing_scope_ack(values))
    preview = launch.prepare_google_creation_launch_intent(**values)
    assert preview['ready_for_enable_preview'] is False and preview['executable'] is False
    assert preview['binding']['launch_scope']['include_existing_owned'] is True
    assert preview['binding']['launch_scope']['existing_owned_scope_ack_digest']


@pytest.mark.parametrize('change', [
    {'acknowledged_by': 'viewer'}, {'policy_revision': 2}, {'owner_generation': 2},
    {'template_id': 'other'}, {'template_version': 2}, {'product_id': 'other'},
    {'final_url': 'https://amasi-sa.com/p2'}, {'campaign_id': '24390429267'},
    {'creation_identity': 'google_create_' + '0' * 64},
    {'acknowledged_at': (NOW - timedelta(seconds=1)).isoformat()},
    {'acknowledged_at': (NOW + timedelta(seconds=1)).isoformat()},
])
def test_existing_owned_ack_is_owner_current_and_exact_scope(change):
    values = old_owned_case(); ack = existing_scope_ack(values); ack.update(change)
    values['launch_state'].update(include_existing_owned=True, existing_owned_scope_ack=ack)
    blocked(values, 'launch_existing_owned_not_authorized')


@pytest.mark.parametrize('stamp', [None, 'unknown', NOW.replace(tzinfo=None).isoformat(), (NOW + timedelta(seconds=1)).isoformat()])
def test_original_creation_time_is_required_even_with_existing_scope_ack(stamp):
    values = old_owned_case(); values['original_proposal']['created_at'] = stamp
    values['launch_state'].update(include_existing_owned=True, existing_owned_scope_ack=existing_scope_ack(values))
    blocked(values, 'launch_creation_time_unproved')


@pytest.mark.parametrize('include', [None, 'true', 1])
def test_existing_opt_in_must_be_exact_bool_and_never_implicit(include):
    values = old_owned_case(); values['launch_state']['include_existing_owned'] = include
    blocked(values, 'launch_existing_owned_not_authorized')


def test_existing_ack_cannot_relaunch_previously_consumed_creation():
    values = old_owned_case()
    values['launch_state'].update(include_existing_owned=True, existing_owned_scope_ack=existing_scope_ack(values),
        consumed_creation_identities=[values['creation_intent']['creation_identity']])
    blocked(values, 'launch_creation_already_consumed')


def test_readonly_evidence_never_claims_a_first_launch_or_owner_stop_authority():
    result = launch.prepare_google_creation_launch_intent(**case())
    assert result['ready_for_enable_preview'] is False
    assert result['launch_history_verified'] is False
    assert result['blockers'] == ['launch_stop_provenance_unproved', 'launch_atomic_authority_unimplemented']


def test_refreshing_only_observation_times_does_not_change_semantic_binding():
    values = case()
    original = launch.prepare_google_creation_launch_intent(**values)
    later = NOW + timedelta(seconds=5)
    values['current'] = later
    values['native_context']['observed_at'] = later.isoformat()
    for row in values['native_context']['campaigns']:
        row['observed_at'] = later.isoformat()
    values['launch_state']['observed_at'] = later.isoformat()
    refreshed = launch.revalidate_google_creation_launch_intent(original, **values)
    assert refreshed['intent_digest'] == original['intent_digest']
    assert refreshed['evidence_observed_at'] != original['evidence_observed_at']
