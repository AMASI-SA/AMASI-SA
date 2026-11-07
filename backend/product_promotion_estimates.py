"""Optional, read-only product promotion allocation from stored ad evidence.

These are period revenue-share estimates, never booked costs or provider CPA.
Every denominator includes all products in the selected order cohort for an ad.
"""
import asyncio

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from marketing_report_costs import allocate
from mezan_attribution_order_ledger import LEDGER_COLLECTION, _order_key
from order_currency import SALLA_RAW_CURRENCY_PROJECTION, hydrate_order_currency_fields, order_amount_to_sar
from product_catalog_cost_resolution import index_current_catalog_products, resolve_current_catalog_line_product
from product_traffic_sources import resolve_traffic_source
from sold_products_report_v2 import CATALOG_FIELDS, SELECTABLE_STATUSES, order_matches_status
from product_v2_routes import PRODUCTS

MAX_ORDERS = 10000
MAX_CATALOG = 20000
MAX_ADS = 1000
ZONE = 'Asia/Riyadh'


class EvidenceUnavailable(ValueError):
    pass


def _text(value):
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ''


def _number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


async def _read(db, collection, query, projection, limit):
    rows = await db[collection].find(query, projection).to_list(length=limit + 1)
    if len(rows) > limit:
        raise EvidenceUnavailable('read_limit_exceeded')
    return rows


async def _snapchat_ads(db, owner, account_id, start, end):
    from snapchat_v2.accounts import resolve_account
    from snapchat_v2.entities import SNAPCHAT_ENTITY_FACTS_COLLECTION
    from snapchat_v2.routes import _entity_performance_report, _add_sar_spend
    account = await resolve_account(db, owner, account_id)
    if not account or _text(account.get('ad_account_id')) != account_id:
        raise EvidenceUnavailable('account_unverified')
    # Metadata proves the child-parent relation independently of attribution.
    identities = await _read(db, SNAPCHAT_ENTITY_FACTS_COLLECTION,
        {'user_id': owner, 'ad_account_id': account_id, 'provider': 'snapchat_ads',
         'entity_type': {'$in': ['ad', 'ad_squad']}},
        {'_id': 0, 'external_id': 1, 'entity_type': 1, 'campaign_id': 1,
         'ad_squad_id': 1, 'name': 1}, MAX_ADS * 2)
    groups = {r['external_id']: r for r in identities if r.get('entity_type') == 'ad_squad'}
    if len(identities) != len({(r.get('entity_type'), r.get('external_id')) for r in identities}):
        raise EvidenceUnavailable('duplicate_ad_identity')
    report = await _entity_performance_report(db, user_id=owner, account=account,
        date_from=start, date_to=end, timezone_name=ZONE, action_report_time='conversion',
        entity_type='ad', include_stale=True)
    rows = report.get('rows') or []
    if len(rows) > MAX_ADS:
        raise EvidenceUnavailable('ad_read_limit_exceeded')
    await _add_sar_spend(db, user_id=owner, account=account, rows=rows, totals=dict(report.get('totals') or {}))
    if len(rows) != len({_text(r.get('ad_id')) for r in rows}):
        raise EvidenceUnavailable('duplicate_ad_facts')
    facts = {_text(r.get('ad_id')): r for r in rows}
    result = {}
    for identity in identities:
        if identity.get('entity_type') != 'ad':
            continue
        aid, group = _text(identity.get('external_id')), groups.get(identity.get('ad_squad_id'))
        if not group or not group.get('campaign_id'):
            continue
        cid = _text(group['campaign_id'])
        if identity.get('campaign_id') and _text(identity['campaign_id']) != cid:
            continue
        fact = facts.get(aid, {})
        valid = (fact.get('amount_complete') is True and _text(fact.get('account_id')) == account_id
                 and _text(fact.get('campaign_id')) == cid
                 and _text(fact.get('ad_squad_id')) == _text(group['external_id']))
        result[aid] = {'campaign_id': cid, 'ad_group_id': _text(group['external_id']),
            'ad_group_name': group.get('name'), 'ad_name': identity.get('name'),
            'spend': fact.get('spend_sar') if valid else None,
            'reason': None if valid else 'spend_incomplete'}
    return result


