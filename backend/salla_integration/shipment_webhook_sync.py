"""Webhook-driven Salla shipment enrichment.

This module is invoked only after Salla webhook verification succeeds. It never
creates, cancels, or modifies a shipment in Salla and never calls Qoyod.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .service import SallaError, call_salla

log = logging.getLogger("salla.shipment_webhook")


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_shipment_id(event_name: str, body: dict[str, Any]) -> Optional[str]:
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    shipment = data.get("shipment") if isinstance(data.get("shipment"), dict) else {}

    candidates = [
        body.get("shipment_id"),
        data.get("shipment_id"),
        shipment.get("id"),
    ]

    # Some shipment webhooks use data.id as the shipment id. Only accept that
    # ambiguous path when the event name itself clearly refers to shipping.
    lowered = (event_name or "").lower()
    if any(token in lowered for token in ("shipment", "shipping", "delivery")):
        candidates.extend([data.get("id"), body.get("id")])

    for value in candidates:
        text = _text(value)
        if text and text.isdigit():
            return text
    return None


def _extract_data(response: Any) -> dict[str, Any]:
    if isinstance(response, dict) and isinstance(response.get("data"), dict):
        return dict(response["data"])
    return {}


async def _resolve_user_id(
    db: Any,
    merchant_id: Optional[str],
) -> tuple[Optional[str], str]:
    """Resolve the Mezan owner of the Salla installation robustly.

    Salla may deliver ``merchant`` as a JSON number while Easy Mode stores
    ``store_id`` as a string (or vice versa in older rows). Match both shapes.
    A connected exact-store row is preferred. We then accept an exact-store row
    with a user_id even when its status is temporarily stale, because call_salla
    is the authoritative token/status gate. Finally, fall back to the oldest
    connected integration that has a user_id.
    """
    merchant_text = _text(merchant_id)
    merchant_values: list[Any] = []
    if merchant_text:
        merchant_values.append(merchant_text)
        if merchant_text.isdigit():
            try:
                merchant_values.append(int(merchant_text))
            except (TypeError, ValueError, OverflowError):
                pass

    projection = {"_id": 0, "user_id": 1, "store_id": 1, "status": 1}

    if merchant_values:
        exact_connected = await db.salla_integrations.find_one(
            {
                "store_id": {"$in": merchant_values},
                "status": "connected",
                "user_id": {"$exists": True, "$nin": [None, ""]},
            },
            projection,
        )
        user_id = _text((exact_connected or {}).get("user_id"))
        if user_id:
            return user_id, "merchant_exact_connected"

        exact_any_status = await db.salla_integrations.find_one(
            {
                "store_id": {"$in": merchant_values},
                "user_id": {"$exists": True, "$nin": [None, ""]},
            },
            projection,
            sort=[("updated_at", -1), ("created_at", 1)],
        )
        user_id = _text((exact_any_status or {}).get("user_id"))
        if user_id:
            return user_id, "merchant_exact_any_status"

    connected = await db.salla_integrations.find_one(
        {
            "status": "connected",
            "user_id": {"$exists": True, "$nin": [None, ""]},
        },
        projection,
        sort=[("updated_at", -1), ("created_at", 1)],
    )
    user_id = _text((connected or {}).get("user_id"))
    if user_id:
        return user_id, "connected_fallback"

    any_integration = await db.salla_integrations.find_one(
        {"user_id": {"$exists": True, "$nin": [None, ""]}},
        projection,
        sort=[("updated_at", -1), ("created_at", 1)],
    )
    user_id = _text((any_integration or {}).get("user_id"))
    if user_id:
        return user_id, "integration_fallback"

    return None, "not_found"


async def sync_shipment_from_verified_webhook(
    db: Any,
    event_body: dict[str, Any],
) -> dict[str, Any]:
    event_name = _text(event_body.get("event")) or ""
    shipment_id = _extract_shipment_id(event_name, event_body)
    if not shipment_id:
        return {"attempted": False, "reason": "no_shipment_id"}

    merchant_id = _text(event_body.get("merchant"))
    user_id, owner_match_source = await _resolve_user_id(db, merchant_id)
    if not user_id:
        return {
            "attempted": True,
            "synced": False,
            "shipment_id": shipment_id,
            "merchant_id": merchant_id,
            "owner_match_source": owner_match_source,
            "reason": "connected_salla_owner_not_found",
        }

    now = datetime.now(timezone.utc)
    try:
        details_response = await call_salla(
            db, user_id, "GET", f"/shipments/{shipment_id}"
        )
        shipment = _extract_data(details_response)
        if not shipment:
            raise RuntimeError("Shipment Details returned no data object")

        tracking: dict[str, Any] = {}
        tracking_error: Optional[str] = None
        try:
            tracking_response = await call_salla(
                db, user_id, "GET", f"/shipments/{shipment_id}/tracking"
            )
            tracking = _extract_data(tracking_response)
        except SallaError as exc:
            tracking_error = str(exc)[:300]

        order_id = _text(shipment.get("order_id"))
        order_reference = _text(shipment.get("order_reference_id"))
        ship_to = shipment.get("ship_to") if isinstance(shipment.get("ship_to"), dict) else {}

        snapshot = {
            "shipment_id": shipment_id,
            "event": event_name or None,
            "merchant_id": merchant_id,
            "owner_match_source": owner_match_source,
            "details": shipment,
            "tracking": tracking,
            "tracking_error": tracking_error,
            "received_at": now,
            "no_qoyod_calls": True,
            "read_only_salla_calls": True,
        }
        await db.salla_shipment_snapshots.update_one(
            {"user_id": user_id, "shipment_id": shipment_id},
            {
                "$set": {**snapshot, "user_id": user_id, "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

        order_selector_parts: list[dict[str, Any]] = []
        for value in (order_reference, order_id):
            if value:
                order_selector_parts.extend([
                    {"order_number": value},
                    {"reference_id": value},
                    {"salla_order_id": value},
                    {"raw.id": value},
                    {"raw.reference_id": value},
                ])

        update_fields = {
            "shipping_company": _text(shipment.get("courier_name"))
                or _text(shipment.get("external_company_name")),
            "shipping_company_code": _text(shipment.get("courier_id")),
            "shipping_company_logo": _text(shipment.get("courier_logo")),
            "shipping_method": _text(shipment.get("type")),
            "shipping_status": _text(shipment.get("status")),
            "tracking_number": _text(shipment.get("tracking_number"))
                or _text(shipment.get("shipping_number")),
            "tracking_url": _text(shipment.get("tracking_link")),
            "shipping_address": ship_to or None,
            "shipping_city": _text(ship_to.get("city")),
            "shipping_district": _text(ship_to.get("block")),
            "shipping_street": _text(ship_to.get("street_number"))
                or _text(ship_to.get("address_line")),
            "shipping_postal_code": _text(ship_to.get("postal_code")),
            "shipping_building_number": _text(ship_to.get("building_number")),
            "shipping_latitude": ship_to.get("latitude"),
            "shipping_longitude": ship_to.get("longitude"),
            "salla_shipment_id": shipment_id,
            "salla_shipment_snapshot": shipment,
            "salla_shipment_tracking": tracking,
            "salla_shipment_updated_at": now,
        }
        update_fields = {key: value for key, value in update_fields.items() if value is not None}

        matched = 0
        modified = 0
        if order_selector_parts:
            result = await db.unified_orders.update_one(
                {"user_id": user_id, "$or": order_selector_parts},
                {"$set": update_fields},
            )
            matched = result.matched_count
            modified = result.modified_count

        log.info(
            "shipment_webhook.synced event=%s shipment_id=%s order_ref=%s matched=%s owner_source=%s",
            event_name, shipment_id, order_reference, matched, owner_match_source,
        )
        return {
            "attempted": True,
            "synced": True,
            "shipment_id": shipment_id,
            "merchant_id": merchant_id,
            "owner_match_source": owner_match_source,
            "order_id": order_id,
            "order_reference_id": order_reference,
            "order_matched": bool(matched),
            "order_modified": bool(modified),
            "tracking_loaded": bool(tracking),
            "tracking_error": tracking_error,
            "no_qoyod_calls": True,
        }
    except SallaError as exc:
        return {
            "attempted": True,
            "synced": False,
            "shipment_id": shipment_id,
            "merchant_id": merchant_id,
            "owner_match_source": owner_match_source,
            "reason": "salla_api_error",
            "error": str(exc)[:500],
            "needs_reauth": exc.needs_reauth,
            "no_qoyod_calls": True,
        }
    except Exception as exc:  # noqa: BLE001
        log.exception("shipment_webhook.sync_failed shipment_id=%s", shipment_id)
        return {
            "attempted": True,
            "synced": False,
            "shipment_id": shipment_id,
            "merchant_id": merchant_id,
            "owner_match_source": owner_match_source,
            "reason": "unexpected_error",
            "error": str(exc)[:500],
            "no_qoyod_calls": True,
        }
