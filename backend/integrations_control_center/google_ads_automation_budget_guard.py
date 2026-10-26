"""Transaction-only storage seam for future Google budget authorities.

NOT installed runtime authorization. Every policy writer and every automated
budget increase must first migrate to this common guard. A transaction probe
alone NEVER establishes that migration or authorizes dispatch. No route, model,
provider call, activation or environment flag is installed by this module.

The synchronous planner is trusted internal Python, never request/model input.
It may only decide from immutable server-loaded snapshots and already acquired
native evidence. It MUST NOT perform IO, call providers, start tasks or mutate
external state. No asynchronous callback or retrying with_transaction is used.
Every database operation explicitly receives the SAME Motor session. Existing
policy/authority collections remain untouched until a future caller integrates
this seam. Unknown commit ACKs grant nothing and require read-based recovery.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
import re
import time
from types import MappingProxyType
import uuid

from fastapi import HTTPException
from pymongo import ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from .google_ads_automation_policy import AUTOMATION_POLICIES, PUBLIC_FIELDS as STANDING_FIELDS
from .google_ads_automation_creation_policy import CREATION_POLICIES, PUBLIC_FIELDS as CREATION_FIELDS
from .google_ads_client import problem

BUDGET_GUARDS = 'mezan_google_ads_automation_budget_guards_v1'
PROTOCOL_VERSION = 1
MAX_ALLOCATIONS = 100
MAX_PAYLOAD_BYTES = 262144
_PROOF = object()
_ID = re.compile(r'^[A-Za-z0-9_.:-]{1,160}$')
_MONEY = re.compile(r'^(?:[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?$|^0\.[0-9]{0,5}[1-9][0-9]{0,5}$')
_COLLECTIONS = {'standing': AUTOMATION_POLICIES, 'creation': CREATION_POLICIES}
_FIELDS = {'standing': frozenset(STANDING_FIELDS), 'creation': frozenset(CREATION_FIELDS)}


@dataclass(frozen=True)
class PolicyMutation:
    policy_kind: str
    expected_revision: int
    values: Mapping
    event: Mapping | None = None


@dataclass(frozen=True)
class LaunchAllocation:
    allocation_id: str
    creation_identity: str
    campaign_id: str
    proposal_id: str
    execution_claim_id: str
    currency_code: str
    budget_native: str
    binding_digest: str


@dataclass(frozen=True)
class BudgetGuardSnapshot:
    user_id: str
    account_id: str
    guard: Mapping
    standing_policy: Mapping | None
    creation_policy: Mapping | None

    @property
    def standing_policy_present(self):
        return self.standing_policy is not None


@dataclass(frozen=True)
class _TransactionCapability:
    supported: bool
    database: object
    probed_monotonic: float
    proof: object


def budget_guard_runtime_available():
    """False until ALL policy writers/increase paths and runtime are integrated."""
    return False


def _fail(code):
    raise problem(code, 'تعذر إثبات معاملة حجز ميزانية جوجل؛ لا تعِد الإرسال تلقائيًا.', 409)


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def thaw_snapshot(value):
    """Return independent plain values for existing pure domain validators."""
    if isinstance(value, Mapping):
        return {key: thaw_snapshot(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw_snapshot(item) for item in value]
    return value


def _plain(value):
    try:
        encoded = json.dumps(thaw_snapshot(value), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if len(encoded.encode()) > MAX_PAYLOAD_BYTES:
            _fail('google_budget_guard_payload_limit')
        result = json.loads(encoded)
    except (ValueError, TypeError, OverflowError):
        _fail('google_budget_guard_payload_invalid')
    return result


def _digest(value):
    return hashlib.sha256(json.dumps(thaw_snapshot(value), ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _scope(user_id, account_id):
    if (not isinstance(user_id, str) or not user_id or len(user_id) > 128
        or not isinstance(account_id, str) or not re.fullmatch(r'[0-9]{1,20}', account_id)):
        _fail('google_budget_guard_scope_invalid')
    return {'user_id': user_id, 'account_id': account_id}


def _guard_id(scope):
    return 'google_budget_' + _digest(scope)


def _transaction(session):
    session.start_transaction(read_concern=ReadConcern('snapshot'), write_concern=WriteConcern('majority', j=True),
        read_preference=ReadPreference.PRIMARY, max_commit_time_ms=5000)


async def probe_google_budget_guard_transactions(db):
    """Explicit opt-in capability probe: actual write transaction, then ABORT.

    No persistent guard, policy or financial record is created. The caller must
    explicitly choose the database; no URI, credentials or environment is read.
    A successful token is bound to this database object for60seconds only.
    """
    async def probe():
        async with await db.client.start_session() as session:
            hello = await db.command('hello', session=session)
            if (not isinstance(hello, dict) or not (hello.get('setName') or hello.get('msg') == 'isdbgrid')
                or type(hello.get('logicalSessionTimeoutMinutes')) is not int):
                _fail('google_budget_guard_transactions_unavailable')
            _transaction(session)
            await db[BUDGET_GUARDS].insert_one({'_id': 'probe_' + uuid.uuid4().hex, 'probe_only': True}, session=session)
            await session.abort_transaction()
            return _TransactionCapability(True, db, time.monotonic(), _PROOF)
    try:
        return await asyncio.wait_for(probe(), timeout=10)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise problem('google_budget_guard_transactions_unavailable', 'معاملات تخزين الميزانية غير مثبتة؛ يبقى التنفيذ غير متاح.', 503) from None


def _capability(db, capability):
    if (not isinstance(capability, _TransactionCapability) or capability.proof is not _PROOF
        or capability.supported is not True or capability.database is not db
        or not 0 <= time.monotonic() - capability.probed_monotonic < 60):
        _fail('google_budget_guard_transactions_unproved')


def _new_guard(scope):
    return {'_id': _guard_id(scope), **scope, 'protocol_version': PROTOCOL_VERSION, 'epoch': 0,
        'allocations': {}, 'active_claim': None, 'last_commit': None}


def _validate_guard(row, scope):
    if (not isinstance(row, dict) or row.get('_id') != _guard_id(scope)
        or any(row.get(key) != value for key, value in scope.items())
        or type(row.get('protocol_version')) is not int or row['protocol_version'] != PROTOCOL_VERSION
        or type(row.get('epoch')) is not int or not 0 <= row['epoch'] < 2**63 - 2
        or not isinstance(row.get('allocations'), dict) or len(row['allocations']) > MAX_ALLOCATIONS
        or row.get('active_claim') is not None and not isinstance(row['active_claim'], dict)):
        _fail('google_budget_guard_state_unproved')
    for allocation_id, entry in row['allocations'].items():
        if (not isinstance(allocation_id, str) or not _ID.fullmatch(allocation_id) or not isinstance(entry, dict)
            or entry.get('allocation_id') != allocation_id or any(entry.get(key) != value for key, value in scope.items())
            or entry.get('state') not in {'reserved', 'native_enabled', 'cancelled_before_http'}):
            _fail('google_budget_guard_state_unproved')


def _command(command):
    if isinstance(command, PolicyMutation):
        if (command.policy_kind not in _COLLECTIONS or type(command.expected_revision) is not int
            or not 0 <= command.expected_revision < 2**63 - 2 or not isinstance(command.values, Mapping)):
            _fail('google_budget_guard_command_invalid')
        values = _plain(command.values)
        forbidden = {'user_id', 'account_id', 'revision', 'events'}
        if (not isinstance(values, dict) or not values or set(values) - (_FIELDS[command.policy_kind] - forbidden)
            or any(key.startswith(('_', '$')) or '.' in key for key in values)):
            _fail('google_budget_guard_policy_fields_invalid')
        event = _plain(command.event) if command.event is not None else None
        if event is not None and not isinstance(event, dict):
            _fail('google_budget_guard_command_invalid')
        return PolicyMutation(command.policy_kind, command.expected_revision, values, event)
    if isinstance(command, LaunchAllocation):
        values = vars(command)
        if (any(not isinstance(values[key], str) or not _ID.fullmatch(values[key]) for key in
                ('allocation_id', 'proposal_id', 'execution_claim_id'))
            or not isinstance(command.creation_identity, str) or not re.fullmatch(r'google_create_[a-f0-9]{64}', command.creation_identity)
            or not isinstance(command.campaign_id, str) or not re.fullmatch(r'[0-9]{1,30}', command.campaign_id)
            or not isinstance(command.currency_code, str) or not re.fullmatch(r'[A-Z]{3}', command.currency_code)
            or not isinstance(command.budget_native, str) or not _MONEY.fullmatch(command.budget_native)
            or not isinstance(command.binding_digest, str) or not re.fullmatch(r'[a-f0-9]{64}', command.binding_digest)):
            _fail('google_budget_guard_command_invalid')
        return command
    _fail('google_budget_guard_command_invalid')


async def _read_policy(db, scope, kind, session):
    # Bounded find proves both presence AND absence, including corrupt duplicate
    # rows before unique-index migration. Caller omission never means absence.
    rows = await db[_COLLECTIONS[kind]].find(scope, {'_id': 0}, session=session).to_list(length=2)
    if len(rows) > 1:
        _fail('google_budget_guard_policy_ambiguous')
    if rows and (any(rows[0].get(key) != value for key, value in scope.items())
        or type(rows[0].get('revision')) is not int or rows[0]['revision'] < 1):
        _fail('google_budget_guard_policy_unproved')
    return rows[0] if rows else None


async def _policy_write(db, scope, snapshot, command, session):
    old = snapshot.standing_policy if command.policy_kind == 'standing' else snapshot.creation_policy
    actual_revision = old.get('revision') if old is not None else 0
    if actual_revision != command.expected_revision:
        _fail('google_budget_guard_policy_changed')
    values = {**command.values, 'revision': command.expected_revision + 1}
    if old is None:
        row = {**scope, **values}
        if command.event is not None:
            row['events'] = [command.event]
        await db[_COLLECTIONS[command.policy_kind]].insert_one(row, session=session)
    else:
        update = {'$set': values}
        if command.event is not None:
            update['$push'] = {'events': {'$each': [command.event], '$slice': -50}}
        changed = await db[_COLLECTIONS[command.policy_kind]].update_one({**scope, 'revision': command.expected_revision}, update, session=session)
        if changed.matched_count != 1:
            _fail('google_budget_guard_policy_changed')
    return {'kind': 'policy_mutation', 'policy_kind': command.policy_kind, 'policy_revision': values['revision']}


def _allocation(snapshot, command, operation_id, stamp):
    old = thaw_snapshot(snapshot.guard)
    allocations = old['allocations']
    if old['active_claim'] is not None or len(allocations) >= MAX_ALLOCATIONS:
        _fail('google_budget_guard_allocation_pending')
    if (command.allocation_id in allocations or any(entry.get('creation_identity') == command.creation_identity
        or entry.get('campaign_id') == command.campaign_id or entry.get('operation_id') == operation_id for entry in allocations.values())):
        _fail('google_budget_guard_identity_consumed')
    creation = snapshot.creation_policy
    if creation is None:
        _fail('google_budget_guard_creation_policy_missing')
    standing = snapshot.standing_policy
    entry = {**vars(command), 'user_id': snapshot.user_id, 'account_id': snapshot.account_id,
        'operation_id': operation_id, 'state': 'reserved', 'created_at': stamp,
        'guard_epoch_before': old['epoch'], 'creation_policy_revision': creation['revision'],
        'creation_policy_digest': _digest(creation), 'standing_policy_present': standing is not None,
        'standing_policy_revision': standing['revision'] if standing is not None else None,
        'standing_policy_digest': _digest(standing) if standing is not None else None}
    claim = {key: entry[key] for key in ('allocation_id', 'operation_id', 'proposal_id', 'execution_claim_id')}
    return {**allocations, command.allocation_id: entry}, claim


async def _run(db, *, scope, operation_id, planner, capability, phase):
    _capability(db, capability)
    async with await db.client.start_session() as session:
        _transaction(session)
        row = await db[BUDGET_GUARDS].find_one({'_id': _guard_id(scope)}, session=session)
        if row is None:
            row = _new_guard(scope)
            await db[BUDGET_GUARDS].insert_one(row, session=session)
        _validate_guard(row, scope)
        if (row.get('last_commit') or {}).get('operation_id') == operation_id:
            _fail('google_budget_guard_operation_consumed')
        standing = await _read_policy(db, scope, 'standing', session)
        creation = await _read_policy(db, scope, 'creation', session)
        snapshot = BudgetGuardSnapshot(**scope, guard=_freeze(row), standing_policy=_freeze(standing), creation_policy=_freeze(creation))
        command = planner(snapshot)
        if inspect.isawaitable(command):
            if inspect.iscoroutine(command):
                command.close()
            _fail('google_budget_guard_async_planner_forbidden')
        command = _command(command)
        stamp = datetime.now(timezone.utc).isoformat()
        update = {'epoch': row['epoch'] + 1, 'last_commit': {'operation_id': operation_id, 'committed_at': stamp}}
        if isinstance(command, PolicyMutation):
            result = await _policy_write(db, scope, snapshot, command, session)
        else:
            allocations, claim = _allocation(snapshot, command, operation_id, stamp)
            update.update(allocations=allocations, active_claim=claim)
            result = {'kind': 'launch_allocation', 'allocation_id': command.allocation_id}
        changed = await db[BUDGET_GUARDS].update_one({'_id': row['_id'], **scope, 'epoch': row['epoch']}, {'$set': update}, session=session)
        if changed.matched_count != 1:
            _fail('google_budget_guard_epoch_changed')
        phase['commit_attempted'] = True
        await session.commit_transaction()  # Exactly ONE attempt. Never retry callback or commit here.
        return {**result, 'committed': True, 'guard_epoch': row['epoch'] + 1, 'operation_id': operation_id,
            'provider_dispatch_authorized': False, 'provider_writes': 0, 'runtime_available': False}


async def run_google_budget_guard_transaction(db, *, user_id, account_id, operation_id, capability=None, planner,
        timeout_seconds=15):
    """One server-internal synchronous decision and one explicit commit attempt.

    Storage commit is NOT dispatch authority. Only future fully integrated
    owner/native/ownership/cap/guard proof may turn it into a local permission.
    No failure permits automatic resend, refund or native fence release.
    """
    phase = {'commit_attempted': False}
    try:
        scope = _scope(user_id, account_id)
        if not isinstance(operation_id, str) or not _ID.fullmatch(operation_id) or not callable(planner):
            _fail('google_budget_guard_command_invalid')
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 15:
            _fail('google_budget_guard_timeout_invalid')
        return await asyncio.wait_for(_run(db, scope=scope, operation_id=operation_id, planner=planner,
            capability=capability, phase=phase), timeout=timeout_seconds)
    except asyncio.CancelledError as exc:
        exc.commit_attempted = phase['commit_attempted']
        exc.preserve_execution_fence = True
        exc.automatic_retry_allowed = False
        raise
    except Exception as exc:
        if not isinstance(exc, HTTPException):
            exc = problem('google_budget_guard_commit_unconfirmed' if phase['commit_attempted'] else 'google_budget_guard_transaction_unconfirmed',
                'لم يتأكد حجز الميزانية؛ تحقق بالقراءة واحتفظ بالقفل دون إعادة إرسال.', 503)
        if isinstance(exc.detail, dict):
            exc.detail.update(commit_attempted=phase['commit_attempted'], preserve_execution_fence=True, automatic_retry_allowed=False)
        raise exc from None
