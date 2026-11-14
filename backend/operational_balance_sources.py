"""Read-only MZ2 operational source boundary.

Only ALLOWED_COLLECTIONS may be read. No accounting service, writer, fallback,
source mutation or root-level unified-order financial value is used here.
Unsupported or ambiguous evidence is reported as incomplete, never as zero.
"""
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo
import calendar
import hashlib
import json
from dashboard_cpu_budget import CPUWorkBudget, PARSER_BUDGET_MS
from operational_demand_refresh import calculation_checkpoint, refresh_stage
from operational_financial_activity_runtime import prepare_in_thread
from operational_piece_index import index_preparation_pieces
from operational_courier_fee_history import build_recorded_courier_fee_lookup
from shipping_companies import normalize_shipping_company
from operational_courier_tariffs import matches as matches_courier_key


def external_courier(row):
    """Exclude internal delivery from opening courier identities as well as discovery."""
    values = [row.get('courier_key'), row.get('id'), row.get('name'), row.get('company_name'),
              *(row.get('salla_carrier_keys') or [])]
    for value in values:
        text = str(value or '').strip().lower()
        key, _ = normalize_shipping_company(text)
        if key in {'mandoob', 'mandoob_riyadh', 'pickup'} or any(
                marker in text for marker in ('store driver', 'store_driver', 'مندوب', 'موصل المتجر')):
            return False
    return True

ENTITY_COLLECTIONS = {
    'employee': 'mezan_employees_v2', 'supplier': 'mezan_suppliers_v2',
    'employee_custody': 'mezan_employees_v2',
    'store_driver': 'store_drivers', 'external_person': 'mz2_external_persons_v2',
    'bank': 'mz2_financial_accounts', 'cash': 'mz2_financial_accounts',
}
AD_SOURCES = {
    'snapchat': ('snapchat_ads', 'mezan_snapchat_daily_projections_v2'),
    'meta': ('meta_ads', 'mezan_meta_performance_daily_v2'),
    'tiktok': ('tiktok_ads', 'mezan_tiktok_performance_daily_v2'),
    'google_ads': ('google_ads', 'mezan_google_ads_performance_daily_v2'),
}
ALLOWED_COLLECTIONS = frozenset(ENTITY_COLLECTIONS.values()) | {
    'accounts', 'mz2_operational_bank_accounts',  # Identity only; excluded from financial scans.
    'mz2_opening_facts_v2',  # POS identity only; never import native opening amounts.
    'settings',  # Scoped courier identities and owner-authorized payment commission terms.
    'mz2_shipping_setup_v2', 'mz2_provider_fee_policies_v2', 'mz2_bank_transfer_bindings',
    'mz2_ad_account_bindings_v2', 'mezan_integration_accounts_v2',
    'mz2_ad_fx_snapshots_v2', 'mezan_ad_account_cost_settings_v2',
    'mz2_ad_automation_policies_v2', 'expense_categories', 'users',
    'unified_orders', 'mezan_products_v2', 'mezan_product_cost_profiles_v2',
    'mezan_product_resource_bindings_v2', 'mezan_product_option_cost_bindings_v2', 'mezan_cost_resources_v2',
    'mezan_preparation_pieces_v1', 'mezan_supplier_invoices_v2',
    'store_delivery_assignments', 'store_delivery_collections',
    'store_delivery_driver_earnings', 'store_delivery_driver_settlements',
    'mezan_employee_salary_contracts_v2', 'operating_recurring_obligations_v2',
    'operating_recurring_invoices_v2', 'mz2_operational_owner_beneficiaries_v2',
} | {v[1] for v in AD_SOURCES.values()}
from operational_remaining_bundle_sources import READ_COLLECTIONS as BUNDLE_READ_COLLECTIONS
ALLOWED_COLLECTIONS = ALLOWED_COLLECTIONS | BUNDLE_READ_COLLECTIONS

PROVIDERS = ('salla', 'tabby', 'tamara', 'emkan')
from operational_payment_evidence import PAYMENT_METHODS, DIAGNOSTICS
from operational_provider_transaction_evidence import (COLLECTION as TXN_COLLECTION,
    load_transaction_rows, normalize_operational_payment)
from operational_courier_transitions import COLLECTION as DELIVERY_HISTORY, load_delivery_history
from operational_historical_shipping_fee import NATIVE_EVENTS as SHIPPING_EVENT_BLOCKERS, NATIVE_V2 as SHIPPING_V2_BLOCKERS
from provider_fee_sources import STATEMENT_COLLECTIONS, bind_statement_plan, load_statement_plans
ALLOWED_COLLECTIONS = ALLOWED_COLLECTIONS | {TXN_COLLECTION, DELIVERY_HISTORY, SHIPPING_EVENT_BLOCKERS, SHIPPING_V2_BLOCKERS}
ALLOWED_COLLECTIONS = ALLOWED_COLLECTIONS | STATEMENT_COLLECTIONS

MAX_ROWS = 20000
# Refresh consumers use the canonical payload plus these independent identity,
# freshness, payment and shipping contradictions. Keep each evidence object
# whole: dotted projections could hide malformed shapes or conflicting proof.
REFRESH_ORDER_FIELDS = (
    'user_id', 'raw_by_source', 'g47_salla_snapshot', 'salla_shipping_current',
    'salla_paid_sar_evidence', 'order_id', 'salla_order_id', 'order_number',
    'order_status', 'order_status_slug', 'last_salla_direct_status_source_updated_at',
    'merchant', 'merchant_id', 'store_id', 'salla_shipment_id', 'tracking_number',
    'shipping_number', 'shipping_company_code', 'shipping_company',
    'shipping_status', 'shipment_status',
)
# Existing MZ2 daily-movement codes plus explicitly requested operational types.
# The physical registry is shared. Only explicit operational provenance or a
# known system expense code may augment this picker; store categories cannot.
EXPENSE_CATEGORIES = {
    'fuel': 'بترول ووقود', 'rent': 'إيجارات', 'telecom': 'إنترنت واتصالات',
    'utilities': 'ماء وكهرباء', 'hospitality_food': 'أكل وضيافة',
    'hospitality_drinks': 'مشروبات وضيافة', 'subscriptions': 'اشتراكات وخدمات',
    'maintenance': 'صيانة', 'office': 'قرطاسية ومستلزمات', 'transportation': 'نقل ومواصلات',
    'other': 'مصروفات تشغيلية أخرى', 'bank_fees': 'رسوم بنكية',
    'advertising': 'إعلانات', 'operating_supplies': 'مشتريات تشغيلية بسيطة',
}
RESERVED_EXPENSE_CODES = {'salary', 'shipping', 'inventory', 'tamara_fees', 'tabby_fees', 'gateway_fees', 'cod_fees'}


def instant(value):
    if isinstance(value, dict):
        zone = value.get('timezone')
        value = value.get('date')
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None and zone:
            parsed = parsed.replace(tzinfo=ZoneInfo(zone))
    elif isinstance(value, datetime):
        # Native BSON dates are UTC even when Motor returns them naive.
        parsed = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    else:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('source_timestamp_timezone_missing')
    return parsed.astimezone(timezone.utc)


def number(value):
    if isinstance(value, dict):
        value = value.get('amount')
    if value is None or isinstance(value, bool):
        raise ValueError('source_amount_missing')
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('source_amount_invalid') from None
    if not amount.is_finite() or amount < 0:
        raise ValueError('source_amount_invalid')
    return amount


def cod_facts(raw, total):
    """Exact native paid/remaining sources; never use synthesized DTO totals."""
    payment = raw.get('payment') if isinstance(raw.get('payment'), dict) else {}
    actions = raw.get('payment_actions') if isinstance(raw.get('payment_actions'), dict) else {}
    remaining = raw.get('remaining_action') if isinstance(raw.get('remaining_action'), dict) else {}
    canonical = actions.get('remaining_action') if isinstance(actions.get('remaining_action'), dict) else {}
    refund = actions.get('refund_action') if isinstance(actions.get('refund_action'), dict) else {}
    dues = [number(r['remaining_amount']) for r in (raw, payment, remaining, canonical) if r.get('remaining_amount') is not None]
    paid = [number(r['paid_amount']) for r in (raw, payment, remaining, canonical, refund) if r.get('paid_amount') is not None]
    if len(set(dues)) > 1 or len(set(paid)) > 1:
        raise ValueError('cod_amount_conflict')
    due = dues[0] if dues else total - (paid[0] if paid else Decimal(0))
    collected = paid[0] if paid else total-due
    if due < 0 or collected < 0 or due + collected != total:
        raise ValueError('cod_amount_conflict')
    if not dues and not paid and str(payment.get('collection_status') or raw.get('payment_status') or '') == 'partial':
        raise ValueError('cod_partial_evidence_missing')
    return {'gross': str(total), 'collected': str(collected), 'outstanding': str(due),
            'custody_amount': str(due), 'payment_method': 'cod', 'evidence_ids': [], 'evidence_complete': True}


def _old_delivery_cod(raw):
    """Do not reconstruct historic carrier custody from contradictory money."""
    payment = raw.get('payment') if isinstance(raw.get('payment'), dict) else {}
    for record in (raw, payment):
        status = record.get('status')
        status = (status.get('slug') or status.get('name')) if isinstance(status, dict) else status
        statuses = {str(value or '').strip().lower() for value in (status, record.get('payment_status'),
                    record.get('collection_status'), record.get('payment_collection_status'))}
        if (statuses & {'cancelled', 'canceled', 'refunded', 'partially_refunded', 'returned',
                        'ملغي', 'ملغى', 'مسترجع', 'مرتجع'}
                or any(record.get(key) for key in ('cancelled_at', 'refunded_at', 'refunds'))
                or any(number(record[key]) != 0 for key in ('refunded_amount', 'refund_amount', 'refunded_sar')
                       if record.get(key) is not None)):
            raise ValueError('operational_delivery_cod_history_reconciliation_required')
    amounts = raw.get('amounts') if isinstance(raw.get('amounts'), dict) else {}
    totals = [number(value) for value in (amounts.get('total'), raw.get('total'), raw.get('total_amount'))
              if value is not None]
    if not totals or len(set(totals)) != 1 or totals[0] <= 0:
        raise ValueError('operational_delivery_total_conflict')
    return totals[0], cod_facts(raw, totals[0])


def _event_collection_valid(collection, event, total, now):
    """Validate the actual collection clock, separately from status eligibility."""
    from operational_delivery_evidence import status_delivery
    value, custody = number(collection.get('amount')), number(collection.get('cod_custody_amount'))
    method = collection.get('payment_method')
    collected = instant(collection.get('collected_at'))
    valid_time = (instant(event['order_created_at']) <= collected <= now if status_delivery(event)
                  else collected == instant(event['delivered_at']) and collected <= now)
    if (not isinstance(collection.get('id'), str) or not collection['id'].strip()
            or collection.get('amount_source') != 'unified_orders.remaining_amount'
            or not valid_time
            or collection.get('currency', 'SAR') != event['currency']
            or value > total or custody > value
            or method not in {'cash', 'bank_transfer', 'card_terminal'}
            or (method != 'cash' and custody != 0)):
        raise ValueError('driver_collection_custody_contract_invalid')


def _independent_cod_fixed(terms):
    """Return the fixed fee only when no amount-based band must be selected."""
    from courier_cod_fee_rules import split_tax_amount, validate_courier_cod_fee_tiers
    tiers = terms.get('cod_fee_tiers') or []
    if tiers:
        tiers = validate_courier_cod_fee_tiers(tiers)
        if len(tiers) != 1 or number(tiers[0]['min_amount']) != 0 or tiers[0].get('max_amount') is not None:
            return None
        rule = tiers[0]
        percent, fixed, vat = (number(rule[k]) for k in ('commission_percent', 'fixed_fee', 'vat_percent'))
        included = rule['vat_included']
    elif all(terms.get(k) is not None for k in ('cod_fee_percent', 'cod_fee_fixed_per_order', 'cod_fee_vat_percent')):
        percent = number(terms['cod_fee_percent'])
        fixed, vat = number(terms['cod_fee_fixed_per_order']), number(terms['cod_fee_vat_percent'])
        included = terms.get('cod_fee_vat_included', False)
    else:
        return None
    return percent, split_tax_amount(fixed, vat, inclusive=included)['gross']


