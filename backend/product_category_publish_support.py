"""Normalize and verify Product Control Center writes at the Salla boundary.

Salla Update Product expects category IDs as integers. Product status is managed
through the dedicated `/products/{product}/status` endpoint and uses Salla status
values (`sale`, `hidden`, `out`) rather than Mezan UI values.

Google taxonomy is stricter: a Mezan write is never considered successful until
a fresh Salla Product Details read returns the requested taxonomy value.
"""
from __future__ import annotations

from typing import Any

from product_google_taxonomy_support import (
    extract_google_taxonomy,
    google_taxonomy_matches,
    taxonomy_candidates,
    taxonomy_sync_state,
)


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


async def _record_google_taxonomy_sync(
    module: Any,
    db: Any,
    *,
    user_id: str,
    path: str,
    expected: Any,
    salla_product_payload: Any,
    attempted_write: bool,
) -> dict[str, Any]:
    state = taxonomy_sync_state(
        expected=expected,
        salla_product_payload=salla_product_payload,
        attempted_write=attempted_write,
    )
    now = module._now()
    salla_id = path.rstrip("/").split("/")[-1]
    actual = extract_google_taxonomy(salla_product_payload)
    await db[module.PRODUCTS].update_one(
        {"user_id": user_id, "salla_product_id": salla_id},
        {"$set": {
            "salla_sync_status": state["salla_sync_status"],
            "salla_synced_at": now if state["verified"] else None,
            "last_verified_at": now,
            "salla_google_taxonomy": actual,
            "salla_sync_error": None if state["verified"] else "google_taxonomy_readback_mismatch",
            "updated_at": now,
        }},
    )
    return state


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
    if getattr(original_call, "_mezan_product_publish_support_v2", False):
        return

    async def wrapped_call(db: Any, user_id: str, method: str, path: str, **kwargs: Any) -> Any:
        incoming_json = kwargs.get("json")
        json_payload = dict(incoming_json) if isinstance(incoming_json, dict) else incoming_json
        if isinstance(json_payload, dict):
            kwargs["json"] = json_payload

        status_value = None
        is_product_update = (
            method.upper() == "PUT"
            and path.startswith("/products/")
            and isinstance(json_payload, dict)
        )
        google_target = None
        if is_product_update:
            status_value = json_payload.pop("__mezan_status", None)
            if "google_product_category" in json_payload:
                google_target = json_payload.get("google_product_category")
                if not taxonomy_candidates(google_target):
                    raise module.SallaError(
                        "Google taxonomy cannot be empty; the write was blocked before Salla.",
                        status_code=422,
                    )
                other_fields = set(json_payload) - {"google_product_category"}
                if other_fields or status_value is not None:
                    raise module.SallaError(
                        "Google taxonomy must be published as an isolated change until its Salla writer is production-verified.",
                        status_code=409,
                    )

        if google_target is not None:
            before_remote = await original_call(db, user_id, "GET", path)
            if google_taxonomy_matches(google_target, before_remote):
                json_payload.pop("google_product_category", None)
                state = await _record_google_taxonomy_sync(
                    module,
                    db,
                    user_id=user_id,
                    path=path,
                    expected=google_target,
                    salla_product_payload=before_remote,
                    attempted_write=False,
                )
                return {
                    "skipped": True,
                    "reason": "google_taxonomy_already_matches",
                    "google_taxonomy_verification": state,
                }

        if is_product_update and not json_payload:
            response: Any = {"skipped": True, "reason": "status_only"}
        else:
            response = await original_call(db, user_id, method, path, **kwargs)

        if status_value is not None:
            salla_status = normalize_salla_status(status_value)
            status_response = await original_call(
                db,
                user_id,
                "POST",
                f"{path}/status",
                json={"status": salla_status},
            )
            return {"product": response, "status": status_response}

        if google_target is not None:
            after_remote = await original_call(db, user_id, "GET", path)
            state = await _record_google_taxonomy_sync(
                module,
                db,
                user_id=user_id,
                path=path,
                expected=google_target,
                salla_product_payload=after_remote,
                attempted_write=True,
            )
            if not state["verified"]:
                raise module.SallaError(
                    "Salla accepted the product request but Google taxonomy did not match on read-back; publish remains unverified.",
                    status_code=502,
                )
            return {"product": response, "google_taxonomy_verification": state}

        return response

    wrapped_call._mezan_product_publish_support_v2 = True  # type: ignore[attr-defined]
    module.call_salla = wrapped_call
