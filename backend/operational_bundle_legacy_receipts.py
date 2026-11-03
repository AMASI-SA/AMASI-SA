"""Map frozen bundle financial costs to existing preparation receipt identities.

Read-only projection. Never change a recipe, service, stock unit or invoice.
The raw physical projector still rejects unvalidated reporting envelopes.
"""
from collections import Counter
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP

from operational_bundle_cost_projection import _number, _leaf_key, _hash

ZERO = Decimal(0)
CENT = Decimal('.01')


def _require(ok, code):
    if not ok:
        raise ValueError(code)


def _id(value):
    _require(isinstance(value, str) and value and value == value.strip(),
             'bundle_legacy_identity_missing')
    return value


def _time(value, upper):
    from operational_balance_sources import instant
    _require(value is not None and instant(value) <= instant(upper),
             'bundle_legacy_receipt_time_unverified')


def _piece_map(pieces, *, owner, number, line, product, count, as_of):
    """Missing unmaterialized units stay expected; conflicting units never do."""
    selected = [p for p in pieces if str(p.get('order_number')) == number
                and str(p.get('order_item_id')) == line
                and not p.get('experiment_mode') and not p.get('experiment_run_id')]
    out = {}
    seen = set()
    for p in selected:
        _require(p.get('user_id') == owner and str(p.get('product_id')) == product,
                 'bundle_legacy_piece_scope_mismatch')
        pid, index = _id(p.get('id')), p.get('unit_index')
        _require(type(index) is int and 1 <= index <= count and index not in out and pid not in seen,
                 'bundle_legacy_piece_identity_ambiguous')
        for field in ('created_at', 'updated_at'):
            if p.get(field):
                _time(p[field], as_of)
        out[index] = p
        seen.add(pid)
    return out


def _receipt(piece, service, quantity, as_of):
    """Return proven received quantity and actual supplier, not assignment.

    Service completion is independent of the parent's remaining services. A
    completed service cannot become unreceived again after supplier reassignment.
    """
    from operational_balance_sources import supplier_receipt_proven
    if piece is None:
        return ZERO, None
    if service is None:
        # A receipt history means the original product has already been received,
        # even while another supplier performs a remaining service. The first
        # durable receiving event is the existing product-charge authority.
        history = piece.get('supplier_receiving_history') or []
        if history:
            _require(isinstance(history, list) and all(isinstance(h, dict) for h in history),
                     'bundle_legacy_receipt_history_invalid')
            refs = [_id(h.get('invoice_id')) for h in history]
            _require(len(refs) == len(set(refs)), 'bundle_legacy_receipt_history_ambiguous')
            for h in history:
                _time(h.get('received_at'), as_of)
                _id(h.get('supplier_id'))
            from operational_balance_sources import instant
            first_at = min(instant(h['received_at']) for h in history)
            first = [h for h in history if instant(h['received_at']) == first_at]
            _require(len(first) == 1, 'bundle_legacy_product_supplier_ambiguous')
            return quantity, first[0]['supplier_id']
        if supplier_receipt_proven(piece, as_of):
            _time(piece['received_at'], as_of)
            return quantity, _id(piece.get('supplier_id'))
        if piece.get('received_at') or piece.get('execution_status') == 'received_from_supplier':
            raise ValueError('bundle_legacy_receipt_evidence_conflict')
        return ZERO, None
    plans = piece.get('services') or []
    _require(isinstance(plans, list) and all(isinstance(p, dict) for p in plans),
             'bundle_legacy_service_evidence_invalid')
    matches = [p for p in plans if str(p.get('service_id')) == service]
    _require(len(matches) == 1, 'bundle_legacy_service_identity_missing_or_duplicated')
    plan = matches[0]
    _require(_number(plan.get('required_quantity'), positive=True) == quantity,
             'bundle_legacy_service_quantity_changed')
    completed = _number(plan.get('completed_quantity', '0'))
    _require(completed <= quantity, 'bundle_legacy_service_quantity_exceeded')
    if completed:
        _time(plan.get('completed_at'), as_of)
        party = _id(plan.get('completed_by_supplier_id'))
        _require(plan.get('status') in {'completed', 'in_progress'}
                 and (plan.get('status') != 'completed' or completed == quantity),
                 'bundle_legacy_service_completion_conflict')
        return completed, party
    _require(plan.get('status') != 'completed' and not plan.get('completed_at'),
             'bundle_legacy_service_completion_conflict')
    return ZERO, None


