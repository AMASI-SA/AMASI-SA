"""Privacy-safe diagnostics for shipping, payment and totals mapping."""
from __future__ import annotations

from typing import Any

from .mapper import OrderMappingError, map_salla_order

_HINTS = (
    "ship", "courier", "delivery", "tracking", "waybill", "label",
    "payment", "paid", "transaction", "bank", "card", "amount",
    "total", "subtotal", "discount", "tax", "currency",
)
_SENSITIVE_KEYS = {
    "name", "full_name", "first_name", "last_name", "mobile", "phone",
    "email", "street", "formatted", "formatted_address", "description",
    "address", "address_line", "postal_code", "zip_code", "building_number",
    "building_no", "additional_number", "latitude", "longitude", "lat", "lng",
}
_SAFE_VALUE_KEYS = {
    "code", "slug", "status", "state", "type", "method", "provider",
    "company", "company_name", "courier_name", "tracking_number", "tracking_id",
    "waybill_number", "label_url", "tracking_url", "reference",
    "transaction_reference", "amount", "value", "total", "currency",
    "is_paid", "paid", "paid_at", "brand", "last_four",
}


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        return text[:160] if text else None
    return None


def _normalise_key(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _collect_signals(value: Any, *, path: str = "raw", depth: int = 0) -> list[dict[str, Any]]:
    if depth > 9:
        return []
    if isinstance(value, list):
        signals: list[dict[str, Any]] = []
        for index, entry in enumerate(value[:8]):
            signals.extend(_collect_signals(entry, path=f"{path}[{index}]", depth=depth + 1))
        return signals
    if not isinstance(value, dict):
        return []

    signals: list[dict[str, Any]] = []
    for raw_key, child in value.items():
        key = str(raw_key or "").strip()
        normalized = _normalise_key(key)
        child_path = f"{path}.{key}"
        if normalized in _SENSITIVE_KEYS:
            continue

        relevant = any(hint in normalized for hint in _HINTS)
        if relevant:
            if isinstance(child, dict):
                safe_values = {
                    str(k): _safe_scalar(v)
                    for k, v in child.items()
                    if _normalise_key(k) in _SAFE_VALUE_KEYS and _safe_scalar(v) is not None
                }
                signals.append({
                    "path": child_path,
                    "kind": "container",
                    "keys": sorted(str(k) for k in child.keys()),
                    "safe_values": safe_values,
                })
            elif isinstance(child, list):
                signals.append({
                    "path": child_path,
                    "kind": "list",
                    "length": len(child),
                })
            else:
                signals.append({
                    "path": child_path,
                    "kind": "scalar",
                    "value": _safe_scalar(child),
                })

        signals.extend(_collect_signals(child, path=child_path, depth=depth + 1))
    return signals


async def build_order_commerce_diagnostic(
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
            "payment_method": 1,
            "payment_status": 1,
            "shipping_company": 1,
            "shipping_method": 1,
            "tracking_number": 1,
            "raw_by_source.salla_direct": 1,
        },
    )
    if not isinstance(row, dict):
        return {"found": False, "order_number": normalized_order_number}

    raw_by_source = row.get("raw_by_source")
    raw = raw_by_source.get("salla_direct") if isinstance(raw_by_source, dict) else None
    raw = raw if isinstance(raw, dict) else {}

    mapped: dict[str, Any] = {}
    mapping_error = None
    try:
        dto = map_salla_order(raw)
        mapped = {
            "shipping": dto.shipping.model_dump(mode="json"),
            "payment": dto.payment.model_dump(mode="json"),
            "totals": dto.totals.model_dump(mode="json"),
        }
    except OrderMappingError as exc:
        mapping_error = str(exc)

    top_level_fields = {
        key: _safe_scalar(row.get(key))
        for key in (
            "payment_method", "payment_status", "shipping_company",
            "shipping_method", "tracking_number",
        )
        if _safe_scalar(row.get(key)) is not None
    }

    return {
        "found": True,
        "order_number": normalized_order_number,
        "mapping_error": mapping_error,
        "mapped": mapped,
        "top_level_fields": top_level_fields,
        "raw_top_level_keys": sorted(str(key) for key in raw.keys()),
        "commerce_signals": _collect_signals(raw),
        "privacy": {
            "customer_identity_excluded": True,
            "phone_email_excluded": True,
            "full_address_excluded": True,
        },
    }
