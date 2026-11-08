"""Bounded, read-only consumed advertising and recurring expense evidence.

Payments and opening balances are never expense inputs. Recurring accrual alone
cannot certify coverage of all operating expenses or input-VAT recoverability.
"""
from collections import Counter
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from operational_balance_sources import AD_SOURCES, MAX_ROWS, _advertising, instant, number, rows, usable
from operational_ad_funding import bindings
from recurring_obligations_routes import cycle_bounds, obligation_active_on, FIXED_TYPES, UTILITY_TYPES, CYCLES

RIYADH = ZoneInfo('Asia/Riyadh')
MAX_DAYS = 3660


def component(amount, count, issues, basis):
    complete = count > 0 and not issues
    value = format(amount.quantize(Decimal('.01'), rounding=ROUND_HALF_UP), '.2f') if count else None
    return {'complete': complete, 'amount': value if complete else None,
            'known_subtotal': value, 'source_count': count, 'issues': issues, 'basis': basis}


def recurring_evidence(obligations, invoices, started, stamp):
    first, last = started.astimezone(RIYADH).date(), stamp.astimezone(RIYADH).date()
    issues, total, count = [], Decimal(0), 0
    def problem(code, identity=None):
        issues.append({'code': code, 'source_id': identity, 'currency': 'SAR'})
    if (last-first).days > MAX_DAYS or last < first:
        problem('profit_recurring_period_unbounded')
        return component(total, count, issues, 'canonical_recurring_calendar_accrual_SAR')
    ids = Counter(str(r.get('id') or '') for r in obligations)
    for row in obligations:
        if row.get('status') not in {'active', 'stopped'}:
            continue
        identity = str(row.get('id') or '')
        before_count = count
        try:
            if not identity or ids[identity] != 1 or row.get('currency', 'SAR') != 'SAR':
                raise ValueError('profit_recurring_identity_or_currency_conflict')
            origin = date.fromisoformat(row['start_date'])
            kind = row['expense_type']
            if row.get('cycle') not in set(CYCLES) | {'custom'}:
                raise ValueError('profit_recurring_cycle_missing')
            if kind not in FIXED_TYPES | UTILITY_TYPES:
                raise ValueError('profit_recurring_type_unverified')
            if row.get('updated_at') and row.get('created_at') and instant(row['updated_at']) != instant(row['created_at']):
                if instant(row['updated_at']) > started:
                    raise ValueError('profit_recurring_historical_revision_required')
            if row.get('updated_at') and instant(row['updated_at']) > stamp:
                raise ValueError('profit_recurring_snapshot_after_asof')
            selected = [r for r in invoices if r.get('obligation_id') == identity]
            for ordinal in range(first.toordinal(), last.toordinal()+1):
                day = date.fromordinal(ordinal)
                if not obligation_active_on(row, day):
                    continue
                bounds = cycle_bounds(origin, row['cycle'], day,
                    custom_end=date.fromisoformat(row['custom_end_date']) if row.get('custom_end_date') else None)
                if not bounds:
                    raise ValueError('profit_recurring_cycle_missing')
                begin, end = bounds
                if kind in UTILITY_TYPES:
                    matches = [r for r in selected if r.get('period_start', '9999') <= day.isoformat() <= r.get('period_end', '')]
                    if len(matches) != 1:
                        raise ValueError('profit_utility_actual_invoice_missing_or_ambiguous')
                    invoice = matches[0]
                    if any(invoice.get(k) and instant(invoice[k]) > stamp for k in ('created_at', 'updated_at')):
                        raise ValueError('profit_utility_invoice_after_asof')
                    begin, end = date.fromisoformat(invoice['period_start']), date.fromisoformat(invoice['period_end'])
                    amount = number(invoice.get('amount'))
                else:
                    amount = number(row.get('period_amount'))
                total += amount / Decimal((end-begin).days+1)
                count += 1
        except (ValueError, KeyError, TypeError) as exc:
            problem(str(exc) if isinstance(exc, ValueError) else 'profit_recurring_contract_incomplete', identity)
        if count > before_count:
            problem('profit_expense_input_tax_basis_required', identity)
    result = component(total, count, issues, 'canonical_recurring_calendar_accrual_SAR_before_input_tax_review')
    if not count and not issues:
        result.update(complete=True, amount='0.00', known_subtotal='0.00')
    return result


