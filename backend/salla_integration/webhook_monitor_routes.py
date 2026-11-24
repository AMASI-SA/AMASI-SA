"""Read-only monitor for Salla webhook events approved for this app."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import PyMongoError


log = logging.getLogger("salla.webhook_monitor")
CAPTURES = "salla_webhook_event_captures"
MONITOR_INDEX = "salla_webhook_monitor_metadata_v1"
MONITOR_INDEX_KEYS = [("merchant_id", 1), ("event", 1), ("last_received_at", -1),
                      ("first_received_at", 1), ("delivery_count", 1)]
QUERY_MS = 1500
REQUEST_SECONDS = 5.0
INDEX_BUILD_MS = 15000
INDEX_BUILD_SECONDS = 16.0


# Only events approved/enabled by Salla for this application are monitored.
APPROVED_EVENTS: tuple[tuple[str, str, str], ...] = (
    ("abandoned.cart", "إنشاء سلة متروكة", "abandoned_carts"),
    (
        "abandoned.cart.updated",
        "تحديث السلة المتروكة",
        "abandoned_carts",
    ),
    (
        "abandoned.cart.status.changed",
        "تغيير حالة السلة المتروكة",
        "abandoned_carts",
    ),
    (
        "abandoned.cart.purchased",
        "تحويل السلة إلى طلب",
        "abandoned_carts",
    ),
    ("order.created", "إنشاء الطلب", "orders"),
    ("order.updated", "تحديث بيانات الطلب", "orders"),
    ("order.payment.updated", "تحديث سداد الطلب", "orders"),
    ("order.status.updated", "تحديث حالة الطلب", "orders"),
    ("order.products.updated", "تحديث منتجات الطلب", "orders"),
    ("order.total.price.updated", "تحديث إجمالي سعر الطلب", "orders"),
    ("order.cancelled", "إلغاء الطلب", "orders"),
    ("order.shipment.creating", "جاري إنشاء الشحنة", "shipping"),
    ("order.shipment.created", "تم إنشاء شحنة الطلب", "shipping"),
    ("order.shipment.cancelled", "إلغاء شحنة الطلب", "shipping"),
    ("order.shipment.return.creating", "جاري إنشاء شحنة استرجاع", "shipping"),
    ("shipment.created", "إنشاء الشحنة", "shipping"),
)

_ORDER_PATHS = ("order_number", "reference_id", "data.reference_id", "data.order_number",
                "data.order.reference_id", "data.order.order_number", "data.order_reference_id")
_SYNC_FIELDS = ("attempted", "synced", "no_salla_api_calls", "no_qoyod_calls")
_SYNC_NAMES = ("order_sync", "shipment_sync", "abandoned_cart_sync")
_LATEST_PROJECTION = {"_id": 0, **{f"payload.{path}": 1 for path in _ORDER_PATHS},
                      **{f"{name}.{key}": 1 for name in _SYNC_NAMES for key in _SYNC_FIELDS}}


async def ensure_monitor_indexes(db: Any) -> None:
    """One bounded metadata index build, never invoked by a request."""
    started = time.monotonic()
    log.info("salla_webhook_monitor.index_starting")
    try:
        async with asyncio.timeout(INDEX_BUILD_SECONDS):
            await db[CAPTURES].create_index(MONITOR_INDEX_KEYS, name=MONITOR_INDEX,
                                             maxTimeMS=INDEX_BUILD_MS)
    except (TimeoutError, PyMongoError) as exc:
        log.error("salla_webhook_monitor.index_failed error_type=%s", type(exc).__name__)
        raise
    log.info("salla_webhook_monitor.index_ready elapsed_ms=%d",
             int((time.monotonic() - started) * 1000))


def start_monitor_index_initialization(db: Any) -> asyncio.Task:
    """An owned runtime task; failure does not stop web readiness or other jobs."""
    async def initialize():
        try:
            await ensure_monitor_indexes(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("salla_webhook_monitor.index_unavailable error_type=%s", type(exc).__name__)
            return False
        return True

    # No retry loop and no process-local 'ready' shortcut. The database's named
    # index is the read authority, including on other web/worker replicas.
    return asyncio.create_task(initialize(), name="salla-webhook-monitor-index")


def _scope_unavailable() -> HTTPException:
    return HTTPException(status_code=409, detail={
        "code": "salla_webhook_monitor_scope_unavailable", "status": "unknown",
        "message": "تعذر تحديد متجر سلة المرتبط بالحساب بشكل موثوق؛ حالة الأحداث غير معروفة.",
    })


async def _binding(db: Any, owner: Any) -> tuple[dict, str]:
    if not isinstance(owner, str) or not owner.strip():
        raise _scope_unavailable()
    projection = {"_id": 1, "user_id": 1, "store_id": 1, "status": 1}
    rows = await db.salla_integrations.find({"user_id": owner}, projection).hint(
        "user_id_1").max_time_ms(QUERY_MS).limit(2).to_list(length=2)
    if len(rows) != 1:
        raise _scope_unavailable()
    row = rows[0]
    native = row.get("store_id")
    if isinstance(native, bool) or not isinstance(native, (str, int)):
        raise _scope_unavailable()
    merchant = str(native).strip()
    if not merchant or row.get("user_id") != owner:
        raise _scope_unavailable()
    variants: list[Any] = [merchant]
    if merchant.isascii() and merchant.isdigit() and len(merchant) <= 19:
        numeric = int(merchant)
        if numeric <= 2**63 - 1:
            variants.append(numeric)
    reverse = await db.salla_integrations.find({"store_id": {"$in": variants}}, projection).hint(
        "salla_webhook_store_owner").max_time_ms(QUERY_MS).limit(2).to_list(length=2)
    if len(reverse) != 1 or reverse[0] != row:
        raise _scope_unavailable()
    return row, merchant


def _extract_order_number(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    order = data.get("order") if isinstance(data.get("order"), dict) else {}
    candidates = [
        payload.get("order_number"),
        payload.get("reference_id"),
        data.get("reference_id"),
        data.get("order_number"),
        order.get("reference_id"),
        order.get("order_number"),
        data.get("order_reference_id"),
    ]
    for value in candidates:
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            continue
        text = str(value or "").strip()
        if text:
            return text
    return None


def _as_utc(value: Any) -> datetime | None:
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


def _event_pipeline(merchant_id: str) -> list[dict[str, Any]]:
    # Every history row contributes to its delivery total. The metadata index
    # supplies this order and all group inputs without fetching any payload.
    return [
        {"$match": {"merchant_id": merchant_id,
                    "event": {"$in": [name for name, _, _ in APPROVED_EVENTS]}}},
        {"$sort": {"event": 1, "last_received_at": -1}},
        {"$group": {
            "_id": "$event",
            "last_received_at": {"$first": "$last_received_at"},
            # Preserve the original selection, including absent timestamps;
            # an older first_received_at is not a substitute for this ordering.
            "first_received_at": {"$last": "$first_received_at"},
            "delivery_count": {"$sum": {"$ifNull": ["$delivery_count", 1]}},
        }},
    ]


async def _event_snapshot(db: Any, merchant_id: str | None) -> dict[str, dict[str, Any]]:
    if not isinstance(merchant_id, str) or not merchant_id.strip():
        raise _scope_unavailable()
    captures = db[CAPTURES]
    # A missing/building index fails closed. Never fall back to a collection
    # scan, create an index here, or turn an incomplete count into zero.
    rows = await captures.aggregate(_event_pipeline(merchant_id), hint=MONITOR_INDEX,
        maxTimeMS=QUERY_MS, allowDiskUse=False).to_list(length=len(APPROVED_EVENTS))
    result = {}
    for row in rows:
        event = row["_id"]
        # Only one document per observed approved event is fetched, and only
        # order-reference paths plus boolean processing metadata leave Mongo.
        latest = await captures.find({"merchant_id": merchant_id, "event": event,
            "last_received_at": row.get("last_received_at")}, _LATEST_PROJECTION).hint(
            MONITOR_INDEX).max_time_ms(QUERY_MS).limit(1).to_list(length=1)
        if not latest:
            # A concurrent delivery can move the selected last timestamp.
            # Do not combine its new detail with the earlier aggregate total.
            raise HTTPException(status_code=503, detail={
                "code": "salla_webhook_monitor_changed", "status": "unknown",
                "message": "تغير سجل الأحداث أثناء القراءة؛ أعد تحميل حالة المراقبة.",
            })
        row["last_payload"] = latest[0].get("payload")
        for name in _SYNC_NAMES:
            value = latest[0].get(name)
            row[f"last_{name}"] = ({key: value[key] for key in _SYNC_FIELDS
                if isinstance(value.get(key), bool)} if isinstance(value, dict) else None)
        result[event] = row
    return result


async def _queue_snapshot(db: Any, *, user_id: str, merchant_id: str | None,
                          now: datetime | None = None) -> dict[str, Any]:
    """Count only this owner's unfinished jobs; never retrieve event payloads."""
    states = ("pending", "processing", "retry", "quarantined")
    summary = {**dict.fromkeys(states, 0), "backlog": 0,
               "oldest_unfinished_at": None, "oldest_unfinished_age_seconds": 0,
               "expired_processing_count": 0, "oldest_expired_processing_age_seconds": 0}
    if not user_id or not merchant_id:
        return summary
    current = _as_utc(now) or datetime.now(timezone.utc)
    expired = {"$and": [{"$eq": ["$state", "processing"]},
                         {"$lte": ["$lease_until", current]}]}
    from .webhook_inbox import COLLECTION
    rows = await db[COLLECTION].aggregate([
        {"$match": {"user_id": str(user_id), "merchant_id": str(merchant_id),
                    "state": {"$in": list(states)}}},
        {"$group": {"_id": "$state", "count": {"$sum": 1},
                    "oldest_created_at": {"$min": "$created_at"},
                    "expired_count": {"$sum": {"$cond": [expired, 1, 0]}},
                    "oldest_expired_lease": {"$min": {"$cond": [expired, "$lease_until", None]}}}},
    ], hint="salla_webhook_inbox_merchant_state", maxTimeMS=QUERY_MS,
        allowDiskUse=False).to_list(length=len(states))
    oldest = None
    for row in rows:
        state = row.get("_id")
        if state not in states:
            continue
        summary[state] = _safe_int(row.get("count"))
        summary["backlog"] += summary[state]
        created = _as_utc(row.get("oldest_created_at"))
        if created is not None and (oldest is None or created < oldest):
            oldest = created
        summary["expired_processing_count"] += _safe_int(row.get("expired_count"))
        expired_at = _as_utc(row.get("oldest_expired_lease"))
        if expired_at is not None:
            summary["oldest_expired_processing_age_seconds"] = max(
                summary["oldest_expired_processing_age_seconds"],
                int((current - expired_at).total_seconds()),
            )
    summary["oldest_unfinished_at"] = oldest
    if oldest is not None:
        summary["oldest_unfinished_age_seconds"] = max(0, int((current - oldest).total_seconds()))
    return summary


