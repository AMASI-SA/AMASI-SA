"""Bounded, durable eligible cohort: every invoice is attempted once per cycle.

Only indexed canonical reference pages are walked once to build the cohort.
Invoice evidence is always scoped to those references. A page checkpoint is
published after idempotent item writes; crashes cannot drop the rest of a page.
The final sender still performs its live payment/status/reference/lock checks.
"""
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

from pymongo import UpdateOne

CURRENT_CYCLE = ContextVar('qoyod_current_cycle', default=None)
ITEMS = 'qoyod_auto_cycle_items'
PAGE = 100


async def prepare_page(db, claim):
    from .auto_cycle import COLLECTION, _owned
    from integrations.qoyod.candidate_orders import build_candidate_audit
    state = await db[COLLECTION].find_one(_owned(claim['owner']))
    if not state:
        raise RuntimeError('cycle_lease_lost')
    if state.get('manifest_complete'):
        return True
    owner = claim['orders_user_id']
    if 'manifest_high_ref' not in state:
        highest = await db.unified_orders.find(
            {'user_id': owner, 'order_number': {'$type': 'string', '$ne': ''}},
            {'_id': 0, 'order_number': 1},
        ).sort('order_number', -1).limit(1).max_time_ms(2000).to_list(length=1)
        state['manifest_high_ref'] = highest[0]['order_number'] if highest else ''
        await db[COLLECTION].update_one(_owned(claim['owner']), {'$set': {
            'manifest_high_ref': state['manifest_high_ref'], 'manifest_after': '',
        }})
    refs = []
    if state['manifest_high_ref']:
        refs = [row['order_number'] async for row in db.unified_orders.find(
            {'user_id': owner, 'order_number': {
                '$gt': state.get('manifest_after') or '', '$lte': state['manifest_high_ref'],
            }}, {'_id': 0, 'order_number': 1},
        ).sort('order_number', 1).limit(PAGE).batch_size(PAGE).max_time_ms(2000)]
    if refs:
        from . import auto_send
        statuses = auto_send._configured_pending_statuses(await auto_send._current_settings(db))
        audit = await build_candidate_audit(db, orders_user_id=owner,
            markers_user_id='main', marker_user_ids=('main', owner),
            from_date='2026-07-01', lightweight=True, include_inbox_evidence=False,
            order_numbers=refs, indexed_reference_lookup=True)
        priorities = {r['order_number']: r.get('priority', 1) async for r in
            db.qoyod_order_followup.find({'user_id': owner, 'order_number': {'$in': refs}},
                {'_id': 0, 'order_number': 1, 'priority': 1}).limit(PAGE)}
        operations = [UpdateOne(
            {'cycle_id': claim['cycle_id'], 'order_number': row['order_number']},
            {'$setOnInsert': {'orders_user_id': owner, 'state': 'pending',
                'order_date': row.get('order_date') or '',
                'priority': priorities.get(row['order_number'], 1)}}, upsert=True,
        ) for row in audit['orders'] if row.get('worker_candidate') and any(
            auto_send._pending_row_matches_status(row, status) for status in statuses)]
        if operations:
            await db[ITEMS].bulk_write(operations, ordered=False)
    values = {'manifest_after': refs[-1] if refs else state.get('manifest_after', '')}
    if len(refs) < PAGE:
        values['manifest_complete'] = True
        values['eligible_count'] = await db[ITEMS].count_documents({'cycle_id': claim['cycle_id']})
    updated = await db[COLLECTION].update_one(_owned(claim['owner']), {'$set': values})
    if not updated.matched_count:
        raise RuntimeError('cycle_lease_lost')
    return values.get('manifest_complete', False)


async def discover(db, owner, audit_builder):
    claim = CURRENT_CYCLE.get()
    assert claim and claim['orders_user_id'] == str(owner)
    async def lane(priority, limit):
        return await db[ITEMS].find({'cycle_id': claim['cycle_id'], 'state': 'pending',
            'priority': priority}, {'_id': 0}).sort([
                ('order_date', 1), ('order_number', 1),
            ]).limit(limit).max_time_ms(2000).to_list(length=limit)
    fresh = await lane(0, 3)
    old = await lane(1, 5 - len(fresh))
    if len(fresh) + len(old) < 5 and len(fresh) == 3:
        fresh = await lane(0, 5 - len(old))
    pending = fresh + old
    audit = await audit_builder(db, orders_user_id=str(owner), markers_user_id='main',
        marker_user_ids=('main', str(owner)), from_date='2026-07-01', lightweight=True,
        include_inbox_evidence=False, order_numbers=[r['order_number'] for r in pending],
        indexed_reference_lookup=True)
    from . import auto_send
    statuses = auto_send._configured_pending_statuses(await auto_send._current_settings(db))
    candidates = {r['order_number'] for r in audit['orders'] if r.get('worker_candidate')
        and any(auto_send._pending_row_matches_status(r, status) for status in statuses)}
    changed = [r['order_number'] for r in pending if r['order_number'] not in candidates]
    if changed:
        await terminalize(db, changed, 'eligibility_changed')
    audit['discovery'] = {'scope': 'cycle_manifest', 'checked': len(pending),
                          'intake_hints': 0, 'recovered': 0, 'batch_limit': 5}
    audit['_pending_followup'] = pending
    return audit


async def terminalize(db, references, outcome):
    claim = CURRENT_CYCLE.get()
    if not claim or not references:
        return
    from .auto_cycle import COLLECTION, _owned
    if not await db[COLLECTION].find_one(_owned(claim['owner']), {'_id': 1}):
        raise RuntimeError('cycle_lease_lost')
    await db[ITEMS].update_many({'cycle_id': claim['cycle_id'], 'state': 'pending',
        'order_number': {'$in': list(references)}}, {'$set': {'state': outcome}})


async def record_results(db, result):
    for row in result.get('results') or []:
        reference = row.get('order_number')
        if reference:
            outcome = row.get('outcome')
            if outcome not in {'sent', 'already_sent', 'manual_review', 'retry_later',
                'skipped_status_changed', 'skipped_in_progress', 'credentials_paused'}:
                outcome = 'manual_review'
            await terminalize(db, [str(reference)], outcome)


async def summary(db, cycle_id):
    rows = await db[ITEMS].aggregate([
        {'$match': {'cycle_id': cycle_id}}, {'$group': {'_id': '$state', 'count': {'$sum': 1}}},
    ]).to_list(length=20)
    counts = {row['_id']: row['count'] for row in rows}
    return {'eligible_count': sum(counts.values()),
        'processed_count': sum(v for k, v in counts.items() if k != 'pending'),
        'sent_count': counts.get('sent', 0), 'already_sent_count': counts.get('already_sent', 0),
        'review_count': sum(counts.get(k, 0) for k in (
            'manual_review', 'retry_later', 'held', 'skipped_in_progress', 'credentials_paused'))}


async def expire_finished(db, cycle_id):
    await db[ITEMS].update_many({'cycle_id': cycle_id}, {'$set': {
        'expires_at': datetime.now(timezone.utc) + timedelta(days=2),
    }})
