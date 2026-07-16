"""Privacy-safe diagnostics for Salla order address mapping."""
from __future__ import annotations

from typing import Any

from .mapper import OrderMappingError, map_salla_order


_CITY_KEYS = {
    "city",
    "city_name",
    "cityname",
    "town",
    "locality",
    "region",
    "region_name",
    "state",
    "province",
    "district",
    "neighborhood",
}
_CONTAINER_HINTS = (
    "address",
    "shipping",
    "shipment",
    "receiver",
    "recipient",
    "location",
    "delivery",
)
_SENSITIVE_KEYS = {
    "name",
    "full_name",
    "first_name",
    "last_name",
    "mobile",
    "phone",
    "email",
    "street",
    "formatted",
    "formatted_address",
    "description",
    "address_line",
    "postal_code",
    "zip_code",
    "building_number",
    "building_no",
    "additional_number",
    "latitude",
    "longitude",
    "lat",
    "lng",
}


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        return text[:120] if text else None
    return None


def _collect_address_signals(value: Any, *, path: str = "raw", depth: int = 0) -> list[dict[str, Any]]:
    if depth > 8:
        return []

    signals: list[dict[str, Any]] = []
    if isinstance(value, list):
        for index, entry in enumerate(value[:5]):
            signals.extend(
                _collect_address_signals(entry, path=f"{path}[{index}]", depth=depth + 1)
            )
        return signals

    if not isinstance(value, dict):
        return signals

    for raw_key, child in value.items():
        key = str(raw_key or "").strip()
        normalized = key.casefold().replace("-", "_").replace(" ", "_")
        child_path = f"{path}.{key}"

        if normalized in _SENSITIVE_KEYS:
            continue

        if normalized in _CITY_KEYS:
            if isinstance(child, dict):
                safe_values = {
                    k: _safe_scalar(v)
                    for k, v in child.items()
                    if str(k).casefold() in {"name", "label", "value", "title", "slug", "code"}
                    and _safe_scalar(v) is not None
                }
                signals.append(
                    {
                        "path": child_path,
                        "kind": "city_object",
                        "keys": sorted(str(k) for k in child.keys()),
                        "safe_values": safe_values,
                    }
                )
            else:
                signals.append(
                    {
                        "path": child_path,
                        "kind": "city_scalar",
                        "value": _safe_scalar(child),
                    }
                )

        if isinstance(child, dict) and any(hint in normalized for hint in _CONTAINER_HINTS):
            signals.append(
                {
                    "path": child_path,
                    "kind": "container",
                    "keys": sorted(str(k) for k in child.keys()),
                }
            )

        signals.extend(
            _collect_address_signals(child, path=child_path, depth=depth + 1)
        )

    return signals


async def build_order_address_diagnostic(
    db: Any,
    *,
    user_id: str,
    order_number: str,
) -> dict[str, Any]:
    normalized_order_number = str(order_number or "").strip()
    row = await db.unified_orders.find_one(
        {
            "user_id": str(user_id),
            "order_number": normalized_order_number,
            "raw_by_source.salla_direct": {"$exists": True},
        },
        {
            "_id": 0,
            "order_number": 1,
            "city": 1,
            "customer_city": 1,
            "shipping_city": 1,
            "raw_by_source.salla_direct": 1,
        },
    )

    if not isinstance(row, dict):
        return {
            "found": False,
            "order_number": normalized_order_number,
        }

    raw_by_source = row.get("raw_by_source")
    raw = raw_by_source.get("salla_direct") if isinstance(raw_by_source, dict) else None
    raw = raw if isinstance(raw, dict) else {}

    mapped_city = None
    mapping_error = None
    try:
        dto = map_salla_order(raw)
        mapped_city = (
            dto.shipping.address.city
            if dto.shipping and dto.shipping.address
            else None
        )
    except OrderMappingError as exc:
        mapping_error = str(exc)

    top_level_city_fields = {
        key: _safe_scalar(row.get(key))
        for key in ("city", "customer_city", "shipping_city")
        if _safe_scalar(row.get(key)) is not None
    }

    return {
        "found": True,
        "order_number": normalized_order_number,
        "mapped_city": mapped_city,
        "mapping_error": mapping_error,
        "top_level_city_fields": top_level_city_fields,
        "raw_top_level_keys": sorted(str(key) for key in raw.keys()),
        "address_signals": _collect_address_signals(raw),
        "privacy": {
            "customer_identity_excluded": True,
            "phone_email_excluded": True,
            "full_address_excluded": True,
        },
    }


# Backward-compatible alias for deployments where routes.py still imports the
# earlier function name. Keep this until all environments are confirmed on the
# same revision; both names execute the exact same read-only diagnostic.
build_address_diagnostic = build_order_address_diagnostic
