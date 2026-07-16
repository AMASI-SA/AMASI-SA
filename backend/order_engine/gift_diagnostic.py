"""Privacy-safe diagnostics for Salla order gift mapping."""
from __future__ import annotations

from typing import Any


_GIFT_KEY_HINTS = (
    "gift",
    "present",
    "هدية",
    "هديه",
    "إهداء",
    "اهداء",
    "order_type",
    "type",
    "feature",
)
_GIFT_VALUE_HINTS = (
    "gift",
    "gift order",
    "is gift",
    "هدية",
    "هديه",
    "طلب هدية",
    "طلب هديه",
    "طلب كهدية",
    "طلب كهديه",
    "إهداء",
    "اهداء",
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
    "address",
    "shipping_address",
    "billing_address",
    "notes",
    "customer_notes",
    "staff_notes",
}


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        return text[:160] if text else None
    return None


def _normalise(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").strip().casefold().split())


def _is_gift_value(value: Any) -> bool:
    normalized = _normalise(value)
    return normalized in {_normalise(entry) for entry in _GIFT_VALUE_HINTS}


def _collect_gift_signals(value: Any, *, path: str = "raw", depth: int = 0) -> list[dict[str, Any]]:
    if depth > 9:
        return []

    signals: list[dict[str, Any]] = []
    if isinstance(value, list):
        for index, entry in enumerate(value[:10]):
            signals.extend(_collect_gift_signals(entry, path=f"{path}[{index}]", depth=depth + 1))
        return signals

    if not isinstance(value, dict):
        return signals

    for raw_key, child in value.items():
        key = str(raw_key or "").strip()
        normalized_key = _normalise(key)
        child_path = f"{path}.{key}"

        if normalized_key in _SENSITIVE_KEYS:
            continue

        key_matches = any(_normalise(hint) in normalized_key for hint in _GIFT_KEY_HINTS)
        value_matches = not isinstance(child, (dict, list)) and _is_gift_value(child)

        if key_matches:
            if isinstance(child, dict):
                safe_values = {
                    str(k): _safe_scalar(v)
                    for k, v in child.items()
                    if str(k).casefold() in {
                        "enabled", "is_gift", "gift", "type", "name", "label",
                        "title", "value", "slug", "code",
                    }
                    and _safe_scalar(v) is not None
                }
                signals.append({
                    "path": child_path,
                    "kind": "gift_container",
                    "keys": sorted(str(k) for k in child.keys()),
                    "safe_values": safe_values,
                })
            elif isinstance(child, list):
                signals.append({
                    "path": child_path,
                    "kind": "gift_list",
                    "length": len(child),
                })
            else:
                signals.append({
                    "path": child_path,
                    "kind": "gift_scalar",
                    "value": _safe_scalar(child),
                    "matches_gift_label": value_matches,
                })
        elif value_matches:
            signals.append({
                "path": child_path,
                "kind": "gift_label_value",
                "value": _safe_scalar(child),
            })

        signals.extend(_collect_gift_signals(child, path=child_path, depth=depth + 1))

    return signals


async def build_gift_diagnostic(
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
            "is_gift": 1,
            "tags": 1,
            "raw_by_source.salla_direct": 1,
        },
    )

    if not isinstance(row, dict):
        return {"found": False, "order_number": normalized_order_number}

    raw_by_source = row.get("raw_by_source")
    raw = raw_by_source.get("salla_direct") if isinstance(raw_by_source, dict) else None
    raw = raw if isinstance(raw, dict) else {}

    safe_tags = []
    for tag in row.get("tags") or []:
        if isinstance(tag, str) and _is_gift_value(tag):
            safe_tags.append(tag[:120])
        elif isinstance(tag, dict):
            for key in ("name", "label", "value", "title", "slug"):
                candidate = tag.get(key)
                if _is_gift_value(candidate):
                    safe_tags.append(str(candidate)[:120])
                    break

    return {
        "found": True,
        "order_number": normalized_order_number,
        "stored_is_gift": row.get("is_gift"),
        "gift_tags": safe_tags,
        "raw_top_level_keys": sorted(str(key) for key in raw.keys()),
        "gift_signals": _collect_gift_signals(raw),
        "privacy": {
            "customer_identity_excluded": True,
            "phone_email_excluded": True,
            "addresses_excluded": True,
            "notes_excluded": True,
        },
    }
