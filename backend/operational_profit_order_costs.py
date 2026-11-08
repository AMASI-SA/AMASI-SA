"""Expected sale-cohort costs, preserving source currency and per-order evidence."""
from collections import defaultdict
from decimal import Decimal

from operational_balance_sources import cost_components, number, unique_index
from operational_configured_provider_fees import estimate_fee
from operational_provider_summary import _records


def collect(state, data, cohort, scope_issues):
    engine = state.get('engine', {})
    obligations = engine.get('obligations', {})
    facts = engine.get('facts', {})
    inputs = engine.get('provider_fee_inputs', {})
    profiles = unique_index(data['mezan_product_cost_profiles_v2'], 'salla_product_id')
    try:
        records, _ = _records(state, list(obligations.values()), {})
    except (ValueError, TypeError, KeyError, ArithmeticError):
        records = {}
    totals, counts, issues = defaultdict(Decimal), defaultdict(int), defaultdict(list)
    def reject(kind, oid, code):
        issues[kind].append({'code':code, 'source_id':oid, 'currency':None})
    for raw in cohort:
        oid = str(raw['id'])
        try:
            items = raw.get('items')
            if not items:
                raise ValueError('profit_product_items_missing')
            seen, value = set(), Decimal(0)
            for item in items:
                iid = str(item.get('id') or item.get('item_id') or '')
                if not iid or iid in seen:
                    raise ValueError('profit_product_item_identity_ambiguous')
                seen.add(iid)
                pid = str(item.get('parent_product_id') or item.get('product_id') or (item.get('product') or {}).get('id') or '')
                # Bundles need their native physical-piece resolver; no fallback
                # to a parent base price that drops child services/components.
                if item.get('bundle') or item.get('bundle_items') or item.get('components'):
                    raise ValueError('profit_bundle_cost_evidence_required')
                cost, services = cost_components(data, pid, item, profiles.get(pid, {}))
                quantity = number(item.get('quantity'))
                if quantity <= 0:
                    raise ValueError('profit_product_quantity_invalid')
                value += (cost + sum((r['cost'] for r in services.values()), Decimal(0))) * quantity
            totals['SAR','cost_of_goods'] += value
            counts['SAR','cost_of_goods'] += 1
        except (ValueError, TypeError, KeyError, ArithmeticError):
            reject('cost_of_goods', oid, 'profit_product_cost_source_incomplete')
        shipping = [(key,row) for key,row in obligations.items() if row.get('kind') == 'shipping'
            and not row.get('superseded') and (key == 'shipping:' + oid or key.startswith('shipping:' + oid + ':')
                or str(row.get('order_id') or '') == oid)]
        if not shipping:
            reject('shipping', oid, 'profit_shipping_recognition_evidence_missing')
        else:
            for key,row in shipping:
                try:
                    # Only a confirmed event is expense evidence; expected carrier
                    # assignments do not prove a shipment. Prepaid has own fact.
                    fact = facts.get(key) or facts.get('prepaid_shipping:' + oid)
                    if not fact or row.get('incomplete') or row.get('currency') != 'SAR':
                        raise ValueError('profit_shipping_source_incomplete')
                    value = number(row['confirmed'])
                    if value <= 0:
                        raise ValueError('profit_shipping_zero_requires_scope')
                    totals['SAR','shipping'] += value
                    counts['SAR','shipping'] += 1
                except (ValueError, TypeError, KeyError, ArithmeticError):
                    reject('shipping', oid, 'profit_shipping_source_incomplete')
        matches = [row for row in records.values() if row['order_id'] == oid]
        if not matches:
            reject('payment_fees', oid, 'profit_payment_fee_scope_missing')
        for row in matches:
            fee = None
            if row['currency'] == 'SAR' and not row.get('unresolved_split_fees'):
                fee = estimate_fee(inputs.get('settings', {}), row['provider'], row['gross'],
                    method=row.get('method'), refunded=row['refunded'], policies=inputs.get('policies', []),
                    payment=row.get('payment'), created_at=row.get('created_at'), observed_at=row.get('observed_at'),
                    refunds=row.get('refund_events'), source_mode=inputs.get('source_mode','legacy_settings'),
                    as_of=engine.get('as_of'))
            if fee is None:
                reject('payment_fees', oid, 'profit_payment_fee_tariff_or_fx_missing')
            else:
                totals['SAR','payment_fees'] += fee
                counts['SAR','payment_fees'] += 1
    result = {}
    for currency in {currency for currency, _ in totals} | {'SAR'}:
        result[currency] = {}
        for kind in ('cost_of_goods', 'shipping', 'payment_fees'):
            errors = [*scope_issues, *issues[kind]]
            present = (currency,kind) in totals
            complete = present and not errors
            result[currency][kind] = {'complete':complete, 'amount':format(totals[currency,kind],'.2f') if complete else None,
                'known_subtotal':format(totals[currency,kind],'.2f') if present else None,
                'source_count':counts[currency,kind], 'issues':errors,
                'basis': {'cost_of_goods':'current_authoritative_catalog_expected_cost_SAR',
                    'shipping':'recorded_shipping_event_gross_cost', 'payment_fees':'saved_effective_tariff_including_fee_vat'}[kind]}
    return result
