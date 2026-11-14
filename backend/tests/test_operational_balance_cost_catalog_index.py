"""Cost catalogue indexing preserves financial evidence and lazy validation."""
import asyncio
from copy import deepcopy
from decimal import Decimal, localcontext
from pathlib import Path
import pickle
import sys
from threading import Event

from bson.int64 import Int64
import pytest

import operational_balance_sources as sources
import operational_cost_catalog_index as catalog


def fixture(size):
    return {
        'mezan_cost_resources_v2': [
            {'id': 'resource-' + str(i), 'kind': 'component', 'unit_cost': '2'} for i in range(size)],
        'mezan_product_resource_bindings_v2': [
            {'id': 'product-' + str(i), 'salla_product_id': 'other-' + str(i),
             'resource_id': 'resource-' + str(i)} for i in range(size)],
        'mezan_product_option_cost_bindings_v2': [
            {'id': 'option-' + str(i), 'salla_product_id': 'other-' + str(i),
             'option_id': 'color', 'value_id': 'red', 'mode': 'direct', 'direct_amount': '1'}
            for i in range(size)],
    }


def cost(data, product_id, item, profile, index):
    if index is None:
        return sources.cost_components(data, product_id, item, profile)
    return sources.cost_components(data, product_id, item, profile, catalog_index=index)


def measured_work(size, builder):
    filenames = {str(Path(sources.__file__).resolve()), str(Path(catalog.__file__).resolve())}
    count = 0
    def trace(frame, event, _arg):
        nonlocal count
        if event == 'line' and frame.f_code.co_filename in filenames:
            count += 1
        return trace
    data = fixture(size)
    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        index = builder(data, sources.unique_index)
        for _ in range(size):
            assert cost(data, 'selected', {'id': 'line'}, {'base_cost': '20'}, index) == (Decimal('20'), {})
    finally:
        sys.settrace(previous)
    return count


def test_attempt_catalog_work_does_not_multiply_items_by_entire_catalog():
    small = measured_work(64, catalog.build_cost_catalog_index)
    large = measured_work(128, catalog.build_cost_catalog_index)
    legacy = measured_work(128, lambda *_: None)
    assert large <= small * 2.4, (small, large)
    assert large < legacy / 4, (large, legacy)


def priced_fixture():
    data = {
        catalog.RESOURCES: [
            {'id': 'cloth', 'kind': 'component', 'unit_cost': '5'},
            {'id': 'print', 'kind': 'service', 'unit_cost': '7'}],
        catalog.PRODUCT_BINDINGS: [
            {'id': 'material', 'salla_product_id': 'p', 'resource_id': 'cloth',
             'quantity': '2', 'mode': 'direct'}],
        catalog.OPTION_BINDINGS: [
            {'id': 'direct', 'salla_product_id': 'p', 'option_id': 'color',
             'value_id': 'red', 'option_name': 'Color', 'value_name': 'Red',
             'mode': 'direct', 'direct_amount': '3', 'quantity': '2'},
            {'id': 'service', 'salla_product_id': 'p', 'option_id': 'color',
             'value_id': 'red', 'option_name': 'Color', 'value_name': 'Red',
             'mode': 'resource', 'resource_id': 'print', 'quantity': '1'}],
    }
    item = {'id': 'line', 'product_id': 'p', 'quantity': 2,
            'options': [{'id': 'color', 'name': 'Color', 'value': {'id': 'red', 'name': 'Red'}}]}
    return data, item, {'base_cost': '20'}


def outcome(operation):
    with localcontext() as context:
        context.clear_flags()
        try:
            result = ('value', operation())
        except Exception as error:
            result = ('error', type(error), str(error))
        return result, {flag.__name__ for flag, active in context.flags.items() if active}


def parity(data, item=None, profile=None, product_id='p'):
    item = {'id': 'line', 'product_id': product_id} if item is None else item
    profile = {'base_cost': '20'} if profile is None else profile
    before = pickle.dumps((data, item, profile), protocol=5)
    with localcontext() as context:
        context.clear_flags()
        index = catalog.build_cost_catalog_index(data, sources.unique_index)
        assert not any(context.flags.values()), 'index construction interpreted a price'
    expected = outcome(lambda: sources.cost_components(data, product_id, item, profile))
    actual = outcome(lambda: cost(data, product_id, item, profile, index))
    assert actual == expected
    assert pickle.dumps((data, item, profile), protocol=5) == before
    return index, actual


