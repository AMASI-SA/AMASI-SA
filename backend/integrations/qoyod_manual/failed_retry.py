"""Safe, operator-confirmed retry for one failed Qoyod order.

The retry deliberately reuses the production Plan-B sender.  It refreshes the
order from Salla first, then lets manual_send_one apply the existing date,
amount, idempotency, and Qoyod-reference guards.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from integrations.qoyod_manual.canary_batch import SAFE_ALREADY_SENT_CODES
from integrations.qoyod_manual.send import ManualSendRefused, manual_send_one


_TENANT = "main"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _mark_retry_started(
    db, *, order_number: str, actor: str,
) -> None:
    """Record an attempt only when an open automatic-send quarantine exists."""
    await db.qoyod_manual_auto_quarantines.update_one(
        {
            "_id": f"{_TENANT}:{order_number}",
            "user_id": _TENANT,
            "status": "open",
        },
        {
            "$set": {
                "last_manual_retry_at": _now(),
                "last_manual_retry_by": actor,
                "last_manual_retry_error": None,
            },
            "$inc": {"manual_retry_attempt_count": 1},
        },
    )


async def _mark_retry_failed(
    db, *, order_number: str, actor: str, error: dict[str, Any],
) -> None:
    await db.qoyod_manual_auto_quarantines.update_one(
        {
            "_id": f"{_TENANT}:{order_number}",
            "user_id": _TENANT,
            "status": "open",
        },
        {
            "$set": {
                "last_manual_retry_at": _now(),
                "last_manual_retry_by": actor,
                "last_manual_retry_error": error,
            },
        },
    )


async def _resolve_quarantine(
    db, *, order_number: str, actor: str, resolution: str,
    result: dict[str, Any],
) -> None:
    await db.qoyod_manual_auto_quarantines.update_one(
        {
            "_id": f"{_TENANT}:{order_number}",
            "user_id": _TENANT,
            "status": "open",
        },
        {
            "$set": {
                "status": "resolved",
                "resolved_at": _now(),
                "resolved_by": actor,
                "resolution": resolution,
                "resolved_invoice_id": (
                    result.get("invoice_id")
                    or result.get("qoyod_invoice_id")
                    or result.get("manual_qoyod_invoice_id")
                ),
                "resolved_payment_id": (
                    result.get("payment_id")
                    or result.get("manual_qoyod_payment_id")
                ),
                "last_manual_retry_error": None,
            },
        },
    )


async def retry_failed_order(
    db,
    *,
    orders_user_id: str,
    order_number: str,
    actor: str,
    refresh_fn: Callable[..., Awaitable[tuple[bool, dict[str, Any]]]] | None = None,
    send_fn: Callable[..., Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Recheck one order in Salla and retry it through the guarded sender."""
    if refresh_fn is None:
        from integrations.qoyod_manual.auto_send import (
            _refresh_and_verify_salla_status,
        )
        refresh_fn = _refresh_and_verify_salla_status
    if send_fn is None:
        send_fn = manual_send_one

    order_number = str(order_number).strip()
    await _mark_retry_started(
        db, order_number=order_number, actor=actor,
    )

    try:
        still_eligible, refresh = await refresh_fn(
            db,
            orders_user_id=orders_user_id,
            order_number=order_number,
        )
        if not still_eligible:
            snapshot = refresh.get("plan_b_status_snapshot") or {}
            raise ManualSendRefused(
                "not_qoyod_eligible_status",
                "حالة الطلب الحالية في سلة ليست ضمن الحالات المسموحة "
                "(تم التنفيذ / جاري التوصيل / تم التوصيل)",
                {
                    "order_number": order_number,
                    "current_status": (
                        snapshot.get("status_native")
                        or snapshot.get("status_slug")
                    ),
                },
            )

        sent = await send_fn(
            db,
            user_id=_TENANT,
            orders_user_id=orders_user_id,
            order_number=order_number,
            actor=actor,
            allow_missing_salla_order_date=True,
            allow_historical_positive_total=True,
        )
    except ManualSendRefused as exc:
        if exc.code in SAFE_ALREADY_SENT_CODES:
            already = {
                "ok": True,
                "retry_outcome": "already_sent",
                "order_number": order_number,
                "code": exc.code,
                "message": exc.message,
                "invoice_id": (
                    exc.extra.get("qoyod_invoice_id")
                    or exc.extra.get("manual_qoyod_invoice_id")
                ),
                "payment_id": exc.extra.get("manual_qoyod_payment_id"),
                "invoice_only": bool(exc.extra.get("invoice_only")),
            }
            await _resolve_quarantine(
                db,
                order_number=order_number,
                actor=actor,
                resolution="already_sent_verified",
                result=already,
            )
            return already

        await _mark_retry_failed(
            db,
            order_number=order_number,
            actor=actor,
            error=exc.to_dict(),
        )
        raise
    except Exception as exc:
        await _mark_retry_failed(
            db,
            order_number=order_number,
            actor=actor,
            error={
                "code": "unexpected_retry_error",
                "message": str(exc)[:500],
            },
        )
        raise

    result = {
        **sent,
        "ok": True,
        "retry_outcome": "sent",
        "order_number": order_number,
    }
    await _resolve_quarantine(
        db,
        order_number=order_number,
        actor=actor,
        resolution="manual_retry_succeeded",
        result=result,
    )
    return result
