"""Private bundle inputs preserve financial output and public copy contracts."""
import asyncio
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import pickle

from bson import ObjectId
from mongomock_motor import AsyncMongoMockClient
import pytest

import operational_balance_sources as sources
import operational_remaining_bundle_sources as bundles
from test_operational_balance_cost_catalog_index import collector_database
from test_operational_balance_option_authority import repeated_fixture
from test_operational_balance_sources import START, NOW


def test_collector_does_not_clone_complete_native_items_before_bundle_selection(monkeypatch):
    data = repeated_fixture(8)
    before = deepcopy(data)
    calls = []
    original = bundles.deepcopy

    def copied(item):
        calls.append(item)
        return original(item)

    monkeypatch.setattr(bundles, 'deepcopy', copied)
    result = asyncio.run(sources.collect_sources(collector_database(data), 'owner', START, NOW))
    assert len(result['orders']) == 8
    assert all(order['items'][0]['cost'] == '36' for order in result['orders'])
    assert data == before
    assert calls == [], 'fresh collector items were fully copied before bundle selection'


def raw_order():
    return {'id': 'native-order', 'reference_id': 'ORDER', 'status': {'slug': 'in_progress'},
        'items': [{'id': 'LINE', 'product_id': 'bundle', 'quantity': 4,
            'product': {'id': 'bundle', 'options': [], 'images': [{'url': 'synthetic'}]},
            'variant': {'id': 'variant', 'options': []},
            'options': [{'id': 'gift', 'name': 'Gift', 'value': {'id': 'yes', 'name': 'Yes'}}],
            'custom_fields': [{'id': 'name', 'value': 'Synthetic name'}],
            'metadata': {'history': [{'status': 'synthetic'}]}}]}


def normalize(rows, private):
    return (bundles._normalized_orders(rows, bundles._copy_item_read_view) if private
            else bundles.normalized_orders(rows))


def mutable_ids(value):
    pending, seen, result = [value], set(), set()
    while pending:
        row = pending.pop()
        identity = id(row)
        if identity in seen:
            continue
        seen.add(identity)
        if type(row) is dict:
            result.add(identity)
            pending.extend(row.values())
        elif type(row) is list:
            result.add(identity)
            pending.extend(row)
        elif type(row) is tuple:
            pending.extend(row)
    return result


def test_public_normalizer_keeps_full_item_copy_and_private_overrides_are_independent():
    from order_cost_authority import line_fingerprint
    from stock_component_consumption_service import selection_tokens
    raw = raw_order()
    before = deepcopy(raw)
    public, private = normalize([raw], False), normalize([raw], True)
    assert public == private
    item, expected, actual = raw['items'][0], public[0]['items'][0], private[0]['items'][0]
    assert expected['metadata'] is not item['metadata']
    assert actual['metadata'] is item['metadata']
    assert actual is not item
    assert line_fingerprint(actual) == line_fingerprint(expected)
    assert selection_tokens(actual) == selection_tokens(expected)
    assert mutable_ids(actual['options_raw']).isdisjoint(mutable_ids(item))
    assert mutable_ids(actual['custom_fields']).isdisjoint(mutable_ids(item))
    actual['options_raw'][0]['raw']['value']['id'] = 'changed-private-option'
    actual['custom_fields'][0]['value'] = 'changed-private-field'
    assert raw == before
    expected['metadata']['history'][0]['status'] = 'changed-public-copy'
    assert raw == before
    # These references were already borrowed by the public normalizer.
    assert public[0]['status'] is private[0]['status'] is raw['status']


@pytest.mark.parametrize('shape', ['alias', 'cycle', 'item_cycle'])
def test_aliases_and_cycles_keep_complete_copy_independence(shape):
    item = raw_order()['items'][0]
    if shape == 'alias':
        shared = {'proof': ['synthetic']}
        item.update(first=shared, second=shared)
    elif shape == 'cycle':
        item['metadata']['self'] = item['metadata']
    else:
        item['self'] = item
    assert bundles._plain_item_tree(item) is False
    result = bundles._copy_item_read_view(item)
    assert mutable_ids(result).isdisjoint(mutable_ids(item))
    if shape == 'alias':
        assert result['first'] is result['second']
    elif shape == 'cycle':
        assert result['metadata']['self'] is result['metadata']
    else:
        assert result['self'] is result


