"""Central Salla refresh capability owned by the Mezan Order Engine V2.

The service deliberately reads shipping/customer facts from Order Details itself
and retrieves line items from List Order Items.  It never calls Shipments APIs,
Qoyod, legacy Mezan routes, or page-specific persistence.

For modern Salla applications, Order Details is requested with ``format=light``.
That response still contains the order/customer/receiver facts, while order items
are retrieved through the dedicated ``/orders/items`` endpoint.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from orders_db import upsert_order
from salla_integration.service import SallaError, call_salla
from salla_integration.sync import (
    _enrich_order_receiving_bank,
    _fetch_salla_order_items,
    _salla_order_to_doc,
)


REFRESH_TIMESTAMP_FIELD = "orders_v2_salla_refreshed_at"
REFRESH_MODE_FIELD = "orders_v2_salla_refresh_mode"
REFRESH_ENDPOINT_FIELD = "orders_v2_salla_refresh_endpoint"
REFRESH_ITEMS_FIELD = "orders_v2_salla_refresh_items_count"


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _named(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        for key in ("name", "name_ar", "label", "title", "display_name", "value"):
            text = _text(value.get(key))
            if text and not text.replace(".", "", 1).isdigit():
                return text
        return None
    text = _text(value)
    if text and not text.replace(".", "", 1).isdigit():
        return text
    return None


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        text = _text(value)
        if text:
            return text
    return None


def _deep_overlay_non_empty(base: Any, overlay: Any) -> Any:
    """Overlay current Salla facts without deleting richer saved containers."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = deepcopy(base)
        for key, value in overlay.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = _deep_overlay_non_empty(merged[key], value)
            elif _present(value):
                merged[key] = deepcopy(value)
        return merged
    return deepcopy(overlay) if _present(overlay) else deepcopy(base)


def _address_score(value: Any) -> int:
    if not isinstance(value, dict) or not value:
        return 0
    weights = {
        "street": 10,
        "street_name": 10,
        "street_number": 9,
        "address_line": 9,
        "address_line1": 9,
        "formatted": 8,
        "formatted_address": 8,
        "description": 8,
        "location": 8,
        "short_address": 7,
        "national_address": 7,
        "district": 6,
        "district_name": 6,
        "neighborhood": 6,
        "block": 5,
        "building_number": 5,
        "postal_code": 4,
        "zip_code": 4,
        "city": 3,
        "city_name": 3,
        "country": 2,
        "country_name": 2,
    }
    return sum(weight for key, weight in weights.items() if _present(value.get(key)))


def _walk_address_candidates(
    value: Any,
    *,
    path: str = "order",
    depth: int = 0,
) -> list[tuple[int, str, dict[str, Any]]]:
    if depth > 8:
        return []
    rows: list[tuple[int, str, dict[str, Any]]] = []
    if isinstance(value, dict):
        score = _address_score(value)
        if score:
            rows.append((score, path, dict(value)))
        for key, child in value.items():
            rows.extend(
                _walk_address_candidates(
                    child,
                    path=f"{path}.{key}",
                    depth=depth + 1,
                )
            )
    elif isinstance(value, list):
        for index, child in enumerate(value[:20]):
            rows.extend(
                _walk_address_candidates(
                    child,
                    path=f"{path}[{index}]",
                    depth=depth + 1,
                )
            )
    return rows


