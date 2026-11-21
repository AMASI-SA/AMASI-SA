"""Independent fee-contract extension for the immutable 05b159dc fixtures.

This oracle never imports the candidate fee calculator. The frozen fixture has
no actual fee rows, dated policies, plans, or waiting/wating payment methods.
All complete legacy amounts remain untouched. The one approved numerical
correction excludes order 1000010's fixed fee from Tamara's *known* subtotal:
that KWD order has no verified SAR amount on which to calculate its full fee.
"""
from decimal import Decimal
import re


FEE_BASIS = "actual_statement_then_dated_provider_policy_then_legacy_settings"
OLD_FEE_SOURCE = "legacy_payment_method_settings + mezan_ad_account_cost_settings_v2"
FEE_SOURCE = FEE_BASIS + " + mezan_ad_account_cost_settings_v2"

MIXED_CASES = frozenset({
    "current_month_mixed", "overlapping_period", "prior_month", "empty_period",
    "first_day_inclusive", "last_day_inclusive", "no_dates", "from_only",
    "to_only", "hide_inferred", "include_inferred", "payment_substring_case",
    "bnpl_and_cod", "shipping_substring", "combined_filters", "no_matching_filter",
    "all_statuses", "completed_status", "arabic_status_normalization",
    "returns_only", "electronic_no_exclusions", "rounding_and_known_fx",
    "other_owner", "permission_denied",
})
MIXED_WITHOUT_BLANK_METHOD = frozenset({
    "prior_month", "empty_period", "first_day_inclusive", "last_day_inclusive",
    "other_owner", "permission_denied",
})
MIXED_WITH_UNVERIFIED_FEE_BASIS = frozenset({
    "current_month_mixed", "overlapping_period", "no_dates", "from_only",
    "to_only", "hide_inferred", "include_inferred", "bnpl_and_cod",
    "shipping_substring", "all_statuses", "electronic_no_exclusions",
})
MUTATION_CASES = frozenset({
    "status", "payment_status", "shipping_status", "payment_only",
    "shipping_only", "product_profile_cost", "embedded_order_cost",
    "advertising_google", "date_universe", "irrelevant_note",
})
INDEX_CASES = frozenset({
    "catalog", "profile", "product_binding", "option_binding", "resource", "policy",
})


def _fixture_contract(fixture_case):
    match = re.search(r"::([^:]+)\[([^]]+)\]::request-\d+$", fixture_case or "")
    assert match, "Fee oracle requires a named immutable fixture request"
    family, case = match.groups()
    if family == "test_dashboard_mixed_permissions_and_tenants":
        assert case in MIXED_CASES, "Unreviewed immutable mixed fee fixture"
        return case not in MIXED_WITHOUT_BLANK_METHOD, case in MIXED_WITH_UNVERIFIED_FEE_BASIS, case
    if family == "test_existing_independent_read_mutations":
        assert case in MUTATION_CASES, "Unreviewed immutable mutation fee fixture"
    elif family == "test_mutations_during_index_preserve_captured_catalog_and_later_reads":
        assert case in INDEX_CASES, "Unreviewed immutable index fee fixture"
    else:
        raise AssertionError("Unreviewed immutable fee fixture family")
    return False, False, case


def _aggregate_tamara_fee(sales, count):
    # Frozen defaults: 6.99% + 1.50 SAR per order, VAT 15%. These are immutable
    # fixture terms, not values read from candidate settings or calculations.
    # Preserve the original aggregate rounding (rather than subtracting a
    # separately rounded per-order 1.725 SAR from the inclusive subtotal).
    base = round(float(sales) * 6.99 / 100 + count * 1.50, 2)
    vat = round(base * .15, 2)
    return round(base + vat, 2), vat


def _exclude_unknown_fx_fixed_fee(response, case):
    rows = [row for row in response["payment_breakdown"] if row["key"] == "tamara"]
    assert len(rows) == 1, "Frozen unknown-FX Tamara bucket missing or duplicated"
    row = rows[0]
    count, sales = (5, 400.0) if case == "hide_inferred" else (6, 500.0)
    assert row["orders_count"] == count
    assert row["known_total_sales_sar"] == sales
    assert row["currency_conversion_complete"] is False
    assert row["fee_amount"] is None and row["vat_amount"] is None
    assert (row["commission_percent"], row["fixed_fee"], row["vat_percent"]) == (6.99, 1.5, 15.0)
    previous_fee, previous_vat = _aggregate_tamara_fee(sales, count)
    assert row["known_fee_amount_sar"] == previous_fee, "Frozen Tamara fee changed outside the approved correction"
    assert row["known_vat_amount_sar"] == previous_vat, "Frozen Tamara VAT changed outside the approved correction"
    assert len(row["sub_methods"]) == 1
    sub = row["sub_methods"][0]
    assert sub["key"] == "tamara" and sub["orders_count"] == count
    assert sub["known_total_sales_sar"] == sales
    assert sub["known_fee_amount_sar"] == previous_fee
    assert sub["currency_conversion_complete"] is False and sub["fee_amount"] is None
    fee, vat = _aggregate_tamara_fee(sales, count - 1)
    row["known_fee_amount_sar"] = sub["known_fee_amount_sar"] = fee
    row["known_vat_amount_sar"] = vat
    row["fees_complete"] = False


