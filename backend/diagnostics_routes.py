"""Order Diagnostics — تشخيص فروقات الطلبات
============================================
Iter-58 — a read-only diagnostic console that explains WHY the merchant
sees a gap between Salla's order/sales totals and what the system shows.

It does NOT modify any data. The merchant approves any cleanup
explicitly in a follow-up flow (deferred to Phase 2).

Key signals collected
---------------------
1. **per_source_counts**       — how many orders in each store: unified_orders,
                                 webhook_orders, legacy `analyses` (with
                                 imported=null/0), per the date range.
2. **legacy_overlap**           — orders whose `order_number` lives in BOTH a
                                 legacy analysis (counted as standalone) AND
                                 in `unified_orders` (counted again). This is
                                 the most common cause of "system > Salla".
3. **legacy_file_duplicates**   — same Excel filename uploaded multiple times
                                 with `orders_imported=null` (each upload adds
                                 to dashboard totals).
4. **unified_status_breakdown** — distribution of statuses in unified_orders
                                 (some may be cancelled but still counted in
                                 gross sales).
5. **salla_diff**               — when merchant supplies salla reference numbers,
                                 we compute exact gap and try to attribute it.
"""

from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import get_current_user_from_db


# ── Pydantic ───────────────────────────────────────────────────────────────
class CompareIn(BaseModel):
    from_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    to_date:   Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    salla_orders_count: Optional[int] = Field(None, ge=0)
    salla_total_sales:  Optional[float] = Field(None, ge=0)
    # Optional list of order numbers exported from Salla, for set-diff
    salla_order_numbers: Optional[List[str]] = None


# ── Helpers ────────────────────────────────────────────────────────────────
def _date_range_to_iso(from_date: str | None, to_date: str | None):
    """Mongo expects ISO strings; orders sometimes store dates as YYYY-MM-DD
    and sometimes as datetime ISO. We match prefix for safety."""
    return from_date, to_date


async def _unified_in_range(db, user_id: str, from_date: str | None, to_date: str | None):
    q: dict = {"user_id": user_id}
    if from_date or to_date:
        # `order_date` is the canonical date field on unified_orders (YYYY-MM-DD).
        q["order_date"] = {}
        if from_date:
            q["order_date"]["$gte"] = from_date
        if to_date:
            q["order_date"]["$lte"] = to_date
    return await db.unified_orders.find(q, {"_id": 0}).to_list(50000)


async def _legacy_analyses_in_range(db, user_id: str, from_date: str | None, to_date: str | None):
    q: dict = {
        "user_id": user_id,
        "$or": [
            {"orders_imported": None},
            {"orders_imported": {"$in": [None, 0]}},
        ],
    }
    if from_date or to_date:
        q["date"] = {}
        if from_date:
            q["date"]["$gte"] = from_date
        if to_date:
            q["date"]["$lte"] = to_date
    return await db.analyses.find(q, {"_id": 0}).to_list(2000)


