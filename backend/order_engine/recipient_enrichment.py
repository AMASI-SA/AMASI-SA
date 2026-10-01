"""Resolve the operational delivery recipient from stored Salla payloads.

Buyer/customer identity and delivery-recipient identity are intentionally kept
separate.  Provider payloads vary between order and shipment shapes, so this
module is the single resolver used by Order Engine read projections and by the
store-courier label path.
"""
from __future__ import annotations

from typing import Any

from .models import OrderDTO


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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
        return _url(
            _first(
                value.get("url"),
                value.get("original"),
                value.get("medium"),
                value.get("thumbnail"),
            )
        )
    return None


def _address(value: Any) -> dict[str, Any] | None:
    data = _dict(value)
    if not data:
        return None

    country = _dict(data.get("country"))
    city = _dict(data.get("city"))
    region = _dict(data.get("region"))
    location = data.get("location")
    location_dict = _dict(location)

    result = {
        "country": _text(_first(country.get("name"), data.get("country"))),
        "country_code": _text(
            _first(country.get("code"), data.get("country_code"))
        ),
        "city": _text(_first(city.get("name"), data.get("city"))),
        "district": _text(
            _first(
                data.get("district"),
                data.get("neighborhood"),
                data.get("block"),
            )
        ),
        "neighborhood": _text(data.get("neighborhood")),
        "block": _text(data.get("block")),
        "street": _text(
            _first(
                data.get("street"),
                data.get("street_name"),
                data.get("street_number"),
            )
        ),
        "street_name": _text(data.get("street_name")),
        "street_number": _text(data.get("street_number")),
        "address_line": _text(
            _first(
                data.get("address_line"),
                data.get("address_line1"),
                data.get("address"),
            )
        ),
        "address_line1": _text(data.get("address_line1")),
        "address_line_two": _text(
            _first(data.get("address_line_two"), data.get("address_line2"))
        ),
        "postal_code": _text(
            _first(data.get("postal_code"), data.get("zip_code"))
        ),
        "building_number": _text(
            _first(data.get("building_number"), data.get("building_no"))
        ),
        "additional_number": _text(data.get("additional_number")),
        "formatted": _text(
            _first(
                data.get("formatted"),
                data.get("formatted_address"),
                data.get("address_line"),
                data.get("address_line1"),
                data.get("address"),
                data.get("description"),
            )
        ),
        "latitude": _first(
            data.get("latitude"),
            data.get("lat"),
            location_dict.get("latitude"),
            location_dict.get("lat"),
        ),
        "longitude": _first(
            data.get("longitude"),
            data.get("lng"),
            location_dict.get("longitude"),
            location_dict.get("lng"),
        ),
        "region": _text(
            _first(region.get("name"), data.get("region_name"))
        ),
        "short_address": _text(
            _first(
                data.get("short_address"),
                data.get("national_address"),
            )
        ),
        "national_address": _text(data.get("national_address")),
        "map_url": _url(
            _first(
                data.get("map_url"),
                data.get("location_url"),
                location,
                data.get("map"),
            )
        ),
    }
    return (
        result
        if any(value not in (None, "", {}, []) for value in result.values())
        else None
    )


