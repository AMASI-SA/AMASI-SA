"""Physical bundle leaves use the existing supplier invoice, never fake stock.

All financial integration evidence is synthetic and confined to an isolated
loopback replica set. The real session/scan/close routes and native ledger run.
"""
import asyncio
from copy import deepcopy
from decimal import Decimal

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError
import pytest

import bundle_supplier_pieces as bridge
import bundle_fulfillment_v2 as bundles
import preparation_piece_operations as pieces
import preparation_supplier_dispatch as dispatch
import stock_component_consumption_service as stock
import supplier_receipt_ready_policy as ready
import supplier_receiving_routes as receiving
from order_engine.mapper import map_salla_order
from fulfillment_v2_routes import _component_order_lines, COMPONENT_LIFECYCLES
from order_cost_authority import _bundle_cost
from test_supplier_native_invoice_v2 import env, http, OWNER, ACTOR, NOW, identities, mapping, legs

NUMBER = "bundle-supplier-synthetic"
LINE = "salla:bundle-supplier-synthetic:pair"


def frozen_leaves():
    return [{"leaf_id": "leaf-" + slot, "kind": "product", "current_product_reference": True,
        "product_id": "product", "mezan_product_id": "product", "salla_product_id": "product",
        "variant_id": None, "slot_id": slot, "path": ["pair", slot], "quantity": "1",
        "customization": {slot + "-size": "M", slot + "-color": "Black"},
        "customization_labels": {slot + "-size": "Size " + slot, slot + "-color": "Color " + slot},
        "selected_options": [{"option_id": "offer", "value_id": "pair", "name": "Offer", "value": "Pair"}],
        "sourcing_mode": "supplier", "supplier_id": "supplier", "unit_cost": "50",
        "direct_unit_cost": "0", "required": True, "cost_source": "mezan_v2_base"}
        for slot in ("batman", "catwoman")]


def unit_fixture():
    return {"_id": stock._id("component_unit", OWNER, NUMBER, LINE, 1), "user_id": OWNER,
        "plan_id": stock._id("component_plan", OWNER, NUMBER), "order_id": NUMBER,
        "order_line_id": LINE, "unit_index": 1, "product_id": "product", "variant_id": None,
        "selected_context": [], "state": "reserved", "bundle_revision": 3,
        "bundle_leaves": [{**leaf, "state": "awaiting_supplier", "reserved_quantity": "0",
                          "consumed_quantity": "0", "received_quantity": "0"} for leaf in frozen_leaves()],
        "resource_demands": [], "allocations": [], "prebuilt": None,
        "consumption_id": "synthetic-bundle-consumption"}


def template_fixture():
    return {"user_id": OWNER, "piece_id": "ordinary-parent", "batch_id": "synthetic-batch",
        "file_number": "synthetic-file", "group_key": "product", "product_id": "product",
        "product_name": "Synthetic pair", "sku": "product", "status": "in_progress",
        "execution_status": "supplier_sent", "supplier_dispatch_status": "sent", "supplier_id": "supplier",
        "supplier_name": "Synthetic supplier", "responsible_employee_id": OWNER,
        "responsible_employee_name": "Synthetic owner", "services": []}


async def seed_bundle(db, *, materialized=True):
    source = {"user_id": OWNER, "order_number": NUMBER, "order_status": "in_progress",
        "order_status_slug": "in_progress", "g47_salla_snapshot": {"revision": 7, "component_pending": False},
        "raw_by_source": {"salla_direct": {"id": "synthetic-native-order", "reference_id": NUMBER,
            "date": "2026-11-18T12:00:00+00:00", "status": {"slug": "in_progress"},
            "items": [{"id": "pair", "quantity": 1, "product": {"id": "product"}}]}}}
    lines = stock._lines(_component_order_lines(map_salla_order(source["raw_by_source"]["salla_direct"]), {}))
    snapshot = {"product_id": "product", "revision": 3, "leaves": frozen_leaves(), "estimated_cost": "100"}
    original = unit_fixture()
    original["selected_context"] = lines[0]["selected_context"]
    unit = bridge.prepare_supplier_leaf_unit(original) if materialized else original
    plan = {"_id": unit["plan_id"], "user_id": OWNER, "order_id": NUMBER, "state": "accepted",
        "source_version": {"kind": "revision", "value": 7}, "lines": lines,
        "bundle_snapshots": {LINE: snapshot}, "warehouse_ids": []}
    await db[bridge.PLANS].insert_one(deepcopy(plan))
    await db[bridge.UNITS].insert_one(deepcopy(unit))
    await db.unified_orders.insert_one(deepcopy(source))
    workflow = {"user_id": OWNER, "order_number": NUMBER, "stage": "in_progress", "items": [],
        "fulfillment_decision": {"lines": [{"order_item_id": LINE, "bundle": True}]}}
    await db.order_review_workflows.insert_one(deepcopy(workflow))
    await db[COMPONENT_LIFECYCLES].insert_one({"user_id": OWNER, "order_number": NUMBER,
        "generation": 7, "state": "reserved", "accepted": True, "bundle_intake_snapshots": {LINE: snapshot}})
    physical = bridge.build_supplier_leaf_pieces(owner=OWNER, unit=unit, template=template_fixture()) if materialized else []
    if physical:
        await db[bridge.PIECES].insert_many(deepcopy(physical))
    await db[receiving.PRODUCTS].update_one({"user_id": OWNER}, {"$set": {
        "salla_product_id": "product", "mezan_product_id": "product", "name": "Synthetic pair", "sku": "product"}})
    await db[receiving.COST_PROFILES].insert_one({"user_id": OWNER, "salla_product_id": "product",
        "mezan_product_id": "product", "base_cost": 50})
    return unit, snapshot, physical, source, workflow


