"""Plan-B automatic Qoyod sender.

This worker deliberately reuses :func:`manual_send_one`, the production path
that was proven by the closed four-order canary.  It does not call the legacy
Qoyod pipeline.  The existing Qoyod settings switches are the control plane:

* ``enabled`` and ``auto_send`` must be true;
* ``dry_run_mode`` must be false;
* new automatic work may start from the three approved Salla states:
  ``completed``, ``in_delivery``/``delivering``, or ``delivered``;
* the final live Salla check revalidates the same closed three-state policy
  immediately before every external write;
* the legacy pipeline must remain frozen;
* a validated, authenticated live-settings save arms the worker explicitly.

Safety properties:

* orders are processed sequentially in small batches;
* the manual sender's per-order lock and Qoyod reference lookup provide the
  duplicate barrier;
* COD keeps the manual sender invariant (invoice only, never a receipt);
* every order-specific failure is isolated for manual review while later
  orders continue;
* infrastructure and unknown errors never turn ``auto_send`` off; a failure
  with an order number is quarantined, while a round-level failure is retried
  on the next worker tick;
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

from integrations.qoyod.credentials import get_api_key
from integrations.qoyod.candidate_orders import (
    API_STATUS_TO_KEY,
    UNIFIED_CANDIDATE_AUTO_FLAG,
    build_candidate_audit,
)
from integrations.qoyod.eligible_orders import QOYOD_SYNC_START_DATE
from integrations.qoyod_manual.canary_batch import SAFE_ALREADY_SENT_CODES
from integrations.qoyod_manual.send import ManualSendRefused, manual_send_one
from salla_integration.sync import refresh_single_order_status

logger = logging.getLogger(__name__)

_TENANT = "main"
_WORKER_TASK: Optional[asyncio.Task] = None
_LAST_RUN_AT: Optional[datetime] = None
_LAST_RUN_OK = True
_LAST_ROUND: dict[str, Any] = {}

# Settings store the exact Salla slug selected by the operator.  Salla can
# expose the in-delivery state under more than one equivalent slug, so map the
# closed approved list to the three keys used to partition the single unified
# candidate snapshot. Nothing outside these aliases can arm the worker.
_TRIGGER_TO_PENDING_STATUS = {
    "completed": "completed",
    "تم التنفيذ": "completed",
    "تم التجهيز": "completed",
    "in_delivery": "in_delivery",
    "in delivery": "in_delivery",
    "shipping": "in_delivery",
    "delivering": "in_delivery",
    "جاري_التوصيل": "in_delivery",
    "جاري التوصيل": "in_delivery",
    "جارٍ التوصيل": "in_delivery",
    "delivered": "delivered",
    "تم التوصيل": "delivered",
}

_PENDING_STATUS_NATIVE_VALUES = {
    "completed": frozenset({"completed", "تم التنفيذ", "تم التجهيز"}),
    "in_delivery": frozenset({
        "in_delivery", "in delivery", "shipping", "delivering",
        "جاري التوصيل", "جارٍ التوصيل",
    }),
    "delivered": frozenset({"delivered", "تم التوصيل"}),
}

_CANONICAL_PENDING_STATUSES = ("completed", "in_delivery", "delivered")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _capabilities(settings: dict) -> dict:
    value = settings.get("capabilities") or {}
    return value if isinstance(value, dict) else {}


def _configured_pending_statuses(settings: dict) -> list[str]:
    """Resolve saved Salla trigger slugs to the closed Plan-B status keys."""
    resolved: list[str] = []
    for raw in list(settings.get("invoice_trigger_statuses") or []):
        if not isinstance(raw, str):
            continue
        value = raw.strip().lower()
        status_key = _TRIGGER_TO_PENDING_STATUS.get(value)
        if status_key and status_key not in resolved:
            resolved.append(status_key)
    # Settings may validate safety, but they cannot silently narrow the
    # canonical candidate definition. Rollout authorization is handled by the
    # independent, default-off UNIFIED_CANDIDATE_AUTO_FLAG.
    return list(_CANONICAL_PENDING_STATUSES) if resolved else []


def _trigger_statuses_are_safe(settings: dict) -> bool:
    raw = list(settings.get("invoice_trigger_statuses") or [])
    if not raw:
        return False
    valid_count = sum(
        isinstance(value, str)
        and value.strip().lower() in _TRIGGER_TO_PENDING_STATUS
        for value in raw
    )
    return (
        valid_count == len(raw)
        and bool(_configured_pending_statuses(settings))
    )


def _pending_row_matches_status(row: dict[str, Any], status_key: str) -> bool:
    canonical_status = API_STATUS_TO_KEY.get(status_key)
    if row.get("current_status_key") and canonical_status:
        return row.get("current_status_key") == canonical_status
    native = str(row.get("salla_status") or "").strip().lower()
    return native in _PENDING_STATUS_NATIVE_VALUES.get(status_key, frozenset())


def activation_issues(
    settings: dict,
    *,
    credentials_configured: bool,
    canary_succeeded: bool,
    salla_connected: bool = True,
) -> list[dict[str, str]]:
    """Return the closed list of reasons that block live Plan-B auto-send."""
    issues: list[dict[str, str]] = []

    def add(code: str, message: str) -> None:
        issues.append({"code": code, "message": message})

    if not credentials_configured:
        add("credentials_missing", "مفتاح قيود غير محفوظ")
    if not salla_connected:
        add(
            "salla_connection_required",
            "يجب ربط متجر سلة بحساب مالك المتجر قبل تشغيل الإرسال التلقائي",
        )
    if not settings.get("legacy_pipeline_frozen"):
        add("legacy_pipeline_not_frozen", "يجب إبقاء مسار قيود القديم مجمداً")
    if not _trigger_statuses_are_safe(settings):
        add(
            "completed_trigger_required",
            "حالات الإرسال التلقائي المسموحة فقط: تم التنفيذ، جاري التوصيل، "
            "تم التوصيل",
        )
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
    # A canary is recorded when available, but it is not a prerequisite for
    # an authenticated operator who explicitly saves the validated live
    # settings. The independent unified-candidate flag remains default-off
    # until that successful save.
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
        and settings.get(UNIFIED_CANDIDATE_AUTO_FLAG) is True
        and settings.get("plan_b_auto_send_armed_at")
        and settings.get("plan_b_auto_send_orders_user_id")
        and settings.get("legacy_pipeline_frozen")
        and _trigger_statuses_are_safe(settings)
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
        "unified_candidate_auto_enabled": (
            settings.get(UNIFIED_CANDIDATE_AUTO_FLAG) is True
        ),
        "worker": liveness(),
    }


async def _current_settings(db) -> dict:
    return await db.qoyod_settings.find_one(
        {"user_id": _TENANT}, {"_id": 0},
    ) or {}


async def _recover_legacy_circuit_breaker(
    db, settings: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Re-arm any circuit breaker persisted by an older worker.

    The current policy never disables the Qoyod application for an order
    failure.  An old deployment may still have persisted a global breaker,
    so recover it atomically after re-validating the same credential, canary,
    Salla-owner and settings contract used by the activation endpoint.
    """
    last_error = settings.get("plan_b_auto_send_last_error") or {}
    error_code = str(last_error.get("code") or "").strip()
    if not (
        settings.get("enabled") is True
        and settings.get("auto_send") is False
        and settings.get("dry_run_mode") is not True
        and settings.get("plan_b_auto_send_disabled_reason")
        == "circuit_breaker"
        and bool(error_code)
    ):
        return None

    orders_user_id = str(
        settings.get("plan_b_auto_send_orders_user_id") or ""
    ).strip()
    if not orders_user_id:
        return None

    try:
        key = await get_api_key(db, _TENANT)
        canary = await db.qoyod_manual_canary_runs.find_one(
            {"status": "succeeded"},
            {"_id": 0, "run_id": 1, "finished_at": 1},
            sort=[("finished_at", -1)],
        )
        salla_integration = await db.salla_integrations.find_one(
            {"user_id": orders_user_id, "status": "connected"},
            {"_id": 0, "user_id": 1},
        )
    except Exception:
        logger.exception(
            "Plan-B legacy breaker readiness check failed"
        )
        return None

    candidate = {
        **settings,
        "auto_send": True,
        "plan_b_auto_send_armed_at": _now().isoformat(),
    }
    issues = activation_issues(
        candidate,
        credentials_configured=bool(key),
        canary_succeeded=bool(canary),
        salla_connected=bool(salla_integration),
    )
    if issues or not is_armed(candidate):
        logger.warning(
            "Plan-B legacy breaker remains stopped issues=%s",
            [issue.get("code") for issue in issues],
        )
        return None

    now = _now()
    recovered_at = now.isoformat()
    recovery_audit = {
        "reason": "legacy_global_breaker_removed",
        "previous_error_code": error_code,
        "previous_run_id": last_error.get("run_id"),
        "previous_error_at": last_error.get("at"),
        "recovered_at": recovered_at,
    }
    result = await db.qoyod_settings.update_one(
        {
            "user_id": _TENANT,
            "enabled": True,
            "auto_send": False,
            "dry_run_mode": {"$ne": True},
            "plan_b_auto_send_disabled_reason": "circuit_breaker",
            "plan_b_auto_send_last_error.code": error_code,
            "plan_b_auto_send_orders_user_id": orders_user_id,
        },
        {
            "$set": {
                "auto_send": True,
                "plan_b_auto_send_armed_at": recovered_at,
                "plan_b_auto_send_disabled_at": None,
                "plan_b_auto_send_disabled_reason": None,
                "plan_b_auto_send_last_error": None,
                "plan_b_auto_send_last_recovery": recovery_audit,
                "updated_at": now,
            },
            "$inc": {"plan_b_auto_send_recovery_count": 1},
        },
    )
    if result.modified_count != 1:
        return None

    logger.warning(
        "Plan-B automatic sender recovered legacy global breaker"
    )
    return {
        **candidate,
        "plan_b_auto_send_armed_at": recovered_at,
        "plan_b_auto_send_disabled_at": None,
        "plan_b_auto_send_disabled_reason": None,
        "plan_b_auto_send_last_error": None,
        "plan_b_auto_send_last_recovery": recovery_audit,
    }


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


