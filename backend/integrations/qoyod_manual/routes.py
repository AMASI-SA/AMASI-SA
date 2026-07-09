"""Plan-B Manual Send — FastAPI router.

Endpoints (all mounted under /api/integrations/qoyod/manual):
    GET  /pending-orders            List orders eligible for manual push.
    POST /send/{order_number}       Push ONE order end-to-end.
    GET  /status/{order_number}     Latest manual-send lock row.
    GET  /health                    Frozen-flag + module presence probe.
    POST /freeze-legacy-pipeline    Toggle legacy_pipeline_frozen on/off.

Auth: the caller must be authenticated (uses the same `current_user`
dependency as the rest of the qoyod router).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from integrations.qoyod_manual.pending import list_pending_orders
from integrations.qoyod_manual.send import (
    manual_send_one, ManualSendRefused,
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

logger = logging.getLogger(__name__)

_TENANT = "main"  # matches the rest of the qoyod router's convention


class FreezeLegacyPipelinePayload(BaseModel):
    """Body for POST /freeze-legacy-pipeline.

    `enabled=True`  → sets legacy_pipeline_frozen=True (freezes worker).
    `enabled=False` → sets it False (worker resumes normal ticks).
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool


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
        limit: int = Query(200, ge=1, le=1000),
        search: Optional[str] = Query(None),
        status: str = Query(
            "completed",
            description=("Salla status filter: completed | delivered | "
                          "in_delivery. Default = completed. Each tab in "
                          "the UI hits this endpoint with a different "
                          "value.")),
        user=Depends(current_user),
    ):
        return await list_pending_orders(
            db, user_id=_TENANT, days=days, limit=limit,
            search=search, status=status)

    class _ExclusionDiagPayload(BaseModel):
        model_config = ConfigDict(extra="ignore")
        order_numbers: list[str]
        status: str = "delivered"
        days:   int = 60
        limit:  int = 200

    # ── Diagnostic-only endpoint (temporary, opt-in) ──────────────
    # Read-only. Simulates the exact filter chain of
    # `list_pending_orders` for a caller-supplied list of order
    # numbers and reports the primary exclusion reason (or a positive
    # verdict) for each. REMOVE this route + module + tests once the
    # Plan-B pending list is unified with `list_unsent_orders`.
    @router.post("/pending-orders/diagnose-exclusion")
    async def diagnose_exclusion(
        payload: _ExclusionDiagPayload,
        user=Depends(current_user),
    ):
        return await diagnose_pending_exclusion(
            db, user_id=_TENANT,
            order_numbers=list(payload.order_numbers),
            status=payload.status,
            days=payload.days, limit=payload.limit,
        )

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
            result = await manual_send_one(
                db, user_id=_TENANT, order_number=str(order_number),
                actor=actor)
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
            logger.error(
                "manual/send unhandled_exception order=%s ref=%s\n%s",
                order_number, ref, tb_text)
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
            orders_user_id=user["id"],
            markers_user_id=_TENANT,
            days=days, limit=limit,
            search=search, include_already_sent=include_already_sent)

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
            orders_user_id=user["id"],
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
            db, user_id=_TENANT, order_number=str(order_number))

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

        cursor = db.integration_inbox.find(
            {"user_id": _TENANT,
             "manual_qoyod_invoice_id":
                {"$exists": True, "$nin": [None, ""]}},
            {"_id": 0, "id": 1, "salla_order_number": 1,
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
                {"id": row.get("id")}, {"$set": patch})
            updated += 1
            on = str(row.get("salla_order_number") or "")
            if on and len(affected) < 200:
                affected.append(on)

        logger.warning(
            "plan-b repair-recon-markers scanned=%s updated=%s actor=%s",
            scanned, updated, actor)
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
            "at":     _now().isoformat(),
        }

    return router
