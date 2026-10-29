"""Mandatory transaction adapter for all six Google owner policy mutations.

Every write, including revocation, proves transaction capability in the supplied
Mongo database and commits the policy with the common account epoch. Revocation
requires no live Google connection. No provider mutation, retry or fallback.
Budget runtime remains disabled until integrated acceptance is proven.
"""
from dataclasses import dataclass
import asyncio
import uuid

from fastapi import HTTPException

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


async def mutate_google_automation_policy(db, *, policy_kind, scope, expected_revision, values, event=None):
    """Mandatory common-epoch transaction; no OAuth access or legacy fallback.

    Probe explicitly against the supplied database for every mutation. A failed
    probe or unknown commit never selects a different persistence path.
    """
    from .google_ads_automation_budget_guard import probe_google_budget_guard_transactions
    values = _values(policy_kind, scope, expected_revision, values)
    capability = await probe_google_budget_guard_transactions(db)
    return await GuardedGooglePolicyWriter(capability).write(db, policy_kind=policy_kind,
        scope=scope, expected_revision=expected_revision, values=values, event=event)
