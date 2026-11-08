from collections import Counter

import pytest
from mongomock_motor import AsyncMongoMockClient

from product_traffic_sources import load_product_traffic_sources, resolve_traffic_source
from mezan_attribution_order_ledger import LEDGER_COLLECTION
from sold_products_report_v2 import aggregate_sold_products, load_sold_products


def ledger(platform='snapchat', account='snap-account', **fields):
    return {'attribution': {'provider': platform, 'account_id': account,
                           'quality': 'confirmed', 'decision_safe': True, **fields}}


def order(number, quantity=1, **fields):
    return {'user_id': 'owner', 'order_number': number, 'order_status': 'تم التنفيذ',
            'total_amount': 100 * quantity, 'currency': 'SAR',
            'products': [{'product_id': 'p', 'order_item_id': number + '-line', 'quantity': quantity, 'total': 100 * quantity}], **fields}


def test_unknown_is_not_direct_and_inferred_account_is_not_confirmed():
    assert resolve_traffic_source({'source': 'salla_direct'})['platform'] == 'unknown'
    direct = resolve_traffic_source({'utm_source': 'direct'})
    assert direct['platform'] == 'direct' and direct['account_status'] == 'not_applicable'
    inferred = resolve_traffic_source({'utm_source': 'snapchat'}, [ledger(quality='inferred', decision_safe=False)])
    assert inferred['platform'] == 'snapchat'
    assert inferred['account_id'] is None and inferred['account_status'] == 'unknown'


def test_conflicting_platforms_and_duplicate_ledger_fail_closed():
    for source, rows in [({'utm_source': 'snapchat', 'marketing': {'utm_source': 'tiktok'}}, []),
                         ({'utm_source': 'tiktok'}, [ledger()]),
                         ({'utm_source': 'snapchat'}, [ledger(), ledger()]),
                         ({}, [ledger(quality='ambiguous')])]:
        result = resolve_traffic_source(source, rows)
        assert result['platform'] == 'unknown'
        assert result['attribution_status'] == 'conflict'
        assert result['account_id'] is None


def test_sold_units_split_per_product_and_keep_unknown():
    rows = aggregate_sold_products([order('snap', 3, utm_source='snapchat'),
                                    order('direct', 2, utm_source='direct'), order('unknown', 4)],
                                   [{'salla_product_id': 'p', 'cost_price': 5}], [])
    row = rows[0]
    assert row['units_sold'] == sum(x['units_sold'] for x in row['traffic_sources']) == 9
    assert {x['platform']: x['units_sold'] for x in row['traffic_sources']} == {'snapchat': 3, 'direct': 2, 'unknown': 4}


class ReadCollection:
    def __init__(self, collection, calls): self.collection, self.calls = collection, calls
    def find(self, query, projection):
        assert query['user_id'] == 'owner'
        self.calls.append((self.collection.name, query, projection))
        return self.collection.find(query, projection)
    def __getattr__(self, name): raise AssertionError('unexpected write/read ' + name)


class ReadDB:
    def __init__(self, db): self.db, self.calls = db, []
    def __getitem__(self, name): return ReadCollection(self.db[name], self.calls)


@pytest.mark.asyncio
async def test_loader_is_owner_scoped_batched_and_reads_projected_raw_source():
    db = AsyncMongoMockClient().db
    orders = [order('a', 3), order('b', 2), order('c', 1)]
    await db[LEDGER_COLLECTION].insert_many([
        {'user_id': 'owner', 'order_key': 'a', **ledger()},
        {'user_id': 'other-owner', 'order_key': 'b', **ledger('tiktok', 'foreign-account')}])
    await db.unified_orders.insert_many([
        {**orders[0], 'raw_by_source': {'salla_direct': {'utm_source': 'snapchat'}}},
        {**orders[1], 'raw_by_source': {'salla_direct': {'utm_source': 'tiktok'}}}, orders[2]])
    readonly = ReadDB(db)
    sources = await load_product_traffic_sources(readonly, 'owner', orders)
    assert len(readonly.calls) == 2
    assert sources[id(orders[0])]['account_id'] == 'snap-account'
    assert sources[id(orders[1])]['platform'] == 'tiktok'
    assert sources[id(orders[1])]['account_id'] is None
    assert sources[id(orders[2])]['platform'] == 'unknown'
    rows = aggregate_sold_products(orders, [{'salla_product_id': 'p', 'cost_price': 5}], [], traffic_by_order=sources)
    assert sum(s['units_sold'] for s in rows[0]['traffic_sources']) == 6


