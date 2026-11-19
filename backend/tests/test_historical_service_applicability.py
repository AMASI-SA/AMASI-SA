"""Positive frozen service scope can use current prices without inventing choices."""
from copy import deepcopy

import pytest

from order_cost_authority import (
    calculate_catalog_line_cost, line_fingerprint, resolve_order_line_cost,
)


def fixture(*, quantity=2, old_multiplier=1, old_product_service=False, extra_answers=None, custom_fields=None):
    item = {
        "order_item_id": "synthetic-line", "product_id": "synthetic-product",
        "quantity": quantity,
        "options_raw": [{"option_id": "old-name", "value": "Synthetic name"}, *(extra_answers or [])],
        "custom_fields": custom_fields or [],
    }
    product = {"options": [
        {"id": "old-name", "name": "Name", "type": "text", "values": []},
        {"id": "new-question", "name": "Embroidery?", "type": "radio",
         "values": [{"id": "yes", "name": "Yes"}, {"id": "no", "name": "No"}]},
    ]}
    product_bindings = [{"id": "bag", "resource_id": "bag", "quantity": 1}]
    old_bindings = [{
        "id": "old-name-cost", "option_id": "old-name", "value_id": "filled",
        "mode": "resource", "resource_id": "embroidery", "quantity": old_multiplier,
    }]
    resources = {
        "bag": {"kind": "component", "unit_cost": 2},
        "embroidery": {"kind": "service", "unit_cost": 6},
    }
    old = calculate_catalog_line_cost(
        item, product=product, profile={"base_cost": 20},
        product_bindings=product_bindings + ([{
            "id": "other-service", "resource_id": "embroidery", "quantity": 1,
        }] if old_product_service else []),
        option_bindings=old_bindings, resources=resources,
    )
    old.update(cost_authority="order_snapshot", historical_cost_verified=True)
    record = {
        "line_fingerprint": line_fingerprint(item), "quantity": quantity,
        "product_id": item["product_id"], "cost": old,
    }
    historical = resolve_order_line_cost(
        item, order_number="synthetic-order", catalog_cost={},
        authority={"lines": {("synthetic-order", "synthetic-line"): record}},
        include_service_scope_proof=True,
    )
    current = {
        "product": product, "profile": {"base_cost": 20},
        "product_bindings": product_bindings,
        "option_bindings": [{
            "id": "new-yes-cost", "option_id": "new-question", "value_id": "yes",
            "mode": "resource", "resource_id": "embroidery", "quantity": old_multiplier,
        }],
        "resources": {**resources, "embroidery": {"kind": "service", "unit_cost": 8}},
    }
    return item, historical, current, record


def estimate(item, historical, current, **kwargs):
    return calculate_catalog_line_cost(
        item, **current, current_report=kwargs.get("current_report", True),
        verified_historical_cost=historical,
    )


def assert_unresolved(result):
    assert result["calculation_cost_available"] is False
    assert "option_selection_unresolved:new-yes-cost" in result["missing_cost_reasons"]
    assert not result.get("historical_service_applicability")


def test_verified_old_service_uses_current_price_without_new_customer_answer():
    item, historical, current, _ = fixture()
    before = deepcopy((item, historical, current))
    assert historical["line_total"] == 56  # 2 x (base20 + bag2 + old service6).

    result = estimate(item, historical, current)

    assert result["line_total"] == 60  # 2 x (base20 + bag2 + current service8).
    assert result["selected_options_total"] == 16
    assert result["calculation_cost_available"] is True
    assert result["missing_cost_reasons"] == []
    assert result["cost_authority"] == "catalog_estimate"
    assert result["cost_stage"] == "expected"
    assert result["historical_cost_verified"] is False
    assert result["resolved_option_selections"] == [{"option_id": "old-name", "value_id": "filled"}]
    assert result["historical_service_applicability"] == [{
        "binding_id": "new-yes-cost", "historical_binding_id": "old-name-cost",
        "resource_id": "embroidery", "quantity": "1", "source": "verified_order_snapshot",
    }]
    assert (item, historical, current) == before


