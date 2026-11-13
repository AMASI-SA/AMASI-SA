"""Owner-reviewed payroll start; never silently reopen opening-covered salary.

An approved policy changes only the payroll cutoff. Existing daily IDs, settled
amounts, bank movements and opening balances are never rewritten here. Candidate
amounts still use the authoritative calendar-day proration function.

The approved first day is exactly the operational cutover date in Riyadh.
That day earns one full daily share even when startup is later than midnight;
no earlier day in the month is added. Existing tenants require opening review
before changing their stored payroll cutoff.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json

from operational_salary_projection import candidates, legacy_salary_day_matches
from operational_financial_activity_runtime import preparation_checkpoint, prepare_in_thread, checked_rows

RIYADH = timezone(timedelta(hours=3))
POLICY_KEY = 'salary_accrual_policy'
MODE = 'calendar_daily_inclusive'


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), default=str).encode()).hexdigest()


def instant(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('operational_salary_timezone_required')
    return parsed


def strict_day(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError('operational_salary_start_date_invalid')
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError('operational_salary_start_date_invalid')
    return result


def select_first_day(state, as_of):
    """Keep legacy tenants unchanged until their owner reviews opening coverage."""
    started = instant(state['started_at'])
    policy = state.get(POLICY_KEY)
    if policy is None:
        return started.astimezone(RIYADH).date() + timedelta(days=1)
    try:
        first = strict_day(policy['first_day'])
        content = {key: value for key, value in policy.items() if key != 'fingerprint'}
        if (type(policy.get('version')) is not int or policy['version'] != 1
                or policy.get('mode') != MODE or not state.get('owner_id')
                or policy.get('owner_id') != state['owner_id']
                or policy.get('approved_by') != state['owner_id']
                or instant(policy['started_at']) != started
                or not started <= instant(policy['approved_at']) <= instant(as_of)
                or first != started.astimezone(RIYADH).date()
                or policy.get('opening_salary_excluded_since') != first.isoformat()
                or not isinstance(policy.get('request_id'), str) or not policy['request_id']
                or policy.get('fingerprint') != fingerprint(content)):
            raise ValueError('invalid policy')
        return first
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError('operational_salary_start_policy_invalid') from None


def preview_from_sources(state, employees, source_issues, first_day, as_of):
    """Pure preview: a stale/ambiguous source cannot authorize financial changes."""
    preparation_checkpoint()
    first = strict_day(first_day)
    started, clock = instant(state['started_at']), instant(as_of)
    today = clock.astimezone(RIYADH).date()
    if state.get('status') != 'active' or not state.get('owner_id') or clock < started:
        raise ValueError('operational_salary_start_not_active')
    if first != started.astimezone(RIYADH).date() or first > today:
        raise ValueError('operational_salary_start_must_equal_cutover_day')
    if state.get(POLICY_KEY) is not None:
        raise ValueError('operational_salary_start_already_approved')
    issues = deepcopy(source_issues)
    proposed, existing_cutoff = {}, {}
    ids = [row.get('id') for row in employees]
    if any(not isinstance(identity, str) or not identity for identity in ids) or len(ids) != len(set(ids)):
        issues.append({'code': 'operational_salary_employee_identity_ambiguous'})
    else:
        for employee in checked_rows(employees):
            try:
                if not isinstance(employee.get('currency'), str) or len(employee['currency']) != 3:
                    raise ValueError('operational_salary_currency_required')
                days, missing = candidates(employee, first, today)
                prior_days, _ = candidates(employee, select_first_day(state, as_of), today)
                proposed.update(days)
                existing_cutoff.update(prior_days)
                issues.extend({'code': 'salary_contract_missing', 'source_id': key} for key in missing)
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                issues.append({'code': 'operational_salary_source_incomplete', 'source_id': employee['id'],
                               'reason': str(exc)})
    engine = state.get('engine') or {}
    recorded = engine.get('salary_days') or {}
    obligations = engine.get('obligations') or {}
    for key, previous in checked_rows(recorded.items()):
        following = proposed.get(key)
        currency = previous.get('currency', obligations.get(key, {}).get('currency'))
        unchanged = following is not None and previous.get('amount') == following['amount'] and currency == following['currency']
        if not unchanged and not legacy_salary_day_matches(key, previous, following, currency):
            issues.append({'code': 'operational_salary_recorded_day_reconciliation_required',
                           'source_id': key, 'recorded': previous.get('amount'),
                           'proposed': following.get('amount') if following else None,
                           'currency': currency,
                           'proposed_currency': following.get('currency') if following else None,
                           'business_date': key.rsplit(':', 1)[-1],
                           # Exact whole-row equality proves unchanged contract inputs under
                           # the saved cutoff. This is explanation only, never an approval
                           # exemption, tolerance or rewrite of a recorded salary day.
                           'reason': ('cutover_rounding_changed' if following is not None
                                      and previous == existing_cutoff.get(key)
                                      and currency == following.get('currency')
                                      else 'recorded_source_changed')})
    additions = {key: value for key, value in proposed.items() if key not in recorded}
    totals = {}
    for row in checked_rows(additions.values()):
        totals[row['currency']] = totals.get(row['currency'], Decimal(0)) + Decimal(row['amount'])
    proof = {'owner_id': state['owner_id'], 'started_at': state['started_at'], 'first_day': first_day,
             'business_date': today.isoformat(), 'employees': sorted(deepcopy(employees), key=lambda row: str(row.get('id'))),
             'source_issues': source_issues, 'recorded': recorded, 'openings': state.get('openings') or {},
             'salary_obligations': {key: row for key, row in obligations.items() if row.get('kind') == 'salary'},
             'policy': state.get(POLICY_KEY)}
    preparation_checkpoint()
    return {'first_day': first_day, 'business_date': today.isoformat(), 'employee_count': len(employees),
            'new_days': len(additions), 'additional_amounts': {key: format(value, '.2f') for key, value in sorted(totals.items())},
            'can_approve': not issues, 'issues': issues, 'preview_hash': fingerprint(proof),
            'opening_confirmation_required': True,
            'note': 'تبدأ الرواتب من يوم القطع التشغيلي نفسه ويُحتسب كامل استحقاق اليوم؛ لا تشمل الأيام السابقة. الاعتماد يؤكد عدم تضمين هذه الرواتب في الأرصدة الافتتاحية، ولا يُعدّل الأيام أو الدفعات المحفوظة.'}


async def _owner(db, owner, actor):
    from operational_balance_store import fail
    user = await db.users.find_one({'id': actor}, {'_id': 0})
    if (actor != owner or not user or user.get('role') != 'owner' or user.get('disabled')
            or user.get('is_active') is False or user.get('deleted_at')):
        fail('operational_salary_owner_required', 'اعتماد بداية الرواتب متاح للمالك فقط', 403)


def _normalize_sources(data):
    from collections import defaultdict
    from operational_balance_sources import _employees
    out = {'employees': [], 'issues': []}
    contracts = defaultdict(list)
    all_contracts = data['mezan_employee_salary_contracts_v2']
    for contract in checked_rows(all_contracts):
        try:
            contracts[contract.get('employee_id')].append(contract)
        except TypeError:
            pass  # Preserve the source normalizer's handling of malformed identities.
    for employee in checked_rows(data['mezan_employees_v2']):
        try:
            matches = contracts[employee.get('id')]
        except TypeError:
            matches = [row for row in checked_rows(all_contracts) if row.get('employee_id') == employee.get('id')]
        _employees({'mezan_employees_v2': [employee],
                    'mezan_employee_salary_contracts_v2': matches}, out)
    return out


async def _sources(db, owner):
    from operational_balance_sources import rows
    data = {name: await rows(db, owner, name) for name in ('mezan_employees_v2', 'mezan_employee_salary_contracts_v2')}
    return await prepare_in_thread(_normalize_sources, data)


async def read_context(db, owner, actor, *, clock=None):
    """Owner-only read of the exact cutoff and validated policy; no projection/write."""
    from operational_balance_store import read, now, digest, fail
    from operational_balance_service import active_gate
    await _owner(db, owner, actor)
    state = await read(db, owner)
    await active_gate(db, owner, state)
    stamp = clock or now()
    try:
        started = instant(state['started_at'])
        if instant(stamp) < started:
            raise ValueError('operational_salary_start_not_active')
        first = started.astimezone(RIYADH).date().isoformat()
        effective = select_first_day(state, stamp).isoformat()
    except (ValueError, KeyError, TypeError, AttributeError):
        fail('operational_salary_start_context_invalid', 'تعذر التحقق من بداية الرواتب المحفوظة؛ يلزم مراجعة الإعدادات', 409)
    policy = state.get(POLICY_KEY)
    await _owner(db, owner, actor)
    await active_gate(db, owner, state)
    return {'source': 'operational_salary_cutover', 'read_only': True,
            'started_at': state['started_at'], 'first_day': first,
            'effective_first_day': effective, 'timezone': 'Asia/Riyadh',
            'approved': policy is not None,
            'approved_at': policy['approved_at'] if policy is not None else None,
            'session_scope': digest([owner, actor]), 'as_of': stamp}


async def preview(db, owner, actor, first_day, *, clock=None):
    from operational_balance_store import read, now, digest, fail
    from operational_balance_service import active_gate
    await _owner(db, owner, actor)
    state = await read(db, owner)
    await active_gate(db, owner, state)
    sources = await _sources(db, owner)
    try:
        result = await prepare_in_thread(preview_from_sources, state, sources['employees'], sources['issues'], first_day, clock or now())
    except (ValueError, KeyError) as exc:
        fail(str(exc), 'تعذر معاينة بداية الرواتب؛ راجع الفترة والإعدادات')
    result['session_scope'] = digest([owner, actor])
    return result


async def approve(db, owner, actor, payload, *, clock=None):
    from operational_balance_store import mutate, now, digest, fail, replay, remember, audit
    from operational_balance_service import active_gate
    stamp = clock or now()
    await _owner(db, owner, actor)
    if payload.get('expected_session_scope') != digest([owner, actor]):
        fail('operational_actor_scope_changed', 'تغير نطاق الحساب؛ أعد المعاينة', 409)
    if payload.get('opening_salaries_excluded') is not True:
        fail('operational_salary_opening_confirmation_required', 'يلزم تأكيد عدم تضمين هذه الرواتب في الأرصدة الافتتاحية', 422)

    async def apply(state):
        await _owner(db, owner, actor)
        old = replay(state, 'salary_start_policy', payload)
        if old is not None:
            return old
        await active_gate(db, owner, state)
        sources = await _sources(db, owner)
        try:
            check = preview_from_sources(state, sources['employees'], sources['issues'], payload['first_day'], stamp)
        except (ValueError, KeyError) as exc:
            fail(str(exc), 'تعذر اعتماد بداية الرواتب؛ أعد المعاينة')
        if check['preview_hash'] != payload.get('expected_preview_hash'):
            fail('operational_salary_preview_stale', 'تغيرت الرواتب أو الأرصدة منذ المعاينة؛ أعد المعاينة')
        if not check['can_approve']:
            fail('operational_salary_reconciliation_required', 'توجد أيام محفوظة أو مصادر تحتاج مطابقة؛ لم تُعدّل الأرصدة')
        policy = {'version': 1, 'mode': MODE, 'owner_id': owner, 'started_at': state['started_at'],
                  'first_day': payload['first_day'], 'opening_salary_excluded_since': payload['first_day'],
                  'approved_by': actor, 'approved_at': stamp, 'request_id': payload['request_id']}
        policy['fingerprint'] = fingerprint(policy)
        await _owner(db, owner, actor)
        await active_gate(db, owner, state)
        state[POLICY_KEY] = policy
        audit(state, actor, 'salary_start_policy_approved', None, policy,
              'بداية يومية معتمدة؛ الأرصدة الافتتاحية تستبعد رواتب الفترة', 'mezan2', stamp)
        result = {'ok': True, 'first_day': payload['first_day'], 'policy': deepcopy(policy),
                  'next_step': 'automatic_operational_salary_refresh', 'bank_money_changed': False}
        remember(state, 'salary_start_policy', payload, result)
        return result
    return await mutate(db, owner, apply)
