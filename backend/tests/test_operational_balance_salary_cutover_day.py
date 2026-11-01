"""Payroll begins on the operational cutover's Riyadh day, not month day one."""
from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from operational_balance_engine import reconcile
from operational_salary_start import preview_from_sources, select_first_day
from test_operational_balance_salary_start import employee, earned, policy, state


@pytest.mark.parametrize('started, first_day', [
    ('2026-11-15T00:30:01+03:00', '2026-11-15'),
    ('2026-11-15T17:45:00+03:00', '2026-11-15'),
    ('2026-11-15T23:59:59+03:00', '2026-11-15'),
    ('2026-11-14T21:05:00+00:00', '2026-11-15'),
    ('2026-11-14T16:05:00-05:00', '2026-11-15'),
])
def test_cutover_day_is_one_full_daily_accrual_even_when_start_is_not_midnight(started, first_day):
    value = policy(state(started), first=first_day, approved=started)
    before = deepcopy(value)
    source = {'employees': [employee()]}
    assert select_first_day(value, started) == date.fromisoformat(first_day)
    result = reconcile(value, source, started)
    assert earned(result) == Decimal('100.00')
    assert set(result['engine']['salary_days']) == {'salary:employee:' + first_day}
    assert result['openings'] == before['openings']
    assert result['movements'] == before['movements']
    assert value == before
    assert reconcile(result, source, started) == result


def test_midmonth_cutover_preview_does_not_include_earlier_month_days():
    started = '2026-11-15T12:00:00+03:00'
    value = state(started)
    before = deepcopy(value)
    check = preview_from_sources(value, [employee()], [], '2026-11-15', started)
    assert check['can_approve'] and check['new_days'] == 1
    assert check['first_day'] == check['business_date'] == '2026-11-15'
    assert check['additional_amounts'] == {'SAR': '100.00'}
    assert value == before


@pytest.mark.parametrize('wrong_day', ['2026-11-01', '2026-11-14', '2026-11-16', '2026-12-01'])
def test_preview_requires_exact_cutover_day_not_month_first_or_following_day(wrong_day):
    with pytest.raises(ValueError, match='operational_salary_start_must_equal_cutover_day'):
        preview_from_sources(state('2026-11-15T12:00:00+03:00'), [employee()], [],
                             wrong_day, '2026-12-02T12:00:00+03:00')


def test_preview_cannot_accrue_before_the_actual_start_instant_on_the_same_day():
    with pytest.raises(ValueError, match='operational_salary_start_not_active'):
        preview_from_sources(state('2026-11-15T12:00:00+03:00'), [employee()], [],
                             '2026-11-15', '2026-11-15T11:59:59+03:00')


def test_midmonth_start_counts_only_remaining_calendar_days_then_uses_next_month_denominator():
    started = '2026-11-15T12:00:00+03:00'
    value = policy(state(started), first='2026-11-15', approved=started)
    source = {'employees': [employee()]}
    result = reconcile(value, source, '2026-11-30T23:59:59+03:00')
    assert earned(result) == Decimal('1600.00')
    assert len(result['engine']['salary_days']) == 16
    december = reconcile(result, source, '2026-12-01T00:00:00+03:00')
    assert earned(december) == Decimal('1696.77')
    assert december['engine']['salary_days']['salary:employee:2026-12-01']['amount'] == '96.77'
    finished = reconcile(december, source, '2026-12-31T23:59:59+03:00')
    assert earned(finished) == Decimal('4600.00')
    assert len(finished['engine']['salary_days']) == 47
    assert reconcile(finished, source, '2026-12-31T23:59:59+03:00') == finished


@pytest.mark.parametrize('started, first_day, expected', [
    ('2026-02-28T12:00:00+03:00', '2026-02-28', '107.14'),
    ('2028-02-29T12:00:00+03:00', '2028-02-29', '103.45'),
    ('2026-11-30T12:00:00+03:00', '2026-11-30', '100.00'),
    ('2026-12-31T12:00:00+03:00', '2026-12-31', '96.77'),
])
def test_last_day_cutover_earns_only_one_day_using_the_real_month_length(started, first_day, expected):
    result = reconcile(policy(state(started), first=first_day, approved=started),
                       {'employees': [employee()]}, started)
    assert earned(result) == Decimal(expected)
    assert set(result['engine']['salary_days']) == {'salary:employee:' + first_day}


def test_policy_starting_before_midmonth_cutover_is_not_reinterpreted_as_a_valid_policy():
    started = '2026-11-15T12:00:00+03:00'
    value = policy(state(started), first='2026-11-01', approved=started)
    with pytest.raises(ValueError, match='operational_salary_start_policy_invalid'):
        select_first_day(value, started)


def test_later_hire_still_overrides_inclusive_cutover_day():
    started = '2026-11-15T12:00:00+03:00'
    value = policy(state(started), first='2026-11-15', approved=started)
    source = {'employees': [employee(hire_date='2026-11-17')]}
    result = reconcile(value, source, started)
    assert earned(result) == 0
    result = reconcile(result, source, '2026-11-17T00:00:00+03:00')
    assert earned(result) == Decimal('100.00')
    assert set(result['engine']['salary_days']) == {'salary:employee:2026-11-17'}
