"""Explicit first-of-month payroll policy, without opening or bank rewrites."""
import asyncio
from calendar import monthrange
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

from operational_balance_engine import reconcile
from operational_balance_store import initial, digest, STATES
from operational_salary_start import (
    MODE, POLICY_KEY, fingerprint, select_first_day, preview_from_sources, preview, approve,
)

START = '2026-11-01T00:30:01+03:00'
NOW = '2026-11-01T12:00:00+03:00'


def employee(amount='3000', **extra):
    return {'id': 'employee', 'currency': 'SAR', 'hire_date': '2026-01-01',
            'salary_revisions': [{'effective_from': '2026-01-01', 'salary': amount, 'status': 'active'}], **extra}


def state(start=START):
    value = initial('owner')
    value.update(status='active', started_at=start)
    value['openings'] = {'opening': {'id': 'opening', 'party_type': 'employee', 'party_id': 'employee',
                                    'amount': '600', 'currency': 'SAR', 'direction': 'for_party', 'name': 'Employee'}}
    return value


def policy(value, *, first='2026-11-01', approved=NOW):
    item = {'version': 1, 'mode': MODE, 'owner_id': 'owner', 'started_at': value['started_at'],
            'first_day': first, 'opening_salary_excluded_since': first, 'approved_by': 'owner',
            'approved_at': approved, 'request_id': 'test-approval'}
    item['fingerprint'] = fingerprint(item)
    value[POLICY_KEY] = item
    return value


def earned(value):
    return sum((Decimal(row['amount']) for row in value['engine']['salary_days'].values()), Decimal(0))


def test_cutover_rounding_conflicts_expose_exact_review_evidence_without_authorizing():
    emp = employee('1000')
    stamp = '2026-11-08T12:00:00+03:00'
    recorded = reconcile(state(), {'employees': [emp]}, stamp)
    before = deepcopy(recorded)
    check = preview_from_sources(recorded, [emp], [], '2026-11-01', stamp)
    conflicts = check['issues']
    assert len(conflicts) == 5
    assert all(row['reason'] == 'cutover_rounding_changed' for row in conflicts)
    assert all(row['currency'] == 'SAR' and row['business_date'].startswith('2026-11-') for row in conflicts)
    assert check['additional_amounts'] == {'SAR': '33.33'}
    assert check['can_approve'] is False
    assert recorded == before
    changed = deepcopy(emp)
    changed['salary_revisions'][0]['salary'] = '1000.01'
    altered = preview_from_sources(recorded, [changed], [], '2026-11-01', stamp)
    assert all(row.get('reason') != 'cutover_rounding_changed' for row in altered['issues'])


def test_no_approval_does_not_change_existing_tenant_cutoff():
    value = state()
    assert select_first_day(value, NOW) == date(2026, 11, 2)
    assert earned(reconcile(value, {'employees': [employee()]}, NOW)) == 0


def test_start_day_one_is_included_and_next_day_added_once_without_opening_or_cash_changes():
    value = policy(state())
    value['movements'] = [{'id': 'recorded-bank-operation', 'amount': '50.00', 'allocations': []}]
    before = deepcopy(value)
    result = reconcile(value, {'employees': [employee()]}, NOW)
    assert earned(result) == Decimal('100.00')
    assert result['openings'] == before['openings'] and result['movements'] == before['movements']
    assert value == before
    assert reconcile(result, {'employees': [employee()]}, NOW) == result
    second = reconcile(result, {'employees': [employee()]}, '2026-11-01T21:00:00+00:00')
    assert earned(second) == Decimal('200.00')
    assert len(second['engine']['salary_days']) == 2


@pytest.mark.parametrize('year,month', [(2026, 2), (2028, 2), (2026, 11), (2026, 12)])
@pytest.mark.parametrize('salary', ['3000', '3000.01', '1000.99'])
def test_actual_month_days_and_cumulative_rounding_conserve_full_month(year, month, salary):
    start = f'{year}-{month:02d}-01T00:30:00+03:00'
    value = policy(state(start), first=f'{year}-{month:02d}-01', approved=start)
    last = monthrange(year, month)[1]
    source = {'employees': [employee(salary)]}
    for day in range(1, last + 1):
        value = reconcile(value, source, f'{year}-{month:02d}-{day:02d}T12:00:00+03:00')
    assert len(value['engine']['salary_days']) == last
    assert earned(value) == Decimal(salary)
    assert not value['engine']['issues']


