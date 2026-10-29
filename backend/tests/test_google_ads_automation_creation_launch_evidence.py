"""Read-only launch facts cannot authorize spend or invent external history."""
from copy import deepcopy
from datetime import timedelta
import inspect
import os
import uuid

import pytest

from integrations_control_center import google_ads_automation_creation_contract as creation
from integrations_control_center import google_ads_automation_creation_launch_evidence as evidence
from tests.test_google_ads_automation_creation_contract import NOW, prepared_case, valid_ledger, valid_proposal, prove_inventory


def evidence_case():
    values = prepared_case()
    intent = creation.prepare_google_creation_intent(**values)
    policy = deepcopy(values['policy']); policy.update(auto_enable_new=True, revision=3, activation_revision=3)
    proposal = valid_proposal(intent)
    # The generic ownership fixture deliberately doesn't model child result
    # identities. This native read fixture uses actual collection/parent shapes.
    proposal['resource_names'][2] = 'customers/123/campaignConversionGoals/999~PURCHASE~WEBSITE'
    proposal['resource_names'][3] = 'customers/123/campaignCriteria/999~103'
    proposal['resource_names'][5] = 'customers/123/adGroupCriteria/104~105'
    ledger = valid_ledger(intent)
    ledger.update(state='owned', reservation_state='paused_created', reservation_native='15', reservation_currency_code='SAR')
    from integrations_control_center.google_ads_verification import _replace
    names, operations = proposal['resource_names'], proposal['operations']
    mapping = {body['create']['resourceName']: name for op, name in zip(operations, names)
        for body in op.values() if 'create' in body and 'resourceName' in body['create']}
    materialized = {}
    for op, name in zip(operations, names):
        kind, body = next(iter(op.items()))
        row = _replace(body.get('create') or body.get('update'), mapping)
        row['resourceName'] = name
        materialized.setdefault(kind.removesuffix('Operation'), []).append(row)
    campaign, budget = materialized['campaign'][0], materialized['campaignBudget'][0]
    campaign.update(id='999', biddingStrategyType='MAXIMIZE_CONVERSION_VALUE')
    budget.update(referenceCount='1', period='DAILY')
    groups = materialized['adGroup']
    for group in groups:
        group.update(id=group['resourceName'].rsplit('/', 1)[-1], criteria=[], group_assets=[])
        for criterion in materialized['adGroupCriterion']:
            criterion['type'] = 'KEYWORD'; criterion['negative'] = False
            if criterion['adGroup'] == group['resourceName']: group['criteria'].append(criterion)
    for criterion in materialized['campaignCriterion']:
        criterion.update(type='LOCATION', status='ENABLED', negative=False)
    for row in materialized['adGroupAd']:
        row['ad'].update(id=row['resourceName'].rsplit('~', 1)[-1], type='RESPONSIVE_SEARCH_AD')
    for row in materialized['campaignConversionGoal']:
        row.update(campaign=campaign['resourceName'], category='PURCHASE', origin='WEBSITE')
    snapshot = {'campaign': campaign, 'campaign_budget': budget, 'groups': groups,
        'ads': materialized['adGroupAd'], 'criteria': materialized['campaignCriterion'],
        'conversion_goals': materialized['campaignConversionGoal'],
        'conversion_goal_config': [{'resourceName': 'customers/123/conversionGoalCampaignConfigs/999',
            'campaign': campaign['resourceName'], 'goalConfigLevel': 'CAMPAIGN'}],
        'campaign_assets': [], 'duplicate_controls_complete': True, 'duplicate_control_gaps': [], 'field_gaps': []}
    prior = {'campaign': {'id': '456', 'resourceName': 'customers/123/campaigns/456', 'status': 'ENABLED',
        'advertisingChannelType': 'SEARCH', 'campaignBudget': 'customers/123/campaignBudgets/456'},
        'campaignBudget': {'resourceName': 'customers/123/campaignBudgets/456', 'period': 'DAILY',
            'amountMicros': '40000000', 'explicitlyShared': False, 'referenceCount': '1'}}
    purchase_context = deepcopy(values['native_context'])
    purchase_context['campaigns'].append({**deepcopy(purchase_context['campaigns'][0]), 'campaign_id': '999',
        'status': 'PAUSED', 'budget_native': '15'})
    return dict(user_id='owner', account_id='123', campaign_id='999', policy=policy, creation_intent=intent,
        ledger_entry=ledger, original_proposal=proposal, product=deepcopy(values['product']),
        metadata={'account_id': '123', 'identity_verified': True, 'currency_code': 'SAR', 'time_zone': 'Asia/Riyadh'},
        inventory_rows=[prior, {'campaign': deepcopy(campaign), 'campaignBudget': deepcopy(budget)}],
        inventory_complete=True, snapshot=snapshot,
        verification={'readback_independent': True, 'verified_resources': list(names), 'mismatches': [], 'coverage_gaps': []},
        purchase_context=purchase_context, read_started_at=NOW, read_finished_at=NOW, current=NOW)


