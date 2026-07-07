"""Plan-B Manual Send — FastAPI router.

Endpoints (all mounted under /api/integrations/qoyod/manual):
    GET  /pending-orders            List orders eligible for manual push.
    POST /send/{order_number}       Push ONE order end-to-end.
    GET  /status/{order_number}     Latest manual-send lock row.
    GET  /health                    Frozen-flag + module presence probe.

Auth: the caller must be authenticated (uses the same `current_user`
dependency as the rest of the qoyod router).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from integrations.qoyod_manual.pending import list_pending_orders
from integrations.qoyod_manual.send import (
    manual_send_one, ManualSendRefused,
)

logger = logging.getLogger(__name__)

_TENANT = "main"  # matches the rest of the qoyod router's convention


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
             "payment_method_mapping": 1}) or {}
        return {
            "ok":                        True,
            "legacy_pipeline_frozen":    bool(settings.get(
                                              "legacy_pipeline_frozen")),
            "payment_method_mapping_count": len(
                settings.get("payment_method_mapping") or []),
            "at":                        _now().isoformat(),
        }

    @router.get("/pending-orders")
    async def pending_orders(
        days: int = Query(60, ge=1, le=365),
        limit: int = Query(200, ge=1, le=1000),
        search: Optional[str] = Query(None),
        user=Depends(current_user),
    ):
        return await list_pending_orders(
            db, user_id=_TENANT, days=days, limit=limit, search=search)

    @router.post("/send/{order_number}")
    async def send_one(order_number: str, user=Depends(current_user)):
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

    return router
