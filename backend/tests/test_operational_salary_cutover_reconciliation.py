"""Reviewed cutoff rounding has independent debt, never a rewritten salary day."""
from copy import deepcopy
from decimal import Decimal
from calendar import monthrange

import pytest

from operational_balance_engine import reconcile
from operational_salary_start import preview_from_sources
from test_operational_balance_salary_start import state, employee, policy

STAMP = '2026-11-08T12:00:00+03:00'


def recorded():
    emp = employee('1000')
    value = reconcile(state(), {'employees': [emp]}, STAMP)
    return value, emp


def reviewed(value, emp):
    check = preview_from_sources(value, [emp], [], '2026-11-01', STAMP)
    assert check['can_approve'] is False
    assert check['can_approve_with_rounding_review'] is True
    policy(value, approved=STAMP)
    value['salary_accrual_policy']['rounding_review'] = check['rounding_review']
    from operational_salary_start import fingerprint
    p = value['salary_accrual_policy']
    p['fingerprint'] = fingerprint({k: v for k, v in p.items() if k != 'fingerprint'})
    return check


def test_review_exposes_signed_gross_and_net_impact_without_mutating():
    value, emp = recorded()
    before = deepcopy(value)
    check = preview_from_sources(value, [emp], [], '2026-11-01', STAMP)
    assert check['rounding_review']['totals']['SAR'] == {'increase': '0.03', 'decrease': '0.02', 'net': '0.01'}
    assert check['additional_amounts'] == {'SAR': '33.33'}
    assert value == before


def test_reviewed_adjustments_preserve_paid_days_and_retry_exactly_once():
    value, emp = recorded()
    key = 'salary:employee:2026-11-02'
    value['movements'] = [{'id': 'paid-day', 'currency': 'SAR', 'amount': '33.33',
                          'allocations': [{'obligation_id': key, 'amount': '33.33'}]}]
    before = deepcopy(value)
    reviewed(value, emp)
    result = reconcile(value, {'employees': [emp]}, STAMP)
    for key, row in before['engine']['salary_days'].items():
        assert result['engine']['salary_days'][key] == row
        assert result['engine']['obligations'][key] == before['engine']['obligations'][key]
    assert result['movements'] == before['movements'] and result['openings'] == before['openings']
    fixes = [r for r in result['engine']['obligations'].values() if r['kind'] == 'salary_cutover_adjustment']
    assert len(fixes) == 5
    assert sum(Decimal(r['confirmed']) * (1 if r['direction'] == 'payable' else -1) for r in fixes) == Decimal('.01')
    assert not result['engine'].get('salary_conflicts')
    assert reconcile(result, {'employees': [emp]}, STAMP) == result
    from operational_profit_payroll import recorded_salary_subtotals
    result['engine']['as_of'] = STAMP
    subtotal = recorded_salary_subtotals(result)['SAR']
    assert subtotal['amount'] == '266.67'
    assert subtotal['employee_days'] == 8
    assert subtotal['cutover_adjustment_amount'] == '0.01'
    final = reconcile(result, {'employees': [emp]}, '2026-11-30T12:00:00+03:00')
    total = sum(Decimal(r['confirmed']) * (1 if r['direction'] == 'payable' else -1)
                for r in final['engine']['obligations'].values() if r['kind'] in {'salary', 'salary_cutover_adjustment'})
    assert total == Decimal('1000.00')


def test_incomplete_recorded_obligation_blocks_review_and_bogus_adjustment_is_not_rewritten():
    value, emp = recorded()
    key = 'salary:employee:2026-11-02'
    damaged = deepcopy(value)
    damaged['engine']['obligations'].pop(key)
    check = preview_from_sources(damaged, [emp], [], '2026-11-01', STAMP)
    assert not check['can_approve_with_rounding_review']
    reviewed(value, emp)
    result = reconcile(value, {'employees': [emp]}, STAMP)
    adjustment_key = next(k for k in result['engine']['obligations'] if k.startswith('salary_cutover_adjustment:'))
    result['engine']['obligations'][adjustment_key]['confirmed'] = '10.00'
    retry = reconcile(result, {'employees': [emp]}, STAMP)
    retry['engine']['as_of'] = STAMP
    assert retry['engine']['obligations'][adjustment_key]['confirmed'] == '10.00'
    assert retry['engine']['salary_conflicts']
    from operational_profit_payroll import recorded_salary_subtotals
    assert 'SAR' not in recorded_salary_subtotals(retry)


def test_contract_change_and_incomplete_sources_cannot_use_rounding_review():
    value, emp = recorded()
    check = preview_from_sources(value, [emp], [{'code': 'employee_salary_contract_incomplete'}], '2026-11-01', STAMP)
    assert check['can_approve_with_rounding_review'] is False
    changed = deepcopy(emp)
    changed['salary_revisions'][0]['salary'] = '1000.01'
    check = preview_from_sources(value, [changed], [], '2026-11-01', STAMP)
    assert check['can_approve_with_rounding_review'] is False


def test_new_source_change_after_review_is_still_a_conflict():
    value, emp = recorded()
    reviewed(value, emp)
    result = reconcile(value, {'employees': [emp]}, STAMP)
    changed = deepcopy(emp)
    changed['salary_revisions'][0]['salary'] = '1200'
    altered = reconcile(result, {'employees': [changed]}, STAMP)
    assert altered['engine']['salary_conflicts']
    for key, row in result['engine']['obligations'].items():
        if row['kind'] == 'salary_cutover_adjustment':
            assert altered['engine']['obligations'][key] == row


@pytest.mark.parametrize('year,month', [(2026, 2), (2028, 2), (2026, 11), (2026, 12)])
@pytest.mark.parametrize('salary', ['1000', '1000.01', '2000.99'])
def test_month_total_conserves_salary_after_explicit_review(year, month, salary):
    first = f'{year}-{month:02d}-01'
    stamp = f'{year}-{month:02d}-08T12:00:00+03:00'
    emp = employee(salary)
    value = reconcile(state(first + 'T00:30:01+03:00'), {'employees': [emp]}, stamp)
    original = deepcopy(value['engine']['salary_days'])
    check = preview_from_sources(value, [emp], [], first, stamp)
    policy(value, first=first, approved=stamp)
    if check['rounding_review']:
        value['salary_accrual_policy']['rounding_review'] = check['rounding_review']
    from operational_salary_start import fingerprint
    p = value['salary_accrual_policy']
    p['fingerprint'] = fingerprint({k: v for k, v in p.items() if k != 'fingerprint'})
    for day in range(8, monthrange(year, month)[1]+1):
        value = reconcile(value, {'employees': [emp]}, f'{year}-{month:02d}-{day:02d}T12:00:00+03:00')
    assert all(value['engine']['salary_days'][k] == v for k, v in original.items())
    total = sum(Decimal(r['confirmed']) * (1 if r['direction'] == 'payable' else -1)
        for r in value['engine']['obligations'].values() if r['kind'] in {'salary', 'salary_cutover_adjustment'})
    assert total == Decimal(salary)
    assert not value['engine'].get('salary_conflicts')