def blocked(values, code=None):
    with pytest.raises(evidence.LaunchEvidenceError) as caught:
        evidence.prepare_launch_evidence(**values)
    if code: assert caught.value.code == code


def test_owned_search_rsa_current_configuration_and_inventory_are_readonly_evidence():
    values = evidence_case(); original = deepcopy(values)
    result = evidence.prepare_launch_evidence(**values)
    assert values == original
    assert result['created_by_mezan_verified'] is True
    assert result['launch_history_verified'] is False
    assert result['ready_for_enable_preview'] is False
    assert result['executable'] is False and result['authority_required'] is True and result['provider_writes'] == 0
    assert result['binding']['product']['inventory_digest']
    evidence.validate_launch_evidence(result, policy=values['policy'], creation_intent=values['creation_intent'],
        ledger_entry=values['ledger_entry'], original_proposal=values['original_proposal'], campaign_id='999', current=NOW)


@pytest.mark.parametrize('change,code', [
    (lambda v: v['original_proposal'].update(actor_id='owner'), 'launch_creation_ownership_unproved'),
    (lambda v: v.update(ledger_entry={}), 'launch_creation_ownership_unproved'),
    (lambda v: v['metadata'].update(account_id='456'), 'launch_native_identity_unproved'),
    (lambda v: v['product'].update(user_id='other'), 'launch_product_scope_unproved'),
    (lambda v: v['product'].update(status='inactive'), 'launch_product_not_public'),
    (lambda v: v['product'].update(archived=True), 'launch_product_not_public'),
    (lambda v: v['product'].update(canonical_url='https://amasi-sa.com/other'), 'launch_product_url_changed'),
    (lambda v: v['product'].update(name='new name'), 'launch_product_copy_changed'),
    (lambda v: v['product'].update(last_synced_at=(NOW-timedelta(hours=1)).isoformat()), 'launch_product_stale'),
    (lambda v: v['product'].update(quantity=0), 'launch_inventory_unproved'),
    (lambda v: v.update(inventory_complete=False), 'launch_native_inventory_unproved'),
    (lambda v: v['inventory_rows'][0]['campaignBudget'].update(referenceCount=True), 'launch_native_budget_unproved'),
    (lambda v: v['inventory_rows'][0]['campaignBudget'].update(referenceCount='2'), 'launch_native_inventory_unproved'),
    (lambda v: v['inventory_rows'][0]['campaignBudget'].update(explicitlyShared=True), 'launch_native_inventory_unproved'),
    (lambda v: v['snapshot']['campaign'].update(status='ENABLED'), 'launch_native_target_changed'),
    (lambda v: v['snapshot'].update(duplicate_controls_complete=False), 'launch_native_configuration_unproved'),
    (lambda v: v['verification'].update(coverage_gaps=['field missing']), 'launch_native_configuration_unproved'),
])
def test_unproved_ownership_inventory_scope_and_native_fields_fail_closed(change, code):
    values = evidence_case(); change(values); blocked(values, code)


@pytest.mark.parametrize('where', ['groups', 'ads', 'criteria', 'keywords'])
def test_extra_and_removed_resources_are_detected_even_if_original_verification_passed(where):
    values = evidence_case()
    rows = values['snapshot']['groups'][0]['criteria'] if where == 'keywords' else values['snapshot'][where]
    extra = deepcopy(rows[0]); extra['resourceName'] += '8'; rows.append(extra)
    blocked(values)
    values = evidence_case()
    rows = values['snapshot']['groups'][0]['criteria'] if where == 'keywords' else values['snapshot'][where]
    rows.clear(); blocked(values)


def test_original_successful_mutation_and_asserted_flags_cannot_replace_current_readback():
    values = evidence_case(); values['snapshot'] = {'creation_configuration_verified': True, 'creation_draft_digest': values['creation_intent']['binding']['draft_digest']}
    blocked(values, 'launch_native_configuration_unproved')