def shipping_charge(rate, owner, party, at, cod_amount, setup=None, *, cod_incomplete=False, cod_currency=None):
    if not rate.get('confirmed_by') or not rate.get('confirmed_at'):
        raise ValueError('shipping_contract_approval_missing')
    currency = rate.get('currency')
    if not isinstance(currency, str) or not currency.strip():
        raise ValueError('shipping_contract_currency_missing')
    # A foreign sale is not a conversion basis for a percentage or tier in the
    # courier contract. Keep the independently evidenced base fee available.
    cod_issue = None
    if cod_amount is not None and cod_amount > 0 and cod_currency != currency:
        cod_issue = 'shipping_cod_currency_requires_review' if cod_currency else 'shipping_cod_currency_missing'
    if rate.get('kind') == 'operational_courier_tariff':
        from operational_courier_tariffs import charge
        known = _independent_cod_fixed(rate['terms']) if cod_issue else None
        if known and known[0] == 0:
            cod_issue = None
        quote = charge(rate, None if cod_issue else cod_amount, cod_incomplete or bool(cod_issue))
        if cod_issue:
            quote['component_issues'] = [{'code': cod_issue, 'component': 'cod_commission'}]
            if known and known[1] > 0:
                quote['fee_components'].insert(1, {'id': 'cod_fixed_commission', 'amount': str(known[1]), 'complete': True})
                quote['cost'] = str(number(quote['cost']) + known[1])
        return {**quote, 'currency': currency}
    parts, problems = [], []
    if rate.get('kind') == 'rich':
        # Pure Pydantic/Decimal calculators, no database or financial writers.
        from accounting_shipping_contracts import require_shipping_contract_charges
        version = rate['contract_version']
        if (version.get('id') != rate.get('id') or version.get('approved_by') != rate['confirmed_by']
                or instant(version.get('approved_at')) != instant(rate['confirmed_at'])
                or instant(version.get('effective_from')) != instant(rate['effective_from'])
                or version.get('effective_to') != rate.get('effective_to')):
            raise ValueError('shipping_contract_record_conflict')
        # Validate the entire native terms schema. Invalid/unapproved terms are
        # not repaired; a valid contract can have an uncovered commission band.
        base_quote = require_shipping_contract_charges(version, owner=owner, courier_id=party,
            accounting_at=at, cod_amount=None)
        def fingerprint(value):
            return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        bundle = rate.get('evidence_snapshot') or {}
        items = bundle.get('items') or []
        if (bundle.get('schema') != 'mz2.shipping.evidence.snapshot.v1' or not items
                or fingerprint(items) != bundle.get('sha256')
                or len({p.get('purpose') for p in items}) != len(items)):
            raise ValueError('shipping_contract_evidence_incomplete')
        def verify(purpose):
            candidates = [p for p in items if p.get('purpose') == purpose]
            if len(candidates) != 1:
                raise ValueError(f'shipping_{purpose}_evidence_incomplete')
            item = candidates[0]
            retained = {k: v for k, v in item.items() if k != 'snapshot_sha256'}
            matches = [r for r in (setup or {}).get('contract_evidence', []) if r.get('evidence_id') == item.get('evidence_id')]
            if len(matches) != 1 or fingerprint(retained) != item.get('snapshot_sha256'):
                raise ValueError(f'shipping_{purpose}_evidence_incomplete')
            current = matches[0]
            identity = {k: v for k, v in retained.items() if k != 'size'}
            if (any(current.get(k) != v for k, v in identity.items()) or current.get('revoked') or current.get('revoked_at')
                    or current.get('is_deleted') or current.get('deleted') or current.get('state') != 'approved'
                    or current.get('record_type') != 'accountant_reviewed_shipping_source'
                    or current.get('user_id') != owner or current.get('courier_id') != party):
                raise ValueError(f'shipping_{purpose}_evidence_changed')
        verify('contract')
        if number(version['shipping_cost']) > 0 and number(version['shipping_vat_percent']) > 0:
            verify('shipping_tax')
        base = base_quote['calculation']['shipping_gross']
        parts.append({'id': 'base_shipping', 'amount': str(base), 'complete': True})
        try:
            if cod_issue:
                known = _independent_cod_fixed(base_quote['contract_snapshot'])
                if known and known[1] > 0 and number(version['commission_vat_percent']) > 0:
                    verify('commission_tax')
                if known and known[0] == 0:
                    cod_issue = None
                elif known and known[1] > 0:
                    parts.append({'id': 'cod_fixed_commission', 'amount': str(known[1]), 'complete': True})
            if cod_issue:
                raise ValueError(cod_issue)
            if cod_incomplete:
                raise ValueError('shipping_cod_amount_unavailable')
            if cod_amount is not None and cod_amount > 0 and number(version['commission_vat_percent']) > 0 and any(number(t['commission_percent']) > 0 or number(t['fixed_fee']) > 0 for t in version['cod_fee_tiers']):
                verify('commission_tax')
            quote = require_shipping_contract_charges(version, owner=owner, courier_id=party,
                accounting_at=at, cod_amount=cod_amount)
            commission = quote['calculation']['payable_total'] - base
            parts.append({'id': 'cod_commission', 'amount': str(commission), 'complete': True})
        except (ValueError, KeyError, TypeError) as exc:
            parts.append({'id': 'cod_commission', 'amount': None, 'complete': False})
            problems.append({'code': str(exc), 'component': 'cod_commission'})
    else:
        def gross(value):
            value = value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            if rate.get('vat_included') is False:
                value += (value * number(rate.get('vat_percent')) / 100).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            elif rate.get('vat_included') is not True:
                raise ValueError('shipping_tax_contract_incomplete')
            return value
        base_net = number(rate.get('delivery_fee'))
        base = gross(base_net)
        parts.append({'id': 'base_shipping', 'amount': str(base), 'complete': True})
        try:
            if cod_issue:
                fixed = number(rate.get('cod_fixed_fee'))
                if number(rate.get('cod_percent')) == 0:
                    cod_issue = None
                elif fixed > 0:
                    parts.append({'id': 'cod_fixed_commission', 'amount': str(gross(base_net + fixed) - base), 'complete': True})
            if cod_issue:
                raise ValueError(cod_issue)
            if cod_incomplete:
                raise ValueError('shipping_cod_amount_unavailable')
            cod_net = number(rate.get('cod_fixed_fee')) + cod_amount * number(rate.get('cod_percent')) / 100 if cod_amount is not None and cod_amount > 0 else Decimal(0)
            parts.append({'id': 'cod_commission', 'amount': str(gross(base_net+cod_net)-base), 'complete': True})
        except (ValueError, KeyError, TypeError) as exc:
            parts.append({'id': 'cod_commission', 'amount': None, 'complete': False})
            problems.append({'code': str(exc), 'component': 'cod_commission'})
    total = sum((number(p['amount']) for p in parts if p['complete']), Decimal(0))
    return {'cost': str(total), 'currency': currency, 'fee_components': parts,
            'fee_complete': not problems, 'component_issues': problems}


