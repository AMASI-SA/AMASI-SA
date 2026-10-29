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
from decimal import Decimal
import hashlib
import inspect
import json
import re
import time
from types import MappingProxyType
import uuid

from fastapi import HTTPException
from pymongo import ReadPreference, timeout
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern
from pymongo.errors import DuplicateKeyError, OperationFailure

from .google_ads_automation_policy import AUTOMATION_POLICIES, PUBLIC_FIELDS as STANDING_FIELDS
from .google_ads_automation_creation_policy import CREATION_POLICIES, PUBLIC_FIELDS as CREATION_FIELDS
from .google_ads_client import problem

BUDGET_GUARDS = 'mezan_google_ads_automation_budget_guards_v1'
PROTOCOL_VERSION = 1
MAX_ALLOCATIONS = 100
MAX_PAYLOAD_BYTES = 262144
_PROOF = object()
_ID = re.compile(r'^[A-Za-z0-9_.:-]{1,160}$')
_MONEY = re.compile(r'^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?$')
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
class StandingBudgetReservation:
    """Internal command, built only after the standing authority's full proof.

    Inventory is a complete, fresh API snapshot. The guard independently checks
    money, common caps and pending commitments inside the policy transaction.
    A committed reservation is still NOT permission to dispatch.
    """
    allocation_id: str
    expected_revision: int
    claim: Mapping
    inventory: Mapping


@dataclass(frozen=True)
class BudgetReservationSettlement:
    """Terminal proof is reloaded from PROPOSALS in this same transaction."""
    allocation_id: str
    execution_claim_id: str
    recover_legacy_terminal: bool = False


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


def _acknowledged(reply):
    if (not isinstance(reply, dict) or type(reply.get('ok')) not in (int, float)
        or reply['ok'] != 1 or 'writeConcernError' in reply or 'writeErrors' in reply):
        raise ValueError('transaction acknowledgement unproved')


async def _abort_once(db, session):
    reply = await db.client.admin.command({'abortTransaction': 1,
        'writeConcern': {'w': 'majority', 'j': True, 'wtimeout': 5000}}, session=session)
    _acknowledged(reply)


async def _commit_once(db, session):
    # Generic public command has no commit helper's automatic retry. Public
    # session machinery attaches lsid/txnNumber/autocommit; never mutate internals.
    reply = await db.client.admin.command({'commitTransaction': 1,
        'writeConcern': {'w': 'majority', 'j': True, 'wtimeout': 5000}, 'maxTimeMS': 5000}, session=session)
    _acknowledged(reply)


async def _end_session(session):
    cleanup = asyncio.ensure_future(session.end_session())
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
    cleanup.result()
    if cancelled:
        raise asyncio.CancelledError()


# Technical pending-resource limits per process, never financial limits. Reserve
# read slots so reconciliation remains available while write work is unfinished.
MAX_PENDING_WRITE_TASKS = 24
MAX_PENDING_READ_TASKS = 8
RECOVERY_TIMEOUT_SECONDS = 10
_pending_operations = {}


async def _retained_operation(db, scope, operation_id, kind, work, seconds, phase):
    key = (id(db), scope['user_id'], scope['account_id'], operation_id, kind)
    limit = MAX_PENDING_WRITE_TASKS if kind == 'write' else MAX_PENDING_READ_TASKS
    if key in _pending_operations or sum(item['kind'] == kind for item in _pending_operations.values()) >= limit:
        raise problem('google_budget_guard_operation_pending', 'عمل تخزين سابق لم ينتهِ؛ لا تعِد الإرسال.', 503)
    async def original_task():
        with timeout(seconds):
            return await work()
    state = {'kind': kind, 'task': asyncio.create_task(original_task())}
    _pending_operations[key] = state
    def consume(done):
        if not done.cancelled():
            done.exception()
        if _pending_operations.get(key) is state:
            del _pending_operations[key]
    state['task'].add_done_callback(consume)
    try:
        done, _ = await asyncio.wait({state['task']}, timeout=seconds)
        if not done:
            phase['stop_requested'] = True
            raise TimeoutError()
        return state['task'].result()
    except asyncio.CancelledError:
        phase['stop_requested'] = True
        raise


