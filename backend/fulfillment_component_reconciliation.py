"""PR2 internal recipe/allocation reconciliation under an existing PR1 hold.

No public route, commercial item writer, stock deduction/restock or provider IO.
The active legacy unit slot is retained; immutable audit stores prior generations.
Execution stays blocked until a later approved activation/fulfillment contract.
"""
from copy import deepcopy
from datetime import datetime, timezone
import os

from fastapi import HTTPException

import fulfillment_lifecycle as controls
import stock_component_consumption_service as stock
from operational_atomic import operational_owner

FLAG = "ORDER_FULFILLMENT_COMPONENT_RECONCILIATION_ENABLED"
LIFECYCLES = "mezan_component_order_lifecycle_v1"
PREPARATION_ALLOCATIONS = "mezan_preparation_unit_allocations_v2"
MAX_TARGETS = 250


def enabled():
    return controls.enabled() and os.environ.get(FLAG, "") == "true"


def fail(code, status=409, **details):
    raise HTTPException(status, detail={"code": "component_reconciliation_" + code, **details})


def _actor(context, owner):
    if not controls._authorized(context, owner):
        fail("permission_required", 403)


def _order(number):
    if not isinstance(number, str) or not number.strip() or number != number.strip() or len(number) > 180:
        fail("order_identity_invalid", 422)


def _targets(units):
    if not isinstance(units, list) or not 1 <= len(units) <= MAX_TARGETS:
        fail("targets_invalid", 422)
    seen, result = set(), []
    for row in units:
        if not isinstance(row, dict) or set(row) != {"order_item_id", "unit_index", "generation"}:
            fail("unit_identity_required", 422)
        line, index, generation = row["order_item_id"], row["unit_index"], row["generation"]
        if (not isinstance(line, str) or not line.strip() or line != line.strip()
                or type(index) is not int or index < 1 or type(generation) is not int or generation < 0):
            fail("unit_identity_invalid", 422)
        key = (line, index)
        if key in seen:
            fail("duplicate_unit", 422)
        seen.add(key)
        result.append(dict(row))
    return sorted(result, key=lambda row: (row["order_item_id"], row["unit_index"]))


def _identity(unit):
    return {"order_item_id": unit["order_line_id"], "unit_index": unit["unit_index"],
            "generation": int(unit.get("generation") or 0)}


def _ambiguous_consumption(unit):
    # The existing authority consumes whole units atomically. Never guess an
    # unconsumed fraction from imported/inconsistent per-allocation evidence.
    if unit.get("state") not in {"reserved", "consumed"}:
        return True
    if unit.get("state") == "reserved" and (unit.get("consumed_at") or unit.get("consumed_quantity")):
        return True
    return any(row.get("consumed_at") or row.get("consumed_quantity") or
               row.get("state") in {"consumed", "partially_consumed"}
               for row in unit.get("allocations", []))


