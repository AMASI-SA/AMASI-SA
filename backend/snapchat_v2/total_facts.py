"""Exact account-day TOTAL facts for Snapchat V2 hierarchy reporting.

Hourly facts remain the financial source of truth. These rows preserve the
provider's exact one-day hierarchy metrics, including non-additive reach and
frequency, so pages and Decision Intelligence never sum audience ratios.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable

from .models import (
    DEFAULT_SWIPE_ATTRIBUTION_WINDOW,
    DEFAULT_VIEW_ATTRIBUTION_WINDOW,
    SNAPCHAT_PROVIDER,
    build_attribution_key,
    clean_text,
    ensure_aware_utc,
)

SNAPCHAT_TOTAL_FACTS_COLLECTION = "mezan_snapchat_daily_total_facts_v2"
MAX_TOTAL_FACT_ROWS_PER_WRITE = 100_000

ADDITIVE_INT_FIELDS = (
    "impressions",
    "swipes",
    "video_views",
    "view_content",
    "add_to_cart",
    "start_checkout",
    "add_billing",
    "purchases",
)
ADDITIVE_FLOAT_FIELDS = (
    "spend_native",
    "view_completion",
    "purchase_value_native",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _number(value: Any, *, integer: bool = False) -> int | float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Snapchat TOTAL metric must be numeric") from exc
    if parsed < 0 or parsed != parsed or abs(parsed) == float("inf"):
        raise ValueError("Snapchat TOTAL metric must be finite and non-negative")
    if integer:
        rounded = int(parsed)
        if abs(parsed - rounded) > 1e-9:
            raise ValueError("Snapchat TOTAL integer metric is fractional")
        return rounded
    return parsed


def normalize_total_fact(
    fact: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    user_id = clean_text(fact.get("user_id"), limit=128)
    account_id = clean_text(fact.get("ad_account_id"), limit=128)
    entity_type = clean_text(fact.get("entity_type"), limit=32)
    external_id = clean_text(fact.get("external_id"), limit=128)
    report_date = str(fact.get("report_date") or "").strip()
    if entity_type not in {"campaign", "ad_squad", "ad"}:
        raise ValueError("invalid Snapchat TOTAL entity_type")
    if not user_id or not account_id or not external_id:
        raise ValueError("Snapchat TOTAL identity is incomplete")
    try:
        date.fromisoformat(report_date)
    except ValueError as exc:
        raise ValueError("report_date must use YYYY-MM-DD") from exc
    timezone_name = clean_text(fact.get("account_timezone"), limit=80)
    currency = clean_text(fact.get("currency"), limit=12).upper()
    if not timezone_name or not currency:
        raise ValueError("Snapchat TOTAL timezone and currency are required")
    action_report_time = clean_text(
        fact.get("action_report_time") or "conversion",
        limit=32,
    ).lower()
    attribution_windows = dict(fact.get("attribution_windows") or {})
    if not attribution_windows:
        attribution_windows = {
            "swipe": DEFAULT_SWIPE_ATTRIBUTION_WINDOW,
            "view": DEFAULT_VIEW_ATTRIBUTION_WINDOW,
        }
    normalized = {
        "user_id": user_id,
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": account_id,
        "entity_type": entity_type,
        "external_id": external_id,
        "campaign_id": clean_text(fact.get("campaign_id"), limit=128) or None,
        "ad_squad_id": clean_text(fact.get("ad_squad_id"), limit=128) or None,
        "ad_id": clean_text(fact.get("ad_id"), limit=128) or None,
        "report_date": report_date,
        "account_timezone": timezone_name,
        "currency": currency,
        "action_report_time": action_report_time,
        "attribution_windows": attribution_windows,
        "attribution_key": build_attribution_key(
            action_report_time,
            attribution_windows,
        ),
        "window_start_utc": ensure_aware_utc(
            fact.get("window_start_utc"),
            field="window_start_utc",
        ),
        "window_end_utc": ensure_aware_utc(
            fact.get("window_end_utc"),
            field="window_end_utc",
        ),
        "paid_reach": (
            int(_number(fact.get("paid_reach"), integer=True))
            if fact.get("paid_reach") is not None
            else None
        ),
        "paid_frequency": (
            float(_number(fact.get("paid_frequency")))
            if fact.get("paid_frequency") is not None
            else None
        ),
        "reach_frequency_scope": "exact_one_day_total",
        "coverage": dict(fact.get("coverage") or {}),
        "source": dict(fact.get("source") or {}),
        "sync_run_id": clean_text(fact.get("sync_run_id"), limit=128),
        "observed_at": ensure_aware_utc(now or _utcnow(), field="now"),
    }
    for field in ADDITIVE_INT_FIELDS:
        normalized[field] = int(_number(fact.get(field), integer=True))
    for field in ADDITIVE_FLOAT_FIELDS:
        normalized[field] = float(_number(fact.get(field)))
    if normalized["window_end_utc"] <= normalized["window_start_utc"]:
        raise ValueError("Snapchat TOTAL window is invalid")
    return normalized


def total_fact_identity(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": fact["user_id"],
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": fact["ad_account_id"],
        "entity_type": fact["entity_type"],
        "external_id": fact["external_id"],
        "report_date": fact["report_date"],
        "account_timezone": fact["account_timezone"],
        "action_report_time": fact["action_report_time"],
        "attribution_key": fact["attribution_key"],
    }


async def ensure_total_fact_indexes(db: Any) -> None:
    collection = db[SNAPCHAT_TOTAL_FACTS_COLLECTION]
    await collection.create_index(
        [
            ("user_id", 1),
            ("provider", 1),
            ("ad_account_id", 1),
            ("entity_type", 1),
            ("external_id", 1),
            ("report_date", 1),
            ("account_timezone", 1),
            ("action_report_time", 1),
            ("attribution_key", 1),
        ],
        unique=True,
        name="snapchat_v2_daily_total_fact_identity_unique",
    )
    await collection.create_index(
        [("user_id", 1), ("ad_account_id", 1), ("entity_type", 1), ("report_date", -1)],
        name="snapchat_v2_daily_total_entity_date",
    )


async def upsert_total_facts(
    db: Any,
    facts: Iterable[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    rows = list(facts)
    if len(rows) > MAX_TOTAL_FACT_ROWS_PER_WRITE:
        raise ValueError("Snapchat TOTAL fact write exceeded the safe row limit")
    await ensure_total_fact_indexes(db)
    timestamp = ensure_aware_utc(now or _utcnow(), field="now")
    seen: set[tuple[Any, ...]] = set()
    inserted = modified = 0
    for raw in rows:
        row = normalize_total_fact(raw, now=timestamp)
        identity = total_fact_identity(row)
        key = tuple(identity.values())
        if key in seen:
            raise ValueError("duplicate Snapchat TOTAL fact in one write batch")
        seen.add(key)
        result = await db[SNAPCHAT_TOTAL_FACTS_COLLECTION].update_one(
            identity,
            {
                "$set": {**row, "updated_at": timestamp},
                "$setOnInsert": {"created_at": timestamp},
            },
            upsert=True,
        )
        inserted += int(getattr(result, "upserted_id", None) is not None)
        modified += int(getattr(result, "modified_count", 0) or 0)
    return {
        "rows_received": len(rows),
        "rows_saved": len(rows),
        "inserted": inserted,
        "modified": modified,
    }


async def load_total_facts(
    db: Any,
    *,
    user_id: str,
    ad_account_id: str,
    entity_type: str,
    date_from: date,
    date_to: date,
    account_timezone: str,
    action_report_time: str = "conversion",
    limit: int = MAX_TOTAL_FACT_ROWS_PER_WRITE,
) -> list[dict[str, Any]]:
    action = clean_text(action_report_time, limit=32).lower()
    windows = {
        "swipe": DEFAULT_SWIPE_ATTRIBUTION_WINDOW,
        "view": DEFAULT_VIEW_ATTRIBUTION_WINDOW,
    }
    query = {
        "user_id": str(user_id),
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": str(ad_account_id),
        "entity_type": str(entity_type),
        "report_date": {"$gte": date_from.isoformat(), "$lte": date_to.isoformat()},
        "account_timezone": str(account_timezone),
        "action_report_time": action,
        "attribution_key": build_attribution_key(action, windows),
    }
    cursor = db[SNAPCHAT_TOTAL_FACTS_COLLECTION].find(query, {"_id": 0})
    if hasattr(cursor, "sort"):
        cursor = cursor.sort([("report_date", 1), ("external_id", 1)])
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(limit + 1)
    if hasattr(cursor, "to_list"):
        try:
            rows = list(await cursor.to_list(length=limit + 1))
        except TypeError:
            rows = list(await cursor.to_list(limit + 1))
    else:
        rows = [row async for row in cursor]
    if len(rows) > limit:
        raise ValueError("Snapchat TOTAL fact read was truncated")
    return rows


__all__ = [
    "ADDITIVE_FLOAT_FIELDS",
    "ADDITIVE_INT_FIELDS",
    "SNAPCHAT_TOTAL_FACTS_COLLECTION",
    "ensure_total_fact_indexes",
    "load_total_facts",
    "normalize_total_fact",
    "upsert_total_facts",
]
