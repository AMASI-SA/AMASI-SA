"""Catalog payload and matching regressions; every database operation is in memory."""
import asyncio
from copy import deepcopy

from bson import BSON, ObjectId
from mongomock_motor import AsyncMongoMockClient
import pytest

import operational_balance_sources as sources


OWNER = 'catalog-owner'
CONFIRMED_AT = '2026-11-22T00:00:00+00:00'
SETUPS = 'mz2_shipping_setup_v2'
ACCOUNTS = 'mezan_integration_accounts_v2'
BINDINGS = 'mz2_ad_account_bindings_v2'
POLICIES = 'mz2_ad_automation_policies_v2'


class CountedAccount(dict):
    def __init__(self, value, counts):
        super().__init__(value)
        self.counts = counts

    def get(self, key, default=None):
        if key == 'mezan_integration_account_id':
            self.counts['account_identity_reads'] += 1
        return super().get(key, default)


class ReadOnlyCatalogDB:
    """Exercise real Mongo projections while denying writes and unrelated reads."""
    def __init__(self, memory, *, full_shipping=False):
        self.memory = memory
        self.full_shipping = full_shipping
        self.reads = []
        self.counts = {'account_identity_reads': 0}

    def __getitem__(self, name):
        assert name in {SETUPS, ACCOUNTS, BINDINGS, POLICIES, 'settings', 'unified_orders'}
        boundary = self

        class Collection:
            def find(self, query, projection=None):
                assert query == {'user_id': OWNER}
                selected = None if boundary.full_shipping and name == SETUPS else projection
                cursor = boundary.memory[name].find(query, selected)

                class Cursor:
                    async def to_list(self, count):
                        values = await cursor.to_list(count)
                        boundary.reads.append({
                            'collection': name, 'projection': deepcopy(selected),
                            'rows': deepcopy(values), 'bytes': sum(len(BSON.encode(row)) for row in values),
                        })
                        return ([CountedAccount(row, boundary.counts) for row in values]
                                if name == ACCOUNTS else values)
                return Cursor()

            def __getattr__(self, operation):
                raise AssertionError(f'Catalog attempted a write or unsupported operation: {operation}')
        return Collection()


async def seeded(data):
    memory = AsyncMongoMockClient()['operational_catalog_read_scope']
    for name, values in data.items():
        if values:
            await memory[name].insert_many(deepcopy(values))
    return memory


def account(integration='integration', external='external', **extra):
    return {'user_id': OWNER, 'mezan_integration_account_id': integration,
            'external_account_id': external, 'provider': 'meta_ads',
            'display_name': 'Account', 'currency': 'SAR', 'timezone': 'UTC',
            'connection_status': 'connected', **extra}


def binding(identity='binding', integration='integration', external='external', **extra):
    return {'user_id': OWNER, '_id': identity, 'integration_account_id': integration,
            'platform_account_id': external, 'platform': 'meta', 'funding_mode': 'postpaid',
            'confirmed_by': OWNER, 'confirmed_at': CONFIRMED_AT, **extra}


def expected_ad(identity='binding', integration='integration', external='external',
                name='Account', platform='meta', **extra):
    return {'id': str(identity), 'name': f'{platform}: {name}', 'currency': 'SAR',
            'kind': 'ad_account', 'platform': platform, 'integration_account_id': integration,
            'platform_account_id': external, 'funding_mode': 'postpaid', 'wallet_fraction': None,
            'hybrid_policy_id': None, 'settings_complete': None, 'funding_policy': None, **extra}


@pytest.mark.parametrize('include_unready', [False, True])
def test_courier_directory_excludes_history_with_full_output_equivalence(include_unready):
    couriers = [
        {'courier_key': 'native-smsa', 'name': 'A carrier', 'salla_carrier_keys': ['SMSA', 'smsa'],
         'confirmed_by': OWNER, 'confirmed_at': CONFIRMED_AT},
        {'courier_key': 'incomplete', 'display_name': 'B incomplete', 'ready': False,
         'setup_issue': 'documents_required', 'confirmed_by': OWNER, 'confirmed_at': CONFIRMED_AT},
        {'courier_key': 'unconfirmed', 'name': 'Z unconfirmed'},
        {'courier_key': 'inactive', 'name': 'Inactive', 'status': 'inactive',
         'confirmed_by': OWNER, 'confirmed_at': CONFIRMED_AT},
        {'courier_key': 'pickup', 'name': 'pickup', 'confirmed_by': OWNER, 'confirmed_at': CONFIRMED_AT},
    ]
    history = [{'version': i, 'evidence': 'synthetic-history-' * 80} for i in range(300)]
    setup = {'user_id': OWNER, 'couriers': couriers, 'version': 301, 'contracts': history,
             'audit': history, 'requests': history, 'rich_requests': history, 'rich_drafts': history,
             'contract_evidence': history, 'usage_revision': 300}
    expected = [
        {'id': 'native-smsa', 'name': 'A carrier', 'currency': 'SAR', 'kind': 'courier'},
        {'id': 'incomplete', 'name': 'B incomplete', 'currency': 'SAR', 'kind': 'courier',
         'ready': False, 'setup_issue': 'documents_required'},
        {'id': 'dhl', 'name': 'DHL', 'currency': 'SAR', 'kind': 'courier'},
    ]
    if include_unready:
        expected.append({'id': 'unconfirmed', 'name': 'Z unconfirmed', 'currency': 'SAR',
                         'kind': 'courier', 'ready': False, 'setup_issue': 'courier_confirmation_required'})

    async def exercise():
        memory = await seeded({SETUPS: [setup, {'user_id': 'other', 'couriers': couriers}],
            'settings': [{'user_id': OWNER, 'shipping_companies': [
                {'name': 'SMSA'}, {'name': 'DHL'}, {'name': 'FedEx', 'is_active': False}],
                'unrelated_catalog': ['synthetic-unused' * 2000]}],
            'unified_orders': [{'user_id': OWNER, 'raw_by_source': {'salla_direct': {
                'shipping': {'company': 'SMSA'}}}}]})
        before = await memory[SETUPS].find({}).to_list(None)
        full = ReadOnlyCatalogDB(memory, full_shipping=True)
        narrow = ReadOnlyCatalogDB(memory)
        legacy = await sources.entities(full, OWNER, 'courier', include_unready=include_unready)
        actual = await sources.entities(narrow, OWNER, 'courier', include_unready=include_unready)
        assert actual == legacy == expected
        assert await memory[SETUPS].find({}).to_list(None) == before
        full_read = next(read for read in full.reads if read['collection'] == SETUPS)
        narrow_read = next(read for read in narrow.reads if read['collection'] == SETUPS)
        assert narrow_read['bytes'] < full_read['bytes'] / 100
        assert narrow_read['rows'] == [{'user_id': OWNER, 'couriers': couriers}]
        settings_read = next(read for read in narrow.reads if read['collection'] == 'settings')
        assert 'unrelated_catalog' not in settings_read['rows'][0]
        # A directory projection must not change financial/contract readers of the same collection.
        assert (await sources.rows(ReadOnlyCatalogDB(memory), OWNER, SETUPS))[0]['contracts'] == history
    asyncio.run(exercise())


