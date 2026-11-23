"""Current reporting costs for a choice of one hoodie or a two-hoodie set."""
from copy import deepcopy
from decimal import Decimal

import pytest

from current_bundle_reporting_estimate import (
    _Unresolved, _build_line_cost, load_current_bundle_estimates,
)


def hoodie_cost(choice="pair", quantity=1, sourcing="supplier", *, slots=False):
    choices = [{"id": value, "name": value} for value in ("batman", "catwoman", "pair")]
    options = [{"id": "type", "name": "Hoodie choice", "type": "radio",
                "required": True, "values": choices}]
    selected = [] if choice is None else [{"option_id": "type", "values": [{"value_id": choice}]}]
    rows = [{"kind": "current_product", "rule_id": value, "repeat_policy": "sum",
             "quantity": count, "sourcing_mode": sourcing, "supplier_id": "supplier",
             "condition": {"option_id": "type", "value_id": value}}
            for value, count in (("batman", 1), ("catwoman", 1), ("pair", 2))]
    if slots:
        for name in ("batman", "catwoman"):
            for field, value in (("size", "M"), ("color", "black")):
                oid = name + "-" + field
                options.append({"id": oid, "type": "radio", "values": [{"id": value, "name": value}]})
                selected.append({"option_id": oid, "values": [{"value_id": value}]})
        for row in rows:
            names = ("batman", "catwoman") if row["rule_id"] == "pair" else (row["rule_id"],)
            row["slots"] = [{"slot_id": name, "selection_option_ids": [name + "-size", name + "-color"]}
                            for name in names]
    return {
        "item": {"quantity": quantity, "options_raw": selected}, "root": "hoodie",
        "definitions": {"hoodie": {"schema_version": 2, "items": rows}},
        # Deliberately different Salla price: the explicit Mezan unit cost wins.
        "products": {"hoodie": {"name": "Hoodie", "cost_price": 80, "options": options}},
        "profiles": {"hoodie": {"base_cost": 50}},
        "options": {}, "bindings": {}, "resources": {},
    }


def calculate(args):
    before = deepcopy(args)
    result = _build_line_cost(**args)
    assert args == before, "Reporting must never mutate the catalog or order."
    return result


@pytest.mark.parametrize("choice,quantity,total", [
    ("batman", 1, 50), ("catwoman", 1, 50), ("pair", 1, 100), ("pair", 2, 200),
])
@pytest.mark.parametrize("sourcing", ["supplier", "auto"])
def test_selected_physical_units_replace_the_single_parent_base(choice, quantity, total, sourcing):
    result = calculate(hoodie_cost(choice, quantity, sourcing))
    assert result["quantity"] == quantity  # Commercial quantity is unchanged.
    assert result["base_total"] == result["line_total"] == total
    assert result["components_total"] == 0
    assert result["calculation_cost_available"] is True
    assert result["mezan_cost_complete"] is True
    assert result["uses_salla_fallback"] is False
    assert result["cost_authority"] == "current_bundle_catalog_estimate"
    assert result["historical_cost_verified"] is False
    assert result["cost_stage"] == "expected"
    cells = result["cost_detail_cells"]
    assert next(c for c in cells if c["key"] == "root:base")["unit_cost"] == total / quantity
    assert sum(Decimal(str(c["unit_cost"])) for c in cells if c["unit_cost"] is not None) == total / quantity


def test_display_only_slots_preserve_the_pair_cost_and_accept_distinct_choices():
    args = hoodie_cost(slots=True)
    args["products"]["hoodie"]["options"][-2]["values"].append({"id": "L", "name": "L"})
    args["item"]["options_raw"][-2]["values"] = [{"value_id": "L"}]
    result = calculate(args)
    assert result["line_total"] == 100
    assert result["calculation_cost_available"] is True


def test_unselected_slot_choices_do_not_make_the_single_hoodie_incomplete():
    args = hoodie_cost("batman", slots=True)
    args["item"]["options_raw"] = args["item"]["options_raw"][:3]
    result = calculate(args)
    assert result["line_total"] == 50
    assert result["calculation_cost_available"] is True


@pytest.mark.parametrize("condition_source", ["normalized", "provider", "show_if"])
@pytest.mark.parametrize("parent,child,complete", [
    ("no", False, True), ("yes", True, True), ("yes", False, False),
    (None, False, False), ("no", True, False),
])
def test_display_slot_skips_only_a_proved_hidden_choice(condition_source, parent, child, complete):
    args = hoodie_cost(slots=True)
    product = args["products"]["hoodie"]
    product["options"].append({"id": "customize", "type": "radio", "values": [
        {"id": "yes", "name": "Yes"}, {"id": "no", "name": "No"}]})
    field = next(o for o in product["options"] if o["id"] == "batman-size")
    field["required"] = True
    condition = {"visibility": "on_condition", "visibility_condition_option": "customize",
                 "visibility_condition_value": "yes", "visibility_condition_type": "="}
    if condition_source == "provider":
        product["raw_salla_details"] = {"options": [{**field, **condition}]}
    elif condition_source == "show_if":
        field["show_if"] = [{"option_id": "customize", "value_id": "yes"}]
    else:
        field.update(condition)
    if parent is not None:
        args["item"]["options_raw"].append({"option_id": "customize", "values": [{"value_id": parent}]})
    if not child:
        args["item"]["options_raw"] = [o for o in args["item"]["options_raw"] if o["option_id"] != "batman-size"]
    if complete:
        result = calculate(args)
        assert result["line_total"] == 100
        assert result["calculation_cost_available"] is True
    else:
        with pytest.raises(_Unresolved):
            calculate(args)