@pytest.mark.parametrize('change', [
    lambda v: v['snapshot']['campaign'].update(trackingUrlTemplate='https://tracker.example/{lpurl}'),
    lambda v: v['snapshot']['campaign']['networkSettings'].update(targetContentNetwork=True),
    lambda v: v['snapshot']['ads'][0]['ad'].update(finalUrls=['https://amasi-sa.com/other']),
    lambda v: v['snapshot']['campaign'].update(unknownMutableControl=True),
])
def test_added_or_changed_native_controls_cannot_hide_behind_original_resource_ids(change):
    values = evidence_case(); change(values); blocked(values)


@pytest.mark.parametrize('start,finish,current', [
    (NOW-timedelta(seconds=90), NOW, NOW), (NOW, NOW, NOW-timedelta(seconds=1)),
    (NOW, NOW-timedelta(seconds=1), NOW), (NOW, NOW+timedelta(seconds=90), NOW+timedelta(seconds=90)),
    (NOW.replace(tzinfo=None), NOW, NOW), (NOW, NOW+timedelta(seconds=1), NOW),
])
def test_freshness_uses_earliest_read_and_final_clock(start, finish, current):
    values = evidence_case(); values.update(read_started_at=start, read_finished_at=finish, current=current)
    blocked(values, 'launch_evidence_stale')


def test_read_timestamps_change_without_changing_semantics_but_stock_and_revision_do():
    values = evidence_case(); first = evidence.prepare_launch_evidence(**values)
    later = NOW+timedelta(seconds=5); values.update(read_started_at=later, read_finished_at=later, current=later)
    values['purchase_context']['observed_at'] = later.isoformat()
    for row in values['purchase_context']['campaigns']: row['observed_at'] = later.isoformat()
    second = evidence.prepare_launch_evidence(**values)
    assert first['semantic_digest'] == second['semantic_digest'] and first['observed_at'] != second['observed_at']
    values['product'].update(quantity=3); prove_inventory(values['product'])
    third = evidence.prepare_launch_evidence(**values)
    assert third['semantic_digest'] != first['semantic_digest']
    values['policy'].update(revision=4, activation_revision=4)
    fourth = evidence.prepare_launch_evidence(**values)
    assert fourth['semantic_digest'] != third['semantic_digest']


def test_later_validation_rechecks_deadlines_and_rejects_changed_or_fabricated_binding():
    values = evidence_case(); proof = evidence.prepare_launch_evidence(**values)
    arguments = dict(policy=values['policy'], creation_intent=values['creation_intent'],
        ledger_entry=values['ledger_entry'], original_proposal=values['original_proposal'], campaign_id='999', current=NOW)
    with pytest.raises(evidence.LaunchEvidenceError, match='launch_evidence_stale'):
        evidence.validate_launch_evidence(proof, **{**arguments, 'current': NOW+timedelta(seconds=90)})
    proof['binding']['product']['final_url'] = 'https://amasi-sa.com/other'
    with pytest.raises(evidence.LaunchEvidenceError, match='launch_evidence_binding_changed'):
        evidence.validate_launch_evidence(proof, **arguments)


@pytest.mark.parametrize('change', [
    lambda v: v['purchase_context'].update(purchase_value_bidding_verified=False),
    lambda v: v['purchase_context']['campaigns'][0].update(budget_native='39'),
    lambda v: v['purchase_context']['campaigns'].append(deepcopy(v['purchase_context']['campaigns'][0])),
    lambda v: v['inventory_rows'].append(deepcopy(v['inventory_rows'][0])),
    lambda v: v['inventory_rows'][0]['campaignBudget'].update(amountMicros='-1'),
    lambda v: v['inventory_rows'][0]['campaignBudget'].update(resourceName='customers/456/campaignBudgets/456'),
    lambda v: v['snapshot']['campaign_budget'].update(referenceCount='2'),
])
def test_independent_native_reads_must_agree_and_be_complete(change):
    values = evidence_case(); change(values); blocked(values)


def test_fresh_product_unavailability_is_not_confused_with_changed_inventory_proof():
    values = evidence_case(); values['product']['quantity'] = 0; prove_inventory(values['product'])
    blocked(values, 'launch_product_unavailable')


class Cursor:
    def __init__(self, rows): self.rows = rows
    async def to_list(self, length): return deepcopy(self.rows[:length])


def matches(row, query):
    return all(any(matches(row, clause) for clause in value) if key == '$or' else row.get(key) == value
        for key, value in query.items())


