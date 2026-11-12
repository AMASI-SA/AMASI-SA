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


@pytest.mark.parametrize("options", [[], [{"option_id": "emb", "value_id": "unrecognized"}]])
def test_optional_or_unknown_choice_cannot_prove_a_free_service(options):
    product = deepcopy(PRODUCT)
    product["options"][0]["required"] = False
    result = cost(options, product)
    assert result["mezan_cost_complete"] is False
    assert result["calculation_cost_available"] is False


def test_unresolved_legacy_binding_without_ids_is_incomplete():
    binding = {key: value for key, value in BINDING.items() if key not in {"option_id", "value_id"}}
    result = calculate_catalog_line_cost({"quantity": 1, "options": []}, product={}, profile={"base_cost": 15},
        product_bindings=[], option_bindings=[binding], resources={"emb": {"unit_cost": 8}})
    assert result["mezan_cost_complete"] is False


def test_unknown_legacy_answer_is_not_treated_as_declined():
    binding = {key: value for key, value in BINDING.items() if key not in {"option_id", "value_id"}}
    result = calculate_catalog_line_cost({"quantity": 1, "options": [{"name": "تطريز الاسم", "value": "unrecognized"}]},
        product=PRODUCT, profile={"base_cost": 15}, product_bindings=[], option_bindings=[binding], resources={"emb": {"unit_cost": 8}})
    assert result["calculation_cost_available"] is False


# Synthetic identities preserve the provider's conditional-embroidery shape.
FARWA_QUESTION = "100100001"
FARWA_YES = "100100002"
FARWA_NO = "100100003"
FARWA_NAME = "100100004"


def farwa_product():
    question = {
        "id": FARWA_QUESTION, "name": "هل تريد تطريز إسم ع الفروه", "type": "radio",
        "values": [{"id": FARWA_YES, "name": "نعم"}, {"id": FARWA_NO, "name": "لا"}],
    }
    field = {"id": FARWA_NAME, "name": "الاسم على الفروه", "type": "textarea",
             "required": True, "values": []}
    return {
        "name": "فروة شتوية للأطفال", "options": [question, field],
        # The normalized catalog omits Salla's visibility metadata.
        "raw_salla_details": {"options": [deepcopy(question), {
            **field, "visibility": "on_condition", "visibility_condition_type": "=",
            "visibility_condition_option": int(FARWA_QUESTION),
            "visibility_condition_value": int(FARWA_YES),
        }]},
    }


def farwa_bindings(field_value="__option__", same_service=False):
    return [
        {"id": "embroidery-yes", "option_id": FARWA_QUESTION, "value_id": FARWA_YES,
         "mode": "resource", "resource_id": "embroidery", "quantity": 1},
        {"id": "legacy-name", "option_id": FARWA_NAME, "value_id": field_value,
         "mode": "resource", "resource_id": "embroidery" if same_service else "name-work", "quantity": 1},
    ]


def farwa_cost(options, product=None, field_value="__option__", bindings=None):
    return calculate_catalog_line_cost(
        {"quantity": 2, "options_raw": options},
        product=farwa_product() if product is None else product,
        profile={"base_cost": 40},
        product_bindings=[{"id": "bag-binding", "resource_id": "bag"}],
        option_bindings=farwa_bindings(field_value) if bindings is None else bindings,
        resources={"bag": {"unit_cost": 5.5, "kind": "component"},
                   "embroidery": {"unit_cost": 10, "kind": "service"},
                   "name-work": {"unit_cost": 10, "kind": "service"}},
    )


def farwa_answer(value=FARWA_NO):
    # Salla's checkout row ID differs from its catalog option ID. The actual
    # chosen value is nested under `value`, not a top-level value_id.
    label = "لا" if value == FARWA_NO else "نعم" if value == FARWA_YES else "Unrecognized"
    return {"product_option_id": int(FARWA_QUESTION), "id": 900000001,
            "name": "هل تريد تطريز إسم ع الفروه", "type": "radio",
            "value": {"id": value, "name": label, "option_value": label, "price": 0}}


@pytest.mark.parametrize("field_value", ["__option__", "filled"])
def test_declined_embroidery_proves_its_absent_conditional_name_cost_not_required(field_value):
    product = farwa_product()
    options = [farwa_answer()]
    before = deepcopy((product, options))
    result = farwa_cost(options, product, field_value)
    assert result["line_total"] == 91
    assert result["calculation_cost_available"] is True
    assert result["mezan_cost_complete"] is True
    assert result["missing_cost_reasons"] == []
    assert result["selected_options_total"] == 0
    assert result["applied_option_costs"] == []
    assert [cell["status"] for cell in result["cost_detail_cells"][2:]] == ["not_required", "not_required"]
    assert (product, options) == before


@pytest.mark.parametrize("options", [
    [], [farwa_answer("unrecognized")],
    [farwa_answer(FARWA_YES), farwa_answer(FARWA_NO)],
    [{**farwa_answer(), "option_id": "conflicting-id"}],
    [{"name": "Unrelated question", "value": "لا"}],
])
def test_missing_or_conflicting_parent_choice_cannot_prove_name_cost_unnecessary(options):
    result = farwa_cost(options)
    assert result["calculation_cost_available"] is False
    assert result["cost_detail_cells"][-1]["status"] == "unresolved"