@pytest.mark.parametrize("kind", ["explicit", "inherited", "option"])
@pytest.mark.parametrize("scope,total", [("per_bundle", 103), ("per_unit", 106)])
def test_resource_scope_counts_packaging_once_or_once_per_physical_piece(kind, scope, total):
    args = hoodie_cost()
    args["resources"] = {"pack": {"name": "Packaging", "unit_cost": 3}}
    if kind == "explicit":
        # Put this before the current-product rules to prove order independence.
        args["definitions"]["hoodie"]["items"].insert(0, {
            "kind": "resource", "resource_id": "pack", "component_scope": scope})
    else:
        args["definitions"]["hoodie"]["component_scopes"] = {"pack": scope}
        binding = {"id": "pack-binding", "resource_id": "pack", "quantity": 1}
        if kind == "option":
            args["options"] = {"hoodie": [{**binding, "option_id": "type", "value_id": "pair"}]}
        else:
            args["bindings"] = {"hoodie": [binding]}
    result = calculate(args)
    assert result["line_total"] == total
    assert result["base_total"] == 100
    assert result["calculation_cost_available"] is True
    assert sum(c["unit_cost"] for c in result["cost_detail_cells"] if c["unit_cost"] is not None) == total


def test_direct_selected_service_is_per_commercial_set_and_not_per_hoodie():
    args = hoodie_cost(quantity=2)
    args["options"] = {"hoodie": [{"id": "gift", "option_id": "type", "value_id": "pair",
                                    "mode": "direct", "direct_amount": 7, "quantity": 1}]}
    result = calculate(args)
    assert result["base_total"] == 200
    assert result["selected_options_total"] == 14
    assert result["line_total"] == 214


def test_cost_included_in_base_is_not_added_again_for_each_piece():
    args = hoodie_cost()
    args["definitions"]["hoodie"]["component_scopes"] = {"pack": "per_unit"}
    args["bindings"] = {"hoodie": [{"id": "pack-binding", "resource_id": "pack"}]}
    args["resources"] = {"pack": {"unit_cost": 3}}
    args["profiles"]["hoodie"]["composition_components"] = {
        "product_resource:pack-binding": {"cost_treatment": "included_in_base"}}
    result = calculate(args)
    assert result["line_total"] == 100
    assert result["cost_detail_cells"][-1]["status"] == "included_in_base"


def test_missing_hoodie_choice_remains_incomplete():
    result = calculate(hoodie_cost(None))
    assert result["calculation_cost_available"] is False
    assert result["mezan_cost_complete"] is False
    assert result["missing_cost_reasons"]


@pytest.mark.parametrize("choice,status,complete", [(None, "unresolved", False), ("batman", "not_required", True)])
def test_per_unit_resource_distinguishes_unknown_and_unselected_physical_rule(choice, status, complete):
    args = hoodie_cost(choice)
    args["definitions"]["hoodie"]["items"] = args["definitions"]["hoodie"]["items"][-1:]
    args["definitions"]["hoodie"]["component_scopes"] = {"pack": "per_unit"}
    args["bindings"] = {"hoodie": [{"id": "pack-binding", "resource_id": "pack"}]}
    args["resources"] = {"pack": {"unit_cost": 3}}
    result = calculate(args)
    assert result["line_total"] == 50  # Existing financial base, no guessed physical extra.
    assert result["calculation_cost_available"] is complete
    assert result["cost_detail_cells"][-1]["status"] == status


@pytest.mark.parametrize("schema_version", [1, 2])
def test_inherited_resource_uses_existing_schema_scope_default(schema_version):
    args = hoodie_cost()
    args["definitions"]["hoodie"]["schema_version"] = schema_version
    args["bindings"] = {"hoodie": [{"id": "pack-binding", "resource_id": "pack"}]}
    args["resources"] = {"pack": {"unit_cost": 3}}
    if schema_version == 1:
        with pytest.raises(_Unresolved, match="component_scope_unresolved"):
            calculate(args)
    else:
        assert calculate(args)["line_total"] == 103  # V2's default is per bundle.


def test_nested_physical_count_and_commercial_quantity_each_apply_once():
    args = hoodie_cost(quantity=3)
    args["root"] = "gift"
    args["products"]["gift"] = {"name": "Gift", "cost_price": 10, "options": []}
    args["definitions"]["gift"] = {"items": [{
        "kind": "product", "product_id": "hoodie", "quantity": 2,
        "selected_options": [{"option_id": "type", "value_id": "pair"}]}]}
    args["item"]["options_raw"] = []
    result = calculate(args)
    assert result["base_total"] == 30
    assert result["product_components_total"] == 600
    assert result["line_total"] == 630  # Three gifts, each with two 100-SAR pairs plus 10.
    assert sum(c["unit_cost"] for c in result["cost_detail_cells"] if c["unit_cost"] is not None) == 210


