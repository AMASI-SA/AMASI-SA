"""Persist verified Salla order/shipment webhooks without calling Salla API.

The webhook payload is the source of truth for order.created/order.updated. Shipment
webhooks only enrich shipment fields already present in the verified payload. This
module never calls Qoyod and never performs outbound Salla API requests.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from orders_db import upsert_order

from .sync import _salla_order_to_doc


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _merchant_values(merchant_id: Optional[str]) -> list[Any]:
    values: list[Any] = []
    text = _text(merchant_id)
    if not text:
        return values
    values.append(text)
    try:
        values.append(int(text))
    except (TypeError, ValueError, OverflowError):
        pass
    return values


async def _resolve_user_id(db: Any, merchant_id: Optional[str]) -> Optional[str]:
    values = _merchant_values(merchant_id)
    if values:
        doc = await db.salla_integrations.find_one(
            {"store_id": {"$in": values}},
            {"user_id": 1},
            sort=[("updated_at", -1)],
        )
        user_id = _text((doc or {}).get("user_id"))
        if user_id:
            return user_id

    doc = await db.salla_integrations.find_one(
        {"user_id": {"$exists": True, "$nin": [None, ""]}},
        {"user_id": 1},
        sort=[("updated_at", -1)],
    )
    return _text((doc or {}).get("user_id"))


def _event_data(event_body: dict[str, Any]) -> dict[str, Any]:
    data = event_body.get("data")
    return dict(data) if isinstance(data, dict) else {}


def _order_payload(event_body: dict[str, Any]) -> dict[str, Any]:
    data = _event_data(event_body)
    order = data.get("order")
    if isinstance(order, dict):
        merged = dict(order)
        # Preserve useful event-level fields without replacing full order values.
        for key, value in data.items():
            if key != "order" and key not in merged:
                merged[key] = value
        return merged
    return data


def _order_reference(payload: dict[str, Any]) -> Optional[str]:
    candidates = (
        payload.get("reference_id"),
        payload.get("order_number"),
        (payload.get("order") or {}).get("reference_id")
        if isinstance(payload.get("order"), dict) else None,
    )
    for value in candidates:
        text = _text(value)
        if text:
            return text
    return None


async def sync_order_from_verified_webhook(
    db: Any,
    event_body: dict[str, Any],
) -> dict[str, Any]:
    event_name = _text(event_body.get("event")) or ""
    if event_name not in {"order.created", "order.updated"}:
        return {"attempted": False, "reason": "not_order_snapshot_event"}

    merchant_id = _text(event_body.get("merchant"))
    user_id = await _resolve_user_id(db, merchant_id)
    if not user_id:
        return {
            "attempted": True,
            "synced": False,
            "reason": "connected_salla_owner_not_found",
        }

    payload = _order_payload(event_body)
    if not payload:
        return {"attempted": True, "synced": False, "reason": "missing_order_payload"}

    try:
        doc = _salla_order_to_doc(payload)
        order_number = _text(doc.get("order_number")) or _order_reference(payload)
        if not order_number:
            return {
                "attempted": True,
                "synced": False,
                "reason": "missing_order_reference",
            }

        doc["order_number"] = order_number
        doc["salla_webhook_event"] = event_name
        doc["salla_webhook_received_at"] = datetime.now(timezone.utc)

        result = await upsert_order(
            db,
            user_id,
            order_number,
            doc,
            source="salla_direct",
            raw=payload,
        )
        return {
            "attempted": True,
            "synced": True,
            "order_number": order_number,
            "created": bool(result.get("created")),
            "updated": not bool(result.get("created")),
            "address_from_order_webhook": True,
            "no_salla_api_calls": True,
            "no_qoyod_calls": True,
        }
    except Exception as exc:  # webhook acknowledgement must remain successful
        return {
            "attempted": True,
            "synced": False,
            "reason": "order_webhook_persist_failed",
            "error": str(exc)[:500],
            "no_salla_api_calls": True,
            "no_qoyod_calls": True,
        }


def _shipment_payload(event_body: dict[str, Any]) -> dict[str, Any]:
    data = _event_data(event_body)
    shipment = data.get("shipment")
    if isinstance(shipment, dict):
        merged = dict(shipment)
        for key, value in data.items():
            if key != "shipment" and key not in merged:
                merged[key] = value
        return merged
    return data


async def sync_shipment_payload_from_verified_webhook(
    db: Any,
    event_body: dict[str, Any],
) -> dict[str, Any]:
    event_name = _text(event_body.get("event")) or ""
    if "shipment" not in event_name:
        return {"attempted": False, "reason": "not_shipment_event"}

    merchant_id = _text(event_body.get("merchant"))
    user_id = await _resolve_user_id(db, merchant_id)
    if not user_id:
        return {
            "attempted": True,
            "synced": False,
            "reason": "connected_salla_owner_not_found",
            "no_salla_api_calls": True,
        }

    payload = _shipment_payload(event_body)
    order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
    order_number = (
        _text(payload.get("order_reference_id"))
        or _text(payload.get("reference_id"))
        or _text(order.get("reference_id"))
        or _text(order.get("order_number"))
    )
    order_id = _text(payload.get("order_id")) or _text(order.get("id"))
    shipment_id = (
        _text(payload.get("shipment_id"))
        or _text(payload.get("id"))
        or _text(event_body.get("shipment_id"))
    )

    selectors: list[dict[str, Any]] = []
    for value in (order_number, order_id):
        if value:
            selectors.extend([
                {"order_number": value},
                {"reference_id": value},
                {"salla_order_id": value},
                {"raw.id": value},
                {"raw.reference_id": value},
            ])

    if not selectors:
        return {
            "attempted": True,
            "synced": False,
            "shipment_id": shipment_id,
            "reason": "missing_order_reference_in_webhook",
            "no_salla_api_calls": True,
            "no_qoyod_calls": True,
        }

    tracking = payload.get("tracking") if isinstance(payload.get("tracking"), dict) else {}
    courier = payload.get("courier") if isinstance(payload.get("courier"), dict) else {}
    now = datetime.now(timezone.utc)
    update_fields = {
        "salla_shipment_id": shipment_id,
        "shipping_company": _text(payload.get("courier_name"))
            or _text(payload.get("external_company_name"))
            or _text(courier.get("name")),
        "shipping_company_code": _text(payload.get("courier_id"))
            or _text(courier.get("id")),
        "shipping_company_logo": _text(payload.get("courier_logo"))
            or _text(courier.get("logo")),
        "shipping_method": _text(payload.get("type")),
        "shipping_status": _text(payload.get("status")),
        "shipment_status": _text(payload.get("status")),
        "tracking_number": _text(payload.get("tracking_number"))
            or _text(payload.get("shipping_number"))
            or _text(tracking.get("number")),
        "tracking_url": _text(payload.get("tracking_link"))
            or _text(tracking.get("url")),
        "salla_shipment_webhook_snapshot": payload,
        "salla_shipment_webhook_event": event_name,
        "salla_shipment_updated_at": now,
    }
    update_fields = {key: value for key, value in update_fields.items() if value is not None}

    result = await db.unified_orders.update_one(
        {"user_id": user_id, "$or": selectors},
        {"$set": update_fields},
    )
    return {
        "attempted": True,
        "synced": bool(result.matched_count),
        "shipment_id": shipment_id,
        "order_reference_id": order_number,
        "order_id": order_id,
        "order_matched": bool(result.matched_count),
        "order_modified": bool(result.modified_count),
        "reason": "synced_from_webhook" if result.matched_count else "order_not_found",
        "no_salla_api_calls": True,
        "no_qoyod_calls": True,
    }
