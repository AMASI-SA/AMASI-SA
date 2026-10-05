"""Explicit new approval of current facts; never repair a historical approval.

Only the incident allowlist is discoverable. Preview does not complete Review.
Confirmation enters the existing durable engine with a distinct stable identity.
Provider verification is GET-only, including every automatic/manual retry.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import uuid

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from operational_atomic import operational_owner
from order_review_acceptance_snapshot import acceptance_snapshot
from order_review_business_snapshot import build_snapshot, compare_snapshots, verify_snapshot
from order_review_recovery_guard import RECOVERY_ORDERS

SESSIONS = "order_review_manual_approval_sessions"
SESSION_SECONDS = 900
REASON = "manual_review_recovery"


def reject(code):
    raise HTTPException(409, detail={"code": code})


def now():
    return datetime.now(timezone.utc).isoformat()


def reviewed(source):
    import order_review_routes as routes
    raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
    status = raw.get("status") or {}
    if not isinstance(status, dict):
        return False
    custom = status.get("customized") or {}
    name = custom.get("name") if isinstance(custom, dict) else custom
    return name in routes.REVIEWED_STATUS_NAMES or status.get("name") in routes.REVIEWED_STATUS_NAMES


async def candidate(db, owner, number, old_id=None):
    from order_review_completion import OPERATIONS, WORKFLOWS
    if number not in RECOVERY_ORDERS:
        reject("manual_review_recovery_out_of_scope")
    selector = {"user_id": owner, "order_number": number}
    query = {**selector, "state": "requires_review", "resume_block_reason": "review_completion_source_changed",
             "manual_review_recovery": {"$exists": False}}
    if old_id:
        query["_id"] = old_id
    old = await db[OPERATIONS].find_one(query, sort=[("created_at", -1)])
    if (not old or not old.get("provider_confirmed_at") or old.get("superseded_by")
            or old.get("lease_until", "") > now()
            or (verify_snapshot(old.get("business_snapshot")) and old.get("approved_component_source"))):
        reject("manual_review_recovery_not_eligible")
    last = old.get("last_error")
    if ((isinstance(last, dict) and last.get("code") not in {None, "review_completion_source_changed"})
            or old.get("resume_last_error") not in {None, "review_completion_source_changed"}):
        reject("manual_review_recovery_not_eligible")
    source = await db.unified_orders.find_one(selector) or {}
    if not reviewed(source):
        reject("manual_review_recovery_status_changed")
    workflow = await db[WORKFLOWS].find_one(selector)
    # This procedure restores missing review evidence, never downstream stages.
    if workflow and workflow.get("stage") not in {None, "pending_review"}:
        reject("manual_review_recovery_already_reviewed")
    latest = await db[OPERATIONS].find_one({**selector, "manual_review_recovery.old_operation_id": old["_id"]}, sort=[("created_at", -1)])
    if latest and latest.get("state") != "requires_review":
        reject("manual_review_recovery_already_started")
    return old, source, workflow


def component_basis(row):
    return {key: row.get(key) for key in (
        "generation", "source_fingerprint", "source_created_at", "source_updated_at", "cancelled", "eligible",
    )}


async def validate_session(scoped, session, *, operation_exists=False):
    """Run inside the engine's source/config-fenced owner transaction."""
    from order_review_completion import OPERATIONS, WORKFLOWS, _digest, workflow_fingerprint
    from order_engine.repository import MongoOrderRepository
    from order_engine.service import get_order
    from product_fulfillment_rules import order_is_active, payment_is_eligible
    from fulfillment_v2_routes import COMPONENT_LIFECYCLES

    if not operation_exists and session["expires_at"] <= now():
        reject("manual_review_recovery_preview_expired")
    old = await scoped[OPERATIONS].find_one({"_id": session["old_operation_id"], "user_id": session["user_id"]})
    if (not old or _digest(old) != session["old_operation_digest"]
            or old.get("state") != "requires_review"
            or old.get("resume_block_reason") != "review_completion_source_changed"):
        reject("manual_review_recovery_original_changed")
    predecessor = session.get("previous_manual_operation_id")
    if predecessor:
        previous = await scoped[OPERATIONS].find_one({"_id": predecessor, "user_id": session["user_id"]})
        if not previous or previous.get("state") != "requires_review" or _digest(previous) != session["previous_manual_operation_digest"]:
            reject("manual_review_recovery_original_changed")
    selector = {"user_id": session["user_id"], "order_number": session["order_number"]}
    source = await scoped.unified_orders.find_one(selector) or {}
    order = await get_order(MongoOrderRepository(scoped), **selector)
    acceptance = await acceptance_snapshot(scoped, user_id=session["user_id"], order=order)
    if acceptance != session["acceptance_snapshot"]:
        reject("component_acceptance_changed")
    if not reviewed(source) or not order_is_active(order) or not payment_is_eligible(order.payment):
        reject("manual_review_recovery_order_ineligible")
    comparison = compare_snapshots(session["business_snapshot"],
        build_snapshot(source, order, acceptance, identity=session["approval_identity"]))
    if not comparison["equal"]:
        reject(comparison["code"])
    watermark = source.get("g47_salla_snapshot") or {}
    if (watermark.get("cancelled") or watermark.get("requires_authoritative_refresh")
            or watermark.get("component_pending") or watermark.get("revision") != session["source_revision"]):
        reject("component_source_event_stale")
    lifecycle = await scoped[COMPONENT_LIFECYCLES].find_one(selector) or {}
    if component_basis(lifecycle) != session["component_basis"]:
        reject("component_acceptance_changed")
    workflow = await scoped[WORKFLOWS].find_one(selector)
    if workflow_fingerprint(workflow) != session["workflow_fingerprint"]:
        reject("review_revision_conflict")


