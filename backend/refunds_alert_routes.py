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
from order_currency import SALLA_RAW_CURRENCY_PROJECTION, order_total_sar


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

        # Aggregate from verified SAR amounts.
        # Iter-82: when actual_refund_amount is missing (no settlement
        # file yet) but the order has a "مسترجع" status, we treat the
        # verified SAR total as the refund (status-driven refund), so the
        # Refund Monitor reflects reality even before the merchant
        # uploads Tamara/Tabby settlement files.
        projection = {
            **SALLA_RAW_CURRENCY_PROJECTION,
            "order_number": 1,
            "order_date": 1,
            "settlement_date": 1,
            "settlement_source": 1,
            "customer_name": 1,
            "total_amount": 1,
            "currency": 1,
            "original_total_amount": 1,
            "original_currency": 1,
            "exchange_rate_to_sar": 1,
            "total_amount_sar": 1,
            "accounting_currency": 1,
            "currency_conversion_status": 1,
            "actual_gross_amount": 1,
            "actual_net_amount": 1,
            "actual_refund_amount": 1,
            "actual_partial_refund_amount": 1,
            "actual_payment_method": 1,
            "payment_method": 1,
            "order_status": 1,
        }
        pipeline = [
            {"$match": match},
            {"$sort": {"settlement_date": -1, "order_date": -1}},
            {"$project": projection},
        ]
        orders_count = 0
        total_full = 0.0
        total_partial = 0.0
        total_gross = 0.0
        conversion_complete = True
        unverified_order_numbers: list[str] = []
        order_rows: list[dict] = []
        method_rows: dict[str, dict] = {}
        async for order in db.unified_orders.aggregate(pipeline):
            orders_count += 1
            total_sar = order_total_sar(order)
            if total_sar is None:
                conversion_complete = False
                if len(unverified_order_numbers) < 100:
                    unverified_order_numbers.append(
                        str(order.get("order_number") or "unknown")
                    )
            else:
                total_gross += total_sar

            status_text = str(order.get("order_status") or "")
            is_status_refund = (
                "مسترج" in status_text
                or "refund" in status_text.casefold()
            )
            actual_full = float(order.get("actual_refund_amount") or 0)
            effective_full = (
                actual_full
                if actual_full > 0
                else (total_sar if is_status_refund else 0.0)
            )
            partial = float(order.get("actual_partial_refund_amount") or 0)
            if effective_full is not None:
                total_full += effective_full
            total_partial += partial

            method = str(
                order.get("actual_payment_method")
                or order.get("payment_method")
                or "—"
            )
            slot = method_rows.setdefault(method, {
                "payment_method": method,
                "orders": 0,
                "known_amount_sar": 0.0,
            })
            slot["orders"] += 1
            if effective_full is not None:
                slot["known_amount_sar"] += effective_full + partial

            if len(order_rows) < limit:
                safe_order = dict(order)
                safe_order.pop("raw_by_source", None)
                order_rows.append({
                    **safe_order,
                    "original_total_amount": (
                        order.get("original_total_amount")
                        if order.get("original_total_amount") is not None
                        else order.get("total_amount")
                    ),
                    "original_currency": (
                        order.get("original_currency")
                        or order.get("currency")
                        or "SAR"
                    ),
                    "total_amount": total_sar,
                    "accounting_currency": "SAR",
                    "_effective_refund_full": effective_full,
                    "_is_status_refund": is_status_refund,
                    "currency_conversion_complete": total_sar is not None,
                })

        total_full = round(total_full, 2)
        total_partial = round(total_partial, 2)
        total_gross = round(total_gross, 2)

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
                "refund_orders_count": orders_count,
                "total_orders_in_period": total_orders_in_period,
                "refund_rate_pct": round(
                    (orders_count /
                     max(1, total_orders_in_period)) * 100, 2,
                ),
                "total_refund_full": total_full if conversion_complete else None,
                "total_refund_partial": total_partial if conversion_complete else None,
                "total_refund_amount": (
                    round(total_full + total_partial, 2)
                    if conversion_complete else None
                ),
                "total_gross_affected": (
                    total_gross if conversion_complete else None
                ),
                "known_total_refund_full_sar": total_full,
                "known_total_refund_partial_sar": total_partial,
                "known_total_gross_affected_sar": total_gross,
                "accounting_currency": "SAR",
                "currency_conversion_complete": conversion_complete,
                "unverified_order_numbers": unverified_order_numbers,
            },
            "orders": order_rows,
            "by_payment_method": sorted([
                {
                    **row,
                    "amount": (
                        round(row["known_amount_sar"], 2)
                        if conversion_complete else None
                    ),
                }
                for row in method_rows.values()
            ], key=lambda row: -row["known_amount_sar"]),
        }

    api_router.include_router(router)
