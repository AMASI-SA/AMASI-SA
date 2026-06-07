"""Order Status Policy (Iter-83)
================================

Per-user mapping: order_status → category, where category is one of:
  - "confirmed"   → counted in gross/net (assets)
  - "pending"     → counted SEPARATELY in pending bucket (not in net)
  - "refunded"    → booked as full refund (subtracted from net)
  - "cancelled"   → excluded entirely (only feeds cancelled_orders_count)

Collection: `order_status_policy`
Document : { user_id, status, category, updated_at }

Endpoints (attached under /api):
  GET  /api/order-status-policy             → current policy + observed statuses
  PUT  /api/order-status-policy             → bulk update (list of {status, category})
  POST /api/order-status-policy/reset       → reset to defaults
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import get_current_user_from_db


VALID_CATEGORIES = {"confirmed", "pending", "refunded", "cancelled"}


# Default mapping (Arabic statuses observed in real Salla data + English aliases).
# Anything not in this map falls back to category resolution by keywords.
DEFAULT_POLICY: dict[str, str] = {
    # Confirmed (counted in assets/net)
    "تم التوصيل": "confirmed",
    "تم التنفيذ": "confirmed",
    "delivered": "confirmed",
    "completed": "confirmed",

    # Pending (separated bucket)
    "تم الشحن": "pending",
    "جاري التوصيل": "pending",
    "تم المراجعة": "pending",
    "قيد التنفيذ": "pending",
    "بإنتظار المراجعة": "pending",
    "بانتظار المراجعة": "pending",
    "shipping": "pending",
    "shipped": "pending",
    "processing": "pending",
    "pending": "pending",
    "in_review": "pending",
    "__none__": "pending",   # special key: orders with NO status

    # Refunded
    "مسترجع": "refunded",
    "مسترجعة": "refunded",
    "refunded": "refunded",

    # Cancelled
    "ملغي": "cancelled",
    "ملغية": "cancelled",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}


def _keyword_fallback(status: str) -> str:
    """Categorise a status we haven't seen before using Arabic/English
    keywords — used as a SAFE default for unrecognised statuses."""
    s = (status or "").strip()
    if not s or s == "\\N":
        return "pending"
    sl = s.lower()
    if "ملغ" in s or "cancel" in sl:
        return "cancelled"
    if "مسترج" in s or "refund" in sl:
        return "refunded"
    if (
        "توصيل" in s and "تم" in s
    ) or (
        "تنفيذ" in s and "تم" in s
    ) or "delivered" in sl or "completed" in sl:
        return "confirmed"
    # Anything else (shipping in-progress, awaiting review, unknown) is pending.
    return "pending"


def default_category_for(status: Optional[str]) -> str:
    """Public helper used by compute_metrics when the user has not
    overridden the default policy for a given status."""
    if not status or str(status).strip() in ("", "\\N"):
        return DEFAULT_POLICY.get("__none__", "pending")
    s = str(status).strip()
    if s in DEFAULT_POLICY:
        return DEFAULT_POLICY[s]
    # Try a case-insensitive English match
    sl = s.lower()
    if sl in DEFAULT_POLICY:
        return DEFAULT_POLICY[sl]
    return _keyword_fallback(s)


async def get_policy_map(db, user_id: str) -> dict[str, str]:
    """Return effective policy map for the user — overrides merged on
    top of defaults. Caller uses `.get(status, default_category_for(status))`."""
    overrides: dict[str, str] = {}
    async for doc in db.order_status_policy.find(
        {"user_id": user_id}, {"_id": 0, "status": 1, "category": 1}
    ):
        s = (doc.get("status") or "").strip()
        c = doc.get("category")
        if s and c in VALID_CATEGORIES:
            overrides[s] = c
    return overrides


def resolve_category(
    status: Optional[str], overrides: dict[str, str]
) -> str:
    """Final category for an order_status given the user's overrides."""
    s = (str(status).strip() if status else "") or "__none__"
    if s in overrides:
        return overrides[s]
    if s == "__none__":
        return overrides.get("__none__", default_category_for(None))
    return default_category_for(s)


