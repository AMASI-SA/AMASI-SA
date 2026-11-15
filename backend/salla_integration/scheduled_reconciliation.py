"""One durable, update-only Salla refresh at 10:00 Asia/Riyadh each day.

A daily cycle may start at 10:00–10:05. Its orders and catalogue steps can
resume, with bounded retries, before 11:00; a missed cycle is never caught up
on startup. Every attempt retains yesterday 10:00 → today 10:00 as its window.
The shared manual-sync lease serializes both steps across worker replicas.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pymongo.errors import DuplicateKeyError

from .routes import _acquire_salla_sync_lease, _release_salla_sync_lease
from .sync import create_sync_log, finish_sync_log, run_orders_sync

log = logging.getLogger(__name__)
STATE = 'salla_scheduled_reconciliation'
MERCHANT_ID = '1673234356'
SCHEDULE_VERSION = 'daily_10_riyadh_update_v1'
RIYADH = ZoneInfo('Asia/Riyadh')
START_GRACE_SECONDS = 5 * 60
CONTINUATION_SECONDS = 60 * 60
RUN_TIMEOUT_SECONDS = 12 * 60  # Below the shared manual-sync lease's 30 minutes.
RETRY_SECONDS = 15 * 60
MAX_ATTEMPTS = 3


def now_utc():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def scheduled_for(at):
    return aware(at).astimezone(RIYADH).replace(
        hour=10, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def slot(at):
    # A new string identity cannot collide with legacy half-day integer slots.
    return aware(at).astimezone(RIYADH).date().isoformat()


def _next_due(at, *, completed=False):
    due = scheduled_for(at)
    return due + timedelta(days=1) if completed or at >= due + timedelta(seconds=START_GRACE_SECONDS) else due


async def _state(db, state_id, at):
    """Migrate metadata only; never adopt an old twice-daily pending window."""
    records = db[STATE]
    state = await records.find_one({'_id': state_id}) or {}
    if state.get('schedule_version') != SCHEDULE_VERSION:
        try:
            await records.update_one(
                {'_id': state_id, 'schedule_version': {'$ne': SCHEDULE_VERSION}},
                {'$set': {'schedule_version': SCHEDULE_VERSION, 'schedule_riyadh': ['10:00'],
                          'timezone': 'Asia/Riyadh', 'mode': 'update_only', 'status': 'scheduled',
                          'next_due_at': _next_due(at), 'schedule_migrated_at': at},
                 '$unset': {'pending_window': '', 'retry_at': '', 'completed_slot': '',
                            'attempt_slot': '', 'attempts': '', 'lease_token': ''}},
                upsert=not bool(state),
            )
        except DuplicateKeyError:
            # Another replica initialized the same schedule identity.
            pass
        state = await records.find_one({'_id': state_id}) or {}
    pending = state.get('pending_window') or {}
    due = scheduled_for(at)
    if pending and (pending.get('slot') != slot(at)
                    or at >= due + timedelta(seconds=CONTINUATION_SECONDS)):
        await records.update_one(
            {'_id': state_id, 'schedule_version': SCHEDULE_VERSION,
             'pending_window': pending},
            {'$set': {'status': 'incomplete', 'last_incomplete_window': pending,
                      'next_due_at': _next_due(at)},
             '$unset': {'pending_window': '', 'retry_at': '', 'lease_token': ''}},
        )
        state = await records.find_one({'_id': state_id}) or {}
    next_due = _next_due(at, completed=(state.get('completed_slot') == slot(at)
                                      or state.get('attempt_slot') == slot(at)))
    if aware(state.get('next_due_at')) != next_due:
        await records.update_one({'_id': state_id, 'schedule_version': SCHEDULE_VERSION,
                                  'next_due_at': state.get('next_due_at')},
                                 {'$set': {'next_due_at': next_due}})
        state['next_due_at'] = next_due
    return state


async def _admitted(db, user_id, at, *, claim=False):
    due = scheduled_for(at)
    deadline = due + timedelta(seconds=CONTINUATION_SECONDS)
    if not due <= at < deadline:
        return False
    key = f'{user_id}:daily10'
    record = await db[STATE].find_one({'_id': key}) or {}
    if (record.get('schedule_version') == SCHEDULE_VERSION
            and record.get('slot') == slot(at)
            and aware(record.get('scheduled_for')) == due):
        return True
    if not at < due + timedelta(seconds=START_GRACE_SECONDS):
        return False
    if claim:
        # Caller holds the same durable lease used by the manual order import.
        await db[STATE].update_one({'_id': key}, {'$set': {
            'schedule_version': SCHEDULE_VERSION, 'slot': slot(at),
            'scheduled_for': due, 'admitted_at': at, 'resume_before': deadline,
            'next_due_at': due + timedelta(days=1), 'mode': 'update_only',
        }}, upsert=True)
    return True


def _can_attempt(state, at):
    if state.get('completed_slot') == slot(at):
        return False
    if aware(state.get('retry_at')) and aware(state['retry_at']) > at:
        return False
    return state.get('attempt_slot') != slot(at) or int(state.get('attempts') or 0) < MAX_ATTEMPTS


async def run_due(db, user_id, *, now=None, catalogue=False):
    at = aware(now or now_utc())
    state_id = f'{user_id}:catalogue' if catalogue else user_id
    state = await _state(db, state_id, at)
    if not _can_attempt(state, at) or not await _admitted(db, user_id, at):
        return False
    token, _ = await _acquire_salla_sync_lease(db, user_id)
    if not token:
        return False
    try:
        at = aware(now or now_utc())
        state = await _state(db, state_id, at)
        if not _can_attempt(state, at) or not await _admitted(db, user_id, at, claim=True):
            return False
        end = scheduled_for(at)
        start = end - timedelta(hours=24)
        window_slot = slot(at)
        attempts = int(state.get('attempts') or 0) if state.get('attempt_slot') == window_slot else 0
        deadline = end + timedelta(seconds=CONTINUATION_SECONDS)
        log_id = await create_sync_log(db, user_id, 'products' if catalogue else 'orders')
        await db.salla_sync_logs.update_one({'id': log_id}, {'$set': {
            'trigger': 'scheduled_daily_10_riyadh', 'window_start': start, 'window_end': end,
            'schedule_riyadh': ['10:00'], 'mode': 'update_only',
        }})
        await db[STATE].update_one({'_id': state_id}, {'$set': {
            'status': 'running', 'log_id': log_id, 'started_at': at,
            'attempt_slot': window_slot, 'attempts': attempts + 1, 'lease_token': token,
            'next_due_at': end + timedelta(days=1),
            'pending_window': {'start': start, 'end': end, 'slot': window_slot},
        }})
        selector = {'_id': state_id, 'lease_token': token, 'attempt_slot': window_slot}
        try:
            work_at = aware(now or now_utc())
            timeout = min(RUN_TIMEOUT_SECONDS, (deadline - work_at).total_seconds())
            if timeout <= 0:
                raise TimeoutError('daily_cycle_expired')
            if catalogue:
                from .scheduled_catalogue import refresh_catalogue
                result = await asyncio.wait_for(refresh_catalogue(
                    db, user_id, run_slot=window_slot, refresh_window=(start, end),
                    existing_only=True), timeout)
                partial = bool(result.get('errors_count') or result.get('truncated') or result.get('incomplete_count')
                               or result.get('coverage_complete') is False)
                await finish_sync_log(db, log_id, 'partial' if partial else 'completed', extra=result)
            else:
                result = await asyncio.wait_for(run_orders_sync(
                    db, user_id, log_id=log_id, refresh_window=(start, end),
                    existing_only=True), timeout)
            if (result.get('errors_count') or result.get('truncated')) and result.get('retryable', True):
                raise RuntimeError('incomplete_reconciliation')
            partial = bool(result.get('errors_count') or result.get('truncated') or result.get('incomplete_count')
                           or result.get('coverage_complete') is False)
            values = {'status': 'partial' if partial else 'completed',
                      'completed_slot': window_slot, 'last_result': result}
            if not partial:
                values['last_success_at'] = now_utc()
            await db[STATE].update_one(selector, {
                '$set': values,
                '$unset': {'pending_window': '', 'retry_at': '', 'last_error_type': '', 'lease_token': ''},
            })
        except BaseException as exc:
            finished = aware(now_utc())
            retry = finished + timedelta(seconds=RETRY_SECONDS)
            retryable = attempts + 1 < MAX_ATTEMPTS and retry < deadline
            await finish_sync_log(db, log_id,
                                  'interrupted' if isinstance(exc, asyncio.CancelledError) else 'failed',
                                  extra={'last_error': type(exc).__name__})
            values = {'status': 'interrupted' if isinstance(exc, asyncio.CancelledError) else 'failed',
                      'last_error_type': type(exc).__name__}
            update = {'$set': values, '$unset': {'lease_token': ''}}
            if retryable:
                values['retry_at'] = retry
            else:
                values['last_incomplete_window'] = {'start': start, 'end': end, 'slot': window_slot}
                # Exhausted is not a successful refresh, but cannot start again.
                values['completed_slot'] = window_slot
                update['$unset'].update({'pending_window': '', 'retry_at': ''})
            await db[STATE].update_one(selector, update)
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            log.warning('Scheduled Salla reconciliation incomplete type=%s', type(exc).__name__)
        return True
    finally:
        await _release_salla_sync_lease(db, user_id, token)


async def scheduler(db):
    await asyncio.sleep(90)
    initialized = False
    while True:
        try:
            at = now_utc()
            due = scheduled_for(at)
            # Outside the daily cycle only startup migrates small metadata.
            # No provider calls or repeated integration scans.
            if not initialized or due <= at < due + timedelta(seconds=CONTINUATION_SECONDS):
                cursor = db.salla_integrations.find({
                    'status': 'connected', 'store_id': {'$in': [MERCHANT_ID, int(MERCHANT_ID)]},
                }, {'user_id': 1})
                async for integration in cursor:
                    if integration.get('user_id'):
                        await run_due(db, str(integration['user_id']))
                        await run_due(db, str(integration['user_id']), catalogue=True)
                initialized = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning('Scheduled Salla check unavailable type=%s', type(exc).__name__)
        await asyncio.sleep(60)