def test_known_component_service_and_variant_costs_are_not_cached_as_one_result():
    data, item, profile = priced_fixture()
    index, result = parity(data, item, profile)
    assert index is not None
    assert result[0] == ('value', (Decimal('36'), {'print': {'cost': Decimal('7'), 'quantity': Decimal('1')}}))
    profile['variant_costs'] = {'adult': '30'}
    item['variant_id'] = 'adult'
    assert cost(data, 'p', item, profile, index)[0] == Decimal('46')
    item['options'][0]['value'] = {'id': 'blue', 'name': 'Blue'}
    assert cost(data, 'p', item, profile, index) == (Decimal('40'), {})
    assert data[catalog.PRODUCT_BINDINGS][0]['mode'] == 'direct'


@pytest.mark.parametrize('identities', [
    ['x'], ['x', 'x'], [1, '1'], [0], [0, '0'], ['0', 0],
    [False, 'False'], ['False', False], [True, 'True'], [None, ''], [''],
    [Int64(9), '9'], [Int64(0), '0'], [None, None],
])
def test_resource_index_preserves_exact_duplicate_and_falsey_identity_semantics(identities):
    data = fixture(0)
    data[catalog.RESOURCES] = [{'id': identity, 'unit_cost': str(i)} for i, identity in enumerate(identities)]
    index = catalog.build_cost_catalog_index(data, sources.unique_index)
    assert index is not None
    expected = sources.unique_index(data[catalog.RESOURCES], 'id')
    assert index.resources == expected
    assert all(index.resources[key] is expected[key] for key in expected)
    parity(data)


@pytest.mark.parametrize('query', ['p', 'None', '0', 'False', '1', '', 'p:q', 'unrelated', 1, None, [], b'p'])
def test_binding_groups_preserve_string_matching_input_order_and_duplicate_rows(query):
    data = fixture(0)
    rows = [{'id': str(i), 'salla_product_id': value} for i, value in
            enumerate(['p', None, 0, False, Int64(1), '', 'p:q', 'p'])]
    rows.insert(2, rows[0])
    data[catalog.PRODUCT_BINDINGS] = rows
    data[catalog.OPTION_BINDINGS] = list(reversed(rows))
    index = catalog.build_cost_catalog_index(data, sources.unique_index)
    assert index is not None
    for collection, lookup in [(catalog.PRODUCT_BINDINGS, index.product_rows),
                               (catalog.OPTION_BINDINGS, index.option_rows)]:
        expected = [row for row in data[collection] if str(row.get('salla_product_id')) == query]
        actual = lookup(query)
        assert actual == expected and all(a is b for a, b in zip(actual, expected))
        actual.clear()
        assert lookup(query) == expected


@pytest.mark.parametrize('where', ['profile', 'direct', 'resource'])
@pytest.mark.parametrize('amount', ['0', '0.123', '-1', 'NaN', 'sNaN', 'Infinity', 'bad', None, False, {}, 0.25])
def test_money_results_errors_and_decimal_flags_stay_at_per_item_evaluation(where, amount):
    data, item, profile = priced_fixture()
    if where == 'profile':
        profile['base_cost'] = amount
    elif where == 'direct':
        data[catalog.OPTION_BINDINGS][0]['direct_amount'] = amount
    else:
        data[catalog.RESOURCES][0]['unit_cost'] = amount
    index, _ = parity(data, item, profile)
    assert index is not None


@pytest.mark.parametrize('where', ['profile', 'direct', 'resource'])
def test_explicit_foreign_cost_policy_is_not_relabeled(where):
    data, item, profile = priced_fixture()
    target = profile if where == 'profile' else data[catalog.OPTION_BINDINGS][0] if where == 'direct' else data[catalog.RESOURCES][0]
    target['cost_currency'] = 'KWD'
    _, result = parity(data, item, profile)
    assert result[0] == ('error', ValueError, 'mz2_product_cost_currency_requires_review')


@pytest.mark.parametrize('change', ['duplicate_binding', 'duplicate_resource', 'same_resource_twice',
                                   'inactive_resource', 'missing_resource', 'zero_quantity', 'ambiguous_order'])
