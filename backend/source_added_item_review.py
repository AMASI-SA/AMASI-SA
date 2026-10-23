"""Explicit review of source-added Salla lines; never assign or rewind an order."""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy

from fastapi import Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from operational_atomic import operational_owner
from order_engine.repository import MongoOrderRepository
from order_engine.service import get_order, OrderNotFoundError
from product_inventory_rules import order_item_specifications
from product_fulfillment_rules import order_is_active, payment_is_eligible
from review_local_policy import (
    LOCAL_COMPLETION_MODE, load_local_review_workflows,
    load_source_current_assignment_workflows,
)

WORKFLOWS = "order_review_workflows"
EVENTS = "order_review_events"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str, separators=(",", ":")).encode()).hexdigest()


def fail(code, status=409):
    raise HTTPException(status, detail={"code": code,
        "message": "تعذّر اعتماد المنتج الإضافي. حدّث الطلب وتحقق من حالته والمنتجات والمكونات."})


class ReviewSourceAddition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    source_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


def original_lines_unchanged(states, identities):
    """A different quantity, replacement or deletion is not a new-line ADD."""
    if not states or len({r.get("order_item_id") for r in states}) != len(states):
        return False
    current = {r.order_item_id: r for r in identities}
    for saved in states:
        item = current.get(saved.get("order_item_id"))
        if item is None or not item.source.source_order_item_id:
            return False
        if saved.get("quantity") != item.quantity:
            return False
        for key in ("product_id", "parent_product_id", "variant_id"):
            if saved.get(key) is not None and str(saved[key] or "") != str(getattr(item, key, None) or ""):
                return False
        if saved.get("options", []) != [r.model_dump(mode="json") for r in item.options]:
            return False
        if saved.get("specifications_snapshot", {}) != order_item_specifications(item):
            return False
    return True


def source_identities_match(raw, number, identities):
    rows = raw.get("items")
    if not isinstance(rows, list) or not rows or str(raw.get("reference_id")) != number or not raw.get("id"):
        return False
    ids = [str(r.get("id") or "").strip() for r in rows if isinstance(r, dict)]
    if len(ids) != len(rows) or not all(ids) or len(set(ids)) != len(ids):
        return False
    mapped = [str(i.source.source_order_item_id or "") for i in identities]
    return (len(mapped) == len(ids) and set(mapped) == set(ids)
            and len({i.order_item_id for i in identities}) == len(identities)
            and all(i.source.provider == "salla" and str(i.source.source_order_id) == str(raw["id"])
                    for i in identities))


async def context(db, owner, number):
    try:
        order = await get_order(MongoOrderRepository(db), user_id=owner, order_number=number)
    except OrderNotFoundError:
        fail("order_not_found", 404)
    selector = {"user_id": owner, "order_number": number}
    source = await db.unified_orders.find_one(selector) or {}
    workflow = await db[WORKFLOWS].find_one(selector) or {}
    from order_review_routes import _review_item_identities
    identities = await _review_item_identities(db, owner, order, local_only=True)
    states = workflow.get("items") or []
    by_id = {row.get("order_item_id"): row for row in states}
    raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
    fingerprint = digest({"source": raw, "watermark": source.get("g47_salla_snapshot"),
                          "items": [i.model_dump(mode="json") for i in identities]})
    reason = None
    statuses = {str(v or "").strip().casefold().replace("_", " ")
                for v in (order.status, order.status_native)} - {""}
    if (workflow.get("stage") != "in_progress" or not statuses
            or not statuses.issubset({"in progress", "processing", "قيد التنفيذ", "جاري التنفيذ"})
            or not order_is_active(order) or not payment_is_eligible(order.payment)):
        reason = "source_addition_order_not_in_progress"
    elif not source_identities_match(raw, number, identities):
        reason = "source_addition_identity_unproven"
    elif not original_lines_unchanged(states, identities):
        reason = "source_addition_existing_lines_changed"
    elif workflow.get("completion_mode") == LOCAL_COMPLETION_MODE:
        if number not in await load_local_review_workflows(db, user_id=owner,
                order_numbers=[number], workflows=[workflow]):
            reason = "source_addition_review_proof_missing"
    elif workflow.get("completion_mode") is not None or not workflow.get("reviewed_at"):
        reason = "source_addition_review_proof_missing"
    if reason is None and number not in await load_source_current_assignment_workflows(
            db, user_id=owner, workflows=[workflow], strict=True):
        reason = "source_addition_components_not_current"
    rows = []
    for item in identities:
        state = by_id.get(item.order_item_id)
        if state and not state.get("source_addition"):
            continue
        reviewed = bool(state and state.get("review_status") == "reviewed")
        item_reason = reason
        if not math.isfinite(item.quantity) or item.quantity <= 0 or not float(item.quantity).is_integer():
            item_reason = "source_addition_whole_quantity_required"
        rows.append({"order_item_id": item.order_item_id, "reviewed": reviewed,
                     "eligible": not reviewed and item_reason is None,
                     "blocked_reason": None if reviewed else item_reason})
    return {"revision": int(workflow.get("revision") or 0), "source_fingerprint": fingerprint,
            "items": rows}, order, workflow, identities