def advertising_evidence(data, state, owner, started, stamp):
    output = {'issues': [], 'ad_snapshots': [], 'ad_balance_sar_basis': {}}
    configured, conflicts = bindings(state, owner, data['mz2_ad_account_bindings_v2'])
    data = {**data, 'mz2_ad_account_bindings_v2': configured}
    _advertising(data, output, started, stamp, state.get('source_baselines'))
    issues = list(output['issues'])
    issues.extend({'code': 'profit_ad_funding_authority_conflict', 'source_id': key} for key in conflicts)
    total, count = Decimal(0), 0
    snapshots = output['ad_snapshots']
    active = [r for r in configured if usable(r) and r.get('confirmed_by') and r.get('confirmed_at')]
    identities = Counter(str(r.get('_id') or '') for r in active)
    accounts_seen = Counter((r.get('platform'), r.get('platform_account_id')) for r in active)
    for binding in configured:
        if usable(binding) and (not binding.get('confirmed_by') or not binding.get('confirmed_at')):
            issues.append({'code': 'profit_ad_binding_evidence_required', 'source_id': str(binding.get('_id') or '')})
    for binding in active:
        identity = str(binding.get('_id') or '')
        try:
            if (not identity or identities[identity] != 1
                    or accounts_seen[binding.get('platform'), binding.get('platform_account_id')] != 1):
                raise ValueError('profit_ad_account_identity_ambiguous')
            if instant(binding['confirmed_at']) > stamp:
                raise ValueError('profit_ad_binding_after_asof')
            provider = AD_SOURCES[binding['platform']][0]
            accounts = [r for r in data['mezan_integration_accounts_v2'] if r.get('provider') == provider
                        and r.get('mezan_integration_account_id') == binding.get('integration_account_id')
                        and r.get('external_account_id') == binding.get('platform_account_id')]
            if len(accounts) != 1:
                raise ValueError('profit_ad_account_missing_or_ambiguous')
            zone = ZoneInfo(accounts[0]['timezone'])
            first, last = started.astimezone(zone).date(), stamp.astimezone(zone).date()
            if (last-first).days > MAX_DAYS or last < first:
                raise ValueError('profit_ad_period_unbounded')
            for ordinal in range(first.toordinal(), last.toordinal()+1):
                day = date.fromordinal(ordinal)
                begin = datetime.combine(day, time.min, zone)
                if begin >= stamp:
                    continue
                matches = [r for r in snapshots if r['account_id'] == identity and r['date'] == day.isoformat()]
                if len(matches) != 1 or not matches[0]['closed'] or not matches[0]['complete']:
                    issues.append({'code': 'profit_ad_daily_consumption_proof_required', 'source_id': identity, 'date': day.isoformat()})
                    continue
                row = matches[0]
                if row.get('sar_amount') is None:
                    issues.append({'code': 'profit_ad_consumption_fx_required', 'source_id': identity, 'date': day.isoformat()})
                    continue
                total += number(row['sar_amount'])
                count += 1
        except (ValueError, KeyError, TypeError) as exc:
            issues.append({'code': str(exc) if isinstance(exc, ValueError) else 'profit_ad_account_contract_incomplete', 'source_id': identity})
    if not active:
        issues.append({'code': 'profit_ad_applicability_unverified', 'source_id': None})
    return component(total, count, issues, 'verified_closed_daily_consumption_saved_SAR_conversion')


