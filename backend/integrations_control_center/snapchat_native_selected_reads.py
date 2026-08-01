"""Read-only Snapchat performance summaries limited to owner-selected accounts."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from .snapchat_account_selection import _load_selected_accounts
from .snapchat_native_data_common import (
    ATTRIBUTION_MODEL,
    BUSINESS_TIMEZONE,
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
    SNAPCHAT_PERFORMANCE_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatNativeSyncInput,
    _collection,
    _timezone,
    _utcnow,
    enumerate_native_sync_dates,
)

MAX_SELECTED_PERFORMANCE_ROWS = 5000
MAX_SELECTED_ENTITY_ROWS = 5000


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


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _ratio(numerator: float | None, denominator: float | int | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(numerator / denominator, 2)


def _new_bucket() -> dict[str, Any]:
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


def _add_bucket_row(bucket: dict[str, Any], row: dict) -> None:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    bucket["rows"] += 1
    date_key = _text(row.get("date"), 10)
    if date_key:
        bucket["dates"].add(date_key)
        if not bucket["last_observed_date"] or date_key > bucket["last_observed_date"]:
            bucket["last_observed_date"] = date_key
    observed_at = _text(row.get("updated_at") or row.get("provider_window_end"), 80)
    if observed_at and (
        not bucket["last_observed_at"]
        or observed_at > bucket["last_observed_at"]
    ):
        bucket["last_observed_at"] = observed_at

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


def _finish_bucket(bucket: dict[str, Any] | None) -> dict[str, Any]:
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

    spend = _round(bucket["spend_sar"]) if bucket["spend_complete"] else None
    sales = _round(bucket["sales_sar"]) if bucket["sales_complete"] else None
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


def _budget(entity: dict, currency: str | None) -> dict[str, Any]:
    daily_micro = _number(entity.get("daily_budget_micro"))
    lifetime_micro = _number(entity.get("lifetime_spend_cap_micro"))
    return {
        "currency": currency,
        "daily_native": (
            _round(daily_micro / 1_000_000)
            if daily_micro is not None
            else None
        ),
        "lifetime_native": (
            _round(lifetime_micro / 1_000_000)
            if lifetime_micro is not None
            else None
        ),
    }


def _campaign_insights(
    campaigns: list[dict[str, Any]],
    totals: dict[str, Any],
    *,
    row_limit_reached: bool,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    spenders = [row for row in campaigns if (row.get("spend_sar") or 0) > 0]
    if spenders:
        highest = max(spenders, key=lambda row: row.get("spend_sar") or 0)
        output.append({
            "code": "highest_spend_campaign",
            "severity": "info",
            "title": "أعلى حملة في الصرف",
            "detail": f"{highest['campaign_name']} هي الأعلى صرفًا ضمن الفترة المحددة.",
            "campaign_id": highest["campaign_id"],
        })
    sales_rows = [row for row in campaigns if row.get("sales_sar") is not None]
    if sales_rows:
        highest = max(sales_rows, key=lambda row: row.get("sales_sar") or 0)
        output.append({
            "code": "highest_sales_campaign",
            "severity": "info",
            "title": "أعلى حملة في المبيعات المنسوبة",
            "detail": f"{highest['campaign_name']} حققت أعلى مبيعات منسوبة بواسطة Snapchat.",
            "campaign_id": highest["campaign_id"],
        })
    if row_limit_reached:
        output.append({
            "code": "source_row_limit_reached",
            "severity": "warning",
            "title": "قراءة التقرير غير مكتملة",
            "detail": "بلغت بيانات الحملات حد القراءة؛ لا يعتمد الذكاء الاصطناعي على التقرير لاتخاذ قرار.",
        })
    if totals.get("spend_sar") is not None and totals.get("sales_sar") is None:
        output.append({
            "code": "sales_attribution_unavailable",
            "severity": "warning",
            "title": "الصرف متاح والمبيعات غير مكتملة",
            "detail": "يجب اكتمال قيمة التحويلات قبل مقارنة ROAS أو اقتراح تعديل ميزانية.",
        })
    return output[:10]


async def selected_snapchat_performance_summary(
    db: Any,
    user_id: str,
    *,
    from_date: str | None,
    to_date: str | None,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Aggregate ad-account rows while excluding every unselected account."""
    payload = SnapchatNativeSyncInput(
        from_date=from_date,
        to_date=to_date,
        days=1,
    )
    now_value = now().astimezone(timezone.utc)
    dates = enumerate_native_sync_dates(
        payload,
        today=now_value.astimezone(
            _timezone(BUSINESS_TIMEZONE)
        ).date(),
    )
    selected_accounts = await _load_selected_accounts(db, user_id)
    selected_ids = [
        str(account["ad_account_id"])
        for account in selected_accounts
    ]
    account_meta = {
        str(account["ad_account_id"]): account
        for account in selected_accounts
    }
    date_query = {
        "$gte": dates[0].isoformat(),
        "$lte": dates[-1].isoformat(),
    }
    cursor = _collection(
        db, SNAPCHAT_PERFORMANCE_COLLECTION
    ).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": {"$in": selected_ids},
            "entity_type": "ad_account",
            "date": date_query,
        },
        {
            "_id": 0,
            "ad_account_id": 1,
            "currency": 1,
            "spend_native": 1,
            "spend_sar": 1,
            "purchase_value_native": 1,
            "purchase_value_sar": 1,
        },
    )
    rows = (
        await cursor.to_list(length=5000)
        if hasattr(cursor, "to_list")
        else [row async for row in cursor]
    )

    per_account: dict[str, dict[str, Any]] = {}
    total_spend_sar = 0.0
    total_purchase_value_sar = 0.0
    numeric_fields = (
        "spend_native",
        "spend_sar",
        "purchase_value_native",
        "purchase_value_sar",
    )
    for row in rows:
        account_id = str(row.get("ad_account_id") or "")
        if not account_id:
            continue
        meta = account_meta.get(account_id, {})
        item = per_account.setdefault(
            account_id,
            {
                "account_id": account_id,
                "display_name": meta.get("display_name"),
                "currency": row.get("currency") or meta.get("currency"),
                "timezone": meta.get("timezone"),
                "rows": 0,
                **{field: 0.0 for field in numeric_fields},
            },
        )
        item["rows"] += 1
        for field in numeric_fields:
            value = row.get(field)
            if value is not None:
                item[field] += float(value)
        if row.get("spend_sar") is not None:
            total_spend_sar += float(row["spend_sar"])
        if row.get("purchase_value_sar") is not None:
            total_purchase_value_sar += float(
                row["purchase_value_sar"]
            )

    excluded_rows = await _collection(
        db, SNAPCHAT_PERFORMANCE_COLLECTION
    ).count_documents(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": {"$nin": selected_ids},
            "entity_type": "ad_account",
            "date": date_query,
        }
    )

    accounts: list[dict[str, Any]] = []
    for account_id in selected_ids:
        item = per_account.get(account_id)
        if item is None:
            meta = account_meta[account_id]
            item = {
                "account_id": account_id,
                "display_name": meta.get("display_name"),
                "currency": meta.get("currency"),
                "timezone": meta.get("timezone"),
                "rows": 0,
                **{field: 0.0 for field in numeric_fields},
            }
        for field in numeric_fields:
            item[field] = round(float(item[field]), 6)
        accounts.append(item)

    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "date_from": dates[0].isoformat(),
        "date_to": dates[-1].isoformat(),
        "selected_account_ids": selected_ids,
        "selected_account_count": len(selected_ids),
        "rows_included": len(rows),
        "unselected_rows_excluded": int(excluded_rows),
        "spend_sar": round(total_spend_sar, 6),
        "purchase_value_sar": round(
            total_purchase_value_sar, 6
        ),
        "accounts": accounts,
        "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
        "source_only": True,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


