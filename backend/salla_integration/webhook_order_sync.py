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


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        text = _text(value)
        if text:
            return text
    return None


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
    return _dict(event_body.get("data"))


def _order_payload(event_body: dict[str, Any]) -> dict[str, Any]:
    data = _event_data(event_body)
    order = _dict(data.get("order"))
    if order:
        merged = dict(order)
        for key, value in data.items():
            if key != "order" and key not in merged:
                merged[key] = value
        return merged
    return data


def _order_reference(payload: dict[str, Any]) -> Optional[str]:
    order = _dict(payload.get("order"))
    return _first_text(
        payload.get("reference_id"),
        payload.get("order_number"),
        order.get("reference_id"),
        order.get("order_number"),
    )


def _first_shipment(payload: dict[str, Any]) -> dict[str, Any]:
    shipments = payload.get("shipments")
    if isinstance(shipments, list):
        for row in shipments:
            if isinstance(row, dict):
                return dict(row)
    if isinstance(shipments, dict):
        return dict(shipments)
    return _dict(payload.get("shipment"))


def _address_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    shipping = _dict(payload.get("shipping"))
    receiver = _dict(payload.get("receiver"))
    shipment = _first_shipment(payload)
    ship_to = _dict(shipment.get("ship_to"))

    candidates = (
        _dict(shipping.get("address")),
        _dict(payload.get("shipping_address")),
        _dict(payload.get("address")),
        _dict(receiver.get("address")),
        ship_to,
        _dict(shipment.get("address")),
    )
    for candidate in candidates:
        if candidate:
            return candidate
    return {}


def _company_name(payload: dict[str, Any]) -> Optional[str]:
    shipping = _dict(payload.get("shipping"))
    shipment = _first_shipment(payload)
    courier = _dict(shipment.get("courier"))
    shipping_company = _dict(payload.get("shipping_company"))
    delivery_method = _dict(payload.get("delivery_method"))
    shipping_method = _dict(shipping.get("method"))
    shipping_company_obj = _dict(shipping.get("company"))

    return _first_text(
        shipment.get("courier_name"),
        shipment.get("external_company_name"),
        courier.get("name"),
        courier.get("title"),
        shipping.get("company_name"),
        shipping_company_obj.get("name"),
        shipping_company_obj.get("title"),
        shipping_company.get("name"),
        shipping_company.get("title"),
        delivery_method.get("name"),
        delivery_method.get("title"),
        shipping_method.get("name"),
        shipping_method.get("title"),
        payload.get("courier_name"),
        payload.get("shipping_company_name"),
    )


def _address_fields(payload: dict[str, Any]) -> dict[str, Any]:
    address = _address_candidate(payload)
    if not address:
        return {}

    city = _first_text(
        address.get("city"),
        _dict(address.get("city_data")).get("name"),
        _dict(address.get("city" )).get("name") if isinstance(address.get("city"), dict) else None,
    )
    district = _first_text(
        address.get("district"),
        address.get("neighborhood"),
        address.get("block"),
    )
    street = _first_text(
        address.get("street"),
        address.get("street_name"),
        address.get("address_line"),
        address.get("address_line1"),
        address.get("short_address"),
    )
    postal_code = _first_text(address.get("postal_code"), address.get("zip_code"))
    building_number = _first_text(address.get("building_number"), address.get("building_no"))
    additional_number = _first_text(address.get("additional_number"), address.get("additional_no"))
    country = _first_text(
        address.get("country"),
        _dict(address.get("country_data")).get("name"),
        _dict(address.get("country")).get("name") if isinstance(address.get("country"), dict) else None,
    )

    parts = [district, street, building_number, postal_code, additional_number, city]
    address_text = "، ".join(part for part in parts if part)

    result = {
        "shipping_address": address_text or None,
        "shipping_address_raw": address,
        "shipping_city": city,
        "shipping_district": district,
        "shipping_street": street,
        "shipping_postal_code": postal_code,
        "shipping_building_number": building_number,
        "shipping_additional_number": additional_number,
        "shipping_country": country,
        "shipping_latitude": address.get("latitude") or address.get("lat"),
        "shipping_longitude": address.get("longitude") or address.get("lng"),
    }
    return {key: value for key, value in result.items() if value not in (None, "", {})}


