"""Explicit merchant plan availability, separate from immutable fee contracts.

This is configuration, not a guessed plan on a payment or a posting/backfill.
The scope pins reviewed contract contents so editing a tariff invalidates it.
"""
from datetime import date
from hashlib import sha256
import json


METADATA = {'id', 'user_id', '_id', 'plan_id', 'confirmed_by', 'confirmed_at',
            'evidence', 'version', 'revisions', '_approved_plan_scopes'}


def contract_signature(policy):
    return json.dumps({k: v for k, v in policy.items() if k not in METADATA},
                      sort_keys=True, separators=(',', ':'), allow_nan=False)


def contract_digest(policy):
    content = [policy.get('plan_id'), policy.get('version', 1), contract_signature(policy)]
    return sha256(json.dumps(content, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def scoped_uniform_policy(payment, candidates, transaction_date):
    if payment.get('provider') != 'tabby' or payment.get('event_type', 'capture') != 'capture':
        return None
    scopes = {}
    for policy in candidates:
        for scope in policy.get('_approved_plan_scopes', []):
            if (scope.get('provider') == payment['provider']
                    and scope.get('currency') == payment['currency']
                    and scope.get('status') == 'active'
                    and scope['effective_from'] <= transaction_date.isoformat()
                    and (not scope.get('effective_to') or transaction_date.isoformat() <= scope['effective_to'])):
                identity = scope.get('id')
                if identity in scopes and scopes[identity] != scope:
                    raise ValueError('provider_plan_scope_conflict')
                scopes[identity] = scope
    if not scopes:
        return None
    if len(scopes) != 1:
        raise ValueError('provider_plan_scope_conflict')
    scope = next(iter(scopes.values()))
    from operational_provider_fees import _validate_policy_evidence
    _validate_policy_evidence(scope)
    refs = scope.get('contracts', [])
    if not refs or len({r['policy_id'] for r in refs}) != len(refs):
        raise ValueError('provider_plan_scope_invalid')
    selected = []
    for ref in refs:
        matches = [p for p in candidates if p.get('id') == ref['policy_id']]
        if len(matches) != 1 or contract_digest(matches[0]) != ref['contract_digest']:
            raise ValueError('provider_plan_scope_contract_changed')
        policy = matches[0]
        _validate_policy_evidence(policy)
        if not policy.get('plan_id') or type(policy.get('fee_model_version')) is not int or policy['fee_model_version'] != 2:
            raise ValueError('provider_plan_scope_invalid')
        selected.append(policy)
    if len({contract_signature(p) for p in selected}) != 1:
        raise ValueError('provider_plan_scope_terms_differ')
    # A deterministic representative supplies arithmetic only. Never assign its
    # plan_id to the order; result.plan_id remains the payment's unknown value.
    chosen = min(selected, key=lambda p: p['id'])
    return {**chosen, '_equivalent_policy_ids': tuple(sorted(p['id'] for p in selected)),
            '_approved_scope_id': scope['id']}


def scope_boundaries(policies):
    for policy in policies:
        for scope in policy.get('_approved_plan_scopes', []):
            yield date.fromisoformat(scope['effective_from'])
            if scope.get('effective_to'):
                from datetime import timedelta
                yield date.fromisoformat(scope['effective_to']) + timedelta(days=1)


from pydantic import Field, model_validator
from accounting_onboarding_ssot import Contract


class PlanScopeCreate(Contract):
    provider: str = Field(pattern='^tabby$')
    currency: str = Field(pattern='^[A-Z]{3}$')
    policy_ids: list[str] = Field(min_length=1, max_length=20)
    expected_version: int = Field(ge=1)
    effective_from: date
    effective_to: date | None = None
    evidence: str = Field(min_length=3, max_length=1000)

    @model_validator(mode='after')
    def valid_scope(self):
        if len(set(self.policy_ids)) != len(self.policy_ids):
            raise ValueError('provider_plan_scope_duplicate_policy')
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError('provider_plan_scope_date_range_invalid')
        return self


async def list_plan_scopes(db, owner, currency):
    from accounting_onboarding_ssot import POLICIES, _key
    doc = await db[POLICIES].find_one({'_id': _key(owner, 'tabby', currency), 'user_id': owner})
    return {'items': (doc or {}).get('plan_scopes', []), 'version': (doc or {}).get('version')}


async def create_plan_scope(db, owner, actor, payload):
    from uuid import uuid4
    from accounting_onboarding_ssot import POLICIES, _key, _fail, _now, _overlap
    from operational_provider_fees import _validate_policy_evidence
    key = _key(owner, payload.provider, payload.currency)
    old = await db[POLICIES].find_one({'_id': key, 'user_id': owner})
    if not old or old.get('version') != payload.expected_version:
        _fail('provider_plan_scope_version_conflict')
    scope = payload.model_dump(mode='json', exclude={'expected_version', 'policy_ids'})
    if any(_overlap(scope, row) for row in old.get('plan_scopes', []) if row.get('status') == 'active'):
        _fail('provider_plan_scope_overlap')
    selected = []
    for identity in payload.policy_ids:
        matches = [p for p in old.get('policies', []) if p.get('id') == identity]
        if len(matches) != 1:
            _fail('provider_plan_scope_contract_missing')
        p = matches[0]
        if (p.get('user_id') != owner or p.get('provider') != payload.provider
                or p.get('currency') != payload.currency or p.get('status') != 'active'
                or not p.get('plan_id') or p.get('event_type', 'capture') != 'capture'
                or type(p.get('fee_model_version')) is not int or p['fee_model_version'] != 2
                or p['effective_from'] > scope['effective_from']
                or (p.get('effective_to') and (not scope.get('effective_to') or p['effective_to'] < scope['effective_to']))):
            _fail('provider_plan_scope_contract_outside_scope')
        try:
            _validate_policy_evidence(p)
            contract_digest(p)
        except (ValueError, TypeError):
            _fail('provider_plan_scope_contract_invalid')
        selected.append(p)
    if len({contract_signature(p) for p in selected}) != 1:
        _fail('provider_plan_scope_terms_differ')
    scope.update(id=str(uuid4()), status='active', confirmed_by=actor, confirmed_at=_now(),
                 contracts=[{'policy_id': p['id'], 'contract_digest': contract_digest(p)} for p in selected])
    result = await db[POLICIES].update_one({'_id': key, 'user_id': owner, 'version': payload.expected_version},
        {'$push': {'plan_scopes': scope, 'audit': {'action': 'plan_scope_confirmed', 'actor_id': actor,
                   'at': scope['confirmed_at'], 'scope_id': scope['id']}}, '$inc': {'version': 1}})
    if result.modified_count != 1:
        _fail('provider_plan_scope_version_conflict')
    return scope