def test_courier_projection_retains_duplicate_setup_and_owner_rejection():
    async def exercise():
        memory = await seeded({SETUPS: [{'user_id': OWNER, 'couriers': []},
                                       {'user_id': OWNER, 'couriers': []}]})
        with pytest.raises(ValueError, match='shipping_setup_ambiguous'):
            await sources.entities(ReadOnlyCatalogDB(memory), OWNER, 'courier')
        with pytest.raises(ValueError, match='operational_source_rejected'):
            await sources.entities(ReadOnlyCatalogDB(memory), '', 'courier')
    asyncio.run(exercise())


@pytest.mark.parametrize('size', [128, 512])
def test_ad_directory_matching_has_linear_identity_reads_and_identical_output(size):
    async def exercise():
        memory = await seeded({
            ACCOUNTS: [account(f'integration-{i}', f'external-{i}', display_name=f'Account {i:04}')
                       for i in range(size)],
            BINDINGS: [binding(f'binding-{i}', f'integration-{i}', f'external-{i}')
                       for i in reversed(range(size))],
        })
        db = ReadOnlyCatalogDB(memory)
        result = await sources.entities(db, OWNER, 'ad_account')
        assert result == [expected_ad(f'binding-{i}', f'integration-{i}', f'external-{i}',
                                     name=f'Account {i:04}') for i in range(size)]
        assert db.counts['account_identity_reads'] <= size * 3, db.counts
    asyncio.run(exercise())


@pytest.mark.parametrize('account_rows,binding_row,expected', [
    ([account(), account(connection_status='disconnected')], binding(), []),
    ([account(), account(provider='snapchat_ads')], binding(), [expected_ad()]),
    ([account(connection_status='disconnected')], binding(), []),
    ([account(integration='CaseSensitive')], binding(integration='casesensitive'), []),
    ([account(external='7')], binding(external=7), []),
    ([account(integration=True)], binding(integration=1), [expected_ad(integration=1)]),
    ([account(external=['native', 1])], binding(external=['native', 1]),
     [expected_ad(external=['native', 1])]),
    ([account(external={'native': 1})], binding(external={'native': True}),
     [expected_ad(external={'native': True})]),
    ([account(integration=None, external=None, provider=None)],
     binding(integration=None, external=None, platform='unknown'),
     [expected_ad(integration=None, external=None, platform='unknown')]),
    ([account()], binding(confirmed_at=None), []),
    ([account()], binding(status='archived'), []),
    ([account(integration='different')], binding(platform=[]), []),
    ([account(user_id='other')], binding(), []),
    ([account()], binding(identity=ObjectId('000000000000000000000001')),
     [expected_ad(identity='000000000000000000000001')]),
])
def test_ad_directory_preserves_exact_identity_and_duplicate_semantics(account_rows, binding_row, expected):
    async def exercise():
        memory = await seeded({ACCOUNTS: account_rows, BINDINGS: [binding_row]})
        before = await memory[BINDINGS].find({}).to_list(None)
        assert await sources.entities(ReadOnlyCatalogDB(memory), OWNER, 'ad_account') == expected
        assert await memory[BINDINGS].find({}).to_list(None) == before
    asyncio.run(exercise())


@pytest.mark.parametrize('account_row,binding_row', [
    (account(), binding(platform=[])),
    (account(integration=['legacy']), binding(integration=['legacy'])),
])
def test_ad_directory_preserves_malformed_identity_failures(account_row, binding_row):
    async def exercise():
        memory = await seeded({ACCOUNTS: [account_row], BINDINGS: [binding_row]})
        with pytest.raises(TypeError):
            await sources.entities(ReadOnlyCatalogDB(memory), OWNER, 'ad_account')
    asyncio.run(exercise())
