"""Read-only operational remaining-product adapter over the shared frozen recipe.

No definition repair, stock reservation, invoice recognition or financial write.
Only quantities still required are exposed as expected costs. Posted purchasing
proof removes received quantities; it never posts a second supplier liability.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP

from operational_bundle_cost_projection import project_bundle_costs, _leaf_key, _number

READ_COLLECTIONS = frozenset({
    'mezan_bundle_definitions_v2', 'mezan_order_item_cost_snapshots_v2',
    'mezan_component_order_lifecycle_v1', 'mezan_component_consumption_plans_v1',
    'mezan_component_consumption_units_v1', 'mezan_inventory_receipts_v2', 'purchase_invoices',
})
CENT, ZERO, CHUNK = Decimal('.01'), Decimal(0), 250


def normalized_orders(raw_orders):
    """Use native option normalization; do not match a historical line by SKU."""
    from order_engine.mapper import _normalise_options, _custom_fields
    result = []
    counts = Counter(str(r.get('reference_id') or r.get('order_number') or '') for r in raw_orders)
    for raw in raw_orders:
        number = str(raw.get('reference_id') or raw.get('order_number') or '')
        if not number or counts[number] != 1:
            continue
        items = []
        for item in raw.get('items') or []:
            if not isinstance(item, dict):
                continue
            native = item.get('id') or item.get('item_id') or item.get('order_item_id')
            if not native:
                continue
            product = item.get('product') if isinstance(item.get('product'), dict) else {}
            variant = item.get('variant') if isinstance(item.get('variant'), dict) else {}
            pid = str(item.get('parent_product_id') or item.get('product_id') or product.get('id') or '')
            candidates = []
            for value in (item.get('options'), item.get('choices'), item.get('attributes'),
                    item.get('product_options'), item.get('selected_options'), item.get('customer_options'),
                    variant.get('options'), product.get('options')):
                if isinstance(value, list): candidates.extend(value)
                elif isinstance(value, dict): candidates.append(value)
            options, _ = _normalise_options(candidates)
            items.append({**deepcopy(item), 'product_id': pid, 'source_item_id': str(native),
                'order_item_id': f'salla:{number}:{native}',
                'variant_id': item.get('variant_id') or variant.get('id'),
                'options_raw': options, 'custom_fields': _custom_fields(item)})
        result.append({'order_number': number, 'items': items, 'status': raw.get('status')})
    return result


async def _read(db, owner, name, query, fields, limit=20000):
    if name not in READ_COLLECTIONS or not owner or query.get('user_id') != owner:
        raise ValueError('bundle_remaining_read_scope_invalid')
    records = await db[name].find(query, fields).to_list(length=limit + 1)
    if len(records) > limit or any(r.get('user_id') != owner for r in records):
        raise ValueError('bundle_remaining_read_coverage_invalid')
    return records


def _problem(code, line=None, *, scope='products_unreceived'):
    return {'code': code, 'source_id': line or '', 'scope': scope,
        'required_action': 'طابق تركيب الطلب واستلام مكوناته وفاتورة المورد؛ لا تعد إدخال الفاتورة ولا تغير الرصيد الافتتاحي'}


def _receipt_quantity(allocation, leaf, receipts, invoices, stamp):
    from operational_balance_sources import instant
    from bundle_fulfillment_v2 import identity
    from purchase_receiving_service import same_identity
    from product_inventory_rules import specifications_are_exact
    rid = str(allocation.get('lot_id') or '')
    receipt = receipts.get(rid)
    if not receipt or rid not in (leaf.get('receipt_ids') or []):
        raise ValueError('bundle_supplier_receipt_missing')
    when = receipt.get('posted_at') or receipt.get('occurred_at')
    if (receipt.get('status') != 'posted' or receipt.get('source_type') != 'purchase_invoice'
            or not when or instant(when) > stamp or not same_identity(receipt, identity(leaf))):
        raise ValueError('bundle_supplier_receipt_unverified')
    invoice_id = str(receipt.get('source_id') or '')
    invoice = invoices.get(invoice_id)
    if (not invoice or invoice.get('state') != 'approved'
            or receipt.get('purchase_invoice_id', invoice_id) != invoice_id
            or receipt.get('invoice_id', invoice_id) != invoice_id):
        raise ValueError('bundle_supplier_invoice_unverified')
    supplier = receipt.get('supplier_id') or invoice.get('supplier_counterparty_id') or invoice.get('supplier_id')
    if not supplier or supplier != leaf.get('supplier_id'):
        raise ValueError('bundle_supplier_receipt_party_mismatch')
    if leaf.get('customization') and not specifications_are_exact(receipt.get('specifications'), leaf['customization']):
        raise ValueError('bundle_supplier_receipt_customization_mismatch')
    q = _number(allocation.get('quantity'), positive=True)
    if q > _number(receipt.get('quantity'), positive=True):
        raise ValueError('bundle_supplier_receipt_overallocated')
    return q


def _remaining(unit, leaf, allocations, receipts, invoices, stamp):
    """Allocation identity and native posted evidence, never assignment alone."""
    from operational_balance_sources import instant
    from bundle_fulfillment_v2 import identity
    from purchase_receiving_service import same_identity
    wanted = _number(leaf.get('quantity'), positive=True)
    for key in ('created_at', 'updated_at', 'consumed_at'):
        if unit.get(key) and instant(unit[key]) > stamp:
            raise ValueError('bundle_unit_newer_than_report')
    if not unit.get('created_at'):
        raise ValueError('bundle_unit_observation_time_missing')
    if unit.get('state') == 'consumed' and leaf.get('state') == 'skipped' and leaf.get('required', True) is False:
        if _number(leaf.get('actual_cost')) != 0:
            raise ValueError('bundle_skipped_cost_conflict')
        return ZERO, ZERO
    cost_only = leaf.get('cost_only') or (leaf.get('kind') == 'resource' and leaf.get('track_inventory') is False
                                        and leaf.get('sourcing_mode') == 'stock')
    if cost_only:
        if unit.get('state') == 'consumed' and leaf.get('state') == 'consumed':
            _number(leaf.get('actual_cost'))
            return ZERO, ZERO
        if allocations:
            raise ValueError('bundle_cost_only_allocation_conflict')
        return wanted, ZERO
    mode = leaf.get('sourcing_mode')
    if mode not in {'stock', 'supplier', 'auto'}:
        raise ValueError('bundle_sourcing_mode_unresolved')
    segments = leaf.get('sourcing_segments') or []
    if not isinstance(segments, list) or any(not isinstance(s, dict) for s in segments):
        raise ValueError('bundle_source_segments_invalid')
    if mode != 'auto' and segments:
        raise ValueError('bundle_source_segments_mode_conflict')
    segment_map = {_leaf_key(s): s for s in segments}
    if mode == 'auto':
        if len(segment_map) != len(segments):
            raise ValueError('bundle_source_segments_duplicate')
        if sum((_number(s.get('quantity'), positive=True) for s in segments), ZERO) + _number(leaf.get('unresolved_quantity', '0')) != wanted:
            raise ValueError('bundle_source_segments_quantity_mismatch')
    allocated, received = ZERO, ZERO
    by_segment, received_by_segment = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    for a in allocations:
        q = _number(a.get('quantity'), positive=True)
        actual_leaf, actual_mode = leaf, mode
        if mode == 'auto':
            actual_leaf = segment_map.get(a.get('sourcing_segment_id'))
            if not actual_leaf:
                raise ValueError('bundle_source_segment_allocation_missing')
            actual_mode = actual_leaf.get('sourcing_mode')
            if a.get('sourcing_mode') != actual_mode:
                raise ValueError('bundle_source_segment_mode_mismatch')
            by_segment[_leaf_key(actual_leaf)] += q
        if (not isinstance(a.get('inventory_identity'), dict)
                or not same_identity(a['inventory_identity'], identity(actual_leaf))
                or not isinstance(a.get('location_id'), str) or not a['location_id'].strip()
                or not isinstance(a.get('lot_id'), str) or not a['lot_id'].strip()):
            raise ValueError('bundle_allocation_item_mismatch')
        if actual_mode == 'supplier':
            taken = _receipt_quantity(a, actual_leaf, receipts, invoices, stamp)
            received += taken
            if mode == 'auto':
                received_by_segment[_leaf_key(actual_leaf)] += taken
        elif actual_mode != 'stock':
            raise ValueError('bundle_sourcing_mode_unresolved')
        allocated += q
    if allocated > wanted or allocated != _number(leaf.get('reserved_quantity', '0')):
        raise ValueError('bundle_reserved_quantity_mismatch')
    if received != _number(leaf.get('received_quantity', '0')):
        raise ValueError('bundle_received_quantity_mismatch')
    if mode == 'auto':
        for key, segment in segment_map.items():
            if (by_segment[key] > _number(segment['quantity']) or
                    by_segment[key] != _number(segment.get('reserved_quantity', '0'))
                    or received_by_segment[key] != _number(segment.get('received_quantity', '0'))):
                raise ValueError('bundle_source_segment_quantity_mismatch')
    if unit.get('state') == 'consumed':
        if leaf.get('state') != 'consumed' or _number(leaf.get('consumed_quantity')) != allocated:
            raise ValueError('bundle_consumption_coverage_mismatch')
        if leaf.get('required', True) and allocated != wanted:
            raise ValueError('bundle_consumption_coverage_mismatch')
        return ZERO, received
    if _number(leaf.get('consumed_quantity', '0')):
        raise ValueError('bundle_consumption_coverage_mismatch')
    return wanted - allocated, received


async def load_remaining_bundles(db, owner, raw_orders, as_of, *, preparation_pieces=None, operational_state=None, native_matches=None):
    """One bounded authority load, then batched units/receipts/invoices only.

    Reporting-only cost corrections do not constitute a physical recipe or a
    new payable. We deliberately use the original frozen fulfilment authority.
    """
    from operational_balance_sources import instant
    from order_cost_authority import load_order_cost_authority, resolve_order_line_cost, line_id
    from bundle_fulfillment_v2 import selected_ids
    from stock_component_consumption_service import UNITS, RECEIPTS, PLANS
    orders = normalized_orders(raw_orders)
    if not orders:
        return {}
    authority = await load_order_cost_authority(db, owner, orders, include_corrections=False,
        include_frozen_metadata=True, parallel_reads=True)
    selected, result = {}, {}
    for order in orders:
        for item in order['items']:
            key = (order['order_number'], line_id(item))
            record = authority['lines'].get(key) or {}
            if record.get('bundle_snapshot') or item['product_id'] in authority['composite_products']:
                selected[key] = (order, item, record)
    if not selected:
        return result
    if preparation_pieces is None:
        # Physical-only bundles keep their existing bounded read budget.
        financial_orders = sorted({key[0] for key, (_, _, record) in selected.items()
            if 'reporting_cost' in (record.get('bundle_snapshot') or {})})
        preparation_pieces = []
        for offset in range(0, len(financial_orders), CHUNK):
            preparation_pieces.extend(await db['mezan_preparation_pieces_v1'].find(
                {'user_id': owner, 'order_number': {'$in': financial_orders[offset:offset + CHUNK]}},
                {'_id': 0}).to_list(length=20001 - len(preparation_pieces)))
            if len(preparation_pieces) > 20000:
                raise ValueError('bundle_legacy_piece_read_scope_invalid')
    if len(preparation_pieces) > 20000 or any(p.get('user_id') != owner for p in preparation_pieces):
        raise ValueError('bundle_legacy_piece_read_scope_invalid')
    unit_rows, plan_rows = [], []
    numbers = sorted({k[0] for k in selected})
    for i in range(0, len(numbers), CHUNK):
        plan_rows.extend(await _read(db, owner, PLANS,
            {'user_id': owner, 'order_id': {'$in': numbers[i:i+CHUNK]}, 'bundle_snapshots': {'$exists': True}},
            {'_id': 1, 'user_id': 1, 'order_id': 1, 'reconciliation_required': 1}))
        unit_rows.extend(await _read(db, owner, UNITS,
            {'user_id': owner, 'order_id': {'$in': numbers[i:i+CHUNK]}, 'state': {'$ne': 'released'}},
            {'user_id': 1, 'plan_id': 1, 'order_id': 1, 'order_line_id': 1, 'unit_index': 1,
             'state': 1, 'bundle_leaves': 1, 'allocations': 1, 'created_at': 1, 'updated_at': 1,
             'consumed_at': 1, 'product_id': 1, 'variant_id': 1, 'selected_context': 1}))
    units = defaultdict(list)
    usage = defaultdict(lambda: ZERO)
    duplicate_allocations, seen_allocations = set(), {}
    plans = defaultdict(list)
    for plan in plan_rows:
        plans[str(plan['order_id'])].append(plan)
    ids = set()
    for unit in unit_rows:
        key = (str(unit['order_id']), str(unit['order_line_id']))
        if key not in selected:
            continue
        units[key].append(unit)
        allocations = unit.get('allocations') or []
        try:
            known_leaves = {_leaf_key(leaf) for leaf in unit.get('bundle_leaves') or []}
            if not isinstance(allocations, list):
                raise ValueError('invalid allocations')
        except (ValueError, TypeError, AttributeError):
            duplicate_allocations.add(key)
            continue
        for a in allocations:
            if (not isinstance(a, dict) or not isinstance(a.get('id'), str) or not a['id']
                    or a.get('bundle_leaf_id') not in known_leaves):
                duplicate_allocations.add(key)
                continue
            if a['id'] in seen_allocations:
                duplicate_allocations.update((key, seen_allocations[a['id']]))
            seen_allocations[a['id']] = key
            rid = str(a.get('lot_id') or '')
            if rid:
                ids.add(rid)
                try: usage[rid] += _number(a.get('quantity'), positive=True)
                except ValueError: duplicate_allocations.add(key)
    receipts, invoices = {}, {}
    ids = sorted(ids)
    for i in range(0, len(ids), CHUNK):
        for row in await _read(db, owner, RECEIPTS, {'user_id': owner, 'id': {'$in': ids[i:i+CHUNK]}}, {'_id': 0}):
            key = str(row.get('id') or '')
            receipts[key] = row if key not in receipts else None
    invoice_ids = sorted({str(r.get('source_id')) for r in receipts.values() if r and r.get('source_id')})
    for i in range(0, len(invoice_ids), CHUNK):
        for row in await _read(db, owner, 'purchase_invoices', {'user_id': owner, 'id': {'$in': invoice_ids[i:i+CHUNK]}},
                {'_id': 0, 'id': 1, 'user_id': 1, 'state': 1, 'supplier_counterparty_id': 1, 'supplier_id': 1}):
            key = str(row.get('id') or '')
            invoices[key] = row if key not in invoices else None
    if native_matches is None:
        from operational_native_purchase_link_service import verified_saved_links
        native_matches = await verified_saved_links(db, owner, operational_state, as_of=as_of)
    for key, (order, item, record) in selected.items():
        problems, normalized = [], []
        result[key] = {'items': normalized, 'issues': problems, 'complete': False}
        native = units.get(key)
        if native:
            plan_candidates = plans[key[0]]
            if (len(plan_candidates) != 1 or plan_candidates[0].get('reconciliation_required')
                    or any(u.get('plan_id') != plan_candidates[0]['_id'] for u in native)):
                problems.append(_problem('bundle_unit_plan_mismatch', key[1]))
                continue
        cost = resolve_order_line_cost(item, order_number=key[0], catalog_cost={}, authority=authority)
        snapshot = record.get('bundle_snapshot')
        if not cost.get('calculation_cost_available') or not isinstance(snapshot, dict):
            for code in cost.get('missing_cost_reasons') or ['bundle_frozen_recipe_required']:
                problems.append(_problem(code, key[1]))
            continue
        legacy_items = []
        mapped_financial = 'reporting_cost' in snapshot
        if mapped_financial:
            from operational_bundle_legacy_receipts import map_financial_receipts
            try:
                snapshot, native, legacy_items = map_financial_receipts(
                    owner=owner, number=key[0], item=item, snapshot=snapshot,
                    pieces=preparation_pieces, native_units=native, as_of=as_of)
            except (ValueError, TypeError, KeyError) as exc:
                code = str(exc) if str(exc).startswith('bundle_') else 'bundle_financial_policy_operational_mapping_required'
                problems.append(_problem(code, key[1]))
                continue
        if mapped_financial and not snapshot['leaves']:
            # An opted-in base-only bundle has no stock leaf by design. Receipt
            # is proved through existing preparation pieces, never fabricated.
            projected = {'rows': [], 'issues': []}
        else:
            projected = project_bundle_costs(owner=owner, order_number=key[0], order_item_id=key[1],
                product_id=item['product_id'], sale_quantity=item.get('quantity'), status=order['status'],
                selected_options=selected_ids(item), snapshot=snapshot, native_units=native)
            if not projected['rows']:
                problems.extend(_problem(p['code'], key[1]) for p in projected['issues'])
                continue
        if key in duplicate_allocations:
            problems.append(_problem('bundle_allocation_identity_ambiguous', key[1]))
            continue
        if native:
            from order_cost_authority import line_fingerprint
            try:
                for unit in native:
                    context = unit.get('selected_context')
                    if (not isinstance(context, list) or any(not isinstance(v, (list, tuple)) or len(v) != 2
                            or any(not isinstance(x, str) for x in v) for v in context)):
                        raise ValueError('bundle_unit_selection_evidence_missing')
                    observed = dict(item, product_id=unit.get('product_id'), parent_product_id=None,
                                    variant_id=unit.get('variant_id'), selected_context=context)
                    if line_fingerprint(observed) != line_fingerprint(item):
                        raise ValueError('bundle_unit_selection_mismatch')
            except (ValueError, TypeError, KeyError):
                problems.append(_problem('bundle_unit_selection_mismatch', key[1]))
                continue
        normalized.extend(legacy_items)
        by_index = {u['unit_index']: u for u in native or []}
        frozen_leaves = {_leaf_key(leaf): leaf for leaf in snapshot['leaves']}
        native_leaves = {index: {_leaf_key(leaf): leaf for leaf in unit['bundle_leaves']}
                         for index, unit in by_index.items()}
        total, previous = ZERO, ZERO
        for row in projected['rows']:
            leaf = frozen_leaves[row['leaf_id']]
            try:
                remaining = _number(leaf['quantity'], positive=True)
                unit = by_index.get(row['unit_index'])
                received = ZERO
                if unit:
                    actual = native_leaves[row['unit_index']][row['leaf_id']]
                    allocations = [a for a in unit.get('allocations') or [] if a.get('bundle_leaf_id') == row['leaf_id']]
                    for allocation in allocations:
                        receipt = receipts.get(str(allocation.get('lot_id') or ''))
                        if receipt and usage[str(allocation['lot_id'])] > _number(receipt.get('quantity'), positive=True):
                            raise ValueError('bundle_supplier_receipt_overallocated')
                    remaining, received = _remaining(unit, actual, allocations, receipts, invoices, instant(as_of))
                if received:
                    modes = {_leaf_key(s): s.get('sourcing_mode') for s in actual.get('sourcing_segments') or []}
                    supplier_allocations = [a for a in allocations if actual.get('sourcing_mode') == 'supplier'
                        or (actual.get('sourcing_mode') == 'auto' and modes.get(a.get('sourcing_segment_id')) == 'supplier')]
                    native_ids = {str(receipts[str(a['lot_id'])]['source_id']) for a in supplier_allocations}
                    if not native_ids or not all(native_matches.get(i, {}).get('matched') for i in native_ids):
                        problems.append(_problem('bundle_received_purchase_financial_matching_required', row['id'], scope='received_products'))
                # Each row is already one sale-unit leaf. Do not multiply by
                # the parent order quantity or add the parent's catalog cost.
                total += _number(leaf['unit_cost']) * remaining
                rounded = total.quantize(CENT, rounding=ROUND_HALF_UP)
                value, previous = rounded - previous, rounded
                normalized.append({'id': row['id'], 'cost': str(value), 'quantity': '1',
                    'cost_currency': 'SAR', 'supplier_id': None, 'supplier_receipt_proven': False,
                    'source_cost_stage': 'bundle_unreceived', 'source_line_id': key[1],
                    'source_leaf_id': row['leaf_id'], 'remaining_quantity': str(remaining)})
            except (ValueError, TypeError, KeyError, StopIteration) as error:
                code = str(error) if str(error).startswith('bundle_') else 'bundle_remaining_evidence_invalid'
                problems.append(_problem(code, row['id']))
        result[key]['complete'] = not any(p['scope'] == 'products_unreceived' for p in problems)
    return result
