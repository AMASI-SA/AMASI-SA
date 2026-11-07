"""Read-only product unit attribution from explicit Salla/confirmed ledger facts."""
import asyncio
from collections import Counter, defaultdict
from pymongo.errors import PyMongoError
from decimal import Decimal, ROUND_FLOOR

from mezan_attribution_order_ledger import LEDGER_COLLECTION, _order_key, _provider
from salla_marketing_attribution import (
    SALLA_RAW_ATTRIBUTION_PROJECTION, canonical_ad_platform,
    canonical_marketing_source, order_source_candidates,
)

PLATFORMS = {'snapchat': 'سناب شات', 'tiktok': 'تيك توك', 'meta': 'ميتا',
             'google': 'جوجل', 'direct': 'مباشر', 'whatsapp': 'واتساب',
             'manual': 'يدوي', 'gift': 'هدية', 'unknown': 'غير معروف'}
AD_PLATFORMS = {'snapchat', 'tiktok', 'meta', 'google'}


def _bucket(platform='unknown', account=None, status='unknown'):
    return {'platform': platform, 'platform_label': PLATFORMS[platform],
            'account_id': account,
            'account_status': 'confirmed' if account else 'not_applicable' if platform not in AD_PLATFORMS | {'unknown'} else 'unknown',
            'attribution_status': status}


def resolve_traffic_source(order, ledger_rows=()):
    # Existing source extraction is authoritative. Check all explicit platform
    # candidates first so its first-match rule cannot conceal a conflict.
    providers = {canonical_ad_platform({'utm_source': value}) for value in order_source_candidates(order)} - {None}
    if len(providers) > 1 or len(ledger_rows) > 1:
        return _bucket(status='conflict')
    source = canonical_marketing_source(order)
    platform = source if source in PLATFORMS else 'unknown'
    if ledger_rows:
        attribution = ledger_rows[0].get('attribution') or {}
        if attribution.get('quality') == 'ambiguous':
            return _bucket(status='conflict')
        ledger_platform = _provider(attribution.get('provider'))
        ledger_source = attribution.get('marketing_source')
        ledger_platform = ledger_platform if ledger_platform in AD_PLATFORMS else ledger_source if ledger_source in PLATFORMS else None
        if ledger_platform and platform != 'unknown' and ledger_platform != platform:
            return _bucket(status='conflict')
        platform = ledger_platform or platform
        if (attribution.get('quality') == 'confirmed' and attribution.get('decision_safe') is True
                and platform in AD_PLATFORMS):
            account = attribution.get('account_id')
            account = str(account).strip() if isinstance(account, (str, int)) and not isinstance(account, bool) else None
            return _bucket(platform, account or None, 'confirmed' if account else 'platform_only')
    return _bucket(platform, status='platform_only' if platform in AD_PLATFORMS else 'confirmed' if platform != 'unknown' else 'unknown')


class _TrafficReadLimit(ValueError):
    pass


async def load_product_traffic_sources(db, owner, orders, *, budget=None):
    """Optional attribution failures never prevent authoritative cost reporting."""
    try:
        return await _load_product_traffic_sources(db, owner, orders, budget=budget)
    except (PyMongoError, asyncio.TimeoutError, _TrafficReadLimit):
        # Do not claim raw fallback/platform/account verification when the
        # authoritative source reads could not be completed.
        return {id(order): _bucket(status='unavailable') for order in orders}