async def collect(db, owner, state, started, stamp):
    names = {'mz2_ad_account_bindings_v2', 'mezan_integration_accounts_v2',
             'mezan_ad_account_cost_settings_v2', 'mz2_ad_automation_policies_v2',
             'mz2_ad_fx_snapshots_v2', 'operating_recurring_obligations_v2',
             'operating_recurring_invoices_v2'} | {value[1] for value in AD_SOURCES.values()}
    data = {name: await rows(db, owner, name) for name in sorted(names)}
    legacy = {name: await expense_rows(db, owner, name) for name in sorted(EXPENSE_SOURCES)}
    expense = operating_expense_evidence(data, legacy, state, started, stamp)
    return {'by_currency': {'SAR': {
        'advertising': advertising_evidence(data, state, owner, started, stamp),
        'operating_expenses': expense}}}


# These are the exhaustive non-payroll sources declared by expenses_routes.
# Read source facts only; linked account_transactions are settlement, not expense.
EXPENSE_SOURCES = frozenset({'operating_daily_expenses', 'operating_rentals',
    'operating_prepaid_expenses', 'operating_salaries'})
EXPENSE_FIELDS = {key: 1 for key in ('id', 'user_id', 'date', 'amount', 'currency',
    'annual_amount', 'monthly_amount', 'category', 'expense_type', 'status',
    'start_date', 'end_date', 'created_at', 'updated_at', 'linked_transaction_id')}


async def expense_rows(db, owner, collection):
    if not owner or collection not in EXPENSE_SOURCES:
        raise ValueError('profit_expense_source_rejected')
    result = await db[collection].find({'user_id': owner}, {'_id': 0, **EXPENSE_FIELDS}).to_list(MAX_ROWS+1)
    if len(result) > MAX_ROWS or any(row.get('user_id') != owner for row in result):
        raise ValueError('profit_expense_source_scope_invalid')
    return result


