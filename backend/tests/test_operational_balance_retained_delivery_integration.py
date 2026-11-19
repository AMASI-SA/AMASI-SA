"""Exercise the collection, settlement projection and scoped financial report together."""
from copy import deepcopy

import pytest

from operational_financial_position import build_financial_position
from test_operational_balance_delivery_start_rule import calculate, undated
from test_operational_balance_delivery_cutover import fee_total


@pytest.mark.parametrize(('fixture_kind', 'view'), [('courier', 'couriers'), ('native_driver', 'drivers')])
def test_later_status_preserves_count_original_fee_and_custody(fixture_kind, view):
    data = undated(fixture_kind)
    source = data['unified_orders'][0]
    original = calculate(data)
    key = 'salla_delivered_order:100'
    party = original['engine']['delivery_register'][key]['party_id']
    assert fee_total(original) > 0
    current = original
    for status in ('canceled', 'restored', 'custom_after_delivery', 'delivered'):
        source['raw_by_source']['salla_direct']['status'] = status
        current = calculate(data, prior=current)
        assert current['engine']['delivery_register'][key]['party_id'] == party
        assert fee_total(current) == fee_total(original)
        assert current['engine']['facts'].get('cod_collection:100') == original['engine']['facts'].get('cod_collection:100')
        result = build_financial_position(current, view=view)
        notice = result['delivered_status_notice']
        assert notice['count'] == int(status in {'canceled', 'restored'})
        if notice['count']:
            assert notice['groups'][0]['orders'][0]['order_number'] == '10'
        table = next(t for t in result['currencies']['SAR']['tables'] if t['id'] == view)
        assert table['totals']['delivered_orders'] == 1


def test_salla_status_and_creation_survive_stale_snapshot_missing_waybill():
    data = undated()
    source = data['unified_orders'][0]
    source['g47_salla_snapshot'] = {'requires_authoritative_refresh': True}
    source['salla_shipping_current'].pop('shipment_id', None)
    source['salla_shipping_current'].pop('tracking_number', None)
    first = calculate(data)
    assert first['engine']['delivery_register']['salla_delivered_order:100']['party_type'] == 'courier'
    assert first['engine']['obligations']['shipping:100:base_shipping']['confirmed'] == '10.00'
    second = calculate(deepcopy(data), prior=first)
    assert fee_total(second) == fee_total(first)
    assert second['movements'] == first['movements']


def test_old_order_cannot_enter_new_delivery_register_or_earn_new_fee():
    from test_operational_balance_delivery_cutover import courier_data
    result = calculate(courier_data())
    assert 'salla_delivered_order:100' not in result['engine']['delivery_register']
    assert fee_total(result) == 0


def test_aramex_completion_and_later_delivery_keep_one_wallet_debit():
    from test_operational_balance_prepaid_shipping import fixture, calculate as calculate_prepaid
    data, state = fixture(old=False, current='completed', at=None)
    source = data['unified_orders'][0]
    source['g47_salla_snapshot']['requires_authoritative_refresh'] = True
    raw = source['raw_by_source']['salla_direct']
    for status in ('completed', 'delivered', 'canceled', 'restored'):
        raw['status'] = status
        state = calculate_prepaid(data, state)
        assert len(state['movements']) == 1
        assert state['movements'][0]['bank_id'] == 'salla-cash'
        assert state['movements'][0]['amount'] == '34.50'
    notice = build_financial_position(state, view='couriers')['delivered_status_notice']
    assert notice['count'] == 1 and notice['groups'][0]['status'] == 'restored'