def _customer_address(order: dict[str, Any]) -> dict[str, Any]:
    customer = _dict(order.get("customer"))
    receiver = _dict(order.get("receiver"))
    address: dict[str, Any] = {}

    values = {
        "country": customer.get("country") or receiver.get("country"),
        "country_code": customer.get("country_code") or receiver.get("country_code"),
        "city": customer.get("city") or receiver.get("city"),
        "district": (
            customer.get("district")
            or customer.get("neighborhood")
            or customer.get("block")
            or receiver.get("district")
            or receiver.get("neighborhood")
            or receiver.get("block")
        ),
        "street": (
            customer.get("street")
            or customer.get("street_name")
            or receiver.get("street")
            or receiver.get("street_name")
        ),
        "formatted": (
            customer.get("location")
            or customer.get("formatted_address")
            or customer.get("address_line")
            or receiver.get("location")
            or receiver.get("formatted_address")
            or receiver.get("address_line")
        ),
        "short_address": (
            customer.get("short_address")
            or customer.get("national_address")
            or receiver.get("short_address")
            or receiver.get("national_address")
        ),
        "postal_code": customer.get("postal_code") or receiver.get("postal_code"),
        "building_number": customer.get("building_number") or receiver.get("building_number"),
        "additional_number": customer.get("additional_number") or receiver.get("additional_number"),
    }
    for key, value in values.items():
        if _present(value):
            address[key] = deepcopy(value)
    return address


def extract_order_details_address(order: dict[str, Any]) -> tuple[dict[str, Any], Optional[str]]:
    """Read the delivery address from the Order Details payload itself."""
    shipping = _dict(order.get("shipping"))
    receiver = _dict(order.get("receiver"))
    customer = _dict(order.get("customer"))

    preferred = [
        ("order.shipping.ship_to", _dict(shipping.get("ship_to"))),
        ("order.shipping.address", _dict(shipping.get("address"))),
        ("order.ship_to", _dict(order.get("ship_to"))),
        ("order.shipping_address", _dict(order.get("shipping_address"))),
        ("order.address", _dict(order.get("address"))),
        ("order.receiver.address", _dict(receiver.get("address"))),
        ("order.customer.shipping_address", _dict(customer.get("shipping_address"))),
        ("order.customer.address", _dict(customer.get("address"))),
    ]
    for path, candidate in preferred:
        if _address_score(candidate):
            return candidate, path

    customer_candidate = _customer_address(order)
    if _address_score(customer_candidate):
        return customer_candidate, "order.customer"

    candidates = _walk_address_candidates(order)
    if not candidates:
        return {}, None
    candidates.sort(key=lambda row: row[0], reverse=True)
    _, path, candidate = candidates[0]
    return candidate, path


def _address_fields(address: dict[str, Any], source_path: Optional[str]) -> dict[str, Any]:
    if not address:
        return {"shipping_address_found": False}

    city = _named(address.get("city") or address.get("city_name") or address.get("city_data"))
    district = _named(
        address.get("district")
        or address.get("district_name")
        or address.get("neighborhood")
        or address.get("neighbourhood")
        or address.get("block")
    )
    street = _named(
        address.get("street")
        or address.get("street_name")
        or address.get("street_number")
    )
    country = _named(address.get("country") or address.get("country_name") or address.get("country_data"))
    formatted = _first_text(
        address.get("formatted"),
        address.get("formatted_address"),
        address.get("shipping_address"),
        address.get("address_line"),
        address.get("address_line1"),
        address.get("description"),
        address.get("location"),
    )
    short_address = _first_text(
        address.get("short_address"),
        address.get("national_address"),
        address.get("national_address_code"),
    )

    fields: dict[str, Any] = {
        "shipping_address": formatted,
        "shipping_address_raw": deepcopy(address),
        "shipping_address_source_path": source_path,
        "shipping_address_found": True,
        "shipping_address_keys": sorted(str(key) for key in address.keys()),
        "shipping_city": city,
        "customer_city": city,
        "shipping_district": district,
        "shipping_street": street,
        "shipping_national_address": short_address,
        "shipping_short_address": short_address,
        "shipping_postal_code": _first_text(address.get("postal_code"), address.get("zip_code"), address.get("postcode")),
        "shipping_building_number": _first_text(address.get("building_number"), address.get("building_no")),
        "shipping_additional_number": _first_text(address.get("additional_number"), address.get("additional_no")),
        "shipping_country": country,
        "shipping_latitude": address.get("latitude") or address.get("lat"),
        "shipping_longitude": address.get("longitude") or address.get("lng"),
    }
    return {key: value for key, value in fields.items() if _present(value) or key == "shipping_address_found"}


