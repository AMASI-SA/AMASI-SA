"""Read-only option evidence for current-cost reports, never historical facts.

Recover only an empty compatibility projection from the same provider order
and unique native line, with matching product and quantity. No SKU/name join.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation

_RAW = 'raw_by_source.salla_direct'
SALLA_REPORT_OPTION_PROJECTION = {
    '_id': 0, 'order_number': 1,
    **{f'{_RAW}.{path}': 1 for path in (
        'reference_id', 'items.id', 'items.product_id', 'items.product.id',
        'items.quantity', 'items.options',
    )},
}


def _id(value):
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ''


def _quantity(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result > 0 else None
    except (ValueError, InvalidOperation):
        return None


def _has_options(item):
    raw = item.get('raw_item') if isinstance(item.get('raw_item'), dict) else {}
    return any(item.get(key) for key in ('options_raw', 'options', 'selected_options', 'options_normalized')) or bool(raw.get('options'))


def _attach_steps(orders, projected_rows):
    """Attach bounded evidence in memory without changing source/fingerprint fields."""
    projected = defaultdict(list)
    for row in projected_rows:
        yield
        projected[_id(row.get('order_number'))].append(row)
    counts = Counter(_id(o.get('order_number')) for o in orders)
    for order in orders:
        yield
        for item in order.get('products') or []:
            if isinstance(item, dict):
                item.pop('_report_options_raw', None)
        number = _id(order.get('order_number'))
        candidates = projected.get(number, [])
        if not number or counts[number] != 1 or len(candidates) != 1:
            continue
        sources = candidates[0].get('raw_by_source')
        raw = sources.get('salla_direct') if isinstance(sources, dict) else None
        if not isinstance(raw, dict) or _id(raw.get('reference_id')) != number:
            continue
        rows = raw.get('items')
        if not isinstance(rows, list) or len(rows) > 1000:
            continue
        source = defaultdict(list)
        for row in rows:
            if isinstance(row, dict) and _id(row.get('id')):
                source[_id(row['id'])].append(row)
        items = [i for i in order.get('products') or [] if isinstance(i, dict)]
        identities = Counter(_id(i.get('order_item_id')) for i in items)
        for item in items:
            identity = _id(item.get('order_item_id'))
            matched = source.get(identity, [])
            if not identity or identities[identity] != 1 or len(matched) != 1 or _has_options(item):
                continue
            provider = matched[0]
            product = provider.get('product') if isinstance(provider.get('product'), dict) else {}
            pids = {_id(provider.get('product_id')), _id(product.get('id'))} - {''}
            pid = _id(item.get('product_id'))
            parent = _id(item.get('parent_product_id'))
            quantity = _quantity(item.get('quantity'))
            options = provider.get('options')
            if (pids != {pid} or parent and parent != pid or quantity is None or quantity != _quantity(provider.get('quantity'))
                    or not isinstance(options, list) or not options or len(options) > 100
                    or any(not isinstance(option, dict) for option in options)):
                continue
            item['_report_options_raw'] = deepcopy(options)


def report_cost_item(item):
    """Use recovered choices ONLY in current catalog estimates, not snapshots."""
    if item.get('_report_options_raw') and not _has_options(item):
        return {**item, 'options_raw': deepcopy(item['_report_options_raw'])}
    return item


def attach_report_option_evidence(orders, projected_rows):
    for _ in _attach_steps(orders, projected_rows):
        pass


async def attach_report_option_evidence_cooperative(orders, projected_rows, *, checkpoint):
    for _ in _attach_steps(orders, projected_rows):
        await checkpoint()