@pytest.mark.parametrize('value', [
    b'bytes', Decimal('1.25'), datetime(2026, 11, 1),
    ObjectId('000000000000000000000001'), ('tuple',), {1, 2},
    float('nan'), float('inf'), float('-inf'),
])
def test_non_json_native_types_keep_original_copy_path(monkeypatch, value):
    item = {'metadata': value}
    calls = []
    original = bundles.deepcopy
    def copied(row):
        calls.append(row)
        return original(row)
    monkeypatch.setattr(bundles, 'deepcopy', copied)
    result = bundles._copy_item_read_view(item)
    assert calls == [item] and result is not item
    assert type(result['metadata']) is type(value)


def test_custom_types_keys_and_metaclasses_are_not_invoked_by_admission():
    class Mapping(dict):
        def items(self): raise AssertionError('custom mapping visited')
    class Rows(list):
        def __iter__(self): raise AssertionError('custom list visited')
    class Text(str):
        def __str__(self): raise AssertionError('custom text converted')
    class Meta(type):
        def __eq__(self, other): raise AssertionError('custom type compared')
        def __hash__(self): raise AssertionError('custom type hashed')
    class Value(metaclass=Meta):
        pass
    for item in (Mapping(a=1), {'x': Rows([1])}, {Text('key'): 1},
                 {'x': Text('value')}, {'x': Value()}, {1: 'non-string key'}):
        assert bundles._plain_item_tree(item) is False


@pytest.mark.parametrize('failure', [False, True])
def test_custom_copy_hooks_keep_values_failures_and_original_call_order(failure):
    calls = []
    class Hook:
        def __deepcopy__(self, memo):
            calls.append('metadata-copy')
            if failure:
                raise ValueError('synthetic-copy-failure')
            return {'copied': True}
    raw = raw_order()
    raw['items'][0]['metadata'] = Hook()
    outcomes, observations = [], []
    for private in (False, True):
        calls.clear()
        try:
            output = normalize([raw], private)
            outcomes.append(output)
        except ValueError as error:
            outcomes.append((type(error), str(error)))
        observations.append(list(calls))
    assert outcomes[0] == outcomes[1]
    assert observations == [['metadata-copy'], ['metadata-copy']]


def test_option_failure_still_precedes_item_copy_or_private_guard(monkeypatch):
    calls = []
    def reject_options(_):
        calls.append('options')
        raise ValueError('synthetic-option-failure')
    def forbidden(_):
        raise AssertionError('item preparation ran before option validation')
    monkeypatch.setattr('order_engine.mapper._normalise_options', reject_options)
    monkeypatch.setattr(bundles, '_plain_item_tree', forbidden)
    monkeypatch.setattr(bundles, 'deepcopy', forbidden)
    for private in (False, True):
        with pytest.raises(ValueError, match='^synthetic-option-failure$'):
            normalize([raw_order()], private)
    assert calls == ['options', 'options']


@pytest.mark.parametrize('change', ['missing_line', 'duplicate_order', 'invalid_items'])
def test_original_short_circuits_and_errors_precede_private_admission(monkeypatch, change):
    rows = [raw_order()]
    if change == 'missing_line':
        rows[0]['items'][0].pop('id')
    elif change == 'duplicate_order':
        rows.append(deepcopy(rows[0]))
    else:
        rows[0]['items'] = 42
    def forbidden(_):
        raise AssertionError('inadmissible item reached private preparation')
    monkeypatch.setattr(bundles, '_plain_item_tree', forbidden)
    outcomes = []
    for private in (False, True):
        try:
            outcomes.append(normalize(rows, private))
        except TypeError as error:
            outcomes.append((type(error), str(error)))
    assert outcomes[0] == outcomes[1]


async def database_snapshot(db):
    return {name: await db[name].find({}).to_list(None)
            for name in await db.list_collection_names()}


