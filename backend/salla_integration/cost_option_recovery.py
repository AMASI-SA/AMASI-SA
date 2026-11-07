"""Bounded recovery of empty Salla item choices; never infer a free option.

The worker uses List Order Items only: background work must not open Order
Details or mark an order read in Salla. Prices, statuses and financial facts
are not copied from the response. Canonical option writes join the existing
component owner transaction, with a compare-before-write source fence.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import uuid

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from report_option_recovery import attach_report_option_evidence, _has_options

COLLECTION = 'salla_cost_option_recovery'
POLL_SECONDS = 60
SCAN_LIMIT = 100
log = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc)


def raw_order(order):
    sources = order.get('raw_by_source')
    raw = sources.get('salla_direct') if isinstance(sources, dict) else None
    return raw if isinstance(raw, dict) else {}


def source_digest(order):
    # Covers every source field reused by the component fence, including prices
    # and status, so an in-flight response cannot regress a concurrent webhook.
    value = [order.get('products'), raw_order(order), order.get('g47_salla_snapshot'),
             order.get('order_status_slug'), order.get('last_salla_direct_status_source_updated_at')]
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def candidate(order, option_product_ids):
    raw = raw_order(order)
    number = str(order.get('order_number') or '')
    if not number or str(raw.get('reference_id') or '') != number or not raw.get('id'):
        return False
    # Existing raw evidence is already recovered by current-cost reports.
    probe = deepcopy(order)
    attach_report_option_evidence([probe], [order])
    return any(str(item.get('product_id') or '') in option_product_ids
               and item.get('order_item_id') and not _has_options(item)
               and not item.get('_report_options_raw')
               for item in probe.get('products') or [] if isinstance(item, dict))


def option_patch(order, provider_items):
    """Exact native line/product/quantity matches only; fill empty choices only."""
    raw = raw_order(order)
    if (not isinstance(provider_items, list) or len(provider_items) > 1000
            or not isinstance(raw.get('items'), list)):
        return None
    probe = deepcopy(order)
    evidence = {'order_number': order['order_number'], 'raw_by_source': {'salla_direct': {
        'reference_id': raw.get('reference_id'), 'items': provider_items}}}
    attach_report_option_evidence([probe], [evidence])
    products, merged = deepcopy(order.get('products') or []), deepcopy(raw)
    changed = False
    for index, item in enumerate(probe.get('products') or []):
        choices = item.get('_report_options_raw') if isinstance(item, dict) else None
        if not choices:
            continue
        matches = [row for row in merged['items'] if isinstance(row, dict)
                   and str(row.get('id')) == str(item.get('order_item_id'))]
        # A missing/duplicate raw line is not authority to reconstruct a snapshot.
        if len(matches) != 1 or matches[0].get('options'):
            continue
        # Check the stored raw identity too, rather than only canonical identity.
        check = deepcopy(order)
        attach_report_option_evidence([check], [{'order_number': order['order_number'],
            'raw_by_source': {'salla_direct': {**raw, 'items': [
                {**matches[0], 'options': choices}]}}}])
        if not check['products'][index].get('_report_options_raw'):
            continue
        from .sync import _legacy_item_options
        products[index]['options'] = _legacy_item_options({'options': choices})
        products[index]['options_raw'] = deepcopy(choices)
        matches[0]['options'] = deepcopy(choices)
        changed = True
    return {'products': products, 'raw_by_source.salla_direct': merged} if changed else None


class SourceChanged(Exception):
    pass


async def apply_options(db, order, items):
    patch = option_patch(order, items)
    if not patch:
        return False
    from fulfillment_v2_routes import persist_component_source_snapshot
    owner, number = order['user_id'], order['order_number']
    selector = {'user_id': owner, 'order_number': number}
    expected = source_digest(order)

    async def check(scoped):
        current = await scoped.unified_orders.find_one(selector)
        if not current or source_digest(current) != expected:
            raise SourceChanged()

    async def persist(scoped):
        await scoped.unified_orders.update_one(selector, {'$set': patch})
        return {'created': False}

    result = await persist_component_source_snapshot(db, user_id=owner,
        order_number=number, payload=patch['raw_by_source.salla_direct'],
        before_persist=check, persist=persist, allow_delivery_observation=False)
    return not (result.get('blocked') or result.get('stale'))


async def recover_one(db, order):
    owner, number = order['user_id'], order['order_number']
    key = hashlib.sha256(f'{owner}:{number}'.encode()).hexdigest()
    records = db[COLLECTION]
    instant = now()
    try:
        await records.update_one({'_id': key}, {'$setOnInsert': {
            'next_attempt_at': instant, 'attempts': 0}}, upsert=True)
    except DuplicateKeyError:
        pass
    claim = uuid.uuid4().hex
    attempt = await records.find_one_and_update(
        {'_id': key, 'next_attempt_at': {'$lte': instant}},
        {'$set': {'claim': claim, 'next_attempt_at': instant + timedelta(minutes=5)},
         '$inc': {'attempts': 1}}, return_document=ReturnDocument.AFTER)
    if not attempt:
        return False
    outcome = 'unresolved'
    try:
        from .sync import _fetch_salla_order_items
        items = await asyncio.wait_for(_fetch_salla_order_items(db, owner,
            str(raw_order(order)['id'])), timeout=15)
        if await apply_options(db, order, items):
            outcome = 'recovered'
    except SourceChanged:
        outcome = 'source_changed'
    except Exception:
        # No provider payload or customer text in retry records/logs.
        outcome = 'retry_required'
    attempts = min(int(attempt['attempts']), 10)
    seconds = 86400 if outcome == 'recovered' else min(60 * 2 ** attempts, 86400)
    await records.update_one({'_id': key, 'claim': claim}, {'$set': {
        'outcome': outcome, 'last_attempt_at': now(),
        'next_attempt_at': now() + timedelta(seconds=seconds)}})
    return True


async def run_once(db):
    """One global lease, a persisted scan cursor, <=100 rows and four fetches."""
    records = db[COLLECTION]
    instant, claim = now(), uuid.uuid4().hex
    try:
        await records.update_one({'_id': 'scan'}, {'$setOnInsert': {
            'due_at': instant}}, upsert=True)
    except DuplicateKeyError:
        pass
    lease = await records.find_one_and_update({'_id': 'scan', 'due_at': {'$lte': instant}},
        {'$set': {'claim': claim, 'due_at': instant + timedelta(minutes=3)}},
        return_document=ReturnDocument.AFTER)
    if not lease:
        return
    query = {'raw_by_source.salla_direct.id': {'$exists': True}}
    if lease.get('cursor'):
        query['_id'] = {'$lt': lease['cursor']}
    projection = {'user_id': 1, 'order_number': 1, 'products': 1,
                  'raw_by_source.salla_direct.id': 1,
                  'raw_by_source.salla_direct.reference_id': 1,
                  'raw_by_source.salla_direct.items': 1}
    hot = await db.unified_orders.find(
        {'raw_by_source.salla_direct.id': {'$exists': True}}, projection
    ).sort('_id', -1).limit(20).to_list(20)
    history = await db.unified_orders.find(query, projection).sort('_id', -1).limit(80).to_list(80)
    rows = list({row['_id']: row for row in hot + history}.values())
    owners = {row.get('user_id') for row in rows} - {None, ''}
    connections = await db.salla_integrations.find(
        {'user_id': {'$in': list(owners)}, 'status': 'connected'},
        {'user_id': 1}).limit(SCAN_LIMIT).to_list(SCAN_LIMIT)
    connected = {row['user_id'] for row in connections}
    option_ids_by_owner = {}
    from product_option_cost_routes import BINDINGS
    for owner in connected:
        ids = list({str(p.get('product_id')) for order in rows if order.get('user_id') == owner
                    for p in order.get('products') or []
                    if isinstance(p, dict) and not _has_options(p) and p.get('product_id')})
        if not ids:
            continue
        bindings = await db[BINDINGS].find({'user_id': owner, 'salla_product_id': {'$in': ids}},
            {'salla_product_id': 1}).limit(1000).to_list(1000)
        definitions = await db['mezan_bundle_definitions_v2'].find(
            {'user_id': owner, 'product_id': {'$in': ids}, 'product_type': 'composite'},
            {'product_id': 1}).limit(1000).to_list(1000)
        option_ids = {str(b['salla_product_id']) for b in bindings}
        option_ids.update(str(d['product_id']) for d in definitions)
        option_ids_by_owner[owner] = option_ids
    fetched, last = 0, None
    attempted = set()
    history_exhausted = len(history) < 80
    for lane, lane_rows, budget in (('new', hot, 2), ('history', history, 2)):
        lane_fetches = 0
        for order in lane_rows:
            if lane == 'history':
                last = order['_id']
            if order['_id'] in attempted:
                continue
            owner = order.get('user_id')
            ids = option_ids_by_owner.get(owner, set())
            if ids and candidate(order, ids):
                full = await db.unified_orders.find_one({'_id': order['_id'], 'user_id': owner})
                if full and candidate(full, ids):
                    did_fetch = await recover_one(db, full)
                    if did_fetch:
                        attempted.add(order['_id'])
                        lane_fetches += 1
                        fetched += 1
            if lane_fetches >= budget:
                if lane == 'history':
                    history_exhausted = False
                break
            await asyncio.sleep(0)
    cursor = None if history_exhausted else last
    await records.update_one({'_id': 'scan', 'claim': claim}, {'$set': {
        'cursor': cursor, 'due_at': now() + timedelta(seconds=POLL_SECONDS)}})


async def start_worker(db):
    async def run():
        while True:
            try:
                await asyncio.wait_for(run_once(db), timeout=90)
            except Exception:
                log.warning('salla.cost_options.recovery_deferred')
            await asyncio.sleep(POLL_SECONDS)
    return asyncio.create_task(run(), name='salla-cost-options')
