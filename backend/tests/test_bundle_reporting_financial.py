"""Bundle-only financial base acceptance with the existing external service path.

All records are synthetic and held in mongomock. The real catalog expansion,
historical authority, reservation/consumption, and service inheritance functions
are used; this module never connects to an application or a production database.
"""
from copy import deepcopy
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

import bundle_catalog_v2 as catalog
import bundle_fulfillment_v2 as fulfillment
import stock_component_consumption_service as stock
from order_cost_authority import (
    LIFECYCLES, SNAPSHOTS, _bundle_cost, capture_reporting_costs, line_fingerprint,
    load_order_cost_authority, resolve_order_line_cost,
)
from product_v2_routes import PRODUCTS


OWNER = "synthetic-bundle-financial-owner"
ROOT = "fixture-shawl"
ROSE = "fixture-rose"
SERVICE = "fixture-external-embroidery"
BINDING = "fixture-name-binding"
POLICY = {"revision": 1, "include_parent_base": True,
          "evidence_ref": "owner-confirmed-fixture-base15-and-external-service10"}
ROSE_NAME = "هل تريد باقة الورد مع الشال ؟"
NAME_NAME = "هل تريد اضافة اسم على الشال؟"


def sale_line(quantity=4, *, rose=True, name=False, key="sale-line", product=ROOT):
    options = [
        {"option_id": "rose-choice", "option_name": ROSE_NAME,
         "value_id": "yes" if rose else "no", "value_name": "نعم" if rose else "لا"},
        {"option_id": "name-choice", "option_name": NAME_NAME,
         "value_id": "yes" if name else "no", "value_name": "نعم" if name else "لا"},
    ]
    return {"order_line_id": key, "order_item_id": key, "source_item_id": key,
            "product_id": product, "quantity": quantity, "variant_id": None,
            "selected_options": [{"option_id": o["option_id"], "value_id": o["value_id"]} for o in options],
            "options_raw": options,
            "file_spec_fields": [{"name": o["option_name"], "value": o["value_name"]} for o in options]}


async def set_policy(db, enabled=True, *, product=ROOT):
    if enabled:
        await db[catalog.COST_PROFILES].update_one(
            {"user_id": OWNER, "salla_product_id": product},
            {"$set": {"bundle_reporting_cost_policy": deepcopy(POLICY)}}, upsert=True)
    else:
        await db[catalog.COST_PROFILES].update_one(
            {"user_id": OWNER, "salla_product_id": product},
            {"$unset": {"bundle_reporting_cost_policy": ""}})


@pytest_asyncio.fixture
async def db(monkeypatch):
    database = AsyncMongoMockClient().bundle_reporting_financial

    async def local_catalog_owner(database, owner, callback, **kwargs):
        assert owner == OWNER
        return await callback(database)

    monkeypatch.setattr(catalog, "operational_owner", local_catalog_owner)
    choices = [{"id": "rose-choice", "name": ROSE_NAME},
               {"id": "name-choice", "name": NAME_NAME}]
    options = [{**row, "required": False, "values": [
        {"id": "yes", "name": "نعم"}, {"id": "no", "name": "لا"}]} for row in choices]
    for key, base in [(ROOT, 15), (ROSE, 40), ("ordinary", 7), ("outer-bundle", 100)]:
        await database[PRODUCTS].insert_one({"user_id": OWNER, "id": "mpv2_" + key,
            "mezan_product_id": "mpv2_" + key, "salla_product_id": key,
            "name": key, "sku": "FIXTURE-" + key, "cost_price_from_salla": 70,
            "variants": [], "options": deepcopy(options) if key in {ROOT, "ordinary"} else []})
        await database[catalog.COST_PROFILES].insert_one({"user_id": OWNER,
            "salla_product_id": key, "base_cost": base})
    await database[catalog.RESOURCES].insert_one({"user_id": OWNER, "id": SERVICE,
        "name": "Fixture external embroidery", "kind": "service", "status": "active",
        "unit": "job", "unit_cost": 10, "track_inventory": False, "requires_preparation": True})
    for product, binding in [(ROOT, BINDING), ("ordinary", "ordinary-name-binding")]:
        await database[catalog.BINDINGS].insert_one({"user_id": OWNER, "id": binding,
            "salla_product_id": product, "option_id": "name-choice", "option_name": NAME_NAME,
            "value_id": "yes", "value_name": "نعم", "mode": "resource",
            "resource_id": SERVICE, "quantity": 1})
        await database[catalog.COST_PROFILES].update_one(
            {"user_id": OWNER, "salla_product_id": product},
            {"$set": {"composition_components": {"option_resource:" + binding: {
                "cost_treatment": "independent", "assembly_origin": "supplier",
                "evidence_ref": "owner-confirmed-external-supplier-fixture"}}}})
    await database["mezan_suppliers_v2"].insert_one(
        {"user_id": OWNER, "id": "external-supplier", "status": "active"})
    await set_policy(database)
    await catalog.save_bundle(database, OWNER, ROOT, {"expected_revision": 0,
        "product_type": "composite", "schema_version": 1,
        "items": [{"kind": "product", "product_id": ROSE, "quantity": "1",
                   "condition": {"option_id": "rose-choice", "value_id": "yes"}}]}, OWNER)
    return database


async def expand(db, item=None, *, frozen=None, strict=True):
    item = item or sale_line()
    return await catalog.expand_bundle(db, OWNER, item["product_id"],
        selected_options=item["selected_options"], frozen_definitions=frozen, strict=strict)


