"""Hourly AI monitoring and explicitly approved execution for Snapchat and Meta.

The worker refreshes bounded provider facts, screens entities deterministically,
asks OpenAI to explain/prioritise only the screened evidence, and persists a
small recommendation snapshot for the dashboard. The worker never changes ads;
provider writes exist only behind the separate, owner-only approval endpoint.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Literal

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from integrations_control_center.meta_campaign_reporting import _paged_get
from integrations_control_center.meta_native_reporting import (
    _accounts as _meta_accounts,
    _action_value as _meta_action_value,
    _credential as _meta_credential,
    _fx_to_sar as _meta_fx_to_sar,
)
from integrations_control_center.meta_oauth_security import (
    meta_appsecret_proof,
    meta_graph_base,
)
from integrations_control_center.snapchat_account_selection import (
    _load_selected_accounts as _snapchat_accounts,
)
from integrations_control_center.snapchat_ad_performance import (
    build_account_timezone_ad_report,
)
from integrations_control_center.snapchat_adsquad_performance import (
    build_account_timezone_adsquad_report,
)


logger = logging.getLogger(__name__)
RIYADH_OFFSET = timezone(timedelta(hours=3))
RECOMMENDATION_COLLECTION = "mezan_campaign_ai_recommendations_v1"
RUN_COLLECTION = "mezan_campaign_ai_runs_v1"
META_ENTITY_COLLECTION = "mezan_meta_entity_performance_daily_v1"
LOCK_COLLECTION = "mezan_campaign_ai_scheduler_locks_v1"
EXECUTION_COLLECTION = "mezan_campaign_ai_executions_v1"
LOCK_ID = "campaign_ai_hourly_monitor"
DEFAULT_INTERVAL_SECONDS = 60 * 60
# Run the first pass shortly after boot so a fresh deployment does not leave
# the dashboard stuck on "waiting for first run" for several minutes.  The
# worker is detached from FastAPI startup, so this does not delay readiness.
DEFAULT_INITIAL_DELAY_SECONDS = 5
SCHEDULER_LEASE_SECONDS = 10 * 60
MONITOR_TIMEOUT_SECONDS = 8 * 60
MAX_ENTITY_ROWS = 300
MAX_AI_CANDIDATES = 60
MAX_RECOMMENDATIONS = 18
TARGET_CPA_SAR = float(os.environ.get("MEZAN_CAMPAIGN_TARGET_CPA_SAR", "56.25"))
TARGET_ROAS = float(os.environ.get("MEZAN_CAMPAIGN_TARGET_ROAS", "2.5"))
MIN_WASTE_SPEND_SAR = float(os.environ.get("MEZAN_CAMPAIGN_MIN_WASTE_SPEND_SAR", "75"))
FAST_SPEND_DAILY_SAR = float(
    os.environ.get("MEZAN_CAMPAIGN_FAST_SPEND_DAILY_SAR", str(TARGET_CPA_SAR * 2))
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).astimezone(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _text(value: Any, *, limit: int = 180) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _active(value: Any) -> bool:
    return _text(value).upper() in {
        "ACTIVE", "ENABLED", "RUNNING", "DELIVERING",
    }


def _safe_metric(value: Any, digits: int = 2) -> float | None:
    parsed = _number(value)
    return round(parsed, digits) if parsed is not None and parsed >= 0 else None


class RecommendationItem(BaseModel):
    recommendation_id: str
    provider: Literal["snapchat", "meta"]
    entity_level: Literal["campaign", "ad_group", "ad"]
    entity_id: str
    entity_name: str
    parent_name: str | None = None
    action: Literal["pause", "reduce", "monitor", "maintain", "scale"]
    change_percent: int | None = Field(default=None, ge=5, le=30)
    priority: Literal["critical", "high", "medium", "low"]
    confidence: Literal["high", "medium", "low"]
    title: str
    rationale: str
    evidence: list[str] = Field(default_factory=list)
    guardrail: str
    next_check_at: str


class RecommendationOutput(BaseModel):
    summary: str
    recommendations: list[RecommendationItem]
    limitations: list[str] = Field(default_factory=list)


class RecommendationApprovalInput(BaseModel):
    snapshot_id: str = Field(min_length=8, max_length=160)


AI_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "recommendations": {
            "type": "array",
            "maxItems": MAX_RECOMMENDATIONS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "recommendation_id": {"type": "string"},
                    "provider": {"type": "string", "enum": ["snapchat", "meta"]},
                    "entity_level": {"type": "string", "enum": ["campaign", "ad_group", "ad"]},
                    "entity_id": {"type": "string"},
                    "entity_name": {"type": "string"},
                    "parent_name": {"type": ["string", "null"]},
                    "action": {"type": "string", "enum": ["pause", "reduce", "monitor", "maintain", "scale"]},
                    "change_percent": {"type": ["integer", "null"], "minimum": 5, "maximum": 30},
                    "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "title": {"type": "string"},
                    "rationale": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                    "guardrail": {"type": "string"},
                    "next_check_at": {"type": "string"},
                },
                "required": [
                    "recommendation_id", "provider", "entity_level", "entity_id",
                    "entity_name", "parent_name", "action", "priority", "confidence",
                    "change_percent", "title", "rationale", "evidence", "guardrail", "next_check_at",
                ],
            },
        },
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "recommendations", "limitations"],
}


async def ensure_campaign_ai_indexes(db: Any) -> None:
    await db[RECOMMENDATION_COLLECTION].create_index(
        [("user_id", 1), ("generated_at", -1)],
        name="campaign_ai_user_latest",
    )
    await db[RECOMMENDATION_COLLECTION].create_index(
        [("user_id", 1), ("fingerprint", 1)],
        name="campaign_ai_user_fingerprint",
    )
    await db[RUN_COLLECTION].create_index(
        [("user_id", 1), ("started_at", -1)],
        name="campaign_ai_run_user_latest",
    )
    await db[RUN_COLLECTION].create_index(
        "expires_at",
        expireAfterSeconds=0,
        name="campaign_ai_run_ttl",
    )
    await db[META_ENTITY_COLLECTION].create_index(
        [
            ("user_id", 1), ("ad_account_id", 1), ("entity_level", 1),
            ("entity_id", 1), ("date", 1),
        ],
        unique=True,
        name="meta_ai_entity_user_account_level_date_unique",
    )
    await db[LOCK_COLLECTION].create_index(
        "lock_id", unique=True, name="campaign_ai_scheduler_lock_unique"
    )
    await db[LOCK_COLLECTION].create_index(
        "expires_at", expireAfterSeconds=0, name="campaign_ai_scheduler_lock_ttl"
    )
    await db[EXECUTION_COLLECTION].create_index(
        [("user_id", 1), ("snapshot_id", 1), ("recommendation_id", 1)],
        unique=True,
        name="campaign_ai_approval_idempotency",
    )


async def _refresh_meta_entities(
    db: Any,
    user_id: str,
    *,
    start: date,
    end: date,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Refresh only Meta analytical facts at ad-set and ad grain."""
    current = now().astimezone(timezone.utc)
    access_token = await _meta_credential(db, user_id, current)
    accounts = await _meta_accounts(db, user_id)
    observed_at = _iso(current)
    saved = 0
    provider_calls = 0
    errors: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=35.0) as client:
        for account in accounts:
            account_id = _text(account.get("ad_account_id"), limit=120)
            cursor = start
            while cursor <= end:
                for level in ("adset", "ad"):
                    fields = (
                        "campaign_id,campaign_name,adset_id,adset_name,"
                        + ("ad_id,ad_name," if level == "ad" else "")
                        + "spend,impressions,clicks,actions,action_values,"
                        "account_currency,date_start,date_stop"
                    )
                    try:
                        rows, calls = await _paged_get(
                            client,
                            f"{meta_graph_base()}/{account_id}/insights",
                            {
                                "access_token": access_token,
                                "appsecret_proof": meta_appsecret_proof(access_token),
                                "fields": fields,
                                "time_range": json.dumps(
                                    {"since": cursor.isoformat(), "until": cursor.isoformat()},
                                    separators=(",", ":"),
                                ),
                                "time_increment": 1,
                                "level": level,
                                "action_report_time": "conversion",
                                "use_account_attribution_setting": "true",
                                "use_unified_attribution_setting": "true",
                                "limit": 500,
                            },
                            operation=f"meta_ai_{level}_insights",
                        )
                        provider_calls += calls
                    except Exception as exc:  # provider errors are isolated by level/day
                        errors.append({
                            "account_id": account_id,
                            "date": cursor.isoformat(),
                            "level": level,
                            "code": _text(getattr(exc, "code", type(exc).__name__), limit=100),
                        })
                        continue
                    await db[META_ENTITY_COLLECTION].delete_many({
                        "user_id": user_id,
                        "ad_account_id": account_id,
                        "entity_level": "ad_group" if level == "adset" else "ad",
                        "date": cursor.isoformat(),
                    })
                    for row in rows[:2000]:
                        entity_id = _text(
                            row.get("adset_id") if level == "adset" else row.get("ad_id"),
                            limit=120,
                        )
                        if not entity_id:
                            continue
                        currency = _text(
                            row.get("account_currency") or account.get("currency"),
                            limit=12,
                        ).upper() or None
                        fx_rate, fx_source = _meta_fx_to_sar(currency)
                        spend_native = float(row.get("spend") or 0)
                        purchases, purchase_action = _meta_action_value(row.get("actions"))
                        revenue_native, revenue_action = _meta_action_value(row.get("action_values"))
                        document = {
                            "user_id": user_id,
                            "provider": "meta",
                            "ad_account_id": account_id,
                            "account_name": _text(account.get("display_name")),
                            "entity_level": "ad_group" if level == "adset" else "ad",
                            "entity_id": entity_id,
                            "entity_name": _text(
                                row.get("adset_name") if level == "adset" else row.get("ad_name")
                            ) or entity_id,
                            "campaign_id": _text(row.get("campaign_id"), limit=120),
                            "campaign_name": _text(row.get("campaign_name")),
                            "ad_group_id": _text(row.get("adset_id"), limit=120),
                            "ad_group_name": _text(row.get("adset_name")),
                            "date": cursor.isoformat(),
                            "currency_native": currency,
                            "fx_rate_to_sar": fx_rate,
                            "fx_source": fx_source,
                            "spend_native": spend_native,
                            "spend_sar": round(spend_native * fx_rate, 2) if fx_rate else None,
                            "revenue_native": revenue_native,
                            "revenue_sar": round(revenue_native * fx_rate, 2) if fx_rate else None,
                            "purchases": purchases,
                            "impressions": int(float(row.get("impressions") or 0)),
                            "clicks": int(float(row.get("clicks") or 0)),
                            "purchase_action_type": purchase_action,
                            "revenue_action_type": revenue_action,
                            "source_mode": "meta_ai_entity_reporting_v1",
                            "source_only": True,
                            "observed_at": observed_at,
                            "updated_at": observed_at,
                        }
                        await db[META_ENTITY_COLLECTION].update_one(
                            {
                                "user_id": user_id,
                                "ad_account_id": account_id,
                                "entity_level": document["entity_level"],
                                "entity_id": entity_id,
                                "date": cursor.isoformat(),
                            },
                            {"$set": document, "$setOnInsert": {"created_at": observed_at}},
                            upsert=True,
                        )
                        saved += 1
                cursor += timedelta(days=1)
    return {"rows_saved": saved, "provider_calls": provider_calls, "errors": errors[:50]}


