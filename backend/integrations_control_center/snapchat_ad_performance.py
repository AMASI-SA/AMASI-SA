"""Read-only Snapchat Ad performance for Ads Manager.

Ad-level facts are captured from the Campaign Stats endpoint with
``breakdown=ad`` and HOUR granularity. The same account-local date semantics
used by Campaign and Ad Squad reports are preserved. These rows are diagnostic
and Ads-Manager-only: they are never accounting eligible and never write to
Snapchat.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from . import snapchat_account_hourly_refresh as hourly
from . import snapchat_adsquad_performance as adsquad_report
from .snapchat_account_selection import _load_selected_accounts
from .snapchat_freshness_impl_v6 import (
    ADS_MANAGER_ACTION_REPORT_TIME,
    ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
    ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES,
    ADS_MANAGER_SWIPE_ATTRIBUTION_WINDOW,
    ADS_MANAGER_VIEW_ATTRIBUTION_WINDOW,
    ads_manager_source_mode,
    normalize_ads_manager_action_report_time,
)
from .snapchat_active_campaign_filtering import (
    aggregate_entity_rows,
    is_active_provider_status,
    normalize_entity_sort,
    sort_entity_rows,
)
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
from .snapchat_adsquad_performance import _campaign_entities, _number, _to_list
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
    CONVERSION_SOURCE_TYPES,
    STAT_FIELDS,
    SWIPE_ATTRIBUTION_WINDOW,
    VIEW_ATTRIBUTION_WINDOW,
    _add_to_bucket,
    _computed,
    _finalize_bucket,
    _new_bucket,
)

def ad_source_mode(action_report_time: Any) -> str:
    return f"{ads_manager_source_mode(action_report_time)}:ad_day_v3"


AD_SOURCE_MODE = ad_source_mode(ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME)
AD_REFRESH_SOURCE_MODE = "snapchat_ads_manager_dual_attribution_ad_v1"
AD_REFRESH_STATE_COLLECTION = "mezan_snapchat_ad_refresh_state_v1"
AD_BREAKDOWN = "ad"
AD_REFRESH_INTERVAL_SECONDS = 15 * 60
MAX_REPORT_ROWS = 150_000
MAX_ENTITY_ROWS = 75_000


def extract_ad_hour_rows(
    payload: dict[str, Any],
    *,
    campaign_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Extract HOUR Ad rows from a Campaign Stats breakdown response."""
    wrapped_stats = payload.get("timeseries_stats") or []
    if not isinstance(wrapped_stats, list):
        raise SnapchatNativeSyncError(
            "snapchat_ad_stats_payload_invalid",
            "Snapchat returned invalid Ad performance data.",
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
                "kind": "ad_stats",
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
            for key in ("ad", "ads"):
                value = breakdown.get(key)
                if isinstance(value, list):
                    candidates.extend(
                        item for item in value if isinstance(item, dict)
                    )
        if not candidates and str(stat.get("type") or "").upper() == "AD":
            candidates = [stat]
        for entity in candidates:
            ad_id = _text(entity.get("id"))
            points = entity.get("timeseries")
            if not ad_id or not isinstance(points, list):
                continue
            for point in points:
                metrics = point.get("stats") if isinstance(point, dict) else None
                if not isinstance(metrics, dict):
                    continue
                rows.append({
                    "campaign_id": campaign_id,
                    "ad_id": ad_id,
                    "start_time": point.get("start_time"),
                    "end_time": point.get("end_time"),
                    "metrics": metrics,
                })
    return rows, errors, successful


async def _fetch_campaign_ad_hours(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    *,
    campaign_id: str,
    request_start: datetime,
    request_end: datetime,
    action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
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
        "breakdown": AD_BREAKDOWN,
        "fields": ",".join(STAT_FIELDS),
        "limit": 200,
        "omit_empty": "true",
        "conversion_source_types": CONVERSION_SOURCE_TYPES,
        "swipe_up_attribution_window": ADS_MANAGER_SWIPE_ATTRIBUTION_WINDOW,
        "view_attribution_window": ADS_MANAGER_VIEW_ATTRIBUTION_WINDOW,
        "action_report_time": normalize_ads_manager_action_report_time(action_report_time),
    }
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    successful = 0
    for _ in range(MAX_PAGES):
        payload = await context.get_json(
            client,
            url,
            headers=headers,
            params=params,
        )
        page_rows, page_errors, page_success = extract_ad_hour_rows(
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
        raise SnapchatNativeSyncError(
            "snapchat_ad_stats_failed",
            f"Snapchat Ad stats failed for campaign {campaign_id}.",
            status_code=502,
            retryable=True,
            result={"error": errors[0]},
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
        ad_id = _text(row.get("ad_id"))
        if not campaign_id or not ad_id:
            continue
        metrics = {
            key: _as_number((row.get("metrics") or {}).get(key))
            for key in STAT_FIELDS
        }
        bucket = buckets.setdefault(
            (campaign_id, ad_id, report_date.isoformat()),
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
    ad_id: str,
    date_string: str,
    bucket: dict[str, Any],
    action_report_time: str,
) -> None:
    metrics = _finalize_bucket(bucket)
    currency = _text(account.get("currency")).upper()
    spend_micro = _as_number(metrics.get("spend"))
    value_micro = _as_number(metrics.get("conversion_purchases_value"))
    purchases = _as_number(metrics.get("conversion_purchases"))
    spend_native = (
        round(float(spend_micro) / 1_000_000, 6)
        if spend_micro is not None else None
    )
    value_native = (
        round(float(value_micro) / 1_000_000, 6)
        if value_micro is not None else None
    )
    now_iso = context.now_iso()
    document = {
        "user_id": context.user_id,
        "provider": SNAPCHAT_PROVIDER_ID,
        "ad_account_id": account["ad_account_id"],
        "mezan_integration_account_id": account.get(
            "mezan_integration_account_id"
        ),
        "entity_type": "ad",
        "external_id": ad_id,
        "ad_id": ad_id,
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
            "action_report_time": action_report_time,
            "swipe_up_attribution_window": ADS_MANAGER_SWIPE_ATTRIBUTION_WINDOW,
            "view_attribution_window": ADS_MANAGER_VIEW_ATTRIBUTION_WINDOW,
        },
        "action_report_time": action_report_time,
        "source_mode": ad_source_mode(action_report_time),
        "provider_breakdown": AD_BREAKDOWN,
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
    identity = {
        "user_id": context.user_id,
        "ad_account_id": account["ad_account_id"],
        "entity_type": "ad",
        "external_id": ad_id,
        "date": date_string,
        "attribution_model": ATTRIBUTION_MODEL,
    }
    if collection_name == SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION:
        identity["action_report_time"] = action_report_time

    await _collection(context.db, collection_name).update_one(
        identity,
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
    row = await _collection(db, AD_REFRESH_STATE_COLLECTION).find_one(
        {"user_id": user_id, "ad_account_id": account_id},
    )
    observed = _parse_datetime((row or {}).get("last_success_at"))
    if observed is None:
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return (
        _text((row or {}).get("source_mode")) == AD_REFRESH_SOURCE_MODE
        and (now - observed.astimezone(timezone.utc)).total_seconds()
        < AD_REFRESH_INTERVAL_SECONDS
    )


async def refresh_snapchat_ad_performance(
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
    if not account_id:
        raise SnapchatNativeSyncError(
            "snapchat_account_id_missing",
            "Selected Snapchat account is missing its ad account ID.",
            status_code=409,
            retryable=False,
        )
    timezone_name = _valid_timezone_name(account.get("timezone"))
    current = _aware_now(now)
    if await _recent_refresh(
        context.db,
        context.user_id,
        account_id,
        now=current,
    ):
        return {
            "source_mode": AD_SOURCE_MODE,
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
            "source_mode": AD_SOURCE_MODE,
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
    rows_by_mode: dict[str, list[dict[str, Any]]] = {
        mode: [] for mode in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES
    }
    errors: list[dict[str, Any]] = []
    calls_before = context.provider_calls
    for campaign in campaigns:
        campaign_id = _text(campaign.get("external_id"))
        if not campaign_id:
            continue
        for action_report_time in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES:
            try:
                campaign_rows, campaign_errors = await _fetch_campaign_ad_hours(
                    context,
                    client,
                    access_token,
                    campaign_id=campaign_id,
                    request_start=request["provider_start"],
                    request_end=request["provider_end"],
                    action_report_time=action_report_time,
                )
                rows_by_mode[action_report_time].extend(campaign_rows)
                errors.extend({
                    **error,
                    "action_report_time": action_report_time,
                } for error in campaign_errors)
            except SnapchatNativeSyncError as exc:
                if exc.code == "snapchat_needs_reauth":
                    raise
                errors.append({
                    "kind": "ad_stats",
                    "campaign_id": campaign_id,
                    "action_report_time": action_report_time,
                    "code": exc.code,
                    "message": exc.message[:300],
                    "retryable": bool(exc.retryable),
                })
    business = _day_buckets(
        rows_by_mode[ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME],
        timezone_name=BUSINESS_TIMEZONE,
        start_date=start_date,
        end_date=end_date,
    )
    local_by_mode = {
        mode: _day_buckets(
            rows_by_mode[mode],
            timezone_name=timezone_name,
            start_date=request["account_local_from"],
            end_date=request["account_local_to"],
        )
        for mode in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES
    }
    saved = 0
    for (campaign_id, ad_id, date_string), bucket in sorted(business.items()):
        await _upsert_projection(
            context,
            collection_name=SNAPCHAT_PERFORMANCE_COLLECTION,
            account=account,
            timezone_name=BUSINESS_TIMEZONE,
            stored_granularity="RIYADH_DAY",
            campaign_id=campaign_id,
            ad_id=ad_id,
            date_string=date_string,
            bucket=bucket,
            action_report_time=ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
        )
        saved += 1
    account_local_saved = 0
    for action_report_time, local in local_by_mode.items():
        for (campaign_id, ad_id, date_string), bucket in sorted(local.items()):
            await _upsert_projection(
                context,
                collection_name=SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
                account=account,
                timezone_name=timezone_name,
                stored_granularity="ACCOUNT_LOCAL_DAY",
                campaign_id=campaign_id,
                ad_id=ad_id,
                date_string=date_string,
                bucket=bucket,
                action_report_time=action_report_time,
            )
            saved += 1
            account_local_saved += 1
    now_iso = context.now_iso()
    await _collection(context.db, AD_REFRESH_STATE_COLLECTION).update_one(
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
                "source_mode": AD_REFRESH_SOURCE_MODE,
                "updated_at": now_iso,
            },
            "$setOnInsert": {"created_at": now_iso},
        },
        upsert=True,
    )
    return {
        "source_mode": AD_REFRESH_SOURCE_MODE,
        "supported_action_report_times": list(ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES),
        "skipped": False,
        "rows_saved": saved,
        "riyadh_rows_saved": len(business),
        "account_local_rows_saved": account_local_saved,
        "campaigns_requested": len(campaigns),
        "campaign_limit_reached": campaign_limit_reached,
        "errors_count": len(errors),
        "errors": errors[:50],
        "provider_calls": context.provider_calls - calls_before,
        "source_only": True,
        "provider_write_reached": False,
        "ad_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def install_snapchat_ad_performance_refresh() -> None:
    current = hourly.refresh_snapchat_account_hours
    if getattr(current, "_mezan_ad_performance_refresh", False):
        return

    async def wrapped(
        context: SnapchatSyncContext,
        client: httpx.AsyncClient,
        access_token: str,
        account: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        output = dict(
            await current(
                context,
                client,
                access_token,
                account,
                *args,
                **kwargs,
            ) or {}
        )
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")
        if not isinstance(start_date, date) and len(args) >= 1:
            start_date = args[0]
        if not isinstance(end_date, date) and len(args) >= 2:
            end_date = args[1]
        if not isinstance(start_date, date) or not isinstance(end_date, date):
            return output
        try:
            output["ad_performance"] = await refresh_snapchat_ad_performance(
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
            output["ad_performance"] = {
                "source_mode": AD_SOURCE_MODE,
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

    wrapped._mezan_ad_performance_refresh = True  # type: ignore[attr-defined]
    wrapped._mezan_ad_performance_base = current  # type: ignore[attr-defined]
    hourly.refresh_snapchat_account_hours = wrapped


def _entity_maps(entity_rows: list[dict[str, Any]]) -> tuple[dict, dict, dict, dict]:
    campaigns: dict[str, dict[str, Any]] = {}
    squads: dict[str, dict[str, Any]] = {}
    ads: dict[str, dict[str, Any]] = {}
    creatives: dict[str, dict[str, Any]] = {}
    for row in entity_rows:
        external_id = _text(row.get("external_id"))
        if not external_id:
            continue
        entity_type = _text(row.get("entity_type"))
        if entity_type == "campaign":
            campaigns[external_id] = row
        elif entity_type == "ad_squad":
            squads[external_id] = row
        elif entity_type == "ad":
            ads[external_id] = row
        elif entity_type == "creative":
            creatives[external_id] = row
    return campaigns, squads, ads, creatives


def _creative_summary(entity: dict[str, Any] | None) -> dict[str, Any]:
    row = entity or {}
    snapshot = row.get("provider_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    creative_type = (
        _text(snapshot.get("type"))
        or _text(snapshot.get("creative_type"))
        or None
    )
    media_id = (
        _text(snapshot.get("top_snap_media_id"))
        or _text(snapshot.get("media_id"))
        or None
    )
    destination = None
    web_view = snapshot.get("web_view_properties")
    if isinstance(web_view, dict):
        destination = _text(web_view.get("url")) or None
    return {
        "creative_id": _text(row.get("external_id")) or None,
        "creative_name": _text(row.get("display_name")) or None,
        "creative_type": creative_type,
        "media_id": media_id,
        "destination_url": destination,
    }


def _delivery_for_ad(
    ad: dict[str, Any],
    parent: dict[str, Any] | None,
) -> dict[str, Any]:
    configured = _text(ad.get("status") or "UNKNOWN").upper()
    review = _text(ad.get("review_status") or "").upper()
    if configured not in {"ACTIVE", "ENABLED"}:
        return {
            "configured_status": configured,
            "delivery_state": "NOT_DELIVERING",
            "delivery_status": "غير نشط — الإعلان متوقف",
            "delivery_reason_code": "AD_CONFIGURED_PAUSED",
            "deliverable": False,
        }
    if review in {"REJECTED", "DISAPPROVED"}:
        return {
            "configured_status": configured,
            "delivery_state": "NOT_DELIVERING",
            "delivery_status": "لا تسليم — الإعلان مرفوض",
            "delivery_reason_code": "AD_REVIEW_REJECTED",
            "deliverable": False,
        }
    if review in {"PENDING", "IN_REVIEW", "PENDING_REVIEW"}:
        return {
            "configured_status": configured,
            "delivery_state": "PENDING",
            "delivery_status": "قيد المراجعة",
            "delivery_reason_code": "AD_REVIEW_PENDING",
            "deliverable": False,
        }
    parent_row = parent or {}
    parent_state = _text(parent_row.get("delivery_state"))
    parent_label = _text(
        parent_row.get("delivery_status")
        or parent_row.get("delivery_label")
    )
    if parent_state and parent_state != "DELIVERING":
        return {
            "configured_status": configured,
            "delivery_state": parent_state,
            "delivery_status": parent_label or "لا تسليم — المجموعة الإعلانية لا تسلّم",
            "delivery_reason_code": _text(
                parent_row.get("delivery_reason_code")
            ) or "PARENT_AD_SQUAD_NOT_DELIVERING",
            "deliverable": False,
            "delivery_inherited_from_ad_squad": True,
        }
    return {
        "configured_status": configured,
        "delivery_state": "DELIVERING",
        "delivery_status": "يتم التسليم",
        "delivery_reason_code": "DELIVERING",
        "deliverable": True,
    }


async def build_account_timezone_ad_report(
    db: Any,
    user_id: str,
    *,
    account_id: str | None,
    from_date: str | None,
    to_date: str | None,
    query: str | None,
    page: int,
    limit: int,
    active_campaigns_only: bool = False,
    sort_by: str = "orders",
    action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    current = _aware_now(now())
    action_report_time = normalize_ads_manager_action_report_time(action_report_time)
    accounts = await _load_selected_accounts(db, user_id)
    if not accounts:
        raise SnapchatNativeSyncError(
            "snapchat_accounts_not_selected",
            "لا توجد حسابات Snapchat محددة داخل ميزان.",
            status_code=409,
        )
    requested_id = _text(account_id)
    selected = next(
        (
            row for row in accounts
            if _text(row.get("ad_account_id")) == requested_id
        ),
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
    date_query = {
        "$gte": dates[0].isoformat(),
        "$lte": dates[-1].isoformat(),
    }
    performance_cursor = _collection(
        db,
        SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
    ).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": account["account_id"],
            "entity_type": "ad",
            "date": date_query,
            "date_timezone": timezone_name,
            "source_mode": ad_source_mode(action_report_time),
            "action_report_time": action_report_time,
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
            "entity_type": {"$in": ["campaign", "ad_squad", "ad", "creative"]},
        },
        {"_id": 0},
    )
    entity_rows = await _to_list(entity_cursor, MAX_ENTITY_ROWS)
    entity_limit_reached = len(entity_rows) >= MAX_ENTITY_ROWS
    campaigns, squads, ads, creatives = _entity_maps(entity_rows)

    parent_report = await adsquad_report.build_account_timezone_adsquad_report(
        db,
        user_id,
        account_id=account["account_id"],
        from_date=dates[0].isoformat(),
        to_date=dates[-1].isoformat(),
        query=None,
        page=1,
        limit=100,
        active_campaigns_only=False,
        sort_by="spend",
        action_report_time=action_report_time,
        now=lambda: current,
    )
    parent_rows = {
        _text(row.get("ad_squad_id")): row
        for row in parent_report.get("ad_squads") or []
        if isinstance(row, dict) and _text(row.get("ad_squad_id"))
    }

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in performance_rows:
        ad_id = _text(row.get("ad_id") or row.get("external_id"))
        if ad_id:
            groups[ad_id].append(row)
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
    all_ad_ids = sorted(set(groups) | set(ads))
    for ad_id in all_ad_ids:
        facts = groups.get(ad_id, [])
        entity = ads.get(ad_id, {})
        if entity:
            identity_matches += 1
        ad_squad_id = _text(entity.get("ad_squad_id"))
        campaign_id = _text(entity.get("campaign_id"))
        if not campaign_id and facts:
            campaign_id = _text(facts[0].get("campaign_id"))
        squad = squads.get(ad_squad_id, {})
        if not campaign_id:
            campaign_id = _text(squad.get("campaign_id"))
        campaign = campaigns.get(campaign_id, {})
        parent = parent_rows.get(ad_squad_id)
        creative_id = _text(entity.get("creative_id"))
        metrics = _aggregate_rows(facts, requested_days=requested_days)
        delivery = _delivery_for_ad(entity, parent)
        rows.append({
            "account_id": account["account_id"],
            "account_name": account["account_name"],
            "ad_id": ad_id,
            "ad_name": _text(entity.get("display_name")) or ad_id,
            "ad_squad_id": ad_squad_id or None,
            "ad_squad_name": _text(squad.get("display_name")) or ad_squad_id or "مجموعة غير معروفة",
            "campaign_id": campaign_id or None,
            "campaign_name": _text(campaign.get("display_name")) or campaign_id or "حملة غير معروفة",
            "campaign_status": campaign.get("status") or "unknown",
            "campaign_active": is_active_provider_status(campaign.get("status")),
            "ad_squad_status": squad.get("status") or "unknown",
            "status": delivery["configured_status"],
            "review_status": entity.get("review_status"),
            "created_at_provider": entity.get("created_at_provider"),
            "updated_at_provider": entity.get("updated_at_provider"),
            "display_currency": currency,
            "exchange_rate_to_sar": round(rate, 6),
            "result_source": "platform",
            "commercial_results_scope": (
                f"snapchat_ad_{action_report_time}_reporting"
            ),
            **_creative_summary(creatives.get(creative_id)),
            **delivery,
            **metrics,
        })
    search = _text(query).casefold()[:120]
    if search:
        rows = [
            item for item in rows
            if search in " ".join([
                _text(item.get("ad_name")),
                _text(item.get("ad_id")),
                _text(item.get("ad_squad_name")),
                _text(item.get("ad_squad_id")),
                _text(item.get("campaign_name")),
                _text(item.get("campaign_id")),
                _text(item.get("creative_name")),
                _text(item.get("creative_id")),
            ]).casefold()
        ]
    if active_campaigns_only:
        rows = [item for item in rows if item.get("campaign_active") is True]
    sort_mode = normalize_entity_sort(sort_by)
    rows = sort_entity_rows(
        rows,
        sort_mode,
        name_field="ad_name",
        status_field="status",
    )
    filtered_totals = aggregate_entity_rows(rows)
    total = len(rows)
    pages = (total + limit - 1) // limit if total else 0
    offset = (page - 1) * limit
    page_rows = rows[offset:offset + limit]
    totals = filtered_totals
    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "entity_level": "ad",
        "date_from": dates[0].isoformat(),
        "date_to": dates[-1].isoformat(),
        "account_timezone": timezone_name,
        "selected_account_id": account["account_id"],
        "selected_account": account,
        "result_source": "platform",
        "action_report_time": action_report_time,
        "supported_action_report_times": list(ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES),
        "supported_result_sources": ["platform"],
        "active_campaigns_only": bool(active_campaigns_only),
        "sort_by": sort_mode,
        "totals": totals,
        "ads": page_rows,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "pages": pages,
        },
        "source": {
            "performance_collection": SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
            "entity_collection": SNAPCHAT_ENTITY_COLLECTION,
            "source_mode": ad_source_mode(action_report_time),
            "performance_rows": len(performance_rows),
            "entity_rows": len(entity_rows),
            "ad_entities": len(ads),
            "creative_entities": len(creatives),
            "ads_returned_before_pagination": total,
            "identity_matches": identity_matches,
            "identity_coverage_pct": (
                round(identity_matches / len(all_ad_ids) * 100, 2)
                if all_ad_ids else None
            ),
            "row_limit_reached": row_limit_reached,
            "entity_limit_reached": entity_limit_reached,
            "commercial_results_source": f"snapchat_ads_manager_{action_report_time}_reporting",
            "action_report_time": action_report_time,
            "salla_results_supported": False,
        },
        "policy": {"mode": "observe_only", "mutations_allowed": False},
        "source_only": True,
        "provider_read_reached": False,
        "provider_write_reached": False,
        "ad_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def attach_snapchat_ad_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get(
        f"/{SNAPCHAT_PROVIDER_ID}/ad-report",
        name="get_snapchat_ad_report",
    )
    async def ad_report(
        account_id: str | None = Query(default=None, max_length=120),
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
        query: str | None = Query(default=None, max_length=120),
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=25, ge=10, le=100),
        active_campaigns_only: bool = Query(default=True),
        sort_by: str = Query(default="orders", pattern="^(orders|spend|newest|active)$"),
        action_report_time: str = Query(default=ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME, pattern="^(conversion|impression)$"),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        try:
            return await build_account_timezone_ad_report(
                db,
                str(owner["id"]),
                account_id=account_id,
                from_date=from_date,
                to_date=to_date,
                query=query,
                page=page,
                limit=limit,
                active_campaigns_only=active_campaigns_only,
                sort_by=sort_by,
                action_report_time=action_report_time,
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
                    "ad_write_reached": False,
                    "accounting_write_reached": False,
                    "qoyod_write_reached": False,
                },
            ) from exc


__all__ = [
    "AD_BREAKDOWN",
    "AD_REFRESH_INTERVAL_SECONDS",
    "AD_SOURCE_MODE",
    "attach_snapchat_ad_routes",
    "build_account_timezone_ad_report",
    "extract_ad_hour_rows",
    "install_snapchat_ad_performance_refresh",
    "refresh_snapchat_ad_performance",
]