async def open_and_scan(http, physical):
    response = await http.post("/supplier-receiving-v1/sessions", json={
        "supplier_id": "supplier", "client_request_id": "session-" + physical[0]["piece_id"]})
    assert response.status_code == 201, response.text
    session = response.json()["session"]
    for piece in physical:
        response = await http.post(f'/supplier-receiving-v1/sessions/{session["id"]}/scan', json={
            "barcode": piece["barcode"], "client_request_id": "scan-" + piece["piece_id"]})
        assert response.status_code == 200, response.text
        assert response.json().get("requires_quantity_selection") is not True
    return session


def close_payload(physical, *, price=5000):
    return {"expected_supplier_id": "supplier", "confirmed_total_halalas": price * len(physical),
        "invoice_lines": [{"piece_ids": [piece["piece_id"] for piece in physical],
                           "product_unit_price_halalas": price, "services": []}]}


async def mark_ready(db):
    await db[bridge.PIECES].update_many({"user_id": OWNER, "supplier_dispatch_status": "received"}, {"$set": {
        "status": "ready_for_assembly", "assembly_status": "ready", "assembly_ready_basis": ready.POLICY,
        "preparation_receipt_status": "received", "preparation_received_at": NOW,
        "preparation_received_by": OWNER}})
    return await db[bridge.PIECES].find({"user_id": OWNER}).to_list(10)


def test_materialization_keeps_one_commercial_unit_and_two_frozen_printable_pieces():
    unit = bridge.prepare_supplier_leaf_unit(unit_fixture())
    template = {**template_fixture(), "options_normalized": [{"name": "Offer", "value": "Pair"}],
        "product_options_snapshot": {"Offer": "Pair"}, "size": "parent-size",
        "receipt_event_id": "old-receipt", "received_at": NOW, "assembly_status": "ready"}
    physical = bridge.build_supplier_leaf_pieces(owner=OWNER, unit=unit, template=template)
    assert len(physical) == 2 and {p["unit_index"] for p in physical} == {1}
    assert len({p["piece_id"] for p in physical}) == 2
    assert physical == bridge.build_supplier_leaf_pieces(owner=OWNER, unit=unit, template=template)
    assert {p[bridge.REFERENCE_FIELD]["leaf_id"] for p in physical} == {"leaf-batman", "leaf-catwoman"}
    for piece in physical:
        assert not piece.get("options_normalized") and "size" not in piece
        assert "receipt_event_id" not in piece and "received_at" not in piece
        assert piece["assembly_status"] == "pending"
        assert all(pair["name"] != "Offer" for pair in piece["service_specifications_snapshot"])
        assert len(piece["service_specifications_snapshot"]) == 2
    dispatch._hydrate_piece_print_facts_from_batches(physical, [{"id": "synthetic-batch", "lines": [{
        "order_item_id": LINE, "shipping_company": "Carrier", "product_options": {"Offer": "Pair"},
        "service_spec_fields": [{"name": "Offer", "value": "Pair"}]}]}])
    assert all(piece["product_options_snapshot"] == {} for piece in physical)
    assert all(piece["shipping_company"] == "Carrier" for piece in physical)


