"""Read-only Snapchat V2 summaries for the legacy Dashboard UI.

The Dashboard keeps its existing visual contract, but all Snapchat spend and
attribution values come from owner-selected accounts in
``mezan_snapchat_performance_daily_v2``. No legacy Snapchat credential,
``daily_costs`` value, accounting entry, campaign mutation, or Qoyod write is
used by this module.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends

from .snapchat_account_selection import _load_selected_accounts
from .snapchat_native_data_common import (
    BUSINESS_TIMEZONE,
    SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
    SNAPCHAT_PERFORMANCE_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
)
from .snapchat_native_performance_sync import (
    ACTION_REPORT_TIME,
    CONVERSION_SOURCE_TYPES,
    SWIPE_ATTRIBUTION_WINDOW,
    VIEW_ATTRIBUTION_WINDOW,
)

RIYADH_TZ = ZoneInfo(BUSINESS_TIMEZONE)
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
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if parsed >= 0 else 0.0


def _row_metric(row: dict[str, Any], top_level: str, nested: str) -> Any:
    if row.get(top_level) is not None:
        return row.get(top_level)
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    return metrics.get(nested)


def _round_purchase_count(value: float) -> int:
    """Match provider/UI half-up rounding for modeled fractional conversions."""
    return int(
        Decimal(str(max(0.0, float(value or 0)))).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def _metric(spend: float, orders: float, revenue: float) -> dict[str, Any]:
    spend_value = round(spend, 2)
    order_raw = round(max(0.0, float(orders or 0)), 4)
    order_value = _round_purchase_count(order_raw)
    revenue_value = round(revenue, 2)
    return {
        "spend": spend_value,
        "orders": order_value,
        "orders_raw": order_raw,
        "revenue": revenue_value,
        "roas": round(revenue_value / spend_value, 2) if spend_value > 0 else 0.0,
        "cost_per_order": round(spend_value / order_raw, 2) if order_raw > 0 else None,
    }


def _period(daily: dict[str, dict[str, float]], start: date, end: date) -> dict[str, Any]:
    spend = orders = revenue = 0.0
    cursor = start
    while cursor <= end:
        row = daily.get(cursor.isoformat(), {})
        spend += _number(row.get("spend"))
        orders += _number(row.get("orders"))
        revenue += _number(row.get("revenue"))
        cursor += timedelta(days=1)
    return _metric(spend, orders, revenue)


def _history(daily: dict[str, dict[str, float]], start: date, end: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        bucket = daily.get(cursor.isoformat(), {})
        rows.append({
            "date": cursor.isoformat(),
            "spend": round(_number(bucket.get("spend")), 2),
            "orders": _round_purchase_count(_number(bucket.get("orders"))),
            "revenue": round(_number(bucket.get("revenue")), 2),
        })
        cursor += timedelta(days=1)
    return rows


def _connection_status(snapshot: dict[str, Any], selected_count: int) -> str:
    raw = str(snapshot.get("connection_status") or "not_connected").strip().lower()
    if raw in {"needs_reauth", "expired"}:
        return "expired"
    if raw == "connected" and selected_count == 0:
        return "needs_selection"
    if raw == "connected":
        return "ok"
    return raw or "not_connected"


def summarize_snapchat_dashboard_rows(
    rows: list[dict[str, Any]],
    *,
    selected_accounts: list[dict[str, Any]],
    snapshot: dict[str, Any] | None = None,
    latest_error: dict[str, Any] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    snapshot = snapshot or {}
    latest_error = latest_error or {}
    today_value = today or _riyadh_today()
    month_start = today_value.replace(day=1)
    history_start = today_value - timedelta(days=29)
    query_start = min(month_start, history_start)

    daily: dict[str, dict[str, float]] = {}
    per_account_daily: dict[str, dict[str, dict[str, float]]] = {}
    per_account_today_native: dict[str, dict[str, float]] = {}
    observed_values: list[str] = []

    for row in rows:
        row_date = str(row.get("date") or "").strip()
        try:
            parsed_date = date.fromisoformat(row_date)
        except ValueError:
            continue
        if parsed_date < query_start or parsed_date > today_value:
            continue

        account_id = str(row.get("ad_account_id") or "").strip()
        spend_sar = _number(row.get("spend_sar"))
        purchases = _number(
            _row_metric(row, "purchases", "conversion_purchases")
        )
        revenue_sar = _number(row.get("purchase_value_sar"))

        bucket = daily.setdefault(
            row_date,
            {"spend": 0.0, "orders": 0.0, "revenue": 0.0},
        )
        bucket["spend"] += spend_sar
        bucket["orders"] += purchases
        bucket["revenue"] += revenue_sar

        if account_id:
            account_daily = per_account_daily.setdefault(account_id, {})
            account_bucket = account_daily.setdefault(
                row_date,
                {"spend": 0.0, "orders": 0.0, "revenue": 0.0},
            )
            account_bucket["spend"] += spend_sar
            account_bucket["orders"] += purchases
            account_bucket["revenue"] += revenue_sar

            if parsed_date == today_value:
                native_bucket = per_account_today_native.setdefault(
                    account_id,
                    {"spend_sar": 0.0, "spend_native": 0.0},
                )
                native_bucket["spend_sar"] += spend_sar
                native_bucket["spend_native"] += _number(row.get("spend_native"))

        observed_at = str(
            row.get("observed_at") or row.get("updated_at") or ""
        ).strip()
        if observed_at:
            observed_values.append(observed_at)

    status = _connection_status(snapshot, len(selected_accounts))
    error_message = None
    if status == "expired":
        error_message = str(
            latest_error.get("message")
            or "انتهت صلاحية ربط Snapchat V2، أعد التفويض من مركز التطبيقات."
        )[:300]
    elif status == "needs_selection":
        error_message = "اختر حسابات Snapchat الخاصة بأماسي من مركز التطبيقات."
    elif status not in {"ok", "connected"}:
        error_message = str(
            latest_error.get("message") or "ربط Snapchat V2 غير جاهز."
        )[:300]

    today_metrics = _period(daily, today_value, today_value)
    month_metrics = _period(daily, month_start, today_value)
    last_30d_metrics = _period(daily, history_start, today_value)

    # Each selected account gets its own isolated periods. No spend-share
    # allocation or aggregate prorating is used anywhere in these cards.
    accounts = []
    for account in selected_accounts:
        account_id = str(account.get("ad_account_id") or "").strip()
        account_daily = per_account_daily.get(account_id, {})
        native_today = per_account_today_native.get(account_id, {})
        account_today = _period(account_daily, today_value, today_value)
        account_today.update({
            "spend_sar": round(_number(native_today.get("spend_sar")), 2),
            "spend_native": round(_number(native_today.get("spend_native")), 6),
        })
        accounts.append({
            "id": account_id,
            "ad_account_id": account_id,
            "external_account_id": account_id,
            "name": account.get("display_name") or account.get("name") or account_id,
            "currency_native": account.get("currency") or "SAR",
            "timezone": account.get("timezone"),
            "report_timezone": BUSINESS_TIMEZONE,
            "day_start": "00:00",
            "day_end": "23:59",
            "today": account_today,
            "month": {
                "start": month_start.isoformat(),
                **_period(account_daily, month_start, today_value),
            },
            "last_30d": {
                "start": history_start.isoformat(),
                "end": today_value.isoformat(),
                **_period(account_daily, history_start, today_value),
            },
            "history": _history(account_daily, history_start, today_value),
            "source": "snapchat_marketing_api_account",
            "isolated_account": True,
        })

    last_sync_at = max(observed_values) if observed_values else (
        snapshot.get("last_sync_at")
        or snapshot.get("checked_at")
        or snapshot.get("updated_at")
    )
    conversion_reporting = {
        "metric": "conversion_purchases",
        "source_types": [CONVERSION_SOURCE_TYPES],
        "action_report_time": ACTION_REPORT_TIME,
        "swipe_up_attribution_window": SWIPE_ATTRIBUTION_WINDOW,
        "view_attribution_window": VIEW_ATTRIBUTION_WINDOW,
        "today_is_provisional": True,
    }
    return {
        "provider": "snapchat",
        "integration_provider": SNAPCHAT_PROVIDER_ID,
        "connection_status": status,
        "last_error_message": error_message,
        "last_fetched_at": last_sync_at,
        "selected_account_count": len(selected_accounts),
        "business_timezone": BUSINESS_TIMEZONE,
        "day_start": "00:00",
        "day_end": "23:59",
        "source": "snapchat_v2",
        "today": {"date": today_value.isoformat(), **today_metrics},
        "month": {"start": month_start.isoformat(), **month_metrics},
        "last_30d": {
            "start": history_start.isoformat(),
            "end": today_value.isoformat(),
            **last_30d_metrics,
        },
        "history": _history(daily, history_start, today_value),
        "accounts": accounts,
        "conversion_reporting": conversion_reporting,
        "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
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


async def build_snapchat_dashboard_summary(
    db: Any,
    user_id: str,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    today_value = _riyadh_today(now())
    month_start = today_value.replace(day=1)
    history_start = today_value - timedelta(days=29)
    query_start = min(month_start, history_start)

    try:
        selected_accounts = await _load_selected_accounts(db, user_id)
    except Exception:  # noqa: BLE001 - summary remains fail-closed
        selected_accounts = []
    selected_ids = [
        str(account.get("ad_account_id") or "").strip()
        for account in selected_accounts
        if account.get("ad_account_id")
    ]
    snapshot = await db.mezan_integrations_v2.find_one(
        {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID},
        {"_id": 0},
    ) or {}
    latest_error = await db.mezan_integration_errors_v2.find_one(
        {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID},
        {"_id": 0, "message": 1, "code": 1, "occurred_at": 1},
        sort=[("occurred_at", -1)],
    ) or {}

    rows: list[dict[str, Any]] = []
    if selected_ids:
        cursor = db[SNAPCHAT_PERFORMANCE_COLLECTION].find(
            {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "ad_account_id": {"$in": selected_ids},
                "entity_type": "ad_account",
                "date": {
                    "$gte": query_start.isoformat(),
                    "$lte": today_value.isoformat(),
                },
            },
            {
                "_id": 0,
                "ad_account_id": 1,
                "date": 1,
                "spend_native": 1,
                "spend_sar": 1,
                "purchases": 1,
                "metrics": 1,
                "purchase_value_sar": 1,
                "conversion_reporting": 1,
                "observed_at": 1,
                "updated_at": 1,
            },
        )
        rows = await _to_list(cursor, MAX_DASHBOARD_ROWS)

    return summarize_snapchat_dashboard_rows(
        rows,
        selected_accounts=selected_accounts,
        snapshot=snapshot,
        latest_error=latest_error,
        today=today_value,
    )


def attach_snapchat_dashboard_summary_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get(f"/{SNAPCHAT_PROVIDER_ID}/dashboard-summary")
    async def snapchat_dashboard_summary(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await build_snapchat_dashboard_summary(db, str(owner["id"]))

    @router.get(f"/{SNAPCHAT_PROVIDER_ID}/accounts-dashboard-summary")
    async def snapchat_accounts_dashboard_summary(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        summary = await build_snapchat_dashboard_summary(db, str(owner["id"]))
        return {
            "count": len(summary["accounts"]),
            "accounts": summary["accounts"],
            "today_date": summary["today"]["date"],
            "month_start": summary["month"]["start"],
            "last_30d_start": summary["last_30d"]["start"],
            "last_30d_end": summary["last_30d"]["end"],
            "last_fetched_at": summary.get("last_fetched_at"),
            "conversion_reporting": summary.get("conversion_reporting") or {},
            "business_timezone": BUSINESS_TIMEZONE,
            "day_start": "00:00",
            "day_end": "23:59",
            "source": "snapchat_v2_isolated_accounts",
            "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
            "source_only": True,
            "provider_write_reached": False,
            "campaign_write_reached": False,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }


__all__ = [
    "attach_snapchat_dashboard_summary_routes",
    "build_snapchat_dashboard_summary",
    "summarize_snapchat_dashboard_rows",
]
