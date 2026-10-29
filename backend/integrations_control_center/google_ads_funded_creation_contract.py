"""Pure new-ENABLED Search graph contract; NEVER dispatch or owner authority.

Inputs must eventually be loaded from trusted owner/product/native reads. This
module validates supplied claims, not their provenance. Existing PAUSED builders
and authorities are unchanged. No OAuth, database, environment or provider IO.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import re
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .google_ads_campaign_contract import GoogleCampaignDraft
from .google_ads_campaign_builder import GoogleCampaignBuildError, build_campaign_operations, native_to_micros

_MONEY = r'^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?$'
_HASH = r'^[a-f0-9]{64}$'
_ID = r'^[A-Za-z0-9_-]{8,128}$'
_MODE = 'create_new_enabled_search'
_ORIGIN = 'google_automation_funded_creation_v1'
_ALLOWED = {'campaignBudgetOperation','campaignOperation','campaignConversionGoalOperation',
    'campaignCriterionOperation','adGroupOperation','adGroupCriterionOperation',
    'adGroupAdOperation','labelOperation','campaignLabelOperation'}


class FundedCreationContractError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise FundedCreationContractError(code)


def _digest(value):
    try:
        payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
        _fail('funded_creation_value_invalid')
    return hashlib.sha256(payload.encode()).hexdigest()


def _instant(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


class FundedCreationGrant(BaseModel):
    """Shape of explicit supplied review claims, not a verified persisted grant."""
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal[1]
    mode: Literal['create_new_enabled_search']
    user_id: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,128}$')
    account_id: str = Field(pattern=r'^[0-9]{1,20}$')
    revision: int = Field(ge=1, le=2**63-2)
    owner_generation: int = Field(ge=1, le=2**63-2)
    reviewed_by: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,128}$')
    owner_review_digest: str = Field(pattern=_HASH)
    valid_from: str = Field(max_length=64)
    expires_at: str = Field(max_length=64)
    currency_code: str = Field(pattern=r'^[A-Z]{3}$')
    time_zone: str = Field(min_length=1, max_length=100)
    budget_native: str = Field(pattern=_MONEY)
    max_campaign_daily_budget_native: str = Field(pattern=_MONEY)
    max_account_daily_budget_native: str = Field(pattern=_MONEY)
    draft_digest: str = Field(pattern=_HASH)
    request_key: str = Field(pattern=_ID)


def _draft(value):
    try:
        draft = GoogleCampaignDraft.model_validate(value)
    except (ValidationError, ValueError, TypeError):
        _fail('funded_creation_draft_invalid')
    if (draft.campaign_type != 'SEARCH' or draft.budget_mode != 'DAILY' or draft.shopping or draft.app or draft.hotel
        or draft.ai_max and draft.ai_max.enabled or draft.search and draft.search.dynamic_search_domain
        or not draft.groups or any(group.ad_format != 'RSA' or group.status != 'ENABLED'
            or group.ad_status != 'ENABLED' or group.ads or group.audience_ids for group in draft.groups)):
        _fail('funded_creation_search_rsa_only')
    return draft


def funded_creation_draft_digest(draft):
    return _digest(_draft(draft).model_dump(mode='json'))


def _resource_strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _resource_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _resource_strings(item)
    elif isinstance(value, str) and value.startswith('customers/'):
        yield value


def _new_graph(operations, account_id):
    """Only this new graph may be mutated, with parents before references."""
    if not isinstance(operations, list) or not 1 <= len(operations) <= 10_000:
        _fail('funded_creation_graph_invalid')
    seen, ids, counts = set(), set(), {}
    campaign = budget = label = None
    groups = set()
    for operation in operations:
        if not isinstance(operation, dict) or len(operation) != 1:
            _fail('funded_creation_graph_invalid')
        kind, spec = next(iter(operation.items()))
        if kind not in _ALLOWED or not isinstance(spec, dict):
            _fail('funded_creation_operation_forbidden')
        counts[kind] = counts.get(kind, 0) + 1
        if kind == 'campaignConversionGoalOperation':
            target = (spec.get('update') or {}).get('resourceName')
            expected = rf'customers/{account_id}/campaignConversionGoals/{re.escape(campaign.rsplit("/",1)[-1])}~[A-Z_]+~[A-Z_]+' if campaign else r'(?!)'
            if (set(spec) != {'update','updateMask'} or spec['updateMask'] != 'biddable'
                or not isinstance(spec.get('update'),dict) or set(spec['update']) != {'resourceName','biddable'}
                or type(spec['update']['biddable']) is not bool or not isinstance(target,str)
                or re.fullmatch(expected,target) is None):
                _fail('funded_creation_existing_target_forbidden')
            continue
        if set(spec) != {'create'} or not isinstance(spec['create'],dict):
            _fail('funded_creation_operation_forbidden')
        create = spec['create']
        own = create.get('resourceName')
        if kind in {'campaignBudgetOperation','campaignOperation','adGroupOperation','labelOperation'}:
            collection = {'campaignBudgetOperation':'campaignBudgets','campaignOperation':'campaigns',
                'adGroupOperation':'adGroups','labelOperation':'labels'}[kind]
            match = re.fullmatch(rf'customers/{account_id}/{collection}/(-[1-9][0-9]{{0,18}})',own or '')
            if not match or match[1] in ids:
                _fail('funded_creation_existing_target_forbidden')
            ids.add(match[1])
        elif own is not None:
            _fail('funded_creation_existing_target_forbidden')
        # Positive or foreign customer references cannot hide in nested fields.
        for resource in _resource_strings(create):
            if resource != own and resource not in seen:
                _fail('funded_creation_reference_unproved')
        if kind == 'campaignBudgetOperation':
            if create.get('period') != 'DAILY' or create.get('explicitlyShared') is not False:
                _fail('funded_creation_budget_invalid')
            budget = own
        elif kind == 'campaignOperation':
            if (not budget or create.get('campaignBudget') != budget or create.get('status') != 'ENABLED'
                or create.get('advertisingChannelType') != 'SEARCH'):
                _fail('funded_creation_graph_invalid')
            campaign = own
        elif kind == 'adGroupOperation':
            if not campaign or create.get('campaign') != campaign or create.get('status') != 'ENABLED':
                _fail('funded_creation_graph_invalid')
            groups.add(own)
        elif kind in {'adGroupAdOperation','adGroupCriterionOperation'}:
            if create.get('adGroup') not in groups:
                _fail('funded_creation_reference_unproved')
            if kind == 'adGroupAdOperation' and (create.get('status') != 'ENABLED'
                or not isinstance(create.get('ad'),dict) or 'responsiveSearchAd' not in create['ad']):
                _fail('funded_creation_search_rsa_only')
        elif kind == 'labelOperation':
            label = own
        elif kind == 'campaignLabelOperation':
            if not campaign or not label or create.get('campaign') != campaign or create.get('label') != label:
                _fail('funded_creation_reference_unproved')
        elif kind == 'campaignCriterionOperation' and (not campaign or create.get('campaign') != campaign):
            _fail('funded_creation_reference_unproved')
        if own:
            seen.add(own)
    if (any(counts.get(kind) != 1 for kind in ('campaignBudgetOperation','campaignOperation','labelOperation','campaignLabelOperation'))
        or not groups or counts.get('adGroupAdOperation') != len(groups)):
        _fail('funded_creation_graph_invalid')


def prepare_google_funded_creation_intent(*, grant, draft, current):
    if not isinstance(current,datetime) or current.tzinfo is None:
        _fail('funded_creation_clock_invalid')
    if not isinstance(grant,dict) or type(grant.get('schema_version')) is not int:
        _fail('funded_creation_grant_invalid')
    try:
        approved = FundedCreationGrant.model_validate(grant)
        ZoneInfo(approved.time_zone)
    except (ValidationError, ValueError, TypeError, KeyError):
        _fail('funded_creation_grant_invalid')
    start, end = _instant(approved.valid_from), _instant(approved.expires_at)
    if (approved.reviewed_by != approved.user_id or start is None or end is None
        or not start <= current.astimezone(timezone.utc) < end or not end <= start + timedelta(days=30)
        or approved.request_key.startswith(('google_create_','google_funded_create_'))):
        _fail('funded_creation_grant_invalid')
    canonical = _draft(draft)
    if canonical.account_id != approved.account_id or funded_creation_draft_digest(canonical) != approved.draft_digest:
        _fail('funded_creation_grant_binding_invalid')
    amount, campaign_cap, account_cap = (Decimal(getattr(approved,key)) for key in
        ('budget_native','max_campaign_daily_budget_native','max_account_daily_budget_native'))
    if not 0 < amount <= campaign_cap <= account_cap or Decimal(canonical.budget_native) != amount:
        _fail('funded_creation_budget_invalid')
    binding = {'grant':approved.model_dump(mode='json'),'draft_digest':approved.draft_digest,'origin':_ORIGIN}
    identity = 'google_funded_create_' + _digest(binding)
    try:
        operations = deepcopy(build_campaign_operations(canonical,approved.account_id))
    except (GoogleCampaignBuildError, ValueError, TypeError):
        _fail('funded_creation_draft_invalid')
    creates = [operation['campaignOperation']['create'] for operation in operations if 'campaignOperation' in operation]
    if len(creates) != 1 or creates[0].get('status') != 'PAUSED':
        _fail('funded_creation_graph_invalid')
    creates[0]['status'] = 'ENABLED'
    label = f'customers/{approved.account_id}/labels/-9000000000000'
    operations += [{'labelOperation':{'create':{'resourceName':label,'name':'MEZAN-FUNDED-'+identity[-32:]}}},
        {'campaignLabelOperation':{'create':{'campaign':creates[0]['resourceName'],'label':label}}}]
    _new_graph(operations,approved.account_id)
    budgets=[operation['campaignBudgetOperation']['create'] for operation in operations if 'campaignBudgetOperation' in operation]
    if len(budgets)!=1 or budgets[0].get('amountMicros')!=native_to_micros(approved.budget_native):
        _fail('funded_creation_budget_invalid')
    request={'mutateOperations':operations,'partialFailure':False,'responseContentType':'MUTABLE_RESOURCE'}
    return {'schema_version':1,'provider':'google_ads','origin':_ORIGIN,'creation_identity':identity,
        'mode':_MODE,'account_id':approved.account_id,'user_id':approved.user_id,
        'binding':binding,'draft':canonical.model_dump(mode='json'),'request':request,
        'graph_digest':_digest({'account_id':approved.account_id,'request':request}),
        'request_count':1,'executable':False,'funded_dispatch_authorized':False,'provider_writes':0,
        'persisted_owner_authorization_verified':False,'native_configuration_verified':False,
        'required_proofs':['persisted_owner_grant','common_guard_reservation_and_floor',
            'fresh_product_and_native_configuration','prepared_auth_and_immediate_dispatch']}


def revalidate_google_funded_creation_intent(intent, *, grant, draft, current):
    rebuilt=prepare_google_funded_creation_intent(grant=grant,draft=draft,current=current)
    if not isinstance(intent,dict) or _digest(intent)!=_digest(rebuilt):
        _fail('funded_creation_intent_changed')
    return rebuilt
