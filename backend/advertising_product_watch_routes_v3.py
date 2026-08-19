"""Owner-scoped read routes for Advertising Product Watch V3."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, Query

from advertising_product_watch_v3 import ALERT_COLLECTION


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    output = {key: value for key, value in row.items() if key != "_id"}
    for key in ("first_seen_at", "last_seen_at", "resolved_at", "updated_at"):
        if hasattr(output.get(key), "isoformat"):
            output[key] = output[key].isoformat()
    return output


def attach_advertising_product_watch_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
) -> None:
    @router.get("/ai-monitor/product-watch/alerts", include_in_schema=False)
    async def product_watch_alerts(
        status: str = Query(default="active", pattern="^(active|resolved|all)$"),
        limit: int = Query(default=50, ge=1, le=200),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = str(user.get("id") or user.get("_id") or "")
        selector: dict[str, Any] = {"user_id": user_id}
        if status != "all":
            selector["status"] = status
        rows = await db[ALERT_COLLECTION].find(
            selector,
            {"_id": 0},
        ).sort([("severity", 1), ("last_seen_at", -1)]).limit(limit).to_list(length=limit)
        active = await db[ALERT_COLLECTION].count_documents({"user_id": user_id, "status": "active"})
        critical = await db[ALERT_COLLECTION].count_documents({
            "user_id": user_id,
            "status": "active",
            "severity": "critical",
        })
        return {
            "source": "advertising_product_watch_v3",
            "marketing_decision": False,
            "writes_performed": False,
            "active_count": active,
            "critical_count": critical,
            "items": [_serialize(row) for row in rows],
        }


__all__ = ["attach_advertising_product_watch_routes"]
