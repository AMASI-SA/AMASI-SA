"""Read the proven current shipment's city without changing stored order data.

Customer/profile cities and first-writer root fields can differ from the delivery
destination. Raw shipment history is useful only after current identity fences.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from salla_shipping import (
    CANCELLED, CURRENT_SHIPPING, RETURNS, extract_shipping, provider_time, same_carrier,
)
from store_delivery_domain import StoreDeliveryRuleError, normalize_city, normalize_text


_INACTIVE = CANCELLED | RETURNS | {"archived", "superseded", "returned", "return_to_origin", "return_in_progress"}


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
    try:
        Decimal(text)
    except InvalidOperation:
        return text
    return ""


def _status(value: Any) -> str:
    obj = _dict(value)
    return _identity(obj.get("slug") or obj.get("code") or obj.get("name") or value).casefold()


def _has_carrier(value: dict) -> bool:
    return bool(value.get("company_code") or value.get("company_name"))


def _order_carrier(order: dict) -> dict:
    # Embedded shipment history must not supply the order's current carrier.
    return extract_shipping({
        "shipping_company": order.get("shipping_company"),
        "shipping_company_code": order.get("shipping_company_code"),
        "shipping": {key: value for key, value in _dict(order.get("shipping")).items()
                     if key != "shipment"},
    }) or {}


def _inactive(row: dict) -> bool:
    return bool(
        _status(row.get("status") or row.get("shipment_status")) in _INACTIVE
        or _status(row.get("type") or row.get("shipment_type")) in RETURNS
        or any(row.get(key) is True for key in ("archived", "is_archived", "superseded", "cancelled", "returned"))
        or row.get("archived_at")
    )


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
    if _inactive(current) or current_id in superseded:
        return ""

    # Extract carrier identity without letting raw embedded history select an ID.
    raw_carrier = _order_carrier(raw)
    root_carrier = _order_carrier(order)
    carrier = current if _has_carrier(current) else root_carrier or raw_carrier
    shipment_clock = provider_time(current.get("shipment_updated_at"))
    candidates = [(row, raw_carrier) for row in _raw_shipments(raw)]
    webhook = _dict(order.get("salla_shipment_webhook_snapshot"))
    if webhook:
        candidates.append((webhook, {}))

    matching: list[tuple[str, str, dict, dict]] = []
    for row, enclosing_carrier in candidates:
        row_id = _identity(row.get("id") or row.get("shipment_id"))
        if current_id:
            if row_id != current_id:
                continue
        else:
            # Canonical None can be an intentional switch awaiting a new label.
            # Only a carrier-matching ID-less address context is safe then.
            if superseded or (current and "shipment_id" in current and row_id):
                continue
        clock = provider_time(row.get("updated_at")) or provider_time(row.get("created_at"))
        matching.append((row_id, clock or "", row, enclosing_carrier))

    # Multiple possible shipments are not permission to choose an address by
    # array order or richness. Exact current ID is resolved before its city.
    if not matching or len({row_id for row_id, _, _, _ in matching}) != 1:
        return ""
    newest = max(clock for _, clock, _, _ in matching)
    cities = []
    for row_id, clock, row, enclosing_carrier in matching:
        if clock != newest:
            continue
        # Select the newest representation before eligibility: a cancellation
        # or carrier conflict cannot resurrect an older accepted webhook city.
        observation = extract_shipping(row, event_name="shipment.updated") or {}
        row_carrier = observation if _has_carrier(observation) else enclosing_carrier
        if (_inactive(row) or row_id in superseded
                or (clock and shipment_clock and clock < shipment_clock)
                or (_has_carrier(carrier) and _has_carrier(row_carrier)
                    and not same_carrier(carrier, row_carrier))
                or (not current_id and (not _has_carrier(carrier)
                                        or not same_carrier(carrier, row_carrier)))):
            return ""
        cities.append(_destination_city(row))
    if not all(cities) or len({normalize_city(city) for city in cities}) != 1:
        return ""
    return cities[0]


def legacy_destination_city(order: dict) -> str:
    """Compatibility for orders without any Salla destination context."""
    return next((city for value in (order.get("shipping_city"),
        _dict(order.get("shipping_address")).get("city"), order.get("customer_city"))
        if (city := city_name(value))), "")


def _provider_destination_is_current(order: dict, row: dict, carrier: dict) -> bool:
    """Fresh reads must not undo a newer stored cancellation or destination."""
    current = _dict(order.get(CURRENT_SHIPPING))
    raw = _dict(_dict(order.get("raw_by_source")).get("salla_direct"))
    shipment_id = _identity(row.get("id"))
    candidates = [(candidate, _order_carrier(raw)) for candidate in _raw_shipments(raw)]
    candidates.append((_dict(order.get("salla_shipment_webhook_snapshot")), {}))
    known = [(candidate, enclosing_carrier) for candidate, enclosing_carrier in candidates
        if _identity(candidate.get("id") or candidate.get("shipment_id")) == shipment_id]
    timed = [(provider_time(candidate.get("updated_at"))
              or provider_time(candidate.get("created_at")) or "", candidate, enclosing_carrier)
             for candidate, enclosing_carrier in known]
    latest = max([provider_time(current.get("shipment_updated_at")) or "",
                  *(clock for clock, _, _ in timed)])
    observed = provider_time(row.get("updated_at")) or provider_time(row.get("created_at")) or ""
    if observed and latest and observed < latest:
        return False
    # Equal or unknown clocks cannot prove a known conflict was superseded.
    for clock, candidate, enclosing_carrier in timed:
        if clock and (clock < latest or (observed and clock < observed)):
            continue
        candidate_carrier = extract_shipping(candidate, event_name="shipment.updated") or {}
        if not _has_carrier(candidate_carrier):
            candidate_carrier = enclosing_carrier
        city = _destination_city(candidate)
        if (_inactive(candidate)
                or (_has_carrier(candidate_carrier) and not same_carrier(carrier, candidate_carrier))
                or (city and normalize_city(city) != normalize_city(_destination_city(row)))):
            return False
    return True


_FENCE_FIELDS = (
    "order_id", "order_number", "reference_id", "shipping_company", "shipping_company_code",
    "shipping", "salla_shipment_id", CURRENT_SHIPPING, "salla_shipment_webhook_snapshot",
    "shipping_city", "shipping_address", "customer_city",
    "order_status", "order_status_slug", "store_delivery_assignment_id", "store_delivery_driver_id",
    "store_delivery_driver_name", "store_delivery_fee_snapshot", "store_delivery_status",
    "store_delivery_assigned_at", "store_delivery_updated_at",
    *(f"raw_by_source.salla_direct.{field}" for field in
      ("id", "reference_id", "shipping_company", "shipping_company_code", "shipping", "shipments", "shipment")),
)


def destination_fence(order: dict) -> dict:
    """Freeze consumed evidence and assignment fields, including missing/null."""
    result = {}
    for field in _FENCE_FIELDS:
        value = order
        for key in field.split("."):
            if not isinstance(value, dict) or key not in value:
                result[field] = {"$exists": False}
                break
            value = value[key]
        else:
            result[field] = {"$exists": True, "$eq": deepcopy(value)}
    return result


async def resolve_assignment_city(db: Any, *, user_id: str, order: dict) -> str:
    """Read the current destination without syncing orders or restoring labels.

    Internal-courier normalization can deliberately clear the canonical label
    ID and omit its destination. In that case only a new, scoped provider read
    can supply the city; stale flat/customer fields are not a safe fallback.
    """
    city = current_destination_city(order)
    if city:
        return city
    raw = _dict(_dict(order.get("raw_by_source")).get("salla_direct"))
    current = _dict(order.get(CURRENT_SHIPPING))
    if not (raw or current or order.get("salla_shipment_id") or order.get("salla_shipment_webhook_snapshot")):
        return legacy_destination_city(order)

    from order_engine.shipping_label_service import (
        ShippingLabelError, _is_store_courier, _order_is_completed, _print_shipment_rows, _resolve_order,
    )
    from salla_integration.service import SallaError
    import httpx

    number = normalize_text(order.get("order_number") or order.get("reference_id"))
    carrier = current if _has_carrier(current) else _order_carrier(order) or _order_carrier(raw)
    current_id = _identity(current.get("shipment_id") if current else order.get("salla_shipment_id"))
    superseded = {_identity(value) for value in current.get("superseded_shipment_ids") or []}
    if (not number or not _has_carrier(carrier) or _inactive(current)
            or (current_id and current_id in superseded)):
        raise StoreDeliveryRuleError("shipping_city_required")
    fence = destination_fence(order)
    try:
        internal_id, provider_order = await _resolve_order(db, user_id, number)
        if (_identity(provider_order.get("id")) != _identity(internal_id)
                or _identity(provider_order.get("reference_id")) != number):
            raise StoreDeliveryRuleError("shipping_city_required")
        if not _order_is_completed(provider_order):
            raise StoreDeliveryRuleError("store_courier_order_not_completed")
        provider_carrier = _order_carrier(provider_order)
        if _has_carrier(provider_carrier) and not same_carrier(carrier, provider_carrier):
            raise StoreDeliveryRuleError("shipping_city_required")
        rows = await _print_shipment_rows(db, user_id, internal_id)
        if len(rows) != 1:
            raise StoreDeliveryRuleError("shipping_city_required")
        row = rows[0]
        row_carrier = extract_shipping(row, event_name="shipment.updated") or {}
        if (_inactive(row) or not _is_store_courier(row)
                or not same_carrier(carrier, row_carrier)
                or (current_id and _identity(row.get("id")) != current_id)
                or not _provider_destination_is_current(order, row, carrier)):
            raise StoreDeliveryRuleError("shipping_city_required")
        city = _destination_city(row)
        if not city:
            raise StoreDeliveryRuleError("shipping_city_required")
    except (ShippingLabelError, SallaError, httpx.HTTPError, TimeoutError) as exc:
        raise StoreDeliveryRuleError("shipping_city_required") from exc

    # Provider reads are awaited; reject if a local update changed their basis.
    if not await db["unified_orders"].find_one({"user_id": user_id, **fence}, {"_id": 1}):
        raise StoreDeliveryRuleError("shipment_destination_changed")
    return city