def test_cost_conflicts_keep_duplicate_evidence_and_first_error_order(change):
    data, item, profile = priced_fixture()
    if change == 'duplicate_binding':
        data[catalog.OPTION_BINDINGS][0]['id'] = 'material'
    elif change == 'duplicate_resource':
        data[catalog.RESOURCES].append(deepcopy(data[catalog.RESOURCES][0]))
    elif change == 'same_resource_twice':
        data[catalog.OPTION_BINDINGS][1]['resource_id'] = 'cloth'
    elif change == 'inactive_resource':
        data[catalog.RESOURCES][0]['status'] = 'inactive'
    elif change == 'missing_resource':
        data[catalog.PRODUCT_BINDINGS][0]['resource_id'] = 'missing'
    elif change == 'zero_quantity':
        data[catalog.PRODUCT_BINDINGS][0]['quantity'] = '0'
    else:
        data[catalog.PRODUCT_BINDINGS].insert(0, {'id': 'bad', 'salla_product_id': 'p', 'quantity': 'bad'})
        data[catalog.OPTION_BINDINGS][0]['id'] = 'material'
    index, result = parity(data, item, profile)
    assert index is not None and result[0][0] == 'error'


@pytest.mark.parametrize('shape', ['missing_resources', 'resource_none', 'tuple_resources', 'bad_resource_row',
                                  'numeric_resource_key', 'bad_product_row', 'bad_option_rows',
                                  'complex_product_identity', 'float_resource_identity', 'large_identity'])
@pytest.mark.parametrize('early_error', [False, True])
def test_unsupported_catalogue_shape_falls_back_without_eager_errors(shape, early_error):
    data, item, profile = priced_fixture()
    if shape == 'missing_resources':
        del data[catalog.RESOURCES]
    elif shape == 'resource_none':
        data[catalog.RESOURCES] = None
    elif shape == 'tuple_resources':
        data[catalog.RESOURCES] = tuple(data[catalog.RESOURCES])
    elif shape == 'bad_resource_row':
        data[catalog.RESOURCES].append(None)
    elif shape == 'numeric_resource_key':
        data[catalog.RESOURCES][0][7] = 'unrelated'
    elif shape == 'bad_product_row':
        data[catalog.PRODUCT_BINDINGS].append(None)
    elif shape == 'bad_option_rows':
        data[catalog.OPTION_BINDINGS] = None
    elif shape == 'complex_product_identity':
        data[catalog.PRODUCT_BINDINGS][0]['salla_product_id'] = []
    elif shape == 'float_resource_identity':
        data[catalog.RESOURCES][0]['id'] = 0.5
    else:
        data[catalog.PRODUCT_BINDINGS][0]['salla_product_id'] = 10 ** 5000
    if early_error:
        profile['cost_currency'] = 'USD'
    index, result = parity(data, item, profile)
    assert index is None
    if early_error:
        assert result[0] == ('error', ValueError, 'mz2_product_cost_currency_requires_review')


def test_bad_options_precede_later_binding_shape_failures():
    data, item, profile = priced_fixture()
    data[catalog.PRODUCT_BINDINGS].append(None)
    item['options'] = [None]
    index, result = parity(data, item, profile)
    assert index is None
    assert result[0] == ('error', ValueError, 'order_option_shape_invalid')


def test_custom_mapping_access_remains_lazy_in_legacy_fallback():
    events = []
    class Custom(dict):
        def get(self, *args):
            events.append(args[0])
            raise RuntimeError('synthetic custom mapping')
    data, item, profile = priced_fixture()
    data[catalog.RESOURCES][0] = Custom(data[catalog.RESOURCES][0])
    assert catalog.build_cost_catalog_index(data, sources.unique_index) is None
    assert events == []
    profile['cost_currency'] = 'USD'
    assert outcome(lambda: cost(data, 'p', item, profile, None))[0] == (
        'error', ValueError, 'mz2_product_cost_currency_requires_review')
    assert events == []
    profile['cost_currency'] = 'SAR'
    assert outcome(lambda: cost(data, 'p', item, profile, None))[0] == (
        'error', RuntimeError, 'synthetic custom mapping')
    assert events == ['id']


@pytest.mark.parametrize('choice', ['normal', 'contradictory_id', 'missing_selection', 'catalog_recovery',
                                   'duplicate_catalog', 'catalog_error'])
