"""Contract-level checks for the independent frozen-response fee oracle."""
from copy import deepcopy

import pytest

from dashboard_runtime_fee_contract import (
    FEE_BASIS, FEE_SOURCE, OLD_FEE_SOURCE, extend_frozen_fee_contract,
)


def fixture_key(case, family="test_dashboard_mixed_permissions_and_tenants"):
    return f"tests/test_dashboard_b_index_cooperative_correctness.py::{family}[{case}]::request-0"


def response_fixture(*, unknown_fx=False, inferred_hidden=False, electronic=True):
    row = {
        "key": "salla", "orders_count": 1, "total_sales": 100.0,
        "fee_amount": 2.3, "vat_amount": .3, "sub_methods": [],
    }
    if unknown_fx:
        count, sales, fee, vat = (5, 400.0, 40.78, 5.32) if inferred_hidden else (6, 500.0, 50.54, 6.59)
        row = {
            "key": "tamara", "orders_count": count, "total_sales": None,
            "fee_amount": None, "vat_amount": None, "commission_percent": 6.99,
            "fixed_fee": 1.5, "vat_percent": 15.0,
            "known_total_sales_sar": sales, "known_fee_amount_sar": fee,
            "known_vat_amount_sar": vat, "currency_conversion_complete": False,
            "sub_methods": [{"key": "tamara", "orders_count": count,
                "known_total_sales_sar": sales, "known_fee_amount_sar": fee,
                "fee_amount": None, "currency_conversion_complete": False}],
        }
    return {
        "range": {"from_date": "2026-10-01", "to_date": "2026-10-08"},
        "totals": {
            "total_orders": 1, "total_sales": 100.0, "total_payment_fees": 7.49,
            "net_profit": 60.0, "net_sales": 95.0, "expected_salla_transfer": 97.7,
            "total_vat": .3, "other_payment_fees": 2.3, "electronic_net": 97.7,
            "electronic_net_before_settlements": 97.7,
            "tamara_fees": 0.0, "bnpl_fees": 0.0, "bnpl_net": 0.0,
            "unverified_currency_orders_count": int(unknown_fx), "legacy_analyses_count": 0,
            "electronic_net_breakdown": {"included_count": int(electronic), "excluded_count": 0,
                "fees_before_filter": 2.3, "fees_after_filter": 2.3},
            "cod_unapproved": 0.0, "ads_spend_data_complete": True,
            "product_cost_complete": True,
        },
        "payment_breakdown": [row, {"key": "ad_bank_commissions", "orders_count": 0,
            "fee_amount": 5.19, "vat_amount": 0.0}],
        "net_sales_config": {"deduct_payment_fees": True},
        "product_cost_v2": {"total": 37.5},
        "source_contract": {"payment_gateway_fees": OLD_FEE_SOURCE},
    }


def test_complete_sar_money_counts_and_ad_fees_are_exactly_preserved():
    response = response_fixture()
    original = deepcopy(response)
    extend_frozen_fee_contract(response, fixture_case=fixture_key("prior_month"))
    authority = response.pop("payment_fee_authority")
    assert authority == {
        "basis": FEE_BASIS, "complete": True, "known_payment_fees_sar": 2.3,
        "reason_codes": [], "incomplete_buckets": [], "electronic_complete": True,
        "electronic_before_filter_complete": True, "legacy_analysis_fees_included": False,
    }
    assert response["totals"].pop("payment_fees_complete") is True
    assert response["totals"].pop("payment_fee_review_reasons") == []
    assert response["source_contract"]["payment_gateway_fees"] == FEE_SOURCE
    response["source_contract"]["payment_gateway_fees"] = OLD_FEE_SOURCE
    assert response == original


def test_fee_completeness_key_order_precedes_ads_and_product_enrichment():
    response = response_fixture()
    extend_frozen_fee_contract(response, fixture_case=fixture_key("prior_month"))
    keys = list(response["totals"])
    boundary = keys.index("cod_unapproved")
    assert keys[boundary:boundary + 5] == [
        "cod_unapproved", "payment_fees_complete", "payment_fee_review_reasons",
        "ads_spend_data_complete", "product_cost_complete",
    ]


