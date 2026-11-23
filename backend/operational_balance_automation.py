"""Explicit owner opt-in for serial, isolated operational reconciliation.

Only the authenticated owner may persist the capability. The independent child
rechecks its generation, current owner and accounting gate before every source
write and aggregate CAS through the existing authorization guard. No setting
or successful attempt claims completeness of the financial/source evidence.
"""
import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import logging
import math
from pathlib import Path
from uuid import uuid4

import pymongo
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from operational_balance_freshness import (
    begin_attempt, finish_attempt, iso, source_warnings, timestamp,
)
from operational_balance_store import STATES, digest, fail
from operational_demand_refresh import (
    LEASE_SECONDS, TIMING_LIMIT, TIMING_MAX_MS, TIMING_OUTCOMES, TIMING_STAGES,
    PRIMARY_ORDERS_READ_LIMIT, _safe_primary_orders_read,
    demand_context, refresh_stage, run_scheduled_refresh,
)


SETTINGS = 'operational_balance_automation_v1'
SERIAL_LEASES = 'operational_balance_automation_leases_v1'
INTERVAL_SECONDS = 7200
POLL_SECONDS = 60
METADATA_SECONDS = 3
_LOG = logging.getLogger(__name__)
_HEADER = {'_id': 0, 'status': 1, 'revision': 1, 'engine.as_of': 1}
_STATUS_FIELDS = {'_id': 0, 'owner_id': 1, 'actor_id': 1, 'schema_version': 1,
    'enabled': 1, 'generation': 1, 'session_scope': 1, 'next_due_at': 1,
    'last_attempt_at': 1, 'last_success_at': 1, 'last_error_code': 1,
    'consecutive_failures': 1, 'last_outcome': 1, 'last_trace': 1}
_CODES = frozenset({'operational_refresh_failed', 'operational_refresh_timeout',
    'operational_refresh_in_progress', 'operational_refresh_lease_lost',
    'operational_automation_disabled', 'operational_automation_lease_lost',
    'operational_automation_owner_required', 'operational_owner_inactive',
    'operational_not_active', 'operational_accounting_active',
    'operational_automation_configuration_invalid',
    'operational_source_setup_incomplete', 'operational_capacity_requires_archive',
    'operational_concurrent_change', 'operational_actor_scope_changed',
    'operational_refresh_cancelled'})


def _now():
    return datetime.now(timezone.utc)


def _enabled(row, owner):
    return (type(row) is dict and type(row.get('schema_version')) is int and row['schema_version'] == 1
        and row.get('owner_id') == owner and row.get('actor_id') == owner
        and row.get('enabled') is True and row.get('session_scope') == digest([owner, owner])
        and type(row.get('generation')) is str and len(row['generation']) == 32
        and all(char in '0123456789abcdef' for char in row['generation']))


def _failures(row):
    value = row.get('consecutive_failures', 0)
    return min(value, 20) if type(value) is int and value >= 0 else 0


def _error_code(error):
    detail = getattr(error, 'detail', None)
    code = detail.get('code') if type(detail) is dict else None
    if isinstance(error, asyncio.CancelledError):
        return 'operational_refresh_cancelled'
    return code if type(code) is str and code in _CODES else 'operational_refresh_failed'


def safe_trace(value):
    """Owner diagnostics use the same closed vocabulary as manual refresh."""
    result = {'stage_timings': [], 'primary_orders_reads': []}
    if type(value) is not dict:
        return result
    stages = value.get('stage_timings')
    for row in stages[-TIMING_LIMIT:] if type(stages) is list else []:
        if type(row) is not dict:
            continue
        stage, outcome, duration = (row.get(key) for key in ('stage', 'outcome', 'duration_ms'))
        if (type(stage) is str and stage in TIMING_STAGES
                and type(outcome) is str and outcome in TIMING_OUTCOMES
                and type(duration) in (int, float) and 0 <= duration <= TIMING_MAX_MS
                and math.isfinite(duration)):
            result['stage_timings'].append({'stage': stage, 'outcome': outcome, 'duration_ms': round(duration, 3)})
    primary = value.get('primary_orders_reads')
    for row in primary[-PRIMARY_ORDERS_READ_LIMIT:] if type(primary) is list else []:
        try:
            selected = _safe_primary_orders_read(row)
        except Exception:
            selected = None
        if selected is not None:
            result['primary_orders_reads'].append(selected)
    execution = value.get('execution')
    if type(execution) is dict and type(execution.get('cpu_priority_lowered')) is bool:
        result['execution'] = {'cpu_priority_lowered': execution['cpu_priority_lowered']}
    return result


