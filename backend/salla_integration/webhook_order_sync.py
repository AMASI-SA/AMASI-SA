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
from .store_scope import is_attribution_pilot_store


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


def _named_text(*values: Any) -> Optional[str]:
    """Return a human-readable name and never stringify an object or numeric ID."""
    for value in values:
        if isinstance(value, dict):
            candidate = _first_text(
                value.get("name"),
                value.get("name_ar"),
                value.get("title"),
                value.get("label"),
                value.get("display_name"),
            )
        else:
            candidate = _text(value)

        if not candidate:
            continue

        # A numeric-only value is an ID, not a city/district/street name.
        if candidate.replace(".", "", 1).isdigit():
            continue

        return candidate

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
    return None


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


def _address_score(address: dict[str, Any]) -> int:
    """Prefer the richest real shipping-address object, not a city-only object."""
    if not isinstance(address, dict) or not address:
        return 0

    score = 0
    weighted_keys = {
        "street": 8,
        "street_name": 8,
        "address_line": 8,
        "address_line1": 8,
        "description": 7,
        "short_address": 7,
        "district": 6,
        "neighborhood": 6,
        "block": 5,
        "building_number": 5,
        "building_no": 5,
        "postal_code": 4,
        "zip_code": 4,
        "additional_number": 3,
        "additional_no": 3,
        "city": 2,
        "country": 1,
        "latitude": 1,
        "longitude": 1,
    }

    for key, weight in weighted_keys.items():
        value = address.get(key)
        if value not in (None, "", [], {}):
            score += weight

    return score


def _walk_address_candidates(
    value: Any,
    *,
    path: str = "payload",
    depth: int = 0,
) -> list[tuple[int, str, dict[str, Any]]]:
    if depth > 10:
        return []

    results: list[tuple[int, str, dict[str, Any]]] = []

    if isinstance(value, dict):
        score = _address_score(value)
        if score:
            results.append((score, path, dict(value)))

        for key, child in value.items():
            child_path = f"{path}.{key}"
            results.extend(
                _walk_address_candidates(
                    child,
                    path=child_path,
                    depth=depth + 1,
                )
            )

    elif isinstance(value, list):
        for index, child in enumerate(value[:30]):
            results.extend(
                _walk_address_candidates(
                    child,
                    path=f"{path}[{index}]",
                    depth=depth + 1,
                )
            )

    return results


def _address_candidate_with_path(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], Optional[str]]:
    """Use Salla's documented shipping-address precedence."""
    shipping = _dict(payload.get("shipping"))
    receiver = _dict(payload.get("receiver"))
    customer = _dict(payload.get("customer"))
    shipment = _first_shipment(payload)

    # Explicit deterministic precedence from Salla webhook models.
    preferred = [
        ("payload.shipments[0].ship_to", _dict(shipment.get("ship_to")), 1000),
        ("payload.shipping.address", _dict(shipping.get("address")), 900),
        ("payload.shipment.address", _dict(shipment.get("address")), 800),
        ("payload.shipping_address", _dict(payload.get("shipping_address")), 700),
        ("payload.address", _dict(payload.get("address")), 600),
        ("payload.receiver.address", _dict(receiver.get("address")), 500),
        ("payload.customer.address", _dict(customer.get("address")), 400),
    ]

    for source_path, candidate, _priority in preferred:
        if candidate and _address_score(candidate):
            return candidate, source_path

    # Unknown payload variants: choose the richest nested address.
    candidates = _walk_address_candidates(payload)
    if not candidates:
        return {}, None

    candidates.sort(key=lambda row: row[0], reverse=True)
    _, source_path, address = candidates[0]
    return address, source_path


