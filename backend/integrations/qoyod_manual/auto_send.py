"""Plan-B automatic Qoyod sender.

This worker deliberately reuses :func:`manual_send_one`, the production path
that was proven by the closed four-order canary.  It does not call the legacy
Qoyod pipeline.  The existing Qoyod settings switches are the control plane:

* ``enabled`` and ``auto_send`` must be true;
* ``dry_run_mode`` must be false;
* only the canonical ``completed`` (تم التنفيذ) trigger is accepted;
* the legacy pipeline must remain frozen;
* a successful closed canary must exist before the settings can arm it.

Safety properties:

* orders are processed sequentially in small batches;
* the manual sender's per-order lock and Qoyod reference lookup provide the
  duplicate barrier;
* COD keeps the manual sender invariant (invoice only, never a receipt);
* an order-specific total mismatch is isolated for manual review while later
  orders continue;
* infrastructure and unknown errors still trip the circuit breaker by turning
  ``auto_send`` off;
* every mutating run is written to ``qoyod_manual_auto_runs`` before the first
  external request.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pymongo.errors import DuplicateKeyError

from integrations.qoyod_manual.canary_batch import SAFE_ALREADY_SENT_CODES
from integrations.qoyod_manual.pending import list_pending_orders
from integrations.qoyod_manual.send import ManualSendRefused, manual_send_one
from salla_integration.sync import resync_single_order

logger = logging.getLogger(__name__)

_TENANT = "main"
_WORKER_TASK: Optional[asyncio.Task] = None
_LAST_RUN_AT: Optional[datetime] = None
_LAST_RUN_OK = True
_LAST_ROUND: dict[str, Any] = {}

# This refusal is emitted only after send.py has persisted the real Qoyod
# invoice id and before it creates a payment.  The persisted invoice marker is
# the duplicate barrier on later scans, while the full outstanding balance in
# Qoyod leaves the order available for an operator to review and pay manually.
# Keep this allow-list deliberately narrow: authentication, network, unknown,
# and other possibly systemic failures must continue to stop the worker.
_PER_ORDER_MANUAL_REVIEW_CODES = frozenset({
    "qoyod_actual_total_mismatch",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _capabilities(settings: dict) -> dict:
    value = settings.get("capabilities") or {}
    return value if isinstance(value, dict) else {}


def activation_issues(
    settings: dict,
    *,
    credentials_configured: bool,
    canary_succeeded: bool,
) -> list[dict[str, str]]:
    """Return the closed list of reasons that block live Plan-B auto-send."""
    issues: list[dict[str, str]] = []

    def add(code: str, message: str) -> None:
        issues.append({"code": code, "message": message})

    if not credentials_configured:
        add("credentials_missing", "مفتاح قيود غير محفوظ")
    if not settings.get("legacy_pipeline_frozen"):
        add("legacy_pipeline_not_frozen", "يجب إبقاء مسار قيود القديم مجمداً")
    if list(settings.get("invoice_trigger_statuses") or []) != ["completed"]:
        add("completed_only_required", "الإرسال التلقائي يقبل حالة تم التنفيذ فقط")
    if settings.get("trigger_once_only") is not True:
        add("trigger_once_required", "يجب تفعيل إنشاء الفاتورة مرة واحدة فقط")
    if settings.get("auto_receipt") is not True:
        add("auto_receipt_required", "يجب تفعيل إنشاء سند القبض للطلبات المدفوعة")

    caps = _capabilities(settings)
    required_caps = (
        "create_customers", "create_products",
        "create_invoices", "create_receipts",
    )
    if any(caps.get(key) is not True for key in required_caps):
        add("capabilities_required", "يجب تفعيل صلاحيات عمليات قيود الأربع")
    if not canary_succeeded:
        add("successful_canary_required", "يجب نجاح تجربة الإرسال الآلي المقيدة أولاً")
    return issues


def is_live_requested(settings: dict) -> bool:
    return bool(
        settings.get("enabled")
        and settings.get("auto_send")
        and not settings.get("dry_run_mode")
    )


def is_armed(settings: dict) -> bool:
    """Cheap runtime gate. Full validation happens when settings are saved."""
    return bool(
        is_live_requested(settings)
        and settings.get("plan_b_auto_send_armed_at")
        and settings.get("plan_b_auto_send_orders_user_id")
        and settings.get("legacy_pipeline_frozen")
        and list(settings.get("invoice_trigger_statuses") or []) == ["completed"]
        and settings.get("trigger_once_only") is True
    )


def status_snapshot(settings: dict) -> dict[str, Any]:
    return {
        "requested": is_live_requested(settings),
        "armed": is_armed(settings),
        "armed_at": settings.get("plan_b_auto_send_armed_at"),
        "armed_by": settings.get("plan_b_auto_send_actor"),
        "last_error": settings.get("plan_b_auto_send_last_error"),
        "disabled_reason": settings.get("plan_b_auto_send_disabled_reason"),
        "worker": liveness(),
    }


async def _current_settings(db) -> dict:
    return await db.qoyod_settings.find_one(
        {"user_id": _TENANT}, {"_id": 0},
    ) or {}


async def _acquire_lease(db, *, seconds: int = 45) -> Optional[str]:
    """Small distributed lease so multiple web processes do not scan together."""
    now = _now()
    owner = f"plan-b-auto-{uuid.uuid4().hex}"
    lease_until = now + timedelta(seconds=seconds)
    try:
        await db.qoyod_manual_auto_worker_leases.insert_one({
            "_id": _TENANT,
            "owner": owner,
            "lease_until": lease_until,
            "acquired_at": now,
        })
        return owner
    except DuplicateKeyError:
        pass

    try:
        result = await db.qoyod_manual_auto_worker_leases.update_one(
            {
                "_id": _TENANT,
                "$or": [
                    {"lease_until": {"$lt": now}},
                    {"lease_until": {"$exists": False}},
                ],
            },
            {"$set": {
                "owner": owner,
                "lease_until": lease_until,
                "acquired_at": now,
            }},
        )
        if result.modified_count == 1:
            return owner
    except Exception:
        logger.exception("Plan-B auto-send lease acquisition failed")
    return None


async def _release_lease(db, owner: Optional[str]) -> None:
    if not owner:
        return
    try:
        await db.qoyod_manual_auto_worker_leases.update_one(
            {"_id": _TENANT, "owner": owner},
            {"$set": {"lease_until": _now(), "released_at": _now()}},
        )
    except Exception:
        logger.exception("Plan-B auto-send lease release failed")


async def _still_exactly_completed(
    db, order_number: str, *, orders_user_id: str,
) -> bool:
    """Re-read the newest trace immediately before the external mutation.

    Arabic aliases such as ``مكتمل`` are intentionally not accepted for the
    automatic path.  The native value must be ``تم التنفيذ``.  Older rows
    without a native value may use the canonical ``completed`` slug.
    """
    # Direct Salla resync snapshots are written under the authenticated
    # Orders owner id, while historical webhook traces use the MVP tenant.
    # Read both and sort by time so the authoritative snapshot created just
    # above wins over any older ``main`` row.
    row = await db.integration_inbox.find_one(
        {
            "user_id": {"$in": [_TENANT, str(orders_user_id)]},
            "salla_order_number": str(order_number),
        },
        {"_id": 0, "canonical_payload.order_status": 1,
         "canonical_payload.order_status_native": 1},
        sort=[("received_at", -1)],
    )
    if not row:
        return False
    canonical = row.get("canonical_payload") or {}
    native = str(canonical.get("order_status_native") or "").strip()
    slug = str(canonical.get("order_status") or "").strip().lower()
    if native:
        return native == "تم التنفيذ"
    return slug == "completed"


async def _refresh_and_verify_salla_status(
    db, *, orders_user_id: str, order_number: str,
) -> tuple[bool, dict[str, Any]]:
    """Refresh the order from Salla, then apply the exact status gate.

    Automatic accounting must never rely only on a cached row: an operator
    may move the order away from ``تم التنفيذ`` in Salla between two worker
    scans.  A failed authoritative refresh is a real preflight error and the
    caller trips the circuit breaker before any Qoyod mutation.
    """
    refresh = await resync_single_order(
        db,
        orders_user_id,
        order_number,
    )
    if not refresh.get("ok") or not refresh.get("found"):
        raise ManualSendRefused(
            "salla_status_refresh_failed",
            "تعذر التحقق من الحالة الحالية للطلب في سلة",
            {
                "order_number": order_number,
                "stage": refresh.get("stage"),
                "error": refresh.get("error"),
                "needs_reauth": bool(refresh.get("needs_reauth")),
            },
        )
    return await _still_exactly_completed(
        db,
        order_number,
        orders_user_id=orders_user_id,
    ), refresh


async def _trip_circuit_breaker(
    db, *, code: str, message: str, run_id: str,
) -> None:
    now = _now()
    await db.qoyod_settings.update_one(
        {"user_id": _TENANT},
        {"$set": {
            "auto_send": False,
            "plan_b_auto_send_armed_at": None,
            "plan_b_auto_send_disabled_at": now,
            "plan_b_auto_send_disabled_reason": "circuit_breaker",
            "plan_b_auto_send_last_error": {
                "code": code,
                "message": message,
                "run_id": run_id,
                "at": now,
            },
        }},
    )


async def run_once(db, *, batch_limit: int = 5) -> dict[str, Any]:
    """Run one bounded, sequential automatic-send round."""
    global _LAST_RUN_AT, _LAST_RUN_OK, _LAST_ROUND
    settings = await _current_settings(db)
    if not is_armed(settings):
        result = {"ok": True, "status": "not_armed", "sent_count": 0}
        _LAST_RUN_OK = True
        _LAST_ROUND = result
        _LAST_RUN_AT = _now()
        return result

    owner = await _acquire_lease(db)
    if not owner:
        result = {"ok": True, "status": "lease_busy", "sent_count": 0}
        _LAST_RUN_OK = True
        _LAST_ROUND = result
        _LAST_RUN_AT = _now()
        return result

    try:
        # Re-read after the lease. A save/disable may have happened while the
        # worker was waiting.
        settings = await _current_settings(db)
        if not is_armed(settings):
            result = {"ok": True, "status": "disarmed", "sent_count": 0}
            _LAST_RUN_OK = True
            _LAST_ROUND = result
            return result

        orders_user_id = str(settings["plan_b_auto_send_orders_user_id"])
        pending = await list_pending_orders(
            db,
            user_id=_TENANT,
            orders_user_id=orders_user_id,
            days=60,
            limit=max(25, batch_limit * 5),
            status="completed",
        )
        candidates = [
            row for row in (pending.get("orders") or [])
            if str(row.get("salla_status") or "").strip()
            in {"تم التنفيذ", "completed"}
        ][:max(1, int(batch_limit))]

        if not candidates:
            result = {"ok": True, "status": "idle", "sent_count": 0}
            _LAST_RUN_OK = True
            _LAST_ROUND = result
            return result

        run_id = f"qoyod-auto-{uuid.uuid4().hex[:16]}"
        started_at = _now()
        audit = {
            "run_id": run_id,
            "status": "in_progress",
            "actor": "auto-plan-b",
            "order_numbers": [str(r["order_number"]) for r in candidates],
            "started_at": started_at,
            "settings_armed_at": settings.get("plan_b_auto_send_armed_at"),
        }
        # Audit exists before the first external write.
        await db.qoyod_manual_auto_runs.insert_one(audit)

        results: list[dict[str, Any]] = []
        stopped = False
        for row in candidates:
            order_number = str(row["order_number"])
            try:
                still_completed, refresh = await _refresh_and_verify_salla_status(
                    db,
                    orders_user_id=orders_user_id,
                    order_number=order_number,
                )
                if not still_completed:
                    snapshot = refresh.get("plan_b_status_snapshot") or {}
                    results.append({
                        "order_number": order_number,
                        "outcome": "skipped_status_changed",
                        "current_status": snapshot.get("status_native")
                            or snapshot.get("status_slug"),
                    })
                    continue
                payload = await manual_send_one(
                    db,
                    user_id=_TENANT,
                    orders_user_id=orders_user_id,
                    order_number=order_number,
                    actor=f"auto-plan-b:{run_id}",
                )
                results.append({
                    "order_number": order_number,
                    "outcome": "sent",
                    "invoice_only": bool(payload.get("invoice_only")),
                    "invoice_id": payload.get("invoice_id"),
                    "payment_id": payload.get("payment_id"),
                })
            except ManualSendRefused as exc:
                if exc.code in SAFE_ALREADY_SENT_CODES:
                    results.append({
                        "order_number": order_number,
                        "outcome": "already_sent",
                        "code": exc.code,
                    })
                    continue
                if exc.code == "in_progress":
                    results.append({
                        "order_number": order_number,
                        "outcome": "skipped_in_progress",
                        "code": exc.code,
                    })
                    continue
                if exc.code in _PER_ORDER_MANUAL_REVIEW_CODES:
                    results.append({
                        "order_number": order_number,
                        "outcome": "manual_review",
                        "code": exc.code,
                        "message": exc.message,
                        "detail": exc.extra,
                    })
                    continue
                results.append({
                    "order_number": order_number,
                    "outcome": "failed",
                    "code": exc.code,
                    "message": exc.message,
                    "detail": exc.extra,
                })
                await _trip_circuit_breaker(
                    db, code=exc.code, message=exc.message, run_id=run_id,
                )
                stopped = True
                break
            except Exception as exc:  # noqa: BLE001
                error_reference = uuid.uuid4().hex[:8]
                logger.exception(
                    "Plan-B auto-send unhandled order=%s run=%s ref=%s",
                    order_number, run_id, error_reference,
                )
                results.append({
                    "order_number": order_number,
                    "outcome": "failed_unhandled",
                    "code": "unhandled_exception",
                    "error_reference": error_reference,
                })
                await _trip_circuit_breaker(
                    db,
                    code="unhandled_exception",
                    message=f"خطأ غير متوقع (مرجع {error_reference})",
                    run_id=run_id,
                )
                stopped = True
                break

        finished_at = _now()
        sent_count = sum(r["outcome"] == "sent" for r in results)
        already_count = sum(r["outcome"] == "already_sent" for r in results)
        manual_review_count = sum(
            r["outcome"] == "manual_review" for r in results
        )
        result = {
            "ok": not stopped,
            "status": "stopped_on_error" if stopped else "succeeded",
            "run_id": run_id,
            "sent_count": sent_count,
            "already_sent_count": already_count,
            "manual_review_count": manual_review_count,
            "results": results,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
        }
        await db.qoyod_manual_auto_runs.update_one(
            {"run_id": run_id},
            {"$set": {
                "status": result["status"],
                "finished_at": finished_at,
                "result": result,
            }},
        )
        _LAST_RUN_OK = not stopped
        _LAST_ROUND = result
        return result
    except Exception as exc:  # failure before an audited external mutation
        _LAST_RUN_OK = False
        error_reference = uuid.uuid4().hex[:8]
        logger.exception(
            "Plan-B auto-send round failed before completion ref=%s",
            error_reference,
        )
        # Fail closed. An operator must review and save the settings again.
        try:
            await _trip_circuit_breaker(
                db,
                code="auto_round_failed",
                message=f"تعذر تشغيل جولة الإرسال (مرجع {error_reference})",
                run_id=f"preflight-{error_reference}",
            )
        except Exception:
            logger.exception("Plan-B circuit breaker persistence failed")
        result = {
            "ok": False,
            "status": "round_failed",
            "error_reference": error_reference,
            "exception_type": type(exc).__name__,
        }
        _LAST_ROUND = result
        return result
    finally:
        _LAST_RUN_AT = _now()
        await _release_lease(db, owner)


async def _loop(db, *, interval_sec: float, batch_limit: int) -> None:
    logger.info(
        "Plan-B Qoyod auto-send worker started interval=%ss batch=%s",
        interval_sec, batch_limit,
    )
    while True:
        try:
            await run_once(db, batch_limit=batch_limit)
        except Exception:
            # run_once is already fail-closed; this protects task liveness.
            logger.exception("Plan-B Qoyod auto-send worker tick escaped")
        await asyncio.sleep(interval_sec)


def start_worker(
    db, *, interval_sec: float = 15.0, batch_limit: int = 5,
) -> asyncio.Task:
    global _WORKER_TASK
    if _WORKER_TASK is not None and not _WORKER_TASK.done():
        return _WORKER_TASK
    _WORKER_TASK = asyncio.create_task(
        _loop(db, interval_sec=interval_sec, batch_limit=batch_limit),
        name="qoyod-plan-b-auto-send-worker",
    )
    return _WORKER_TASK


def liveness() -> dict[str, Any]:
    return {
        "running": _WORKER_TASK is not None and not _WORKER_TASK.done(),
        "last_run_at": _LAST_RUN_AT.isoformat() if _LAST_RUN_AT else None,
        "last_run_ok": _LAST_RUN_OK,
        "last_round": _LAST_ROUND,
    }
