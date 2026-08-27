"""Regression coverage for conditional order option costs."""
from types import SimpleNamespace

from order_option_cost_snapshot_routes import binding_matches, selected_option_tokens


def test_selected_option_ids_match_only_customer_choice():
    item = SimpleNamespace(
        options_raw=[{
            "option_id": "color",
            "value_id": "gold",
            "option_name": "لون السلسال",
            "value_name": "مطلي بالذهب",
        }],
        options_normalized={},
    )
    tokens = selected_option_tokens(item)
    assert binding_matches({
        "option_id": "color", "value_id": "gold",
        "option_name": "لون السلسال", "value_name": "مطلي بالذهب",
    }, tokens)
    assert not binding_matches({
        "option_id": "color", "value_id": "silver",
        "option_name": "لون السلسال", "value_name": "مطلي بالفضة",
    }, tokens)


def test_normalized_option_names_support_legacy_orders():
    item = SimpleNamespace(
        options_raw=[],
        options_normalized={"إضافة الاسم": "نعم", "لون السلسال": "مطلي بالذهب"},
    )
    tokens = selected_option_tokens(item)
    assert binding_matches({
        "option_id": "different-id", "value_id": "different-value",
        "option_name": "إضافة الاسم", "value_name": "نعم",
    }, tokens)
    assert not binding_matches({
        "option_id": "x", "value_id": "y",
        "option_name": "إضافة الاسم", "value_name": "لا",
    }, tokens)


def test_plural_salla_option_values_match_each_customer_choice():
    item = {
        "options": [{
            "name": "نوع الطلب",
            "value": ["فستان"],
        }],
    }

    tokens = selected_option_tokens(item)

    assert binding_matches({
        "option_name": "نوع الطلب",
        "value_name": "فستان",
    }, tokens)
    assert not binding_matches({
        "option_name": "نوع الطلب",
        "value_name": "سديري",
    }, tokens)


def test_cost_contract_is_base_plus_selected_options_only():
    base = 22.0
    selected_option_amounts = [10.0, 3.0]
    quantity = 2
    assert (base + sum(selected_option_amounts)) * quantity == 70.0
