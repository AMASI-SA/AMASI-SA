"""Shared provider-hour projection for the Mezan 2 Dashboard.

Rows in this collection are provider-reported analytical facts only. They are
never eligible for accounting and never mutate provider campaigns.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

PLATFORM_HOURLY_COLLECTION = "mezan_ads_platform_hourly_v2"
PLATFORM_KEYS = ("meta", "tiktok", "google")
INTEGRATION_PROVIDER_BY_KEY = {
    "meta": "meta_ads",
    "tiktok": "tiktok_ads",
    "google": "google_ads",
}


def _timezone(name: Any) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or "Asia/Riyadh"))
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Riyadh")


def local_hour_start_utc(
    day: date,
    hour_index: int,
    timezone_name: Any,
) -> datetime:
    local = datetime.combine(
        day,
        time(hour=max(0, min(int(hour_index), 23))),
        tzinfo=_timezone(timezone_name),
    )
    return local.astimezone(timezone.utc)


async def ensure_platform_hourly_indexes(db: Any) -> None:
    collection = db[PLATFORM_HOURLY_COLLECTION]
    await collection.create_index(
        [
            ("user_id", 1),
            ("provider", 1),
            ("ad_account_id", 1),
            ("hour_start_utc", 1),
        ],
        unique=True,
        name="ads_platform_hourly_identity_unique",
    )
    await collection.create_index(
        [("user_id", 1), ("hour_start_utc", -1)],
        name="ads_platform_hourly_user_time",
    )


async def upsert_platform_hour(
    db: Any,
    *,
    user_id: str,
    provider: str,
    ad_account_id: str,
    display_name: str | None,
    day: date,
    hour_index: int,
    account_timezone: str | None,
    currency_native: str | None,
    spend_native: float,
    fx_rate_to_sar: float | None,
    spend_sar: float | None,
    impressions: int = 0,
    clicks: int = 0,
    conversions: float = 0.0,
    source_mode: str,
    observed_at: str,
) -> None:
    if provider not in PLATFORM_KEYS:
        raise ValueError("unsupported_platform_hour_provider")
    hour_start = local_hour_start_utc(day, hour_index, account_timezone)
    await db[PLATFORM_HOURLY_COLLECTION].update_one(
        {
            "user_id": user_id,
            "provider": provider,
            "ad_account_id": str(ad_account_id),
            "hour_start_utc": hour_start.isoformat(timespec="seconds"),
        },
        {
            "$set": {
                "user_id": user_id,
                "provider": provider,
                "integration_provider": INTEGRATION_PROVIDER_BY_KEY[provider],
                "ad_account_id": str(ad_account_id),
                "display_name": display_name,
                "date_account_local": day.isoformat(),
                "hour_index_account_local": int(hour_index),
                "hour_start_utc": hour_start.isoformat(timespec="seconds"),
                "account_timezone": str(account_timezone or "Asia/Riyadh"),
                "currency_native": currency_native,
                "spend_native": round(float(spend_native or 0), 6),
                "fx_rate_to_sar": fx_rate_to_sar,
                "spend_sar": (
                    round(float(spend_sar), 2)
                    if spend_sar is not None
                    else None
                ),
                "impressions": max(0, int(impressions or 0)),
                "clicks": max(0, int(clicks or 0)),
                "conversions": max(0.0, float(conversions or 0)),
                "source_mode": source_mode,
                "source_only": True,
                "accounting_eligible": False,
                "provider_write_reached": False,
                "campaign_write_reached": False,
                "accounting_write_reached": False,
                "qoyod_write_reached": False,
                "observed_at": observed_at,
                "updated_at": observed_at,
            },
            "$setOnInsert": {"created_at": observed_at},
        },
        upsert=True,
    )


__all__ = [
    "INTEGRATION_PROVIDER_BY_KEY",
    "PLATFORM_HOURLY_COLLECTION",
    "PLATFORM_KEYS",
    "ensure_platform_hourly_indexes",
    "local_hour_start_utc",
    "upsert_platform_hour",
]
