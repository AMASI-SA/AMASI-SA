"""Authoritative payment refresh for Plan-B automatic Qoyod sends.

The automatic worker calls Salla ``resync_single_order`` before every send. The
resync writes the latest payment facts to ``unified_orders`` under the Orders
owner, while ``manual_send_one`` historically loaded a legacy inbox row under
``main``. This module copies the fresh payment/status facts into that exact row
before any automatic Qoyod write. Missing or pending facts fail closed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional


_PENDING_METHODS = frozenset({
    "",
    "pending",
    "pending_payment",
    "awaiting_payment",
    "unpaid",
    "unknown",
})

_PAYMENT_FIELDS = (
    "payment_method",
    "payment_status",
    "paid_amount",
    "remaining_amount",
    "has_remaining_amount",
    "payment_collection_status",
    "payment_checkout_url",
    "receiving_bank_name",
    "receiving_bank_id",
    "payment_receipt_url",
)

_STATUS_FIELDS = (
    "order_status",
    "order_status_slug",
)


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


async def sync_authoritative_payment_to_inbox(
    db: Any,
    *,
    orders_user_id: str,
    legacy_user_id: str,
    order_number: str,
) -> dict[str, Any]:
    order_number = str(order_number or "").strip()
    orders_user_id = str(orders_user_id or "").strip()
    legacy_user_id = str(legacy_user_id or "main").strip() or "main"

    unified = await db.unified_orders.find_one(
        {"user_id": orders_user_id, "order_number": order_number},
        {"_id": 0, **{field: 1 for field in (*_PAYMENT_FIELDS, *_STATUS_FIELDS)}},
    )
    if not unified:
        return {
            "ok": False,
            "code": "authoritative_order_missing_after_resync",
            "order_number": order_number,
            "orders_user_id": orders_user_id,
        }

    payment_method = str(unified.get("payment_method") or "").strip().lower()
    collection_status = str(
        unified.get("payment_collection_status")
        or unified.get("payment_status")
        or ""
    ).strip().lower()

    if payment_method in _PENDING_METHODS:
        return {
            "ok": False,
            "code": "authoritative_payment_method_still_pending",
            "order_number": order_number,
            "payment_method": payment_method or None,
            "payment_status": collection_status or None,
        }

    target = await db.integration_inbox.find_one(
        {
            "user_id": legacy_user_id,
            "salla_order_number": order_number,
        },
        {"_id": 0, "id": 1},
        sort=[("received_at", -1)],
    )
    if not target or not target.get("id"):
        return {
            "ok": False,
            "code": "legacy_sender_inbox_row_missing",
            "order_number": order_number,
            "legacy_user_id": legacy_user_id,
        }

    patch: dict[str, Any] = {}
    for field in (*_PAYMENT_FIELDS, *_STATUS_FIELDS):
        value = unified.get(field)
        if _present(value) or value in (0, 0.0, False):
            canonical_field = "order_status_native" if field == "order_status" else field
            patch[f"canonical_payload.{canonical_field}"] = value

    patch.update({
        "auto_send_payment_refresh.at": datetime.now(timezone.utc),
        "auto_send_payment_refresh.source": "unified_orders_after_salla_resync",
        "auto_send_payment_refresh.orders_user_id": orders_user_id,
        "auto_send_payment_refresh.payment_method": unified.get("payment_method"),
        "auto_send_payment_refresh.payment_status": (
            unified.get("payment_collection_status")
            or unified.get("payment_status")
        ),
        "auto_send_payment_refresh.paid_amount": unified.get("paid_amount"),
        "auto_send_payment_refresh.remaining_amount": unified.get("remaining_amount"),
    })

    result = await db.integration_inbox.update_one(
        {"id": target["id"], "user_id": legacy_user_id},
        {"$set": patch},
    )
    return {
        "ok": result.matched_count == 1,
        "code": None if result.matched_count == 1 else "legacy_sender_inbox_update_missed",
        "order_number": order_number,
        "row_id": target["id"],
        "payment_method": unified.get("payment_method"),
        "payment_status": (
            unified.get("payment_collection_status")
            or unified.get("payment_status")
        ),
        "paid_amount": unified.get("paid_amount"),
        "remaining_amount": unified.get("remaining_amount"),
    }


def install_auto_send_payment_freshness_patch() -> None:
    from integrations.qoyod_manual import auto_send as auto_send_module
    from integrations.qoyod_manual.send import ManualSendRefused

    if getattr(auto_send_module, "_payment_freshness_patch_installed", False):
        return

    original: Callable[..., Awaitable[dict[str, Any]]] = auto_send_module.manual_send_one

    async def authoritative_manual_send_one(
        db: Any,
        *,
        user_id: str,
        order_number: str,
        orders_user_id: Optional[str] = None,
        actor: str = "manual-ui",
    ) -> dict[str, Any]:
        if actor.startswith("auto-plan-b:"):
            freshness = await sync_authoritative_payment_to_inbox(
                db,
                orders_user_id=str(orders_user_id or user_id),
                legacy_user_id=str(user_id),
                order_number=str(order_number),
            )
            if not freshness.get("ok"):
                raise ManualSendRefused(
                    freshness.get("code") or "authoritative_payment_refresh_failed",
                    "تعذر اعتماد أحدث حالة دفع من سلة قبل الإرسال التلقائي؛ "
                    "لم يتم إرسال أي شيء إلى قيود.",
                    freshness,
                )

        return await original(
            db,
            user_id=user_id,
            orders_user_id=orders_user_id,
            order_number=order_number,
            actor=actor,
        )

    auto_send_module.manual_send_one = authoritative_manual_send_one
    auto_send_module._payment_freshness_patch_installed = True
