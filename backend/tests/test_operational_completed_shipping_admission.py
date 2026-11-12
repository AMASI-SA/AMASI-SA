"""Historical eligibility requires exact review and never adjusts an opening."""
from copy import deepcopy

import pytest

from operational_balance_store import digest
from operational_completed_shipping_admission import (
    APPROVALS, MARKER, CompletionReviewRequired, approval_record, initialize_admission,
    needs_review, require_admission, review,
)
from operational_completed_shipping import deferred_source_event, apply_deferred_events
from operational_courier_tariffs import with_tariffs
from operational_delivery_evidence import _instant
from operational_prepaid_shipping import source_event, apply_events
from test_operational_balance_prepaid_shipping import fixture, COMPLETED
from test_operational_balance_delivery_cutover import START, NOW
from test_operational_completed_shipping_expansion import admitted, without_label


def candidate(data, state, *, mode='prepaid', review_only=True):
    setup, conflicts = with_tariffs({}, 'owner', state)
    assert not conflicts
    source = data['unified_orders'][0]
    args = (data, setup, source, source['raw_by_source']['salla_direct'], state, 'owner',
            _instant(START), _instant(NOW), (), ())
    return (source_event(*args, review_only=review_only)[1] if mode == 'prepaid'
            else deferred_source_event(*args, review_only=review_only))


def apply(state, event, mode):
    result = deepcopy(state)
    result['engine'].setdefault('facts', {})
    result['engine'].setdefault('obligations', {})
    issues = []
    writer = apply_events if mode == 'prepaid' else apply_deferred_events
    writer(result, [event], _instant(START), _instant(NOW), lambda code, oid: issues.append((code, oid)))
    return result, issues


def fee_fixture(mode='prepaid', *, marker=None):
    data, state = fixture()
    data.setdefault('store_delivery_assignments', [])
    without_label(data)
    if mode == 'deferred':
        state['courier_tariffs'][0]['payment_mode'] = 'deferred'
        state['courier_tariffs'][0].pop('cash_account')
    if marker is not None:
        admitted(state, marker)
    return data, state


@pytest.mark.parametrize('mode', ['prepaid', 'deferred'])
def test_preview_is_read_only_and_missing_activation_never_posts(mode):
    data, state = fee_fixture(mode)
    before = deepcopy((data, state))
    event = candidate(data, state, mode=mode)
    assert (data, state) == before
    assert needs_review(state, event)
    with pytest.raises(CompletionReviewRequired) as held:
        candidate(data, state, mode=mode, review_only=False)
    assert held.value.candidate == event
    assert held.value.review == {'requires_review': True, 'issues': []}
    assert review(state, event)['requires_review'] is True
    result, issues = apply(state, event, mode)
    assert issues == [('operational_shipping_completion_history_review_required', '100')]
    assert not result['movements'] and not result['engine']['obligations']


@pytest.mark.parametrize('mode', ['prepaid', 'deferred'])
def test_exact_owner_review_releases_one_fee_and_keeps_opening_unchanged(mode):
    data, state = fee_fixture(mode)
    state['openings'] = {'opening': {'id': 'opening', 'name': 'Aramex', 'party_type': 'courier',
        'party_id': 'aramex', 'currency': 'SAR', 'direction': 'for_party', 'amount': '70.00'}}
    event = candidate(data, state, mode=mode)
    check = review(state, event)
    saved = approval_record(state, event, actor='owner', approved_at=NOW, evidence_ref='reviewed opening',
                            expected_preview_hash=check['preview_hash'])
    state[APPROVALS] = {digest('10'): saved}
    assert candidate(data, state, mode=mode, review_only=False) == event
    result, issues = apply(state, event, mode)
    assert not issues
    assert result['openings'] == state['openings']
    charge_id = 'shipping-prepaid:100' if mode == 'prepaid' else 'shipping-completed:100'
    assert result['engine']['obligations'][charge_id]['confirmed'] == '34.50'
    assert len(result['movements']) == (1 if mode == 'prepaid' else 0)
    again, issues = apply(result, event, mode)
    assert not issues and again == result


@pytest.mark.parametrize('change', ['opening', 'payout', 'candidate', 'owner'])
def test_any_relevant_change_invalidates_historical_approval(change):
    data, state = fee_fixture('deferred')
    event = candidate(data, state, mode='deferred')
    state[APPROVALS] = {digest('10'): approval_record(state, event, actor='owner', approved_at=NOW,
        evidence_ref='reviewed history', expected_preview_hash=review(state, event)['preview_hash'])}
    if change == 'opening':
        state['openings']['later'] = {'id': 'later', 'party_type': 'courier', 'party_id': 'aramex',
            'currency': 'SAR', 'direction': 'for_party', 'amount': '10.00'}
    elif change == 'payout':
        state['movements'].append({'id': 'later', 'party_type': 'courier', 'party_id': 'aramex',
            'currency': 'SAR', 'direction': 'outgoing', 'kind': 'payment', 'amount': '10.00', 'allocations': []})
    elif change == 'candidate':
        event = {**event, 'source_carrier_key': 'changed'}
    else:
        state[APPROVALS][digest('10')]['approved_by'] = 'other'
    with pytest.raises(CompletionReviewRequired):
        require_admission(state, event)


