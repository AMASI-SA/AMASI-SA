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
    from sold_products_report_v2 import order_matches_status
    settings = await db['settings'].find({'user_id': owner}, {'_id': 0, 'user_id': 1, 'report_included_statuses': 1}).to_list(2)
    if len(settings) > 1 or any(r.get('user_id') != owner for r in settings):
        problem('net_sales', 'profit_sales_policy_ambiguous')
    configured = settings[0].get('report_included_statuses') or [] if len(settings) == 1 else []
    returns_by_order = defaultdict(list)
    for returned in state.get('customer_returns', []):
        returns_by_order[str(returned.get('order_id'))].append(returned)
    cohort = []
    identities = Counter(str(raw.get('id') or '') for _, raw in raw_orders)
    numbers = Counter(str(raw.get('reference_id') or raw.get('order_number') or '') for _, raw in raw_orders)
    if len(raw_orders) != len(data['unified_orders']):
        problem('net_sales', 'profit_canonical_orders_missing')
    for source, raw in raw_orders:
        oid, currency = str(raw.get('id') or ''), raw.get('currency')
        try:
            created = instant(raw.get('date') or raw.get('created_at') or raw.get('order_date'))
            if not started < created <= stamp:
                continue
            order_number = str(raw.get('reference_id') or raw.get('order_number') or '')
            if (not oid or not order_number or identities[oid] != 1 or numbers[order_number] != 1
                    or str(source.get('order_number') or '') != order_number
                    or source.get('salla_order_id') is not None and str(source['salla_order_id']) != oid
                    or not order_page_source_current(source, raw)
                    or instant(raw.get('updated_at') or raw.get('date_updated') or created) > stamp):
                raise ValueError('profit_order_snapshot_unverified')
            status = raw.get('status')
            status = status.get('slug') or status.get('name') if isinstance(status, dict) else status
            selected = {'order_status': source.get('order_status') or status,
                        'order_status_slug': source.get('order_status_slug') or status}
            if not order_matches_status(selected, ['default'], configured):
                if configured or status in {'cancelled', 'canceled', 'deleted', 'refunded', 'returned', 'ملغي', 'ملغى', 'محذوف', 'مسترجع'}:
                    continue
                raise ValueError('profit_sales_policy_status_unverified')
            amounts = raw.get('amounts') or {}
            currency = currency or amounts['total'].get('currency')
            if not currency:
                raise ValueError('profit_sale_currency_missing')
            # Canonical current total already includes order edits. A return must
            # first establish whether that edit included it; never subtract twice.
            returns = returns_by_order.get(oid, [])
            from operational_profit_returns import current_total_is_net
            if not current_total_is_net(raw, returns, stamp):
                raise ValueError('profit_return_sales_tax_basis_required')
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
        for employee in payroll_data['mezan_employees_v2']:
            if any(employee.get(key) for key in ('archived', 'deleted', 'is_archived', 'is_deleted')):
                problem('salaries', 'profit_archived_employee_period_history_required', str(employee.get('id') or ''))
        for record in [*payroll_data['mezan_employees_v2'], *payroll_data['mezan_employee_salary_contracts_v2']]:
            if record.get('updated_at') and instant(record['updated_at']) > stamp:
                problem('salaries', 'profit_salary_source_after_snapshot', str(record.get('id') or record.get('employee_id') or ''))
        _employees(payroll_data, payroll)
        first, last = select_first_day(state, stamp), stamp.astimezone(RIYADH).date()
        span = (last-first).days + 1
        workload = span * sum(max(1, len(e.get('salary_revisions', []))) for e in payroll['employees'])
        if span < 0 or span > 3660 or workload > 250000:
            problem('salaries', 'profit_salary_period_or_workload_unbounded')
            raise ValueError('profit_salary_period_or_workload_unbounded')
        saved_days = state.get('engine', {}).get('salary_days', {})
        conflicts = state.get('engine', {}).get('salary_conflicts', {})
        proposed = {}
        ids = Counter(str(e.get('id') or '') for e in payroll['employees'])
        for employee in payroll['employees']:
            currency, eid = employee['currency'], str(employee['id'])
            if not eid or ids[eid] != 1:
                raise ValueError('profit_employee_identity_ambiguous')
            days, missing = candidates(employee, first, last)
            for key in missing:
                problem('salaries', 'profit_salary_revision_missing', key, currency)
            proposed.update(days)
            for key, row in days.items():
                previous = saved_days.get(key)
                from operational_salary_projection import legacy_salary_day_matches
                from operational_salary_reconciliation import reviewed_salary_day_matches
                obligation = state.get('engine', {}).get('obligations', {}).get(key)
                reconciled = (previous is None and obligation is None) or (
                    previous is not None and obligation is not None
                    and (previous == row or legacy_salary_day_matches(key, previous, row, currency)
                         or reviewed_salary_day_matches(state, key, previous, row, currency))
                    and obligation.get('kind') == 'salary' and obligation.get('party_id') == eid
                    and obligation.get('currency') == currency
                    and number(obligation.get('confirmed')) == number(previous.get('amount')))
                if key in conflicts or not reconciled:
                    problem('salaries', 'profit_salary_recorded_reconciliation_required', key, currency)
                totals[currency, 'salaries'] += Decimal(row['amount'])
                counts[currency, 'salaries'] += 1
        recorded_keys = {key for key, value in state.get('engine', {}).get('obligations', {}).items() if value.get('kind') == 'salary'}
        for key in set(saved_days) | set(conflicts) | recorded_keys:
            if first.isoformat() <= key.rsplit(':', 1)[-1] <= last.isoformat() and key not in proposed:
                problem('salaries', 'profit_salary_recorded_reconciliation_required', key, saved_days.get(key, {}).get('currency'))
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
                'basis': 'canonical_sales_status_scope_created_in_period_less_explicit_vat' if component == 'net_sales' else 'approved_salary_contract_calendar_accrual'}
    from operational_profit_order_costs import collect as order_costs
    for currency, components in order_costs(state, data, cohort, problems['net_sales']).items():
        result.setdefault(currency, {}).update(components)
    from operational_profit_period_expenses import collect as expense_period
    try:
        expense_evidence = await expense_period(db, owner, state, started, stamp)
    except (ValueError, TypeError, KeyError, ArithmeticError):
        expense_evidence = {'by_currency': {'SAR': {kind: {'complete': False, 'amount': None,
            'known_subtotal': None, 'source_count': 0, 'issues': [{'code': 'profit_expense_source_scope_unverified'}]}
            for kind in ('advertising', 'operating_expenses')}}}
    for currency, components in expense_evidence['by_currency'].items():
        result.setdefault(currency, {}).update(components)
    return {'version': 1, 'scope': 'operating_period_accrual', 'started_at': started.isoformat(),
        'as_of': stamp.isoformat(), 'sales_scope': {'date_basis': 'canonical_created_at', 'status_policy': configured or 'sold_products_default'}, 'by_currency': result, 'issues': dict(problems)}