def _address_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    address, _ = _address_candidate_with_path(payload)
    return address


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
    address, source_path = _address_candidate_with_path(payload)
    if not address:
        return {"shipping_address_found": False}

    city = _named_text(
        address.get("city"),
        address.get("city_name"),
        address.get("city_data"),
    )

    district = _named_text(
        address.get("district"),
        address.get("district_name"),
        address.get("neighborhood"),
        address.get("neighbourhood"),
        address.get("block"),
    )

    street = _named_text(
        address.get("street"),
        address.get("street_name"),
        address.get("street_number"),
    )

    national_address = _first_text(
        address.get("short_address"),
        address.get("national_address"),
        address.get("national_address_code"),
    )

    full_address = _first_text(
        address.get("shipping_address"),
        address.get("address_line"),
        address.get("address_line1"),
        address.get("formatted"),
        address.get("description"),
    )

    postal_code = _first_text(
        address.get("postal_code"),
        address.get("postcode"),
        address.get("zip_code"),
        address.get("zip"),
    )

    building_number = _first_text(
        address.get("building_number"),
        address.get("building_no"),
        address.get("building"),
        address.get("house_number"),
    )

    additional_number = _first_text(
        address.get("additional_number"),
        address.get("additional_no"),
        address.get("secondary_number"),
    )

    country = _named_text(
        address.get("country"),
        address.get("country_name"),
        address.get("country_data"),
    )

    geo = _dict(address.get("geo_coordinates"))
    coordinates = _dict(address.get("coordinates"))

    result = {
        "shipping_address": full_address,
        "shipping_address_raw": address,
        "shipping_address_source_path": source_path,
        "shipping_address_found": True,
        "shipping_address_keys": sorted(str(key) for key in address.keys()),
        "shipping_city": city,
        "customer_city": city,
        "shipping_district": district,
        "shipping_street": street,
        "shipping_national_address": national_address,
        "shipping_short_address": national_address,
        "shipping_postal_code": postal_code,
        "shipping_building_number": building_number,
        "shipping_additional_number": additional_number,
        "shipping_country": country,
        "shipping_latitude": (
            address.get("latitude")
            or address.get("lat")
            or geo.get("lat")
            or coordinates.get("lat")
        ),
        "shipping_longitude": (
            address.get("longitude")
            or address.get("lng")
            or geo.get("lng")
            or coordinates.get("lng")
        ),
    }

    return {
        key: value
        for key, value in result.items()
        if value not in (None, "", {})
    }


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
    if event_name not in {
        "order.created",
        "order.updated",
        "order.status.updated",
    }:
        return {"attempted": False, "reason": "not_order_snapshot_event"}

    merchant_id = _text(event_body.get("merchant"))
    if is_attribution_pilot_store(merchant_id):
        return {
            "attempted": True,
            "synced": False,
            "merchant_id": merchant_id,
            "reason": "attribution_pilot_store_orders_blocked",
        }
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
        attribution_ledger = {
            "synced": False,
            "reason": "not_attempted",
        }
        try:
            from mezan_attribution_ledger_sync import (
                safe_sync_order_to_attribution_ledger,
            )

            attribution_ledger = await safe_sync_order_to_attribution_ledger(
                db,
                user_id=user_id,
                order=result.get("doc") or doc,
            )
        except Exception as exc:
            # Ledger attribution is analytics enrichment only. It must never
            # block authoritative Salla ingestion or fulfilment.
            attribution_ledger = {
                "synced": False,
                "reason": "ledger_bridge_unavailable",
                "error_type": type(exc).__name__,
            }
        first_party_attribution = {
            "linked": False,
            "reason": "linking_failed",
        }
        try:
            from first_party_attribution import link_order_attribution

            first_party_attribution = await link_order_attribution(
                db,
                user_id=user_id,
                order_number=order_number,
                order_payload=payload,
                order_doc=result.get("doc") or doc,
                store_id=merchant_id,
            )
        except Exception as exc:
            # Attribution is enrichment only. An analytics failure must never
            # block Salla's authoritative order ingestion or fulfilment.
            first_party_attribution["error_type"] = type(exc).__name__
        auto_fulfillment = {
            "attempted": True,
            "promoted": False,
            "reason": "evaluation_failed",
        }
        try:
            from fulfillment_v2_routes import auto_route_instant_order
            from order_engine.repository import MongoOrderRepository
            from order_engine.service import get_order

            canonical_order = await get_order(
                MongoOrderRepository(db),
                user_id=user_id,
                order_number=order_number,
            )
            auto_fulfillment = await auto_route_instant_order(
                db,
                user_id=user_id,
                order=canonical_order,
            )
            auto_fulfillment["attempted"] = True
        except Exception as exc:
            auto_fulfillment["error"] = str(exc)[:300]
        return {
            "attempted": True,
            "synced": True,
            "order_number": order_number,
            "created": bool(result.get("created")),
            "updated": not bool(result.get("created")),
            "address_from_order_webhook": bool(_address_candidate(payload)),
            "shipping_company_from_order_webhook": bool(shipping_fields.get("shipping_company")),
            "tracking_from_order_webhook": bool(shipping_fields.get("tracking_number")),
            "auto_fulfillment": auto_fulfillment,
            "first_party_attribution": first_party_attribution,
            "attribution_ledger": attribution_ledger,
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
    if is_attribution_pilot_store(merchant_id):
        return {
            "attempted": True,
            "synced": False,
            "merchant_id": merchant_id,
            "reason": "attribution_pilot_store_shipments_blocked",
            "no_salla_api_calls": True,
            "no_qoyod_calls": True,
        }
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

    now = datetime.now(timezone.utc)

    # Shipment webhook models can carry the full order snapshot.
    # Reuse the same documented extractor used by order.created.
    update_fields: dict[str, Any] = {
        **_order_shipping_fields(payload),
        "salla_shipment_id": (
            shipment_id
            or _order_shipping_fields(payload).get("salla_shipment_id")
        ),
        "salla_shipment_webhook_snapshot": payload,
        "salla_shipment_webhook_event": event_name,
        "salla_shipment_updated_at": now,
    }

    is_return_shipment = (
        "return" in event_name.lower()
        or _text(payload.get("type")) in {"return", "return_shipment", "reverse"}
    )
    is_cancelled = (
        event_name.lower().endswith(".cancelled")
        or _text(payload.get("status")) in {"cancelled", "canceled"}
    )

    if not is_return_shipment:
        shipment_snapshot = dict(payload)
        if is_cancelled:
            shipment_snapshot["status"] = "cancelled"
            for key in (
                "label", "label_url", "shipping_number", "tracking_number",
                "tracking_link", "tracking_url",
            ):
                shipment_snapshot.pop(key, None)
            update_fields["shipping_status"] = "cancelled"
            update_fields["shipment_status"] = "cancelled"
        # Keep the canonical raw snapshot aligned with shipment webhooks.
        # Order Engine reads this snapshot, not the legacy root fields.
        update_fields[
            "raw_by_source.salla_direct.shipments"
        ] = [shipment_snapshot]

    update_fields = {
        key: value for key, value in update_fields.items()
        if value not in (None, "", {})
    }

    mongo_update: dict[str, Any] = {"$set": update_fields}
    if is_cancelled and not is_return_shipment:
        mongo_update["$unset"] = {
            "shipping_label_url": "",
            "tracking_number": "",
            "tracking_url": "",
            "shipping_number": "",
            "raw_by_source.salla_direct.shipping.label": "",
            "raw_by_source.salla_direct.shipping.label_url": "",
            "raw_by_source.salla_direct.shipping.tracking_number": "",
            "raw_by_source.salla_direct.shipping.shipping_number": "",
            "raw_by_source.salla_direct.shipping.tracking_link": "",
            "raw_by_source.salla_direct.shipping.tracking_url": "",
        }

    result = await db.unified_orders.update_one(
        {"user_id": user_id, "$or": selectors},
        mongo_update,
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
        "address_from_webhook": bool(_address_candidate(payload)),
        "no_salla_api_calls": True,
        "no_qoyod_calls": True,
    }