async def _read(db, owner, number, targets):
    workflow, generation = await controls._snapshot(db, owner, number)
    if workflow.get("stage") not in {"reviewed", "in_progress", "preparation", "assembly"}:
        fail("stage_locked")
    lifecycle = await db[LIFECYCLES].find_one({"user_id": owner, "order_number": number})
    if not lifecycle:
        fail("lifecycle_missing")
    source = await db["unified_orders"].find_one({"user_id": owner, "order_number": number}) or {}
    watermark = source.get("g47_salla_snapshot") or {}
    if (watermark.get("cancelled") or watermark.get("component_pending")
            or watermark.get("requires_authoritative_refresh") or lifecycle.get("cancelled")
            or source.get("order_status_slug") in {"cancelled", "canceled"}):
        fail("source_pending")
    plan = await db[stock.PLANS].find_one({"_id": stock._id("component_plan", owner, number), "user_id": owner})
    if not plan or plan.get("state") != "accepted":
        fail("accepted_plan_required")
    if (plan.get("source_version", {}).get("kind") != "revision"
            or plan["source_version"]["value"] != lifecycle.get("generation")):
        fail("source_generation_conflict")
    if lifecycle.get("state") != "reserved" and not (
            lifecycle.get("state") == "reconciliation_required" and lifecycle.get("reconciliation_authority") == "order_change_pr2"):
        fail("source_plan_not_accepted")
    plan_lines = {row["order_line_id"]: row for row in plan.get("lines", [])}
    pieces = await stock._rows(db[controls.PIECES], {"user_id": owner, "order_number": number})
    assignments = await stock._rows(db[PREPARATION_ALLOCATIONS], {"user_id": owner, "order_number": number})
    selected = []
    for ref in targets:
        line = plan_lines.get(ref["order_item_id"])
        if not line or ref["unit_index"] > line["quantity"]:
            fail("commercial_change_not_enabled")
        matches = await db[stock.UNITS].find({"user_id": owner, "plan_id": plan["_id"],
            "order_line_id": ref["order_item_id"], "unit_index": ref["unit_index"]}).to_list(2)
        if not matches:
            fail("unit_missing")
        if len(matches) != 1:
            fail("duplicate_unit_identity")
        unit = matches[0]
        if _identity(unit) != ref:
            fail("unit_generation_conflict")
        product_id, demands = await stock._recipe(db, owner, line, include_demands=not bool(unit.get("prebuilt")))
        if product_id != unit.get("product_id") or line["selected_context"] != unit.get("selected_context"):
            fail("commercial_change_not_enabled")
        selected.append({"before": unit, "demands": demands,
            "pieces": [row for row in pieces if row.get("order_item_id") == ref["order_item_id"]
                       and row.get("unit_index") == ref["unit_index"]],
            "assignments": [row for row in assignments if row.get("order_item_id") == ref["order_item_id"]
                            and row.get("unit_index") == ref["unit_index"]]})
    recipe_hash = controls._digest({"plan": plan, "targets": targets, "selected": selected})
    return workflow, generation, lifecycle, plan, selected, recipe_hash


async def preview_reconciliation(db, *, user_id, order_number, context, units):
    """Read-only plan from frozen customer selections; no commercial payload."""
    _actor(context, user_id)
    _order(order_number)
    targets = _targets(units)
    workflow, generation, _, plan, selected, recipe_hash = await _read(db, user_id, order_number, targets)
    return {"enabled": enabled(), "units": targets, "expected_revision": int(workflow.get("revision") or 0),
            "expected_generation": generation, "expected_plan_revision": int(plan.get("reconciliation_revision") or 0),
            "expected_recipe_hash": recipe_hash,
            "changes": [{"unit": _identity(row["before"]), "old_demands": row["before"]["resource_demands"],
                         "new_demands": row["demands"], "state": row["before"]["state"]} for row in selected],
            "commercial_mutations_enabled": False, "salla_mutation_enabled": False}


def _allocate(owner, number, unit, demands, generation, available):
    allocations = []
    for demand in demands:
        remaining = stock.quantity_decimal(demand["quantity"], positive=True)
        for lot in available:
            if lot["resource_id"] != demand["resource_id"] or lot["available"] <= 0:
                continue
            taken = min(remaining, lot["available"])
            allocations.append({"id": stock._id("reconciled_allocation", owner, number,
                unit["order_line_id"], unit["unit_index"], generation, demand["binding_id"],
                demand["resource_id"], demand["selected_context_hash"], lot["location_id"], lot["lot_id"]),
                **{key: lot[key] for key in ("location_id", "lot_id", "resource_id")},
                "binding_id": demand["binding_id"], "quantity": str(taken)})
            lot["available"] -= taken
            remaining -= taken
            if not remaining:
                break
        if remaining:
            fail("insufficient_stock", resource_id=demand["resource_id"], missing_quantity=str(remaining))
    return allocations


