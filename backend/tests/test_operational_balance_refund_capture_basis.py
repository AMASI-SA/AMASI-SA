"""No-I/O regressions for preserving proved capture through order cancellation."""
from copy import deepcopy
import pytest
from operational_refund_capture_basis import recorded_capture_for_cancelled_order as restore

CREATED = '2026-11-01T10:00:00+00:00'
CAPTURED = '2026-11-01T10:01:00+00:00'
NOW = '2026-11-02T10:00:00+00:00'


def inputs(provider='tamara'):
    raw = {'status': 'cancelled', 'payment_method': provider + '_installment',
           'payment': {'method': provider, 'status': 'paid', 'reference': 'capture-one',
                       'paid_amount': '300', 'paid_at': CAPTURED, 'plan_id': 'synthetic-plan'}}
    snapshot = {'provider': provider, 'currency': 'SAR', 'gross': '300.00',
                'cancelled': '0', 'plan_id': 'synthetic-plan',
                'captures': [{'id': 'capture-one', 'amount': '300', 'captured_at': CAPTURED}]}
    case = {'order_id': 'order', 'status': 'refunded', 'case_type': 'item_return',
            'refund_source_type': 'provider', 'refund_source_id': provider,
            'refund_reference': 'refund-one', 'amount': '100.00', 'order_total': '300.00',
            'confirmed_by': 'staff', 'refunded_at': NOW, 'payment_snapshot': snapshot}
    state = {'customer_returns': [case], 'engine': {'facts': {
        'capture:capture-one': {'order_id': 'order', 'provider': provider, 'amount': '300.00'},
        'refund:' + provider + ':refund-one': {'order_id': 'order', 'amount': '100.00'}}}}
    return raw, state


def call(raw, state, provider='tamara', total='300'):
    return restore(raw, state, 'order', provider, 'SAR', total, CREATED, NOW)


@pytest.mark.parametrize('provider', ['tamara', 'tabby'])
def test_unchanged_proved_capture_survives_generic_cancelled_status(provider):
    raw, state = inputs(provider); before = deepcopy((raw, state))
    result = call(raw, state, provider)
    assert result['captures'][0]['id'] == 'capture-one'
    assert result['cancelled'] == '0' and result['refunds'] == []
    assert (raw, state) == before
    assert call(raw, state, provider) == result


@pytest.mark.parametrize('field,value', [
    ('reference', 'different'), ('paid_amount', '299'), ('paid_at', CREATED),
    ('paid_at', '2026-11-05T00:00:00+00:00'), ('paid_at', None),
    ('status', 'pending'), ('plan_id', 'different-plan'), ('plan_id', None),
    ('cancelled', '1'), ('cancellations', [{'id': 'void-proof'}]),
    ('refunded_amount', '1'),
])
def test_current_payment_disagreement_does_not_recover_snapshot(field, value):
    raw, state = inputs(); raw['payment'][field] = value
    assert call(raw, state) is None


@pytest.mark.parametrize('field,value', [
    ('status', 'pending'), ('order_id', 'foreign'), ('refund_source_type', 'bank'),
    ('refund_source_id', 'tabby'), ('confirmed_by', ''), ('refund_reference', 'other'),
    ('amount', '101'), ('order_total', '299'), ('refunded_at', '2026-11-05T00:00:00+00:00'),
    ('payment_snapshot', None),
])
def test_unproved_or_foreign_previous_refund_is_not_authority(field, value):
    raw, state = inputs(); state['customer_returns'][0][field] = value
    assert call(raw, state) is None


@pytest.mark.parametrize('mutation', ['no_capture_fact', 'no_refund_fact', 'wrong_capture_party',
                                    'changed_snapshot_capture', 'changed_snapshot_total',
                                    'native_refund', 'pending_payment', 'reduced_total', 'conflicting_prior'])
def test_no_inferred_capture_or_reduced_total_or_unrecorded_refund(mutation):
    raw, state = inputs(); snapshot = state['customer_returns'][0]['payment_snapshot']
    total = '300'
    if mutation == 'no_capture_fact': state['engine']['facts'].pop('capture:capture-one')
    elif mutation == 'no_refund_fact': state['engine']['facts'].pop('refund:tamara:refund-one')
    elif mutation == 'wrong_capture_party': state['engine']['facts']['capture:capture-one']['provider'] = 'tabby'
    elif mutation == 'changed_snapshot_capture': snapshot['captures'][0]['amount'] = '290'
    elif mutation == 'changed_snapshot_total': snapshot['gross'] = '301'
    elif mutation == 'native_refund': raw['payment_actions'] = {'refund_action': {'refunded_transactions': [{'amount': {'amount': 100, 'currency': 'SAR'}}]}}
    elif mutation == 'pending_payment': raw['is_pending_payment'] = True
    elif mutation == 'reduced_total': total = '200'
    elif mutation == 'conflicting_prior':
        other = deepcopy(state['customer_returns'][0]); other['payment_snapshot']['plan_id'] = 'other'
        state['customer_returns'].append(other)
    before = deepcopy((raw, state))
    assert call(raw, state, total=total) is None
    assert (raw, state) == before
