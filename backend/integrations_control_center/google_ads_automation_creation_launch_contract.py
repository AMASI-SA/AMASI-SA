"""Pure future launch preview. NO storage, provider calls, claims or execution.

All inputs are loaded by a future trusted server authority, never accepted as
client/model proof. ALL_CAMPAIGNS requires a complete unfiltered, unsegmented
native inventory. Legacy configuration flags alone are explicitly insufficient;
only the new trusted read bundle can establish current product/configuration
evidence. Neither form proves stop provenance or grants launch authority.
The allocation/standing-policy absence snapshots need a common transactional
fence before actual execution; this pure function supplies no such guarantee.
guard_epoch binds a read-only future shared account barrier snapshot; its presence
never establishes that policy writers participate in the required protocol.
Current creation policy routes deliberately reject auto_enable_new=True. This
module does not add activation, acknowledgements, receipts or runtime support.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re

from pydantic import ValidationError

from . import google_ads_automation_creation_contract as creation


class CreationLaunchContractError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise CreationLaunchContractError(code)


def _money(value):
    if not isinstance(value, str) or re.fullmatch(r'(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,6})?', value) is None:
        return None
    return creation._amount(value)


def _activated(policy, current):
    at = creation._instant(policy.get('activated_at'))
    return (policy.get('enabled') is True and type(policy.get('revision')) is int and policy['revision'] > 0
        and type(policy.get('activation_version')) is int and policy['activation_version'] == 1
        and type(policy.get('activation_revision')) is int and policy['activation_revision'] == policy['revision']
        and policy.get('activated_by') == policy.get('user_id') and at is not None and at <= current)


def _scope(row, policy):
    return isinstance(row, dict) and all(row.get(key) == policy[key] for key in ('user_id', 'account_id'))


def _inventory(native, policy, current):
    if (not _scope(native, policy) or native.get('source') != 'google_ads_api'
        or native.get('identity_verified') is not True or native.get('complete') is not True
        or native.get('purchase_value_bidding_verified') is not True
        or native.get('segmented') is not False or native.get('has_more') is not False
        or native.get('inventory_scope') != 'ALL_CAMPAIGNS'
        or not creation._fresh(native.get('observed_at'), current, 90)
        or any(native.get(key) != policy[key] for key in ('currency_code', 'time_zone'))):
        _fail('launch_native_inventory_unproved')
    rows = native.get('campaigns')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
        _fail('launch_native_inventory_unproved')
    campaigns, budgets = {}, set()
    for row in rows:
        if not isinstance(row, dict):
            _fail('launch_native_inventory_unproved')
        cid = row.get('campaign_id')
        budget = row.get('budget_resource')
        if (not isinstance(cid, str) or not re.fullmatch(r'[0-9]{1,30}', cid) or cid in campaigns
            or row.get('resource_name') != f"customers/{policy['account_id']}/campaigns/{cid}"
            or not isinstance(budget, str) or not re.fullmatch(f"customers/{policy['account_id']}/campaignBudgets/[0-9]+", budget)
            or budget in budgets or row.get('shared_budget') is not False
            or row.get('status') not in {'PAUSED', 'ENABLED', 'REMOVED'} or row.get('budget_mode') != 'DAILY'
            or _money(row.get('budget_native')) is None or row.get('source') != 'google_ads_api'
            or row.get('account_id') != policy['account_id']
            or any(row.get(key) != policy[key] for key in ('currency_code', 'time_zone'))
            or not creation._fresh(row.get('observed_at'), current, 90)):
            _fail('launch_native_inventory_unproved')
        campaigns[cid] = row
        budgets.add(budget)
    return campaigns


def _cap(policy, standing, state, current):
    present = state.get('standing_policy_present')
    if type(present) is not bool or present != (standing is not None):
        _fail('launch_standing_policy_unproved')
    caps = [Decimal(policy['max_account_daily_budget_native'])]
    if standing is not None:
        if (not _scope(standing, policy) or type(standing.get('enabled')) is not bool
            or any(standing.get(key) != policy[key] for key in ('currency_code', 'time_zone'))):
            _fail('launch_standing_policy_unproved')
        if standing['enabled']:
            expiry = creation._instant(standing.get('expires_at'))
            amount = _money(standing.get('max_account_daily_budget_native'))
            # An ambiguous enabled policy is never treated as absent/disabled.
            if not _activated(standing, current) or expiry is None or amount is None:
                _fail('launch_standing_policy_unproved')
            if current < expiry:
                caps.append(amount)
    return min(caps)


def _allocations(state, policy, campaigns, campaign_id, current):
    if (not _scope(state, policy) or state.get('complete') is not True or state.get('pending_write') is not False
        or type(state.get('guard_epoch')) is not int or not 0 <= state['guard_epoch'] < 2**63 - 2
        or not creation._fresh(state.get('observed_at'), current, 90)):
        _fail('launch_state_unproved')
    rows = state.get('allocations')
    if not isinstance(rows, list) or len(rows) > 100:
        _fail('launch_allocations_unproved')
    total, ids, targets = Decimal(0), set(), set()
    for row in rows:
        if not isinstance(row, dict):
            _fail('launch_allocations_unproved')
        aid, cid, amount = row.get('allocation_id'), row.get('campaign_id'), _money(row.get('budget_native'))
        native = campaigns.get(cid) if isinstance(cid, str) else None
        if (not _scope(row, policy) or row.get('currency_code') != policy['currency_code']
            or not isinstance(aid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', aid)
            or aid in ids or not isinstance(cid, str) or cid in targets or cid == campaign_id
            or native is None or amount is None or amount != _money(native['budget_native'])
            or row.get('state') not in {'reserved', 'native_enabled'}):
            _fail('launch_allocations_unproved')
        if row['state'] == 'native_enabled':
            if native['status'] != 'ENABLED':
                _fail('launch_allocations_unproved')
            # Already included in native active commitments; count exactly once.
        else:
            if native['status'] != 'PAUSED':
                _fail('launch_allocations_unproved')
            total += amount
        ids.add(aid)
        targets.add(cid)
    return total



def _launch_scope(policy, intent, proposal, campaign_id, state, current):
    """Default to creations after this activation, with a separate old-scope ACK.

    The ACK is an exact persisted owner decision loaded by the future authority,
    never a caller assertion generated from auto_enable_new or native labels.
    """
    created = creation._instant(proposal.get('created_at'))
    activated = creation._instant(policy['activated_at'])
    if created is None or created > current:
        _fail('launch_creation_time_unproved')
    include = state.get('include_existing_owned', False)
    if type(include) is not bool or created < activated and not include:
        _fail('launch_existing_owned_not_authorized')
    ack = state.get('existing_owned_scope_ack')
    if include:
        identity, product = intent['binding']['identity'], intent['binding']['product']
        expected = {'acknowledged_by': policy['user_id'], 'policy_revision': policy['revision'],
            'owner_generation': policy['owner_generation'], 'template_id': policy['template_id'],
            'template_version': policy['template_version'], 'product_id': identity['product_id'],
            'final_url': product['final_url'], 'campaign_id': campaign_id, 'creation_identity': intent['creation_identity']}
        if not isinstance(ack, dict) or any(ack.get(key) != value or type(ack.get(key)) is not type(value)
                for key, value in expected.items()):
            _fail('launch_existing_owned_not_authorized')
        acknowledged = creation._instant(ack.get('acknowledged_at'))
        if acknowledged is None or not activated <= acknowledged <= current:
            _fail('launch_existing_owned_not_authorized')
    return {'include_existing_owned': include, 'creation_proposal_created_at': created.isoformat(),
        'existing_owned_scope_ack_digest': creation._digest(ack) if include else None}


def _semantic_snapshots(native, state):
    """Observation clocks are freshness evidence, never semantic configuration.

    Only known read clocks are removed. Owner activation/acknowledgement times
    and every monetary/resource/revision fact remain part of the binding.
    """
    native, state = deepcopy(native), deepcopy(state)
    native.pop('observed_at', None)
    for row in native['campaigns']:
        row.pop('observed_at', None)
    native['campaigns'].sort(key=lambda row: row['campaign_id'])
    state.pop('observed_at', None)
    state['allocations'].sort(key=lambda row: row['allocation_id'])
    state['consumed_creation_identities'].sort()
    return native, state


def prepare_google_creation_launch_intent(*, policy, creation_intent, ledger_entry, original_proposal,
        native_context, launch_state, campaign_id, current, standing_policy=None, launch_evidence=None):
    """Return nonexecutable enable-only preview, or a bounded reason code.

    Never adopts campaigns. Never decreases budgets or reallocates planned
    savings. Monetary totals are DAILY average commitments, not hard spend caps.
    No active standing policy may be omitted: caller must prove its absence.
    Older owned campaigns are excluded unless a separate exact owner scope ACK
    is persisted; enabling auto_enable_new never creates that acknowledgement.
    """
    if not isinstance(current, datetime) or current.tzinfo is None:
        _fail('launch_clock_unproved')
    current = current.astimezone(timezone.utc)
    try:
        policy = creation.GoogleAutomationCreationPolicy.model_validate(policy).model_dump(mode='json')
    except ValidationError:
        _fail('launch_policy_invalid')
    if not _activated(policy, current) or policy['auto_enable_new'] is not True:
        _fail('launch_policy_not_authorized')
    expiry = creation._instant(policy['expires_at'])
    if expiry is None or not current < expiry <= current + timedelta(days=30):
        _fail('launch_policy_expired')
    if (not creation.creation_campaign_ownership_verified(ledger_entry, intent=creation_intent,
        user_id=policy['user_id'], account_id=policy['account_id'], campaign_id=campaign_id, proposal=original_proposal)
        or ledger_entry.get('state') != 'owned' or ledger_entry.get('reservation_state') != 'paused_created'
        or ledger_entry.get('reservation_currency_code') != policy['currency_code']
        or _money(ledger_entry.get('reservation_native')) != _money(policy['budget_native'])):
        _fail('launch_creation_ownership_unproved')
    binding = creation_intent['binding']
    identity, draft = binding['identity'], creation_intent['draft']
    product = binding.get('product', {})
    if (any(identity.get(key) != policy[key] for key in ('template_id', 'template_version', 'owner_generation'))
        or binding.get('template_digest') != policy['template_digest']
        or {'product_id': identity.get('product_id'), 'final_url': product.get('final_url')} not in policy['product_scopes']
        or draft.get('budget_native') != policy['budget_native']
        or draft.get('geo_target_ids') != policy['geo_target_ids'] or draft.get('bidding', {}).get('strategy') != policy['bidding_strategy']):
        _fail('launch_creation_scope_changed')
    evidence_binding = None
    if launch_evidence is not None:
        from .google_ads_automation_creation_launch_evidence import validate_launch_evidence, LaunchEvidenceError
        try:
            evidence_binding = validate_launch_evidence(launch_evidence, policy=policy,
                creation_intent=creation_intent, ledger_entry=ledger_entry, original_proposal=original_proposal,
                campaign_id=campaign_id, current=current)
        except LaunchEvidenceError as exc:
            _fail(exc.code)
        # Current native facts come from the validated server read bundle, not
        # older caller flags. The public interface has no route accepting this.
        native_context = {**evidence_binding['purchase_context'], 'observed_at': launch_evidence['observed_at'],
            'campaigns': deepcopy(launch_evidence['campaigns']), 'segmented': False,
            'inventory_scope': 'ALL_CAMPAIGNS', 'has_more': False}
    campaigns = _inventory(native_context, policy, current)
    target = campaigns.get(campaign_id)
    if (target is None or target['status'] != 'PAUSED' or target.get('campaign_type') != 'SEARCH'
        or target.get('correlation_label') != ledger_entry['correlation_label']
        or target.get('creation_configuration_verified') is not True
        or target.get('creation_draft_digest') != binding['draft_digest']
        or type(target.get('budget_reference_count')) is not int or target['budget_reference_count'] != 1
        or _money(target['budget_native']) != _money(policy['budget_native'])):
        _fail('launch_native_target_changed')
    reserved = _allocations(launch_state, policy, campaigns, campaign_id, current)
    launch_scope = _launch_scope(policy, creation_intent, original_proposal, campaign_id, launch_state, current)
    consumed = launch_state.get('consumed_creation_identities')
    if (not isinstance(consumed, list) or len(consumed) > 100
        or any(not isinstance(value, str) or re.fullmatch(r'google_create_[a-f0-9]{64}', value) is None for value in consumed)
        or len(set(consumed)) != len(consumed)):
        _fail('launch_history_unproved')
    if creation_intent['creation_identity'] in consumed:
        _fail('launch_creation_already_consumed')
    cap = _cap(policy, standing_policy, launch_state, current)
    enabled = sum((_money(row['budget_native']) for row in campaigns.values() if row['status'] == 'ENABLED'), Decimal(0))
    resulting = enabled + reserved + _money(target['budget_native'])
    if resulting > cap:
        _fail('launch_account_commitment_limit')
    ownership = {'creation_identity': creation_intent['creation_identity'], 'intent_digest': creation_intent['intent_digest'],
        'proposal_id': original_proposal['proposal_id'], 'confirmation_digest': original_proposal['confirmation_digest'],
        'ledger_digest': creation._digest(ledger_entry), 'draft_digest': binding['draft_digest']}
    semantic_native, semantic_state = _semantic_snapshots(native_context, launch_state)
    before = {'native_inventory': semantic_native, 'launch_state': semantic_state, 'launch_evidence': evidence_binding,
        'creation_policy': policy, 'standing_policy': standing_policy, 'ownership': ownership}
    result_binding = {'user_id': policy['user_id'], 'account_id': policy['account_id'], 'campaign_id': campaign_id,
        'policy_revision': policy['revision'], 'policy_digest': creation._digest(policy), 'launch_scope': launch_scope,
        'cap_binding': {'creation_cap_native': policy['max_account_daily_budget_native'],
            'standing_policy_present': standing_policy is not None,
            'standing_policy_digest': creation._digest(standing_policy) if standing_policy is not None else None,
            'standing_policy_revision': standing_policy.get('revision') if standing_policy is not None else None,
            'account_guard_epoch': launch_state['guard_epoch']},
        'before_digest': creation._digest(before), 'ownership': ownership}
    return {'provider': 'google_ads', 'provider_writes': 0, 'executable': False, 'runtime_supported': False,
        'authority_required': True, 'provider_validation_required': True, 'catalog_revalidation_required': True,
        'launch_identity': 'google_launch_' + creation._digest({'user_id': policy['user_id'], 'account_id': policy['account_id'],
            'campaign_id': campaign_id, 'creation_identity': creation_intent['creation_identity']}),
        'ready_for_enable_preview': False, 'launch_history_verified': False,
        'configuration_evidence_verified': evidence_binding is not None,
        'blockers': ([] if evidence_binding is not None else ['launch_product_configuration_evidence_unproved'])
            + ['launch_stop_provenance_unproved', 'launch_atomic_authority_unimplemented'],
        'evidence_observed_at': {'native': native_context['observed_at'], 'state': launch_state['observed_at']},
        'planned': {'action': 'enable', 'campaign_id': campaign_id, 'before_status': 'PAUSED', 'after_status': 'ENABLED'},
        'funding': {'budget_semantics': 'DAILY_AVERAGE_COMMITMENT', 'enabled_average_budget_native': str(enabled),
            'additional_reserved_average_budget_native': str(reserved), 'target_average_budget_native': target['budget_native'],
            'resulting_average_commitment_native': str(resulting), 'account_commitment_limit_native': str(cap),
            'currency_code': policy['currency_code']},
        'before_digest': result_binding['before_digest'], 'ownership_binding': ownership, 'binding': result_binding,
        'intent_digest': creation._digest(result_binding)}


def revalidate_google_creation_launch_intent(intent, **fresh_inputs):
    fresh = prepare_google_creation_launch_intent(**fresh_inputs)
    if (not isinstance(intent, dict) or intent.get('binding') != fresh['binding']
        or intent.get('intent_digest') != fresh['intent_digest']
        or intent.get('launch_identity') != fresh['launch_identity']):
        _fail('launch_intent_changed')
    return fresh