async def adapter_pair(monkeypatch, db, owner, raw, *, pieces=(), expected_borrowed=True):
    import order_cost_authority
    from test_operational_balance_remaining_products import NOW as BUNDLE_NOW
    originals = deepcopy((raw, pieces))
    stored = await database_snapshot(db)
    collection_type = type(db['mezan_order_item_cost_snapshots_v2'])
    original_find = collection_type.find
    original_authority = order_cost_authority.load_order_cost_authority
    original_copy = bundles.deepcopy
    queries = []
    borrowed, copied_items = [], []
    def observed(collection, *args, **kwargs):
        queries.append((collection.name, deepcopy(args), deepcopy(kwargs)))
        return original_find(collection, *args, **kwargs)
    def copied(item):
        copied_items.append(item)
        return original_copy(item)
    async def observed_authority(database, account, orders, **kwargs):
        item = orders[0]['items'][0]
        borrowed.append(item['metadata'] is raw['items'][0]['metadata'])
        if 'same_metadata' in item:
            assert item['same_metadata'] is item['metadata']
        return await original_authority(database, account, orders, **kwargs)
    async def call(private):
        try:
            return await bundles.load_remaining_bundles(db, owner, [raw], BUNDLE_NOW,
                preparation_pieces=list(pieces), native_matches={},
                **({'_read_only_items': True} if private else {}))
        except (ValueError, TypeError, KeyError) as error:
            return type(error), str(error)
    with monkeypatch.context() as traced:
        traced.setattr(collection_type, 'find', observed)
        traced.setattr(bundles, 'deepcopy', copied)
        traced.setattr(order_cost_authority, 'load_order_cost_authority', observed_authority)
        expected = await call(False)
        assert len(copied_items) == 1 and copied_items[0] is raw['items'][0]
        expected_queries = deepcopy(queries)
        queries.clear()
        copied_items.clear()
        actual = await call(True)
        assert queries == expected_queries
        assert borrowed == [False, expected_borrowed]
        if expected_borrowed:
            assert copied_items == []
        else:
            assert len(copied_items) == 1 and copied_items[0] is raw['items'][0]
    assert actual == expected
    assert originals == (raw, pieces)
    assert stored == await database_snapshot(db)
    assert mutable_ids(actual).isdisjoint(mutable_ids((raw, pieces)))
    return actual, expected, originals, stored


async def assert_output_isolated(actual, expected, raw, pieces, stored, db):
    original_input = deepcopy((raw, pieces))
    expected_before = deepcopy(expected)
    for result in actual.values():
        if result['items']:
            result['items'][0]['cost'] = '999.99'
        result['issues'].append({'code': 'synthetic-output-mutation'})
    assert (raw, pieces) == original_input
    assert expected == expected_before
    assert stored == await database_snapshot(db)


@pytest.mark.parametrize('damage', [None, 'selection', 'variant', 'invoice', 'receipt_owner'])
def test_populated_physical_bundle_receipts_and_fingerprints_match_default(monkeypatch, damage):
    from test_operational_balance_remaining_products import seed, receive, OWNER, LINE
    from stock_component_consumption_service import UNITS, RECEIPTS
    async def exercise():
        db = AsyncMongoMockClient().private_bundle_physical
        raw, _ = await seed(db)
        await receive(db, 1, 'rose')
        raw['items'][0]['metadata'] = {'images': [{'name': 'synthetic'}]}
        if damage == 'selection':
            raw['items'][0]['options'][0]['value']['id'] = 'no'
        elif damage == 'variant':
            await db[UNITS].update_one({'_id': 'unit1'}, {'$set': {'variant_id': 'other'}})
        elif damage == 'invoice':
            await db.purchase_invoices.update_one({'id': 'invoice-1-rose'}, {'$set': {'state': 'draft'}})
        elif damage == 'receipt_owner':
            await db[RECEIPTS].update_one({'id': 'receipt-1-rose'}, {'$set': {'user_id': 'other'}})
        actual, expected, _, stored = await adapter_pair(monkeypatch, db, OWNER, raw)
        line = actual[('ORDER', LINE)]
        if damage is None:
            assert line['complete']
            assert sum(Decimal(row['cost']) for row in line['items']) == 180
            assert all(row['supplier_receipt_proven'] is False for row in line['items'])
            assert any(row['code'] == 'bundle_received_purchase_financial_matching_required'
                       for row in line['issues'])
        else:
            assert not line['complete'] and line['issues']
        await assert_output_isolated(actual, expected, raw, (), stored, db)
    asyncio.run(exercise())