@pytest.mark.asyncio
@pytest.mark.parametrize("rose,name,expected,components", [
    (True, False, 220, 160), (True, True, 260, 200),
    (False, True, 100, 40), (False, False, 60, 0),
])
async def test_external_service_bundle_cost_matrix(db, rose, name, expected, components):
    snapshot = await expand(db, sale_line(rose=rose, name=name))
    result = _bundle_cost(snapshot, 4)
    assert result["line_total"] == expected
    assert result["base_total"] == 60
    assert result["components_total"] == components
    assert result["calculation_cost_available"] is True
    assert result["currency"] == "SAR"
    report = snapshot["reporting_cost"]
    assert report["version"] == "bundle-reporting-cost-v1"
    assert report["currency"] == "SAR" and report["cost_stage"] == "expected"
    assert report["complete"] is True and report["missing_cost_reasons"] == []
    assert Decimal(str(report["unit_total_sar"])) * 4 == expected
    assert Decimal(str(report["base_total_sar"])) == 15
    assert Decimal(str(report["components_total_sar"])) * 4 == components
    assert [row["product_id"] for row in report["added_bases"]] == [ROOT]
    assert snapshot["schema_version"] == 1
    assert Decimal(snapshot["estimated_cost"]) * 4 == components
    services = [leaf for leaf in snapshot["leaves"] if leaf["kind"] == "resource"]
    assert len(services) == int(name)
    assert all(leaf["resource_id"] == SERVICE for leaf in services)
    assert not any(leaf["kind"] == "direct_cost" for leaf in snapshot["leaves"])


@pytest.mark.asyncio
@pytest.mark.parametrize("second_rose,expected", [(True, 240), (False, 160)])
async def test_mixed_customer_choices_remain_separate_lines(db, second_rose, expected):
    items = [sale_line(2, rose=True, name=True, key="with-name"),
             sale_line(2, rose=second_rose, name=False, key="without-name")]
    snapshots = await fulfillment.capture_intake(db, OWNER, items, {})
    await db[LIFECYCLES].insert_one({"user_id": OWNER, "order_number": "mixed",
                                    "bundle_intake_snapshots": snapshots})
    authority = await load_order_cost_authority(db, OWNER, [
        {"order_number": "mixed", "products": items}])
    costs = [resolve_order_line_cost(item, order_number="mixed", catalog_cost={},
                                    authority=authority) for item in items]
    assert sum(cost["line_total"] for cost in costs) == expected
    assert sum(cost["base_total"] for cost in costs) == 60
    assert all(cost["calculation_cost_available"] for cost in costs)


@pytest.mark.asyncio
@pytest.mark.parametrize("quantity,expected", [(1, 65), (3, 195), (4, 260)])
async def test_sale_quantity_multiplies_financial_base_and_real_service_once(db, quantity, expected):
    snapshot = await expand(db, sale_line(quantity, name=True))
    assert _bundle_cost(snapshot, quantity)["line_total"] == expected
    assert _bundle_cost(snapshot, quantity)["base_total"] == 15 * quantity


@pytest.mark.asyncio
async def test_zero_sale_quantity_is_unresolved_and_not_a_free_complete_bundle(db):
    snapshot = await expand(db, sale_line(name=True))
    result = _bundle_cost(snapshot, 0)
    assert result["calculation_cost_available"] is False
    assert result["cost_stage"] == "unknown"
    assert "bundle_quantity_unresolved" in result["missing_cost_reasons"]
    with pytest.raises(HTTPException):
        stock._lines([sale_line(0, name=True)])


async def database_records(db):
    return {name: await db[name].find({}).sort("_id", 1).to_list(None)
            for name in sorted(await db.list_collection_names())}


def operational_snapshot(snapshot):
    result = deepcopy(snapshot)
    result.pop("reporting_cost", None)
    for definition in result.get("frozen_definitions", {}).values():
        profile = (definition.get("catalog_metadata") or {}).get("profile")
        if isinstance(profile, dict):
            profile.pop("bundle_reporting_cost_policy", None)
    return result


@pytest.mark.asyncio
async def test_opt_in_changes_only_financial_snapshot_and_never_catalog_or_services(db):
    await set_policy(db, False)
    before = await expand(db, sale_line(name=True))
    assert _bundle_cost(before, 4)["line_total"] == 200
    await set_policy(db)
    records = await database_records(db)
    after = await expand(db, sale_line(name=True))
    assert _bundle_cost(after, 4)["line_total"] == 260
    assert operational_snapshot(after) == operational_snapshot(before)
    assert await database_records(db) == records
    definition = await db[catalog.BUNDLES].find_one({"user_id": OWNER, "product_id": ROOT})
    assert definition["schema_version"] == 1 and len(definition["items"]) == 1
    assert definition["items"][0]["product_id"] == ROSE
    binding = await db[catalog.BINDINGS].find_one({"user_id": OWNER, "id": BINDING})
    assert binding["mode"] == "resource" and binding["resource_id"] == SERVICE
    assert "direct_amount" not in binding


