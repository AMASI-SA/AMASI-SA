"""Salla Product V2 publish payload normalization.

Keep Mezan drafts expressive while only sending fields accepted by Salla's
Update Product endpoint. In particular, Salla uses `sale` / `out` / `hidden`
status values and integer category IDs.
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
        category_id = int(text)
        if category_id > 0 and category_id not in seen:
            seen.add(category_id)
            result.append(category_id)
    return result


def normalize_publish_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    if "categories" in result:
        result["categories"] = normalize_category_ids(result.get("categories"))
    if "status" in result:
        status = str(result.get("status") or "").strip()
        result["status"] = STATUS_MAP.get(status, status)
    # Update Product uses sale_start/sale_end. Internal draft fields are mapped
    # before this function by product_control_center_routes.
    return result
