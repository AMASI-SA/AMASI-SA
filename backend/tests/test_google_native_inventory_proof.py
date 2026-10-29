from copy import deepcopy
import pytest
from integrations_control_center.google_native_inventory_proof import product_observations


def raw():
    return {"id": 1, "status": "sale", "is_available": True, "quantity": 0,
            "options": [], "skus": [], "urls": {"customer": "https://store.test/item"}}


def test_explicit_simple_requires_empty_complete_native_variants():
    value = raw()
    result = product_observations(value, deepcopy(value), [], True)
    assert result["simple_product_observed"] is True
    assert result["parent_stock"]["state"] == "zero_unlimited_unknown"
    assert result["globally_ready"] is False and result["preparation_time"] is None
    value.pop("options")
    assert product_observations(value, deepcopy(value), [], True)["simple_product_observed"] is False


def test_variant_positive_is_separate_from_parent_zero_and_not_global_ready():
    value = raw()
    result = product_observations(value, deepcopy(value), [{"id": 7, "stock_quantity": 2}], True)
    assert result["variants"][0]["stock"]["state"] == "positive"
    assert result["parent_stock"]["quantity"] == "0"
    assert result["globally_ready"] is False


@pytest.mark.parametrize("change", [{"status": "hidden"}, {"quantity": 3}, {"is_available": False}])
def test_relevant_native_product_drift_is_rejected(change):
    value = raw()
    with pytest.raises(ValueError, match="product_changed"):
        product_observations(value, value | change, [], True)


@pytest.mark.parametrize("variant", [{"id": 7, "stock_quantity": True}, {"id": 7, "quantity": 1, "stock_quantity": 2}, {"id": "07", "quantity": 2}])
def test_ambiguous_raw_variant_facts_never_normalize_into_evidence(variant):
    value = raw()
    with pytest.raises(ValueError):
        product_observations(value, deepcopy(value), [variant], True)


def test_option_label_requires_exact_ownership_and_exposes_source_stock_flag_only():
    value = raw()
    value["options"] = [{"id": 8, "name": "Size", "values": [{"id": 9, "option_id": 8, "name": "Large", "is_out_of_stock": True}]}]
    result = product_observations(value, deepcopy(value), [{"id": 7, "stock_quantity": 2, "related_option_values": [9]}], True)
    assert result["variants"][0]["source_option_labels"][0]["value_label"] == "Large"
    assert "native_option_value_out_of_stock" in result["blocking_reasons"]
    assert result["size_coverage_verified"] is False


@pytest.mark.parametrize("status", ["hidden", "out", None])
def test_hidden_out_or_missing_status_never_becomes_ready_with_positive_variant(status):
    value = raw() | {"status": status}
    result = product_observations(value, deepcopy(value), [{"id": 7, "stock_quantity": 2}], True)
    assert "native_visibility_or_availability_blocked_or_unknown" in result["blocking_reasons"]
    assert result["eligible_for_ad_selection"] is False
