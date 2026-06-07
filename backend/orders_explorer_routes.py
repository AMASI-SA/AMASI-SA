"""Orders explorer (Iter-85)

Endpoints:
  GET /api/orders                  → paginated list with filters
  GET /api/orders/status-summary   → historical counts + totals per status
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from auth import get_current_user_from_db
from order_status_policy import default_category_for, get_policy_map, resolve_category


def attach_orders_explorer_routes(parent_router: APIRouter, db) -> None:
    router = APIRouter(prefix="/orders", tags=["orders"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    @router.get("/status-summary")
    async def status_summary(
        from_date: Optional[str] = Query(None),
        to_date: Optional[str] = Query(None),
        user: dict = Depends(current_user),
    ):
        """Aggregate counts + totals per order_status. When date filters
        are passed, returns the slice for that range; otherwise full
        historical data. Always includes the policy category so the UI
        can colour the chip."""
        uid = user["id"]
        match: dict = {"user_id": uid}
        if from_date or to_date:
            match["order_date"] = {}
            if from_date:
                match["order_date"]["$gte"] = from_date
            if to_date:
                match["order_date"]["$lte"] = to_date

        overrides = await get_policy_map(db, uid)

        pipeline = [
            {"$match": match},
            {"$group": {
                "_id": {"$ifNull": ["$order_status", ""]},
                "orders_count": {"$sum": 1},
                "total_amount": {"$sum": {"$ifNull": ["$total_amount", 0]}},
            }},
            {"$sort": {"orders_count": -1}},
        ]
        rows = []
        grand_count, grand_total = 0, 0.0
        async for r in db.unified_orders.aggregate(pipeline):
            raw = (r["_id"] or "").strip()
            display = raw if raw and raw != "\\N" else "(بدون حالة)"
            category = resolve_category(
                raw if raw and raw != "\\N" else None, overrides
            )
            rows.append({
                "status": raw or "__none__",
                "display": display,
                "orders_count": int(r["orders_count"]),
                "total_amount": round(float(r["total_amount"]) or 0, 2),
                "category": category,
                "default_category": default_category_for(
                    raw if raw and raw != "\\N" else None
                ),
            })
            grand_count += int(r["orders_count"])
            grand_total += float(r["total_amount"]) or 0

        by_category = {"confirmed": {"count": 0, "amount": 0.0},
                       "pending":   {"count": 0, "amount": 0.0},
                       "refunded":  {"count": 0, "amount": 0.0},
                       "cancelled": {"count": 0, "amount": 0.0}}
        for r in rows:
            c = r["category"]
            if c in by_category:
                by_category[c]["count"] += r["orders_count"]
                by_category[c]["amount"] += r["total_amount"]
        for c in by_category:
            by_category[c]["amount"] = round(by_category[c]["amount"], 2)

        return {
            "rows": rows,
            "by_category": by_category,
            "totals": {
                "orders_count": grand_count,
                "total_amount": round(grand_total, 2),
            },
        }

    @router.get("")
    async def list_orders(
        from_date: Optional[str] = Query(None),
        to_date: Optional[str] = Query(None),
        status: Optional[str] = Query(None, description="exact order_status or __none__"),
        category: Optional[str] = Query(None, description="policy category filter"),
        payment_method: Optional[str] = Query(None),
        search: Optional[str] = Query(None, description="order_number / customer_name / customer_phone"),
        page: int = Query(1, ge=1),
        limit: int = Query(50, ge=1, le=500),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        overrides = await get_policy_map(db, uid)

        q: dict = {"user_id": uid}
        # Collect AND-able sub-conditions that themselves use $or so we
        # don't clobber them when multiple filters are active.
        and_clauses: list[dict] = []

        if from_date or to_date:
            q["order_date"] = {}
            if from_date:
                q["order_date"]["$gte"] = from_date
            if to_date:
                q["order_date"]["$lte"] = to_date

        if status:
            if status == "__none__":
                and_clauses.append({"$or": [
                    {"order_status": {"$in": [None, ""]}},
                    {"order_status": "\\N"},
                ]})
            else:
                q["order_status"] = status

        # Iter-85 fix — category filter is FIRST-CLASS now. Map the
        # category to the concrete list of order_status values whose
        # policy resolves to that bucket. This way count_documents()
        # and skip/limit operate on the filtered set.
        if category:
            # Build status→category mapping from observed values
            distinct_statuses = await db.unified_orders.distinct(
                "order_status", {"user_id": uid}
            )
            matching: list = []
            include_none = False
            for s in distinct_statuses:
                cat = resolve_category(s, overrides)
                if cat == category:
                    if s in (None, "", "\\N"):
                        include_none = True
                    else:
                        matching.append(s)
            if include_none:
                and_clauses.append({"$or": [
                    {"order_status": {"$in": matching + [None, "", "\\N"]}},
                ]})
            else:
                # No statuses match — fall through with an impossible
                # condition so the result-set is empty (rather than
                # silently returning everything).
                q["order_status"] = {"$in": matching or ["__none_match__"]}

        if payment_method:
            q["payment_method"] = payment_method

        if search:
            s = search.strip()
            and_clauses.append({"$or": [
                {"order_number": {"$regex": s, "$options": "i"}},
                {"customer_name": {"$regex": s, "$options": "i"}},
                {"customer_phone": {"$regex": s, "$options": "i"}},
            ]})

        if and_clauses:
            q["$and"] = and_clauses

        skip = (page - 1) * limit
        total = await db.unified_orders.count_documents(q)
        cursor = (
            db.unified_orders.find(q, {
                "_id": 0,
                "raw_by_source": 0,
                "raw_by_user": 0,
            })
            .sort("order_date", -1)
            .skip(skip)
            .limit(limit)
        )
        items = []
        async for o in cursor:
            o["category"] = resolve_category(o.get("order_status"), overrides)
            items.append(o)

        return {
            "items": items,
            "page": page,
            "limit": limit,
            "total": total,
            "pages": (total + limit - 1) // limit if total > 0 else 1,
        }

    parent_router.include_router(router)
