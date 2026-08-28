"""Timezone-aware projections built exclusively from Snapchat V2 hourly facts."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .facts import load_hourly_facts
from .models import SNAPCHAT_PROVIDER, clean_text, ensure_aware_utc

SNAPCHAT_DAILY_PROJECTIONS_COLLECTION = "mezan_snapchat_daily_projections_v2"
RIYADH_TIMEZONE = "Asia/Riyadh"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stored_utc(value: Any, *, field: str) -> datetime:
    """Normalize UTC datetimes read back from MongoDB.

    Mongo/PyMongo can deserialize BSON UTC datetimes without tzinfo depending
    on client configuration. Persisted Snapchat V2 timestamps are UTC by
    contract, so a naive stored value is explicitly re-attached to UTC here.
    """
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _timezone(value: str) -> ZoneInfo:
    name = clean_text(value, limit=80)
    if not name:
        raise ValueError("timezone_name is required")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"invalid timezone: {name}") from exc


def business_day_window(report_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    tz = _timezone(timezone_name)
    local_start = datetime.combine(report_date, time.min, tzinfo=tz)
    local_end = datetime.combine(report_date + timedelta(days=1), time.min, tzinfo=tz)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def _hour_starts(start_utc: datetime, end_utc: datetime) -> list[datetime]:
    rows: list[datetime] = []
    cursor = start_utc
    while cursor < end_utc:
        rows.append(cursor)
        cursor += timedelta(hours=1)
    return rows


def _metric_sum(rows: list[dict[str, Any]], field: str) -> int | float:
    total = sum(float(row.get(field) or 0) for row in rows)
    if field in {
        "impressions",
        "swipes",
        "video_views",
        "view_content",
        "add_to_cart",
        "start_checkout",
        "add_billing",
        "purchases",
    }:
        return int(total)
    return round(total, 6)


async def ensure_projection_indexes(db: Any) -> None:
    collection = db[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION]
    await collection.create_index(
        [
            ("user_id", 1),
            ("provider", 1),
            ("ad_account_id", 1),
            ("report_date", 1),
            ("projection_timezone", 1),
            ("action_report_time", 1),
        ],
        unique=True,
        name="snapchat_v2_daily_projection_identity_unique",
    )
    await collection.create_index(
        [("user_id", 1), ("report_date", -1), ("projection_timezone", 1)],
        name="snapchat_v2_daily_projection_date",
    )


async def build_daily_projection(
    db: Any,
    *,
    user_id: str,
    account: dict[str, Any],
    report_date: date,
    projection_timezone: str,
    action_report_time: str = "conversion",
    coverage: dict[str, Any] | None = None,
    sync_run_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = ensure_aware_utc(now or _utcnow(), field="now")
    account_id = clean_text(
        account.get("ad_account_id") or account.get("external_account_id"),
        limit=128,
    )
    currency = clean_text(account.get("currency"), limit=12).upper()
    account_timezone = clean_text(account.get("timezone"), limit=80)
    if not account_id or not currency or not account_timezone:
        raise ValueError("Snapchat account identity, currency, and timezone are required")
    projection_tz = _timezone(projection_timezone)
    start_utc, end_utc = business_day_window(report_date, projection_timezone)
    facts = await load_hourly_facts(
        db,
        user_id=str(user_id),
        ad_account_id=account_id,
        start_utc=start_utc,
        end_utc=end_utc,
        entity_type="ad_account",
        action_report_time=action_report_time,
    )
    if sync_run_id:
        facts = [
            row for row in facts
            if str(row.get("sync_run_id") or "") == str(sync_run_id)
        ]
    by_hour: dict[datetime, dict[str, Any]] = {}
    for fact in facts:
        point = _stored_utc(fact.get("hour_start_utc"), field="hour_start_utc")
        if point in by_hour:
            raise ValueError("duplicate Snapchat account hourly fact")
        by_hour[point] = fact

    coverage_doc = dict(coverage or {})
    coverage_complete = coverage_doc.get("status") == "complete"
    no_data_confirmed = (
        coverage_complete and coverage_doc.get("data_state") == "confirmed_no_data"
    )
    rows: list[dict[str, Any]] = []
    missing_closed_hours = 0
    provisional_hours = 0
    future_hours = 0
    for sequence, hour_start in enumerate(_hour_starts(start_utc, end_utc)):
        hour_end = hour_start + timedelta(hours=1)
        local_start = hour_start.astimezone(projection_tz)
        fact = by_hour.get(hour_start)
        is_future = hour_start >= current
        is_open = hour_start <= current < hour_end
        if fact is not None:
            status = (
                "provisional"
                if is_open or fact.get("provisional") is True
                else "confirmed_data"
                if float(fact.get("spend_native") or 0) > 0
                else "confirmed_zero"
            )
            if status == "provisional":
                provisional_hours += 1
            row = {
                "sequence": sequence,
                "hour_start_utc": hour_start,
                "hour_end_utc": hour_end,
                "local_hour": local_start.strftime("%H:00"),
                "local_date": local_start.date().isoformat(),
                "utc_offset": local_start.strftime("%z"),
                "status": status,
                "spend_native": round(float(fact.get("spend_native") or 0), 6),
                "impressions": int(fact.get("impressions") or 0),
                "swipes": int(fact.get("swipes") or 0),
                "video_views": int(fact.get("video_views") or 0),
                "view_completion": round(
                    float(fact.get("view_completion") or 0),
                    6,
                ),
                "view_content": int(fact.get("view_content") or 0),
                "add_to_cart": int(fact.get("add_to_cart") or 0),
                "start_checkout": int(fact.get("start_checkout") or 0),
                "add_billing": int(fact.get("add_billing") or 0),
                "purchases": int(fact.get("purchases") or 0),
                "purchase_value_native": round(
                    float(fact.get("purchase_value_native") or 0),
                    6,
                ),
                "sync_run_id": fact.get("sync_run_id"),
                "updated_at": fact.get("updated_at"),
            }
        elif is_future:
            future_hours += 1
            row = {
                "sequence": sequence,
                "hour_start_utc": hour_start,
                "hour_end_utc": hour_end,
                "local_hour": local_start.strftime("%H:00"),
                "local_date": local_start.date().isoformat(),
                "utc_offset": local_start.strftime("%z"),
                "status": "future",
                "spend_native": None,
                "impressions": None,
                "swipes": None,
                "video_views": None,
                "view_completion": None,
                "view_content": None,
                "add_to_cart": None,
                "start_checkout": None,
                "add_billing": None,
                "purchases": None,
                "purchase_value_native": None,
                "sync_run_id": None,
                "updated_at": None,
            }
        elif no_data_confirmed:
            row = {
                "sequence": sequence,
                "hour_start_utc": hour_start,
                "hour_end_utc": hour_end,
                "local_hour": local_start.strftime("%H:00"),
                "local_date": local_start.date().isoformat(),
                "utc_offset": local_start.strftime("%z"),
                "status": "confirmed_no_data",
                "spend_native": None,
                "impressions": None,
                "swipes": None,
                "video_views": None,
                "view_completion": None,
                "view_content": None,
                "add_to_cart": None,
                "start_checkout": None,
                "add_billing": None,
                "purchases": None,
                "purchase_value_native": None,
                "sync_run_id": None,
                "updated_at": None,
            }
        elif is_open:
            provisional_hours += 1
            row = {
                "sequence": sequence,
                "hour_start_utc": hour_start,
                "hour_end_utc": hour_end,
                "local_hour": local_start.strftime("%H:00"),
                "local_date": local_start.date().isoformat(),
                "utc_offset": local_start.strftime("%z"),
                "status": "provisional_unavailable",
                "spend_native": None,
                "impressions": None,
                "swipes": None,
                "video_views": None,
                "view_completion": None,
                "view_content": None,
                "add_to_cart": None,
                "start_checkout": None,
                "add_billing": None,
                "purchases": None,
                "purchase_value_native": None,
                "sync_run_id": None,
                "updated_at": None,
            }
        else:
            missing_closed_hours += 1
            row = {
                "sequence": sequence,
                "hour_start_utc": hour_start,
                "hour_end_utc": hour_end,
                "local_hour": local_start.strftime("%H:00"),
                "local_date": local_start.date().isoformat(),
                "utc_offset": local_start.strftime("%z"),
                "status": "unknown_incomplete",
                "spend_native": None,
                "impressions": None,
                "swipes": None,
                "video_views": None,
                "view_completion": None,
                "view_content": None,
                "add_to_cart": None,
                "start_checkout": None,
                "add_billing": None,
                "purchases": None,
                "purchase_value_native": None,
                "sync_run_id": None,
                "updated_at": None,
            }
        rows.append(row)

    known_facts = list(by_hour.values())
    amount_complete = missing_closed_hours == 0 and (
        coverage_complete or bool(known_facts)
    )
    base_spend_native = round(
        sum(float(row.get("spend_native") or 0) for row in known_facts),
        6,
    )
    source_run_ids = sorted(
        {
            str(row.get("sync_run_id"))
            for row in known_facts
            if row.get("sync_run_id")
        }
    )
    latest_update = max(
        (
            _stored_utc(row.get("updated_at"), field="updated_at")
            for row in known_facts
            if isinstance(row.get("updated_at"), datetime)
        ),
        default=None,
    )
    return {
        "user_id": str(user_id),
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": account_id,
        "report_date": report_date.isoformat(),
        "projection_timezone": projection_timezone,
        "account_timezone": account_timezone,
        "currency": currency,
        "action_report_time": clean_text(action_report_time, limit=32).lower(),
        "sync_run_id": clean_text(sync_run_id, limit=128) or None,
        "window_start_utc": start_utc,
        "window_end_utc": end_utc,
        "base_spend_native": base_spend_native,
        "impressions": _metric_sum(known_facts, "impressions"),
        "swipes": _metric_sum(known_facts, "swipes"),
        "video_views": _metric_sum(known_facts, "video_views"),
        "view_completion": _metric_sum(known_facts, "view_completion"),
        "view_content": _metric_sum(known_facts, "view_content"),
        "add_to_cart": _metric_sum(known_facts, "add_to_cart"),
        "start_checkout": _metric_sum(known_facts, "start_checkout"),
        "add_billing": _metric_sum(known_facts, "add_billing"),
        "purchases": _metric_sum(known_facts, "purchases"),
        "purchase_value_native": _metric_sum(
            known_facts,
            "purchase_value_native",
        ),
        "hours": rows,
        "source_fact_count": len(known_facts),
        "source_sync_run_ids": source_run_ids,
        "source_latest_updated_at": latest_update,
        "coverage": {
            **coverage_doc,
            "expected_local_hours": len(rows),
            "known_fact_hours": len(known_facts),
            "missing_closed_hours": missing_closed_hours,
            "provisional_hours": provisional_hours,
            "future_hours": future_hours,
            "amount_complete": amount_complete,
        },
        "amount_complete": amount_complete,
        "data_state": (
            "confirmed_data"
            if base_spend_native > 0
            else "confirmed_no_data"
            if no_data_confirmed
            else "confirmed_zero"
            if amount_complete
            else "unknown_incomplete"
        ),
        "generated_at": current,
    }


async def persist_daily_projection(db: Any, projection: dict[str, Any]) -> None:
    await ensure_projection_indexes(db)
    identity = {
        "user_id": projection["user_id"],
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": projection["ad_account_id"],
        "report_date": projection["report_date"],
        "projection_timezone": projection["projection_timezone"],
        "action_report_time": projection["action_report_time"],
    }
    now = _utcnow()
    await db[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION].update_one(
        identity,
        {
            "$set": {**projection, "updated_at": now},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


async def build_and_persist_daily_projections(
    db: Any,
    *,
    user_id: str,
    account: dict[str, Any],
    report_dates: list[date],
    action_report_time: str,
    coverage: dict[str, Any],
    sync_run_id: str | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    timezones = [clean_text(account.get("timezone"), limit=80), RIYADH_TIMEZONE]
    projections: list[dict[str, Any]] = []
    for timezone_name in dict.fromkeys(timezones):
        for report_date in report_dates:
            projection = await build_daily_projection(
                db,
                user_id=user_id,
                account=account,
                report_date=report_date,
                projection_timezone=timezone_name,
                action_report_time=action_report_time,
                coverage=coverage,
                sync_run_id=sync_run_id,
                now=now,
            )
            await persist_daily_projection(db, projection)
            projections.append(projection)
    return projections


async def list_daily_projections(
    db: Any,
    *,
    user_id: str,
    ad_account_id: str,
    date_from: date,
    date_to: date,
    projection_timezone: str,
    action_report_time: str = "conversion",
) -> list[dict[str, Any]]:
    cursor = db[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION].find(
        {
            "user_id": str(user_id),
            "provider": SNAPCHAT_PROVIDER,
            "ad_account_id": str(ad_account_id),
            "report_date": {
                "$gte": date_from.isoformat(),
                "$lte": date_to.isoformat(),
            },
            "projection_timezone": projection_timezone,
            "action_report_time": clean_text(action_report_time, limit=32).lower(),
        },
        {"_id": 0},
    )
    if hasattr(cursor, "sort"):
        cursor = cursor.sort("report_date", 1)
    if hasattr(cursor, "to_list"):
        try:
            return list(await cursor.to_list(length=400))
        except TypeError:
            return list(await cursor.to_list(400))
    return [row async for row in cursor]


__all__ = [
    "RIYADH_TIMEZONE",
    "SNAPCHAT_DAILY_PROJECTIONS_COLLECTION",
    "build_and_persist_daily_projections",
    "build_daily_projection",
    "business_day_window",
    "list_daily_projections",
]
