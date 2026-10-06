"""Apply only source-proven ADD events; no commercial or provider writer."""
from copy import deepcopy
from datetime import datetime, timezone
import os

from fastapi import HTTPException
import fulfillment_lifecycle as controls
import salla_order_change_reconciliation as source
import stock_component_consumption_service as stock
from fulfillment_component_reconciliation import _allocate
from operational_atomic import operational_owner
from order_change_hold_contract import ADD_KIND, AUTHORITY, IDENTITY_FIELDS, add_identity_matches, event_holds
from preparation_file_registry import _assignable_employees
from salla_add_preparation import build_add_preparation, MAX_BATCH_UNITS

FLAG = "ORDER_SALLA_ADD_APPLICATION_ENABLED"
APPLIED = "salla_add_applied"
BATCHES = "mezan_preparation_batches_v2"
REGISTRY = "mezan_preparation_file_registry_v2"
ALLOCATIONS = "mezan_preparation_unit_allocations_v2"


def fail(code, status=409):
    raise HTTPException(status, detail={"code": "salla_add_" + code})


def _authorize(context, owner):
    if not controls._authorized(context, owner):
        fail("permission_required", 403)


def enabled():
    return controls.enabled() and os.environ.get(source.FLAG) == "true" and os.environ.get(FLAG) == "true"


async def _component_state(db, owner, number):
    return await db["mezan_component_order_lifecycle_v1"].find_one(
        {"user_id": owner, "order_number": number}) or {}


def _expected_holds(event):
    rows = event_holds(event, existing_item_ids=set())
    if (event.get("hold_contract_version") != "pr3.1" or not rows
            or any(r.get("hold_kind") != ADD_KIND for r in rows)
            or event.get("hold_ids") != [r["id"] for r in rows]):
        fail("add_hold_contract_required")
    return rows


async def _holds(db, owner, number, event, pending):
    active = await db[controls.HOLDS].find({"user_id": owner, "order_number": number,
                                          "status": "active"}).to_list(10001)
    if len(active) > 10000:
        fail("hold_limit")
    expected = _expected_holds(event)
    own = {r["id"]: r for r in expected}
    # Other valid ADDs are independent. Everything else remains a hard guard,
    # including manual/item stops, PR2 and the old conservative order barrier.
    sibling = {}
    for row in pending:
        if row["event_id"] != event["event_id"] and row.get("change_type") == "add_product":
            for h in _expected_holds(row):
                sibling[h["id"]] = h
    found = []
    for hold in active:
        reference = own.get(hold.get("id")) or sibling.get(hold.get("id"))
        if not reference or not add_identity_matches(hold, {k: reference[k] for k in IDENTITY_FIELDS}):
            fail("other_hold_active")
        if any(hold.get(k) != reference[k] for k in ("event_id", "source_generation", "order_number")):
            fail("add_hold_contract_required")
        if hold["id"] in own:
            found.append(hold)
    if len(found) != len(expected) or {h["id"] for h in found} != set(own):
        fail("add_hold_contract_required")
    return sorted(found, key=lambda h: h["unit_index"])