# ── Router ─────────────────────────────────────────────────────────────────
def attach_diagnostics_routes(parent_router: APIRouter, db) -> None:
    router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    @router.get("/summary")
    async def diag_summary(
        user: dict = Depends(current_user),
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ):
        """Quick non-destructive overview — what the merchant sees first."""
        uid = user["id"]
        unified = await _unified_in_range(db, uid, from_date, to_date)
        legacy = await _legacy_analyses_in_range(db, uid, from_date, to_date)
        webhook_count = await db.webhook_orders.count_documents({"user_id": uid})

        # Totals in unified
        unified_sales = round(sum(float(o.get("total_amount") or 0) for o in unified), 2)
        unified_count = len(unified)

        # Totals in legacy (these get ADDED to the dashboard total today)
        legacy_orders = 0
        legacy_sales = 0.0
        for a in legacy:
            s = (a.get("report") or {}).get("summary") or {}
            legacy_orders += int(s.get("total_orders") or 0)
            legacy_sales  += float(s.get("total_sales") or 0)
        legacy_sales = round(legacy_sales, 2)

        return {
            "range": {"from_date": from_date, "to_date": to_date},
            "unified": {"orders": unified_count, "sales": unified_sales},
            "legacy_analyses": {
                "count": len(legacy),
                "orders": legacy_orders,
                "sales": legacy_sales,
            },
            "webhook_orders_total": webhook_count,
            "system_total": {
                "orders": unified_count + legacy_orders,
                "sales": round(unified_sales + legacy_sales, 2),
            },
        }

    @router.get("/scan-duplicates")
    async def scan_duplicates(
        user: dict = Depends(current_user),
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ):
        """Find every possible source of inflation:

        - same order_number appearing in both `unified_orders` AND embedded
          inside a legacy `analyses.report.orders_sample`
        - duplicate legacy analyses (same filename, overlapping orders)
        - same order_number with multiple records (shouldn't happen but we
          check anyway as a safety net)
        """
        uid = user["id"]
        unified = await _unified_in_range(db, uid, from_date, to_date)
        legacy = await _legacy_analyses_in_range(db, uid, from_date, to_date)

        # 1) Index unified by order_number (canonical key)
        unified_by_num: dict[str, list] = defaultdict(list)
        for o in unified:
            num = str(o.get("order_number") or "").strip()
            if num:
                unified_by_num[num].append(o)

        # 2) Walk legacy analyses, collect each order_number found in their
        #    sample, and detect overlaps with unified.
        legacy_overlap_orders: list[dict] = []
        legacy_only_orders: list[dict] = []
        legacy_order_seen_in: dict[str, list[str]] = defaultdict(list)
        for a in legacy:
            aid = a.get("id") or "?"
            filename = a.get("filename") or "?"
            created = (a.get("created_at") or "?")[:19]
            for o in ((a.get("report") or {}).get("orders_sample") or []):
                num = str(o.get("order_number") or "").strip()
                if not num:
                    continue
                legacy_order_seen_in[num].append(f"{filename} @ {created}")
                base = {
                    "order_number":     num,
                    "amount":           float(o.get("total_amount") or 0),
                    "order_date":       o.get("order_date"),
                    "payment_method":   o.get("payment_method"),
                    "legacy_filename":  filename,
                    "legacy_created":   created,
                    "legacy_id":        aid,
                }
                if num in unified_by_num:
                    base["unified_source"] = unified_by_num[num][0].get("source")
                    legacy_overlap_orders.append(base)
                else:
                    legacy_only_orders.append(base)

        # 3) Duplicate legacy files: same filename uploaded N>1 times
        legacy_file_dups: dict[str, list[dict]] = defaultdict(list)
        for a in legacy:
            fn = a.get("filename") or "?"
            legacy_file_dups[fn].append({
                "id": a.get("id"),
                "created_at": (a.get("created_at") or "")[:19],
                "orders":     ((a.get("report") or {}).get("summary") or {}).get("total_orders"),
                "sales":      ((a.get("report") or {}).get("summary") or {}).get("total_sales"),
            })
        legacy_file_dups_list = [
            {"filename": fn, "uploads": entries}
            for fn, entries in legacy_file_dups.items()
            if len(entries) > 1
        ]

        # 4) Unified self-duplicates (should be ~0 due to upsert key)
        unified_self_dups = [
            {"order_number": num, "count": len(rows)}
            for num, rows in unified_by_num.items()
            if len(rows) > 1
        ]

        # Aggregate inflation estimate: the gap between dashboard total and
        # "true" total if we collapsed duplicates.
        overlap_amount = round(sum(x["amount"] for x in legacy_overlap_orders), 2)

        return {
            "range": {"from_date": from_date, "to_date": to_date},
            "legacy_overlap_orders": legacy_overlap_orders,
            "legacy_overlap_summary": {
                "count": len(legacy_overlap_orders),
                "total_amount": overlap_amount,
                "explanation": (
                    "هذه الطلبات موجودة في unified_orders ومسجلة أيضاً ضمن "
                    "تحليلات Excel/Make قديمة بدون orders_imported. كل واحد "
                    "منها يُحسب مرتين في إجماليات لوحة التحكم."
                ),
            },
            "legacy_only_orders_count": len(legacy_only_orders),
            "legacy_file_duplicates":   legacy_file_dups_list,
            "unified_self_dups":        unified_self_dups,
        }

    @router.post("/compare-with-salla")
    async def compare_with_salla(payload: CompareIn, user: dict = Depends(current_user)):
        """Given Salla's official numbers (orders count + total sales) and
        optionally the full list of order numbers from Salla, compute:

        - exact arithmetic gap (orders + sales)
        - in-system-not-in-salla list (orders the system has but Salla doesn't
          → candidates for "phantom" duplicates or mis-imports)
        - in-salla-not-in-system list (orders Salla has but system missed →
          gaps in import/sync coverage)
        """
        uid = user["id"]
        unified = await _unified_in_range(db, uid, payload.from_date, payload.to_date)
        legacy = await _legacy_analyses_in_range(db, uid, payload.from_date, payload.to_date)

        unified_count = len(unified)
        unified_sales = round(sum(float(o.get("total_amount") or 0) for o in unified), 2)
        legacy_count = 0
        legacy_sales = 0.0
        for a in legacy:
            s = (a.get("report") or {}).get("summary") or {}
            legacy_count += int(s.get("total_orders") or 0)
            legacy_sales += float(s.get("total_sales") or 0)
        sys_total_orders = unified_count + legacy_count
        sys_total_sales = round(unified_sales + round(legacy_sales, 2), 2)

        out = {
            "range": {"from_date": payload.from_date, "to_date": payload.to_date},
            "system": {
                "unified_orders": unified_count,
                "unified_sales":  unified_sales,
                "legacy_orders":  legacy_count,
                "legacy_sales":   round(legacy_sales, 2),
                "total_orders":   sys_total_orders,
                "total_sales":    sys_total_sales,
            },
            "salla": {
                "orders_count":   payload.salla_orders_count,
                "total_sales":    payload.salla_total_sales,
            },
        }

        if payload.salla_orders_count is not None:
            out["diff"] = {
                "orders": sys_total_orders - payload.salla_orders_count,
                "sales":  round(sys_total_sales - (payload.salla_total_sales or 0), 2),
            }

        # Set-diff if merchant supplied the order_number list
        if payload.salla_order_numbers:
            salla_set = {str(n).strip() for n in payload.salla_order_numbers if str(n).strip()}
            sys_set = {str(o.get("order_number") or "").strip() for o in unified}
            # Also include orders from legacy samples
            for a in legacy:
                for o in (a.get("report") or {}).get("orders_sample") or []:
                    num = str(o.get("order_number") or "").strip()
                    if num:
                        sys_set.add(num)
            in_system_not_salla = sorted(sys_set - salla_set)
            in_salla_not_system = sorted(salla_set - sys_set)
            out["in_system_not_in_salla"] = {
                "count":   len(in_system_not_salla),
                "samples": in_system_not_salla[:200],
            }
            out["in_salla_not_in_system"] = {
                "count":   len(in_salla_not_system),
                "samples": in_salla_not_system[:200],
            }
        return out

    @router.get("/order-trace/{order_number}")
    async def trace_order(order_number: str, user: dict = Depends(current_user)):
        """Find every place an order_number lives — used to investigate a
        specific suspicious order without modifying it."""
        uid = user["id"]
        results: dict = {"order_number": order_number, "locations": []}

        u = await db.unified_orders.find_one(
            {"user_id": uid, "order_number": order_number}, {"_id": 0}
        )
        if u:
            results["locations"].append({
                "store": "unified_orders",
                "source": u.get("source"),
                "received_at": u.get("received_at"),
                "amount": u.get("total_amount"),
                "order_date": u.get("order_date"),
                "status": u.get("order_status"),
                "raw_sources": list((u.get("raw_by_source") or {}).keys()),
            })

        w = await db.webhook_orders.find_one(
            {"user_id": uid, "order_number": order_number}, {"_id": 0}
        )
        if w:
            results["locations"].append({
                "store": "webhook_orders",
                "received_at": w.get("received_at"),
                "amount": w.get("total_amount"),
            })

        analyses = await db.analyses.find(
            {"user_id": uid, "report.orders_sample.order_number": order_number},
            {"_id": 0, "id": 1, "filename": 1, "created_at": 1, "orders_imported": 1},
        ).to_list(50)
        for a in analyses:
            results["locations"].append({
                "store": "analyses",
                "analysis_id": a.get("id"),
                "filename": a.get("filename"),
                "created_at": (a.get("created_at") or "")[:19],
                "orders_imported": a.get("orders_imported"),
            })

        results["found_in_stores_count"] = len(results["locations"])
        return results

    parent_router.include_router(router)

    # Mobile manager preparation supervision is a separate read-only router.
    # It is wired here only because diagnostics_routes is already attached
    # during server startup; the endpoint itself lives in its own module.
    from preparation_supervision_routes import make_preparation_supervision_router
    parent_router.include_router(
        make_preparation_supervision_router(db, current_user)
    )