def status_value(row, owner):
    row = row if type(row) is dict else {}
    code = row.get('last_error_code')
    outcome = row.get('last_outcome')
    return {'schema_version': 1, 'enabled': _enabled(row, owner),
        'interval_seconds': INTERVAL_SECONDS, 'session_scope': digest([owner, owner]),
        'next_due_at': iso(row.get('next_due_at')) if _enabled(row, owner) else None,
        'last_attempt_at': iso(row.get('last_attempt_at')),
        'last_success_at': iso(row.get('last_success_at')),
        'last_error_code': code if type(code) is str and code in _CODES else None,
        'consecutive_failures': _failures(row),
        'last_outcome': outcome if outcome in ('running', 'success', 'failed', 'unknown', 'deferred') else None,
        'last_trace': safe_trace(row.get('last_trace')),
        'financial_coverage_verified': False}


async def read_status(db, owner):
    with pymongo.timeout(METADATA_SECONDS):
        async with asyncio.timeout(METADATA_SECONDS):
            row = await db[SETTINGS].find_one({'_id': owner, 'owner_id': owner}, _STATUS_FIELDS)
    return status_value(row, owner)


async def set_enabled(db, owner, enabled, verify):
    """Metadata only; an explicit route supplies current owner authorization."""
    if type(enabled) is not bool:
        fail('operational_automation_input_invalid', 'إعداد غير صالح', 422)
    await verify()
    if enabled:
        from operational_balance_service import active_gate
        header = await db[STATES].find_one({'_id': owner, 'owner_id': owner}, _HEADER) or {'status': 'draft'}
        await active_gate(db, owner, header)
    await verify()
    stamp = _now()
    values = {'schema_version': 1, 'owner_id': owner, 'actor_id': owner,
        'enabled': enabled, 'generation': uuid4().hex, 'session_scope': digest([owner, owner]),
        'updated_at': stamp, 'next_due_at': stamp if enabled else None,
        'consecutive_failures': 0, 'last_error_code': None}
    with pymongo.timeout(METADATA_SECONDS):
        async with asyncio.timeout(METADATA_SECONDS):
            await db[SETTINGS].update_one({'_id': owner, 'owner_id': owner}, {'$set': values}, upsert=True)
    return await read_status(db, owner)


async def _current_owner(db, owner):
    account = await db['users'].find_one({'id': owner},
        {'_id': 0, 'id': 1, 'role': 1, 'disabled': 1, 'is_active': 1, 'deleted_at': 1})
    if (not account or account.get('id') != owner or account.get('role') != 'owner'
            or account.get('disabled') or account.get('is_active') is False or account.get('deleted_at')):
        fail('operational_owner_inactive', 'حساب المالك غير متاح للاحتساب التلقائي', 403)


def _successful_receipt(result, error, stamp):
    as_of = timestamp(result.get('as_of')) if type(result) is dict else None
    return (error is None and type(result) is dict and result.get('status') == 'refreshed'
        and as_of is not None and as_of <= stamp
        and type(result.get('revision')) is int and result['revision'] > 0)


async def _finish_setting(db, owner, generation, run_token, previous, *, result=None, error=None, trace=None):
    stamp = _now()
    success = _successful_receipt(result, error, stamp)
    failures = 0 if success else min(_failures(previous) + 1, 20)
    code = None if success else _error_code(error)
    delay = INTERVAL_SECONDS if success else min(INTERVAL_SECONDS, 300 * 2 ** min(failures - 1, 5))
    if code == 'operational_refresh_in_progress':
        delay = POLL_SECONDS
    values = {'last_outcome': 'success' if success else 'unknown' if isinstance(error, asyncio.CancelledError) else 'failed',
        'last_error_code': code, 'consecutive_failures': failures,
        'last_finished_at': stamp, 'next_due_at': stamp + timedelta(seconds=delay),
        'last_trace': trace or {}}
    if success:
        values['last_success_at'] = result['as_of']
        values['last_success_revision'] = result['revision']
    # Diagnostics never overwrite a newer enable/disable generation or attempt.
    with pymongo.timeout(METADATA_SECONDS):
        async with asyncio.timeout(METADATA_SECONDS):
            await db[SETTINGS].update_one({'_id': owner, 'owner_id': owner, 'generation': generation,
                'run_token': run_token}, {'$set': values})


