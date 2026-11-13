"""Bounded Snapchat hourly evidence and account-local spend snapshots.

Existing Riyadh accounting facts are not changed. Valid observed spend remains
usable when hours are missing; coverage is separate advisory metadata. Missing
hours are never fabricated as zero. Invalid/conflicting evidence stays unknown.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pymongo import UpdateOne

HOUR_COLLECTION = "mezan_snapchat_campaign_hourly_v2"
ACCOUNT_DAY_COLLECTION = "mezan_snapchat_campaign_account_day_v2"
BUSINESS_TIMEZONE = "Asia/Riyadh"
MAX_FACTS_PER_SYNC = 10000
BATCH_SIZE = 200
MAX_WINDOW = timedelta(days=7)
HOUR = timedelta(hours=1)
log = logging.getLogger("mezan.snapchat_account_day")


def validated_tz(name: Any) -> ZoneInfo | None:
    if not isinstance(name, str) or not name.strip():
        return None
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _instant(value: Any) -> datetime | None:
    try:
        point = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return point.astimezone(timezone.utc) if point.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _sar(native: Decimal, rate: Decimal | None) -> float | None:
    if rate is None:
        return None
    return float((native * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def local_day_utc_range(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    following = day + timedelta(days=1)
    end = datetime(following.year, following.month, following.day, tzinfo=zone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def sync_windows(start_date: date, end_date: date, zone: ZoneInfo) -> list[tuple[datetime, datetime]]:
    if end_date < start_date:
        raise ValueError("invalid_snapchat_date_range")
    riyadh = ZoneInfo(BUSINESS_TIMEZONE)
    start = min(local_day_utc_range(start_date, riyadh)[0], local_day_utc_range(start_date, zone)[0])
    end = max(local_day_utc_range(end_date, riyadh)[1], local_day_utc_range(end_date, zone)[1])
    windows = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + MAX_WINDOW, end)
        windows.append((cursor, nxt))
        cursor = nxt
    return windows


def required_local_date_range(start_date: date, end_date: date, zone: ZoneInfo, *, now: datetime) -> tuple[date, date]:
    point = _instant(now)
    if point is None:
        raise ValueError("snapchat_sync_clock_timezone_missing")
    riyadh_today = point.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).date()
    if start_date <= riyadh_today <= end_date:
        account_today = point.astimezone(zone).date()
        return min(start_date, account_today), max(end_date, account_today)
    return start_date, end_date


def normalize_hour_point(row: dict[str, Any], *, window_start: datetime, window_end: datetime):
    start, end = _instant(row.get("start_time")), _instant(row.get("end_time"))
    if start is None or end is None:
        return None
    if end - start != HOUR or not window_start <= start < end <= window_end:
        return None
    if start.minute or start.second or start.microsecond:
        return None
    campaign = str(row.get("external_id") or "").strip()
    metrics = row.get("metrics")
    if not campaign or not isinstance(metrics, dict):
        return None
    micro = _decimal(metrics.get("spend"))
    if micro is None:
        return None
    return campaign, start, end, float(micro / Decimal(1000000))


def summarize_hours(rows: list[dict[str, Any]], zone: ZoneInfo, *, start: datetime, end: datetime,
                    as_of: datetime | None = None) -> dict:
    """Sum observed hours and report midnight-to-observation coverage separately.

    A current-hour bucket may be provisional. Future buckets never contribute.
    Missing historical 23/24/25-hour buckets are advisory, not invented zeros.
    """
    observed = _instant(as_of) if as_of is not None else end
    if observed is None:
        return {}
    grouped: dict[tuple[str, str], dict[datetime, dict]] = {}
    conflicts = set()
    for row in rows:
        hour_start, hour_end = row["start"], row["end"]
        if hour_start >= observed:
            continue
        day = hour_start.astimezone(zone).date()
        day_start, day_end = local_day_utc_range(day, zone)
        if not start <= day_start < day_end <= end:
            continue
        key = (row["campaign_id"], day.isoformat())
        bucket = grouped.setdefault(key, {})
        if hour_start in bucket and bucket[hour_start]["spend_native"] != row["spend_native"]:
            conflicts.add(key)
        bucket[hour_start] = row
    result = {}
    for key, bucket in grouped.items():
        day_start, day_end = local_day_utc_range(date.fromisoformat(key[1]), zone)
        floor = observed.replace(minute=0, second=0, microsecond=0)
        through = min(day_end, floor)
        # A partial current hour is used only when the provider returned it.
        if day_start <= floor < day_end and floor in bucket and observed > floor:
            through = min(day_end, observed)
        expected = set()
        point = day_start
        while point < through:
            expected.add(point)
            point += HOUR
        present = set(bucket).intersection(expected)
        missing = expected - present
        amount = sum((_decimal(bucket[h]["spend_native"]) or Decimal(0) for h in present), Decimal(0))
        complete = bool(expected) and not missing and key not in conflicts
        spend_valid = bool(present) and key not in conflicts
        result[key] = {
            "spend_native": float(amount) if spend_valid else None,
            "known_spend_native": float(amount),
            "count": len(present), "hours_expected": len(expected),
            "missing_hours": len(missing), "coverage_complete": complete,
            "coverage_start": day_start, "coverage_end": through,
            "last_hour": max((min(bucket[h]["end"], observed) for h in present), default=day_start),
        }
    return result


async def _bulk(collection, operations: list[UpdateOne]):
    for index in range(0, len(operations), BATCH_SIZE):
        await collection.bulk_write(operations[index:index + BATCH_SIZE], ordered=False)


async def persist_account_local_campaign_facts(context: Any, account: dict[str, Any],
                                             source_rows: list[dict[str, Any]], *,
                                             windows: list[tuple[datetime, datetime]],
                                             source_complete: bool = True) -> int:
    zone = validated_tz(account.get("timezone"))
    account_id = str(account.get("ad_account_id") or "").strip()
    if zone is None or not account_id or not windows or not source_complete:
        return 0
    ordered = sorted(windows)
    if any(_instant(a) is None or _instant(b) is None or a >= b for a, b in ordered):
        return 0
    if any(ordered[i][1] != ordered[i + 1][0] for i in range(len(ordered) - 1)):
        return 0
    requested_start, requested_end = ordered[0][0], ordered[-1][1]
    now_iso = context.now_iso()
    observed = _instant(now_iso)
    if observed is None:
        return 0
    facts = {}
    invalid_campaigns = set()
    for source in source_rows:
        parsed = normalize_hour_point(source, window_start=requested_start, window_end=requested_end)
        if parsed is None:
            campaign = str(source.get("external_id") or "").strip()
            point = _instant(source.get("start_time"))
            if campaign and (point is None or point < observed):
                invalid_campaigns.add(campaign)
            continue
        campaign, hour_start, hour_end, native = parsed
        if hour_start >= observed:
            continue
        key = campaign, hour_start
        prior = facts.get(key)
        if prior and prior["spend_native"] != native:
            invalid_campaigns.add(campaign)
        facts[key] = {"campaign_id": campaign, "start": hour_start, "end": hour_end, "spend_native": native}
        if len(facts) > MAX_FACTS_PER_SYNC:
            log.warning("snapchat hourly projection skipped: write budget exceeded")
            return 0
    if not facts:
        return 0
    currency = str(account.get("currency") or "").strip().upper()
    if not currency:
        return 0
    rate = Decimal(1) if currency == "SAR" else None
    if currency == "USD":
        try:
            settings = await context.db["ads_currency_settings"].find_one(
                {"user_id": context.user_id}, {"_id": 0, "usd_to_sar_rate": 1})
            candidate = _decimal((settings or {}).get("usd_to_sar_rate"))
            if candidate is not None and candidate > 0:
                rate = candidate
        except Exception:
            # Native evidence is still useful. A failed FX read is not zero.
            rate = None
    raw_ops = []
    for row in facts.values():
        key = {"user_id": context.user_id, "ad_account_id": account_id,
               "campaign_id": row["campaign_id"], "hour_start_utc": row["start"].isoformat()}
        raw_ops.append(UpdateOne(key, {"$set": {
            **key, "hour_end_utc": row["end"].isoformat(), "timezone": str(zone),
            "account_date": row["start"].astimezone(zone).date().isoformat(),
            "currency_native": currency, "spend_native": row["spend_native"],
            "spend_sar": _sar(Decimal(str(row["spend_native"])), rate),
            "fx_rate_to_sar": float(rate) if rate is not None else None,
            "updated_at": now_iso, "source_mode": "snapchat_native_hourly_v2",
            "accounting_eligible": False,
        }}, upsert=True))
    await _bulk(context.db[HOUR_COLLECTION], raw_ops)
    grouped = summarize_hours(list(facts.values()), zone, start=requested_start,
                              end=requested_end, as_of=observed)
    day_ops = []
    for (campaign, day), summary in grouped.items():
        complete = summary["coverage_complete"] and campaign not in invalid_campaigns
        spend_valid = summary["spend_native"] is not None and campaign not in invalid_campaigns
        native = summary["spend_native"] if spend_valid else None
        sar = _sar(Decimal(str(native)), rate) if native is not None else None
        key = {"user_id": context.user_id, "ad_account_id": account_id, "campaign_id": campaign,
               "date": day, "account_timezone": str(zone)}
        day_ops.append(UpdateOne(key, {"$set": {
            **key, "spend_native": native, "spend_sar": sar,
            "known_spend_native": summary["known_spend_native"],
            "spend_evidence_valid": spend_valid,
            "currency_native": currency, "fx_rate_to_sar": float(rate) if rate is not None else None,
            "hours_observed": summary["count"], "hours_expected": summary["hours_expected"],
            "missing_hours": summary["missing_hours"], "coverage_complete": complete,
            "coverage_start_utc": summary["coverage_start"].isoformat(),
            "coverage_end_utc": summary["coverage_end"].isoformat(),
            "last_hour_end_utc": summary["last_hour"].isoformat(),
            "updated_at": now_iso, "source_mode": "snapchat_native_account_day_v2",
            "coverage_status": ("provisional_hourly" if complete else
                                "incomplete_hourly" if spend_valid else "invalid_hourly"),
            "accounting_eligible": False,
        }}, upsert=True))
    await _bulk(context.db[ACCOUNT_DAY_COLLECTION], day_ops)
    return len(raw_ops) + len(day_ops)
