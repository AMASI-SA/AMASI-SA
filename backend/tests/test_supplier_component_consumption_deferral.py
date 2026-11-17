"""Deterministic stock effects; real transaction rollback remains a CI gate.

The in-memory owner callback below tests preflight/write ordering only. It does
not emulate or claim Mongo transaction rollback or concurrent-writer safety.
"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

import fulfillment_v2_routes as fulfillment
import stock_component_consumption_service as stock


OWNER = "supplier-stock-owner"
ORDER = "supplier-stock-order"
PLAN = stock._id("component_plan", OWNER, ORDER)
FIXED_TIME = "2026-11-18T12:00:00+00:00"


class TrackedCollection:
    def __init__(self, database, name):
        self.database = database
        self.name = name
        self.collection = database.raw[name]

    def __getattr__(self, name):
        if name in {"insert_one", "insert_many", "update_many", "replace_one", "delete_one", "delete_many", "find_one_and_update"}:
            async def tracked_write(*args, **kwargs):
                self.database.writes.append((self.name, name, deepcopy((args, kwargs))))
                return await getattr(self.collection, name)(*args, **kwargs)
            return tracked_write
        return getattr(self.collection, name)

    async def update_one(self, selector, update, **kwargs):
        if self.database.before_update:
            result = self.database.before_update(self.name, selector, update)
            if result is not None:
                return result
        result = await self.collection.update_one(selector, update, **kwargs)
        self.database.writes.append((self.name, deepcopy(selector), deepcopy(update)))
        if self.database.after_update:
            self.database.after_update(self.name, selector, update)
        return result


class TrackedDatabase:
    def __init__(self, raw):
        self.raw = raw
        self.writes = []
        self.before_update = None
        self.after_update = None

    def __getitem__(self, name):
        return TrackedCollection(self, name)

    def __getattr__(self, name):
        return self[name]


@pytest.fixture
def database(monkeypatch):
    client = AsyncMongoMockClient()
    db = TrackedDatabase(client["supplier_stock_deferral_unit"])

    async def invoke_owner_callback(database, owner, callback):
        assert owner == OWNER
        return await callback(database)

    monkeypatch.setattr(stock, "operational_owner", invoke_owner_callback)
    monkeypatch.setattr(stock, "_now", lambda: FIXED_TIME)
    yield db
    client.close()


async def seed(db, *, locations=1):
    await db.raw[stock.PLANS].insert_one({
        "_id": PLAN, "user_id": OWNER, "order_id": ORDER, "state": "accepted",
        "source_version": {"kind": "revision", "value": 7},
    })
    await db.raw[fulfillment.COMPONENT_LIFECYCLES].insert_one({
        "user_id": OWNER, "order_number": ORDER, "state": "reserved",
        "accepted": True, "generation": 7, "snapshot_revision": 7,
    })
    await db.raw.unified_orders.insert_one({
        "user_id": OWNER, "order_number": ORDER,
        "g47_salla_snapshot": {"revision": 7, "component_pending": False},
    })
    allocations = []
    for index in range(locations):
        location, lot = f"loc-{index}", f"lot-{index}"
        item = {"item_type": "stock_component", "resource_id": "component",
                "receipt_id": lot, "quantity": 10, "configuration_key": "component-config"}
        await db.raw[stock.LOCATIONS].insert_one({
            "user_id": OWNER, "id": location, "warehouse_id": "warehouse", "state": "occupied",
            "occupancy": {"items": [item], "total_quantity": 10},
        })
        await db.raw[stock.RECEIPTS].insert_one({
            "id": lot, "user_id": OWNER, "location_id": location, "warehouse_id": "warehouse",
            "status": "posted", "source_type": "purchase_invoice", "source_id": "synthetic-purchase",
            "source_line_id": f"line-{index}", "item_type": "stock_component", "resource_id": "component",
            "quantity": 10, "configuration_key": "component-config",
        })
        allocations.append({"location_id": location, "lot_id": lot, "resource_id": "component", "quantity": "2"})
    await db.raw[stock.UNITS].insert_one({
        "_id": "selected-unit", "user_id": OWNER, "plan_id": PLAN, "order_id": ORDER,
        "order_line_id": "line", "unit_index": 1, "state": "reserved",
        "allocations": allocations, "resource_demands": [], "consumption_id": "stable-consumption",
    })


async def snapshot(db):
    return {name: await db.raw[name].find({}).sort("_id", 1).to_list(100)
            for name in (stock.LOCATIONS, stock.PLANS, stock.UNITS, stock.RECEIPTS,
                         fulfillment.COMPONENT_LIFECYCLES, "unified_orders")}


async def consume(db, **kwargs):
    return await stock.consume_component_stock(
        db, merchant_id=OWNER, order_id=ORDER, units={"line": [1]}, actor_id="supplier-worker", **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("defer_unavailable", [False, True])
async def test_healthy_consumption_and_retry_keep_existing_stock_semantics(database, defer_unavailable):
    db = database
    await seed(db, locations=2)

    first = await consume(db, defer_unavailable=defer_unavailable)
    retry = await consume(db, defer_unavailable=defer_unavailable)

    assert first.get("deferred") is not True
    assert first["duplicate"] is False
    assert retry["duplicate"] is True
    assert first["units"][0]["state"] == retry["units"][0]["state"] == "consumed"
    assert first["component_provenance"]["units"] == [{
        "order_line_id": "line", "unit_index": 1, "consumption_id": "stable-consumption",
    }]
    for location in await db.raw[stock.LOCATIONS].find({}).to_list(10):
        assert location["occupancy"]["items"][0]["quantity"] == 8
        assert location["occupancy"]["total_quantity"] == 8
    unit_writes = [row for row in db.writes if row[0] == stock.UNITS]
    assert len(unit_writes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case, reason", [
    ("missing_plan", "component_reservation_missing"),
    ("cancelled_plan", "component_order_cancelled"),
    ("stale_acceptance", "component_execution_blocked"),
    ("unaccepted_lifecycle", "component_execution_blocked"),
    ("retry_required", "component_execution_blocked"),
    ("source_pending", "component_execution_blocked"),
    ("source_refresh_required", "component_execution_blocked"),
    ("unknown_unit", "component_unit_selection_unknown"),
    ("released_unit", "component_unit_released"),
    ("insufficient_stock", "component_reservation_exceeds_stock"),
    ("pending_receipt", "component_stock_ineligible_reconciliation_required"),
    ("bundle_incomplete", "bundle_components_incomplete"),
])
async def test_unavailable_component_business_evidence_defers_before_any_stock_write(database, case, reason):
    db = database
    await seed(db)
    if case == "missing_plan":
        await db.raw[stock.PLANS].delete_many({})
    elif case == "cancelled_plan":
        await db.raw[stock.PLANS].update_one({}, {"$set": {"state": "cancelled"}})
    elif case == "stale_acceptance":
        await db.raw[fulfillment.COMPONENT_LIFECYCLES].update_one({}, {"$set": {"generation": 99}})
    elif case == "unaccepted_lifecycle":
        await db.raw[fulfillment.COMPONENT_LIFECYCLES].update_one({}, {"$set": {"accepted": False}})
    elif case == "retry_required":
        await db.raw[fulfillment.COMPONENT_LIFECYCLES].update_one({}, {"$set": {"retry_required": True}})
    elif case == "source_pending":
        await db.raw.unified_orders.update_one({}, {"$set": {"g47_salla_snapshot.component_pending": True}})
    elif case == "source_refresh_required":
        await db.raw.unified_orders.update_one({}, {"$set": {"g47_salla_snapshot.requires_authoritative_refresh": True}})
    elif case == "unknown_unit":
        await db.raw[stock.UNITS].delete_many({})
    elif case == "released_unit":
        await db.raw[stock.UNITS].update_one({}, {"$set": {"state": "released"}})
    elif case == "insufficient_stock":
        await db.raw[stock.LOCATIONS].update_one({}, {"$set": {
            "occupancy.items.0.quantity": 1, "occupancy.total_quantity": 1,
        }})
    elif case == "pending_receipt":
        await db.raw[stock.RECEIPTS].update_one({}, {"$set": {"status": "pending"}})
    else:
        await db.raw[stock.UNITS].update_one({}, {"$set": {"bundle_leaves": [
            {"kind": "resource", "resource_id": "component", "sourcing_mode": "supplier",
             "required": True, "state": "awaiting_supplier"},
        ]}})
    before = await snapshot(db)

    result = await consume(db, defer_unavailable=True)

    assert result == {"deferred": True, "reason": reason}
    assert db.writes == []
    assert await snapshot(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("approved, current", [
    (6, 7), (7, 8), (None, 7), (7, None),
    ("7", 7), (7, "7"), (7.0, 7), (7, 7.0), (True, 1), (1, True),
])
async def test_optional_consumption_defers_unproven_source_revision_before_any_write(database, approved, current):
    db = database
    await seed(db)
    await db.raw[fulfillment.COMPONENT_LIFECYCLES].update_one({},
        {"$unset": {"snapshot_revision": ""}} if approved is None else {"$set": {"snapshot_revision": approved}})
    await db.raw.unified_orders.update_one({},
        {"$unset": {"g47_salla_snapshot.revision": ""}} if current is None else {"$set": {"g47_salla_snapshot.revision": current}})
    before = await snapshot(db)

    result = await consume(db, defer_unavailable=True)

    assert result == {"deferred": True, "reason": "component_source_snapshot_stale"}
    assert db.writes == []
    assert await snapshot(db) == before


@pytest.mark.asyncio
async def test_default_consumption_does_not_add_supplier_revision_preflight(database):
    db = database
    await seed(db)
    await db.raw[fulfillment.COMPONENT_LIFECYCLES].update_one({}, {"$unset": {"snapshot_revision": ""}})

    result = await consume(db)

    assert result["duplicate"] is False
    assert result["units"][0]["state"] == "consumed"
    assert (await db.raw[stock.LOCATIONS].find_one({}))["occupancy"]["total_quantity"] == 8


@pytest.mark.asyncio
async def test_second_location_validation_failure_does_not_pin_or_deduct_first(database):
    db = database
    await seed(db, locations=2)
    # Availability uses the physical lots. The later deduction validation must
    # reject this inconsistent aggregate before touching the first valid lot.
    await db.raw[stock.LOCATIONS].update_one({"id": "loc-1"}, {"$set": {"occupancy.total_quantity": 999}})
    before = await snapshot(db)

    result = await consume(db, defer_unavailable=True)

    assert result == {"deferred": True, "reason": "component_stock_total_invalid"}
    assert db.writes == []
    assert await snapshot(db) == before


@pytest.mark.asyncio
async def test_default_mode_keeps_business_conflicts_as_errors(database):
    db = database
    await seed(db)
    await db.raw[stock.PLANS].delete_many({})
    with pytest.raises(HTTPException) as error:
        await consume(db)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "component_reservation_missing"
    assert db.writes == []


@pytest.mark.asyncio
async def test_default_mode_retains_caller_owned_component_acceptance(database):
    db = database
    await seed(db)
    await db.raw[fulfillment.COMPONENT_LIFECYCLES].delete_many({})
    # Existing callers own this check; the new optional path adds its own
    # preflight without silently changing the default service API contract.
    result = await consume(db)
    assert result["units"][0]["state"] == "consumed"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    HTTPException(403, detail={"code": "component_execution_blocked"}),
    HTTPException(409, detail={"code": "unrelated_business_conflict"}),
    HTTPException(503, detail={"code": "component_execution_blocked"}),
    RuntimeError("synthetic unexpected read failure"),
])
async def test_unexpected_preflight_errors_are_never_reported_as_deferral(database, monkeypatch, error):
    db = database
    await seed(db)
    monkeypatch.setattr(fulfillment, "assert_component_execution", AsyncMock(side_effect=error))
    before = await snapshot(db)
    with pytest.raises(type(error)) as raised:
        await consume(db, defer_unavailable=True)
    assert raised.value is error
    assert db.writes == []
    assert await snapshot(db) == before


@pytest.mark.asyncio
async def test_healthy_bundle_actual_cost_is_captured_before_consumption(database):
    from purchase_receiving_service import COST_POLICY, COST_STATES, identity_key
    db = database
    await seed(db)
    await db.raw[stock.UNITS].update_one({}, {"$set": {"bundle_leaves": [{
        "kind": "resource", "resource_id": "component", "sourcing_mode": "stock",
        "quantity": "2", "reserved_quantity": "2", "state": "reserved", "required": True,
    }]}})
    await db.raw[COST_STATES].insert_one({
        "_id": identity_key(OWNER, {"item_type": "stock_component", "resource_id": "component"}),
        "user_id": OWNER, "authoritative": True, "cost_policy_version": COST_POLICY, "average_cost": 7,
    })

    result = await consume(db, defer_unavailable=True)

    assert result.get("deferred") is not True
    saved = await db.raw[stock.UNITS].find_one({})
    assert saved["bundle_leaves"][0]["actual_cost"] == "14"
    assert saved["bundle_leaves"][0]["state"] == "consumed"


@pytest.mark.asyncio
async def test_second_deduction_cas_failure_escapes_for_real_owner_rollback(database):
    db = database
    await seed(db, locations=2)

    def fail_second_deduction(name, selector, update):
        if name == stock.LOCATIONS and selector.get("id") == "loc-1" and "occupancy" in update.get("$set", {}):
            return SimpleNamespace(matched_count=0)

    db.before_update = fail_second_deduction
    with pytest.raises(HTTPException) as error:
        await consume(db, defer_unavailable=True)

    assert error.value.detail["code"] == "component_stock_conflict"
    assert any(name == stock.LOCATIONS and "occupancy" in update["$set"] for name, _, update in db.writes)
    assert not any(name == stock.UNITS for name, _, _ in db.writes)


@pytest.mark.asyncio
async def test_even_allowlisted_failure_after_first_write_is_not_caught(database):
    db = database
    await seed(db)
    failure = HTTPException(409, detail={"code": "component_insufficient_stock"})

    def fail_after_pin(_name, _selector, _update):
        raise failure

    db.after_update = fail_after_pin
    with pytest.raises(HTTPException) as error:
        await consume(db, defer_unavailable=True)
    assert error.value is failure
    assert len(db.writes) == 1
    assert db.writes[0][0] == stock.LOCATIONS
    assert "occupancy" not in db.writes[0][2]["$set"]