def _entity(
    *,
    provider: str,
    level: str,
    entity_id: Any,
    entity_name: Any,
    parent_name: Any,
    status: Any,
    spend_sar: Any,
    revenue_sar: Any,
    purchases: Any,
    impressions: Any,
    clicks: Any,
    observed_days: Any,
    data_complete: Any,
    account_id: Any = None,
    parent_id: Any = None,
    current_daily_budget_native: Any = None,
) -> dict[str, Any] | None:
    clean_id = _text(entity_id, limit=120)
    if not clean_id:
        return None
    spend = _safe_metric(spend_sar)
    revenue = _safe_metric(revenue_sar)
    order_count = _safe_metric(purchases, 0)
    order_int = int(order_count) if order_count is not None else None
    return {
        "provider": provider,
        "entity_level": level,
        "entity_id": clean_id,
        "entity_name": _text(entity_name) or clean_id,
        "parent_name": _text(parent_name) or None,
        "status": _text(status, limit=60) or "unknown",
        "active": _active(status),
        "spend_sar": spend,
        "revenue_sar": revenue,
        "purchases": order_int,
        "impressions": int(_safe_metric(impressions, 0) or 0),
        "clicks": int(_safe_metric(clicks, 0) or 0),
        "roas": round(revenue / spend, 2) if spend and revenue is not None else None,
        "cpa_sar": round(spend / order_int, 2) if spend and order_int else None,
        "observed_days": int(_safe_metric(observed_days, 0) or 0),
        "data_complete": bool(data_complete),
        "account_id": _text(account_id, limit=120) or None,
        "parent_id": _text(parent_id, limit=120) or None,
        "current_daily_budget_native": _safe_metric(current_daily_budget_native, 6),
    }