def attach_salla_webhook_monitor_routes(api_router: APIRouter, db: Any) -> None:
    from server import current_user  # type: ignore  # circular by app bootstrap design

    router = APIRouter(prefix="/salla", tags=["salla-webhook-monitor"])

    @router.get("/webhook-monitor")
    async def webhook_monitor(user: dict = Depends(current_user)):
        stage = "binding"
        try:
            async with asyncio.timeout(REQUEST_SECONDS):
                integration, merchant_id = await _binding(db, user.get("id"))
                stage = "events"
                snapshot = await _event_snapshot(db, merchant_id)
                now = datetime.now(timezone.utc)
                stage = "queue"
                queue = await _queue_snapshot(db, user_id=user["id"], merchant_id=merchant_id, now=now)
                stage = "binding_recheck"
                if await _binding(db, user["id"]) != (integration, merchant_id):
                    raise _scope_unavailable()
        except (TimeoutError, PyMongoError) as exc:
            log.warning("salla_webhook_monitor.unavailable stage=%s error_type=%s",
                        stage, type(exc).__name__)
            raise HTTPException(status_code=503, detail={
                "code": "salla_webhook_monitor_unavailable", "status": "unknown",
                "message": "تعذر تحميل مراقبة أحداث سلة الآن؛ الأعداد وحالة الاستلام غير معروفة. حاول مجددًا.",
            }, headers={"Retry-After": "5"}) from None
        recent_cutoff = now - timedelta(days=30)

        events: list[dict[str, Any]] = []
        for event_name, label, group in APPROVED_EVENTS:
            row = snapshot.get(event_name)
            last_received = _as_utc(row.get("last_received_at")) if row else None
            first_received = _as_utc(row.get("first_received_at")) if row else None
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
                "order_sync": row.get("last_order_sync") if row else None,
                "shipment_sync": row.get("last_shipment_sync") if row else None,
                "abandoned_cart_sync": (
                    row.get("last_abandoned_cart_sync") if row else None
                ),
            })

        received_count = sum(1 for item in events if item["observed"])
        return {
            "ok": True,
            "merchant_id": merchant_id,
            "integration_status": (integration.get("status")
                if isinstance(integration.get("status"), str) else "not_connected"),
            "api_fallback_enabled": True,
            "api_fallback_note": "API سلة ما زال متاحًا للوظائف الأخرى، لكن الطلبات والعنوان والشحن في هذه المرحلة تُحفظ مباشرة من Webhooks المعتمدة.",
            "status_meaning": {
                "working": "وصل الحدث فعليًا من سلة وتم توثيقه.",
                "not_observed": "الحدث معتمد لكنه لم يصل حتى الآن.",
            },
            "received_events": received_count,
            "total_monitored_events": len(events),
            "generated_at": now,
            "queue": queue,
            "events": events,
        }

    api_router.include_router(router)