def operating_expense_evidence(data, legacy, state, started, stamp):
    recurring = recurring_evidence(data['operating_recurring_obligations_v2'],
        data['operating_recurring_invoices_v2'], started, stamp)
    total = Decimal(recurring['known_subtotal'] or '0')
    count, issues = recurring['source_count'], list(recurring['issues'])
    first, last = started.astimezone(RIYADH).date(), stamp.astimezone(RIYADH).date()
    def problem(code, identity, source):
        issues.append({'code': code, 'source_id': identity, 'source': source, 'currency': 'SAR'})
    if last < first or (last-first).days > MAX_DAYS:
        problem('profit_expense_period_unbounded', None, 'period')
        return component(total, count, issues, 'canonical_expense_accrual_SAR_before_input_tax_review')
    recurring_types = {r.get('expense_type') for r in data['operating_recurring_obligations_v2']
                       if r.get('status') in {'active', 'stopped'}}
    for source in sorted(EXPENSE_SOURCES):
        facts = legacy[source]
        identities = Counter(str(row.get('id') or '') for row in facts)
        for row in facts:
            identity = str(row.get('id') or '')
            try:
                # Employee salary belongs to the payroll component. Household
                # consumption is explicitly personal, not business expense.
                if source == 'operating_salaries' and row.get('category') in {'employee', 'household'}:
                    continue
                if source == 'operating_daily_expenses':
                    day = date.fromisoformat(row['date'])
                    if not first <= day <= last:
                        continue
                    begin, end = day, day
                else:
                    begin = date.fromisoformat(row['start_date'])
                    end = date.fromisoformat(row['end_date']) if row.get('end_date') else last
                    if end < begin:
                        raise ValueError('profit_expense_period_invalid')
                    if end < first or begin > last:
                        continue
                    if row.get('status', 'active') != 'active':
                        raise ValueError('profit_expense_historical_status_required')
                if not identity or identities[identity] != 1 or row.get('currency', 'SAR') != 'SAR':
                    raise ValueError('profit_expense_identity_or_currency_conflict')
                if any(row.get(k) and instant(row[k]) > stamp for k in ('created_at', 'updated_at')):
                    raise ValueError('profit_expense_snapshot_after_asof')
                if source != 'operating_daily_expenses' and row.get('updated_at') and row.get('created_at'):
                    if instant(row['updated_at']) != instant(row['created_at']) and instant(row['updated_at']) > started:
                        raise ValueError('profit_expense_historical_revision_required')
                days = (min(last, end)-max(first, begin)).days+1
                if source == 'operating_daily_expenses':
                    amount = number(row.get('amount'))
                elif source == 'operating_rentals':
                    if 'rent' in recurring_types:
                        raise ValueError('profit_legacy_recurring_overlap_review_required')
                    amount = number(row.get('annual_amount')) * Decimal(days) / Decimal(365)
                elif source == 'operating_prepaid_expenses':
                    if not row.get('end_date'):
                        raise ValueError('profit_prepaid_consumption_end_required')
                    if recurring_types:
                        raise ValueError('profit_legacy_recurring_overlap_review_required')
                    amount = number(row.get('amount')) * Decimal(days) / Decimal((end-begin).days+1)
                else:
                    # Charity is present in old operating summaries, but needs
                    # business/personal classification before it becomes P&L.
                    raise ValueError('profit_nonemployee_salary_business_classification_required')
                total += amount
                count += 1
                problem('profit_expense_input_tax_basis_required', identity, source)
            except (ValueError, KeyError, TypeError) as exc:
                problem(str(exc) if isinstance(exc, ValueError) else 'profit_expense_contract_incomplete', identity, source)
    # Direct expense payments do not carry an accrual date, tax split, or a
    # canonical expense ID. Allocated recurring settlements are already covered.
    movements = state.get('movements', [])
    if len(movements) > MAX_ROWS:
        raise ValueError('profit_expense_movement_scope_too_large')
    movement_ids = Counter(str(row.get('id') or '') for row in movements)
    for movement in movements:
        bank_fee = movement.get('bank_fee_amount')
        if bank_fee is not None and movement.get('party_type') == 'ad_account':
            try:
                event = instant(movement.get('occurred_at'))
                if started < event <= stamp and number(bank_fee) > 0:
                    if movement.get('bank_currency') != 'SAR' or movement_ids[str(movement.get('id') or '')] != 1:
                        raise ValueError('profit_bank_fee_currency_or_identity_conflict')
                    total += number(bank_fee)
                    count += 1
                    problem('profit_expense_input_tax_basis_required', movement.get('id'), 'operational_bank_fee')
            except (ValueError, TypeError, KeyError):
                problem('profit_bank_fee_evidence_required', movement.get('id'), 'operational_bank_fee')
        if movement.get('party_type') != 'operating_expense' or movement.get('kind') == 'correction':
            continue
        try:
            event = instant(movement.get('occurred_at'))
            if not started < event <= stamp:
                continue
            allocated = sum((number(a.get('amount')) for a in movement.get('allocations', [])), Decimal(0))
            obligations = state.get('engine', {}).get('obligations', {})
            known_ids = {str(row.get('id')) for row in data['operating_recurring_obligations_v2']}
            linked = all(
                obligations.get(a.get('obligation_id'), {}).get('kind') == 'recurring'
                and str(obligations[a['obligation_id']].get('source_context', {}).get('obligation_id')) in known_ids
                for a in movement.get('allocations', []))
            if allocated != number(movement.get('amount')) or not linked:
                problem('profit_direct_expense_accrual_link_required', movement.get('id'), 'operational_movements')
        except (ValueError, KeyError, TypeError):
            problem('profit_direct_expense_event_required', movement.get('id'), 'operational_movements')
    result = component(total, count, issues, 'canonical_expense_accrual_SAR_before_input_tax_review')
    if not count and not issues:
        result.update(complete=True, amount='0.00', known_subtotal='0.00')
    result['coverage_sources'] = sorted(EXPENSE_SOURCES | {'operating_recurring_obligations_v2',
        'operating_recurring_invoices_v2', 'operational_movements'})
    return result