@pytest.mark.parametrize("case,hidden,fee,vat", [
    ("current_month_mixed", False, 48.82, 6.37),
    ("hide_inferred", True, 39.05, 5.09),
])
def test_only_unpriced_order_fixed_fee_leaves_known_subtotal_with_aggregate_rounding(case, hidden, fee, vat):
    response = response_fixture(unknown_fx=True, inferred_hidden=hidden)
    original = deepcopy(response)
    extend_frozen_fee_contract(response, fixture_case=fixture_key(case))
    row = response["payment_breakdown"][0]
    assert (row["known_fee_amount_sar"], row["known_vat_amount_sar"]) == (fee, vat)
    assert row["sub_methods"][0]["known_fee_amount_sar"] == fee
    assert row["fees_complete"] is False
    assert row["orders_count"] == original["payment_breakdown"][0]["orders_count"]
    assert row["known_total_sales_sar"] == original["payment_breakdown"][0]["known_total_sales_sar"]
    assert response["payment_breakdown"][1] == original["payment_breakdown"][1]
    authority = response["payment_fee_authority"]
    assert authority["known_payment_fees_sar"] == fee
    assert authority["reason_codes"] == ["payment_method_unconfigured", "payment_sales_currency_unverified"]
    assert authority["incomplete_buckets"] == ["", "tamara"]
    assert response["totals"]["total_orders"] == original["totals"]["total_orders"]
    assert response["totals"]["total_payment_fees"] is None
    assert response["totals"]["electronic_net_breakdown"]["fees_before_filter"] is None
    assert response["totals"]["electronic_net_breakdown"]["fees_after_filter"] == 2.3


@pytest.mark.parametrize("field,value", [
    ("known_fee_amount_sar", 50.55), ("known_vat_amount_sar", 6.60),
    ("orders_count", 7), ("known_total_sales_sar", 501.0), ("fixed_fee", 1.51),
])
def test_unreviewed_financial_changes_cannot_be_normalized_away(field, value):
    response = response_fixture(unknown_fx=True)
    response["payment_breakdown"][0][field] = value
    with pytest.raises(AssertionError):
        extend_frozen_fee_contract(response, fixture_case=fixture_key("current_month_mixed"))


def test_blank_method_masks_only_affected_totals_and_does_not_change_ad_fee():
    response = response_fixture(electronic=False)
    response["payment_breakdown"] = response["payment_breakdown"][1:]
    extend_frozen_fee_contract(response, fixture_case=fixture_key("no_matching_filter"))
    assert response["payment_fee_authority"]["known_payment_fees_sar"] == 0.0
    assert response["payment_fee_authority"]["electronic_complete"] is False
    assert response["payment_fee_authority"]["reason_codes"] == ["payment_method_unconfigured"]
    assert response["totals"]["total_sales"] == 100.0
    assert response["totals"]["other_payment_fees"] is None
    assert response["payment_breakdown"][0]["fee_amount"] == 5.19


def test_arabic_status_registry_fx_order_is_not_in_legacy_fee_reader_cohort():
    response = response_fixture(electronic=False)
    response["payment_breakdown"] = response["payment_breakdown"][1:]
    response["totals"].update(total_orders=7, unverified_currency_orders_count=1)
    extend_frozen_fee_contract(response, fixture_case=fixture_key("arabic_status_normalization"))
    assert response["payment_fee_authority"]["reason_codes"] == ["payment_method_unconfigured"]
    assert response["payment_fee_authority"]["incomplete_buckets"] == [""]
    assert response["totals"]["total_orders"] == 7


def test_permission_failure_retains_exact_body_and_unknown_case_rejects():
    denied = {"http_error": 403, "detail": "permission"}
    assert extend_frozen_fee_contract(denied, fixture_case=fixture_key("permission_denied")) == denied
    with pytest.raises(AssertionError, match="Unreviewed"):
        extend_frozen_fee_contract(response_fixture(), fixture_case=fixture_key("unreviewed"))


def test_mutation_fee_snapshot_is_independent_of_later_v2_zero_count():
    response = response_fixture()
    response["totals"]["total_orders"] = 0
    extend_frozen_fee_contract(response, fixture_case=fixture_key("status", "test_existing_independent_read_mutations"))
    assert response["payment_fee_authority"]["complete"] is True
    assert response["payment_fee_authority"]["known_payment_fees_sar"] == 2.3
    assert response["totals"]["total_orders"] == 0
    assert response["payment_breakdown"][0]["orders_count"] == 1
