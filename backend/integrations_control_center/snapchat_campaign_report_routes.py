"""Owner-only local Snapchat campaign report for the Mezan marketing workspace.

The report reads only Mezan V2 native Snapchat snapshots. It never calls the
provider and never writes to Snapchat, accounting, campaigns, events, or Qoyod.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from .snapchat_account_selection import _load_selected_accounts
from .snapchat_native_data_common import (
    ATTRIBUTION_MODEL,
    BUSINESS_TIMEZONE,
    MAX_SYNC_DAYS,
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_PERFORMANCE_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatNativeSyncInput,
    _collection,
    _parse_datetime,
    _timezone,
    enumerate_native_sync_dates,
)

DEFAULT_REPORT_DAYS = 30
MAX_REPORT_ROWS = 100_000
MAX_REPORT_ENTITY_ROWS = 50_000


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _integer(value: Any) -> int | None:
    parsed = _number(value)
    return int(parsed) if parsed is not None else None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _metric(row: dict[str, Any], key: str) -> float | None:
    metrics = row.get("metrics")
    if isinstance(metrics, dict):
        return _number(metrics.get(key))
    return None


def _value(row: dict[str, Any], key: str) -> float | None:
    if key == "orders":
        return _number(row.get("purchases")) or _metric(row, "conversion_purchases")
    if key == "sales_sar":
        return _number(row.get("purchase_value_sar"))
    if key == "spend_sar":
        return _number(row.get("spend_sar"))
    if key == "impressions":
        return _metric(row, "impressions")
    if key == "swipes":
        return _metric(row, "swipes")
    if key == "video_views":
        return _metric(row, "video_views")
    return None


def _sum_or_none(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [value for row in rows if (value := _value(row, key)) is not None]
    return round(sum(values), 6) if values else None


def _latest_updated_at(rows: list[dict[str, Any]]) -> str | None:
    candidates: list[tuple[datetime, str]] = []
    for row in rows:
        raw = _text(row.get("updated_at"))
        parsed = _parse_datetime(raw)
        if raw and parsed is not None:
            candidates.append((parsed, raw))
    return max(candidates, default=(None, None), key=lambda item: item[0])[1]


def _ratio(numerator: float | None, denominator: float | None, multiplier: float = 1.0) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return round((numerator / denominator) * multiplier, 6)


def aggregate_report_rows(
    rows: list[dict[str, Any]],
    *,
    requested_days: int,
) -> dict[str, Any]:
    spend = _sum_or_none(rows, "spend_sar")
    sales = _sum_or_none(rows, "sales_sar")
    orders = _sum_or_none(rows, "orders")
    impressions = _sum_or_none(rows, "impressions")
    swipes = _sum_or_none(rows, "swipes")
    video_views = _sum_or_none(rows, "video_views")
    observed_dates = sorted({_text(row.get("date")) for row in rows if _text(row.get("date"))})
    return {
        "spend_sar": spend,
        "sales_sar": sales,
        "orders": int(round(orders)) if orders is not None else None,
        "impressions": int(round(impressions)) if impressions is not None else None,
        "swipes": int(round(swipes)) if swipes is not None else None,
        "video_views": int(round(video_views)) if video_views is not None else None,
        "roas": _ratio(sales, spend),
        "cpa_sar": _ratio(spend, orders),
        "cpc_sar": _ratio(spend, swipes),
        "cpm_sar": _ratio(spend, impressions, 1000.0),
        "ctr_pct": _ratio(swipes, impressions, 100.0),
        "observed_days": len(observed_dates),
        "source_rows": len(rows),
        "last_observed_at": _latest_updated_at(rows),
        "last_observed_date": observed_dates[-1] if observed_dates else None,
        "data_complete": bool(rows) and len(observed_dates) >= requested_days,
    }


def resolve_report_dates(
    from_date: str | None,
    to_date: str | None,
    *,
    today: date | None = None,
) -> list[date]:
    business_today = today or datetime.now(_timezone(BUSINESS_TIMEZONE)).date()
    payload = SnapchatNativeSyncInput(
        days=DEFAULT_REPORT_DAYS,
        from_date=from_date,
        to_date=to_date,
    )
    dates = enumerate_native_sync_dates(payload, today=business_today)
    if dates[-1] > business_today:
        raise SnapchatNativeSyncError(
            "future_date_not_allowed",
            "لا يمكن طلب تقرير إعلاني لفترة مستقبلية.",
            status_code=400,
        )
    return dates


async def _to_list(cursor: Any, *, length: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=length)
    return [row async for row in cursor]


async def read_snapchat_campaign_report(
    db: Any,
    user_id: str,
    *,
    from_date: str | None,
    to_date: str | None,
    campaign_query: str | None,
    page: int,
    limit: int,
) -> dict[str, Any]:
    dates = resolve_report_dates(from_date, to_date)
    selected_accounts = await _load_selected_accounts(db, user_id)
    selected_ids = [str(row["ad_account_id"]) for row in selected_accounts]
    account_meta = {
        str(row["ad_account_id"]): row
        for row in selected_accounts
    }
    range_query = {"$gte": dates[0].isoformat(), "$lte": dates[-1].isoformat()}

    performance_cursor = _collection(db, SNAPCHAT_PERFORMANCE_COLLECTION).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": {"$in": selected_ids},
            "entity_type": {"$in": ["ad_account", "campaign"]},
            "date": range_query,
        },
        {
            "_id": 0,
            "ad_account_id": 1,
            "entity_type": 1,
            "external_id": 1,
            "campaign_id": 1,
            "date": 1,
            "currency": 1,
            "spend_sar": 1,
            "purchase_value_sar": 1,
            "purchases": 1,
            "metrics": 1,
            "updated_at": 1,
        },
    )
    performance_rows = await _to_list(performance_cursor, length=MAX_REPORT_ROWS)
    row_limit_reached = len(performance_rows) >= MAX_REPORT_ROWS
    account_rows = [row for row in performance_rows if row.get("entity_type") == "ad_account"]
    campaign_rows = [row for row in performance_rows if row.get("entity_type") == "campaign"]
    summary_rows = account_rows or campaign_rows

    entity_cursor = _collection(db, SNAPCHAT_ENTITY_COLLECTION).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": {"$in": selected_ids},
            "entity_type": "campaign",
        },
        {
            "_id": 0,
            "ad_account_id": 1,
            "external_id": 1,
            "display_name": 1,
            "status": 1,
            "delivery_status": 1,
            "objective": 1,
            "daily_budget_micro": 1,
            "lifetime_spend_cap_micro": 1,
            "start_time": 1,
            "end_time": 1,
            "last_observed_at": 1,
        },
    )
    entity_rows = await _to_list(entity_cursor, length=MAX_REPORT_ENTITY_ROWS)
    entity_limit_reached = len(entity_rows) >= MAX_REPORT_ENTITY_ROWS
    entity_by_key = {
        (str(row.get("ad_account_id") or ""), str(row.get("external_id") or "")): row
        for row in entity_rows
        if row.get("ad_account_id") and row.get("external_id")
    }

    totals = aggregate_report_rows(summary_rows, requested_days=len(dates))

    daily_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in summary_rows:
        daily_groups[_text(row.get("date"))].append(row)
    daily = [
        {
            "date": day.isoformat(),
            **aggregate_report_rows(
                daily_groups.get(day.isoformat(), []),
                requested_days=1,
            ),
        }
        for day in dates
    ]

    account_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in summary_rows:
        account_groups[_text(row.get("ad_account_id"))].append(row)
    accounts: list[dict[str, Any]] = []
    for account_id in selected_ids:
        meta = account_meta.get(account_id, {})
        rows = account_groups.get(account_id, [])
        accounts.append({
            "account_id": account_id,
            "account_name": meta.get("display_name") or account_id,
            "currency": meta.get("currency"),
            "timezone": meta.get("timezone"),
            **aggregate_report_rows(rows, requested_days=len(dates)),
        })

    campaign_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in campaign_rows:
        campaign_id = _text(row.get("campaign_id") or row.get("external_id"))
        account_id = _text(row.get("ad_account_id"))
        if campaign_id and account_id:
            campaign_groups[(account_id, campaign_id)].append(row)

    campaigns: list[dict[str, Any]] = []
    identity_matches = 0
    for (account_id, campaign_id), rows in campaign_groups.items():
        meta = account_meta.get(account_id, {})
        entity = entity_by_key.get((account_id, campaign_id), {})
        if entity:
            identity_matches += 1
        currency = meta.get("currency") or rows[0].get("currency")
        campaigns.append({
            "account_id": account_id,
            "account_name": meta.get("display_name") or account_id,
            "campaign_id": campaign_id,
            "campaign_name": entity.get("display_name") or campaign_id,
            "status": entity.get("status") or "unknown",
            "delivery_status": entity.get("delivery_status"),
            "objective": entity.get("objective"),
            "start_time": entity.get("start_time"),
            "end_time": entity.get("end_time"),
            "budget": {
                "currency": currency,
                "daily_native": (
                    round(float(entity["daily_budget_micro"]) / 1_000_000, 6)
                    if _number(entity.get("daily_budget_micro")) is not None
                    else None
                ),
                "lifetime_native": (
                    round(float(entity["lifetime_spend_cap_micro"]) / 1_000_000, 6)
                    if _number(entity.get("lifetime_spend_cap_micro")) is not None
                    else None
                ),
            },
            **aggregate_report_rows(rows, requested_days=len(dates)),
        })

    query = _text(campaign_query).casefold()[:120]
    if query:
        campaigns = [
            row for row in campaigns
            if query in " ".join([
                _text(row.get("campaign_name")),
                _text(row.get("campaign_id")),
                _text(row.get("account_name")),
                _text(row.get("account_id")),
            ]).casefold()
        ]
    campaigns.sort(key=lambda row: (
        -(float(row.get("spend_sar") or 0)),
        _text(row.get("campaign_name")).casefold(),
        _text(row.get("campaign_id")),
    ))
    campaign_total = len(campaigns)
    pages = (campaign_total + limit - 1) // limit if campaign_total else 0
    offset = (page - 1) * limit
    paged_campaigns = campaigns[offset:offset + limit]

    requested_days = len(dates)
    observed_days = int(totals.get("observed_days") or 0)
    report_ready = bool(summary_rows)
    campaign_identity_ready = bool(campaign_groups) and identity_matches == len(campaign_groups)
    ratios_ready = any(totals.get(key) is not None for key in ("roas", "cpa_sar", "cpc_sar", "cpm_sar", "ctr_pct"))
    insights: list[dict[str, Any]] = []
    if not report_ready:
        insights.append({
            "code": "snapchat_report_no_local_rows",
            "severity": "warning",
            "title": "لا توجد بيانات سناب ضمن الفترة",
            "detail": "شغّل مزامنة سناب ثم أعد تحميل التقرير.",
        })
    elif observed_days < requested_days:
        insights.append({
            "code": "snapchat_report_partial_dates",
            "severity": "warning",
            "title": "تغطية الفترة غير مكتملة",
            "detail": f"تتوفر بيانات {observed_days} يوم من أصل {requested_days} يوم.",
        })
    if campaign_groups and not campaign_identity_ready:
        insights.append({
            "code": "snapchat_campaign_identity_partial",
            "severity": "info",
            "title": "بعض أسماء الحملات غير مكتملة",
            "detail": "التقرير يحتفظ بالأرقام ويستخدم رقم الحملة عندما لا يتوفر اسمها المحلي.",
        })
    if row_limit_reached or entity_limit_reached:
        insights.append({
            "code": "snapchat_report_local_row_limit",
            "severity": "critical",
            "title": "بلغ التقرير الحد التشغيلي للصفوف",
            "detail": "قلّص الفترة ثم أعد تحميل التقرير للحصول على تغطية مؤكدة.",
        })

    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "date_from": dates[0].isoformat(),
        "date_to": dates[-1].isoformat(),
        "business_timezone": BUSINESS_TIMEZONE,
        "totals": totals,
        "daily": daily,
        "accounts": accounts,
        "campaigns": paged_campaigns,
        "campaign_pagination": {
            "page": page,
            "limit": limit,
            "total": campaign_total,
            "pages": pages,
        },
        "source": {
            "performance_collection": SNAPCHAT_PERFORMANCE_COLLECTION,
            "entity_collection": SNAPCHAT_ENTITY_COLLECTION,
            "attribution_model": ATTRIBUTION_MODEL,
            "selected_account_count": len(selected_ids),
            "performance_rows": len(performance_rows),
            "entity_rows": len(entity_rows),
            "identity_matches": identity_matches,
            "identity_coverage_pct": (
                round(identity_matches / len(campaign_groups) * 100, 2)
                if campaign_groups else None
            ),
            "row_limit_reached": row_limit_reached,
            "entity_limit_reached": entity_limit_reached,
        },
        "ai_readiness": {
            "report_ready": report_ready,
            "campaign_identity_ready": campaign_identity_ready,
            "spend_ready": totals.get("spend_sar") is not None,
            "orders_ready": totals.get("orders") is not None,
            "sales_ready": totals.get("sales_sar") is not None,
            "ratios_ready": ratios_ready,
            "ai_analysis_ready": report_ready and totals.get("spend_sar") is not None,
            "required_lifecycle": [
                "proposal", "preview", "approval", "execution",
                "verification", "audit", "rollback",
            ],
        },
        "insights": insights,
        "policy": {"mode": "observe_only", "mutations_allowed": False},
        "source_only": True,
        "provider_read_reached": False,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def attach_snapchat_campaign_report_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get(
        f"/{SNAPCHAT_PROVIDER_ID}/campaign-report",
        name="get_snapchat_campaign_report",
    )
    async def campaign_report(
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
        campaign_query: str | None = Query(default=None, max_length=120),
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=25, ge=10, le=100),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        try:
            return await read_snapchat_campaign_report(
                db,
                str(owner["id"]),
                from_date=from_date,
                to_date=to_date,
                campaign_query=campaign_query,
                page=page,
                limit=limit,
            )
        except SnapchatNativeSyncError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    "source_only": True,
                    "provider_write_reached": False,
                    "campaign_write_reached": False,
                    "accounting_write_reached": False,
                    "qoyod_write_reached": False,
                },
            ) from exc


__all__ = [
    "aggregate_report_rows",
    "attach_snapchat_campaign_report_routes",
    "read_snapchat_campaign_report",
    "resolve_report_dates",
]