async def _tiktok_ads(db, owner, account_id, start, end):
    from integrations_control_center.tiktok_native_hierarchy import tiktok_workspace
    result, expected, seen = {}, None, set()
    for page in range(1, 11):
        report = await tiktok_workspace(db, owner, from_date=start.isoformat(), to_date=end.isoformat(),
            entity_type='ad', page=page, limit=100, account_id=account_id)
        account = next((a for a in report.get('accounts', []) if _text(a.get('account_id')) == account_id), None)
        if not account or account.get('timezone') != ZONE:
            raise EvidenceUnavailable('account_timezone_unverified')
        if report.get('ai_readiness', {}).get('campaign_identity_ready') is not True:
            raise EvidenceUnavailable('ad_identity_incomplete')
        pagination = report.get('campaign_pagination') or {}
        total, pages = pagination.get('total'), pagination.get('pages')
        if not isinstance(total, int) or not isinstance(pages, int) or total > MAX_ADS or pages > 10:
            raise EvidenceUnavailable('ad_read_limit_exceeded')
        if expected is not None and expected != (total, pages):
            raise EvidenceUnavailable('ad_snapshot_changed')
        expected = (total, pages)
        for entity in report.get('entities', []):
            aid = _text(entity.get('entity_id'))
            if not aid or aid in seen or _text(entity.get('account_id')) != account_id:
                raise EvidenceUnavailable('duplicate_ad_identity')
            seen.add(aid)
            # Smart+ creative report keys are not Ads Manager ad identities.
            # Legacy/unclassified snapshots cannot prove the ad grain either.
            if (entity.get('identity_level') != 'ad'
                    or (entity.get('platform_ad_id') is not None
                        and _text(entity['platform_ad_id']) != aid)):
                continue
            result[aid] = {'campaign_id': _text(entity.get('campaign_id')),
                'ad_group_id': _text(entity.get('adgroup_id')), 'ad_group_name': entity.get('adgroup_name'),
                'ad_name': entity.get('entity_name'),
                'spend': entity.get('spend_sar') if entity.get('data_complete') is True else None,
                'reason': None if entity.get('data_complete') is True else 'spend_incomplete'}
        if page >= pages:
            if len(seen) != total:
                raise EvidenceUnavailable('ad_snapshot_incomplete')
            return result
    raise EvidenceUnavailable('ad_read_limit_exceeded')


async def _stored_ads(db, owner, platform, account_id, start, end):
    if not account_id:
        raise EvidenceUnavailable('account_unverified')
    if platform == 'snapchat':
        return await _snapchat_ads(db, owner, account_id, start, end)
    if platform == 'tiktok':
        return await _tiktok_ads(db, owner, account_id, start, end)
    raise EvidenceUnavailable('stored_ad_spend_unavailable')


def _line_revenue(item, quantity, order):
    # The dashboard helper's missing-price default must not become zero evidence.
    from dashboard_v2_routes import _line_sales_total
    if _number(item.get('total')) is None and _number(item.get('price', item.get('unit_price'))) is None:
        return None
    return _number(order_amount_to_sar(_line_sales_total(item, float(quantity)), order))


