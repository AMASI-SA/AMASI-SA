"""Acquire missing reverse-shipment evidence within the existing visible-refresh budget."""
from datetime import datetime, timedelta, timezone

from operational_balance_store import check_authorization
from operational_delivery_register import history_version
from operational_return_waybills import PROOF, TERMINAL, native_proof, reverse_rows, text
from .courier_delivery_sync import _complete_pages

RAW = 'raw_by_source.salla_direct'
RETRY = 'salla_operational_return_lookup_after'


async def acquire(db, owner, pending, *, limit):
    now = datetime.now(timezone.utc)
    checked = saved = 0
    targets = {text(row.get('source_id')): text(row.get('order_number')) for row in pending}
    numbers = [number for number in targets.values() if number]
    for _ in range(min(limit, len(numbers))):
        await check_authorization()
        row = await db.unified_orders.find_one_and_update(
            {'user_id': owner, 'order_number': {'$in': numbers},
             '$or': [{RETRY: {'$exists': False}}, {RETRY: {'$lte': now}}]},
            {'$set': {RETRY: now + timedelta(minutes=30)}},
            projection={'order_number': 1, RAW: 1, 'g47_salla_snapshot': 1, 'order_id': 1},
            sort=[(RETRY, 1), ('_id', 1)])
        if not row:
            break
        raw = (row.get('raw_by_source') or {}).get('salla_direct') or {}
        oid, number = text(raw.get('id')), text(row.get('order_number'))
        if (targets.get(oid) != number or text(raw.get('reference_id') or raw.get('order_number')) != number
                or text(row.get('order_id')) not in {'', oid}
                or (row.get('g47_salla_snapshot') or {}).get('requires_authoritative_refresh')):
            continue
        checked += 1
        try:
            rows = await _complete_pages(db, owner, '/shipments', {'order_id': oid}, max_pages=3)
            legs = reverse_rows(rows)
            histories = []
            if (len(legs) == 1 and legs[0].get('status') in TERMINAL
                    and not (legs[0].get('actual_delivered_at') or legs[0].get('returned_at'))):
                histories = await _complete_pages(db, owner, f'/orders/{oid}/histories', {}, max_pages=3)
            proof = native_proof(rows, histories, oid, number)
        except (KeyError, ValueError, TypeError):
            continue
        await check_authorization()
        result = await db.unified_orders.update_one(
            {'_id': row['_id'], 'user_id': owner, 'order_number': number, RAW: raw,
             'g47_salla_snapshot': row.get('g47_salla_snapshot'), RETRY: now + timedelta(minutes=30)},
            {'$set': {PROOF: {'order_id': oid, 'version': history_version(raw), 'legs': proof,
                              'checked_at': now.isoformat()}}})
        saved += result.matched_count
    return {'checked': checked, 'saved': saved}
