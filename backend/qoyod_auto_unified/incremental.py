"""Durable bounded discovery. Queue entries are hints, never permission to send.

Intake marks an order dirty. A resumable keyset sweep recovers missed hooks,
legacy rows and direct writers. No reconciliation snapshot is consulted here.
"""
from datetime import datetime, timedelta, timezone
import uuid
from pymongo import UpdateOne

DISCOVERY_BATCH = 100
SWEEP_BATCH = 100
SEND_BATCH = 5


async def write_batch(collection, operations):
    if operations:
        await collection.bulk_write(operations, ordered=False)


def now():
    return datetime.now(timezone.utc)


async def mark_dirty(db, owner, reference):
    await db.qoyod_order_followup.update_one(
        {'user_id': str(owner), 'order_number': str(reference)},
        {'$set': {'revision': uuid.uuid4().hex, 'priority': 0,
                  'next_check_at': now()}}, upsert=True,
    )


async def recover_page(db, owner):
    """One indexed page, checkpoint only after every hint is persisted."""
    key = 'qoyod-followup:' + str(owner)
    state = await db.qoyod_followup_cursors.find_one({'_id': key}) or {}
    due = state.get('next_sweep_at')
    if isinstance(due, datetime):
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due > now():
            return 0
    query = {'user_id': str(owner)}
    if state.get('after'):
        query['order_number'] = {'$gt': state['after']}
    cursor = db.unified_orders.find(query, {'_id': 0, 'order_number': 1})
    cursor = cursor.sort('order_number', 1).limit(SWEEP_BATCH).batch_size(SWEEP_BATCH).max_time_ms(2000)
    references = [str(row['order_number']) async for row in cursor if row.get('order_number')]
    operations = [
        UpdateOne(
            {'user_id': str(owner), 'order_number': reference},
            {'$setOnInsert': {'revision': uuid.uuid4().hex, 'priority': 1,
                              'next_check_at': now()}}, upsert=True,
        )
        for reference in references
    ]
    await write_batch(db.qoyod_order_followup, operations)
    await db.qoyod_followup_cursors.update_one(
        {'_id': key}, {'$set': {'after': references[-1] if references else '',
                               'next_sweep_at': now() + timedelta(minutes=1)}}, upsert=True,
    )
    return len(references)


async def drain_dirty_page(db, owner):
    cursor = db.unified_orders.find(
        {'user_id': str(owner), 'qoyod_followup_dirty': True},
        {'_id': 0, 'order_number': 1, 'qoyod_followup_revision': 1},
    ).sort('order_number', 1).limit(DISCOVERY_BATCH).batch_size(DISCOVERY_BATCH).max_time_ms(2000)
    hints = [row async for row in cursor]
    await write_batch(db.qoyod_order_followup, [UpdateOne(
        {'user_id': str(owner), 'order_number': row['order_number']},
        {'$set': {'revision': row['qoyod_followup_revision'], 'priority': 0, 'next_check_at': now()}},
        upsert=True,
    ) for row in hints])
    # Acknowledge only the revision durably queued. New events and crashes
    # cannot clear an unprocessed hint from the canonical order.
    await write_batch(db.unified_orders, [UpdateOne(
        {'user_id': str(owner), 'order_number': row['order_number'],
         'qoyod_followup_revision': row['qoyod_followup_revision']},
        {'$set': {'qoyod_followup_dirty': False}},
    ) for row in hints])
    return len(hints)


async def discover(db, owner, audit_builder, *, clock=None):
    hinted = await drain_dirty_page(db, owner)
    recovered = await recover_page(db, owner)
    at = clock or now()
    async def due(priority, limit):
        cursor = db.qoyod_order_followup.find(
            {'user_id': str(owner), 'priority': priority, 'next_check_at': {'$lte': at}}, {'_id': 0},
        ).sort([('next_check_at', 1), ('order_number', 1)])
        return [row async for row in cursor.limit(limit).batch_size(limit).max_time_ms(2000)]
    # Reserve capacity for recovery even under continuous new-order intake.
    fresh = await due(0, DISCOVERY_BATCH // 2)
    recovery = await due(1, DISCOVERY_BATCH - len(fresh))
    pending = fresh + recovery
    references = [row['order_number'] for row in pending]
    # This exact, bounded scope is re-read from canonical orders and local
    # references. All final live payment/status/provider/lock gates stay intact.
    audit = await audit_builder(
        db, orders_user_id=str(owner), markers_user_id='main',
        marker_user_ids=('main', str(owner)), from_date='2026-07-01',
        lightweight=True, include_inbox_evidence=False,
        order_numbers=references,
        indexed_reference_lookup=True,
    )
    candidate_refs = {row['order_number'] for row in audit['orders'] if row.get('worker_candidate')}
    operations = []
    for row in pending:
        # Fence against an intake update arriving during discovery. A crash
        # before this checkpoint simply repeats a safe canonical read.
        if row['order_number'] in candidate_refs:
            continue
        delay = timedelta(hours=4)
        operations.append(UpdateOne(
            {'user_id': str(owner), 'order_number': row['order_number'], 'revision': row['revision']},
            {'$set': {'next_check_at': at + delay, 'priority': 1}},
        ))
    await write_batch(db.qoyod_order_followup, operations)
    audit['discovery'] = {'scope': 'incremental', 'checked': len(pending),
                          'intake_hints': hinted,
                          'recovered': recovered, 'batch_limit': DISCOVERY_BATCH}
    audit['_pending_followup'] = pending
    return audit


async def defer_selected(db, owner, pending, references):
    if not pending:
        return
    await write_batch(db.qoyod_order_followup, [UpdateOne(
        {'user_id': str(owner), 'order_number': row['order_number'], 'revision': row['revision']},
        {'$set': {'next_check_at': now() + timedelta(minutes=1), 'priority': 1}},
    ) for row in pending if row['order_number'] in references])