async def _evidence(db, owner, number, event):
    if event.get("event_type") != source.EVENT or event.get("change_type") != "add_product" or event.get("old_data") is not None:
        fail("add_event_required")
    if event.get("intake_state") != "pending_application" or event.get("application_state") != "pending_application":
        fail("source_exception")
    workflow, generation = await controls._snapshot(db, owner, number)
    if workflow.get("stage") != "in_progress":
        fail("stage_locked")
    if workflow.get("experiment_mode") or workflow.get("experiment_run_id"):
        fail("experiment_not_supported")
    if await db[controls.EXECUTIONS].find_one({"user_id": owner, "_id": controls._identity(owner, number),
                                             "state": {"$in": ["active", "uncertain"]}}):
        fail("execution_in_flight")
    if generation != event.get("generation"):
        fail("source_generation_conflict")
    lifecycle = await _component_state(db, owner, number)
    canonical = await db["unified_orders"].find_one({"user_id": owner, "order_number": number}) or {}
    from salla_shipping import projected_shipping
    shipping = projected_shipping(canonical) or {}
    if (any(canonical.get(k) for k in ("awb_number", "salla_shipment_id", "qoyod_invoice_id", "manual_qoyod_invoice_id", "duplicate_of_invoice"))
            or any(shipping.get(k) for k in ("awb", "awb_number", "tracking_number", "shipment_id"))):
        fail("shipping_or_invoice_locked")
    if await db["qoyod_invoices"].find_one({"user_id": owner, "$or": [
            {key: number} for key in ("reference", "salla_order_number", "external_reference", "source_reference")]}):
        fail("invoice_locked")
    watermark = canonical.get("g47_salla_snapshot") or {}
    if (watermark.get("cancelled") or watermark.get("component_pending") or watermark.get("requires_authoritative_refresh")
            or lifecycle.get("cancelled") or lifecycle.get("retry_required")
            or canonical.get("order_status_slug") in {"cancelled", "canceled"}
            or lifecycle.get("state") in {"blocked", "cancelled", "reconciliation_required"}):
        fail("source_reconciliation_required")
    pending = await db[controls.AUDIT].find({"user_id": owner, "order_number": number,
        "event_type": source.EVENT}).to_list(1001)
    if len(pending) > 1000:
        fail("event_limit")
    if any(e.get("change_type") != "add_product" for e in pending):
        fail("non_add_change_requires_resolution")
    heads = await db[controls.AUDIT].find({"user_id": owner, "order_number": number,
        "event_type": source.INTAKE, "accepted": True}).sort("source_version", -1).limit(1).to_list(1)
    line = event.get("new_data") or {}
    if type(line.get("quantity")) is not int or not 1 <= line["quantity"] <= MAX_BATCH_UNITS:
        fail("quantity_limit", 422)
    if not heads or line not in heads[0]["snapshot"].get("items", []) or heads[0]["snapshot"].get("cancelled"):
        fail("source_changed")
    holds = await _holds(db, owner, number, event, pending)
    return workflow, generation, line, holds



async def pending_additions(db, *, user_id, order_number, context):
    _authorize(context, user_id)
    if not enabled():
        return {"enabled": False, "changes": [], "employees": []}
    workflow, generation = await controls._snapshot(db, user_id, order_number)
    rows = await db[controls.AUDIT].find({"user_id": user_id, "order_number": order_number,
        "event_type": source.EVENT, "change_type": "add_product"}).to_list(1001)
    if len(rows) > 1000:
        fail("event_limit")
    changes = []
    for row in rows:
        if await db[controls.AUDIT].find_one({"user_id": user_id, "event_type": APPLIED, "event_id": row["event_id"]}):
            continue
        allowed, reason = True, None
        try:
            await _evidence(db, user_id, order_number, row)
        except HTTPException as exc:
            allowed, reason = False, "حالة الطلب أو التغيير لا تسمح بالتطبيق حاليًا."
        changes.append({"event_id": row["event_id"], "change_id": row["change_id"], "new_data": row["new_data"],
            "change_type": "add_product", "application_state": "pending_application",
            "apply_allowed": allowed, "reason": reason, "expected_revision": int(workflow.get("revision") or 0),
            "expected_generation": generation})
    employees = await _assignable_employees(db, user_id=user_id, reviewer={"id": context["actor_id"]})
    return {"enabled": True, "changes": changes, "employees": employees}