_LIVE_STATUS_NATIVE_BY_SLUG = {
    "completed": frozenset({"", "completed", "تم التنفيذ", "تم التجهيز"}),
    "in_delivery": frozenset({
        "", "in_delivery", "in delivery", "shipping",
        "جاري التوصيل", "جارٍ التوصيل",
    }),
    "delivered": frozenset({"", "delivered", "تم التوصيل"}),
}
_LIVE_STATUS_SLUG_ALIASES = {
    "completed": "completed",
    "تم التنفيذ": "completed",
    "in_delivery": "in_delivery",
    "in delivery": "in_delivery",
    "shipping": "in_delivery",
    "delivering": "in_delivery",
    "جاري_التوصيل": "in_delivery",
    "جاري التوصيل": "in_delivery",
    "delivered": "delivered",
    "تم التوصيل": "delivered",
}


def _live_salla_status_is_eligible(canonical: dict[str, Any]) -> bool:
    """Return whether the authoritative Salla status is Qoyod-eligible.

    A non-empty canonical slug is authoritative.  ``تم التجهيز`` is a custom
    native label that is accepted only when Salla classifies it as
    ``completed``; the same label without that trusted slug stays blocked.
    This explicit pair mapping also prevents a cancelled/refunded native state
    from being accepted because of a stale or conflicting slug.
    """
    slug = str(canonical.get("order_status") or "").strip().lower()
    native = str(canonical.get("order_status_native") or "").strip()
    native_normalized = native.lower()

    if slug:
        canonical_slug = _LIVE_STATUS_SLUG_ALIASES.get(slug)
        allowed_native = _LIVE_STATUS_NATIVE_BY_SLUG.get(canonical_slug or "")
        return bool(
            allowed_native is not None
            and native_normalized in allowed_native
        )

    # Older snapshots can legitimately lack a canonical slug.  Keep this
    # fallback closed to the three exact statuses; notably, تم التجهيز is not
    # present here and therefore still requires a trusted completed slug.
    return native_normalized in {
        "completed", "تم التنفيذ",
        "in_delivery", "in delivery", "shipping",
        "جاري التوصيل", "جارٍ التوصيل",
        "delivered", "تم التوصيل",
    }


