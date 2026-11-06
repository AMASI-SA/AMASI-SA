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
LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 10
MAX_BATCHES = 100
MAX_CYCLE_SECONDS = 20 * 60
COLLECTION = 'qoyod_auto_cycles'
log = logging.getLogger(__name__)


def _owned(owner):
    return {'_id': 'main', 'owner': owner, 'status': 'running',
            '$expr': {'$gt': ['$lease_until', '$$NOW']}}


async def _claim(db):
    collection = db[COLLECTION]
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        await collection.update_one({'_id': 'main'}, {'$setOnInsert': {
            'status': 'idle', 'lease_until': epoch, 'next_due_at': epoch,
        }}, upsert=True)
    except DuplicateKeyError:
        pass
    owner = uuid.uuid4().hex
    resume = {'$eq': ['$status', 'running']}
    return await collection.find_one_and_update(
        {'_id': 'main', '$expr': {'$and': [
            {'$lte': ['$lease_until', '$$NOW']},
            {'$or': [resume, {'$lte': ['$next_due_at', '$$NOW']}]},
        ]}},
        [{'$set': {
            'owner': owner, 'status': 'running',
            'cycle_id': {'$cond': [resume, '$cycle_id', owner]},
            'started_at': {'$cond': [resume, '$started_at', '$$NOW']},
            'batches': {'$cond': [resume, '$batches', 0]},
            'sent_count': {'$cond': [resume, '$sent_count', 0]},
            'attempted_count': {'$cond': [resume, '$attempted_count', 0]},
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
    }) or {}
    return {**row, 'automatic_interval_seconds': INTERVAL_SECONDS,
            'automatic_interval_minutes': 120, 'cadence_basis': 'last_finished_at',
            'max_batches': MAX_BATCHES, 'batch_limit': 5}


async def _drain(db, claim, batch_runner, batch_limit, max_batches, batch_pause):
    owner = claim['owner']
    batches = int(claim.get('batches') or 0)
    sent = int(claim.get('sent_count') or 0)
    attempted = int(claim.get('attempted_count') or 0)
    status, error = 'budget_exhausted', None
    # Resume uses the original server-clock start, not a fresh twenty minutes.
    now = await db[COLLECTION].aggregate([
        {'$match': _owned(owner)}, {'$project': {'remaining_ms': {
            '$subtract': [MAX_CYCLE_SECONDS * 1000, {'$subtract': ['$$NOW', '$started_at']}]}, '_id': 0}},
    ]).to_list(length=1)
    if not now:
        return {'status': 'lease_lost', 'batches': batches, 'sent_count': sent}
    deadline = time.monotonic() + max(0, now[0]['remaining_ms']) / 1000
    while batches < max_batches and time.monotonic() < deadline:
        if not await db[COLLECTION].find_one(_owned(owner), {'_id': 1}):
            return {'status': 'lease_lost', 'batches': batches, 'sent_count': sent}
        remaining = deadline - time.monotonic()
        try:
            async with asyncio.timeout(max(.001, remaining)):
                result = await batch_runner(db, batch_limit=batch_limit)
        except TimeoutError:
            status = 'budget_exhausted'
            break
        if result.get('status') == 'lease_busy':
            await asyncio.sleep(min(5, max(0, deadline - time.monotonic())))
            continue
        batches += 1
        sent += max(0, int(result.get('sent_count') or 0))
        attempted += len(result.get('results') or [])
        checkpoint = await db[COLLECTION].update_one(_owned(owner), {'$set': {
            'batches': batches, 'sent_count': sent, 'attempted_count': attempted,
        }})
        if not checkpoint.matched_count:
            return {'status': 'lease_lost', 'batches': batches, 'sent_count': sent}
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


async def run_due_cycle(db, *, batch_runner=None, batch_limit=5,
                        max_batches=MAX_BATCHES, batch_pause=1):
    from . import auto_send
    from .recovery_campaign import CAMPAIGN
    settings = await auto_send._current_settings(db)
    if settings.get(auto_send.UNIFIED_CANDIDATE_AUTO_FLAG) is True:
        recovered = await auto_send._recover_legacy_circuit_breaker(db, settings)
        settings = recovered or settings
    recovery = await db.qoyod_404_campaigns.find_one({'_id': CAMPAIGN, 'state': 'active'}, {'_id': 1})
    if not auto_send.is_armed(settings) and not recovery:
        return {'claimed': False, 'status': 'not_armed'}
    claim = await _claim(db)
    if not claim:
        return {'claimed': False, 'status': 'not_due'}
    owner = claim['owner']
    batch_runner = batch_runner or auto_send._run_automatic_batch
    heartbeat = asyncio.create_task(_heartbeat(db, owner))
    work = asyncio.create_task(_drain(db, claim, batch_runner,
        min(5, max(1, int(batch_limit))), min(MAX_BATCHES, max(1, int(max_batches))), batch_pause))
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
        if result['status'] != 'lease_lost':
            finished = await db[COLLECTION].update_one(_owned(owner), [{'$set': {
                'status': result['status'], 'last_error': result.get('last_error'),
                'finished_at': '$$NOW', 'lease_until': '$$NOW',
                'next_due_at': {'$add': ['$$NOW', INTERVAL_SECONDS * 1000]},
            }}])
            if not finished.matched_count:
                result['status'] = 'lease_lost'
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
