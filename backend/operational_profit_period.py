"""Read-only period accrual evidence. Never infer profit from account balances."""
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

from operational_balance_sources import instant, number, rows, _employees
from operational_salary_projection import candidates
from operational_salary_start import RIYADH, select_first_day
from salla_payment_collection import order_page_source_current


def _amount(value, currency):
    if isinstance(value, dict):
        if value.get('currency', currency) != currency:
            raise ValueError('currency_conflict')
        value = value.get('amount')
    return number(value)


async def collect(db, owner, state, raw_orders, data, started, stamp):
    totals, counts, problems = defaultdict(Decimal), Counter(), defaultdict(list)
    def problem(component, code, identity=None, currency=None):
        problems[component].append({'code': code, 'source_id': identity, 'currency': currency})
    cohort = []
    identities = Counter(str(raw.get('id') or '') for _, raw in raw_orders)
    if len(raw_orders) != len(data['unified_orders']):
        problem('net_sales', 'profit_canonical_orders_missing')
    for source, raw in raw_orders:
        oid, currency = str(raw.get('id') or ''), raw.get('currency')
        try:
            created = instant(raw.get('date') or raw.get('created_at') or raw.get('order_date'))
            if not started < created <= stamp:
                continue
            if (not oid or identities[oid] != 1 or not order_page_source_current(source, raw)
                    or instant(raw.get('updated_at') or raw.get('date_updated') or created) > stamp):
                raise ValueError('profit_order_snapshot_unverified')
            amounts = raw.get('amounts') or {}
            currency = currency or amounts['total'].get('currency')
            if not currency:
                raise ValueError('profit_sale_currency_missing')
            # Canonical current total already includes order edits. A return must
            # first establish whether that edit included it; never subtract twice.
            returns = [r for r in state.get('customer_returns', []) if str(r.get('order_id')) == oid]
            if returns or raw.get('refund_amount') or raw.get('refunded_amount') or (raw.get('payment_actions') or {}).get('refund_action'):
                raise ValueError('profit_return_sales_tax_basis_required')
            status = raw.get('status')
            status = status.get('slug') if isinstance(status, dict) else status
            if status not in {'delivered', 'تم التوصيل'}:
                raise ValueError('profit_sale_recognition_event_required')
            gross, vat = _amount(amounts.get('total'), currency), _amount(amounts.get('tax'), currency)
            if vat > gross:
                raise ValueError('profit_sale_tax_exceeds_total')
            cohort.append(raw)
            totals[currency, 'net_sales'] += gross - vat
            counts[currency, 'net_sales'] += 1
        except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation) as exc:
            problem('net_sales', str(exc) if isinstance(exc, ValueError) else 'profit_sale_amount_or_tax_missing', oid, currency)
    # Owner-scoped authoritative contracts; use the same approved cutover policy
    # and cumulative calendar rounding as the operational engine, without posting.
    payroll = {'employees': [], 'issues': []}
    try:
        payroll_data = {name: await rows(db, owner, name) for name in
            ('mezan_employees_v2', 'mezan_employee_salary_contracts_v2')}
        _employees(payroll_data, payroll)
        first, last = select_first_day(state, stamp), stamp.astimezone(RIYADH).date()
        ids = Counter(str(e.get('id') or '') for e in payroll['employees'])
        for employee in payroll['employees']:
            currency, eid = employee['currency'], str(employee['id'])
            if not eid or ids[eid] != 1:
                raise ValueError('profit_employee_identity_ambiguous')
            days, missing = candidates(employee, first, last)
            for key in missing:
                problem('salaries', 'profit_salary_revision_missing', key, currency)
            for key, row in days.items():
                totals[currency, 'salaries'] += Decimal(row['amount'])
                counts[currency, 'salaries'] += 1
        for issue in payroll['issues']:
            problem('salaries', issue['code'], issue.get('source_id'))
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation):
        problem('salaries', 'profit_salary_source_or_cutover_unverified')
    currencies = {c for c, _ in totals} | {row.get('currency') for group in problems.values() for row in group if row.get('currency')}
    result = {}
    for currency in currencies:
        result[currency] = {}
        for component in ('net_sales', 'salaries'):
            errors = [r for r in problems[component] if r['currency'] in (None, currency)]
            # A missing currency scope is not evidence of a zero amount.
            present = (currency, component) in totals
            complete = present and not errors
            result[currency][component] = {'complete': complete,
                'amount': format(totals[currency, component], '.2f') if complete else None,
                'known_subtotal': format(totals[currency, component], '.2f') if present else None,
                'source_count': counts[currency, component], 'issues': errors,
                'basis': 'delivered_current_order_total_less_explicit_vat' if component == 'net_sales' else 'approved_salary_contract_calendar_accrual'}
    from operational_profit_order_costs import collect as order_costs
    for currency, components in order_costs(state, data, cohort, problems['net_sales']).items():
        result.setdefault(currency, {}).update(components)
    return {'version': 1, 'scope': 'operating_period_accrual', 'started_at': started.isoformat(),
        'as_of': stamp.isoformat(), 'by_currency': result, 'issues': dict(problems)}