@pytest.mark.asyncio
async def test_external_service_identity_preparation_and_supplier_price_are_unchanged(db):
    from preparation_piece_operations import inherit_required_services
    from supplier_receiving_routes import (
        _supplier_product_reference_price, supplier_piece_invoice_services,
    )
    item = sale_line(name=True)

    async def service_views():
        bindings = await db[catalog.BINDINGS].find(
            {"user_id": OWNER, "salla_product_id": ROOT}, {"_id": 0}).to_list(None)
        resource = await db[catalog.RESOURCES].find_one({"user_id": OWNER, "id": SERVICE}, {"_id": 0})
        inherited = inherit_required_services(line=item, product_links=[],
            option_bindings=bindings, resources_by_id={SERVICE: resource})
        piece = {"product_id": ROOT, "services": inherited,
                 "product_options_snapshot": {NAME_NAME: "نعم", ROSE_NAME: "نعم"}}
        invoice = supplier_piece_invoice_services(piece, {"supplier_snapshot": {
            "id": "external-supplier", "service_links": [{"service_id": SERVICE}]}}, {SERVICE: resource})
        reference = await _supplier_product_reference_price(db, user_id=OWNER, piece=piece)
        return inherited, invoice, reference

    await set_policy(db, False)
    before = await service_views()
    await set_policy(db)
    records = await database_records(db)
    after = await service_views()
    assert after == before
    services, invoice, reference = after
    assert len(services) == len(invoice) == 1
    assert services[0]["service_id"] == invoice[0]["service_id"] == SERVICE
    assert services[0]["source"] == "option" and services[0]["customer_selected"] is True
    assert services[0]["status"] == "pending" and services[0]["completed_quantity"] == 0
    assert services[0]["required_quantity"] == 1
    assert invoice[0]["reference_unit_price_halalas"] == 1000
    assert invoice[0]["reference_unit_price_halalas"] * 4 == 4000
    assert reference["reference_product_base_unit_price_halalas"] == 1500
    assert reference["reference_product_option_cost_halalas"] == 0
    assert reference["reference_product_unit_price_halalas"] == 1500
    assert await database_records(db) == records


@pytest.mark.asyncio
async def test_ordinary_service_and_physical_component_ignore_bundle_only_policy(db):
    await db[catalog.RESOURCES].insert_one({"user_id": OWNER, "id": "ordinary-thread",
        "name": "Fixture thread", "kind": "material", "track_inventory": True, "unit_cost": 2})
    await db[catalog.PRODUCT_RESOURCE_BINDINGS].insert_one({"user_id": OWNER,
        "id": "ordinary-thread-binding", "salla_product_id": "ordinary",
        "resource_id": "ordinary-thread", "quantity": 2})
    item = sale_line(name=True, product="ordinary")
    original = await capture_reporting_costs(db, OWNER, [item], {})
    recipe = await stock._recipe(db, OWNER, stock._lines([item])[0])
    await set_policy(db, product="ordinary")
    records = await database_records(db)
    assert await expand(db, item) is None
    assert await capture_reporting_costs(db, OWNER, [item], {}) == original
    assert await stock._recipe(db, OWNER, stock._lines([item])[0]) == recipe
    assert original["sale-line"]["cost"]["line_total"] == (7 + 10 + 2 * 2) * 4
    assert len(recipe[1]) == 1 and recipe[1][0]["resource_id"] == "ordinary-thread"
    assert await database_records(db) == records


@pytest.mark.asyncio
async def test_frozen_expansion_and_intake_replay_ignore_future_catalog_and_policy_changes(db):
    item = sale_line(name=True)
    initial = await fulfillment.capture_intake(db, OWNER, [item], {})
    snapshot = initial["sale-line"]
    assert _bundle_cost(snapshot, 4)["line_total"] == 260
    await db[catalog.COST_PROFILES].update_many({"user_id": OWNER}, {"$set": {"base_cost": 999}})
    await db[catalog.RESOURCES].update_one({"user_id": OWNER, "id": SERVICE}, {"$set": {"unit_cost": 777}})
    await set_policy(db, False)
    replay = await expand(db, item, frozen=snapshot["frozen_definitions"])
    assert replay["reporting_cost"] == snapshot["reporting_cost"]
    assert replay["leaves"] == snapshot["leaves"]
    assert _bundle_cost(replay, 4)["line_total"] == 260
    assert await fulfillment.capture_intake(db, OWNER, [item], initial) == initial


async def seed_rose_inventory(db, quantity=20):
    from purchase_receiving_service import COST_POLICY, COST_STATES, identity_key
    item = {"item_type": "product", "product_id": "mpv2_" + ROSE,
            "mezan_product_id": "mpv2_" + ROSE, "salla_product_id": ROSE,
            "receipt_id": "fixture-rose-receipt", "quantity": quantity,
            "configuration_key": "product-" + ROSE}
    await db[stock.LOCATIONS].insert_one({"user_id": OWNER, "id": "fixture-location",
        "warehouse_id": "fixture-warehouse", "state": "occupied",
        "occupancy": {"items": [item], "total_quantity": quantity}})
    await db[stock.RECEIPTS].insert_one({**item, "id": "fixture-rose-receipt", "user_id": OWNER,
        "location_id": "fixture-location", "warehouse_id": "fixture-warehouse", "status": "posted",
        "source_type": "purchase_invoice", "source_id": "fixture-rose-purchase", "source_line_id": ROSE,
        "unit_purchase_cost": "40"})
    await db["purchase_invoices"].insert_one({"user_id": OWNER, "id": "fixture-rose-purchase", "status": "approved"})
    identity = {"item_type": "product", "product_id": "mpv2_" + ROSE, "variant_id": None}
    await db[COST_STATES].insert_one({"_id": identity_key(OWNER, identity), "user_id": OWNER,
        "authoritative": True, "cost_policy_version": COST_POLICY, "average_cost": "40"})


