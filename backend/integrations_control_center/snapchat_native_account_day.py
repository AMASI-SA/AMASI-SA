"""Snapchat campaign-hour evidence and account-local day snapshots.

A native HOUR point is the only permitted input. The existing Riyadh-day
facts are left unchanged; this is a separate, idempotent analytical projection.
No provider calls and no accounting writes are performed by this module.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any

from pymongo import UpdateOne

from .snapchat_native_data_common import _parse_datetime, _as_number, BUSINESS_TIMEZONE

HOUR_COLLECTION = "mezan_snapchat_campaign_hourly_v2"
ACCOUNT_DAY_COLLECTION = "mezan_snapchat_campaign_account_day_v2"
MAX_FACTS_PER_SYNC = 10000
BATCH_SIZE = 200
MAX_WINDOW = timedelta(days=7)
log = logging.getLogger("mezan.snapchat_account_day")


def validated_tz(name: Any) -> ZoneInfo | None:
    if not isinstance(name, str) or not name.strip():
        return None
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return None


def local_day_utc_range(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    following = day + timedelta(days=1)
    end = datetime(following.year, following.month, following.day, tzinfo=zone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def sync_windows(start_date: date, end_date: date, zone: ZoneInfo) -> list[tuple[datetime, datetime]]:
    """Cover both old Riyadh business dates and the same account-local dates.

    Use UTC arithmetic so spring-forward 23-hour and fall-back 25-hour days
    never duplicate or erase one repeated local clock hour.
    """
    riyadh = ZoneInfo(BUSINESS_TIMEZONE)
    start = min(local_day_utc_range(start_date, riyadh)[0],
                local_day_utc_range(start_date, zone)[0])
    end = max(local_day_utc_range(end_date, riyadh)[1],
              local_day_utc_range(end_date, zone)[1])
    windows = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + MAX_WINDOW, end)
        windows.append((cursor, nxt))
        cursor = nxt
    return windows


def required_local_date_range(
    start_date: date,
    end_date: date,
    zone: ZoneInfo,
    *,
    now: datetime,
) -> tuple[date, date]:
    """Include the current account date during a current Riyadh-day sync.

    Between 00:00 Riyadh and 00:00 New York, an hourly sync requested for
    Riyadh 'today' must also include New York's still-active prior date.
    Historical backfills retain precisely their requested date range.
    """
    point = now.astimezone(timezone.utc)
    riyadh_today = point.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).date()
    if start_date <= riyadh_today <= end_date:
        account_today = point.astimezone(zone).date()
        return min(start_date, account_today), max(end_date, account_today)
    return start_date, end_date


def normalize_hour_point(row: dict[str, Any], *, window_start: datetime, window_end: datetime):
    start = _parse_datetime(row.get("start_time"))
    end = _parse_datetime(row.get("end_time"))
    if not start or not end:
        return None
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    if (end - start != timedelta(hours=1) or
        not window_start <= start < end <= window_end or
        start.minute or start.second or start.microsecond):
        return None
    campaign = str(row.get("external_id") or "").strip()
    if not campaign:
        return None
    metrics = row.get("metrics")
    if not isinstance(metrics, dict):
        return None
    spend_micro = _as_number(metrics.get("spend"))
    if spend_micro is None or spend_micro < 0:
        return None
    return campaign, start, end, float(spend_micro) / 1_000_000


def summarize_hours(rows: list[dict[str, Any]], zone: ZoneInfo, *, start: datetime, end: datetime):
    """One campaign/day value only when the complete local day was requested."""
    daily: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        local_day = row["start"].astimezone(zone).date()
        day_start, day_end = local_day_utc_range(local_day, zone)
        if not (start <= day_start and end >= day_end):
            continue
        key = (row["campaign_id"], local_day.isoformat())
        entry = daily.setdefault(key, {"spend_native": 0.0, "count": 0, "last_hour": None})
        entry["spend_native"] += row["spend_native"]
        entry["count"] += 1
        if entry["last_hour"] is None or entry["last_hour"] < row["end"]:
            entry["last_hour"] = row["end"]
    return daily


async def _bulk(collection, operations: list[UpdateOne]):
    for index in range(0, len(operations), BATCH_SIZE):
        await collection.bulk_write(operations[index:index + BATCH_SIZE], ordered=False)


async def persist_account_local_campaign_facts(
    context: Any,
    account: dict[str, Any],
    source_rows: list[dict[str, Any]],
    *,
    windows: list[tuple[datetime, datetime]],
) -> int:
    zone = validated_tz(account.get("timezone"))
    account_id = str(account.get("ad_account_id") or "").strip()
    if zone is None or not account_id or not windows:
        return 0
    requested_start = min(w[0].astimezone(timezone.utc) for w in windows)
    requested_end = max(w[1].astimezone(timezone.utc) for w in windows)
    # Only true, complete one-hour points are eligible. Fake daily points
    # returned inside a HOUR response must not be treated as one hour.
    facts: dict[tuple[str, datetime], dict[str, Any]] = {}
    for source in source_rows:
        parsed = normalize_hour_point(source, window_start=requested_start, window_end=requested_end)
        if parsed is None:
            continue
        campaign, hour_start, hour_end, spend_native = parsed
        key = campaign, hour_start
        prior = facts.get(key)
        if prior and prior["spend_native"] != spend_native:
            raise ValueError("conflicting_snapchat_hour_duplicate")
        facts[key] = {"campaign_id": campaign, "start": hour_start,
                      "end": hour_end, "spend_native": spend_native}
    if len(facts) > MAX_FACTS_PER_SYNC:
        # Supplemental analytics cannot invalidate the existing Riyadh sync.
        # Do not publish an incomplete subset as a complete daily amount.
        log.warning("snapchat hourly projection skipped: fact limit exceeded (%s)", len(facts))
        return 0
    if not facts:
        return 0

    currency = str(account.get("currency") or "").strip().upper()
    if not currency:
        return 0
    # A foreign-currency result without an explicitly stored conversion rate
    # is unknown, not 3.75 (or any other fallback rate). Read the setting once.
    fx_rate = 1.0 if currency == "SAR" else None
    if currency == "USD":
        fx_doc = await context.db["ads_currency_settings"].find_one(
            {"user_id": context.user_id}, {"_id": 0, "usd_to_sar_rate": 1},
        )
        configured = _as_number((fx_doc or {}).get("usd_to_sar_rate"))
        if configured is not None and configured > 0:
            fx_rate = float(configured)
    now_iso = context.now_iso()
    raw_ops = []
    for row in facts.values():
        hourly_sar = round(row["spend_native"] * fx_rate, 2) if fx_rate is not None else None
        key = {
            "user_id": context.user_id, "ad_account_id": account_id,
            "campaign_id": row["campaign_id"], "hour_start_utc": row["start"].isoformat(),
        }
        raw_ops.append(UpdateOne(key, {"$set": {
            **key, "hour_end_utc": row["end"].isoformat(),
            "timezone": str(zone), "account_date": row["start"].astimezone(zone).date().isoformat(),
            "currency_native": currency, "spend_native": row["spend_native"],
            "spend_sar": hourly_sar, "fx_rate_to_sar": fx_rate,
            "updated_at": now_iso,
            "source_mode": "snapchat_native_hourly_v2",
            "accounting_eligible": False,
        }}, upsert=True))

    await _bulk(context.db[HOUR_COLLECTION], raw_ops)
    grouped = summarize_hours(list(facts.values()), zone,
                              start=requested_start, end=requested_end)
    if not grouped:
        return len(raw_ops)
    day_ops = []
    for (campaign, day), summary in grouped.items():
        native = round(summary["spend_native"], 6)
        sar = round(native * fx_rate, 2) if fx_rate is not None else None
        key = {
            "user_id": context.user_id, "ad_account_id": account_id,
            "campaign_id": campaign, "date": day,
            "account_timezone": str(zone),
        }
        day_ops.append(UpdateOne(key, {"$set": {
            **key, "spend_native": native, "spend_sar": sar,
            "currency_native": currency, "fx_rate_to_sar": fx_rate,
            "hours_observed": summary["count"],
            "last_hour_end_utc": summary["last_hour"].isoformat(),
            "updated_at": now_iso,
            "source_mode": "snapchat_native_account_day_v2",
            "coverage_status": "provisional_hourly",
            "accounting_eligible": False,
        }}, upsert=True))
    await _bulk(context.db[ACCOUNT_DAY_COLLECTION], day_ops)
    return len(raw_ops) + len(day_ops)
