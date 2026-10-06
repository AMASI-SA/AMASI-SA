"""Offline Salla-source reconciliation. No provider, commercial or financial writer.

The replay adapter receives normalized Salla evidence, never a Mezan edit command.
All history/outbox records use the existing append-only operational event store.
No live intake registration or application handler is installed by PR3.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os

from fastapi import HTTPException
import fulfillment_lifecycle as controls
from operational_atomic import operational_owner
from order_change_hold_contract import event_holds

FLAG = "ORDER_SALLA_CHANGE_RECONCILIATION_ENABLED"
INTAKE = "salla_change_intake"
EVENT = "salla_order_change"
OUTBOX = "salla_change_notification_outbox"
AUTHORITY = "salla_change_pr3"


def fail(code, status=409):
    raise HTTPException(status, detail={"code": "salla_change_" + code})


def _version(value):
    if value is None:
        return None
    if not isinstance(value, str):
        fail("version_invalid", 422)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        fail("version_invalid", 422)


def _snapshot(snapshot):
    if not isinstance(snapshot, dict):
        fail("snapshot_invalid", 422)
    try:
        encoded = json.dumps(snapshot, allow_nan=False)
    except (TypeError, ValueError):
        fail("snapshot_invalid", 422)
    if len(encoded.encode()) > 262144:
        fail("snapshot_too_large", 422)
    source = deepcopy(snapshot)
    source["version"] = _version(source.get("version"))
    if type(source.get("complete")) is not bool:
        fail("completeness_required", 422)
    if type(source.get("cancelled", False)) is not bool or not isinstance(source.get("replacements", []), list):
        fail("source_evidence_invalid", 422)
    if source.get("actor") is not None and not isinstance(source["actor"], dict):
        fail("actor_evidence_invalid", 422)
    if not source["complete"]:
        return source
    items = source.get("items")
    if not isinstance(items, list) or len(items) > 250:
        fail("items_invalid", 422)
    seen = set()
    for row in items:
        if not isinstance(row, dict):
            fail("item_invalid", 422)
        for key in ("order_item_id", "product_id"):
            if not isinstance(row.get(key), str) or not row[key].strip() or row[key] != row[key].strip():
                fail("item_identity_invalid", 422)
        if row["order_item_id"] in seen:
            fail("duplicate_item_identity", 422)
        seen.add(row["order_item_id"])
        if type(row.get("quantity")) is not int or not 1 <= row["quantity"] <= 1000:
            fail("quantity_invalid", 422)
        if not isinstance(row.get("options"), dict):
            fail("options_required", 422)
    source["items"] = sorted(items, key=lambda row: row["order_item_id"])
    return source


def _facts(source):
    return {"items": source.get("items"), "commercial": source.get("commercial", {}),
            "cancelled": source.get("cancelled", False)}


def detect_changes(before, after):
    """Compare complete source facts; never infer replacement from matching SKU."""
    old = {r["order_item_id"]: r for r in before["items"]}
    new = {r["order_item_id"]: r for r in after["items"]}
    changes, paired_old, paired_new = [], set(), set()
    for proof in after.get("replacements", []):
        if not isinstance(proof, dict):
            fail("replacement_evidence_invalid")
        a, b = proof.get("old_item_id"), proof.get("new_item_id")
        if a not in old or a in new or b not in new or b in old or a in paired_old or b in paired_new:
            fail("replacement_evidence_invalid")
        paired_old.add(a)
        paired_new.add(b)
        changes.append({"change_type": "replace_product", "old_data": old[a], "new_data": new[b],
                        "source_replacement_evidence": deepcopy(proof)})
    for key in sorted(old.keys() | new.keys()):
        if key in paired_old or key in paired_new:
            continue
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        kind = "add_product" if a is None else "cancel_product" if b is None else (
            "replace_product" if a["product_id"] != b["product_id"] else
            "edit_options" if any(a.get(k) != b.get(k) for k in ("variant_id", "options", "custom_fields")) else
            "quantity_change" if a["quantity"] != b["quantity"] else "commercial_change")
        changes.append({"change_type": kind, "old_data": a, "new_data": b,
            "changed_fields": sorted(k for k in (a or {}).keys() | (b or {}).keys()
                                     if (a or {}).get(k) != (b or {}).get(k))})
    if before.get("commercial", {}) != after.get("commercial", {}):
        changes.append({"change_type": "financial_change", "old_data": before.get("commercial", {}),
                        "new_data": after.get("commercial", {})})
    if after.get("cancelled") and not before.get("cancelled"):
        changes.append({"change_type": "cancel_order", "old_data": None, "new_data": {"cancelled": True}})
    if before.get("cancelled") and not after.get("cancelled"):
        changes.append({"change_type": "order_reopened", "old_data": {"cancelled": True},
                        "new_data": {"cancelled": False}})
    return changes


async def replay_snapshot(db, *, user_id, order_number, snapshot, idempotency_key, baseline=False,
                          expected_revision=None, expected_generation=None):
    """Trusted offline replay seam, not a public or live webhook handler.

    baseline=True explicitly establishes the fixture's previous source snapshot.
    It may only be used once. No fulfillment activation follows either path.
    """
    if any(not isinstance(v, str) or not v.strip() or v != v.strip() or len(v) > 180
           for v in (user_id, order_number, idempotency_key)) or len(idempotency_key) < 8 or type(baseline) is not bool:
        fail("identity_invalid", 422)
    if (expected_revision is None) != (expected_generation is None):
        fail("fences_required", 422)
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0
            or not isinstance(expected_generation, str) or len(expected_generation) != 64):
        fail("fences_invalid", 422)
    source = _snapshot(snapshot)
    key = "salla-pr3:" + idempotency_key
    request_id = controls._identity(user_id, key)
    fingerprint = controls._digest({"order": order_number, "source": source, "baseline": baseline})
    if expected_revision is not None:
        fingerprint = controls._digest([fingerprint, expected_revision, expected_generation])
    facts_hash = controls._digest(_facts(source))

    async def apply(scoped):
        replay = await controls._replay(scoped, user_id, key, fingerprint)
        if replay:
            return replay
        if not controls.enabled() or os.environ.get(FLAG, "") != "true":
            fail("disabled")
        workflow, generation = await controls._snapshot(scoped, user_id, order_number)
        if expected_revision is not None:
            if int(workflow.get("revision") or 0) != expected_revision:
                fail("revision_conflict")
            if generation != expected_generation:
                fail("generation_conflict")
        prior = await scoped[controls.AUDIT].find({"user_id": user_id, "order_number": order_number,
            "event_type": INTAKE, "accepted": True}).sort("source_version", -1).limit(1).to_list(1)
        previous = prior[0] if prior else None
        now = datetime.now(timezone.utc).isoformat()
        version = source.get("version")
        status, changes, accepted = "no_change", [], False
        if baseline:
            if previous or not version or not source["complete"]:
                fail("baseline_invalid")
            status, accepted = "baseline_recorded", True
        elif previous and version and version < previous["source_version"]:
            status = "stale_ignored"
        elif not source["complete"] or not version or not previous:
            status = "awaiting_authoritative_refresh"
        elif version == previous["source_version"] and facts_hash != previous["source_fingerprint"]:
            status = "source_conflict"
        elif facts_hash == previous["source_fingerprint"]:
            status, accepted = "no_change", True
        else:
            try:
                changes = detect_changes(previous["snapshot"], source)
                accepted = True
                status = "pending_application" if changes else "no_change"
            except HTTPException:
                status = "source_conflict"
        material = status in {"pending_application", "source_conflict", "awaiting_authoritative_refresh"}
        revision = int(workflow.get("revision") or 0)
        change_id = "salla-change-" + controls._identity(user_id,
            [order_number, version, facts_hash, source["complete"], baseline, accepted])
        # Same normalized source facts under another transport key have one result.
        duplicate = await scoped[controls.AUDIT].find_one({"user_id": user_id, "order_number": order_number,
            "event_type": INTAKE, "change_id": change_id, "baseline": baseline,
            "source_complete": source["complete"]})
        if duplicate:
            result = {**duplicate["result"], "idempotent_replay": True}
            await scoped[controls.REQUESTS].insert_one({"_id": request_id, "user_id": user_id,
                "fingerprint": fingerprint, "result": result})
            return result
        pieces = await scoped[controls.PIECES].find({"user_id": user_id, "order_number": order_number}).to_list(10001)
        if len(pieces) > 10000:
            fail("piece_limit")
        known = {p.get("piece_id") for p in pieces}
        pieces.extend(p for p in controls.virtual_pieces(workflow) if p["piece_id"] not in known)
        claim = await scoped[controls.EXECUTIONS].find_one({"_id": controls._identity(user_id, order_number),
            "user_id": user_id, "state": {"$in": ["active", "uncertain"]}})
        if material and claim:
            status = "awaiting_execution_resolution"
        elif material and workflow.get("stage") not in {"reviewed", "in_progress", "preparation", "assembly"}:
            status = "exception_required"
        elif any(c["change_type"] == "order_reopened" for c in changes):
            status = "exception_required"
        if material and source.get("cancelled"):
            status = "exception_required"
        events = []
        if material:
            # Source ADD cannot reuse a known operational identity/generation.
            known_items = {p.get("order_item_id") for p in pieces}
            known_items.update(r.get("order_item_id") for r in workflow.get("items", []))
            reservations = await scoped["mezan_component_consumption_units_v1"].find(
                {"user_id": user_id, "order_id": order_number}).to_list(10001)
            old_holds = await scoped[controls.HOLDS].find(
                {"user_id": user_id, "order_number": order_number}).to_list(10001)
            if len(reservations) > 10000 or len(old_holds) > 10000:
                fail("hold_identity_limit")
            known_items.update(r.get("order_line_id") for r in reservations)
            known_items.update(r.get("order_item_id") for r in old_holds if r.get("order_item_id"))
            known_items.update(r.get("target_id") for r in old_holds if r.get("scope") == "item")
            projected = sum((c.get("new_data") or {}).get("quantity", 1)
                            if c["change_type"] == "add_product" else 2 for c in changes)
            if len(old_holds) + projected > 9999:
                # Avoid creating more holds than PR1 can safely scan. Keep source
                # evidence and a conservative singleton instead of partial scope.
                if len(old_holds) >= 10000:
                    fail("hold_identity_limit")
                status = "exception_required"
            await scoped[controls.CONTROL_OWNERS].update_one({"_id": user_id, "user_id": user_id},
                {"$setOnInsert": {"user_id": user_id, "contract_version": 2}}, upsert=True)
            for index, change in enumerate(changes or [{"change_type": "source_validation_required",
                                                       "old_data": None, "new_data": None}]):
                old = change.get("old_data") or {}
                affected = [p for p in pieces if change["change_type"] in {"cancel_order", "order_reopened", "source_validation_required"}
                            or p.get("order_item_id") == old.get("order_item_id")]
                if change["change_type"] == "quantity_change":
                    remaining = change["new_data"]["quantity"]
                    affected = [p for p in affected if int(p.get("unit_index") or 0) > remaining]
                event_id = change_id + ":" + str(index)
                event = {"schema_version": 1, "event_type": EVENT, "event_id": event_id, "change_id": change_id,
                    "idempotency_key": idempotency_key, "user_id": user_id, "order_number": order_number,
                    **deepcopy(change), "source": "salla_replay", "source_version": version,
                    "source_fingerprint": facts_hash, "previous_source_version": previous["source_version"] if previous else None,
                    "revision": revision + 1, "generation": generation,
                    "affected_units": [{k: p.get(k, 0 if k in {"generation", "revision"} else None)
                        for k in ("piece_id", "order_item_id", "unit_index", "generation", "revision")} for p in affected],
                    "fulfillment_stage": workflow.get("stage"), "actor": deepcopy(source.get("actor")),
                    "actor_evidence": "source_supplied" if source.get("actor") else "not_supplied",
                    "reason": source.get("reason"), "required_action": "reconciliation_review",
                    "application_state": "pending_application", "intake_state": status,
                    "source_timestamp": version, "received_at": now, "recorded_at": now,
                    "financial_impact": "pending_contract", "salla_mutation_enabled": False}
                holds = event_holds(event, existing_item_ids=known_items)
                for hold in holds:
                    existing = await scoped[controls.HOLDS].find_one({"user_id": user_id, "id": hold["id"]})
                    if existing:
                        fields = ("status", "contract_version", "authority", "scope", "order_number", "target_id")
                        if hold["contract_version"] == 5:
                            fields += ("change_id", "event_id", "order_item_id", "unit_index", "generation", "revision", "hold_kind")
                        if any(existing.get(k) != hold[k] for k in fields):
                            fail("barrier_conflict")
                    else:
                        await scoped[controls.HOLDS].insert_one({"_id": hold["id"], **hold})
                event["hold_ids"] = [hold["id"] for hold in holds]
                event["hold_contract_version"] = "pr3.1"
                await scoped[controls.AUDIT].insert_one({"_id": event_id, **event})
                employees = sorted({p["responsible_employee_id"] for p in affected if p.get("responsible_employee_id")})
                await scoped[controls.AUDIT].insert_one({"_id": event_id + ":outbox", "event_type": OUTBOX,
                    "user_id": user_id, "order_number": order_number, "change_id": change_id,
                    "event_id": event_id, "status": "pending", "read_at": None, "acknowledged_at": None,
                    "recipients": employees, "stage": workflow.get("stage"), "affected_units": event["affected_units"],
                    "required_action": event["required_action"], "actor": event["actor"], "created_at": now})
                events.append(event)
            updated = await scoped[controls.WORKFLOWS].update_one({"user_id": user_id, "order_number": order_number,
                "revision": revision}, {"$inc": {"revision": 1}})
            if updated.matched_count != 1:
                fail("revision_conflict")
        result = {"status": status, "change_id": change_id, "revision": revision + int(material),
            "generation": generation, "events": events, "idempotent_replay": False,
            "application_state": "pending_application" if material else None,
            "salla_updated": False, "accounting_updated": False}
        await scoped[controls.AUDIT].insert_one({"_id": request_id + ":intake", "event_type": INTAKE,
            "user_id": user_id, "order_number": order_number, "change_id": change_id,
            "accepted": accepted, "source_complete": source["complete"], "baseline": baseline,
            "source_version": version, "source_fingerprint": facts_hash, "snapshot": source,
            "before": {"snapshot": previous["snapshot"] if previous else None, "revision": revision},
            "after": {"snapshot": source, "revision": result["revision"], "status": status},
            "occurred_at": now, "result": result})
        await scoped[controls.REQUESTS].insert_one({"_id": request_id, "user_id": user_id,
            "fingerprint": fingerprint, "result": result})
        return result
    return await operational_owner(db, user_id, apply)
