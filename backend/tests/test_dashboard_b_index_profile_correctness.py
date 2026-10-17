"""Index timer equivalence: values, insertion order and object aliasing."""
import copy
import json

import pytest
from dashboard_b_ads_instrumentation import Recorder
from dashboard_b_index_instrumentation import instrumented
from product_catalog_cost_resolution import (
    NAME_ALIAS_PREFIX, index_current_catalog_products, normalize_product_name,
)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(',', ':')).encode('utf8')


def aliases(maps):
    """Reference-equivalence graph; equality alone misses first/last objects."""
    nodes = [value for mapping in maps for value in mapping.values()]
    nodes += [variant for value in nodes[:] for variant in value.get('variants', [])]
    return [[left is right for right in nodes] for left in nodes]


def products_fixture(case):
    if case == 'identifier_sku_last_wins':
        return [
            {'salla_product_id': 'same', 'mezan_product_id': 'm1', 'id': 'old', 'sku': 'SKU', 'name': 'Old'},
            {'salla_product_id': 'same', 'mezan_product_id': 'm2', 'id': 'new', 'sku': 'sku', 'name': 'New'},
        ]
    if case == 'name_first_object_same_identity':
        return [{'salla_product_id': 'same', 'id': 'old', 'name': 'Same_Name', 'cost_price': 10},
                {'salla_product_id': 'same', 'id': 'new', 'name': 'same name', 'cost_price': 20}]
    if case == 'name_first_without_identity':
        return [{'name': 'Same name', 'sku': 'first'}, {'salla_product_id': 'one', 'name': 'same_name', 'sku': 'second'}]
    if case == 'name_ambiguous':
        return [{'salla_product_id': 'one', 'name': 'Same name'}, {'salla_product_id': 'two', 'name': 'same_name'}]
    if case == 'name_without_identity':
        return [{'name': 'Same name'}, {'name': 'same_name'}]
    if case == 'name_alias_overwrites_sku':
        return [{'salla_product_id': 'one', 'name': 'Foo'},
                {'salla_product_id': 'two', 'name': 'Bar', 'sku': NAME_ALIAS_PREFIX + 'foo'}]
    if case == 'variant_duplicates_and_raw':
        return [{'salla_product_id': 'one', 'name': 'One', 'variants': [
            {'id': 'dup', 'sku': 'SKU', 'cost_price_from_salla': 1, 'options': {'color': 'black'}},
            {'id': 'dup', 'sku': 'sku', 'cost_price_from_salla': ''}, None, 3],
            'raw_salla_details': {'variants': [
                {'id': 'dup', 'sku': 'sku', 'cost_price': 17},
                {'id': 'dup', 'sku': 'sku', 'cost_price': 99},
                {'id': 'raw', 'sku': 'RAW', 'cost': 23}, 'malformed']}}]
    if case == 'variant_cross_product_collision':
        return [{'salla_product_id': 'one', 'variants': [{'id': 'v', 'sku': 'shared'}]},
                {'salla_product_id': 'two', 'sku': 'shared', 'variants': [{'id': 'v', 'sku': 'SHARED'}]}]
    if case == 'raw_cost_precedence_and_zero':
        return [{'salla_product_id': 'one', 'cost_price': 5, 'raw_salla': {'cost_price': 6},
                 'raw_salla_details': {'cost_price': {'amount': '7.25'}, 'cost': 8}},
                {'salla_product_id': 'zero', 'cost_price_from_salla': 0, 'raw_salla_details': {'cost_price': 99}},
                {'salla_product_id': 'raw-light', 'raw_salla_details': {'variants': []},
                 'raw_salla': {'skus': [{'id': 'lv', 'cost_price': {'amount': '3.335'}}]}}]
    if case == 'unicode':
        return [{'salla_product_id': 'one', 'sku': ' STRAßE ', 'name': '  Abaya_شال!!!  '},
                {'salla_product_id': 'two', 'sku': 'strasse', 'name': 'إبَايَة'}]
    if case == 'malformed_supported':
        return [None, False, [], {}, [('salla_product_id', 'pairs')],
                {'salla_product_id': 0, 'id': True, 'name': ['odd'], 'variants': 'not-list', 'raw_salla_details': 7}]
    if case == 'empty':
        return []
    raise AssertionError(case)


