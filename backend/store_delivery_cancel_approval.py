"""Owner/operator approval of a courier's saved customer cancellation request.

Feature is OFF until the cancellation/Salla/driver-race integration gates pass.
No delivered order, confirmed collection, courier earning or settlement can be
silently cancelled or reversed by this route.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from store_delivery_handover_routes import ASSIGNMENTS, EVENTS, ORDERS
from store_courier_domain import WORKFLOWS
from store_delivery_driver_app_routes import _call_salla, _salla_order_id
from store_delivery_domain import normalize_text

_CANCEL = "customer_requested_cancel"
_FINAL = "canceled"
_FINANCIAL_COLLECTIONS = (
    "store_delivery_collections",
    "store_delivery_driver_earnings",
    "store_delivery_payment_reviews",
    "store_delivery_driver_settlements",
    "store_delivery_receipts",
    "store_delivery_delivery_proofs",
)
_ACTIVE = ("assigned", "out_for_delivery")
_ALLOWED_SALLA = frozenset(("in_progress", "completed", "delivering", "shipped"))
_CANCELED_SALLA = frozenset(("canceled", "cancelled"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _flag_enabled() -> bool:
    # Explicitly fail closed until cross-app/driver-race and Salla acceptance.
    return os.environ.get("AMASI_COURIER_CANCELLATION_APPROVAL_ENABLED") == "1"


class ApproveCourierCancellation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_driver_id: str = Field(min_length=1, max_length=80)
    request_id: str = Field(min_length=8, max_length=150)


def _salla_status(response: dict[str, Any]) -> tuple[str, str, str]:
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict):
        return "", "", ""
    status = data.get("status")
    slug = normalize_text(status.get("slug")) if isinstance(status, dict) else ""
    return normalize_text(data.get("id")), normalize_text(data.get("reference_id")), slug


async def _read_salla(db, *, owner: str, order_id: str) -> tuple[str, str]:
    try:
        response = await _call_salla(
            db, owner, "GET", f"/orders/{order_id}", params={"format": "light"},
        )
    except Exception:
        raise HTTPException(status_code=503, detail={"code": "cancel_salla_read_unavailable"})
    rid, reference, slug = _salla_status(response)
    if not rid or rid != order_id or not slug:
        raise HTTPException(status_code=502, detail={"code": "cancel_salla_identity_unverified"})
    return reference, slug


def _assert_cancel_allowed(assignment: dict, order: dict, workflow: dict) -> None:
    if assignment.get("active") is not True or normalize_text(assignment.get("status")) not in _ACTIVE:
        raise HTTPException(409, detail={"code": "cancel_assignment_not_active"})
    if normalize_text(assignment.get("delivery_exception_code")) != _CANCEL:
        raise HTTPException(409, detail={"code": "cancel_driver_request_missing"})
    if (
        normalize_text(order.get("store_delivery_assignment_id")) != normalize_text(assignment.get("id"))
        or normalize_text(order.get("store_delivery_driver_id")) != normalize_text(assignment.get("driver_id"))
        or normalize_text(workflow.get("store_delivery_assignment_id")) != normalize_text(assignment.get("id"))
        or normalize_text(workflow.get("store_courier_driver_profile_id")) != normalize_text(assignment.get("driver_id"))
    ):
        raise HTTPException(409, detail={"code": "cancel_assignment_projection_changed"})
    if any(normalize_text(assignment.get(field)) for field in (
        "delivered_at", "collection_method", "collection_amount", "delivery_proof_reference",
        "receipt_reference", "physical_cash_amount", "physical_cash_confirmed",
        "financial_handoff_status", "pending_delivery_completion",
    )):
        raise HTTPException(409, detail={"code": "cancel_financial_evidence_requires_review"})
    if normalize_text(workflow.get("store_courier_delivered_at")):
        raise HTTPException(409, detail={"code": "cancel_already_delivered"})


async def _assert_no_financial_entries(db: Any, owner: str, assignment_id: str, *, session=None) -> None:
    for collection in _FINANCIAL_COLLECTIONS:
        if await db[collection].find_one(
            {"user_id": owner, "assignment_id": assignment_id}, {"_id": 1}, session=session,
        ):
            raise HTTPException(409, detail={
                "code": "cancel_financial_evidence_requires_review",
                "collection": collection,
            })
    # Customer-conversation photos are NOT financial evidence. They must survive.


async def _finish_local_cancellation(
    db: Any, *, owner: str, assignment_id: str, driver_id: str, request_id: str, actor_id: str,
) -> dict:
    now = _now()
    try:
        async with await db.client.start_session() as tx:
            async with tx.start_transaction():
                a = await db[ASSIGNMENTS].find_one(
                    {"user_id": owner, "id": assignment_id, "driver_id": driver_id,
                     "active": True, "status": {"$in": list(_ACTIVE)},
                     "cancel_approval_state": "reserved",
                     "cancel_approval_request_id": request_id},
                    {"_id": 0}, session=tx,
                )
                if not a:
                    done = await db[ASSIGNMENTS].find_one(
                        {"user_id": owner, "id": assignment_id, "driver_id": driver_id,
                         "active": False, "status": _FINAL,
                         "cancel_approval_request_id": request_id},
                        {"_id": 0}, session=tx,
                    )
                    if done:
                        return {"ok": True, "status": "completed", "assignment_id": assignment_id,
                                "courier_fee_due": 0, "courier_cod_due": 0}
                    raise HTTPException(409, detail={"code": "cancel_assignment_changed"})
                order_query = {
                    "user_id": owner, "store_delivery_assignment_id": assignment_id,
                    "store_delivery_driver_id": driver_id,
                    "$or": [{"order_id": a.get("order_id")}, {"order_number": a.get("order_number")}],
                }
                o = await db[ORDERS].find_one(order_query, {"_id": 0}, session=tx)
                w_query = {
                    "user_id": owner, "order_number": a.get("order_number"),
                    "store_delivery_assignment_id": assignment_id,
                    "store_courier_driver_profile_id": driver_id,
                }
                w = await db[WORKFLOWS].find_one(w_query, {"_id": 0}, session=tx)
                if not o or not w:
                    raise HTTPException(409, detail={"code": "cancel_assignment_projection_changed"})
                await _assert_no_financial_entries(db, owner, assignment_id, session=tx)
                if any(normalize_text(a.get(field)) for field in (
                    "delivered_at", "collection_method", "collection_amount",
                    "receipt_reference", "delivery_proof_reference",
                )):
                    raise HTTPException(409, detail={"code": "cancel_financial_evidence_requires_review"})
                old_status = normalize_text(a.get("cancel_previous_delivery_status"))
                updated = await db[ASSIGNMENTS].update_one(
                    {"user_id": owner, "id": assignment_id, "driver_id": driver_id,
                     "active": True, "status": {"$in": list(_ACTIVE)},
                     "cancel_approval_state": "reserved",
                     "cancel_approval_request_id": request_id},
                    {"$set": {
                        "active": False, "status": _FINAL,
                        "cancel_approval_state": "completed",
                        "canceled_at": now, "canceled_by": actor_id,
                        "courier_fee_due": 0, "courier_cod_due": 0, "updated_at": now,
                        "physical_return_pending": old_status == "out_for_delivery",
                    }},
                    session=tx,
                )
                if updated.modified_count != 1:
                    raise HTTPException(409, detail={"code": "cancel_assignment_changed"})
                order_updated = await db[ORDERS].update_one(
                    order_query,
                    {"$set": {
                        "store_delivery_status": _FINAL, "order_status_slug": "canceled",
                        "store_delivery_canceled_at": now, "store_delivery_canceled_by": actor_id,
                        "store_delivery_courier_fee_due": 0, "store_delivery_courier_cod_due": 0,
                        "store_delivery_updated_at": now,
                    }},
                    session=tx,
                )
                if order_updated.modified_count != 1:
                    raise HTTPException(409, detail={"code": "cancel_order_projection_changed"})
                workflow_updated = await db[WORKFLOWS].update_one(
                    w_query,
                    {"$set": {
                        "stage": "cancelled", "store_courier_assignment_state": _FINAL,
                        "store_courier_canceled_at": now, "store_courier_canceled_by": actor_id,
                        "store_courier_fee_due": 0, "store_courier_cod_due": 0, "updated_at": now,
                    }},
                    session=tx,
                )
                if workflow_updated.modified_count != 1:
                    raise HTTPException(409, detail={"code": "cancel_workflow_projection_changed"})
                await db[EVENTS].insert_one({
                    "id": str(uuid.uuid4()), "user_id": owner,
                    "event_type": "store_delivery_customer_cancellation_approved",
                    "assignment_id": assignment_id, "order_id": a.get("order_id"),
                    "order_number": a.get("order_number"), "driver_id": driver_id,
                    "actor_id": actor_id, "request_id": request_id,
                    "prior_status": old_status, "courier_fee_due": 0, "courier_cod_due": 0,
                    "physical_return_pending": old_status == "out_for_delivery",
                    "occurred_at": now,
                }, session=tx)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, detail={"code": "cancel_local_finalization_pending"}) from exc
    return {"ok": True, "status": "completed", "assignment_id": assignment_id,
            "courier_fee_due": 0, "courier_cod_due": 0}


def attach_cancel_approval_route(
    router: APIRouter, db: Any, current_user: Callable[..., Any],
    *, require_operator: Callable, merchant_user_id: Callable,
) -> None:
    @router.get("/cancellation-capability")
    async def cancellation_capability(user: dict = Depends(current_user)) -> dict:
        actor = require_operator(user)
        merchant_user_id(actor)  # Staff permission and tenant binding must exist.
        return {"contract_version": 1, "enabled": _flag_enabled(),
                "salla_canceled_slug": "canceled",
                "confirmation_required": True,
                "automatic_retries": False}

    @router.post("/{assignment_id}/approve-cancellation")
    async def approve_cancellation(
        assignment_id: str, payload: ApproveCourierCancellation,
        user: dict = Depends(current_user),
    ) -> dict:
        if not _flag_enabled():
            raise HTTPException(503, detail={"code": "cancel_approval_release_gate_closed"})
        actor = require_operator(user)
        owner = merchant_user_id(actor)
        actor_id = normalize_text(actor.get("id"))
        aid = normalize_text(assignment_id)
        driver_id = normalize_text(payload.expected_driver_id)
        request_id = normalize_text(payload.request_id)
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,150}", request_id):
            raise HTTPException(422, detail={"code": "cancel_request_id_invalid"})
        a = await db[ASSIGNMENTS].find_one(
            {"user_id": owner, "id": aid, "driver_id": driver_id}, {"_id": 0},
        )
        if not a:
            raise HTTPException(404, detail={"code": "cancel_assignment_not_found"})
        if a.get("active") is False and a.get("status") == _FINAL:
            if a.get("cancel_approval_request_id") == request_id:
                return {"ok": True, "status": "completed", "assignment_id": aid,
                        "courier_fee_due": 0, "courier_cod_due": 0}
            raise HTTPException(409, detail={"code": "cancel_already_completed_by_other_request"})
        order = await db[ORDERS].find_one(
            {"user_id": owner, "store_delivery_assignment_id": aid,
             "$or": [{"order_id": a.get("order_id")}, {"order_number": a.get("order_number")}]},
            {"_id": 0},
        )
        workflow = await db[WORKFLOWS].find_one(
            {"user_id": owner, "order_number": a.get("order_number"),
             "store_delivery_assignment_id": aid}, {"_id": 0},
        )
        if not order or not workflow:
            raise HTTPException(409, detail={"code": "cancel_assignment_projection_changed"})
        sid = _salla_order_id(order, a)
        if not sid or not sid.isdecimal():
            raise HTTPException(409, detail={"code": "cancel_salla_order_id_unverified"})
        if a.get("cancel_approval_state") == "reserved":
            if a.get("cancel_approval_request_id") != request_id:
                raise HTTPException(409, detail={"code": "cancel_other_approval_in_progress"})
            # Deliberately GET-only on recovery: the one external POST could
            # have reached Salla before a network timeout or process restart.
            _, slug = await _read_salla(db, owner=owner, order_id=sid)
            if slug not in _CANCELED_SALLA:
                return {"ok": False, "status": "awaiting_salla_confirmation",
                        "assignment_id": aid, "requires_review": True}
            return await _finish_local_cancellation(
                db, owner=owner, assignment_id=aid, driver_id=driver_id,
                request_id=request_id, actor_id=actor_id,
            )
        _assert_cancel_allowed(a, order, workflow)
        await _assert_no_financial_entries(db, owner, aid)
        reference, slug = await _read_salla(db, owner=owner, order_id=sid)
        if reference and reference != normalize_text(a.get("order_number")):
            raise HTTPException(409, detail={"code": "cancel_salla_reference_mismatch"})
        if slug not in _ALLOWED_SALLA and slug not in _CANCELED_SALLA:
            raise HTTPException(409, detail={"code": "cancel_salla_status_not_eligible", "salla_status": slug})
        # A reserved cancellation approval freezes driver delivery transitions,
        # but the original status and active assignment remain visible in the
        # courier app until Salla confirms cancellation and Mongo commits.
        claimed = await db[ASSIGNMENTS].update_one(
            {"user_id": owner, "id": aid, "driver_id": driver_id,
             "active": True, "status": a["status"],
             "delivery_exception_code": _CANCEL,
             "cancel_approval_state": {"$nin": ["reserved", "completed"]},
             "delivered_at": {"$in": [None, ""]}},
            {"$set": {
                "cancel_previous_delivery_status": a["status"],
                "cancel_approval_state": "reserved",
                "cancel_approval_request_id": request_id,
                "cancel_approval_actor_id": actor_id, "cancel_approval_started_at": _now(),
            }},
        )
        if claimed.modified_count != 1:
            raise HTTPException(409, detail={"code": "cancel_concurrent_status_changed"})
        if slug not in _CANCELED_SALLA:
            try:
                # Single POST: no network retry or automatic redelivery.
                await _call_salla(
                    db, owner, "POST", f"/orders/{sid}/status",
                    json={"slug": "canceled", "send_status_sms": False},
                    single_post_attempt=True,
                )
            except Exception:
                # A timeout might still mean the Salla update succeeded.
                # Keep the status reservation and reconcile on next request.
                return {"ok": False, "status": "awaiting_salla_confirmation",
                        "assignment_id": aid, "requires_review": True}
        _, observed = await _read_salla(db, owner=owner, order_id=sid)
        if observed not in _CANCELED_SALLA:
            return {"ok": False, "status": "awaiting_salla_confirmation",
                    "assignment_id": aid, "requires_review": True}
        return await _finish_local_cancellation(
            db, owner=owner, assignment_id=aid, driver_id=driver_id,
            request_id=request_id, actor_id=actor_id,
        )
