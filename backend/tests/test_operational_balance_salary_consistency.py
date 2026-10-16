from copy import deepcopy
from datetime import date
from decimal import Decimal
import pytest
from operational_balance_engine import reconcile
from operational_balance_sources import _employees
from employee_payroll_status import contract_salary_row, salary_accrual_for_period

START = '2026-10-05T09:00:00+00:00'
NOW = '2026-10-08T12:00:00+00:00'


def source(amount='3100'):
    employee = {'id':'e', 'status':'active', 'hire_date':'2026-10-07'}
    contract = {'employee_id':'e', 'effective_from':'2026-10-07', 'monthly_amount':amount, 'currency':'SAR'}
    out = {'employees':[], 'issues':[]}
    _employees({'mezan_employees_v2':[employee], 'mezan_employee_salary_contracts_v2':[contract]}, out)
    return employee, contract, out


@pytest.mark.parametrize('amount', ['3100', '3000.01'])
def test_start_and_rounding_match_canonical_partial_month(amount):
    employee, contract, sources = source(amount)
    result = reconcile({'status':'active', 'started_at':START}, sources, NOW)
    expected = salary_accrual_for_period(contract_salary_row(contract, employee), '2026-10',
        through=date(2026,10,8), not_before=date(2026,10,6))
    assert sum(Decimal(r['amount']) for r in result['engine']['salary_days'].values()) == Decimal(str(expected))
    assert set(result['engine']['salary_days']) == {'salary:e:2026-10-08'}


@pytest.mark.parametrize('change', ['amount','leave','hire','currency','missing'])
def test_changed_source_preserves_days_and_allocations_and_audits_once(change):
    _, _, sources = source()
    original = reconcile({'status':'active', 'started_at':START}, sources, NOW)
    original['movements'] = [{'allocations':[{'obligation_id':'salary:e:2026-10-08', 'amount':'100.00'}]}]
    altered = deepcopy(sources)
    employee = altered['employees'][0]
    if change == 'amount': employee['salary_revisions'][0]['salary'] = '6200'
    if change == 'leave': employee['payroll_suspension_periods'] = [{'started_on':'2026-10-08'}]
    if change == 'hire': employee['accrual_start_date'] = '2026-10-09'
    if change == 'currency': employee['currency'] = 'USD'
    if change == 'missing': altered['employees'] = []
    result = reconcile(original, altered, NOW)
    assert result['engine']['salary_days'] == original['engine']['salary_days']
    assert result['engine']['obligations'] == original['engine']['obligations']
    assert result['movements'] == original['movements']
    assert {'code':'salary_source_changed', 'source_id':'salary:e:2026-10-08'} in result['engine']['issues']
    assert reconcile(result, altered, NOW) == result


def test_future_revision_end_metadata_does_not_invalidate_saved_days():
    _, _, sources = source()
    original = reconcile({'status':'active', 'started_at':START}, sources, NOW)
    # Legacy saved days have currency only in their obligation.
    original['engine']['salary_days']['salary:e:2026-10-08'].pop('currency')
    altered = deepcopy(sources)
    revisions = altered['employees'][0]['salary_revisions']
    revisions[0]['effective_to'] = '2026-10-16'
    revisions.append({'effective_from':'2026-10-17', 'salary':'6200', 'status':'active'})
    result = reconcile(original, altered, NOW)
    assert result['engine']['salary_days'] == original['engine']['salary_days']
    assert not result['engine'].get('salary_conflicts')
    assert not any(i['code'] == 'salary_source_changed' for i in result['engine']['issues'])


def legacy_rounding_state():
    # Frozen production c03c9df22 calendar-day deltas for 3000/31, Oct 6-8.
    employee = {'id': 'e', 'currency': 'SAR', 'accrual_start_date': '2026-10-01',
                'salary_revisions': [{'effective_from': '2026-10-01', 'salary': '3000', 'status': 'active'}]}
    sources = {'employees': [employee]}
    state = reconcile({'status': 'active', 'started_at': START}, sources, NOW)
    for day, amount in [('06', '96.78'), ('07', '96.77'), ('08', '96.77')]:
        key = 'salary:e:2026-10-' + day
        state['engine']['salary_days'][key] = {'amount': amount, 'revision': deepcopy(employee['salary_revisions'][0])}
        state['engine']['obligations'][key]['confirmed'] = amount
    return state, sources


def test_upgrade_preserves_legacy_rounding_without_false_salary_conflicts():
    state, sources = legacy_rounding_state()
    result = reconcile(state, sources, NOW)
    assert result['engine']['salary_days'] == state['engine']['salary_days']
    assert result['engine']['obligations'] == state['engine']['obligations']
    assert not result['engine'].get('salary_conflicts')
    assert not any(row['action'] == 'salary_source_changed' for row in result['engine']['audit'])


@pytest.mark.parametrize('change', ['amount', 'leave', 'currency', 'corrupt_amount', 'modern'])
def test_legacy_rounding_compatibility_does_not_hide_real_changes(change):
    state, sources = legacy_rounding_state()
    employee = sources['employees'][0]
    key = 'salary:e:2026-10-06'
    if change == 'amount': employee['salary_revisions'][0]['salary'] = '6200'
    if change == 'leave': employee['payroll_suspension_periods'] = [{'started_on': '2026-10-06'}]
    if change == 'currency': employee['currency'] = 'USD'
    if change == 'corrupt_amount': state['engine']['salary_days'][key]['amount'] = '96.79'
    if change == 'modern': state['engine']['salary_days'][key]['currency'] = 'SAR'
    result = reconcile(state, sources, NOW)
    assert key in result['engine']['salary_conflicts']
    assert result['engine']['salary_days'] == state['engine']['salary_days']


def test_legacy_rounding_remains_compatible_when_future_revision_added():
    state, sources = legacy_rounding_state()
    revisions = sources['employees'][0]['salary_revisions']
    revisions[0]['effective_to'] = '2026-10-16'
    revisions.append({'effective_from': '2026-10-17', 'salary': '6200', 'status': 'active'})
    result = reconcile(state, sources, NOW)
    assert not result['engine'].get('salary_conflicts')
    assert result['engine']['salary_days'] == state['engine']['salary_days']
