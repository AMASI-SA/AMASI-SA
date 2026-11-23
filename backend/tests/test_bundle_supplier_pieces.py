"""Physical bundle leaves use the existing supplier invoice, never fake stock.

All financial integration evidence is synthetic and confined to an isolated
loopback replica set. The real session/scan/close routes and native ledger run.
"""
import asyncio
from copy import deepcopy
from decimal import Decimal

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError, OperationFailure
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
from order_cost_authority import _bundle_cost, load_order_cost_authority
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
    source = {"user_id": OWNER, "order_number": NUMBER, "order_date": "2026-11-18T12:00:00+00:00", "order_status": "in_progress",
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


async def report_cost(db, source):
    order = map_salla_order(source["raw_by_source"]["salla_direct"]).model_dump(mode="json")
    authority = await load_order_cost_authority(db, OWNER, [order], current_profitability_estimate=False)
    return authority["lines"][(NUMBER, LINE)]["cost"]


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


def test_old_frozen_slots_use_generic_labels_and_ordinary_print_barcode_is_unchanged():
    from uuid import uuid5, NAMESPACE_URL
    from reviewed_preparation_batches import _line_from_batch_storage
    unit = unit_fixture()
    for leaf in unit["bundle_leaves"]:
        leaf.pop("customization_labels")
    physical = bridge.build_supplier_leaf_pieces(owner=OWNER,
        unit=bridge.prepare_supplier_leaf_unit(unit), template=template_fixture())
    assert all(pair["name"] == "اختيار القطعة" for piece in physical for pair in piece["options_raw"])
    line = {"order_number": NUMBER, "order_item_id": LINE, "unit_indices": [1], "quantity": 1}
    ordinary = _line_from_batch_storage(line, {"user_id": OWNER, "id": "batch"})
    expected = uuid5(NAMESPACE_URL, f"mezan-piece-v2:{OWNER}:{NUMBER}:{LINE}:1").hex
    assert ordinary.barcode_payload == "MEZAN-PIECE:" + expected


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
async def test_index_migration_failure_keeps_the_valid_legacy_unique_index(env):
    db, _ = env
    keys = [(field, 1) for field in ("user_id", "batch_id", "order_number", "order_item_id", "unit_index")]
    await db[bridge.PIECES].create_index(keys, unique=True, name="uq_preparation_piece_physical_unit_v1")
    await db[bridge.PIECES].create_index([*keys, (bridge.REFERENCE_FIELD + ".leaf_id", 1),
        (bridge.REFERENCE_FIELD + ".leaf_unit_index", 1)], name="uq_preparation_piece_physical_unit_v2")
    with pytest.raises(OperationFailure):
        await bridge.ensure_supplier_piece_identity_index(db)
    assert (await db[bridge.PIECES].index_information())["uq_preparation_piece_physical_unit_v1"]["unique"] is True


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
    assert _bundle_cost(snapshot, 1, [consumed])["calculation_cost_available"] is False
    cost = await report_cost(db, source)
    assert cost["line_total"] == 100 and cost["cost_stage"] == "actual"
    assert len(await legs(db)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["amount_cache", "proof_digest", "missing_event", "event_specs", "invoice_status",
    "native_fields_removed", "native_contract_changed", "leaf_channel_removed", "journal_reversal"])
async def test_reporting_revalidates_consumed_supplier_proof_in_batches(env, http, tamper):
    db, commands = env
    await identities(db); await mapping(db)
    unit, _, physical, source, _ = await seed_bundle(db)
    session = await open_and_scan(http, physical)
    response = await http.post(f'/supplier-receiving-v1/sessions/{session["id"]}/close', json=close_payload(physical))
    assert response.status_code == 200, response.text
    await stock.consume_component_stock(db, merchant_id=OWNER, order_id=NUMBER, units={LINE: [1]}, actor_id=OWNER)
    assert (await report_cost(db, source))["line_total"] == 100
    invoice = await db[bridge.INVOICES].find_one({"user_id": OWNER})
    if tamper == "amount_cache":
        await db[bridge.UNITS].update_one({"_id": unit["_id"]}, {"$set": {"bundle_leaves.0.actual_cost": "500"}})
    elif tamper == "proof_digest":
        await db[bridge.UNITS].update_one({"_id": unit["_id"]}, {"$set": {"bundle_leaves.0.supplier_receipt_proofs.0.invoice_digest": "bad"}})
    elif tamper == "leaf_channel_removed":
        await db[bridge.UNITS].update_one({"_id": unit["_id"]}, {"$unset": {"bundle_leaves.0.supplier_receiving_channel": ""}})
    elif tamper == "missing_event":
        await db[bridge.EVENTS].delete_one({"piece_id": physical[0]["piece_id"], "event_type": "supplier_piece_service_recorded"})
    elif tamper == "event_specs":
        await db[bridge.EVENTS].update_one({"piece_id": physical[0]["piece_id"], "event_type": "supplier_piece_service_recorded"},
            {"$set": {"bundle_supplier_specifications": {"bad": "choice"}}})
    elif tamper == "invoice_status":
        await db[bridge.INVOICES].update_one({"id": invoice["id"]}, {"$set": {"status": "cancelled"}})
    elif tamper == "native_fields_removed":
        await db[bridge.INVOICES].update_one({"id": invoice["id"]}, {"$unset": {"mz2_financial_contract": "", "mz2_txn_group_id": ""}})
    elif tamper == "native_contract_changed":
        await db[bridge.INVOICES].update_one({"id": invoice["id"]}, {"$set": {"mz2_financial_contract": "other-contract"}})
    else:
        from accounting_ledger_v2 import GROUPS_COLLECTION
        await db[GROUPS_COLLECTION].insert_one({"user_id": OWNER, "txn_group_id": "synthetic-reversal-evidence",
            "reversal_of_txn_group_id": invoice["mz2_txn_group_id"], "status": "posted"})
    commands.collections.clear()
    cost = await report_cost(db, source)
    assert cost["cost_stage"] == "unknown" and cost["calculation_cost_available"] is False
    assert cost["missing_cost_reasons"] == ["bundle_supplier_invoice_evidence_unverified"]
    # The report reads a bounded proof set once, rather than querying per leaf.
    if tamper != "leaf_channel_removed":
        assert commands.collections.count(bridge.PIECES) == 1
        assert commands.collections.count(bridge.EVENTS) == 1
        assert commands.collections.count(bridge.INVOICES) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["product", "variant"])