PROBE_TIMEOUT_SECONDS = 10
_pending_probe = None


async def probe_google_budget_guard_transactions(db):
    """Same-DB callers share one original-deadline, retained storage probe.

    Never cache completed proof, renew a deadline, retry uncertain operations,
    or cancel executor-backed driver Futures on a caller timeout/cancellation.
    Capability is issued only after explicit server abort ACK and async cleanup.
    """
    global _pending_probe
    state = _pending_probe
    if state is not None and not state['task'].done():
        if state['db'] is not db or state['expired']:
            raise problem('google_budget_guard_probe_busy', 'تحقق التخزين السابق لم ينتهِ؛ لم يبدأ تحقق جديد.', 503)
    else:
        state = {'db': db, 'deadline': asyncio.get_running_loop().time() + PROBE_TIMEOUT_SECONDS,
            'expired': False}

        async def probe():
            with timeout(PROBE_TIMEOUT_SECONDS):
                session = await db.client.start_session()
                try:
                    hello = await db.command('hello', session=session)
                    if (not isinstance(hello, dict) or not (hello.get('setName') or hello.get('msg') == 'isdbgrid')
                        or type(hello.get('logicalSessionTimeoutMinutes')) is not int):
                        _fail('google_budget_guard_transactions_unavailable')
                    _transaction(session)
                    result = await db[BUDGET_GUARDS].insert_one(
                        {'_id': 'probe_' + uuid.uuid4().hex, 'probe_only': True}, session=session)
                    if result.acknowledged is not True:
                        raise ValueError('unacknowledged probe')
                    await _abort_once(db, session)
                finally:
                    await _end_session(session)
                return _TransactionCapability(True, db, time.monotonic(), _PROOF)

        state['task'] = asyncio.create_task(probe())
        _pending_probe = state
        def consume(done):
            global _pending_probe
            if not done.cancelled():
                done.exception()
            if _pending_probe is state:
                _pending_probe = None
        state['task'].add_done_callback(consume)
    try:
        remaining = max(0, state['deadline'] - asyncio.get_running_loop().time())
        done, _ = await asyncio.wait({state['task']}, timeout=remaining)
        if not done or state['expired'] or asyncio.get_running_loop().time() > state['deadline']:
            state['expired'] = True
            raise TimeoutError()
        return state['task'].result()
    except asyncio.CancelledError:
        # One cancelled waiter cannot cancel shared driver work or other waiters.
        raise
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise problem('google_budget_guard_transactions_unavailable',
            'معاملات تخزين الميزانية غير مثبتة؛ يبقى التنفيذ غير متاح.', 503) from None


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
            or entry.get('state') not in {'reserved', 'native_enabled', 'cancelled_before_http', 'settled'}):
            _fail('google_budget_guard_state_unproved')


def _command(command):
    if isinstance(command, StandingBudgetReservation):
        if (not isinstance(command.allocation_id, str) or not _ID.fullmatch(command.allocation_id)
            or type(command.expected_revision) is not int or command.expected_revision < 1
            or not isinstance(command.claim, Mapping) or not isinstance(command.inventory, Mapping)):
            _fail('google_budget_guard_command_invalid')
        return StandingBudgetReservation(command.allocation_id, command.expected_revision,
            _plain(command.claim), _plain(command.inventory))
    if isinstance(command, BudgetReservationSettlement):
        if any(not isinstance(value, str) or not _ID.fullmatch(value)
            for value in (command.allocation_id, command.execution_claim_id)):
            _fail('google_budget_guard_command_invalid')
        if type(command.recover_legacy_terminal) is not bool:
            _fail('google_budget_guard_command_invalid')
        return command
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
            or Decimal(command.budget_native) <= 0
            or not isinstance(command.binding_digest, str) or not re.fullmatch(r'[a-f0-9]{64}', command.binding_digest)):
            _fail('google_budget_guard_command_invalid')
        return command
    _fail('google_budget_guard_command_invalid')