class PolicyRow(BaseModel):
    status: str = Field(..., min_length=1, max_length=120)
    category: str


class PolicyUpdate(BaseModel):
    items: list[PolicyRow]


def attach_order_status_policy_routes(parent_router: APIRouter, db) -> None:
    router = APIRouter(prefix="/order-status-policy", tags=["settings"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    async def _discover_statuses(uid: str) -> list[dict]:
        """Aggregate unified_orders for the user → list of observed
        statuses with their order count and total amount."""
        pipeline = [
            {"$match": {"user_id": uid}},
            {"$group": {
                "_id": {"$ifNull": ["$order_status", ""]},
                "orders_count": {"$sum": 1},
                "total_amount": {"$sum": {"$ifNull": ["$total_amount", 0]}},
            }},
            {"$sort": {"orders_count": -1}},
        ]
        out = []
        async for r in db.unified_orders.aggregate(pipeline):
            raw = (r["_id"] or "").strip()
            key = raw if raw and raw != "\\N" else "__none__"
            out.append({
                "status": key,
                "display": raw if raw and raw != "\\N" else "(بدون حالة)",
                "orders_count": int(r["orders_count"]),
                "total_amount": round(float(r["total_amount"]) or 0, 2),
            })
        return out

    @router.get("")
    async def get_policy(user: dict = Depends(current_user)):
        uid = user["id"]
        observed = await _discover_statuses(uid)
        overrides = await get_policy_map(db, uid)

        rows = []
        for o in observed:
            s = o["status"]
            category = overrides.get(s, default_category_for(
                None if s == "__none__" else s
            ))
            rows.append({
                **o,
                "category": category,
                "default_category": default_category_for(
                    None if s == "__none__" else s
                ),
                "is_overridden": s in overrides,
            })
        return {
            "rows": rows,
            "categories": [
                {"key": "confirmed", "label": "مؤكدة", "desc": "تُحسب ضمن الأصول والصافي"},
                {"key": "pending",   "label": "معلّقة", "desc": "تظهر منفصلة ولا تدخل الصافي"},
                {"key": "refunded",  "label": "مسترجعة", "desc": "تُخصم من الصافي كاسترجاع"},
                {"key": "cancelled", "label": "ملغاة",  "desc": "تُستبعد بالكامل من المبيعات"},
            ],
        }

    @router.put("")
    async def update_policy(
        payload: PolicyUpdate = Body(...),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        now = datetime.now(timezone.utc).isoformat()
        bulk_ops = []
        from pymongo import UpdateOne
        for it in payload.items:
            if it.category not in VALID_CATEGORIES:
                raise HTTPException(400, f"فئة غير مدعومة: {it.category}")
            s = it.status.strip()
            if not s:
                continue
            default = default_category_for(None if s == "__none__" else s)
            if it.category == default:
                # No need to store an override that matches the default —
                # delete it instead to keep the collection tidy.
                bulk_ops.append(UpdateOne(
                    {"user_id": uid, "status": s},
                    {"$unset": {"category": ""}, "$set": {"updated_at": now}},
                    upsert=False,
                ))
                await db.order_status_policy.delete_one(
                    {"user_id": uid, "status": s}
                )
                continue
            bulk_ops.append(UpdateOne(
                {"user_id": uid, "status": s},
                {"$set": {"category": it.category, "updated_at": now,
                          "user_id": uid, "status": s}},
                upsert=True,
            ))
        if bulk_ops:
            await db.order_status_policy.bulk_write(bulk_ops, ordered=False)
        return {"ok": True, "updated": len(payload.items)}

    @router.post("/reset")
    async def reset_policy(user: dict = Depends(current_user)):
        uid = user["id"]
        r = await db.order_status_policy.delete_many({"user_id": uid})
        return {"ok": True, "deleted": r.deleted_count}

    parent_router.include_router(router)