async def test_ledger_valid_invoice_cannot_price_the_wrong_leaf_product_or_variant(env, case):
    from test_supplier_native_invoice_v2 import invoice as invoice_fixture, post
    from supplier_invoice_integrity import verify_persisted_supplier_invoice
    db, _ = env
    await identities(db)
    unit, _, physical, _, _ = await seed_bundle(db)
    pid = physical[0]["piece_id"]
    row = invoice_fixture()
    row.update(id="wrong-source-invoice", session_id="wrong-source-session", status="payable_posted",
        financial_invoice_created=True, liability_created=True, financial_integrity_verified=True,
        subtotal_halalas=5000, total_halalas=5000)
    row["lines"][0].update(piece_ids=[pid], product_unit_price_halalas=5000,
        product_total_halalas=5000, total_halalas=5000)
    if case == "product":
        await db.mezan_products_v2.insert_one({"user_id": OWNER, "id": "wrong-product", "mezan_product_id": "wrong-product", "status": "active"})
        await mapping(db, source_id="wrong-product")
        row["lines"][0]["product_id"] = "wrong-product"
    else:
        await mapping(db, variant="variant")
        row["lines"][0]["variant_id"] = "variant"
    await post(db, row)
    await db[receiving.SESSIONS].insert_one({"id": row["session_id"], "user_id": OWNER,
        "status": "closed", "supplier_id": "supplier", "supplier_invoice_id": row["id"]})
    await db[bridge.PIECES].update_one({"piece_id": pid}, {"$set": {
        "supplier_dispatch_status": "received", "received_at": NOW}})
    await db[bridge.EVENTS].insert_one({**deepcopy(physical[0]), "id": "wrong-source-event",
        "session_id": row["session_id"], "supplier_invoice_id": row["id"], "event_type": "supplier_piece_service_recorded",
        "finalized_at": NOW, "financial_invoice_created": True, "liability_created": True,
        "supplier_context": {"id": "supplier"}})
    await verify_persisted_supplier_invoice(db, user_id=OWNER, invoice_id=row["id"],
        session_id=row["session_id"], supplier_id="supplier")
    with pytest.raises(HTTPException) as exc:
        await bridge.supplier_leaf_receipts(db, OWNER, unit, unit["bundle_leaves"][0])
    assert exc.value.detail["code"] == "bundle_supplier_invoice_product_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["source_change", "receipt", "amendment"])