def test_hire_and_unpaid_leave_are_still_respected():
    emp = employee(hire_date='2026-11-03', payroll_suspension_periods=[{'started_on': '2026-11-04', 'returned_on': '2026-11-06'}])
    value = reconcile(policy(state()), {'employees': [emp]}, '2026-11-06T12:00:00+03:00')
    assert earned(value) == Decimal('200.00')
    assert value['engine']['salary_days']['salary:employee:2026-11-04']['amount'] == '0.00'
    assert 'salary:employee:2026-11-02' not in value['engine']['salary_days']


def test_salary_change_uses_effective_day_not_latest_salary_for_whole_month():
    emp = employee()
    emp['salary_revisions'].append({'effective_from': '2026-11-16', 'salary': '6000', 'status': 'active'})
    value = reconcile(policy(state()), {'employees': [emp]}, '2026-11-30T12:00:00+03:00')
    assert earned(value) == Decimal('4500.00')


@pytest.mark.parametrize('field,value', [('version', True), ('owner_id', 'other'), ('approved_by', 'staff'),
    ('first_day', '2026-10-01'), ('opening_salary_excluded_since', '2026-10-31'),
    ('approved_at', '2026-11-02T12:00:00+03:00'), ('approved_at', '2026-11-01T12:00:00'),
    ('started_at', '2026-10-31T23:00:00+03:00'), ('mode', 'unknown'), ('request_id', '')])
def test_invalid_policy_cannot_add_or_rewrite_recorded_days(field, value):
    initial_state = policy(state())
    recorded = reconcile(initial_state, {'employees': [employee()]}, NOW)
    recorded['engine']['salary_days']['salary:employee:2026-11-01'].pop('currency')
    original = deepcopy(recorded['engine']['salary_days'])
    recorded[POLICY_KEY][field] = value
    recorded[POLICY_KEY]['fingerprint'] = fingerprint({k: v for k, v in recorded[POLICY_KEY].items() if k != 'fingerprint'})
    result = reconcile(recorded, {'employees': [employee()]}, NOW)
    assert result['engine']['salary_days'] == original
    assert any(row['code'] == 'operational_salary_start_policy_invalid' for row in result['engine']['issues'])


def test_policy_fingerprint_detects_accidental_edits():
    value = policy(state())
    value[POLICY_KEY]['request_id'] = 'different'
    with pytest.raises(ValueError, match='policy_invalid'):
        select_first_day(value, NOW)


def test_preview_is_pure_shows_only_missing_days_and_keeps_currencies_separate():
    value = state()
    employees = [employee(), {**employee('310'), 'id': 'usd-employee', 'currency': 'USD'}]
    before = deepcopy(value)
    result = preview_from_sources(value, employees, [], '2026-11-01', NOW)
    assert result['can_approve'] and result['new_days'] == 2
    assert result['additional_amounts'] == {'SAR': '100.00', 'USD': '10.33'}
    assert value == before


def test_previously_recorded_day_is_not_counted_twice_in_preview():
    value = reconcile(policy(state()), {'employees': [employee()]}, NOW)
    value.pop(POLICY_KEY)
    check = preview_from_sources(value, [employee()], [], '2026-11-01', NOW)
    assert check['can_approve'] and check['new_days'] == 0 and check['additional_amounts'] == {}


def test_changed_rounding_of_a_preserved_day_blocks_approval_instead_of_rewriting_it():
    start = '2026-12-01T00:30:00+03:00'
    value = reconcile(state(start), {'employees': [employee()]}, '2026-12-02T12:00:00+03:00')
    before = deepcopy(value)
    check = preview_from_sources(value, [employee()], [], '2026-12-01', '2026-12-02T12:00:00+03:00')
    assert not check['can_approve']
    assert check['issues'][0]['code'] == 'operational_salary_recorded_day_reconciliation_required'
    assert value == before


@pytest.mark.parametrize('first', ['2026-10-01', '2026-11-02', '2026-12-01', '2026-11-1', '', None])
def test_invalid_or_other_month_start_is_rejected(first):
    with pytest.raises((ValueError, TypeError)):
        preview_from_sources(state(), [employee()], [], first, NOW)


