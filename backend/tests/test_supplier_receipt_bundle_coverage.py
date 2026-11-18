"""Mixed supplier/bundle coverage with real read-only bundle collaborators."""
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
import pytest

import bundle_fulfillment_v2 as bundles
import fulfillment_v2_routes as fulfillment
from order_engine.mapper import map_salla_order
import preparation_piece_operations as pieces
import stock_component_consumption_service as stock
import supplier_receipt_ready_policy as policy


OWNER = "supplier-bundle-owner"
NUMBER = "supplier-bundle-order"
SUPPLIER_LINE = f"salla:{NUMBER}:supplier"
BUNDLE_LINE = f"salla:{NUMBER}:bundle"
PLAN_ID = stock._id("component_plan", OWNER, NUMBER)
WHEN = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)


class ReadOnlyCollection:
    def __init__(self, database, name):
        self.database, self.name = database, name

    def __getattr__(self, method):
        if method not in {"find", "find_one", "count_documents"}:
            raise AssertionError(f"Completion proof must not write: {self.name}.{method}")

        def read(*args, **kwargs):
            self.database.reads.append((self.name, method))
            return getattr(self.database.raw[self.name], method)(*args, **kwargs)

        return read


class ReadOnlyDatabase:
    def __init__(self, raw):
        self.raw, self.reads = raw, []

    def __getitem__(self, name):
        return ReadOnlyCollection(self, name)

    def __getattr__(self, name):
        return self[name]


@pytest.fixture
def database():
    client = AsyncMongoMockClient()
    db = ReadOnlyDatabase(client["supplier_bundle_coverage_unit"])
    yield db
    client.close()


async def seed(db):
    source = {
        "user_id": OWNER, "order_number": NUMBER,
        "order_status": "in_progress", "order_status_slug": "in_progress",
        "g47_salla_snapshot": {"revision": 7, "component_pending": False},
        "raw_by_source": {"salla_direct": {
            "id": "synthetic-native-id", "reference_id": NUMBER,
            "date": "2026-11-18T12:00:00+00:00", "status": {"slug": "in_progress"},
            "shipping_address": {"city": "Riyadh", "street": "Synthetic street"},
            "items": [
                {"id": "supplier", "quantity": 1, "product": {"id": "physical-product"}},
                {"id": "bundle", "quantity": 2, "product": {"id": "bundle-root"},
                 "variant_id": "bundle-variant",
                 "options": [{"id": "color", "name": "Color", "value": {"id": "blue", "value": "Blue"}}]},
            ],
        }},
    }
    workflow = {
        "user_id": OWNER, "order_number": NUMBER, "stage": "completed",
        "assembly_status": "completed", "assembly_completion_basis": policy.POLICY,
        "items": [], "operational_items": [],
        "fulfillment_decision": {"lines": [
            {"order_item_id": SUPPLIER_LINE, "configured": True, "requires_preparation": True},
            {"order_item_id": BUNDLE_LINE, "configured": True, "bundle": True,
             "requires_preparation": False, "bundle_ready": True},
        ]},
    }
    piece = {
        "user_id": OWNER, "order_number": NUMBER, "piece_id": "received-supplier-piece",
        "order_item_id": SUPPLIER_LINE, "unit_index": 1,
        "status": "ready_for_assembly", "supplier_dispatch_status": "received", "received_at": WHEN,
        "preparation_receipt_status": "received", "preparation_received_at": WHEN,
        "preparation_received_by": "receiver", "services": [],
        "assembly_status": "ready", "assembly_ready_basis": policy.POLICY,
    }
    current = map_salla_order(source["raw_by_source"]["salla_direct"])
    normalized = stock._lines(fulfillment._component_order_lines(current, {}))
    bundle_line = next(row for row in normalized if row["order_line_id"] == BUNDLE_LINE)
    leaf = {"leaf_id": "frozen-component", "kind": "resource", "resource_id": "ribbon",
            "sourcing_mode": "stock", "quantity": "1", "required": True, "unit_cost": "5"}
    plan = {
        "_id": PLAN_ID, "user_id": OWNER, "order_id": NUMBER, "state": "accepted",
        "source_version": {"kind": "revision", "value": 7}, "lines": normalized,
        "bundle_snapshots": {BUNDLE_LINE: {
            "product_id": "bundle-root", "revision": 4, "leaves": [leaf],
            "selected_options": [], "estimated_cost": "5",
        }},
    }
    units = [{
        "_id": stock._id("component_unit", OWNER, NUMBER, BUNDLE_LINE, index),
        "user_id": OWNER, "order_id": NUMBER, "plan_id": PLAN_ID, "order_line_id": BUNDLE_LINE,
        "unit_index": index, "product_id": "bundle-root", "variant_id": bundle_line["variant_id"],
        "selected_context": deepcopy(bundle_line["selected_context"]), "state": "reserved",
        "bundle_revision": 4, "bundle_leaves": [{**leaf, "state": "reserved",
            "reserved_quantity": "1", "consumed_quantity": "0", "actual_cost": None}],
        "allocations": [],
    } for index in (1, 2)]
    await db.raw.unified_orders.insert_one(deepcopy(source))
    await db.raw[pieces.WORKFLOWS].insert_one(deepcopy(workflow))
    await db.raw[pieces.PIECES].insert_one(deepcopy(piece))
    await db.raw[stock.PLANS].insert_one(plan)
    await db.raw[stock.UNITS].insert_many(units)
    await db.raw[fulfillment.COMPONENT_LIFECYCLES].insert_one({
        "user_id": OWNER, "order_number": NUMBER, "generation": 7,
        "state": "reserved", "accepted": True,
    })
    return source, workflow, [piece]