async def reconcile_components(db, *, user_id, order_number, context, payload):
    """Replan existing reservation units atomically; keep the order held.

Commercial line identity/options/quantity remain frozen in the accepted plan.
No automatic source-plan repair, preparation reassignment or consumed reversal.
"""
    _actor(context, user_id)
    _order(order_number)
    allowed = {"units", "expected_revision", "expected_generation", "expected_plan_revision", "expected_recipe_hash",
               "reason", "idempotency_key"}
    if not isinstance(payload, dict) or set(payload) != allowed:
        fail("request_invalid", 422)
    reason, key = controls._request(payload)
    targets = _targets(payload["units"])
    if (type(payload["expected_plan_revision"]) is not int or payload["expected_plan_revision"] < 0
            or not isinstance(payload["expected_recipe_hash"], str) or len(payload["expected_recipe_hash"]) != 64):
        fail("fences_required", 422)
    fingerprint = controls._digest({"operation": "component_reconciliation", "order": order_number,
                                   "actor": context["actor_id"], "payload": payload})

    async def apply(scoped):
        replay = await controls._replay(scoped, user_id, key, fingerprint)
        if replay:
            return replay
        if not enabled():
            fail("disabled")
        await controls._fences(scoped, user_id, order_number, payload)
        hold = await scoped[controls.HOLDS].find_one({"user_id": user_id, "order_number": order_number,
            "scope": "order", "status": "active", "$or": [{"contract_version": 2},
                {"contract_version": 3, "authority": "order_change_pr2"}]})
        if not hold:
            fail("order_hold_required")
        workflow, generation, lifecycle, plan, selected, recipe_hash = await _read(scoped, user_id, order_number, targets)
        if int(plan.get("reconciliation_revision") or 0) != payload["expected_plan_revision"]:
            fail("plan_revision_conflict")
        if recipe_hash != payload["expected_recipe_hash"]:
            fail("preview_conflict")
        now = datetime.now(timezone.utc).isoformat()
        change_id = "component-reconciliation-" + controls._identity(user_id, key)
        # PR2 cannot resolve prior manual/consumption exceptions by omission.
        required, replace = deepcopy(plan.get("reconciliation_required_actions") or []), []
        for row in selected:
            unit = row["before"]
            ref = _identity(unit)
            if unit.get("prebuilt"):
                required.append({"unit": ref, "reason": "prebuilt_provenance_requires_review"})
            elif _ambiguous_consumption(unit):
                required.append({"unit": ref, "reason": "ambiguous_or_partial_consumption"})
            elif unit["state"] == "consumed":
                if unit["resource_demands"] != row["demands"]:
                    required.append({"unit": ref, "reason": "consumed_component_preserved"})
            elif unit["resource_demands"] != row["demands"]:
                replace.append(row)
                if row["assignments"] or any(p.get("status") not in {"assigned", "pending"} for p in row["pieces"]):
                    required.append({"unit": ref, "reason": "preparation_assignment_requires_review"})
        # Remove selected reservation pressure only inside this transaction.
        # An aborted allocation or audit leaves ALL original reservations intact.
        for row in replace:
            await scoped[stock.UNITS].update_one({"_id": row["before"]["_id"], "state": "reserved"},
                                                {"$set": {"state": "released"}})
        available = await stock._available(scoped, user_id, plan.get("warehouse_ids") or []) if replace else []
        after = []
        for row in replace:
            old = row["before"]
            next_generation = int(old.get("generation") or 0) + 1
            allocations = _allocate(user_id, order_number, old, row["demands"], next_generation, available)
            updated = {**old, "generation": next_generation, "state": "reserved", "resource_demands": row["demands"],
                "allocations": allocations, "reconciled_at": now, "reconciled_by": context["actor_id"],
                "reconciliation_change_id": change_id,
                "consumption_id": stock._id("component_consumption", old["_id"], next_generation, row["demands"])}
            await scoped[stock.UNITS].replace_one({"_id": old["_id"]}, updated)
            after.append(updated)
        required = list({controls._digest(action): action for action in required}.values())
        state = "reconciliation_required" if required else "reconciled_held"
        plan_revision = payload["expected_plan_revision"] + 1
        plan_patch = {"reconciliation_revision": plan_revision, "reconciliation_state": state,
                      "reconciliation_required": bool(required), "last_reconciliation_change_id": change_id,
                      "reconciliation_required_actions": required,
                      "last_reconciled_at": now}
        await scoped[stock.PLANS].update_one({"_id": plan["_id"]}, {"$set": plan_patch})
        lifecycle_patch = {"state": "reconciliation_required", "accepted": False,
                           "reconciliation_authority": "order_change_pr2", "reconciliation_change_id": change_id}
        await scoped[LIFECYCLES].update_one({"_id": lifecycle["_id"]}, {"$set": lifecycle_patch})
        # Source reconciliation may legitimately rewrite lifecycle.state. Keep
        # a durable independent barrier in the existing PR1 hold contract.
        # PR1 enforces all active holds but only resumes version2: this internal
        # version3 hold requires a future approved activation contract.
        barrier_id = "component-reconciliation-hold-" + controls._identity(user_id, order_number)
        barrier = await scoped[controls.HOLDS].find_one({"user_id": user_id, "id": barrier_id})
        if barrier and (barrier.get("status") != "active" or barrier.get("authority") != "order_change_pr2"
                        or barrier.get("contract_version") != 3 or barrier.get("scope") != "order"
                        or barrier.get("order_number") != order_number or barrier.get("target_id") != order_number):
            fail("activation_barrier_conflict")
        if not barrier:
            await scoped[controls.HOLDS].insert_one({"_id": barrier_id, "id": barrier_id, "user_id": user_id,
                "order_number": order_number, "scope": "order", "target_id": order_number,
                "status": "active", "contract_version": 3, "authority": "order_change_pr2", "stop_type": "note",
                "reason": "Component reconciliation requires approved activation", "created_at": now,
                "created_by": context["actor_id"], "created_by_name": context.get("actor_name", ""),
                "change_id": change_id, "generation": generation, "before_states": [], "piece_ids": [],
                "mezan_only": True, "salla_updated": False, "qoyod_updated": False})
        revision = int(workflow.get("revision") or 0)
        result = await scoped[controls.WORKFLOWS].update_one({"user_id": user_id, "order_number": order_number,
            "revision": revision}, {"$inc": {"revision": 1}, "$set": {
                "component_reconciliation_state": state, "component_reconciliation_change_id": change_id}})
        if result.matched_count != 1:
            fail("revision_conflict")
        result = {"ok": True, "idempotent_replay": False, "change_id": change_id, "revision": revision + 1,
            "generation": generation, "plan_revision": plan_revision, "state": state,
            "changed_units": [_identity(row) for row in after], "required_actions": required,
            "hold_retained": hold["id"], "activation_hold_id": barrier_id,
            "activation_required": True, "salla_updated": False, "accounting_updated": False}
        event = {"_id": controls._identity(user_id, key), "user_id": user_id, "event_type": "fulfillment_components_reconciled",
            "order_number": order_number, "change_id": change_id, "idempotency_key": key,
            "actor_id": context["actor_id"], "actor_name": context.get("actor_name", ""), "reason": reason,
            "occurred_at": now, "old_fulfillment_stage": workflow["stage"], "generation": generation,
            "revision": revision + 1, "plan_revision": plan_revision, "required_actions": required,
            "activation_hold_id": barrier_id,
            "affected_employees": sorted({p["responsible_employee_id"] for row in selected for p in row["pieces"]
                                          if p.get("responsible_employee_id")}),
            "before": {"plan": deepcopy(plan), "lifecycle": deepcopy(lifecycle), "workflow": deepcopy(workflow),
                       "unit_identities": [_identity(row["before"]) for row in selected],
                       "units": [deepcopy(row["before"]) for row in selected],
                       "pieces": [deepcopy(p) for row in selected for p in row["pieces"]],
                       "preparation_allocations": [deepcopy(a) for row in selected for a in row["assignments"]]},
            "after": {"plan": {**plan, **plan_patch}, "lifecycle": {**lifecycle, **lifecycle_patch},
                      "workflow": {**workflow, "revision": revision + 1, "component_reconciliation_state": state,
                                   "component_reconciliation_change_id": change_id},
                      "units": [next((new for new in after if new["_id"] == row["before"]["_id"]), row["before"]) for row in selected],
                      "pieces": [deepcopy(p) for row in selected for p in row["pieces"]],
                      "preparation_allocations": [deepcopy(a) for row in selected for a in row["assignments"]]},
            "financial_impact": "pending_contract", "salla_mutation_enabled": False}
        await scoped[controls.AUDIT].insert_one(event)
        await scoped[controls.REQUESTS].insert_one({"_id": controls._identity(user_id, key), "user_id": user_id,
                                                   "fingerprint": fingerprint, "result": result})
        return result
    return await operational_owner(db, user_id, apply)