CASES = ['identifier_sku_last_wins', 'name_first_object_same_identity',
         'name_first_without_identity', 'name_ambiguous', 'name_without_identity',
         'name_alias_overwrites_sku', 'variant_duplicates_and_raw',
         'variant_cross_product_collision', 'raw_cost_precedence_and_zero',
         'unicode', 'malformed_supported', 'empty']


@pytest.mark.parametrize('fine', [False, True], ids=['coarse', 'fine'])
@pytest.mark.parametrize('case', CASES)
def test_index_values_order_aliases_and_input_immutability(fine, case):
    products = products_fixture(case)
    before = encoded(products)
    expected = index_current_catalog_products(products)
    actual = instrumented(Recorder(), fine=fine)(products)
    assert encoded(actual) == encoded(expected)
    assert aliases(actual) == aliases(expected)
    assert encoded(products) == before
    by_id, by_variant, by_sku = actual
    name = NAME_ALIAS_PREFIX + 'same name'
    if case == 'identifier_sku_last_wins':
        assert by_id['same'] is by_id['new'] is by_id['m2'] is by_sku['sku']
        assert by_id['old'] is not by_id['same']
    elif case == 'name_first_object_same_identity':
        assert by_sku[name] is by_id['old']
        assert by_sku[name] is not by_id['same']
        assert by_id['same']['cost_price_from_salla'] == 20
    elif case == 'name_first_without_identity':
        assert by_sku[name] is by_sku['first']
        assert by_sku[name] is not by_id['one']
    elif case in {'name_ambiguous', 'name_without_identity'}:
        assert name not in by_sku
    elif case == 'name_alias_overwrites_sku':
        assert by_sku[NAME_ALIAS_PREFIX + 'foo'] is by_id['one']
    elif case == 'variant_duplicates_and_raw':
        variants = by_id['one']['variants']
        assert [row['cost_price_from_salla'] for row in variants] == [1, 17, 23]
        assert by_variant['dup'] is by_variant['raw'] is by_sku['raw'] is by_id['one']
    elif case == 'variant_cross_product_collision':
        assert by_variant['v'] is by_sku['shared'] is by_id['two']
    elif case == 'raw_cost_precedence_and_zero':
        assert by_id['one']['cost_price_from_salla'] == 7.25
        assert by_id['zero']['cost_price_from_salla'] == 0
    elif case == 'unicode':
        assert by_sku['strasse'] is by_id['two']
        assert by_sku[NAME_ALIAS_PREFIX + normalize_product_name(products[0]['name'])] is by_id['one']


@pytest.mark.parametrize('fine', [False, True])
def test_shallow_copy_and_nested_sharing_are_preserved(fine):
    product = {'salla_product_id': 'one', 'id': 'alternate', 'sku': 'sku', 'name': 'One',
               'options': {'fabric': ['silk']}, 'raw_salla_details': {'cost_price': 4},
               'variants': [{'id': 'v', 'sku': 'vs', 'options': {'color': ['black']}}]}
    before = copy.deepcopy(product)
    by_id, by_variant, by_sku = instrumented(Recorder(), fine=fine)([product])
    row = by_id['one']
    assert row is not product
    assert row is by_id['alternate'] is by_variant['v'] is by_sku['sku'] is by_sku['vs']
    assert row['options'] is product['options']
    assert row['raw_salla_details'] is product['raw_salla_details']
    assert row['variants'] is not product['variants']
    assert row['variants'][0] is not product['variants'][0]
    assert row['variants'][0]['options'] is product['variants'][0]['options']
    assert product == before


@pytest.mark.parametrize('fine', [False, True])
@pytest.mark.parametrize('products', [None, [7], ['not-a-mapping'], [[('broken',)]], [{'variants': [{'id': 'v'}]}, 7]])
def test_malformed_exception_parity(fine, products):
    before = encoded(products)
    with pytest.raises(Exception) as expected:
        index_current_catalog_products(products)
    with pytest.raises(type(expected.value)) as actual:
        instrumented(Recorder(), fine=fine)(products)
    assert str(actual.value) == str(expected.value)
    assert encoded(products) == before