async def run_owner(db, row, serial_verify, *, stop_event=None, cpu_priority_lowered=False):
    """One immutable opt-in generation; all heavy work lives in the child."""
    owner = row.get('owner_id')
    if type(owner) is not str or not owner or row.get('_id') != owner or not _enabled(row, owner):
        return {'outcome': 'ineligible'}
    generation, run_token = row['generation'], uuid4().hex
    attempt, context, result, warnings, error = None, None, None, [], None

    async def verify():
        with pymongo.timeout(METADATA_SECONDS):
            async with asyncio.timeout(METADATA_SECONDS):
                await serial_verify()
                current = await db[SETTINGS].find_one({'_id': owner, 'owner_id': owner}, _STATUS_FIELDS)
                if not _enabled(current, owner) or current['generation'] != generation:
                    fail('operational_automation_disabled', 'أوقف المالك الاحتساب التلقائي أو غيّر إعداداته', 403)
                await _current_owner(db, owner)
                from operational_balance_service import active_gate
                header = await db[STATES].find_one({'_id': owner, 'owner_id': owner}, _HEADER) or {'status': 'draft'}
                await active_gate(db, owner, header)

    async def operation():
        nonlocal attempt, context, warnings
        from operational_balance_service import refresh
        from operational_visible_evidence import acquire_visible_evidence
        context = demand_context()
        # The canonical owner lease has admitted this job. A crash can retry
        # only after that short lease and a fresh persisted-generation check.
        stamp = _now()
        changed = await db[SETTINGS].update_one({'_id': owner, 'owner_id': owner,
            'enabled': True, 'generation': generation}, {'$set': {
                'run_token': run_token, 'last_attempt_at': stamp, 'last_outcome': 'running',
                'next_due_at': stamp + timedelta(seconds=LEASE_SECONDS)}})
        if changed.matched_count != 1:
            fail('operational_automation_disabled', 'أوقف المالك الاحتساب التلقائي', 403)
        attempt = await begin_attempt(db, owner)
        with refresh_stage('native_evidence'):
            native = await acquire_visible_evidence(db, owner)
        with refresh_stage('aggregate'):
            state = await refresh(db, owner)
        warnings = source_warnings(state, native)
        return {'status': 'refreshed', 'as_of': state.get('engine', {}).get('as_of'),
            'revision': state.get('revision')}

    try:
        result = await run_scheduled_refresh(db, owner, owner, verify, operation, stop_event=stop_event)
        if not _successful_receipt(result, None, _now()):
            fail('operational_refresh_failed', 'لم يثبت حفظ نتيجة احتساب صحيحة', 503)
        return {'outcome': 'success', **result}
    except BaseException as caught:
        error = caught
        if not isinstance(caught, Exception):
            raise
        # No provider error, owner identifier or row enters logs or metadata.
        return {'outcome': 'failed', 'error_code': _error_code(caught)}
    finally:
        await finish_attempt(db, owner, attempt, result=result, error=error, warnings=warnings)
        trace = safe_trace({'stage_timings': list(context.stage_timings), **context.primary_read_details()}
                           if context is not None else {})
        trace['execution'] = {'cpu_priority_lowered': cpu_priority_lowered is True}
        with suppress(Exception):
            # Pre-lease failures have no run token; fence by unchanged due row
            # to retain a reason/backoff without pretending a finance attempt.
            eligible_finish = True
            if context is None:
                with pymongo.timeout(METADATA_SECONDS):
                    async with asyncio.timeout(METADATA_SECONDS):
                        changed = await db[SETTINGS].update_one({'_id': owner, 'owner_id': owner,
                            'enabled': True, 'generation': generation, 'next_due_at': row.get('next_due_at')},
                            {'$set': {'run_token': run_token}})
                eligible_finish = changed.matched_count == 1
            if eligible_finish:
                await _finish_setting(db, owner, generation, run_token, row, result=result, error=error, trace=trace)


