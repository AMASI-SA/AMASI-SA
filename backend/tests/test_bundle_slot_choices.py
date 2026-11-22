"""Choice-field specifications belong to a physical bundle piece, not stock SKUs."""
from copy import deepcopy
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

import bundle_catalog_v2 as bundles
from product_v2_routes import PRODUCTS


@pytest_asyncio.fixture
async def db(monkeypatch):
    database = AsyncMongoMockClient().bundle_slot_choices

    async def local_owner(database, owner, callback, **kwargs):
        return await callback(database)

    monkeypatch.setattr(bundles, "operational_owner", local_owner)
    options = [{"id": "kind", "name": "Hoodie selection", "type": "radio", "required": True,
                "values": [{"id": value, "name": value} for value in ("first", "second", "pair")]}]
    for side, excluded in (("first", "second"), ("second", "first")):
        for field, values in (("size", ("M", "XL")), ("color", ("Black", "White"))):
            options.append({"id": side + "_" + field, "name": side + " " + field,
                            "type": "radio", "required": True,
                            "visibility": "on_condition", "visibility_condition_type": "!=",
                            "visibility_condition_option": "kind", "visibility_condition_value": excluded,
                            "values": [{"id": value.lower(), "name": value} for value in values]})
    options.append({"id": "note", "name": "Gift note", "type": "text", "values": []})
    await database[PRODUCTS].insert_one({"user_id": "owner", "salla_product_id": "hoodie",
        "mezan_product_id": "mpv2_hoodie", "name": "Synthetic hoodie set", "variants": [],
        "cost_price_from_salla": 80, "options": options})
    await database[bundles.COST_PROFILES].insert_one({"user_id": "owner", "salla_product_id": "hoodie", "base_cost": 50})
    await database["mezan_suppliers_v2"].insert_one({"user_id": "owner", "id": "supplier"})
    return database


def slot(side):
    return {"slot_id": side, "selection_option_ids": [side + "_size", side + "_color"]}


def recipe():
    return [{"kind": "current_product", "quantity": str(len(sides)),
             "condition": {"option_id": "kind", "value_id": choice},
             "sourcing_mode": "supplier", "supplier_id": "supplier",
             "slots": [slot(side) for side in sides]}
            for choice, sides in (("first", ("first",)), ("second", ("second",)),
                                  ("pair", ("first", "second")))]


def choices(kind="pair"):
    values = [("kind", kind)]
    if kind != "second":
        values += [("first_size", "xl"), ("first_color", "black")]
    if kind != "first":
        values += [("second_size", "m"), ("second_color", "white")]
    return [{"option_id": oid, "value_id": value} for oid, value in values]


async def save(db, rows):
    return await bundles.save_bundle(db, "owner", "hoodie", {
        "expected_revision": 0, "schema_version": 2, "product_type": "composite", "items": rows}, "actor")


@pytest.mark.asyncio
async def test_pair_keeps_distinct_choice_specs_and_frozen_cost(db):
    await save(db, recipe())
    selected = choices()
    expanded = await bundles.expand_bundle(db, "owner", "hoodie", selected_options=selected)
    assert expanded["cost_complete"] is True
    assert Decimal(expanded["estimated_cost"]) == 100
    assert expanded["current_product_units_per_selection"] == "2"
    assert {leaf["slot_id"]: leaf["customization"] for leaf in expanded["leaves"]} == {
        "first": {"first_size": "XL", "first_color": "Black"},
        "second": {"second_size": "M", "second_color": "White"},
    }
    assert all(Decimal(leaf["quantity"]) == 1 and Decimal(leaf["unit_cost"]) == 50 and leaf["variant_id"] is None
               for leaf in expanded["leaves"])
    # Full stable-ID context still drives the existing supplier sourcing rules.
    assert all(leaf["selected_options"] == sorted(selected, key=lambda item: (item["option_id"], item["value_id"]))
               for leaf in expanded["leaves"])
    await db[PRODUCTS].update_one({"salla_product_id": "hoodie"}, {"$set": {"options": [], "name": "Changed catalog"}})
    await db[bundles.COST_PROFILES].update_one({"salla_product_id": "hoodie"}, {"$set": {"base_cost": 99}})
    replayed = await bundles.expand_bundle(db, "owner", "hoodie", selected_options=selected,
                                          frozen_definitions=expanded["frozen_definitions"])
    assert replayed["leaves"] == expanded["leaves"]
    assert Decimal(replayed["estimated_cost"]) == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["first", "second"])
