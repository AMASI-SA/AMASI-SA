"""Manually refreshed, display-only counts from the local invoice mirror.

Never calls a provider or drives sending. GET reads one small saved document;
POST counts projected local records under a cross-process lease and cooldown.
This is local absence, deliberately not certified provider absence.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import uuid

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from integrations.qoyod.invoice_review import (
    _eligible_salla_status, _parse_iso_date, _real_local_invoice, _reference,
    parse_review_range,
)


async def read_manual_summary(db, *, tenant, owner):
    row = await db.qoyod_header_counts.find_one(
        {'_id': f'{tenant}:{owner}'}, {'_id': 0, 'summary': 1},
    ) or {}
    return {'local_summary': row.get('summary')}


async def _count_local(db, tenant, owner):
    start, end = parse_review_range(None, None)
    eligible = set()
    cursor = db.unified_orders.find({
        'user_id': owner, 'order_date': {'$gte': start.isoformat(), '$lte': end.isoformat()},
    }, {'_id': 0, 'order_number': 1, 'order_date': 1, 'order_date_inferred': 1,
        'order_status': 1, 'order_status_slug': 1}).batch_size(100)
    scanned = 0
    async for row in cursor:
        reference = str(row.get('order_number') or '').strip()
        day = _parse_iso_date(row.get('order_date'))
        if (reference and not row.get('order_date_inferred') and day is not None
                and start <= day <= end and _eligible_salla_status(row)):
            eligible.add(reference)
        scanned += 1
        if scanned % 100 == 0:
            await asyncio.sleep(0)
    missing = set(eligible)
    latest_sync = None
    cursor = db.qoyod_invoices.find({'user_id': tenant}, {
        '_id': 0, 'reference': 1, 'qoyod_invoice_id': 1, 'last_sync_at': 1,
    }).batch_size(100)
    async for row in cursor:
        if _real_local_invoice(row):
            missing.discard(_reference(row))
            stamp = row.get('last_sync_at')
            if stamp:
                stamp = stamp.isoformat() if isinstance(stamp, datetime) else str(stamp)
                latest_sync = max(latest_sync or stamp, stamp)
        scanned += 1
        if scanned % 100 == 0:
            await asyncio.sleep(0)
    return {'count': len(missing), 'eligible': len(eligible),
            'matched': len(eligible) - len(missing), 'source': 'local_invoice_mirror',
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'latest_invoice_sync_at': latest_sync,
            'from_date': start.isoformat(), 'to_date': end.isoformat()}


async def refresh_manual_summary(db, *, tenant, owner):
    collection = db.qoyod_header_counts
    key = f'{tenant}:{owner}'
    now = datetime.now(timezone.utc)
    try:
        await collection.insert_one({'_id': key})
    except DuplicateKeyError:
        pass
    token = uuid.uuid4().hex
    claim = await collection.find_one_and_update({
        '_id': key, '$and': [
            {'$or': [{'lease_until': {'$exists': False}}, {'lease_until': {'$lte': now}}]},
            {'$or': [{'next_refresh_at': {'$exists': False}}, {'next_refresh_at': {'$lte': now}}]},
        ],
    }, {'$set': {'token': token, 'lease_until': now + timedelta(seconds=45),
                 'next_refresh_at': now + timedelta(seconds=60)}},
        return_document=ReturnDocument.AFTER)
    if not claim:
        raise HTTPException(429, 'qoyod_header_refresh_busy')
    try:
        summary = await asyncio.wait_for(_count_local(db, tenant, owner), timeout=30)
        saved = await collection.update_one(
            {'_id': key, 'token': token, 'lease_until': {'$gt': datetime.now(timezone.utc)}},
            {'$set': {'summary': summary}},
        )
        if saved.matched_count != 1:
            raise HTTPException(409, 'qoyod_header_refresh_superseded')
        return {'local_summary': summary}
    except asyncio.TimeoutError as exc:
        raise HTTPException(503, 'qoyod_header_refresh_timeout') from exc
    finally:
        await collection.update_one({'_id': key, 'token': token}, {'$unset': {'lease_until': '', 'token': ''}})