def test_option_identity_and_catalogue_recovery_remain_per_item(choice):
    data, item, profile = priced_fixture()
    if choice == 'contradictory_id':
        item['options'][0]['value']['id'] = 'blue'
    elif choice == 'missing_selection':
        item['options'] = []
    elif choice in {'catalog_recovery', 'duplicate_catalog', 'catalog_error'}:
        item['options'] = []
        item['options_normalized'] = {'Color': 'Scarlet'}
        data['mezan_products_v2'] = [{'salla_product_id': 'p', 'options': [
            {'id': 'color', 'name': 'Color', 'values': [{'id': 'red', 'name': 'Scarlet'}]}]}]
        if choice == 'duplicate_catalog':
            data['mezan_products_v2'] *= 2
        if choice == 'catalog_error':
            data['_option_catalog_errors'] = {'p'}
    index, result = parity(data, item, profile)
    assert index is not None
    if choice in {'normal', 'catalog_recovery'}:
        assert result[0][0] == 'value' and result[0][1][0] == Decimal('36')
    else:
        assert result[0][0] == 'error'


def test_each_attempt_observes_replaced_catalogue_and_does_not_reuse_prior_lookup():
    data, item, profile = priced_fixture()
    first = catalog.build_cost_catalog_index(data, sources.unique_index)
    original = cost(data, 'p', item, profile, first)
    data[catalog.RESOURCES] = [{'id': 'cloth', 'kind': 'component', 'unit_cost': '8'},
                               {'id': 'print', 'kind': 'service', 'unit_cost': '11'}]
    second = catalog.build_cost_catalog_index(data, sources.unique_index)
    assert second is not first
    assert cost(data, 'p', item, profile, second) == sources.cost_components(data, 'p', item, profile)
    assert cost(data, 'p', item, profile, second) != original
    assert cost(data, 'p', item, profile, first) == original
    data[catalog.PRODUCT_BINDINGS] = []
    third = catalog.build_cost_catalog_index(data, sources.unique_index)
    assert third is not second and third.product_rows('p') == []


@pytest.mark.asyncio
@pytest.mark.parametrize('stop', ['disconnect', 'deadline'])
async def test_request_owned_catalogue_build_is_cancelled_and_joined_before_return(monkeypatch, stop):
    from fastapi import HTTPException
    import operational_demand_refresh as demand
    from operational_financial_activity_runtime import prepare_in_thread
    from test_operational_balance_demand_refresh import DB, Request, allowed
    data, before = fixture(1000), fixture(1000)
    entered, release, exited = Event(), Event(), Event()
    original, visits = catalog._checkpoint, 0
    def controlled_checkpoint():
        nonlocal visits
        visits += 1
        if visits == 10:
            entered.set()
            assert release.wait(4), 'catalogue test did not release worker'
        original()
    def build():
        try:
            return catalog.build_cost_catalog_index(data, sources.unique_index)
        finally:
            exited.set()
    monkeypatch.setattr(catalog, '_checkpoint', controlled_checkpoint)
    if stop == 'deadline':
        monkeypatch.setattr(demand, 'REQUEST_SECONDS', 0.1)
    database, request = DB(), Request()
    task = asyncio.create_task(demand.run_visible_refresh(request, database, 'owner', 'staff', allowed,
        lambda: prepare_in_thread(build)))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        if stop == 'disconnect':
            request.disconnect()
            for _ in range(10):
                await asyncio.sleep(0)
        else:
            await asyncio.sleep(0.15)
        assert not task.done() and not exited.is_set()
        release.set()
        with pytest.raises(HTTPException) as failure:
            await asyncio.wait_for(task, 3)
        assert failure.value.status_code == (499 if stop == 'disconnect' else 408)
        assert visits == 10 and exited.is_set()
        assert data == before and database.data[demand.LEASES] == {}
        assert all(name == demand.LEASES for name, *_ in database.writes)
        assert not [pending for pending in asyncio.all_tasks() if pending is not asyncio.current_task()]
    finally:
        release.set()