async def test_single_hoodie_does_not_require_inactive_piece_choices(db, side):
    await save(db, recipe())
    expanded = await bundles.expand_bundle(db, "owner", "hoodie", selected_options=choices(side))
    assert expanded["cost_complete"] is True
    assert Decimal(expanded["estimated_cost"]) == 50
    assert len(expanded["leaves"]) == 1
    assert expanded["leaves"][0]["slot_id"] == side
    assert set(expanded["leaves"][0]["customization"]) == {side + "_size", side + "_color"}


@pytest.mark.asyncio
async def test_missing_selected_piece_spec_never_claims_complete(db):
    await save(db, recipe())
    selected = [value for value in choices() if value["option_id"] != "second_color"]
    preview = await bundles.expand_bundle(db, "owner", "hoodie", selected_options=selected, strict=False)
    assert preview["cost_complete"] is False
    assert {"product_id": "hoodie", "slot_id": "second", "option_id": "second_color"} in preview["missing_options"]
    with pytest.raises(HTTPException) as error:
        await bundles.expand_bundle(db, "owner", "hoodie", selected_options=selected)
    assert error.value.detail["code"] == "bundle_selection_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", ["first_size", None, [1], [True], ["first_size", "first_size"],
                                 ["unknown"], ["note"], ["first_size"] * 101])
async def test_choice_mapping_rejects_invalid_or_non_choice_ids(db, ids):
    rows = recipe()
    rows[0]["slots"][0]["selection_option_ids"] = ids
    with pytest.raises(HTTPException) as error:
        await save(db, rows)
    assert error.value.detail["code"] == "bundle_invalid_selection_option_ids"
    assert await db[bundles.BUNDLES].count_documents({}) == 0


@pytest.mark.asyncio
async def test_choice_mapping_keeps_stock_variant_requirement(db):
    rows = recipe()
    for row in rows:
        row.update(sourcing_mode="stock", supplier_id=None)
    await save(db, rows)
    with pytest.raises(HTTPException) as error:
        await bundles.expand_bundle(db, "owner", "hoodie", selected_options=choices())
    assert error.value.detail["code"] == "bundle_stock_customization_requires_variant"


@pytest.mark.asyncio
async def test_choice_mapping_accepts_auto_source_and_keeps_text_specs(db):
    rows = recipe()
    for row in rows:
        row.update(sourcing_mode="auto", supplier_id=None)
    rows[2]["slots"][0]["customization_option_ids"] = ["note"]
    await save(db, rows)
    expanded = await bundles.expand_bundle(db, "owner", "hoodie", selected_options=choices(),
                                          option_answers={"note": "A gift"})
    first = next(leaf for leaf in expanded["leaves"] if leaf["slot_id"] == "first")
    assert first["sourcing_mode"] == "auto"
    assert first["customization"] == {"first_size": "XL", "first_color": "Black", "note": "A gift"}
    assert Decimal(expanded["estimated_cost"]) == 100


@pytest.mark.asyncio
async def test_multi_choice_specs_are_deterministic_and_preserve_choice_ids(db):
    await db[PRODUCTS].update_one({"salla_product_id": "hoodie"}, {"$set": {"options": [
        {"id": "features", "type": "checkbox", "values": [{"id": "b", "name": "Second"}, {"id": "a", "name": "First"}]}]}})
    await save(db, [{"kind": "current_product", "quantity": "1", "sourcing_mode": "supplier",
                     "supplier_id": "supplier", "slots": [{"slot_id": "one", "selection_option_ids": ["features"]}]}])
    selected = [{"option_id": "features", "value_id": value} for value in ("b", "a")]
    expanded = await bundles.expand_bundle(db, "owner", "hoodie", selected_options=selected)
    assert expanded["leaves"][0]["customization"] == {"features": "First / Second"}
    assert expanded["leaves"][0]["selected_options"] == list(reversed(selected))


