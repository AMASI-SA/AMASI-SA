"""Owner-only GET routes for the unified read-only advertising manager."""
from __future__ import annotations

import math
import os
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status

from integrations_control_center.legacy_readers import sanitize_for_output
from integrations_control_center.routes import _require_owner
from integrations_control_center.service import IntegrationsControlCenterService
from integrations_control_center.snapchat_native_data_common import (
    ATTRIBUTION_MODEL,
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_PERFORMANCE_COLLECTION,
)

from .models import AdsManagerOverview
from .service import AdsManagerService


_ERROR_MESSAGES = {
    "invalid_date": "استخدم تاريخًا صحيحًا بصيغة YYYY-MM-DD.",
    "date_to_before_date_from": "تاريخ النهاية يجب ألا يسبق تاريخ البداية.",
    "future_date_not_allowed": "لا يمكن طلب فترة مستقبلية.",
    "range_too_wide": "الحد الأقصى للفترة هو 90 يومًا.",
    "invalid_provider": "منصة الإعلانات غير معروفة.",
}
FEATURE_FLAG_ENV = "MEZAN_ADS_MANAGER_READ_ONLY_ENABLED"
RIYADH_TZ = ZoneInfo("Asia/Riyadh")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_SNAPCHAT_REPORT_DAYS = 90
MAX_SNAPCHAT_PERFORMANCE_ROWS = 5_000
MAX_SNAPCHAT_ENTITY_ROWS = 5_000
MAX_SNAPCHAT_ACCOUNTS = 250


