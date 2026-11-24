"""Read-only cost previews reuse catalogue lookups without caching prices."""
from collections import Counter
from copy import deepcopy

import pytest

import operational_balance_sources as sources
import operational_cost_catalog_index as catalog
import operational_open_cost_exposure as exposure
from test_operational_balance_sources import DB, row, run
from test_operational_open_cost_exposure import NOW, native_order, operating_state


def workload(repeats=12):
    data = {
        'unified_orders': [],
        'mezan_product_cost_profiles_v2': [
            row(salla_product_id='p1', base_cost='20', variant_costs={'adult': '30'}),
            row(salla_product_id='p2', base_cost='11')],
        catalog.RESOURCES: [
            row(id='cloth', kind='component', unit_cost='5'),
            row(id='print', kind='service', unit_cost='7')],
        catalog.PRODUCT_BINDINGS: [
            row(id='material', salla_product_id='p1', resource_id='cloth', quantity='2')],
        catalog.OPTION_BINDINGS: [
            row(id='direct', salla_product_id='p1', option_id='color', value_id='red',
                option_name='Color', value_name='Red', mode='direct', direct_amount='3', quantity='2'),
            row(id='service', salla_product_id='p1', option_id='color', value_id='red',
                option_name='Color', value_name='Red', mode='resource', resource_id='print')],
    }
    for i in range(repeats * 3):
        order = native_order(number='OLD-' + str(i), qty=2)
        item = order['raw_by_source']['salla_direct']['items'][0]
        if i % 3 == 2:
            item.update(product_id='p2', quantity=1)
        else:
            red = i % 3 == 0
            item['options'] = [{'id': 'color', 'name': 'Color',
                                'value': {'id': 'red' if red else 'blue',
                                          'name': 'Red' if red else 'Blue'}}]
            if not red:
                item['variant_id'] = 'adult'
        data['unified_orders'].append(order)
    # A larger unrelated catalogue must not multiply the work per order.
    for i in range(64):
        data[catalog.RESOURCES].append(row(id='unused-' + str(i), unit_cost='bad-unused-price'))
        data[catalog.PRODUCT_BINDINGS].append(row(id='unrelated-' + str(i),
            salla_product_id='other-' + str(i), resource_id='unused-' + str(i)))
        data[catalog.OPTION_BINDINGS].append(row(id='unselected-' + str(i),
            salla_product_id='other-' + str(i), mode='direct', direct_amount='bad-unused-price'))
    return data


def project(data, state=None):
    db, state = DB(deepcopy(data)), deepcopy(state or operating_state())
    before_data, before_state = deepcopy(db.data), deepcopy(state)
    result = run(exposure.project(db, 'owner', state, as_of=NOW))
    assert db.data == before_data and state == before_state
    return result, Counter(db.reads)


def count_resource_indexes(monkeypatch):
    calls = []
    original = sources.unique_index

    def counted(records, field):
        records = list(records)
        if field == 'id' and any(r.get('id') == 'cloth' for r in records):
            calls.append(len(records))
        return original(records, field)

    monkeypatch.setattr(sources, 'unique_index', counted)
    monkeypatch.setattr(exposure, 'unique_index', counted)
    return calls


def test_preview_indexes_catalogue_once_for_many_historical_items(monkeypatch):
    calls = count_resource_indexes(monkeypatch)
    result, reads = project(workload())
    # Per three orders: 2 * (20 + 2*5 + 2*3 + 7), 2 * (30 + 2*5), 11.
    assert result['currencies']['SAR']['prestart_supplier_unreceived'] == '2124.00'
    assert result['currencies']['SAR']['prestart_supplier_expected'] == '2124.00'
    assert calls == [66]
    assert all(reads[name] == 1 for name in exposure.READS)
    calls.clear()
    monkeypatch.setattr(exposure, 'build_cost_catalog_index', lambda *_: None)
    assert project(workload()) == (result, reads)
    assert calls == [66] * 36