@pytest.mark.asyncio
async def test_duplicate_canonical_order_or_raw_join_remains_unknown():
    db = AsyncMongoMockClient().db
    a, b = order('a'), order('a')
    result = await load_product_traffic_sources(ReadDB(db), 'owner', [a, b])
    assert all(entry['attribution_status'] == 'conflict' for entry in result.values())
    await db.unified_orders.insert_many([{**a}, {**a}])
    result = await load_product_traffic_sources(ReadDB(db), 'owner', [a])
    assert result[id(a)]['attribution_status'] == 'conflict'


@pytest.mark.asyncio
async def test_sold_and_both_executive_reducers_keep_same_unit_attribution():
    from dashboard_v2_routes import build_mezan_v2_product_cost, build_mezan_v2_product_cost_cooperative
    db = AsyncMongoMockClient().db
    await db.mezan_products_v2.insert_one({'user_id': 'owner', 'salla_product_id': 'p', 'name': 'Shawl', 'cost_price': 5})
    orders = [order('snap', 3, utm_source='snapchat'), order('unknown', 2)]
    await db[LEDGER_COLLECTION].insert_one({'user_id': 'owner', 'order_key': 'snap', **ledger(campaign_id='winter', campaign_name='Winter campaign')})
    sold = (await load_sold_products(db, 'owner', orders))[0]
    normal = (await build_mezan_v2_product_cost(db, 'owner', orders))['product_rows'][0]
    cooperative = (await build_mezan_v2_product_cost_cooperative(db, 'owner', orders))['product_rows'][0]
    assert sold['traffic_sources'] == normal['traffic_sources'] == cooperative['traffic_sources']
    assert next(source for source in sold['traffic_sources'] if source['platform'] == 'snapchat')['campaigns'] == [
        {'campaign_id': 'winter', 'campaign_name': 'Winter campaign', 'attribution_status': 'confirmed', 'units_sold': 3}]
    assert sum(row['units_sold'] for row in sold['traffic_sources']) == sold['units_sold'] == normal['units_sold'] == 5


@pytest.mark.asyncio
async def test_report_business_number_joins_ledger_via_fetched_canonical_id():
    db = AsyncMongoMockClient().db
    report_order = order('business-2026', 2)
    await db.unified_orders.insert_one({**report_order, 'order_id': 'canonical-salla-42'})
    await db[LEDGER_COLLECTION].insert_many([
        {'user_id': 'owner', 'order_key': 'canonical-salla-42', **ledger('tiktok', 'verified-account')},
        {'user_id': 'owner', 'order_key': 'business-2026', **ledger('snapchat', 'wrong-key-account')}])
    readonly = ReadDB(db)
    result = await load_product_traffic_sources(readonly, 'owner', [report_order])
    source = result[id(report_order)]
    assert source['platform'] == 'tiktok' and source['account_id'] == 'verified-account'
    assert readonly.calls[1][1]['order_key']['$in'] == ['canonical-salla-42']
    mismatch = {**report_order, 'order_id': 'different-canonical-id'}
    result = await load_product_traffic_sources(ReadDB(db), 'owner', [mismatch])
    assert result[id(mismatch)]['attribution_status'] == 'conflict'


@pytest.mark.asyncio
async def test_duplicate_canonical_ids_cannot_cross_assign_accounts():
    db = AsyncMongoMockClient().db
    orders = [order('a'), order('b')]
    await db.unified_orders.insert_many([{**entry, 'order_id': 'duplicate-canonical'} for entry in orders])
    await db[LEDGER_COLLECTION].insert_one({'user_id': 'owner', 'order_key': 'duplicate-canonical', **ledger()})
    result = await load_product_traffic_sources(ReadDB(db), 'owner', orders)
    assert all(entry['attribution_status'] == 'conflict' for entry in result.values())


def test_fractional_effective_units_round_to_same_displayed_total():
    from decimal import Decimal
    from product_traffic_sources import add_traffic_units, traffic_sources
    row = {}
    for platform in ('snapchat', 'tiktok', 'direct'):
        add_traffic_units(row, resolve_traffic_source({'utm_source': platform}), Decimal(1) / Decimal(3))
    entries = traffic_sources(row, total_units=1)
    assert sum(Decimal(str(entry['units_sold'])) for entry in entries) == Decimal(1)
    assert sorted(entry['units_sold'] for entry in entries) == [.33, .33, .34]



