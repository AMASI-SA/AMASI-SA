"""Normalize Product Control Center payloads for Salla.

Mezan keeps friendly internal values while Salla Update Product expects integer
category IDs and product status values: sale, hidden, or out.
"""
from __future__ import annotations

from typing import Any


STATUS_MAP = {
    "active": "sale",
    "sale": "sale",
    "inactive": "hidden",
    "hidden": "hidden",
    "out_of_stock": "out",
    "out": "out",
}


def normalize_category_ids(value: Any) -> list[int]:
    if value is None:
        return []
    rows = value if isinstance(value, (list, tuple, set)) else str(value).split(",")
    result: list[int] = []
    seen: set[int] = set()
    for row in rows:
        candidate = row.get("id") if isinstance(row, dict) else row
        text = str(candidate or "").strip()
        if not text:
            continue
        try:
            category_id = int(text)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid category id: {text}") from exc
        if category_id <= 0:
            raise ValueError(f"invalid category id: {text}")
        if category_id not in seen:
            seen.add(category_id)
            result.append(category_id)
    return result


def normalize_product_status(value: Any) -> str:
    text = str(value or "").strip()
    return STATUS_MAP.get(text, text)


def install_product_category_publish_support() -> None:
    import product_control_center_routes as module

    original = module._salla_payload
    if getattr(original, "_mezan_category_publish_support", False):
        return

    def wrapped(patch: dict[str, Any]) -> dict[str, Any]:
        payload = original(patch)
        if "categories" in payload:
            payload["categories"] = normalize_category_ids(payload["categories"])
        if "status" in payload:
            payload["status"] = normalize_product_status(payload["status"])
        return payload

    wrapped._mezan_category_publish_support = True  # type: ignore[attr-defined]
    module._salla_payload = wrapped