async def _still_qoyod_eligible(
    db, order_number: str, *, orders_user_id: str,
) -> bool:
    """Re-read the newest trace immediately before the external mutation.

    Only the three approved Qoyod statuses are accepted.  This function is
    shared by the manual route and automatic worker through
    :func:`_refresh_and_verify_salla_status` so their final gate cannot drift.
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
    return _live_salla_status_is_eligible(canonical)


async def _refresh_and_verify_salla_status(
    db, *, orders_user_id: str, order_number: str,
) -> tuple[bool, dict[str, Any]]:
    """Refresh the order from Salla, then apply the shared status gate.

    Automatic accounting must never rely only on a cached row: an operator
    may move the order to a cancelled/refunded state in Salla between two
    worker scans.  A failed authoritative refresh is a real preflight error
    and the caller quarantines only that order before any Qoyod mutation.
    """
    refresh = await refresh_single_order_status(
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
    return await _still_qoyod_eligible(
        db,
        order_number,
        orders_user_id=orders_user_id,
    ), refresh


async def _open_quarantined_order_numbers(
    db, order_numbers: Optional[list[str]] = None,
) -> set[str]:
    query: dict[str, Any] = {"user_id": _TENANT, "status": "open"}
    if order_numbers is not None:
        normalized = [str(value) for value in order_numbers if str(value)]
        if not normalized:
            return set()
        query["order_number"] = {"$in": normalized}
    result: set[str] = set()
    cursor = db.qoyod_manual_auto_quarantines.find(
        query,
        {"_id": 0, "order_number": 1, "code": 1},
    )
    async for row in cursor:
        # A failed Salla status refresh is transient. It must never become a
        # permanent backlog exclusion; the next cycle rechecks it before any
        # Qoyod write.
        if row.get("code") == "salla_status_refresh_failed":
            continue
        result.add(str(row.get("order_number") or ""))
    return result


async def _load_candidate_rows(
    db, *, settings: dict[str, Any], orders_user_id: str,
    batch_limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load one unified snapshot and select a fair, bounded worker batch.

    A round performs exactly one ``build_candidate_audit`` call for its Orders
    owner.  The three status groups, every counter, and the persisted run-audit
    metadata are derived from that same in-memory result.  No Salla request is
    made here; the selected order alone is refreshed immediately before its
    existing ``manual_send_one`` path is allowed to POST.
    """
    snapshot = await build_candidate_audit(
        db,
        orders_user_id=str(orders_user_id),
        markers_user_id=_TENANT,
        marker_user_ids=(_TENANT, str(orders_user_id)),
        from_date=QOYOD_SYNC_START_DATE,
    )
    all_candidates = [
        row for row in (snapshot.get("orders") or [])
        if row.get("worker_candidate") is True
    ]
    quarantined = await _open_quarantined_order_numbers(
        db,
        [str(row.get("order_number") or "") for row in all_candidates],
    )
    runnable_candidates = [
        row for row in all_candidates
        if str(row.get("order_number") or "") not in quarantined
    ]
    pending_statuses = _configured_pending_statuses(settings)
    candidate_groups = [
        [
            row for row in runnable_candidates
            if _pending_row_matches_status(row, pending_status)
        ]
        for pending_status in pending_statuses
    ]

    candidates: list[dict[str, Any]] = []
    seen_order_numbers: set[str] = set()
    max_group_size = max((len(group) for group in candidate_groups), default=0)
    for index in range(max_group_size):
        for group in candidate_groups:
            if index >= len(group):
                continue
            row = group[index]
            order_number = str(row.get("order_number") or "")
            if not order_number or order_number in seen_order_numbers:
                continue
            seen_order_numbers.add(order_number)
            candidates.append(row)
            if len(candidates) >= max(1, int(batch_limit)):
                break
        if len(candidates) >= max(1, int(batch_limit)):
            break

    authoritative_status_counts = {
        API_STATUS_TO_KEY[pending_status]: sum(
            _pending_row_matches_status(row, pending_status)
            for row in all_candidates
        )
        for pending_status in pending_statuses
    }
    runnable_status_counts = {
        API_STATUS_TO_KEY[pending_status]: len(group)
        for pending_status, group in zip(pending_statuses, candidate_groups)
    }
    snapshot_captured_at = snapshot.get("captured_at") or _now().isoformat()
    snapshot_fingerprint = (
        snapshot.get("snapshot_fingerprint")
        or (snapshot.get("reference_hashes") or {}).get("worker_candidates")
    )
    return candidates, {
        "authoritative_backlog_count": len(all_candidates),
        "runnable_candidate_count": len(runnable_candidates),
        "open_quarantined_candidate_count": max(
            0, len(all_candidates) - len(runnable_candidates)
        ),
        "status_candidate_count": sum(runnable_status_counts.values()),
        "batch_candidate_count": len(candidates),
        "candidate_snapshot": {
            "source_authority": snapshot.get("source_authority"),
            "orders_user_id": str(orders_user_id),
            "from_date": snapshot.get("from_date"),
            "to_date": snapshot.get("to_date"),
            "captured_at": snapshot_captured_at,
            "snapshot_fingerprint": snapshot_fingerprint,
            "status_counts": (
                snapshot.get("worker_candidate_status_counts")
                or authoritative_status_counts
            ),
            "status_display_counts": snapshot.get(
                "worker_candidate_status_display_counts"
            ) or {},
            "runnable_status_counts": runnable_status_counts,
        },
    }