def test_missing_contract_and_duplicate_employee_cannot_authorize_start():
    check = preview_from_sources(state(), [employee(), employee()], [], '2026-11-01', NOW)
    assert not check['can_approve']
    check = preview_from_sources(state(), [], [{'code': 'employee_salary_contract_missing_or_ambiguous'}], '2026-11-01', NOW)
    assert not check['can_approve']


async def seed(db):
    await db.users.insert_many([{'id': 'owner', 'role': 'owner', 'is_active': True},
                               {'id': 'staff', 'role': 'employee', 'created_by': 'owner', 'is_active': True}])
    await seed_state(db)


async def seed_state(db):
    value = state();value['revision'] = 1
    await db[STATES].insert_one(value)
    await db.mezan_employees_v2.insert_one({'id': 'employee', 'user_id': 'owner', 'status': 'active', 'hire_date': '2026-01-01'})
    await db.mezan_employee_salary_contracts_v2.insert_one({'id': 'contract', 'employee_id': 'employee', 'user_id': 'owner',
        'effective_from': '2026-01-01', 'monthly_amount': '3000', 'currency': 'SAR'})


async def payload_for(db):
    check = await preview(db, 'owner', 'owner', '2026-11-01', clock=NOW)
    return {'request_id': 'approve-salary-start', 'first_day': '2026-11-01',
            'expected_preview_hash': check['preview_hash'], 'expected_session_scope': check['session_scope'],
            'opening_salaries_excluded': True}


def run_mock(fn):
    async def scenario():
        db = AsyncMongoMockClient()['salary_policy']
        await seed(db)
        await fn(db)
    asyncio.run(scenario())


def test_owner_approval_and_identical_retry_are_once_only_and_do_not_move_money():
    async def scenario(db):
        before = await db[STATES].find_one({'_id': 'owner'})
        payload = await payload_for(db)
        first = await approve(db, 'owner', 'owner', payload, clock=NOW)
        assert await approve(db, 'owner', 'owner', payload, clock=NOW) == first
        after = await db[STATES].find_one({'_id': 'owner'})
        assert after['openings'] == before['openings'] and after['movements'] == before['movements']
        assert after['engine'] == before['engine']
        assert len(after['audit']) == 1 and after['revision'] == before['revision'] + 1
        assert select_first_day(after, NOW) == date(2026, 11, 1)
        assert await db.general_ledger.count_documents({}) == 0
    run_mock(scenario)


@pytest.mark.parametrize('change', ['actor', 'scope', 'confirmation', 'salary', 'opening', 'frozen', 'native'])
def test_rejected_approvals_preserve_state(change):
    async def scenario(db):
        payload = await payload_for(db)
        actor = 'owner'
        if change == 'actor': actor = 'staff'
        if change == 'scope': payload['expected_session_scope'] = digest(['other', 'owner'])
        if change == 'confirmation': payload['opening_salaries_excluded'] = False
        if change == 'salary':
            await db.mezan_employee_salary_contracts_v2.update_one({'id': 'contract'}, {'$set': {'monthly_amount': '6000'}})
        if change == 'opening':
            await db[STATES].update_one({'_id': 'owner'}, {'$set': {'openings.opening.amount': '700'}})
        if change == 'frozen': await db[STATES].update_one({'_id': 'owner'}, {'$set': {'status': 'frozen'}})
        if change == 'native': await db.settings.insert_one({'user_id': 'owner', 'mezan2_financial_cutover': {'status': 'active'}})
        before = await db[STATES].find_one({'_id': 'owner'})
        with pytest.raises(HTTPException):
            await approve(db, 'owner', actor, payload, clock=NOW)
        assert await db[STATES].find_one({'_id': 'owner'}) == before
    run_mock(scenario)


def test_state_refresh_clock_alone_does_not_invalidate_same_business_day_preview():
    async def scenario(db):
        payload = await payload_for(db)
        await db[STATES].update_one({'_id': 'owner'}, {'$set': {'engine.as_of': '2026-11-01T12:00:05+03:00'}, '$inc': {'revision': 1}})
        assert (await approve(db, 'owner', 'owner', payload, clock='2026-11-01T12:00:10+03:00'))['ok']
    run_mock(scenario)
