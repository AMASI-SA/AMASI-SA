"""Explicit opt-in WIP seam for all six Google owner policy mutations.

writer=None preserves legacy storage behavior; it is NOT a migrated guard path.
Supplying GuardedGooglePolicyWriter requires a previously established capability
for the same database. No implicit transaction probe, legacy-CAS fallback, OAuth
read, retry, or runtime activation is performed. Revoke therefore needs no live
Google connection; its guarded form still needs proven database transactions.

Route/bootstrap integration and migration of all automated budget increases are
NOT installed here. budget_guard_runtime_available deliberately remains False.
"""
from dataclasses import dataclass
import asyncio
import uuid

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from .google_ads_client import problem


def _changed(policy_kind):
    return problem('google_creation_policy_changed' if policy_kind == 'creation' else 'google_automation_policy_changed',
        'تغيرت سياسة جوجل؛ أعد تحميلها ومراجعتها قبل التعديل.', 409)


def _values(policy_kind, scope, expected_revision, values):
    # Local imports avoid the budget guard -> policy -> adapter import cycle.
    from .google_ads_automation_policy import PUBLIC_FIELDS as standing
    from .google_ads_automation_creation_policy import PUBLIC_FIELDS as creation
    allowed = set(creation if policy_kind == 'creation' else standing) - {'events', 'revision', 'account_id'}
    if (policy_kind not in {'standing', 'creation'} or not isinstance(scope, dict)
        or set(scope) != {'user_id', 'account_id'} or not isinstance(values, dict)
        or type(expected_revision) is not int or not 0 <= expected_revision <= 2**63 - 2):
        raise problem('google_policy_mutation_invalid', 'بيانات تعديل سياسة جوجل غير صالحة.', 422)
    result = dict(values)
    for key, expected in {**scope, 'revision': expected_revision + 1}.items():
        if key in result and (type(result[key]) is not type(expected) or result[key] != expected):
            raise problem('google_policy_mutation_scope_changed', 'تغير نطاق سياسة جوجل أو إصدارها.', 409)
        result.pop(key, None)
    if not result or set(result) - allowed:
        raise problem('google_policy_mutation_fields_invalid', 'حقول تعديل سياسة جوجل غير صالحة.', 422)
    return result


@dataclass(frozen=True)
class GuardedGooglePolicyWriter:
    capability: object

    async def write(self, db, *, policy_kind, scope, expected_revision, values, event=None):
        from .google_ads_automation_budget_guard import PolicyMutation, run_google_budget_guard_transaction
        operation_id = 'google_policy_' + uuid.uuid4().hex
        try:
            return await run_google_budget_guard_transaction(db, **scope, operation_id=operation_id,
                capability=self.capability,
                planner=lambda snapshot: PolicyMutation(policy_kind, expected_revision, values, event))
        except asyncio.CancelledError as exc:
            exc.operation_id = operation_id
            raise
        except HTTPException as exc:
            if isinstance(exc.detail, dict):
                exc.detail['operation_id'] = operation_id
                if exc.detail.get('code') == 'google_budget_guard_policy_changed':
                    exc.detail['code'] = _changed(policy_kind).detail['code']
            raise


async def mutate_google_automation_policy(db, *, policy_kind, scope, expected_revision, values, event=None, writer=None):
    """Exactly one selected storage mode. Guard failures NEVER choose legacy.

    values/event contain only already validated server-side domain decisions.
    Guarded writers reload both policy snapshots and CAS their common epoch in
    one session; private claims/quota/ledger fields are never replaced.
    """
    values = _values(policy_kind, scope, expected_revision, values)
    if writer is not None:
        if not isinstance(writer, GuardedGooglePolicyWriter):
            raise problem('google_policy_mutation_writer_invalid', 'كاتب سياسة جوجل غير مثبت.', 503)
        return await writer.write(db, policy_kind=policy_kind, scope=scope, expected_revision=expected_revision,
            values=values, event=event)
    # Explicit compatibility mode, not an error fallback. It remains unmigrated.
    from .google_ads_automation_policy import AUTOMATION_POLICIES
    from .google_ads_automation_creation_policy import CREATION_POLICIES
    collection = db[CREATION_POLICIES if policy_kind == 'creation' else AUTOMATION_POLICIES]
    payload = {**values, 'revision': expected_revision + 1}
    if expected_revision == 0:
        row = {**scope, **payload}
        if event is not None:
            row['events'] = [event]
        try:
            await collection.insert_one(row)
        except DuplicateKeyError:
            raise _changed(policy_kind) from None
    else:
        update = {'$set': payload}
        if event is not None:
            update['$push'] = {'events': {'$each': [event], '$slice': -50}}
        changed = await collection.update_one({**scope, 'revision': expected_revision}, update)
        if not changed.matched_count:
            raise _changed(policy_kind)
    return {'kind': 'policy_mutation', 'policy_kind': policy_kind, 'policy_revision': payload['revision'],
        'guarded': False, 'provider_dispatch_authorized': False, 'provider_writes': 0, 'runtime_available': False}
