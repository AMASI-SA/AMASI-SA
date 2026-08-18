"""Sequential temporal evidence for Campaign AI Decision Intelligence V3.

The module intentionally does *not* decide whether performance is good or bad.
It gives OpenAI the ordered evidence needed to reason Today -> Yesterday ->
Day-2, then 7d/30d baselines.  The established provider loaders remain the
source of truth, including Snapchat conversion-time semantics.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


EntityLoader = Callable[[Any, str, date, date], Awaitable[list[dict[str, Any]]]]
CampaignLoader = Callable[[Any, str, str, date, date], Awaitable[list[dict[str, Any]]]]

CORE_METRICS = (
    "spend_sar",
    "revenue_sar",
    "purchases",
    "impressions",
    "clicks",
    "roas",
    "cpa_sar",
    "ctr_pct",
    "spend_per_day_sar",
    "data_complete",
    "data_quality",
    "current_daily_budget_native",
)


def entity_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("provider") or ""),
        str(row.get("entity_level") or ""),
        str(row.get("account_id") or ""),
        str(row.get("entity_id") or ""),
    )


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _account_day_fraction(row: dict[str, Any], current: datetime) -> float | None:
    timezone_name = str(row.get("account_timezone") or "").strip()
    try:
        local = current.astimezone(ZoneInfo(timezone_name)) if timezone_name else current.astimezone(timezone.utc)
    except ZoneInfoNotFoundError:
        local = current.astimezone(timezone.utc)
    elapsed = local.hour * 3600 + local.minute * 60 + local.second
    return round(min(max(elapsed / 86400.0, 0.0), 1.0), 4)


def _row_snapshot(
    row: dict[str, Any] | None,
    *,
    days: int,
    label: str,
    current: datetime,
) -> dict[str, Any]:
    if not row:
        return {
            "window": label,
            "available": False,
            "days": days,
            "day_fraction_elapsed": None,
            "metrics": {},
            "daily_average": {},
            "source": None,
            "limitations": ["entity_not_observed_in_window"],
        }
    metrics = {key: row.get(key) for key in CORE_METRICS}
    spend = _number(row.get("spend_sar"))
    revenue = _number(row.get("revenue_sar"))
    purchases = _number(row.get("purchases"))
    daily_average = {
        "spend_sar": round(spend / days, 2) if spend is not None and days > 0 else None,
        "revenue_sar": round(revenue / days, 2) if revenue is not None and days > 0 else None,
        "purchases": round(purchases / days, 3) if purchases is not None and days > 0 else None,
    }
    return {
        "window": label,
        "available": True,
        "days": days,
        "day_fraction_elapsed": _account_day_fraction(row, current) if label == "today" else 1.0,
        "metrics": metrics,
        "daily_average": daily_average,
        "source": {
            "provider_result_source": row.get("provider_result_source"),
            "action_report_time": row.get("action_report_time"),
            "result_source": row.get("result_source"),
            "source_date_from": row.get("source_date_from"),
            "source_date_to": row.get("source_date_to"),
            "account_timezone": row.get("account_timezone"),
        },
        "limitations": [] if row.get("data_complete") else ["provider_window_partial_or_incomplete"],
    }


async def _load_entities(
    db: Any,
    user_id: str,
    start: date,
    end: date,
    *,
    campaign_loader: CampaignLoader,
    snapchat_child_loader: EntityLoader,
    meta_child_loader: EntityLoader,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for provider in ("snapchat", "meta"):
        try:
            rows.extend(await campaign_loader(db, user_id, provider, start, end))
        except Exception:
            # A missing source remains explicit as missing evidence.  The caller
            # already records provider-source errors in the main run document.
            continue
    try:
        rows.extend(await snapchat_child_loader(db, user_id, start, end))
    except Exception:
        pass
    try:
        rows.extend(await meta_child_loader(db, user_id, start, end))
    except Exception:
        pass
    return rows


async def build_sequential_temporal_evidence(
    db: Any,
    user_id: str,
    candidates: list[dict[str, Any]],
    *,
    end: date,
    current: datetime,
    campaign_loader: CampaignLoader,
    snapchat_child_loader: EntityLoader,
    meta_child_loader: EntityLoader,
) -> dict[str, Any]:
    """Load ordered current-day evidence and non-decision historical baselines."""
    windows = {
        "today": (end, end, 1),
        "yesterday": (end - timedelta(days=1), end - timedelta(days=1), 1),
        "day_minus_2": (end - timedelta(days=2), end - timedelta(days=2), 1),
        "baseline_7d": (end - timedelta(days=6), end, 7),
        "baseline_30d": (end - timedelta(days=29), end, 30),
    }
    loaded: dict[str, dict[tuple[str, str, str, str], dict[str, Any]]] = {}
    for label, (start, stop, _days) in windows.items():
        rows = await _load_entities(
            db,
            user_id,
            start,
            stop,
            campaign_loader=campaign_loader,
            snapchat_child_loader=snapchat_child_loader,
            meta_child_loader=meta_child_loader,
        )
        loaded[label] = {entity_key(row): row for row in rows}

    entities: dict[str, Any] = {}
    for candidate in candidates:
        key = entity_key(candidate)
        stable_key = "|".join(key)
        entities[stable_key] = {
            "provider": key[0],
            "entity_level": key[1],
            "account_id": key[2] or None,
            "entity_id": key[3],
            "entity_name": candidate.get("entity_name"),
            "today": _row_snapshot(loaded["today"].get(key), days=1, label="today", current=current),
            "yesterday": _row_snapshot(loaded["yesterday"].get(key), days=1, label="yesterday", current=current),
            "day_minus_2": _row_snapshot(loaded["day_minus_2"].get(key), days=1, label="day_minus_2", current=current),
            "baseline_7d": _row_snapshot(loaded["baseline_7d"].get(key), days=7, label="baseline_7d", current=current),
            "baseline_30d": _row_snapshot(loaded["baseline_30d"].get(key), days=30, label="baseline_30d", current=current),
        }

    return {
        "schema_version": "campaign_ai_temporal_evidence_v3",
        "reasoning_order": ["today", "yesterday", "day_minus_2", "baseline_7d", "baseline_30d"],
        "contract": {
            "three_day_aggregate_is_primary_rule": False,
            "today_is_examined_first": True,
            "yesterday_is_examined_only_as_followup_context": True,
            "day_minus_2_establishes_persistence_context": True,
            "baseline_7d_is_context_not_rule": True,
            "baseline_30d_is_context_not_rule": True,
            "incomplete_day_must_be_treated_as_partial_evidence": True,
            "insufficient_evidence_may_return": ["INSUFFICIENT_DATA", "MONITOR", "NO_ACTION_INSUFFICIENT_DATA"],
        },
        "entities": entities,
    }


__all__ = ["build_sequential_temporal_evidence", "entity_key"]