class CustomResource(dict):
    """An unsupported source mapping must retain the old per-item fallback."""


@pytest.mark.parametrize('change, indexed', [
    ('ordinary', True), ('duplicate_resource', True), ('duplicate_binding', True),
    ('foreign_currency', True), ('bad_selected_price', True), ('custom_mapping', False),
    ('complex_identity', False), ('oversized_identity', False),
])
def test_full_preview_matches_legacy_result_and_reads(monkeypatch, change, indexed):
    data = workload(2)
    if change == 'duplicate_resource':
        data[catalog.RESOURCES].append(deepcopy(data[catalog.RESOURCES][0]))
    elif change == 'duplicate_binding':
        data[catalog.OPTION_BINDINGS][0]['id'] = 'material'
    elif change == 'foreign_currency':
        data[catalog.RESOURCES][0]['cost_currency'] = 'USD'
    elif change == 'bad_selected_price':
        data[catalog.OPTION_BINDINGS][0]['direct_amount'] = 'not-money'
    elif change == 'custom_mapping':
        data[catalog.RESOURCES][0] = CustomResource(data[catalog.RESOURCES][0])
    elif change == 'complex_identity':
        data[catalog.PRODUCT_BINDINGS][-1]['salla_product_id'] = []
    elif change == 'oversized_identity':
        data[catalog.PRODUCT_BINDINGS][-1]['salla_product_id'] = 10 ** 5000
    built = []

    def build(*args):
        index = catalog.build_cost_catalog_index(*args)
        built.append(index)
        return index

    monkeypatch.setattr(exposure, 'build_cost_catalog_index', build)
    actual = project(data)
    assert len(built) == 1 and (built[0] is not None) is indexed
    monkeypatch.setattr(exposure, 'build_cost_catalog_index', lambda *_: None)
    expected = project(data)
    assert actual == expected
    if change in {'duplicate_resource', 'duplicate_binding', 'foreign_currency',
                  'bad_selected_price', 'oversized_identity'}:
        assert 'historical_supplier_cost_incomplete' in {r['code'] for r in actual[0]['issues']}


def test_new_projection_observes_replaced_catalogue_and_variant_prices(monkeypatch):
    calls = count_resource_indexes(monkeypatch)
    data = workload(1)
    first, _ = project(data)
    data[catalog.RESOURCES][0]['unit_cost'] = '8'
    data['mezan_product_cost_profiles_v2'][0]['variant_costs']['adult'] = '31'
    second, _ = project(data)
    assert first['currencies']['SAR']['prestart_supplier_unreceived'] == '177.00'
    # Cloth increased by 3 * 2 per unit across four units; the variant by 1 * 2.
    assert second['currencies']['SAR']['prestart_supplier_unreceived'] == '203.00'
    assert calls == [66, 66]


def test_foreign_owner_catalogue_rows_cannot_change_preview():
    data = workload(1)
    before = project(data)
    data[catalog.RESOURCES].append(dict(data[catalog.RESOURCES][0], user_id='other-owner', unit_cost='999'))
    data[catalog.PRODUCT_BINDINGS].append(row(user_id='other-owner', id='foreign-binding',
                                             salla_product_id='p1', resource_id='print', quantity='100'))
    assert project(data) == before


def test_rejected_currency_keeps_custom_resource_access_lazy():
    accesses = []

    class ExplodingResource(dict):
        def get(self, key, *args):
            if key == 'id':
                accesses.append(key)
                raise RuntimeError('unreachable resource identity')
            return super().get(key, *args)

    data = workload(1)
    data[catalog.RESOURCES][0] = ExplodingResource(data[catalog.RESOURCES][0])
    for profile in data['mezan_product_cost_profiles_v2']:
        profile['cost_currency'] = 'USD'
    result, _ = project(data)
    assert accesses == []
    assert 'historical_supplier_cost_incomplete' in {r['code'] for r in result['issues']}
    assert not any(g['kind'] == 'supplier' for g in result['groups'])
