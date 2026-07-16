"""Read-only city fallback for Order Engine responses.

Salla light-order payloads may expose the delivery city as ``customer.city``
without a full shipping-address object. The canonical mapper correctly gives
shipping addresses precedence; this helper only fills a missing city from the
explicit stored Salla city field. It never guesses from names, notes or text.
"""
from __future__ import annotations

from typing import Any, Iterable

from .models import AddressDTO, OrderDTO


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("name", "label", "value", "title"):
            candidate = _text(value.get(key))
            if candidate:
                return candidate
        return None
    text = str(value).strip()
    return text or None


def _city_from_doc(doc: dict[str, Any]) -> str | None:
    raw_by_source = doc.get("raw_by_source") if isinstance(doc.get("raw_by_source"), dict) else {}
    raw = raw_by_source.get("salla_direct") if isinstance(raw_by_source.get("salla_direct"), dict) else {}
    customer = raw.get("customer") if isinstance(raw.get("customer"), dict) else {}

    # Explicit provider fields only. The first value is the confirmed live Salla
    # shape from order 272552391: raw.customer.city = "الرياض".
    candidates = (
        customer.get("city"),
        raw.get("city"),
        doc.get("city"),
        doc.get("customer_city"),
    )
    for candidate in candidates:
        city = _text(candidate)
        if city:
            return city
    return None


def _with_city(order: OrderDTO, city: str | None) -> OrderDTO:
    if not city:
        return order

    current_address = order.customer.shipping_address
    if current_address and current_address.city:
        return order

    if current_address:
        shipping_address = current_address.model_copy(update={"city": city})
    else:
        shipping_address = AddressDTO(city=city)

    customer = order.customer.model_copy(update={"shipping_address": shipping_address})
    shipping = order.shipping
    if not shipping.address or not shipping.address.city:
        shipping_address_for_order = (
            shipping.address.model_copy(update={"city": city})
            if shipping.address
            else AddressDTO(city=city)
        )
        shipping = shipping.model_copy(update={"address": shipping_address_for_order})

    return order.model_copy(update={"customer": customer, "shipping": shipping})


async def enrich_order_cities(
    db: Any,
    *,
    user_id: str,
    orders: Iterable[OrderDTO],
) -> list[OrderDTO]:
    rows = list(orders)
    missing_numbers = [
        str(order.order_number)
        for order in rows
        if not (order.customer.shipping_address and order.customer.shipping_address.city)
    ]
    if not missing_numbers:
        return rows

    projection = {
        "_id": 0,
        "order_number": 1,
        "reference_id": 1,
        "city": 1,
        "customer_city": 1,
        "raw_by_source.salla_direct.customer.city": 1,
        "raw_by_source.salla_direct.city": 1,
    }
    docs = await db.unified_orders.find(
        {
            "user_id": str(user_id),
            "$or": [
                {"order_number": {"$in": missing_numbers}},
                {"reference_id": {"$in": missing_numbers}},
            ],
        },
        projection,
    ).to_list(length=len(missing_numbers))

    city_by_number: dict[str, str] = {}
    for doc in docs:
        number = str(doc.get("order_number") or doc.get("reference_id") or "").strip()
        city = _city_from_doc(doc)
        if number and city:
            city_by_number[number] = city

    return [_with_city(order, city_by_number.get(str(order.order_number))) for order in rows]