async def build_product_promotion_estimates(db, owner, *, from_date, to_date, statuses='default',
                                            product_id, platform, account_id, campaign_id=None,
                                            report_scope='sold', payment_methods=None, shipping_companies=None):
    owner = str(owner)
    if report_scope not in {'sold', 'dashboard'}:
        raise ValueError('invalid_report_scope')
    start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
    selected = [p.strip() for p in statuses.split(',')]
    if start.isoformat() != from_date or end.isoformat() != to_date or start > end or not selected or any(p != 'default' and p not in SELECTABLE_STATUSES for p in selected) or ('default' in selected and selected != ['default']):
        raise ValueError('invalid_promotion_report_scope')
    base = {'rows': [], 'allocation_method': 'product_sales_share', 'currency': 'SAR', 'timezone': ZONE,
            'from_date': from_date, 'to_date': to_date, 'statuses': selected,
            'status': 'available', 'reason': None, 'report_scope': report_scope}
    if (end - start).days >= 31:
        return {**base, 'status': 'unavailable', 'reason': 'period_exceeds_31_days'}
    settings = await db['settings'].find_one({'user_id': owner},
        {'_id': 0, 'report_included_statuses': 1, 'hide_inferred_date_orders': 1}) or {}
    query = {'user_id': owner, 'order_date': {'$gte': from_date, '$lte': to_date}}
    if settings.get('hide_inferred_date_orders'):
        query['order_date_inferred'] = {'$ne': True}
    fields = ('user_id', 'order_id', 'id', 'reference_id', 'order_number', 'products', 'order_status',
              'order_status_slug', 'order_date', 'payment_method', 'shipping_company',
              'actual_refund_amount', 'actual_partial_refund_amount', 'currency', 'original_currency', 'accounting_currency',
              'currency_conversion_status', 'exchange_rate_to_sar', 'original_total_amount',
              'total_amount', 'total', 'total_amount_sar', 'utm_source', 'marketing_source', 'marketing')
    from salla_marketing_attribution import SALLA_RAW_ATTRIBUTION_PROJECTION
    projection = {**SALLA_RAW_CURRENCY_PROJECTION, **SALLA_RAW_ATTRIBUTION_PROJECTION,
                  **{field: 1 for field in fields}, '_id': 0}
    source_paths = {path.split('.source.', 1)[0] + '.source'
                    for path in projection if '.source.' in path}
    projection.update({path: 1 for path in source_paths})
    # Avoid Mongo parent/child path collisions in the source projections.
    projection = {key: value for key, value in projection.items()
                  if not any(key.startswith(parent + '.') for parent in projection if parent != key)}
    orders = await _read(db, 'unified_orders', query, projection, MAX_ORDERS)
    from dashboard_v2_routes import _matches_any
    configured = settings.get('report_included_statuses') or []
    orders = [o for o in orders if o.get('user_id') == owner
              and (_matches_any(o.get('order_status', ''), configured)
                   if report_scope == 'dashboard' and selected == ['default']
                   else order_matches_status(o, selected, configured))
              and _matches_any(o.get('payment_method', ''), [p.strip() for p in (payment_methods or '').split(',') if p.strip()])
              and _matches_any(o.get('shipping_company', ''), [p.strip() for p in (shipping_companies or '').split(',') if p.strip()])]
    policy_rows = await _read(db, 'order_status_policy', {'user_id': owner},
        {'_id': 0, 'status': 1, 'category': 1}, 1000) if report_scope == 'dashboard' else []
    policy = {r.get('status'): r.get('category') for r in policy_rows}
    from order_status_policy import resolve_category
    for order in orders:
        hydrated = hydrate_order_currency_fields([dict(order)])[0]
        order.update(hydrated)  # preserve the narrow raw attribution projection
    counts = Counter(_order_key(o) for o in orders)
    if any(not key or count != 1 for key, count in counts.items()):
        return {**base, 'status': 'unavailable', 'reason': 'order_identity_conflict'}
    ledger_rows = await _read(db, LEDGER_COLLECTION, {'user_id': owner, 'order_key': {'$in': list(counts)}},
        {'_id': 0, 'user_id': 1, 'order_key': 1, 'attribution': 1}, MAX_ORDERS * 2)
    ledgers = defaultdict(list)
    for row in ledger_rows:
        if row.get('user_id') == owner:
            ledgers[row.get('order_key')].append(row)
    catalog = await _read(db, PRODUCTS, {'user_id': owner}, CATALOG_FIELDS, MAX_CATALOG)
    by_id, by_variant, by_sku = index_current_catalog_products(catalog)
    try:
        ads = await _stored_ads(db, owner, platform, _text(account_id), start, end)
        adapter_reason = None
    except (EvidenceUnavailable, ValueError, LookupError, TimeoutError):
        ads, adapter_reason = {}, 'stored_ad_evidence_unavailable'
    groups = {}
    line_count = 0
    for order_index, order in enumerate(orders):
        if order_index % 50 == 0:
            await asyncio.sleep(0)
        ledger = ledgers[_order_key(order)]
        source = resolve_traffic_source(order, ledger)
        if source['platform'] != platform or _text(source.get('account_id')) != _text(account_id):
            continue
        campaign = source.get('_campaign') or {}
        cid = campaign.get('campaign_id')
        if campaign_id and cid != campaign_id:
            continue
        if report_scope == 'dashboard' and (
                resolve_category(order.get('order_status'), policy) in {'refunded', 'cancelled'}
                or (_number(order.get('actual_refund_amount')) or 0) > 0
                or (_number(order.get('actual_partial_refund_amount')) or 0) > 0):
            return {**base, 'rows': [], 'status': 'unavailable', 'reason': 'refund_quantity_scope_unverified'}
        attribution = ledger[0].get('attribution', {}) if len(ledger) == 1 else {}
        aid = _text(attribution.get('ad_id')) if cid else ''
        evidence = ads.get(aid, {})
        gid = _text(evidence.get('ad_group_id'))
        verified = bool(cid and aid and gid and evidence.get('campaign_id') == cid
                        and (not attribution.get('ad_group_id') or _text(attribution['ad_group_id']) == gid))
        key = (cid, aid if verified else None)
        bucket = groups.setdefault(key, {'campaign_id': cid, 'campaign_name': campaign.get('campaign_name'),
            'ad_group_id': gid if verified else None, 'ad_group_name': evidence.get('ad_group_name') if verified else None,
            'ad_id': aid if verified else None, 'ad_name': evidence.get('ad_name') if verified else None,
            'allocation_level': 'ad' if verified else 'campaign',
            'units_sold': Decimal(0), 'ad_spend_sar': None, 'estimated_product_spend_sar': None,
            'estimated_unit_promotion_cost_sar': None, 'status': 'unavailable',
            'reason': adapter_reason or ('ad_identity_unverified' if not verified else evidence.get('reason')),
            '_weights': defaultdict(Decimal), '_complete': True, '_spend': _number(evidence.get('spend')) if verified else None})
        for item in order.get('products') or []:
            line_count += 1
            if line_count > 100000:
                raise EvidenceUnavailable('product_line_limit_exceeded')
            if not isinstance(item, dict):
                bucket['_complete'] = False
                continue
            quantity = _number(item.get('quantity'))
            if quantity is None or quantity <= 0:
                bucket['_complete'] = False
                continue
            product = resolve_current_catalog_line_product(item, products_by_id=by_id, products_by_variant=by_variant, products_by_sku=by_sku)
            identity = _text((product or {}).get('salla_product_id') or item.get('parent_product_id') or item.get('product_id'))
            pkey = 'product:' + identity if identity else _text(item.get('sku') or item.get('name')).casefold()
            target = str(product_id) in (identity, pkey)
            if target:
                bucket['units_sold'] += quantity
            revenue = _line_revenue(item, quantity, order)
            if revenue is None or not pkey:
                bucket['_complete'] = False
            else:
                bucket['_weights'][pkey] += revenue
            if target:
                bucket['_target'] = pkey
    for bucket in groups.values():
        if bucket['units_sold'] <= 0:
            continue
        weights, spend = bucket.pop('_weights'), bucket.pop('_spend')
        complete, target = bucket.pop('_complete'), bucket.pop('_target', None)
        if spend is not None:
            bucket['ad_spend_sar'] = float(spend)
            if not complete:
                bucket['reason'] = 'product_revenue_unavailable'
            elif sum(weights.values()) <= 0:
                bucket['reason'] = 'positive_product_revenue_required'
            else:
                keys = sorted(weights)
                parts = allocate(float(spend), [weights[k] for k in keys])
                allocated = parts[keys.index(target)]
                bucket.update(estimated_product_spend_sar=allocated,
                    estimated_unit_promotion_cost_sar=round(allocated / float(bucket['units_sold']), 4),
                    status='estimated', reason=None)
        elif not bucket['reason']:
            bucket['reason'] = 'spend_incomplete'
        bucket['units_sold'] = float(bucket['units_sold'])
        base['rows'].append(bucket)
    base['rows'].sort(key=lambda r: (r['campaign_id'] or '', r['ad_group_id'] or '', r['ad_id'] or ''))
    return base