@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['database', 'timeout', 'read_limit'])
async def test_optional_attribution_read_failure_never_claims_source(failure):
    import asyncio
    from pymongo.errors import OperationFailure
    entry = order('a', utm_source='snapchat')
    class Collection:
        def find(self, *args, **kwargs): return self
        async def to_list(self, length):
            if failure == 'database': raise OperationFailure('source unavailable')
            if failure == 'timeout': raise asyncio.TimeoutError()
            return [{'user_id': 'owner', 'order_number': 'a'}] * length
    class DB:
        def __getitem__(self, key): return Collection()
    result = await load_product_traffic_sources(DB(), 'owner', [entry])
    assert result[id(entry)]['attribution_status'] == 'unavailable'
    assert result[id(entry)]['platform'] == 'unknown'
    assert result[id(entry)]['account_id'] is None


@pytest.mark.asyncio
async def test_optional_ledger_failure_preserves_authoritative_sold_costs():
    from pymongo.errors import OperationFailure
    db = AsyncMongoMockClient().db
    await db.mezan_products_v2.insert_one({'user_id': 'owner', 'salla_product_id': 'p', 'cost_price': 5})
    class FailedLedger:
        def find(self, *args, **kwargs): raise OperationFailure('ledger unavailable')
    class DB:
        def __getitem__(self, name): return FailedLedger() if name == LEDGER_COLLECTION else db[name]
        def __getattr__(self, name): return self[name]
    row = (await load_sold_products(DB(), 'owner', [order('a', 3, utm_source='snapchat')]))[0]
    assert row['total_cost'] == 15
    assert row['cost_status'] == 'complete'
    assert row['traffic_sources'][0]['units_sold'] == 3
    assert row['traffic_sources'][0]['attribution_status'] == 'unavailable'


@pytest.mark.asyncio
async def test_attribution_programming_errors_are_not_suppressed():
    class DB:
        def __getitem__(self, name): raise TypeError('programming error')
    with pytest.raises(TypeError, match='programming error'):
        await load_product_traffic_sources(DB(), 'owner', [order('a')])



@pytest.mark.asyncio
@pytest.mark.parametrize('source_payload', [
    {'source': 'facebook'},
    {'source': {'name': 'facebook'}},
    {'marketing': {'source': 'facebook'}},
    {'marketing': {'source': {'name': 'facebook'}}},
])
async def test_mongo_projection_preserves_scalar_and_object_marketing_source(source_payload):
    db = AsyncMongoMockClient().db
    entry = order('scalar-source', 2)
    await db.unified_orders.insert_one({**entry, 'raw_by_source': {'salla_direct': {
        **source_payload, 'customer': {'name': 'private'}, 'payment': {'private': 'excluded'}}}})
    readonly = ReadDB(db)
    result = await load_product_traffic_sources(readonly, 'owner', [entry])
    assert result[id(entry)]['platform'] == 'meta'
    assert result[id(entry)]['account_id'] is None
    projection = readonly.calls[0][2]
    assert projection['raw_by_source.salla_direct.source'] == 1
    assert 'raw_by_source.salla_direct.source.name' not in projection
    assert 'raw_by_source.salla_direct' not in projection
    projected = await db.unified_orders.find({'order_number': 'scalar-source'}, projection).to_list(length=1)
    assert 'customer' not in projected[0]['raw_by_source']['salla_direct']
    assert 'payment' not in projected[0]['raw_by_source']['salla_direct']


def test_campaign_breakdown_counts_pieces_and_merges_stable_campaign_identity():
    from product_traffic_sources import add_traffic_units, traffic_sources
    row = {}
    for campaign_id, name, quantity in [('a', 'Winter', 3), ('a', 'Winter renamed', 2), ('b', 'Winter', 4)]:
        source = resolve_traffic_source({'utm_source': 'snapchat'},
            [ledger(campaign_id=campaign_id, campaign_name=name)])
        add_traffic_units(row, source, quantity)
    # Platform/account is confirmed even when the campaign itself is unknown.
    add_traffic_units(row, resolve_traffic_source({'utm_source': 'snapchat'}, [ledger()]), 1)
    source = traffic_sources(row)[0]
    assert source['units_sold'] == 10
    assert {c['campaign_id']: c['units_sold'] for c in source['campaigns']} == {'a': 5, 'b': 4, None: 1}
    assert len([c for c in source['campaigns'] if c['campaign_id'] == 'a']) == 1
    assert next(c for c in source['campaigns'] if c['campaign_id'] is None)['attribution_status'] == 'unknown'