def test_selected_embroidery_still_requires_its_name_evidence():
    result = farwa_cost([farwa_answer(FARWA_YES)])
    assert result["line_total"] == 111
    assert result["calculation_cost_available"] is False
    assert result["cost_detail_cells"][-1]["status"] == "unresolved"


def test_conditional_field_with_an_independent_selected_cost_remains_a_separate_charge():
    result = farwa_cost([farwa_answer(FARWA_YES), {
        "product_option_id": FARWA_NAME, "value": "Synthetic name",
    }])
    assert result["line_total"] == 131
    assert result["calculation_cost_available"] is True


def test_a_present_answer_contradicting_its_hidden_condition_stays_unresolved():
    result = farwa_cost([farwa_answer(FARWA_NO), {
        "product_option_id": FARWA_NAME, "value": "Synthetic name",
    }])
    assert result["calculation_cost_available"] is False
    assert result["cost_detail_cells"][-1]["status"] == "unresolved"


@pytest.mark.parametrize("field_patch", [
    {"visibility": "always"},
    {"visibility": "unsupported"},
    {"visibility_condition_type": "unsupported"},
    {"visibility_condition_option": "deleted-option"},
    {"visibility_condition_value": "deleted-value"},
    {"visibility_condition_option": FARWA_NAME},
])
def test_unproved_or_invalid_condition_does_not_waive_absent_field_cost(field_patch):
    product = farwa_product()
    product["raw_salla_details"]["options"][1].update(field_patch)
    result = farwa_cost([farwa_answer()], product)
    assert result["calculation_cost_available"] is False
    assert result["cost_detail_cells"][-1]["status"] == "unresolved"


@pytest.mark.parametrize("answer,expected_status", [
    (FARWA_YES, "not_required"), (FARWA_NO, "unresolved"),
])
def test_not_equal_visibility_requires_a_known_parent_answer(answer, expected_status):
    product = farwa_product()
    product["raw_salla_details"]["options"][1]["visibility_condition_type"] = "!="
    result = farwa_cost([farwa_answer(answer)], product)
    assert result["cost_detail_cells"][-1]["status"] == expected_status


def test_conditional_parent_without_its_own_required_visibility_proof_is_unresolved():
    product = farwa_product()
    product["raw_salla_details"]["options"][0].update({
        "visibility": "on_condition", "visibility_condition_type": "=",
        "visibility_condition_option": FARWA_NAME, "visibility_condition_value": "filled",
    })
    result = farwa_cost([farwa_answer()], product)
    assert result["calculation_cost_available"] is False
    assert result["cost_detail_cells"][-1]["status"] == "unresolved"


def test_saved_binding_cannot_validate_a_condition_target_removed_from_the_catalog():
    product = farwa_product()
    product["raw_salla"] = deepcopy(product["raw_salla_details"])
    for source in (product, product["raw_salla_details"]):
        source["options"][0]["values"] = [{"id": FARWA_NO, "name": "لا"}]
    result = farwa_cost([farwa_answer()], product)
    assert result["calculation_cost_available"] is False
    assert result["cost_detail_cells"][-1]["status"] == "unresolved"


def test_saved_binding_cannot_validate_a_selected_parent_value_removed_from_the_catalog():
    product = farwa_product()
    product["raw_salla"] = deepcopy(product["raw_salla_details"])
    for source in (product, product["raw_salla_details"]):
        source["options"][0]["values"] = [{"id": FARWA_YES, "name": "نعم"}]
    bindings = farwa_bindings() + [{
        "id": "deleted-no-binding", "option_id": FARWA_QUESTION, "value_id": FARWA_NO,
        "mode": "direct", "direct_amount": 0,
    }]
    result = farwa_cost([farwa_answer()], product, bindings=bindings)
    assert result["line_total"] == 91
    assert result["calculation_cost_available"] is False
    assert result["cost_detail_cells"][3]["status"] == "unresolved"


@pytest.mark.parametrize("wrapped", [False, True])
def test_live_shape_declined_embroidery_has_91_complete_without_service_charge(wrapped):
    option = farwa_answer()
    if wrapped:
        option = {**option, "value": "لا", "raw": option}
    result = farwa_cost([option], bindings=farwa_bindings(same_service=True))
    assert result["line_total"] == 91
    assert result["calculation_cost_available"] is True
    assert result["applied_option_costs"] == []


def farwa_yes_with_name():
    return [farwa_answer(FARWA_YES), {
        "product_option_id": FARWA_NAME, "name": "الاسم على الفروه", "value": "Synthetic name",
    }]


def test_one_service_is_charged_once_for_its_yes_choice_and_legacy_conditional_name():
    bindings = farwa_bindings(same_service=True)
    before = deepcopy(bindings)
    result = farwa_cost(farwa_yes_with_name(), bindings=bindings)
    assert result["line_total"] == 111
    assert result["calculation_cost_available"] is True
    assert [row["binding_id"] for row in result["applied_option_costs"]] == ["embroidery-yes"]
    assert len(result["cost_detail_cells"]) == 3  # Product, bag, one embroidery service.
    assert bindings == before


