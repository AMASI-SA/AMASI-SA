"""Shared tenant-scoped component category validation and group protection.

ADR-001: reuse the canonical collections (9), validate before the single resource
write (6), preserve existing router-specific required/active policies (7), and
scope every read by the authenticated tenant (11). No new store or migration.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from product_v2_routes import _text

COMPONENT_CATEGORIES = "mezan_component_categories_v2"
COMPONENT_GROUPS = "mezan_component_groups_v2"


def unique_category_ids(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list) else []:
        category_id = _text(value)
        if category_id and category_id not in seen:
            seen.add(category_id)
            result.append(category_id)
    return result


async def validate_category_ids(
    db: Any, *, user_id: str, values: Any,
    required: bool = False, active_only: bool = False,
) -> list[str]:
    category_ids = unique_category_ids(values)
    if required and not category_ids:
        raise HTTPException(status_code=422, detail={"code": "component_category_required"})
    selector: dict[str, Any] = {"user_id": user_id, "id": {"$in": category_ids}}
    if active_only:
        selector["status"] = {"$ne": "inactive"}
    rows = await db[COMPONENT_CATEGORIES].find(
        selector, {"_id": 0, "id": 1},
    ).to_list(length=max(1, len(category_ids)))
    found = {_text(row.get("id")) for row in rows}
    missing = [category_id for category_id in category_ids if category_id not in found]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"code": "component_category_not_found", "category_ids": missing},
        )
    return category_ids


async def protect_group_categories(
    db: Any, *, user_id: str, resource_id: str, category_ids: list[str],
) -> None:
    protected = await db[COMPONENT_GROUPS].find_one(
        {"user_id": user_id, "resource_ids": resource_id,
         "category_id": {"$nin": category_ids}},
        {"_id": 0, "id": 1, "category_id": 1},
    )
    if protected:
        raise HTTPException(
            status_code=409,
            detail={"code": "component_category_used_by_group",
                    "group_id": protected.get("id"),
                    "category_id": protected.get("category_id")},
        )


async def validate_category_change(
    db: Any, *, user_id: str, resource_id: str, values: Any,
    required: bool = False, active_only: bool = False,
) -> list[str]:
    # Invalid IDs always fail before the write. For an empty removal, the group
    # blocker takes precedence; otherwise the newer required-category rule stays.
    category_ids = await validate_category_ids(
        db, user_id=user_id, values=values, active_only=active_only,
    )
    await protect_group_categories(
        db, user_id=user_id, resource_id=resource_id, category_ids=category_ids,
    )
    if required and not category_ids:
        raise HTTPException(status_code=422, detail={"code": "component_category_required"})
    return category_ids
