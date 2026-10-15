"""Sequential temporal evidence for Campaign AI Decision Intelligence V3.

The established provider sync remains the source of truth.  This module reads
its persisted daily facts instead of making five additional provider calls per
entity.  It gives OpenAI ordered Today -> Yesterday -> Day-2 evidence followed
by 7d and 30d baselines.  No metric threshold in this module chooses an action.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from integrations_control_center.meta_campaign_reporting import (
    META_CAMPAIGN_REPORTING_COLLECTION,
)
from unified_marketing.gateway import load_unified_marketing_entity_daily_series


META_ENTITY_COLLECTION = "mezan_meta_entity_performance_daily_v1"
ACTION_REPORT_TIME = "conversion"
MAX_FACT_ROWS = 100


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
        local = (
            current.astimezone(ZoneInfo(timezone_name))
            if timezone_name
            else current.astimezone(timezone.utc)
        )
    except ZoneInfoNotFoundError:
        local = current.astimezone(timezone.utc)
    elapsed = local.hour * 3600 + local.minute * 60 + local.second
    return round(min(max(elapsed / 86400.0, 0.0), 1.0), 4)


def _candidate_end(row: dict[str, Any], fallback: date) -> date:
    raw = str(row.get("source_date_to") or "")[:10]
    try:
        return date.fromisoformat(raw) if raw else fallback
    except ValueError:
        return fallback


def _snap_fact(doc: dict[str, Any]) -> dict[str, Any]:
    metrics = doc.get("metrics") if isinstance(doc.get("metrics"), dict) else {}
    computed = doc.get("computed") if isinstance(doc.get("computed"), dict) else {}
    spend = _number(doc.get("spend_sar"))
    revenue = _number(doc.get("purchase_value_sar"))
    purchases = _number(doc.get("purchases") or metrics.get("conversion_purchases"))
    impressions = _number(metrics.get("impressions"))
    clicks = _number(metrics.get("swipes"))
    return {
        "date": str(doc.get("date") or "")[:10],
        "spend_sar": spend,
        "revenue_sar": revenue,
        "purchases": purchases,
        "impressions": impressions,
        "clicks": clicks,
        "roas": _number(computed.get("roas")),
        "cpa_sar": _number(computed.get("cost_per_purchase")),
        "ctr_pct": (
            round(float(computed["ctr"]) * 100.0, 4)
            if _number(computed.get("ctr")) is not None
            else None
        ),
        "cpc_sar": _number(computed.get("cpc")),
        "cpm_sar": _number(computed.get("cpm")),
        "source": doc.get("source_mode"),
        "action_report_time": doc.get("action_report_time")
        or (doc.get("conversion_reporting") or {}).get("action_report_time"),
        "provider_window_start": doc.get("provider_window_start"),
        "provider_window_end": doc.get("provider_window_end"),
    }


def _meta_fact(doc: dict[str, Any]) -> dict[str, Any]:
    spend = _number(doc.get("spend_sar"))
    revenue = _number(doc.get("purchase_value_sar"))
    if revenue is None:
        revenue = _number(doc.get("revenue_sar"))
    purchases = _number(doc.get("purchases"))
    impressions = _number(doc.get("impressions"))
    clicks = _number(doc.get("clicks"))
    return {
        "date": str(doc.get("date") or "")[:10],
        "spend_sar": spend,
        "revenue_sar": revenue,
        "purchases": purchases,
        "impressions": impressions,
        "clicks": clicks,
        "roas": round(revenue / spend, 4) if spend not in {None, 0} and revenue is not None else None,
        "cpa_sar": round(spend / purchases, 4) if spend is not None and purchases not in {None, 0} else None,
        "ctr_pct": round(clicks / impressions * 100, 4) if clicks is not None and impressions not in {None, 0} else None,
        "cpc_sar": round(spend / clicks, 4) if spend is not None and clicks not in {None, 0} else None,
        "cpm_sar": round(spend * 1000 / impressions, 4) if spend is not None and impressions not in {None, 0} else None,
        "source": doc.get("source_mode"),
        "action_report_time": "conversion",
        "provider_window_start": doc.get("date_start") or doc.get("date"),
        "provider_window_end": doc.get("date_stop") or doc.get("date"),
    }


async def _daily_facts(
    db: Any,
    user_id: str,
    candidate: dict[str, Any],
    *,
    end: date,
) -> list[dict[str, Any]]:
    provider, level, account_id, entity_id = entity_key(candidate)
    start = end - timedelta(days=29)
    if provider == "snapchat":
        if level not in {"campaign", "ad_group", "ad"}:
            return []
        report = await load_unified_marketing_entity_daily_series(
            db,
            user_id,
            provider="snapchat_ads",
            entity_level=level,
            entity_ids=[entity_id],
            date_from=start,
            date_to=end,
            timezone_name=str(candidate.get("account_timezone") or ""),
        )
        output = []
        for doc in report.get("rows") or []:
            delivery = doc.get("delivery") or {}
            platform = doc.get("platform_outcomes") or {}
            spend = _number((delivery.get("spend_sar") or {}).get("amount"))
            native_spend = _number((delivery.get("spend") or {}).get("amount"))
            native_revenue = _number((platform.get("revenue") or {}).get("amount"))
            revenue = (
                round(native_revenue * spend / native_spend, 4)
                if native_revenue is not None
                and spend is not None
                and native_spend not in {None, 0}
                else None
            )
            purchases = _number(platform.get("conversions"))
            impressions = _number(delivery.get("impressions"))
            clicks = _number(delivery.get("clicks"))
            output.append({
                "date": str((doc.get("period") or {}).get("date_from") or "")[:10],
                "spend_sar": spend,
                "revenue_sar": revenue,
                "purchases": purchases,
                "impressions": impressions,
                "clicks": clicks,
                "roas": round(revenue / spend, 4) if spend not in {None, 0} and revenue is not None else None,
                "cpa_sar": round(spend / purchases, 4) if spend is not None and purchases not in {None, 0} else None,
                "ctr_pct": delivery.get("ctr_pct"),
                "cpc_sar": round(spend / clicks, 4) if spend is not None and clicks not in {None, 0} else None,
                "cpm_sar": round(spend * 1000 / impressions, 4) if spend is not None and impressions not in {None, 0} else None,
                "source": (doc.get("lineage") or {}).get("source_collection"),
                "action_report_time": (doc.get("period") or {}).get("action_report_time"),
                "provider_window_start": (doc.get("period") or {}).get("date_from"),
                "provider_window_end": (doc.get("period") or {}).get("date_to"),
            })
        return output

    if provider == "meta" and level == "campaign":
        query = {
            "user_id": user_id,
            "ad_account_id": account_id,
            "campaign_id": entity_id,
            "date": {"$gte": start.isoformat(), "$lte": end.isoformat()},
        }
        docs = await db[META_CAMPAIGN_REPORTING_COLLECTION].find(
            query,
            {"_id": 0},
        ).sort("date", 1).limit(MAX_FACT_ROWS).to_list(length=MAX_FACT_ROWS)
        return [_meta_fact(doc) for doc in docs]

    if provider == "meta" and level in {"ad_group", "ad"}:
        query = {
            "user_id": user_id,
            "ad_account_id": account_id,
            "entity_level": level,
            "entity_id": entity_id,
            "date": {"$gte": start.isoformat(), "$lte": end.isoformat()},
        }
        docs = await db[META_ENTITY_COLLECTION].find(
            query,
            {"_id": 0},
        ).sort("date", 1).limit(MAX_FACT_ROWS).to_list(length=MAX_FACT_ROWS)
        return [_meta_fact(doc) for doc in docs]
    return []


def _aggregate(rows: list[dict[str, Any]], *, days: int, label: str, current: datetime, candidate: dict[str, Any]) -> dict[str, Any]:
    if not rows:
        return {
            "window": label,
            "available": False,
            "days": days,
            "day_fraction_elapsed": _account_day_fraction(candidate, current) if label == "today" else 1.0,
            "metrics": {},
            "daily_average": {},
            "source": None,
            "limitations": ["persisted_daily_fact_not_available"],
        }

    def total(key: str) -> float | None:
        values = [_number(row.get(key)) for row in rows]
        usable = [value for value in values if value is not None]
        return round(sum(usable), 4) if usable else None

    spend = total("spend_sar")
    revenue = total("revenue_sar")
    purchases = total("purchases")
    impressions = total("impressions")
    clicks = total("clicks")
    metrics = {
        "spend_sar": spend,
        "revenue_sar": revenue,
        "purchases": purchases,
        "impressions": impressions,
        "clicks": clicks,
        "roas": round(revenue / spend, 4) if spend not in {None, 0} and revenue is not None else None,
        "cpa_sar": round(spend / purchases, 4) if spend is not None and purchases not in {None, 0} else None,
        "ctr_pct": round(clicks / impressions * 100, 4) if clicks is not None and impressions not in {None, 0} else None,
        "cpc_sar": round(spend / clicks, 4) if spend is not None and clicks not in {None, 0} else None,
        "cpm_sar": round(spend * 1000 / impressions, 4) if spend is not None and impressions not in {None, 0} else None,
        "current_daily_budget_native": candidate.get("current_daily_budget_native"),
    }
    source_modes = sorted({str(row.get("source") or "") for row in rows if row.get("source")})
    report_times = sorted({str(row.get("action_report_time") or "") for row in rows if row.get("action_report_time")})
    limitations = []
    expected_rows = min(days, 30)
    if len({row.get("date") for row in rows}) < expected_rows:
        limitations.append("persisted_window_has_missing_days")
    if candidate.get("provider") == "snapchat" and report_times and report_times != [ACTION_REPORT_TIME]:
        limitations.append("snapchat_window_not_pure_conversion_time")
    return {
        "window": label,
        "available": True,
        "days": days,
        "observed_days": len({row.get("date") for row in rows}),
        "day_fraction_elapsed": _account_day_fraction(candidate, current) if label == "today" else 1.0,
        "metrics": metrics,
        "daily_average": {
            "spend_sar": round(spend / days, 2) if spend is not None else None,
            "revenue_sar": round(revenue / days, 2) if revenue is not None else None,
            "purchases": round(purchases / days, 3) if purchases is not None else None,
        },
        "source": {
            "persisted_fact_collections": source_modes,
            "action_report_times": report_times,
            "account_timezone": candidate.get("account_timezone"),
        },
        "limitations": limitations,
    }


async def build_sequential_temporal_evidence(
    db: Any,
    user_id: str,
    candidates: list[dict[str, Any]],
    *,
    end: date,
    current: datetime,
    **_ignored: Any,
) -> dict[str, Any]:
    """Read ordered daily facts and contextual baselines without provider calls."""
    entities: dict[str, Any] = {}
    for candidate in candidates:
        candidate_end = _candidate_end(candidate, end)
        facts = await _daily_facts(db, user_id, candidate, end=candidate_end)
        by_day = {str(row.get("date") or ""): row for row in facts}
        today = candidate_end
        yesterday = today - timedelta(days=1)
        day_minus_2 = today - timedelta(days=2)
        last_7_start = today - timedelta(days=6)
        last_30_start = today - timedelta(days=29)
        key = "|".join(entity_key(candidate))
        entities[key] = {
            "provider": candidate.get("provider"),
            "entity_level": candidate.get("entity_level"),
            "account_id": candidate.get("account_id"),
            "entity_id": candidate.get("entity_id"),
            "entity_name": candidate.get("entity_name"),
            "today": _aggregate(
                [by_day[today.isoformat()]] if today.isoformat() in by_day else [],
                days=1, label="today", current=current, candidate=candidate,
            ),
            "yesterday": _aggregate(
                [by_day[yesterday.isoformat()]] if yesterday.isoformat() in by_day else [],
                days=1, label="yesterday", current=current, candidate=candidate,
            ),
            "day_minus_2": _aggregate(
                [by_day[day_minus_2.isoformat()]] if day_minus_2.isoformat() in by_day else [],
                days=1, label="day_minus_2", current=current, candidate=candidate,
            ),
            "baseline_7d": _aggregate(
                [row for row in facts if last_7_start.isoformat() <= str(row.get("date") or "") <= today.isoformat()],
                days=7, label="baseline_7d", current=current, candidate=candidate,
            ),
            "baseline_30d": _aggregate(
                [row for row in facts if last_30_start.isoformat() <= str(row.get("date") or "") <= today.isoformat()],
                days=30, label="baseline_30d", current=current, candidate=candidate,
            ),
        }

    return {
        "schema_version": "campaign_ai_temporal_evidence_v3",
        "reasoning_order": ["today", "yesterday", "day_minus_2", "baseline_7d", "baseline_30d"],
        "contract": {
            "three_day_aggregate_is_primary_rule": False,
            "today_is_examined_first": True,
            "yesterday_is_followup_context": True,
            "day_minus_2_establishes_persistence_context": True,
            "baseline_7d_is_context_not_rule": True,
            "baseline_30d_is_context_not_rule": True,
            "incomplete_day_must_be_treated_as_partial_evidence": True,
            "data_source": "persisted_provider_daily_facts_no_extra_provider_calls",
        },
        "entities": entities,
    }


__all__ = ["build_sequential_temporal_evidence", "entity_key"]
