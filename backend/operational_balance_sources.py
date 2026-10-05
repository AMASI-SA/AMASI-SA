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
    'mz2_shipping_setup_v2', 'mz2_provider_fee_policies_v2',
    'mz2_ad_account_bindings_v2', 'mezan_integration_accounts_v2',
    'mz2_ad_fx_snapshots_v2',
    'mz2_ad_automation_policies_v2', 'expense_categories', 'users',
    'unified_orders', 'mezan_product_cost_profiles_v2',
    'mezan_product_resource_bindings_v2', 'mezan_product_option_cost_bindings_v2', 'mezan_cost_resources_v2',
    'mezan_preparation_pieces_v1', 'mezan_supplier_invoices_v2',
    'store_delivery_assignments', 'store_delivery_collections',
    'mezan_employee_salary_contracts_v2', 'operating_recurring_obligations_v2',
    'operating_recurring_invoices_v2',
} | {v[1] for v in AD_SOURCES.values()}
PROVIDERS = ('salla', 'tabby', 'tamara', 'emkan')
PAYMENT_METHODS = {
    'salla': {'applepay', 'apple pay', 'checkout', 'stcpay', 'mada', 'visa', 'mastercard', 'salla', 'salla_pay'},
    'tabby': {'tabby', 'تابي'}, 'tamara': {'tamara', 'تمارا'},
    'emkan': {'emkaninstallment', 'emkan', 'imkan', 'إمكان', 'امكان'},
}
MAX_ROWS = 20000
# Existing MZ2 daily-movement codes plus explicitly requested operational types.
# expense_categories is MZ2's direct registry, also physically shared with older
# consumers; no claim is made about the provenance of an untagged stored row.
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


def shipping_charge(rate, owner, party, at, cod_amount, setup=None, *, cod_incomplete=False):
    if not rate.get('confirmed_by') or not rate.get('confirmed_at'):
        raise ValueError('shipping_contract_approval_missing')
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
            if cod_incomplete:
                raise ValueError('shipping_cod_amount_unavailable')
            cod_net = number(rate.get('cod_fixed_fee')) + cod_amount * number(rate.get('cod_percent')) / 100 if cod_amount is not None and cod_amount > 0 else Decimal(0)
            parts.append({'id': 'cod_commission', 'amount': str(gross(base_net+cod_net)-base), 'complete': True})
        except (ValueError, KeyError, TypeError) as exc:
            parts.append({'id': 'cod_commission', 'amount': None, 'complete': False})
            problems.append({'code': str(exc), 'component': 'cod_commission'})
    total = sum((number(p['amount']) for p in parts if p['complete']), Decimal(0))
    return {'cost': str(total), 'fee_components': parts, 'fee_complete': not problems, 'component_issues': problems}


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
    if not owner or collection not in ALLOWED_COLLECTIONS or collection == 'users':
        raise ValueError('operational_source_rejected')
    result = await db[collection].find({'user_id': owner}).to_list(MAX_ROWS + 1)
    if len(result) > MAX_ROWS:
        raise ValueError('operational_source_scope_too_large')
    if any(r.get('user_id') != owner for r in result):
        raise ValueError('operational_source_owner_mismatch')
    return result


