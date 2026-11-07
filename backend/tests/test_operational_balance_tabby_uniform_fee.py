"""Synthetic, read-only regression coverage; no merchant tariff is embedded."""
from copy import deepcopy
from datetime import date
from itertools import permutations

import pytest

from operational_provider_fees import provider_projection

DAY = date(2026, 6, 10)


def policy(identity='all-plans', plan=None, **changes):
    return dict({
        'id': identity, 'provider': 'tabby', 'currency': 'SAR', 'status': 'active',
        'effective_from': '2026-06-01', 'event_type': 'capture',
        'fee_model_version': 2, 'plan_id': plan,
        'confirmed_by': 'synthetic-owner', 'confirmed_at': '2026-06-01T00:00:00Z',
        'evidence': 'synthetic owner-approved universal tariff',
        'components': [{
            'id': 'capture', 'percentage': '2.5', 'fixed_amount': '0.50',
            'vat_treatment': 'exclusive', 'vat_rate': '17',
            'vat_rounding': 'half_even', 'vat_basis': 'rounded_fee',
            'refund_fee_treatment': 'retain', 'cancellation_fee_treatment': 'recalculate',
        }],
    }, **changes)


def policies():
    return [policy(), policy('three-policy', 'three'), policy('four-policy', 'four')]


def payment(**changes):
    return dict({'provider': 'tabby', 'currency': 'SAR', 'gross': '100.00',
                 'plan_id': None, 'refunds': []}, **changes)


def project(source=None, terms=None, **kwargs):
    return provider_projection(payment() if source is None else source,
                               policies() if terms is None else terms, DAY, **kwargs)


def test_universal_approved_tariff_unblocks_identical_plan_rows_without_inventing_plan():
    source, terms = payment(), policies()
    before = deepcopy((source, terms))
    result = project(source, terms)
    assert result['estimated_fees'] == '3.51'
    assert result['expected_receivable'] == '96.49'
    assert result['plan_id'] is None
    assert result['policy_id'] == 'all-plans'
    assert result['fee_recognition'] == 'estimated'
    assert result['fee_policy_resolution'] == 'approved_uniform_plan_terms'
    assert result['equivalent_policy_ids'] == ['all-plans', 'four-policy', 'three-policy']
    assert (source, terms) == before


def test_candidate_order_does_not_change_amount_or_evidence():
    expected = project()
    for rows in permutations(policies()):
        assert project(terms=list(rows)) == expected


@pytest.mark.parametrize('plan', ['three', 'four'])
def test_known_plan_keeps_its_specific_policy_and_original_result_shape(plan):
    result = project(payment(plan_id=plan))
    assert result['policy_id'] == plan + '-policy'
    assert result['plan_id'] == plan
    assert 'fee_policy_resolution' not in result
    assert result['estimated_fees'] == '3.51'


def test_missing_plan_without_explicit_universal_approval_remains_blocked():
    with pytest.raises(ValueError, match='provider_payment_plan_required'):
        project(terms=policies()[1:])


def test_single_generic_policy_keeps_original_behavior():
    result = project(terms=[policy()])
    assert result['estimated_fees'] == '3.51'
    assert 'fee_policy_resolution' not in result


@pytest.mark.parametrize('field,value', [
    ('percentage', '2.6'), ('fixed_amount', '0.60'), ('vat_rate', '18'),
    ('vat_rounding', 'half_up'), ('vat_basis', 'unrounded_fee'),
    ('vat_treatment', 'inclusive'), ('refund_fee_treatment', 'recalculate'),
    ('cancellation_fee_treatment', 'retain'),
])
def test_equal_percentage_alone_is_not_enough(field, value):
    rows = policies()
    rows[1]['components'][0][field] = value
    with pytest.raises(ValueError, match='provider_payment_plan_required'):
        project(terms=rows)


@pytest.mark.parametrize('field,value', [
    ('evidence', ''), ('confirmed_by', ''), ('confirmed_at', ''),
    ('confirmed_at', '2026-06-01'),
])
def test_every_candidate_must_have_valid_approval_not_just_the_generic(field, value):
    rows = policies()
    rows[2][field] = value
    with pytest.raises(ValueError, match='provider_fee_policy_evidence_required'):
        project(terms=rows)