async def apply_addition(db, *, user_id, order_number, context, payload):
    _authorize(context, user_id)
    if not isinstance(payload, dict) or set(payload) != {"event_id", "employee_id", "reason", "idempotency_key", "expected_revision", "expected_generation"}:
        fail("request_invalid", 422)
    reason, key = controls._request(payload)
    if any(not isinstance(payload[k], str) or not payload[k].strip() for k in ("event_id", "employee_id")):
        fail("identity_invalid", 422)
    key = "salla-add:" + key
    fingerprint = controls._digest({"order": order_number, "actor": context["actor_id"], "payload": payload})

    async def apply(scoped):
        replay = await controls._replay(scoped, user_id, key, fingerprint)
        if replay:
            return replay
        if not enabled():
            fail("disabled")
        existing = await scoped[controls.AUDIT].find_one({"user_id": user_id, "order_number": order_number,
            "event_type": APPLIED, "event_id": payload["event_id"]})
        if existing:
            return {**existing["result"], "idempotent_replay": True}
        workflow, generation = await controls._fences(scoped, user_id, order_number, payload)
        event = await scoped[controls.AUDIT].find_one({"user_id": user_id, "order_number": order_number,
            "event_type": source.EVENT, "event_id": payload["event_id"]})
        if not event:
            fail("event_missing", 404)
        workflow, generation, line, holds = await _evidence(scoped, user_id, order_number, event)
        employees = await _assignable_employees(scoped, user_id=user_id, reviewer={"id": context["actor_id"]})
        employee = next((e for e in employees if e["id"] == payload["employee_id"]), None)
        if not employee:
            fail("employee_ineligible", 403)
        plan = await scoped[stock.PLANS].find_one({"user_id": user_id, "order_id": order_number})
        lifecycle = await _component_state(scoped, user_id, order_number)
        if (not plan or plan.get("state") != "accepted"
                or not (lifecycle.get("state") == "reserved" and lifecycle.get("accepted") is True)
                or plan.get("source_version", {}).get("kind") != "revision"
                or plan.get("source_version", {}).get("value") != lifecycle.get("generation")):
            fail("component_acceptance_required")
        item = line["order_item_id"]
        if any(r.get("order_line_id") == item for r in plan["lines"]) or any(r.get("order_item_id") == item for r in workflow.get("items", [])):
            fail("item_already_exists")
        for collection, field in ((controls.PIECES, "order_item_id"), (ALLOCATIONS, "order_item_id"), (stock.UNITS, "order_line_id")):
            order_field = "order_id" if collection == stock.UNITS else "order_number"
            if await scoped[collection].find_one({"user_id": user_id, order_field: order_number, field: item}):
                fail("unit_already_exists")
        # Inventory tokens use the stock contract; immutable source snapshots
        # and employee display keep the original customer structures unchanged.
        custom = line.get("custom_fields")
        if isinstance(custom, dict):
            custom = [{"field_name": k, "value": v} for k, v in custom.items()]
        normalized = stock._lines([{**line, "options": [], "custom_fields": custom,
            "order_line_id": item, "options_normalized": line["options"]}])[0]
        product_id, demands = await stock._recipe(scoped, user_id, normalized)
        available = await stock._available(scoped, user_id, plan.get("warehouse_ids") or [])
        now = datetime.now(timezone.utc)
        records = await build_add_preparation(scoped, user_id=user_id, order_number=order_number,
            change_id=event["change_id"], revision=event["revision"],
            application_revision=payload["expected_revision"] + 1, line=line, employee=employee,
            actor={"id": context["actor_id"], "name": context.get("actor_name", "")}, now=now)
        units = []
        for index in range(1, normalized["quantity"] + 1):
            unit_id = stock._id("component_unit", user_id, order_number, item, index)
            unit = {"_id": unit_id, "user_id": user_id, "plan_id": plan["_id"], "order_id": order_number,
                "order_line_id": item, "unit_index": index, "generation": 0, "change_id": event["change_id"],
                "revision": event["revision"], "application_revision": payload["expected_revision"] + 1,
                "source_options_snapshot": deepcopy(line["options"]), "product_id": product_id, "variant_id": normalized["variant_id"], "selected_context": normalized["selected_context"],
                "resource_demands": demands, "prebuilt": None, "state": "reserved", "created_at": now.isoformat(),
                "consumption_id": stock._id("component_consumption", unit_id, demands)}
            unit["allocations"] = _allocate(user_id, order_number, unit, demands, 0, available)
            await scoped[stock.UNITS].insert_one(unit)
            units.append(unit)
        lines = sorted([*plan["lines"], normalized], key=lambda r: r["order_line_id"])
        await scoped[stock.PLANS].update_one({"_id": plan["_id"], "user_id": user_id}, {"$set": {
            "input_hash": stock._digest({"lines": lines, "warehouse_ids": plan.get("warehouse_ids") or []})},
            "$push": {"lines": normalized}})
        for collection, docs in ((BATCHES, [records["batch"]]), (REGISTRY, [records["registry"]]),
                                  (ALLOCATIONS, records["allocations"]), (controls.PIECES, records["pieces"])):
            for doc in docs:
                await scoped[collection].insert_one({"_id": doc["id"], **doc})
        revision = payload["expected_revision"] + 1
        reviewed = {"order_item_id": item, "review_status": "reviewed", "fulfillment_type": "preparation",
            "quantity": normalized["quantity"], "generation": 0, "change_id": event["change_id"],
            "revision": event["revision"], "application_revision": revision,
            "reviewed_at": now.isoformat(), "source_options_snapshot": deepcopy(line["options"])}
        changed = await scoped[controls.WORKFLOWS].update_one({"user_id": user_id, "order_number": order_number,
            "revision": payload["expected_revision"]}, {"$inc": {"revision": 1}, "$push": {"items": reviewed}})
        if changed.matched_count != 1:
            fail("revision_conflict")
        application_id = "salla-add-application-" + controls._identity(user_id, event["event_id"])
        result = {"status": "applied", "application_id": application_id, "change_id": event["change_id"],
            "piece_ids": [p["piece_id"] for p in records["pieces"]], "revision": revision, "generation": generation,
            "eligible_for_execution": True, "idempotent_replay": False,
            "released_hold_ids": [h["id"] for h in holds], "salla_updated": False, "accounting_updated": False}
        await scoped[controls.AUDIT].insert_one({"_id": application_id, "user_id": user_id, "order_number": order_number,
            "event_type": APPLIED, "event_id": event["event_id"], "change_id": event["change_id"],
            "actor_id": context["actor_id"], "reason": reason, "occurred_at": now.isoformat(),
            "before": {"workflow": workflow, "plan": plan, "holds": holds},
            "after": {"reviewed_item": reviewed, "units": units, "preparation": records, "result": result}, "result": result})
        await scoped[controls.AUDIT].insert_one({"_id": application_id + ":outbox", "user_id": user_id,
            "order_number": order_number, "event_type": "salla_add_notification_outbox", "event_id": event["event_id"],
            "change_id": event["change_id"], "recipients": [employee["id"]], "piece_ids": result["piece_ids"],
            "message": "منتج مضاف إلى الطلب", "required_action": "prepare_assigned_units", "status": "pending",
            "read_at": None, "acknowledged_at": None, "occurred_at": now.isoformat(), "actor_id": context["actor_id"]})
        await scoped[controls.REQUESTS].insert_one({"_id": controls._identity(user_id, key), "user_id": user_id,
            "fingerprint": fingerprint, "result": result})
        # Release comes last and is in the same transaction as every artifact.
        for hold in holds:
            released = await scoped[controls.HOLDS].update_one({
                "user_id": user_id, "id": hold["id"], "order_number": order_number,
                "event_id": event["event_id"], "authority": AUTHORITY, "contract_version": 5,
                "hold_kind": ADD_KIND, "scope": "item", "target_id": line["order_item_id"],
                "status": "active", "source_generation": generation,
                **{k: hold[k] for k in IDENTITY_FIELDS}}, {"$set": {"status": "released",
                    "released_at": now.isoformat(), "released_by": context["actor_id"],
                    "application_id": application_id, "released_revision": revision}})
            if released.matched_count != 1:
                fail("hold_revision_conflict")
        return result
    return await operational_owner(db, user_id, apply, profile="salla_add")