def _root_services(snapshot):
    """Frozen root binding/resource identity, never the current catalogue."""
    root = str(snapshot.get('product_id'))
    definition = (snapshot.get('frozen_definitions') or {}).get(root) or {}
    meta = definition.get('catalog_metadata') or {}
    resources = meta.get('resources') or {}
    selected = {(str(s.get('option_id')), str(s.get('value_id')))
                for s in snapshot.get('selected_options') or []}
    result = {str(c.get('resource_id')) for c in (snapshot.get('reporting_cost') or {}).get('components', [])
              if c.get('financial_only') and c.get('product_id') == root
              and c.get('binding_kind') in {'product_resource', 'option_resource'}
              and c.get('resource_id')}
    # Compact first-capture envelopes intentionally omit catalogue metadata.
    # Native inherited non-stock bindings retain their exact root path and
    # resource ID. Receipt still requires that SAME ID in the legacy service
    # plan; absence or conflict can never prove completion.
    for leaf in snapshot.get('leaves') or []:
        if (leaf.get('kind') == 'resource' and leaf.get('track_inventory') is False
                and any(str(part).startswith(root + '@') and str(part).endswith(':binding')
                        for part in leaf.get('path') or [])):
            result.add(str(leaf.get('resource_id')))
    for field in ('bindings', 'options'):
        for binding in meta.get(field) or []:
            rid = binding.get('resource_id')
            resource = resources.get(rid) or {}
            if resource.get('kind') != 'service' or resource.get('requires_preparation') is not True:
                continue
            if field == 'options' and (str(binding.get('option_id')), str(binding.get('value_id'))) not in selected:
                continue
            result.add(str(rid))
    return result