async def historical_cost(db, item, number="lifecycle-order"):
    authority = await load_order_cost_authority(db, OWNER, [
        {"order_number": number, "products": [item]}])
    return resolve_order_line_cost(item, order_number=number, catalog_cost={
        "line_total": 9999, "calculation_cost_available": True}, authority=authority)


@pytest.mark.asyncio
async def test_intake_plan_replay_and_consumption_keep_base_without_false_full_actual(db, monkeypatch):
    # Use the normal reservation and deduction functions with a session-less
    # test wrapper; transaction/concurrency contracts have their own real-Mongo gates.
    from accounting_atomic import SessionDatabase

    async def local_stock_owner(database, owner, callback, **kwargs):
        assert owner == OWNER
        scoped = database if isinstance(database, SessionDatabase) else SessionDatabase(database, None)
        scoped._owner = owner
        return await callback(scoped)

    monkeypatch.setattr(stock, "operational_owner", local_stock_owner)
    await db["settings"].insert_one({"user_id": OWNER, "g47_inventory": {
        "component_lifecycle_starts_at": "2026-09-01T00:00:00+00:00"}})
    await seed_rose_inventory(db)
    item = sale_line(name=True)
    intake = await fulfillment.capture_intake(db, OWNER, [item], {})
    await db[LIFECYCLES].insert_one({"user_id": OWNER, "order_number": "lifecycle-order",
                                    "bundle_intake_snapshots": intake})
    assert (await historical_cost(db, item))["line_total"] == 260
    await db[catalog.COST_PROFILES].update_many({"user_id": OWNER}, {"$set": {"base_cost": 999}})
    await db[catalog.RESOURCES].update_one({"user_id": OWNER, "id": SERVICE}, {"$set": {"unit_cost": 777}})
    await set_policy(db, False)
    reserve_args = {"merchant_id": OWNER, "order_id": "lifecycle-order", "lines": [item],
                    "source_version": 1, "source_created_at": "2026-10-01T00:00:00+00:00"}
    await stock.reserve_component_stock(db, **reserve_args)
    plan = await db[stock.PLANS].find_one({"user_id": OWNER, "order_id": "lifecycle-order"})
    assert plan["bundle_snapshots"]["sale-line"]["reporting_cost"] == intake["sale-line"]["reporting_cost"]
    assert (await historical_cost(db, item))["line_total"] == 260
    units = await db[stock.UNITS].find({"user_id": OWNER}).sort("unit_index", 1).to_list(None)
    assert len(units) == 4
    assert all(len(unit["allocations"]) == 1 for unit in units)
    assert all(unit["allocations"][0]["inventory_identity"]["product_id"] == "mpv2_" + ROSE for unit in units)
    await stock.reserve_component_stock(db, **reserve_args)
    assert await db[stock.UNITS].find({"user_id": OWNER}).sort("unit_index", 1).to_list(None) == units
    await stock.consume_component_stock(db, merchant_id=OWNER, order_id="lifecycle-order")
    consumed = await db[stock.UNITS].find({"user_id": OWNER}).sort("unit_index", 1).to_list(None)
    assert all(unit["state"] == "consumed" for unit in consumed)
    assert sum(Decimal(leaf["actual_cost"]) for unit in consumed for leaf in unit["bundle_leaves"]) == 200
    result = await historical_cost(db, item)
    assert result["line_total"] == 260 and result["base_total"] == 60
    assert result["components_total"] == 200
    assert result["cost_stage"] in {"expected", "mixed"}
    assert result["calculation_cost_source"] != "bundle_actual"
    location = await db[stock.LOCATIONS].find_one({"user_id": OWNER})
    assert location["occupancy"]["total_quantity"] == 16
    assert all(leaf["resource_id"] == SERVICE for unit in consumed
               for leaf in unit["bundle_leaves"] if leaf["kind"] == "resource")
    await stock.consume_component_stock(db, merchant_id=OWNER, order_id="lifecycle-order")
    assert (await db[stock.LOCATIONS].find_one({"user_id": OWNER}))["occupancy"] == location["occupancy"]
    assert (await historical_cost(db, item))["line_total"] == 260


@pytest.mark.asyncio
@pytest.mark.parametrize("current_quantity,expected", [(1, 260), (2, 320)])
async def test_explicit_current_product_never_receives_another_financial_base(db, current_quantity, expected):
    definition = await db[catalog.BUNDLES].find_one({"user_id": OWNER, "product_id": ROOT})
    await catalog.save_bundle(db, OWNER, ROOT, {"expected_revision": definition["revision"],
        "schema_version": 1, "product_type": "composite", "component_scopes": {SERVICE: "per_bundle"},
        "items": [{"kind": "current_product", "quantity": str(current_quantity)}, *definition["items"]]}, OWNER)
    snapshot = await expand(db, sale_line(name=True))
    assert _bundle_cost(snapshot, 4)["line_total"] == expected
    assert Decimal(snapshot["estimated_cost"]) * 4 == expected
    assert not (snapshot.get("reporting_cost") or {}).get("added_bases")
    assert sum(Decimal(leaf["quantity"]) for leaf in snapshot["leaves"]
               if leaf.get("current_product_reference")) == current_quantity


