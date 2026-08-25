"""Runtime installer for the unified Qoyod automatic-send patch."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from integrations.qoyod.unsent_orders import _is_real

from .common import RETRYABLE_SYNC_FAILURE_CODES, _TENANT, _now
from .live_source import _refresh_snapshot_with_complete_payment
from .queue_api import _list_unsent_orders_with_queue_counts
from .queue_select import _load_candidate_rows_oldest_first, _retry_delay
from .reconcile import (
    _reconcile_existing_reference,
    _reconcile_local_mirror_after_sync,
    _requeue_absent_unhandled_after_complete_sync,
)
from .invoice_state import _resolve_order_exception
from .sender_projection import sync_authoritative_payment_to_inbox


def install_auto_send_payment_freshness_patch() -> None:
    """Install the unified source, recovery, queue and reconciliation patch."""
    from integrations.qoyod import qoyod_invoices_sync, unsent_orders
    from integrations.qoyod_manual import auto_send as auto_send_module
    from salla_integration import sync as salla_sync_module
    from integrations.qoyod_manual.canary_batch import SAFE_ALREADY_SENT_CODES
    from integrations.qoyod_manual.send import ManualSendRefused

    if getattr(auto_send_module, "_unified_source_patch_installed", False):
        return

    original_send: Callable[..., Awaitable[dict[str, Any]]] = (
        auto_send_module.manual_send_one
    )
    original_status_snapshot = salla_sync_module._refresh_plan_b_status_snapshot

    async def refresh_status_snapshot_complete(
        db: Any, user_id: str, order_number: str, order_doc: dict[str, Any]
    ) -> dict[str, Any]:
        return await _refresh_snapshot_with_complete_payment(
            original_status_snapshot, db, user_id, order_number, order_doc
        )

    async def authoritative_manual_send_one(
        db: Any,
        *,
        user_id: str,
        order_number: str,
        orders_user_id: Optional[str] = None,
        actor: str = "manual-ui",
        **kwargs: Any,
    ) -> dict[str, Any]:
        automatic = actor.startswith("auto-plan-b:")
        effective_owner = str(orders_user_id or user_id)
        if automatic:
            try:
                freshness = await sync_authoritative_payment_to_inbox(
                    db,
                    orders_user_id=effective_owner,
                    legacy_user_id=str(user_id),
                    order_number=str(order_number),
                )
            except Exception as exc:  # pre-Qoyod projection boundary
                freshness = {
                    "ok": False,
                    "code": "authoritative_payment_refresh_failed",
                    "stage": "authoritative_sender_projection",
                    "exception_type": type(exc).__name__,
                    "order_number": str(order_number),
                }
            if not freshness.get("ok"):
                code = (
                    freshness.get("code")
                    or "authoritative_payment_refresh_failed"
                )
                message = (
                    "حالة الدفع الحالية غير مؤهلة للإرسال إلى قيود."
                    if code == "authoritative_payment_not_eligible"
                    else (
                        "يحتاج الطلب إلى تحقق دفع مباشر من سلة قبل الإرسال."
                        if code == "authoritative_payment_needs_verification"
                        else (
                            "تعذر تجهيز بيانات الطلب الموحدة قبل الإرسال "
                            "التلقائي؛ لم يتم إرسال أي شيء إلى قيود."
                        )
                    )
                )
                raise ManualSendRefused(code, message, freshness)

        try:
            result = await original_send(
                db,
                user_id=user_id,
                orders_user_id=orders_user_id,
                order_number=order_number,
                actor=actor,
                allow_historical_positive_total=(
                    True if automatic else kwargs.pop(
                        "allow_historical_positive_total", False
                    )
                ),
                **kwargs,
            )
        except ManualSendRefused as exc:
            if automatic and exc.code in SAFE_ALREADY_SENT_CODES:
                reconciliation = await _reconcile_existing_reference(
                    db,
                    orders_user_id=effective_owner,
                    order_number=str(order_number),
                    actor=actor,
                )
                if not reconciliation.get("ok"):
                    raise ManualSendRefused(
                        "qoyod_reference_reconciliation_failed",
                        "وُجدت فاتورة بنفس المرجع في قيود لكن تعذرت "
                        "مصالحتها محلياً؛ لن تُنشأ فاتورة جديدة.",
                        reconciliation,
                    ) from exc
                if reconciliation.get("duplicate"):
                    raise ManualSendRefused(
                        "duplicate_qoyod_reference",
                        "يوجد أكثر من فاتورة في قيود بنفس رقم المرجع؛ "
                        "أُوقف الطلب للمراجعة دون أي إرسال جديد.",
                        reconciliation,
                    ) from exc
                if not reconciliation.get("resolved"):
                    raise ManualSendRefused(
                        "invoice_found_payment_incomplete",
                        "الفاتورة موجودة في قيود لكن السداد غير مكتمل؛ "
                        "أُوقف الطلب للمراجعة دون إنشاء فاتورة مكررة.",
                        reconciliation,
                    ) from exc
            raise

        if automatic:
            invoice_id = result.get("invoice_id")
            if _is_real(invoice_id):
                await _resolve_order_exception(
                    db,
                    order_number=str(order_number),
                    invoice_id=invoice_id,
                    resolution="automatic_send_succeeded",
                    actor=actor,
                )
        return result

    original_quarantine = auto_send_module._quarantine_order

    async def quarantine_with_retry_schedule(
        db: Any, *, order_number: str, exc: Any, run_id: str,
    ) -> None:
        await original_quarantine(
            db, order_number=order_number, exc=exc, run_id=run_id
        )
        if exc.code in RETRYABLE_SYNC_FAILURE_CODES:
            now = _now()
            await db.qoyod_manual_auto_quarantines.update_one(
                {
                    "user_id": _TENANT,
                    "order_number": str(order_number),
                    "status": "open",
                },
                {"$set": {
                    "recovery_class": "sync_retryable",
                    "next_retry_at": now + _retry_delay(str(exc.code)),
                    "retry_scheduled_at": now,
                }},
            )

    async def open_true_quarantines(
        db: Any, order_numbers: Optional[list[str]] = None,
    ) -> set[str]:
        query: dict[str, Any] = {"user_id": _TENANT, "status": "open"}
        if order_numbers is not None:
            normalized = [str(value) for value in order_numbers if str(value)]
            if not normalized:
                return set()
            query["order_number"] = {"$in": normalized}
        now = _now()
        blocked: set[str] = set()
        cursor = db.qoyod_manual_auto_quarantines.find(
            query,
            {
                "_id": 0, "order_number": 1, "code": 1,
                "next_retry_at": 1,
            },
        )
        async for row in cursor:
            order_number = str(row.get("order_number") or "")
            code = str(row.get("code") or "")
            next_retry = row.get("next_retry_at")
            if isinstance(next_retry, datetime) and next_retry.tzinfo is None:
                next_retry = next_retry.replace(tzinfo=timezone.utc)
            retry_due = (
                code in RETRYABLE_SYNC_FAILURE_CODES
                and (not isinstance(next_retry, datetime) or next_retry <= now)
            )
            if order_number and not retry_due:
                blocked.add(order_number)
        return blocked

    async def load_oldest_first(
        db: Any,
        *,
        settings: dict[str, Any],
        orders_user_id: str,
        batch_limit: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return await _load_candidate_rows_oldest_first(
            auto_send_module,
            db,
            settings=settings,
            orders_user_id=orders_user_id,
            batch_limit=batch_limit,
        )

    original_unsent = unsent_orders.list_unsent_orders

    async def list_unsent_orders_patched(db: Any, **kwargs: Any) -> dict[str, Any]:
        return await _list_unsent_orders_with_queue_counts(
            original_unsent, db, **kwargs
        )

    original_invoice_sync = qoyod_invoices_sync.sync_qoyod_invoices

    async def sync_qoyod_invoices_patched(db: Any, **kwargs: Any) -> dict[str, Any]:
        result = await original_invoice_sync(db, **kwargs)
        if not result.get("ok"):
            return result
        settings = await db.qoyod_settings.find_one(
            {"user_id": str(kwargs.get("user_id") or _TENANT)},
            {"_id": 0, "plan_b_auto_send_orders_user_id": 1},
        ) or {}
        orders_owner = str(
            settings.get("plan_b_auto_send_orders_user_id") or ""
        ).strip()
        if orders_owner:
            result["reconciliation_repair"] = (
                await _reconcile_local_mirror_after_sync(
                    db,
                    orders_user_id=orders_owner,
                    markers_user_id=str(kwargs.get("user_id") or _TENANT),
                )
            )
            max_pages = max(1, int(kwargs.get("max_pages") or 200))
            page_size = max(1, int(kwargs.get("page_size") or 50))
            sync_is_complete = (
                int(result.get("row_errors") or 0) == 0
                and int(result.get("fetched") or 0) < max_pages * page_size
            )
            if sync_is_complete:
                started_at = result.get("started_at")
                if isinstance(started_at, str):
                    try:
                        started_at = datetime.fromisoformat(started_at)
                    except ValueError:
                        started_at = None
                if isinstance(started_at, datetime):
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=timezone.utc)
                    result["unhandled_recovery"] = (
                        await _requeue_absent_unhandled_after_complete_sync(
                            db,
                            markers_user_id=str(
                                kwargs.get("user_id") or _TENANT
                            ),
                            sync_started_at=started_at,
                        )
                    )
            else:
                result["unhandled_recovery"] = {
                    "ok": False,
                    "skipped": True,
                    "reason": "qoyod_sync_not_proven_complete",
                }
        return result

    salla_sync_module._refresh_plan_b_status_snapshot = refresh_status_snapshot_complete
    auto_send_module.manual_send_one = authoritative_manual_send_one
    auto_send_module._open_quarantined_order_numbers = open_true_quarantines
    auto_send_module._quarantine_order = quarantine_with_retry_schedule
    auto_send_module._load_candidate_rows = load_oldest_first
    unsent_orders.list_unsent_orders = list_unsent_orders_patched
    qoyod_invoices_sync.sync_qoyod_invoices = sync_qoyod_invoices_patched
    auto_send_module._unified_source_patch_installed = True
    auto_send_module._payment_freshness_patch_installed = True
