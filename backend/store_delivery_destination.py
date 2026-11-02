"""Read the proven current shipment's city without changing stored order data.

Customer/profile cities and first-writer root fields can differ from the delivery
destination. Raw shipment history is useful only after current identity fences.
"""
from __future__ import annotations

from typing import Any

from salla_shipping import (
    CANCELLED, CURRENT_SHIPPING, RETURNS, extract_shipping, provider_time, same_carrier,
)
from store_delivery_domain import normalize_city, normalize_text


_INACTIVE = CANCELLED | RETURNS | {"archived", "returned", "return_to_origin", "return_in_progress"}


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _identity(value: Any) -> str:
    if isinstance(value, (dict, list, bool)):
        return ""
    text = normalize_text(value)
    return text if text != "0" else ""


def city_name(value: Any) -> str:
    """Accept textual names, never stringify a provider object or numeric ID."""
    if isinstance(value, dict):
        return next((name for key in ("name", "name_ar", "name_en", "label", "title", "value")
                     if (name := city_name(value.get(key)))), "")
    if not isinstance(value, str):
        return ""
    text = normalize_text(value)
    return text if not text.isdecimal() else ""


def _status(value: Any) -> str:
    obj = _dict(value)
    return _identity(obj.get("slug") or obj.get("code") or obj.get("name") or value).casefold()


def _has_carrier(value: dict) -> bool:
    return bool(value.get("company_code") or value.get("company_name"))


def _raw_shipments(raw: dict) -> list[dict]:
    rows = raw.get("shipments")
    if isinstance(rows, list) and rows:
        return [row for row in rows if isinstance(row, dict)]
    if isinstance(rows, dict) and rows:
        return [rows]
    if raw.get("shipment"):
        return [_dict(raw["shipment"])]
    # A nested shipment may inherit its enclosing carrier, but never displace
    # explicit top-level history, including an unusable row in that history.
    nested = _dict(_dict(raw.get("shipping")).get("shipment"))
    return [nested] if nested else []


def _destination_city(row: dict) -> str:
    for key in ("ship_to", "shipping_address", "address"):
        address = _dict(row.get(key))
        city = city_name(address.get("city")) or city_name(_dict(address.get("address")).get("city"))
        if city:
            return city
    return ""


def current_destination_city(order: dict[str, Any]) -> str:
    """Return a stronger city override only from unambiguous current evidence.

This is deliberately not a new global shipping selector. Existing canonical
shipping identity/clocks govern which stored destination may override legacy
city fields; no provider request or database write is performed here.
"""
    raw = _dict(_dict(order.get("raw_by_source")).get("salla_direct"))
    current = _dict(order.get(CURRENT_SHIPPING))
    current_id = _identity(current.get("shipment_id") if current else order.get("salla_shipment_id"))
    superseded = {_identity(value) for value in current.get("superseded_shipment_ids") or []}
    superseded.discard("")
    if _status(current.get("status")) in _INACTIVE or current_id in superseded:
        return ""

    # Extract carrier identity without letting raw embedded history select an ID.
    raw_carrier_payload = {key: raw[key] for key in
        ("shipping_company", "shipping_company_code") if key in raw}
    raw_carrier_payload["shipping"] = {key: value for key, value in
        _dict(raw.get("shipping")).items() if key != "shipment"}
    raw_carrier = extract_shipping(raw_carrier_payload) or {}
    root_carrier = extract_shipping({key: order[key] for key in
        ("shipping_company", "shipping_company_code") if key in order}) or {}
    carrier = current if _has_carrier(current) else root_carrier or raw_carrier
    shipment_clock = provider_time(current.get("shipment_updated_at"))
    candidates = [(row, raw_carrier) for row in _raw_shipments(raw)]
    webhook = _dict(order.get("salla_shipment_webhook_snapshot"))
    if webhook:
        candidates.append((webhook, {}))

    eligible: list[tuple[str, str, dict]] = []
    for row, enclosing_carrier in candidates:
        row_id = _identity(row.get("id") or row.get("shipment_id"))
        if (_status(row.get("status") or row.get("shipment_status")) in _INACTIVE
                or _status(row.get("type") or row.get("shipment_type")) in RETURNS
                or row.get("archived") is True or row.get("is_archived") is True
                or row.get("archived_at") or row_id in superseded):
            continue
        observation = extract_shipping(row, event_name="shipment.updated") or {}
        row_carrier = observation if _has_carrier(observation) else enclosing_carrier
        if _has_carrier(carrier) and _has_carrier(row_carrier) and not same_carrier(carrier, row_carrier):
            continue
        if current_id:
            if row_id != current_id:
                continue
        else:
            # Canonical None can be an intentional switch awaiting a new label.
            # Only a carrier-matching ID-less address context is safe then.
            if (superseded or (current and "shipment_id" in current and row_id)
                    or not _has_carrier(carrier) or not same_carrier(carrier, row_carrier)):
                continue
        clock = provider_time(row.get("updated_at")) or provider_time(row.get("created_at"))
        if clock and shipment_clock and clock < shipment_clock:
            continue
        eligible.append((row_id, clock or "", row))

    # Multiple possible shipments are not permission to choose an address by
    # array order or richness. Exact current ID is resolved before its city.
    if not eligible or len({row_id for row_id, _, _ in eligible}) != 1:
        return ""
    newest = max(clock for _, clock, _ in eligible)
    cities = [_destination_city(row) for _, clock, row in eligible if clock == newest]
    if not all(cities) or len({normalize_city(city) for city in cities}) != 1:
        return ""
    return cities[0]
