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