def test_activation_is_stable_and_never_changes_source_dates():
    _, state = fee_fixture('deferred')
    start = state['started_at']
    initialize_admission(state, NOW)
    first = deepcopy(state['engine'][MARKER])
    initialize_admission(state, '2026-10-08T12:00:00+00:00')
    assert state['engine'][MARKER] == first
    assert state['started_at'] == start
    assert first['activated_at'] == NOW


@pytest.mark.parametrize('marker', [None, COMPLETED, NOW])
def test_first_reconciliation_and_boundary_are_not_backfill_permission(marker):
    data, state = fee_fixture('deferred', marker=marker)
    event = candidate(data, state, mode='deferred')
    assert needs_review(state, event)
    initialize_admission(state, NOW)
    assert needs_review(state, event)


@pytest.mark.parametrize('mode', ['prepaid', 'deferred'])
def test_after_saved_activation_only_base_fee_is_recognized(mode):
    data, state = fee_fixture(mode, marker=START)
    event = candidate(data, state, mode=mode, review_only=False)
    result, issues = apply(state, event, mode)
    assert not issues
    assert all(row['kind'] == 'shipping' for row in result['engine']['obligations'].values())
    assert all(not key.startswith(('cod:', 'cod_collection:', 'delivery_event:')) for key in result['engine']['facts'])
    if mode == 'deferred':
        assert not result['movements']
        assert result['engine']['obligations']['shipping-completed:100']['business_date'] == '2026-10-06'


@pytest.mark.parametrize('change', ['carrier', 'missing_time', 'before_start', 'overlap', 'assignment', 'native_fee'])
def test_deferred_completion_never_guesses_or_replaces_existing_fee(change):
    data, state = fee_fixture('deferred', marker=START)
    raw = data['unified_orders'][0]['raw_by_source']['salla_direct']
    if change == 'carrier':
        raw['shipping']['company_code'] = 'another'
    elif change == 'missing_time':
        raw.pop('status_changed_at')
    elif change == 'before_start':
        raw['status_changed_at'] = '2026-10-04T12:00:00+00:00'
    elif change == 'overlap':
        state['courier_tariffs'].append({**state['courier_tariffs'][0], 'version_id': 'overlap'})
    elif change == 'assignment':
        data['store_delivery_assignments'] = [{'user_id': 'owner', 'order_id': '100', 'order_number': '10',
            'id': 'assignment', 'driver_id': 'driver', 'active': True, 'status': 'assigned'}]
    else:
        state['engine']['facts'] = {'shipping:100': {'cost': '34.50', 'id': 'aramex'}}
    if change in {'before_start', 'assignment', 'native_fee'}:
        assert candidate(data, state, mode='deferred', review_only=False) is None
    else:
        with pytest.raises(ValueError):
            candidate(data, state, mode='deferred', review_only=False)


def test_a_recorded_and_settled_cod_commission_does_not_block_a_separate_base_fee():
    data, state = fee_fixture('deferred', marker=START)
    key = 'shipping:100:cod_commission'
    state['engine']['facts'] = {'shipping:100': {'cost': '2.00', 'component_mode': True, 'party_type': 'courier',
        'id': 'aramex'}, key: {'cost': '2.00', 'currency': 'SAR'}}
    commission = {'id': key, 'kind': 'shipping', 'party_type': 'courier', 'party_id': 'aramex',
        'currency': 'SAR', 'direction': 'payable', 'confirmed': '2.00', 'expected': '0.00', 'fee_component': 'cod_commission'}
    state['engine']['obligations'] = {key: commission}
    state['movements'] = [{'id': 'paid-commission', 'party_type': 'courier', 'party_id': 'aramex',
        'currency': 'SAR', 'direction': 'outgoing', 'kind': 'payment', 'amount': '2.00', 'order_id': '100',
        'allocations': [{'obligation_id': key, 'amount': '2.00'}]}]
    event = candidate(data, state, mode='deferred', review_only=False)
    result, issues = apply(state, event, 'deferred')
    assert not issues
    assert result['engine']['obligations'][key] == commission
    assert result['engine']['obligations']['shipping-completed:100']['confirmed'] == '34.50'
    assert result['movements'] == state['movements']


@pytest.mark.parametrize('mode', ['prepaid', 'deferred'])
def test_legacy_historical_delivery_review_cannot_authorize_a_second_completed_base(mode):
    from operational_historical_shipping_fee import APPROVALS as OLD_APPROVALS, apply_reviewed_fees, review as old_review
    data, state = fee_fixture(mode, marker=START)
    event = candidate(data, state, mode=mode, review_only=False)
    recorded, issues = apply(state, event, mode)
    assert not issues
    legacy = {'order_id': '100', 'order_number': '10', 'party_type': 'courier', 'party_id': 'aramex',
              'currency': 'SAR', 'amount': '34.50', 'delivered_at': NOW, 'source_hash': 'source',
              'delivery_hash': 'delivery', 'obligation_id': 'shipping:100:base_shipping'}
    assert old_review(recorded, legacy)[2]
    recorded[OLD_APPROVALS] = {digest('10'): {'candidate': legacy, 'approved_by': 'owner',
        'opening_payout_hash': digest([[], []])}}
    before = deepcopy(recorded)
    errors = []
    def forbidden(*args, **kwargs):
        pytest.fail('duplicate historical base was posted')
    resolved = apply_reviewed_fees(recorded, {'delivery_event_coverage': {'version': 1},
        'historical_fee_candidates': [legacy]}, forbidden, forbidden, lambda *args: errors.append(args))
    assert not resolved and errors
    assert recorded == before