async def _campaign_entities(
    db: Any,
    user_id: str,
    provider: str,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    from ads_manager.service import AdsManagerService

    overview = await AdsManagerService(db).overview(
        user_id,
        date_from=start.isoformat(),
        date_to=end.isoformat(),
        provider=provider,
        campaign_query=None,
        page=1,
        limit=100,
    )
    rows = []
    provider_summary = next(
        (
            item for item in overview.get("providers") or []
            if item.get("provider") == provider
        ),
        {},
    )
    coverage = provider_summary.get("performance_coverage") or {}
    observed_days = coverage.get("observed_days") or (end - start).days + 1
    data_complete = coverage.get("status") in {None, "complete"}
    for item in overview.get("campaigns") or []:
        row = _entity(
            provider=provider,
            level="campaign",
            entity_id=item.get("campaign_id"),
            entity_name=item.get("campaign_name"),
            parent_name=None,
            status=item.get("delivery_status") or item.get("status"),
            spend_sar=item.get("spend_sar_equivalent"),
            revenue_sar=item.get("revenue_sar_equivalent"),
            purchases=item.get("purchases"),
            impressions=item.get("impressions"),
            clicks=item.get("clicks"),
            observed_days=observed_days,
            data_complete=data_complete,
            account_id=item.get("account_id") or item.get("ad_account_id"),
            current_daily_budget_native=(item.get("budget") or {}).get("daily_native"),
        )
        if row:
            rows.append(row)
    return rows


async def _snapchat_child_entities(
    db: Any,
    user_id: str,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    accounts = await _snapchat_accounts(db, user_id)
    for account in accounts:
        account_id = _text(account.get("ad_account_id"), limit=120)
        reports = [
            await build_account_timezone_adsquad_report(
                db, user_id, account_id=account_id,
                from_date=start.isoformat(), to_date=end.isoformat(), query=None,
                page=1, limit=100, active_campaigns_only=False, sort_by="spend",
            ),
            await build_account_timezone_ad_report(
                db, user_id, account_id=account_id,
                from_date=start.isoformat(), to_date=end.isoformat(), query=None,
                page=1, limit=100, active_campaigns_only=False, sort_by="spend",
            ),
        ]
        for item in reports[0].get("ad_squads") or []:
            row = _entity(
                provider="snapchat", level="ad_group",
                entity_id=item.get("ad_squad_id"), entity_name=item.get("ad_squad_name"),
                parent_name=item.get("campaign_name"), status=item.get("delivery_status") or item.get("status"),
                spend_sar=item.get("spend_sar"), revenue_sar=item.get("sales_sar"),
                purchases=item.get("orders"), impressions=item.get("impressions"), clicks=item.get("swipes"),
                observed_days=item.get("observed_days"), data_complete=item.get("data_complete"),
                account_id=account_id, parent_id=item.get("campaign_id"),
                current_daily_budget_native=(item.get("budget") or {}).get("daily_native"),
            )
            if row:
                rows.append(row)
        for item in reports[1].get("ads") or []:
            row = _entity(
                provider="snapchat", level="ad",
                entity_id=item.get("ad_id"), entity_name=item.get("ad_name"),
                parent_name=item.get("ad_squad_name") or item.get("campaign_name"),
                status=item.get("delivery_status") or item.get("status"),
                spend_sar=item.get("spend_sar"), revenue_sar=item.get("sales_sar"),
                purchases=item.get("orders"), impressions=item.get("impressions"), clicks=item.get("swipes"),
                observed_days=item.get("observed_days"), data_complete=item.get("data_complete"),
                account_id=account_id, parent_id=item.get("ad_squad_id"),
            )
            if row:
                rows.append(row)
    return rows


async def _meta_child_entities(
    db: Any,
    user_id: str,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    cursor = db[META_ENTITY_COLLECTION].find(
        {"user_id": user_id, "date": {"$gte": start.isoformat(), "$lte": end.isoformat()}},
        {"_id": 0},
    ).limit(10000)
    documents = await cursor.to_list(length=10000)
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for document in documents:
        groups[(document["entity_level"], document["entity_id"], document.get("ad_account_id") or "")].append(document)
    rows: list[dict[str, Any]] = []
    requested_days = (end - start).days + 1
    for (level, entity_id, account_id), facts in groups.items():
        spend = sum(float(item.get("spend_sar") or 0) for item in facts)
        revenue = sum(float(item.get("revenue_sar") or 0) for item in facts)
        purchases = sum(float(item.get("purchases") or 0) for item in facts)
        impressions = sum(int(item.get("impressions") or 0) for item in facts)
        clicks = sum(int(item.get("clicks") or 0) for item in facts)
        first = facts[0]
        row = _entity(
            provider="meta", level=level, entity_id=entity_id,
            entity_name=first.get("entity_name"),
            parent_name=first.get("ad_group_name") if level == "ad" else first.get("campaign_name"),
            status="ACTIVE", spend_sar=spend, revenue_sar=revenue,
            purchases=purchases, impressions=impressions, clicks=clicks,
            observed_days=len({item.get("date") for item in facts}),
            data_complete=len({item.get("date") for item in facts}) >= requested_days,
            account_id=account_id,
            parent_id=first.get("ad_group_id") if level == "ad" else first.get("campaign_id"),
        )
        if row:
            rows.append(row)
    return rows


def _median(values: list[float]) -> float | None:
    clean = sorted(value for value in values if math.isfinite(value) and value >= 0)
    if not clean:
        return None
    middle = len(clean) // 2
    return clean[middle] if len(clean) % 2 else (clean[middle - 1] + clean[middle]) / 2


def deterministic_candidates(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank evidence for the model using account-relative, not command-driven, signals."""
    prepared: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for source in entities:
        row = dict(source)
        spend = _number(row.get("spend_sar")) or 0.0
        if spend <= 0 or row.get("active") is False:
            continue
        days = max(1, int(_number(row.get("observed_days")) or 1))
        row["spend_per_day_sar"] = round(spend / days, 2)
        row["expected_results_at_target"] = round(spend / TARGET_CPA_SAR, 1) if TARGET_CPA_SAR > 0 else None
        prepared.append(row)
        groups[(str(row.get("provider")), str(row.get("entity_level")))].append(row)

    benchmarks: dict[tuple[str, str], dict[str, float | None]] = {}
    for key, group in groups.items():
        benchmarks[key] = {
            "median_cpa_sar": _median([value for row in group if (value := _number(row.get("cpa_sar"))) is not None]),
            "median_roas": _median([value for row in group if (value := _number(row.get("roas"))) is not None]),
            "median_spend_per_day_sar": _median([float(row["spend_per_day_sar"]) for row in group]),
            "peer_count": float(len(group)),
        }

    candidates: list[dict[str, Any]] = []
    for row in prepared:
        spend = float(row.get("spend_sar") or 0)
        purchases = int(_number(row.get("purchases")) or 0)
        roas = _number(row.get("roas"))
        cpa = _number(row.get("cpa_sar"))
        pace = float(row.get("spend_per_day_sar") or 0)
        baseline = benchmarks[(str(row.get("provider")), str(row.get("entity_level")))]
        peer_count = int(baseline["peer_count"] or 0)
        peer_pace = _number(baseline["median_spend_per_day_sar"])
        rapid_pace = FAST_SPEND_DAILY_SAR
        if peer_count >= 4 and peer_pace is not None:
            rapid_pace = max(FAST_SPEND_DAILY_SAR, peer_pace * 1.5)
        peer_cpa = _number(baseline["median_cpa_sar"])
        high_cpa = max(
            TARGET_CPA_SAR * 1.35,
            (peer_cpa or 0) * 1.35 if peer_count >= 4 else 0,
        )
        peer_roas = _number(baseline["median_roas"])
        scale_roas = max(
            TARGET_ROAS,
            (peer_roas or 0) * 1.15 if peer_count >= 4 else 0,
        )
        scale_cpa = min(
            TARGET_CPA_SAR,
            peer_cpa if peer_count >= 4 and peer_cpa is not None else TARGET_CPA_SAR,
        )

        signal = None
        score = 0.0
        if purchases == 0 and spend >= max(MIN_WASTE_SPEND_SAR, TARGET_CPA_SAR * 1.5) and pace >= rapid_pace:
            signal = "rapid_spend_without_results"
            score = 1400 + pace * 10 + spend
        elif purchases == 0 and spend >= max(MIN_WASTE_SPEND_SAR, TARGET_CPA_SAR * 1.5):
            signal = "waste_without_purchase"
            score = 1000 + spend
        elif purchases > 0 and cpa is not None and cpa >= high_cpa:
            signal = "underperforming_vs_account"
            score = 800 + cpa
        elif (
            purchases >= 3 and roas is not None and cpa is not None
            and roas >= scale_roas
            and cpa <= scale_cpa
        ):
            signal = "scale_candidate"
            score = 600 + purchases * 10 + roas
        elif spend >= MIN_WASTE_SPEND_SAR:
            signal = "expert_review"
            score = 300 + spend
        if signal:
            row["account_benchmark"] = baseline
            row["screening_signal"] = signal
            row["screening_score"] = round(score, 2)
            candidates.append(row)
    candidates.sort(key=lambda item: float(item.get("screening_score") or 0), reverse=True)
    return candidates[:MAX_AI_CANDIDATES]


def _fingerprint(candidates: list[dict[str, Any]]) -> str:
    stable = [
        {
            "provider": row.get("provider"), "level": row.get("entity_level"),
            "id": row.get("entity_id"), "spend": round(float(row.get("spend_sar") or 0), 0),
            "pace": round(float(row.get("spend_per_day_sar") or 0), 0),
            "revenue": round(float(row.get("revenue_sar") or 0), 0),
            "roas": round(float(row.get("roas") or 0), 2),
            "cpa": round(float(row.get("cpa_sar") or 0), 1),
            "purchases": row.get("purchases"), "signal": row.get("screening_signal"),
            "complete": row.get("data_complete"),
        }
        for row in candidates
    ]
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


def _govern_output(
    output: RecommendationOutput,
    candidates: list[dict[str, Any]],
    *,
    next_check_at: str,
) -> RecommendationOutput:
    """Keep model judgment inside the supplied evidence and safety guardrails."""
    evidence = {
        (row.get("provider"), row.get("entity_level"), str(row.get("entity_id"))): row
        for row in candidates
    }
    governed: list[RecommendationItem] = []
    for item in output.recommendations[:MAX_RECOMMENDATIONS]:
        row = evidence.get((item.provider, item.entity_level, item.entity_id))
        if not row:
            continue
        action = item.action
        signal = row.get("screening_signal")
        if action == "scale" and signal != "scale_candidate":
            action = "monitor"
        if action in {"pause", "reduce"} and signal not in {
            "rapid_spend_without_results", "waste_without_purchase", "underperforming_vs_account",
        }:
            action = "monitor"
        if action == "scale" and (not row.get("data_complete") or int(row.get("purchases") or 0) < 3):
            action = "monitor"
        governed.append(item.model_copy(update={
            "recommendation_id": f"{item.provider}:{item.entity_level}:{item.entity_id}",
            "entity_name": str(row.get("entity_name") or item.entity_id),
            "parent_name": row.get("parent_name"),
            "action": action,
            "change_percent": (
                min(30, max(5, int(item.change_percent or 15)))
                if action in {"reduce", "scale"} else None
            ),
            "next_check_at": next_check_at,
        }))
    return RecommendationOutput(
        summary=output.summary,
        recommendations=governed,
        limitations=output.limitations,
    )


def _deterministic_recommendations(
    candidates: list[dict[str, Any]],
    *,
    next_check_at: str,
    limitation: str,
) -> RecommendationOutput:
    """Produce a safe snapshot when the model response cannot be validated."""
    recommendations: list[RecommendationItem] = []
    action_by_signal = {
        "rapid_spend_without_results": ("reduce", 20, "critical", "صرف سريع بلا نتائج"),
        "waste_without_purchase": ("reduce", 15, "high", "صرف بلا مشتريات"),
        "underperforming_vs_account": ("reduce", 15, "high", "أداء أضعف من معيار الحساب"),
        "scale_candidate": ("scale", 15, "medium", "فرصة توسعة منضبطة"),
        "expert_review": ("monitor", None, "low", "يحتاج مراقبة إضافية"),
    }
    for row in candidates[:MAX_RECOMMENDATIONS]:
        signal = str(row.get("screening_signal") or "expert_review")
        action, change_percent, priority, title = action_by_signal.get(
            signal, action_by_signal["expert_review"]
        )
        complete = bool(row.get("data_complete"))
        if action == "scale" and (
            not complete or int(_number(row.get("purchases")) or 0) < 3
        ):
            action, change_percent, priority, title = (
                "monitor", None, "medium", "راقب قبل التوسعة"
            )
        spend = _safe_metric(row.get("spend_sar")) or 0
        purchases = int(_number(row.get("purchases")) or 0)
        roas = _safe_metric(row.get("roas"))
        cpa = _safe_metric(row.get("cpa_sar"))
        pace = _safe_metric(row.get("spend_per_day_sar"))
        evidence = [f"الصرف {spend:.2f} ر.س", f"المشتريات {purchases}"]
        if pace is not None:
            evidence.append(f"وتيرة الصرف اليومية {pace:.2f} ر.س")
        if cpa is not None:
            evidence.append(f"تكلفة الشراء {cpa:.2f} ر.س")
        if roas is not None:
            evidence.append(f"العائد {roas:.2f}×")
        entity_id = str(row.get("entity_id") or "")
        recommendations.append(RecommendationItem(
            recommendation_id=(
                f"{row.get('provider')}:{row.get('entity_level')}:{entity_id}"
            ),
            provider=str(row.get("provider")),
            entity_level=str(row.get("entity_level")),
            entity_id=entity_id,
            entity_name=str(row.get("entity_name") or entity_id),
            parent_name=row.get("parent_name"),
            action=action,
            change_percent=change_percent,
            priority=priority,
            confidence="medium" if complete else "low",
            title=title,
            rationale=(
                "التوصية مبنية على الصرف والنتائج الفعلية ومقارنتها بمعيار الحساب، "
                "وتبقى أيّ كتابة معلّقة حتى موافقة المالك."
            ),
            evidence=evidence[:6],
            guardrail="نفّذ التغيير تدريجيًا ثم أعد القياس بعد ساعة؛ لا يُنفّذ شيء تلقائيًا.",
            next_check_at=next_check_at,
        ))
    return RecommendationOutput(
        summary=(
            f"رُصدت {len(recommendations)} إشارة أداء تستحق المتابعة؛ "
            "رتّبها النظام حسب الهدر وسرعة الصرف وجودة النتائج."
        ),
        recommendations=recommendations,
        limitations=[limitation],
    )


async def _ask_openai(candidates: list[dict[str, Any]], *, now: datetime) -> RecommendationOutput:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("openai_api_key_missing")
    next_check = _iso(now + timedelta(hours=1))
    safe_rows = [
        {key: row.get(key) for key in (
            "provider", "entity_level", "entity_id", "entity_name", "parent_name",
            "status", "active", "spend_sar", "revenue_sar", "purchases", "impressions",
            "clicks", "roas", "cpa_sar", "observed_days", "spend_per_day_sar",
            "expected_results_at_target", "data_complete", "screening_signal", "account_benchmark",
        )}
        for row in candidates
    ]
    client = AsyncOpenAI(api_key=api_key, max_retries=1, timeout=45.0)
    try:
        response = await client.responses.create(
            model=os.environ.get("MEZAN_CAMPAIGN_AI_MODEL", os.environ.get("MEZAN_OPENAI_MODEL", "gpt-5-mini")),
            instructions=(
                "أنت مدير أداء إعلانات مستقل لمتجر أماسي داخل ميزان. كوّن حكمك المهني من "
                "الأرقام ومقارنة كل كيان بمعيار حسابه account_benchmark، ولا تتبع نتيجة "
                "مطلوبة مسبقًا. حلّل الحملة والمجموعة والإعلان. أهداف العمل المرجعية: CPA %.2f "
                "ر.س وROAS %.2f، لكنها سياق وليست قاعدة عمياء. افحص خصوصًا سرعة الصرف بلا نتائج، "
                "مرحلة التعلم، حجم العينة، جودة التحويل والفرق عن أداء الحساب. اكتشف الهدر وفرص "
                "التوسع، لكن لا تدّعِ تنفيذ أي تعديل. لا توصِ بالتوسع إذا "
                "data_complete=false أو المشتريات أقل من 3. الإيقاف أو الخفض توصية مراجعة بشرية "
                "فقط. أعطِ الأولوية للإعلان أو المجموعة المهدرة قبل الحملة الأم حتى لا نوقف "
                "عناصر رابحة معها. recommendation_id يجب أن يكون ثابتًا بصيغة provider:level:id. "
                "اكتب بالعربية وبأرقام إنجليزية، واجعل next_check_at مساويًا للقيمة المرسلة."
            ) % (TARGET_CPA_SAR, TARGET_ROAS),
            input=json.dumps({"next_check_at": next_check, "candidates": safe_rows}, ensure_ascii=False),
            max_output_tokens=2600,
            text={"format": {"type": "json_schema", "name": "campaign_monitor_recommendations", "strict": True, "schema": AI_SCHEMA}},
        )
        output = RecommendationOutput.model_validate_json(response.output_text)
        return _govern_output(output, candidates, next_check_at=next_check)
    finally:
        await client.close()


async def run_campaign_ai_monitor(
    db: Any,
    user_id: str,
    *,
    now: Callable[[], datetime] = _utcnow,
    refresh_meta: bool = True,
) -> dict[str, Any]:
    current = now().astimezone(timezone.utc)
    end = current.astimezone(RIYADH_OFFSET).date()
    start = end - timedelta(days=2)
    run_id = str(uuid.uuid4())
    started_at = _iso(current)
    await db[RUN_COLLECTION].insert_one({
        "run_id": run_id, "user_id": user_id, "status": "running",
        "started_at": started_at, "finished_at": None,
        "expires_at": current + timedelta(days=14),
    })
    errors: list[dict[str, str]] = []
    meta_refresh = None
    try:
        if refresh_meta:
            try:
                meta_refresh = await _refresh_meta_entities(db, user_id, start=start, end=end, now=now)
            except Exception as exc:  # preserve campaign-level monitoring if Meta child refresh fails
                errors.append({"source": "meta_entity_refresh", "code": _text(getattr(exc, "code", type(exc).__name__), limit=100)})
        entities: list[dict[str, Any]] = []
        for provider in ("snapchat", "meta"):
            try:
                entities.extend(await _campaign_entities(db, user_id, provider, start, end))
            except Exception as exc:
                errors.append({"source": f"{provider}_campaigns", "code": _text(type(exc).__name__, limit=100)})
        try:
            entities.extend(await _snapchat_child_entities(db, user_id, start, end))
        except Exception as exc:
            errors.append({"source": "snapchat_children", "code": _text(getattr(exc, "code", type(exc).__name__), limit=100)})
        try:
            entities.extend(await _meta_child_entities(db, user_id, start, end))
        except Exception as exc:
            errors.append({"source": "meta_children", "code": _text(type(exc).__name__, limit=100)})
        entities = entities[:MAX_ENTITY_ROWS]
        candidates = deterministic_candidates(entities)
        fingerprint = _fingerprint(candidates)
        previous = await db[RECOMMENDATION_COLLECTION].find_one(
            {"user_id": user_id}, {"_id": 0}, sort=[("generated_at", -1)]
        )
        if not candidates:
            result = RecommendationOutput(
                summary="لا توجد إشارات هدر أو توسع موثوقة في آخر 3 أيام.",
                recommendations=[],
                limitations=[item["source"] for item in errors],
            )
        elif previous and previous.get("fingerprint") == fingerprint:
            result = RecommendationOutput.model_validate({
                "summary": previous.get("summary") or "لم تتغير الإشارات منذ آخر تحليل.",
                "recommendations": previous.get("recommendations") or [],
                "limitations": previous.get("limitations") or [],
            })
        else:
            try:
                result = await _ask_openai(candidates, now=current)
            except Exception as exc:
                error_code = _text(getattr(exc, "code", type(exc).__name__), limit=100)
                logger.warning(
                    "Campaign AI model output unavailable for user %s (%s); using governed fallback",
                    user_id,
                    error_code,
                )
                errors.append({"source": "openai_recommendation", "code": error_code})
                result = _deterministic_recommendations(
                    candidates,
                    next_check_at=_iso(current + timedelta(hours=1)),
                    limitation=f"openai_recommendation:{error_code}",
                )
        candidate_by_key = {
            (row.get("provider"), row.get("entity_level"), str(row.get("entity_id"))): row
            for row in candidates
        }
        recommendation_rows = []
        execution_targets: dict[str, dict[str, Any]] = {}
        for item in result.recommendations:
            public_item = item.model_dump()
            target = candidate_by_key.get((item.provider, item.entity_level, item.entity_id)) or {}
            executable = bool(
                item.action in {"pause", "reduce", "scale"}
                and target.get("account_id")
                and (item.entity_level != "ad" or item.action == "pause")
            )
            public_item["approval_available"] = executable
            public_item["execution_status"] = "awaiting_approval" if executable else "recommendation_only"
            recommendation_rows.append(public_item)
            if executable:
                execution_targets[item.recommendation_id] = {
                    key: target.get(key) for key in (
                        "provider", "entity_level", "entity_id", "account_id", "parent_id",
                        "current_daily_budget_native", "spend_sar", "purchases", "data_complete",
                        "screening_signal",
                    )
                }
        document = {
            "snapshot_id": str(uuid.uuid4()), "run_id": run_id, "user_id": user_id,
            "generated_at": _iso(current), "next_run_at": _iso(current + timedelta(hours=1)),
            "range": {"from": start.isoformat(), "to": end.isoformat()},
            "summary": result.summary,
            "recommendations": recommendation_rows,
            "execution_targets": execution_targets,
            "limitations": list(dict.fromkeys([*result.limitations, *[item["source"] for item in errors]])),
            "fingerprint": fingerprint,
            "entities_scanned": len(entities), "candidates_scanned": len(candidates),
            "providers": ["snapchat", "meta"], "mode": "recommend_then_approve",
            "writes_performed": False, "meta_refresh": meta_refresh,
        }
        await db[RECOMMENDATION_COLLECTION].insert_one(document)
        await db[RUN_COLLECTION].update_one(
            {"run_id": run_id, "user_id": user_id},
            {"$set": {"status": "complete", "finished_at": _iso(), "snapshot_id": document["snapshot_id"], "recommendations": len(document["recommendations"])}},
        )
        return {key: value for key, value in document.items() if key != "user_id"}
    except Exception as exc:
        logger.exception("Hourly campaign AI monitor failed for user %s", user_id)
        await db[RUN_COLLECTION].update_one(
            {"run_id": run_id, "user_id": user_id},
            {"$set": {"status": "failed", "finished_at": _iso(), "error_code": _text(getattr(exc, "code", type(exc).__name__), limit=100)}},
        )
        raise


async def _monitored_user_ids(db: Any) -> list[str]:
    values = await db.mezan_integration_accounts_v2.distinct(
        "user_id",
        {
            "provider": {"$in": ["snapchat_ads", "meta_ads"]},
            # The V2 control plane persists owner selection as
            # ``mezan_selected`` (not ``selected``/``is_selected``).  A
            # connected account is sufficient to schedule the owner: the
            # per-provider readers still enforce their own selected-account
            # boundary and degrade to campaign-level facts when needed.
            "connection_status": {"$in": ["connected", "needs_reauth"]},
        },
    )
    if not values:
        values = await db.mezan_integrations_v2.distinct(
            "user_id",
            {"provider": {"$in": ["snapchat_ads", "meta_ads"]}, "connection_status": "connected"},
        )
    return [_text(value, limit=120) for value in values if _text(value, limit=120)][:100]


async def _acquire_scheduler_lease(db: Any) -> str | None:
    owner = str(uuid.uuid4())
    current = _utcnow()
    collection = db[LOCK_COLLECTION]
    stale_before = current - timedelta(seconds=SCHEDULER_LEASE_SECONDS)
    result = await collection.update_one(
        {
            "lock_id": LOCK_ID,
            "$or": [
                {"expires_at": {"$lte": current}},
                {"acquired_at": {"$lte": stale_before}},
            ],
        },
        {"$set": {
            "owner": owner,
            "acquired_at": current,
            "expires_at": current + timedelta(seconds=SCHEDULER_LEASE_SECONDS),
        }},
    )
    if getattr(result, "modified_count", 0):
        return owner
    try:
        await collection.insert_one({
            "lock_id": LOCK_ID, "owner": owner, "acquired_at": current,
            "expires_at": current + timedelta(seconds=SCHEDULER_LEASE_SECONDS),
        })
        return owner
    except DuplicateKeyError:
        return None


async def run_all_campaign_ai_monitors(db: Any) -> dict[str, Any]:
    owner = await _acquire_scheduler_lease(db)
    if not owner:
        return {"users": 0, "completed": 0, "failed": 0, "skipped": "lease_held", "ran_at": _iso()}
    try:
        users = await _monitored_user_ids(db)
        completed = 0
        failed = 0
        for user_id in users:
            try:
                await asyncio.wait_for(
                    run_campaign_ai_monitor(db, user_id),
                    timeout=MONITOR_TIMEOUT_SECONDS,
                )
                completed += 1
            except asyncio.TimeoutError:
                failed += 1
                await db[RUN_COLLECTION].update_many(
                    {"user_id": user_id, "status": "running"},
                    {"$set": {
                        "status": "failed",
                        "finished_at": _iso(),
                        "error_code": "monitor_timeout",
                    }},
                )
            except Exception:
                failed += 1
        return {"users": len(users), "completed": completed, "failed": failed, "ran_at": _iso()}
    finally:
        await db[LOCK_COLLECTION].delete_one({"lock_id": LOCK_ID, "owner": owner})


def start_campaign_ai_worker(
    db: Any,
    *,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    initial_delay_seconds: float = DEFAULT_INITIAL_DELAY_SECONDS,
) -> asyncio.Task:
    async def loop() -> None:
        await asyncio.sleep(max(0.0, initial_delay_seconds))
        while True:
            try:
                summary = await run_all_campaign_ai_monitors(db)
                logger.info("Campaign AI hourly monitor complete: %s", summary)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Campaign AI hourly scheduler failed")
            await asyncio.sleep(max(60.0, interval_seconds))
    return asyncio.create_task(loop())


async def _execute_snapchat_approval(
    db: Any,
    user_id: str,
    recommendation: dict[str, Any],
    target: dict[str, Any],
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    from integrations_control_center.snapchat_campaign_management import (
        SnapchatManagementApprovalInput,
        SnapchatManagementProposalInput,
        approve_snapchat_management_proposal,
        create_snapchat_management_proposal,
        execute_snapchat_management_proposal,
    )

    level = recommendation["entity_level"]
    action = {"campaign": "campaign.update", "ad_group": "ad_squad.update", "ad": "ad.update"}[level]
    requested = recommendation["action"]
    payload: dict[str, Any]
    if requested == "pause":
        payload = {"status": "PAUSED"}
    else:
        current_budget = _number(target.get("current_daily_budget_native"))
        if current_budget is None or current_budget <= 0 or level == "ad":
            raise HTTPException(status_code=409, detail={"code": "recommendation_budget_not_available"})
        direction = -1 if requested == "reduce" else 1
        percent = min(30, max(5, int(recommendation.get("change_percent") or 15)))
        ratio = 1 + direction * percent / 100
        payload = {"daily_budget_micro": max(5_000_000, int(round(current_budget * ratio * 1_000_000)))}
    proposal = await create_snapchat_management_proposal(
        db,
        user_id,
        user_id,
        SnapchatManagementProposalInput(
            action=action,
            account_id=str(target["account_id"]),
            target_id=str(target["entity_id"]),
            parent_id=target.get("parent_id"),
            payload=payload,
            reason=_text(recommendation.get("rationale"), limit=500),
            idempotency_key=idempotency_key,
            expected_outcome={"source": "hourly_ai_recommendation", "action": requested},
            safety_protocol_version=2,
        ),
    )
    token = proposal.get("confirm_token")
    if not token:
        if proposal.get("status") == "completed":
            return proposal
        raise HTTPException(status_code=409, detail={"code": "recommendation_proposal_not_approvable"})
    approved = await approve_snapchat_management_proposal(
        db,
        user_id,
        user_id,
        str(proposal["proposal_id"]),
        SnapchatManagementApprovalInput(
            confirm_token=str(token), expected_revision=int(proposal.get("revision") or 1)
        ),
    )
    return await execute_snapchat_management_proposal(
        db, user_id, user_id, str(approved["proposal_id"])
    )


async def _execute_meta_approval(
    db: Any,
    user_id: str,
    recommendation: dict[str, Any],
    target: dict[str, Any],
) -> dict[str, Any]:
    access_token = await _meta_credential(db, user_id, _utcnow())
    entity_id = _text(target.get("entity_id"), limit=120)
    proof = meta_appsecret_proof(access_token)
    base = meta_graph_base()
    async with httpx.AsyncClient(timeout=35.0) as client:
        read = await client.get(
            f"{base}/{entity_id}",
            params={
                "access_token": access_token,
                "appsecret_proof": proof,
                "fields": "id,name,status,effective_status,daily_budget",
            },
        )
        if read.status_code >= 400:
            raise HTTPException(status_code=502, detail={"code": "meta_recommendation_preflight_failed"})
        before = read.json()
        requested = recommendation["action"]
        current_status = str(before.get("status") or before.get("effective_status") or "").upper()
        if current_status not in {"ACTIVE", "ENABLED"}:
            raise HTTPException(status_code=409, detail={"code": "recommendation_target_no_longer_active"})
        if requested == "pause":
            mutation = {"status": "PAUSED"}
        else:
            if recommendation.get("entity_level") == "ad":
                raise HTTPException(status_code=409, detail={"code": "meta_ad_budget_change_unsupported"})
            current_budget = _number(before.get("daily_budget"))
            if current_budget is None or current_budget <= 0:
                raise HTTPException(status_code=409, detail={"code": "recommendation_budget_not_available"})
            direction = -1 if requested == "reduce" else 1
            percent = min(30, max(5, int(recommendation.get("change_percent") or 15)))
            ratio = 1 + direction * percent / 100
            mutation = {"daily_budget": str(max(1, int(round(current_budget * ratio))))}
        write = await client.post(
            f"{base}/{entity_id}",
            data={"access_token": access_token, "appsecret_proof": proof, **mutation},
        )
        if write.status_code >= 400:
            raise HTTPException(status_code=502, detail={"code": "meta_recommendation_write_failed"})
        verify = await client.get(
            f"{base}/{entity_id}",
            params={
                "access_token": access_token,
                "appsecret_proof": proof,
                "fields": "id,name,status,effective_status,daily_budget",
            },
        )
        after = verify.json() if verify.status_code < 400 else {}
        if "status" in mutation:
            verified = str(after.get("status") or after.get("effective_status") or "").upper() == "PAUSED"
        else:
            verified = str(after.get("daily_budget") or "") == str(mutation["daily_budget"])
        return {
            "provider": "meta",
            "entity_id": entity_id,
            "status": "completed" if verified else "verification_required",
            "before": {key: before.get(key) for key in ("status", "effective_status", "daily_budget")},
            "requested_change": mutation,
            "verification": after if verify.status_code < 400 else None,
            "provider_write_reached": True,
        }


def attach_campaign_ai_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get("/ai-monitor/latest")
    async def latest_campaign_recommendations(user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = _text(user.get("id") or user.get("_id"), limit=120)
        document = await db[RECOMMENDATION_COLLECTION].find_one(
            {"user_id": user_id}, {"_id": 0, "user_id": 0, "execution_targets": 0}, sort=[("generated_at", -1)]
        )
        if not document:
            return {
                "available": False, "mode": "recommend_then_approve", "writes_performed": False,
                "summary": "سيظهر أول تحليل بعد اكتمال التشغيل الدوري.",
                "recommendations": [], "next_run_at": None,
            }
        return {"available": True, **document}

    @router.get("/ai-monitor/history")
    async def campaign_recommendation_history(
        limit: int = Query(default=12, ge=1, le=48),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = _text(user.get("id") or user.get("_id"), limit=120)
        cursor = db[RECOMMENDATION_COLLECTION].find(
            {"user_id": user_id}, {"_id": 0, "user_id": 0, "execution_targets": 0}
        ).sort("generated_at", -1).limit(limit)
        return {"items": await cursor.to_list(length=limit), "mode": "recommend_then_approve"}

    @router.post("/ai-monitor/recommendations/{recommendation_id}/approve", status_code=202)
    async def approve_campaign_recommendation(
        recommendation_id: str,
        payload: RecommendationApprovalInput,
        background_tasks: BackgroundTasks,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = _text(owner.get("id") or owner.get("_id"), limit=120)
        latest = await db[RECOMMENDATION_COLLECTION].find_one(
            {"user_id": user_id}, {"_id": 0}, sort=[("generated_at", -1)]
        )
        if not latest or latest.get("snapshot_id") != payload.snapshot_id:
            raise HTTPException(status_code=409, detail={"code": "recommendation_snapshot_stale"})
        try:
            generated_at = datetime.fromisoformat(str(latest.get("generated_at")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=409, detail={"code": "recommendation_timestamp_invalid"})
        if _utcnow() - generated_at.astimezone(timezone.utc) > timedelta(hours=2):
            raise HTTPException(status_code=409, detail={"code": "recommendation_expired"})
        recommendation = next(
            (
                item for item in latest.get("recommendations") or []
                if item.get("recommendation_id") == recommendation_id
            ),
            None,
        )
        target = (latest.get("execution_targets") or {}).get(recommendation_id)
        if not recommendation or not target or not recommendation.get("approval_available"):
            raise HTTPException(status_code=409, detail={"code": "recommendation_not_executable"})
        execution_id = hashlib.sha256(
            f"{user_id}:{payload.snapshot_id}:{recommendation_id}".encode()
        ).hexdigest()
        started = {
            "execution_id": execution_id,
            "user_id": user_id,
            "snapshot_id": payload.snapshot_id,
            "recommendation_id": recommendation_id,
            "provider": recommendation.get("provider"),
            "action": recommendation.get("action"),
            "status": "executing",
            "approved_by": user_id,
            "approved_at": _iso(),
            "writes_performed": False,
        }
        try:
            await db[EXECUTION_COLLECTION].insert_one(started)
        except DuplicateKeyError:
            existing = await db[EXECUTION_COLLECTION].find_one(
                {"execution_id": execution_id}, {"_id": 0, "user_id": 0}
            )
            return existing or {"execution_id": execution_id, "status": "executing"}
        async def execute_in_background() -> None:
            try:
                if recommendation.get("provider") == "snapchat":
                    result = await _execute_snapchat_approval(
                        db, user_id, recommendation, target, idempotency_key=execution_id
                    )
                elif recommendation.get("provider") == "meta":
                    result = await _execute_meta_approval(db, user_id, recommendation, target)
                else:
                    raise HTTPException(status_code=409, detail={"code": "recommendation_provider_unsupported"})
                final_status = "completed" if result.get("status") == "completed" else "verification_required"
                await db[EXECUTION_COLLECTION].update_one(
                    {"execution_id": execution_id},
                    {"$set": {
                        "status": final_status,
                        "finished_at": _iso(),
                        "writes_performed": True,
                        "result": result,
                    }},
                )
                await db[RECOMMENDATION_COLLECTION].update_one(
                    {"user_id": user_id, "snapshot_id": payload.snapshot_id},
                    {"$set": {
                        "recommendations.$[item].execution_status": final_status,
                        "recommendations.$[item].executed_at": _iso(),
                    }},
                    array_filters=[{"item.recommendation_id": recommendation_id}],
                )
            except Exception as exc:
                detail = exc.detail if isinstance(exc, HTTPException) else {"code": type(exc).__name__}
                await db[EXECUTION_COLLECTION].update_one(
                    {"execution_id": execution_id},
                    {"$set": {"status": "failed", "finished_at": _iso(), "failure": detail}},
                )
                await db[RECOMMENDATION_COLLECTION].update_one(
                    {"user_id": user_id, "snapshot_id": payload.snapshot_id},
                    {"$set": {"recommendations.$[item].execution_status": "failed"}},
                    array_filters=[{"item.recommendation_id": recommendation_id}],
                )
                logger.exception("Approved campaign recommendation execution failed")

        background_tasks.add_task(execute_in_background)
        return {
            "execution_id": execution_id,
            "status": "executing",
            "provider": recommendation.get("provider"),
            "action": recommendation.get("action"),
            "writes_performed": False,
        }


__all__ = [
    "attach_campaign_ai_routes", "deterministic_candidates",
    "ensure_campaign_ai_indexes", "run_campaign_ai_monitor",
    "start_campaign_ai_worker",
]