def test_unknown_hoodie_choice_does_not_become_a_guessed_pair():
    with pytest.raises(_Unresolved, match="value_unproved"):
        calculate(hoodie_cost("retired-value"))


@pytest.mark.parametrize("cost,complete,total", [(None, False, 0), (0, True, 0)])
def test_missing_and_zero_unit_cost_remain_distinct_for_two_hoodies(cost, complete, total):
    args = hoodie_cost()
    args["products"]["hoodie"]["cost_price"] = None
    args["profiles"]["hoodie"]["base_cost"] = cost
    result = calculate(args)
    assert result["line_total"] == total
    assert result["calculation_cost_available"] is complete


@pytest.mark.parametrize("change", [
    "fractional", "too_many", "variant", "catalog_variants", "variant_rule",
    "slot_count", "duplicate_slot", "unknown_choice", "text_choice", "missing_choice",
    "nonempty_override", "overlapping_current_rules",
])
def test_unsupported_or_unproved_physical_semantics_remain_unresolved(change):
    args = hoodie_cost(slots=True)
    row = args["definitions"]["hoodie"]["items"][-1]
    if change == "fractional":
        row["quantity"] = "1.5"
    elif change == "too_many":
        row["quantity"] = 101
    elif change == "variant":
        row["slots"][0]["variant_id"] = "variant"
    elif change == "catalog_variants":
        args["products"]["hoodie"]["variants"] = [{"id": "variant", "cost_price": 75}]
    elif change == "variant_rule":
        row["slots"][0]["variant_rules"] = [{"conditions": [], "variant_id": "variant"}]
    elif change == "slot_count":
        row["slots"].pop()
    elif change == "duplicate_slot":
        row["slots"][1]["slot_id"] = row["slots"][0]["slot_id"]
    elif change == "unknown_choice":
        row["slots"][0]["selection_option_ids"] = ["unknown"]
    elif change == "text_choice":
        args["products"]["hoodie"]["options"].append({"id": "message", "type": "text", "values": []})
        row["slots"][0]["selection_option_ids"] = ["message"]
    elif change == "missing_choice":
        args["item"]["options_raw"].pop()
    elif change == "nonempty_override":
        row["component_overrides"] = [{"binding_kind": "product_resource", "binding_id": "pack", "use": False}]
    elif change == "overlapping_current_rules":
        args["definitions"]["hoodie"]["items"].append({"kind": "current_product", "quantity": 1})
    with pytest.raises(_Unresolved):
        calculate(args)


@pytest.mark.asyncio
async def test_frozen_or_received_line_authority_prevents_any_catalog_estimate_read():
    class UnreadableDB:
        def __getitem__(self, key):
            raise AssertionError("Frozen/received authority must win before catalog reads.")

    result = await load_current_bundle_estimates(UnreadableDB(), "owner", [
        {"order_number": "order", "products": [{"order_item_id": "line", "product_id": "hoodie", "quantity": 1}]}
    ], {"composite_products": ["hoodie"], "lines": {("order", "line"): {"line_total": 96}}})
    assert result == {}


@pytest.mark.asyncio
async def test_tenant_scoped_loader_keeps_per_unit_scope_and_does_not_write():
    from mongomock_motor import AsyncMongoMockClient

    db = AsyncMongoMockClient().db
    args = hoodie_cost(slots=True)
    definition = {**args["definitions"]["hoodie"], "user_id": "owner", "product_id": "hoodie",
                  "product_type": "composite", "component_scopes": {"pack": "per_unit"}}
    data = {
        "mezan_bundle_definitions_v2": [definition, {**definition, "user_id": "other-owner", "items": []}],
        "mezan_products_v2": [{**args["products"]["hoodie"], "user_id": "owner", "salla_product_id": "hoodie"}],
        "mezan_product_cost_profiles_v2": [
            {"user_id": "owner", "salla_product_id": "hoodie", "base_cost": 50},
            {"user_id": "other-owner", "salla_product_id": "hoodie", "base_cost": 999}],
        "mezan_product_resource_bindings_v2": [{"user_id": "owner", "salla_product_id": "hoodie",
                                                "id": "pack-link", "resource_id": "pack"}],
        "mezan_cost_resources_v2": [{"user_id": "owner", "id": "pack", "unit_cost": 3},
                                    {"user_id": "other-owner", "id": "pack", "unit_cost": 999}],
    }
    for collection, documents in data.items():
        await db[collection].insert_many(deepcopy(documents))
    before = {key: await db[key].find({}).to_list(None) for key in data}
    result = await load_current_bundle_estimates(db, "owner", [{
        "order_number": "order", "products": [{**args["item"], "order_item_id": "line", "product_id": "hoodie"}]
    }], {"composite_products": ["hoodie"], "lines": {}})
    assert result[("order", "line")]["line_total"] == 106
    assert result[("order", "line")]["calculation_cost_available"] is True
    assert {key: await db[key].find({}).to_list(None) for key in data} == before
