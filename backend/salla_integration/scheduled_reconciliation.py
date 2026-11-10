"""Amasi's twice-daily, durable 24-hour order reconciliation.

Runs in the dedicated background process. Uses the manual import's Mongo lease,
canonical snapshot/ownership guards, and only Salla GET requests. Product V2
metadata also refreshes; no stock, preparation or financial-provider actions.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .routes import _acquire_order_sync_lease, _release_order_sync_lease
from .sync import create_sync_log, run_orders_sync

log = logging.getLogger(__name__)
STATE = 'salla_scheduled_reconciliation'
MERCHANT_ID = '1673234356'
RUN_TIMEOUT_SECONDS = 12 * 60  # Within Salla's 15m page cache and shared 30m lease.
RETRY_SECONDS = 15 * 60


def now_utc():
    return datetime.now(timezone.utc)


def slot(at):
    # 00:00 and 12:00 Asia/Riyadh; restart does not reset the schedule.
    return int((at.timestamp() + 3 * 3600) // (12 * 3600))


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


async def run_due(db, user_id, *, now=None, catalogue=False):
    at = now or now_utc()
    state_id = f'{user_id}:catalogue' if catalogue else user_id
    lease_user = state_id
    state = await db[STATE].find_one({'_id': state_id}) or {}
    if state.get('completed_slot') == slot(at):
        return False
    if aware(state.get('retry_at')) and aware(state['retry_at']) > at:
        return False
    token, _ = await _acquire_order_sync_lease(db, lease_user)
    if not token:
        return False
    try:
        # Recheck inside the shared lease: another replica may have completed.
        state = await db[STATE].find_one({'_id': state_id}) or {}
        if state.get('completed_slot') == slot(at):
            return False
        if aware(state.get('retry_at')) and aware(state['retry_at']) > at:
            return False
        pending = state.get('pending_window') or {}
        start = aware(pending.get('start')) or at - timedelta(hours=24)
        end = aware(pending.get('end')) or at
        window_slot = pending.get('slot', slot(at))
        log_id = await create_sync_log(db, user_id, 'products' if catalogue else 'orders')
        await db.salla_sync_logs.update_one({'id': log_id}, {'$set': {
            'trigger': 'scheduled_12h', 'window_start': start, 'window_end': end,
            'schedule_riyadh': ['00:00', '12:00'],
        }})
        await db[STATE].update_one({'_id': state_id}, {'$set': {
            'status': 'running', 'log_id': log_id, 'started_at': at,
            'pending_window': {'start': start, 'end': end, 'slot': window_slot},
        }}, upsert=True)
        try:
            if catalogue:
                from .scheduled_catalogue import refresh_catalogue
                from .sync import finish_sync_log
                result = await asyncio.wait_for(refresh_catalogue(
                    db, user_id, run_slot=window_slot), RUN_TIMEOUT_SECONDS)
                await finish_sync_log(db, log_id, 'partial' if result.get('errors_count') or result.get('truncated') else 'completed', extra=result)
            else:
                result = await asyncio.wait_for(run_orders_sync(
                    db, user_id, log_id=log_id, refresh_window=(start, end)), RUN_TIMEOUT_SECONDS)
            if result.get('errors_count') or result.get('truncated'):
                raise RuntimeError('incomplete_reconciliation')
            await db[STATE].update_one({'_id': state_id}, {
                '$set': {'status': 'completed', 'completed_slot': window_slot,
                         'last_success_at': now_utc(), 'last_result': result},
                '$unset': {'pending_window': '', 'retry_at': '', 'last_error_type': ''},
            })
        except BaseException as exc:
            if catalogue:
                from .sync import finish_sync_log
                await finish_sync_log(db, log_id, 'failed', extra={'last_error': type(exc).__name__})
            await db[STATE].update_one({'_id': state_id}, {'$set': {
                'status': 'interrupted' if isinstance(exc, asyncio.CancelledError) else 'failed',
                'retry_at': now_utc() + timedelta(seconds=RETRY_SECONDS),
                'last_error_type': type(exc).__name__,
            }})
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            log.warning('Scheduled Salla reconciliation incomplete type=%s', type(exc).__name__)
        return True
    finally:
        await _release_order_sync_lease(db, lease_user, token)


async def scheduler(db):
    await asyncio.sleep(90)  # Stagger startup away from operational workers.
    while True:
        try:
            # Explicit merchant scope; never sync a demo/other connected store.
            cursor = db.salla_integrations.find({
                'status': 'connected', 'store_id': {'$in': [MERCHANT_ID, int(MERCHANT_ID)]},
            }, {'user_id': 1})
            async for integration in cursor:
                if integration.get('user_id'):
                    await run_due(db, str(integration['user_id']))
                    await run_due(db, str(integration['user_id']), catalogue=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning('Scheduled Salla check unavailable type=%s', type(exc).__name__)
        await asyncio.sleep(60)
