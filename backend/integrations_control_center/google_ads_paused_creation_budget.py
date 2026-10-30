"""Internal PAUSED creation accounting. No provider/launch/enable authority."""
from decimal import Decimal
import re

from .google_ads_management import digest


class SessionCollection:
    """Public Motor calls explicitly bound to one existing guard session."""
    def __init__(self, collection, session):
        self.collection, self.session = collection, session

    async def find_one(self, *args, **kwargs):
        return await self.collection.find_one(*args, **kwargs, session=self.session)

    def find(self, *args, **kwargs):
        return self.collection.find(*args, **kwargs, session=self.session)

    async def update_one(self, *args, **kwargs):
        return await self.collection.update_one(*args, **kwargs, session=self.session)

    async def insert_one(self, *args, **kwargs):
        return await self.collection.insert_one(*args, **kwargs, session=self.session)

    async def create_index(self, *args, **kwargs):
        raise AssertionError('DDL is forbidden inside the common budget transaction')


class SessionDatabase:
    def __init__(self, database, session):
        self.database, self.session = database, session

    def __getitem__(self, name):
        return SessionCollection(self.database[name], self.session)

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return self[name]


def allocation_id(identity, claim_id):
    return 'paused_creation_' + digest({'identity': identity, 'claim_id': claim_id})


def standing_lineage(entry):
    # Integrity linkage to the original guard-created claim/resource. This is
    # not a signature, reusable authority token or external-writer fence.
    return digest({key: entry.get(key) for key in ('user_id', 'account_id', 'allocation_id',
        'operation_id', 'campaign_id', 'proposal_id', 'execution_claim_id', 'currency_code',
        'action', 'budget_resource', 'budget_native', 'claim_digest', 'created_at')})


def common_caps(snapshot, reference, *, funded_grant=None):
    from .google_ads_automation_budget_guard import _money, _fail
    caps = []
    if funded_grant is not None:
        from .google_ads_funded_creation_contract import FundedCreationGrant
        grant=FundedCreationGrant.model_validate(funded_grant)
        if (grant.user_id!=snapshot.user_id or grant.account_id!=snapshot.account_id or grant.reviewed_by!=snapshot.user_id
            or any(getattr(grant,key)!=reference.get(key) for key in ('currency_code','time_zone'))):
            _fail('google_budget_guard_funded_binding_unproved')
        caps.append(_money(grant.max_account_daily_budget_native))
    for entry in snapshot.guard['allocations'].values():
        if entry.get('kind')=='funded_new_creation' and entry.get('state')!='settled':
            from .google_ads_funded_creation_authority import allocation_lineage
            if (entry.get('reservation_lineage_digest')!=allocation_lineage(entry)
                or any(entry.get(key)!=value for key,value in {'user_id':snapshot.user_id,'account_id':snapshot.account_id,
                    'currency_code':reference.get('currency_code')}.items())):
                _fail('google_budget_guard_funded_binding_unproved')
            caps.append(_money(entry.get('account_cap_native')))
    for policy in (snapshot.standing_policy, snapshot.creation_policy):
        if policy is None:
            continue
        if (policy.get('user_id') != snapshot.user_id or policy.get('account_id') != snapshot.account_id
            or policy.get('updated_by') != snapshot.user_id
            or any(policy.get(key) != reference.get(key) for key in ('currency_code', 'time_zone'))):
            _fail('google_budget_guard_policy_unproved')
        # A stored owner cap is an explicit bound, even after revocation. Never
        # derive a monetary amount from quotas, missing fields or defaults.
        caps.append(_money(policy.get('max_account_daily_budget_native')))
    if not caps:
        _fail('google_budget_guard_policy_unproved')
    return min(caps)