def _candidate_containers(
    raw: dict[str, Any],
    shipment: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    shipping = _dict(raw.get("shipping"))
    shipping_address = _dict(raw.get("shipping_address"))
    shipments = _list(raw.get("shipments"))
    first_shipment = _dict(shipments[0]) if shipments else {}
    explicit_shipment = _dict(shipment)

    values = (
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
        explicit_shipment.get("recipient"),
        explicit_shipment.get("receiver"),
        explicit_shipment.get("consignee"),
        explicit_shipment.get("ship_to"),
        first_shipment.get("recipient"),
        first_shipment.get("receiver"),
        first_shipment.get("consignee"),
        first_shipment.get("ship_to"),
    )
    return [_dict(value) for value in values if _dict(value)]


def _delivery_address_candidates(
    raw: dict[str, Any],
    *,
    recipient_candidate: dict[str, Any],
    shipment: dict[str, Any] | None = None,
) -> list[Any]:
    shipping = _dict(raw.get("shipping"))
    shipments = _list(raw.get("shipments"))
    first_shipment = _dict(shipments[0]) if shipments else {}
    explicit_shipment = _dict(shipment)

    # Once a recipient container exists, never silently use customer/buyer
    # address as a fallback.  Missing recipient data must remain visibly
    # missing rather than mixing identities.
    return [
        recipient_candidate.get("address"),
        recipient_candidate.get("shipping_address"),
        recipient_candidate.get("location"),
        recipient_candidate,
        _dict(explicit_shipment.get("ship_to")).get("address"),
        explicit_shipment.get("ship_to"),
        _dict(explicit_shipment.get("receiver")).get("address"),
        explicit_shipment.get("receiver"),
        explicit_shipment.get("address"),
        _dict(first_shipment.get("ship_to")).get("address"),
        first_shipment.get("ship_to"),
        _dict(first_shipment.get("receiver")).get("address"),
        first_shipment.get("receiver"),
        first_shipment.get("shipping_address"),
        first_shipment.get("address"),
        _dict(shipping.get("recipient")).get("address"),
        shipping.get("address"),
    ]


def resolve_salla_recipient(
    raw: dict[str, Any],
    *,
    shipment: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Resolve the provider-supplied delivery recipient without buyer fallback."""

    candidates = _candidate_containers(raw, shipment)
    if not candidates:
        return None
    candidate = candidates[0]

    address = None
    recipient_address_candidates: list[Any] = []
    for row in candidates:
        recipient_address_candidates.extend(
            [
                row.get("address"),
                row.get("shipping_address"),
                row.get("location"),
                row,
            ]
        )
    recipient_address_candidates.extend(
        _delivery_address_candidates(
            raw,
            recipient_candidate=candidate,
            shipment=shipment,
        )
    )
    for address_candidate in recipient_address_candidates:
        address = _address(address_candidate)
        if address:
            break

    recipient = {
        "name": _text(
            _first(
                *[
                    _first(
                        row.get("full_name"),
                        row.get("name"),
                        row.get("recipient_name"),
                    )
                    for row in candidates
                ]
            )
        ),
        "mobile": _text(
            _first(
                *[
                    _first(
                        row.get("mobile"),
                        row.get("phone"),
                        row.get("mobile_number"),
                    )
                    for row in candidates
                ]
            )
        ),
        "email": _text(_first(*[row.get("email") for row in candidates])),
        "avatar_url": _url(
            _first(
                *[
                    _first(
                        row.get("avatar_url"),
                        row.get("avatar"),
                        row.get("image"),
                        row.get("photo"),
                    )
                    for row in candidates
                ]
            )
        ),
        "notes": _text(
            _first(
                *[
                    _first(
                        row.get("notes"),
                        row.get("note"),
                        row.get("description"),
                    )
                    for row in candidates
                ]
            )
        ),
        "address": address,
    }
    return (
        recipient
        if any(value not in (None, "", {}) for value in recipient.values())
        else None
    )


def recipient_is_independent(
    raw: dict[str, Any],
    recipient: dict[str, Any] | None,
) -> bool:
    """Return whether recipient identity is observably different from buyer."""

    if not recipient:
        return False
    customer = _dict(raw.get("customer"))
    buyer_name = _text(
        _first(
            customer.get("full_name"),
            customer.get("name"),
            customer.get("first_name"),
        )
    )
    buyer_mobile = _text(
        _first(customer.get("mobile"), customer.get("phone"))
    )
    recipient_name = _text(recipient.get("name"))
    recipient_mobile = _text(
        _first(recipient.get("mobile"), recipient.get("phone"))
    )

    if recipient_name and buyer_name and recipient_name.casefold() != buyer_name.casefold():
        return True
    if recipient_mobile and buyer_mobile and recipient_mobile != buyer_mobile:
        return True
    return False


# Backward-compatible private name used by older focused tests.
def _recipient_from_raw(raw: dict[str, Any]) -> dict[str, Any] | None:
    return resolve_salla_recipient(raw)


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
        recipient = resolve_salla_recipient(raw)
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


__all__ = [
    "_address",
    "_recipient_from_raw",
    "enrich_order_recipients",
    "recipient_is_independent",
    "resolve_salla_recipient",
]
