import pytest
from pydantic import ValidationError

from courier_cod_fee_rules import (
    CourierCodFeeTier,
    calculate_courier_cod_fee,
    validate_courier_cod_fee_tiers,
)


SMSA_RULES = [
    {
        "min_amount": 50,
        "max_amount": 1000,
        "min_inclusive": True,
        "max_inclusive": True,
        "commission_percent": 0.01,
        "fixed_fee": 2,
        "vat_percent": 15,
    },
    {
        "min_amount": 1000,
        "max_amount": 3000,
        "min_inclusive": False,
        "max_inclusive": True,
        "commission_percent": 0.02,
        "fixed_fee": 5,
        "vat_percent": 15,
    },
    {
        "min_amount": 3000,
        "max_amount": None,
        "min_inclusive": False,
        "max_inclusive": True,
        "commission_percent": 0.03,
        "fixed_fee": 0,
        "vat_percent": 15,
    },
]


def test_smsa_tiers_keep_shared_boundaries_unambiguous_and_split_vat():
    company = {"cod_fee_tiers": SMSA_RULES}

    at_1000 = calculate_courier_cod_fee(1000, company)
    assert at_1000["source"] == "tier"
    assert at_1000["commission_percent"] == 1.0
    assert at_1000["fee_net"] == 12.0
    assert at_1000["fee_vat"] == 1.8
    assert at_1000["fee_total"] == 13.8

    above_1000 = calculate_courier_cod_fee(1000.01, company)
    assert above_1000["commission_percent"] == 2.0
    assert above_1000["fixed_fee"] == 5.0

    at_3000 = calculate_courier_cod_fee(3000, company)
    assert at_3000["commission_percent"] == 2.0
    assert at_3000["fee_net"] == 65.0

    above_3000 = calculate_courier_cod_fee(3000.01, company)
    assert above_3000["commission_percent"] == 3.0
    assert above_3000["fixed_fee"] == 0.0


def test_uncovered_tier_range_is_flagged_instead_of_silently_charged_zero():
    result = calculate_courier_cod_fee(49.99, {"cod_fee_tiers": SMSA_RULES})
    assert result["needs_review"] is True
    assert result["source"] == "tier_unmatched"
    assert result["fee_total"] == 0.0


def test_overlapping_tiers_are_rejected():
    with pytest.raises(ValueError, match="overlap"):
        validate_courier_cod_fee_tiers([
            {**SMSA_RULES[0]},
            {**SMSA_RULES[1], "min_inclusive": True},
        ])


def test_empty_and_reversed_ranges_are_rejected():
    with pytest.raises(ValidationError):
        CourierCodFeeTier(
            min_amount=100,
            max_amount=50,
            commission_percent=0.01,
        )
    with pytest.raises(ValidationError):
        CourierCodFeeTier(
            min_amount=100,
            max_amount=100,
            min_inclusive=False,
            max_inclusive=True,
            commission_percent=0.01,
        )


def test_legacy_flat_rule_still_calculates_fee_and_vat():
    result = calculate_courier_cod_fee(500, {
        "cod_fee_percent": 0.01,
        "cod_fee_fixed_per_order": 2,
        "cod_fee_vat_percent": 15,
    })
    assert result["source"] == "flat"
    assert result["fee_net"] == 7.0
    assert result["fee_vat"] == 1.05
    assert result["fee_total"] == 8.05