def test_conflicting_plan_evidence_is_never_ignored():
    with pytest.raises(ValueError, match='provider_payment_plan_conflict'):
        project(payment(fee_plan_issue='conflicting-source-plans'))


def test_explicit_unconfigured_plan_is_never_replaced_by_generic():
    with pytest.raises(ValueError, match='provider_fee_policy_incomplete_or_ambiguous'):
        project(payment(plan_id='unconfigured-long-plan'))


def test_differently_priced_active_plan_is_not_silently_excluded():
    rows = policies() + [policy('long-policy', 'long')]
    rows[-1]['components'][0]['percentage'] = '9.9'
    with pytest.raises(ValueError, match='provider_payment_plan_required'):
        project(terms=rows)


@pytest.mark.parametrize('change', [
    {'status': 'inactive'}, {'effective_from': '2026-07-01'},
    {'effective_to': '2026-05-31'}, {'provider': 'tamara'},
    {'currency': 'USD'}, {'event_type': 'payout'},
])
def test_inapplicable_policies_do_not_pollute_capture_scope(change):
    other = policy('other-policy', 'other', **change)
    other['components'][0]['percentage'] = '9.9'
    assert project(terms=policies() + [other]) == project()


@pytest.mark.parametrize('extra', [
    policy('second-generic'), policy('duplicate-plan', 'three'),
    policy('three-policy', 'different-plan'),
])
def test_duplicate_or_ambiguous_contracts_remain_blocked(extra):
    with pytest.raises(ValueError, match='provider_payment_plan_required'):
        project(terms=policies() + [extra])


def test_legacy_aggregate_policies_do_not_gain_a_new_fallback():
    rows = policies()
    for row in rows:
        row['fee_model_version'] = 1
    with pytest.raises(ValueError, match='provider_payment_plan_required'):
        project(terms=rows)


def test_other_providers_are_unchanged():
    rows = policies()
    for row in rows:
        row['provider'] = 'salla'
    with pytest.raises(ValueError, match='provider_payment_plan_required'):
        project(payment(provider='salla'), rows)


def test_missing_payment_method_keeps_its_existing_guard():
    rows = policies()
    for row in rows:
        row['payment_method'] = 'specified-rail'
    with pytest.raises(ValueError, match='provider_payment_method_required_for_fee'):
        project(terms=rows)


def test_refund_is_counted_once_and_retained_fee_not_reversed():
    event = {'id': 'refund', 'amount': '10.00', 'status': 'executed'}
    result = project(payment(refunds=[event, deepcopy(event)]))
    assert (result['refunded'], result['estimated_fees'], result['expected_receivable']) == ('10.00', '3.51', '86.49')


def test_refund_processing_still_requires_its_own_terms():
    source = payment(refunds=[{'id': 'refund', 'amount': '10.00', 'status': 'executed',
                               'refunded_at': '2026-06-10T12:00:00Z'}])
    rows = policies() + [policy('refund-three', 'three', event_type='refund')]
    with pytest.raises(ValueError, match='provider_payment_plan_required'):
        project(source, rows, as_of_date=DAY)


def test_unknown_refund_terms_do_not_become_zero():
    rows = policies()
    for row in rows:
        row['components'][0].pop('refund_fee_treatment')
    source = payment(refunds=[{'id': 'refund', 'amount': '10.00', 'status': 'executed'}])
    with pytest.raises(ValueError, match='refund_fee_treatment_incomplete'):
        project(source, rows)


def test_source_window_before_approval_effective_period_is_not_backfilled():
    with pytest.raises(ValueError, match='provider_fee_policy_incomplete_or_ambiguous'):
        project(earliest_date=date(2026, 5, 31))


def test_contract_boundary_is_not_hidden_when_amounts_coincide():
    rows = policies()
    earlier = []
    for row in rows:
        prev = deepcopy(row)
        prev.update(id='old-' + row['id'], effective_from='2026-05-01', effective_to='2026-05-31')
        earlier.append(prev)
    with pytest.raises(ValueError, match='provider_fee_transaction_date_required'):
        project(terms=rows + earlier, earliest_date=date(2026, 5, 31))


def test_unknown_future_contract_fields_are_not_discarded_by_equivalence():
    rows = policies()
    rows[1]['future_contract_term'] = 'different'
    with pytest.raises(ValueError, match='provider_payment_plan_required'):
        project(terms=rows)
