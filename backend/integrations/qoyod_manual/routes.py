"""Plan-B Manual Send — FastAPI router.

Endpoints (all mounted under /api/integrations/qoyod/manual):
    GET  /pending-orders            List orders eligible for manual push.
    POST /send/{order_number}       Push ONE order end-to-end.
    POST /auto-canary               Run the closed four-order canary.
    GET  /status/{order_number}     Latest manual-send lock row.
    GET  /health                    Frozen-flag + module presence probe.
    POST /freeze-legacy-pipeline    Toggle legacy_pipeline_frozen on/off.

Auth: the caller must be authenticated (uses the same `current_user`
dependency as the rest of the qoyod router).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from qoyod_order_accounting_sync import repair_qoyod_order_accounting

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from integrations.qoyod_manual.pending import list_pending_orders
from integrations.qoyod.orders_owner import orders_owner_id
from integrations.qoyod_manual.send import (
    manual_send_one, ManualSendRefused,
)
from integrations.qoyod_manual.failed_retry import retry_failed_order
from integrations.qoyod_manual.payment_recheck import (
    recheck_payment_batch_read_only,
    recheck_payment_read_only,
)
from integrations.qoyod_manual.diagnose import diagnose_totals
from integrations.qoyod_manual.missing_diagnostics import (
    list_missing_from_plan_b,
)
from integrations.qoyod_manual.audit_sent_count import (
    audit_plan_b_vs_diagnostic,
)
from integrations.qoyod_manual.pending_exclusion_diagnose import (
    diagnose_pending_exclusion,
)
from integrations.qoyod_manual.canary_batch import (
    CANARY_CONFIRMATION,
    CANARY_ORDER_NUMBERS,
    execute_canary_batch,
)

logger = logging.getLogger(__name__)

_TENANT = "main"  # matches the rest of the qoyod router's convention


class FreezeLegacyPipelinePayload(BaseModel):
    """Body for POST /freeze-legacy-pipeline.

    `enabled=True`  → sets legacy_pipeline_frozen=True (freezes worker).
    `enabled=False` → sets it False (worker resumes normal ticks).
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class PendingExclusionDiagPayload(BaseModel):
    """Body for POST /pending-orders/diagnose-exclusion.

    Diagnostic-only. Must be a JSON body — FastAPI needs the class at
    module level so `payload: PendingExclusionDiagPayload` is bound
    to the request body, not to a query parameter.
    """
    model_config = ConfigDict(extra="ignore")
    order_numbers:      list[str]
    status:             str = "delivered"
    days:               int = 60
    limit:              int = 200
    # Optional {order_number: trace_id}. When set for an order, the
    # diagnostic analyses THAT trace instead of the newest one. Used
    # to prove `not_newest_trace` exclusion.
    trace_ids_by_order: dict[str, str] = {}


class CanaryBatchPayload(BaseModel):
    """Explicit confirmation for the closed four-order auto-send canary."""
    model_config = ConfigDict(extra="forbid")
    confirmation: str