@pytest.mark.parametrize("change", [
    {"state": "consumed"}, {"allocations": [{"lot_id": "stock"}]},
    {"resource_demands": [{"resource_id": "extra"}]}, {"prebuilt": {"id": "stock"}},
])
def test_materialization_rejects_consumed_or_stock_backed_units(change):
    with pytest.raises(HTTPException):
        bridge.prepare_supplier_leaf_unit({**unit_fixture(), **change})


@pytest.mark.asyncio
async def test_v2_unique_index_preserves_ordinary_collisions_and_accepts_exact_leaf_dimensions(env):
    db, _ = env
    keys = [(field, 1) for field in ("user_id", "batch_id", "order_number", "order_item_id", "unit_index")]
    await db[bridge.PIECES].create_index(keys, unique=True, name="uq_preparation_piece_physical_unit_v1")
    ordinary = {**template_fixture(), "order_number": NUMBER, "order_item_id": LINE, "unit_index": 1}
    await db[bridge.PIECES].insert_one(deepcopy(ordinary))
    await bridge.ensure_supplier_piece_identity_index(db)
    await bridge.ensure_supplier_piece_identity_index(db)
    with pytest.raises(DuplicateKeyError):
        await db[bridge.PIECES].insert_one({**ordinary, "piece_id": "ordinary-duplicate"})
    physical = bridge.build_supplier_leaf_pieces(owner=OWNER,
        unit=bridge.prepare_supplier_leaf_unit(unit_fixture()), template=template_fixture())
    await db[bridge.PIECES].insert_many(deepcopy(physical))
    with pytest.raises(DuplicateKeyError):
        await db[bridge.PIECES].insert_one({**physical[0], "piece_id": "leaf-duplicate"})
    indexes = await db[bridge.PIECES].index_information()
    assert "uq_preparation_piece_physical_unit_v1" not in indexes
    assert indexes["uq_preparation_piece_physical_unit_v2"]["unique"] is True


@pytest.mark.asyncio
async def test_index_migration_keeps_unexpected_legacy_constraint(env):
    db, _ = env
    await db[bridge.PIECES].create_index([("piece_id", 1)], unique=True, name="uq_preparation_piece_physical_unit_v1")
    with pytest.raises(HTTPException) as exc:
        await bridge.ensure_supplier_piece_identity_index(db)
    assert exc.value.detail["code"] == "bundle_supplier_legacy_identity_index_unexpected"
    assert "uq_preparation_piece_physical_unit_v1" in await db[bridge.PIECES].index_information()


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["owner", "fingerprint", "leaf_id", "index_bool", "version_bool", "unit_bool", "specifications", "retired"])
async def test_leaf_reference_cannot_bypass_ordinary_guards(env, tamper):
    db, _ = env
    _, _, physical, _, _ = await seed_bundle(db)
    piece = deepcopy(physical[0])
    if tamper == "owner": piece["user_id"] = "foreign-owner"
    elif tamper == "fingerprint": piece[bridge.REFERENCE_FIELD]["leaf_fingerprint"] = "0" * 64
    elif tamper == "leaf_id": piece[bridge.REFERENCE_FIELD]["leaf_id"] = "invented"
    elif tamper == "index_bool": piece[bridge.REFERENCE_FIELD]["leaf_unit_index"] = True
    elif tamper == "version_bool": piece[bridge.REFERENCE_FIELD]["version"] = True
    elif tamper == "unit_bool": piece["unit_index"] = True
    elif tamper == "specifications": piece["bundle_supplier_specifications"] = {"other": "XXL"}
    else: piece["bundle_supplier_retired"] = True
    with pytest.raises(HTTPException):
        await bridge.validate_supplier_leaf_piece(db, OWNER, piece)


