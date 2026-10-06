"""Source EDIT_OPTIONS application; no commercial input or provider/financial IO."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import os

from fastapi import HTTPException
import fulfillment_lifecycle as controls
import salla_order_change_reconciliation as source
import stock_component_consumption_service as stock
from fulfillment_component_reconciliation import _allocate, _ambiguous_consumption
from operational_atomic import operational_owner
from order_change_hold_contract import event_holds
from preparation_file_registry import _assignable_employees
from salla_edit_preparation import build_edit_preparation, MAX_BATCH_UNITS

FLAG = "ORDER_SALLA_EDIT_APPLICATION_ENABLED"
APPLIED = "salla_edit_applied"
DECISION = "salla_edit_decision"
OUTBOX = "salla_edit_notification_outbox"
BATCHES = "mezan_preparation_batches_v2"
REGISTRY = "mezan_preparation_file_registry_v2"
ALLOCATIONS = "mezan_preparation_unit_allocations_v2"
FIELDS = ("order_item_id", "unit_index", "generation", "revision", "change_id")


def fail(code, status=409):
    raise HTTPException(status, detail={"code": "salla_edit_" + code})


def enabled():
    return controls.enabled() and os.environ.get(source.FLAG) == "true" and os.environ.get(FLAG) == "true"


def authorize(context, owner):
    if not controls._authorized(context, owner):
        fail("permission_required", 403)


def equivalent(value):
    """Only representation equivalence, never trim or case-fold customer text."""
    if isinstance(value, dict):
        return {k: equivalent(v) for k, v in value.items()}
    if isinstance(value, list):
        return [equivalent(v) for v in value]
    if type(value) in (int, float):
        return format(Decimal(str(value)).normalize(), "f")
    return value


def normalized(line):
    custom = line.get("custom_fields")
    if isinstance(custom, dict):
        custom = [{"field_name": k, "value": v} for k, v in custom.items()]
    return stock._lines([{**line, "options": [], "options_normalized": line["options"],
                          "custom_fields": custom, "order_line_id": line["order_item_id"]}])[0]


def unit_ref(piece):
    return {k: piece.get(k, 0 if k in {"generation", "revision"} else None) for k in FIELDS}


def stored_fence(row, key):
    # Native generation-zero materializers predate explicit fence fields.
    # Match the actual missing representation, never an unfenced update.
    return row[key] if key in row else {"$exists": False}


def validate_units(rows):
    if not isinstance(rows, list) or not rows or len(rows) > MAX_BATCH_UNITS:
        fail("units_required", 422)
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != set(FIELDS)
                or not isinstance(row["order_item_id"], str) or not row["order_item_id"]
                or any(type(row[k]) is not int or row[k] < 0 for k in ("generation", "revision"))
                or type(row["unit_index"]) is not int or row["unit_index"] < 1
                or row["change_id"] is not None and not isinstance(row["change_id"], str)):
            fail("unit_identity_invalid", 422)
        key = (row["order_item_id"], row["unit_index"])
        if key in seen:
            fail("duplicate_unit", 422)
        seen.add(key)
    return sorted(rows, key=lambda r: (r["order_item_id"], r["unit_index"]))


async def evidence(db, owner, number, event):
    if event.get("event_type") != source.EVENT or event.get("change_type") != "edit_options":
        fail("edit_event_required")
    if event.get("intake_state") != "pending_application" or event.get("application_state") != "pending_application":
        fail("source_exception")
    old, new = event.get("old_data") or {}, event.get("new_data") or {}
    if (not all(old.get(k) == new.get(k) and old.get(k) is not None for k in ("order_item_id", "product_id", "quantity"))
            or type(new.get("quantity")) is not int or not 1 <= new["quantity"] <= MAX_BATCH_UNITS):
        fail("item_identity_ambiguous")
    workflow, generation = await controls._snapshot(db, owner, number)
    if workflow.get("stage") not in {"in_progress", "preparation", "assembly"} or workflow.get("experiment_mode") or workflow.get("experiment_run_id"):
        fail("stage_locked")
    if generation != event.get("generation"):
        fail("source_generation_conflict")
    if int(workflow.get("revision") or 0) < event["revision"]:
        fail("revision_conflict")
    if await db[controls.EXECUTIONS].find_one({"user_id": owner, "_id": controls._identity(owner, number),
                                              "state": {"$in": ["active", "uncertain"]}}):
        fail("execution_in_flight")
    canonical = await db["unified_orders"].find_one({"user_id": owner, "order_number": number}) or {}
    lifecycle = await db["mezan_component_order_lifecycle_v1"].find_one({"user_id": owner, "order_number": number}) or {}
    watermark = canonical.get("g47_salla_snapshot") or {}
    from salla_shipping import projected_shipping
    shipping = projected_shipping(canonical) or {}
    if (watermark.get("cancelled") or watermark.get("component_pending") or watermark.get("requires_authoritative_refresh")
            or lifecycle.get("cancelled") or lifecycle.get("retry_required")
            or canonical.get("order_status_slug") in {"cancelled", "canceled"}
            or any(canonical.get(k) for k in ("awb_number", "salla_shipment_id", "qoyod_invoice_id", "manual_qoyod_invoice_id", "duplicate_of_invoice"))
            or any(shipping.get(k) for k in ("awb", "awb_number", "tracking_number", "shipment_id"))):
        fail("source_locked")
    if await db["qoyod_invoices"].find_one({"user_id": owner, "$or": [{k: number} for k in
            ("reference", "salla_order_number", "external_reference", "source_reference")]}):
        fail("invoice_locked")
    heads = await db[controls.AUDIT].find({"user_id": owner, "order_number": number,
        "event_type": source.INTAKE, "accepted": True}).sort("source_version", -1).limit(1).to_list(1)
    if not heads or new not in heads[0]["snapshot"].get("items", []) or heads[0]["snapshot"].get("cancelled"):
        fail("source_changed")
    expected = event_holds(event, existing_item_ids=set())
    if (event.get("hold_contract_version") != "pr3.1" or len(expected) != 1
            or expected[0].get("hold_kind") != "SOURCE_ITEM_CHANGE_HOLD"
            or event.get("hold_ids") != [expected[0]["id"]]):
        fail("edit_hold_contract_required")
    active = await db[controls.HOLDS].find({"user_id": owner, "order_number": number, "status": "active"}).to_list(10001)
    if len(active) != 1:
        fail("other_hold_active")
    hold = active[0]
    if any(hold.get(k) != expected[0].get(k) for k in ("id", "event_id", "change_id", "scope", "target_id", "authority", "contract_version", "hold_kind", "revision", "source_generation")):
        fail("edit_hold_contract_required")
    item = new["order_item_id"]
    rows = await db[controls.PIECES].find({"user_id": owner, "order_number": number, "order_item_id": item,
        "current": {"$ne": False}, "active": {"$ne": False}, "obsolete": {"$ne": True},
        "status": {"$nin": ["obsolete", "replaced", "cancelled", "canceled", "superseded", "archived"]}}).to_list(MAX_BATCH_UNITS + 1)
    rows.sort(key=lambda p: int(p.get("unit_index") or 0))
    if len(rows) != new["quantity"] or [p.get("unit_index") for p in rows] != list(range(1, new["quantity"] + 1)):
        fail("physical_units_ambiguous")
    for piece in rows:
        await controls.assert_piece_current(db, user_id=owner, piece_id=piece["piece_id"])
        if piece.get("active_hold_id") or piece.get("status") == "blocked" or piece.get("execution_status") == "blocked":
            fail("piece_blocked")
        options = piece.get("source_options_snapshot", piece.get("product_options_snapshot"))
        if equivalent(options) != equivalent(old["options"]):
            fail("old_options_conflict")
    plan = await db[stock.PLANS].find_one({"user_id": owner, "order_id": number})
    if (not plan or plan.get("state") != "accepted" or lifecycle.get("state") != "reserved"
            or lifecycle.get("accepted") is not True or plan.get("source_version", {}).get("kind") != "revision"
            or plan["source_version"]["value"] != lifecycle.get("generation")):
        fail("component_acceptance_required")
    plan_line = next((r for r in plan["lines"] if r["order_line_id"] == item), None)
    old_normalized, new_normalized = normalized(old), normalized(new)
    if not plan_line or plan_line["quantity"] != old["quantity"]:
        fail("component_line_missing")
    # Representation-only prior edits may retain the original inventory tokens.
    if plan_line["variant_id"] != old_normalized["variant_id"]:
        fail("component_source_conflict")
    components = await db[stock.UNITS].find({"user_id": owner, "plan_id": plan["_id"], "order_line_id": item}).sort("unit_index", 1).to_list(MAX_BATCH_UNITS + 1)
    allocations = await db[ALLOCATIONS].find({"user_id": owner, "order_number": number, "order_item_id": item}).sort("unit_index", 1).to_list(MAX_BATCH_UNITS + 1)
    if len(components) != len(rows) or len(allocations) != len(rows):
        fail("unit_artifacts_missing")
    for piece, unit, assignment in zip(rows, components, allocations):
        if (unit.get("unit_index") != piece["unit_index"] or int(unit.get("generation") or 0) != int(piece.get("generation") or 0)
                or unit.get("selected_context") != plan_line["selected_context"]
                or assignment.get("unit_index") != piece["unit_index"] or assignment.get("batch_id") != piece.get("batch_id")
                or assignment.get("status") != "committed"):
            fail("unit_identity_conflict")
    representation = all(equivalent(old.get(k)) == equivalent(new.get(k)) for k in ("options", "custom_fields", "variant_id"))
    classification, product, demands = "representation_only", None, []
    if any(_ambiguous_consumption(u) for u in components):
        classification = "reconciliation_required"
    elif not representation:
        if any(u.get("prebuilt") or _ambiguous_consumption(u) or u.get("state") == "consumed" for u in components):
            classification = "reconciliation_required"
        else:
            product, demands = await stock._recipe(db, owner, new_normalized)
            signature = lambda ds: sorted((d["binding_id"], d["resource_id"], str(Decimal(d["quantity"]))) for d in ds)
            classification = "components" if any(signature(u["resource_demands"]) != signature(demands) for u in components) else "preparation_file"
    return dict(workflow=workflow, generation=generation, old=old, new=new, hold=hold, pieces=rows,
                plan=plan, units=components, assignments=allocations, normalized=new_normalized,
                classification=classification, product=product, demands=demands)


async def pending_edits(db, *, user_id, order_number, context):
    authorize(context, user_id)
    if not enabled():
        return {"enabled": False, "changes": [], "employees": []}
    workflow, generation = await controls._snapshot(db, user_id, order_number)
    events = await db[controls.AUDIT].find({"user_id": user_id, "order_number": order_number,
        "event_type": source.EVENT, "change_type": "edit_options"}).to_list(1001)
    if len(events) > 1000:
        fail("event_limit")
    changes = []
    employees = await _assignable_employees(db, user_id=user_id, reviewer={"id": context["actor_id"]})
    for event in events:
        if await db[controls.AUDIT].find_one({"user_id": user_id, "event_type": APPLIED, "event_id": event["event_id"]}):
            continue
        classification, reason, refs, people = "exception_required", None, [], []
        try:
            data = await evidence(db, user_id, order_number, event)
            classification = data["classification"]
            refs = [unit_ref(p) for p in data["pieces"]]
            people = sorted({p["responsible_employee_id"] for p in data["pieces"] if p.get("responsible_employee_id")})
        except HTTPException as exc:
            reason = exc.detail.get("code") if isinstance(exc.detail, dict) else "source_exception"
        notification = await db[controls.AUDIT].find_one({"user_id": user_id, "event_type": source.OUTBOX, "event_id": event["event_id"]}) or {}
        changes.append({"event_id": event["event_id"], "change_id": event["change_id"], "change_type": "edit_options",
            "application_state": "pending_application", "old_data": event["old_data"], "new_data": event["new_data"],
            "classification": classification, "apply_allowed": classification in {"representation_only", "preparation_file", "components"},
            "reason": reason, "stage": workflow.get("stage"), "units": refs,
            "affected_employees": [{"id": p, "name": next((e.get("name", p) for e in employees if e["id"] == p), p)} for p in people],
            "expected_revision": int(workflow.get("revision") or 0), "expected_generation": generation,
            "notification_status": notification.get("status", "pending"), "required_action": "stop_old_generation"})
    return {"enabled": True, "changes": changes, "employees": employees}


async def apply_edit(db, *, user_id, order_number, context, payload):
    authorize(context, user_id)
    if not isinstance(payload, dict) or set(payload) != {"event_id", "employee_id", "reason", "idempotency_key", "expected_revision", "expected_generation", "units"}:
        fail("request_invalid", 422)
    reason, key = controls._request(payload)
    targets = validate_units(payload["units"])
    if any(not isinstance(payload[k], str) or not payload[k].strip() for k in ("event_id", "employee_id")):
        fail("identity_invalid", 422)
    key = "salla-edit:" + key
    fingerprint = controls._digest({"order": order_number, "actor": context["actor_id"], "payload": payload})
    async def apply(scoped):
        replay = await controls._replay(scoped, user_id, key, fingerprint)
        if replay:
            return replay
        if not enabled():
            fail("disabled")
        prior = await scoped[controls.AUDIT].find_one({"user_id": user_id, "order_number": order_number,
            "event_type": {"$in": [APPLIED, DECISION]}, "event_id": payload["event_id"]})
        if prior:
            return {**prior["result"], "idempotent_replay": True}
        await controls._fences(scoped, user_id, order_number, payload)
        event = await scoped[controls.AUDIT].find_one({"user_id": user_id, "order_number": order_number,
            "event_type": source.EVENT, "event_id": payload["event_id"]})
        if not event:
            fail("event_missing", 404)
        d = await evidence(scoped, user_id, order_number, event)
        if targets != [unit_ref(p) for p in d["pieces"]]:
            fail("unit_fence_conflict")
        now = datetime.now(timezone.utc)
        app_id = "salla-edit-application-" + controls._identity(user_id, event["event_id"])
        kind, new_pieces, new_units, records = d["classification"], [], [], None
        revision = payload["expected_revision"] + 1
        if kind in {"preparation_file", "components"}:
            employees = await _assignable_employees(scoped, user_id=user_id, reviewer={"id": context["actor_id"]})
            employee = next((e for e in employees if e["id"] == payload["employee_id"]), None)
            if not employee:
                fail("employee_ineligible", 403)
            records = await build_edit_preparation(scoped, user_id=user_id, order_number=order_number,
                change_id=event["change_id"], revision=event["revision"], application_revision=revision,
                unit_generations={str(p["unit_index"]): int(p.get("generation") or 0) + 1 for p in d["pieces"]},
                line=d["new"], employee=employee, actor={"id": context["actor_id"], "name": context.get("actor_name", "")}, now=now)
            for unit in d["units"]:
                changed = await scoped[stock.UNITS].update_one({"user_id": user_id, "_id": unit["_id"], "state": "reserved",
                    "generation": stored_fence(unit, "generation")}, {"$set": {"state": "released"}})
                if changed.matched_count != 1:
                    fail("component_conflict")
            available = await stock._available(scoped, user_id, d["plan"].get("warehouse_ids") or [])
            for old_piece, old_unit, old_assignment, new_piece, assignment in zip(d["pieces"], d["units"], d["assignments"], records["pieces"], records["allocations"]):
                generation = int(old_piece.get("generation") or 0) + 1
                new_piece.update(generation=generation, source_event_id=event["event_id"], previous_piece_id=old_piece["piece_id"])
                assignment.update(generation=generation, previous_assignment_id=old_assignment.get("id"))
                unit = {**old_unit, "generation": generation, "revision": event["revision"], "application_revision": revision,
                    "change_id": event["change_id"], "product_id": d["product"], "variant_id": d["normalized"]["variant_id"],
                    "selected_context": d["normalized"]["selected_context"], "resource_demands": d["demands"], "state": "reserved",
                    "source_options_snapshot": deepcopy(d["new"]["options"]), "reconciled_at": now.isoformat(),
                    "consumption_id": stock._id("component_consumption", old_unit["_id"], generation, d["demands"])}
                unit["allocations"] = _allocate(user_id, order_number, unit, d["demands"], generation, available)
                changed = await scoped[stock.UNITS].replace_one({"user_id": user_id, "_id": old_unit["_id"], "state": "released", "generation": stored_fence(old_unit, "generation")}, unit)
                if changed.matched_count != 1:
                    fail("component_conflict")
                changed = await scoped[ALLOCATIONS].replace_one({"user_id": user_id, "_id": old_assignment["_id"], "id": old_assignment["id"], "batch_id": old_piece["batch_id"], "status": "committed"}, {"_id": old_assignment["_id"], **assignment})
                if changed.matched_count != 1:
                    fail("assignment_conflict")
                await scoped[controls.PIECES].insert_one({"_id": new_piece["piece_id"], **new_piece})
                changed = await scoped[controls.PIECES].update_one({"user_id": user_id, "piece_id": old_piece["piece_id"],
                    "generation": stored_fence(old_piece, "generation"), "revision": stored_fence(old_piece, "revision"), "status": old_piece["status"]},
                    {"$set": {"status": "obsolete", "active": False, "current": False, "obsolete": True,
                        "replaced_by": new_piece["piece_id"], "obsolete_at": now, "obsolete_change_id": event["change_id"]}})
                if changed.matched_count != 1:
                    fail("piece_conflict")
                new_units.append(unit); new_pieces.append(new_piece)
            for collection, document in ((BATCHES, records["batch"]), (REGISTRY, records["registry"])):
                await scoped[collection].insert_one({"_id": document["id"], **document})
            lines = [d["normalized"] if r["order_line_id"] == d["new"]["order_item_id"] else r for r in d["plan"]["lines"]]
            await scoped[stock.PLANS].update_one({"user_id": user_id, "_id": d["plan"]["_id"]}, {"$set": {
                "lines": lines, "input_hash": stock._digest({"lines": sorted(lines, key=lambda r:r["order_line_id"]), "warehouse_ids": d["plan"].get("warehouse_ids") or []})}})
        status = "reconciliation_required" if kind == "reconciliation_required" else "applied"
        patch = {"$inc": {"revision": 1}}
        if new_pieces:
            items = deepcopy(d["workflow"].get("items") or [])
            item = next((r for r in items if r.get("order_item_id") == d["new"]["order_item_id"]), None)
            if item is None:
                fail("workflow_item_missing")
            item.update(review_status="reviewed", generation=max(p["generation"] for p in new_pieces), change_id=event["change_id"],
                source_options_snapshot=deepcopy(d["new"]["options"]), revision=event["revision"], application_revision=revision)
            patch["$set"] = {"items": items}
        changed = await scoped[controls.WORKFLOWS].update_one({"user_id": user_id, "order_number": order_number,
            "revision": payload["expected_revision"]}, patch)
        if changed.matched_count != 1:
            fail("revision_conflict")
        result = {"status": status, "classification": kind, "event_id": event["event_id"], "change_id": event["change_id"],
            "application_id": app_id, "new_piece_ids": [p["piece_id"] for p in new_pieces],
            "new_generation": max((p["generation"] for p in new_pieces), default=None), "revision": revision,
            "released_hold_ids": [d["hold"]["id"]] if status == "applied" else [],
            "idempotent_replay": False, "financial_impact": "pending_contract", "salla_updated": False, "accounting_updated": False}
        await scoped[controls.AUDIT].insert_one({"_id": app_id, "user_id": user_id, "order_number": order_number,
            "event_type": APPLIED if status == "applied" else DECISION, "event_id": event["event_id"], "change_id": event["change_id"],
            "actor_id": context["actor_id"], "reason": reason, "occurred_at": now, "old_options": d["old"]["options"],
            "new_options": d["new"]["options"], "before": d, "after": {"pieces": new_pieces, "units": new_units, "preparation": records}, "result": result})
        recipients = sorted({p["responsible_employee_id"] for p in d["pieces"] if p.get("responsible_employee_id")}
                            | {p["responsible_employee_id"] for p in new_pieces})
        await scoped[controls.AUDIT].insert_one({"_id": app_id + ":outbox", "user_id": user_id, "order_number": order_number,
            "event_type": OUTBOX, "event_id": event["event_id"], "change_id": event["change_id"], "recipients": recipients,
            "old_options": d["old"]["options"], "new_options": d["new"]["options"], "required_action": ("continue_current_generation" if kind == "representation_only" else
                "reconcile_consumption" if kind == "reconciliation_required" else "prepare_new_generation"),
            "old_piece_ids": [p["piece_id"] for p in d["pieces"]], "new_piece_ids": result["new_piece_ids"],
            "status": "pending", "read_at": None, "acknowledged_at": None, "occurred_at": now})
        await scoped[controls.REQUESTS].insert_one({"_id": controls._identity(user_id, key), "user_id": user_id,
            "fingerprint": fingerprint, "result": result})
        if status == "applied":
            hold = d["hold"]
            changed = await scoped[controls.HOLDS].update_one({"user_id": user_id, "id": hold["id"], "event_id": event["event_id"],
                "change_id": event["change_id"], "revision": event["revision"], "source_generation": d["generation"],
                "scope": "item", "target_id": d["new"]["order_item_id"], "authority": "salla_change_pr3", "contract_version": 4,
                "hold_kind": "SOURCE_ITEM_CHANGE_HOLD", "status": "active"}, {"$set": {"status": "released", "released_at": now,
                "released_by": context["actor_id"], "application_id": app_id, "released_revision": revision}})
            if changed.matched_count != 1:
                fail("hold_conflict")
        return result
    return await operational_owner(db, user_id, apply, profile="salla_edit")
