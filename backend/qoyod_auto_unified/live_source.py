"""Promote the latest direct Salla status/payment snapshot into unified_orders."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from .common import _now


async def _refresh_snapshot_with_complete_payment(
    original: Callable[..., Awaitable[dict[str, Any]]],
    db: Any,
    user_id: str,
    order_number: str,
    order_doc: dict[str, Any],
) -> dict[str, Any]:
    """Persist all live Salla payment facts, not only the method label."""
    result = await original(db, user_id, order_number, order_doc)
    if not result.get("trace_id"):
        return result
    source_fields = (
        "payment_method", "payment_status", "paid_amount",
        "remaining_amount", "has_remaining_amount",
        "is_pending_payment",
        "payment_collection_status", "payment_checkout_url",
        "receiving_bank_name", "receiving_bank_id",
        "payment_receipt_url",
    )
    canonical_patch: dict[str, Any] = {}
    unified_patch: dict[str, Any] = {}
    for field in source_fields:
        value = order_doc.get(field)
        if value not in (None, "", [], {}) or value in (0, 0.0, False):
            canonical_patch[f"canonical_payload.{field}"] = value
            unified_patch[field] = value
    if canonical_patch:
        canonical_patch["salla_direct_status_resync.payment_facts_complete"] = True
        canonical_patch["salla_direct_status_resync.payment_facts_at"] = _now()
        await db.integration_inbox.update_one(
            {
                "user_id": str(user_id),
                "trace_id": str(result["trace_id"]),
                "connector_key": "salla_direct_status_resync",
            },
            {"$set": canonical_patch},
        )
    if unified_patch:
        unified_patch.update({
            "qoyod_live_preflight_at": _now(),
            "qoyod_live_preflight_source": "salla_order_details",
        })
        await db.unified_orders.update_one(
            {"user_id": str(user_id), "order_number": str(order_number)},
            {"$set": unified_patch},
        )
    result["payment_facts_complete"] = bool(unified_patch)
    return result


async def _latest_salla_status_snapshot(
    db: Any, *, orders_user_id: str, order_number: str,
) -> Optional[dict[str, Any]]:
    row = await db.integration_inbox.find_one(
        {
            "user_id": str(orders_user_id),
            "connector_key": "salla_direct_status_resync",
            "salla_order_number": str(order_number),
        },
        {"_id": 0, "canonical_payload": 1, "received_at": 1},
        sort=[("received_at", -1)],
    )
    return row if isinstance(row, dict) else None


async def _promote_snapshot_to_unified(
    db: Any, *, orders_user_id: str, order_number: str,
) -> dict[str, Any]:
    """Promote the just-fetched live Salla status/payment into unified_orders."""
    snapshot = await _latest_salla_status_snapshot(
        db, orders_user_id=orders_user_id, order_number=order_number
    )
    canonical = (snapshot or {}).get("canonical_payload") or {}
    if not isinstance(canonical, dict):
        canonical = {}

    mapping = {
        "order_status": "order_status_slug",
        "order_status_native": "order_status",
        "payment_method": "payment_method",
        "payment_method_native": "payment_method_native",
        "payment_status": "payment_status",
        "paid_amount": "paid_amount",
        "remaining_amount": "remaining_amount",
        "has_remaining_amount": "has_remaining_amount",
        "is_pending_payment": "is_pending_payment",
        "payment_collection_status": "payment_collection_status",
        "payment_checkout_url": "payment_checkout_url",
        "receiving_bank_name": "receiving_bank_name",
        "receiving_bank_id": "receiving_bank_id",
        "payment_receipt_url": "payment_receipt_url",
    }
    patch: dict[str, Any] = {}
    for source, target in mapping.items():
        value = canonical.get(source)
        if value not in (None, "", [], {}) or value in (0, 0.0, False):
            patch[target] = value
    if not patch:
        return {"ok": True, "updated": False, "reason": "no_live_fields"}
    patch.update({
        "qoyod_live_preflight_at": _now(),
        "qoyod_live_preflight_source": "salla_direct_status_resync",
    })
    result = await db.unified_orders.update_one(
        {"user_id": str(orders_user_id), "order_number": str(order_number)},
        {"$set": patch},
    )
    return {
        "ok": int(getattr(result, "matched_count", 0) or 0) == 1,
        "updated": int(getattr(result, "modified_count", 0) or 0) > 0,
        "fields": sorted(patch),
    }