def test_matching_service_multiplier_and_line_quantity_are_applied_once():
    item, historical, current, _ = fixture(quantity=3, old_multiplier=2)
    assert estimate(item, historical, current)["line_total"] == 114  # 3 x (20 + 2 + 2 x 8).


def test_positive_scope_survives_removal_of_the_old_catalog_field():
    item, historical, current, _ = fixture()
    current["product"]["options"] = current["product"]["options"][1:]
    result = estimate(item, historical, current)
    assert result["line_total"] == 60
    assert result["calculation_cost_available"] is True
    assert result["resolved_option_selections"] == []


@pytest.mark.parametrize("patch", [
    {"historical_cost_verified": False},
    {"calculation_cost_available": False},
    {"base_complete": False},
    {"cost_authority": "catalog_estimate"},
    {"cost_authority": "bundle_snapshot"},
    {"cost_authority": "approved_correction"},
    {"cost_authority": "unresolved_snapshot"},
    {"cost_stage": "actual"},
    {"repair_version": 1},
    {"missing_cost_reasons": ["cost_snapshot_line_changed"]},
    {"cost_semantics_version": "unknown"},
    {"quantity": 1},
    {"quantity": "NaN"},
    {"line_total": 55},
    {"components_total": 0},
    {"cost_detail_cells": []},
    {"applied_option_costs": []},
    {"cost_currency": "USD"},
    {"_verified_service_scope_line_fingerprint": None},
    {"_verified_service_scope_line_fingerprint": "not-the-source-fingerprint"},
])
def test_missing_or_inconsistent_frozen_proof_does_not_recover(patch):
    item, historical, current, _ = fixture()
    historical.update(patch)
    assert_unresolved(estimate(item, historical, current))


def test_no_proof_or_non_report_calculation_does_not_recover():
    item, historical, current, _ = fixture()
    assert_unresolved(estimate(item, None, current))
    assert_unresolved(estimate(item, historical, current, current_report=False))


def test_legacy_verified_amount_without_original_line_fingerprint_cannot_recover():
    item, _, current, record = fixture()
    record.pop("line_fingerprint")
    historical = resolve_order_line_cost(
        item, order_number="synthetic-order", catalog_cost={},
        authority={"lines": {("synthetic-order", "synthetic-line"): record}},
        include_service_scope_proof=True,
    )
    assert historical["historical_cost_verified"] is True
    assert "_verified_service_scope_line_fingerprint" not in historical
    assert_unresolved(estimate(item, historical, current))


def test_source_attestation_is_opt_in_and_never_loaded_from_saved_cost():
    item, _, current, record = fixture()
    record["cost"]["_verified_service_scope_line_fingerprint"] = line_fingerprint(item)
    historical = resolve_order_line_cost(
        item, order_number="synthetic-order", catalog_cost={},
        authority={"lines": {("synthetic-order", "synthetic-line"): record}},
    )
    assert "_verified_service_scope_line_fingerprint" not in historical
    assert_unresolved(estimate(item, historical, current))


@pytest.mark.parametrize("field,value", [
    ("quantity", 0), ("quantity", -1), ("quantity", True), ("quantity", "NaN"),
    ("quantity", 2), ("unit_cost_sar", "NaN"), ("resolved_amount", 0),
])
def test_invalid_historical_service_quantity_or_money_does_not_recover(field, value):
    item, historical, current, _ = fixture()
    historical["applied_option_costs"][0][field] = value
    assert_unresolved(estimate(item, historical, current))


@pytest.mark.parametrize("location", ["old_cell", "old_applied", "new_option", "new_product"])
def test_ambiguous_service_mapping_does_not_recover(location):
    item, historical, current, _ = fixture()
    if location == "old_cell":
        historical["cost_detail_cells"].append(deepcopy(historical["cost_detail_cells"][-1]))
    elif location == "old_applied":
        historical["applied_option_costs"].append(deepcopy(historical["applied_option_costs"][0]))
    elif location == "new_option":
        current["option_bindings"].append({**current["option_bindings"][0], "id": "independent-option"})
    else:
        current["product_bindings"].append({"id": "independent-product", "resource_id": "embroidery", "quantity": 1})
    assert_unresolved(estimate(item, historical, current))


