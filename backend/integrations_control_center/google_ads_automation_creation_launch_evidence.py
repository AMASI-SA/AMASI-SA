"""Internal read-only evidence for a future Search/RSA first launch.

There is no route, claim, mutation or launch permission here. Inputs come from
tenant-owned server storage and one native client, never a request/model body.
Mezan creation ownership does not prove a campaign has never been enabled or
manually stopped. This reader deliberately makes no external-history claim.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import re

from product_inventory_proof import inventory_readiness
from . import google_ads_automation_creation_contract as creation
from .google_ads_automation_creation_policy import CREATION_POLICIES, POLICY_FIELDS, creation_policy_snapshot
from .google_ads_ai import PRODUCT_COLLECTION
from .google_ads_creation_inventory import PRODUCT_PROJECTION
from .google_ads_campaign_builder import build_campaign_operations, GoogleCampaignBuildError
from .google_ads_creative_projection import duplicate_campaign_draft
from .google_ads_management import PROPOSALS

CREATION_INTENTS = 'mezan_google_ads_automation_creation_intents_v1'
_MAX_BYTES = 1_000_000
_RESOURCE = re.compile(r'^customers/([0-9]+)/([A-Za-z]+)/([0-9A-Za-z_~.]+)$')
_PROPOSAL_FIELDS = {'_id': 0, **{key: 1 for key in (
    'user_id', 'account_id', 'provider', 'proposal_id', 'actor_id', 'action', 'status', 'verified',
    'submission_started', 'created_campaign_id', 'correlation_label', 'operations', 'resource_names',
    'planned', 'automation_source', 'confirmation_digest', 'created_at', 'currency', 'time_zone',
    'auth_epoch', 'before_digest', 'draft_id', 'draft_revision', 'reason', 'financial_bound', 'expires_at', 'ai_source')}}
_INVENTORY_FIELDS = (
    'campaign.resource_name', 'campaign.id', 'campaign.status', 'campaign.advertising_channel_type',
    'campaign.campaign_budget', 'campaign_budget.resource_name', 'campaign_budget.period',
    'campaign_budget.amount_micros', 'campaign_budget.explicitly_shared', 'campaign_budget.reference_count')


class LaunchEvidenceError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise LaunchEvidenceError(code)


def _stored_utc(value):
    """Only BSON datetime objects have implicit UTC at this storage boundary.

    Do not apply this rule to caller clock strings or generic values. Motor's
    default codec returns Mongo UTC dates as naive datetime objects.
    """
    if not isinstance(value, datetime):
        raise TypeError('unsupported stored evidence type')
    stamp = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return stamp.isoformat()


def _stored_product_clocks(product):
    if not isinstance(product, dict):
        return product
    product = deepcopy(product)
    for key in ('last_synced_at', 'details_synced_at'):
        if isinstance(product.get(key), datetime): product[key] = _stored_utc(product[key])
    proof = product.get('inventory_evidence_v1')
    if isinstance(proof, dict) and isinstance(proof.get('observed_at'), datetime):
        proof['observed_at'] = _stored_utc(proof['observed_at'])
    return product


def _bounded(value):
    try:
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False, default=_stored_utc).encode()) > _MAX_BYTES:
            _fail('launch_evidence_size_limit')
    except (TypeError, ValueError, OverflowError):
        _fail('launch_evidence_shape_unproved')


def _resource(value, account_id, collection):
    match = _RESOURCE.fullmatch(value) if isinstance(value, str) else None
    if not match or match.group(1) != account_id or match.group(2) != collection:
        _fail('launch_native_resource_unproved')
    return value


def _integer(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r'[0-9]{1,18}', str(value)):
        _fail('launch_native_budget_unproved')
    value = int(value)
    if positive and value <= 0:
        _fail('launch_native_budget_unproved')
    return value


def _window(start, finish, current):
    start, finish, current = (creation._instant(value) for value in (start, finish, current))
    if (start is None or finish is None or current is None or not start <= finish <= current
        or not 0 <= (current - start).total_seconds() < 90):
        _fail('launch_evidence_stale')
    return start, finish, current


def _product_binding(product, policy, intent, current):
    expected = intent['binding']['product']
    identity = intent['binding']['identity']
    if (not isinstance(product, dict) or product.get('user_id') != policy['user_id']
        or str(product.get('salla_product_id') or product.get('mezan_product_id') or '') != identity['product_id']):
        _fail('launch_product_scope_unproved')
    if product.get('status') != 'active' or product.get('archived') is not False:
        _fail('launch_product_not_public')
    if (not isinstance(product.get('source_revision'), str) or not re.fullmatch(r'[a-f0-9]{64}', product['source_revision'])
        or not creation._fresh(product.get('last_synced_at'), current, policy['catalog_max_age_seconds'])):
        _fail('launch_product_stale')
    state, stock_digest = inventory_readiness(product, current, policy['catalog_max_age_seconds'])
    if state != 'available':
        _fail('launch_product_unavailable' if state == 'unavailable' else 'launch_inventory_unproved')
    if creation._canonical_url(product) != expected['final_url']:
        _fail('launch_product_url_changed')
    # The native copy remains exactly the original approved copy. A newer
    # catalog revision is evidence, not permission to silently rewrite ads.
    if product.get('name') != expected['product_name'] or (product.get('sku') or None) != expected['sku']:
        _fail('launch_product_copy_changed')
    return {'product_id': identity['product_id'], 'source_revision': product['source_revision'],
        'inventory_digest': stock_digest, 'final_url': expected['final_url'],
        'product_name': expected['product_name'], 'sku': expected['sku']}


def _inventory(rows, complete, *, account_id, metadata, user_id, stamp):
    if complete is not True or not isinstance(rows, list) or not 1 <= len(rows) <= 500:
        _fail('launch_native_inventory_unproved')
    output, campaigns, budgets = [], set(), set()
    for raw in rows:
        if not isinstance(raw, dict) or not isinstance(raw.get('campaign'), dict) or not isinstance(raw.get('campaignBudget'), dict):
            _fail('launch_native_inventory_unproved')
        campaign, budget = raw['campaign'], raw['campaignBudget']
        cid = campaign.get('id')
        if not isinstance(cid, str) or not re.fullmatch(r'[0-9]{1,30}', cid) or cid in campaigns:
            _fail('launch_native_inventory_unproved')
        name = _resource(campaign.get('resourceName'), account_id, 'campaigns')
        reference = _resource(budget.get('resourceName'), account_id, 'campaignBudgets')
        if (name != f'customers/{account_id}/campaigns/{cid}' or campaign.get('campaignBudget') != reference
            or reference in budgets or budget.get('explicitlyShared') is not False
            or _integer(budget.get('referenceCount')) != 1 or budget.get('period') != 'DAILY'
            or campaign.get('status') not in {'ENABLED', 'PAUSED', 'REMOVED'}
            or not isinstance(campaign.get('advertisingChannelType'), str)):
            _fail('launch_native_inventory_unproved')
        amount = _integer(budget.get('amountMicros'), positive=True)
        campaigns.add(cid); budgets.add(reference)
        output.append({'account_id': account_id, 'campaign_id': cid, 'resource_name': name,
            'campaign_type': campaign['advertisingChannelType'], 'status': campaign['status'],
            'budget_resource': reference, 'budget_reference_count': 1, 'shared_budget': False,
            'budget_mode': 'DAILY', 'budget_native': format(Decimal(amount) / Decimal(1_000_000), 'f'),
            'currency_code': metadata['currency_code'], 'time_zone': metadata['time_zone'],
            'source': 'google_ads_api', 'observed_at': stamp})
    return sorted(output, key=lambda row: row['campaign_id'])


def _identities(rows, account_id, collection, *, parent_field=None, parents=()):
    if not isinstance(rows, list) or len(rows) > 1000:
        _fail('launch_native_children_unproved')
    names = []
    for row in rows:
        if not isinstance(row, dict):
            _fail('launch_native_children_unproved')
        names.append(_resource(row.get('resourceName'), account_id, collection))
        if parent_field and row.get(parent_field) not in parents:
            _fail('launch_native_children_unproved')
        if collection in {'adGroupAds', 'adGroupCriteria', 'campaignCriteria', 'campaignConversionGoals'}:
            if names[-1].rsplit('/', 1)[-1].split('~', 1)[0] != row[parent_field].rsplit('/', 1)[-1]:
                _fail('launch_native_children_unproved')
    if len(set(names)) != len(names):
        _fail('launch_native_children_unproved')
    return set(names)


def _purchase_context_binding(context, policy, campaigns, start, current):
    try:
        creation._native_gate(creation.GoogleAutomationCreationPolicy.model_validate(policy), context, current)
    except creation.CreationContractError:
        _fail('launch_purchase_value_context_unproved')
    rows = context.get('campaigns')
    if (creation._instant(context['observed_at']) != start or not isinstance(rows, list) or len(rows) > 500):
        _fail('launch_native_reads_changed')
    observed = {}
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('campaign_id'), str)
            or row['campaign_id'] in observed or creation._instant(row.get('observed_at')) != start
            or row.get('account_id') != policy['account_id']
            or row.get('currency_code') != policy['currency_code'] or row.get('time_zone') != policy['time_zone']):
            _fail('launch_native_reads_changed')
        observed[row['campaign_id']] = (row.get('status'), creation._amount(row.get('budget_native')))
    expected = {row['campaign_id']: (row['status'], Decimal(row['budget_native']))
        for row in campaigns if row['status'] != 'REMOVED'}
    if observed != expected:
        _fail('launch_native_reads_changed')
    return {key: value for key, value in context.items() if key not in {'observed_at', 'campaigns'}}


def _configuration(snapshot, proposal, intent, *, account_id, campaign_id, campaigns, verification):
    if (not isinstance(snapshot, dict) or snapshot.get('duplicate_controls_complete') is not True
        or snapshot.get('duplicate_control_gaps') != []):
        _fail('launch_native_configuration_unproved')
    target = next((row for row in campaigns if row['campaign_id'] == campaign_id), None)
    campaign, budget = snapshot.get('campaign', {}), snapshot.get('campaign_budget', {})
    if (not isinstance(campaign, dict) or not isinstance(budget, dict)
        or target is None or target['status'] != 'PAUSED' or target['campaign_type'] != 'SEARCH'
        or campaign.get('resourceName') != target['resource_name'] or campaign.get('status') != 'PAUSED'
        or campaign.get('advertisingChannelType') != 'SEARCH'
        or campaign.get('campaignBudget') != target['budget_resource']
        or budget.get('resourceName') != target['budget_resource'] or budget.get('explicitlyShared') is not False
        or _integer(budget.get('referenceCount')) != 1 or budget.get('period') != 'DAILY'
        or Decimal(_integer(budget.get('amountMicros'), positive=True)) / Decimal(1_000_000) != Decimal(target['budget_native'])):
        _fail('launch_native_target_changed')
    operations, names = proposal['operations'], proposal['resource_names']
    expected = {}
    for operation, name in zip(operations, names):
        kind = next(iter(operation)).removesuffix('Operation')
        expected.setdefault(kind, set()).add(name)
    campaign_name = target['resource_name']
    group_names = _identities(snapshot.get('groups'), account_id, 'adGroups', parent_field='campaign', parents={campaign_name})
    if any(not isinstance(group.get('criteria'), list) for group in snapshot['groups']):
        _fail('launch_native_children_unproved')
    actual = {'adGroup': group_names,
        'adGroupAd': _identities(snapshot.get('ads'), account_id, 'adGroupAds', parent_field='adGroup', parents=group_names),
        'campaignCriterion': _identities(snapshot.get('criteria'), account_id, 'campaignCriteria', parent_field='campaign', parents={campaign_name}),
        'adGroupCriterion': _identities([row for group in snapshot['groups'] for row in group.get('criteria', [])],
            account_id, 'adGroupCriteria', parent_field='adGroup', parents=group_names),
        'campaignConversionGoal': _identities(snapshot.get('conversion_goals'), account_id, 'campaignConversionGoals',
            parent_field='campaign', parents={campaign_name})}
    configs = _identities(snapshot.get('conversion_goal_config'), account_id, 'conversionGoalCampaignConfigs',
        parent_field='campaign', parents={campaign_name})
    if configs != {f'customers/{account_id}/conversionGoalCampaignConfigs/{campaign_id}'}:
        _fail('launch_native_children_unproved')
    if any(actual[kind] != expected.get(kind, set()) for kind in actual):
        _fail('launch_native_children_changed')
    if snapshot.get('campaign_assets') or any(group.get('group_assets') for group in snapshot['groups']):
        _fail('launch_native_controls_changed')
    if (not isinstance(verification, dict) or verification.get('readback_independent') is not True
        or verification.get('mismatches') != [] or verification.get('coverage_gaps') != []
        or not isinstance(verification.get('verified_resources'), list)
        or len(verification['verified_resources']) != len(names)
        or set(verification['verified_resources']) != set(names)):
        _fail('launch_native_configuration_unproved')
    try:
        projected = duplicate_campaign_draft(snapshot, account_id, intent['draft']['name'])
        projected_operations = build_campaign_operations(projected.model_dump(mode='json'), account_id)
    except (GoogleCampaignBuildError, ValueError, TypeError, KeyError):
        _fail('launch_native_controls_unrepresented')
    # Reuse the supported native-to-draft projection to reject fields absent
    # from the original immutable creation. Independent readback alone checks
    # only original named resources and cannot exclude added native settings.
    if projected_operations != operations[:-2]:
        _fail('launch_native_configuration_changed')
    target.update(correlation_label=proposal['correlation_label'], creation_configuration_verified=True,
        creation_draft_digest=intent['binding']['draft_digest'])
    return {'configuration_operations_digest': creation._digest(projected_operations),
        'resource_names': sorted(names), 'child_resource_names': {key: sorted(value) for key, value in actual.items()}}


def prepare_launch_evidence(*, user_id, account_id, campaign_id, policy, creation_intent, ledger_entry,
        original_proposal, product, metadata, inventory_rows, inventory_complete, snapshot, verification,
        purchase_context, read_started_at, read_finished_at, current):
    """Validate server-read inputs; a JSON flag alone is never configuration proof."""
    start, finish, current = _window(read_started_at, read_finished_at, current)
    _bounded([policy, creation_intent, ledger_entry, original_proposal, product, metadata, inventory_rows, snapshot, verification, purchase_context])
    product = _stored_product_clocks(product)
    if (not creation.creation_campaign_ownership_verified(ledger_entry, intent=creation_intent, user_id=user_id,
        account_id=account_id, campaign_id=campaign_id, proposal=original_proposal)):
        _fail('launch_creation_ownership_unproved')
    try:
        policy = creation.GoogleAutomationCreationPolicy.model_validate(creation_policy_snapshot(policy)).model_dump(mode='json')
    except (ValueError, TypeError):
        _fail('launch_policy_invalid')
    if (policy['user_id'] != user_id or policy['account_id'] != account_id
        or not isinstance(metadata, dict) or metadata.get('identity_verified') is not True or metadata.get('account_id') != account_id
        or any(metadata.get(key) != policy[key] for key in ('currency_code', 'time_zone'))):
        _fail('launch_native_identity_unproved')
    product_binding = _product_binding(product, policy, creation_intent, current)
    campaigns = _inventory(inventory_rows, inventory_complete, account_id=account_id, metadata=metadata,
        user_id=user_id, stamp=start.isoformat())
    purchase_binding = _purchase_context_binding(purchase_context, policy, campaigns, start, current)
    configuration = _configuration(snapshot, original_proposal, creation_intent, account_id=account_id,
        campaign_id=campaign_id, campaigns=campaigns, verification=verification)
    binding = {'schema_version': 1, 'user_id': user_id, 'account_id': account_id, 'campaign_id': campaign_id,
        'creation_identity': creation_intent['creation_identity'], 'intent_digest': creation_intent['intent_digest'],
        'ledger_digest': creation._digest(ledger_entry), 'proposal_id': original_proposal['proposal_id'],
        'proposal_confirmation_digest': original_proposal['confirmation_digest'], 'policy_digest': creation._digest(policy),
        'product': product_binding, 'configuration': configuration,
        'purchase_context': purchase_binding,
        'campaigns': [{key: value for key, value in row.items() if key != 'observed_at'} for row in campaigns]}
    return {'binding': binding, 'semantic_digest': creation._digest(binding),
        'observed_at': start.isoformat(), 'read_finished_at': finish.isoformat(),
        'product_observed_at': product['last_synced_at'], 'inventory_observed_at': product['details_synced_at'],
        'catalog_max_age_seconds': policy['catalog_max_age_seconds'], 'campaigns': campaigns,
        'created_by_mezan_verified': True, 'launch_history_verified': False,
        'ready_for_enable_preview': False, 'authority_required': True, 'provider_writes': 0, 'executable': False}


def validate_launch_evidence(evidence, *, policy, creation_intent, ledger_entry, original_proposal, campaign_id, current):
    """Recheck immutable binding and every independent deadline at later use.

    This is internal evidence integrity, not a signature or authorization. A
    future native authority must load/rebuild it itself, never accept caller JSON.
    """
    if not isinstance(evidence, dict):
        _fail('launch_evidence_unproved')
    _bounded(evidence)
    _, _, current = _window(evidence.get('observed_at'), evidence.get('read_finished_at'), current)
    binding = evidence.get('binding')
    rows = evidence.get('campaigns')
    if not isinstance(rows, list) or len(rows) > 500 or any(not isinstance(row, dict) for row in rows):
        _fail('launch_evidence_binding_changed')
    if (not isinstance(binding, dict) or not isinstance(binding.get('configuration'), dict)
        or evidence.get('semantic_digest') != creation._digest(binding)
        or any(binding.get(key) != policy[key] for key in ('user_id', 'account_id'))
        or binding.get('campaign_id') != campaign_id or binding.get('creation_identity') != creation_intent['creation_identity']
        or binding.get('intent_digest') != creation_intent['intent_digest'] or binding.get('policy_digest') != creation._digest(policy)
        or binding.get('ledger_digest') != creation._digest(ledger_entry)
        or binding.get('proposal_id') != original_proposal.get('proposal_id')
        or binding.get('proposal_confirmation_digest') != original_proposal.get('confirmation_digest')
        or not isinstance(original_proposal.get('resource_names'), list)
        or binding.get('configuration', {}).get('resource_names') != sorted(original_proposal['resource_names'])
        or evidence.get('created_by_mezan_verified') is not True or evidence.get('launch_history_verified') is not False
        or evidence.get('catalog_max_age_seconds') != policy['catalog_max_age_seconds']
        or any(not creation._fresh(evidence.get(key), current, policy['catalog_max_age_seconds'])
            for key in ('product_observed_at', 'inventory_observed_at'))
        or binding.get('campaigns') != [{key: value for key, value in row.items() if key != 'observed_at'}
            for row in rows]):
        _fail('launch_evidence_binding_changed')
    return deepcopy(binding)


async def read_google_creation_launch_evidence(db, *, user_id, account_id, campaign_id, creation_identity, client, clock=None):
    """No OAuth acquisition or writes. Caller owns an already scoped client.

    Re-read storage after native reads to detect owner/product changes during
    the read. These checks do not form an atomic dispatch snapshot or exclude
    external writers. Pagination/field incompleteness always fails closed.
    """
    clock = clock or (lambda: datetime.now(timezone.utc))
    started = clock()
    if (not isinstance(account_id, str) or not re.fullmatch(r'[0-9]{1,30}', account_id)
        or not isinstance(campaign_id, str) or not re.fullmatch(r'[0-9]{1,30}', campaign_id)
        or not isinstance(creation_identity, str) or not re.fullmatch(r'google_create_[a-f0-9]{64}', creation_identity)
        or client.customer_id != account_id):
        _fail('launch_read_scope_unproved')
    scope = {'user_id': user_id, 'account_id': account_id}
    projection = {'_id': 0, **{key: 1 for key in POLICY_FIELDS}, '_creation_ledger': 1}
    async with asyncio.timeout(60):
        policy = await db[CREATION_POLICIES].find_one(scope, projection)
        saved = await db[CREATION_INTENTS].find_one({**scope, 'creation_identity': creation_identity},
            {'_id': 0, 'user_id': 1, 'account_id': 1, 'creation_identity': 1, 'proposal_id': 1, 'intent': 1})
        if not policy or not saved or not isinstance(policy.get('_creation_ledger'), dict):
            _fail('launch_creation_ownership_unproved')
        _bounded([policy, saved])
        if len(policy['_creation_ledger']) > 100:
            _fail('launch_creation_ownership_unproved')
        ledger = policy['_creation_ledger'].get(creation_identity)
        if not isinstance(ledger, dict) or saved.get('proposal_id') != ledger.get('proposal_id'):
            _fail('launch_creation_ownership_unproved')
        proposal = await db[PROPOSALS].find_one({**scope, 'proposal_id': ledger['proposal_id']}, _PROPOSAL_FIELDS)
        intent = saved.get('intent')
        if not creation.creation_campaign_ownership_verified(ledger, intent=intent, user_id=user_id,
            account_id=account_id, campaign_id=campaign_id, proposal=proposal):
            _fail('launch_creation_ownership_unproved')
        pid = intent['binding']['identity']['product_id']
        product_scope = {'user_id': user_id, '$or': [{'salla_product_id': pid}, {'mezan_product_id': pid}]}
        products = await db[PRODUCT_COLLECTION].find(product_scope, PRODUCT_PROJECTION).to_list(length=2)
        if len(products) != 1:
            _fail('launch_product_identity_unproved')
        product = products[0]
        _bounded([proposal, product])
        metadata = await client.account_metadata()
        from .google_ads_automation_creation_plan import read_google_creation_native_context
        purchase_context = await read_google_creation_native_context(client, user_id=user_id, account_id=account_id,
            policy=policy, read_started_at=started, metadata=metadata)
        rows, complete = await client.search('SELECT ' + ', '.join(_INVENTORY_FIELDS) + ' FROM campaign LIMIT 501', max_rows=501)
        snapshot = await client.campaign_snapshot(campaign_id, children=True, duplicate_controls=True)
        verified, report = await client.verify_operations(proposal['operations'], proposal['resource_names'])
        if verified is not True:
            _fail('launch_native_configuration_unproved')
        fresh_policy = await db[CREATION_POLICIES].find_one(scope, projection)
        fresh_intent = await db[CREATION_INTENTS].find_one({**scope, 'creation_identity': creation_identity},
            {'_id': 0, 'user_id': 1, 'account_id': 1, 'creation_identity': 1, 'proposal_id': 1, 'intent': 1})
        fresh_proposal = await db[PROPOSALS].find_one({**scope, 'proposal_id': ledger['proposal_id']}, _PROPOSAL_FIELDS)
        fresh_products = await db[PRODUCT_COLLECTION].find(product_scope, PRODUCT_PROJECTION).to_list(length=2)
        if fresh_policy != policy or fresh_intent != saved or fresh_proposal != proposal or fresh_products != products:
            _fail('launch_storage_changed_during_read')
        finished = clock()
        return prepare_launch_evidence(user_id=user_id, account_id=account_id, campaign_id=campaign_id,
            policy=policy, creation_intent=intent, ledger_entry=ledger, original_proposal=proposal,
            product=product, metadata=metadata, inventory_rows=rows, inventory_complete=complete,
            snapshot=snapshot, verification=report, purchase_context=purchase_context,
            read_started_at=started, read_finished_at=finished, current=clock())