def account_exposure(snapshot, inventory, reference, current, *, funded_grant=None):
    """One maximum per native campaign/resource, then sum across the account.

    Original creation amount and observed native amount are different facts.
    Unreflected standing targets remain held; no unknown outcome is released.
    External Google writers cannot be serialized by this local accounting.
    """
    from .google_ads_automation_budget_guard import _money, _fail, _instant, thaw_snapshot
    from .google_ads_automation_creation_authority import _policy_ledger
    observed = _instant(inventory.get('observed_at'))
    if (inventory.get('complete') is not True or inventory.get('source') != 'google_ads_api'
        or inventory.get('account_id') != snapshot.account_id or observed is None
        or not 0 <= (current - observed).total_seconds() < 90
        or any(inventory.get(key) != reference.get(key) for key in ('currency_code', 'time_zone'))):
        _fail('google_budget_guard_inventory_unproved')
    rows = inventory.get('campaigns')
    if not isinstance(rows, list) or len(rows) > 500:
        _fail('google_budget_guard_inventory_unproved')
    campaigns, resources, exposure = {}, set(), {}
    for row in rows:
        cid = row.get('campaign_id') if isinstance(row, dict) else None
        if (not isinstance(cid, str) or not re.fullmatch(r'[0-9]{1,30}', cid)
            or cid in campaigns or row.get('status') not in {'ENABLED', 'PAUSED', 'REMOVED'}):
            _fail('google_budget_guard_inventory_unproved')
        campaigns[cid] = row
        if row['status'] != 'REMOVED':
            resource = row.get('budget_resource')
            if (row.get('budget_mode') != 'DAILY' or row.get('shared_budget') is not False
                or not isinstance(resource, str) or not re.fullmatch(rf'customers/{snapshot.account_id}/campaignBudgets/[0-9]+', resource)
                or resource in resources):
                _fail('google_budget_guard_inventory_unproved')
            resources.add(resource)
            amount = _money(row.get('budget_native'))
            exposure[cid] = amount if row['status'] == 'ENABLED' else Decimal(0)
    allocations = thaw_snapshot(snapshot.guard)['allocations']
    policy = thaw_snapshot(snapshot.creation_policy)
    ledger, _ = _policy_ledger(policy) if policy is not None else ({}, 0)
    creation_ids, unassigned, drift = set(), Decimal(0), []
    for identity, entry in ledger.items():
        matching = [(key, value) for key, value in allocations.items()
            if value.get('kind') == 'paused_creation' and value.get('creation_identity') == identity]
        if entry['state'] == 'no_campaign':
            if matching and (len(matching) != 1 or matching[0][1].get('state') != 'settled'):
                _fail('google_budget_guard_legacy_creation_unmapped')
            creation_ids.update(key for key, _ in matching)
            continue
        if len(matching) != 1:
            _fail('google_budget_guard_legacy_creation_unmapped')
        key, allocation = matching[0]
        creation_ids.add(key)
        if (allocation.get('creation_entry_digest') != digest(entry)
            or allocation.get('execution_claim_id') != entry['execution_claim_id']
            or allocation.get('proposal_id') != entry['proposal_id']
            or allocation.get('currency_code') != reference['currency_code']
            or _money(allocation.get('original_creation_budget_native')) != _money(entry['reservation_native'])):
            _fail('google_budget_guard_creation_binding_unproved')
        hold = _money(entry['reservation_native'])
        if entry['state'] == 'reserved':
            if allocation.get('state') != 'reserved' or policy.get('_creation_claim') != entry:
                _fail('google_budget_guard_creation_binding_unproved')
            unassigned += hold
            continue
        cid, resource = entry.get('created_campaign_id'), allocation.get('budget_resource')
        native = campaigns.get(cid)
        settled = _instant(entry.get('settled_at'))
        if (allocation.get('state') != 'native_paused' or allocation.get('campaign_id') != cid
            or not isinstance(resource, str) or native is None or native.get('budget_resource') != resource
            or settled is None or observed < settled or entry.get('initial_status') != 'PAUSED'
            or entry.get('native_correlation_verified') is not True):
            _fail('google_budget_guard_creation_binding_unproved')
        if native['status'] == 'REMOVED':
            # Current production reader excludes REMOVED. Neither absence nor
            # an unsupported synthetic removal proof can release this hold.
            _fail('google_budget_guard_removed_resource_unproved')
        amount = _money(native['budget_native'])
        exposure[cid] = max(exposure.get(cid, Decimal(0)), hold, amount)
        if amount != hold:
            drift.append(cid)
    if any(value.get('kind') == 'paused_creation' and key not in creation_ids
        for key, value in allocations.items()):
        _fail('google_budget_guard_creation_binding_unproved')
    funded_ids=set()
    for key,entry in allocations.items():
        if entry.get('kind')!='funded_new_creation':continue
        from .google_ads_funded_creation_authority import allocation_lineage
        if (entry.get('reservation_lineage_digest')!=allocation_lineage(entry)
            or entry.get('allocation_id')!=key or entry.get('user_id')!=snapshot.user_id
            or entry.get('account_id')!=snapshot.account_id or entry.get('currency_code')!=reference['currency_code']
            or not isinstance(entry.get('grant_key'),str) or not isinstance(entry.get('graph_digest'),str)
            or not re.fullmatch(r'[a-f0-9]{64}',entry['graph_digest'])):
            _fail('google_budget_guard_funded_binding_unproved')
        funded_ids.add(key);hold=_money(entry.get('budget_native'))
        if entry.get('state')=='reserved':
            if entry.get('campaign_id') is not None or entry.get('budget_resource') is not None:
                _fail('google_budget_guard_funded_binding_unproved')
            unassigned+=hold
        elif entry.get('state')=='native_enabled':
            cid=entry.get('campaign_id');native=campaigns.get(cid);settled=_instant(entry.get('settled_at'))
            if (not native or native['status']=='REMOVED' or native.get('budget_resource')!=entry.get('budget_resource')
                or settled is None or observed<settled or not isinstance(entry.get('settlement_proof_digest'),str)
                or not re.fullmatch(r'[a-f0-9]{64}',entry['settlement_proof_digest'])
                or any(other.get('campaign_id')==cid and other.get('kind') in {'paused_creation','funded_new_creation'}
                    for other_key,other in allocations.items() if other_key!=key)):
                _fail('google_budget_guard_funded_binding_unproved')
            exposure[cid]=max(exposure.get(cid,Decimal(0)),hold,_money(native['budget_native']))
        elif (entry.get('state')!='settled' or entry.get('outcome') not in {'cancelled_before_http','provider_rejected'}
            or not isinstance(entry.get('settlement_proof_digest'),str)
            or not re.fullmatch(r'[a-f0-9]{64}',entry['settlement_proof_digest'])):
            _fail('google_budget_guard_funded_binding_unproved')
    targets, reflected = set(), []
    for key, entry in allocations.items():
        if key in creation_ids or key in funded_ids or entry['state'] in {'cancelled_before_http', 'settled'}:
            continue
        cid, native = entry.get('campaign_id'), campaigns.get(entry.get('campaign_id'))
        if (cid in targets or native is None or native['status'] == 'REMOVED'
            or entry.get('kind') != 'standing' or entry.get('budget_resource') != native.get('budget_resource')
            or entry.get('currency_code') != reference['currency_code']
            or entry.get('user_id') != snapshot.user_id or entry.get('account_id') != snapshot.account_id
            or any(not isinstance(entry.get(field), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', entry[field])
                for field in ('allocation_id','operation_id','proposal_id','execution_claim_id'))
            or _instant(entry.get('created_at')) is None
            or not isinstance(entry.get('claim_digest'), str) or not re.fullmatch(r'[a-f0-9]{64}', entry['claim_digest'])
            or entry.get('reservation_lineage_digest') != standing_lineage(entry)):
            _fail('google_budget_guard_pending_unproved')
        amount = _money(entry.get('budget_native'))
        if entry['state'] == 'native_enabled':
            settled = _instant(entry.get('settled_at'))
            lineage = entry.get('settlement_lineage_digest')
            if (settled is None or observed < settled or not isinstance(lineage, str)
                or not re.fullmatch(r'[a-f0-9]{64}', lineage)):
                _fail('google_budget_guard_reflection_unproved')
            if ((entry.get('action') == 'pause' and native['status'] == 'PAUSED')
                or entry.get('action') != 'pause' and native['status'] == 'ENABLED'
                and _money(native['budget_native']) == amount):
                reflected.append(key)
                continue
        targets.add(cid)
        # One maximum covers native, creation hold and standing target. Their
        # distinct bindings and clocks remain recorded independently.
        exposure[cid] = max(exposure.get(cid, Decimal(0)), amount)
    return {'total': sum(exposure.values(), unassigned), 'campaigns': campaigns,
        'exposure_by_campaign': exposure, 'targeted_campaign_ids': targets,
        'reflected_allocation_ids': reflected, 'native_amount_drift_campaign_ids': drift,
        'limit': common_caps(snapshot, reference, funded_grant=funded_grant)}