class Collection:
    def __init__(self, rows=()): self.rows = deepcopy(list(rows)); self.reads = []
    def find(self, query, projection):
        self.reads.append((deepcopy(query), deepcopy(projection)))
        return Cursor([{key: deepcopy(value) for key, value in row.items() if projection.get(key)}
            for row in self.rows if matches(row, query)])
    async def find_one(self, query, projection):
        rows = await self.find(query, projection).to_list(length=1)
        return rows[0] if rows else None
    def __getattr__(self, name):
        raise AssertionError('Unexpected database operation: ' + name)


class DB(dict):
    def __missing__(self, key): self[key] = Collection(); return self[key]


class Client:
    customer_id = '123'
    def __init__(self, values, hook=None): self.values = values; self.reads = []; self.hook = hook
    async def account_metadata(self): self.reads.append('metadata'); return deepcopy(self.values['metadata'])
    async def search(self, query, *, max_rows):
        self.reads.append('inventory')
        assert 'WHERE' not in query and 'segments.' not in query and max_rows == 501
        assert 'campaign_budget.reference_count' in query and 'campaign_budget.explicitly_shared' in query
        return deepcopy(self.values['inventory_rows']), self.values['inventory_complete']
    async def campaign_snapshot(self, campaign_id, *, children, duplicate_controls):
        self.reads.append('snapshot'); assert campaign_id == '999' and children is True and duplicate_controls is True
        return deepcopy(self.values['snapshot'])
    async def verify_operations(self, operations, resource_names):
        self.reads.append('verification')
        assert operations == self.values['original_proposal']['operations']
        assert resource_names == self.values['original_proposal']['resource_names']
        if self.hook:
            pending = self.hook()
            if inspect.isawaitable(pending): await pending
        return True, deepcopy(self.values['verification'])
    async def mutate(self, *args, **kwargs): raise AssertionError('No validate-only or mutation is allowed')


def reader_case(monkeypatch):
    values = evidence_case(); identity = values['creation_intent']['creation_identity']
    proposal = values['original_proposal']
    proposal.update(currency='SAR', time_zone='Asia/Riyadh', auth_epoch='opaque-test-epoch',
        draft_id=identity, draft_revision=1, reason='approved paused creation',
        expires_at=(NOW+timedelta(minutes=30)).isoformat())
    from integrations_control_center.google_ads_management import digest, immutable
    proposal['confirmation_digest'] = digest(immutable(proposal))
    policy = {**deepcopy(values['policy']), '_creation_ledger': {identity: deepcopy(values['ledger_entry'])}}
    saved = {'user_id': 'owner', 'account_id': '123', 'creation_identity': identity,
        'proposal_id': proposal['proposal_id'], 'intent': values['creation_intent']}
    db = DB({evidence.CREATION_POLICIES: Collection([policy]), evidence.CREATION_INTENTS: Collection([saved]),
        evidence.PROPOSALS: Collection([proposal]), evidence.PRODUCT_COLLECTION: Collection([values['product']])})
    async def context(client, **kwargs):
        assert kwargs['read_started_at'] == NOW and kwargs['metadata'] == values['metadata']
        client.reads.append('purchase-context'); return deepcopy(values['purchase_context'])
    monkeypatch.setattr('integrations_control_center.google_ads_automation_creation_plan.read_google_creation_native_context', context)
    return values, db, dict(user_id='owner', account_id='123', campaign_id='999', creation_identity=identity, clock=lambda: NOW)


@pytest.mark.asyncio
async def test_reader_projects_original_approval_and_current_catalog_and_makes_only_reads(monkeypatch):
    values, db, args = reader_case(monkeypatch); client = Client(values)
    result = await evidence.read_google_creation_launch_evidence(db, client=client, **args)
    assert client.reads == ['metadata', 'purchase-context', 'inventory', 'snapshot', 'verification']
    assert result['created_by_mezan_verified'] is True and result['launch_history_verified'] is False
    assert all(len(collection.reads) == 2 for collection in db.values())
    assert all(query.get('user_id') == 'owner' for collection in db.values() for query, _ in collection.reads)
    assert db[evidence.PROPOSALS].reads[0][1]['auth_epoch'] == 1
    assert db[evidence.PRODUCT_COLLECTION].reads[0][1]['inventory_evidence_v1'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['policy', 'product', 'intent', 'proposal'])
async def test_reader_rejects_storage_changed_while_native_reads_are_in_flight(monkeypatch, source):
    values, db, args = reader_case(monkeypatch)
    def change():
        if source == 'policy': db[evidence.CREATION_POLICIES].rows[0]['revision'] += 1
        elif source == 'product': db[evidence.PRODUCT_COLLECTION].rows[0]['archived'] = True
        elif source == 'intent': db[evidence.CREATION_INTENTS].rows[0]['intent']['binding']['product']['final_url'] += '/changed'
        else: db[evidence.PROPOSALS].rows[0]['status'] = 'uncertain'
    with pytest.raises(evidence.LaunchEvidenceError, match='launch_storage_changed_during_read'):
        await evidence.read_google_creation_launch_evidence(db, client=Client(values, hook=change), **args)


