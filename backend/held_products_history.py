"""Read-only mobile stop history; released stops never leave this feed."""
from copy import deepcopy
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query


HOLDS = "mezan_fulfillment_holds_v1"
PIECES = "mezan_preparation_pieces_v1"
PRODUCT_FIELDS = (
    "piece_id", "order_number", "order_item_id", "unit_index", "product_id",
    "product_name", "sku", "image_url", "selected_image_url", "resolved_image_url",
    "product_options_snapshot", "specifications_snapshot", "file_number", "batch_id",
    "responsible_employee_id", "responsible_employee_name", "supplier_id", "supplier_name",
)
HISTORY_FIELDS = (
    "id", "order_number", "scope", "stop_type", "stop_label", "note", "status",
    "created_at", "created_by_name", "released_at", "released_by_name", "release_note",
    "instruction_id", "source",
)


def hold_product_snapshot(piece: dict[str, Any]) -> dict[str, Any]:
    """Freeze display and assignment at stop time, without costs or private data."""
    return deepcopy({key: piece[key] for key in PRODUCT_FIELDS if key in piece})


def make_held_products_history_router(db: Any, current_user: Callable, actor_context: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/holds/history")
    async def held_products_history(
        status: Literal["all", "active", "released"] = "all",
        scope: Literal["mine", "all"] = "mine",
        page: int = Query(default=1, ge=1, le=10000),
        page_size: int = Query(default=20, ge=1, le=100),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await actor_context(db, user)
        permissions = set(context.get("permissions") or [])
        manage = bool(context.get("is_owner") or "fulfillment.stop.manage" in permissions)
        if not (manage or "preparation.assigned.stop" in permissions
                or "preparation.assigned.work" in permissions):
            raise HTTPException(403, detail={"code": "fulfillment_stop_permission_required"})
        if scope == "all" and not manage:
            raise HTTPException(403, detail={"code": "fulfillment_stop_manage_permission_required"})
        actor_id = str(context.get("actor_id") or "").strip()
        merchant_id = str(context.get("merchant_id") or "").strip()
        if not actor_id or not merchant_id:
            raise HTTPException(403, detail={"code": "fulfillment_stop_permission_required"})
        await db[HOLDS].create_index(
            [("user_id", 1), ("employee_ids", 1), ("created_at", -1), ("id", -1)],
            name="ix_fulfillment_hold_employee_history_v1",
        )
        await db[HOLDS].create_index(
            [("user_id", 1), ("created_at", -1), ("id", -1)],
            name="ix_fulfillment_hold_history_v1",
        )
        query = {"user_id": merchant_id}
        if scope == "mine":
            query["employee_ids"] = actor_id
        if status != "all":
            query["status"] = status
        offset = (page - 1) * page_size
        total = await db[HOLDS].count_documents(query)
        holds = await db[HOLDS].find(query, {"_id": 0}).sort(
            [("created_at", -1), ("id", -1)]
        ).skip(offset).limit(page_size).to_list(page_size)
        legacy_ids = list({pid for row in holds if row.get("product_snapshots") is None
                           for pid in row.get("piece_ids") or []})
        legacy = {}
        if legacy_ids:
            # Old holds have no immutable per-piece assignee. Never infer that
            # every product belonged to every employee listed on an order hold.
            piece_query = {"user_id": merchant_id, "piece_id": {"$in": legacy_ids}}
            if scope == "mine":
                piece_query["responsible_employee_id"] = actor_id
            rows = await db[PIECES].find(piece_query, {"_id": 0}).to_list(len(legacy_ids))
            legacy = {row["piece_id"]: hold_product_snapshot(row) for row in rows}
        items = []
        for hold in holds:
            available = hold.get("product_snapshots") is not None
            products = hold.get("product_snapshots") if available else [
                legacy[pid] for pid in hold.get("piece_ids") or [] if pid in legacy
            ]
            products = [hold_product_snapshot(p) for p in products or []
                        if scope == "all" or p.get("responsible_employee_id") == actor_id]
            entry = {key: hold[key] for key in HISTORY_FIELDS if key in hold}
            entry.update(products=products, snapshot_available=available)
            items.append(entry)
        return {"items": items, "total": total, "page": page, "page_size": page_size,
                "has_more": offset + len(holds) < total, "scope": scope, "status": status}

    return router