@pytest.mark.asyncio
async def test_unknown_choice_value_is_not_used_as_supplier_instruction(db):
    await save(db, recipe())
    selected = deepcopy(choices())
    selected[1]["value_id"] = "not-a-catalog-choice"
    with pytest.raises(HTTPException) as error:
        await bundles.expand_bundle(db, "owner", "hoodie", selected_options=selected)
    assert error.value.detail["code"] == "bundle_option_value_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("option_type, labels", [
    ("radio", ["M", " m "]),
    ("radio", ["M", "Ｍ"]),
    ("radio", ["M", "  "]),
    ("radio", ["M", None]),
    ("radio", ["M", 42]),
    ("checkbox", ["X", "Y", "X / Y"]),
    ("multiple", ["X", "Y", "X ／ Y"]),
    ("multi_select", ["X /", "Y", "X", "/ Y"]),
])
async def test_choice_labels_cannot_collide_in_supplier_receipt_identity(db, option_type, labels):
    await db[PRODUCTS].update_one({"salla_product_id": "hoodie"}, {"$set": {"options": [
        {"id": "spec", "type": option_type, "values": [{"id": str(index), "name": name}
                                                          for index, name in enumerate(labels)]}]}})
    rows = [{"kind": "current_product", "quantity": "1", "sourcing_mode": "supplier",
             "supplier_id": "supplier", "slots": [{"slot_id": "one", "selection_option_ids": ["spec"]}]}]
    with pytest.raises(HTTPException) as error:
        await save(db, rows)
    assert error.value.detail["code"] == "bundle_invalid_selection_option_labels"
    assert await db[bundles.BUNDLES].count_documents({}) == 0


@pytest.mark.asyncio
async def test_frozen_choice_labels_are_validated_before_supplier_expansion(db):
    await save(db, recipe())
    expanded = await bundles.expand_bundle(db, "owner", "hoodie", selected_options=choices())
    frozen = deepcopy(expanded["frozen_definitions"])
    size = next(option for option in frozen["hoodie"]["catalog_product"]["options"] if option["id"] == "first_size")
    size["values"][1]["name"] = " m "
    with pytest.raises(HTTPException) as error:
        await bundles.expand_bundle(db, "owner", "hoodie", selected_options=choices(), frozen_definitions=frozen)
    assert error.value.detail["code"] == "bundle_invalid_selection_option_labels"


@pytest.mark.asyncio
@pytest.mark.parametrize("show_extra", [False, True, None])
async def test_only_proven_hidden_piece_choice_can_be_omitted(db, show_extra):
    await db[PRODUCTS].update_one({"salla_product_id": "hoodie"}, {"$set": {"options": [
        {"id": "customize", "type": "radio", "required": show_extra is not None,
         "values": [{"id": "yes", "name": "Yes"}, {"id": "no", "name": "No"}]},
        {"id": "extra_color", "type": "radio", "required": False,
         "visibility": "on_condition", "visibility_condition_type": "=",
         "visibility_condition_option": "customize", "visibility_condition_value": "yes",
         "values": [{"id": "black", "name": "Black"}]},
    ]}})
    await save(db, [{"kind": "current_product", "quantity": "1", "sourcing_mode": "supplier",
                     "supplier_id": "supplier", "slots": [{"slot_id": "one", "selection_option_ids": ["extra_color"]}]}])
    selected = [] if show_extra is None else [{"option_id": "customize", "value_id": "yes" if show_extra else "no"}]
    if show_extra is not False:
        with pytest.raises(HTTPException) as error:
            await bundles.expand_bundle(db, "owner", "hoodie", selected_options=selected)
        assert error.value.detail["code"] == "bundle_selection_required"
        assert {"product_id": "hoodie", "slot_id": "one", "option_id": "extra_color"} in error.value.detail["options"]
    else:
        expanded = await bundles.expand_bundle(db, "owner", "hoodie", selected_options=selected)
        assert expanded["cost_complete"] is True
        assert Decimal(expanded["estimated_cost"]) == 50
        assert expanded["leaves"][0]["customization"] == {}