class PaymentRecheckBatchPayload(BaseModel):
    """Bounded read-only Salla payment scan; never sends to Qoyod."""
    model_config = ConfigDict(extra="forbid")
    order_numbers: list[str]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def make_qoyod_manual_router(db, current_user) -> APIRouter:
    router = APIRouter(
        prefix="/integrations/qoyod/manual",
        tags=["integrations:qoyod:manual"],
    )

    @router.get("/health")
    async def health(user=Depends(current_user)):
        settings = await db.qoyod_settings.find_one(
            {"user_id": _TENANT},
            {"_id": 0, "legacy_pipeline_frozen": 1,
             "legacy_pipeline_frozen_updated_at": 1,
             "legacy_pipeline_frozen_actor": 1,
             "payment_method_mapping": 1}) or {}
        _upd = settings.get("legacy_pipeline_frozen_updated_at")
        return {
            "ok":                        True,
            "legacy_pipeline_frozen":    bool(settings.get(
                                              "legacy_pipeline_frozen")),
            "legacy_pipeline_frozen_updated_at": (
                _upd.isoformat() if isinstance(_upd, datetime) else _upd),
            "legacy_pipeline_frozen_actor": settings.get(
                "legacy_pipeline_frozen_actor"),
            "payment_method_mapping_count": len(
                settings.get("payment_method_mapping") or []),
            "at":                        _now().isoformat(),
        }

    @router.get("/pending-orders")
    async def pending_orders(
        days: int = Query(60, ge=1, le=365),
        from_date: Optional[str] = Query(None),
        to_date: Optional[str] = Query(None),
        limit: int = Query(500, ge=1, le=1000),
        search: Optional[str] = Query(None),
        status: str = Query(
            "completed",
            description=("Salla status filter: completed | delivered | "
                          "in_delivery. Default = completed. Each tab in "
                          "the UI hits this endpoint with a different "
                          "value.")),
        user=Depends(current_user),
    ):
        # An exact order-number search is also an explicit request to see
        # Salla's current state. Refresh that single order first so a
        # completed -> under_review -> completed recurrence is visible even
        # if the repeated completed webhook was previously deduplicated.
        exact_order = str(search or "").strip()
        if exact_order.isdigit():
            try:
                from salla_integration.sync import resync_single_order
                await resync_single_order(db, orders_owner_id(user), exact_order)
            except Exception:
                # Listing remains available from local evidence when Salla
                # is temporarily unavailable; resync diagnostics live in the
                # standard order-resync endpoint.
                logger.exception(
                    "Plan-B exact-search resync failed order=%s", exact_order)
        return await list_pending_orders(
            db, user_id=_TENANT, orders_user_id=orders_owner_id(user),
            days=days, limit=limit,
            search=search, status=status,
            from_date=from_date, to_date=to_date)

    # ── Diagnostic-only endpoint (temporary, opt-in) ──────────────
    # Read-only. Simulates the exact filter chain of
    # `list_pending_orders` for a caller-supplied list of order
    # numbers and reports the primary exclusion reason (or a positive
    # verdict) for each. REMOVE this route + module + tests once the
    # Plan-B pending list is unified with `list_unsent_orders`.
    #
    # Body binding note: `PendingExclusionDiagPayload` MUST be defined
    # at module level (see top of file). Nesting it inside this
    # closure causes FastAPI to fall back to query-parameter binding
    # and return HTTP 422 "Field required" for `payload` as a query.
    @router.post("/pending-orders/diagnose-exclusion")
    async def diagnose_exclusion(
        payload: PendingExclusionDiagPayload,
        user=Depends(current_user),
    ):
        try:
            return await diagnose_pending_exclusion(
                db, user_id=_TENANT,
                order_numbers=list(payload.order_numbers),
                status=payload.status,
                days=payload.days, limit=payload.limit,
                trace_ids_by_order=dict(payload.trace_ids_by_order or {}),
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            # Return a structured JSON error instead of a bare 500.
            import traceback as _tb
            import uuid as _uuid
            ref = _uuid.uuid4().hex[:8]
            tb_text = _tb.format_exc()
            logger.error(
                "diagnose_exclusion failed ref=%s\n%s", ref, tb_text)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code":              "diagnose_exclusion_failed",
                    "error_reference":   ref,
                    "exception_type":    type(exc).__name__,
                    "exception_message": str(exc)[:500],
                    "traceback_tail":    tb_text.splitlines()[-15:],
                })

    @router.post("/send/{order_number}")
    async def send_one(order_number: str,
                       request: Request,
                       user=Depends(current_user)):
        actor = "manual-ui"
        try:
            username = (user or {}).get("email") or (user or {}).get("id")
            if username:
                actor = f"manual-ui:{username}"
        except Exception:
            pass
        try:
            # A manual recovery request is still an accounting write. Refresh
            # the order from Salla immediately before the Qoyod path and use
            # the same closed three-status gate as the automatic worker. This
            # prevents a stale eligible inbox row from posting an order that
            # has since moved to a cancelled/refunded state.
            from integrations.qoyod_manual.auto_send import (
                _refresh_and_verify_salla_status,
            )
            orders_user = orders_owner_id(user)
            still_eligible, refresh = await _refresh_and_verify_salla_status(
                db,
                orders_user_id=orders_user,
                order_number=str(order_number),
            )
            if not still_eligible:
                snapshot = refresh.get("plan_b_status_snapshot") or {}
                raise ManualSendRefused(
                    "not_qoyod_eligible_status",
                    "حالة الطلب الحالية في سلة ليست ضمن الحالات المسموحة "
                    "(تم التنفيذ / جاري التوصيل / تم التوصيل)",
                    {
                        "order_number": str(order_number),
                        "current_status": (
                            snapshot.get("status_native")
                            or snapshot.get("status_slug")
                        ),
                    },
                )
            result = await manual_send_one(
                db, user_id=_TENANT, orders_user_id=orders_user,
                order_number=str(order_number),
                actor=actor,
                # This operator-confirmed path is bounded to explicit order
                # numbers and already rechecks the live native Salla status.
                # Qoyod invoice/payment dates remain the send date.
                allow_missing_salla_order_date=True,
                allow_historical_positive_total=True)
            return result
        except ManualSendRefused as exc:
            # 409 = business-rule / guard refusal.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=exc.to_dict())
        except HTTPException:
            # Never wrap explicit HTTPException in the diagnostic path.
            raise
        except Exception as exc:  # noqa: BLE001
            # ── Diagnostic Mode (temporary, opt-in) ────────────────
            # By default the operator sees a plain HTTP 500 with no
            # payload — the full Python traceback goes ONLY to the
            # backend logger. This preserves production hygiene.
            #
            # To surface the traceback in the API response for a
            # single call, either:
            #   • append `?diag=1` to the URL
            #   • or send header `X-Debug-Diagnostic: 1`
            # Both routes require the authenticated `Depends(current_user)`,
            # so a random visitor cannot trigger the diagnostic payload.
            #
            # REMOVE THIS ENTIRE except-block once the root cause of
            # the current 500 is found and fixed.
            import traceback as _tb
            import uuid as _uuid
            ref = _uuid.uuid4().hex[:8]
            tb_text = _tb.format_exc()
            logger.exception(
                "manual/send unhandled_exception order=%s ref=%s",
                order_number,
                ref,
            )

            # Never leave a manual-send lock hanging after the request
            # has already terminated with an unexpected exception.
            try:
                await db.qoyod_manual_send_locks.update_many(
                    {
                        "order_number": str(order_number),
                        "user_id": _TENANT,
                        "status": "in_progress",
                    },
                    {
                        "$set": {
                            "status": "failed_unhandled",
                            "finished_at": datetime.now(timezone.utc),
                            "last_error": {
                                "code": "unhandled_exception",
                                "error_reference": ref,
                                "exception_type": type(exc).__name__,
                                "exception_message": str(exc)[:500],
                                "traceback_tail":
                                    tb_text.splitlines()[-15:],
                            },
                        }
                    },
                )
            except Exception:
                logger.exception(
                    "failed to release manual-send lock "
                    "order=%s ref=%s",
                    order_number,
                    ref,
                )

            diag_flag = False
            try:
                if str(request.query_params.get("diag") or "") == "1":
                    diag_flag = True
                if request.headers.get("X-Debug-Diagnostic") == "1":
                    diag_flag = True
            except Exception:
                diag_flag = False
            if not diag_flag:
                # Production-hygiene default: HTTP 500, no body detail.
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={"error_reference": ref})
            # Diagnostic on: return the last few frames only.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code":              "unhandled_exception",
                    "error_reference":   ref,
                    "exception_type":    type(exc).__name__,
                    "exception_message": str(exc)[:500],
                    "traceback_tail":    tb_text.splitlines()[-20:],
                    "diagnostic_mode":   True,
                    "note":              ("هذا الرد التشخيصي مؤقّت — "
                                           "يُحذَف من الكود بعد إصلاح "
                                           "السبب الجذري."),
                })

    @router.post("/auto-canary")
    async def auto_canary(
        payload: CanaryBatchPayload,
        user=Depends(current_user),
    ):
        """Run the first automatic-send test on a closed four-order list.

        The orders are deliberately compiled into the backend.  The caller
        cannot add a fifth order or substitute another number.  Calls are
        sequential and the batch stops on the first real refusal; duplicate
        guards count as safe completion on a retry.
        """
        if payload.confirmation != CANARY_CONFIRMATION:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "canary_confirmation_required",
                    "message": "عبارة تأكيد تجربة الإرسال الآلي غير صحيحة",
                },
            )

        settings = await db.qoyod_settings.find_one(
            {"user_id": _TENANT},
            {"_id": 0, "legacy_pipeline_frozen": 1},
        ) or {}
        if not settings.get("legacy_pipeline_frozen"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "legacy_pipeline_not_frozen",
                    "message": (
                        "يجب تجميد المسار القديم قبل تشغيل تجربة "
                        "الإرسال الآلي"
                    ),
                },
            )

        username = str(
            (user or {}).get("email") or (user or {}).get("id") or "unknown"
        )
        actor = f"auto-canary:{username}"
        run_id = f"qoyod-canary-{uuid.uuid4().hex[:12]}"
        started_at = _now()
        audit_doc = {
            "run_id": run_id,
            "canary_key": "qoyod-auto-send-canary-v1",
            "status": "in_progress",
            "order_numbers": list(CANARY_ORDER_NUMBERS),
            "actor": actor,
            "started_at": started_at,
        }
        # The audit row must exist before the first external mutation.
        await db.qoyod_manual_canary_runs.insert_one(audit_doc)

        async def _send(order_number: str) -> dict:
            return await manual_send_one(
                db,
                user_id=_TENANT,
                orders_user_id=orders_owner_id(user),
                order_number=order_number,
                actor=actor,
            )

        try:
            result = await execute_canary_batch(_send)
        except Exception as exc:  # noqa: BLE001
            error_reference = uuid.uuid4().hex[:8]
            logger.exception(
                "qoyod auto canary unhandled run=%s ref=%s",
                run_id,
                error_reference,
            )
            await db.qoyod_manual_canary_runs.update_one(
                {"run_id": run_id},
                {"$set": {
                    "status": "failed_unhandled",
                    "finished_at": _now(),
                    "error_reference": error_reference,
                    "exception_type": type(exc).__name__,
                }},
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "canary_unhandled_error",
                    "message": "توقفت تجربة الإرسال الآلي بأمان",
                    "error_reference": error_reference,
                },
            )

        finished_at = _now()
        result.update({
            "run_id": run_id,
            "actor": actor,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
        })
        await db.qoyod_manual_canary_runs.update_one(
            {"run_id": run_id},
            {"$set": {
                "status": result["status"],
                "finished_at": finished_at,
                "result": result,
            }},
        )
        logger.warning(
            "qoyod auto canary finished run=%s status=%s sent=%s "
            "already=%s failed=%s actor=%s",
            run_id,
            result["status"],
            result["sent_count"],
            result["already_sent_count"],
            result["failed_count"],
            actor,
        )
        return result

    @router.post("/retry-failed/{order_number}")
    async def retry_failed(order_number: str,
                           user=Depends(current_user)):
        """Safely retry one failed order after a fresh Salla status check."""
        actor = "failed-retry-ui"
        try:
            username = (user or {}).get("email") or (user or {}).get("id")
            if username:
                actor = f"failed-retry-ui:{username}"
        except Exception:
            pass

        try:
            return await retry_failed_order(
                db,
                orders_user_id=orders_owner_id(user),
                order_number=str(order_number),
                actor=actor,
            )
        except ManualSendRefused as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=exc.to_dict(),
            )
        except Exception as exc:  # noqa: BLE001
            ref = uuid.uuid4().hex[:8]
            logger.exception(
                "failed Qoyod retry ref=%s order=%s actor=%s",
                ref, order_number, actor,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "failed_retry_unexpected",
                    "message": "تعذر إكمال إعادة الإرسال بأمان",
                    "error_reference": ref,
                },
            ) from exc

    @router.post("/recheck-payment/{order_number}")
    async def recheck_payment(order_number: str,
                              user=Depends(current_user)):
        """Read current Salla payment facts only; never send or persist."""
        return await recheck_payment_read_only(
            db,
            orders_user_id=orders_owner_id(user),
            order_number=order_number,
        )

    @router.post("/recheck-payment-bulk")
    async def recheck_payment_bulk(
        payload: PaymentRecheckBatchPayload = Body(...),
        user=Depends(current_user),
    ):
        order_numbers = list(dict.fromkeys(
            str(value or "").strip() for value in payload.order_numbers
        ))
        if not order_numbers or len(order_numbers) > 100:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_payment_recheck_batch",
                    "message": "اختر من 1 إلى 100 طلب للفحص فقط",
                },
            )
        return await recheck_payment_batch_read_only(
            db,
            orders_user_id=orders_owner_id(user),
            order_numbers=order_numbers,
        )

    @router.get("/status/{order_number}")
    async def send_status(order_number: str,
                          user=Depends(current_user)):
        doc = await db.qoyod_manual_send_locks.find_one(
            {"user_id": _TENANT, "order_number": str(order_number)},
            {"_id": 0},
        )
        if not doc:
            return {"ok": True, "order_number": order_number,
                    "status": "never_attempted"}
        # Coerce datetime fields to ISO for JSON safety.
        for k in ("started_at", "finished_at"):
            v = doc.get(k)
            if isinstance(v, datetime):
                doc[k] = v.isoformat()
        return {"ok": True, "order_number": order_number, "lock": doc}

    @router.post("/freeze-legacy-pipeline")
    async def freeze_legacy_pipeline(
        payload: FreezeLegacyPipelinePayload = Body(...),
        user=Depends(current_user),
    ):
        """Set `qoyod_settings.legacy_pipeline_frozen` to True/False.

        This is the ONE knob that stops the Rev32→Rev48 worker from
        touching integration_inbox rows. It is NOT a delete — the
        legacy code files remain in place as reference. Only the
        worker tick short-circuits.

        Emits an audit line so operators can review who flipped it.
        """
        actor = "unknown"
        try:
            actor = ((user or {}).get("email")
                     or (user or {}).get("id") or "unknown")
        except Exception:
            pass
        now = _now()
        await db.qoyod_settings.update_one(
            {"user_id": _TENANT},
            {"$set": {
                "legacy_pipeline_frozen":            bool(payload.enabled),
                "legacy_pipeline_frozen_updated_at": now,
                "legacy_pipeline_frozen_actor":      actor,
            },
             "$setOnInsert": {"user_id": _TENANT}},
            upsert=True,
        )
        logger.warning(
            "plan-b freeze_legacy_pipeline enabled=%s actor=%s",
            payload.enabled, actor)
        # Return the post-write snapshot so the caller can verify.
        after = await db.qoyod_settings.find_one(
            {"user_id": _TENANT},
            {"_id": 0, "legacy_pipeline_frozen": 1,
             "legacy_pipeline_frozen_updated_at": 1,
             "legacy_pipeline_frozen_actor": 1}) or {}
        _upd = after.get("legacy_pipeline_frozen_updated_at")
        return {
            "ok":                     True,
            "legacy_pipeline_frozen": bool(
                after.get("legacy_pipeline_frozen")),
            "actor":                  after.get(
                "legacy_pipeline_frozen_actor"),
            "updated_at":             (_upd.isoformat()
                                        if isinstance(_upd, datetime)
                                        else _upd),
        }

    @router.get("/missing-from-plan-b")
    async def missing_from_plan_b(
        days: int = Query(90, ge=1, le=365),
        from_date: Optional[str] = Query(None),
        to_date: Optional[str] = Query(None),
        limit: int = Query(1000, ge=1, le=5000),
        search: Optional[str] = Query(None),
        include_already_sent: bool = Query(
            True,
            description=("Include rows whose invisibility reason is "
                          "`already_sent` / `duplicate_invoice_in_qoyod`. "
                          "Default True — the diagnostic view wants to "
                          "prove that these ARE sent, not lost.")),
        user=Depends(current_user),
    ):
        """Read-only: enumerate every Salla order that is NOT visible
        on the Plan-B pending page, with the pipeline stage where it
        got stuck and a short reason code.

        Cross-references six independent sources: unified_orders,
        integration_inbox, Plan-B pending logic, qoyod_invoices,
        `manual_qoyod_invoice_id` and `qoyod_invoice_id` markers.

        NO writes. NO قيود network calls. NO send buttons on the UI
        side either — this page is 100% diagnostic.

        Tenant axis (user directive 2026-07-09):
          • unified_orders is queried under the CALLER's JWT user_id
            (same tenant used by /orders).
          • integration_inbox / qoyod_invoices / Plan-B pending logic
            stay under `_TENANT` — that's where webhook markers live.
        """
        return await list_missing_from_plan_b(
            db,
            orders_user_id=orders_owner_id(user),
            markers_user_id=_TENANT,
            days=days, limit=limit,
            search=search, include_already_sent=include_already_sent,
            from_date=from_date, to_date=to_date)

    @router.get("/audit/plan-b-sent-vs-diagnostic")
    async def audit_sent(
        days: int = Query(365, ge=1, le=365),
        user=Depends(current_user),
    ):
        """Read-only audit: compare Plan-B marker-based sent count
        (authoritative) with the diagnostic's `already_sent_plan_b`
        counter, and enumerate the delta with a per-order exclusion
        reason. No side-effects, no writes."""
        return await audit_plan_b_vs_diagnostic(
            db,
            orders_user_id=orders_owner_id(user),
            markers_user_id=_TENANT,
            days=days,
        )

    @router.get("/diagnose/{order_number}")
    async def diagnose(order_number: str,
                        user=Depends(current_user)):
        """Read-only RCA for the totals-mismatch guard. Runs the exact
        same math the send path uses (quantise → line grosses → tax
        factor → sum → compare with Salla total) but WITHOUT any قيود
        network call. Returns the full breakdown so an operator can
        see where the pennies leaked."""
        return await diagnose_totals(
            db,
            user_id=_TENANT,
            orders_user_id=orders_owner_id(user),
            order_number=str(order_number),
        )

    @router.post("/repair-recon-markers")
    async def repair_recon_markers(user=Depends(current_user)):
        """Retroactive migration for the reconciliation page.

        For every inbox row whose `manual_qoyod_invoice_id` is a real
        (non-DRY) numeric id but the unified `qoyod_invoice_id` field
        is missing/empty, copy the value across. This heals Plan-B
        sends made BEFORE we started writing the unified marker, so
        the "مقارنة ميزان ↔ قيود" page shows them as MATCHED.

        Also copies the invoice number when present. Idempotent —
        subsequent calls are no-ops.

        In addition, every real local Qoyod invoice whose strict reference
        matches a Salla order repairs the Orders V2 accounting projection
        and any missing inbox marker. This is local-only and never writes
        to Qoyod.

        Returns a summary: how many rows were scanned/updated and the
        list of affected order numbers (up to 200 for UI display).
        """
        actor = "unknown"
        try:
            actor = ((user or {}).get("email")
                     or (user or {}).get("id") or "unknown")
        except Exception:
            pass

        scanned = 0
        updated = 0
        skipped_no_manual = 0
        skipped_already_unified = 0
        affected: list[str] = []

        repair_owner_ids = list(dict.fromkeys(
            value for value in (
                _TENANT,
                str(orders_owner_id(user) or "").strip(),
            ) if value
        ))
        repair_owner_query: str | dict = repair_owner_ids[0]
        if len(repair_owner_ids) > 1:
            repair_owner_query = {"$in": repair_owner_ids}
        cursor = db.integration_inbox.find(
            {"user_id": repair_owner_query,
             "manual_qoyod_invoice_id":
                {"$exists": True, "$nin": [None, ""]}},
            {"_id": 0, "id": 1, "user_id": 1,
             "salla_order_number": 1,
             "manual_qoyod_invoice_id": 1,
             "manual_qoyod_invoice_number": 1,
             "qoyod_invoice_id": 1},
        )
        async for row in cursor:
            scanned += 1
            mid = row.get("manual_qoyod_invoice_id")
            if not (mid and str(mid).strip()
                    and not str(mid).upper().startswith(
                        ("DRY:", "PREVIEW:"))):
                skipped_no_manual += 1
                continue
            existing = row.get("qoyod_invoice_id")
            if existing and str(existing).strip() and \
               not str(existing).upper().startswith(("DRY:", "PREVIEW:")):
                skipped_already_unified += 1
                continue
            patch: dict = {
                "qoyod_invoice_id":     str(mid),
                "qoyod_invoice_source": "manual_plan_b_repair",
                "qoyod_marker_repaired_at": _now(),
                "qoyod_marker_repaired_by": actor,
            }
            num = row.get("manual_qoyod_invoice_number")
            if num:
                patch["qoyod_invoice_number"] = str(num)
            await db.integration_inbox.update_one(
                {"id": row.get("id"), "user_id": row.get("user_id")},
                {"$set": patch},
                upsert=False,
            )
            updated += 1
            on = str(row.get("salla_order_number") or "")
            if on and len(affected) < 200:
                affected.append(on)

        accounting_repair = await repair_qoyod_order_accounting(
            db,
            orders_user_id=orders_owner_id(user),
            markers_user_id=_TENANT,
            actor=str(actor),
        )

        logger.warning(
            "plan-b repair-recon-markers scanned=%s updated=%s "
            "accounting_updated=%s actor=%s",
            scanned,
            updated,
            (accounting_repair.get("counts") or {}).get(
                "unified_orders_updated", 0),
            actor,
        )
        return {
            "ok":      True,
            "actor":   actor,
            "counts": {
                "scanned_manual_rows":      scanned,
                "updated":                  updated,
                "skipped_no_manual_id":     skipped_no_manual,
                "skipped_already_unified":  skipped_already_unified,
            },
            "affected_orders_sample": affected,
            "accounting_repair": accounting_repair,
            "at":     _now().isoformat(),
        }

    return router