def _order_shipping_fields(payload: dict[str, Any]) -> dict[str, Any]:
    shipment = _first_shipment(payload)
    tracking = _dict(shipment.get("tracking"))
    fields: dict[str, Any] = {
        "shipping_company": _company_name(payload),
        "tracking_number": _first_text(
            shipment.get("tracking_number"),
            shipment.get("shipping_number"),
            shipment.get("awb"),
            tracking.get("number"),
        ),
        "tracking_url": _first_text(
            shipment.get("tracking_link"),
            shipment.get("tracking_url"),
            tracking.get("url"),
        ),
        "shipping_status": _first_text(shipment.get("status")),
        "shipment_status": _first_text(shipment.get("status")),
        "salla_shipment_id": _first_text(shipment.get("id"), shipment.get("shipment_id")),
    }
    fields.update(_address_fields(payload))
    return {key: value for key, value in fields.items() if value not in (None, "", {})}


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

        shipping_fields = _order_shipping_fields(payload)
        doc.update(shipping_fields)
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
            "address_from_order_webhook": bool(_address_candidate(payload)),
            "shipping_company_from_order_webhook": bool(shipping_fields.get("shipping_company")),
            "tracking_from_order_webhook": bool(shipping_fields.get("tracking_number")),
            "no_salla_api_calls": True,
            "no_qoyod_calls": True,
        }
    except Exception as exc:
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
    shipment = _dict(data.get("shipment"))
    if shipment:
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
    order = _dict(payload.get("order"))
    order_number = _first_text(
        payload.get("order_reference_id"),
        payload.get("reference_id"),
        order.get("reference_id"),
        order.get("order_number"),
    )
    order_id = _first_text(payload.get("order_id"), order.get("id"))
    shipment_id = _first_text(
        payload.get("shipment_id"),
        payload.get("id"),
        event_body.get("shipment_id"),
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

    tracking = _dict(payload.get("tracking"))
    courier = _dict(payload.get("courier"))
    address = _dict(payload.get("ship_to")) or _dict(payload.get("address"))
    now = datetime.now(timezone.utc)

    update_fields: dict[str, Any] = {
        "salla_shipment_id": shipment_id,
        "shipping_company": _first_text(
            payload.get("courier_name"),
            payload.get("external_company_name"),
            courier.get("name"),
            courier.get("title"),
        ),
        "shipping_company_code": _first_text(payload.get("courier_id"), courier.get("id")),
        "shipping_company_logo": _first_text(payload.get("courier_logo"), courier.get("logo")),
        "shipping_method": _first_text(payload.get("type")),
        "shipping_status": _first_text(payload.get("status")),
        "shipment_status": _first_text(payload.get("status")),
        "tracking_number": _first_text(
            payload.get("tracking_number"),
            payload.get("shipping_number"),
            payload.get("awb"),
            tracking.get("number"),
        ),
        "tracking_url": _first_text(
            payload.get("tracking_link"),
            payload.get("tracking_url"),
            tracking.get("url"),
        ),
        "salla_shipment_webhook_snapshot": payload,
        "salla_shipment_webhook_event": event_name,
        "salla_shipment_updated_at": now,
    }
    if address:
        update_fields.update(_address_fields({"address": address}))

    update_fields = {
        key: value for key, value in update_fields.items()
        if value not in (None, "", {})
    }

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
        "shipping_company_from_webhook": bool(update_fields.get("shipping_company")),
        "address_from_webhook": bool(address),
        "no_salla_api_calls": True,
        "no_qoyod_calls": True,
    }