def _money(value):
    if not isinstance(value, str) or not _MONEY.fullmatch(value):
        _fail('google_budget_guard_money_unproved')
    return Decimal(value)


def _instant(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def standing_commitment(snapshot, command, current):
    """Pure independent cap calculation; average daily amounts, not spend caps.

    Enabled inventory contributes its current amount once. Each unresolved
    reservation contributes only the positive unreflected target difference;
    a pending enable contributes its full target while the native row is paused.
    Unknown, missing or unexpectedly changed native rows fail closed.
    """
    policy = snapshot.standing_policy
    scope = {'user_id': snapshot.user_id, 'account_id': snapshot.account_id}
    if (policy is None or policy.get('revision') != command.expected_revision
        or policy.get('enabled') is not True or policy.get('_dispatch_claim') is not None):
        _fail('google_budget_guard_policy_changed')
    from .google_ads_automation_authority import activation_proof_valid, policy_authorization_digest
    if not activation_proof_valid(thaw_snapshot(policy), current):
        _fail('google_budget_guard_activation_unproved')
    expiry = _instant(policy.get('expires_at'))
    if expiry is None or expiry <= current:
        _fail('google_budget_guard_policy_changed')
    inventory, claim = command.inventory, command.claim
    if (claim.get('policy_revision') != policy.get('revision')
        or claim.get('policy_digest') != policy_authorization_digest(thaw_snapshot(policy))):
        _fail('google_budget_guard_policy_changed')
    observed = _instant(inventory.get('observed_at'))
    if (inventory.get('complete') is not True or inventory.get('source') != 'google_ads_api'
        or inventory.get('account_id') != snapshot.account_id or observed is None
        or not 0 <= (current - observed).total_seconds() < 90
        or any(inventory.get(key) != policy.get(key) for key in ('currency_code', 'time_zone'))):
        _fail('google_budget_guard_inventory_unproved')
    caps = [_money(policy.get('max_account_daily_budget_native'))]
    for other in (snapshot.creation_policy,):
        if other is None:
            continue
        if type(other.get('enabled')) is not bool:
            _fail('google_budget_guard_policy_unproved')
        if other['enabled']:
            expiry = _instant(other.get('expires_at'))
            if (expiry is None or not activation_proof_valid(thaw_snapshot(other), current)
                or any(other.get(key) != policy.get(key) for key in ('currency_code', 'time_zone'))):
                _fail('google_budget_guard_policy_unproved')
            if expiry > current:
                caps.append(_money(other.get('max_account_daily_budget_native')))
    rows = inventory.get('campaigns')
    if not isinstance(rows, list) or len(rows) > 500:
        _fail('google_budget_guard_inventory_unproved')
    campaigns, budgets, total = {}, set(), Decimal(0)
    for row in rows:
        cid = row.get('campaign_id') if isinstance(row, dict) else None
        if (not isinstance(cid, str) or not re.fullmatch(r'[0-9]{1,30}', cid) or cid in campaigns
            or row.get('status') not in {'ENABLED', 'PAUSED', 'REMOVED'}):
            _fail('google_budget_guard_inventory_unproved')
        if row['status'] != 'REMOVED':
            budget = row.get('budget_resource')
            if (row.get('budget_mode') != 'DAILY' or row.get('shared_budget') is not False
                or not isinstance(budget, str) or not re.fullmatch(f'customers/{snapshot.account_id}/campaignBudgets/[0-9]+', budget)
                or budget in budgets):
                _fail('google_budget_guard_inventory_unproved')
            budgets.add(budget)
            _money(row.get('budget_native'))
        campaigns[cid] = row
        if row['status'] == 'ENABLED':
            total += _money(row['budget_native'])
    targets, reflected = set(), []
    for allocation_id, entry in snapshot.guard['allocations'].items():
        if entry['state'] in {'cancelled_before_http', 'settled'}:
            continue
        cid = entry.get('campaign_id')
        native = campaigns.get(cid)
        if (cid in targets or not native or native['status'] == 'REMOVED'
            or entry.get('currency_code') != policy.get('currency_code')):
            _fail('google_budget_guard_pending_unproved')
        amount = _money(entry.get('budget_native'))
        if entry.get('state') == 'native_enabled':
            settled_at = _instant(entry.get('settled_at'))
            if settled_at is None or observed < settled_at:
                _fail('google_budget_guard_reflection_unproved')
            if entry.get('action') == 'pause':
                if native['status'] != 'PAUSED':
                    _fail('google_budget_guard_reflection_unproved')
                reflected.append(allocation_id)
                continue
            if native['status'] != 'ENABLED' or _money(native['budget_native']) != amount:
                _fail('google_budget_guard_reflection_unproved')
            reflected.append(allocation_id)
            continue
        targets.add(cid)
        if native['status'] == 'ENABLED':
            total += max(Decimal(0), amount - _money(native['budget_native']))
        else:
            total += amount
    cid, action = claim.get('campaign_id'), claim.get('action')
    target = campaigns.get(cid)
    if (not target or cid in targets or action not in {'enable', 'set_budget', 'pause'}
        or any(not isinstance(claim.get(key), str) or not _ID.fullmatch(claim[key])
            for key in ('claim_id', 'proposal_id', 'execution_claim_id'))
        or claim['claim_id'] != claim['execution_claim_id']):
        _fail('google_budget_guard_claim_unproved')
    prior = _money(claim.get('campaign_budget_before_native'))
    proposed = _money(claim.get('campaign_budget_target_native'))
    if prior != _money(target.get('budget_native')):
        _fail('google_budget_guard_claim_unproved')
    if (action == 'enable' and (target['status'] != 'PAUSED' or proposed != prior)
        or action == 'pause' and (target['status'] != 'ENABLED' or proposed != prior)
        or action == 'set_budget' and target['status'] != 'ENABLED'):
        _fail('google_budget_guard_claim_unproved')
    increase = proposed if action == 'enable' else max(proposed - prior, Decimal(0)) if action == 'set_budget' and target['status'] == 'ENABLED' else Decimal(0)
    if increase > 0 and total + increase > min(caps):
        _fail('google_budget_guard_account_limit')
    return {'current_average_commitment_native': str(total), 'increase_native': str(increase),
        'resulting_average_commitment_native': str(total + increase), 'limit_native': str(min(caps)),
        'target_native': str(proposed), 'currency_code': policy['currency_code'],
        'reflected_allocation_ids': reflected}


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
    if (snapshot.standing_policy or {}).get('_dispatch_claim') is not None:
        # A pre-migration claim may not have a common allocation yet. Absence
        # from the new ledger does not prove available funding for a launch.
        _fail('google_budget_guard_legacy_claim_pending')
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
    session = await db.client.start_session()
    try:
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
        elif isinstance(command, StandingBudgetReservation):
            from zoneinfo import ZoneInfo
            from .google_ads_management import PROPOSALS
            from .google_ads_automation_authority import _day_buckets, _daily_history_ids, MAX_DAY_BUCKETS, MAX_HISTORY, HISTORY_FIELDS
            current = datetime.fromisoformat(stamp)
            commitment = standing_commitment(snapshot, command, current)
            old = thaw_snapshot(snapshot.guard)
            if old['active_claim'] is not None or len(old['allocations']) >= MAX_ALLOCATIONS:
                _fail('google_budget_guard_allocation_pending')
            if command.allocation_id in old['allocations']:
                _fail('google_budget_guard_identity_consumed')
            claim = dict(command.claim)
            zone = ZoneInfo(snapshot.standing_policy['time_zone'])
            today = current.astimezone(zone).date()
            prior_days, consumed = _day_buckets(thaw_snapshot(snapshot.standing_policy), today, snapshot.standing_policy['time_zone'])
            history = await db[PROPOSALS].find(scope, {'_id': 0, **{key: 1 for key in HISTORY_FIELDS}}, session=session).to_list(length=MAX_HISTORY + 1)
            if len(history) > MAX_HISTORY:
                _fail('google_budget_guard_history_unproved')
            consumed |= _daily_history_ids(history, today, zone)
            consumed.add(claim['proposal_id'])
            maximum = snapshot.standing_policy.get('max_actions_per_day')
            if type(maximum) is not int or not 1 <= maximum <= 20 or len(consumed) > maximum:
                _fail('google_budget_guard_daily_action_limit')
            days = sorted([*prior_days, {'date': today.isoformat(), 'time_zone': snapshot.standing_policy['time_zone'],
                'proposal_ids': sorted(consumed)}], key=lambda value: value['date'])[-MAX_DAY_BUCKETS:]
            entry = {**scope, 'allocation_id': command.allocation_id, 'operation_id': operation_id,
                'kind': 'standing', 'campaign_id': claim['campaign_id'], 'proposal_id': claim['proposal_id'],
                'execution_claim_id': claim['execution_claim_id'], 'currency_code': commitment['currency_code'],
                'action': claim['action'],
                'budget_native': commitment['target_native'], 'state': 'reserved', 'created_at': stamp,
                'commitment': commitment, 'claim_digest': _digest(claim)}
            policy = snapshot.standing_policy
            generation = policy.get('_dispatch_generation', 0)
            if type(generation) is not int or not 0 <= generation < 2**63 - 2:
                _fail('google_budget_guard_state_unproved')
            changed = await db[AUTOMATION_POLICIES].update_one({**scope, 'revision': command.expected_revision,
                '_dispatch_claim': {'$in': [None]}}, {'$set': {'_dispatch_claim': claim, '_dispatch_days': days},
                '$inc': {'_dispatch_generation': 1}}, session=session)
            if changed.matched_count != 1:
                _fail('google_budget_guard_policy_changed')
            for reflected_id in commitment['reflected_allocation_ids']:
                old['allocations'][reflected_id] = {**old['allocations'][reflected_id], 'state': 'settled',
                    'reflection_observed_at': command.inventory['observed_at']}
            update.update(allocations={**old['allocations'], command.allocation_id: entry},
                active_claim={'allocation_id': command.allocation_id, 'operation_id': operation_id,
                    'proposal_id': claim['proposal_id'], 'execution_claim_id': claim['execution_claim_id']})
            result = {'kind': 'standing_reservation', 'allocation_id': command.allocation_id, **commitment}
        elif isinstance(command, BudgetReservationSettlement):
            from .google_ads_management import PROPOSALS, FENCES, digest
            from .google_ads_automation_authority import _bound_proposal, stopped_before_http_proven
            old = thaw_snapshot(snapshot.guard)
            entry = old['allocations'].get(command.allocation_id)
            claim = thaw_snapshot((snapshot.standing_policy or {}).get('_dispatch_claim'))
            legacy = entry is None and command.recover_legacy_terminal
            if legacy:
                if (not isinstance(claim, dict) or old.get('active_claim') is not None
                    or len(old['allocations']) >= MAX_ALLOCATIONS
                    or command.allocation_id != 'standing_' + command.execution_claim_id
                    or claim.get('claim_id') != command.execution_claim_id
                    or claim.get('execution_claim_id') != command.execution_claim_id
                    or not isinstance(claim.get('proposal_id'), str) or not _ID.fullmatch(claim['proposal_id'])
                    or not isinstance(claim.get('campaign_id'), str) or not re.fullmatch(r'[0-9]{1,30}',claim['campaign_id'])
                    or claim.get('action') not in {'pause','enable','set_budget'}
                    or claim.get('budget_semantics') != 'average_daily_budget'
                    or any(item.get('execution_claim_id') == command.execution_claim_id
                        or item.get('proposal_id') == claim['proposal_id']
                        or item.get('campaign_id') == claim['campaign_id'] and item.get('state') not in {'settled','cancelled_before_http'}
                        for item in old['allocations'].values())):
                    _fail('google_budget_guard_settlement_unproved')
                # The provisional entry exists only in this transaction's local
                # memory. Unknown outcome rolls back even a newly created guard.
                _money(claim.get('campaign_budget_before_native'))
                _money(claim.get('campaign_budget_target_native'))
                entry = {**scope,'allocation_id':command.allocation_id,'operation_id':operation_id,
                    'kind':'standing','state':'reserved','origin':'legacy_terminal_recovery',
                    'campaign_id':claim['campaign_id'],'action':claim['action'],'proposal_id':claim['proposal_id'],
                    'execution_claim_id':command.execution_claim_id,'claim_digest':_digest(claim),
                    'budget_native':claim['campaign_budget_target_native'],'created_at':stamp}
            if (not entry or entry.get('kind') != 'standing' or entry.get('state') != 'reserved'
                or entry.get('execution_claim_id') != command.execution_claim_id or not isinstance(claim, dict)
                or _digest(claim) != entry.get('claim_digest')
                or not legacy and (old.get('active_claim') or {}).get('allocation_id') != command.allocation_id):
                _fail('google_budget_guard_settlement_unproved')
            proposal = await db[PROPOSALS].find_one({**scope, 'proposal_id': entry['proposal_id'],
                'execution_claim_id': command.execution_claim_id}, {'_id': 0}, session=session)
            if not proposal:
                _fail('google_budget_guard_settlement_unproved')
            source = _bound_proposal(proposal)
            if (proposal.get('confirmation_digest') != claim.get('confirmation_digest')
                or digest(proposal.get('operations')) != claim.get('operations_digest')
                or source['policy_revision'] != claim.get('policy_revision')
                or source['policy_digest'] != claim.get('policy_digest')):
                _fail('google_budget_guard_settlement_unproved')
            if legacy:
                if (proposal.get('campaign_id') != entry['campaign_id'] or proposal.get('action') != entry['action']
                    or not isinstance(proposal.get('currency'),str) or not re.fullmatch(r'[A-Z]{3}',proposal['currency'])
                    or not await db[FENCES].find_one({**scope,'proposal_id':entry['proposal_id'],
                        'execution_claim_id':command.execution_claim_id,'status':'claimed'}, {'_id':0}, session=session)):
                    _fail('google_budget_guard_settlement_unproved')
                before = proposal.get('before_snapshot')
                campaign = before.get('campaign') if isinstance(before,dict) else None
                budget = before.get('campaign_budget') if isinstance(before,dict) else None
                micros = budget.get('amountMicros') if isinstance(budget,dict) else None
                if (not isinstance(campaign,dict) or not isinstance(budget,dict) or campaign.get('id') != entry['campaign_id']
                    or digest(before) != proposal.get('before_digest')
                    or budget.get('period') != 'DAILY' or isinstance(micros,bool)
                    or not isinstance(micros,(str,int)) or not re.fullmatch(r'[0-9]{1,18}',str(micros))):
                    _fail('google_budget_guard_settlement_unproved')
                before_amount = Decimal(str(micros)) / Decimal(1000000)
                planned = proposal.get('planned')
                if (not isinstance(planned,dict) or planned.get('action') != entry['action']
                    or _money(claim['campaign_budget_before_native']) != before_amount
                    or _money(entry['budget_native']) != (_money(planned.get('budget_native'))
                        if entry['action']=='set_budget' else before_amount)):
                    _fail('google_budget_guard_settlement_unproved')
                entry['currency_code'] = proposal['currency']
            rejection = proposal.get('rejection') or {}
            completed = proposal.get('status') == 'completed' and proposal.get('verified') is True and proposal.get('submission_started') is True
            cancelled = proposal.get('status') == 'failed' and proposal.get('submission_started') is False and proposal.get('safe_error') == 'execution_cancelled_before_dispatch'
            rejected = (proposal.get('status') == 'failed' and proposal.get('submission_started') is True
                and proposal.get('provider_write_reached') is False and isinstance(rejection, dict)
                and type(rejection.get('provider_status')) is int and 400 <= rejection['provider_status'] < 500)
            stopped = stopped_before_http_proven(proposal, datetime.fromisoformat(stamp))
            if stopped:
                authorized, stopped_at = _instant(claim.get('authorized_at')), _instant(proposal.get('dispatch_cancelled_before_http_at'))
                stopped = authorized is not None and stopped_at is not None and stopped_at >= authorized
            if not (completed or cancelled or rejected or stopped):
                _fail('google_budget_guard_settlement_unproved')
            if legacy:
                # Snapshot reads alone do not conflict with a concurrent native
                # fence release. A real marker write participates in that CAS
                # boundary; it never releases or changes the fence's ownership.
                fenced = await db[FENCES].update_one({**scope,'proposal_id':entry['proposal_id'],
                    'execution_claim_id':command.execution_claim_id,'status':'claimed'},
                    {'$set':{'_budget_settlement_operation_id':operation_id,
                        '_budget_settlement_observed_at':stamp}}, session=session)
                if fenced.matched_count != 1:
                    _fail('google_budget_guard_settlement_unproved')
            generation = (snapshot.standing_policy or {}).get('_dispatch_generation')
            if type(generation) is not int or not 0 <= generation < 2**63 - 2:
                _fail('google_budget_guard_settlement_unproved')
            changed = await db[AUTOMATION_POLICIES].update_one({**scope, '_dispatch_claim': claim},
                {'$set': {'_dispatch_claim': None, '_dispatch_last_settlement': {
                    'claim_id': command.execution_claim_id,
                    'status': 'completed' if completed else 'cancelled_before_dispatch' if cancelled else 'provider_rejected' if rejected else 'stopped_before_provider_dispatch',
                    'observed_at': stamp,'legacy_recovered':legacy}}, '$inc': {'_dispatch_generation': 1}}, session=session)
            if changed.matched_count != 1:
                _fail('google_budget_guard_settlement_unproved')
            old['allocations'][command.allocation_id] = {**entry, 'state': 'native_enabled' if completed else 'settled', 'settled_at': stamp,
                'outcome': 'completed' if completed else 'cancelled_before_http' if cancelled or stopped else 'provider_rejected'}
            update.update(allocations=old['allocations'], active_claim=None)
            result = {'kind': 'standing_settlement', 'allocation_id': command.allocation_id,
                'quota_refunded': False,'legacy_recovered':legacy}
        else:
            allocations, claim = _allocation(snapshot, command, operation_id, stamp)
            update.update(allocations=allocations, active_claim=claim)
            result = {'kind': 'launch_allocation', 'allocation_id': command.allocation_id}
        changed = await db[BUDGET_GUARDS].update_one({'_id': row['_id'], **scope, 'epoch': row['epoch']}, {'$set': update}, session=session)
        if changed.matched_count != 1:
            _fail('google_budget_guard_epoch_changed')
        if phase.get('stop_requested'):
            _fail('google_budget_guard_request_ended_before_commit')
        phase['commit_attempted'] = True
        await _commit_once(db, session)  # Exactly one wire command; no automatic commit retry.
        return {**result, 'committed': True, 'guard_epoch': row['epoch'] + 1, 'operation_id': operation_id,
            'provider_dispatch_authorized': False, 'provider_writes': 0, 'runtime_available': False}

    finally:
        await _end_session(session)

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
        return await _retained_operation(db, scope, operation_id, 'write',
            lambda: _run(db, scope=scope, operation_id=operation_id, planner=planner,
                capability=capability, phase=phase), timeout_seconds, phase)
    except asyncio.CancelledError as exc:
        exc.commit_attempted = phase['commit_attempted']
        exc.preserve_execution_fence = True
        exc.automatic_retry_allowed = False
        raise
    except Exception as exc:
        if (not phase['commit_attempted'] and (isinstance(exc, DuplicateKeyError)
            or isinstance(exc, OperationFailure) and exc.code == 112)):
            # A known-aborted precommit conflict asks the owner to refresh. It
            # still grants no dispatch, retry, refund or fence-release evidence.
            exc = problem('google_budget_guard_policy_changed', 'تغيرت سياسة جوجل أثناء المعاملة؛ أعد تحميلها.', 409)
        if not isinstance(exc, HTTPException):
            exc = problem('google_budget_guard_commit_unconfirmed' if phase['commit_attempted'] else 'google_budget_guard_transaction_unconfirmed',
                'لم يتأكد حجز الميزانية؛ تحقق بالقراءة واحتفظ بالقفل دون إعادة إرسال.', 503)
        if isinstance(exc.detail, dict):
            exc.detail.update(commit_attempted=phase['commit_attempted'], preserve_execution_fence=True, automatic_retry_allowed=False)
        raise exc from None


async def read_google_budget_guard_commit(db, *, user_id, account_id, operation_id):
    """Read storage evidence after an unknown ACK; NEVER permission or release.

    Missing/currently overwritten evidence is NOT proof of an absent historical
    commit. The caller must retain its execution fence and cannot resend. This
    path works without a current probe token or an enabled runtime, but still
    uses an explicit read transaction with snapshot/majority and the same session.
    """
    async def read():
        scope = _scope(user_id, account_id)
        if not isinstance(operation_id, str) or not _ID.fullmatch(operation_id):
            _fail('google_budget_guard_command_invalid')
        session = await db.client.start_session()
        try:
            _transaction(session)
            row = await db[BUDGET_GUARDS].find_one({'_id': _guard_id(scope)}, session=session)
            if row is not None:
                _validate_guard(row, scope)
            entries = [entry for entry in (row or {}).get('allocations', {}).values() if entry.get('operation_id') == operation_id]
            if len(entries) > 1:
                _fail('google_budget_guard_state_unproved')
            last = (row or {}).get('last_commit')
            exact_last = last if isinstance(last, dict) and last.get('operation_id') == operation_id else None
            await _abort_once(db, session)
            return {'storage_outcome': 'committed' if entries or exact_last else 'not_proven',
                'operation_id': operation_id, 'guard_epoch': row['epoch'] if row is not None else None,
                'allocation': _plain(entries[0]) if entries else None, 'last_commit': _plain(exact_last),
                'provider_dispatch_authorized': False, 'provider_writes': 0,
                'preserve_execution_fence': True, 'automatic_retry_allowed': False}
        finally:
            await _end_session(session)
    try:
        scope = _scope(user_id, account_id)
        return await _retained_operation(db, scope, operation_id, 'read', read, RECOVERY_TIMEOUT_SECONDS, {})
    except asyncio.CancelledError as exc:
        exc.preserve_execution_fence = True
        exc.automatic_retry_allowed = False
        raise
    except Exception as exc:
        if not isinstance(exc, HTTPException):
            exc = problem('google_budget_guard_recovery_unconfirmed', 'لم يتأكد سجل الحجز؛ احتفظ بالقفل دون إعادة إرسال.', 503)
        if isinstance(exc.detail, dict):
            exc.detail.update(preserve_execution_fence=True, automatic_retry_allowed=False)
        raise exc from None