@pytest.mark.parametrize('damage', [None, 'alias', 'selection', 'future_piece', 'foreign_piece'])
def test_populated_financial_bundle_keeps_external_supplier_and_service_receipts(monkeypatch, damage):
    from test_operational_balance_bundle_legacy_receipts import fixture, pieces as seed_pieces, OWNER, LINE
    async def exercise():
        db = AsyncMongoMockClient().private_bundle_financial
        raw, _, _ = await fixture(db, monkeypatch, rose=True, name=True)
        await seed_pieces(db, received=4, completed=2)
        pieces = await db.mezan_preparation_pieces_v1.find({}).to_list(None)
        raw['items'][0]['metadata'] = {'images': [{'name': 'synthetic'}]}
        if damage == 'alias':
            raw['items'][0]['same_metadata'] = raw['items'][0]['metadata']
        elif damage == 'selection':
            raw['items'][0]['options'][0]['value']['id'] = 'no'
        elif damage == 'future_piece':
            pieces[0]['updated_at'] = '2027-01-01T00:00:00Z'
        elif damage == 'foreign_piece':
            pieces[0]['user_id'] = 'other'
        actual, expected, _, stored = await adapter_pair(monkeypatch, db, OWNER, raw, pieces=pieces,
            expected_borrowed=damage != 'alias')
        if damage == 'foreign_piece':
            assert actual == (ValueError, 'bundle_legacy_piece_read_scope_invalid')
            return
        line = actual[('ORDER', LINE)]
        if damage in (None, 'alias'):
            assert line['complete'] and line['issues'] == []
            received = [row for row in line['items'] if row['supplier_receipt_proven']]
            unreceived = [row for row in line['items'] if not row['supplier_receipt_proven']]
            assert sum(Decimal(row['cost']) for row in received) == 80
            assert sum(Decimal(row['cost']) for row in unreceived) == 180
            assert {row['supplier_id'] for row in received} == {'external-supplier'}
            assert all(row['cost_currency'] == 'SAR' for row in line['items'])
        else:
            assert not line['complete'] and line['issues']
        await assert_output_isolated(actual, expected, raw, pieces, stored, db)
    asyncio.run(exercise())


def test_complete_collector_and_replay_keep_populated_financial_bundle_output(monkeypatch):
    from operational_balance_engine import reconcile
    from operational_balance_store import initial
    from test_operational_balance_bundle_legacy_receipts import fixture, pieces as seed_pieces, OWNER
    from test_operational_balance_remaining_products import START as BUNDLE_START, NOW as BUNDLE_NOW
    async def exercise():
        db = AsyncMongoMockClient().private_bundle_collector
        await fixture(db, monkeypatch, rose=True, name=True)
        await seed_pieces(db, received=4, completed=2)
        state = initial(OWNER)
        state.update(status='active', started_at=BUNDLE_START)
        before, stored = deepcopy(state), await database_snapshot(db)
        original_loader = bundles.load_remaining_bundles
        actual = await sources.collect_sources(db, OWNER, BUNDLE_START, BUNDLE_NOW, operational_state=state)
        async def default_loader(*args, **kwargs):
            assert kwargs.pop('_read_only_items') is True
            return await original_loader(*args, **kwargs)
        with monkeypatch.context() as default:
            default.setattr(bundles, 'load_remaining_bundles', default_loader)
            expected = await sources.collect_sources(db, OWNER, BUNDLE_START, BUNDLE_NOW, operational_state=state)
        assert actual == expected
        result, reference = reconcile(state, actual, BUNDLE_NOW), reconcile(state, expected, BUNDLE_NOW)
        assert result == reference == reconcile(result, actual, BUNDLE_NOW)
        suppliers = [row for row in result['engine']['obligations'].values() if row['kind'] == 'supplier']
        assert sum(Decimal(row['expected']) for row in suppliers if not row['party_id']) == 180
        assert sum(Decimal(row['expected']) for row in suppliers if row['party_id']) == 80
        assert {row['party_id'] for row in suppliers if row['party_id']} == {'external-supplier'}
        assert all(row['currency'] == 'SAR' and row['confirmed'] == '0.00' for row in suppliers)
        assert state == before and stored == await database_snapshot(db)
        assert not result['movements'] and not result['engine']['facts']
    asyncio.run(exercise())


@pytest.mark.parametrize('private', [False, True])
def test_cancellation_during_authority_read_joins_current_task_and_returns_no_view(monkeypatch, private):
    async def exercise():
        raw = raw_order()
        before = pickle.dumps(raw, protocol=5)
        entered, closed = asyncio.Event(), asyncio.Event()
        async def wait_authority(db, owner, orders, **kwargs):
            item = orders[0]['items'][0]
            assert (item['metadata'] is raw['items'][0]['metadata']) is private
            assert mutable_ids(item['options_raw']).isdisjoint(mutable_ids(raw))
            assert kwargs == {'include_corrections': False, 'include_frozen_metadata': True,
                              'parallel_reads': True}
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        monkeypatch.setattr('order_cost_authority.load_order_cost_authority', wait_authority)
        work = asyncio.create_task(bundles.load_remaining_bundles(object(), 'owner', [raw], NOW,
            **({'_read_only_items': True} if private else {})))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            work.cancel()
            with pytest.raises(asyncio.CancelledError):
                await work
        finally:
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
        assert closed.is_set() and work.cancelled()
        assert pickle.dumps(raw, protocol=5) == before
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(exercise())