@pytest.mark.asyncio
async def test_nested_component_quantities_do_not_multiply_parent_base_or_include_unflagged_container(db):
    await catalog.save_bundle(db, OWNER, "outer-bundle", {"expected_revision": 0,
        "schema_version": 1, "product_type": "composite",
        "items": [{"kind": "product", "product_id": ROSE, "quantity": "1"}]}, OWNER)
    definition = await db[catalog.BUNDLES].find_one({"user_id": OWNER, "product_id": ROOT})
    await catalog.save_bundle(db, OWNER, ROOT, {"expected_revision": definition["revision"],
        "schema_version": 1, "product_type": "composite",
        "items": [{"kind": "product", "product_id": "outer-bundle", "quantity": "2",
                   "condition": {"option_id": "rose-choice", "value_id": "yes"}}]}, OWNER)
    snapshot = await expand(db, sale_line(name=True))
    cost = _bundle_cost(snapshot, 4)
    assert cost["line_total"] == (15 + 40 * 2 + 10) * 4
    assert cost["base_total"] == 60 and cost["components_total"] == 360
    assert [row["product_id"] for row in snapshot["reporting_cost"]["added_bases"]] == [ROOT]


async def repeated_nested_financial_bundle(db, first_policy, second_policy, *, second_quantity=1):
    condition = {"option_id": "extra-copy", "value_id": "yes"}
    await db[PRODUCTS].update_one({"user_id": OWNER, "salla_product_id": "outer-bundle"},
        {"$set": {"options": [{"id": "extra-copy", "name": "Fixture extra copy", "required": False,
                               "values": [{"id": "yes", "name": "Yes"}]}]}})
    selected = sale_line(name=False)["selected_options"]
    await catalog.save_bundle(db, OWNER, "outer-bundle", {"expected_revision": 0,
        "schema_version": 1, "product_type": "composite", "items": [
            {"kind": "product", "product_id": ROOT, "quantity": "1",
             "selected_options": selected, "repeat_policy": first_policy},
            {"kind": "product", "product_id": ROOT, "quantity": str(second_quantity),
             "selected_options": selected, "repeat_policy": second_policy, "condition": condition},
        ]}, OWNER)
    return condition


@pytest.mark.asyncio
@pytest.mark.parametrize("first_policy,second_policy,base_units", [
    ("once", "once", 1), ("sum", "once", 2), ("once", "sum", 2), ("sum", "sum", 2),
])
async def test_nested_financial_base_follows_once_or_sum_without_changing_operational_leaves(
        db, first_policy, second_policy, base_units):
    condition = await repeated_nested_financial_bundle(db, first_policy, second_policy)
    await set_policy(db, False)
    previous = await catalog.expand_bundle(db, OWNER, "outer-bundle", selected_options=[condition])
    await set_policy(db)
    snapshot = await catalog.expand_bundle(db, OWNER, "outer-bundle", selected_options=[condition])
    assert operational_snapshot(snapshot) == operational_snapshot(previous)
    cost = _bundle_cost(snapshot, 4)
    assert cost["calculation_cost_available"] is True
    assert cost["base_total"] == 15 * base_units * 4
    assert cost["line_total"] == _bundle_cost(previous, 4)["line_total"] + 15 * base_units * 4
    additions = snapshot["reporting_cost"]["added_bases"]
    assert len(additions) == 1 and additions[0]["product_id"] == ROOT
    assert Decimal(additions[0]["quantity"]) == base_units
    replay = await catalog.expand_bundle(db, OWNER, "outer-bundle", selected_options=[condition],
                                        frozen_definitions=snapshot["frozen_definitions"])
    assert replay["reporting_cost"] == snapshot["reporting_cost"]


