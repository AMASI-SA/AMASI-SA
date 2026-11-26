"""Attempt-local option authority preserves line-specific financial checks."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone, tzinfo
from decimal import Decimal
import pickle
from threading import Event

import pytest
from bson import ObjectId

import operational_balance_sources as sources
import operational_option_authority as authority
import report_option_identity
from test_operational_balance_cost_catalog_index import collector_database, collector_fixture, outcome
from test_operational_balance_sources import NOW, START


def repeated_fixture(count):
    data = collector_fixture('catalog_recovery')
    original = data['unified_orders'][0]
    rows = []
    for i in range(count):
        row = deepcopy(original)
        row['order_number'] = str(10 + i)
        raw = row['raw_by_source']['salla_direct']
        raw.update(id=str(100 + i), reference_id=str(10 + i))
        raw['items'][0]['quantity'] = i + 1
        rows.append(row)
    data['unified_orders'] = rows
    return data


def test_complete_collector_prepares_option_authority_once_per_product(monkeypatch):
    data = repeated_fixture(8)
    before = deepcopy(data)
    builds = []
    original = report_option_identity.catalog_options
    def counted(product):
        builds.append(product)
        return original(product)
    monkeypatch.setattr(report_option_identity, 'catalog_options', counted)
    result = asyncio.run(sources.collect_sources(collector_database(data), 'owner', START, NOW))
    assert len(result['orders']) == 8
    assert [order['items'][0]['quantity'] for order in result['orders']] == [str(i) for i in range(1, 9)]
    assert all(order['items'][0]['cost'] == '36' for order in result['orders'])
    assert data == before
    assert len(builds) == 1, 'the same catalog authority was rebuilt for every order line'


def test_native_binding_metadata_keeps_authority_ready_without_conversion(monkeypatch):
    data, item, profile = cost_fixture()
    for i, row in enumerate(data['mezan_product_option_cost_bindings_v2']):
        row.update(_id=ObjectId(f'{i + 1:024x}'), created_at=datetime(2026, 11, 1),
                   updated_at=datetime(2026, 11, 2, tzinfo=timezone.utc))
    def forbidden(*_args, **_kwargs):
        raise AssertionError('authority interpreted unrelated BSON metadata')
    monkeypatch.setattr(ObjectId, '__str__', forbidden)
    with authority.OptionAuthorityScope(data) as scope:
        assert scope.build() is scope
        assert cost(data, item, profile, scope)[0] == Decimal('36')
        assert cost(data, item, profile, scope)[0] == Decimal('36')
        assert len(scope.cache) == 1
        scope.validate()


@pytest.mark.parametrize('kind', ['object_id', 'datetime'])
def test_metadata_subclasses_still_use_default_path(kind):
    class CustomId(ObjectId):
        pass
    class CustomTime(datetime):
        pass
    data, item, profile = cost_fixture()
    data['mezan_product_option_cost_bindings_v2'][0]['metadata'] = (
        CustomId('000000000000000000000001') if kind == 'object_id' else CustomTime(2026, 11, 1))
    with authority.OptionAuthorityScope(data) as scope:
        assert scope.build() is None
        assert cost(data, item, profile, scope) == cost(data, item, profile)
        scope.validate()


def test_in_place_object_id_change_invalidates_the_native_snapshot():
    data, item, profile = cost_fixture()
    identity = ObjectId('000000000000000000000001')
    data['mezan_product_option_cost_bindings_v2'][0]['_id'] = identity
    with pytest.raises(ValueError, match='operational_option_authority_snapshot_changed'):
        with authority.OptionAuthorityScope(data) as scope:
            assert scope.build() is scope
            cost(data, item, profile, scope)
            identity.__init__('000000000000000000000002')
            scope.validate()


def test_custom_datetime_timezone_keeps_original_lazy_path():
    class CustomZone(tzinfo):
        pass
    data, item, profile = cost_fixture()
    data['mezan_product_option_cost_bindings_v2'][0]['created_at'] = datetime(2026, 11, 1, tzinfo=CustomZone())
    with authority.OptionAuthorityScope(data) as scope:
        assert scope.build() is None
        assert cost(data, item, profile, scope) == cost(data, item, profile)
        scope.validate()


def cost_fixture():
    data = collector_fixture('catalog_recovery')
    item = data['unified_orders'][0]['raw_by_source']['salla_direct']['items'][0]
    profile = data['mezan_product_cost_profiles_v2'][0]
    data['_option_catalog_errors'] = set()
    return data, item, profile


def cost(data, item, profile, option_authority=None):
    return sources.cost_components(data, 'p', item, profile, option_authority=option_authority)


def parity(data, item, profile):
    before = pickle.dumps((data, item, profile), protocol=5)
    expected = outcome(lambda: cost(data, item, profile))
    with authority.OptionAuthorityScope(data) as scope:
        prepared = scope.build()
        actual = outcome(lambda: cost(data, item, profile, prepared))
        repeated = outcome(lambda: cost(data, item, profile, prepared))
        assert actual == repeated == expected
        scope.validate()
    assert pickle.dumps((data, item, profile), protocol=5) == before
    return actual, prepared


@pytest.mark.parametrize('change', [
    'valid', 'duplicate_catalog', 'missing_catalog', 'bad_options', 'bad_option',
    'bad_values', 'bad_value', 'boolean_option_id', 'boolean_value_id',
    'bad_option_labels', 'bad_value_labels', 'bad_translations', 'bad_translation',
    'bad_raw_options', 'missing_bound_option', 'missing_bound_value', 'read_error',
    'unsupported_explicit_id', 'contradictory_explicit_id', 'empty_selection',
    'wrong_product', 'multiple_conflict', 'raw_translated_catalog',
])
def test_catalog_preparation_preserves_values_failures_and_error_order(change):
    data, item, profile = cost_fixture()
    product = data['mezan_products_v2'][0]
    option = product['options'][0]
    value = option['values'][0]
    if change == 'duplicate_catalog':
        data['mezan_products_v2'].append(deepcopy(product))
    elif change == 'missing_catalog':
        data['mezan_products_v2'] = []
    elif change == 'bad_options':
        product['options'] = 'invalid'
    elif change == 'bad_option':
        product['options'] = [None]
    elif change == 'bad_values':
        option['values'] = 'invalid'
    elif change == 'bad_value':
        option['values'] = [None]
    elif change == 'boolean_option_id':
        option['id'] = True
    elif change == 'boolean_value_id':
        value['id'] = True
    elif change == 'bad_option_labels':
        option['identity_labels'] = 'invalid'
    elif change == 'bad_value_labels':
        value['identity_labels'] = [1]
    elif change == 'bad_translations':
        value['translations'] = []
    elif change == 'bad_translation':
        option['translations'] = {'en': 'invalid'}
    elif change == 'bad_raw_options':
        product['raw_salla'] = {'options': {'invalid': True}}
    elif change == 'missing_bound_option':
        data['mezan_product_option_cost_bindings_v2'][0]['option_id'] = 'unrelated'
    elif change == 'missing_bound_value':
        data['mezan_product_option_cost_bindings_v2'][0]['value_id'] = 'unrelated'
    elif change == 'read_error':
        data['_option_catalog_errors'].add('p')
    elif change == 'unsupported_explicit_id':
        item['options'] = [{'option_id': True, 'name': 'Color', 'value': 'Scarlet'}]
    elif change == 'contradictory_explicit_id':
        item['options'] = [{'option_id': 'color', 'id': 'another', 'name': 'Color', 'value': 'Scarlet'}]
    elif change == 'empty_selection':
        item['options_normalized'] = {}
    elif change == 'wrong_product':
        item['product_id'] = 'another'
    elif change == 'multiple_conflict':
        option['values'].append({'id': 'blue', 'name': 'Azure'})
        item['options_normalized']['Color'] = ['Scarlet', 'Azure']
    elif change == 'raw_translated_catalog':
        product['raw_salla'] = {'options': [{'id': 'color', 'name': 'Colour',
            'values': [{'id': 'red', 'name': 'Rouge',
                        'translations': {'en': {'option_details_name': 'Scarlet'}}}]}]}
        value['name'] = 'Red'
    result, _prepared = parity(data, item, profile)
    assert result[0][0] == ('value' if change in {'valid', 'raw_translated_catalog'} else 'error')


@pytest.mark.parametrize('amount', ['0', '0.123', '-1', 'NaN', 'Infinity', None, False, {}, Decimal('sNaN')])
def test_authority_build_does_not_interpret_or_cache_money(amount):
    data, item, profile = cost_fixture()
    data['mezan_product_option_cost_bindings_v2'][0]['direct_amount'] = amount
    parity(data, item, profile)


def test_cached_catalog_does_not_cache_variants_or_line_identity_failures():
    data, item, profile = cost_fixture()
    with authority.OptionAuthorityScope(data) as scope:
        prepared = scope.build()
        assert cost(data, item, profile, prepared)[0] == Decimal('36')
        profile['variant_costs'] = {'adult': '30'}
        item['variant_id'] = 'adult'
        assert cost(data, item, profile, prepared)[0] == Decimal('46')
        item['options'] = [{'option_id': 'color', 'id': 'other', 'name': 'Color', 'value': 'Scarlet'}]
        with pytest.raises(ValueError, match='mz2_cost_option_identity_conflict'):
            cost(data, item, profile, prepared)
        item['options'] = []
        assert cost(data, item, profile, prepared)[0] == Decimal('46')
        profile['cost_currency'] = 'KWD'
        with pytest.raises(ValueError, match='mz2_product_cost_currency_requires_review'):
            cost(data, item, profile, prepared)
        scope.validate()


def test_only_successful_preparation_is_cached_and_default_callers_rebuild(monkeypatch):
    data, item, profile = cost_fixture()
    builds = []
    original = sources._option_catalog_authority
    def counted(*args):
        builds.append(args[1])
        return original(*args)
    monkeypatch.setattr(sources, '_option_catalog_authority', counted)
    assert cost(data, item, profile) == cost(data, item, profile)
    assert len(builds) == 2
    data['mezan_products_v2'] = []
    with authority.OptionAuthorityScope(data) as scope:
        prepared = scope.build()
        for _ in range(2):
            with pytest.raises(ValueError, match='mz2_cost_option_catalog_unverified'):
                cost(data, item, profile, prepared)
        assert scope.cache == {}
        scope.validate()
    assert len(builds) == 4


@pytest.mark.parametrize('shape', ['product_subclass', 'binding_subclass', 'custom_value', 'tuple_products'])
def test_custom_source_shapes_keep_lazy_default_access(shape):
    data, item, profile = cost_fixture()
    calls = []
    class Custom(dict):
        def get(self, *args):
            calls.append(args[0])
            return super().get(*args)
    class Value:
        pass
    if shape == 'product_subclass':
        data['mezan_products_v2'][0] = Custom(data['mezan_products_v2'][0])
    elif shape == 'binding_subclass':
        data['mezan_product_option_cost_bindings_v2'][0] = Custom(data['mezan_product_option_cost_bindings_v2'][0])
    elif shape == 'custom_value':
        data['mezan_products_v2'][0]['unused'] = Value()
    else:
        data['mezan_products_v2'] = tuple(data['mezan_products_v2'])
    with authority.OptionAuthorityScope(data) as scope:
        assert scope.build() is None
        assert calls == []
        profile['cost_currency'] = 'KWD'
        with pytest.raises(ValueError, match='mz2_product_cost_currency_requires_review'):
            cost(data, item, profile, scope)
        assert calls == []
        profile['cost_currency'] = 'SAR'
        expected = outcome(lambda: cost(data, item, profile))
        calls.clear()
        assert outcome(lambda: cost(data, item, profile, scope)) == expected
        scope.validate()


@pytest.mark.parametrize('change', ['nested_label', 'binding_label', 'append_product', 'replace_root', 'error_set'])
def test_changed_authority_cannot_leave_the_attempt(change):
    data, item, profile = cost_fixture()
    with pytest.raises(ValueError, match='operational_option_authority_snapshot_changed'):
        with authority.OptionAuthorityScope(data) as scope:
            prepared = scope.build()
            cost(data, item, profile, prepared)
            if change == 'nested_label':
                data['mezan_products_v2'][0]['options'][0]['values'][0]['name'] = 'Changed'
            elif change == 'binding_label':
                data['mezan_product_option_cost_bindings_v2'][0]['value_name'] = 'Changed'
            elif change == 'append_product':
                data['mezan_products_v2'].append(deepcopy(data['mezan_products_v2'][0]))
            elif change == 'replace_root':
                data['mezan_products_v2'] = deepcopy(data['mezan_products_v2'])
            else:
                data['_option_catalog_errors'].add('p')
            scope.validate()
    assert scope.closed and not scope.active and scope.cache == {}


def test_normal_exit_requires_validation_and_closed_scope_cannot_reuse_catalog():
    data, item, profile = cost_fixture()
    with pytest.raises(ValueError, match='operational_option_authority_snapshot_changed'):
        with authority.OptionAuthorityScope(data) as scope:
            cost(data, item, profile, scope.build())
    data['mezan_products_v2'][0]['options'][0]['values'][0]['name'] = 'Changed'
    assert outcome(lambda: cost(data, item, profile, scope)) == outcome(lambda: cost(data, item, profile))
    with pytest.raises(ValueError, match='operational_option_authority_snapshot_changed'):
        scope.__enter__()


@pytest.mark.parametrize('kind', ['valid', 'duplicate_resource', 'duplicate_binding', 'foreign_cost', 'catalog_recovery'])
@pytest.mark.parametrize('replay', [False, True])
def test_complete_source_reconciliation_and_replay_match_default_path(monkeypatch, kind, replay):
    from operational_balance_engine import reconcile
    from operational_balance_store import initial
    data = collector_fixture(kind)
    state = initial('owner')
    state.update(status='active', started_at=START)
    if replay:
        first = asyncio.run(sources.collect_sources(collector_database(data), 'owner', START, NOW,
                                                   operational_state=state))
        state = reconcile(state, first, NOW)
    before = pickle.dumps((data, state), protocol=5)
    actual_db = collector_database(data)
    actual = asyncio.run(sources.collect_sources(actual_db, 'owner', START, NOW, operational_state=state))
    monkeypatch.setattr(authority.OptionAuthorityScope, 'build', lambda _self: None)
    expected_db = collector_database(data)
    expected = asyncio.run(sources.collect_sources(expected_db, 'owner', START, NOW, operational_state=state))
    assert actual == expected and actual_db.reads == expected_db.reads
    actual_state, expected_state = reconcile(state, actual, NOW), reconcile(state, expected, NOW)
    assert actual_state == expected_state
    assert reconcile(actual_state, actual, NOW) == actual_state
    assert pickle.dumps((data, state), protocol=5) == before


def test_collector_rebuilds_authority_for_each_attempt(monkeypatch):
    data = repeated_fixture(4)
    calls = []
    original = report_option_identity.catalog_options
    def counted(product):
        calls.append(product)
        return original(product)
    monkeypatch.setattr(report_option_identity, 'catalog_options', counted)
    first = asyncio.run(sources.collect_sources(collector_database(data), 'owner', START, NOW))
    assert len(calls) == 1
    data['mezan_products_v2'][0]['options'][0]['values'][0]['name'] = 'Changed'
    second = asyncio.run(sources.collect_sources(collector_database(data), 'owner', START, NOW))
    assert len(calls) == 2
    assert first != second and any(row['code'] == 'mz2_product_cost_incomplete' for row in second['issues'])


@pytest.mark.parametrize('phase', ['build', 'validate'])
def test_owned_preparation_cancellation_joins_and_closes_scope(monkeypatch, phase):
    from operational_financial_activity_runtime import prepare_in_thread
    data, item, profile = cost_fixture()
    entered, release, exited = Event(), Event(), Event()
    original = authority._checkpoint
    active = False
    def controlled():
        if active and not entered.is_set():
            entered.set()
            assert release.wait(3)
        original()
    monkeypatch.setattr(authority, '_checkpoint', controlled)
    async def run():
        nonlocal active
        scope = authority.OptionAuthorityScope(data)
        async def operation():
            nonlocal active
            with scope:
                if phase == 'validate':
                    await prepare_in_thread(scope.build)
                    cost(data, item, profile, scope)
                active = True
                def work():
                    try:
                        return getattr(scope, phase)()
                    finally:
                        exited.set()
                await prepare_in_thread(work)
        task = asyncio.create_task(operation())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and not exited.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert exited.is_set() and scope.closed and not scope.active and scope.cache == {}
            assert not [pending for pending in asyncio.all_tasks() if pending is not asyncio.current_task()]
        finally:
            release.set()
    asyncio.run(run())
