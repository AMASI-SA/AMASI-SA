"""Merchant-scoped admission and a serialized pause barrier for local writes.

Business transactions update the SAME control document before touching state.
An owner pause therefore conflicts with every admitted in-flight transaction;
a committed pause is a barrier for these instrumented writers, not an assertion
that arbitrary legacy workers or external provider calls have been stopped.
No ordinary transaction bootstraps control. Missing/malformed state fails closed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field, StrictBool, StrictInt

from .contracts import Contract, Key
from .domain import DomainError, digest

CONTROL = 'mezan_special_order_write_control_v1'
AUDIT = 'mezan_special_order_write_control_audit_v1'
SCOPES = frozenset({'creation', 'workflow', 'financial', 'evidence', 'configuration', 'dispatch'})


class ScopeFlags(Contract):
    creation: StrictBool
    workflow: StrictBool
    financial: StrictBool
    evidence: StrictBool
    configuration: StrictBool
    dispatch: StrictBool


class ControlChange(Contract):
    expected_revision: Annotated[StrictInt, Field(ge=0, le=10**12)]
    enabled: ScopeFlags
    reason: Annotated[str, Field(min_length=5, max_length=1000)]


def validate_scopes(scopes):
    if not isinstance(scopes, frozenset) or not scopes or scopes - SCOPES:
        raise DomainError('explicit_writer_scopes_required', 422)
    return scopes


def validate_state(row, tenant):
    if (not isinstance(row, dict) or row.get('_id') != tenant or row.get('tenant_id') != tenant
            or type(row.get('schema_version')) is not int or row['schema_version'] != 1):
        raise DomainError('special_write_control_invalid', 503)
    for key, minimum in (('revision', 1), ('write_epoch', 1), ('activity_seq', 0)):
        if type(row.get(key)) is not int or not minimum <= row[key] <= 10**15:
            raise DomainError('special_write_control_invalid', 503)
    flags = row.get('enabled')
    if not isinstance(flags, dict) or set(flags) != SCOPES or any(type(v) is not bool for v in flags.values()):
        raise DomainError('special_write_control_invalid', 503)
    if (not isinstance(row.get('last_command_id'), str) or not row['last_command_id']
            or row.get('changed_by') != tenant or not isinstance(row.get('changed_at'), str)):
        raise DomainError('special_write_control_invalid', 503)
    return row


async def state(db, tenant_id):
    from .binding import require_bound
    require_bound(db, tenant_id=tenant_id)
    tenant = str(tenant_id)
    if not tenant:
        raise DomainError('merchant_scope_required', 403)
    row = await db[CONTROL].find_one({'_id': tenant, 'tenant_id': tenant})
    if row is None:
        raise DomainError('special_write_control_uninitialized', 503)
    return validate_state(row, tenant)


async def capabilities(db, tenant_id):
    from .binding import require_bound
    static = require_bound(db, tenant_id=tenant_id).enablement
    try:
        row = await state(db, tenant_id)
    except DomainError as exc:
        if exc.code != 'special_write_control_uninitialized':
            raise
        return {'initialized': False, 'revision': 0, 'write_epoch': None,
                'effective': {scope: False for scope in sorted(SCOPES)}}
    effective = {scope: allowed and static.commands for scope, allowed in row['enabled'].items()}
    effective['creation'] &= static.creation and effective['workflow']
    effective['financial'] &= static.financial
    return {'initialized': True, 'revision': row['revision'], 'write_epoch': row['write_epoch'],
            'effective': effective}


async def admit(db, tenant_id, scopes, *, expected_epoch=None):
    """Must be the first business write of the same real Mongo transaction."""
    from .binding import require_bound
    scopes = validate_scopes(scopes)
    binding = require_bound(db, write=True, finance='financial' in scopes, tenant_id=tenant_id)
    if binding.session is None or not binding.session.in_transaction:
        raise DomainError('special_write_admission_requires_transaction')
    if 'creation' in scopes and not binding.enablement.creation:
        raise DomainError('creation_disabled', 403)
    tenant = str(tenant_id)
    row = await state(db, tenant)
    if expected_epoch is not None and (type(expected_epoch) is not int or expected_epoch != row['write_epoch']):
        raise DomainError('special_write_epoch_stale')
    if any(row['enabled'][scope] is not True for scope in scopes):
        raise DomainError('special_writes_paused', 423)
    # Use the raw collection with the exact active session: its control write is
    # the admission itself, not an unscoped business write. No data upsert.
    result = await binding.raw_database[CONTROL].update_one(
        {'_id': tenant, 'tenant_id': tenant, 'schema_version': 1,
         'revision': row['revision'], 'write_epoch': row['write_epoch'],
         **{'enabled.'+scope: True for scope in scopes}},
        {'$inc': {'activity_seq': 1}}, session=binding.session)
    if result.matched_count != 1:
        raise DomainError('special_write_control_changed')
    return {'tenant_id': tenant, 'scopes': scopes, 'write_epoch': row['write_epoch'],
            'revision': row['revision']}


async def change(db, session_user, request: ControlChange, key: str):
    """Fresh owner only; first explicit initialization always creates paused state.

    Administrative control intentionally does not require business scopes open,
    so an authenticated owner can recover a stopped instance. Static enablement
    remains an independent upper bound. This function never enables static gates.
    """
    from .access import fresh_principal
    from .binding import require_bound, _atomic_transaction, _CONTROL_ADMIN
    from .service import validate_key
    validate_key(key)
    if require_bound(db).session is not None:
        raise DomainError('control_transition_requires_own_transaction')
    # Reads remain available even while commands are OFF.
    fresh, tenant = await fresh_principal(db, session_user)
    if fresh.get('role') != 'owner' or fresh['id'] != tenant:
        raise DomainError('write_control_owner_required', 403)
    require_bound(db, tenant_id=tenant)
    command_id = str(uuid5(NAMESPACE_URL, 'special-control:'+tenant+':'+key))
    fingerprint = digest(request.model_dump(mode='json'))
    flags = request.enabled.model_dump(mode='json')
    if request.expected_revision == 0 and any(flags.values()):
        raise DomainError('control_initialization_must_be_paused', 422)

    async def apply(scoped):
        principal, actual_tenant = await fresh_principal(scoped, session_user)
        if actual_tenant != tenant or principal.get('role') != 'owner' or principal['id'] != tenant:
            raise DomainError('write_control_owner_required', 403)
        # A concurrent revocation of the persisted owner must conflict, rather
        # than accepting authority only from the pre-transaction session view.
        await scoped.users.update_one({'id': tenant, 'role': 'owner'},
                                      {'$inc': {'special_control_fence': 1}})
        previous = await scoped[AUDIT].find_one({'_id': command_id, 'tenant_id': tenant})
        current = await scoped[CONTROL].find_one({'_id': tenant, 'tenant_id': tenant})
        if previous:
            if previous.get('fingerprint') != fingerprint:
                raise DomainError('idempotency_payload_conflict')
            if current is None:
                raise DomainError('write_control_history_without_state')
            validate_state(current, tenant)
            return {'control': _public(current), 'applied_revision': previous['after']['revision'],
                    'replayed': True, 'pause_barrier_scope': 'instrumented_local_transactions_only'}
        if current is None:
            if await scoped[AUDIT].find_one({'tenant_id':tenant},{'_id':1}):
                raise DomainError('write_control_history_without_state')
            if request.expected_revision != 0:
                raise DomainError('special_write_control_uninitialized', 503)
            before = None
            row = {'_id': tenant, 'tenant_id': tenant, 'schema_version': 1,
                   'revision': 1, 'write_epoch': 1, 'activity_seq': 0, 'enabled': flags}
        else:
            validate_state(current, tenant)
            if current['revision'] != request.expected_revision:
                raise DomainError('write_control_revision_conflict')
            before = _public(current)
            row = {**current, 'enabled': flags, 'revision': current['revision']+1,
                   'write_epoch': current['write_epoch']+1}
        now = datetime.now(timezone.utc).isoformat()
        row.update(changed_by=tenant, changed_at=now, last_command_id=command_id)
        marker = _CONTROL_ADMIN.set({**_CONTROL_ADMIN.get(), id(scoped.raw_database): tenant})
        try:
            if current is None:
                await scoped[CONTROL].insert_one(dict(row))
            else:
                result = await scoped[CONTROL].replace_one(
                    {'_id': tenant, 'tenant_id': tenant, 'revision': request.expected_revision}, dict(row))
                if result.matched_count != 1:
                    raise DomainError('write_control_revision_conflict')
            await scoped[AUDIT].insert_one({'_id': command_id, 'tenant_id': tenant,
                'fingerprint': fingerprint, 'before': before, 'after': _public(row),
                'reason': request.reason, 'actor_id': tenant, 'occurred_at': now})
        finally:
            _CONTROL_ADMIN.reset(marker)
        return {'control': _public(row), 'applied_revision': row['revision'], 'replayed': False,
                'pause_barrier_scope': 'instrumented_local_transactions_only'}
    from pymongo.errors import DuplicateKeyError
    # A concurrent first initialization may observe DuplicateKeyError rather
    # than a retryable write conflict. Retry the same command, never overwrite.
    for attempt in range(2):
        try:
            return await _atomic_transaction(db, apply)
        except DuplicateKeyError:
            if attempt:
                raise DomainError('write_control_initialization_conflict') from None


def _public(row):
    return {key: row[key] for key in ('tenant_id', 'schema_version', 'revision',
            'write_epoch', 'enabled', 'changed_by', 'changed_at', 'last_command_id')}