def map_financial_receipts(*, owner, number, item, snapshot, pieces, native_units, as_of):
    """Produce calculation-only physical data and legacy cost items.

    The caller has already verified the immutable line fingerprint through the
    shared authority. This function also validates the financial proof, policy
    date, sale quantity and stable native piece/service identities. Unsupported
    nested financial-only bases fail explicitly rather than hiding their cost.
    """
    from bundle_reporting_cost import verified_reporting_cost, policy_applies_to_sale
    from order_cost_authority import line_id, line_fingerprint
    report = verified_reporting_cost(snapshot)
    _require(report is not None and snapshot.get('historical_cost_verified') is True
             and policy_applies_to_sale(report, snapshot.get('order_created_at')),
             'bundle_financial_policy_operational_mapping_required')
    q = _number(item.get('quantity'), positive=True)
    _require(q == q.to_integral_value() and q <= 10000
             and _number(snapshot.get('sale_quantity'), positive=True) == q
             and snapshot.get('line_fingerprint') == line_fingerprint(item),
             'bundle_legacy_sale_identity_mismatch')
    root, line = str(item['product_id']), line_id(item)
    by_index = _piece_map(pieces, owner=owner, number=number, line=line,
                          product=root, count=int(q), as_of=as_of)
    costs = []
    for base in report['added_bases']:
        _require(str(base.get('product_id')) == root and not base.get('path')
                 and all(not p for p in base.get('paths', [[]]))
                 and _number(base.get('quantity'), positive=True) == 1,
                 'bundle_nested_financial_base_receipt_mapping_required')
        costs.append((None, _number(base['unit_cost_sar']), Decimal(1)))
    _require(sum(1 for sid, _, _ in costs if sid is None) <= 1,
             'bundle_legacy_base_identity_ambiguous')
    legacy_services = _root_services(snapshot)
    physical = deepcopy(snapshot)
    physical.pop('reporting_cost', None)
    physical.update(cost_complete=True, unresolved_components=[], needs_selection=False, cost_currency='SAR')
    physical['leaves'] = []
    remapped = set()
    original = {_leaf_key(l): l for l in snapshot['leaves']}
    _require(len(original) == len(snapshot['leaves']), 'bundle_leaf_identity_duplicate')
    for component in report['components']:
        rid = str(component.get('resource_id') or '')
        is_service = rid in legacy_services
        if component.get('financial_only'):
            _require(component.get('product_id') == root and is_service,
                     'bundle_financial_service_receipt_mapping_required')
        if is_service:
            costs.append((rid, _number(component['unit_cost_sar']), _number(component['quantity'], positive=True)))
            if not component.get('financial_only'):
                remapped.add(_leaf_key(component))
        elif not component.get('financial_only'):
            leaf = deepcopy(original[_leaf_key(component)])
            leaf.update(unit_cost=component['unit_cost_sar'], cost_currency='SAR')
            physical['leaves'].append(leaf)
    service_counts = Counter(sid for sid, _, _ in costs if sid is not None)
    _require(not any(n != 1 for n in service_counts.values()), 'bundle_legacy_service_binding_ambiguous')
    physical['estimated_cost'] = str(sum((_number(l['unit_cost']) * _number(l['quantity'])
                                        for l in physical['leaves']), ZERO))
    physical_units = deepcopy(native_units)
    if physical_units is not None:
        _require(isinstance(physical_units, list) and len(physical_units) == int(q),
                 'bundle_unit_coverage_incomplete')
        indices = set()
        for unit in physical_units:
            index = unit.get('unit_index')
            _require(unit.get('user_id') == owner and str(unit.get('order_id')) == number
                     and str(unit.get('order_line_id')) == line and type(index) is int
                     and 1 <= index <= int(q) and index not in indices
                     and unit.get('state') in {'reserved', 'consumed'},
                     'bundle_unit_scope_or_identity_mismatch')
            indices.add(index)
            leaves = unit.get('bundle_leaves')
            _require(isinstance(leaves, list), 'bundle_unit_leaf_coverage_incomplete')
            actual = {_leaf_key(l): l for l in leaves}
            _require(len(actual) == len(leaves) and set(actual) == set(original),
                     'bundle_unit_leaf_coverage_incomplete')
            for key, frozen in original.items():
                current = actual[key]
                _require(all(str(current.get(f) or '') == str(frozen.get(f) or '')
                             for f in ('kind', 'product_id', 'resource_id', 'variant_id'))
                         and _number(current.get('quantity'), positive=True) == _number(frozen.get('quantity'), positive=True),
                         'bundle_unit_leaf_identity_mismatch')
            _time(unit.get('created_at'), as_of)
            for field in ('updated_at', 'consumed_at'):
                if unit.get(field):
                    _time(unit[field], as_of)
            # Legacy service receipt and native stock receipt cannot both claim
            # this component without an explicit matching record.
            if any(a.get('bundle_leaf_id') in remapped for a in unit.get('allocations') or []):
                raise ValueError('bundle_service_receipt_authority_conflict')
            unit['bundle_leaves'] = [l for l in unit.get('bundle_leaves') or [] if _leaf_key(l) not in remapped]
    items = []
    subtotal = rounded_before = ZERO
    for index in range(1, int(q) + 1):
        piece = by_index.get(index)
        base_id = piece['id'] if piece else 'bundle-legacy:' + _hash([owner, number, line, index])
        for service, unit_cost, quantity in costs:
            received, party = _receipt(piece, service, quantity, as_of)
            # Keep received cost in the existing received-uninvoiced supplier
            # bucket using the SAME key that the preparation invoice replaces.
            # A partly completed service is split, not rounded up to a whole job.
            for stage, qty, identity in (
                ('received', received, base_id if service is None else base_id + ':service:' + service),
                ('unreceived', quantity - received, base_id if service is None else base_id + ':service:' + service)):
                if not qty:
                    continue
                if received and received != quantity and stage == 'unreceived':
                    identity += ':remaining'
                subtotal += unit_cost * qty
                rounded = subtotal.quantize(CENT, rounding=ROUND_HALF_UP)
                value, rounded_before = rounded - rounded_before, rounded
                items.append(dict(id=identity, cost=str(value), quantity='1', cost_currency='SAR',
                    supplier_id=party if stage == 'received' else None,
                    supplier_receipt_proven=stage == 'received', source_line_id=line,
                    source_cost_stage='bundle_legacy_' + stage,
                    **({'component': 'service'} if service is not None else {})))
    _require(_number(physical['estimated_cost']) + sum((unit * qty for _, unit, qty in costs), ZERO)
             == _number(report['unit_total_sar']), 'bundle_financial_cost_coverage_mismatch')
    return physical, physical_units, items