async def test_dispatched_leaf_identity_cannot_be_rewritten_without_reconciliation(env, mutation):
    db, _ = env
    unit, _, _, _, _ = await seed_bundle(db)
    before = await db[bridge.UNITS].find_one({"_id": unit["_id"]})
    plan = await db[bridge.PLANS].find_one({"_id": unit["plan_id"]})
    with pytest.raises(HTTPException) as exc:
        if mutation == "amendment":
            changed = deepcopy(plan["lines"]); changed[0]["quantity"] = 2
            await bundles.amend_order(db, OWNER, plan, changed, {"kind": "revision", "value": 8}, "changed", OWNER)
        else:
            change = {"order_line_id": LINE, "leaf_id": unit["bundle_leaves"][0]["leaf_id"]}
            change.update({"receipt_id": "fictitious-stock"} if mutation == "receipt" else {
                "sourcing_mode": "supplier", "supplier_id": "other-supplier"})
            await bundles.refresh_requirements(db, OWNER, NUMBER, actor_id=OWNER, **{mutation: change})
    assert exc.value.detail["code"] == "bundle_supplier_identity_reconciliation_required"
    assert await db[bridge.UNITS].find_one({"_id": unit["_id"]}) == before
    assert await db[bridge.PLANS].find_one({"_id": plan["_id"]}) == plan


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", [None, "method", "upsert", "foreign_owner", "cost", "extra_field", "prebuilt", "resources"])
async def test_assignment_unit_capability_is_exact_and_owner_scoped(env, tamper):
    from operational_atomic import operational_owner
    db, _ = env
    unit, _, _, _, _ = await seed_bundle(db, materialized=False)
    query = bridge.supplier_materialization_query(unit)
    patch = {"$set": {"bundle_leaves": bridge.prepare_supplier_leaf_unit(unit)["bundle_leaves"], "bundle_supplier_bridge_version": 1}}
    if tamper == "foreign_owner": query["user_id"] = "foreign-owner"
    elif tamper == "cost": patch["$set"]["bundle_leaves"][0]["unit_cost"] = "0"
    elif tamper == "extra_field": patch["$set"]["state"] = "consumed"
    elif tamper == "prebuilt": query["prebuilt"] = {"receipt_id": "stock"}
    elif tamper == "resources": query["resource_demands"] = [{"resource_id": "stock"}]
    async def mutate(tx):
        method = tx[bridge.UNITS].update_many if tamper == "method" else tx[bridge.UNITS].update_one
        return await method(query, patch, **({"upsert": True} if tamper == "upsert" else {}))
    if tamper is None:
        result = await operational_owner(db, OWNER, mutate, profile="preparation_assignment")
        assert result.matched_count == 1
        assert (await db[bridge.UNITS].find_one({"_id": unit["_id"]}))["bundle_supplier_bridge_version"] == 1
    else:
        with pytest.raises(HTTPException):
            await operational_owner(db, OWNER, mutate, profile="preparation_assignment")
        assert "bundle_supplier_bridge_version" not in await db[bridge.UNITS].find_one({"_id": unit["_id"]})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cancelled", "archived", "receipt_without_piece"])
