"""Read-only-to-Salla reconciliation of an edited order delivery address.

Salla emits order.shipping.address.updated separately from order.updated.
This handler consumes the verified event and updates only the order's current
delivery-address projection. It never changes an order/shipment status,
carrier AWB, product choices, preparation, stock or financial figures.

The historical shipment ship_to is deliberately not modified: an already
issued carrier label might still contain the earlier destination.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from operational_atomic import operational_owner
from order_engine.salla_refresh import _address_score
from salla_shipping import provider_time

from .webhook_order_sync import (
    _dict, _order_payload, _order_reference, _resolve_user_id,
    _address_fields,
)

# All current-delivery scalar projections; absent fields in a newer complete
# address must clear stale street/zone/national-address values.
_DELIVERY_FIELDS = frozenset({
    "shipping_address", "shipping_address_raw", "shipping_address_source_path",
    "shipping_address_found", "shipping_address_keys",
    "shipping_city", "customer_city", "shipping_district", "shipping_street",
    "shipping_national_address", "shipping_short_address",
    "shipping_postal_code", "shipping_building_number",
    "shipping_additional_number", "shipping_country",
    "shipping_latitude", "shipping_longitude",
})


def _current_address(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Prefer the edited order address to an older shipment label's ship_to."""
    shipping = _dict(payload.get("shipping"))
    shipment = _dict(payload.get("shipment"))
    receiver = _dict(payload.get("receiver"))
    customer = _dict(payload.get("customer"))
    shipments = payload.get("shipments")
    first_shipment = next(
        (row for row in shipments if isinstance(row, dict)), {}
    ) if isinstance(shipments, list) else _dict(shipments)
    candidates = (
        ("shipping.address", shipping.get("address")),
        ("shipping.ship_to", shipping.get("ship_to")),
        ("shipping_address", payload.get("shipping_address")),
        ("ship_to", payload.get("ship_to")),
        ("address", payload.get("address")),
        ("receiver.address", receiver.get("address")),
        ("shipment.ship_to", shipment.get("ship_to")),
        ("shipments[0].ship_to", first_shipment.get("ship_to")),
        ("customer.shipping_address", customer.get("shipping_address")),
        ("customer.address", customer.get("address")),
    )
    for path, raw in candidates:
        value = _dict(raw)
        if _address_score(value):
            return deepcopy(value), path
    return {}, ""


async def sync_verified_shipping_address(db: Any, event_body: dict[str, Any]) -> dict[str, Any]:
    event = str(event_body.get("event") or "").strip()
    if event not in {"order.shipping.address.updated", "order.updated"}:
        return {"attempted": False, "reason": "not_address_event"}
    owner = await _resolve_user_id(db, str(event_body.get("merchant") or "").strip())
    if not owner:
        return {"attempted": True, "synced": False, "reason": "salla_owner_missing"}

    payload = _order_payload(event_body)
    order_number = _order_reference(payload)
    if not order_number:
        return {"attempted": True, "synced": False, "reason": "missing_order_reference"}
    address, source_path = _current_address(payload)
    if not address:
        return {"attempted": True, "synced": False, "reason": "address_not_present"}
    # The webhook can be a sparse city-only notification. Do not clear a
    # complete previously stored delivery address based on a partial event.
    if not any(
        address.get(field) not in (None, "", [], {})
        for field in (
            "address_line", "address_line1", "street", "street_name",
            "street_number", "short_address", "national_address",
            "formatted", "formatted_address", "description", "location",
        )
    ):
        return {"attempted": True, "synced": False,
                "reason": "sparse_address_needs_authoritative_refresh"}

    # For a verified webhook, the event timestamp is the observation ordering
    # boundary. Missing clocks may initialize a row, but must not overwrite a
    # previously versioned observation.
    version = provider_time(event_body.get("created_at")) or provider_time(payload.get("updated_at"))
    values = _address_fields({"shipping": {"address": address}})
    values["shipping_address_source_path"] = "webhook." + source_path
    values["shipping_address_raw"] = address
    now = datetime.now(timezone.utc).isoformat()

    async def persist(scoped: Any) -> dict[str, Any]:
        selector = {"user_id": str(owner), "order_number": str(order_number)}
        existing = await scoped.unified_orders.find_one(
            selector,
            {"_id": 0, "order_id": 1, "raw_by_source.salla_direct.id": 1,
             "salla_address_provider_updated_at": 1},
        )
        if not existing:
            # A partial address webhook must never invent a new order.
            return {"attempted": True, "synced": False, "reason": "order_not_in_mezan"}

        internal_id = str(existing.get("order_id") or _dict(_dict(existing.get("raw_by_source")).get("salla_direct")).get("id") or "")
        incoming_id = str(payload.get("id") or "")
        if internal_id and incoming_id and incoming_id != internal_id:
            return {"attempted": True, "synced": False, "reason": "order_identity_mismatch"}

        previous = provider_time(existing.get("salla_address_provider_updated_at"))
        if previous and (not version or version < previous):
            return {"attempted": True, "synced": False, "reason": "older_address_event"}

        patch = {
            **values, "salla_address_verified_at": now,
            "salla_address_event": event,
        }
        if version:
            patch["salla_address_provider_updated_at"] = version
        unset = {key: "" for key in _DELIVERY_FIELDS if key not in values}
        update: dict[str, Any] = {"$set": patch}
        if unset:
            update["$unset"] = unset
        updated = await scoped.unified_orders.update_one(selector, update)
        return {
            "attempted": True, "synced": bool(getattr(updated, "matched_count", 0)),
            "order_number": str(order_number),
            "reason": "updated_current_delivery_address" if getattr(updated, "matched_count", 0) else "order_changed",
            "source": source_path,
            "no_provider_writes": True,
            "no_accounting_writes": True,
        }

    return await operational_owner(db, str(owner), persist)
