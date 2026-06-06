"""Refund-monitor analytics for Reports page (Iter-77).

Aggregates orders that have either a partial or full refund (from
actual_* fields populated by settlement-file imports OR from existing
estimated refund_amount fields) within a date window. Surfaces the
list as a smart alert in the Reports page so the merchant can spot
high-refund periods quickly.

Endpoint
--------
GET /api/reports/refunds-alert?period={today|yesterday|this_month|
                                     last_month|last_30d|this_year|custom}
                              &from_date=YYYY-MM-DD  (when period=custom)
                              &to_date=YYYY-MM-DD    (when period=custom)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth import get_current_user_from_db


def _today() -> date:
    return datetime.now(timezone.utc).astimezone().date()


def _first_of_month(d: date) -> date:
    return d.replace(day=1)


def _last_day_of_prev_month(d: date) -> date:
    return _first_of_month(d) - timedelta(days=1)


def _resolve_period(
    period: str,
    custom_from: Optional[str] = None,
    custom_to: Optional[str] = None,
) -> tuple[str, str, str]:
    """Return (from_iso, to_iso, label)."""
    today = _today()
    if period == "today":
        return today.isoformat(), today.isoformat(), "اليوم"
    if period == "yesterday":
        y = today - timedelta(days=1)
        return y.isoformat(), y.isoformat(), "بالأمس"
    if period == "this_month":
        start = _first_of_month(today)
        return start.isoformat(), today.isoformat(), "هذا الشهر"
    if period == "last_month":
        last_end = _last_day_of_prev_month(today)
        last_start = _first_of_month(last_end)
        return last_start.isoformat(), last_end.isoformat(), "الشهر الماضي"
    if period == "this_year":
        return date(today.year, 1, 1).isoformat(), today.isoformat(), "السنة الحالية"
    if period == "last_30d":
        return (today - timedelta(days=29)).isoformat(), today.isoformat(), "آخر 30 يوم"
    if period == "custom":
        if not custom_from or not custom_to:
            raise HTTPException(
                status_code=400,
                detail="period=custom يتطلب from_date و to_date.",
            )
        try:
            datetime.strptime(custom_from, "%Y-%m-%d")
            datetime.strptime(custom_to, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="تنسيق التاريخ يجب أن يكون YYYY-MM-DD.")
        return custom_from, custom_to, f"{custom_from} → {custom_to}"
    raise HTTPException(
        status_code=400,
        detail=("period غير صالحة. الخيارات المدعومة: today, yesterday, this_month, "
                "last_month, last_30d, this_year, custom."),
    )


def attach_refunds_alert_routes(api_router: APIRouter, db) -> None:
    router = APIRouter(prefix="/reports", tags=["reports"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    @router.get("/refunds-alert")
    async def refunds_alert(
        period: str = Query(default="last_30d"),
        from_date: Optional[str] = Query(default=None),
        to_date: Optional[str] = Query(default=None),
        limit: int = Query(default=200, ge=1, le=1000),
        user: dict = Depends(current_user),
    ):
        from_iso, to_iso, label = _resolve_period(period, from_date, to_date)

        # Build the match criteria — an order qualifies for the refund
        # alert when ANY of these hold:
        #   • actual_refund_amount > 0  (full refund from settlement file)
        #   • actual_partial_refund_amount > 0  (partial)
        #   • order_status indicates refund (Iter-82) — surfaces Salla
        #     "مسترجع" orders even before a settlement file is uploaded.
        match: dict = {
            "user_id": user["id"],
            "$or": [
                {"actual_refund_amount": {"$gt": 0}},
                {"actual_partial_refund_amount": {"$gt": 0}},
                {"order_status": {"$regex": "مسترج", "$options": ""}},
                {"order_status": {"$regex": "refund", "$options": "i"}},
            ],
        }

        # Date filter — settlement_date OR order_date in window
        date_filter = {
            "$or": [
                {"settlement_date": {"$gte": from_iso, "$lte": to_iso}},
                {"$and": [
                    {"$or": [{"settlement_date": {"$exists": False}}, {"settlement_date": None}, {"settlement_date": ""}]},
                    {"order_date": {"$gte": from_iso, "$lte": to_iso}},
                ]},
            ],
        }
        match = {"$and": [match, date_filter]}

        # Aggregate summary + sample orders in one round-trip.
        # Iter-82: when actual_refund_amount is missing (no settlement
        # file yet) but the order has a "مسترجع" status, we treat the
        # full total_amount as the refund (status-driven refund), so the
        # Refund Monitor reflects reality even before the merchant
        # uploads Tamara/Tabby settlement files.
        is_status_refund_expr = {
            "$or": [
                {"$regexMatch": {"input": {"$ifNull": ["$order_status", ""]}, "regex": "مسترج"}},
                {"$regexMatch": {"input": {"$ifNull": ["$order_status", ""]}, "regex": "refund", "options": "i"}},
            ]
        }
        effective_refund_full_expr = {
            "$cond": [
                {"$gt": [{"$ifNull": ["$actual_refund_amount", 0]}, 0]},
                {"$ifNull": ["$actual_refund_amount", 0]},
                {"$cond": [
                    is_status_refund_expr,
                    {"$ifNull": ["$total_amount", 0]},
                    0,
                ]},
            ]
        }

        pipeline = [
            {"$match": match},
            {"$addFields": {
                "_effective_refund_full": effective_refund_full_expr,
                "_is_status_refund": is_status_refund_expr,
            }},
            {"$facet": {
                "summary": [
                    {"$group": {
                        "_id": None,
                        "orders_count": {"$sum": 1},
                        "total_refund_full": {"$sum": "$_effective_refund_full"},
                        "total_refund_partial": {"$sum": {"$ifNull": ["$actual_partial_refund_amount", 0]}},
                        "total_gross_affected": {"$sum": {"$ifNull": ["$total_amount", 0]}},
                    }},
                ],
                "orders": [
                    {"$sort": {"settlement_date": -1, "order_date": -1}},
                    {"$limit": limit},
                    {"$project": {
                        "_id": 0,
                        "order_number": 1,
                        "order_date": 1,
                        "settlement_date": 1,
                        "settlement_source": 1,
                        "customer_name": 1,
                        "total_amount": 1,
                        "actual_gross_amount": 1,
                        "actual_net_amount": 1,
                        "actual_refund_amount": 1,
                        "actual_partial_refund_amount": 1,
                        "actual_payment_method": 1,
                        "payment_method": 1,
                        "order_status": 1,
                        "_effective_refund_full": 1,
                        "_is_status_refund": 1,
                    }},
                ],
                "by_payment_method": [
                    {"$group": {
                        "_id": {"$ifNull": ["$actual_payment_method", {"$ifNull": ["$payment_method", "—"]}]},
                        "orders": {"$sum": 1},
                        "amount": {"$sum": {"$add": [
                            "$_effective_refund_full",
                            {"$ifNull": ["$actual_partial_refund_amount", 0]},
                        ]}},
                    }},
                    {"$sort": {"amount": -1}},
                ],
            }},
        ]
        cursor = db.unified_orders.aggregate(pipeline)
        facets = [r async for r in cursor]
        f = facets[0] if facets else {}

        summary_row = (f.get("summary") or [{}])[0] if f.get("summary") else {}
        total_full = round(summary_row.get("total_refund_full", 0) or 0, 2)
        total_partial = round(summary_row.get("total_refund_partial", 0) or 0, 2)

        # Compute refund rate (% of qualified orders against ALL orders
        # in the same period — gives a sense of magnitude).
        total_orders_in_period = await db.unified_orders.count_documents({
            "user_id": user["id"],
            "$or": [
                {"order_date": {"$gte": from_iso, "$lte": to_iso}},
                {"settlement_date": {"$gte": from_iso, "$lte": to_iso}},
            ],
        })

        return {
            "period": period,
            "label": label,
            "from_date": from_iso,
            "to_date": to_iso,
            "summary": {
                "refund_orders_count": summary_row.get("orders_count", 0),
                "total_orders_in_period": total_orders_in_period,
                "refund_rate_pct": round(
                    (summary_row.get("orders_count", 0) /
                     max(1, total_orders_in_period)) * 100, 2,
                ),
                "total_refund_full": total_full,
                "total_refund_partial": total_partial,
                "total_refund_amount": round(total_full + total_partial, 2),
                "total_gross_affected": round(
                    summary_row.get("total_gross_affected", 0) or 0, 2,
                ),
            },
            "orders": f.get("orders", []),
            "by_payment_method": [
                {
                    "payment_method": row["_id"] or "—",
                    "orders": row["orders"],
                    "amount": round(row["amount"] or 0, 2),
                }
                for row in f.get("by_payment_method", [])
            ],
        }

    api_router.include_router(router)