async def entities(db, owner, kind):
    if kind == 'operating_expense':
        catalog = {code: {'id': code, 'name': name, 'kind': kind, 'currency': 'SAR', 'source': 'mz2_builtin'}
                   for code, name in EXPENSE_CATEGORIES.items()}
        stored = await rows(db, owner, 'expense_categories')
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
        return result
    if kind == 'provider':
        return [{'id': p, 'name': {'salla': 'سلة', 'tabby': 'تابي', 'tamara': 'تمارا', 'emkan': 'إمكان'}[p],
                 'kind': kind, 'currency': 'SAR'} for p in PROVIDERS]
    if kind == 'courier':
        setups = await rows(db, owner, 'mz2_shipping_setup_v2')
        if len(setups) > 1:
            raise ValueError('shipping_setup_ambiguous')
        source = [dict(r, id=r.get('courier_key'), currency='SAR')
                  for setup in setups for r in setup.get('couriers', [])
                  if r.get('confirmed_by') and r.get('confirmed_at')]
    elif kind == 'ad_account':
        accounts = await rows(db, owner, 'mezan_integration_accounts_v2')
        bindings = await rows(db, owner, 'mz2_ad_account_bindings_v2')
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
            if binding.get('funding_mode') == 'hybrid':
                try:
                    metadata = hybrid_policy(binding, policies, owner)
                    metadata['settings_complete'] = True
                except (ValueError, TypeError, KeyError):
                    metadata = {'settings_complete': False}
            source.append(dict(binding, **metadata, id=str(binding.get('_id') or ''),
                               name=f"{binding['platform']}: {account['display_name']}" if account.get('display_name') else None,
                               currency=account.get('currency')))
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
                       **({'ready': False} if not name or not currency else {}),
                       **({k: row.get(k) for k in ('platform', 'integration_account_id', 'platform_account_id', 'funding_mode',
                           'wallet_fraction', 'hybrid_policy_id', 'settings_complete')}
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


def option_tokens(item):
    """Native IDs first; normalized explicit labels follow the MZ2 cost contract."""
    def norm(value):
        return ' '.join(str(value or '').replace('_', ' ').strip().casefold().split())
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
            if option_id not in (None, '') and value_id not in (None, ''):
                tokens.add((f'id:{option_id}', f'id:{value_id}'))
            if option_name and value_name not in (None, '', {}):
                tokens.add((f'name:{norm(option_name)}', f'name:{norm(value_name)}'))
    for key, values in (item.get('options_normalized') or {}).items():
        for value in values if isinstance(values, list) else [values]:
            tokens.add((f'name:{norm(key)}', f'name:{norm(value)}'))
    return tokens


def option_matches(binding, tokens):
    def norm(value):
        return ' '.join(str(value or '').replace('_', ' ').strip().casefold().split())
    return ((f"id:{binding.get('option_id')}", f"id:{binding.get('value_id')}") in tokens
            or (f"name:{norm(binding.get('option_name'))}", f"name:{norm(binding.get('value_name'))}") in tokens)


def cost_components(data, product_id, item, profile):
    """Resolve only MZ2-authoritative amounts; services retain independent identity."""
    variant = str(item.get('variant_id') or '')
    product_cost = number((profile.get('variant_costs') or {}).get(variant, profile.get('base_cost')))
    resources = unique_index(data['mezan_cost_resources_v2'], 'id')
    tokens = option_tokens(item)
    links = [dict(r, mode='resource') for r in data['mezan_product_resource_bindings_v2'] if str(r.get('salla_product_id')) == product_id]
    links += [r for r in data['mezan_product_option_cost_bindings_v2'] if str(r.get('salla_product_id')) == product_id and option_matches(r, tokens)]
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
            product_cost += number(link.get('direct_amount')) * quantity
            continue
        resource_id = str(link.get('resource_id') or '')
        resource = resources.get(resource_id)
        if link.get('mode') != 'resource' or not resource or not usable(resource):
            raise ValueError('mz2_cost_resource_missing')
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


def append_components(order, base_id, product_cost, services, quantity, supplier_id=None, piece=None):
    order['items'].append({'id': base_id, 'supplier_id': supplier_id, 'cost': str(product_cost), 'quantity': str(quantity)})
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
        order['items'].append({'id': f'{base_id}:service:{service_id}', 'supplier_id': party,
                               'cost': str(cost), 'quantity': str(quantity), 'component': 'service'})


async def collect_sources(db, owner, started_at, as_of, baselines=None):
    start, now = instant(started_at), instant(as_of)
    if now < start:
        raise ValueError('operational_source_time_range_invalid')
    out = {key: [] for key in ('orders', 'supplier_receipts', 'supplier_returns', 'employees',
                               'ad_snapshots', 'recurring', 'fee_policies', 'issues')}
    # No service with side effects is called: collection access is allowlisted.
    data = {name: await rows(db, owner, name) for name in sorted(ALLOWED_COLLECTIONS - {'users', 'expense_categories'})}
    setup_rows = data['mz2_shipping_setup_v2']
    setup = setup_rows[0] if len(setup_rows) == 1 else {}
    if len(setup_rows) > 1:
        issue(out, 'shipping_setup_ambiguous')
    profiles = unique_index(data['mezan_product_cost_profiles_v2'], 'salla_product_id')
    suppliers = unique_index(data['mezan_suppliers_v2'], 'id')
    pieces = [r for r in data['mezan_preparation_pieces_v1'] if not r.get('experiment_mode') and not r.get('experiment_run_id')]
    order_numbers = {}
    raw_orders = [(r.get('raw_by_source') or {}).get('salla_direct') for r in data['unified_orders']]
    raw_orders = [r for r in raw_orders if isinstance(r, dict)]
    source_ids = Counter(str(r.get('id') or '') for r in raw_orders)
    source_numbers = Counter(str(r.get('reference_id') or r.get('order_number') or '') for r in raw_orders)
    for source in data['unified_orders']:
        raw = (source.get('raw_by_source') or {}).get('salla_direct')
        if not isinstance(raw, dict):
            issue(out, 'canonical_salla_source_missing', source.get('order_number'))
            continue
        try:
            created = instant(raw.get('date') or raw.get('created_at') or raw.get('order_date'))
            if not start < created <= now:
                continue
            identity = str(raw.get('id') or '')
            order_number = str(raw.get('reference_id') or raw.get('order_number') or '')
            if not identity or not order_number:
                raise ValueError('order_identity_missing')
            if source_ids[identity] != 1 or source_numbers[order_number] != 1:
                raise ValueError('canonical_order_identity_ambiguous')
            watermark = source.get('g47_salla_snapshot') or {}
            if watermark.get('requires_authoritative_refresh'):
                raise ValueError('canonical_salla_refresh_required')
            status_obj = raw.get('status')
            status = (status_obj.get('slug') or status_obj.get('name')) if isinstance(status_obj, dict) else status_obj
            source_total = (raw.get('amounts') or {}).get('total')
            order = {'id': identity, 'order_number': order_number, 'created_at': created.isoformat(),
                     'updated_at': instant(raw.get('updated_at') or raw.get('date_updated') or created).isoformat(),
                     'status': status, 'currency': raw.get('currency') or (source_total.get('currency') if isinstance(source_total, dict) else None),
                     'items': []}
            if not order['currency']:
                raise ValueError('order_currency_missing')
            order_numbers[order_number] = identity
            for item in raw.get('items', []):
                source_item_id = item.get('id') or item.get('item_id') or item.get('order_item_id')
                if not source_item_id:
                    issue(out, 'order_item_stable_identity_missing', identity)
                    continue
                item_id = f'salla:{order_number}:{source_item_id}'
                product = item.get('product') or {}
                product_id = str(item.get('parent_product_id') or item.get('product_id') or product.get('id') or '')
                profile = profiles.get(product_id, {})
                try:
                    cost, services = cost_components(data, product_id, item, profile)
                    quantity = number(item.get('quantity'))
                except ValueError as exc:
                    issue(out, 'mz2_product_cost_incomplete', item_id)
                    continue
                matching = [p for p in pieces if p.get('order_number') == order_number and p.get('order_item_id') == item_id]
                if matching:
                    if quantity != len(matching) or len({p.get('unit_index') for p in matching}) != len(matching):
                        issue(out, 'preparation_piece_coverage_ambiguous', item_id)
                        continue
                    for piece in matching:
                        supplier = piece.get('supplier_id')
                        if supplier and supplier not in suppliers:
                            issue(out, 'mz2_supplier_identity_missing', piece.get('id'))
                            continue
                        append_components(order, str(piece['id']), cost, services, 1, supplier, piece)
                else:
                    append_components(order, item_id, cost, services, quantity)
            payment = raw.get('payment') if isinstance(raw.get('payment'), dict) else {}
            method = str(payment.get('method') or raw.get('payment_method') or '').lower()
            total_value = (raw.get('amounts') or {}).get('total', raw.get('total'))
            total = number(total_value)
            cod = method in {'cod', 'cash_on_delivery', 'cashondelivery', 'cash on delivery', 'الدفع عند الاستلام', 'دفع عند الاستلام', 'دفع عند الإستلام'}
            if cod:
                order['cod_required'] = True
                try:
                    order['cod'] = cod_facts(raw, total)
                    order['cod_amount'] = order['cod']['outstanding']
                except (ValueError, TypeError):
                    issue(out, 'cod_collection_contract_incomplete', identity)
            provider = next((p for p, aliases in PAYMENT_METHODS.items() if method in aliases), None)
            if provider:
                # Executed refund identity/evidence is not present in generic order status.
                refunded = payment.get('refunded_amount', raw.get('refunded_amount'))
                if refunded is not None and number(refunded) > 0 or status in {'refunded', 'مسترجع'}:
                    issue(out, 'provider_refund_execution_evidence_required', identity)
                else:
                    order['payment'] = {'provider': provider, 'gross': str(total), 'currency': order['currency'],
                                        'cancelled': str(total if status in {'cancelled', 'canceled', 'ملغي', 'ملغى'} else 0), 'refunds': []}
                    reference = payment.get('reference') or payment.get('transaction_reference')
                    if payment.get('status') in {'paid', 'captured'} and reference and payment.get('paid_amount') is not None and payment.get('paid_at'):
                        captured = number(payment['paid_amount'])
                        captured_at = instant(payment['paid_at'])
                        if captured > total or captured_at < created or captured_at > now:
                            raise ValueError('provider_capture_conflict')
                        order['payment']['captures'] = [{'id': str(reference), 'amount': str(captured), 'captured_at': captured_at.isoformat()}]
                    elif payment.get('status') in {'paid', 'captured'}:
                        issue(out, 'provider_capture_evidence_incomplete', identity)
            try:
                shipping = raw.get('salla_shipping_current') or raw.get('shipping') or {}
                carrier_key = shipping.get('company_code') or shipping.get('company')
                parties = [c for c in setup.get('couriers', []) if usable(c) and c.get('confirmed_by')
                           and c.get('confirmed_at') and carrier_key in c.get('salla_carrier_keys', [])]
                assignments = [a for a in data['store_delivery_assignments'] if str(a.get('order_number')) == order_number
                               and a.get('status') in {'assigned', 'out_for_delivery', 'delivered'}]
                party_kind, party_id = 'courier', parties[0]['courier_key'] if len(parties) == 1 else None
                if party_id:
                    order['carrier'] = {'id': party_id, 'kind': party_kind, 'cost': None, 'fee_complete': False}
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
                                       and c.get('driver_id') == party_id]
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
                                        or (method != 'cash' and custody != 0)):
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
                                issue(out, 'driver_collection_custody_evidence_incomplete', identity)
                if len(assignments) > 1:
                    issue(out, 'store_driver_assignment_ambiguous', identity)
                    party_id = None
                    order.pop('carrier', None)
                when = instant(shipping.get('delivered_at') or raw.get('updated_at') or raw.get('date_updated') or created)
                rates = [r for r in setup.get('contracts', []) if r.get('status') == 'approved'
                         and r.get('party_type') == party_kind and r.get('party_id') == party_id
                         and r.get('context') == 'delivery' and instant(r['effective_from']) <= when
                         and (not r.get('effective_to') or when < instant(r['effective_to']))]
                if party_id and len(rates) == 1:
                    rate = rates[0]
                    cod_incomplete = cod and 'cod' not in order
                    charge = shipping_charge(rate, owner, party_id, when,
                        number(order['cod'].get('collection_amount', order['cod']['outstanding'])) if cod and not cod_incomplete else None,
                        setup, cod_incomplete=cod_incomplete)
                    problems = charge.pop('component_issues')
                    order['carrier'] = {'id': party_id, 'kind': party_kind, 'contract_id': rate.get('id'), **charge}
                    for problem in problems:
                        issue(out, problem['code'], identity, component=problem['component'],
                              recognition_state='confirmed' if order.get('status') in {'delivered', 'تم التوصيل'} else 'expected')
                elif carrier_key or assignments:
                    issue(out, 'shipping_identity_or_rate_incomplete', identity, component='shipping_fee' if party_id else 'carrier_identity')
            except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
                reason = str(exc)
                code = reason if reason and all(c.islower() or c == '_' for c in reason) else 'shipping_contract_component_incomplete'
                issue(out, code, identity, component='driver_custody' if reason.startswith('driver_') else 'shipping_fee',
                      reason=reason, recognition_state='confirmed' if order.get('status') in {'delivered', 'تم التوصيل'} else 'expected')
            out['orders'].append(order)
        except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
            issue(out, str(exc), raw.get('id'))
    # An issued, approved MZ2 invoice is the sole confirmation authority.
    # Current piece state may have changed after invoicing and cannot revoke it.
    piece_index = unique_index(pieces, 'id')
    invoice_counts = Counter(r.get('id') for r in data['mezan_supplier_invoices_v2'])
    for invoice in data['mezan_supplier_invoices_v2']:
        if invoice.get('experiment_mode') or invoice.get('experiment_run_id') or not invoice.get('approved_at') or invoice.get('supplier_id') not in suppliers:
            continue
        if not invoice.get('id') or invoice_counts[invoice.get('id')] != 1:
            issue(out, 'supplier_invoice_identity_ambiguous', invoice.get('id'))
            continue
        covered = [pid for line in invoice.get('lines', []) for pid in line.get('piece_ids', [])]
        if len(covered) != len(set(covered)):
            issue(out, 'supplier_invoice_piece_coverage_ambiguous', invoice.get('id'))
            continue
        try:
            component_amounts = invoice_component_amounts(invoice)
        except (ValueError, KeyError, TypeError):
            issue(out, 'supplier_invoice_tax_contract_incomplete', invoice.get('id'))
            continue
        for line in invoice.get('lines', []):
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
            for index, piece_id in enumerate(sorted(ids)):
                piece = piece_index.get(piece_id, {})
                order_id = order_numbers.get(str(piece.get('order_number')))
                if not order_id:
                    continue
                try:
                    amount = number(line.get('product_unit_price_halalas')) / 100
                    accepted = instant(invoice['approved_at'])
                    if not start <= accepted <= now:
                        continue
                    if line.get('product_charge_eligible') is not False:
                        out['supplier_receipts'].append({'id': f"{invoice['id']}:{piece_id}:product", 'order_id': order_id,
                            'item_id': piece_id, 'supplier_id': invoice['supplier_id'], **component_amounts[(piece_id, 'product')],
                            'accepted_at': accepted.isoformat(), 'invoice_id': invoice['id']})
                    for service_id, total in service_totals.items():
                        cumulative = (total * (index + 1) / len(ids)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
                        previous = (total * index / len(ids)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
                        amount = (cumulative - previous) / 100
                        out['supplier_receipts'].append({'id': f"{invoice['id']}:{piece_id}:service:{service_id}",
                            'order_id': order_id, 'item_id': f'{piece_id}:service:{service_id}',
                            'supplier_id': invoice['supplier_id'], **component_amounts[(piece_id, f'service:{service_id}')],
                            'accepted_at': accepted.isoformat(), 'invoice_id': invoice['id']})
                except (ValueError, KeyError):
                    issue(out, 'supplier_receipt_evidence_incomplete', invoice.get('id'))
    # Native supplier_piece_product_charge_eligible charges a physical product
    # once, even across supplier reassignment. Completed services likewise retain
    # their original invoice. Two issued invoices claiming the same component
    # are contradictory evidence, not two independent obligations.
    coverage = {}
    for receipt in out['supplier_receipts']:
        key = (receipt['order_id'], receipt['item_id'])
        coverage.setdefault(key, set()).add(receipt['invoice_id'])
    ambiguous = {key for key, invoices in coverage.items() if len(invoices) > 1}
    for key in sorted(ambiguous):
        issue(out, 'supplier_cross_invoice_component_coverage_ambiguous', ':'.join(key))
    if ambiguous:
        out['supplier_receipts'] = [r for r in out['supplier_receipts'] if (r['order_id'], r['item_id']) not in ambiguous]
    for document in data['mz2_provider_fee_policies_v2']:
        for policy in document.get('policies', []):
            if policy.get('user_id') not in (None, owner):
                issue(out, 'provider_fee_owner_mismatch', policy.get('id'))
                continue
            out['fee_policies'].append({k: v for k, v in policy.items() if k not in ('_id', 'user_id')})
    _employees(data, out)
    _advertising(data, out, start, now, baselines)
    _recurring(data, out, start, now)
    return out


def _employees(data, out):
    contracts = data['mezan_employee_salary_contracts_v2']
    for employee in data['mezan_employees_v2']:
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
            out['employees'].append({'id': identity, 'currency': contract.get('currency') or 'SAR',
                'salary_revisions': sorted(normalized, key=lambda r: r['effective_from']),
                'hire_date': employee.get('hire_date'), 'effective_to': contract.get('effective_to'),
                'accrual_start_date': contract.get('accrual_start_date') or contract.get('effective_from'),
                'payroll_suspension_periods': suspensions})
        except (ValueError, KeyError, TypeError):
            issue(out, 'employee_salary_contract_incomplete', identity)


def _advertising(data, out, start, now, baselines=None):
    for binding in data['mz2_ad_account_bindings_v2']:
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
        if len(accounts) != 1 or accounts[0].get('connection_status') != 'connected':
            issue(out, 'ad_account_incomplete', binding.get('_id'))
            continue
        account = accounts[0]
        for row in data[collection]:
            if row.get('ad_account_id') != account.get('external_account_id') or row.get('provider') != provider:
                continue
            try:
                day = date.fromisoformat(row.get('report_date') if platform == 'snapchat' else row.get('date'))
                split = hybrid_policy(binding, data['mz2_ad_automation_policies_v2'], binding['user_id'], day.isoformat()) if binding['funding_mode'] == 'hybrid' else {}
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


async def start_baselines(db, owner, started_at):
    """Capture native cumulative observations without pretending stale data is exact.

    A start-day deduction is verified only at the exact start instant. Older
    observations remain inspectable but require reconciliation before use.
    """
    start = instant(started_at)
    result = {'captured_at': start.isoformat(), 'ad_days': [], 'issues': []}
    bindings = await rows(db, owner, 'mz2_ad_account_bindings_v2')
    accounts = await rows(db, owner, 'mezan_integration_accounts_v2')
    for binding in bindings:
        platform = binding.get('platform')
        if platform not in AD_SOURCES or not usable(binding) or not binding.get('confirmed_by') or not binding.get('confirmed_at'):
            continue
        provider, collection = AD_SOURCES[platform]
        matches = [a for a in accounts if a.get('mezan_integration_account_id') == binding.get('integration_account_id')
                   and a.get('external_account_id') == binding.get('platform_account_id') and a.get('provider') == provider]
        if len(matches) != 1:
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
    expense_categories = {
        'rent': 'rent', 'subscription': 'subscriptions',
        'electricity': 'utilities', 'water': 'utilities',
        'iqama_visa': 'other', 'employee_insurance': 'other',
        'vehicle_insurance': 'other', 'commercial_registration': 'other',
        'government_license': 'other', 'other': 'other',
    }
    first, last = start.astimezone(ZoneInfo('Asia/Riyadh')).date(), now.astimezone(ZoneInfo('Asia/Riyadh')).date()
    for obligation in data['operating_recurring_obligations_v2']:
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

async def order_bank_eligible(db, owner, order_number, started_at):
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
