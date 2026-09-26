from decimal import Decimal

import pytest
from fastapi import HTTPException

from purchase_receiving_service import moving_average, purchase_amounts


def test_remaining_stock_weighted_average_and_zero_stock():
    assert moving_average(10, '4', 5, '10') == Decimal('6.000000')
    assert moving_average(0, None, 3, '7.25') == Decimal('7.250000')


@pytest.mark.parametrize('quantity,cost', [(3, None), (-1, '5'), ('NaN', '5')])
def test_existing_stock_without_cost_or_invalid_quantity_blocks(quantity, cost):
    with pytest.raises(HTTPException):
        moving_average(quantity, cost, 2, '8')


def test_purchase_tax_treatment_changes_capitalized_cost_not_payable():
    lines = [{'id': 'a', 'quantity': 3, 'unit_cost': '10'}]
    deductible = purchase_amounts(lines, '4.50', 'deductible')
    capitalized = purchase_amounts(lines, '4.50', 'non_deductible')
    assert deductible['inventory_total'] == Decimal('30.00')
    assert deductible['input_vat'] == Decimal('4.50')
    assert deductible['total'] == Decimal('34.50')
    assert capitalized['inventory_total'] == Decimal('34.50')
    assert capitalized['input_vat'] == Decimal('0.00')
    assert capitalized['total'] == deductible['total']
    assert capitalized['line_costs']['a'] == Decimal('34.50')