async def stored_snapshot(db):
    return {name: await db.raw[name].find({}).sort("_id", 1).to_list(100)
            for name in ("unified_orders", pieces.WORKFLOWS, pieces.PIECES,
                         stock.PLANS, stock.UNITS, fulfillment.COMPONENT_LIFECYCLES)}


@pytest.mark.asyncio
async def test_completed_supplier_policy_covers_real_ready_bundle_parent_readonly(database):
    db = database
    source, workflow, physical = await seed(db)
    before = await stored_snapshot(db)
    assert policy.supplier_completion_covered(source, physical, owner=OWNER, number=NUMBER) is False
    assert (await bundles.routing_lines(db, OWNER, map_salla_order(
        source["raw_by_source"]["salla_direct"])))[BUNDLE_LINE]["ready"] is True

    assert await policy.completed_supplier_policy(db, OWNER, NUMBER) is True

    assert await stored_snapshot(db) == before
    assert (await db.raw[stock.UNITS].find_one({"unit_index": 1}))["state"] == "reserved"


@pytest.mark.asyncio
async def test_exact_supplier_coverage_reads_no_components_even_with_old_bundle_marker(database):
    source, workflow, physical = await seed(database)
    source["raw_by_source"]["salla_direct"]["items"].pop()
    source["g47_salla_snapshot"].update(component_pending=True, requires_authoritative_refresh=True)

    assert await policy.supplier_completion_covered_current(
        database, source, workflow, physical, owner=OWNER, number=NUMBER,
    ) is True
    assert database.reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing_marker", "ordinary_instant", "missing_supplier_unit", "supplier_hold"])
async def test_bundle_fallback_never_hides_uncovered_supplier_or_ordinary_work(database, case):
    source, workflow, physical = await seed(database)
    if case == "missing_marker":
        workflow.pop("fulfillment_decision")
    elif case == "ordinary_instant":
        workflow["fulfillment_decision"]["lines"][1].update(bundle=False, resolved_type="instant")
    elif case == "missing_supplier_unit":
        source["raw_by_source"]["salla_direct"]["items"][0]["quantity"] = 2
    else:
        physical[0]["active_hold_id"] = "stop"

    assert await policy.supplier_completion_covered_current(
        database, source, workflow, physical, owner=OWNER, number=NUMBER,
    ) is False
    assert database.reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "source_pending", "source_refresh", "unaccepted", "stale_generation", "missing_plan", "cancelled_plan",
    "plan_reconciliation", "missing_snapshot", "blocked_snapshot", "changed_quantity", "changed_product",
    "changed_variant", "changed_options", "missing_unit", "released_unit", "extra_unit", "duplicate_unit",
    "foreign_owner_unit", "foreign_order_unit", "wrong_unit_context", "wrong_unit_variant", "wrong_unit_product",
    "wrong_unit_revision", "pending_leaf", "empty_leaves", "missing_leaf", "changed_leaf_quantity",
])
async def test_bundle_requires_exact_current_accepted_complete_units(database, case):
    db = database
    await seed(db)
    if case in {"source_pending", "source_refresh"}:
        flag = "component_pending" if case == "source_pending" else "requires_authoritative_refresh"
        await db.raw.unified_orders.update_one({}, {"$set": {f"g47_salla_snapshot.{flag}": True}})
    elif case in {"unaccepted", "stale_generation"}:
        update = {"accepted": False} if case == "unaccepted" else {"generation": 8}
        await db.raw[fulfillment.COMPONENT_LIFECYCLES].update_one({}, {"$set": update})
    elif case == "missing_plan":
        await db.raw[stock.PLANS].delete_many({})
    elif case in {"cancelled_plan", "plan_reconciliation", "missing_snapshot", "blocked_snapshot"}:
        update = {
            "cancelled_plan": {"state": "cancelled"},
            "plan_reconciliation": {"reconciliation_required": True},
            "missing_snapshot": {"bundle_snapshots": {}},
            "blocked_snapshot": {f"bundle_snapshots.{BUNDLE_LINE}.operational_blocked": True},
        }[case]
        await db.raw[stock.PLANS].update_one({}, {"$set": update})
    elif case.startswith("changed_") and case != "changed_leaf_quantity":
        path, value = {
            "changed_quantity": ("quantity", 1), "changed_product": ("product.id", "another-bundle"),
            "changed_variant": ("variant_id", "another-variant"),
            "changed_options": ("options.0.value.id", "red"),
        }[case]
        await db.raw.unified_orders.update_one({}, {"$set": {f"raw_by_source.salla_direct.items.1.{path}": value}})
    elif case == "missing_unit":
        await db.raw[stock.UNITS].delete_one({"unit_index": 2})
    elif case in {"extra_unit", "duplicate_unit"}:
        extra = await db.raw[stock.UNITS].find_one({"unit_index": 2})
        extra["_id"] = "unexpected-active-unit"
        if case == "extra_unit":
            extra["unit_index"] = 3
        await db.raw[stock.UNITS].insert_one(extra)
    else:
        update = {
            "released_unit": {"state": "released"}, "foreign_owner_unit": {"user_id": "other-owner"},
            "foreign_order_unit": {"order_id": "other-order"}, "wrong_unit_context": {"selected_context": []},
            "wrong_unit_variant": {"variant_id": "other-variant"}, "wrong_unit_product": {"product_id": "other-product"},
            "wrong_unit_revision": {"bundle_revision": 5}, "pending_leaf": {"bundle_leaves.0.state": "awaiting_supplier"},
            "empty_leaves": {"bundle_leaves": []}, "missing_leaf": {"bundle_leaves.0.leaf_id": "unrelated-leaf"},
            "changed_leaf_quantity": {"bundle_leaves.0.quantity": "2"},
        }[case]
        await db.raw[stock.UNITS].update_one({"unit_index": 2}, {"$set": update})
    before = await stored_snapshot(db)

    assert await policy.completed_supplier_policy(db, OWNER, NUMBER) is False
    assert await stored_snapshot(db) == before