def _feature_enabled() -> bool:
    return os.getenv(FEATURE_FLAG_ENV, "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _text(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _integer(value: Any) -> int | None:
    parsed = _number(value)
    if parsed is None or not parsed.is_integer():
        return None
    return int(parsed)


def _rounded(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _ratio(numerator: float | None, denominator: float | int | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(numerator / denominator, 2)


def _snapchat_range(
    date_from: str | None,
    date_to: str | None,
    *,
    today: date,
) -> tuple[date, date]:
    start_raw = date_from or today.replace(day=1).isoformat()
    end_raw = date_to or today.isoformat()
    if not ISO_DATE_RE.fullmatch(start_raw) or not ISO_DATE_RE.fullmatch(end_raw):
        raise ValueError("invalid_date")
    start = date.fromisoformat(start_raw)
    end = date.fromisoformat(end_raw)
    if end < start:
        raise ValueError("date_to_before_date_from")
    if end > today:
        raise ValueError("future_date_not_allowed")
    if (end - start).days + 1 > MAX_SNAPCHAT_REPORT_DAYS:
        raise ValueError("range_too_wide")
    return start, end


async def _read_rows(
    db: Any,
    collection_name: str,
    query: dict,
    projection: dict,
    *,
    limit: int,
    sort: list[tuple[str, int]] | None = None,
) -> list[dict]:
    cursor = db[collection_name].find(query, projection)
    if sort:
        cursor = cursor.sort(sort)
    cursor = cursor.limit(limit + 1)
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=limit + 1)
    return [row async for row in cursor]


def _empty_performance_bucket() -> dict[str, Any]:
    return {
        "rows": 0,
        "dates": set(),
        "spend_sar": 0.0,
        "sales_sar": 0.0,
        "orders": 0,
        "impressions": 0,
        "swipes": 0,
        "video_views": 0,
        "spend_complete": True,
        "sales_complete": True,
        "orders_complete": True,
        "impressions_complete": True,
        "swipes_complete": True,
        "video_views_complete": True,
        "last_observed_at": None,
        "last_observed_date": None,
    }


def _add_performance(bucket: dict[str, Any], row: dict) -> None:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    bucket["rows"] += 1
    observed_date = _text(row.get("date"), 10)
    if observed_date:
        bucket["dates"].add(observed_date)
        if not bucket["last_observed_date"] or observed_date > bucket["last_observed_date"]:
            bucket["last_observed_date"] = observed_date
    marker = _text(row.get("updated_at") or row.get("provider_window_end"), 80)
    if marker and (not bucket["last_observed_at"] or marker > bucket["last_observed_at"]):
        bucket["last_observed_at"] = marker

    values = {
        "spend_sar": _number(row.get("spend_sar")),
        "sales_sar": _number(row.get("purchase_value_sar")),
        "orders": _integer(metrics.get("conversion_purchases")),
        "impressions": _integer(metrics.get("impressions")),
        "swipes": _integer(metrics.get("swipes")),
        "video_views": _integer(metrics.get("video_views")),
    }
    for key, value in values.items():
        if value is None:
            bucket[f"{key}_complete"] = False
        else:
            bucket[key] += value


def _finish_performance(bucket: dict[str, Any] | None) -> dict[str, Any]:
    if not bucket or not bucket["rows"]:
        return {
            "spend_sar": None,
            "sales_sar": None,
            "orders": None,
            "impressions": None,
            "swipes": None,
            "video_views": None,
            "roas": None,
            "cpa_sar": None,
            "cpc_sar": None,
            "cpm_sar": None,
            "ctr_pct": None,
            "observed_days": 0,
            "source_rows": 0,
            "last_observed_at": None,
            "last_observed_date": None,
            "data_complete": False,
        }
    spend = _rounded(bucket["spend_sar"]) if bucket["spend_complete"] else None
    sales = _rounded(bucket["sales_sar"]) if bucket["sales_complete"] else None
    orders = int(bucket["orders"]) if bucket["orders_complete"] else None
    impressions = int(bucket["impressions"]) if bucket["impressions_complete"] else None
    swipes = int(bucket["swipes"]) if bucket["swipes_complete"] else None
    video_views = int(bucket["video_views"]) if bucket["video_views_complete"] else None
    return {
        "spend_sar": spend,
        "sales_sar": sales,
        "orders": orders,
        "impressions": impressions,
        "swipes": swipes,
        "video_views": video_views,
        "roas": _ratio(sales, spend),
        "cpa_sar": _ratio(spend, orders),
        "cpc_sar": _ratio(spend, swipes),
        "cpm_sar": (
            round(spend * 1000 / impressions, 2)
            if spend is not None and impressions is not None and impressions > 0
            else None
        ),
        "ctr_pct": (
            round(swipes / impressions * 100, 2)
            if swipes is not None and impressions is not None and impressions > 0
            else None
        ),
        "observed_days": len(bucket["dates"]),
        "source_rows": int(bucket["rows"]),
        "last_observed_at": bucket["last_observed_at"],
        "last_observed_date": bucket["last_observed_date"],
        "data_complete": all(
            bucket[key]
            for key in (
                "spend_complete",
                "sales_complete",
                "orders_complete",
                "impressions_complete",
                "swipes_complete",
            )
        ),
    }


def _campaign_budget(entity: dict, currency: str | None) -> dict[str, Any]:
    daily_micro = _number(entity.get("daily_budget_micro"))
    lifetime_micro = _number(entity.get("lifetime_spend_cap_micro"))
    return {
        "currency": currency,
        "daily_native": _rounded(daily_micro / 1_000_000) if daily_micro is not None else None,
        "lifetime_native": _rounded(lifetime_micro / 1_000_000) if lifetime_micro is not None else None,
    }


def _snapchat_insights(campaigns: list[dict], totals: dict, limited: bool) -> list[dict]:
    insights: list[dict] = []
    spenders = [row for row in campaigns if (row.get("spend_sar") or 0) > 0]
    if spenders:
        highest = max(spenders, key=lambda row: row.get("spend_sar") or 0)
        insights.append({
            "code": "highest_spend_campaign",
            "severity": "info",
            "title": "أعلى حملة في الصرف",
            "detail": f"{highest['campaign_name']} هي الأعلى صرفًا ضمن الفترة المحددة.",
            "campaign_id": highest["campaign_id"],
        })
    sales_rows = [row for row in campaigns if row.get("sales_sar") is not None]
    if sales_rows:
        highest = max(sales_rows, key=lambda row: row.get("sales_sar") or 0)
        insights.append({
            "code": "highest_sales_campaign",
            "severity": "info",
            "title": "أعلى حملة في المبيعات المنسوبة",
            "detail": f"{highest['campaign_name']} حققت أعلى مبيعات منسوبة بواسطة Snapchat.",
            "campaign_id": highest["campaign_id"],
        })
    if limited:
        insights.append({
            "code": "source_row_limit_reached",
            "severity": "warning",
            "title": "قراءة التقرير غير مكتملة",
            "detail": "بلغ مصدر الحملات حد القراءة؛ لا يعتمد الذكاء الاصطناعي على التقرير لاتخاذ قرار.",
        })
    if totals.get("spend_sar") is not None and totals.get("sales_sar") is None:
        insights.append({
            "code": "sales_attribution_unavailable",
            "severity": "warning",
            "title": "الصرف متاح والمبيعات غير مكتملة",
            "detail": "يجب اكتمال قيمة التحويلات قبل مقارنة ROAS أو اقتراح تعديل ميزانية.",
        })
    return insights[:10]


async def _snapchat_marketing_workspace(
    db: Any,
    user_id: str,
    *,
    date_from: str | None,
    date_to: str | None,
    campaign_query: str | None,
    page: int,
    limit: int,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    today = now.astimezone(RIYADH_TZ).date()
    start, end = _snapchat_range(date_from, date_to, today=today)
    from_iso, to_iso = start.isoformat(), end.isoformat()

    performance_rows = await _read_rows(
        db,
        SNAPCHAT_PERFORMANCE_COLLECTION,
        {
            "user_id": user_id,
            "provider": "snapchat_ads",
            "entity_type": "campaign",
            "date": {"$gte": from_iso, "$lte": to_iso},
            "attribution_model": ATTRIBUTION_MODEL,
        },
        {
            "_id": 0,
            "ad_account_id": 1,
            "campaign_id": 1,
            "external_id": 1,
            "date": 1,
            "currency": 1,
            "metrics": 1,
            "spend_sar": 1,
            "purchase_value_sar": 1,
            "provider_window_end": 1,
            "updated_at": 1,
        },
        limit=MAX_SNAPCHAT_PERFORMANCE_ROWS,
        sort=[("date", 1), ("ad_account_id", 1), ("campaign_id", 1)],
    )
    row_limit_reached = len(performance_rows) > MAX_SNAPCHAT_PERFORMANCE_ROWS
    performance_rows = performance_rows[:MAX_SNAPCHAT_PERFORMANCE_ROWS]

    entity_rows = await _read_rows(
        db,
        SNAPCHAT_ENTITY_COLLECTION,
        {
            "user_id": user_id,
            "provider": "snapchat_ads",
            "entity_type": "campaign",
        },
        {
            "_id": 0,
            "ad_account_id": 1,
            "campaign_id": 1,
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
        limit=MAX_SNAPCHAT_ENTITY_ROWS,
        sort=[("last_observed_at", -1), ("display_name", 1)],
    )
    entity_limit_reached = len(entity_rows) > MAX_SNAPCHAT_ENTITY_ROWS
    entity_rows = entity_rows[:MAX_SNAPCHAT_ENTITY_ROWS]

    account_rows = await _read_rows(
        db,
        "mezan_integration_accounts_v2",
        {"user_id": user_id, "provider": "snapchat_ads"},
        {
            "_id": 0,
            "external_account_id": 1,
            "ad_account_id": 1,
            "display_name": 1,
            "currency": 1,
            "timezone": 1,
            "connection_status": 1,
            "last_sync_at": 1,
        },
        limit=MAX_SNAPCHAT_ACCOUNTS,
        sort=[("display_name", 1)],
    )
    account_rows = account_rows[:MAX_SNAPCHAT_ACCOUNTS]
    accounts = {
        _text(row.get("ad_account_id") or row.get("external_account_id"), 120): row
        for row in account_rows
        if _text(row.get("ad_account_id") or row.get("external_account_id"), 120)
    }

    entity_map: dict[tuple[str, str], dict] = {}
    entity_by_campaign: dict[str, dict] = {}
    for row in entity_rows:
        campaign_id = _text(row.get("campaign_id") or row.get("external_id"), 160)
        account_id = _text(row.get("ad_account_id"), 120)
        if not campaign_id:
            continue
        entity_map[(account_id, campaign_id)] = row
        entity_by_campaign.setdefault(campaign_id, row)

    campaign_buckets: dict[tuple[str, str], dict[str, Any]] = {}
    account_buckets: dict[str, dict[str, Any]] = {}
    daily_buckets: dict[str, dict[str, Any]] = {}
    total_bucket = _empty_performance_bucket()
    for row in performance_rows:
        account_id = _text(row.get("ad_account_id"), 120)
        campaign_id = _text(row.get("campaign_id") or row.get("external_id"), 160)
        if not account_id or not campaign_id:
            continue
        _add_performance(campaign_buckets.setdefault((account_id, campaign_id), _empty_performance_bucket()), row)
        _add_performance(account_buckets.setdefault(account_id, _empty_performance_bucket()), row)
        observed_date = _text(row.get("date"), 10)
        if observed_date:
            _add_performance(daily_buckets.setdefault(observed_date, _empty_performance_bucket()), row)
        _add_performance(total_bucket, row)

    campaigns: list[dict[str, Any]] = []
    identity_matches = 0
    for (account_id, campaign_id), bucket in campaign_buckets.items():
        entity = entity_map.get((account_id, campaign_id)) or entity_by_campaign.get(campaign_id) or {}
        account = accounts.get(account_id) or {}
        if entity:
            identity_matches += 1
        campaigns.append({
            "account_id": account_id,
            "account_name": _text(account.get("display_name"), 180) or account_id,
            "campaign_id": campaign_id,
            "campaign_name": _text(entity.get("display_name"), 200) or campaign_id,
            "status": _text(entity.get("status"), 60) or "unknown",
            "delivery_status": _text(entity.get("delivery_status"), 60) or None,
            "objective": _text(entity.get("objective"), 80) or None,
            "start_time": _text(entity.get("start_time"), 80) or None,
            "end_time": _text(entity.get("end_time"), 80) or None,
            "budget": _campaign_budget(entity, _text(account.get("currency"), 12) or None),
            **_finish_performance(bucket),
        })

    query = _text(campaign_query, 120).casefold()
    if query:
        campaigns = [
            row for row in campaigns
            if query in row["campaign_name"].casefold()
            or query in row["campaign_id"].casefold()
            or query in row["account_name"].casefold()
        ]
    campaigns.sort(key=lambda row: (row.get("spend_sar") is not None, row.get("spend_sar") or 0), reverse=True)
    total_campaigns = len(campaigns)
    pages = math.ceil(total_campaigns / limit) if total_campaigns else 0
    safe_page = min(max(page, 1), pages) if pages else 1
    offset = (safe_page - 1) * limit

    account_summaries = [
        {
            "account_id": account_id,
            "account_name": _text(accounts.get(account_id, {}).get("display_name"), 180) or account_id,
            "currency": _text(accounts.get(account_id, {}).get("currency"), 12) or None,
            "timezone": _text(accounts.get(account_id, {}).get("timezone"), 80) or None,
            **_finish_performance(bucket),
        }
        for account_id, bucket in account_buckets.items()
    ]
    account_summaries.sort(key=lambda row: row.get("spend_sar") or 0, reverse=True)

    daily = []
    cursor = start
    while cursor <= end:
        date_iso = cursor.isoformat()
        daily.append({"date": date_iso, **_finish_performance(daily_buckets.get(date_iso))})
        cursor = date.fromordinal(cursor.toordinal() + 1)

    integration_overview = await IntegrationsControlCenterService(db, now=lambda: now).overview(user_id)
    card = next(
        (row for row in integration_overview.get("providers") or [] if row.get("provider") == "snapchat_ads"),
        {},
    )
    totals = _finish_performance(total_bucket)
    source = {
        "performance_collection": SNAPCHAT_PERFORMANCE_COLLECTION,
        "entity_collection": SNAPCHAT_ENTITY_COLLECTION,
        "attribution_model": ATTRIBUTION_MODEL,
        "performance_rows": len(performance_rows),
        "entity_rows": len(entity_rows),
        "identity_matches": identity_matches,
        "identity_coverage_pct": (
            round(identity_matches / len(campaign_buckets) * 100, 2)
            if campaign_buckets
            else None
        ),
        "row_limit_reached": row_limit_reached,
        "entity_limit_reached": entity_limit_reached,
    }
    readiness = {
        "report_ready": bool(performance_rows) and not row_limit_reached,
        "campaign_identity_ready": bool(campaign_buckets)
        and identity_matches == len(campaign_buckets)
        and not entity_limit_reached,
        "spend_ready": totals.get("spend_sar") is not None,
        "orders_ready": totals.get("orders") is not None,
        "sales_ready": totals.get("sales_sar") is not None,
        "ratios_ready": all(totals.get(key) is not None for key in ("spend_sar", "orders", "sales_sar")),
        "ai_analysis_ready": bool(performance_rows)
        and not row_limit_reached
        and totals.get("spend_sar") is not None,
        "campaign_creation_enabled": False,
        "campaign_management_enabled": False,
        "required_lifecycle": [
            "proposal",
            "preview",
            "approval",
            "execution",
            "verification",
            "audit",
            "rollback",
        ],
    }

    return sanitize_for_output({
        "generated_at": now.isoformat(),
        "platform": "snapchat",
        "range": {
            "date_from": from_iso,
            "date_to": to_iso,
            "timezone": "Asia/Riyadh",
        },
        "connection": {
            "status": card.get("connection_status") or "unknown",
            "provenance": card.get("connection_provenance") or "unknown",
            "last_sync_at": card.get("last_sync_at"),
            "data_delay_minutes": card.get("data_delay_minutes"),
            "health_status": (card.get("health") or {}).get("status") or "unknown",
            "health_score": (card.get("health") or {}).get("score"),
            "accounts_count": len(card.get("accounts") or account_rows),
        },
        "totals": totals,
        "daily": daily,
        "accounts": account_summaries,
        "campaigns": campaigns[offset : offset + limit],
        "campaign_pagination": {
            "page": safe_page,
            "limit": limit,
            "total": total_campaigns,
            "pages": pages,
        },
        "source": source,
        "ai_readiness": readiness,
        "insights": _snapchat_insights(campaigns, totals, row_limit_reached),
        "policy": {
            "mode": "observe_only",
            "mutations_allowed": False,
            "provider_network_called": False,
            "campaign_write_reached": False,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        },
    })


def make_ads_manager_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(
        prefix="/ads-manager",
        tags=["unified-ads-manager-read-only"],
    )
    if not _feature_enabled():
        @router.get("/overview", include_in_schema=False)
        async def overview_disabled() -> None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "feature_disabled",
                    "message": "مدير الإعلانات للقراءة غير مفعّل.",
                },
            )

        @router.get("/snapchat-workspace", include_in_schema=False)
        async def snapchat_workspace_disabled() -> None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "feature_disabled",
                    "message": "تقارير Snapchat للقراءة غير مفعّلة.",
                },
            )

        return router

    service = AdsManagerService(db)

    @router.get("/overview", response_model=AdsManagerOverview)
    async def overview(
        date_from: str | None = Query(default=None),
        date_to: str | None = Query(default=None),
        provider: str = Query(default="all"),
        campaign_query: str | None = Query(default=None, max_length=120),
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=50, ge=10, le=100),
        user: dict = Depends(current_user),
    ) -> dict:
        owner = _require_owner(user)
        try:
            return await service.overview(
                str(owner["id"]),
                date_from=date_from,
                date_to=date_to,
                provider=provider,
                campaign_query=campaign_query,
                page=page,
                limit=limit,
            )
        except ValueError as exc:
            code = str(exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": code,
                    "message": _ERROR_MESSAGES.get(code, "تعذر قراءة نطاق التقرير."),
                },
            ) from exc

    @router.get("/snapchat-workspace")
    async def snapchat_workspace(
        date_from: str | None = Query(default=None),
        date_to: str | None = Query(default=None),
        campaign_query: str | None = Query(default=None, max_length=120),
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=25, ge=10, le=100),
        user: dict = Depends(current_user),
    ) -> dict:
        owner = _require_owner(user)
        try:
            return await _snapchat_marketing_workspace(
                db,
                str(owner["id"]),
                date_from=date_from,
                date_to=date_to,
                campaign_query=campaign_query,
                page=page,
                limit=limit,
            )
        except ValueError as exc:
            code = str(exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": code,
                    "message": _ERROR_MESSAGES.get(code, "تعذر قراءة تقرير Snapchat."),
                },
            ) from exc

    return router
