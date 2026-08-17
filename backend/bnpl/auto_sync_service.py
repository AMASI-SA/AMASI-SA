"""Emergency-safe BNPL background service.

Production stability takes precedence over periodic provider verification.
Automatic Tabby/Tamara polling is intentionally disabled in-process because
provider pagination/sweeps can monopolize the web backend and make even the
health endpoint time out.

The HTTP routes remain available and return an explicit disabled state. Manual
refund propagation remains local to MongoDB and does not call provider APIs.
Provider webhooks and the rest of Mezan are not changed by this module.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Keep the public constant for route/UI compatibility. The server loop may wake
# on this cadence, but run_auto_sync_for_all_users() is a zero-network no-op.
SYNC_INTERVAL_SECONDS = int(os.environ.get("BNPL_AUTO_SYNC_INTERVAL_SECONDS", "3600"))
BACKGROUND_CHECKS_DISABLED = True
DISABLED_REASON = "production_stability_background_provider_checks_disabled"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _propagate_refunds_to_unified(
    db, user_id: str, provider: str,
) -> int:
    """Local-only refund propagation; never calls Tabby/Tamara APIs."""
    updates = 0
    cursor = db.payment_transactions.find(
        {
            "user_id": user_id,
            "provider": provider,
            "refunded_amount": {"$gt": 0},
        },
        {
            "_id": 0,
            "order_reference_id": 1,
            "refunded_amount": 1,
            "provider_id": 1,
        },
    )
    async for ptx in cursor:
        ref = (ptx.get("order_reference_id") or "").strip()
        try:
            amt = float(ptx.get("refunded_amount") or 0)
        except (TypeError, ValueError):
            amt = 0.0
        if not ref or amt <= 0:
            continue
        res = await db.unified_orders.update_one(
            {
                "user_id": user_id,
                "$or": [
                    {"order_reference_id": ref},
                    {"order_number": ref},
                ],
                "$expr": {
                    "$lt": [
                        {"$ifNull": ["$refunded_amount", 0]},
                        amt,
                    ]
                },
            },
            {
                "$set": {
                    "refunded_amount": round(amt, 2),
                    "last_refund_propagated_at": _now_iso(),
                },
                "$addToSet": {"sources_seen": provider},
            },
        )
        if res.modified_count > 0:
            updates += 1
    return updates


def _disabled_provider_result(user_id: str, provider: str) -> Dict[str, Any]:
    now = _now_iso()
    return {
        "ok": True,
        "disabled": True,
        "provider": provider,
        "user_id": user_id,
        "started_at": now,
        "finished_at": now,
        "fetched": 0,
        "transactions_upserted": 0,
        "refunds_upserted": 0,
        "orders_created": 0,
        "orders_updated": 0,
        "reason": DISABLED_REASON,
    }


async def run_auto_sync_for_user(db, user_id: str) -> Dict[str, Any]:
    """Return immediately; explicit manual auto-sync is disabled as well.

    This guarantees that neither Tabby nor Tamara provider polling can be
    triggered through the shared /bnpl/auto-sync/run-now path while the
    production stability hotfix is active.
    """
    started = _now_iso()
    results: List[Dict[str, Any]] = [
        _disabled_provider_result(user_id, "tabby"),
        _disabled_provider_result(user_id, "tamara"),
    ]
    return {
        "id": str(uuid.uuid4()),
        "kind": "manual",
        "user_id": user_id,
        "started_at": started,
        "finished_at": _now_iso(),
        "providers": ["tabby", "tamara"],
        "results": results,
        "any_failures": False,
        "disabled": True,
        "reason": DISABLED_REASON,
    }


async def run_auto_sync_for_all_users(db) -> Dict[str, Any]:
    """Zero-network no-op used by the hourly in-process scheduler."""
    now = _now_iso()
    return {
        "id": str(uuid.uuid4()),
        "kind": "cron",
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 0.0,
        "pairs_processed": 0,
        "users_processed": 0,
        "any_failures": False,
        "by_provider": {"tabby": 0, "tamara": 0},
        "results": [],
        "disabled": True,
        "reason": DISABLED_REASON,
    }


async def get_auto_sync_status(db, user_id: str) -> Dict[str, Any]:
    """Read local settings only and expose the production-safe disabled state."""
    providers: List[Dict[str, Any]] = []
    cursor = db.bnpl_settings.find(
        {
            "user_id": user_id,
            "provider": {"$in": ["tabby", "tamara"]},
        },
        {
            "_id": 0,
            "provider": 1,
            "enabled": 1,
            "last_auto_sync_at": 1,
            "last_auto_sync_since": 1,
            "last_auto_sync_status": 1,
            "last_auto_sync_error": 1,
            "activation_date": 1,
        },
    )
    async for st in cursor:
        providers.append(
            {
                "provider": st.get("provider"),
                "enabled": bool(st.get("enabled")),
                "background_sync_enabled": False,
                "last_auto_sync_at": st.get("last_auto_sync_at"),
                "last_auto_sync_since": st.get("last_auto_sync_since"),
                "last_auto_sync_status": st.get("last_auto_sync_status") or "disabled",
                "last_auto_sync_error": st.get("last_auto_sync_error") or "",
                "activation_date": st.get("activation_date"),
            }
        )

    return {
        "providers": providers,
        "last_run": None,
        "interval_seconds": SYNC_INTERVAL_SECONDS,
        "next_run_eta_seconds": None,
        "disabled": True,
        "reason": DISABLED_REASON,
    }


async def run_tamara_attribution_sweep(db) -> Dict[str, Any]:
    """Zero-work replacement for the daily/startup Tamara sweep.

    The startup migration in server.py may still invoke this function, but it
    now returns immediately instead of scanning payment_transactions.
    """
    now = _now_iso()
    return {
        "id": str(uuid.uuid4()),
        "kind": "tamara_attribution_sweep",
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 0.0,
        "users_processed": 0,
        "rows_scanned": 0,
        "rows_updated": 0,
        "captured_extracted": 0,
        "per_user_sample": {},
        "disabled": True,
        "reason": DISABLED_REASON,
    }
