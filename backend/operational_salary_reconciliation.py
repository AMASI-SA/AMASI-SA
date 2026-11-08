"""Exact owner-reviewed cutoff differences, represented as separate obligations."""
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import re


def _amount(value):
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.[0-9]{2}', value):
        raise ValueError('operational_salary_rounding_review_invalid')
    return Decimal(value)


def review_for(rows, through):
    totals = {}
    for row in rows.values():
        delta = _amount(row['proposed']['amount']) - _amount(row['recorded']['amount'])
        currency = row['proposed']['currency']
        part = totals.setdefault(currency, {'increase': Decimal(0), 'decrease': Decimal(0), 'net': Decimal(0)})
        part['increase' if delta > 0 else 'decrease'] += abs(delta)
        part['net'] += delta
    return {'version': 1, 'reviewed_through': through, 'rows': deepcopy(rows),
            'totals': {c: {k: format(v, '.2f') for k, v in part.items()} for c, part in sorted(totals.items())}}


def validate_review(review, first_day, approved_day):
    try:
        through = date.fromisoformat(review['reviewed_through'])
        first = date.fromisoformat(first_day)
        if (type(review.get('version')) is not int or review['version'] != 1
                or not first <= through <= approved_day or not isinstance(review['rows'], dict) or not review['rows']):
            raise ValueError()
        for key, row in review['rows'].items():
            if not isinstance(key, str) or not key.startswith('salary:'):
                raise ValueError()
            eid, _, stamp = key.removeprefix('salary:').rpartition(':')
            before, after = row['recorded'], row['proposed']
            if (not eid or not first <= date.fromisoformat(stamp) <= through
                    or not re.fullmatch(r'[A-Z]{3}', before['currency'])
                    or before['currency'] != after['currency']
                    or {k: v for k, v in before.items() if k != 'amount'} != {k: v for k, v in after.items() if k != 'amount'}
                    or abs(_amount(after['amount']) - _amount(before['amount'])) != Decimal('.01')):
                raise ValueError()
        if review != review_for(review['rows'], review['reviewed_through']):
            raise ValueError()
        return review['rows']
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation):
        raise ValueError('operational_salary_rounding_review_invalid') from None


def adjustment_for(key, row):
    employee, _, stamp = key.removeprefix('salary:').rpartition(':')
    delta = _amount(row['proposed']['amount']) - _amount(row['recorded']['amount'])
    identity = 'salary_cutover_adjustment:' + employee + ':' + stamp
    return {'id': identity, 'kind': 'salary_cutover_adjustment', 'party_id': employee,
            'party_type': 'employee', 'currency': row['proposed']['currency'],
            'expected': '0.00', 'confirmed': format(abs(delta), '.2f'),
            'direction': 'payable' if delta > 0 else 'receivable', 'business_date': stamp,
            'original_salary_day_id': key, 'source': 'owner_reviewed_salary_cutover'}


def reviewed_salary_day_matches(state, key, previous, proposed, currency, *, require_adjustment=True):
    """No cent tolerance: both full candidates and the exact separate obligation must match."""
    policy = state.get('salary_accrual_policy') or {}
    review = policy.get('rounding_review')
    if not review or proposed is None:
        return False
    from operational_salary_start import fingerprint, instant, RIYADH, select_first_day
    try:
        select_first_day(state, policy['approved_at'])
        if (policy.get('fingerprint') != fingerprint({k: v for k, v in policy.items() if k != 'fingerprint'})
                or policy.get('owner_id') != state.get('owner_id')
                or policy.get('approved_by') != state.get('owner_id')):
            return False
        rows = validate_review(review, policy['first_day'], instant(policy['approved_at']).astimezone(RIYADH).date())
        proof = rows.get(key)
        if (not proof or previous != proof['recorded'] or proposed != proof['proposed']
                or currency != proof['proposed']['currency']):
            return False
        employee, _, stamp = key.removeprefix('salary:').rpartition(':')
        original = state.get('engine', {}).get('obligations', {}).get(key, {})
        original_fields = {'id': key, 'kind': 'salary', 'party_type': 'employee', 'party_id': employee,
                           'currency': currency, 'confirmed': previous['amount'], 'direction': 'payable',
                           'business_date': stamp}
        if not all(original.get(field) == value for field, value in original_fields.items()):
            return False
        expected = adjustment_for(key, proof)
        actual = state.get('engine', {}).get('obligations', {}).get(expected['id'])
        if actual is None:
            return not require_adjustment
        return all(actual.get(field) == value for field, value in expected.items())
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