def test_duplicate_source_rows_do_not_defeat_unique_legacy_service_alias_proof():
    bindings = farwa_bindings(same_service=True)
    result = farwa_cost(farwa_yes_with_name(), bindings=bindings + deepcopy(bindings))
    assert result["line_total"] == 111
    assert len(result["applied_option_costs"]) == 1


@pytest.mark.parametrize("binding_patch,expected", [
    ({"quantity": 2}, 151),
    ({"resource_id": "name-work"}, 131),
    ({"mode": "direct", "direct_amount": 10}, 131),
    ({"value_id": "filled"}, 131),
])
def test_only_equivalent_legacy_service_rules_are_collapsed(binding_patch, expected):
    bindings = farwa_bindings(same_service=True)
    bindings[1].update(binding_patch)
    result = farwa_cost(farwa_yes_with_name(), bindings=bindings)
    assert result["line_total"] == expected
    assert result["calculation_cost_available"] is True
    assert len(result["applied_option_costs"]) == 2


def test_unrelated_fields_reusing_a_service_remain_independent():
    product = farwa_product()
    product["raw_salla_details"]["options"][1]["visibility"] = "always"
    result = farwa_cost(farwa_yes_with_name(), product, bindings=farwa_bindings(same_service=True))
    assert result["line_total"] == 131
    assert len(result["applied_option_costs"]) == 2


def test_legacy_service_is_not_suppressed_by_a_parent_already_included_in_base():
    bindings = farwa_bindings(same_service=True)
    bindings[0]["cost_treatment"] = "included_in_base"
    result = farwa_cost(farwa_yes_with_name(), bindings=bindings)
    assert result["line_total"] == 111
    assert [row["binding_id"] for row in result["applied_option_costs"]] == ["legacy-name"]


def test_multiple_distinct_legacy_fields_do_not_guess_which_service_is_an_alias():
    product = farwa_product()
    other = {**product["raw_salla_details"]["options"][1], "id": "other-name"}
    product["raw_salla_details"]["options"].append(other)
    bindings = farwa_bindings(same_service=True)
    bindings.append({**bindings[1], "id": "other-name-binding", "option_id": "other-name"})
    options = farwa_yes_with_name() + [{"product_option_id": "other-name", "value": "Synthetic other"}]
    result = farwa_cost(options, product, bindings=bindings)
    assert result["line_total"] == 151
    assert len(result["applied_option_costs"]) == 3


@pytest.mark.parametrize("options,expected", [
    ([farwa_answer()], 91), (farwa_yes_with_name(), 111),
])
def test_sold_report_exposes_the_complete_cost_and_summary_for_conditional_embroidery(options, expected):
    from sold_products_report_v2 import aggregate_sold_products, summarize
    product_id = "100100005"
    rows = aggregate_sold_products(
        [{"order_number": "synthetic-order", "products": [{
            "order_item_id": "synthetic-line", "product_id": product_id,
            "quantity": 2, "options_raw": options,
        }]}],
        [{**farwa_product(), "salla_product_id": product_id, "sku": "FIXTURE-WINTER-COAT"}],
        [{"salla_product_id": product_id, "base_cost": 40}],
        authority={"lines": {}},
        catalog_context={
            "product_binding_map": {product_id: [{"id": "bag", "resource_id": "bag"}]},
            "option_map": {product_id: farwa_bindings(same_service=True)},
            "resources": {"bag": {"unit_cost": 5.5, "kind": "component"},
                          "embroidery": {"unit_cost": 10, "kind": "service"}},
        },
    )
    assert len(rows) == 1
    assert rows[0]["cost_status"] == "complete"
    assert rows[0]["total_cost"] == expected
    assert rows[0]["known_cost_total"] == expected
    assert summarize(rows)["total_cost"] == expected


def test_existing_frozen_cost_is_not_repriced_by_conditional_service_alias_resolution():
    from order_cost_authority import line_fingerprint, resolve_order_line_cost
    item = {"order_item_id": "synthetic-line", "product_id": "100100005",
            "quantity": 2, "options_raw": farwa_yes_with_name()}
    bindings = farwa_bindings(same_service=True)
    current = farwa_cost(item["options_raw"], bindings=bindings)
    previous_product = farwa_product()
    previous_product["raw_salla_details"]["options"][1]["visibility"] = "always"
    frozen = farwa_cost(item["options_raw"], previous_product, bindings=bindings)
    frozen["cost_authority"] = "order_snapshot"
    authority = {"lines": {("synthetic-order", "synthetic-line"): {
        "quantity": 2, "product_id": item["product_id"], "cost": frozen,
        "line_fingerprint": line_fingerprint(item),
    }}}
    before = deepcopy(authority)
    result = resolve_order_line_cost(item, order_number="synthetic-order", catalog_cost=current, authority=authority)
    assert current["line_total"] == 111
    assert result["line_total"] == 131
    assert result["cost_authority"] == "order_snapshot"
    assert authority == before