def invoice_component_amounts(invoice):
    """Allocate the native invoice-level INPUT_VAT exactly across components."""
    if any(invoice.get(k) not in (None, 0, False, '') for k in ('tax', 'vat', 'tax_halalas', 'vat_halalas', 'tax_amount', 'vat_amount')):
        raise ValueError('supplier_invoice_tax_contract_incomplete')
    weights = {}
    for line in invoice.get('lines', []):
        ids = sorted(line.get('piece_ids') or [])
        if not ids or len(ids) != len(set(ids)):
            raise ValueError('supplier_invoice_tax_contract_incomplete')
        for index, pid in enumerate(ids):
            value = number(line.get('product_unit_price_halalas')) if line.get('product_charge_eligible') is not False else Decimal(0)
            weights[(pid, 'product')] = value
            for service in line.get('services') or []:
                total = (number(service['unit_price_halalas']) * number(service['quantity_per_piece']) * len(ids)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
                weights[(pid, f"service:{service['service_id']}")] = (total*(index+1)/len(ids)).quantize(Decimal('1'), rounding=ROUND_HALF_UP) - (total*index/len(ids)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    subtotal = sum(weights.values(), Decimal(0))
    tax = invoice.get('purchase_tax') or {}
    tax_amount = Decimal(0)
    if tax:
        tax_keys = {'treatment', 'amount_halalas', 'entity_id', 'evidence_file_id', 'evidence_sha256', 'confirmed'}
        sha = tax.get('evidence_sha256', '')
        if (set(tax) != tax_keys or tax.get('treatment') != 'INPUT_VAT' or tax.get('confirmed') is not True
                or type(tax.get('amount_halalas')) is not int or tax['amount_halalas'] <= 0
                or not tax.get('entity_id') or not tax.get('evidence_file_id')
                or not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha)
                or number(invoice.get('subtotal_halalas')) != subtotal
                or number(invoice.get('total_halalas')) != subtotal + tax['amount_halalas'] or subtotal <= 0):
            raise ValueError('supplier_invoice_tax_contract_incomplete')
        tax_amount = Decimal(tax['amount_halalas'])
    elif (invoice.get('subtotal_halalas') is not None and number(invoice['subtotal_halalas']) != subtotal
          or invoice.get('total_halalas') is not None and number(invoice['total_halalas']) != subtotal):
        raise ValueError('supplier_invoice_tax_contract_incomplete')
    result, cumulative, prior = {}, Decimal(0), Decimal(0)
    for key, net in sorted(weights.items()):
        cumulative += net
        allocated = (tax_amount*cumulative/subtotal).quantize(Decimal('1'), rounding=ROUND_HALF_UP) if subtotal else Decimal(0)
        share = allocated-prior
        result[key] = {'amount': str((net+share)/100), 'net_amount': str(net/100),
                       'tax_amount': str(share/100), 'gross_amount': str((net+share)/100),
                       **({'tax_evidence': dict(tax)} if tax else {})}
        prior = allocated
    return result


def usable(row):
    return (row.get('status') not in {'inactive', 'archived', 'deleted', 'hidden', 'suspended'}
            and row.get('active') is not False and row.get('is_active') is not False
            and not any(row.get(k) for k in ('archived', 'deleted', 'is_archived', 'is_deleted')))


async def rows(db, owner, collection):
    if not owner or collection not in ALLOWED_COLLECTIONS or collection in {TXN_COLLECTION, DELIVERY_HISTORY, SHIPPING_EVENT_BLOCKERS, SHIPPING_V2_BLOCKERS, 'users', 'settings', 'accounts', 'mz2_operational_bank_accounts'}:
        raise ValueError('operational_source_rejected')
    # Only native fee proof and payout state belong in the financial reader.
    # In particular never load receipt URLs, bank identifiers or note text here.
    if collection in {'store_delivery_driver_earnings', 'store_delivery_driver_settlements'}:
        from operational_shipping_fee_review import FIELDS
        projection = {'_id': 0, **{key: 1 for key in FIELDS[collection]}}
        result = await db[collection].find({'user_id': owner}, projection).to_list(MAX_ROWS + 1)
    else:
        result = await db[collection].find({'user_id': owner}).to_list(MAX_ROWS + 1)
    if len(result) > MAX_ROWS:
        raise ValueError('operational_source_scope_too_large')
    if any(r.get('user_id') != owner for r in result):
        raise ValueError('operational_source_owner_mismatch')
    return result


async def refresh_source_rows(db, owner, collection):
    """Trim only order DTO fields; retain the entire owner cohort and read cap."""
    if collection != 'unified_orders':
        return await rows(db, owner, collection)
    if not owner or collection not in ALLOWED_COLLECTIONS:
        raise ValueError('operational_source_rejected')
    projection = {'_id': 0, **{key: 1 for key in REFRESH_ORDER_FIELDS}}
    result = await db[collection].find({'user_id': owner}, projection).to_list(MAX_ROWS + 1)
    if len(result) > MAX_ROWS:
        raise ValueError('operational_source_scope_too_large')
    if any(row.get('user_id') != owner for row in result):
        raise ValueError('operational_source_owner_mismatch')
    return result


async def entities(db, owner, kind, *, include_unready=False, operational_state=None):
    if kind == 'operating_expense':
        catalog = {code: {'id': code, 'name': name, 'kind': kind, 'currency': 'SAR', 'source': 'mz2_builtin'}
                   for code, name in EXPENSE_CATEGORIES.items()}
        stored = await rows(db, owner, 'expense_categories')
        stored = [r for r in stored if r.get('source') == 'operational_balance'
                  or (r.get('system') is True
                      and str(r.get('code') or '').strip().lower() in EXPENSE_CATEGORIES)]
        counts = Counter(str(r.get('code') or '').strip().lower() for r in stored)
        for row in stored:
            code = str(row.get('code') or '').strip().lower()
            if code in RESERVED_EXPENSE_CODES or not usable(row):
                continue
            if not code or counts[code] != 1:
                raise ValueError('operational_expense_identity_ambiguous')
            catalog[code] = {'id': code, 'name': row.get('name') or 'تصنيف مصروف — إعداد الاسم غير مكتمل', 'kind': kind,
                             'ready': bool(row.get('name')),
                             'currency': 'SAR', 'source': row.get('source') or 'mz2_category_registry'}
        return list(catalog.values())
    if kind == 'owner_withdrawal':
        if not owner:
            raise ValueError('operational_source_rejected')
        query = {'$or': [{'id': owner, 'role': 'owner'}, {'created_by': owner, 'role': 'admin'}]}
        source = await db['users'].find(query, {key: 1 for key in ('id', 'name', 'role', 'created_by', 'is_active', 'disabled', 'status')}).to_list(MAX_ROWS + 1)
        if len(source) > MAX_ROWS:
            raise ValueError('operational_source_scope_too_large')
        result, seen = [], set()
        for row in source:
            if not ((row.get('id') == owner and row.get('role') == 'owner') or (row.get('created_by') == owner and row.get('role') == 'admin')):
                raise ValueError('operational_source_owner_mismatch')
            if not usable(row) or row.get('disabled') is True:
                continue
            if not row.get('id') or row['id'] in seen:
                raise ValueError('operational_entity_identity_ambiguous')
            seen.add(row['id'])
            result.append({'id': row['id'], 'name': row.get('name') or ('المالك' if row['role'] == 'owner' else 'المدير'),
                           'currency': 'SAR', 'kind': kind})
        for row in await rows(db, owner, 'mz2_operational_owner_beneficiaries_v2'):
            if not usable(row):
                continue
            if not row.get('id') or row['id'] in seen or not row.get('name'):
                raise ValueError('operational_entity_identity_ambiguous')
            seen.add(row['id'])
            result.append({'id': row['id'], 'name': row['name'], 'currency': row.get('currency', 'SAR'),
                           'kind': kind, 'source': 'mz2_operational_owner_beneficiaries_v2'})
        return result
    if kind == 'provider':
        return [{'id': p, 'name': {'salla': 'سلة', 'tabby': 'تابي', 'tamara': 'تمارا', 'emkan': 'إمكان'}[p],
                 'kind': kind, 'currency': 'SAR'} for p in PROVIDERS]
    if kind == 'courier':
        setups = await rows(db, owner, 'mz2_shipping_setup_v2')
        if len(setups) > 1:
            raise ValueError('shipping_setup_ambiguous')
        source = [dict(r, id=r.get('courier_key'), currency='SAR',
                       **({'ready': False, 'setup_issue': 'courier_confirmation_required'}
                          if not r.get('confirmed_by') or not r.get('confirmed_at') else {}))
                  for setup in setups for r in setup.get('couriers', [])
                  if external_courier(r) and (include_unready or (r.get('confirmed_by') and r.get('confirmed_at')))]
        if not owner:
            raise ValueError('operational_source_rejected')
        # Same persisted courier identities used by MZ2's shipping contract
        # setup. No default companies, old balances or prices are imported.
        projection = {'_id': 0, 'user_id': 1, 'shipping_companies.name': 1, 'shipping_companies.salla_carrier_key': 1,
                      **{f'shipping_companies.{key}': 1 for key in
                         ('status', 'active', 'is_active', 'archived', 'deleted', 'is_deleted', 'is_archived')}}
        settings = await db['settings'].find({'user_id': owner}, projection).to_list(2)
        if len(settings) > 1 or any(r.get('user_id') != owner for r in settings):
            raise ValueError('shipping_setup_ambiguous')
        configured_rows = (settings[0].get('shipping_companies') or []) if settings else []
        if not isinstance(configured_rows, list) or len(configured_rows) > 1000:
            raise ValueError('shipping_setup_ambiguous')
        configured_keys = set()
        configured_aliases = set()
        native = [r for setup in setups for r in setup.get('couriers', [])]
        for configured in configured_rows:
            if not isinstance(configured, dict):
                raise ValueError('shipping_setup_ambiguous')
            if not usable(configured) or not external_courier(configured):
                continue
            identity = None
            if configured.get('salla_carrier_key') is not None:
                from operational_courier_tariffs import native_matches, saved_identity
                identity = saved_identity(configured)
                if not identity:
                    continue
                if identity.get('identity_error'):
                    raise ValueError('operational_entity_identity_ambiguous')
                key, name = identity['id'], identity['name']
            else:
                key, name = normalize_shipping_company(configured.get('name'))
                if key == 'unknown':
                    continue
            if key in configured_keys:
                raise ValueError('operational_entity_identity_ambiguous')
            configured_keys.add(key)
            configured_aliases.update(identity['keys'] if identity else (key, configured['name']))
            matches = native_matches({'couriers': native}, identity) if identity else [
                r for r in native if r.get('courier_key') == key or
                any(normalize_shipping_company(alias)[0] == key for alias in r.get('salla_carrier_keys', []))]
            if len(matches) > 1:
                raise ValueError('operational_entity_identity_ambiguous')
            if not matches:
                source.append({'id': key, 'name': name, 'currency': 'SAR'})
        if include_unready:
            # Discovery only: reuse the reader's native carrier key, never turn
            # an order label into a confirmed party or import order balances.
            projection = {'user_id': 1, 'raw_by_source.salla_direct.salla_shipping_current': 1,
                          'raw_by_source.salla_direct.shipping': 1, '_id': 0}
            orders = await db['unified_orders'].find({'user_id': owner}, projection).to_list(MAX_ROWS + 1)
            if len(orders) > MAX_ROWS:
                raise ValueError('operational_source_scope_too_large')
            configured = {key for setup in setups for c in setup.get('couriers', [])
                          for key in c.get('salla_carrier_keys', []) if isinstance(key, str)}
            configured.update(configured_aliases)
            discovered = set()
            for record in orders:
                if record.get('user_id') != owner:
                    raise ValueError('operational_source_owner_mismatch')
                raw = (record.get('raw_by_source') or {}).get('salla_direct') or {}
                shipping = raw.get('salla_shipping_current') or raw.get('shipping') or {}
                if not isinstance(shipping, dict):
                    continue
                key = shipping.get('company_code') or shipping.get('company')
                if (not isinstance(key, str) or not key.strip() or key in configured or key in discovered
                        or normalize_shipping_company(key)[0] in configured_keys):
                    continue
                discovered.add(key)
                name = shipping.get('company')
                if not external_courier({'courier_key': key, 'name': name}):
                    continue
                source.append({'id': 'order-carrier:' + hashlib.sha256(key.encode()).hexdigest(),
                               'name': name if isinstance(name, str) and name.strip() else key,
                               'currency': 'SAR', 'ready': False,
                               'setup_issue': 'courier_order_identity_unbound'})
    elif kind == 'ad_account':
        accounts = await rows(db, owner, 'mezan_integration_accounts_v2')
        bindings = await rows(db, owner, 'mz2_ad_account_bindings_v2')
        from operational_ad_funding import bindings as funding_bindings
        bindings, funding_conflicts = funding_bindings(operational_state, owner, bindings)
        policies = await rows(db, owner, 'mz2_ad_automation_policies_v2')
        source = []
        for binding in bindings:
            if not usable(binding) or not binding.get('confirmed_by') or not binding.get('confirmed_at'):
                continue
            matches = [a for a in accounts if a.get('mezan_integration_account_id') == binding.get('integration_account_id')
                       and a.get('external_account_id') == binding.get('platform_account_id')
                       and a.get('provider') == AD_SOURCES.get(binding.get('platform'), (None,))[0]]
            if len(matches) != 1 or matches[0].get('connection_status') != 'connected':
                continue
            account = matches[0]
            metadata = {}
            if binding.get('funding_policy') == 'wallet_first':
                metadata = {'settings_complete': (binding.get('currency') == account.get('currency')
                                                  and binding.get('timezone') == account.get('timezone')),
                            'funding_policy': 'wallet_first'}
            elif binding.get('funding_mode') == 'hybrid':
                try:
                    metadata = hybrid_policy(binding, policies, owner)
                    metadata['settings_complete'] = True
                except (ValueError, TypeError, KeyError):
                    metadata = {'settings_complete': False}
            source.append(dict(binding, **metadata, id=str(binding.get('_id') or ''),
                               name=f"{binding['platform']}: {account['display_name']}" if account.get('display_name') else None,
                               currency=account.get('currency')))
        # Connection evidence can be displayed without granting permission to
        # use an unconfirmed financial identity. Existing binding IDs stay exact.
        linked = {r.get('integration_account_id') for r in source}
        providers = {v[0]: k for k, v in AD_SOURCES.items()}
        for account in accounts if include_unready else []:
            key = account.get('mezan_integration_account_id')
            platform = providers.get(account.get('provider'))
            if (not key or key in linked or not platform or not usable(account)
                    or account.get('connection_status') != 'connected'):
                continue
            source.append(dict(id='unbound:' + str(key),
                               name=f"{platform}: {account.get('display_name') or account.get('external_account_id') or key}",
                               currency=account.get('currency'), platform=platform,
                               integration_account_id=key,
                               platform_account_id=account.get('external_account_id'),
                               ready=False, settings_complete=False,
                               setup_issue='ad_financial_binding_required'))
    elif kind == 'bank':
        from operational_bank_catalog import bank_identities
        source = await bank_identities(db, owner)
    elif kind == 'pos_receivable':
        from operational_pos_identity import pos_candidates
        source = pos_candidates(await rows(db, owner, 'mz2_opening_facts_v2'), owner)
    elif kind in ENTITY_COLLECTIONS:
        source = await rows(db, owner, ENTITY_COLLECTIONS[kind])
        if kind in ('bank', 'cash'):
            source = [r for r in source if r.get('account_type') == kind and r.get('status') == 'active']
    else:
        raise ValueError('operational_entity_kind_invalid')
    result = []
    seen = set()
    for row in source:
        if not usable(row):
            continue
        identity = row.get('id')
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError('operational_entity_identity_ambiguous')
        seen.add(identity)
        # These domestic operational contracts denominate their amounts in SAR.
        currency = row.get('currency') or ('SAR' if kind in ('employee', 'employee_custody', 'supplier', 'store_driver', 'external_person') else None)
        name = row.get('display_name') or row.get('name') or row.get('company_name')
        result.append({'id': identity, 'name': name or 'جهة — إعداد الاسم غير مكتمل',
                       'currency': currency, 'kind': kind,
                       **({'ready': False} if not name or not currency or row.get('ready') is False else {}),
                       **({'setup_issue': row['setup_issue']} if row.get('setup_issue') else {}),
                       **({k: row.get(k) for k in ('platform', 'integration_account_id', 'platform_account_id', 'funding_mode',
                           'wallet_fraction', 'hybrid_policy_id', 'settings_complete', 'funding_policy')}
                          if kind == 'ad_account' else {})})
    return sorted(result, key=lambda r: (r['name'], r['id']))


def issue(out, code, identity=None, **details):
    item = {'code': code, 'source_id': str(identity or ''), 'status': 'incomplete', **details}
    if item not in out['issues']:
        out['issues'].append(item)


def unique_index(records, field):
    counts = Counter(str(r.get(field) or '') for r in records)
    return {str(r[field]): r for r in records if r.get(field) is not None and counts[str(r[field])] == 1}


def hybrid_policy(binding, policies, owner, business_date=None):
    matches = [p for p in policies if p.get('platform') == binding.get('platform')
               and p.get('integration_account_id') == binding.get('integration_account_id')]
    if not matches or binding.get('hybrid_policy') != 'explicit_split':
        raise ValueError('ad_hybrid_split_incomplete')
    version = max(int(p.get('version', 0)) for p in matches)
    latest = [p for p in matches if p.get('version') == version]
    if len(latest) != 1:
        raise ValueError('ad_hybrid_policy_ambiguous')
    policy = latest[0]
    if (policy.get('status') != 'active' or policy.get('confirmed_by') != owner or not policy.get('confirmed_at')
            or policy.get('binding_version') != binding.get('version') or not policy.get('id')
            or not policy.get('start_date')):
        raise ValueError('ad_hybrid_split_incomplete')
    if business_date and policy['start_date'] > business_date:
        raise ValueError('ad_hybrid_policy_not_effective')
    fraction = number(policy.get('wallet_fraction'))
    if fraction > 1:
        raise ValueError('ad_hybrid_fraction_invalid')
    return {'wallet_fraction': str(fraction), 'hybrid_policy_id': policy['id'],
            'hybrid_policy_version': policy['version'], 'hybrid_policy_evidence': policy.get('evidence'),
            'hybrid_confirmed_at': policy['confirmed_at'], 'hybrid_confirmed_by': policy['confirmed_by']}


def _option_label(value):
    return ' '.join(str(value or '').replace('_', ' ').strip().casefold().split())


def _option_value_label(value):
    label = _option_label(value)
    # Only the explicit boolean value is bilingual; option names and IDs are not.
    return {'نعم': 'yes', 'لا': 'no'}.get(label, label)


def _option_binding_token(binding):
    return (str(binding.get('option_id') or ''), str(binding.get('value_id') or ''),
            _option_label(binding.get('option_name')), _option_value_label(binding.get('value_name')))


def option_tokens(item):
    """Retain each selection's IDs with its labels; a label cannot override an ID."""
    tokens = set()
    for row in item.get('options_raw') or item.get('options') or []:
        if not isinstance(row, dict):
            raise ValueError('order_option_shape_invalid')
        option = row.get('option') if isinstance(row.get('option'), dict) else {}
        value = row.get('value') if isinstance(row.get('value'), dict) else {}
        option_id = row.get('option_id') or row.get('id') or option.get('id')
        option_name = row.get('option_name') or row.get('name') or option.get('name')
        values = row.get('values') or row.get('value')
        values = values if isinstance(values, list) else [values]
        for entry in values:
            entry_dict = entry if isinstance(entry, dict) else value
            value_id = row.get('value_id') or entry_dict.get('id')
            value_name = row.get('value_name') or entry_dict.get('name') or entry_dict.get('value') or entry
            if (option_id or option_name) and (value_id or value_name):
                tokens.add((str(option_id or ''), str(value_id or ''),
                            _option_label(option_name), _option_value_label(value_name), 'raw'))
    for key, values in (item.get('options_normalized') or {}).items():
        for value in values if isinstance(values, list) else [values]:
            if key and value not in (None, ''):
                tokens.add(('', '', _option_label(key), _option_value_label(value), 'normalized'))
    return tokens


def option_matches(binding, tokens):
    bound_option, bound_value, bound_name, bound_label = _option_binding_token(binding)
    for option, value, name, label, _ in tokens:
        if (option and bound_option and option != bound_option) or (value and bound_value and value != bound_value):
            continue
        same_option = bool(option and bound_option) or bool(name and name == bound_name)
        same_value = bool(value and bound_value) or bool(label and label == bound_label)
        if same_option and same_value:
            return True
    return False


def selected_cost_options(bindings, tokens):
    """Resolve missing IDs only within one known option/value identity."""
    native_pairs = {(option, value) for option, value, _, _, origin in tokens
                    if origin == 'raw' and option and value}
    native_values = {option: {value for key, value in native_pairs if key == option}
                     for option, _ in native_pairs}
    # Normalized labels repeat native selections. They must not activate another
    # option with the same display name when native IDs already identify this one.
    observations = [token for token in tokens if token[4] != 'normalized' or not any(
        _option_binding_token(binding)[:2] in native_pairs and option_matches(binding, [token])
        for binding in bindings)]
    selected, boolean_values = set(), {}
    for token in observations:
        option, value, name, label, _ = token
        if option:
            candidates = [(i, b) for i, b in enumerate(bindings)
                          if str(b.get('option_id') or '') == option]
            if not candidates:
                candidates = [(i, b) for i, b in enumerate(bindings)
                              if not b.get('option_id') and name and _option_label(b.get('option_name')) == name]
                if not candidates and any(name and _option_label(b.get('option_name')) == name for b in bindings):
                    raise ValueError('mz2_cost_option_identity_conflict')
        else:
            candidates = [(i, b) for i, b in enumerate(bindings)
                          if name and _option_label(b.get('option_name')) == name]
            if len({str(b.get('option_id')) for _, b in candidates if b.get('option_id')}) > 1:
                raise ValueError('mz2_cost_option_identity_ambiguous')
        matched = [(i, b) for i, b in candidates if option_matches(b, [token])]
        if not value and len({str(b.get('value_id')) for _, b in matched if b.get('value_id')}) > 1:
            raise ValueError('mz2_cost_option_identity_ambiguous')
        boolean = [(i, b) for i, b in candidates if _option_value_label(b.get('value_name')) in {'yes', 'no'}]
        for _, b in boolean:
            bound_option, bound_value, bound_name, bound_label = _option_binding_token(b)
            identity = ('id', bound_option) if bound_option else ('name', bound_name)
            observed = boolean_values.setdefault(identity, set())
            if label in {'yes', 'no'}:
                observed.add(label)
            if (bound_option, bound_value) in native_pairs:
                # Native IDs establish this value even if the raw row has no
                # labels. A contrary normalized label remains a conflict.
                observed.add(bound_label)
            if len(observed) > 1 or len(native_values.get(bound_option, ())) > 1:
                raise ValueError('mz2_cost_option_value_conflict')
        if not value and any(_option_value_label(b.get('value_name')) in {'yes', 'no'}
                and native_values.get(str(b.get('option_id') or ''))
                and str(b.get('value_id') or '') not in native_values[str(b.get('option_id') or '')]
                for _, b in matched):
            raise ValueError('mz2_cost_option_identity_conflict')
        if not matched:
            if value and any(label and _option_value_label(b.get('value_name')) == label for _, b in candidates):
                raise ValueError('mz2_cost_option_identity_conflict')
            if boolean and not value and label not in {'yes', 'no'}:
                raise ValueError('mz2_cost_option_value_unverified')
        selected.update(i for i, _ in matched)
    # One binding applies once, even when the same choice appears in raw and
    # normalized input. Duplicate persisted binding identities still fail below.
    return [binding for i, binding in enumerate(bindings) if i in selected]


def _option_needs_catalog(bindings, token):
    option, value, name, label, _ = token
    if option and value:
        return False
    candidates = [b for b in bindings if str(b.get('option_id') or '') == option] if option else [
        b for b in bindings if name and _option_label(b.get('option_name')) == name]
    if any(option_matches(b, [token]) for b in candidates):
        return False
    # Preserve the existing explicit yes/no contract, including a declined yes.
    return not (candidates and label in {'yes', 'no'} and all(
        _option_value_label(b.get('value_name')) in {'yes', 'no'} for b in candidates))


def _catalog_recovery_preserves_ids(item):
    """Unsupported explicit IDs must not disappear into a label-only fallback."""
    for row in item.get('options_raw') or item.get('options') or []:
        option = row.get('option') if isinstance(row.get('option'), dict) else {}
        represented = row.get('option_id') or row.get('id') or option.get('id')
        explicit = [row.get('option_id'), row.get('id'), option.get('id')]
        if isinstance(row.get('raw'), dict) or any(v is not None and v != '' and
                (not _catalog_identity(v) or str(v) != str(represented))
                for v in explicit):
            raise ValueError('mz2_cost_option_identity_conflict')
        values = row.get('values') or row.get('value')
        for value in values if isinstance(values, list) else [values]:
            value = value if isinstance(value, dict) else {}
            represented = row.get('value_id') or value.get('id')
            explicit = [row.get('value_id'), row.get('option_value_id'), value.get('id'),
                        value.get('value_id'), value.get('option_value_id')]
            if any(v is not None and v != '' and (not _catalog_identity(v) or str(v) != str(represented)) for v in explicit):
                raise ValueError('mz2_cost_option_identity_conflict')


def _catalog_identity(value):
    return isinstance(value, (str, int)) and not isinstance(value, bool) and bool(str(value).strip())


def _verified_option_tokens(data, product_id, item, bindings, tokens):
    if not bindings:
        return tokens
    if not tokens:
        raise ValueError('mz2_cost_option_value_unverified')
    if not any(_option_needs_catalog(bindings, token) for token in tokens):
        return tokens
    _catalog_recovery_preserves_ids(item)
    product = item.get('product') if isinstance(item.get('product'), dict) else {}
    native_product = item.get('parent_product_id') or item.get('product_id') or product.get('id')
    if not _catalog_identity(native_product) or str(native_product) != product_id:
        raise ValueError('mz2_cost_option_catalog_unverified')
    if product_id in data.get('_option_catalog_errors', set()):
        raise ValueError('mz2_cost_option_catalog_unverified')
    candidates = [p for p in data.get('mezan_products_v2', []) if isinstance(p, dict)
                  and _catalog_identity(p.get('salla_product_id')) and str(p['salla_product_id']) == product_id]
    if len(candidates) != 1:
        raise ValueError('mz2_cost_option_catalog_unverified')
    from report_option_identity import catalog_options
    try:
        for source in (candidates[0].get('raw_salla'), candidates[0].get('raw_salla_details'), candidates[0]):
            if not isinstance(source, dict):
                continue
            options = source.get('options', [])
            if options is None:
                continue
            if not isinstance(options, list):
                raise ValueError('invalid_options')
            for option in options:
                values = option.get('values', [])
                if values is None:
                    values = []
                if not _catalog_identity(option.get('id')) or not isinstance(values, list) or any(
                        not isinstance(v, dict) or not _catalog_identity(v.get('id')) for v in values):
                    raise ValueError('invalid_option_identity')
                for entry in [option, *values]:
                    labels = entry.get('identity_labels', [])
                    translations = entry.get('translations', {})
                    if (labels is not None and (not isinstance(labels, list) or any(not isinstance(v, str) for v in labels))
                            or translations is not None and (not isinstance(translations, dict)
                                or any(not isinstance(v, dict) for v in translations.values()))):
                        raise ValueError('invalid_option_labels')
        catalog = catalog_options(candidates[0])
    except (TypeError, AttributeError, ValueError):
        raise ValueError('mz2_cost_option_catalog_unverified') from None
    bound = {str(b.get('option_id')) for b in bindings if b.get('option_id')}
    if not bound or not bound <= catalog.keys():
        raise ValueError('mz2_cost_option_catalog_unverified')
    # During recovery, compare every observation against the same identities.
    # Existing bound labels remain evidence for those exact IDs, never new IDs.
    for binding in bindings:
        option, value = str(binding.get('option_id') or ''), str(binding.get('value_id') or '')
        if option not in catalog or value not in catalog[option]['values']:
            raise ValueError('mz2_cost_option_catalog_unverified')
        if binding.get('option_name'):
            catalog[option]['labels'].add(_option_label(binding['option_name']))
        if binding.get('value_name'):
            catalog[option]['values'][value].add(_option_value_label(binding['value_name']))
    resolved = set()
    for token in tokens:
        option, value, name, label, origin = token
        named = [key for key, entry in catalog.items()
            if name in {_option_label(v) for v in entry['labels']}]
        if option and (option not in catalog or named and option not in named):
            raise ValueError('mz2_cost_option_identity_conflict')
        choices = [option] if option else named
        if len(choices) != 1:
            raise ValueError('mz2_cost_option_identity_ambiguous')
        option = choices[0]
        if option in bound:
            values = catalog[option]['values']
            named_values = [key for key, labels in values.items()
                if label in {_option_value_label(v) for v in labels}]
            if value and (value not in values or named_values and value not in named_values):
                raise ValueError('mz2_cost_option_identity_conflict')
            matches = [value] if value else named_values
            if len(matches) != 1:
                raise ValueError('mz2_cost_option_value_unverified')
            value = matches[0]
        # A uniquely identified independent option cannot activate a paid one.
        resolved.add((option, value, name, label, origin))
    for option, entry in catalog.items():
        if not entry.get('multiple') and len({value for oid, value, _, _, _ in resolved if oid == option and value}) > 1:
            raise ValueError('mz2_cost_option_value_conflict')
    return resolved


async def _load_option_cost_catalog(db, owner, data, raw_orders, start, now, bundles):
    """Read only needed owner/product identities; a bad catalogue is a local gap."""
    calculation_checkpoint()
    cpu_budget = CPUWorkBudget(PARSER_BUDGET_MS)
    from pymongo.errors import PyMongoError
    bindings = {}
    for binding in data['mezan_product_option_cost_bindings_v2']:
        await cpu_budget.checkpoint()
        calculation_checkpoint()
        bindings.setdefault(str(binding.get('salla_product_id')), []).append(binding)
    wanted = set()
    for raw in raw_orders:
        await cpu_budget.checkpoint()
        calculation_checkpoint()
        try:
            if not start < instant(raw.get('date') or raw.get('created_at') or raw.get('order_date')) <= now:
                continue
        except (ValueError, TypeError):
            continue
        number = str(raw.get('reference_id') or raw.get('order_number') or '')
        for item in raw.get('items') or []:
            await cpu_budget.checkpoint()
            calculation_checkpoint()
            if not isinstance(item, dict):
                continue
            native = item.get('id') or item.get('item_id') or item.get('order_item_id')
            if not native or (number, f'salla:{number}:{native}') in bundles:
                continue
            product = item.get('product') if isinstance(item.get('product'), dict) else {}
            native_product = item.get('parent_product_id') or item.get('product_id') or product.get('id')
            if not _catalog_identity(native_product):
                continue
            pid = str(native_product)
            try:
                if pid in bindings and any(_option_needs_catalog(bindings[pid], token) for token in option_tokens(item)):
                    wanted.add(pid)
            except (ValueError, TypeError):
                pass  # The normal per-line cost validator reports this shape.
    data['mezan_products_v2'], data['_option_catalog_errors'] = [], set()
    errors = data['_option_catalog_errors']
    if not wanted:
        return
    if len(wanted) > MAX_ROWS:
        errors.update(wanted)
        return
    projection = {'_id': 0, 'user_id': 1, 'salla_product_id': 1, 'options': 1,
                  'raw_salla.options': 1, 'raw_salla_details.options': 1}
    wanted = sorted(wanted)
    seen, read_count = set(), 0
    # Each ID can have a string and numeric storage form; at most 250 query terms.
    for offset in range(0, len(wanted), 125):
        await cpu_budget.checkpoint()
        calculation_checkpoint()
        batch = wanted[offset:offset + 125]
        values = list(batch)
        values.extend(int(pid) for pid in batch if len(pid) <= 19 and pid.isascii() and pid.isdecimal()
                      and int(pid) <= 9223372036854775807 and str(int(pid)) == pid)
        limit = min(len(batch), MAX_ROWS - read_count)
        if limit <= 0:
            errors.update(wanted[offset:])
            break
        try:
            records = await db['mezan_products_v2'].find(
                {'user_id': owner, 'salla_product_id': {'$in': values}}, projection).to_list(length=limit + 1)
        except PyMongoError:
            errors.update(batch)
            continue
        read_count += len(records)
        if len(records) > limit:
            errors.update(batch)
            continue
        for record in records:
            await cpu_budget.checkpoint()
            calculation_checkpoint()
            pid = str(record.get('salla_product_id')) if isinstance(record, dict) else ''
            if pid not in batch:
                errors.update(batch)
                continue
            if not _catalog_identity(record.get('salla_product_id')) or record.get('user_id') != owner or pid in seen:
                errors.add(pid)
                continue
            seen.add(pid)
            data['mezan_products_v2'].append(record)


def cost_components(data, product_id, item, profile):
    """Resolve only MZ2-authoritative amounts; services retain independent identity."""
    from operational_product_cost_policy import require_sar_cost
    require_sar_cost(profile)
    variant = str(item.get('variant_id') or '')
    product_cost = number((profile.get('variant_costs') or {}).get(variant, profile.get('base_cost')))
    resources = unique_index(data['mezan_cost_resources_v2'], 'id')
    tokens = option_tokens(item)
    links = [dict(r, mode='resource') for r in data['mezan_product_resource_bindings_v2'] if str(r.get('salla_product_id')) == product_id]
    bindings = [r for r in data['mezan_product_option_cost_bindings_v2'] if str(r.get('salla_product_id')) == product_id]
    tokens = _verified_option_tokens(data, product_id, item, bindings, tokens)
    links += selected_cost_options(bindings, tokens)
    seen, services, resource_costs = set(), {}, {}
    for link in links:
        identity = link.get('id')
        if not identity or identity in seen:
            raise ValueError('mz2_cost_binding_ambiguous')
        seen.add(identity)
        quantity = number(link.get('quantity', 1))
        if quantity <= 0:
            raise ValueError('mz2_cost_binding_quantity_invalid')
        if link.get('mode') == 'direct':
            require_sar_cost(link)
            product_cost += number(link.get('direct_amount')) * quantity
            continue
        resource_id = str(link.get('resource_id') or '')
        resource = resources.get(resource_id)
        if link.get('mode') != 'resource' or not resource or not usable(resource):
            raise ValueError('mz2_cost_resource_missing')
        require_sar_cost(resource)
        amount = number(resource.get('unit_cost')) * quantity
        if resource_id in resource_costs:
            # MZ2 forbids product+option duplicate resource binding; do not repair silently.
            raise ValueError('mz2_cost_resource_binding_conflict')
        resource_costs[resource_id] = amount
        if resource.get('kind') == 'service':
            services[resource_id] = {'cost': amount, 'quantity': quantity}
        else:
            product_cost += amount
    return product_cost, services


def physical_order_item_id(piece):
    value = piece.get('order_item_id')
    return value.strip() if isinstance(value, str) else None


def physical_unit_index(piece):
    """Native piece unit numbers can be stored as integers or decimal strings."""
    value = piece.get('unit_index')
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value) if 0 < value <= 999_999_999 else None
    if isinstance(value, str) and len(value) <= 9 and value.isascii() and value.isdecimal():
        return int(value) or None
    return None


