"""Explicit visible-page refresh; report GETs remain snapshot readers."""
import asyncio
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
import pymongo
from starlette.requests import Request

from operational_balance_store import STATES, digest, fail
from operational_demand_refresh import demand_context, refresh_stage, run_visible_refresh


_RECEIPT_FAILURES = frozenset({
    (408, "operational_refresh_timeout"),
    (409, "operational_source_setup_incomplete"),
    (409, "operational_capacity_requires_archive"),
})
_RECEIPT_VERIFY_SECONDS = 1
_COURIER_OUTCOMES = frozenset({
    "saved", "unchanged", "already_eligible", "blocked", "unavailable", "source_changed",
})
_COURIER_REASONS = frozenset({
    "native_evidence_unavailable", "native_evidence_access_denied", "native_evidence_request_timeout",
    "courier_native_response_invalid",
    "courier_native_pagination_missing", "courier_native_pagination_incomplete",
    "courier_native_page_repeated", "courier_native_shipment_ambiguous",
    "courier_native_shipment_mismatch", "courier_native_history_order_mismatch",
    "courier_native_timeline_conflict", "courier_native_clock_in_future",
    "courier_native_source_conflict", "courier_native_order_mismatch",
    "courier_native_order_transition_missing", "courier_native_order_not_delivered",
    "courier_native_source_refresh_required", "courier_native_source_version_mismatch",
    "courier_native_shipment_identity_missing", "courier_native_carrier_missing",
    "courier_native_evidence_invalid", "courier_native_source_changed",
})


def _target_receipt(native_evidence, order_id):
    """Copy only a fully returned, bounded result for the requested order."""
    if order_id is None or type(native_evidence) is not dict:
        return None
    courier = native_evidence.get("courier")
    if type(courier) is not dict:
        return None
    status, selected, outcomes = (courier.get(key) for key in ("status", "selected", "outcomes"))
    if (type(status) is not str or status not in {"completed", "deferred"}
            or type(selected) is not int or selected not in {0, 1}
            or type(outcomes) is not list or len(outcomes) != selected):
        return None
    safe = []
    if selected:
        outcome = outcomes[0]
        if (type(outcome) is not dict or outcome.get("order_id") != order_id
                or type(outcome.get("status")) is not str or outcome["status"] not in _COURIER_OUTCOMES):
            return None
        row = {"order_id": order_id, "status": outcome["status"]}
        reason = outcome.get("reason")
        if type(reason) is str and reason in _COURIER_REASONS:
            row["reason"] = reason
        safe.append(row)
    return {"status": status, "selected": selected, "outcomes": safe}


class VisibleRefreshInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    visible: Literal[True]
    expected_session_scope: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    courier_order_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")

    @field_validator("visible", mode="before")
    @classmethod
    def require_visible_page(cls, value):
        if value is not True:
            raise ValueError("visible page required")
        return value


def attach_demand_refresh_routes(router, db, current_user, scope):
    @router.post("/refresh")
    async def refresh_visible(payload: VisibleRefreshInput, request: Request,
                              user=Depends(current_user)):
        from operational_balance_service import active_gate, refresh

        actor, owner, _ = await scope(db, user, "reports")
        actor_id = actor["id"]
        session_scope = digest([owner, actor_id])
        if payload.courier_order_id is not None and payload.expected_session_scope is None:
            fail("operational_actor_scope_required", "أعد فتح التقرير للتحقق من ارتباط الحساب قبل مطابقة الطلب", 400)
        if payload.expected_session_scope is not None and payload.expected_session_scope != session_scope:
            fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ أعد فتح الصفحة", 403)

        async def gate_state():
            fresh_actor, fresh_owner, _ = await scope(db, user, "reports")
            if (fresh_owner, fresh_actor["id"]) != (owner, actor_id):
                fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ أعد فتح الصفحة", 403)
            # Authorization needs status metadata, not repeated archive decode.
            return await db[STATES].find_one(
                {"_id": owner, "owner_id": owner},
                {"status": 1, "revision": 1, "engine.as_of": 1}) or {"status": "draft", "revision": 0}

        async def verify():
            await active_gate(db, owner, await gate_state())

        if await request.is_disconnected():
            fail("operational_refresh_cancelled", "أُلغي تحديث الصفحة", 499)
        metadata = await gate_state()
        try:
            await active_gate(db, owner, metadata)
        except HTTPException as error:
            code = error.detail.get("code") if isinstance(error.detail, dict) else None
            if code not in {"operational_not_active", "operational_accounting_active"}:
                raise
            return {"status": "accounting_active" if code == "operational_accounting_active" else metadata["status"],
                    "as_of": metadata.get("engine", {}).get("as_of"),
                    "revision": metadata.get("revision", 0), "session_scope": session_scope}

        completed_receipt = None
        refresh_context = None

        async def update():
            nonlocal completed_receipt, refresh_context
            from operational_visible_evidence import acquire_visible_evidence
            refresh_context = demand_context()
            with refresh_stage("native_evidence"):
                options = {"courier_order_id": payload.courier_order_id} if payload.courier_order_id is not None else {}
                native_evidence = await acquire_visible_evidence(db, owner, **options)
            # This assignment happens only after acquisition returns. A saved
            # outcome is supplied by the adapter after its source-proof CAS.
            completed_receipt = _target_receipt(native_evidence, payload.courier_order_id)
            with refresh_stage("aggregate"):
                state = await refresh(db, owner)
            return {"status": "refreshed", "as_of": state.get("engine", {}).get("as_of"),
                    "revision": state.get("revision"), "session_scope": session_scope,
                    "native_evidence": native_evidence}

        try:
            return await run_visible_refresh(request, db, owner, actor_id, verify, update)
        except HTTPException as error:
            detail = error.detail
            if (completed_receipt is None or type(detail) is not dict
                    or type(detail.get("code")) is not str
                    or (error.status_code, detail["code"]) not in _RECEIPT_FAILURES
                    or refresh_context is not None and refresh_context.disconnected
                    or await request.is_disconnected()):
                raise
            # The lease and refresh deadline have ended. Only this optional
            # read-only authorization tail gets a separate one-second budget,
            # including Mongo's socket/server work. It cannot resume finance.
            try:
                with pymongo.timeout(_RECEIPT_VERIFY_SECONDS):
                    async with asyncio.timeout(_RECEIPT_VERIFY_SECONDS):
                        fresh_actor, fresh_owner, _ = await scope(db, user, "reports")
                        if (fresh_owner, fresh_actor["id"]) != (owner, actor_id):
                            fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ أعد فتح الصفحة", 403)
            except HTTPException as recheck_error:
                if recheck_error.status_code in {401, 403}:
                    raise
                raise error from None
            except Exception:
                # Unavailable authorization omits the receipt; it cannot erase
                # the original failure/timings. Cancellation still propagates.
                raise error from None
            if await request.is_disconnected():
                raise
            raise HTTPException(status_code=error.status_code, headers=error.headers,
                detail={**detail, "session_scope": session_scope,
                        "native_evidence": {"courier": completed_receipt}}) from None

    return router
