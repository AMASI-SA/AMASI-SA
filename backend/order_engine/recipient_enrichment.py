"""Resolve an order recipient from the stored Salla payload.

Salla may place the independent delivery recipient under different containers
according to event/API shape. This module keeps that provider variability out
of the public OrderDTO and never falls back to the buyer unless the provider
explicitly supplied the same person.
"""
from __future__ import annotations

from typing import Any

from .models import OrderDTO


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", {}, []):
            return value
    return None


def _url(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text if text.startswith(("https://", "http://")) else None
    if isinstance(value, dict):
        return _url(_first(value.get("url"), value.get("original"), value.get("medium"), value.get("thumbnail")))
    return None


def _address(value: Any) -> dict[str, Any] | None:
    data = _dict(value)
    if not data:
        return None
    country = _dict(data.get("country"))
    city = _dict(data.get("city"))
    region = _dict(data.get("region"))
    result = {
        "country": _text(_first(country.get("name"), data.get("country"))),
        "country_code": _text(_first(country.get("code"), data.get("country_code"))),
        "city": _text(_first(city.get("name"), data.get("city"))),
        "district": _text(_first(data.get("district"), data.get("neighborhood"), data.get("block"))),
        "street": _text(_first(data.get("street"), data.get("street_name"), data.get("street_number"))),
        "postal_code": _text(_first(data.get("postal_code"), data.get("zip_code"))),
        "building_number": _text(_first(data.get("building_number"), data.get("building_no"))),
        "additional_number": _text(data.get("additional_number")),
        "formatted": _text(_first(data.get("formatted"), data.get("formatted_address"), data.get("address_line"), data.get("address"), data.get("description"))),
        "latitude": _first(data.get("latitude"), data.get("lat")),
        "longitude": _first(data.get("longitude"), data.get("lng")),
        "region": _text(_first(region.get("name"), data.get("region_name"))),
        "short_address": _text(data.get("short_address")),
    }
    return result if any(value not in (None, "") for value in result.values()) else None


def _recipient_from_raw(raw: dict[str, Any]) -> dict[str, Any] | None:
    shipping = _dict(raw.get("shipping"))
    shipping_address = _dict(raw.get("shipping_address"))
    shipments = raw.get("shipments") if isinstance(raw.get("shipments"), list) else []
    first_shipment = _dict(shipments[0]) if shipments else {}

    candidate = _dict(
        _first(
            raw.get("recipient"),
            raw.get("receiver"),
            raw.get("consignee"),
            raw.get("ship_to"),
            shipping.get("recipient"),
            shipping.get("receiver"),
            shipping.get("consignee"),
            shipping.get("ship_to"),
            shipping_address.get("recipient"),
            shipping_address.get("receiver"),
            first_shipment.get("recipient"),
            first_shipment.get("receiver"),
            first_shipment.get("consignee"),
            first_shipment.get("ship_to"),
        )
    )
    if not candidate:
        return None

    address_source = _first(
        candidate.get("address"),
        candidate.get("shipping_address"),
        candidate.get("location"),
        candidate,
    )
    recipient = {
        "name": _text(_first(candidate.get("full_name"), candidate.get("name"), candidate.get("recipient_name"))),
        "mobile": _text(_first(candidate.get("mobile"), candidate.get("phone"), candidate.get("mobile_number"))),
        "email": _text(candidate.get("email")),
        "avatar_url": _url(_first(candidate.get("avatar_url"), candidate.get("avatar"), candidate.get("image"), candidate.get("photo"))),
        "notes": _text(_first(candidate.get("notes"), candidate.get("note"), candidate.get("description"))),
        "address": _address(address_source),
    }
    return recipient if any(value not in (None, "", {}) for value in recipient.values()) else None


async def enrich_order_recipients(
    db: Any,
    *,
    user_id: str,
    orders: list[OrderDTO],
) -> list[OrderDTO]:
    if not orders:
        return orders

    numbers = [str(order.order_number) for order in orders]
    rows = await db.unified_orders.find(
        {
            "user_id": str(user_id),
            "order_number": {"$in": numbers},
            "raw_by_source.salla_direct": {"$exists": True},
        },
        {"_id": 0, "order_number": 1, "raw_by_source.salla_direct": 1},
    ).to_list(len(numbers))

    by_number: dict[str, dict[str, Any]] = {}
    for row in rows:
        raw_by_source = _dict(row.get("raw_by_source"))
        raw = _dict(raw_by_source.get("salla_direct"))
        recipient = _recipient_from_raw(raw)
        if recipient:
            by_number[str(row.get("order_number"))] = recipient

    enriched: list[OrderDTO] = []
    for order in orders:
        recipient = by_number.get(str(order.order_number))
        if not recipient:
            enriched.append(order)
            continue
        shipping = order.shipping.model_copy(update={"recipient": recipient})
        enriched.append(order.model_copy(update={"shipping": shipping}))
    return enriched
