"""Durable two-hour automatic sending; discovery and reconciliation are separate.

Mongo's clock controls both eligibility and lease ownership. A crashed cycle
can resume after its lease expires; completed/failed cycles wait two hours.
Only bounded counts and machine error names are stored here, never order data.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import logging
import time
import uuid

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

INTERVAL_SECONDS = 2 * 60 * 60
POLL_SECONDS = 60.0
LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 10
SLICE_BATCHES = 100
SLICE_SECONDS = 60
COLLECTION = 'qoyod_auto_cycles'
log = logging.getLogger(__name__)


def _owned(owner):
    return {'_id': 'main', 'owner': owner, 'status': 'running',
            '$expr': {'$gt': ['$lease_until', '$$NOW']}}


async def _ensure_state(db):
    collection = db[COLLECTION]
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        await collection.update_one({'_id': 'main'}, {'$setOnInsert': {
            'status': 'idle', 'lease_until': epoch, 'next_due_at': epoch,
        }}, upsert=True)
    except DuplicateKeyError:
        pass


async def request_cycle(db, *, actor):
    from . import auto_send
    if not auto_send.is_armed(await auto_send._current_settings(db)):
        raise ValueError('automatic_sender_not_armed')
    await _ensure_state(db)
    request_id = uuid.uuid4().hex
    row = await db[COLLECTION].find_one_and_update(
        {'_id': 'main', 'status': {'$ne': 'running'}, 'manual_requested': {'$ne': True}},
        [{'$set': {'manual_requested': True, 'manual_requested_at': '$$NOW',
                   'manual_request_id': request_id, 'requested_by': str(actor)}}],
        return_document=ReturnDocument.AFTER,
    )
    if row:
        return {'ok': True, 'accepted': True, 'joined': False, 'status': 'queued',
                'request_id': request_id}
    row = await db[COLLECTION].find_one({'_id': 'main'}) or {}
    return {'ok': True, 'accepted': True, 'joined': True,
            'status': 'running' if row.get('status') == 'running' else 'queued',
            'request_id': (row.get('cycle_id') if row.get('status') == 'running'
                           else row.get('manual_request_id'))}


async def _claim(db, orders_user_id):
    collection = db[COLLECTION]
    await _ensure_state(db)
    owner = uuid.uuid4().hex
    resume = {'$eq': ['$status', 'running']}
    return await collection.find_one_and_update(
        {'_id': 'main', '$expr': {'$and': [
            {'$lte': ['$lease_until', '$$NOW']},
            {'$or': [resume, {'$eq': ['$manual_requested', True]},
                     {'$lte': ['$next_due_at', '$$NOW']}]},
        ]}},
        [{'$set': {
            'owner': owner, 'status': 'running',
            'cycle_id': {'$cond': [resume, '$cycle_id', owner]},
            'started_at': {'$cond': [resume, '$started_at', '$$NOW']},
            'batches': {'$cond': [resume, '$batches', 0]},
            'sent_count': {'$cond': [resume, '$sent_count', 0]},
            'attempted_count': {'$cond': [resume, '$attempted_count', 0]},
            'orders_user_id': {'$cond': [resume, '$orders_user_id', orders_user_id]},
            'trigger': {'$cond': [resume, '$trigger',
                {'$cond': [{'$eq': ['$manual_requested', True]}, 'manual', 'automatic']}]},
            'manual_requested': False,
            'manifest_high_ref': {'$cond': [resume, '$manifest_high_ref', '$$REMOVE']},
            'manifest_after': {'$cond': [resume, '$manifest_after', '']},
            'manifest_complete': {'$cond': [resume, '$manifest_complete', False]},
            'eligible_count': {'$cond': [resume, '$eligible_count', None]},
            'processed_count': {'$cond': [resume, '$processed_count', 0]},
            'already_sent_count': {'$cond': [resume, '$already_sent_count', 0]},
            'review_count': {'$cond': [resume, '$review_count', 0]},
            'phase': {'$cond': [resume, '$phase', 'preparing']},
            'last_error': None,
            'lease_until': {'$add': ['$$NOW', LEASE_SECONDS * 1000]},
            'next_due_at': {'$add': ['$$NOW', INTERVAL_SECONDS * 1000]},
        }}, {'$unset': 'finished_at'}],
        return_document=ReturnDocument.AFTER,
    )


async def _heartbeat(db, owner):
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        result = await db[COLLECTION].update_one(_owned(owner), [{'$set': {
            'lease_until': {'$add': ['$$NOW', LEASE_SECONDS * 1000]},
        }}])
        if not result.matched_count:
            raise RuntimeError('cycle_lease_lost')


async def read_status(db):
    row = await db[COLLECTION].find_one({'_id': 'main'}, {
        '_id': 0, 'status': 1, 'started_at': 1, 'finished_at': 1,
        'next_due_at': 1, 'batches': 1, 'sent_count': 1,
        'attempted_count': 1, 'last_error': 1,
        'phase': 1, 'eligible_count': 1, 'processed_count': 1, 'already_sent_count': 1,
        'review_count': 1, 'manual_requested': 1, 'trigger': 1,
    }) or {}
    row = {key: ((value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
                  if isinstance(value, datetime) else value) for key, value in row.items()}
    return {**row, 'automatic_interval_seconds': INTERVAL_SECONDS,
            'automatic_interval_minutes': 120, 'cadence_basis': 'last_finished_at',
            'slice_batch_limit': SLICE_BATCHES, 'batch_limit': 5}


async def _drain_body(db, claim, batch_runner, batch_limit, max_batches, batch_pause, use_manifest):
    from . import cycle_manifest as manifest, auto_send
    owner = claim['owner']
    batches = int(claim.get('batches') or 0)
    sent = int(claim.get('sent_count') or 0)
    attempted = int(claim.get('attempted_count') or 0)
    status, error = 'continuing', None
    deadline = time.monotonic() + SLICE_SECONDS
    units = 0
    manifest_complete = bool(claim.get('manifest_complete'))
    while units < max_batches and time.monotonic() < deadline:
        if not await db[COLLECTION].find_one(_owned(owner), {'_id': 1}):
            return {'status': 'lease_lost', 'batches': batches, 'sent_count': sent}
        if use_manifest:
            settings = await auto_send._current_settings(db)
            if (not auto_send.is_armed(settings)
                    or str(settings.get('plan_b_auto_send_orders_user_id')) != claim['orders_user_id']):
                status = 'disarmed'
                break
            if not manifest_complete:
                manifest_complete = await manifest.prepare_page(db, claim)
                units += 1
                await asyncio.sleep(0)
                continue
            await db[COLLECTION].update_one(_owned(owner), {'$set': {'phase': 'sending'}})
        # Never cancel a financial request merely because a work slice ended.
        # Complete this small batch using the sender's existing transport bounds.
        result = await batch_runner(db, batch_limit=batch_limit)
        if result.get('status') == 'lease_busy':
            await asyncio.sleep(min(5, max(0, deadline - time.monotonic())))
            continue
        batches += 1
        units += 1
        sent += max(0, int(result.get('sent_count') or 0))
        attempted += len(result.get('results') or [])
        checkpoint = await db[COLLECTION].update_one(_owned(owner), {'$set': {
            'batches': batches, 'sent_count': sent, 'attempted_count': attempted,
        }})
        if not checkpoint.matched_count:
            return {'status': 'lease_lost', 'batches': batches, 'sent_count': sent}
        if use_manifest:
            await manifest.record_results(db, result)
        if not result.get('ok'):
            status = 'round_failed'
            error = str(result.get('exception_type') or 'batch_failed')[:80]
            break
        state = result.get('status')
        if state in {'not_armed', 'disarmed', 'credentials_paused',
                     'unified_auto_rollout_disabled', 'lease_lost'}:
            status = state
            break
        if state == 'idle' and not (result.get('discovery') or {}).get('checked'):
            status = 'idle'
            break
        await asyncio.sleep(max(0, batch_pause))
    return {'status': status, 'last_error': error, 'batches': batches,
            'sent_count': sent, 'attempted_count': attempted}


async def _drain(db, claim, batch_runner, batch_limit, max_batches, batch_pause, use_manifest):
    from .cycle_manifest import CURRENT_CYCLE
    token = CURRENT_CYCLE.set(claim if use_manifest else None)
    try:
        return await _drain_body(db, claim, batch_runner, batch_limit, max_batches, batch_pause, use_manifest)
    finally:
        CURRENT_CYCLE.reset(token)


async def run_due_cycle(db, *, batch_runner=None, batch_limit=5,
                        max_batches=SLICE_BATCHES, batch_pause=1, use_manifest=None):
    from . import auto_send
    from .recovery_campaign import CAMPAIGN
    settings = await auto_send._current_settings(db)
    if settings.get(auto_send.UNIFIED_CANDIDATE_AUTO_FLAG) is True:
        recovered = await auto_send._recover_legacy_circuit_breaker(db, settings)
        settings = recovered or settings
    recovery = await db.qoyod_404_campaigns.find_one({'_id': CAMPAIGN, 'state': 'active'}, {'_id': 1})
    if not auto_send.is_armed(settings) and not recovery:
        return {'claimed': False, 'status': 'not_armed'}
    claim = await _claim(db, str(settings.get('plan_b_auto_send_orders_user_id') or ''))
    if not claim:
        return {'claimed': False, 'status': 'not_due'}
    owner = claim['owner']
    if use_manifest is None:
        use_manifest = (batch_runner is None and bool(claim['orders_user_id'])
                        and auto_send.is_armed(settings) and not recovery)
    batch_runner = batch_runner or auto_send._run_automatic_batch
    heartbeat = asyncio.create_task(_heartbeat(db, owner))
    work = asyncio.create_task(_drain(db, claim, batch_runner,
        min(5, max(1, int(batch_limit))), min(SLICE_BATCHES, max(1, int(max_batches))), batch_pause, use_manifest))
    try:
        done, _ = await asyncio.wait((heartbeat, work), return_when=asyncio.FIRST_COMPLETED)
        if heartbeat in done:
            work.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await work
            return {'claimed': True, 'status': 'lease_lost', 'cycle_id': claim['cycle_id']}
        try:
            result = await work
        except Exception as exc:
            result = {'status': 'round_failed', 'last_error': type(exc).__name__}
        if use_manifest and result['status'] != 'lease_lost':
            from .cycle_manifest import summary
            result.update(await summary(db, claim['cycle_id']))
        if result['status'] != 'lease_lost':
            progress = {key: result[key] for key in ('eligible_count', 'processed_count',
                'sent_count', 'already_sent_count', 'review_count') if key in result}
            if result['status'] == 'continuing':
                # A slice is a checkpoint, not the end of the two-hour cycle.
                # Another healthy replica can resume this exact cohort at once.
                update = {'status': 'running', 'lease_until': '$$NOW', **progress}
            else:
                update = {'status': result['status'], 'last_error': result.get('last_error'),
                    'phase': 'complete' if result['status'] == 'idle' else 'stopped',
                    'finished_at': '$$NOW', 'lease_until': '$$NOW', **progress,
                    'next_due_at': {'$add': ['$$NOW', INTERVAL_SECONDS * 1000]}}
            finished = await db[COLLECTION].update_one(_owned(owner), [{'$set': update}])
            if not finished.matched_count:
                result['status'] = 'lease_lost'
            elif use_manifest and result['status'] != 'continuing':
                from .cycle_manifest import expire_finished
                await expire_finished(db, claim['cycle_id'])
        log.info('Qoyod automatic cycle status=%s batches=%s sent=%s interval=%s',
                 result['status'], result.get('batches', 0), result.get('sent_count', 0), INTERVAL_SECONDS)
        return {**result, 'claimed': True, 'cycle_id': claim['cycle_id']}
    finally:
        for task in (heartbeat, work):
            task.cancel()
        await asyncio.gather(heartbeat, work, return_exceptions=True)


async def followup_loop(db):
    """Small indexed reads collect hints; this loop cannot create invoices."""
    from . import auto_send
    from qoyod_auto_unified.incremental import drain_dirty_page, recover_page
    while True:
        try:
            settings = await auto_send._current_settings(db)
            owner = settings.get('plan_b_auto_send_orders_user_id')
            if owner and settings.get('enabled') and settings.get(auto_send.UNIFIED_CANDIDATE_AUTO_FLAG):
                await drain_dirty_page(db, str(owner))
                await recover_page(db, str(owner))
        except Exception as exc:
            log.warning('Qoyod bounded followup unavailable code=%s', type(exc).__name__)
        await asyncio.sleep(60)