async def verify_provider(db, owner, order, session):
    """Read back full approved provider facts. There is deliberately no POST."""
    import order_review_routes as routes
    from order_review_completion import guard_provider_request, PROVIDER_CALL_TIMEOUT_SECONDS
    from order_engine.mapper import map_salla_order
    internal = str(order.source.source_order_id or order.order_id)

    async def get(path, **kwargs):
        await guard_provider_request("GET")
        return await asyncio.wait_for(routes.call_salla(db, owner, "GET", path, **kwargs),
                                      timeout=PROVIDER_CALL_TIMEOUT_SECONDS)

    response = await get(f"/orders/{internal}")
    raw = response.get("data") if isinstance(response, dict) else None
    if not isinstance(raw, dict) or str(raw.get("id")) != internal:
        reject("manual_review_recovery_provider_unverified")
    items = []
    for page in range(1, 101):
        response = await get("/orders/items", params={"order_id": internal, "page": page})
        rows = response.get("data") if isinstance(response, dict) else None
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            reject("manual_review_recovery_provider_unverified")
        items.extend(rows)
        pagination = response.get("pagination") or {}
        total = int(pagination.get("totalPages") or pagination.get("total_pages") or pagination.get("last_page") or 1)
        if page >= total:
            break
    else:
        reject("manual_review_recovery_provider_unverified")
    raw = {**raw, "items": items}
    from order_engine.salla_refresh import _enrich_order_receiving_bank
    raw = await _enrich_order_receiving_bank(db, owner, raw)
    source = {"raw_by_source": {"salla_direct": raw}}
    if not reviewed(source):
        reject("manual_review_recovery_status_changed")
    dto = map_salla_order(raw)
    if dto.order_number != order.order_number:
        reject("manual_review_recovery_provider_unverified")
    current = build_snapshot(source, dto, session["acceptance_snapshot"], identity=session["approval_identity"])
    result = compare_snapshots(session["provider_snapshot"], current)
    if not result["equal"]:
        reject(result["code"])
    return "sent", None


class Prepare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    old_operation_id: str = Field(pattern=r"^review_[a-f0-9]{64}$")


class Confirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(pattern=r"^manual_[a-f0-9]{32}$")
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: StrictBool