def extract_order_details_shipping_fields(order: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Optional[str]]:
    """Return normalized root fields plus provider-shaped address/shipping objects."""
    address, source_path = extract_order_details_address(order)
    fields = _address_fields(address, source_path)
    shipping = _dict(order.get("shipping"))
    company = _named(
        shipping.get("company")
        or shipping.get("company_name")
        or order.get("shipping_company")
        or order.get("delivery_method")
    )
    method = _named(shipping.get("method") or order.get("shipping_method"))
    if company:
        fields["shipping_company"] = company
    if method:
        fields["shipping_method"] = method
    return fields, address, source_path


def _parse_timestamp(value: Any) -> Optional[datetime]:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _find_internal_order_id(
    db: Any,
    user_id: str,
    order_number: str,
    existing: dict[str, Any],
) -> Optional[str]:
    raw_by_source = _dict(existing.get("raw_by_source"))
    raw = _dict(raw_by_source.get("salla_direct"))
    internal_id = _first_text(existing.get("order_id"), raw.get("id"))
    if internal_id:
        return internal_id

    for params in (
        {"reference_id": order_number, "format": "light", "per_page": 10},
        {"keyword": order_number, "format": "light", "per_page": 10},
    ):
        response = await call_salla(db, user_id, "GET", "/orders", params=params)
        rows = response.get("data") if isinstance(response, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            reference = _first_text(row.get("reference_id"), row.get("order_number"))
            row_id = _text(row.get("id"))
            if row_id and (reference == order_number or row_id == order_number):
                return row_id
    return None


async def refresh_order_from_salla(
    db: Any,
    user_id: str,
    order_number: str,
    *,
    force: bool = False,
    minimum_fresh_seconds: int = 120,
) -> dict[str, Any]:
    """Refresh one Order Engine V2 order from Salla Order Details and Items."""
    normalized = str(order_number or "").strip()
    if not normalized:
        return {"ok": False, "found": False, "code": "order_number_required"}

    existing = await db.unified_orders.find_one(
        {"user_id": str(user_id), "order_number": normalized},
        {
            "_id": 0,
            "order_id": 1,
            "raw_by_source.salla_direct": 1,
            REFRESH_TIMESTAMP_FIELD: 1,
        },
    ) or {}

    refreshed_at = _parse_timestamp(existing.get(REFRESH_TIMESTAMP_FIELD))
    if not force and refreshed_at and minimum_fresh_seconds > 0:
        age = (datetime.now(timezone.utc) - refreshed_at).total_seconds()
        if age < minimum_fresh_seconds:
            return {
                "ok": True,
                "found": bool(existing),
                "updated": False,
                "skipped": True,
                "reason": "fresh_local_snapshot",
                "order_number": normalized,
                "no_shipments_api_calls": True,
                "no_qoyod_calls": True,
            }

    try:
        internal_id = await _find_internal_order_id(
            db,
            str(user_id),
            normalized,
            existing,
        )
        if not internal_id:
            return {
                "ok": True,
                "found": False,
                "updated": False,
                "order_number": normalized,
                "code": "order_not_found_in_salla",
                "no_shipments_api_calls": True,
                "no_qoyod_calls": True,
            }

        details_response = await call_salla(
            db,
            str(user_id),
            "GET",
            f"/orders/{internal_id}",
            params={"format": "light"},
        )
        details = details_response.get("data") if isinstance(details_response, dict) else None
        if not isinstance(details, dict):
            return {
                "ok": False,
                "found": False,
                "updated": False,
                "order_number": normalized,
                "code": "invalid_salla_order_details",
                "no_shipments_api_calls": True,
                "no_qoyod_calls": True,
            }

        actual_reference = _first_text(details.get("reference_id"), details.get("order_number"))
        if actual_reference and actual_reference != normalized:
            return {
                "ok": False,
                "found": False,
                "updated": False,
                "order_number": normalized,
                "code": "salla_order_reference_mismatch",
                "actual_reference": actual_reference,
                "no_shipments_api_calls": True,
                "no_qoyod_calls": True,
            }

        items = await _fetch_salla_order_items(
            db,
            str(user_id),
            internal_id,
        )
        details = await _enrich_order_receiving_bank(
            db,
            str(user_id),
            dict(details),
        )

        raw_by_source = _dict(existing.get("raw_by_source"))
        existing_raw = _dict(raw_by_source.get("salla_direct"))
        merged_raw = _deep_overlay_non_empty(existing_raw, details)
        merged_raw["id"] = details.get("id") or internal_id
        merged_raw["reference_id"] = details.get("reference_id") or normalized
        merged_raw["items"] = items

        shipping_fields, address, address_source = extract_order_details_shipping_fields(details)
        if address:
            if not isinstance(merged_raw.get("shipping_address"), dict) or not merged_raw.get("shipping_address"):
                merged_raw["shipping_address"] = deepcopy(address)
            shipping = _dict(merged_raw.get("shipping"))
            if not isinstance(shipping.get("address"), dict) or not shipping.get("address"):
                shipping["address"] = deepcopy(address)
            if shipping_fields.get("shipping_company") and not _present(shipping.get("company_name")):
                shipping["company_name"] = shipping_fields["shipping_company"]
            if shipping_fields.get("shipping_method") and not _present(shipping.get("method")):
                shipping["method"] = shipping_fields["shipping_method"]
            if shipping:
                merged_raw["shipping"] = shipping

        doc = _salla_order_to_doc(merged_raw)
        doc.update(shipping_fields)
        doc["order_id"] = str(internal_id)
        doc["order_number"] = normalized

        result = await upsert_order(
            db,
            str(user_id),
            normalized,
            doc,
            source="salla_direct",
            raw=merged_raw,
        )

        now = datetime.now(timezone.utc)
        await db.unified_orders.update_one(
            {"user_id": str(user_id), "order_number": normalized},
            {"$set": {
                REFRESH_TIMESTAMP_FIELD: now.isoformat(),
                REFRESH_MODE_FIELD: "orders_v2_central_refresh",
                REFRESH_ENDPOINT_FIELD: "GET /orders/{id}?format=light + GET /orders/items",
                REFRESH_ITEMS_FIELD: len(items),
                "orders_v2_salla_address_source": address_source,
            }},
        )

        return {
            "ok": True,
            "found": True,
            "created": bool(result.get("created")),
            "updated": not bool(result.get("created")),
            "skipped": False,
            "order_number": normalized,
            "source_order_id": str(internal_id),
            "items_count": len(items),
            "address_found": bool(address),
            "address_source": address_source,
            "shipping_company_found": bool(shipping_fields.get("shipping_company")),
            "refreshed_at": now.isoformat(),
            "source": "orders_v2_central_salla_refresh",
            "no_shipments_api_calls": True,
            "no_qoyod_calls": True,
        }
    except SallaError as exc:
        return {
            "ok": False,
            "found": False,
            "updated": False,
            "order_number": normalized,
            "code": "salla_refresh_failed",
            "status_code": exc.status_code,
            "needs_reauth": exc.needs_reauth,
            "message": str(exc)[:300],
            "no_shipments_api_calls": True,
            "no_qoyod_calls": True,
        }
    except Exception as exc:  # pragma: no cover - fail-safe boundary
        return {
            "ok": False,
            "found": False,
            "updated": False,
            "order_number": normalized,
            "code": "orders_v2_refresh_failed",
            "exception_type": type(exc).__name__,
            "message": str(exc)[:300],
            "no_shipments_api_calls": True,
            "no_qoyod_calls": True,
        }
