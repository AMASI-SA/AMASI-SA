"""Internal PAUSED creation authority; no provider calls and no fence release.

Claim and revoke linearize on ONE owner policy document. An acknowledged claim
is one local authorization, not replayable permission: the shared native core
must still acknowledge its exact proposal submission floor. Unknown Mongo or
provider acknowledgements retain the claim, fence, identity and all quota. Only
server-loaded original native proof can settle; never adopt matching manual ads.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import timedelta
from decimal import Decimal
import os
import re
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from . import google_ads_automation_authority as standing
from . import google_ads_automation_creation_contract as contract
from . import google_ads_management as management
from .google_ads_ai import PRODUCT_COLLECTION
from .google_ads_creation_inventory import PRODUCT_PROJECTION
from .google_ads_automation_creation_policy import CREATION_POLICIES, POLICY_FIELDS, creation_policy_snapshot
from .google_ads_campaign_builder import build_campaign_operations
from .google_ads_client import identifier, problem

CREATION_INTENTS = 'mezan_google_ads_automation_creation_intents_v1'
SERVICE_ACTOR_ID = standing.SERVICE_ACTOR_ID
_IDENTITY = re.compile(r'^google_create_[a-f0-9]{64}$')
_ID = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
_PENDING = frozenset({'executing', 'approved', 'submitted', 'verifying', 'uncertain'})
_MAX_LEDGER = 100
_MAX_HISTORY = 1000


def now(current=None):
    return standing._now(current)


def creation_execution_enabled():
    from .google_ads_automation_worker import worker_enabled
    return (standing.execution_enabled() and worker_enabled()
        and os.getenv('GOOGLE_ADS_AUTOMATION_CREATION_ENABLED', 'false').strip().lower() in {'1', 'true', 'yes', 'on'})


def _fail(code):
    raise problem(code, 'لم يثبت تفويض إنشاء حملة جوجل؛ تحقق دون إعادة إرسال.', 409)


def creation_source(intent):
    return {'kind': 'standing_creation', 'creation_identity': intent['creation_identity'],
        'intent_digest': intent['intent_digest'], 'policy_revision': intent['binding']['policy_revision'],
        'policy_digest': intent['binding']['policy_digest'], 'draft_digest': intent['binding']['draft_digest']}


def _identity(value):
    return isinstance(value, str) and _IDENTITY.fullmatch(value) is not None


def _entry_time(entry):
    # Unclaimed cancellation is a barrier, never a fabricated authorization.
    if entry.get('authorization_granted') is False:
        if entry.get('state') != 'no_campaign' or 'authorized_at' in entry:
            return None
        return contract._instant(entry.get('cancelled_at'))
    return contract._instant(entry.get('authorized_at'))


def _policy_ledger(policy):
    ledger = policy.get('_creation_ledger', {})
    generation = policy.get('_creation_generation', 0)
    if (not isinstance(ledger, dict) or len(ledger) > _MAX_LEDGER
        or type(generation) is not int or not 0 <= generation < 2**63 - 2):
        _fail('google_creation_state_unproved')
    for identity, entry in ledger.items():
        if (not _identity(identity) or not isinstance(entry, dict) or entry.get('creation_identity') != identity
            or entry.get('user_id') != policy.get('user_id') or entry.get('account_id') != policy.get('account_id')
            or entry.get('state') not in {'reserved', 'owned', 'no_campaign'}
            or not isinstance(entry.get('proposal_id'), str) or not _ID.fullmatch(entry['proposal_id'])
            or not isinstance(entry.get('claim_id'), str) or not _ID.fullmatch(entry['claim_id'])
            or _entry_time(entry) is None
            or contract._amount(entry.get('reservation_native'), zero=entry.get('authorization_granted') is False) is None
            or entry.get('authorization_granted') is False and entry.get('reservation_native') != '0'
            or entry.get('reservation_currency_code') != policy.get('currency_code')
            or entry.get('reserved_enable_budget_native') != '0'
            or entry.get('reservation_state') != {'reserved':'paused_reserved', 'owned':'paused_created', 'no_campaign':'no_campaign'}[entry['state']]):
            _fail('google_creation_state_unproved')
    claim = policy.get('_creation_claim')
    if claim is not None:
        if (not isinstance(claim, dict) or claim.get('creation_identity') not in ledger
            or ledger[claim['creation_identity']] != claim or claim.get('state') != 'reserved'):
            _fail('google_creation_state_unproved')
    return ledger, generation


def _bound(row, intent):
    """Freeze full native intent and operations independently of status/receipt."""
    try:
        proposal_id = row.get('proposal_id')
        if (not isinstance(proposal_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', proposal_id)
            or row.get('actor_id') != SERVICE_ACTOR_ID or row.get('provider') != 'google_ads'
            or row.get('action') != 'create_campaign' or row.get('campaign_id') is not None
            or row.get('before_digest') is not None or row.get('automation_source') != creation_source(intent)
            or row.get('confirmation_digest') != management.digest(management.immutable(row))
            or row.get('idempotency_key') != intent['creation_identity']
            or row.get('draft_id') != intent['creation_identity'] or type(row.get('draft_revision')) is not int
            or row['draft_revision'] < 1 or row.get('correlation_label') != 'MEZAN-' + proposal_id):
            _fail('google_creation_proposal_binding_invalid')
        operations = build_campaign_operations(intent['draft'], row['account_id'])
        campaign = [op['campaignOperation']['create'] for op in operations if 'campaignOperation' in op]
        if len(campaign) != 1 or campaign[0].get('status') != 'PAUSED':
            _fail('google_creation_proposal_binding_invalid')
        label = f"customers/{row['account_id']}/labels/-9000000000000"
        operations += [{'labelOperation': {'create': {'resourceName': label, 'name': row['correlation_label']}}},
            {'campaignLabelOperation': {'create': {'campaign': campaign[0]['resourceName'], 'label': label}}}]
        planned = row.get('planned')
        if (row.get('operations') != operations or not isinstance(planned, dict) or planned.get('initial_status') != 'PAUSED'
            or any(planned.get(key) != value for key, value in intent['draft'].items())):
            _fail('google_creation_proposal_binding_invalid')
    except (KeyError, TypeError, ValueError):
        _fail('google_creation_proposal_binding_invalid')


async def _saved_intent(db, scope, identity):
    if not _identity(identity):
        _fail('google_creation_intent_unproved')
    rows = await db[CREATION_INTENTS].find({**scope, 'creation_identity': identity}, {'_id': 0}).to_list(length=2)
    if len(rows) != 1 or not isinstance(rows[0].get('intent'), dict) or not isinstance(rows[0].get('choice'), dict):
        _fail('google_creation_intent_unproved')
    saved = rows[0]
    intent = saved['intent']
    try:
        identity_value = intent['binding']['identity']
        if (intent['creation_identity'] != identity or intent['intent_digest'] != contract._digest(intent['binding'])
            or identity != 'google_create_' + contract._digest(identity_value)
            or any(identity_value.get(key) != value for key, value in scope.items())
            or intent['binding']['draft_digest'] != contract._digest(intent['draft'])
            or intent.get('initial_status') != 'PAUSED' or intent.get('auto_enable_new') is not False
            or saved['choice'] != {key: identity_value[key] for key in ('product_id', 'template_id', 'template_version')}):
            _fail('google_creation_intent_unproved')
    except (KeyError, TypeError, ValueError):
        _fail('google_creation_intent_unproved')
    return saved


def _native_proof(row, evidence, policy, current):
    if not isinstance(evidence, dict):
        _fail('google_creation_native_context_unproved')
    meta, native = evidence.get('metadata'), evidence.get('native_context')
    earliest = contract._instant(evidence.get('observed_at'))
    if (not isinstance(meta, dict) or meta.get('identity_verified') is not True
        or meta.get('account_id') != policy.account_id
        or any(meta.get(key) != getattr(policy, key) for key in ('currency_code', 'time_zone'))
        or any(row.get(key) != getattr(policy, target) for key, target in (('currency', 'currency_code'), ('time_zone', 'time_zone')))
        or not isinstance(row.get('auth_epoch'), str) or not row['auth_epoch'] or evidence.get('auth_epoch') != row['auth_epoch']
        or earliest is None or not timedelta(0) <= current - earliest < timedelta(seconds=90)
        or not isinstance(native, dict) or contract._instant(native.get('observed_at')) != earliest):
        _fail('google_creation_native_context_unproved')
    contract._native_gate(policy, native, current)
    return native


async def read_google_creation_state(db, *, user_id, account_id, native_context, current=None,
        exclude_proposal_id=None, execution_claim_id=None):
    """Complete bounded private ledger/history; no adoption from native names."""
    current = now(current)
    scope = {'user_id': user_id, 'account_id': identifier(account_id)}
    policy = await db[CREATION_POLICIES].find_one(scope, {'_id': 0})
    if not policy:
        _fail('google_creation_state_unproved')
    typed = contract.GoogleAutomationCreationPolicy.model_validate(creation_policy_snapshot(policy))
    contract._native_gate(typed, native_context, current)
    ledger, _ = _policy_ledger(policy)
    rows = await db[management.PROPOSALS].find(scope, {'_id': 0}).to_list(length=_MAX_HISTORY + 1)
    fences = await db[management.FENCES].find({**scope, 'status': 'claimed'}, {'_id': 0}).to_list(length=2)
    if (len(rows) > _MAX_HISTORY or len(fences) > 1
        or any(not isinstance(row.get('proposal_id'), str) or not _ID.fullmatch(row['proposal_id'])
            or row.get('status') not in _PENDING | {'previewed', 'completed', 'failed'} for row in rows)):
        _fail('google_creation_history_unproved')
    own = next((row for row in rows if row.get('proposal_id') == exclude_proposal_id
        and row.get('execution_claim_id') == execution_claim_id and row.get('status') == 'approved'
        and row.get('submission_started') is False), None)
    self_fence = (own is not None and len(fences) == 1 and fences[0].get('proposal_id') == exclude_proposal_id
        and fences[0].get('execution_claim_id') == execution_claim_id)
    pending = policy.get('_creation_claim') is not None or any(
        row.get('status') in _PENDING and not (self_fence and row == own) for row in rows)
    pending = pending or any(not (self_fence and fence.get('proposal_id') == exclude_proposal_id
        and fence.get('execution_claim_id') == execution_claim_id) for fence in fences)
    today = current.astimezone(ZoneInfo(typed.time_zone)).date()
    created_today = paused = 0
    reserved = Decimal(0)
    native_rows = native_context.get('campaigns')
    if not isinstance(native_rows, list):
        _fail('google_creation_native_context_unproved')
    for identity, entry in ledger.items():
        authorized = _entry_time(entry)
        if authorized > current:
            _fail('google_creation_state_unproved')
        created_today += authorized.astimezone(ZoneInfo(typed.time_zone)).date() == today
        if entry['state'] == 'no_campaign':
            continue
        remains_paused = True
        if entry['state'] == 'owned':
            saved = await _saved_intent(db, scope, identity)
            original = next((row for row in rows if row.get('proposal_id') == entry['proposal_id']), None)
            if not contract.creation_campaign_ownership_verified(entry, intent=saved['intent'], user_id=user_id,
                    account_id=scope['account_id'], campaign_id=entry.get('created_campaign_id'), proposal=original):
                _fail('google_creation_ownership_unproved')
            matching = [row for row in native_rows if isinstance(row, dict) and row.get('campaign_id') == entry['created_campaign_id']]
            if (len(matching) == 1 and matching[0].get('status') in {'ENABLED', 'REMOVED'}
                and matching[0].get('account_id') == scope['account_id']
                and matching[0].get('source') == 'google_ads_api'
                and all(matching[0].get(key) == getattr(typed, key) for key in ('currency_code', 'time_zone'))
                and contract._fresh(matching[0].get('observed_at'), current, 90)):
                remains_paused = False
        if remains_paused:
            paused += 1
            reserved += Decimal(entry['reservation_native'])
    return {**scope, 'complete': True, 'observed_at': current.isoformat(), 'created_today': created_today,
        'paused_created': paused, 'total_created': len(ledger), 'pending_write': bool(pending),
        'reserved_creation_budget_native': str(reserved), 'reserved_enable_budget_native': '0'}


async def _linearize_creation_claim(collection, query, update):
    return await collection.update_one(query, update)


async def authorize_google_creation_claim(db, *, user_id, account_id, proposal_id, execution_claim_id,
        native_evidence, current=None):
    """Internal verified-client input only; every failure holds the shared fence."""
    phase = {'attempted': False}
    try:
        if not creation_execution_enabled():
            _fail('google_creation_execution_disabled')
        return await asyncio.wait_for(_authorize(db, user_id=user_id, account_id=account_id, proposal_id=proposal_id,
            execution_claim_id=execution_claim_id, native_evidence=native_evidence, current=current, phase=phase), timeout=15)
    except asyncio.CancelledError as exc:
        exc.preserve_execution_fence = True
        exc.automatic_retry_allowed = False
        exc.authorization_cas_attempted = phase['attempted']
        raise
    except Exception as exc:
        if not isinstance(exc, HTTPException):
            exc = problem('google_creation_authorization_unconfirmed', 'تفويض الإنشاء غير مؤكد؛ تحقق دون إعادة إرسال.', 503)
        if isinstance(exc.detail, dict):
            exc.detail.update(preserve_execution_fence=True, automatic_retry_allowed=False, authorization_cas_attempted=phase['attempted'])
        raise exc from None


async def _authorize(db, *, user_id, account_id, proposal_id, execution_claim_id, native_evidence, current, phase, _guard=None):
    start = now(current)
    scope = {'user_id': user_id, 'account_id': identifier(account_id)}
    if (not isinstance(user_id, str) or not user_id or not isinstance(proposal_id, str) or not _ID.fullmatch(proposal_id)
        or not isinstance(execution_claim_id, str) or not _ID.fullmatch(execution_claim_id)):
        _fail('google_creation_execution_claim_unproved')
    await standing._require_active_owner(db, user_id)
    connection = await db.mezan_integrations_v2.find_one({'user_id': user_id, 'provider': 'google_ads',
        'connection_status': 'connected', 'connection_provenance': 'api_connection'}, {'_id': 0, 'provider': 1})
    if not connection or scope['account_id'] not in {item['account_id'] for item in await management.available_accounts(db, user_id)}:
        _fail('google_creation_account_unavailable')
    row_scope = {**scope, 'proposal_id': proposal_id, 'execution_claim_id': execution_claim_id, 'status': 'approved', 'submission_started': False}
    fence_scope = {**scope, 'proposal_id': proposal_id, 'execution_claim_id': execution_claim_id, 'status': 'claimed'}
    row = await db[management.PROPOSALS].find_one(row_scope, {'_id': 0})
    policy = await db[CREATION_POLICIES].find_one(scope, {'_id': 0})
    if not row or not policy or not await db[management.FENCES].find_one(fence_scope, {'_id': 0}):
        _fail('google_creation_execution_claim_unproved')
    source = row.get('automation_source') or {}
    saved = await _saved_intent(db, scope, source.get('creation_identity'))
    intent = saved['intent']
    _bound(row, intent)
    typed = contract.GoogleAutomationCreationPolicy.model_validate(creation_policy_snapshot(policy))
    if (policy.get('updated_by') != user_id or policy.get('configuration_blockers') or typed.auto_enable_new
        or saved.get('proposal_id') not in (None, proposal_id)):
        _fail('google_creation_policy_changed')
    ledger, generation = _policy_ledger(policy)
    if _guard is not None:
        snapshot, command = _guard
        if (policy['revision'] != command.expected_revision or generation != command.expected_generation
            or contract._digest(creation_policy_snapshot(policy)) != command.expected_policy_digest
            or management.digest(ledger) != command.expected_ledger_digest):
            _fail('google_creation_policy_claim_changed')
    if policy.get('_creation_claim') is not None or intent['creation_identity'] in ledger:
        _fail('google_creation_claim_pending')
    native = _native_proof(row, native_evidence, typed, start)
    products = await db[PRODUCT_COLLECTION].find({'user_id': user_id, '$or': [
        {'salla_product_id': saved['choice']['product_id']}, {'mezan_product_id': saved['choice']['product_id']}]}, PRODUCT_PROJECTION).to_list(length=2)
    if len(products) != 1:
        _fail('google_creation_catalog_ambiguous')
    draft = await db[management.DRAFTS].find_one({'user_id': user_id, 'draft_id': row['draft_id'], 'revision': row['draft_revision']}, {'_id': 0})
    if not draft or draft.get('draft') != intent['draft']:
        _fail('google_creation_draft_changed')
    state = await read_google_creation_state(db, **scope, native_context=native, current=start,
        exclude_proposal_id=proposal_id, execution_claim_id=execution_claim_id)
    if _guard is None:
        await db[CREATION_POLICIES].create_index([('user_id', 1), ('account_id', 1)], unique=True, name='google_creation_tenant_account')
    # Last awaits precede the final persisted owner and clock checks. The policy
    # CAS below, not a fresh-read promise, orders claims against owner revocation.
    fresh_row = await db[management.PROPOSALS].find_one(row_scope, {'_id': 0})
    if fresh_row != row or not await db[management.FENCES].find_one(fence_scope, {'_id': 0}):
        _fail('google_creation_execution_claim_unproved')
    # Re-read the canonical product after slow history/index/fence work. A fresh
    # timestamp on an old in-memory product cannot authorize a changed catalog.
    products = await db[PRODUCT_COLLECTION].find({'user_id': user_id, '$or': [
        {'salla_product_id': saved['choice']['product_id']}, {'mezan_product_id': saved['choice']['product_id']}]}, PRODUCT_PROJECTION).to_list(length=2)
    if len(products) != 1:
        _fail('google_creation_catalog_ambiguous')
    await standing._require_active_owner(db, user_id)
    cas_time = now()
    expiry = contract._instant(row.get('expires_at'))
    if expiry is None or expiry <= cas_time:
        _fail('google_creation_proposal_expired')
    native = _native_proof(row, native_evidence, typed, cas_time)
    contract.revalidate_google_creation_intent(intent, policy=creation_policy_snapshot(policy), template=policy.get('template'),
        product=products[0], native_context=native, creation_state=state, choice=saved['choice'], current=cas_time)
    if not creation_execution_enabled():
        _fail('google_creation_execution_disabled')
    entry = {**scope, 'provider': 'google_ads', 'creation_identity': intent['creation_identity'], 'claim_id': execution_claim_id,
        'execution_claim_id': execution_claim_id, 'proposal_id': proposal_id, 'state': 'reserved',
        'authorized_at': cas_time.isoformat(), 'account_local_date': cas_time.astimezone(ZoneInfo(typed.time_zone)).date().isoformat(),
        'time_zone': typed.time_zone, 'policy_revision': intent['binding']['policy_revision'],
        'policy_digest': intent['binding']['policy_digest'], 'intent_digest': intent['intent_digest'],
        'draft_digest': intent['binding']['draft_digest'], 'confirmation_digest': row['confirmation_digest'],
        'operations_digest': management.digest(row['operations']), 'reservation_native': typed.budget_native,
        'reservation_currency_code': typed.currency_code, 'reservation_state': 'paused_reserved', 'reserved_enable_budget_native': '0',
        'native_inventory_digest': management.digest(native), 'native_observed_at': native['observed_at'],
        'native_account_id': scope['account_id'], 'native_currency_code': typed.currency_code,
        'native_time_zone': typed.time_zone}
    updated_ledger = {**ledger, intent['creation_identity']: entry}
    query = {**scope, **{key: policy.get(key) for key in POLICY_FIELDS if key not in scope}, 'template': policy.get('template'),
        'enabled': True, '_creation_claim': None, '_creation_generation': generation if '_creation_generation' in policy else {'$exists': False},
        '_creation_ledger': ledger if '_creation_ledger' in policy else {'$exists': False},
        'updated_by': user_id, 'configuration_blockers': policy.get('configuration_blockers')}
    phase['attempted'] = True
    if _guard is None:
        from .google_ads_automation_budget_guard import (PausedCreationReservation,
            probe_google_budget_guard_transactions, run_google_budget_guard_transaction)
        capability = await probe_google_budget_guard_transactions(db)
        command = PausedCreationReservation(proposal_id, execution_claim_id, policy['revision'], generation,
            contract._digest(creation_policy_snapshot(policy)), management.digest(ledger), native_evidence)
        result = await run_google_budget_guard_transaction(db, **scope,
            operation_id='paused_reserve_' + management.digest({'proposal_id': proposal_id, 'claim_id': execution_claim_id}),
            capability=capability, planner=lambda snapshot: command)
        if not creation_execution_enabled():
            _fail('google_creation_execution_disabled')
        # Commit latency cannot renew the earliest provider snapshot. A stale
        # postcommit proof keeps the reservation but returns no send permission.
        _native_proof(row, native_evidence, typed, now())
        return result
    changed = await _linearize_creation_claim(db[CREATION_POLICIES], query,
        {'$set': {'_creation_claim': entry, '_creation_ledger': updated_ledger}, '$inc': {'_creation_generation': 1}})
    if not changed.matched_count:
        _fail('google_creation_policy_claim_changed')
    if not creation_execution_enabled():
        _fail('google_creation_execution_disabled')
    return {'authorized': True, 'claim_id': execution_claim_id, 'proposal_id': proposal_id,
        'creation_identity': intent['creation_identity'], 'submission_floor_required': True, 'provider_writes': 0}


def _verified_report(row):
    report = row.get('verification')
    names = row.get('resource_names')
    if (not isinstance(report, dict) or report.get('readback_independent') is not True
        or report.get('mismatches') != [] or report.get('coverage_gaps') != []
        or not isinstance(names, list) or not names or any(not isinstance(name, str) for name in names)
        or not isinstance(report.get('verified_resources'), list)
        or any(not isinstance(name, str) for name in report['verified_resources'])
        or Counter(report['verified_resources']) != Counter(names)):
        return False
    return row.get('accepted_receipt') is not True or row.get('accepted_operations_digest') == management.digest(row.get('operations'))


async def settle_google_creation_claim(db, *, user_id, account_id, claim_id, current=None):
    """Read original proof, then ACK atomic ledger/claim settlement; never release fence."""
    try:
        return await asyncio.wait_for(_settle(db, user_id=user_id, account_id=account_id, claim_id=claim_id, current=current), timeout=15)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if isinstance(exc, HTTPException): raise
        raise problem('google_creation_settlement_unconfirmed', 'تسوية الإنشاء غير مؤكدة؛ احتفظ بالعملية دون إعادة إرسال.', 503) from None


async def _linearize_creation_settlement(collection, query, update):
    return await collection.update_one(query, update)


async def _cancel_unclaimed(db, *, scope, policy, ledger, generation, claim_id, current):
    """Generation CAS excludes delayed authorizers before any native unlock.

    Absence observed in a read grants nothing. This acknowledged write consumes
    the bounded identity and daily/total slot and changes the exact generation
    required by every old authorization attempt. Unknown ACK remains fenced.
    """
    if (not isinstance(claim_id, str) or not _ID.fullmatch(claim_id)
        or policy.get('_creation_claim') is not None or len(ledger) >= _MAX_LEDGER):
        _fail('google_creation_settlement_unproved')
    rows = await db[management.PROPOSALS].find({**scope, 'execution_claim_id': claim_id}, {'_id': 0}).to_list(length=2)
    if len(rows) != 1:
        _fail('google_creation_settlement_unproved')
    row = rows[0]
    if (row.get('status') != 'failed' or row.get('submission_started') is not False
        or row.get('safe_error') != 'execution_cancelled_before_dispatch'
        or row.get('provider_write_reached') is not False
        or row.get('accepted_receipt') is True or row.get('resource_names') not in (None, [])):
        _fail('google_creation_settlement_unproved')
    saved = await _saved_intent(db, scope, (row.get('automation_source') or {}).get('creation_identity'))
    intent = saved['intent']
    _bound(row, intent)
    identity = intent['creation_identity']
    if identity in ledger or saved.get('proposal_id') not in (None, row['proposal_id']):
        _fail('google_creation_settlement_unproved')
    if not await db[management.FENCES].find_one({**scope, 'proposal_id': row['proposal_id'],
            'execution_claim_id': claim_id, 'status': 'claimed'}, {'_id': 0}):
        _fail('google_creation_settlement_unproved')
    entry = {**scope, 'provider': 'google_ads', 'creation_identity': identity, 'claim_id': claim_id,
        'execution_claim_id': claim_id, 'proposal_id': row['proposal_id'], 'state': 'no_campaign',
        'authorization_granted': False, 'cancelled_at': current.isoformat(), 'settled_at': current.isoformat(),
        'policy_revision': intent['binding']['policy_revision'], 'policy_digest': intent['binding']['policy_digest'],
        'intent_digest': intent['intent_digest'], 'draft_digest': intent['binding']['draft_digest'],
        'confirmation_digest': row['confirmation_digest'], 'operations_digest': management.digest(row['operations']),
        'reservation_native': '0', 'planned_budget_native': intent['draft']['budget_native'], 'reservation_currency_code': row['currency'],
        'reservation_state': 'no_campaign', 'reserved_enable_budget_native': '0', 'verified': False}
    # Same document and whole-ledger CAS is the cancellation/authorization barrier.
    changed = await _linearize_creation_settlement(db[CREATION_POLICIES], {**scope, '_creation_claim': None,
        '_creation_generation': generation if '_creation_generation' in policy else {'$exists': False},
        '_creation_ledger': ledger if '_creation_ledger' in policy else {'$exists': False}},
        {'$set': {'_creation_claim': None, '_creation_ledger': {**ledger, identity: entry}}, '$inc': {'_creation_generation': 1}})
    if not changed.matched_count:
        _fail('google_creation_settlement_unproved')
    return {'settled': True, 'released': True, 'verified_ownership': False, 'creation_identity': identity,
        'created_campaign_id': None, 'claim_id': claim_id, 'quota_refunded': False, 'provider_writes': 0}


async def _settle_snapshot(db, *, user_id, account_id, claim_id, current):
    current = now(current)
    scope = {'user_id': user_id, 'account_id': identifier(account_id)}
    policy = await db[CREATION_POLICIES].find_one(scope, {'_id': 0})
    if not policy:
        _fail('google_creation_settlement_unproved')
    ledger, generation = _policy_ledger(policy)
    entries = [entry for entry in ledger.values() if entry['claim_id'] == claim_id]
    if not entries:
        return await _cancel_unclaimed(db, scope=scope, policy=policy, ledger=ledger, generation=generation, claim_id=claim_id, current=current)
    if len(entries) != 1:
        _fail('google_creation_settlement_unproved')
    entry = entries[0]
    claim = policy.get('_creation_claim')
    if claim != entry and not (claim is None and entry['state'] in {'owned', 'no_campaign'}):
        _fail('google_creation_settlement_unproved')
    saved = await _saved_intent(db, scope, entry['creation_identity'])
    row = await db[management.PROPOSALS].find_one({**scope, 'proposal_id': entry['proposal_id'], 'execution_claim_id': claim_id}, {'_id': 0})
    if not row:
        _fail('google_creation_settlement_unproved')
    _bound(row, saved['intent'])
    if (row['confirmation_digest'] != entry.get('confirmation_digest')
        or management.digest(row['operations']) != entry.get('operations_digest')
        or saved['intent']['intent_digest'] != entry.get('intent_digest')
        or saved['intent']['binding']['policy_digest'] != entry.get('policy_digest')
        or saved['intent']['binding']['policy_revision'] != entry.get('policy_revision')):
        _fail('google_creation_settlement_unproved')
    owned = {**entry, 'state': 'owned', 'reservation_state': 'paused_created', 'verified': True,
        'origin': 'google_automation_creation_v1', 'native_correlation_verified': True, 'initial_status': 'PAUSED',
        'correlation_label': row.get('correlation_label'), 'created_campaign_id': row.get('created_campaign_id')}
    completed = _verified_report(row) and contract.creation_campaign_ownership_verified(owned, intent=saved['intent'],
        user_id=user_id, account_id=scope['account_id'], campaign_id=row.get('created_campaign_id'), proposal=row)
    cancelled = (row.get('status') == 'failed' and row.get('submission_started') is False
        and row.get('safe_error') == 'execution_cancelled_before_dispatch' and row.get('provider_write_reached') is False)
    rejection = row.get('rejection') or {}
    rejected = (row.get('status') == 'failed' and row.get('submission_started') is True and row.get('provider_write_reached') is False
        and isinstance(rejection, dict) and type(rejection.get('provider_status')) is int and 400 <= rejection['provider_status'] < 500)
    stopped = standing.stopped_before_http_proven(row, current)
    if stopped and contract._instant(row.get('dispatch_cancelled_before_http_at')) < contract._instant(entry['authorized_at']):
        stopped = False
    no_receipt = row.get('accepted_receipt') is not True and row.get('resource_names') in (None, [])
    if not (completed or no_receipt and (cancelled or rejected or stopped)):
        _fail('google_creation_settlement_unproved')
    terminal = owned if completed else {**entry, 'state': 'no_campaign', 'reservation_state': 'no_campaign', 'verified': False}
    terminal.update(settled_at=entry.get('settled_at') or current.isoformat())
    if claim is not None:
        changed = await _linearize_creation_settlement(db[CREATION_POLICIES], {**scope, '_creation_claim': claim, '_creation_ledger': ledger,
            '_creation_generation': generation}, {'$set': {'_creation_claim': None,
                '_creation_ledger': {**ledger, entry['creation_identity']: terminal}}, '$inc': {'_creation_generation': 1}})
        if not changed.matched_count:
            _fail('google_creation_settlement_unproved')
    elif terminal != entry:
        _fail('google_creation_settlement_unproved')
    return {'settled': True, 'released': True, 'verified_ownership': bool(completed), 'creation_identity': entry['creation_identity'],
        'created_campaign_id': terminal.get('created_campaign_id'), 'claim_id': claim_id, 'quota_refunded': False, 'provider_writes': 0}


async def _settle(db, *, user_id, account_id, claim_id, current):
    from .google_ads_automation_budget_guard import (PausedCreationSettlement,
        probe_google_budget_guard_transactions, run_google_budget_guard_transaction)
    import uuid
    capability = await probe_google_budget_guard_transactions(db)
    return await run_google_budget_guard_transaction(db, user_id=user_id, account_id=identifier(account_id),
        operation_id='paused_settle_' + uuid.uuid4().hex, capability=capability,
        planner=lambda snapshot: PausedCreationSettlement(claim_id))


async def _guard_paused_creation_command(db, *, session, snapshot, command, operation_id, current):
    """Internal same-session boundary; never called by an HTTP owner payload."""
    from .google_ads_automation_budget_guard import (PausedCreationReservation, MAX_ALLOCATIONS,
        thaw_snapshot, _fail as guard_fail)
    from .google_ads_paused_creation_budget import SessionDatabase, account_exposure, allocation_id
    scope = {'user_id': snapshot.user_id, 'account_id': snapshot.account_id}
    old = thaw_snapshot(snapshot.guard)
    policy = thaw_snapshot(snapshot.creation_policy)
    if policy is None:
        guard_fail('google_budget_guard_creation_policy_missing')
    ledger, _ = _policy_ledger(policy)
    view = SessionDatabase(db, session)
    if isinstance(command, PausedCreationReservation):
        if (old['active_claim'] is not None or len(old['allocations']) >= MAX_ALLOCATIONS
            or (snapshot.standing_policy or {}).get('_dispatch_claim') is not None):
            guard_fail('google_budget_guard_allocation_pending')
        native = command.native_evidence.get('native_context')
        if not isinstance(native, dict):
            _fail('google_creation_native_context_unproved')
        exposure = account_exposure(snapshot, native, policy, current)
        from .google_ads_automation_budget_guard import _money
        amount = _money(policy.get('budget_native'))
        if exposure['total'] + amount > exposure['limit']:
            guard_fail('google_budget_guard_account_limit')
        result = await _authorize(view, **scope, proposal_id=command.proposal_id,
            execution_claim_id=command.execution_claim_id, native_evidence=command.native_evidence,
            current=current, phase={'attempted': False}, _guard=(snapshot, command))
        after = await db[CREATION_POLICIES].find_one(scope, {'_id': 0}, session=session)
        entry = after.get('_creation_claim') if after else None
        if (not isinstance(entry, dict) or entry.get('execution_claim_id') != command.execution_claim_id
            or entry.get('proposal_id') != command.proposal_id or entry.get('state') != 'reserved'):
            guard_fail('google_budget_guard_creation_binding_unproved')
        key = allocation_id(entry['creation_identity'], command.execution_claim_id)
        if key in old['allocations']:
            guard_fail('google_budget_guard_identity_consumed')
        allocation = {**scope, 'kind': 'paused_creation', 'allocation_id': key, 'operation_id': operation_id,
            'creation_identity': entry['creation_identity'], 'proposal_id': command.proposal_id,
            'execution_claim_id': command.execution_claim_id, 'currency_code': policy['currency_code'],
            'original_creation_budget_native': policy['budget_native'], 'budget_native': policy['budget_native'],
            'state': 'reserved', 'created_at': current.isoformat(), 'creation_entry_digest': management.digest(entry),
            'native_inventory_digest': entry['native_inventory_digest'], 'native_observed_at': entry['native_observed_at'],
            'creation_policy_revision': policy['revision'], 'creation_policy_digest': command.expected_policy_digest,
            'commitment': {'observed_native_and_holds': str(exposure['total']),
                'resulting_average_commitment_native': str(exposure['total'] + amount),
                'limit_native': str(exposure['limit']), 'observed_at': native['observed_at'],
                'native_amount_drift_campaign_ids': exposure['native_amount_drift_campaign_ids'],
                'external_writer_serialized': False}}
        active = {key: value for key, value in allocation.items()
            if key in {'allocation_id', 'operation_id', 'proposal_id', 'execution_claim_id'}}
        return {**old['allocations'], key: allocation}, active, result
    entries = [entry for entry in ledger.values() if entry['claim_id'] == command.execution_claim_id]
    if len(entries) > 1:
        guard_fail('google_budget_guard_creation_binding_unproved')
    original = entries[0] if entries else None
    key = allocation_id(original['creation_identity'], command.execution_claim_id) if original else None
    allocation = old['allocations'].get(key) if key else None
    if original and original['state'] in {'reserved', 'owned'}:
        if (not isinstance(allocation, dict) or allocation.get('kind') != 'paused_creation'
            or allocation.get('creation_entry_digest') != management.digest(original)
            or allocation.get('execution_claim_id') != command.execution_claim_id):
            guard_fail('google_budget_guard_legacy_creation_unmapped')
    if original and original['state'] == 'reserved':
        active = old['active_claim']
        if (not isinstance(active, dict) or active.get('allocation_id') != key
            or active.get('execution_claim_id') != command.execution_claim_id):
            guard_fail('google_budget_guard_creation_binding_unproved')
    elif old['active_claim'] is not None:
        guard_fail('google_budget_guard_allocation_pending')
    result = await _settle_snapshot(view, **scope, claim_id=command.execution_claim_id, current=current)
    after = await db[CREATION_POLICIES].find_one(scope, {'_id': 0}, session=session)
    next_ledger, _ = _policy_ledger(after)
    terminal = [entry for entry in next_ledger.values() if entry['claim_id'] == command.execution_claim_id]
    if len(terminal) != 1:
        guard_fail('google_budget_guard_creation_binding_unproved')
    terminal = terminal[0]
    if allocation is None:
        # A positively known unclaimed cancellation consumes generation/identity
        # atomically with the epoch. It invents no monetary reservation.
        if terminal.get('authorization_granted') is not False or terminal['state'] != 'no_campaign':
            guard_fail('google_budget_guard_legacy_creation_unmapped')
        return old['allocations'], old['active_claim'], result
    allocation = {**allocation, 'creation_entry_digest': management.digest(terminal),
        'settled_at': terminal['settled_at']}
    if terminal['state'] == 'owned':
        proposal = await db[management.PROPOSALS].find_one({**scope, 'proposal_id': terminal['proposal_id'],
            'execution_claim_id': command.execution_claim_id}, {'_id': 0}, session=session)
        resources = proposal.get('resource_names') if proposal else None
        budgets = [name for name in resources if isinstance(name, str)
            and re.fullmatch(rf'customers/{scope["account_id"]}/campaignBudgets/[0-9]+', name)] if isinstance(resources, list) else []
        if len(budgets) != 1 or not _verified_report(proposal):
            guard_fail('google_budget_guard_creation_binding_unproved')
        allocation.update(state='native_paused', campaign_id=terminal['created_campaign_id'],
            budget_resource=budgets[0], initial_status='PAUSED',
            settlement_lineage_digest=management.digest({'proposal_id': proposal['proposal_id'],
                'confirmation_digest': proposal['confirmation_digest'], 'operations_digest': management.digest(proposal['operations']),
                'verification': proposal['verification'], 'resource_names': resources}))
    elif terminal['state'] == 'no_campaign':
        allocation.update(state='settled', outcome='positively_known_no_campaign')
    else:
        guard_fail('google_budget_guard_creation_binding_unproved')
    return {**old['allocations'], key: allocation}, None, result


async def revalidate_committed_google_creation_claim(db, *, user_id, account_id, proposal_id,
        execution_claim_id, native_evidence):
    """Fresh majority reads after reservation, before the native submission floor.

    These reads detect intervening drift; they do NOT serialize canonical-product,
    owner, OAuth or external Google writers after the final read. The shared
    policy-epoch/native-floor migration remains a separate required boundary.
    """
    from pymongo import ReadPreference
    from pymongo.read_concern import ReadConcern
    from . import google_ads_automation_budget_guard as guard
    from .google_ads_paused_creation_budget import SessionDatabase, account_exposure, allocation_id
    scope = {'user_id': user_id, 'account_id': identifier(account_id)}
    phase = {'commit_attempted': False}

    async def read_current():
        session = await db.client.start_session()
        try:
            live = db.with_options(read_preference=ReadPreference.PRIMARY, read_concern=ReadConcern('majority'))
            view = SessionDatabase(live, session)
            await standing._require_active_owner(view, user_id)
            if not creation_execution_enabled():
                _fail('google_creation_execution_disabled')
            policy = await view[CREATION_POLICIES].find_one(scope, {'_id': 0})
            if not policy:
                _fail('google_creation_policy_changed')
            ledger, _ = _policy_ledger(policy)
            entry = policy.get('_creation_claim')
            if (not isinstance(entry, dict) or entry.get('state') != 'reserved'
                or entry.get('execution_claim_id') != execution_claim_id or entry.get('proposal_id') != proposal_id
                or ledger.get(entry.get('creation_identity')) != entry):
                _fail('google_creation_execution_claim_unproved')
            row = await view[management.PROPOSALS].find_one({**scope, 'proposal_id': proposal_id,
                'execution_claim_id': execution_claim_id, 'status': 'approved', 'submission_started': False}, {'_id': 0})
            if not row or not await view[management.FENCES].find_one({**scope, 'proposal_id': proposal_id,
                'execution_claim_id': execution_claim_id, 'status': 'claimed'}, {'_id': 0}):
                _fail('google_creation_execution_claim_unproved')
            saved = await _saved_intent(view, scope, entry['creation_identity'])
            _bound(row, saved['intent'])
            typed = contract.GoogleAutomationCreationPolicy.model_validate(creation_policy_snapshot(policy))
            if (row['confirmation_digest'] != entry.get('confirmation_digest')
                or management.digest(row['operations']) != entry.get('operations_digest')
                or policy.get('updated_by') != user_id or policy.get('configuration_blockers')
                or policy['revision'] != entry['policy_revision']
                or contract._digest(typed.model_dump(mode='json')) != entry['policy_digest']):
                _fail('google_creation_policy_changed')
            native = _native_proof(row, native_evidence, typed, now())
            if (management.digest(native) != entry.get('native_inventory_digest')
                or native.get('observed_at') != entry.get('native_observed_at')):
                _fail('google_creation_native_context_unproved')
            products = await view[PRODUCT_COLLECTION].find({'user_id': user_id, '$or': [
                {'salla_product_id': saved['choice']['product_id']},
                {'mezan_product_id': saved['choice']['product_id']}]}, PRODUCT_PROJECTION).to_list(length=2)
            draft = await view[management.DRAFTS].find_one({'user_id': user_id,
                'draft_id': row['draft_id'], 'revision': row['draft_revision']}, {'_id': 0})
            if len(products) != 1 or not draft or draft.get('draft') != saved['intent']['draft']:
                _fail('google_creation_draft_changed')
            state = await read_google_creation_state(view, **scope, native_context=native, current=now(),
                exclude_proposal_id=proposal_id, execution_claim_id=execution_claim_id)
            rows = await view[management.PROPOSALS].find(scope, {'_id': 0, 'proposal_id': 1,
                'execution_claim_id': 1, 'status': 1}).to_list(length=_MAX_HISTORY + 1)
            fences = await view[management.FENCES].find({**scope, 'status': 'claimed'}, {'_id': 0}).to_list(length=2)
            if (len(rows) > _MAX_HISTORY or len(fences) != 1
                or fences[0].get('proposal_id') != proposal_id or fences[0].get('execution_claim_id') != execution_claim_id
                or any(item.get('status') in _PENDING and (item.get('proposal_id') != proposal_id
                    or item.get('execution_claim_id') != execution_claim_id) for item in rows)):
                _fail('google_creation_history_unproved')
            # Revalidate this already-reserved identity, not an additional quota
            # slot. Only its exact proved claim is subtracted from current state.
            today = now().astimezone(ZoneInfo(typed.time_zone)).date()
            state.update(pending_write=False, total_created=state['total_created']-1,
                paused_created=state['paused_created']-1,
                created_today=state['created_today']-(_entry_time(entry).astimezone(ZoneInfo(typed.time_zone)).date()==today))
            contract.revalidate_google_creation_intent(saved['intent'], policy=creation_policy_snapshot(policy),
                template=policy.get('template'), product=products[0], native_context=native,
                creation_state=state, choice=saved['choice'], current=now())
            record = await view[guard.BUDGET_GUARDS].find_one({'_id': guard._guard_id(scope)}, {'_id': 1,
                **{key: 1 for key in ('user_id','account_id','protocol_version','epoch','allocations','active_claim')} })
            if not record:
                _fail('google_creation_execution_claim_unproved')
            guard._validate_guard(record, scope)
            key = allocation_id(entry['creation_identity'], execution_claim_id)
            active = record.get('active_claim')
            allocation = record['allocations'].get(key)
            if (not isinstance(active, dict) or active.get('allocation_id') != key
                or active.get('execution_claim_id') != execution_claim_id or not isinstance(allocation, dict)
                or allocation.get('creation_entry_digest') != management.digest(entry)):
                _fail('google_creation_execution_claim_unproved')
            standing_policy = await view[guard.AUTOMATION_POLICIES].find(scope, {'_id': 0}).to_list(length=2)
            if len(standing_policy) > 1:
                _fail('google_creation_policy_changed')
            snapshot = guard.BudgetGuardSnapshot(user_id, scope['account_id'], record,
                standing_policy[0] if standing_policy else None, policy)
            exposure = account_exposure(snapshot, native, policy, now())
            if exposure['total'] > exposure['limit']:
                _fail('google_creation_account_budget_changed')
            connection = await view.mezan_integrations_v2.find_one({**{'user_id':user_id}, 'provider':'google_ads',
                'connection_status':'connected','connection_provenance':'api_connection'}, {'_id':0,'provider':1})
            if not connection or scope['account_id'] not in {item['account_id'] for item in await management.available_accounts(view,user_id)}:
                _fail('google_creation_account_unavailable')
            await standing._require_active_owner(view, user_id)
            _native_proof(row, native_evidence, typed, now())
            if not creation_execution_enabled():
                _fail('google_creation_execution_disabled')
            return {'revalidated': True, 'claim_id': execution_claim_id, 'provider_writes': 0,
                'shared_metadata_writers_serialized': False, 'submission_floor_serialized': False}
        finally:
            await guard._end_session(session)

    try:
        return await guard._retained_operation(db, scope,
            'paused_revalidate_' + management.digest({'proposal_id': proposal_id, 'claim_id': execution_claim_id}),
            'read', read_current, guard.RECOVERY_TIMEOUT_SECONDS, phase)
    except asyncio.CancelledError as exc:
        exc.preserve_execution_fence = True
        exc.automatic_retry_allowed = False
        raise
    except Exception as exc:
        if not isinstance(exc, HTTPException):
            exc = problem('google_creation_dispatch_revalidation_unconfirmed', 'تعذر تأكيد بيانات الإنشاء قبل الإرسال؛ يبقى الحجز للتحقق.', 503)
        if isinstance(exc.detail, dict):
            exc.detail.update(preserve_execution_fence=True, automatic_retry_allowed=False)
        raise exc from None
