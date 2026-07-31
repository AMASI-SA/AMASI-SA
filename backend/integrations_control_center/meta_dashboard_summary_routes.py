"""Read-only Meta V2 dashboard summary compatibility route.

The legacy Dashboard card expects a compact today/month/30-day payload. This
adapter builds that payload exclusively from owner-selected Meta accounts and
``mezan_meta_performance_daily_v2``. It never reads the legacy Meta token or
``meta_ads_daily`` and never writes accounting, campaigns, Qoyod, or provider
state.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends

from .meta_account_selection import get_meta_account_selection
from .meta_native_reporting import META_REPORTING_COLLECTION, META_REPORTING_SOURCE_MODE
from .meta_oauth_security import META_PROVIDER_ID

RIYADH_TZ = ZoneInfo("Asia/Riyadh")
MAX_DASHBOARD_ROWS = 31 * 20 + 20


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _riyadh_today(now: datetime | None = None) -> date:
    value = now or _utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(RIYADH_TZ).date()


def _number(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed >= 0 else 0.0


def _metric(spend: float, orders: float, revenue: float) -> dict[str, Any]:
    spend_value = round(spend, 2)
    order_value = int(round(orders))
    revenue_value = round(revenue, 2)
    return {
        "spend": spend_value,
        "orders": order_value,
        "revenue": revenue_value,
        "roas": round(revenue_value / spend_value, 2) if spend_value > 0 else 0.0,
        "cost_per_order": round(spend_value / order_value, 2) if order_value > 0 else None,
    }


def _period(
    daily: dict[str, dict[str, float]],
    start: date,
    end: date,
) -> dict[str, Any]:
    spend = orders = revenue = 0.0
    cursor = start
    while cursor <= end:
        row = daily.get(cursor.isoformat(), {})
        spend += _number(row.get("spend"))
        orders += _number(row.get("orders"))
        revenue += _number(row.get("revenue"))
        cursor += timedelta(days=1)
    return _metric(spend, orders, revenue)


def _connection_status(snapshot: dict[str, Any], selected_count: int) -> str:
    raw = str(snapshot.get("connection_status") or "not_connected").strip().lower()
    if raw in {"needs_reauth", "expired"}:
        return "expired"
    if raw == "connected" and selected_count == 0:
        return "needs_selection"
    if raw == "connected":
        return "ok"
    return raw or "not_connected"


def summarize_meta_dashboard_rows(
    rows: list[dict[str, Any]],
    *,
    selected_count: int,
    snapshot: dict[str, Any] | None = None,
    latest_error: dict[str, Any] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Build the legacy Dashboard shape from sanitized Meta V2 rows."""
    snapshot = snapshot or {}
    latest_error = latest_error or {}
    today_value = today or _riyadh_today()
    month_start = today_value.replace(day=1)
    history_start = today_value - timedelta(days=29)
    query_start = min(month_start, history_start)

    daily: dict[str, dict[str, float]] = {}
    observed_values: list[str] = []
    for row in rows:
        row_date = str(row.get("date") or "").strip()
        try:
            parsed_date = date.fromisoformat(row_date)
        except ValueError:
            continue
        if parsed_date < query_start or parsed_date > today_value:
            continue
        bucket = daily.setdefault(row_date, {"spend": 0.0, "orders": 0.0, "revenue": 0.0})
        bucket["spend"] += _number(row.get("spend_sar"))
        bucket["orders"] += _number(row.get("purchases"))
        bucket["revenue"] += _number(row.get("purchase_value_sar"))
        observed_at = str(row.get("observed_at") or row.get("updated_at") or "").strip()
        if observed_at:
            observed_values.append(observed_at)

    status = _connection_status(snapshot, selected_count)
    error_message = None
    if status == "expired":
        error_message = str(
            latest_error.get("message")
            or "انتهت صلاحية ربط Meta V2، أعد التفويض من مركز التطبيقات."
        )[:300]
    elif status == "needs_selection":
        error_message = "اختر حساب Meta الخاص بأماسي من مركز التطبيقات قبل المزامنة."
    elif status not in {"ok", "connected"}:
        error_message = str(latest_error.get("message") or "ربط Meta V2 غير جاهز.")[:300]

    today_metrics = _period(daily, today_value, today_value)
    month_metrics = _period(daily, month_start, today_value)
    last_30d_metrics = _period(daily, history_start, today_value)
    history = []
    cursor = history_start
    while cursor <= today_value:
        bucket = daily.get(cursor.isoformat(), {})
        history.append({
            "date": cursor.isoformat(),
            "spend": round(_number(bucket.get("spend")), 2),
        })
        cursor += timedelta(days=1)

    last_sync_at = max(observed_values) if observed_values else (
        snapshot.get("last_sync_at")
        or snapshot.get("checked_at")
        or snapshot.get("updated_at")
    )
    return {
        "provider": "meta",
        "integration_provider": META_PROVIDER_ID,
        "connection_status": status,
        "last_error_message": error_message,
        "last_sync_at": last_sync_at,
        "selected_account_count": int(selected_count),
        "today": {"date": today_value.isoformat(), **today_metrics},
        "month": {"start": month_start.isoformat(), **month_metrics},
        "last_30d": {"start": history_start.isoformat(), "end": today_value.isoformat(), **last_30d_metrics},
        "history": history,
        "source_mode": META_REPORTING_SOURCE_MODE,
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


async def _to_list(cursor: Any, length: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=length)
    return [row async for row in cursor]


async def build_meta_dashboard_summary(
    db: Any,
    user_id: str,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    today_value = _riyadh_today(now())
    month_start = today_value.replace(day=1)
    history_start = today_value - timedelta(days=29)
    query_start = min(month_start, history_start)

    selection = await get_meta_account_selection(db, user_id)
    selected_ids = [
        str(account.get("account_id") or "").strip()
        for account in selection.get("accounts", [])
        if account.get("selected") is True and account.get("account_id")
    ]
    snapshot = await db.mezan_integrations_v2.find_one(
        {"user_id": user_id, "provider": META_PROVIDER_ID},
        {"_id": 0},
    ) or {}
    latest_error = await db.mezan_integration_errors_v2.find_one(
        {"user_id": user_id, "provider": META_PROVIDER_ID},
        {"_id": 0, "message": 1, "code": 1, "occurred_at": 1},
        sort=[("occurred_at", -1)],
    ) or {}

    rows: list[dict[str, Any]] = []
    if selected_ids:
        cursor = db[META_REPORTING_COLLECTION].find(
            {
                "user_id": user_id,
                "provider": META_PROVIDER_ID,
                "ad_account_id": {"$in": selected_ids},
                "date": {
                    "$gte": query_start.isoformat(),
                    "$lte": today_value.isoformat(),
                },
            },
            {
                "_id": 0,
                "date": 1,
                "spend_sar": 1,
                "purchases": 1,
                "purchase_value_sar": 1,
                "observed_at": 1,
                "updated_at": 1,
            },
        )
        rows = await _to_list(cursor, MAX_DASHBOARD_ROWS)

    return summarize_meta_dashboard_rows(
        rows,
        selected_count=len(selected_ids),
        snapshot=snapshot,
        latest_error=latest_error,
        today=today_value,
    )


def attach_meta_dashboard_summary_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get(f"/{META_PROVIDER_ID}/dashboard-summary")
    async def meta_dashboard_summary(user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        return await build_meta_dashboard_summary(db, str(owner["id"]))


__all__ = [
    "attach_meta_dashboard_summary_routes",
    "build_meta_dashboard_summary",
    "summarize_meta_dashboard_rows",
]
