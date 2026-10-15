"""Canonical lifecycle policy for shared Mezan components and services."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException


COMPONENT_STATUS_ACTIVE = "active"
COMPONENT_STATUS_INACTIVE = "inactive"
COMPONENT_STATUSES = {
    COMPONENT_STATUS_ACTIVE,
    COMPONENT_STATUS_INACTIVE,
}


def component_status(row: dict[str, Any] | None) -> str:
    """Treat legacy rows without a status as active."""
    if (row or {}).get("status") == COMPONENT_STATUS_INACTIVE:
        return COMPONENT_STATUS_INACTIVE
    return COMPONENT_STATUS_ACTIVE


def component_is_active(row: dict[str, Any] | None) -> bool:
    return component_status(row) == COMPONENT_STATUS_ACTIVE


def active_component_selector() -> dict[str, Any]:
    """Mongo selector that keeps pre-lifecycle rows backward compatible."""
    return {"status": {"$ne": COMPONENT_STATUS_INACTIVE}}


def require_active_component(
    row: dict[str, Any] | None,
    *,
    not_found_code: str = "component_not_found",
) -> dict[str, Any]:
    if not row:
        raise HTTPException(status_code=404, detail={"code": not_found_code})
    if not component_is_active(row):
        raise HTTPException(
            status_code=409,
            detail={"code": "component_inactive"},
        )
    return row


__all__ = [
    "COMPONENT_STATUS_ACTIVE",
    "COMPONENT_STATUS_INACTIVE",
    "COMPONENT_STATUSES",
    "active_component_selector",
    "component_is_active",
    "component_status",
    "require_active_component",
]