async def _load_product_traffic_sources(db, owner, orders, *, budget=None):
    """Two bounded owner-scoped batch reads, no account inference or writes."""
    numbers = sorted({str(order.get('order_number')) for order in orders if order.get('order_number')})
    number_counts = Counter(str(order.get('order_number') or '') for order in orders)
    if budget is not None:
        await budget.checkpoint()
    # Report parsers can omit the canonical order ID. Recover it alongside the
    # whitelisted source evidence before constructing the ledger join keys.
    projection = {**SALLA_RAW_ATTRIBUTION_PROJECTION, 'user_id': 1,
                  'order_id': 1, 'id': 1, 'reference_id': 1,
                  'raw_by_source.salla_direct.id': 1,
                  'raw_by_source.salla_direct.reference_id': 1,
                  'raw_by_source.salla_direct.order_number': 1}
    # Salla source is polymorphic: scalar "facebook" or {name: "facebook"}.
    # Projecting only source.name drops scalars in MongoDB. Read only these
    # already-approved marketing source fields, never their raw parent object.
    source_paths = {path.split('.source.', 1)[0] + '.source'
                    for path in projection if '.source.' in path}
    projection = {path: value for path, value in projection.items()
                  if not any(path.startswith(source + '.') for source in source_paths)}
    projection.update({path: 1 for path in source_paths})
    raw_rows = await db['unified_orders'].find(
        {'user_id': owner, 'order_number': {'$in': numbers}}, projection,
    ).to_list(length=2 * len(numbers) + 1) if numbers else []
    if len(raw_rows) > 2 * len(numbers):
        raise _TrafficReadLimit('product_traffic_order_read_limit')
    raw = defaultdict(list)
    for row in raw_rows:
        if row.get('user_id') == owner:
            raw[str(row.get('order_number') or '')].append(row)
    canonical, invalid = {}, set()
    for order in orders:
        number = str(order.get('order_number') or '')
        if order.get('user_id') not in (None, owner) or number_counts[number] > 1 or len(raw[number]) > 1:
            invalid.add(id(order))
            continue
        source_order = dict(order)
        if raw[number]:
            stored = raw[number][0]
            explicit_id = any(order.get(field) not in (None, '') for field in ('order_id', 'id', 'reference_id'))
            if explicit_id and _order_key(order) != _order_key(stored):
                invalid.add(id(order))
                continue
            source_order.update(stored)
        canonical[id(order)] = source_order
    key_counts = Counter(_order_key(row) for row in canonical.values())
    keys = sorted(key for key, count in key_counts.items() if key and count == 1)
    if budget is not None:
        await budget.checkpoint()
    rows = await db[LEDGER_COLLECTION].find(
        {'user_id': owner, 'order_key': {'$in': keys}},
        {'_id': 0, 'user_id': 1, 'order_key': 1, 'attribution': 1},
    ).to_list(length=2 * len(keys) + 1) if keys else []
    if len(rows) > 2 * len(keys):
        raise _TrafficReadLimit('product_traffic_ledger_read_limit')
    ledger = defaultdict(list)
    for row in rows:
        if row.get('user_id') == owner:
            ledger[str(row.get('order_key') or '')].append(row)
    result = {}
    for order in orders:
        if budget is not None:
            await budget.checkpoint()
        source_order = canonical.get(id(order)) or {}
        key = _order_key(source_order)
        if id(order) in invalid or not key or key_counts[key] != 1:
            result[id(order)] = _bucket(status='conflict')
        else:
            result[id(order)] = resolve_traffic_source(source_order, ledger[key])
    return result


def add_traffic_units(row, source, quantity):
    key = (source['platform'], source['account_id'], source['account_status'], source['attribution_status'])
    groups = row.setdefault('_traffic_sources', {})
    entry = groups.setdefault(key, {**source, 'units_sold': Decimal(0)})
    entry['units_sold'] += Decimal(str(quantity))


def traffic_sources(row, *, total_units=None):
    entries = [dict(entry) for _, entry in sorted((row.get('_traffic_sources') or {}).items(),
               key=lambda pair: tuple(str(v or '') for v in pair[0]))]
    if total_units is not None and entries:
        # The executive report rounds effective (return-policy-scaled) units
        # to cents. Largest remainders reconcile display rounding only, without
        # changing its underlying quantity policy or attribution allocation.
        cent = Decimal('0.01')
        amounts = [entry['units_sold'].quantize(cent, rounding=ROUND_FLOOR) for entry in entries]
        remainder_order = sorted(range(len(entries)),
            key=lambda index: (-(entries[index]['units_sold'] - amounts[index]), index))
        residual = int((Decimal(str(total_units)) - sum(amounts, Decimal(0))) / cent)
        if 0 <= residual <= len(entries):
            for index in remainder_order[:residual]:
                amounts[index] += cent
            for entry, amount in zip(entries, amounts):
                entry['units_sold'] = amount
    return [{**entry, 'units_sold': float(entry['units_sold'])} for entry in entries]