def supplier_receipt_proven(piece, as_of=None):
    """Match durable physical receiving proof, not mere supplier assignment.

    A generic status='received' or supplier_id alone is not proof; a future
    receiving timestamp cannot establish a current payable estimate.
    """
    if not (piece and piece.get('supplier_dispatch_status') == 'received'
            and piece.get('execution_status') == 'received_from_supplier'
            and piece.get('received_at')):
        return False
    if as_of is None:
        return True
    try:
        return instant(piece['received_at']) <= instant(as_of)
    except (ValueError, TypeError):
        return False


def append_components(order, base_id, product_cost, services, quantity, supplier_id=None, piece=None, as_of=None):
    received = supplier_receipt_proven(piece, as_of)
    # Supplier assignment is a workflow intent, never a supplier receivable/payable.
    # Before physical receipt, all expected product costs remain one unreceived pool.
    financial_supplier = supplier_id if received else None
    order['items'].append({'id': base_id, 'supplier_id': financial_supplier,
                           'supplier_receipt_proven': received, 'cost_currency': 'SAR',
                           'cost': str(product_cost), 'quantity': str(quantity)})
    plans = {str(r.get('service_id')): r for r in (piece or {}).get('services', [])}
    service_ids = set(services) | set(plans)
    for service_id in sorted(service_ids):
        plan = plans.get(service_id)
        if plan:
            unit = number(plan.get('reference_unit_cost'))
            cost = unit * number(plan.get('required_quantity'))
            party = plan.get('completed_by_supplier_id') if plan.get('status') == 'completed' else (
                supplier_id if service_id in (piece or {}).get('supplier_service_ids', []) else None)
        else:
            cost, party = services[service_id]['cost'], None
        service_received = bool(received and (not plan or plan.get('status') == 'completed'))
        order['items'].append({'id': f'{base_id}:service:{service_id}',
                               'supplier_id': party if service_received else None,
                               'supplier_receipt_proven': service_received, 'cost_currency': 'SAR',
                               'cost': str(cost), 'quantity': str(quantity), 'component': 'service'})


def _attach_delivery_sources(data, setup, owner, raw, order, out, now, total, cod, *, delivery_event=None, started_at=None):
    """Attach existing delivery money rules without importing old sale effects."""
    from operational_delivery_evidence import status_delivery
    identity, order_number = order['id'], order['order_number']
    created = instant(order['created_at'])
    undated = status_delivery(delivery_event)
    try:
        shipping = raw.get('salla_shipping_current') or raw.get('shipping') or {}
        carrier_key = shipping.get('company_code') or shipping.get('company')
        parties = [c for c in setup.get('couriers', []) if usable(c) and c.get('confirmed_by')
                   and c.get('confirmed_at') and matches_courier_key(c, carrier_key)]
        assignments = [a for a in data['store_delivery_assignments'] if str(a.get('order_number')) == order_number
                       and a.get('active') is not False and a.get('status') in {'assigned', 'out_for_delivery', 'delivered'}]
        party_kind, party_id = 'courier', parties[0]['courier_key'] if len(parties) == 1 else None
        if delivery_event:
            party_kind, party_id = delivery_event['party_type'], delivery_event['party_id']
            if party_kind == 'courier' and cod:
                from operational_delivery_evidence import delivery_requires_cod_proof
                if delivery_requires_cod_proof(raw, delivery_event):
                    order.pop('cod', None)
                    order.pop('cod_amount', None)
                    issue(out, 'courier_cod_collection_proof_required', identity, component='customer_collection',
                          required_action='طابق تحصيل الدفع عند الاستلام مع كشف الشركة والبوليصة قبل احتسابه.')
            assignments = [a for a in assignments if a.get('id') == delivery_event.get('assignment_id')]
        if not assignments and data['_driver_fee_review'].has_other_assignment(order):
            issue(out, 'driver_fee_evidence_conflict', identity, component='shipping_fee',
                  recognition_state='confirmed')
            return
        if party_id:
            order['carrier'] = {'id': party_id, 'kind': party_kind, 'cost': None, 'fee_complete': False}
        if identity in data.get('_prepaid_orders', set()) and not assignments:
            order['prepaid_shipping'] = True
            if delivery_event and party_id:
                order['carrier'].update(payment_mode='prepaid', currency='SAR')
            else:
                # Completion can pay shipping without asserting delivery or COD.
                order.pop('carrier', None)
            return
        if len(assignments) == 1:
            party_kind, party_id = 'store_driver', assignments[0].get('driver_id')
            if party_id:
                order['carrier'] = {'id': party_id, 'kind': party_kind, 'cost': None, 'fee_complete': False}
            if cod and 'cod' in order:
                order['cod_amount'] = '0'
                order['cod']['custody_amount'] = '0'
                order['cod']['evidence_complete'] = False
            if assignments[0].get('status') == 'delivered':
                order['status'] = 'delivered'
                collections = [c for c in data['store_delivery_collections'] if c.get('assignment_id') == assignments[0].get('id')
                               and c.get('driver_id') == party_id
                               and (not delivery_event or (str(c.get('order_id')) == identity
                                    and str(c.get('order_number')) == order_number))]
                if delivery_event and cod and 'cod' in order and len(collections) == 1:
                    try:
                        _event_collection_valid(collections[0], delivery_event, total, now)
                    except (ValueError, TypeError, KeyError, InvalidOperation):
                        # A broken collection record cannot erase a separately
                        # proven delivery service or supply zero commission.
                        order.pop('cod', None)
                        order.pop('cod_amount', None)
                        issue(out, 'driver_collection_custody_contract_invalid', identity,
                              order_id=identity, order_number=order_number,
                              component='driver_custody', recognition_state='confirmed')
                if cod and 'cod' in order:
                    order['cod_amount'] = '0'
                    order['cod']['custody_amount'] = '0'
                    if len(collections) == 1:
                        collection = collections[0]
                        method = collection.get('payment_method')
                        amount = number(collection.get('amount'))
                        custody = number(collection.get('cod_custody_amount'))
                        if (not collection.get('id') or instant(collection.get('collected_at')) > now or amount > total or custody > amount
                                or method not in {'cash', 'bank_transfer', 'card_terminal'}
                                or (method != 'cash' and custody != 0)
                                or (delivery_event and collection.get('currency', 'SAR') != order['currency'])):
                            raise ValueError('driver_collection_custody_contract_invalid')
                        order['cod_amount'] = str(custody if method == 'cash' else Decimal(0))
                        order['cod'].update(custody_amount=order['cod_amount'], payment_method=method,
                            evidence_ids=[collection['id']], collection_amount=str(amount), evidence_complete=True)
                        verified = custody if method == 'cash' else (amount if collection.get('payment_confirmed') is True else Decimal(0))
                        prior_paid = number(order['cod']['collected'])
                        # Native collection_requirements records the FULL remaining
                        # responsibility at handover, independently of physical cash
                        # evidence. Never interpret an unproven partial amount that way.
                        if collection.get('amount_source') == 'unified_orders.remaining_amount':
                            collected = max(prior_paid, total-amount+verified)
                        else:
                            collected = prior_paid
                            issue(out, 'driver_collection_remaining_snapshot_incomplete', identity,
                                  component='customer_collection', recognition_state='confirmed')
                        order['cod'].update(source_collected=str(prior_paid), source_outstanding=order['cod']['outstanding'],
                            collected=str(collected), outstanding=str(total-collected),
                            collection_confirmed=method == 'cash' or collection.get('payment_confirmed') is True)
                    elif len(collections) != 1:
                        issue(out, 'driver_collection_custody_evidence_incomplete', identity,
                              order_id=identity, order_number=order_number, component='driver_custody')
        if len(assignments) > 1:
            issue(out, 'store_driver_assignment_ambiguous', identity)
            party_id = None
            order.pop('carrier', None)
        when = created if undated else instant(delivery_event['delivered_at']) if delivery_event else instant(
            shipping.get('delivered_at') or raw.get('updated_at') or raw.get('date_updated') or created)
        if undated and delivery_event.get('recognition_basis') == 'owner_confirmed_after_start':
            when = max(created, instant(started_at))
        if not undated and len(assignments) == 1 and assignments[0].get('status') == 'delivered' and assignments[0].get('delivered_at'):
            when = instant(assignments[0]['delivered_at'])
        rates = [r for r in setup.get('contracts', []) if r.get('status') == 'approved'
                 and r.get('party_type') == party_kind and r.get('party_id') == party_id
                 and r.get('context') == 'delivery' and instant(r['effective_from']) <= when
                 and (not r.get('effective_to') or when < instant(r['effective_to']))]
        rate = rates[0] if len(rates) == 1 else None
        if undated:
            through = instant(delivery_event['recorded_at'])
            overlapping = [r for r in setup.get('contracts', []) if r.get('status') == 'approved'
                           and r.get('party_type') == party_kind and r.get('party_id') == party_id
                           and r.get('context') == 'delivery' and instant(r['effective_from']) <= through
                           and (not r.get('effective_to') or when < instant(r['effective_to']))]
            # The fee is certain only if one approved tariff covers every
            # possible delivery instant. A recording time must not pick a new
            # price for an undated historical service.
            rate = (rates[0] if len(rates) == len(overlapping) == 1
                    and (not rates[0].get('effective_to') or through < instant(rates[0]['effective_to'])) else None)
            rates = overlapping
            currencies = {r.get('currency') for r in rates}
            if party_id and len(currencies) == 1 and None not in currencies:
                order['carrier']['currency'] = next(iter(currencies))
        completed_assignment = assignments[0] if (len(assignments) == 1 and party_kind == 'store_driver'
            and (assignments[0].get('status') == 'delivered' or undated)) else None
        quote = None
        if party_id and rate:
            if (completed_assignment or (delivery_event and party_kind == 'store_driver')) and rate.get('currency') != order['currency']:
                raise ValueError('operational_delivery_fee_currency_conflict')
            cod_incomplete = cod and 'cod' not in order
            quote = shipping_charge(rate, owner, party_id, when,
                number(order['cod'].get('collection_amount', order['cod']['outstanding'])) if cod and not cod_incomplete else None,
                setup, cod_incomplete=cod_incomplete, cod_currency=order['currency'])
        if completed_assignment and data.get('_driver_fee_review'):
            checked = data['_driver_fee_review'].check(completed_assignment, order, quote, len(rates),
                ambiguous_setup=len(data['mz2_shipping_setup_v2']) > 1, delivery_event=delivery_event)
            if checked is not None:
                evidence, reason = checked
                if reason:
                    if reason == 'driver_fee_history_review_required':
                        from operational_historical_shipping_fee import register_candidate
                        register_candidate(out, data['_fee_review_numbers'], delivery_event,
                                           rate=rate, quote=quote, earning=evidence)
                    from operational_shipping_fee_review import REASONS
                    issue(out, reason, identity, component='shipping_fee', recognition_state='confirmed',
                          driver_fee_review=evidence, required_action=REASONS[reason])
                    return
                from operational_historical_shipping_fee import register_candidate
                register_candidate(out, data['_fee_review_numbers'], delivery_event,
                                   rate=rate, quote=quote, earning=evidence)
                order['native_driver_earning'] = evidence
                order['delivered_at'] = evidence['delivered_at']
                if quote is None:
                    # The mobile delivery writer freezes the owner's driver price
                    # and earns it on delivery. It doesn't earn a separate COD fee.
                    base = evidence['candidate_amount']
                    quote = {'cost': base, 'currency': evidence['currency'], 'fee_complete': True, 'component_issues': [],
                             'fee_components': [{'id': 'base_shipping', 'amount': base, 'complete': True}]}
        if party_id and quote:
            if party_kind == 'courier' and delivery_event and order_number in data['_fee_review_numbers']:
                from operational_historical_shipping_fee import register_candidate
                register_candidate(out, data['_fee_review_numbers'], delivery_event, rate=rate, quote=quote)
            problems = quote.pop('component_issues')
            order['carrier'] = {'id': party_id, 'kind': party_kind, **({'contract_id': rate['id']} if rate and rate.get('id') else {}), **quote}
            for problem in problems:
                issue(out, problem['code'], identity, component=problem['component'],
                      recognition_state='confirmed' if order.get('status') in {'delivered', 'تم التوصيل'} else 'expected')
        elif carrier_key or assignments:
            if undated and party_id and rates:
                issue(out, 'shipping_delivery_tariff_period_unproven', identity, component='shipping_fee',
                      required_action='التوصيل مثبت؛ طابق تعرفة واحدة تغطي الفترة الممكنة من إنشاء الطلب حتى إثبات حالته، أو اعتمد دليل الأجرة الأصلي.')
            else:
                issue(out, 'shipping_identity_or_rate_incomplete', identity, component='shipping_fee' if party_id else 'carrier_identity')
    except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
        reason = str(exc)
        code = reason if reason and all(c.islower() or c == '_' for c in reason) else 'shipping_contract_component_incomplete'
        issue(out, code, identity, component='driver_custody' if reason.startswith('driver_') else 'shipping_fee',
              order_id=identity, order_number=order_number, reason=reason,
              recognition_state='confirmed' if order.get('status') in {'delivered', 'تم التوصيل'} else 'expected')


