"""Read-only Salla webhook delivery monitor.

The monitor reports only verified events that actually reached Mezan. A missing
record means "not observed yet", not proof that the event is disabled in Salla.
Salla API fallback remains enabled while webhook coverage is being validated.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends


EXPECTED_EVENTS: tuple[tuple[str, str, str], ...] = (
    ("order.created", "إنشاء الطلب", "orders"),
    ("order.updated", "تحديث بيانات الطلب", "orders"),
    ("order.status.updated", "تحديث حالة الطلب", "orders"),
    ("order.products.updated", "تحديث منتجات الطلب", "orders"),
    ("order.customer.updated", "تحديث بيانات مستلم الطلب", "orders"),
    ("order.shipping.address.updated", "تحديث عنوان شحن الطلب", "orders"),
    ("order.payment.updated", "تحديث طريقة دفع الطلب", "orders"),
    ("order.total.price.updated", "تحديث إجمالي سعر الطلب", "orders"),
    ("order.refunded", "إرجاع مبلغ الطلب", "orders"),
    ("order.cancelled", "إلغاء الطلب", "orders"),
    ("order.deleted", "حذف الطلب", "orders"),
    ("order.coupon.updated", "تحديث كوبون الطلب", "orders"),
    ("order.shipment.creating", "جاري إنشاء الشحنة", "shipping"),
    ("order.shipment.created", "تم إنشاء شحنة الطلب", "shipping"),
    ("order.shipment.cancelled", "إلغاء شحنة الطلب", "shipping"),
    ("order.shipment.return.creating", "جاري إنشاء شحنة استرجاع", "shipping"),
    ("order.shipment.return.created", "إنشاء شحنة استرجاع", "shipping"),
    ("order.shipment.return.cancelled", "إلغاء شحنة استرجاع", "shipping"),
    ("shipment.created", "إنشاء الشحنة", "shipping"),
    ("shipment.updated", "تحديث الشحنة", "shipping"),
    ("shipment.cancelled", "إلغاء الشحنة", "shipping"),
)


def _extract_order_number(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    candidates = [
        payload.get("order_number"),
        payload.get("reference_id"),
        data.get("reference_id") if isinstance(data, dict) else None,
        data.get("order_number") if isinstance(data, dict) else None,
        data.get("id") if isinstance(data, dict) else None,
    ]
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _as_utc(value: Any) -> datetime | None:
    """Normalize Mongo/PyMongo datetimes before comparison.

    PyMongo commonly returns naive UTC datetimes even when the application writes
    timezone-aware values. Comparing those directly with an aware UTC cutoff raises
    TypeError and previously caused HTTP 500 on the monitor endpoint.
    """
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


async def _event_snapshot(db: Any, merchant_id: str | None) -> dict[str, dict[str, Any]]:
    match: dict[str, Any] = {}
    if merchant_id:
        match["merchant_id"] = str(merchant_id)

    pipeline = [
        {"$match": match},
        {"$sort": {"last_received_at": -1}},
        {"$group": {
            "_id": "$event",
            "last_received_at": {"$first": "$last_received_at"},
            "first_received_at": {"$last": "$first_received_at"},
            "delivery_count": {"$sum": {"$ifNull": ["$delivery_count", 1]}},
            "last_payload": {"$first": "$payload"},
            "last_sync": {"$first": "$shipment_sync"},
        }},
    ]
    rows = await db.salla_webhook_event_captures.aggregate(pipeline).to_list(length=200)
    return {str(row.get("_id") or ""): row for row in rows if row.get("_id")}


def attach_salla_webhook_monitor_routes(api_router: APIRouter, db: Any) -> None:
    from server import current_user  # type: ignore  # circular by app bootstrap design

    router = APIRouter(prefix="/salla", tags=["salla-webhook-monitor"])

    @router.get("/webhook-monitor")
    async def webhook_monitor(user: dict = Depends(current_user)):
        integration = await db.salla_integrations.find_one(
            {"user_id": user["id"]},
            {"_id": 0, "store_id": 1, "status": 1},
        )
        merchant_id = str((integration or {}).get("store_id") or "").strip() or None
        snapshot = await _event_snapshot(db, merchant_id)
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(days=30)

        events: list[dict[str, Any]] = []
        for event_name, label, group in EXPECTED_EVENTS:
            row = snapshot.get(event_name)
            raw_last_received = row.get("last_received_at") if row else None
            raw_first_received = row.get("first_received_at") if row else None
            last_received = _as_utc(raw_last_received)
            first_received = _as_utc(raw_first_received)
            observed = bool(row)
            recent = bool(observed and last_received and last_received >= recent_cutoff)
            events.append({
                "event": event_name,
                "label": label,
                "group": group,
                "status": "working" if observed else "not_observed",
                "observed": observed,
                "recent_30d": recent,
                "first_received_at": first_received,
                "last_received_at": last_received,
                "delivery_count": _safe_int(row.get("delivery_count")) if row else 0,
                "last_order_number": _extract_order_number(row.get("last_payload")) if row else None,
                "shipment_sync": row.get("last_sync") if row else None,
            })

        received_count = sum(1 for item in events if item["observed"])
        return {
            "ok": True,
            "merchant_id": merchant_id,
            "integration_status": (integration or {}).get("status") or "not_connected",
            "api_fallback_enabled": True,
            "api_fallback_note": "لم يتم حذف الاعتماد على Salla API أثناء فترة التحقق من Webhooks.",
            "status_meaning": {
                "working": "وصل الحدث فعليًا من سلة وتم توثيقه.",
                "not_observed": "لم يصل الحدث حتى الآن؛ لا يعني بالضرورة أنه غير مفعّل.",
            },
            "received_events": received_count,
            "total_monitored_events": len(events),
            "generated_at": now,
            "events": events,
        }

    api_router.include_router(router)
