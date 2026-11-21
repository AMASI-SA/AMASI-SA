"""Count arithmetic/input contracts; transaction behavior has a real Mongo suite."""
from decimal import Decimal
import pytest
from fastapi import HTTPException
from inventory_stocktake_service import quantity, compare_line, posting_enabled


def line(**changes):
    row = {"kind": "product", "unit": "piece", "recorded_quantity": "10",
           "counted_quantity": "8", "unit_cost": "12.50", "reason": "Damaged on inspection"}
    return {**row, **changes}


def test_partial_count_is_not_zero_and_unknown_cost_is_not_guessed():
    assert compare_line(line(counted_quantity=None))["difference"] is None
    assert compare_line(line(unit_cost=None))["difference_value"] is None
    assert compare_line(line(counted_quantity="0"))["difference"] == "-10"


def test_positive_negative_and_native_component_units():
    assert compare_line(line())["difference_value"] == "-25.00"
    assert compare_line(line(counted_quantity="13"))["difference_value"] == "37.50"
    r = compare_line(line(kind="component", unit="gram", counted_quantity="10.125", unit_cost="2"))
    assert r["difference"] == "0.125" and r["difference_value"] == "0.25"
    assert quantity("0.000001", kind="component") == Decimal("0.000001")


@pytest.mark.parametrize("value", [True, False, "NaN", "Infinity", "-1", "1e100", "0.0000001", "", {}, None])
def test_invalid_numbers_never_coerce_to_stock(value):
    with pytest.raises(HTTPException): quantity(value, kind="component")


def test_product_count_must_be_integral():
    with pytest.raises(HTTPException): quantity("1.5", kind="product")


def test_posting_is_closed_without_explicit_development_flag(monkeypatch):
    monkeypatch.delenv("MEZAN_STOCKTAKE_POSTING_ENABLED", raising=False)
    assert not posting_enabled()
    monkeypatch.setenv("MEZAN_STOCKTAKE_POSTING_ENABLED", "true")
    assert not posting_enabled()
    monkeypatch.setenv("MEZAN_STOCKTAKE_POSTING_ENABLED", "1")
    assert posting_enabled()