async def collect_sources(db, owner, started_at, as_of, baselines=None, operational_state=None, historical_fee_order=None):
    calculation_checkpoint()
    cpu_budget = CPUWorkBudget(PARSER_BUDGET_MS)
    start, now = instant(started_at), instant(as_of)
    if now < start:
        raise ValueError('operational_source_time_range_invalid')
    out = {key: [] for key in ('orders', 'delivery_events', 'prepaid_shipping_events', 'completed_shipping_events',
                               'completed_shipping_candidates', 'supplier_receipts', 'supplier_returns', 'employees',
                               'ad_snapshots', 'recurring', 'fee_policies', 'issues')}
    out['delivery_event_coverage'] = {'version': 1, 'started_at': start.isoformat(), 'as_of': now.isoformat()}
    out['ad_balance_sar_basis'] = {}
    # No service with side effects is called: collection access is allowlisted.
    # The inventory catalogue is read on demand, not on each financial sync.
    with refresh_stage('source_primary_reads'):
        from operational_read_runtime import load_collections
        source_names = sorted(ALLOWED_COLLECTIONS - BUNDLE_READ_COLLECTIONS - STATEMENT_COLLECTIONS - {TXN_COLLECTION,
            DELIVERY_HISTORY, SHIPPING_EVENT_BLOCKERS, SHIPPING_V2_BLOCKERS, 'users', 'settings', 'expense_categories',
            'mezan_products_v2', 'mz2_opening_facts_v2', 'accounts', 'mz2_operational_bank_accounts'})
        data = await load_collections(db, owner, source_names, reader=refresh_source_rows)
    with refresh_stage('source_fee_settings'):
        fee_settings = await db['settings'].find({'user_id': owner}, {
            '_id': 0, 'user_id': 1, 'payment_methods': 1, 'updated_at': 1}).to_list(2)
        out['provider_fee_settings'] = {}
        modes = {row.get('fee_source_mode') for row in data['mz2_provider_fee_policies_v2'] if row.get('fee_source_mode')}
        out['provider_fee_source_mode'] = 'mz2_policies' if 'mz2_policies' in modes else 'legacy_settings'
        if len(modes) > 1:
            issue(out, 'provider_fee_source_mode_ambiguous')
        if len(fee_settings) == 1 and fee_settings[0].get('user_id') == owner:
            out['provider_fee_settings'] = {key: fee_settings[0].get(key) for key in ('payment_methods', 'updated_at')}
        elif fee_settings:
            issue(out, 'provider_fee_settings_ambiguous')
    with refresh_stage('source_setup'):
        from operational_shipping_fee_review import DriverEarnings, courier_issue, hold_prior_courier_event
        from operational_historical_shipping_fee import APPROVALS
        data['_fee_review_numbers'] = {r['candidate']['order_number'] for r in (operational_state or {}).get(APPROVALS, {}).values()}
        if historical_fee_order:
            data['_fee_review_numbers'].add(historical_fee_order)
        data['_driver_fee_review'] = DriverEarnings(data, owner, operational_state, start, now)
        from operational_ad_funding import bindings as funding_bindings
        data['mz2_ad_account_bindings_v2'], conflicts = funding_bindings(
            operational_state, owner, data['mz2_ad_account_bindings_v2'])
        for identity in conflicts:
            await cpu_budget.checkpoint()
            calculation_checkpoint()
            issue(out, 'operational_ad_funding_authority_conflict', identity)
        setup_rows = data['mz2_shipping_setup_v2']
        setup = setup_rows[0] if len(setup_rows) == 1 else {}
        if len(setup_rows) > 1:
            issue(out, 'shipping_setup_ambiguous')
        else:
            from operational_courier_tariffs import with_tariffs
            setup, tariff_conflicts = with_tariffs(setup, owner, operational_state)
            for party in tariff_conflicts:
                await cpu_budget.checkpoint()
                calculation_checkpoint()
                issue(out, 'operational_courier_tariff_authority_conflict', party, component='shipping_fee')
        prepaid_keys = {str(key).casefold() for rate in setup.get('contracts', [])
                        if rate.get('context') == 'order_completed' and rate.get('payment_mode') == 'prepaid'
                        for key in rate.get('keys', [])}
        completion_keys = {str(key).casefold() for rate in setup.get('contracts', [])
                           if rate.get('party_type') == 'courier' and rate.get('context') in {'delivery', 'order_completed'}
                           for key in rate.get('keys', [])}
        data['_prepaid_orders'] = set()
        profiles = unique_index(data['mezan_product_cost_profiles_v2'], 'salla_product_id')
        suppliers = unique_index(data['mezan_suppliers_v2'], 'id')
        pieces = [r for r in data['mezan_preparation_pieces_v1'] if not r.get('experiment_mode') and not r.get('experiment_run_id')]
        pieces_by_order_item = await prepare_in_thread(index_preparation_pieces, pieces, physical_order_item_id)
        order_numbers = {}
        raw_orders = [(r.get('raw_by_source') or {}).get('salla_direct') for r in data['unified_orders']]
        raw_orders = [r for r in raw_orders if isinstance(r, dict)]
    with refresh_stage('source_statements'):
        from operational_payment_evidence import provider_for
        plan_providers = {policy.get('provider') for document in data['mz2_provider_fee_policies_v2']
            for policy in document.get('policies', []) if policy.get('status') == 'active'
            and policy.get('event_type', 'capture') == 'capture' and policy.get('plan_id')
            and policy.get('user_id') in (None, owner)}
        plan_orders = []
        for raw in raw_orders:
            await cpu_budget.checkpoint()
            calculation_checkpoint()
            payment = raw.get('payment') if isinstance(raw.get('payment'), dict) else {}
            methods = [payment.get('method'), raw.get('payment_method')]
            if isinstance(raw.get('payment_methods'), list):
                methods.extend(leg.get('payment_method') for leg in raw['payment_methods'] if isinstance(leg, dict))
            if plan_providers.intersection(provider_for(method) for method in methods):
                plan_orders.append(raw.get('reference_id') or raw.get('order_number'))
        statement_plans = await load_statement_plans(db, owner, providers=plan_providers,
            max_rows=MAX_ROWS, order_numbers=plan_orders) if plan_orders else {}
    with refresh_stage('source_delivery_history'):
        from operational_delivery_evidence import conflicting_shipments, created_after_start_delivery, status_delivery
        from operational_delivery_confirmation import PREFIX as DELIVERY_CONFIRMATION_PREFIX, date_gap_identity
        confirmation_facts = (operational_state or {}).get('engine', {}).get('facts', {})
        manual_driver_numbers = {str(record.get('order_number')) for key, record in confirmation_facts.items()
                                if key.startswith(DELIVERY_CONFIRMATION_PREFIX) and isinstance(record, dict)
                                and record.get('party_type') == 'store_driver'}
        shipment_conflicts = conflicting_shipments(data['unified_orders'], operational_state)
        undated_drivers = {str(a.get('order_number')) for a in data['store_delivery_assignments']
                           if a.get('active') is True and a.get('delivered_at') in (None, '')
                           and a.get('status') in {'assigned', 'out_for_delivery', 'delivered'}}
        undated_drivers.update(str(event.get('order_number'))
            for key, event in (operational_state or {}).get('engine', {}).get('facts', {}).items()
            if key.startswith('delivery_event:') and status_delivery(event) and event.get('party_type') == 'store_driver')
        undated_drivers.update(manual_driver_numbers)
        status_order_numbers = set()
        for raw in (raw_orders if undated_drivers else ()):
            await cpu_budget.checkpoint()
            calculation_checkpoint()
            number_key = str(raw.get('reference_id') or raw.get('order_number') or '')
            if number_key not in undated_drivers:
                continue
            try:
                if created_after_start_delivery(raw, start, now) or number_key in manual_driver_numbers:
                    status_order_numbers.add(number_key)
            except (ValueError, TypeError):
                pass  # The ordinary order parser retains its exact source issue.
        delivery_history = await load_delivery_history(db, owner, data['unified_orders'], max_rows=MAX_ROWS,
                                                       completion_keys=completion_keys, status_order_numbers=status_order_numbers)
    with refresh_stage('source_transactions'):
        txn_by_order = await load_transaction_rows(db, owner, raw_orders, start, now, to_instant=instant, max_rows=MAX_ROWS)
    with refresh_stage('source_native_links'):
        from operational_remaining_bundle_sources import load_remaining_bundles
        from operational_native_purchase_link_service import verified_saved_links
        native_matches = await verified_saved_links(db, owner, operational_state, as_of=as_of)
        for invoice_id, match in native_matches.items():
            await cpu_budget.checkpoint()
            calculation_checkpoint()
            if not match.get('matched'):
                out['issues'].append({'code': 'native_purchase_operational_link_changed', 'source_id': invoice_id,
                    'scope': 'received_products', 'reasons': match.get('issues'),
                    'required_action': 'أعد معاينة مطابقة الفاتورة والسداد؛ لم تُرحّل الفروق تلقائيًا'})
    with refresh_stage('source_bundles'):
        bundle_costs = await load_remaining_bundles(db, owner, raw_orders, now, preparation_pieces=pieces,
            operational_state=operational_state, native_matches=native_matches)
    with refresh_stage('source_option_catalog'):
        await _load_option_cost_catalog(db, owner, data, raw_orders, start, now, bundle_costs)
    with refresh_stage('source_orders'):
        from operational_customer_tender_coverage import finish_coverage, new_coverage, observe_nonprovider
        customer_tenders = out["customer_tender_coverage"] = new_coverage(start.isoformat(), now.isoformat())
        counted_order_ids, inspected_order_ids = set(), set()
        from operational_completed_shipping_admission import build_review_index
        completion_review_index = build_review_index(operational_state) if operational_state else None
        source_ids = Counter(str(r.get('id') or '') for r in raw_orders)
        source_numbers = Counter(str(r.get('reference_id') or r.get('order_number') or '') for r in raw_orders)
        bank_catalog = None
        recorded_fee_lookup = await prepare_in_thread(build_recorded_courier_fee_lookup, operational_state)
        for source in data['unified_orders']:
            await cpu_budget.checkpoint()
            calculation_checkpoint()
            raw = (source.get('raw_by_source') or {}).get('salla_direct')
            if not isinstance(raw, dict):
                issue(out, 'canonical_salla_source_missing', source.get('order_number'))
                continue
            try:
                created = instant(raw.get('date') or raw.get('created_at') or raw.get('order_date'))
                if created > now:
                    continue
                identity = str(raw.get('id') or '')
                order_number = str(raw.get('reference_id') or raw.get('order_number') or '')
                if created > start and identity and identity not in counted_order_ids:
                    counted_order_ids.add(identity)
                    customer_tenders["source_order_count"] += 1
                    customer_tenders["uninspected_source_orders"] += 1
                if not identity or not order_number:
                    raise ValueError('order_identity_missing')
                if source_ids[identity] != 1 or source_numbers[order_number] != 1:
                    raise ValueError('canonical_order_identity_ambiguous')
                watermark = source.get('g47_salla_snapshot') or {}
                if watermark.get('requires_authoritative_refresh'):
                    raise ValueError('canonical_salla_refresh_required')
                current_shipping = source.get('salla_shipping_current') or raw.get('salla_shipping_current') or {}
                from operational_completed_shipping_admission import CompletionReviewRequired
                from operational_completed_shipping_sources import completion_issue
                saved_prepaid = (operational_state or {}).get('engine', {}).get('facts', {}).get('prepaid_shipping:' + identity)
                if saved_prepaid or isinstance(current_shipping, dict) and any(
                        str(current_shipping.get(key) or '').casefold() in prepaid_keys for key in ('company_code', 'company_name')):
                    data['_prepaid_orders'].add(identity)
                    from operational_prepaid_shipping import source_event
                    try:
                        prepaid, charge = source_event(data, setup, source, raw, operational_state, owner, start, now,
                                                       delivery_history.get(order_number, ()), shipment_conflicts,
                                                       review_index=completion_review_index)
                        if not prepaid:
                            data['_prepaid_orders'].discard(identity)
                        if charge:
                            out['prepaid_shipping_events'].append(charge)
                    except CompletionReviewRequired as exc:
                        out['completed_shipping_candidates'].append(exc.candidate)
                        completion_issue(out, str(exc), exc.candidate)
                    except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
                        code = str(exc) if str(exc).startswith(('operational_prepaid_shipping_', 'operational_shipping_completion_')) else 'operational_prepaid_shipping_evidence_incomplete'
                        issue(out, code, identity, component='prepaid_shipping',
                              required_action='راجع إثبات تنفيذ الطلب وتعرفة الشحن والصندوق المعتمد قبل احتساب الخصم.')
                        courier_issue(out, code, raw, source, setup=setup, owner=owner)
                if identity not in data['_prepaid_orders']:
                    from operational_completed_shipping import deferred_source_event
                    try:
                        charge = deferred_source_event(data, setup, source, raw, operational_state, owner, start, now,
                                                       delivery_history.get(order_number, ()), shipment_conflicts,
                                                       review_index=completion_review_index)
                        if charge:
                            out['completed_shipping_events'].append(charge)
                    except CompletionReviewRequired as exc:
                        out['completed_shipping_candidates'].append(exc.candidate)
                        completion_issue(out, str(exc), exc.candidate)
                    except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
                        code = str(exc) if str(exc).startswith(('operational_shipping_completion_', 'operational_delivery_', 'operational_prepaid_shipping_')) else 'operational_shipping_completion_evidence_incomplete'
                        issue(out, code, identity, component='shipping_fee',
                              required_action='طابق هوية الشركة ووقت تم التنفيذ والتعرفة المعتمدة قبل احتساب أجرة الشحن.')
                        courier_issue(out, code, raw, source, setup=setup, owner=owner)
                # Supplier approval and actual delivery each have their own cutoff.
                # Keep old orders out of the ordinary sales/estimate/payment stream.
                order_numbers[order_number] = identity
                if created <= start:
                    from operational_delivery_evidence import resolve_delivery_event
                    event = resolve_delivery_event(raw, source=source, owner=owner,
                        assignments=data['store_delivery_assignments'], setup=setup,
                        started_at=started_at, as_of=as_of, history=delivery_history.get(order_number, ()),
                        shipment_conflicts=shipment_conflicts,
                        frozen_event=(operational_state or {}).get('engine', {}).get('facts', {}).get('delivery_event:' + identity),
                        frozen_shipping=(operational_state or {}).get('engine', {}).get('facts', {}).get('shipping:' + identity),
                        recorded_fee=recorded_fee_lookup(identity),
                        owner_confirmation=confirmation_facts.get(DELIVERY_CONFIRMATION_PREFIX + identity))
                    if event is None:
                        continue
                    # Native drivers retain their SAR-only contract. A courier's
                    # earned fee is independently denominated by its approved rate.
                    if event['party_type'] == 'store_driver' and event['currency'] != 'SAR':
                        raise ValueError('operational_delivery_currency_unsupported')
                    order = {'id': identity, 'order_number': order_number, 'created_at': created.isoformat(),
                        'updated_at': max(instant(raw.get('updated_at') or raw.get('date_updated') or created),
                                          instant(event.get('delivered_at') or event.get('recorded_at'))).isoformat(),
                        'delivered_at': event['delivered_at'], 'status': 'delivered',
                        'currency': event['currency'], 'delivery_event': event}
                    payment = raw.get('payment') if isinstance(raw.get('payment'), dict) else {}
                    method = str(payment.get('method') or raw.get('payment_method') or '').lower()
                    cod = method in {'cod', 'cash_on_delivery', 'cashondelivery', 'cash on delivery',
                                     'الدفع عند الاستلام', 'دفع عند الاستلام', 'دفع عند الإستلام'}
                    total = None
                    if cod:
                        order['cod_required'] = True
                        try:
                            total, order['cod'] = _old_delivery_cod(raw)
                            order['cod_amount'] = order['cod']['outstanding']
                            if event['party_type'] == 'courier' and total > 0 and number(order['cod_amount']) == 0:
                                # Paid customer status does not prove remittance by
                                # the carrier. Do not freeze an unproved zero debt.
                                order.pop('cod')
                                order.pop('cod_amount')
                                raise ValueError('operational_delivery_cod_history_reconciliation_required')
                        except (ValueError, TypeError) as exc:
                            issue(out, str(exc) if str(exc).startswith('operational_delivery_') else
                                  'cod_collection_contract_incomplete', identity)
                    else:
                        from operational_balance_bank_orders import transfer_bank
                        if not transfer_bank(method)[0] and not any(method in aliases for aliases in PAYMENT_METHODS.values()):
                            # Earned base shipping can be proved independently; do
                            # not turn an unknown payment method into zero COD fees.
                            cod = order['cod_required'] = True
                            issue(out, 'operational_delivery_payment_method_incomplete', identity)
                    _attach_delivery_sources(data, setup, owner, raw, order, out, now, total, cod,
                                             delivery_event=event, started_at=started_at)
                    if hold_prior_courier_event(operational_state, event, order.get('carrier')):
                        courier_issue(out, 'courier_fee_historical_review_required', raw, source,
                                     carrier=order.get('carrier'), event=event, setup=setup, owner=owner)
                        if event.get('recognition_basis') == 'owner_confirmed_after_start':
                            carrier = order.get('carrier')
                            if carrier:
                                carrier.update(cost=None, fee_complete=False, fee_recognition_held=True)
                                carrier.pop('fee_components', None)
                            out['delivery_events'].append(order)
                    else:
                        out['delivery_events'].append(order)
                    continue
                status_obj = raw.get('status')
                status = (status_obj.get('slug') or status_obj.get('name')) if isinstance(status_obj, dict) else status_obj
                source_total = (raw.get('amounts') or {}).get('total')
                order = {'id': identity, 'order_number': order_number, 'created_at': created.isoformat(),
                         'updated_at': instant(raw.get('updated_at') or raw.get('date_updated') or created).isoformat(),
                         'status': status, 'currency': raw.get('currency') or (source_total.get('currency') if isinstance(source_total, dict) else None),
                         'items': []}
                if not order['currency']:
                    raise ValueError('order_currency_missing')
                for item in raw.get('items', []):
                    await cpu_budget.checkpoint()
                    calculation_checkpoint()
                    source_item_id = item.get('id') or item.get('item_id') or item.get('order_item_id')
                    if not source_item_id:
                        issue(out, 'order_item_stable_identity_missing', identity)
                        continue
                    item_id = f'salla:{order_number}:{source_item_id}'
                    product = item.get('product') or {}
                    product_id = str(item.get('parent_product_id') or item.get('product_id') or product.get('id') or '')
                    bundle = bundle_costs.get((order_number, item_id))
                    if bundle is not None:
                        order['items'].extend(bundle['items'])
                        if not bundle['complete']:
                            order['items_complete'] = False
                        for problem in bundle['issues']:
                            await cpu_budget.checkpoint()
                            calculation_checkpoint()
                            issue(out, problem['code'], problem['source_id'], scope=problem['scope'],
                                  order_number=order_number, required_action=problem['required_action'])
                        continue
                    profile = profiles.get(product_id, {})
                    try:
                        cost, services = cost_components(data, product_id, item, profile)
                        quantity = number(item.get('quantity'))
                    except ValueError as exc:
                        order['items_complete'] = False
                        issue(out, str(exc) if str(exc) == 'mz2_product_cost_currency_requires_review' else 'mz2_product_cost_incomplete', item_id)
                        continue
                    matching = pieces_by_order_item.get((order_number, item_id), [])
                    if matching:
                        units = {physical_unit_index(piece) for piece in matching}
                        if quantity != len(matching) or units != set(range(1, len(matching) + 1)):
                            order['items_complete'] = False
                            issue(out, 'preparation_piece_coverage_ambiguous', item_id)
                            continue
                        for piece in matching:
                            await cpu_budget.checkpoint()
                            calculation_checkpoint()
                            supplier = piece.get('supplier_id')
                            if supplier and supplier not in suppliers and supplier_receipt_proven(piece, now):
                                order['items_complete'] = False
                                issue(out, 'mz2_supplier_identity_missing', piece.get('id'))
                                continue
                            append_components(order, str(piece['id']), cost, services, 1, supplier, piece, as_of=now)
                    else:
                        append_components(order, item_id, cost, services, quantity)
                payment = raw.get('payment') if isinstance(raw.get('payment'), dict) else {}
                method = str(payment.get('method') or raw.get('payment_method') or '').lower()
                total_value = (raw.get('amounts') or {}).get('total', raw.get('total'))
                total = number(total_value)
                normalized_payment, payment_issues = normalize_operational_payment(
                    raw, order['currency'], total, created, now, owner=owner, order_number=order_number,
                    provider_order_id=identity, rows=txn_by_order.get(order_number, ()), source=source)
                if (not normalized_payment and "provider_split:" + identity in
                        (operational_state or {}).get("engine", {}).get("facts", {})):
                    # Changing the order's payment method cannot turn a frozen split receipt into bank cash or COD.
                    payment_issues.append({"code": "provider_payment_method_conflict"})
                payment_identity_conflict = any(row['code'] in {
                    'provider_payment_method_conflict', 'provider_payment_shape_invalid',
                    'provider_split_payment_review_required'} for row in payment_issues)
                observe_nonprovider(customer_tenders, raw, source, order, total_value, normalized_payment, owner,
                                    blocked=payment_identity_conflict, payment_issues=payment_issues)
                inspected_order_ids.add(identity)
                from operational_balance_bank_orders import transfer_bank, resolve, native_paid_amount, QUALIFYING
                native_bank = method == 'bank'
                if not payment_identity_conflict and transfer_bank(method)[0] and (native_bank or status in QUALIFYING):
                    try:
                        paid = total
                        if native_bank:
                            paid = native_paid_amount(raw, source, owner=owner, order_number=order_number,
                                provider_order_id=identity, currency=order['currency'], total=total_value, as_of=as_of)
                            if bank_catalog is None:
                                from operational_bank_catalog import bank_identities
                                bank_catalog = await bank_identities(db, owner, include_routing=True)
                        bank = resolve(data, owner, payment.get('method') or raw.get('payment_method'),
                                       order['currency'], raw=raw, banks=bank_catalog or ())
                        if paid <= 0 or paid != paid.quantize(Decimal('.01')):
                            raise ValueError('operational_order_bank_amount_invalid')
                        order['bank_credit'] = {'bank_id': bank['id'], 'bank_name': bank['name'], 'amount': str(paid)}
                    except ValueError as exc:
                        issue(out, str(exc), identity)
                cod = method in {'cod', 'cash_on_delivery', 'cashondelivery', 'cash on delivery', 'الدفع عند الاستلام', 'دفع عند الاستلام', 'دفع عند الإستلام'}
                if cod:
                    order['cod_required'] = True
                    try:
                        if payment_identity_conflict:
                            raise ValueError('provider_payment_method_conflict')
                        order['cod'] = cod_facts(raw, total)
                        order['cod_amount'] = order['cod']['outstanding']
                    except (ValueError, TypeError):
                        issue(out, 'cod_collection_contract_incomplete', identity)
                if normalized_payment is not None:
                    order['payment'] = bind_statement_plan(normalized_payment, statement_plans, order_number)
                for payment_issue in payment_issues:
                    await cpu_budget.checkpoint()
                    calculation_checkpoint()
                    issue(out, payment_issue['code'], identity, order_number=order_number, currency=order['currency'],
                          scope='provider_payment',
                          message=DIAGNOSTICS.get(payment_issue['code'], 'بيانات الدفع تحتاج مطابقة؛ لا يعني ذلك أن المبلغ صفر'),
                          required_action='راجع إثبات التحصيل ومرجع العملية وتاريخها وخطة الدفع من المصدر المعتمد',
                          **{key: value for key, value in payment_issue.items() if key not in {'code', 'currency'}})
                event = None
                allow_shipping = True
                current = source.get('salla_shipping_current') or raw.get('salla_shipping_current') or {}
                from operational_delivery_evidence import (DELIVERED, _status, _text, created_after_start_delivery,
                                                            resolve_delivery_event)
                native_driver = data['_driver_fee_review'].requires_delivery_proof(order)
                # Saved evidence must be revalidated even when a later source status
                # or assignment loses the fields that originally admitted delivery.
                # Test presence, so malformed saved proofs cannot restore a fallback.
                saved_delivery_proof = ('delivery_event:' + identity in confirmation_facts
                                        or DELIVERY_CONFIRMATION_PREFIX + identity in confirmation_facts)
                if saved_delivery_proof or native_driver or status in DELIVERED or (isinstance(current, dict) and _status(current.get('status')) in DELIVERED):
                    # Legacy drivers have a separate native collection contract. External
                    # couriers must prove this exact current waybill and delivery instant.
                    assigned = [a for a in data['store_delivery_assignments']
                                if str(a.get('order_number')) == order_number and a.get('active') is not False
                                and a.get('status') in {'assigned','out_for_delivery','delivered'}]
                    # The earlier contract-only producer binds one owner-scoped
                    # assignment by the unique canonical order number. Retain its
                    # independent collection contract without promoting a missing
                    # order_id into the new strict delivery-status proof.
                    legacy_driver = (not native_driver and len(assigned) == 1
                        and assigned[0].get('user_id') == owner
                        and assigned[0].get('order_id') in (None, '')
                        and 'delivery_event:' + identity not in (operational_state or {}).get('engine', {}).get('facts', {})
                        and 'owner_delivery_after_start:' + identity not in (operational_state or {}).get('engine', {}).get('facts', {})
                        and _text(assigned[0].get('id')) and _text(assigned[0].get('driver_id'))
                        and data['_driver_fee_review'].assignment_ids[assigned[0]['id']] == 1)
                    if saved_delivery_proof or native_driver or not assigned or (created_after_start_delivery(raw, started_at, as_of) and not legacy_driver):
                        try:
                            if not native_driver and status not in DELIVERED:
                                raise ValueError('operational_delivery_order_shipment_status_conflict')
                            event = resolve_delivery_event(raw, source=source, owner=owner,
                                assignments=data['store_delivery_assignments'], setup=setup, started_at=started_at, as_of=as_of,
                                history=delivery_history.get(order_number, ()), shipment_conflicts=shipment_conflicts,
                                frozen_event=(operational_state or {}).get('engine', {}).get('facts', {}).get('delivery_event:' + identity),
                                frozen_shipping=(operational_state or {}).get('engine', {}).get('facts', {}).get('shipping:' + identity),
                                recorded_fee=recorded_fee_lookup(identity),
                                owner_confirmation=confirmation_facts.get(DELIVERY_CONFIRMATION_PREFIX + identity))
                            if event is None:
                                raise ValueError('operational_delivery_timestamp_missing')
                            saved = (operational_state or {}).get('engine', {}).get('facts', {}).get('delivery_event:' + identity)
                            if saved is not None and saved != event:
                                raise ValueError('operational_delivery_confirmed_evidence_changed')
                            order['delivered_at'] = event['delivered_at']
                        except (ValueError, KeyError, TypeError) as exc:
                            allow_shipping = False
                            order['shipping_evidence_held'] = True
                            code = str(exc) if str(exc).startswith('operational_delivery_') else 'operational_delivery_evidence_invalid'
                            issue(out, code, identity, component='shipping_fee')
                            courier_issue(out, code, raw, source, setup=setup, owner=owner)
                            date_gap_identity(out, code, raw, data['store_delivery_assignments'], owner,
                                record=confirmation_facts.get(DELIVERY_CONFIRMATION_PREFIX + identity), started_at=started_at)
                if allow_shipping:
                    _attach_delivery_sources(data, setup, owner, raw, order, out, now, total, cod,
                                             delivery_event=event, started_at=started_at)
                    if hold_prior_courier_event(operational_state, event, order.get('carrier')):
                        courier_issue(out, 'courier_fee_historical_review_required', raw, source,
                                     carrier=order.get('carrier'), event=event, setup=setup, owner=owner)
                        # Hold only this courier payable. A current verified
                        # delivery can still establish the customer's COD custody.
                        carrier = order.get('carrier')
                        if carrier:
                            carrier.update(cost=None, fee_complete=False, fee_recognition_held=True)
                            carrier.pop('fee_components', None)
                    if event and order.get('carrier'):
                        order['delivery_event'] = event
                out['orders'].append(order)
            except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
                code = str(exc)
                issue(out, code, raw.get('id'))
                if code.startswith('operational_delivery_') and code not in {'operational_delivery_driver_assignment_required',
                        'operational_delivery_owner_mismatch','operational_delivery_order_identity_conflict'}:
                    courier_issue(out, code, raw, source, setup=setup, owner=owner)
                    date_gap_identity(out, code, raw, data['store_delivery_assignments'], owner,
                        record=confirmation_facts.get(DELIVERY_CONFIRMATION_PREFIX + str(raw.get('id'))), started_at=started_at)
    with refresh_stage('source_invoices'):
        # An issued, approved MZ2 invoice is the sole confirmation authority.
        # Current piece state may have changed after invoicing and cannot revoke it.
        piece_index = unique_index(pieces, 'id')
        invoice_counts = Counter(r.get('id') for r in data['mezan_supplier_invoices_v2'])
        coverage = {}
        for invoice in data['mezan_supplier_invoices_v2']:
            await cpu_budget.checkpoint()
            calculation_checkpoint()
            if invoice.get('experiment_mode') or invoice.get('experiment_run_id') or not invoice.get('approved_at'):
                continue
            if invoice.get('status') not in {None, 'payable_posted'}:
                continue
            if invoice.get('supplier_id') not in suppliers:
                issue(out, 'supplier_invoice_supplier_identity_incomplete', invoice.get('id'))
                continue
            if operational_state:
                from operational_native_purchase_link import collision_with_saved_link
                if collision_with_saved_link(operational_state, invoice.get('supplier_id'),
                        invoice.get('invoice_number'), invoice.get('id')):
                    issue(out, 'supplier_native_document_link_conflict', invoice.get('id'),
                        scope='received_products', required_action='المستند مرتبط بفاتورة شراء مسجلة؛ طابق الهوية قبل احتساب دين ثانٍ')
                    continue
            if not invoice.get('id') or invoice_counts[invoice.get('id')] != 1:
                issue(out, 'supplier_invoice_identity_ambiguous', invoice.get('id'))
                continue
            covered = [pid for line in invoice.get('lines', []) for pid in line.get('piece_ids', [])]
            if len(covered) != len(set(covered)):
                issue(out, 'supplier_invoice_piece_coverage_ambiguous', invoice.get('id'))
                continue
            try:
                accepted = instant(invoice['approved_at'])
            except (ValueError, TypeError):
                issue(out, 'supplier_invoice_approval_time_incomplete', invoice.get('id'))
                continue
            if accepted > now:
                continue
            # Both native receiving writers denominate supplier invoices in SAR.
            # Older compatible rows may omit the field; this is the supplier
            # contract, never the customer's order currency or FX.
            currency = invoice.get('currency', 'SAR')
            if currency != 'SAR':
                issue(out, 'supplier_invoice_currency_conflict', invoice.get('id'))
                continue
            try:
                component_amounts = invoice_component_amounts(invoice)
            except (ValueError, KeyError, TypeError):
                issue(out, 'supplier_invoice_tax_contract_incomplete', invoice.get('id'))
                continue
            for line in invoice.get('lines', []):
                await cpu_budget.checkpoint()
                calculation_checkpoint()
                ids = line.get('piece_ids') or []
                if line.get('product_price_authority') != 'mezan_v2' or not ids or len(ids) != len(set(ids)):
                    issue(out, 'supplier_receipt_component_contract_incomplete', invoice.get('id'))
                    continue
                try:
                    if line.get('quantity') is not None and number(line['quantity']) != len(ids):
                        raise ValueError('supplier_invoice_quantity_conflict')
                    unit_halalas = number(line.get('product_unit_price_halalas'))
                    product_total = unit_halalas * len(ids)
                    if line.get('product_total_halalas') is not None and number(line['product_total_halalas']) != product_total:
                        raise ValueError('supplier_invoice_product_total_conflict')
                    service_totals = {}
                    for service in line.get('services') or []:
                        await cpu_budget.checkpoint()
                        calculation_checkpoint()
                        service_id = service['service_id']
                        if not service_id or service_id in service_totals:
                            raise ValueError('supplier_invoice_service_identity_conflict')
                        total = (number(service['unit_price_halalas']) * number(service['quantity_per_piece']) * len(ids)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
                        if service.get('total_halalas') is not None and number(service['total_halalas']) != total:
                            raise ValueError('supplier_invoice_service_total_conflict')
                        service_totals[service_id] = total
                    if line.get('total_halalas') is not None and number(line['total_halalas']) != product_total + sum(service_totals.values()):
                        raise ValueError('supplier_invoice_line_total_conflict')
                except (ValueError, KeyError):
                    issue(out, 'supplier_invoice_amount_or_quantity_incomplete', invoice.get('id'))
                    continue
                # Sort stable IDs so source line ordering cannot change penny allocation.
                for piece_id in sorted(ids):
                    await cpu_budget.checkpoint()
                    calculation_checkpoint()
                    piece = piece_index.get(piece_id, {})
                    order_id = order_numbers.get(str(piece.get('order_number')))
                    if not order_id:
                        issue(out, 'supplier_receipt_order_evidence_incomplete', invoice.get('id'))
                        continue
                    try:
                        # A service invoice may predate the eligibility flag. Its
                        # zero-priced product must not disqualify a proven base charge.
                        has_product = line.get('product_charge_eligible') is not False and (unit_halalas > 0 or not service_totals)
                        components = [('product', piece_id)] if has_product else []
                        components.extend((f'service:{service_id}', f'{piece_id}:service:{service_id}')
                                          for service_id in service_totals)
                        # Historical batches can carry different piece UUIDs for the
                        # same order line and physical unit. Keep posted receipt IDs,
                        # but compare claims across the permanent unit identity.
                        physical_key = (piece_id,)
                        unit = physical_unit_index(piece)
                        order_item_id = physical_order_item_id(piece)
                        if order_item_id and unit is not None:
                            physical_key = (order_item_id, unit)
                        for component, item_id in components:
                            await cpu_budget.checkpoint()
                            calculation_checkpoint()
                            coverage.setdefault((order_id, physical_key, component), set()).add((invoice['id'], item_id))
                            if accepted < start:
                                continue
                            out['supplier_receipts'].append({'id': f"{invoice['id']}:{piece_id}:{component}",
                                'order_id': order_id, 'item_id': item_id, 'supplier_id': invoice['supplier_id'],
                                **component_amounts[(piece_id, component)], 'currency': currency,
                                'accepted_at': accepted.isoformat(), 'invoice_id': invoice['id'],
                                'invoice_number': invoice.get('invoice_number') or invoice.get('reference'),
                                'component_fully_invoiced': True})
                    except (ValueError, KeyError):
                        issue(out, 'supplier_receipt_evidence_incomplete', invoice.get('id'))
        # Native supplier_piece_product_charge_eligible charges a physical product
        # once, even across supplier reassignment. Completed services likewise retain
        # their original invoice. Two issued invoices claiming the same component
        # are contradictory evidence, not two independent obligations.
        current_components = {(receipt['order_id'], receipt['item_id']) for receipt in out['supplier_receipts']}
        ambiguous = {(order_id, item_id) for (order_id, _unit, _component), claims in coverage.items()
                     if len(claims) > 1 for _, item_id in claims} & current_components
        for key in sorted(ambiguous):
            await cpu_budget.checkpoint()
            calculation_checkpoint()
            issue(out, 'supplier_cross_invoice_component_coverage_ambiguous', ':'.join(key),
                  scope='received_products',
                  required_action='طابق سطر القطعة وخدمتها في الفواتير مع الافتتاح والسداد قبل اعتماد أي فرق.')
        if ambiguous:
            out['supplier_receipts'] = [r for r in out['supplier_receipts'] if (r['order_id'], r['item_id']) not in ambiguous]
    with refresh_stage('source_other_sources'):
        for document in data['mz2_provider_fee_policies_v2']:
            await cpu_budget.checkpoint()
            calculation_checkpoint()
            for policy in document.get('policies', []):
                await cpu_budget.checkpoint()
                calculation_checkpoint()
                if policy.get('user_id') not in (None, owner):
                    issue(out, 'provider_fee_owner_mismatch', policy.get('id'))
                    continue
                out['fee_policies'].append({k: v for k, v in policy.items() if k not in ('_id', 'user_id')})
        await prepare_in_thread(_employees, data, out)
        await prepare_in_thread(_advertising, data, out, start, now, baselines)
        await prepare_in_thread(_recurring, data, out, start, now)
    with refresh_stage('source_native_overlaps'):
        from operational_shipping_fee_review import hold_native_fee_overlaps
        await hold_native_fee_overlaps(db, owner, out, operational_state, max_rows=MAX_ROWS)
        completion_candidates = (out['prepaid_shipping_events'] + out['completed_shipping_events']
                                 + out['completed_shipping_candidates'])
        if completion_candidates:
            from operational_historical_shipping_fee import native_conflicts
            overlaps = await native_conflicts(db, owner, completion_candidates, max_rows=MAX_ROWS)
            out['completed_shipping_native_conflicts'] = overlaps
            for charge in completion_candidates:
                await cpu_budget.checkpoint()
                calculation_checkpoint()
                if charge['order_number'] in overlaps:
                    completion_issue(out, ('operational_prepaid_shipping_native_overlap' if charge['schema'] == 'operational_prepaid_shipping_v1'
                                          else 'operational_shipping_completion_native_overlap'), charge,
                                     required_action=overlaps[charge['order_number']])
            for key in ('prepaid_shipping_events', 'completed_shipping_events'):
                await cpu_budget.checkpoint()
                calculation_checkpoint()
                out[key] = [charge for charge in out[key] if charge['order_number'] not in overlaps]
    with refresh_stage('source_finalize'):
        # A completed base charge has its own authority. Delivery can still earn
        # its independent collection commission and establish COD custody.
        managed = {str(charge['order_id']) for charge in completion_candidates
                   if charge.get('schema') == 'operational_completed_shipping_v1'}
        managed.update(key.removeprefix('completed_shipping:') for key in
                       (operational_state or {}).get('engine', {}).get('facts', {}) if key.startswith('completed_shipping:'))
        for order in out['orders'] + out['delivery_events']:
            await cpu_budget.checkpoint()
            calculation_checkpoint()
            if order['id'] in managed:
                order['completed_shipping_base_managed'] = True
        # A persisted owner proof must stay reviewable even when its canonical order
        # disappears from a later successful read. No fee/count is inferred here.
        delivered_now = {str(order['id']) for order in out['orders'] + out['delivery_events'] if order.get('delivery_event')}
        delivery_gaps = {str(row.get('order_id') or row.get('source_id')) for row in out['issues']
                         if str(row.get('code', '')).startswith('operational_delivery_')}
        from operational_delivery_confirmation import validate_record
        for key, record in confirmation_facts.items():
            if not key.startswith(DELIVERY_CONFIRMATION_PREFIX):
                continue
            await cpu_budget.checkpoint()
            calculation_checkpoint()
            oid = key.removeprefix(DELIVERY_CONFIRMATION_PREFIX)
            if oid in delivered_now or oid in delivery_gaps:
                continue
            try:
                validate_record(record, owner=owner, started_at=started_at, order_id=oid)
            except (ValueError, TypeError, KeyError):
                issue(out, 'operational_delivery_confirmation_invalid', oid, component='shipping_fee')
                continue
            issue(out, 'operational_delivery_confirmation_source_unavailable', oid, component='shipping_fee',
                  order_id=oid, order_number=record['order_number'], party_type=record['party_type'], party_id=record['party_id'])
        finish_coverage(customer_tenders, counted_order_ids - inspected_order_ids)
        return out


def _employees(data, out):
    calculation_checkpoint()
    contracts = data['mezan_employee_salary_contracts_v2']
    for employee in data['mezan_employees_v2']:
        calculation_checkpoint()
        if any(employee.get(k) for k in ('archived', 'deleted', 'is_archived', 'is_deleted')):
            continue
        identity = employee.get('id')
        matches = [c for c in contracts if c.get('employee_id') == identity]
        if len(matches) != 1:
            issue(out, 'employee_salary_contract_missing_or_ambiguous', identity)
            continue
        contract = matches[0]
        revisions = contract.get('salary_revisions') or [{'effective_from': contract.get('effective_from'),
                                                         'monthly_amount': contract.get('monthly_amount')}]
        try:
            starts = [date.fromisoformat(r['effective_from']).isoformat() for r in revisions]
            if len(starts) != len(set(starts)):
                raise ValueError('duplicate_salary_revision')
            suspensions = contract.get('suspension_periods') or []
            if employee.get('status') != 'active' and not suspensions and not contract.get('effective_to'):
                raise ValueError('employee_salary_status_history_incomplete')
            normalized = [{'effective_from': r['effective_from'], 'effective_to': r.get('effective_to'),
                           'salary': str(number(r.get('monthly_amount'))), 'status': 'active'}
                          for r in revisions]
            from employee_payroll_status import first_salary_accrual_date
            first = first_salary_accrual_date({**contract, 'start_date': max(
                str(contract.get('effective_from') or ''), str(employee.get('hire_date') or '')) or None})
            out['employees'].append({'id': identity, 'currency': contract.get('currency') or 'SAR',
                'salary_revisions': sorted(normalized, key=lambda r: r['effective_from']),
                'hire_date': employee.get('hire_date'), 'effective_to': contract.get('effective_to'),
                'accrual_start_date': first.isoformat() if first else None,
                'payroll_suspension_periods': suspensions})
        except (ValueError, KeyError, TypeError):
            issue(out, 'employee_salary_contract_incomplete', identity)


def _advertising(data, out, start, now, baselines=None):
    calculation_checkpoint()
    for binding in data['mz2_ad_account_bindings_v2']:
        calculation_checkpoint()
        if not usable(binding) or not binding.get('confirmed_by') or not binding.get('confirmed_at'):
            continue
        platform = binding.get('platform')
        if platform not in AD_SOURCES or binding.get('funding_mode') not in {'prepaid', 'postpaid', 'hybrid'}:
            issue(out, 'ad_funding_contract_incomplete', binding.get('_id'))
            continue
        provider, collection = AD_SOURCES[platform]
        accounts = [a for a in data['mezan_integration_accounts_v2']
                    if a.get('mezan_integration_account_id') == binding.get('integration_account_id')
                    and a.get('provider') == provider and a.get('external_account_id') == binding.get('platform_account_id')]
        if (len(accounts) != 1 or accounts[0].get('connection_status') != 'connected'
                or (binding.get('funding_policy') == 'wallet_first'
                    and (binding.get('currency') != accounts[0].get('currency')
                         or binding.get('timezone') != accounts[0].get('timezone')))):
            issue(out, 'ad_account_incomplete', binding.get('_id'))
            continue
        account = accounts[0]
        if account.get('currency') == 'USD':
            from operational_ad_balance_sar import saved_basis
            basis = saved_basis(binding, account, data['mezan_ad_account_cost_settings_v2'], now)
            if basis:
                out['ad_balance_sar_basis'][str(binding['_id'])] = basis
        for row in data[collection]:
            calculation_checkpoint()
            if row.get('ad_account_id') != account.get('external_account_id') or row.get('provider') != provider:
                continue
            try:
                day = date.fromisoformat(row.get('report_date') if platform == 'snapchat' else row.get('date'))
                split = ({'funding_policy': 'wallet_first'} if binding.get('funding_policy') == 'wallet_first' else
                         hybrid_policy(binding, data['mz2_ad_automation_policies_v2'], binding['user_id'], day.isoformat()) if binding['funding_mode'] == 'hybrid' else {})
                zone = ZoneInfo(account['timezone'])
                end = datetime.combine(day + timedelta(days=1), time.min, zone)
                baseline_amount = Decimal(0)
                if datetime.combine(day, time.min, zone) < start:
                    if end <= start:
                        continue
                    matches = [b for b in (baselines or {}).get('ad_days', [])
                               if b.get('account_id') == str(binding['_id']) and b.get('date') == day.isoformat()]
                    if len(matches) != 1 or matches[0].get('verified') is not True:
                        issue(out, 'ad_start_day_partial_baseline_required', binding.get('_id'))
                        continue
                    baseline = matches[0]
                    if baseline.get('currency') != account.get('currency') or instant(baseline['observed_at']) != start:
                        issue(out, 'ad_start_day_baseline_evidence_invalid', binding.get('_id'))
                        continue
                    baseline_amount = number(baseline['amount'])
                amount = number(row.get('base_spend_native') if platform == 'snapchat' else row.get('spend_native'))
                currency = row.get('currency') if platform == 'snapchat' else row.get('currency_native')
                observed = instant(row.get('source_latest_updated_at') if platform == 'snapchat' else row.get('observed_at'))
                source_zone = row.get('projection_timezone') if platform == 'snapchat' else row.get('account_timezone')
                if currency != account.get('currency') or source_zone != account['timezone'] or observed > now:
                    raise ValueError('ad_currency_timezone_or_timestamp_conflict')
                proof = row.get('source_close_proof') or {}
                complete = all(proof.get(k) is True for k in ('complete', 'complete_response', 'explicit_spend_present'))
                digest = hashlib.sha256(json.dumps({k: v for k, v in proof.items() if k != 'fingerprint'},
                    sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
                complete = complete and proof.get('version') == 1 and proof.get('fingerprint') == digest
                complete = complete and proof.get('account_id') == account['external_account_id'] and proof.get('business_date') == day.isoformat()
                complete = complete and proof.get('timezone') == account['timezone'] and proof.get('currency') == currency
                complete = complete and proof.get('spend_native') is not None and number(proof.get('spend_native')) == amount
                complete = complete and proof.get('observed_at') is not None and instant(proof['observed_at']) == observed
                if amount == 0 and proof.get('zero_confirmed') is not True:
                    complete = False
                if platform == 'snapchat':
                    complete = complete and row.get('amount_complete') is True and row.get('data_state') in {'confirmed_data', 'confirmed_zero'}
                closed = now >= end + timedelta(hours=2) and observed >= end and complete
                if amount < baseline_amount:
                    raise ValueError('ad_cumulative_below_baseline')
                fx = {}
                if currency == 'SAR':
                    fx = {'fx_rate': '1', 'fx_source': 'SAR_identity', 'fx_at': observed.isoformat(),
                          'fx_date': day.isoformat(), 'sar_amount': str(amount-baseline_amount)}
                else:
                    matches = [f for f in data['mz2_ad_fx_snapshots_v2'] if f.get('currency') == currency
                               and f.get('business_date') == day.isoformat() and f.get('status') == 'active']
                    try:
                        if len(matches) != 1:
                            raise ValueError('ad_fx_snapshot_missing_or_ambiguous')
                        native_fx = matches[0]
                        if not all(native_fx.get(k) for k in ('id', 'confirmed_by', 'confirmed_at', 'fx_source', 'evidence', 'fx_at')):
                            raise ValueError('ad_fx_snapshot_evidence_incomplete')
                        rate = number(native_fx.get('fx_rate_to_sar'))
                        at = instant(native_fx['fx_at'])
                        if rate <= 0 or at > now or instant(native_fx['confirmed_at']) > now:
                            raise ValueError('ad_fx_snapshot_invalid')
                        fx = {'fx_rate': str(rate), 'fx_source': native_fx['fx_source'], 'fx_at': at.isoformat(),
                              'fx_date': day.isoformat(), 'fx_snapshot_id': native_fx['id'],
                              'fx_evidence': native_fx['evidence'],
                              'sar_amount': str(((amount-baseline_amount)*rate).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))}
                    except (ValueError, KeyError, TypeError):
                        issue(out, 'ad_fx_snapshot_incomplete', binding.get('_id'))
                out['ad_snapshots'].append({'id': str(row.get('_id') or ''), 'account_id': str(binding['_id']),
                    'date': day.isoformat(), 'observed_at': observed.isoformat(), 'amount': str(amount-baseline_amount), 'currency': currency,
                    'source_cumulative_amount': str(amount), 'baseline_amount': str(baseline_amount),
                    'covers_since_start': True,
                    'funding_type': binding['funding_mode'], 'complete': complete, 'closed': closed,
                    'day_ended': now >= end, 'timezone': account['timezone'], **fx, **split})
                if now >= end + timedelta(hours=2) and not closed:
                    issue(out, 'ad_daily_close_evidence_incomplete', binding.get('_id'))
            except (ValueError, KeyError, TypeError):
                issue(out, 'ad_daily_source_incomplete', row.get('_id'))


async def start_baselines(db, owner, started_at, operational_state=None):
    """Capture native cumulative observations without pretending stale data is exact.

    A start-day deduction is verified only at the exact start instant. Older
    observations remain inspectable but require reconciliation before use.
    """
    start = instant(started_at)
    result = {'captured_at': start.isoformat(), 'ad_days': [], 'issues': []}
    bindings = await rows(db, owner, 'mz2_ad_account_bindings_v2')
    from operational_ad_funding import bindings as funding_bindings
    bindings, conflicts = funding_bindings(operational_state, owner, bindings)
    for identity in conflicts:
        issue(result, 'operational_ad_funding_authority_conflict', identity)
    accounts = await rows(db, owner, 'mezan_integration_accounts_v2')
    for binding in bindings:
        platform = binding.get('platform')
        if platform not in AD_SOURCES or not usable(binding) or not binding.get('confirmed_by') or not binding.get('confirmed_at'):
            continue
        provider, collection = AD_SOURCES[platform]
        matches = [a for a in accounts if a.get('mezan_integration_account_id') == binding.get('integration_account_id')
                   and a.get('external_account_id') == binding.get('platform_account_id') and a.get('provider') == provider]
        if (len(matches) != 1 or (binding.get('funding_policy') == 'wallet_first'
                and (matches[0].get('currency') != binding.get('currency')
                     or matches[0].get('timezone') != binding.get('timezone')))):
            issue(result, 'ad_start_account_ambiguous', binding.get('_id'))
            continue
        account = matches[0]
        try:
            day = start.astimezone(ZoneInfo(account['timezone'])).date().isoformat()
            facts = [r for r in await rows(db, owner, collection) if r.get('ad_account_id') == account['external_account_id']
                     and r.get('provider') == provider and (r.get('report_date') if platform == 'snapchat' else r.get('date')) == day]
            if len(facts) != 1:
                raise ValueError('ad_start_snapshot_missing_or_ambiguous')
            fact = facts[0]
            currency = fact.get('currency') if platform == 'snapchat' else fact.get('currency_native')
            observed = instant(fact.get('source_latest_updated_at') if platform == 'snapchat' else fact.get('observed_at'))
            amount = number(fact.get('base_spend_native') if platform == 'snapchat' else fact.get('spend_native'))
            if currency != account.get('currency') or observed > start:
                raise ValueError('ad_start_snapshot_invalid')
            verified = observed == start
            result['ad_days'].append({'account_id': str(binding['_id']), 'date': day, 'currency': currency,
                'amount': str(amount), 'observed_at': observed.isoformat(), 'source_id': str(fact.get('_id') or ''), 'verified': verified})
            if not verified:
                issue(result, 'ad_start_snapshot_requires_reconciliation', binding.get('_id'))
        except (ValueError, KeyError, TypeError):
            issue(result, 'ad_start_snapshot_incomplete', binding.get('_id'))
    return result


def _recurring(data, out, start, now):
    """Keep daily identity stable; asset associations are context, not payees."""
    calculation_checkpoint()
    expense_categories = {
        'rent': 'rent', 'subscription': 'subscriptions',
        'electricity': 'utilities', 'water': 'utilities',
        'iqama_visa': 'other', 'employee_insurance': 'other',
        'vehicle_insurance': 'other', 'commercial_registration': 'other',
        'government_license': 'other', 'other': 'other',
    }
    first, last = start.astimezone(ZoneInfo('Asia/Riyadh')).date(), now.astimezone(ZoneInfo('Asia/Riyadh')).date()
    for obligation in data['operating_recurring_obligations_v2']:
        calculation_checkpoint()
        if obligation.get('status') not in {'active', 'stopped'}:
            continue
        identity = obligation.get('id')
        emitted = set()
        try:
            expense_type = obligation.get('expense_type')
            if expense_type not in expense_categories:
                raise ValueError('recurring_expense_type_incomplete')
            category = expense_categories[expense_type]
            source_context = {'expense_type': expense_type,
                              'entity_type': obligation.get('entity_type'),
                              'entity_id': obligation.get('entity_id'),
                              'obligation_id': identity}
            origin = date.fromisoformat(obligation['start_date'])
            months = {'monthly': 1, 'semiannual': 6, 'annual': 12, 'biennial': 24}.get(obligation.get('cycle'))
            invoices = [i for i in data['operating_recurring_invoices_v2'] if i.get('obligation_id') == identity]
            for ordinal in range(max(first, origin).toordinal(), last.toordinal() + 1):
                calculation_checkpoint()
                day = date.fromordinal(ordinal)
                if obligation.get('stopped_at') and day >= date.fromisoformat(obligation['stopped_at'][:10]):
                    continue
                def advance(count):
                    index = origin.year * 12 + origin.month - 1 + count
                    year, month = divmod(index, 12)
                    return date(year, month + 1, min(origin.day, calendar.monthrange(year, month + 1)[1]))
                if months:
                    step = max(((day.year-origin.year)*12 + day.month-origin.month)//months, 0)
                    if advance(step*months) > day:
                        step -= 1
                    if obligation.get('auto_renew') is False and step > 0:
                        continue
                    begin, end = advance(step*months), advance((step+1)*months)-timedelta(days=1)
                else:
                    begin, end = origin, date.fromisoformat(obligation['custom_end_date'])
                    if day > end:
                        continue
                matches = [i for i in invoices if i.get('period_start', '9999') <= day.isoformat() <= i.get('period_end', '')]
                if len(matches) > 1:
                    raise ValueError('recurring_invoice_overlap')
                if matches:
                    invoice = matches[0]
                    amount = number(invoice.get('amount'))
                    begin, end = date.fromisoformat(invoice['period_start']), date.fromisoformat(invoice['period_end'])
                else:
                    amount = number(obligation.get('period_amount'))
                    if obligation.get('expense_type') in {'water', 'electricity'} and obligation.get('estimation_basis') != 'manual':
                        raise ValueError('recurring_historical_estimate_incomplete')
                key = f'{identity}:{begin.isoformat()}:{end.isoformat()}'
                if key in emitted:
                    continue
                emitted.add(key)
                out['recurring'].append({'id': key, 'amount': str(amount),
                    'currency': obligation.get('currency') or 'SAR', 'party_id': category, 'party_type': 'operating_expense',
                    'due_at': max(begin, first).isoformat(), 'confirmed': False, 'obligation_id': identity,
                    'period_start': begin.isoformat(), 'period_end': end.isoformat(),
                    'invoice_id': matches[0].get('id') if matches else None,
                    'source_context': source_context})
        except (ValueError, KeyError, TypeError):
            issue(out, 'recurring_contract_incomplete', identity)

async def order_bank_eligible(db, owner, order_number, started_at, reject_automatic=False):
    """Order status permits a receipt; it does not itself prove a cash movement."""
    matches = []
    for source in await rows(db, owner, 'unified_orders'):
        raw = (source.get('raw_by_source') or {}).get('salla_direct')
        if not isinstance(raw, dict) or str(raw.get('reference_id') or raw.get('order_number') or '') != str(order_number):
            continue
        matches.append((source, raw))
    if len(matches) != 1:
        raise ValueError('canonical_order_missing_or_ambiguous')
    source, raw = matches[0]
    from operational_balance_bank_orders import transfer_bank
    method = (raw.get("payment") or {}).get("method") if isinstance(raw.get("payment"), dict) else None
    if reject_automatic and transfer_bank(method or raw.get("payment_method"))[0]:
        raise ValueError("operational_order_bank_automatic")
    if (source.get('g47_salla_snapshot') or {}).get('requires_authoritative_refresh'):
        raise ValueError('canonical_salla_refresh_required')
    created = instant(raw.get('date') or raw.get('created_at') or raw.get('order_date'))
    if created < instant(started_at):
        raise ValueError('order_precedes_operational_start')
    status = raw.get('status')
    if isinstance(status, dict):
        status = status.get('slug') or status.get('name')
    if status not in {'reviewed', 'تم المراجعة', 'تمت المراجعة', 'in_progress', 'processing', 'قيد التنفيذ',
                       'delivered', 'تم التوصيل'}:
        raise ValueError('order_bank_status_ineligible')
    if not raw.get('id'):
        raise ValueError('order_identity_missing')
    return str(raw['id'])
