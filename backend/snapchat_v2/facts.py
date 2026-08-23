"""Idempotent, UTC-normalized Snapchat hourly fact storage."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .models import (
    SNAPCHAT_PROVIDER,
    build_attribution_key,
    clean_text,
    derive_entity_identity,
    ensure_aware_utc,
)

SNAPCHAT_HOURLY_FACTS_COLLECTION = "mezan_snapchat_hourly_facts_v2"
MAX_FACT_ROWS_PER_WRITE = 100_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _nonnegative_number(value: Any, *, field: str) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return parsed


def _nonnegative_int(value: Any, *, field: str) -> int:
    parsed = _nonnegative_number(value, field=field)
    rounded = int(parsed)
    if abs(parsed - rounded) > 1e-9:
        raise ValueError(f"{field} must be an integer")
    return rounded


def normalize_hourly_fact(
    fact: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    user_id = clean_text(fact.get("user_id"), limit=128)
    account_id = clean_text(fact.get("ad_account_id"), limit=128)
    if not user_id or not account_id:
        raise ValueError("user_id and ad_account_id are required")

    hour_start = ensure_aware_utc(fact.get("hour_start_utc"), field="hour_start_utc")
    hour_end_raw = fact.get("hour_end_utc") or (hour_start + timedelta(hours=1))
    hour_end = ensure_aware_utc(hour_end_raw, field="hour_end_utc")
    if hour_start.minute or hour_start.second or hour_start.microsecond:
        raise ValueError("hour_start_utc must be aligned to the hour")
    if hour_end != hour_start + timedelta(hours=1):
        raise ValueError("hour_end_utc must be exactly one hour after hour_start_utc")

    account_timezone = clean_text(fact.get("account_timezone"), limit=80)
    if not account_timezone:
        raise ValueError("account_timezone is required")
    currency = clean_text(fact.get("currency"), limit=12).upper()
    if not currency:
        raise ValueError("currency is required")

    action_report_time = clean_text(
        fact.get("action_report_time") or "conversion",
        limit=32,
    ).lower()
    attribution_windows = dict(fact.get("attribution_windows") or {})
    attribution_key = build_attribution_key(action_report_time, attribution_windows)
    entity_type, external_id = derive_entity_identity(fact)
    current = ensure_aware_utc(now or _utcnow(), field="now")
    spend_native = _nonnegative_number(fact.get("spend_native"), field="spend_native")

    coverage = dict(fact.get("coverage") or {})
    coverage.setdefault("status", "complete")
    coverage.setdefault(
        "data_state",
        "confirmed_data" if spend_native > 0 else "confirmed_zero",
    )
    source = dict(fact.get("source") or {})
    source.pop("access_token", None)
    source.pop("refresh_token", None)
    source.pop("authorization", None)

    return {
        "user_id": user_id,
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": account_id,
        "entity_type": entity_type,
        "external_id": external_id,
        "campaign_id": clean_text(fact.get("campaign_id"), limit=128) or None,
        "ad_squad_id": clean_text(fact.get("ad_squad_id"), limit=128) or None,
        "ad_id": clean_text(fact.get("ad_id"), limit=128) or None,
        "hour_start_utc": hour_start,
        "hour_end_utc": hour_end,
        "account_timezone": account_timezone,
        "currency": currency,
        "action_report_time": action_report_time,
        "attribution_windows": attribution_windows,
        "attribution_key": attribution_key,
        "spend_native": spend_native,
        "impressions": _nonnegative_int(fact.get("impressions"), field="impressions"),
        "swipes": _nonnegative_int(fact.get("swipes"), field="swipes"),
        "video_views": _nonnegative_int(fact.get("video_views"), field="video_views"),
        "purchases": _nonnegative_int(fact.get("purchases"), field="purchases"),
        "purchase_value_native": _nonnegative_number(
            fact.get("purchase_value_native"),
            field="purchase_value_native",
        ),
        "coverage": coverage,
        "source": source,
        "provisional": bool(fact.get("provisional", current < hour_end)),
        "sync_run_id": clean_text(fact.get("sync_run_id"), limit=128),
    }


def hourly_fact_identity(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": fact["user_id"],
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": fact["ad_account_id"],
        "entity_type": fact["entity_type"],
        "external_id": fact["external_id"],
        "hour_start_utc": fact["hour_start_utc"],
        "action_report_time": fact["action_report_time"],
        "attribution_key": fact["attribution_key"],
    }


async def ensure_fact_indexes(db: Any) -> None:
    collection = db[SNAPCHAT_HOURLY_FACTS_COLLECTION]
    await collection.create_index(
        [
            ("user_id", 1),
            ("provider", 1),
            ("ad_account_id", 1),
            ("entity_type", 1),
            ("external_id", 1),
            ("hour_start_utc", 1),
            ("action_report_time", 1),
            ("attribution_key", 1),
        ],
        unique=True,
        name="snapchat_v2_hourly_fact_identity_unique",
    )
    await collection.create_index(
        [("user_id", 1), ("ad_account_id", 1), ("hour_start_utc", -1)],
        name="snapchat_v2_hourly_account_time",
    )
    await collection.create_index(
        [("user_id", 1), ("entity_type", 1), ("external_id", 1), ("hour_start_utc", -1)],
        name="snapchat_v2_hourly_entity_time",
    )
    await collection.create_index(
        [("sync_run_id", 1), ("updated_at", -1)],
        name="snapchat_v2_hourly_sync_run",
    )


async def upsert_hourly_fact(
    db: Any,
    fact: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized = normalize_hourly_fact(fact, now=now)
    identity = hourly_fact_identity(normalized)
    timestamp = ensure_aware_utc(now or _utcnow(), field="now")
    result = await db[SNAPCHAT_HOURLY_FACTS_COLLECTION].update_one(
        identity,
        {
            "$set": {
                **normalized,
                "updated_at": timestamp,
            },
            "$setOnInsert": {
                "created_at": timestamp,
            },
        },
        upsert=True,
    )
    return {
        "identity": identity,
        "inserted": getattr(result, "upserted_id", None) is not None,
        "matched": int(getattr(result, "matched_count", 0) or 0),
        "modified": int(getattr(result, "modified_count", 0) or 0),
    }


async def upsert_hourly_facts(
    db: Any,
    facts: Iterable[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    await ensure_fact_indexes(db)
    rows = list(facts)
    if len(rows) > MAX_FACT_ROWS_PER_WRITE:
        raise ValueError("Snapchat hourly fact write exceeded the safe row limit")
    inserted = matched = modified = 0
    seen: set[tuple[Any, ...]] = set()
    for fact in rows:
        normalized = normalize_hourly_fact(fact, now=now)
        identity = hourly_fact_identity(normalized)
        key = tuple(identity[field] for field in identity)
        if key in seen:
            raise ValueError("duplicate Snapchat hourly fact in one write batch")
        seen.add(key)
        result = await upsert_hourly_fact(db, normalized, now=now)
        inserted += int(result["inserted"])
        matched += int(result["matched"])
        modified += int(result["modified"])
    return {
        "rows_received": len(rows),
        "rows_saved": len(rows),
        "inserted": inserted,
        "matched": matched,
        "modified": modified,
    }


async def load_hourly_facts(
    db: Any,
    *,
    user_id: str,
    ad_account_id: str,
    start_utc: datetime,
    end_utc: datetime,
    entity_type: str | None = None,
    action_report_time: str | None = None,
    limit: int = MAX_FACT_ROWS_PER_WRITE,
) -> list[dict[str, Any]]:
    start = ensure_aware_utc(start_utc, field="start_utc")
    end = ensure_aware_utc(end_utc, field="end_utc")
    if end <= start:
        raise ValueError("end_utc must be after start_utc")
    query: dict[str, Any] = {
        "user_id": str(user_id),
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": str(ad_account_id),
        "hour_start_utc": {"$gte": start, "$lt": end},
    }
    if entity_type:
        query["entity_type"] = entity_type
    if action_report_time:
        query["action_report_time"] = clean_text(action_report_time, limit=32).lower()
    cursor = db[SNAPCHAT_HOURLY_FACTS_COLLECTION].find(query, {"_id": 0})
    if hasattr(cursor, "sort"):
        cursor = cursor.sort([("hour_start_utc", 1), ("entity_type", 1), ("external_id", 1)])
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(limit + 1)
    if hasattr(cursor, "to_list"):
        try:
            rows = list(await cursor.to_list(length=limit + 1))
        except TypeError:
            rows = list(await cursor.to_list(limit + 1))
    else:
        rows = []
        async for row in cursor:
            rows.append(row)
            if len(rows) > limit:
                break
    if len(rows) > limit:
        raise ValueError("Snapchat hourly fact read was truncated")
    return rows
