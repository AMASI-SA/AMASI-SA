"""Pure daily candidates; recorded salary days are never rewritten here."""
from calendar import monthrange
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def candidates(employee, first, today):
    parse = lambda value: date.fromisoformat(str(value)[:10])
    revisions = sorted(employee['salary_revisions'], key=lambda r: parse(r['effective_from']))
    if len({parse(r['effective_from']) for r in revisions}) != len(revisions):
        raise ValueError('salary_revision_ambiguous')
    eligible = max([first] + [parse(employee[f]) for f in ('hire_date', 'accrual_start_date') if employee.get(f)])
    current, total, month = first, Decimal(0), None
    result, missing = {}, []
    while current <= today:
        if month != (current.year, current.month):
            month, total = (current.year, current.month), Decimal(0)
        key = 'salary:' + str(employee['id']) + ':' + str(current)
        if current >= eligible and (not employee.get('effective_to') or current <= parse(employee['effective_to'])):
            active = [r for r in revisions if parse(r['effective_from']) <= current
                      and (not r.get('effective_to') or current <= parse(r['effective_to']))]
            if not active:
                missing.append(key)
            else:
                revision = active[-1]
                if revision.get('status') not in {'active', 'inactive', 'unpaid_leave'}:
                    raise ValueError('salary_status_missing')
                salary = Decimal(str(revision['salary']))
                if not salary.is_finite() or salary < 0:
                    raise ValueError('amount_invalid')
                if revision['status'] != 'active':
                    salary = Decimal(0)
                for period in employee.get('payroll_suspension_periods', []):
                    end = period.get('returned_on', period.get('end_date'))
                    before_end = not end or (current < parse(end) if 'returned_on' in period else current <= parse(end))
                    if parse(period.get('started_on', period.get('start_date'))) <= current and before_end:
                        salary = Decimal(0)
                before = total.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
                total += salary / Decimal(monthrange(current.year, current.month)[1])
                earned = total.quantize(Decimal('.01'), rounding=ROUND_HALF_UP) - before
                result[key] = {'amount': format(earned, '.2f'), 'revision': deepcopy(revision),
                               'currency': employee['currency'], 'daily_salary': format(salary, 'f')}
        current += timedelta(days=1)
    return result, missing


def legacy_salary_day_matches(key, previous, proposed, recorded_currency):
    """Recognize exact pre-upgrade rounding, never a general cent tolerance.

    Old rows have currency only on their obligation. Preserve them only when
    their recorded revision still has the same financial inputs and the amount
    exactly matches the old calendar-day formula with current leave eligibility.
    """
    if 'currency' in previous or not proposed or recorded_currency != proposed['currency']:
        return False
    try:
        before, after = previous['revision'], proposed['revision']
        if (before['status'] != after['status']
                or Decimal(str(before['salary'])) != Decimal(str(after['salary']))):
            return False
        day = date.fromisoformat(key.rsplit(':', 1)[1])
        salary = Decimal(proposed['daily_salary'])
        days = Decimal(monthrange(day.year, day.month)[1])
        rounded = lambda value: value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        expected = rounded(salary * day.day / days) - rounded(salary * (day.day - 1) / days)
        return Decimal(str(previous['amount'])) == expected
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return False