async def _quarantine_order(
    db, *, order_number: str, exc: ManualSendRefused, run_id: str,
) -> None:
    """Persist an order-local refusal without disabling the worker."""
    now = _now()
    await db.qoyod_manual_auto_quarantines.update_one(
        {"_id": f"{_TENANT}:{order_number}"},
        {
            "$set": {
                "user_id": _TENANT,
                "order_number": order_number,
                "status": "open",
                "code": exc.code,
                "message": exc.message,
                "detail": exc.extra,
                "run_id": run_id,
                "last_seen_at": now,
            },
            "$setOnInsert": {
                "first_seen_at": now,
            },
            "$inc": {"attempt_count": 1},
        },
        upsert=True,
    )


async def run_once(db, *, batch_limit: int = 5) -> dict[str, Any]:
    """Run one bounded, sequential automatic-send round."""
    global _LAST_RUN_AT, _LAST_RUN_OK, _LAST_ROUND
    settings = await _current_settings(db)
    if settings.get(UNIFIED_CANDIDATE_AUTO_FLAG) is not True:
        result = {
            "ok": True,
            "status": "unified_auto_rollout_disabled",
            "sent_count": 0,
            "candidate_count": 0,
        }
        _LAST_RUN_OK = True
        _LAST_ROUND = result
        _LAST_RUN_AT = _now()
        return result
    recovered = await _recover_legacy_circuit_breaker(db, settings)
    if recovered is not None:
        settings = recovered
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
        candidates, candidate_counts = await _load_candidate_rows(
            db,
            settings=settings,
            orders_user_id=orders_user_id,
            batch_limit=batch_limit,
        )

        if not candidates:
            result = {
                "ok": True,
                "status": "idle",
                "sent_count": 0,
                "candidate_count": candidate_counts[
                    "authoritative_backlog_count"
                ],
                **candidate_counts,
            }
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
            **candidate_counts,
        }
        # Audit exists before the first external write.
        await db.qoyod_manual_auto_runs.insert_one(audit)

        results: list[dict[str, Any]] = []
        for row in candidates:
            order_number = str(row["order_number"])
            try:
                still_eligible, refresh = await _refresh_and_verify_salla_status(
                    db,
                    orders_user_id=orders_user_id,
                    order_number=order_number,
                )
                if not still_eligible:
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
                if exc.code == "salla_status_refresh_failed":
                    results.append({
                        "order_number": order_number,
                        "outcome": "retry_later",
                        "code": exc.code,
                        "message": exc.message,
                        "detail": exc.extra,
                    })
                    continue
                await _quarantine_order(
                    db,
                    order_number=order_number,
                    exc=exc,
                    run_id=run_id,
                )
                results.append({
                    "order_number": order_number,
                    "outcome": "manual_review",
                    "code": exc.code,
                    "message": exc.message,
                    "detail": exc.extra,
                })
                continue
            except Exception as exc:  # noqa: BLE001
                error_reference = uuid.uuid4().hex[:8]
                logger.exception(
                    "Plan-B auto-send unhandled order=%s run=%s ref=%s",
                    order_number, run_id, error_reference,
                )
                isolated = ManualSendRefused(
                    "unhandled_exception",
                    f"خطأ غير متوقع (مرجع {error_reference})",
                    {"error_reference": error_reference},
                )
                await _quarantine_order(
                    db,
                    order_number=order_number,
                    exc=isolated,
                    run_id=run_id,
                )
                results.append({
                    "order_number": order_number,
                    "outcome": "manual_review",
                    "code": "unhandled_exception",
                    "error_reference": error_reference,
                })
                continue

        finished_at = _now()
        sent_count = sum(r["outcome"] == "sent" for r in results)
        already_count = sum(r["outcome"] == "already_sent" for r in results)
        manual_review_count = sum(
            r["outcome"] == "manual_review" for r in results
        )
        retry_later_count = sum(
            r["outcome"] == "retry_later" for r in results
        )
        result = {
            "ok": True,
            "status": "succeeded",
            "run_id": run_id,
            "sent_count": sent_count,
            "already_sent_count": already_count,
            "manual_review_count": manual_review_count,
            "retry_later_count": retry_later_count,
            "candidate_count": candidate_counts[
                "authoritative_backlog_count"
            ],
            **candidate_counts,
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
        _LAST_RUN_OK = True
        _LAST_ROUND = result
        return result
    except Exception as exc:  # failure before an audited external mutation
        _LAST_RUN_OK = False
        error_reference = uuid.uuid4().hex[:8]
        logger.exception(
            "Plan-B auto-send round failed before completion ref=%s",
            error_reference,
        )
        # Keep the application armed.  No order was selected safely enough to
        # quarantine here, so the next 15-second tick retries the round.
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
            # run_once already isolates failures; this protects task liveness.
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