@pytest.mark.asyncio
async def test_public_pair_scan_close_concurrency_one_payable_and_actual_100(env, http):
    db, commands = env
    await identities(db); await mapping(db)
    unit, snapshot, physical, source, workflow = await seed_bundle(db)
    session = await open_and_scan(http, physical)
    payload = close_payload(physical)
    path = f'/supplier-receiving-v1/sessions/{session["id"]}/close'
    first, retry = await asyncio.gather(http.post(path, json=payload), http.post(path, json=payload))
    assert first.status_code == 200, first.text
    assert retry.status_code == 200, retry.text
    assert sorted([first.json().get("idempotent", False), retry.json().get("idempotent", False)]) == [False, True]
    invoice = first.json()["supplier_invoice"]
    assert invoice["piece_count"] == 2 and invoice["total_halalas"] == 10000
    assert sum(line["quantity"] for line in invoice["lines"]) == 2
    assert await db[bridge.INVOICES].count_documents({"user_id": OWNER}) == 1
    assert len(await legs(db)) == 2
    assert await db.mezan_purchase_invoices_v2.count_documents({}) == 0
    assert await db.mezan_inventory_receipts_v2.count_documents({}) == 0
    refreshed = await db[bridge.UNITS].find_one({"_id": unit["_id"]})
    assert all(leaf["cost_policy"] == "posted_supplier_invoice" for leaf in refreshed["bundle_leaves"])
    assert sum(Decimal(leaf["actual_cost"]) for leaf in refreshed["bundle_leaves"]) == Decimal(100)
    ready_pieces = await mark_ready(db)
    assert await ready.supplier_completion_covered_current(db, source, workflow, ready_pieces, owner=OWNER, number=NUMBER)
    result = await stock.consume_component_stock(db, merchant_id=OWNER, order_id=NUMBER, units={LINE: [1]}, actor_id=OWNER)
    repeated = await stock.consume_component_stock(db, merchant_id=OWNER, order_id=NUMBER, units={LINE: [1]}, actor_id=OWNER)
    assert repeated["duplicate"] is True
    consumed = await db[bridge.UNITS].find_one({"_id": unit["_id"]})
    assert consumed["state"] == "consumed"
    cost = _bundle_cost(snapshot, 1, [consumed])
    assert cost["line_total"] == 100 and cost["cost_stage"] == "actual"
    assert len(await legs(db)) == 2


@pytest.mark.asyncio
async def test_partial_receipt_never_completes_parent_and_second_receipt_costs_both(env, http):
    db, _ = env
    await identities(db); await mapping(db)
    unit, snapshot, physical, source, workflow = await seed_bundle(db)
    first = await open_and_scan(http, physical[:1])
    response = await http.post(f'/supplier-receiving-v1/sessions/{first["id"]}/close', json=close_payload(physical[:1]))
    assert response.status_code == 200, response.text
    ready_pieces = await mark_ready(db)
    assert not await ready.supplier_completion_covered_current(db, source, workflow, ready_pieces, owner=OWNER, number=NUMBER)
    current = await db[bridge.UNITS].find_one({"_id": unit["_id"]})
    with pytest.raises(HTTPException):
        await bundles.capture_actual_cost(db, OWNER, current)
    assert _bundle_cost(snapshot, 1, [current])["cost_stage"] == "expected"
    second = await open_and_scan(http, physical[1:])
    response = await http.post(f'/supplier-receiving-v1/sessions/{second["id"]}/close', json=close_payload(physical[1:]))
    assert response.status_code == 200, response.text
    ready_pieces = await mark_ready(db)
    assert await ready.supplier_completion_covered_current(db, source, workflow, ready_pieces, owner=OWNER, number=NUMBER)
    await db[bridge.PIECES].update_one({"piece_id": physical[1]["piece_id"]}, {"$set": {"active_hold_id": "hold"}})
    ready_pieces = await db[bridge.PIECES].find({"user_id": OWNER}).to_list(10)
    assert not await ready.supplier_completion_covered_current(db, source, workflow, ready_pieces, owner=OWNER, number=NUMBER)


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["event_product", "event_variant", "event_specs", "event_supplier"])
async def test_ledger_valid_invoice_with_wrong_receipt_source_is_rejected(env, http, tamper):
    db, _ = env
    await identities(db); await mapping(db)
    unit, _, physical, _, _ = await seed_bundle(db)
    session = await open_and_scan(http, physical)
    response = await http.post(f'/supplier-receiving-v1/sessions/{session["id"]}/close', json=close_payload(physical))
    assert response.status_code == 200, response.text
    field, value = {"event_product": ("product_id", "wrong-product"),
        "event_variant": ("variant_id", "wrong-variant"),
        "event_specs": ("bundle_supplier_specifications", {"wrong": "size"}),
        "event_supplier": ("supplier_context.id", "wrong-supplier")}[tamper]
    await db[bridge.EVENTS].update_one({"piece_id": physical[0]["piece_id"],
        "event_type": "supplier_piece_service_recorded"}, {"$set": {field: value}})
    with pytest.raises(HTTPException) as exc:
        await bridge.supplier_leaf_receipts(db, OWNER, unit, unit["bundle_leaves"][0])
    assert exc.value.detail["code"] == "bundle_supplier_receipt_evidence_mismatch"