@pytest.mark.asyncio
async def test_consumed_bundle_and_released_historical_unit_keep_current_coverage(database):
    db = database
    await seed(db)
    for index in (1, 2):
        await db.raw[stock.UNITS].update_one({"unit_index": index}, {"$set": {
            "state": "consumed", "bundle_leaves.0.state": "consumed",
            "bundle_leaves.0.consumed_quantity": "1", "bundle_leaves.0.actual_cost": "5",
        }})
    retired = await db.raw[stock.UNITS].find_one({"unit_index": 2})
    retired.update(_id="retired-unit", unit_index=3, state="released")
    await db.raw[stock.UNITS].insert_one(retired)

    assert await policy.completed_supplier_policy(db, OWNER, NUMBER) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    HTTPException(409, detail={"code": "unexpected_component_conflict"}),
    HTTPException(403, detail={"code": "component_execution_blocked"}),
    HTTPException(503, detail={"code": "component_execution_blocked"}),
    RuntimeError("unexpected component read failure"),
])
async def test_unexpected_bundle_proof_errors_propagate(database, monkeypatch, failure):
    await seed(database)
    monkeypatch.setattr(fulfillment, "assert_component_execution", AsyncMock(side_effect=failure))
    with pytest.raises(type(failure)) as caught:
        await policy.completed_supplier_policy(database, OWNER, NUMBER)
    assert caught.value is failure


@pytest.mark.asyncio
async def test_progress_retains_physical_ready_when_real_bundle_preflight_returns_409(database, monkeypatch):
    db = database
    source, _, _ = await seed(db)
    await db.raw[pieces.WORKFLOWS].update_one({}, {
        "$set": {"stage": "in_progress", "assembly_status": "in_progress"},
        "$unset": {"assembly_completion_basis": ""},
    })
    await db.raw[fulfillment.COMPONENT_LIFECYCLES].update_one({}, {"$set": {"accepted": False}})
    current = map_salla_order(source["raw_by_source"]["salla_direct"])
    assert pieces.shipping_address_is_complete(current.shipping) is True
    before = await stored_snapshot(db)
    execution = AsyncMock(wraps=fulfillment.assert_component_execution)
    monkeypatch.setattr(fulfillment, "assert_component_execution", execution)

    result = await pieces._assembly_progress(
        db.raw, user_id=OWNER, order_number=NUMBER, actor_id="assembler", actor_name="Synthetic assembler",
        now=WHEN, eligible_order=current,
    )

    execution.assert_awaited_once()
    assert result["order_completed"] is False
    assert result["ready_count"] == result["total_count"] == 1
    assert (await db.raw[pieces.PIECES].find_one({}))["assembly_status"] == "ready"
    saved = await db.raw[pieces.WORKFLOWS].find_one({})
    assert saved["stage"] == saved["assembly_status"] == "in_progress"
    assert "assembly_delivery" not in saved
    after = await stored_snapshot(db)
    assert after[stock.PLANS] == before[stock.PLANS]
    assert after[stock.UNITS] == before[stock.UNITS]