def test_campaign_names_without_confirmed_id_never_manufacture_attribution():
    from product_traffic_sources import add_traffic_units, traffic_sources
    for source in [
        resolve_traffic_source({'utm_source': 'snapchat', 'utm_campaign': 'Raw name'}),
        resolve_traffic_source({'utm_source': 'snapchat'}, [ledger(campaign_name='Name only')]),
        resolve_traffic_source({'utm_source': 'snapchat'}, [ledger(campaign_id='a', campaign_name='Inferred', quality='inferred', decision_safe=False)]),
    ]:
        row = {}
        add_traffic_units(row, source, 2)
        campaign = traffic_sources(row)[0]['campaigns'][0]
        assert campaign == {'campaign_id': None, 'campaign_name': None, 'attribution_status': 'unknown', 'units_sold': 2}


def test_campaign_conflict_unavailable_and_direct_are_explicit():
    from product_traffic_sources import add_traffic_units, traffic_sources
    cases = [(resolve_traffic_source({'utm_source': 'tiktok'}, [ledger(campaign_id='a')]), 'conflict'),
             ({'platform': 'unknown', 'account_id': None, 'account_status': 'unknown', 'attribution_status': 'unavailable'}, 'unavailable'),
             (resolve_traffic_source({'utm_source': 'direct'}), 'not_applicable')]
    for source, status in cases:
        row = {}
        add_traffic_units(row, source, 3)
        assert traffic_sources(row)[0]['campaigns'] == [{'campaign_id': None, 'campaign_name': None, 'attribution_status': status, 'units_sold': 3}]


def test_campaign_rounding_reconciles_each_platform_and_grand_total():
    from decimal import Decimal
    from product_traffic_sources import add_traffic_units, traffic_sources
    row = {}
    for platform in ('snapchat', 'tiktok', 'meta'):
        for index in range(3):
            add_traffic_units(row, resolve_traffic_source({'utm_source': platform},
                [ledger(platform, campaign_id=str(index), campaign_name=f'Campaign {index}')]), Decimal(1) / Decimal(9))
    sources = traffic_sources(row, total_units=1)
    assert sum(Decimal(str(s['units_sold'])) for s in sources) == 1
    for source in sources:
        assert sum(Decimal(str(c['units_sold'])) for c in source['campaigns']) == Decimal(str(source['units_sold']))


@pytest.mark.asyncio
async def test_campaign_loader_keeps_two_reads_and_owner_isolation():
    from product_traffic_sources import add_traffic_units, traffic_sources
    db = AsyncMongoMockClient().db
    orders = [order('a', 5, utm_source='snapchat'), order('b', 2, utm_source='snapchat')]
    await db.unified_orders.insert_many(orders)
    await db[LEDGER_COLLECTION].insert_many([
        {'user_id': 'owner', 'order_key': 'a', **ledger(campaign_id='safe', campaign_name='Owner campaign')},
        {'user_id': 'other-owner', 'order_key': 'b', **ledger(campaign_id='foreign', campaign_name='Private foreign campaign')}])
    readonly = ReadDB(db)
    sources = await load_product_traffic_sources(readonly, 'owner', orders)
    row = {}
    for entry in orders:
        add_traffic_units(row, sources[id(entry)], entry['products'][0]['quantity'])
    final = traffic_sources(row)
    assert len(readonly.calls) == 2
    assert sum(s['units_sold'] for s in final) == 7
    campaigns = [c for s in final for c in s['campaigns']]
    assert {c['campaign_id']: c['units_sold'] for c in campaigns} == {'safe': 5, None: 2}
    assert 'foreign' not in str(final)


@pytest.mark.asyncio
async def test_product_report_does_not_resolve_missing_campaign_from_catalogs():
    db = AsyncMongoMockClient().db
    orders = [order('campaign-raw', 6, utm_source='snapchat', utm_campaign='not-in-ledger')]
    await db.unified_orders.insert_many(orders)
    class OnlySavedSources(ReadDB):
        def __getitem__(self, name):
            assert name in {'unified_orders', LEDGER_COLLECTION}, name
            return super().__getitem__(name)
    readonly = OnlySavedSources(db)
    result = await load_product_traffic_sources(readonly, 'owner', orders)
    assert len(readonly.calls) == 2
    assert result[id(orders[0])]['platform'] == 'snapchat'
    assert result[id(orders[0])]['account_id'] is None
