"""Read-only, employee-owned supplier-invoice history for My Products."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from fastapi import Depends, HTTPException, Query

CONTRACT = "employee_supplier_invoice_history_v1"
SESSIONS = "mezan_supplier_receiving_sessions_v1"


def own_closed_invoice_query(context: dict[str, Any]) -> dict[str, Any]:
    merchant = str(context.get("merchant_id") or "").strip()
    actor = str(context.get("actor_id") or "").strip()
    if not merchant or not actor:
        raise HTTPException(403, detail={"code": "supplier_invoice_actor_required"})
    return {"user_id": merchant, "opened_by": actor, "status": "closed",
            "supplier_invoice.id": {"$type": "string", "$nin": [""]}}


def history_pipeline(context: dict[str, Any], supplier_id: str | None = None) -> list[dict[str, Any]]:
    stages: list[dict[str, Any]] = [
        {"$match": own_closed_invoice_query(context)},
        {"$addFields": {
            "history_supplier_id": {"$cond": [
                {"$in": [{"$ifNull": ["$supplier_id", ""]}, [""]]},
                "$supplier_snapshot.id", "$supplier_id"]},
            "history_invoice_date": {"$convert": {
                "input": {"$ifNull": ["$closed_at", "$supplier_invoice.created_at"]},
                "to": "date", "onError": None, "onNull": None}},
        }},
        {"$match": {"history_supplier_id": {"$type": "string", "$nin": [""]}}},
    ]
    if supplier_id is not None:
        stages.append({"$match": {"history_supplier_id": supplier_id}})
    stages.append({"$sort": {"history_invoice_date": -1, "id": -1}})
    return stages


def _iso(value: Any) -> str | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()


async def history_page(db: Any, context: dict[str, Any], *, supplier_id: str | None,
                       offset: int, limit: int) -> dict[str, Any]:
    pipeline = history_pipeline(context, supplier_id)
    if supplier_id is None:
        pipeline.extend([
            {"$group": {"_id": "$history_supplier_id",
                        "supplier_name": {"$first": "$supplier_snapshot.company_name"},
                        "invoice_count": {"$sum": 1},
                        "latest_invoice_at": {"$first": "$history_invoice_date"}}},
            {"$sort": {"latest_invoice_at": -1, "_id": 1}},
        ])
    # Count before pagination, not from the catalog's last 50 sessions.
    pipeline.append({"$facet": {"items": [{"$skip": offset}, {"$limit": limit}],
                                 "count": [{"$count": "total"}]}})
    result = await db[SESSIONS].aggregate(pipeline).to_list(length=1)
    page = result[0] if result else {"items": [], "count": []}
    total = int(page["count"][0]["total"]) if page["count"] else 0
    actor_id = context["actor_id"]
    items = []
    for row in page["items"]:
        if supplier_id is None:
            items.append({"supplier_id": row["_id"],
                          "supplier_name": str(row.get("supplier_name") or "مورد"),
                          "invoice_count": int(row["invoice_count"]),
                          "latest_invoice_at": _iso(row.get("latest_invoice_at")),
                          "actor_id": actor_id})
        else:
            invoice = row["supplier_invoice"]
            items.append({"supplier_id": row["history_supplier_id"],
                          "invoice_id": invoice["id"],
                          "invoice_number": str(invoice.get("invoice_number") or invoice["id"]),
                          "invoice_date": _iso(row.get("history_invoice_date")),
                          "product_count": max(0, int(row.get("scan_count") or 0)),
                          "share_confirmed": invoice.get("share_confirmed") is True,
                          "experiment_mode": row.get("experiment_mode") is True,
                          "actor_id": actor_id})
    next_offset = offset + len(items)
    return {"ok": True, "contract": CONTRACT, "scope": "actor_only",
            "actor_id": actor_id, "supplier_id": supplier_id,
            "items": items, "total": total, "offset": offset,
            "next_offset": next_offset if items and next_offset < total else None,
            "read_only": True, "financial_writes": False}


def register_invoice_history_routes(router: Any, db: Any, current_user: Callable[..., Any],
                                    actor_context: Callable[..., Any], require_permission: Callable[..., Any],
                                    receive_permission: str) -> None:
    @router.get("/invoice-history/suppliers")
    async def suppliers(limit: int = Query(default=20, ge=1, le=100),
                        offset: int = Query(default=0, ge=0),
                        user: dict = Depends(current_user)) -> dict[str, Any]:
        context = await actor_context(db, user)
        require_permission(context, receive_permission)
        return await history_page(db, context, supplier_id=None, offset=offset, limit=limit)

    @router.get("/invoice-history/suppliers/{supplier_id}")
    async def invoices(supplier_id: str, limit: int = Query(default=20, ge=1, le=100),
                       offset: int = Query(default=0, ge=0),
                       user: dict = Depends(current_user)) -> dict[str, Any]:
        context = await actor_context(db, user)
        require_permission(context, receive_permission)
        if not supplier_id.strip() or len(supplier_id) > 160:
            raise HTTPException(422, detail={"code": "supplier_invoice_supplier_required"})
        return await history_page(db, context, supplier_id=supplier_id, offset=offset, limit=limit)
