"""Report costs follow provider identities, not checkout language or row IDs."""
from copy import deepcopy

import pytest

from order_cost_authority import calculate_catalog_line_cost

PRODUCT = {"options": [{"id": "emb", "name": "تطريز الاسم", "required": True,
    "translations": {"en": {"option_name": "Name embroidery"}},
    "values": [
        {"id": "yes", "name": "نعم", "translations": {"en": {"option_details_name": "Yes"}}},
        {"id": "no", "name": "لا", "translations": {"en": {"option_details_name": "No"}}},
    ]}]}
BINDING = {"id": "emb-cost", "option_id": "emb", "value_id": "yes",
           "option_name": "تطريز الاسم", "value_name": "نعم", "mode": "resource", "resource_id": "emb"}


def cost(options, product=None, quantity=1):
    return calculate_catalog_line_cost({"order_item_id": "line", "quantity": quantity, "options": options},
        product=PRODUCT if product is None else product, profile={"base_cost": 15},
        product_bindings=[{"id": "bag-cost", "resource_id": "bag"}], option_bindings=[BINDING],
        resources={"bag": {"unit_cost": 1.2}, "emb": {"unit_cost": 8}})


@pytest.mark.parametrize("options", [
    [{"id": "checkout-row-42", "name": "تطريز الاسم", "values": [{"id": "checkout-value-99", "name": "نعم"}]}],
    [{"name": "Name embroidery", "values": [{"name": "Yes"}]}],
    [{"option_id": "emb", "name": "Renamed question", "values": [{"value_id": "yes", "name": "Renamed answer"}]}],
])
def test_cost_survives_order_row_ids_translation_and_rename(options):
    assert cost(options)["line_total"] == 24.2


def test_unknown_paid_choice_never_becomes_complete_zero():
    result = cost([{"option_id": "emb", "values": [{"id": "deleted-choice", "name": "Unrecognized"}]}])
    assert result["mezan_cost_complete"] is False
    assert result["missing_cost_reasons"]


def test_provider_ids_win_even_when_labels_disagree():
    assert cost([{"option_id": "emb", "value_id": "no", "name": "تطريز الاسم", "value": "نعم"}])["line_total"] == 16.2


def test_28_units_mixed_checkout_languages_and_repeated_lines():
    selections = ([{"name": "تطريز الاسم", "values": [{"name": "نعم"}]}],
                  [{"name": "Name embroidery", "values": [{"name": "Yes"}]}],
                  [{"id": "checkout-row", "name": "تطريز الاسم", "values": [{"name": "نعم"}]}])
    # Fifteen Arabic single units, four translated units, nine repeated-product units.
    assert round(sum(cost(options, quantity=q)["line_total"] for options, q in zip(selections, [15, 4, 9])), 2) == 677.6


def test_legacy_identity_survives_catalog_rename_and_translation_removal():
    from report_option_identity import retain_option_identity_labels
    renamed = deepcopy(PRODUCT)
    renamed["options"][0].update(name="Updated question", translations={})
    renamed["options"][0]["values"][0].update(name="Updated yes", translations={})
    retain_option_identity_labels(PRODUCT, renamed)
    result = cost([{"name": "Name embroidery", "values": [{"name": "Yes"}]}], renamed)
    assert result["line_total"] == 24.2
    assert result["resolved_option_selections"] == [{"option_id": "emb", "value_id": "yes"}]


def test_unrelated_yes_is_never_selected_by_value_label_alone():
    result = cost([{"name": "Gift card", "values": [{"name": "Yes"}]}])
    assert result["line_total"] == 16.2
    assert result["mezan_cost_complete"] is False


@pytest.mark.asyncio
async def test_missing_resource_cost_is_not_serialized_as_zero():
    from mongomock_motor import AsyncMongoMockClient
    from product_option_cost_routes import _binding_view, RESOURCES
    db = AsyncMongoMockClient().missing_resource_cost
    await db[RESOURCES].insert_one({"user_id": "owner", "id": "emb", "unit_cost": None})
    view = await _binding_view(db, {**BINDING, "user_id": "owner"})
    assert view["resolved_amount"] is None and view["cost_missing"] is True
    await db[RESOURCES].update_one({"id": "emb"}, {"$set": {"unit_cost": 0}})
    view = await _binding_view(db, {**BINDING, "user_id": "owner"})
    assert view["resolved_amount"] == 0 and view["cost_missing"] is False