async def selected_snapchat_campaign_report(
    db: Any,
    user_id: str,
    *,
    from_date: str | None,
    to_date: str | None,
    campaign_query: str | None = None,
    page: int = 1,
    limit: int = 25,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Return verified campaign performance for owner-selected accounts only."""
    payload = SnapchatNativeSyncInput(
        from_date=from_date,
        to_date=to_date,
        days=1,
    )
    now_value = now().astimezone(timezone.utc)
    dates = enumerate_native_sync_dates(
        payload,
        today=now_value.astimezone(_timezone(BUSINESS_TIMEZONE)).date(),
    )
    selected_accounts = await _load_selected_accounts(db, user_id)
    selected_ids = [str(account["ad_account_id"]) for account in selected_accounts]
    account_meta = {
        str(account["ad_account_id"]): account
        for account in selected_accounts
    }
    date_query = {
        "$gte": dates[0].isoformat(),
        "$lte": dates[-1].isoformat(),
    }

    performance_cursor = _collection(db, SNAPCHAT_PERFORMANCE_COLLECTION).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": {"$in": selected_ids},
            "entity_type": "campaign",
            "date": date_query,
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
    )
    if hasattr(performance_cursor, "sort"):
        performance_cursor = performance_cursor.sort([
            ("date", 1),
            ("ad_account_id", 1),
            ("campaign_id", 1),
        ])
    if hasattr(performance_cursor, "limit"):
        performance_cursor = performance_cursor.limit(
            MAX_SELECTED_PERFORMANCE_ROWS + 1
        )
    performance_rows = (
        await performance_cursor.to_list(
            length=MAX_SELECTED_PERFORMANCE_ROWS + 1
        )
        if hasattr(performance_cursor, "to_list")
        else [row async for row in performance_cursor]
    )
    row_limit_reached = len(performance_rows) > MAX_SELECTED_PERFORMANCE_ROWS
    performance_rows = performance_rows[:MAX_SELECTED_PERFORMANCE_ROWS]

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
    )
    if hasattr(entity_cursor, "sort"):
        entity_cursor = entity_cursor.sort([
            ("last_observed_at", -1),
            ("display_name", 1),
        ])
    if hasattr(entity_cursor, "limit"):
        entity_cursor = entity_cursor.limit(MAX_SELECTED_ENTITY_ROWS + 1)
    entity_rows = (
        await entity_cursor.to_list(length=MAX_SELECTED_ENTITY_ROWS + 1)
        if hasattr(entity_cursor, "to_list")
        else [row async for row in entity_cursor]
    )
    entity_limit_reached = len(entity_rows) > MAX_SELECTED_ENTITY_ROWS
    entity_rows = entity_rows[:MAX_SELECTED_ENTITY_ROWS]

    entity_map: dict[tuple[str, str], dict] = {}
    entity_by_campaign: dict[str, dict] = {}
    for row in entity_rows:
        account_id = _text(row.get("ad_account_id"), 120)
        campaign_id = _text(row.get("campaign_id") or row.get("external_id"), 160)
        if not campaign_id:
            continue
        entity_map[(account_id, campaign_id)] = row
        entity_by_campaign.setdefault(campaign_id, row)

    total_bucket = _new_bucket()
    campaign_buckets: dict[tuple[str, str], dict[str, Any]] = {}
    account_buckets: dict[str, dict[str, Any]] = {}
    daily_buckets: dict[str, dict[str, Any]] = {}
    for row in performance_rows:
        account_id = _text(row.get("ad_account_id"), 120)
        campaign_id = _text(row.get("campaign_id") or row.get("external_id"), 160)
        if not account_id or not campaign_id:
            continue
        _add_bucket_row(
            campaign_buckets.setdefault((account_id, campaign_id), _new_bucket()),
            row,
        )
        _add_bucket_row(
            account_buckets.setdefault(account_id, _new_bucket()),
            row,
        )
        date_key = _text(row.get("date"), 10)
        if date_key:
            _add_bucket_row(
                daily_buckets.setdefault(date_key, _new_bucket()),
                row,
            )
        _add_bucket_row(total_bucket, row)

    campaigns: list[dict[str, Any]] = []
    identity_matches = 0
    for (account_id, campaign_id), bucket in campaign_buckets.items():
        account = account_meta.get(account_id, {})
        entity = (
            entity_map.get((account_id, campaign_id))
            or entity_by_campaign.get(campaign_id)
            or {}
        )
        if entity:
            identity_matches += 1
        campaigns.append({
            "account_id": account_id,
            "account_name": account.get("display_name") or account_id,
            "campaign_id": campaign_id,
            "campaign_name": _text(entity.get("display_name"), 200) or campaign_id,
            "status": _text(entity.get("status"), 60) or "unknown",
            "delivery_status": _text(entity.get("delivery_status"), 60) or None,
            "objective": _text(entity.get("objective"), 80) or None,
            "start_time": _text(entity.get("start_time"), 80) or None,
            "end_time": _text(entity.get("end_time"), 80) or None,
            "budget": _budget(entity, account.get("currency")),
            **_finish_bucket(bucket),
        })

    query = _text(campaign_query, 120).casefold()
    if query:
        campaigns = [
            row for row in campaigns
            if query in row["campaign_name"].casefold()
            or query in row["campaign_id"].casefold()
            or query in _text(row.get("account_name"), 180).casefold()
        ]
    campaigns.sort(
        key=lambda row: (
            row.get("spend_sar") is not None,
            row.get("spend_sar") or 0,
        ),
        reverse=True,
    )
    total_campaigns = len(campaigns)
    pages = math.ceil(total_campaigns / limit) if total_campaigns else 0
    safe_page = min(max(page, 1), pages) if pages else 1
    offset = (safe_page - 1) * limit

    accounts = []
    for account_id in selected_ids:
        meta = account_meta.get(account_id, {})
        accounts.append({
            "account_id": account_id,
            "account_name": meta.get("display_name") or account_id,
            "currency": meta.get("currency"),
            "timezone": meta.get("timezone"),
            **_finish_bucket(account_buckets.get(account_id)),
        })
    accounts.sort(key=lambda row: row.get("spend_sar") or 0, reverse=True)

    daily = []
    cursor = dates[0]
    while cursor <= dates[-1]:
        date_key = cursor.isoformat()
        daily.append({
            "date": date_key,
            **_finish_bucket(daily_buckets.get(date_key)),
        })
        cursor += timedelta(days=1)

    totals = _finish_bucket(total_bucket)
    source = {
        "performance_collection": SNAPCHAT_PERFORMANCE_COLLECTION,
        "entity_collection": SNAPCHAT_ENTITY_COLLECTION,
        "attribution_model": ATTRIBUTION_MODEL,
        "selected_account_ids": selected_ids,
        "selected_account_count": len(selected_ids),
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
        "ratios_ready": all(
            totals.get(key) is not None
            for key in ("spend_sar", "orders", "sales_sar")
        ),
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

    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "date_from": dates[0].isoformat(),
        "date_to": dates[-1].isoformat(),
        "business_timezone": BUSINESS_TIMEZONE,
        "totals": totals,
        "daily": daily,
        "accounts": accounts,
        "campaigns": campaigns[offset : offset + limit],
        "campaign_pagination": {
            "page": safe_page,
            "limit": limit,
            "total": total_campaigns,
            "pages": pages,
        },
        "source": source,
        "ai_readiness": readiness,
        "insights": _campaign_insights(
            campaigns,
            totals,
            row_limit_reached=row_limit_reached,
        ),
        "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
        "source_only": True,
        "provider_network_called": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def attach_snapchat_native_selected_read_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get(
        f"/{SNAPCHAT_PROVIDER_ID}/performance-summary",
        name="get_selected_snapchat_performance_summary",
    )
    async def read_selected_snapchat_performance_summary(
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        try:
            return await selected_snapchat_performance_summary(
                db,
                str(owner["id"]),
                from_date=from_date,
                to_date=to_date,
            )
        except SnapchatNativeSyncError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "provider": SNAPCHAT_PROVIDER_ID,
                    "status": "failed",
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    "source_only": True,
                    "accounting_write_reached": False,
                    "qoyod_write_reached": False,
                },
            ) from exc

    @router.get(
        f"/{SNAPCHAT_PROVIDER_ID}/campaign-report",
        name="get_selected_snapchat_campaign_report",
    )
    async def read_selected_snapchat_campaign_report(
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
        campaign_query: str | None = Query(default=None, max_length=120),
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=25, ge=10, le=100),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        try:
            return await selected_snapchat_campaign_report(
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
                    "provider": SNAPCHAT_PROVIDER_ID,
                    "status": "failed",
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    "source_only": True,
                    "accounting_write_reached": False,
                    "qoyod_write_reached": False,
                },
            ) from exc


__all__ = [
    "attach_snapchat_native_selected_read_routes",
    "selected_snapchat_campaign_report",
    "selected_snapchat_performance_summary",
]
