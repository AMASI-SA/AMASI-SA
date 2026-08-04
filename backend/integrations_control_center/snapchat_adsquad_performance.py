"""Read-only Snapchat Ad Squad performance for Ads Manager.

Campaign reporting remains the authoritative account-level source already used by
Mezan.  This module adds a separate Ad Squad projection by reading Campaign
Stats with ``breakdown=adsquad``.  It persists both the Riyadh-day projection
and the selected account's native calendar projection, but neither projection
is accounting eligible and no Snapchat mutation is performed.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from . import snapchat_account_hourly_refresh as hourly
from .snapchat_account_selection import _load_selected_accounts
from .snapchat_account_timezone_manager import (
    SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
    _account_public_row,
    _aggregate_rows,
    _aware_now,
    _combined_request_window,
    _effective_currency,
    _text,
    _valid_timezone_name,
    resolve_account_report_dates,
)
from .snapchat_native_data_common import (
    ATTRIBUTION_MODEL,
    BUSINESS_TIMEZONE,
    MAX_PAGES,
    SNAPCHAT_API_BASE,
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_PERFORMANCE_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatSyncContext,
    _as_number,
    _collection,
    _parse_datetime,
    _safe_next_url,
    _timezone,
    _utcnow,
)
from .snapchat_native_performance_sync import (
    ACTION_REPORT_TIME,
    CONVERSION_SOURCE_TYPES,
    STAT_FIELDS,
    SWIPE_ATTRIBUTION_WINDOW,
    VIEW_ATTRIBUTION_WINDOW,
    _add_to_bucket,
    _computed,
    _finalize_bucket,
    _new_bucket,
)

ADSQUAD_SOURCE_MODE = "snapchat_campaign_stats_adsquad_account_day_v1"
ADSQUAD_REFRESH_STATE_COLLECTION = "mezan_snapchat_adsquad_refresh_state_v1"
ADSQUAD_BREAKDOWN = "adsquad"
ADSQUAD_REFRESH_INTERVAL_SECONDS = 15 * 60
MAX_CAMPAIGNS_PER_ACCOUNT = 250
MAX_REPORT_ROWS = 100_000
MAX_ENTITY_ROWS = 50_000

AccountRefresh = Callable[..., Awaitable[dict[str, Any]]]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


async def _to_list(cursor: Any, length: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=length)
    return [row async for row in cursor]


async def _campaign_entities(
    db: Any,
    user_id: str,
    account_id: str,
) -> tuple[list[dict[str, Any]], bool]:
    cursor = _collection(db, SNAPCHAT_ENTITY_COLLECTION).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": account_id,
            "entity_type": "campaign",
        },
        {
            "_id": 0,
            "external_id": 1,
            "display_name": 1,
            "status": 1,
            "updated_at_provider": 1,
            "last_observed_at": 1,
        },
    )
    rows = await _to_list(cursor, MAX_CAMPAIGNS_PER_ACCOUNT + 1)
    limited = len(rows) > MAX_CAMPAIGNS_PER_ACCOUNT
    rows = rows[:MAX_CAMPAIGNS_PER_ACCOUNT]
    rows.sort(key=lambda row: (
        str(row.get("status") or "").upper() != "ACTIVE",
        _text(row.get("display_name")).casefold(),
        _text(row.get("external_id")),
    ))
    return rows, limited


def extract_adsquad_hour_rows(
    payload: dict[str, Any],
    *,
    campaign_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    wrapped_stats = payload.get("timeseries_stats") or []
    if not isinstance(wrapped_stats, list):
        raise SnapchatNativeSyncError(
            "snapchat_adsquad_stats_payload_invalid",
            "Snapchat returned invalid Ad Squad performance data.",
            status_code=502,
            retryable=True,
        )
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    successful = 0
    for wrapped in wrapped_stats:
        if not isinstance(wrapped, dict):
            continue
        status = str(wrapped.get("sub_request_status") or "SUCCESS").upper()
        if "FAIL" in status or "ERROR" in status:
            errors.append({
                "kind": "adsquad_stats",
                "campaign_id": campaign_id,
                "error": status[:100],
            })
            continue
        successful += 1
        stat = wrapped.get("timeseries_stat", wrapped)
        if not isinstance(stat, dict):
            continue
        breakdown = stat.get("breakdown_stats")
        candidates: list[dict[str, Any]] = []
        if isinstance(breakdown, dict):
            for key in ("adsquad", "ad_squad", "adsquads"):
                value = breakdown.get(key)
                if isinstance(value, list):
                    candidates.extend(item for item in value if isinstance(item, dict))
        if not candidates and stat.get("type") in {"AD_SQUAD", "AD_SQUAD_V2"}:
            candidates = [stat]
        for entity in candidates:
            adsquad_id = _text(entity.get("id"))
            points = entity.get("timeseries")
            if not adsquad_id or not isinstance(points, list):
                continue
            for point in points:
                metrics = point.get("stats") if isinstance(point, dict) else None
                if not isinstance(metrics, dict):
                    continue
                rows.append({
                    "campaign_id": campaign_id,
                    "ad_squad_id": adsquad_id,
                    "start_time": point.get("start_time"),
                    "end_time": point.get("end_time"),
                    "metrics": metrics,
                })
    return rows, errors, successful


async def _fetch_campaign_adsquad_hours(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    *,
    campaign_id: str,
    request_start: datetime,
    request_end: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    url = f"{SNAPCHAT_API_BASE}/campaigns/{campaign_id}/stats"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    params: dict[str, Any] | None = {
        "start_time": request_start.isoformat(timespec="seconds"),
        "end_time": request_end.isoformat(timespec="seconds"),
        "granularity": "HOUR",
        "breakdown": ADSQUAD_BREAKDOWN,
        "fields": ",".join(STAT_FIELDS),
        "limit": 200,
        "omit_empty": "true",
        "conversion_source_types": CONVERSION_SOURCE_TYPES,
        "swipe_up_attribution_window": SWIPE_ATTRIBUTION_WINDOW,
        "view_attribution_window": VIEW_ATTRIBUTION_WINDOW,
        "action_report_time": ACTION_REPORT_TIME,
    }
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    successful = 0
    for _ in range(MAX_PAGES):
        payload = await context.get_json(client, url, headers=headers, params=params)
        page_rows, page_errors, page_success = extract_adsquad_hour_rows(
            payload,
            campaign_id=campaign_id,
        )
        rows.extend(page_rows)
        errors.extend(page_errors)
        successful += page_success
        next_url = _safe_next_url((payload.get("paging") or {}).get("next_link"))
        if not next_url:
            break
        url, params = next_url, None
    if successful == 0 and errors:
        first = errors[0]
        raise SnapchatNativeSyncError(
            "snapchat_adsquad_stats_failed",
            f"Snapchat Ad Squad stats failed for campaign {campaign_id}.",
            status_code=502,
            retryable=True,
            result={"error": first},
        )
    return rows, errors


def _day_buckets(
    rows: list[dict[str, Any]],
    *,
    timezone_name: str,
    start_date: date,
    end_date: date,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    zone = _timezone(timezone_name)
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        point = _parse_datetime(row.get("start_time"))
        if point is None:
            continue
        if point.tzinfo is None:
            point = point.replace(tzinfo=timezone.utc)
        report_date = point.astimezone(zone).date()
        if report_date < start_date or report_date > end_date:
            continue
        campaign_id = _text(row.get("campaign_id"))
        adsquad_id = _text(row.get("ad_squad_id"))
        if not campaign_id or not adsquad_id:
            continue
        metrics = {
            key: _as_number((row.get("metrics") or {}).get(key))
            for key in STAT_FIELDS
        }
        bucket = buckets.setdefault(
            (campaign_id, adsquad_id, report_date.isoformat()),
            _new_bucket(),
        )
        _add_to_bucket(
            bucket,
            metrics,
            provider_start=row.get("start_time"),
            provider_end=row.get("end_time"),
        )
    return buckets


async def _upsert_projection(
    context: SnapchatSyncContext,
    *,
    collection_name: str,
    account: dict[str, Any],
    timezone_name: str,
    stored_granularity: str,
    campaign_id: str,
    adsquad_id: str,
    date_string: str,
    bucket: dict[str, Any],
) -> None:
    metrics = _finalize_bucket(bucket)
    currency = _text(account.get("currency")).upper()
    spend_micro = _as_number(metrics.get("spend"))
    value_micro = _as_number(metrics.get("conversion_purchases_value"))
    purchases = _as_number(metrics.get("conversion_purchases"))
    spend_native = round(float(spend_micro) / 1_000_000, 6) if spend_micro is not None else None
    value_native = round(float(value_micro) / 1_000_000, 6) if value_micro is not None else None
    now_iso = context.now_iso()
    document = {
        "user_id": context.user_id,
        "provider": SNAPCHAT_PROVIDER_ID,
        "ad_account_id": account["ad_account_id"],
        "mezan_integration_account_id": account.get("mezan_integration_account_id"),
        "entity_type": "ad_squad",
        "external_id": adsquad_id,
        "ad_squad_id": adsquad_id,
        "campaign_id": campaign_id,
        "date": date_string,
        "date_timezone": timezone_name,
        "business_timezone": BUSINESS_TIMEZONE,
        "currency": currency or None,
        "account_timezone": _text(account.get("timezone")) or timezone_name,
        "attribution_model": ATTRIBUTION_MODEL,
        "metrics": metrics,
        "purchases": purchases,
        "spend_native": spend_native,
        "spend_sar": await context.to_sar(spend_native, currency),
        "purchase_value_native": value_native,
        "purchase_value_sar": await context.to_sar(value_native, currency),
        "computed": _computed(metrics),
        "conversion_reporting": {
            "metric": "conversion_purchases",
            "source_types": [CONVERSION_SOURCE_TYPES],
            "action_report_time": ACTION_REPORT_TIME,
            "swipe_up_attribution_window": SWIPE_ATTRIBUTION_WINDOW,
            "view_attribution_window": VIEW_ATTRIBUTION_WINDOW,
        },
        "source_mode": ADSQUAD_SOURCE_MODE,
        "provider_breakdown": ADSQUAD_BREAKDOWN,
        "provider_granularity": "HOUR",
        "stored_granularity": stored_granularity,
        "accounting_eligible": False,
        "report_scope": (
            "snapchat_ads_manager_account_timezone"
            if collection_name == SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION
            else "riyadh_business_day_diagnostics"
        ),
        "provider_window_start": bucket.get("provider_start"),
        "provider_window_end": bucket.get("provider_end"),
        "updated_at": now_iso,
    }
    await _collection(context.db, collection_name).update_one(
        {
            "user_id": context.user_id,
            "ad_account_id": account["ad_account_id"],
            "entity_type": "ad_squad",
            "external_id": adsquad_id,
            "date": date_string,
            "attribution_model": ATTRIBUTION_MODEL,
        },
        {"$set": document, "$setOnInsert": {"created_at": now_iso}},
        upsert=True,
    )


async def _recent_refresh(
    db: Any,
    user_id: str,
    account_id: str,
    *,
    now: datetime,
) -> bool:
    row = await _collection(db, ADSQUAD_REFRESH_STATE_COLLECTION).find_one(
        {"user_id": user_id, "ad_account_id": account_id},
    )
    observed = _parse_datetime((row or {}).get("last_success_at"))
    if observed is None:
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return (now - observed.astimezone(timezone.utc)).total_seconds() < ADSQUAD_REFRESH_INTERVAL_SECONDS


async def refresh_snapchat_adsquad_performance(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
    *,
    start_date: date,
    end_date: date,
    now: datetime | None = None,
) -> dict[str, Any]:
    account_id = _text(account.get("ad_account_id"))
    timezone_name = _valid_timezone_name(account.get("timezone"))
    current = _aware_now(now)
    if await _recent_refresh(context.db, context.user_id, account_id, now=current):
        return {
            "source_mode": ADSQUAD_SOURCE_MODE,
            "skipped": True,
            "skip_reason": "fresh_within_15_minutes",
            "rows_saved": 0,
            "provider_calls": 0,
            "source_only": True,
        }
    request = _combined_request_window(
        start_date,
        end_date,
        timezone_name=timezone_name,
        now=current,
        include_current_hour=True,
    )
    if request is None:
        return {
            "source_mode": ADSQUAD_SOURCE_MODE,
            "skipped": True,
            "skip_reason": "empty_request_window",
            "rows_saved": 0,
            "provider_calls": 0,
            "source_only": True,
        }
    campaigns, campaign_limit_reached = await _campaign_entities(
        context.db,
        context.user_id,
        account_id,
    )
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    calls_before = context.provider_calls
    for campaign in campaigns:
        campaign_id = _text(campaign.get("external_id"))
        if not campaign_id:
            continue
        try:
            campaign_rows, campaign_errors = await _fetch_campaign_adsquad_hours(
                context,
                client,
                access_token,
                campaign_id=campaign_id,
                request_start=request["provider_start"],
                request_end=request["provider_end"],
            )
            rows.extend(campaign_rows)
            errors.extend(campaign_errors)
        except SnapchatNativeSyncError as exc:
            if exc.code == "snapchat_needs_reauth":
                raise
            errors.append({
                "kind": "adsquad_stats",
                "campaign_id": campaign_id,
                "code": exc.code,
                "message": exc.message[:300],
                "retryable": bool(exc.retryable),
            })
    business = _day_buckets(
        rows,
        timezone_name=BUSINESS_TIMEZONE,
        start_date=start_date,
        end_date=end_date,
    )
    local = _day_buckets(
        rows,
        timezone_name=timezone_name,
        start_date=request["account_local_from"],
        end_date=request["account_local_to"],
    )
    saved = 0
    for (campaign_id, adsquad_id, date_string), bucket in sorted(business.items()):
        await _upsert_projection(
            context,
            collection_name=SNAPCHAT_PERFORMANCE_COLLECTION,
            account=account,
            timezone_name=BUSINESS_TIMEZONE,
            stored_granularity="RIYADH_DAY",
            campaign_id=campaign_id,
            adsquad_id=adsquad_id,
            date_string=date_string,
            bucket=bucket,
        )
        saved += 1
    for (campaign_id, adsquad_id, date_string), bucket in sorted(local.items()):
        await _upsert_projection(
            context,
            collection_name=SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
            account=account,
            timezone_name=timezone_name,
            stored_granularity="ACCOUNT_LOCAL_DAY",
            campaign_id=campaign_id,
            adsquad_id=adsquad_id,
            date_string=date_string,
            bucket=bucket,
        )
        saved += 1
    now_iso = context.now_iso()
    await _collection(context.db, ADSQUAD_REFRESH_STATE_COLLECTION).update_one(
        {"user_id": context.user_id, "ad_account_id": account_id},
        {
            "$set": {
                "user_id": context.user_id,
                "ad_account_id": account_id,
                "last_success_at": now_iso,
                "rows_saved": saved,
                "campaigns_requested": len(campaigns),
                "campaign_limit_reached": campaign_limit_reached,
                "errors_count": len(errors),
                "source_mode": ADSQUAD_SOURCE_MODE,
                "updated_at": now_iso,
            },
            "$setOnInsert": {"created_at": now_iso},
        },
        upsert=True,
    )
    return {
        "source_mode": ADSQUAD_SOURCE_MODE,
        "skipped": False,
        "rows_saved": saved,
        "riyadh_rows_saved": len(business),
        "account_local_rows_saved": len(local),
        "campaigns_requested": len(campaigns),
        "campaign_limit_reached": campaign_limit_reached,
        "errors_count": len(errors),
        "errors": errors[:50],
        "provider_calls": context.provider_calls - calls_before,
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def install_snapchat_adsquad_performance_refresh() -> None:
    current = hourly.refresh_snapchat_account_hours
    if getattr(current, "_mezan_adsquad_performance_refresh", False):
        return

    async def wrapped(
        context: SnapchatSyncContext,
        client: httpx.AsyncClient,
        access_token: str,
        account: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        output = dict(await current(
            context,
            client,
            access_token,
            account,
            *args,
            **kwargs,
        ) or {})
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")
        if not isinstance(start_date, date) or not isinstance(end_date, date):
            return output
        try:
            output["ad_squad_performance"] = await refresh_snapchat_adsquad_performance(
                context,
                client,
                access_token,
                account,
                start_date=start_date,
                end_date=end_date,
                now=kwargs.get("now"),
            )
        except SnapchatNativeSyncError as exc:
            if exc.code == "snapchat_needs_reauth":
                raise
            output["ad_squad_performance"] = {
                "source_mode": ADSQUAD_SOURCE_MODE,
                "skipped": False,
                "rows_saved": 0,
                "errors_count": 1,
                "errors": [{
                    "code": exc.code,
                    "message": exc.message[:300],
                    "retryable": bool(exc.retryable),
                }],
                "source_only": True,
            }
        return output

    wrapped._mezan_adsquad_performance_refresh = True  # type: ignore[attr-defined]
    wrapped._mezan_adsquad_performance_base = current  # type: ignore[attr-defined]
    hourly.refresh_snapchat_account_hours = wrapped


async def build_account_timezone_adsquad_report(
    db: Any,
    user_id: str,
    *,
    account_id: str | None,
    from_date: str | None,
    to_date: str | None,
    query: str | None,
    page: int,
    limit: int,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    current = _aware_now(now())
    accounts = await _load_selected_accounts(db, user_id)
    if not accounts:
        raise SnapchatNativeSyncError(
            "snapchat_accounts_not_selected",
            "لا توجد حسابات Snapchat محددة داخل ميزان.",
            status_code=409,
        )
    requested_id = _text(account_id)
    selected = next(
        (row for row in accounts if _text(row.get("ad_account_id")) == requested_id),
        None,
    ) if requested_id else accounts[0]
    if selected is None:
        raise SnapchatNativeSyncError(
            "snapchat_account_not_selected",
            "الحساب الإعلاني المطلوب غير محدد داخل ميزان.",
            status_code=404,
        )
    account = _account_public_row(selected, now=current)
    timezone_name = account["timezone"]
    dates = resolve_account_report_dates(
        from_date,
        to_date,
        timezone_name=timezone_name,
        now=current,
    )
    date_query = {"$gte": dates[0].isoformat(), "$lte": dates[-1].isoformat()}
    performance_cursor = _collection(
        db, SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION
    ).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": account["account_id"],
            "entity_type": "ad_squad",
            "date": date_query,
            "date_timezone": timezone_name,
        },
        {"_id": 0},
    )
    performance_rows = await _to_list(performance_cursor, MAX_REPORT_ROWS)
    row_limit_reached = len(performance_rows) >= MAX_REPORT_ROWS
    entity_cursor = _collection(db, SNAPCHAT_ENTITY_COLLECTION).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": account["account_id"],
            "entity_type": {"$in": ["campaign", "ad_squad"]},
        },
        {"_id": 0},
    )
    entity_rows = await _to_list(entity_cursor, MAX_ENTITY_ROWS)
    entity_limit_reached = len(entity_rows) >= MAX_ENTITY_ROWS
    campaigns = {
        _text(row.get("external_id")): row
        for row in entity_rows
        if row.get("entity_type") == "campaign" and _text(row.get("external_id"))
    }
    squads = {
        _text(row.get("external_id")): row
        for row in entity_rows
        if row.get("entity_type") == "ad_squad" and _text(row.get("external_id"))
    }
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in performance_rows:
        adsquad_id = _text(row.get("ad_squad_id") or row.get("external_id"))
        if adsquad_id:
            groups[adsquad_id].append(row)
    requested_days = len(dates)
    from ads_manager.account_cost_settings import list_account_cost_settings
    settings = await list_account_cost_settings(db, user_id)
    setting = next(
        (
            item for item in settings.get("items") or []
            if _text(item.get("external_account_id")) == account["account_id"]
        ),
        None,
    )
    currency, rate = _effective_currency(selected, setting)
    rows: list[dict[str, Any]] = []
    identity_matches = 0
    for adsquad_id, facts in groups.items():
        entity = squads.get(adsquad_id, {})
        if entity:
            identity_matches += 1
        campaign_id = _text(entity.get("campaign_id")) or _text(facts[0].get("campaign_id"))
        campaign = campaigns.get(campaign_id, {})
        metrics = _aggregate_rows(facts, requested_days=requested_days)
        rows.append({
            "account_id": account["account_id"],
            "account_name": account["account_name"],
            "ad_squad_id": adsquad_id,
            "ad_squad_name": _text(entity.get("display_name")) or adsquad_id,
            "campaign_id": campaign_id or None,
            "campaign_name": _text(campaign.get("display_name")) or campaign_id or "حملة غير معروفة",
            "status": entity.get("status") or "unknown",
            "delivery_status": entity.get("delivery_status"),
            "optimization_goal": entity.get("optimization_goal"),
            "billing_event": entity.get("billing_event"),
            "bid_strategy": entity.get("bid_strategy"),
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
            "display_currency": currency,
            "exchange_rate_to_sar": round(rate, 6),
            "result_source": "platform",
            "commercial_results_scope": "snapchat_ad_squad_conversion_reporting",
            **metrics,
        })
    search = _text(query).casefold()[:120]
    if search:
        rows = [
            item for item in rows
            if search in " ".join([
                _text(item.get("ad_squad_name")),
                _text(item.get("ad_squad_id")),
                _text(item.get("campaign_name")),
                _text(item.get("campaign_id")),
            ]).casefold()
        ]
    rows.sort(key=lambda item: (
        -(float(item.get("spend_sar") or 0)),
        _text(item.get("ad_squad_name")).casefold(),
    ))
    total = len(rows)
    pages = (total + limit - 1) // limit if total else 0
    offset = (page - 1) * limit
    page_rows = rows[offset:offset + limit]
    daily_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in performance_rows:
        daily_groups[_text(row.get("date"))].append(row)
    daily = [
        {
            "date": day.isoformat(),
            **_aggregate_rows(daily_groups.get(day.isoformat(), []), requested_days=1),
        }
        for day in dates
    ]
    totals = _aggregate_rows(performance_rows, requested_days=requested_days)
    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "entity_level": "ad_squad",
        "date_from": dates[0].isoformat(),
        "date_to": dates[-1].isoformat(),
        "account_timezone": timezone_name,
        "selected_account_id": account["account_id"],
        "selected_account": account,
        "result_source": "platform",
        "supported_result_sources": ["platform"],
        "totals": totals,
        "daily": daily,
        "ad_squads": page_rows,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "pages": pages,
        },
        "source": {
            "performance_collection": SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
            "entity_collection": SNAPCHAT_ENTITY_COLLECTION,
            "source_mode": ADSQUAD_SOURCE_MODE,
            "performance_rows": len(performance_rows),
            "entity_rows": len(entity_rows),
            "identity_matches": identity_matches,
            "identity_coverage_pct": (
                round(identity_matches / len(groups) * 100, 2) if groups else None
            ),
            "row_limit_reached": row_limit_reached,
            "entity_limit_reached": entity_limit_reached,
            "commercial_results_source": "snapchat_conversion_reporting",
            "salla_results_supported": False,
        },
        "policy": {"mode": "observe_only", "mutations_allowed": False},
        "source_only": True,
        "provider_read_reached": False,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def attach_snapchat_adsquad_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get(
        f"/{SNAPCHAT_PROVIDER_ID}/ad-squad-report",
        name="get_snapchat_ad_squad_report",
    )
    async def ad_squad_report(
        account_id: str | None = Query(default=None, max_length=120),
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
        query: str | None = Query(default=None, max_length=120),
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=25, ge=10, le=100),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        try:
            return await build_account_timezone_adsquad_report(
                db,
                str(owner["id"]),
                account_id=account_id,
                from_date=from_date,
                to_date=to_date,
                query=query,
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
    "ADSQUAD_BREAKDOWN",
    "ADSQUAD_REFRESH_INTERVAL_SECONDS",
    "ADSQUAD_SOURCE_MODE",
    "attach_snapchat_adsquad_routes",
    "build_account_timezone_adsquad_report",
    "extract_adsquad_hour_rows",
    "install_snapchat_adsquad_performance_refresh",
    "refresh_snapchat_adsquad_performance",
]