def extend_frozen_fee_contract(response, *, fixture_case):
    """Extend a parsed frozen response in place, retaining every other field.

    The blank-method order is fixture index 14. Its historical substring
    matching intentionally survives all method/status filters. The Arabic
    status fixture's fee reader retains only this blank-status order, although
    the independent V2 reader counts seven normalized-status orders; never
    infer the fee cohort from V2 totals or change that frozen read schedule.
    """
    if ("totals" not in response or "product_cost_v2" not in response
            or "payment_breakdown" not in response
            or "payment_gateway_fees" not in response.get("source_contract", {})):
        # Older focused product/Google oracle tests intentionally construct
        # partial historical shapes. Every full frozen response has both fee
        # fields, and runtime equality still rejects their candidate removal.
        return response
    blank_method, unknown_fx, case = _fixture_contract(fixture_case)
    assert "payment_fee_authority" not in response, "Fee contract already extended"
    assert response["source_contract"]["payment_gateway_fees"] == OLD_FEE_SOURCE
    totals = response["totals"]
    assert totals.get("legacy_analyses_count", 0) == 0, "Unreviewed legacy-analysis fixture"
    if unknown_fx:
        _exclude_unknown_fx_fixed_fee(response, case)
    elif case != "arabic_status_normalization":
        # The Arabic fixture differs between the two frozen readers; its
        # unverified-currency count is not evidence of a fee-reader order.
        assert totals["unverified_currency_orders_count"] == 0
    known = Decimal("0")
    for row in response["payment_breakdown"]:
        if row["key"] == "ad_bank_commissions":
            continue  # Advertising bank fees are already added downstream.
        fee = row.get("known_fee_amount_sar", row["fee_amount"])
        assert fee is not None, "Unreviewed partial fee bucket in frozen fixture"
        known += Decimal(str(fee))
    reasons = (["payment_method_unconfigured"] if blank_method else [])
    if unknown_fx:
        reasons.append("payment_sales_currency_unverified")
    unknown = ([""] if blank_method else []) + (["tamara"] if unknown_fx else [])
    detail = totals["electronic_net_breakdown"]
    electronic_scope_exists = detail["included_count"] + detail["excluded_count"] > 0
    electronic_complete = not blank_method or electronic_scope_exists
    authority = {
        "basis": FEE_BASIS,
        "complete": not reasons,
        "known_payment_fees_sar": float(known.quantize(Decimal(".01"))),
        "reason_codes": reasons,
        "incomplete_buckets": unknown,
        "electronic_complete": electronic_complete,
        "electronic_before_filter_complete": not blank_method,
        "legacy_analysis_fees_included": False,
    }
    # JSON order is part of the existing byte-for-byte response gate.
    extended = {}
    for key, value in response.items():
        if key == "totals":
            extended["payment_fee_authority"] = authority
        extended[key] = value
    response.clear()
    response.update(extended)
    response["source_contract"]["payment_gateway_fees"] = FEE_SOURCE
    # The authority is applied to the legacy dashboard before ads/product
    # enrichments append their totals. Preserve that public JSON insertion
    # order as well as its values in the frozen byte-for-byte gate.
    extended_totals = {}
    inserted = False
    for key, value in totals.items():
        extended_totals[key] = value
        if key == "cod_unapproved":
            extended_totals["payment_fees_complete"] = not reasons
            extended_totals["payment_fee_review_reasons"] = reasons
            inserted = True
    assert inserted, "Frozen fee response missing its legacy totals boundary"
    totals.clear()
    totals.update(extended_totals)
    if reasons:
        # Approved unknown propagation only; no numeric totals are recomputed.
        for field in ("total_payment_fees", "net_profit", "expected_salla_transfer", "total_vat"):
            totals[field] = None
        config = response.get("net_sales_config") or {}
        if config.get("deduct_payment_fees", True) or config.get("deduct_vat", False):
            totals["net_sales"] = None
    if unknown_fx:
        for field in ("tamara_fees", "bnpl_fees", "bnpl_net"):
            totals[field] = None
    if blank_method:
        detail["fees_before_filter"] = None
    if not electronic_complete:
        for field in ("other_payment_fees", "electronic_net", "electronic_net_before_settlements"):
            totals[field] = None
        detail["fees_after_filter"] = None
    return response