async def live_reviewer(db, reviewer, owner):
    from order_review_routes import _require_reviewer, _merchant_user_id
    actor = await db.users.find_one({"id": str(reviewer["id"])}, {"_id": 0})
    if not actor or actor.get("is_active") is False or actor.get("disabled") is True:
        fail("order_review_permission_required", 403)
    if reviewer.get("_session_client") == "amasi_mobile":
        from mobile_app_request_context import mobile_app_request_user
        actor["_session_client"] = "amasi_mobile"
        actor = await mobile_app_request_user(db, actor,
            path="/api/order-reviews-v1/source-additions", method="POST")
    current = _require_reviewer(actor)
    if _merchant_user_id(current) != owner:
        fail("order_review_permission_required", 403)


async def approve(db, *, owner, reviewer, number, item_id, request):
    from order_review_routes import _item_view, _now
    from order_review_export_controls import preparation_assignment_product_key
    from order_tracking_notes import enforce_stage_instructions
    session_context = None
    if reviewer.get("_review_session") is not None:
        from review_session_fence import review_session_context
        session_context = {**await review_session_context(db, user_id=owner, actor=reviewer),
                           "actor_id": str(reviewer["id"])}

    async def commit(scoped):
        await live_reviewer(scoped, reviewer, owner)
        if session_context:
            from review_session_fence import fence_review_session
            await fence_review_session(scoped, user_id=owner, **session_context)
        result, order, workflow, identities = await context(scoped, owner, number)
        state = next((r for r in workflow.get("items", []) if r.get("order_item_id") == item_id), None)
        if state and state.get("source_addition") and state.get("review_status") == "reviewed":
            return {"ok": True, "order_item_id": item_id, "already_reviewed": True,
                    "revision": result["revision"]}
        target = next((r for r in identities if r.order_item_id == item_id), None)
        capability = next((r for r in result["items"] if r["order_item_id"] == item_id), None)
        if target is None:
            fail("order_item_not_found", 404)
        if not capability or not capability["eligible"]:
            fail((capability or {}).get("blocked_reason") or "source_addition_not_new")
        if request.expected_revision != result["revision"]:
            fail("review_revision_conflict")
        if request.source_fingerprint != result["source_fingerprint"]:
            fail("source_addition_source_changed")
        await enforce_stage_instructions(scoped, user_id=owner, order_number=number,
            order_item_id=item_id, stage="pending_review", actor_id=str(reviewer["id"]))
        view = _item_view(target, None, None)
        now = _now()
        frozen = {
            "order_item_id": item_id, "source_item_id": target.source.source_order_item_id,
            "product_key": preparation_assignment_product_key(target),
            "product_id": target.product_id, "parent_product_id": target.parent_product_id,
            "variant_id": target.variant_id, "product_name": target.name,
            "sku": target.sku, "barcode": target.barcode, "quantity": target.quantity,
            "options": [o.model_dump(mode="json") for o in target.options],
            "custom_fields": deepcopy(target.custom_fields),
            "specifications_snapshot": order_item_specifications(target),
            "review_status": "reviewed", "source_addition": True,
            "selected_image_url": view["selected_image_url"],
            "selected_image_source": view["selected_image_source"],
            "preparation_note": "", "internal_note": "", "revision": 1,
            "reviewed_at": now, "reviewed_by": str(reviewer["id"]),
            "reviewed_by_name": str(reviewer.get("name") or ""),
            "source_fingerprint": result["source_fingerprint"],
        }
        updated = await scoped[WORKFLOWS].update_one({"user_id": owner, "order_number": number,
            "stage": "in_progress", "revision": request.expected_revision}, {
                "$push": {"items": frozen}, "$inc": {"revision": 1},
                "$set": {"source_additions_reviewed": True, "updated_at": now}})
        if updated.matched_count != 1:
            fail("review_revision_conflict")
        await scoped[EVENTS].insert_one({"_id": "source-addition:" + digest([owner, number, item_id]),
            "user_id": owner, "order_number": number, "order_item_id": item_id,
            "event_type": "source_added_item_reviewed", "occurred_at": now,
            "actor_id": str(reviewer["id"]), "source_fingerprint": result["source_fingerprint"],
            "workflow_revision": request.expected_revision + 1,
            "parent_stage": "in_progress", "salla_updated": False})
        return {"ok": True, "order_item_id": item_id, "already_reviewed": False,
                "revision": request.expected_revision + 1}
    return await operational_owner(db, owner, commit, review_session=session_context)


def register_source_addition_routes(router, db, current_user):
    @router.get("/{order_number}/source-additions")
    async def list_additions(order_number: str, response: Response, user=Depends(current_user)):
        from order_review_routes import _require_reviewer, _merchant_user_id
        from order_review_approval import ReadSnapshot
        from accounting_write_control import AccountingDatabase
        from pymongo.read_concern import ReadConcern
        reviewer = _require_reviewer(user)
        response.headers["Cache-Control"] = "no-store"
        owner = _merchant_user_id(reviewer)
        raw_db = db.current() if isinstance(db, AccountingDatabase) else db
        async with await raw_db.client.start_session() as session:
            async with session.start_transaction(read_concern=ReadConcern("snapshot")):
                scoped = ReadSnapshot(raw_db, session)
                await live_reviewer(scoped, reviewer, owner)
                result, *_ = await context(scoped, owner, order_number)
                return result

    @router.post("/{order_number}/items/{order_item_id}/review-addition")
    async def review_addition(order_number: str, order_item_id: str,
                              payload: ReviewSourceAddition, user=Depends(current_user)):
        from order_review_routes import _require_reviewer, _merchant_user_id
        reviewer = _require_reviewer(user)
        return await approve(db, owner=_merchant_user_id(reviewer), reviewer=reviewer,
                             number=order_number, item_id=order_item_id, request=payload)
