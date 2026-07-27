"""Normalize Product Control Center payloads at the Salla write boundary.

Salla Update Product expects category IDs as integers. Product status is managed
through the dedicated `/products/{product}/status` endpoint and uses Salla status
values (`sale`, `hidden`, `out`) rather than Mezan UI values.
"""
from __future__ import annotations

from typing import Any


STATUS_TO_SALLA = {
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


def normalize_salla_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text not in STATUS_TO_SALLA:
        raise ValueError(f"invalid product status: {text}")
    return STATUS_TO_SALLA[text]


def install_product_category_publish_support() -> None:
    import product_control_center_routes as module

    original_payload = module._salla_payload
    if not getattr(original_payload, "_mezan_category_publish_support", False):
        def wrapped_payload(patch: dict[str, Any]) -> dict[str, Any]:
            payload = original_payload(patch)
            if "categories" in payload:
                payload["categories"] = normalize_category_ids(payload["categories"])
            if "status" in patch:
                payload["__mezan_status"] = patch["status"]
            payload.pop("status", None)
            return payload

        wrapped_payload._mezan_category_publish_support = True  # type: ignore[attr-defined]
        module._salla_payload = wrapped_payload

    original_call = module.call_salla
    if getattr(original_call, "_mezan_product_status_publish_support", False):
        return

    async def wrapped_call(db: Any, user_id: str, method: str, path: str, **kwargs: Any) -> Any:
        json_payload = kwargs.get("json")
        status_value = None
        is_product_update = method.upper() == "PUT" and path.startswith("/products/") and isinstance(json_payload, dict)
        if is_product_update:
            status_value = json_payload.pop("__mezan_status", None)

        if is_product_update and not json_payload:
            response: Any = {"skipped": True, "reason": "status_only"}
        else:
            response = await original_call(db, user_id, method, path, **kwargs)

        if status_value is not None:
            salla_status = normalize_salla_status(status_value)
            status_response = await original_call(db, user_id, "POST", f"{path}/status", json={"status": salla_status})
            return {"product": response, "status": status_response}
        return response

    wrapped_call._mezan_product_status_publish_support = True  # type: ignore[attr-defined]
    module.call_salla = wrapped_call