@pytest.mark.asyncio
async def test_nested_financial_once_quantity_conflict_is_not_silently_summed(db):
    condition = await repeated_nested_financial_bundle(db, "once", "once", second_quantity=2)
    snapshot = await catalog.expand_bundle(db, OWNER, "outer-bundle", selected_options=[condition])
    assert snapshot["reporting_cost"]["complete"] is False
    assert _bundle_cost(snapshot, 4)["calculation_cost_available"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("already_included", [False, True])
async def test_historical_missing_base_repair_never_adds_financial_base_twice(db, already_included):
    from order_cost_reporting_repair import _with_missing_base
    if not already_included:
        await set_policy(db, False)
    item = sale_line(name=True)
    snapshot = await expand(db, item)
    cost = _bundle_cost(snapshot, 4)
    corrected, addition, reason = _with_missing_base(item, {"bundle_snapshot": snapshot}, cost, OWNER)
    assert corrected["line_total"] == 260
    assert corrected["base_total"] == 60 and corrected["components_total"] == 200
    if already_included:
        assert addition is None
        assert "included" in reason
        assert corrected == cost


@pytest.mark.asyncio
@pytest.mark.parametrize("sale_currency", ["SAR", "QAR", "AED", "KWD"])
async def test_base_currency_is_converted_once_and_never_uses_sale_currency(db, sale_currency):
    await db[catalog.COST_PROFILES].update_one({"user_id": OWNER, "salla_product_id": ROOT},
        {"$set": {"base_cost": 4, "cost_currency": "USD", "cost_fx_rate_to_sar": "3.75"}})
    item = {**sale_line(name=True), "currency": sale_currency, "exchange_rate": 99}
    snapshot = await expand(db, item)
    assert _bundle_cost(snapshot, 4)["line_total"] == 260
    await db[catalog.COST_PROFILES].update_one({"user_id": OWNER, "salla_product_id": ROOT},
        {"$set": {"cost_fx_rate_to_sar": 99}})
    replay = await expand(db, item, frozen=snapshot["frozen_definitions"])
    assert _bundle_cost(replay, 4)["line_total"] == 260


@pytest.mark.asyncio
async def test_missing_base_fx_is_not_misreported_as_a_complete_bundle(db):
    await db[catalog.COST_PROFILES].update_one({"user_id": OWNER, "salla_product_id": ROOT},
        {"$set": {"base_cost": 4, "cost_currency": "USD"}, "$unset": {"cost_fx_rate_to_sar": ""}})
    snapshot = await expand(db, sale_line(name=True))
    assert _bundle_cost(snapshot, 4)["calculation_cost_available"] is False
    assert snapshot["reporting_cost"]["complete"] is False
    assert snapshot["reporting_cost"]["missing_cost_reasons"]


@pytest.mark.asyncio
async def test_schema2_external_service_execution_guard_stays_closed_with_financial_policy(db):
    definition = await db[catalog.BUNDLES].find_one({"user_id": OWNER, "product_id": ROOT})
    await catalog.save_bundle(db, OWNER, ROOT, {"expected_revision": definition["revision"],
        "schema_version": 2, "product_type": "composite", "items": definition["items"]}, OWNER)
    errors = []
    for enabled in (False, True):
        await set_policy(db, enabled)
        with pytest.raises(HTTPException) as caught:
            await expand(db, sale_line(name=True))
        assert caught.value.status_code == 409
        assert caught.value.detail["code"] == "bundle_component_policy_unresolved"
        assert any(row["reason"] == "supplier_component_execution_required"
                   for row in caught.value.detail["components"])
        errors.append(caught.value.detail)
    assert errors[0] == errors[1]


@pytest.mark.asyncio
async def test_nonstock_supplier_source_guard_cannot_be_bypassed_by_financial_policy(db):
    before = await db[catalog.BUNDLES].find_one({"user_id": OWNER, "product_id": ROOT})
    with pytest.raises(HTTPException) as caught:
        await catalog.save_bundle(db, OWNER, ROOT, {"expected_revision": before["revision"],
            "schema_version": 1, "product_type": "composite", "items": [{"kind": "resource",
                "resource_id": SERVICE, "quantity": "1", "sourcing_mode": "supplier",
                "supplier_id": "external-supplier"}]}, OWNER)
    assert caught.value.detail["code"] == "bundle_nonstock_supplier_requires_service_receipt"
    assert await db[catalog.BUNDLES].find_one({"user_id": OWNER, "product_id": ROOT}) == before


async def schema2_service_fixture(db, *, origin="supplier", treatment="independent"):
    await db[catalog.COST_PROFILES].update_one({"user_id": OWNER, "salla_product_id": ROOT},
        {"$set": {"composition_components.option_resource:" + BINDING: {
            "cost_treatment": treatment, "assembly_origin": origin,
            "evidence_ref": "owner-confirmed-existing-service-is-additional"}}})
    definition = await db[catalog.BUNDLES].find_one({"user_id": OWNER, "product_id": ROOT})
    await catalog.save_bundle(db, OWNER, ROOT, {"expected_revision": definition["revision"],
        "schema_version": 2, "product_type": "composite", "items": definition["items"]}, OWNER)


@pytest.mark.asyncio
@pytest.mark.parametrize("origin,reason", [
    ("unknown", "component_provenance_required"),
    ("supplier", "supplier_component_execution_required"),
])
async def test_schema2_known_service_price_is_financial_only_while_execution_stays_blocked(db, origin, reason):
    from preparation_piece_operations import inherit_required_services

    await schema2_service_fixture(db, origin=origin)
    item = sale_line(name=True)
    await set_policy(db, False)
    previous = await expand(db, item, strict=False)
    await set_policy(db)
    records = await database_records(db)
    snapshot = await expand(db, item, strict=False)
    assert operational_snapshot(snapshot) == operational_snapshot(previous)
    assert snapshot["schema_version"] == 2
    assert snapshot["cost_complete"] is False
    assert snapshot["estimated_cost"] is None
    assert Decimal(snapshot["known_cost"]) == 40
    assert len(snapshot["leaves"]) == 1 and snapshot["leaves"][0]["product_id"] == ROSE
    decision = snapshot["unresolved_components"][0]
    assert decision["reason"] == reason
    assert decision["resource_id"] == SERVICE and decision["binding_id"] == BINDING
    assert decision["cost_treatment"] == "independent" and decision["use"] is True
    assert Decimal(str(decision["unit_cost"])) == 10
    cost = _bundle_cost(snapshot, 4)
    assert cost["calculation_cost_available"] is True
    assert cost["line_total"] == 260
    assert cost["base_total"] == 60 and cost["components_total"] == 200
    assert cost["cost_stage"] == "expected"
    assert snapshot["reporting_cost"]["complete"] is True
    assert not any(leaf.get("resource_id") == SERVICE for leaf in snapshot["leaves"])
    with pytest.raises(HTTPException) as caught:
        await expand(db, item)
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "bundle_component_policy_unresolved"
    assert any(row["reason"] == reason for row in caught.value.detail["components"])
    binding = await db[catalog.BINDINGS].find_one({"user_id": OWNER, "id": BINDING}, {"_id": 0})
    resource = await db[catalog.RESOURCES].find_one({"user_id": OWNER, "id": SERVICE}, {"_id": 0})
    services = inherit_required_services(line=item, product_links=[],
        option_bindings=[binding], resources_by_id={SERVICE: resource})
    assert len(services) == 1 and services[0]["service_id"] == SERVICE
    assert services[0]["status"] == "pending" and services[0]["completed_quantity"] == 0
    assert await database_records(db) == records

    # The expense proof is the original price/selection, not a later catalog estimate.
    await db[catalog.RESOURCES].update_one({"user_id": OWNER, "id": SERVICE}, {"$set": {"unit_cost": 777}})
    await db[catalog.COST_PROFILES].update_many({"user_id": OWNER}, {"$set": {"base_cost": 999}})
    await set_policy(db, False)
    replay = await expand(db, item, strict=False, frozen=snapshot["frozen_definitions"])
    assert replay["reporting_cost"] == snapshot["reporting_cost"]
    assert replay["unresolved_components"] == snapshot["unresolved_components"]
    assert _bundle_cost(replay, 4)["line_total"] == 260


@pytest.mark.asyncio
@pytest.mark.parametrize("rose,expected", [(True, 220), (False, 60)])
async def test_schema2_unselected_external_service_is_not_added_to_financial_cost(db, rose, expected):
    await schema2_service_fixture(db)
    snapshot = await expand(db, sale_line(rose=rose, name=False))
    assert snapshot["unresolved_components"] == []
    assert not any(row.get("resource_id") == SERVICE for row in snapshot["component_decisions"])
    assert _bundle_cost(snapshot, 4)["line_total"] == expected


@pytest.mark.asyncio
async def test_schema2_existing_used_service_leaf_is_not_charged_again_as_a_financial_decision(db):
    # A synthetic non-blocked control proves the new financial-only path does
    # not charge a decision that the unchanged engine already emitted as a leaf.
    await schema2_service_fixture(db, origin="inhouse")
    snapshot = await expand(db, sale_line(name=True))
    assert snapshot["unresolved_components"] == []
    assert len([leaf for leaf in snapshot["leaves"] if leaf.get("resource_id") == SERVICE]) == 1
    assert len([row for row in snapshot["component_decisions"] if row.get("resource_id") == SERVICE]) == 1
    assert _bundle_cost(snapshot, 4)["line_total"] == 260
    assert _bundle_cost(snapshot, 4)["components_total"] == 200


@pytest.mark.asyncio
async def test_schema2_financial_service_uses_its_own_frozen_cost_fx_once(db):
    await schema2_service_fixture(db)
    await db[catalog.COST_PROFILES].update_one({"user_id": OWNER, "salla_product_id": ROOT},
        {"$set": {"base_cost": 4, "cost_currency": "USD", "cost_fx_rate_to_sar": "3.75"}})
    await db[catalog.COST_PROFILES].update_one({"user_id": OWNER, "salla_product_id": ROSE},
        {"$set": {"base_cost": 16, "cost_currency": "USD", "cost_fx_rate_to_sar": "2.5"}})
    await db[catalog.RESOURCES].update_one({"user_id": OWNER, "id": SERVICE},
        {"$set": {"unit_cost": 4, "cost_currency": "USD", "cost_fx_rate_to_sar": "2.5"}})
    item = {**sale_line(name=True), "currency": "KWD", "exchange_rate": 999}
    snapshot = await expand(db, item, strict=False)
    assert _bundle_cost(snapshot, 4)["line_total"] == 260
    await db[catalog.RESOURCES].update_one({"user_id": OWNER, "id": SERVICE},
        {"$set": {"unit_cost": 99, "cost_fx_rate_to_sar": 99}})
    replay = await expand(db, item, strict=False, frozen=snapshot["frozen_definitions"])
    assert replay["reporting_cost"] == snapshot["reporting_cost"]
    assert _bundle_cost(replay, 4)["line_total"] == 260


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["cost_treatment", "resource_cost", "resource_fx"])
async def test_schema2_execution_reason_does_not_authorize_unknown_financial_cost(db, missing):
    await schema2_service_fixture(db, treatment="unknown" if missing == "cost_treatment" else "independent")
    if missing == "resource_cost":
        await db[catalog.RESOURCES].update_one({"user_id": OWNER, "id": SERVICE}, {"$set": {"unit_cost": None}})
    if missing == "resource_fx":
        await db[catalog.RESOURCES].update_one({"user_id": OWNER, "id": SERVICE},
            {"$set": {"cost_currency": "USD"}, "$unset": {"cost_fx_rate_to_sar": ""}})
    snapshot = await expand(db, sale_line(name=True), strict=False)
    assert snapshot["unresolved_components"]
    assert snapshot["reporting_cost"]["complete"] is False
    assert snapshot["reporting_cost"]["missing_cost_reasons"]
    assert _bundle_cost(snapshot, 4)["calculation_cost_available"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [
    "shared_component_override_conflict",
    "supplier_omission_unconfirmed",
    "included_component_inventory_cost_basis_required",
])
async def test_schema2_financial_allowlist_rejects_unrelated_operational_conflicts(db, reason):
    from bundle_reporting_cost import freeze_reporting_cost

    await schema2_service_fixture(db)
    snapshot = await expand(db, sale_line(name=True), strict=False)
    # Exercise the financial boundary directly with a different producer reason;
    # a known price alone must not make every unresolved decision chargeable.
    for rows in (snapshot["component_decisions"], snapshot["unresolved_components"]):
        for row in rows:
            if row.get("resource_id") == SERVICE:
                row["reason"] = reason
    snapshot["reporting_cost"] = freeze_reporting_cost(snapshot, snapshot["reporting_cost"]["added_bases"])
    assert snapshot["reporting_cost"]["complete"] is False
    assert _bundle_cost(snapshot, 4)["calculation_cost_available"] is False


