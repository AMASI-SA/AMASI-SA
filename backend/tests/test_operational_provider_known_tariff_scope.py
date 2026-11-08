"""Known tariff subsets must not pretend to be complete provider balances."""
from copy import deepcopy
from decimal import Decimal

import pytest

from operational_provider_summary import provider_balances
from test_operational_provider_fee_comparison import snapshot


def project(args, **options):
    before = deepcopy(args[:3])
    result, _ = provider_balances(*args, **options)
    assert args[:3] == before
    return next(row for rows in result.values() for row in rows)


def test_one_unresolved_order_retains_only_the_proven_tariff_subtotal():
    args = snapshot(opening='0')
    held = {**args[2][0], 'id': 'provider:held', 'incomplete': True}
    args[2].append(held)
    args[3]['provider']['outstanding_receivable'] += Decimal('100')
    row = project(args)
    assert row['tariff_fees'] is None and row['tariff_net'] is None and row['net'] is None
    assert not row['complete'] and not row['tariff_complete']
    assert row['tariff_known_order_count'] == 1 and row['tariff_unresolved_order_count'] == 1
    assert (row['known_tariff_gross'], row['known_tariff_refunded']) == ('100.00', '0.00')
    assert (row['known_tariff_fees'], row['known_tariff_net']) == ('10.00', '90.00')
    assert row['actual_settlement_fees'] is None and row['fee_variance'] is None


def test_no_proven_tariff_orders_is_unknown_not_zero():
    row = project(snapshot(tariff=None))
    assert row['tariff_known_order_count'] == 0 and row['tariff_unresolved_order_count'] == 1
    for key in ('known_tariff_gross', 'known_tariff_refunded', 'known_tariff_fees', 'known_tariff_net'):
        assert row[key] is None


@pytest.mark.parametrize('currency', ['SAR', 'QAR'])
def test_known_scope_matches_complete_tariff_scope_without_currency_conversion(currency):
    row = project(snapshot(currency=currency, refund='20', tariff='10'))
    assert row['tariff_known_order_count'] == 1 and row['tariff_unresolved_order_count'] == 0
    assert row['known_tariff_gross'] == '100.00' and row['known_tariff_refunded'] == '20.00'
    assert row['known_tariff_fees'] == row['tariff_fees'] == '10.00'
    assert row['known_tariff_net'] == row['tariff_net'] == '70.00'


@pytest.mark.parametrize('paid,known,orders', [('300.00', '300.00', 3), (None, '100.00', 3), ('150.00', '150.00', 1)])
def test_missing_customer_payment_scope_does_not_invent_unresolved_order_count(paid, known, orders):
    args = snapshot(opening='0')
    args[1]['customer_tender_coverage'] = {'sar': {'by_method': [
        {'method': 'tamara', 'orders': orders, 'paid': paid, 'known_paid': known}]}}
    row = project(args, sar_view=True)
    # Tender method counts can include unpaid orders, so they cannot establish
    # the number of missing paid orders when the paid scope itself is incomplete.
    assert row['tariff_known_order_count'] == 1 and row['tariff_unresolved_order_count'] is None
    assert row['known_tariff_net'] == '90.00' and row['known_tariff_gross'] == '100.00'
    assert row['tariff_net'] is None and row['net'] is None


def test_unpaid_order_does_not_count_as_a_known_or_unresolved_paid_order():
    args = snapshot(tariff='0')
    args[2].append({**args[2][0], 'id': 'provider:unpaid', 'confirmed': '0', 'expected': '100'})
    row = project(args)
    assert row['tariff_known_order_count'] == 1 and row['tariff_unresolved_order_count'] == 0
    assert row['known_tariff_fees'] == '0.00' and row['known_tariff_net'] == '100.00'


def test_fully_settled_missing_tariff_does_not_turn_actual_fee_into_known_tariff():
    row = project(snapshot(applied='100', actual='12', tariff=None))
    assert row['complete'] and row['actual_settlement_fees'] == '12.00'
    assert row['known_tariff_fees'] is None and row['known_tariff_net'] is None
    assert row['tariff_known_order_count'] == 0 and row['tariff_unresolved_order_count'] == 1