def collector_fixture(kind):
    from test_operational_balance_sources import order
    data, item, profile = priced_fixture()
    if kind == 'duplicate_resource':
        data[catalog.RESOURCES].append(deepcopy(data[catalog.RESOURCES][0]))
    elif kind == 'duplicate_binding':
        data[catalog.OPTION_BINDINGS][0]['id'] = 'material'
    elif kind == 'foreign_cost':
        profile['cost_currency'] = 'KWD'
    elif kind == 'catalog_recovery':
        item['options'] = []
        item['options_normalized'] = {'Color': 'Scarlet'}
        data['mezan_products_v2'] = [{'salla_product_id': 'p', 'options': [
            {'id': 'color', 'name': 'Color', 'values': [{'id': 'red', 'name': 'Scarlet'}]}]}]
    for rows in data.values():
        for row in rows:
            row['user_id'] = 'owner'
    data['mezan_product_cost_profiles_v2'] = [{'user_id': 'owner', 'salla_product_id': 'p', **profile}]
    data['unified_orders'] = [order(items=[item])]
    return data


def collector_database(data):
    from test_operational_balance_sources import Cursor, HardBoundaryDB
    class Products:
        def __init__(self, database):
            self.database = database

        def find(self, query, projection):
            assert set(query) == {'user_id', 'salla_product_id'} and query['user_id'] == 'owner'
            assert set(query['salla_product_id']) == {'$in'}
            values = query['salla_product_id']['$in']
            assert isinstance(values, list) and 0 < len(values) <= 250
            assert projection == {'_id': 0, 'user_id': 1, 'salla_product_id': 1, 'options': 1,
                                  'raw_salla.options': 1, 'raw_salla_details.options': 1}
            self.database.reads.append('mezan_products_v2')
            return Cursor([row for row in self.database.data.get('mezan_products_v2', [])
                           if row.get('user_id') == 'owner' and row.get('salla_product_id') in values])

    class Database(HardBoundaryDB):
        def __getitem__(self, name):
            return Products(self) if name == 'mezan_products_v2' else super().__getitem__(name)
    return Database(data)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['valid', 'duplicate_resource', 'duplicate_binding', 'foreign_cost', 'catalog_recovery'])
@pytest.mark.parametrize('replay', [False, True])
async def test_complete_collector_reconciliation_and_reports_match_default_cost_path(monkeypatch, kind, replay):
    from operational_balance_engine import reconcile
    from operational_balance_service import report
    from operational_balance_store import initial
    from operational_financial_position import build_financial_position
    from test_operational_balance_sources import START, NOW
    monkeypatch.setattr('operational_financial_activity.now', lambda: NOW)
    data = collector_fixture(kind)
    prior = initial('owner')
    prior.update(status='active', started_at=START)
    if replay:
        first = await sources.collect_sources(collector_database(data), 'owner', START, NOW, operational_state=prior)
        prior = reconcile(prior, first, NOW)
    before_data, before_state = deepcopy(data), deepcopy(prior)
    builds = []
    def indexed(current, indexer):
        result = catalog.build_cost_catalog_index(current, indexer)
        assert result is not None
        builds.append(result)
        return result
    monkeypatch.setattr(sources, 'build_cost_catalog_index', indexed)
    actual_db = collector_database(data)
    actual = await sources.collect_sources(actual_db, 'owner', START, NOW, operational_state=prior)
    assert len(builds) == 1
    monkeypatch.setattr(sources, 'build_cost_catalog_index', lambda *_: None)
    expected_db = collector_database(data)
    expected = await sources.collect_sources(expected_db, 'owner', START, NOW, operational_state=prior)
    assert actual == expected and actual_db.reads == expected_db.reads
    actual_state, expected_state = reconcile(prior, actual, NOW), reconcile(prior, expected, NOW)
    assert actual_state == expected_state
    assert report(actual_state, as_of=NOW) == report(expected_state, as_of=NOW)
    assert build_financial_position(actual_state, generated_at=NOW) == build_financial_position(expected_state, generated_at=NOW)
    assert data == before_data and prior == before_state


@pytest.mark.asyncio
async def test_collector_rebuilds_catalogue_for_every_attempt_with_the_same_inputs(monkeypatch):
    from test_operational_balance_sources import START, NOW
    data, builds = collector_fixture('valid'), []
    def indexed(current, indexer):
        result = catalog.build_cost_catalog_index(current, indexer)
        builds.append(result)
        return result
    monkeypatch.setattr(sources, 'build_cost_catalog_index', indexed)
    for _ in range(2):
        await sources.collect_sources(collector_database(data), 'owner', START, NOW)
    assert len(builds) == 2 and all(result is not None for result in builds)
    assert builds[0] is not builds[1]