def test_consistent_old_product_and_option_service_rows_are_still_ambiguous():
    item, historical, current, _ = fixture(old_product_service=True)
    assert historical["calculation_cost_available"] is True
    assert_unresolved(estimate(item, historical, current))


def test_duplicate_current_binding_id_with_another_resource_is_not_recovered():
    item, historical, current, _ = fixture()
    current["option_bindings"].append({
        **current["option_bindings"][0], "resource_id": "other-service",
    })
    current["resources"]["other-service"] = {"kind": "service", "unit_cost": 5}
    assert_unresolved(estimate(item, historical, current))


def test_changed_current_multiplier_is_not_a_verified_service_mapping():
    item, historical, current, _ = fixture()
    current["option_bindings"][0]["quantity"] = 2
    assert_unresolved(estimate(item, historical, current))


def test_explicit_no_is_never_overridden_by_old_service_scope():
    item, historical, current, _ = fixture()
    item["options_raw"].append({"option_id": "new-question", "value_id": "no"})
    result = estimate(item, historical, current)
    assert result["line_total"] == 44
    assert result["calculation_cost_available"] is True
    assert result["cost_detail_cells"][-1]["status"] == "not_required"
    assert not result.get("historical_service_applicability")


@pytest.mark.parametrize("answer", [
    {"option_id": "new-question", "value_id": "unrecognized"},
    {"option_id": "new-question", "product_option_id": "conflicting", "value_id": "yes"},
    {"name": "Embroidery?", "value": "unrecognized"},
])
def test_present_unresolved_answer_is_not_replaced_with_historical_applicability(answer):
    item, historical, current, _ = fixture(extra_answers=[answer])
    assert_unresolved(estimate(item, historical, current))


def test_malformed_normalized_options_stay_unresolved_without_recovery():
    item, historical, current, _ = fixture()
    item["options_normalized"] = ["invalid legacy shape"]
    assert_unresolved(estimate(item, historical, current))


@pytest.mark.parametrize("field", [
    {"field_id": "new-field", "field_name": "New field", "value": "unrecognized"},
    {"field": {"id": "new-field", "name": "New field"}, "value": "unrecognized"},
    {"id": "new-field", "name": "New field", "value": "unrecognized"},
])
def test_present_custom_field_is_not_inferred_from_historical_scope(field):
    item, historical, current, _ = fixture(custom_fields=[field])
    current["option_bindings"][0]["option_id"] = "field:new-field"
    current["product"]["options"][1]["id"] = "field:new-field"
    assert_unresolved(estimate(item, historical, current))


@pytest.mark.parametrize("price", [None, "NaN", -1])
def test_positive_scope_never_substitutes_old_price_for_missing_current_price(price):
    item, historical, current, _ = fixture()
    current["resources"]["embroidery"]["unit_cost"] = price
    result = estimate(item, historical, current)
    assert result["line_total"] == 44
    assert result["calculation_cost_available"] is False
    assert "component_cost_or_fx_missing:new-yes-cost" in result["missing_cost_reasons"]


def test_explicit_current_zero_is_valid_and_base_and_material_use_current_prices():
    item, historical, current, _ = fixture()
    current["resources"]["embroidery"]["unit_cost"] = 0
    current["resources"]["bag"]["unit_cost"] = 3
    current["profile"]["base_cost"] = 30
    result = estimate(item, historical, current)
    assert result["line_total"] == 66
    assert result["calculation_cost_available"] is True


def test_historical_service_scope_never_recovers_an_internal_material():
    item, historical, current, _ = fixture()
    current["resources"]["embroidery"]["kind"] = "component"
    assert_unresolved(estimate(item, historical, current))


def test_source_mismatch_from_existing_authority_guard_cannot_recover():
    item, _, current, record = fixture()
    item["options_raw"][0]["value"] = "Different synthetic name"
    historical = resolve_order_line_cost(
        item, order_number="synthetic-order", catalog_cost={},
        authority={"lines": {("synthetic-order", "synthetic-line"): record}},
    )
    assert historical["missing_cost_reasons"] == ["cost_snapshot_line_changed"]
    assert_unresolved(estimate(item, historical, current))
