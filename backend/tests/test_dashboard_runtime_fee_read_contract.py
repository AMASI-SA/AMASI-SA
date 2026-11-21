"""Fee authority adds only two precisely scoped empty reads to the frozen gate."""
import copy
import hashlib
import json

import pytest

from dashboard_runtime_gate_plugin import (
    PAYMENT_FEE_POLICIES, _expected_payment_fee_commands, _payment_fee_command,
    _frozen_financial_cohort, assert_runtime_read_contract,
)
from test_dashboard_runtime_authority_read_contract import evidence_pair


def fee_pair():
    runtime, baseline = evidence_pair()
    runtime['json'] = baseline['json'] = json.dumps({'total': 220, 'profit': 180, 'payment_breakdown': []})
    audit = runtime['cost_authority_audit']
    audit['payment_fee_cohorts'] = [{'user_id': 'owner'}]
    audit['payment_fee_commands'] = _expected_payment_fee_commands(audit, json.loads(baseline['json']))
    runtime['commands']['find'] += 2
    runtime['collections'][PAYMENT_FEE_POLICIES] = 0
    return runtime, baseline


def test_empty_fee_policy_reads_preserve_existing_counts_and_money():
    runtime, baseline = fee_pair()
    original = copy.deepcopy((runtime, baseline))
    assert_runtime_read_contract(runtime, baseline)
    assert (runtime, baseline) == original


@pytest.mark.parametrize('defect', [
    'owner', 'cohort_owner', 'second_cohort', 'mode_id', 'projection', 'extra_read',
    'missing_read', 'nonempty_policy', 'settings_read', 'missing_empty_collection',
    'limit', 'single_batch', 'sort', 'write', 'database', 'command_database',
])
def test_fee_authority_rejects_unreviewed_reads(defect):
    runtime, baseline = fee_pair()
    audit = runtime['cost_authority_audit']
    reads = audit['payment_fee_commands']
    if defect == 'owner':
        reads[0]['filter']['user_id'] = 'foreign'
    elif defect == 'cohort_owner':
        audit['payment_fee_cohorts'][0]['user_id'] = 'foreign'
    elif defect == 'second_cohort':
        audit['payment_fee_cohorts'].append({'user_id': 'owner'})
    elif defect == 'mode_id':
        reads[1]['filter']['_id'] = 'unscoped'
    elif defect == 'projection':
        reads[0]['projection']['private_data'] = 1
    elif defect == 'extra_read':
        reads.append(copy.deepcopy(reads[0])); runtime['commands']['find'] += 1
    elif defect == 'missing_read':
        reads.pop(); runtime['commands']['find'] -= 1
    elif defect == 'nonempty_policy':
        runtime['collections'][PAYMENT_FEE_POLICIES] = 1; runtime['documents'] += 1
    elif defect == 'settings_read':
        runtime['commands']['find'] += 1; runtime['collections']['settings'] += 1; runtime['documents'] += 1
    elif defect == 'missing_empty_collection':
        del runtime['collections'][PAYMENT_FEE_POLICIES]
    elif defect == 'limit':
        reads[1]['limit'] = 100
    elif defect == 'single_batch':
        reads[1]['single_batch'] = False
    elif defect == 'sort':
        reads[0]['unreviewed_fields'] = ['sort']
    elif defect == 'write':
        reads[0]['operation'] = 'update'
    elif defect in ('database', 'command_database'):
        reads[0][defect] = 'foreign_database'
    with pytest.raises(AssertionError):
        assert_runtime_read_contract(runtime, baseline)


def test_fee_command_capture_retains_single_batch_and_unreviewed_fields():
    row = _payment_fee_command('find', {
        'find': PAYMENT_FEE_POLICIES, 'filter': {'user_id': 'owner'},
        'singleBatch': True, 'limit': 1, 'sort': {'created_at': 1}, '$db': 'fixture',
    }, database_name='fixture')
    assert row['single_batch'] is True and row['limit'] == 1
    assert row['unreviewed_fields'] == ['sort']


def test_no_placeholder_financial_digest_is_bound_to_frozen_owner_and_order_identity():
    def cohort(case, count):
        return _frozen_financial_cohort({'totals': {'total_orders': count}},
            'tests/test_dashboard_b_index_cooperative_correctness.py::'
            f'test_dashboard_mixed_permissions_and_tenants[{case}]::request-0')
    actual = cohort('other_owner', 1)
    decisions = [['fixture-other', '', '1000000', '', False, False, '', False]]
    assert actual == {
        'reader_version': 'financial-cohort-proof-history-v1', 'total_orders': 1,
        'financial_eligible_orders': 1, 'excluded_unpaid_orders': 0,
        'review_orders': 0, 'complete': True, 'review_reason_counts': {},
        'eligibility_digest': hashlib.sha256(json.dumps(decisions, separators=(',', ':')).encode()).hexdigest(),
    }
    assert actual['eligibility_digest'] != cohort('prior_month', 1)['eligibility_digest']
    assert cohort('first_day_inclusive', 1)['eligibility_digest'] != cohort('last_day_inclusive', 1)['eligibility_digest']
    with pytest.raises(AssertionError):
        cohort('current_month_mixed', 37)