@pytest.mark.asyncio
async def test_schema2_missing_customer_selection_remains_financially_incomplete(db):
    await schema2_service_fixture(db)
    await db[PRODUCTS].update_one({"user_id": OWNER, "salla_product_id": ROOT}, {"$push": {
        "options": {"id": "required-color", "name": "Fixture required color", "required": True,
                    "values": [{"id": "red", "name": "Red"}]}}})
    snapshot = await expand(db, sale_line(name=True), strict=False)
    assert snapshot["needs_selection"] is True
    assert snapshot["reporting_cost"]["complete"] is False
    assert _bundle_cost(snapshot, 4)["calculation_cost_available"] is False


@pytest.mark.asyncio
async def test_schema2_financial_capture_does_not_claim_operational_intake_or_legacy_parent(db):
    from reviewed_products_catalog import bundle_parent_line_ids

    await schema2_service_fixture(db)
    item = sale_line(name=True)
    number = "financial-only-order"
    before_parent_ids = (await bundle_parent_line_ids(db, OWNER, [number])).get(number, set())
    manual = {"user_id": OWNER, "order_number": number, "order_item_id": "sale-line",
              "salla_product_id": ROOT, "quantity": 4, "currency": "SAR",
              "base_unit_cost": 15, "line_cost": 60, "line_fingerprint": line_fingerprint(item)}
    await db[SNAPSHOTS].insert_one(deepcopy(manual))
    manual_before = await db[SNAPSHOTS].find_one({"user_id": OWNER, "order_number": number})
    records = await database_records(db)
    financial, blocked = {}, {}
    intake = await fulfillment.capture_intake(db, OWNER, [item], {},
        reporting_costs=financial, blocked_intakes=blocked)
    assert "sale-line" not in intake  # Even an explicit None would claim this intake decision.
    assert blocked["sale-line"] == "bundle_component_policy_unresolved"
    assert set(financial) == {"sale-line"}
    record = financial["sale-line"]
    assert record["bundle_financial"] is True
    assert record["quantity"] == 4 and record["product_id"] == ROOT
    assert record["line_fingerprint"] == line_fingerprint(item)
    assert {(row["option_id"], row["value_id"]) for row in record["selected_options"]} == {
        (row["option_id"], row["value_id"]) for row in item["selected_options"]}
    assert record["cost"]["line_total"] == 260
    assert record["cost"]["cost_authority"] == "bundle_snapshot"
    assert record["cost"]["cost_stage"] == "expected"
    assert record["cost"]["calculation_cost_available"] is True
    assert "frozen_definitions" not in record["bundle_snapshot"]
    assert record["bundle_snapshot"]["operational_blocked"] is True
    assert record["bundle_snapshot"]["unresolved_components"]
    assert await database_records(db) == records

    await db[LIFECYCLES].insert_one({"user_id": OWNER, "order_number": number,
        "bundle_intake_snapshots": intake, "reporting_cost_snapshots": deepcopy(financial)})
    after_parent_ids = (await bundle_parent_line_ids(db, OWNER, [number])).get(number, set())
    assert after_parent_ids == before_parent_ids == set()
    assert (await historical_cost(db, item, number))["line_total"] == 260
    assert await db[SNAPSHOTS].find_one({"user_id": OWNER, "order_number": number}) == manual_before
    assert await db[stock.PLANS].count_documents({"user_id": OWNER}) == 0
    assert await db[stock.UNITS].count_documents({"user_id": OWNER}) == 0

    # The immutable financial proof must survive without turning its compact
    # snapshot into an operational definition or reading today's prices again.
    await db[catalog.RESOURCES].update_one({"user_id": OWNER, "id": SERVICE}, {"$set": {"unit_cost": 777}})
    await db[catalog.COST_PROFILES].update_many({"user_id": OWNER}, {"$set": {"base_cost": 999}})
    await set_policy(db, False)
    saved = deepcopy(financial)
    replay_blocked = {}
    assert await fulfillment.capture_intake(db, OWNER, [item], intake,
        reporting_costs=financial, blocked_intakes=replay_blocked) == intake
    assert financial == saved
    assert replay_blocked["sale-line"] == "bundle_component_policy_unresolved"
    assert (await historical_cost(db, item, number))["line_total"] == 260
    assert (await bundle_parent_line_ids(db, OWNER, [number])).get(number, set()) == set()
    assert await db[SNAPSHOTS].find_one({"user_id": OWNER, "order_number": number}) == manual_before
    # Callers without financial sinks still receive the unchanged strict guard.
    with pytest.raises(HTTPException) as caught:
        await fulfillment.capture_intake(db, OWNER, [item], {})
    assert caught.value.detail["code"] == "bundle_component_policy_unresolved"

