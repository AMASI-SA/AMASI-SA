"""Expected sale-cohort costs, preserving source currency and per-order evidence."""
from collections import defaultdict
from decimal import Decimal

from operational_balance_sources import cost_components, number, unique_index, invoice_component_amounts, instant, supplier_receipt_proven, physical_unit_index
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
    estimates = defaultdict(Decimal)
    totals, counts, issues = defaultdict(Decimal), defaultdict(int), defaultdict(list)
    def reject(kind, oid, code):
        issues[kind].append({'code':code, 'source_id':oid, 'currency':None})
    pieces_by_line, shipping_by_root, fees_by_order = defaultdict(list), defaultdict(list), defaultdict(list)
    for piece in data['mezan_preparation_pieces_v1']:
        pieces_by_line[str(piece.get('order_number')), piece.get('order_item_id')].append(piece)
    for key, obligation in obligations.items():
        if obligation.get('kind') != 'shipping' or obligation.get('superseded'):
            continue
        roots = {key}
        roots.update(key[:i] for i, char in enumerate(key) if char == ':' and i > len('shipping:'))
        if obligation.get('order_id'):
            roots.add('shipping:' + str(obligation['order_id']))
        for root in roots:
            shipping_by_root[root].append((key, obligation))
    for record in records.values():
        fees_by_order[record['order_id']].append(record)
    invoice_parts, receipts = defaultdict(list), defaultdict(list)
    for obligation in obligations.values():
        for rid in obligation.get('evidence_ids') or []:
            receipts[rid].append(obligation)
    for invoice in data['mezan_supplier_invoices_v2']:
        if not invoice.get('approved_at') or invoice.get('experiment_mode') or invoice.get('experiment_run_id'):
            continue
        try:
            if instant(invoice['approved_at']) > instant(engine['as_of']):
                continue
            for key, value in invoice_component_amounts(invoice).items():
                invoice_parts[key].append((invoice, value))
        except (ValueError, TypeError, KeyError, ArithmeticError):
            reject('cost_of_goods', str(invoice.get('id') or ''), 'profit_invoice_component_source_invalid')
    for raw in cohort:
        oid = str(raw['id'])
        value = Decimal(0)
        try:
            items = raw.get('items')
            if not items:
                raise ValueError('profit_product_items_missing')
            seen, value, actual = set(), Decimal(0), Decimal(0)
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
                order_number = str(raw.get('reference_id') or raw.get('order_number'))
                pieces = pieces_by_line[order_number, 'salla:' + order_number + ':' + iid]
                if (len(pieces) != quantity or {physical_unit_index(p) for p in pieces} != set(range(1, int(quantity)+1))
                        or any(not supplier_receipt_proven(p, instant(engine['as_of'])) for p in pieces)):
                    raise ValueError('profit_received_product_units_unverified')
                for piece in pieces:
                    for component in ['product', *('service:' + key for key in services)]:
                        matches = []
                        for invoice, amount in invoice_parts.get((piece['id'], component), []):
                            rid = 'receipt:' + str(invoice['id']) + ':' + str(piece['id']) + ':' + component
                            saved = receipts.get(rid, [])
                            if (invoice.get('currency') not in (None, 'SAR') or len(saved) != 1
                                    or saved[0].get('source_component_fully_invoiced') is not True
                                    or saved[0].get('source_invoice_id') != invoice['id'] or saved[0].get('currency') != 'SAR'
                                    or number(saved[0].get('returned_amount')) != 0
                                    or number(saved[0].get('net_amount')) != number(amount['net_amount'])):
                                raise ValueError('profit_supplier_receipt_not_reconciled')
                            matches.append(amount)
                        if len(matches) != 1:
                            raise ValueError('profit_invoice_component_coverage_unverified')
                        actual += number(matches[0]['net_amount'])
            totals['SAR','cost_of_goods'] += actual
            counts['SAR','cost_of_goods'] += 1
        except (ValueError, TypeError, KeyError, ArithmeticError):
            estimates['SAR','cost_of_goods'] += value
            reject('cost_of_goods', oid, 'profit_product_cost_source_incomplete')
        shipping = shipping_by_root['shipping:' + oid]
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
                    tax_evidence = row.get('tax_evidence') or fact.get('tax_evidence') or {}
                    net, tax = row.get('net_amount', fact.get('net_amount')), row.get('tax_amount', fact.get('tax_amount'))
                    if (net is not None and tax is not None and number(net) + number(tax) == value
                            and (number(tax) == 0 or tax_evidence.get('treatment') == 'INPUT_VAT'
                                 and tax_evidence.get('confirmed') is True)):
                        value = number(net)
                    else:
                        reject('shipping', oid, 'profit_shipping_input_vat_treatment_required')
                    totals['SAR','shipping'] += value
                    counts['SAR','shipping'] += 1
                except (ValueError, TypeError, KeyError, ArithmeticError):
                    reject('shipping', oid, 'profit_shipping_source_incomplete')
        matches = fees_by_order[oid]
        if not matches:
            reject('payment_fees', oid, 'profit_payment_fee_scope_missing')
        for row in matches:
            fee = None
            try:
                current = instant(row.get('observed_at')) == instant(raw.get('updated_at') or raw.get('date_updated') or raw.get('created_at') or raw.get('date'))
            except (ValueError, TypeError, KeyError):
                current = False
            if current and row['currency'] == 'SAR' and not row.get('unresolved_split_fees'):
                fee = estimate_fee(inputs.get('settings', {}), row['provider'], row['gross'],
                    method=row.get('method'), refunded=row['refunded'], policies=inputs.get('policies', []),
                    payment=row.get('payment'), created_at=row.get('created_at'), observed_at=row.get('observed_at'),
                    refunds=row.get('refund_events'), source_mode=inputs.get('source_mode','legacy_settings'),
                    as_of=engine.get('as_of'))
            if fee is None:
                reject('payment_fees', oid, 'profit_payment_fee_tariff_or_fx_missing')
            else:
                totals['SAR','payment_fees'] += fee
                reject('payment_fees', oid, 'profit_fee_input_vat_treatment_required')
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
                'estimated_subtotal':format(estimates[currency,kind],'.2f') if (currency,kind) in estimates else None,
                'basis': {'cost_of_goods':'accepted_supplier_invoice_net_cost_with_complete_physical_components',
                    'shipping':'recorded_shipping_event_gross_cost', 'payment_fees':'saved_effective_tariff_including_fee_vat'}[kind]}
    return result