async def test_new_intake_rejects_historical_parent_aliases(env, kind):
    db, _ = env
    await seed_bundle(db, materialized=False)
    identity = {"user_id": OWNER, "order_number": NUMBER, "order_item_id": LINE, "unit_index": 1}
    if kind == "receipt_without_piece":
        await db[bridge.EVENTS].insert_one({**identity, "event_type": "supplier_piece_service_recorded", "id": "old-receipt"})
    else:
        await db[bridge.PIECES].insert_one({**identity, "piece_id": "old-parent", "status": "cancelled",
            **({"experiment_archived_at": NOW} if kind == "archived" else {})})
    with pytest.raises(HTTPException) as exc:
        await bridge.supplier_leaf_intake_unit(db, OWNER, NUMBER, LINE, 1)
    assert exc.value.detail["code"] == "bundle_supplier_existing_piece_requires_adoption"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["assign", "defer"])
async def test_new_intake_public_assignment_prints_and_materializes_two_leaves_once(env, monkeypatch, mode):
    import httpx
    from fastapi import FastAPI
    import preparation_assignment_v2 as assignment
    import reviewed_preparation_batches as batches
    import reviewed_products_catalog as catalog
    db, _ = env
    unit, _, _, source, _ = await seed_bundle(db, materialized=False)
    source["order_status"] = source["order_status_slug"] = "reviewed"
    source["raw_by_source"]["salla_direct"]["status"] = {"slug": "reviewed"}
    await db.unified_orders.replace_one({"user_id": OWNER, "order_number": NUMBER}, source)
    await db.order_review_workflows.update_one({"user_id": OWNER, "order_number": NUMBER}, {"$set": {
        "stage": "reviewed", "reviewed_at": NOW, "revision": 1, "items": [{"order_item_id": LINE,
            "product_id": "product", "product_key": "product:product", "product_name": "Synthetic pair",
            "quantity": 1, "sku": "product", "supplier_export": True, "review_status": "reviewed"}]}})
    await db.users.update_one({"id": OWNER}, {"$set": {"email": "owner@example.test"}})
    await db[receiving.SUPPLIERS].update_one({"id": "supplier", "user_id": OWNER}, {"$set": {
        "company_name": "Synthetic supplier", "category_ids": ["hoodies"]}})
    await db[receiving.PRODUCTS].update_one({"user_id": OWNER}, {"$set": {"cost_category_id": "hoodies"}})
    async def current(): return deepcopy(ACTOR)
    async def provider(*_args, **_kwargs): return "sent", None
    async def provider_read(*_args, **_kwargs): return {"ok": True, "found": True}
    monkeypatch.setattr(pieces, "_sync_salla_in_progress", provider)
    monkeypatch.setattr(batches, "refresh_order_from_salla", provider_read)
    app = FastAPI(); app.include_router(assignment.make_preparation_assignment_v2_router(db, current))
    app.include_router(receiving.make_supplier_receiving_router(db, current))
    routes = await bridge.supplier_bundle_intake_lines(db, OWNER, [NUMBER])
    assert routes[NUMBER][LINE]["physical_quantity"] == 2
    context = await catalog.load_reviewed_product_context(db, user_id=OWNER, limit=1, order_numbers=[NUMBER])
    cards = context["catalog"]["products"]
    assert len(cards) == 1, context
    assert cards[0]["quantity"] == 1 and cards[0]["physical_quantity"] == 2
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as client:
        options = await client.get("/preparation-assignment-v2/options")
        assert options.status_code == 200, options.text
        command = {"client_request_id": "synthetic-bundle-assignment", "file_title": "Synthetic bundle",
            "responsible_employee_id": OWNER, "expected_quantity": 1, "selected_product_count": 1,
            "schedule_mode": "automatic", "required_due_at": None,
            "selections": [{"group_key": cards[0]["group_key"], "quantity": 1, "revision": cards[0]["revision"], "order_number": NUMBER}],
            "supplier_assignment": {"mode": mode, **({"supplier_id": "supplier"} if mode == "assign" else {})},
            "expected_session_scope": options.json()["session_scope"]}
        response = await client.post("/preparation-assignment-v2/requests", json=command)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "COMMITTED", response.text
        receipt = response.json()["receipt"]
        assert receipt["commercial_quantity"] == receipt["batch"]["allocated_quantity"] == 1
        assert receipt["physical_quantity"] == receipt["piece_count"] == 2
        assert len(receipt["piece_ids"]) == 2
        assert await db[catalog.PREPARATION_UNIT_ALLOCATIONS].count_documents({}) == 1
        assert await db[bridge.UNITS].count_documents({}) == 1
        batch = await db[batches.BATCHES].find_one({"user_id": OWNER})
        printable = [batches._line_from_batch_storage(row, batch) for row in batch["physical_print_lines"]]
        assert {row.barcode_payload for row in printable} == {"MEZAN-PIECE:" + pid for pid in receipt["piece_ids"]}
        assert batches.render_preparation_batch_pdf(batch).startswith(b"%PDF")
        for printed in printable:
            assert printed.quantity == 1 and len(printed.file_spec_fields) == 2
        replay = await client.post("/preparation-assignment-v2/requests", json=command)
        assert replay.status_code == 200 and replay.json() == response.json()
        assert await db[bridge.PIECES].count_documents({}) == 2
        if mode == "assign":
            physical = await db[bridge.PIECES].find({"user_id": OWNER}).to_list(3)
            await open_and_scan(client, physical)


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
async def test_mixed_supplier_pair_and_another_ready_bundle_use_existing_coverage(env, http):
    db, _ = env
    await identities(db); await mapping(db)
    unit, _, physical, source, workflow = await seed_bundle(db)
    session = await open_and_scan(http, physical)
    response = await http.post(f'/supplier-receiving-v1/sessions/{session["id"]}/close', json=close_payload(physical))
    assert response.status_code == 200, response.text
    physical = await mark_ready(db)
    other_line = f"salla:{NUMBER}:other"
    source["raw_by_source"]["salla_direct"]["items"].append({"id": "other", "quantity": 1, "product": {"id": "other-bundle"}})
    await db.unified_orders.replace_one({"user_id": OWNER, "order_number": NUMBER}, deepcopy(source))
    lines = stock._lines(_component_order_lines(map_salla_order(source["raw_by_source"]["salla_direct"]), {}))
    leaf = {"leaf_id": "other-stock-leaf", "kind": "resource", "resource_id": "ribbon", "sourcing_mode": "stock",
        "required": True, "quantity": "1", "unit_cost": "5"}
    other_snapshot = {"product_id": "other-bundle", "revision": 1, "leaves": [leaf], "estimated_cost": "5"}
    await db[bridge.PLANS].update_one({"_id": unit["plan_id"]}, {"$set": {
        "lines": lines, "bundle_snapshots." + other_line: other_snapshot}})
    await db[bridge.UNITS].insert_one({"_id": "other-stock-unit", "user_id": OWNER, "plan_id": unit["plan_id"],
        "order_id": NUMBER, "order_line_id": other_line, "unit_index": 1, "product_id": "other-bundle",
        "variant_id": None, "selected_context": [], "bundle_revision": 1, "state": "reserved", "allocations": [],
        "bundle_leaves": [{**leaf, "state": "reserved", "reserved_quantity": "1", "consumed_quantity": "0"}]})
    workflow["fulfillment_decision"]["lines"].append({"order_item_id": other_line, "bundle": True})
    assert await ready.supplier_completion_covered_current(db, source, workflow, physical, owner=OWNER, number=NUMBER)
    await db[bridge.UNITS].update_one({"_id": "other-stock-unit"}, {"$set": {"bundle_leaves.0.state": "shortage"}})
    assert not await ready.supplier_completion_covered_current(db, source, workflow, physical, owner=OWNER, number=NUMBER)


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

