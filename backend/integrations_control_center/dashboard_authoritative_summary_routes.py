"""Authoritative read-only Dashboard source summary for Mezan V2.

Snapchat and Meta spend are read exclusively from owner-selected V2 account
facts. Transitional TikTok and Google amounts remain read-only from their
current daily feed until their native V2 connections are approved. Legacy
Snapchat/Instagram values in ``daily_costs`` are intentionally excluded to
prevent double counting.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from .meta_account_selection import get_meta_account_selection
from .meta_native_reporting import META_REPORTING_COLLECTION
from .meta_oauth_security import META_PROVIDER_ID
from .snapchat_native_selected_reads import selected_snapchat_performance_summary

RIYADH_TZ = ZoneInfo("Asia/Riyadh")
MAX_DASHBOARD_RANGE_DAYS = 90


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today(now: datetime | None = None) -> date:
    value = now or _utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(RIYADH_TZ).date()


def _parse_range(
    from_date: str | None,
    to_date: str | None,
    *,
    today: date,
) -> tuple[date, date]:
    start_raw = from_date or today.isoformat()
    end_raw = to_date or start_raw
    try:
        start = date.fromisoformat(start_raw)
        end = date.fromisoformat(end_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={
            "code": "invalid_dashboard_date",
            "message": "يجب أن يكون تاريخ لوحة التحكم بصيغة YYYY-MM-DD.",
        }) from exc
    if end < start:
        raise HTTPException(status_code=400, detail={
            "code": "invalid_dashboard_date_range",
            "message": "تاريخ النهاية يجب ألا يسبق تاريخ البداية.",
        })
    if end > today:
        end = today
    if (end - start).days + 1 > MAX_DASHBOARD_RANGE_DAYS:
        raise HTTPException(status_code=400, detail={
            "code": "dashboard_date_range_too_wide",
            "message": f"الفترة القصوى لمصادر ميزان 2 هي {MAX_DASHBOARD_RANGE_DAYS} يومًا.",
        })
    return start, end


def _number(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if parsed >= 0 else 0.0


async def _to_list(cursor: Any, length: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=length)
    return [row async for row in cursor]


async def _meta_spend(
    db: Any,
    user_id: str,
    start: date,
    end: date,
) -> tuple[float, int, int]:
    selection = await get_meta_account_selection(db, user_id)
    selected_ids = [
        str(account.get("account_id") or "").strip()
        for account in selection.get("accounts", [])
        if account.get("selected") is True and account.get("account_id")
    ]
    if not selected_ids:
        return 0.0, 0, 0
    cursor = db[META_REPORTING_COLLECTION].find(
        {
            "user_id": user_id,
            "provider": META_PROVIDER_ID,
            "ad_account_id": {"$in": selected_ids},
            "date": {"$gte": start.isoformat(), "$lte": end.isoformat()},
        },
        {"_id": 0, "spend_sar": 1},
    )
    rows = await _to_list(cursor, MAX_DASHBOARD_RANGE_DAYS * max(len(selected_ids), 1) + 20)
    return round(sum(_number(row.get("spend_sar")) for row in rows), 2), len(rows), len(selected_ids)


async def _transitional_spend(
    db: Any,
    user_id: str,
    start: date,
    end: date,
) -> tuple[float, float, int]:
    """Read only TikTok/Google transitional feeds; exclude legacy Snap/Meta."""
    cursor = db.daily_costs.find(
        {
            "user_id": user_id,
            "date": {"$gte": start.isoformat(), "$lte": end.isoformat()},
        },
        {"_id": 0, "tiktok_ads": 1, "google_ads": 1},
    )
    rows = await _to_list(cursor, MAX_DASHBOARD_RANGE_DAYS + 10)
    tiktok = sum(_number(row.get("tiktok_ads")) for row in rows)
    google = sum(_number(row.get("google_ads")) for row in rows)
    return round(tiktok, 2), round(google, 2), len(rows)


async def build_dashboard_authoritative_summary(
    db: Any,
    user_id: str,
    *,
    from_date: str | None,
    to_date: str | None,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    today = _today(now())
    start, end = _parse_range(from_date, to_date, today=today)

    try:
        snapchat = await selected_snapchat_performance_summary(
            db,
            user_id,
            from_date=start.isoformat(),
            to_date=end.isoformat(),
            now=now,
        )
        snapchat_spend = round(_number(snapchat.get("spend_sar")), 2)
        snapchat_rows = int(snapchat.get("rows_included") or 0)
        snapchat_accounts = int(snapchat.get("selected_account_count") or 0)
        snapchat_status = "available"
    except Exception:  # noqa: BLE001 - one unavailable provider must not hide others
        snapchat_spend = 0.0
        snapchat_rows = 0
        snapchat_accounts = 0
        snapchat_status = "unavailable"

    try:
        meta_spend, meta_rows, meta_accounts = await _meta_spend(
            db, user_id, start, end
        )
        meta_status = "available" if meta_accounts > 0 else "unavailable"
    except Exception:  # noqa: BLE001
        meta_spend = 0.0
        meta_rows = 0
        meta_accounts = 0
        meta_status = "unavailable"

    tiktok_spend, google_spend, transitional_rows = await _transitional_spend(
        db, user_id, start, end
    )
    total = round(
        snapchat_spend + meta_spend + tiktok_spend + google_spend,
        2,
    )
    return {
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "total_ads_cost": total,
        "breakdown": {
            "snapchat_v2": snapchat_spend,
            "meta_v2": meta_spend,
            "tiktok_transitional": tiktok_spend,
            "google_transitional": google_spend,
        },
        "providers": {
            "snapchat": {
                "status": snapchat_status,
                "selected_accounts": snapchat_accounts,
                "rows": snapchat_rows,
            },
            "meta": {
                "status": meta_status,
                "selected_accounts": meta_accounts,
                "rows": meta_rows,
            },
            "transitional": {"rows": transitional_rows},
        },
        "excluded_legacy_fields": [
            "daily_costs.snapchat_ads",
            "daily_costs.snapchat_ads_2",
            "daily_costs.instagram_ads",
        ],
        "source_contract": {
            "sales_orders": "unified_orders",
            "product_costs": "unified_orders+product_costs",
            "snapchat": "mezan_snapchat_performance_daily_v2:selected_accounts",
            "meta": "mezan_meta_performance_daily_v2:selected_accounts",
            "tiktok": "daily_costs:tiktok_ads:transitional",
            "google": "daily_costs:google_ads:transitional",
        },
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def attach_dashboard_authoritative_summary_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get("/dashboard/authoritative-summary")
    async def dashboard_authoritative_summary(
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await build_dashboard_authoritative_summary(
            db,
            str(owner["id"]),
            from_date=from_date,
            to_date=to_date,
        )


__all__ = [
    "attach_dashboard_authoritative_summary_routes",
    "build_dashboard_authoritative_summary",
]
