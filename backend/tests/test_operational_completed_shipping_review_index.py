"""Batch reads stay bounded without weakening exact historical approval checks."""
from copy import deepcopy

import pytest

from operational_balance_store import digest
from operational_completed_shipping_admission import (
    APPROVALS, build_review_index, recorded_base_fee, review, approval_record,
)
from operational_delivery_evidence import _instant
from operational_prepaid_shipping import apply_events
from test_operational_balance_delivery_cutover import START, NOW
from test_operational_completed_shipping_admission import candidate, fee_fixture


def ledger(count):
    _, state = fee_fixture('deferred')
    state['engine']['facts'] = {}
    state['engine']['obligations'] = {}
    for number in range(count):
        oid = 'old-' + str(number)
        key = 'shipping:' + oid
        state['engine']['obligations'][key] = {
            'id': key, 'kind': 'shipping', 'party_type': 'courier', 'party_id': 'aramex',
            'currency': 'SAR', 'direction': 'payable', 'order_id': oid, 'confirmed': '10.00',
        }
        state['movements'].append({
            'id': 'paid-' + str(number), 'party_type': 'courier', 'party_id': 'aramex',
            'currency': 'SAR', 'direction': 'outgoing', 'kind': 'payment', 'amount': '10.00',
            'allocations': [{'obligation_id': key, 'amount': '10.00'}],
        })
    return state


def test_indexed_review_matches_a_fresh_snapshot_and_cannot_cross_state_objects():
    data, _ = fee_fixture('deferred')
    state = ledger(3)
    event = candidate(data, state, mode='deferred')
    before = deepcopy(state)
    index = build_review_index(state)
    assert review(state, event, review_index=index) == review(state, event)
    assert not review(state, event, review_index=index)['issues']
    assert state == before
    with pytest.raises(ValueError, match='review_snapshot_changed'):
        review(deepcopy(state), event, review_index=index)


@pytest.mark.parametrize('fact', [
    {'shipping:100': {'cost': '10.00'}},
    {'shipping:100:base_shipping': {'cost': '10.00', 'id': 'aramex'}},
    {'completed_shipping:100': {'order_id': '100'}},
    {'prepaid_shipping:100': {'order_id': '100'}},
    {'historical_base_fee:100': {'order_id': '100'}},
    {'shipping_fee:100': {'cost': '10.00'}},
    {'shipping:100': {'cost': '2.00', 'component_mode': True},
     'shipping:100:cod_commission': {'cost': '2.00'}},
])
def test_legacy_and_component_facts_keep_the_same_duplicate_guard(fact):
    _, state = fee_fixture('deferred')
    state['engine']['facts'] = fact
    assert recorded_base_fee(state, '100', review_index=build_review_index(state)) == recorded_base_fee(state, '100')


def test_five_thousand_candidates_share_one_ledger_scan_and_party_digest(monkeypatch):
    import operational_completed_shipping_admission as admission

    class ScannedList(list):
        scans = 0

        def __iter__(self):
            self.scans += 1
            return super().__iter__()

    state = ledger(5000)
    state['movements'] = ScannedList(state['movements'])
    party_digests = []
    original_digest = admission.digest

    def measured_digest(value):
        if isinstance(value, list) and len(value) == 2 and all(isinstance(v, list) for v in value):
            party_digests.append(len(value[1]))
        return original_digest(value)

    monkeypatch.setattr(admission, 'digest', measured_digest)
    index = build_review_index(state)
    for number in range(5000):
        event = {'owner_id': 'owner', 'order_id': 'new-' + str(number), 'order_number': str(number),
                 'party_type': 'courier', 'party_id': 'aramex', 'currency': 'SAR'}
        assert not review(state, event, review_index=index)['issues']
    assert state['movements'].scans == 1
    assert party_digests == [5000]


def test_new_prepaid_payment_invalidates_a_second_approval_with_the_old_payout_hash():
    data, state = fee_fixture('prepaid')
    first = candidate(data, state)
    second = {**first, 'order_id': '101', 'order_number': '11'}
    state['engine'].update(facts={}, obligations={})
    state[APPROVALS] = {
        digest(event['order_number']): approval_record(
            state, event, actor='owner', approved_at=NOW, evidence_ref='opening and payout reviewed',
            expected_preview_hash=review(state, event)['preview_hash'])
        for event in (first, second)
    }
    issues = []
    apply_events(state, [first, second], _instant(START), _instant(NOW), lambda *args: issues.append(args))
    assert list(state['engine']['obligations']) == ['shipping-prepaid:100']
    assert len(state['movements']) == 1
    assert issues == [('operational_shipping_completion_history_review_required', '101')]