async def tick(db, *, stop_event=None, cpu_priority_lowered=False):
    """Read at most one due opt-in row; serialize all owners across replicas."""
    token, stamp = uuid4().hex, _now()
    try:
        with pymongo.timeout(METADATA_SECONDS):
            async with asyncio.timeout(METADATA_SECONDS):
                lease = await db[SERIAL_LEASES].find_one_and_update(
                    {'_id': 'serial', '$or': [{'expires_at': {'$lte': stamp}}, {'token': token}]},
                    {'$set': {'token': token, 'expires_at': stamp + timedelta(seconds=LEASE_SECONDS)}},
                    upsert=True, return_document=ReturnDocument.AFTER)
        if not lease or lease.get('token') != token:
            return {'outcome': 'busy'}
    except DuplicateKeyError:
        return {'outcome': 'busy'}

    async def serial_verify():
        moment = _now()
        with pymongo.timeout(METADATA_SECONDS):
            async with asyncio.timeout(METADATA_SECONDS):
                changed = await db[SERIAL_LEASES].update_one(
                    {'_id': 'serial', 'token': token, 'expires_at': {'$gt': moment}},
                    {'$set': {'expires_at': moment + timedelta(seconds=LEASE_SECONDS)}})
        if changed.matched_count != 1:
            fail('operational_automation_lease_lost', 'انتهى حجز عامل الاحتساب التلقائي', 409)

    try:
        with pymongo.timeout(METADATA_SECONDS):
            async with asyncio.timeout(METADATA_SECONDS):
                row = await db[SETTINGS].find_one({'_id': {'$type': 'string'},
                    'enabled': True, 'next_due_at': {'$lte': stamp}},
                    {**_STATUS_FIELDS, '_id': 1}, sort=[('next_due_at', 1), ('_id', 1)], max_time_ms=2000)
        if row is None:
            return {'outcome': 'idle'}
        owner = row.get('owner_id')
        if type(owner) is not str or not owner or row.get('_id') != owner or not _enabled(row, owner):
            # A corrupt old configuration must not starve all later owners.
            # Compare every authority field as data, so a concurrent explicit
            # repair wins. This only disables the malformed metadata record.
            exact = {'_id': row['_id']}
            for key in ('schema_version', 'owner_id', 'actor_id', 'enabled', 'generation',
                        'session_scope', 'next_due_at'):
                exact[key] = {'$eq': row[key]} if key in row else {'$exists': False}
            with pymongo.timeout(METADATA_SECONDS):
                async with asyncio.timeout(METADATA_SECONDS):
                    await db[SETTINGS].update_one(exact, {'$set': {
                        'enabled': False, 'next_due_at': None, 'last_outcome': 'failed',
                        'last_error_code': 'operational_automation_configuration_invalid'}})
            return {'outcome': 'ineligible'}
        return await run_owner(db, row, serial_verify, stop_event=stop_event,
                               cpu_priority_lowered=cpu_priority_lowered)
    finally:
        with suppress(Exception):
            with pymongo.timeout(METADATA_SECONDS):
                async with asyncio.timeout(METADATA_SECONDS):
                    await db[SERIAL_LEASES].delete_one({'_id': 'serial', 'token': token})


async def run(db, *, stop_event, cpu_priority_lowered=False):
    # Metadata index only. Reads/routes never initialize indexes or automation.
    await db[SETTINGS].create_index([('enabled', 1), ('next_due_at', 1), ('_id', 1)],
                                    name='enabled_due_owner_v1')
    while not stop_event.is_set():
        try:
            await tick(db, stop_event=stop_event, cpu_priority_lowered=cpu_priority_lowered)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.warning('Operational automation metadata poll unavailable')
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            pass


async def supervise(db):
    """Reuse the existing pipe-owned child supervision, never run work here."""
    from background_worker_process import ManagedWorker
    worker = ManagedWorker(worker_path=Path(__file__).with_name('operational_balance_automation_runner.py'))
    try:
        await worker.start()
    finally:
        await worker.stop()
