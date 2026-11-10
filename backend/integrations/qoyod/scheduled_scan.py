"""Five full read refreshes/day, separate from guarded order delivery.

One Mongo lease covers scheduled and manual requests across replicas. Failed
or interrupted generations never replace the last successful display result.
No provider financial write is made by this module.
"""
import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import logging
import time
import uuid

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

log = logging.getLogger(__name__)
SLOT_SECONDS = 86400 // 5
LEASE_SECONDS = 120
MAX_SCAN_SECONDS = 1200
REPORT_ROW_LIMIT = 1000
_TASKS = set()


class ScanFailure(RuntimeError):
    """Machine-only failure details; never retain provider text or payloads."""

    def __init__(self, stage, diagnostic=None):
        super().__init__('scan_failed')
        diagnostic = diagnostic or {}
        self.diagnostic = {'stage': stage}
        error_type = diagnostic.get('error_type')
        if isinstance(error_type, str) and error_type.isidentifier() and len(error_type) <= 80:
            self.diagnostic['error_type'] = error_type
        status = diagnostic.get('http_status')
        if type(status) is int and 0 <= status <= 599:
            self.diagnostic['http_status'] = status


def utcnow():
    return datetime.now(timezone.utc)


def slot_key(at):
    # Riyadh 00:00, 04:48, 09:36, 14:24, 19:12; missed slots coalesce.
    return int((at.timestamp() + 3 * 3600) // SLOT_SECONDS)


async def read_scan(db, *, tenant, owner):
    state = await db.qoyod_full_scans.find_one({'_id': str(tenant)}, {'_id': 0}) or {}
    return _public_state(state, owner)


def _public_state(state, owner):
    result = state.get('last_success') or {}
    if result.get('orders_user_id') != str(owner):
        result = {}
    at = utcnow()
    lease = state.get('lease_until')
    if isinstance(lease, datetime) and lease.tzinfo is None:
        lease = lease.replace(tzinfo=timezone.utc)
    running = bool(lease and lease > at and state.get('status') == 'running')
    stale = bool(result) and (
        state.get('status') == 'failed' or not result.get('slot') == slot_key(at)
        or (state.get('status') == 'running' and not running)
    )
    return {'ok': True, 'scan': {'running': running, 'stale': stale,
            'last_error': state.get('last_error'),
            'updated_at': result.get('updated_at'),
            'schedule_riyadh': ['00:00', '04:48', '09:36', '14:24', '19:12']},
            'report': result.get('report'), 'invoice_presence': result.get('invoice_presence')}


async def _claim(db, tenant, owner, manual):
    at = utcnow()
    try:
        await db.qoyod_full_scans.insert_one({'_id': str(tenant)})
    except DuplicateKeyError:
        pass
    expired = {'$or': [{'lease_until': {'$lte': at}}, {'lease_until': {'$exists': False}}]}
    # A process may die after claiming its slot. Expired in-progress work may
    # resume in that same slot; completed/failed attempts keep normal throttles.
    abandoned = {'status': 'running', 'lease_until': {'$lte': at}}
    if manual:
        eligible = {'$or': [abandoned, {'last_attempt_at': {'$not': {'$gt': at - timedelta(seconds=60)}}}]}
    else:
        eligible = {'$or': [abandoned, {'last_slot': {'$ne': slot_key(at)}}]}
    query = {'_id': str(tenant), '$and': [expired, eligible]}
    token = uuid.uuid4().hex
    claimed = await db.qoyod_full_scans.find_one_and_update(
        query, {'$set': {'token': token, 'orders_user_id': str(owner),
                        'status': 'running', 'last_attempt_at': at,
                        'last_slot': slot_key(at), 'last_error': None,
                        'lease_until': at + timedelta(seconds=LEASE_SECONDS)}},
        return_document=ReturnDocument.AFTER,
    )
    if claimed:
        await db.qoyod_settings.update_one({'user_id': str(tenant)}, {'$set': {
            'scheduled_scan_token': token, 'scheduled_scan_error': None,
            'scheduled_scan_running': True,
            'scheduled_scan_running_until': (at + timedelta(seconds=LEASE_SECONDS)).isoformat()}}, upsert=True)
    return claimed


async def execute_scan(db, *, tenant, owner):
    """Bounded existing readers; complete evidence required before publishing."""
    from integrations.qoyod.credentials import get_api_key
    from integrations.qoyod.routes import _build_qoyod_client_for, _public_reconciliation, _public_sync_summary
    from integrations.qoyod.qoyod_invoices_sync import sync_qoyod_invoices
    from integrations.qoyod.reconciliation_v2 import run_reconciliation_v2
    from resource_governor import governor, ResourcePressure
    decision, _ = governor.peek()
    if decision in {'blocked', 'cancel'}:
        raise ResourcePressure('resource_pressure')
    key = await get_api_key(db, tenant)
    if not key:
        raise RuntimeError('credentials_unavailable')
    client = await _build_qoyod_client_for(db, tenant, key)
    sync = await sync_qoyod_invoices(db, user_id=tenant, api_client=client,
                                    max_pages=200, page_size=50)
    settings = await db.qoyod_settings.find_one({'user_id': tenant}) or {}
    if not sync.get('ok') or not (settings.get('invoice_presence_snapshot') or {}).get('complete'):
        raise ScanFailure('provider_refresh', sync.get('diagnostic'))
    raw = await run_reconciliation_v2(db, orders_user_id=owner, markers_user_id=tenant,
                                     from_date='2026-07-01', full_scan=True, publish_summary=False)
    if not raw.get('ok'):
        raise ScanFailure('comparison')
    from integrations.qoyod import unsent_orders
    from qoyod_auto_unified.queue_api import _execute_list
    view = await _execute_list(unsent_orders.snapshot_builder, db,
        user_id=tenant, orders_user_id=owner, from_date='2026-07-01',
        limit=5000, audit=raw['_candidate_audit'], cache_summary=False)
    report = _public_reconciliation(raw)
    report['scan_metrics'] = {'returned_audit_rows': raw['_candidate_audit']['scanned_rows'],
                              'mongo_batch_size': 100, 'provider_page_size': 50}
    total_rows = len(report['rows'])
    report['rows'] = report['rows'][:REPORT_ROW_LIMIT]
    report['returned_row_count'] = len(report['rows'])
    report['matched_row_count'] = total_rows
    report['truncated'] = total_rows > REPORT_ROW_LIMIT
    # Avoid retaining large reference lists twice. Counts cover the full audit.
    report.pop('reference_sets', None)
    report['sync_summary'] = {**_public_sync_summary(sync), 'ran': True}
    latest = await db.qoyod_settings.find_one({'user_id': tenant}, {'invoice_presence_snapshot': 1}) or {}
    generation = (settings.get('invoice_presence_snapshot') or {}).get('started_at')
    if generation != (latest.get('invoice_presence_snapshot') or {}).get('started_at'):
        raise ScanFailure('provider_snapshot_superseded')
    presence = {k: view['invoice_presence'].get(k) for k in
                ('count', 'verification_required', 'verified_at', 'expires_at', 'complete')}
    presence.update(orders_user_id=str(owner), snapshot_started_at=generation)
    if presence.get('complete') is not True or type(presence.get('count')) is not int:
        raise ScanFailure('summary_verification')
    return report, presence, view


async def _run(db, tenant, owner, claim, executor):
    token = claim['token']
    selector = {'_id': str(tenant), 'token': token}
    parent = asyncio.current_task()
    started, cpu = time.monotonic(), time.process_time()

    async def heartbeat():
        while True:
            await asyncio.sleep(20)
            try:
                updated = await db.qoyod_full_scans.update_one(
                    {**selector, 'lease_until': {'$gt': utcnow()}},
                    {'$set': {'lease_until': utcnow() + timedelta(seconds=LEASE_SECONDS)}},
                )
                if updated.matched_count != 1:
                    parent.cancel()
                    return
                await db.qoyod_settings.update_one(
                    {'user_id': str(tenant), 'scheduled_scan_token': token},
                    {'$set': {'scheduled_scan_running_until': (utcnow() + timedelta(seconds=LEASE_SECONDS)).isoformat()}},
                )
            except Exception:
                parent.cancel()
                return

    beat = asyncio.create_task(heartbeat())
    committed = False
    try:
        output = await asyncio.wait_for(executor(db, tenant=tenant, owner=owner), MAX_SCAN_SECONDS)
        report, presence = output[:2]
        view = output[2] if len(output) > 2 else None
        view_meta = None
        if view is not None:
            view_meta = {k: v for k, v in view.items() if k != 'orders'}
            for offset in range(0, len(view['orders']), 100):
                await db.qoyod_scan_views.insert_one({
                    '_id': f'{tenant}:{token}:{offset}', 'tenant': str(tenant),
                    'generation': token, 'created_at': claim['last_attempt_at'],
                    'offset': offset, 'rows': view['orders'][offset:offset + 100],
                })
                await asyncio.sleep(0)
        finished = utcnow()
        # Fenced publication: an expired/replaced lease cannot publish a result.
        published = await db.qoyod_full_scans.update_one(
            {**selector, 'lease_until': {'$gt': finished}},
            {'$set': {'status': 'succeeded', 'last_error': None,
                      'last_slot': slot_key(finished),
                      'last_success': {'orders_user_id': str(owner), 'slot': slot_key(finished),
                                       'generation': token, 'unsent_meta': view_meta,
                                       'updated_at': finished.isoformat(),
                                       'report': report, 'invoice_presence': presence}}},
        )
        committed = bool(published.matched_count)
        if committed:
            await db.qoyod_settings.update_one(
                {'user_id': str(tenant), 'scheduled_scan_token': token},
                {'$set': {'last_successful_scan_summary': {
                    **presence, 'orders_user_id': str(owner), 'updated_at': finished.isoformat()},
                    'scheduled_scan_error': None}},
            )
            previous = (claim.get('last_success') or {}).get('generation')
            await db.qoyod_scan_views.delete_many({
                'tenant': str(tenant), 'generation': {'$nin': [token, previous]},
                'created_at': {'$lt': claim['last_attempt_at']},
            })
    except BaseException as exc:
        failure = {'stage': 'scan', 'error_type': type(exc).__name__}
        if isinstance(exc, ScanFailure):
            failure.update(exc.diagnostic)
        if not committed:
            await db.qoyod_full_scans.update_one(selector, {'$set': {
                'status': 'failed', 'last_failure': failure,
                'last_error': 'scan_interrupted' if isinstance(exc, asyncio.CancelledError) else 'scan_failed'}})
            await db.qoyod_settings.update_one(
                {'user_id': str(tenant), 'scheduled_scan_token': token},
                {'$set': {'scheduled_scan_error': 'scan_failed'}},
            )
        else:
            # The generation pointer is the commit point. A display-cache or
            # cleanup failure cannot revoke an already published valid result.
            with suppress(Exception):
                await db.qoyod_settings.update_one(
                    {'user_id': str(tenant), 'scheduled_scan_token': token},
                    {'$set': {'scheduled_scan_error': 'display_cache_update_failed'}})
        if isinstance(exc, asyncio.CancelledError):
            raise
        log.warning('Qoyod full scan failed type=%s committed=%s', type(exc).__name__, committed)
    finally:
        beat.cancel()
        with suppress(asyncio.CancelledError):
            await beat
        await db.qoyod_full_scans.update_one(selector, {'$set': {'lease_until': utcnow(),
            'last_duration_ms': round((time.monotonic() - started) * 1000, 2)}})
        await db.qoyod_settings.update_one({'user_id': str(tenant), 'scheduled_scan_token': token},
            {'$set': {'scheduled_scan_running': False}})
        # Process CPU includes concurrent requests; never label it scan-only CPU.
        log.info('qoyod_scan_metrics duration_ms=%.2f process_cpu_delta_ms=%.2f',
                 (time.monotonic() - started) * 1000, (time.process_time() - cpu) * 1000)


async def request_scan(db, *, tenant, owner, manual=False, executor=None):
    claim = await _claim(db, tenant, owner, manual)
    if claim:
        task = asyncio.create_task(_run(db, tenant, owner, claim, executor or execute_scan), name='qoyod-full-scan')
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
    return {**await read_scan(db, tenant=tenant, owner=owner), 'accepted': bool(claim)}


async def scheduler(db):
    while True:
        try:
            settings = await db.qoyod_settings.find_one({'user_id': 'main'},
                {'enabled': 1, 'plan_b_auto_send_orders_user_id': 1, 'security_owner_id': 1}) or {}
            owner = settings.get('plan_b_auto_send_orders_user_id')
            if settings.get('enabled') and owner and settings.get('security_owner_id'):
                await request_scan(db, tenant='main', owner=owner)
        except Exception:
            log.warning('Qoyod scheduled scan check unavailable')
        await asyncio.sleep(30)


async def stop_scans():
    tasks = list(_TASKS)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def read_unsent_view(db, *, tenant, owner, days=30, limit=1000,
                           from_date=None, to_date=None, status=None,
                           salla_status=None, search=None):
    """Read only a published display generation, never build a fresh audit."""
    from integrations.qoyod.candidate_orders import resolve_candidate_date_range
    state = await db.qoyod_full_scans.find_one({'_id': str(tenant)}, {'last_success.report': 0}) or {}
    saved = state.get('last_success') or {}
    result = saved.get('unsent_meta')
    if saved.get('orders_user_id') != str(owner) or not result:
        return {'ok': True, 'orders': [], 'counts': {}, 'verification_required': True,
                'snapshot_available': False, 'total': None}
    result = dict(result)
    date_range = resolve_candidate_date_range(from_date=from_date, to_date=to_date, days=days)
    cursor = db.qoyod_scan_views.find({'tenant': str(tenant), 'generation': saved['generation']},
                                    {'_id': 0, 'rows': 1}).sort('offset', 1).limit(50).batch_size(1)
    rows = []
    async for chunk in cursor:
        for row in chunk['rows']:
            day = str(row.get('order_date') or '')[:10]
            if not date_range.from_date.isoformat() <= day <= date_range.to_date.isoformat():
                continue
            if salla_status and salla_status not in (row.get('salla_status'), row.get('salla_status_slug')):
                continue
            if search and search.casefold() not in str(row.get('order_number') or '').casefold() and search.casefold() not in str(row.get('customer_name') or '').casefold():
                continue
            rows.append(row)
        await asyncio.sleep(0)
    counts = {key: 0 for key in ('أُرسل', 'لم يُرسل', 'فشل', 'مكرر')}
    for row in rows:
        counts[row['status']] += 1
    presence = result.get('invoice_presence') or {}
    verified_refs = set(presence.get('order_numbers') or [])
    selected = [r for r in rows if not status or
                (r['order_number'] in verified_refs if status == 'verified_absent' else r['status'] == status)]
    complete_rows = not result.get('truncated')
    result.update(orders=selected[:max(1, min(int(limit), 5000))], counts=counts,
                  total=sum(counts.values()), matched_order_count=len(selected),
                  returned_order_count=min(len(selected), max(1, min(int(limit), 5000))),
                  counts_complete=complete_rows, counts_scope='last_successful_snapshot',
                  truncated=not complete_rows or len(selected) > limit,
                  snapshot_available=True, updated_at=saved['updated_at'])
    result['scan'] = _public_state(state, owner)['scan']
    try:
        result['scan']['stale'] = result['scan']['stale'] or datetime.fromisoformat(presence['expires_at']) <= utcnow()
    except (KeyError, TypeError, ValueError):
        result['scan']['stale'] = True
    return result