def register_manual_recovery_routes(router, db, current_user):
    import order_review_routes as routes
    from order_review_completion import OPERATIONS, WORKFLOWS, _digest, approval_identity, workflow_fingerprint, order_fingerprint
    from order_engine.repository import MongoOrderRepository
    from order_engine.service import get_order
    from order_engine.mapper import map_salla_order
    from order_engine.salla_refresh import refresh_order_from_salla
    from fulfillment_v2_routes import (build_order_fulfillment_decision, reconcile_component_order_lifecycle,
                                      assert_component_acceptance, COMPONENT_LIFECYCLES, ensure_fulfillment_indexes)
    from review_acceptance_config_guard import FENCES

    @router.get("/manual-recovery/candidates")
    async def candidates(user=Depends(current_user)):
        owner = routes._merchant_user_id(routes._require_reviewer(user))
        result = []
        for number in sorted(RECOVERY_ORDERS):
            try:
                old, _, _ = await candidate(db, owner, number)
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                continue
            result.append({"order_number": number, "old_operation_id": old["_id"]})
        return {"items": result}

    @router.post("/{order_number}/manual-recovery/prepare")
    async def prepare(order_number: str, payload: Prepare, user=Depends(current_user)):
        actor = routes._require_reviewer(user)
        owner = routes._merchant_user_id(actor)
        await candidate(db, owner, order_number, payload.old_operation_id)
        await routes.enforce_stage_instructions(db, user_id=owner, order_number=order_number,
                                               actor_id=str(actor["id"]), stage="pending_review", order_wide=True)
        refreshed = await refresh_order_from_salla(db, owner, order_number, force=True,
                                                 allow_auto_fulfillment=False, include_review_evidence=True)
        raw = refreshed.get("review_authoritative_payload")
        if not refreshed.get("ok") or not refreshed.get("updated") or not isinstance(raw, dict):
            reject("manual_review_recovery_refresh_failed")
        if not reviewed({"raw_by_source": {"salla_direct": raw}}):
            reject("manual_review_recovery_status_changed")
        await routes._ensure_indexes(db)
        await ensure_fulfillment_indexes(db)
        # Catalog enrichment performs GET/cache writes and must stay outside the
        # restricted owner transaction. Bind its identities to the same DTO.
        hydrated = await get_order(MongoOrderRepository(db), user_id=owner, order_number=order_number)
        identities = await routes._review_item_identities(db, owner, hydrated)
        session_id = "manual_" + uuid.uuid4().hex

        async def freeze(scoped):
            await scoped[FENCES].update_one({"_id": owner}, {"$inc": {"fence": 1}}, upsert=True)
            selector = {"user_id": owner, "order_number": order_number}
            await scoped.unified_orders.update_one(selector, {"$inc": {"review_completion_source_fence": 1}})
            old, source, workflow = await candidate(scoped, owner, order_number, payload.old_operation_id)
            if source.get("orders_v2_salla_refreshed_at") != refreshed.get("refreshed_at"):
                reject("component_source_event_stale")
            order = await get_order(MongoOrderRepository(scoped), **selector)
            if order_fingerprint(order) != order_fingerprint(hydrated):
                reject("review_completion_source_changed")
            acceptance = await acceptance_snapshot(scoped, user_id=owner, order=order)
            from stock_component_consumption_service import PLANS
            if await scoped[PLANS].find_one({"user_id": owner, "order_id": order_number}):
                keys = ("products", "product_bindings", "option_bindings", "resources")
                approved_recipe = old.get("acceptance_snapshot") or {}
                if any(key not in approved_recipe or approved_recipe[key] != acceptance[key] for key in keys):
                    reject("component_plan_reapproval_required")
            items = await routes.freeze_review_items(scoped, owner, order, workflow, actor, identities=identities)
            watermark = source.get("g47_salla_snapshot") or {}
            decision = await build_order_fulfillment_decision(scoped, user_id=owner, order=order,
                operational_items=(workflow or {}).get("operational_items") or [], review_items=items)
            ticket = await reconcile_component_order_lifecycle(scoped, user_id=owner, order=order,
                actor_id=str(actor["id"]), decision=decision, strict=True,
                source_revision=watermark.get("revision"), source_updated_at=watermark.get("source_updated_at"))
            await assert_component_acceptance(scoped, ticket=ticket)
            if decision.get("ready_to_ship"):
                reject("manual_review_recovery_not_review_route")
            source = await scoped.unified_orders.find_one(selector)
            lifecycle = await scoped[COMPONENT_LIFECYCLES].find_one(selector) or {}
            previous = await scoped[OPERATIONS].find_one({**selector, "manual_review_recovery.old_operation_id": old["_id"]}, sort=[("created_at", -1)])
            identity = approval_identity({"_id": "review_" + _digest([REASON, owner, order_number, old["_id"], (previous or {}).get("_id")]),
                **selector, "revision": int((workflow or {}).get("revision") or 0),
                "workflow_fingerprint": workflow_fingerprint(workflow)})
            snapshot = build_snapshot(source, order, acceptance, identity=identity)
            # Refresh can preserve richer old containers. Do not let that
            # overlay silently turn absent/current provider facts into approval.
            same_source = compare_snapshots(snapshot, build_snapshot(
                {"raw_by_source": {"salla_direct": raw}}, order, acceptance, identity=identity))
            if not same_source["equal"]:
                reject("manual_review_recovery_authoritative_mismatch")
            provider_snapshot = build_snapshot({"raw_by_source": {"salla_direct": raw}},
                map_salla_order(raw), acceptance, identity=identity)
            session = {"_id": session_id, **selector, "actor_id": str(actor["id"]),
                "actor_name": routes._text(actor.get("name") or actor.get("email")),
                "old_operation_id": old["_id"], "old_operation_digest": _digest(old),
                "previous_manual_operation_id": (previous or {}).get("_id"),
                "previous_manual_operation_digest": _digest(previous) if previous else None,
                "approval_identity": identity, "workflow_fingerprint": workflow_fingerprint(workflow),
                "business_snapshot": snapshot, "provider_snapshot": provider_snapshot,
                "acceptance_snapshot": acceptance, "source_revision": watermark.get("revision"),
                "component_basis": component_basis(lifecycle), "items": items,
                "created_at": now(), "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS)).isoformat(),
                "source": "salla_order_details_and_items", "reason": REASON}
            await validate_session(scoped, session)
            await scoped[SESSIONS].insert_one(session)
            return {"session_id": session_id, "approval_hash": snapshot["integrity_hash"],
                "old_operation_id": old["_id"], "order_number": order_number, "expires_at": session["expires_at"],
                "preview": {"order": order.model_dump(mode="json"), "items": items,
                    "components": {"state": lifecycle.get("state"), "accepted": ticket.get("accepted"),
                                   "generation": lifecycle.get("generation"), "items": [
                                       {key: line.get(key) for key in ("name", "sku", "quantity", "forcing_services", "requires_preparation", "order_specifications")}
                                       for line in decision.get("lines", [])]},
                    "eligibility": {"accepted": True, "source": True, "component": True}, "source": session["source"]}}
        return await operational_owner(db, owner, freeze)

    @router.post("/{order_number}/manual-recovery/confirm")
    async def confirm(order_number: str, payload: Confirm, user=Depends(current_user)):
        from order_review_completion import complete_review_operation
        actor = routes._require_reviewer(user)
        owner = routes._merchant_user_id(actor)
        if payload.confirmed is not True:
            reject("manual_review_recovery_confirmation_required")
        session = await db[SESSIONS].find_one({"_id": payload.session_id, "user_id": owner,
            "order_number": order_number, "actor_id": str(actor["id"])})
        if (not session or not verify_snapshot(session["business_snapshot"])
                or payload.approval_hash != session["business_snapshot"]["integrity_hash"]):
            reject("manual_review_recovery_approval_mismatch")

        async def load(scoped):
            return await get_order(MongoOrderRepository(scoped), user_id=owner, order_number=order_number)

        async def instructions(scoped):
            await routes.enforce_stage_instructions(scoped, user_id=owner, order_number=order_number,
                actor_id=str(actor["id"]), stage="pending_review", order_wide=True)

        order = await load(db)
        return await complete_review_operation(db, user_id=owner, actor_id=str(actor["id"]),
            actor_name=session["actor_name"], order=order,
            workflow=await db[WORKFLOWS].find_one({"user_id": owner, "order_number": order_number}),
            frozen_items=session["items"], revision=session["approval_identity"]["revision"], load_order=load,
            sync_salla=lambda current: verify_provider(db, owner, current, session), enforce_instructions=instructions,
            source_snapshot=await db.unified_orders.find_one({"user_id": owner, "order_number": order_number}),
            approved_acceptance=session["acceptance_snapshot"], manual_session_id=session["_id"])