@pytest.mark.asyncio
async def test_missing_foreign_or_duplicate_product_cannot_call_google(monkeypatch):
    for replacement in ([], [{'user_id': 'other', 'salla_product_id': '1'}]):
        values, db, args = reader_case(monkeypatch); db[evidence.PRODUCT_COLLECTION].rows = replacement
        client = Client(values)
        with pytest.raises(evidence.LaunchEvidenceError, match='launch_product_identity_unproved'):
            await evidence.read_google_creation_launch_evidence(db, client=client, **args)
        assert client.reads == []
    values, db, args = reader_case(monkeypatch)
    db[evidence.PRODUCT_COLLECTION].rows.append(deepcopy(db[evidence.PRODUCT_COLLECTION].rows[0]))
    client = Client(values)
    with pytest.raises(evidence.LaunchEvidenceError, match='launch_product_identity_unproved'):
        await evidence.read_google_creation_launch_evidence(db, client=client, **args)
    assert client.reads == []


@pytest.mark.asyncio
async def test_reader_final_clock_cannot_hide_slow_successful_reads(monkeypatch):
    values, db, args = reader_case(monkeypatch)
    times = iter([NOW, NOW+timedelta(seconds=89), NOW+timedelta(seconds=90)])
    with pytest.raises(evidence.LaunchEvidenceError, match='launch_evidence_stale'):
        await evidence.read_google_creation_launch_evidence(db, client=Client(values), **{**args, 'clock': lambda: next(times)})


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['verified_read', 'duplicate_product', 'foreign_proposal', 'policy_drift'])
@pytest.mark.skipif(not os.getenv('GOOGLE_ADS_TEST_MONGO_URI'), reason='Disposable loopback Mongo is required')
async def test_real_mongo_projected_evidence_remains_readonly_and_scoped(monkeypatch, scenario):
    from motor.motor_asyncio import AsyncIOMotorClient
    from pymongo.monitoring import CommandListener
    uri = os.getenv('GOOGLE_ADS_TEST_MONGO_URI')
    assert uri == 'mongodb://127.0.0.1:27017', 'This test only supports the disposable loopback Mongo service'
    class Commands(CommandListener):
        def __init__(self): self.names = []
        def started(self, event): self.names.append(event.command_name.lower())
        def succeeded(self, event): pass
        def failed(self, event): pass
    commands = Commands()
    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000, connectTimeoutMS=2000, event_listeners=[commands])
    db = mongo['google_launch_evidence_' + uuid.uuid4().hex]
    try:
        await db.command('ping')
        values, fake, args = reader_case(monkeypatch)
        for name, collection in fake.items():
            await db[name].insert_many(deepcopy(collection.rows))
        if scenario == 'duplicate_product':
            await db[evidence.PRODUCT_COLLECTION].insert_one(deepcopy(values['product']))
        elif scenario == 'foreign_proposal':
            await db[evidence.PROPOSALS].update_one({}, {'$set': {'user_id': 'other'}})
        async def drift():
            await db[evidence.CREATION_POLICIES].update_one({}, {'$inc': {'revision': 1}})
        client = Client(values, hook=drift if scenario == 'policy_drift' else None)
        commands.names.clear()
        if scenario == 'verified_read':
            proof = await evidence.read_google_creation_launch_evidence(db, client=client, **args)
            assert proof['created_by_mezan_verified'] is True and proof['ready_for_enable_preview'] is False
            assert proof['binding']['product']['inventory_digest'] == values['product']['inventory_evidence_v1']['stock_digest']
        else:
            code = {'duplicate_product': 'launch_product_identity_unproved', 'foreign_proposal': 'launch_creation_ownership_unproved',
                'policy_drift': 'launch_storage_changed_during_read'}[scenario]
            with pytest.raises(evidence.LaunchEvidenceError, match=code):
                await evidence.read_google_creation_launch_evidence(db, client=client, **args)
        writes = [name for name in commands.names if name not in {'find', 'getmore', 'killcursors', 'endsessions'}]
        assert writes == (['update'] if scenario == 'policy_drift' else [])  # only the test's injected concurrent change
    finally:
        await mongo.drop_database(db.name)
        mongo.close()
