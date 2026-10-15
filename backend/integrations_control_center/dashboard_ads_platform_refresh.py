"""Bounded read-only refresh for the four-platform Dashboard spend card."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .ads_platform_hourly import (
    ensure_platform_hourly_indexes,
    upsert_platform_hour,
)
from .google_ads_reporting import (
    GOOGLE_ADS_PROVIDER_ID,
    GoogleAdsReportingError,
    google_ads_reporting_enabled,
    run_google_ads_reporting_sync,
)
from .google_oauth_security import google_oauth_configured
from . import meta_native_reporting as meta
from .meta_oauth_security import META_PROVIDER_ID, meta_oauth_configured
from . import tiktok_native_reporting as tiktok
from .tiktok_oauth_security import TIKTOK_PROVIDER_ID, tiktok_oauth_configured
from .snapchat_native_data_common import SNAPCHAT_PROVIDER_ID

FOUR_PLATFORM_KEYS = ("snapchat", "meta", "tiktok", "google")
MAX_REFRESH_DAYS = 31


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).astimezone(timezone.utc).isoformat()


def _safe_timezone(value: Any) -> str:
    name = str(value or "Asia/Riyadh").strip() or "Asia/Riyadh"
    try:
        ZoneInfo(name)
        return name
    except Exception:  # noqa: BLE001
        return "Asia/Riyadh"


def _meta_hour(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        hour = int(text[:2])
    except (TypeError, ValueError):
        return None
    return hour if 0 <= hour <= 23 else None


def _tiktok_hour(value: Any) -> tuple[date, int] | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return parsed.date(), parsed.hour


async def _refresh_meta_hourly(db: Any, user_id: str, day: date) -> dict[str, Any]:
    if not meta_oauth_configured() or not meta.meta_reporting_enabled():
        return {"provider": META_PROVIDER_ID, "status": "skipped", "reason": "disabled"}
    observed_at = _iso()
    await ensure_platform_hourly_indexes(db)
    access_token = await meta._credential(db, user_id, _utcnow())
    accounts = await meta._accounts(db, user_id)
    saved = 0
    errors: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=35.0) as client:
        for account in accounts:
            account_id = str(account.get("ad_account_id") or "").strip()
            timezone_name = _safe_timezone(account.get("timezone"))
            currency = str(account.get("currency") or "").strip().upper() or None
            try:
                response = await client.get(
                    f"{meta.meta_graph_base()}/{account_id}/insights",
                    params={
                        "access_token": access_token,
                        "appsecret_proof": meta.meta_appsecret_proof(access_token),
                        "fields": (
                            "spend,impressions,clicks,actions,account_currency,"
                            "date_start,date_stop"
                        ),
                        "time_range": json.dumps(
                            {"since": day.isoformat(), "until": day.isoformat()},
                            separators=(",", ":"),
                        ),
                        "breakdowns": "hourly_stats_aggregated_by_advertiser_time_zone",
                        "level": "account",
                        "use_account_attribution_setting": "true",
                        "use_unified_attribution_setting": "true",
                        "limit": 100,
                    },
                )
                if response.status_code >= 400:
                    raise meta._graph_error(response, "meta_hourly_insights")
                payload = response.json() or {}
                rows = payload.get("data") if isinstance(payload, dict) else []
                rows = rows if isinstance(rows, list) else []
                by_hour: dict[int, dict[str, float]] = {
                    hour: {"spend": 0.0, "impressions": 0.0, "clicks": 0.0, "conversions": 0.0}
                    for hour in range(24)
                }
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    hour = _meta_hour(
                        row.get("hourly_stats_aggregated_by_advertiser_time_zone")
                    )
                    if hour is None:
                        continue
                    purchases, _ = meta._action_value(row.get("actions"))
                    currency = str(row.get("account_currency") or currency or "").upper() or None
                    bucket = by_hour[hour]
                    bucket["spend"] += float(row.get("spend") or 0)
                    bucket["impressions"] += float(row.get("impressions") or 0)
                    bucket["clicks"] += float(row.get("clicks") or 0)
                    bucket["conversions"] += float(purchases or 0)
                fx_rate, _ = meta._fx_to_sar(currency)
                for hour, bucket in by_hour.items():
                    spend_sar = (
                        round(bucket["spend"] * fx_rate, 2)
                        if fx_rate is not None
                        else None
                    )
                    await upsert_platform_hour(
                        db,
                        user_id=user_id,
                        provider="meta",
                        ad_account_id=account_id,
                        display_name=account.get("display_name"),
                        day=day,
                        hour_index=hour,
                        account_timezone=timezone_name,
                        currency_native=currency,
                        spend_native=bucket["spend"],
                        fx_rate_to_sar=fx_rate,
                        spend_sar=spend_sar,
                        impressions=int(bucket["impressions"]),
                        clicks=int(bucket["clicks"]),
                        conversions=bucket["conversions"],
                        source_mode="meta_marketing_hourly_reporting_v1",
                        observed_at=observed_at,
                    )
                    saved += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "ad_account_id": account_id,
                        "code": getattr(exc, "code", "meta_hourly_failed"),
                        "message": str(getattr(exc, "message", exc))[:300],
                    }
                )
    return {
        "provider": META_PROVIDER_ID,
        "status": "complete" if not errors else "partial" if saved else "failed",
        "hourly_rows_saved": saved,
        "errors_count": len(errors),
        "error_samples": errors[:5],
    }


async def _refresh_tiktok_hourly(db: Any, user_id: str, day: date) -> dict[str, Any]:
    if not tiktok_oauth_configured() or not tiktok.tiktok_reporting_enabled():
        return {"provider": TIKTOK_PROVIDER_ID, "status": "skipped", "reason": "disabled"}
    observed_at = _iso()
    await ensure_platform_hourly_indexes(db)
    access_token = await tiktok._credential(db, user_id)
    accounts = await tiktok._accounts(db, user_id)
    saved = 0
    errors: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for account in accounts:
            account_id = str(account.get("ad_account_id") or "").strip()
            timezone_name = _safe_timezone(account.get("timezone"))
            currency = str(account.get("currency") or "").strip().upper() or None
            fx_rate, _ = tiktok._fx_to_sar(currency)
            try:
                response = await client.get(
                    tiktok.TIKTOK_REPORT_URL,
                    headers={"Access-Token": access_token},
                    params={
                        "advertiser_id": account_id,
                        "report_type": "BASIC",
                        "data_level": "AUCTION_ADVERTISER",
                        "dimensions": json.dumps(
                            ["advertiser_id", "stat_time_hour"],
                            separators=(",", ":"),
                        ),
                        "metrics": json.dumps(
                            ["spend", "impressions", "clicks", "conversion"],
                            separators=(",", ":"),
                        ),
                        "start_date": day.isoformat(),
                        "end_date": day.isoformat(),
                        "page": 1,
                        "page_size": 1000,
                    },
                )
                data = tiktok._provider_data(response, "tiktok_hourly_report")
                rows = data.get("list") or []
                by_hour: dict[int, dict[str, float]] = {
                    hour: {"spend": 0.0, "impressions": 0.0, "clicks": 0.0, "conversions": 0.0}
                    for hour in range(24)
                }
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    dimensions = row.get("dimensions") if isinstance(row.get("dimensions"), dict) else {}
                    parsed = _tiktok_hour(dimensions.get("stat_time_hour"))
                    if not parsed or parsed[0] != day:
                        continue
                    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
                    bucket = by_hour[parsed[1]]
                    bucket["spend"] += float(metrics.get("spend") or 0)
                    bucket["impressions"] += float(metrics.get("impressions") or 0)
                    bucket["clicks"] += float(metrics.get("clicks") or 0)
                    bucket["conversions"] += float(metrics.get("conversion") or 0)
                for hour, bucket in by_hour.items():
                    spend_sar = (
                        round(bucket["spend"] * fx_rate, 2)
                        if fx_rate is not None
                        else None
                    )
                    await upsert_platform_hour(
                        db,
                        user_id=user_id,
                        provider="tiktok",
                        ad_account_id=account_id,
                        display_name=account.get("display_name"),
                        day=day,
                        hour_index=hour,
                        account_timezone=timezone_name,
                        currency_native=currency,
                        spend_native=bucket["spend"],
                        fx_rate_to_sar=fx_rate,
                        spend_sar=spend_sar,
                        impressions=int(bucket["impressions"]),
                        clicks=int(bucket["clicks"]),
                        conversions=bucket["conversions"],
                        source_mode="tiktok_marketing_hourly_reporting_v1",
                        observed_at=observed_at,
                    )
                    saved += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "ad_account_id": account_id,
                        "code": getattr(exc, "code", "tiktok_hourly_failed"),
                        "message": str(getattr(exc, "message", exc))[:300],
                    }
                )
    return {
        "provider": TIKTOK_PROVIDER_ID,
        "status": "complete" if not errors else "partial" if saved else "failed",
        "hourly_rows_saved": saved,
        "errors_count": len(errors),
        "error_samples": errors[:5],
    }


async def _refresh_snapchat(
    db: Any,
    user_id: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    # The canonical Snapchat writer is the durable analytics scheduler.  A
    # second writer without a run record could be mistaken for a scheduler
    # fact when their write windows overlap, so Dashboard refresh is read-only.
    del db, user_id, start, end
    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "status": "incomplete",
        "reason": "canonical_dashboard_scheduler_required",
        "projection_write_reached": False,
        "rows_saved": 0,
        "errors_count": 0,
    }


async def _refresh_meta(db: Any, user_id: str, start: date, end: date) -> dict[str, Any]:
    if not meta_oauth_configured() or not meta.meta_reporting_enabled():
        return {"provider": META_PROVIDER_ID, "status": "skipped", "reason": "disabled"}
    daily = await meta.run_meta_reporting_sync(
        db,
        user_id,
        meta.MetaReportingSyncInput(
            days=(end - start).days + 1,
            from_date=start.isoformat(),
            to_date=end.isoformat(),
        ),
    )
    if start == end:
        hourly = await _refresh_meta_hourly(db, user_id, start)
        return {**daily, "hourly": hourly}
    return daily


async def _refresh_tiktok(db: Any, user_id: str, start: date, end: date) -> dict[str, Any]:
    if not tiktok_oauth_configured() or not tiktok.tiktok_reporting_enabled():
        return {"provider": TIKTOK_PROVIDER_ID, "status": "skipped", "reason": "disabled"}
    daily = await tiktok.run_tiktok_reporting_sync(
        db,
        user_id,
        tiktok.TikTokReportingSyncInput(
            days=(end - start).days + 1,
            from_date=start.isoformat(),
            to_date=end.isoformat(),
        ),
    )
    if start == end:
        hourly = await _refresh_tiktok_hourly(db, user_id, start)
        return {**daily, "hourly": hourly}
    return daily


async def _refresh_google(db: Any, user_id: str, start: date, end: date) -> dict[str, Any]:
    if not google_oauth_configured() or not google_ads_reporting_enabled():
        return {"provider": GOOGLE_ADS_PROVIDER_ID, "status": "skipped", "reason": "disabled"}
    return await run_google_ads_reporting_sync(
        db,
        user_id,
        date_from=start.isoformat(),
        date_to=end.isoformat(),
    )


async def refresh_dashboard_platform_spend(
    db: Any,
    user_id: str,
    *,
    date_from: str,
    date_to: str,
) -> dict[str, Any]:
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    days = (end - start).days + 1
    if end < start or days > MAX_REFRESH_DAYS:
        raise ValueError("dashboard_ads_platform_refresh_invalid_range")

    tasks = (
        _refresh_snapchat(db, user_id, start, end),
        _refresh_meta(db, user_id, start, end),
        _refresh_tiktok(db, user_id, start, end),
        _refresh_google(db, user_id, start, end),
    )
    raw = await asyncio.gather(*tasks, return_exceptions=True)
    results: list[dict[str, Any]] = []
    provider_ids = (
        SNAPCHAT_PROVIDER_ID,
        META_PROVIDER_ID,
        TIKTOK_PROVIDER_ID,
        GOOGLE_ADS_PROVIDER_ID,
    )
    for provider_id, item in zip(provider_ids, raw):
        if isinstance(item, Exception):
            results.append(
                {
                    "provider": provider_id,
                    "status": "failed",
                    "code": getattr(item, "code", "dashboard_platform_refresh_failed"),
                    "message": str(getattr(item, "message", item))[:300],
                }
            )
        else:
            results.append(item)
    return {
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "results": results,
        "status": (
            "complete"
            if all(item.get("status") in {"complete", "skipped"} for item in results)
            else "partial"
        ),
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


__all__ = [
    "FOUR_PLATFORM_KEYS",
    "MAX_REFRESH_DAYS",
    "refresh_dashboard_platform_spend",
]
