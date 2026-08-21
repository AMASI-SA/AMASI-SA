"""AI-led monitoring and explicitly approved execution for Snapchat and Meta.

The worker refreshes bounded provider facts and gives OpenAI the complete active
entity set, account-relative benchmarks, longer campaign history, Saudi calendar
context, and the outcomes of prior Mezan recommendations.  OpenAI owns the
marketing judgment; Mezan code only validates evidence, protects execution, and
persists an auditable snapshot. The worker never changes ads; provider writes
exist only behind the separate, owner-only approval endpoint.
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
try:
    from openai import AsyncOpenAI
except ImportError:  # Optional in focused test/runtime images that never call OpenAI.
    AsyncOpenAI = None
from pydantic import BaseModel, Field, ValidationError
from pymongo.errors import DuplicateKeyError

import campaign_ai_execution_quality_gate as _execution_quality

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
DEFAULT_INTERVAL_SECONDS = 5 * 60 * 60
# Run the first pass shortly after boot so a fresh deployment does not leave
# the dashboard stuck on "waiting for first run" for several minutes.  The
# worker is detached from FastAPI startup, so this does not delay readiness.
DEFAULT_INITIAL_DELAY_SECONDS = 5
SCHEDULER_LEASE_SECONDS = 10 * 60
MONITOR_TIMEOUT_SECONDS = 8 * 60
MAX_ENTITY_ROWS = 300
MAX_AI_CANDIDATES = 120
MAX_RECOMMENDATIONS = 18
OPENAI_MAX_OUTPUT_TOKENS = min(
    24000,
    max(8000, int(os.environ.get("MEZAN_CAMPAIGN_AI_MAX_OUTPUT_TOKENS", "12000"))),
)
# The request includes multi-window campaign history and a strict Arabic JSON
# schema. Production responses can legitimately need longer than 45 seconds,
# so allow one bounded request to finish within the outer monitor deadline.
OPENAI_TIMEOUT_SECONDS = min(
    300.0,
    max(60.0, float(os.environ.get("MEZAN_CAMPAIGN_AI_TIMEOUT_SECONDS", "240"))),
)
TARGET_CPA_SAR = float(os.environ.get("MEZAN_CAMPAIGN_TARGET_CPA_SAR", "56.25"))
TARGET_ROAS = float(os.environ.get("MEZAN_CAMPAIGN_TARGET_ROAS", "2.5"))
MIN_WASTE_SPEND_SAR = float(os.environ.get("MEZAN_CAMPAIGN_MIN_WASTE_SPEND_SAR", "75"))
FAST_SPEND_DAILY_SAR = float(
    os.environ.get("MEZAN_CAMPAIGN_FAST_SPEND_DAILY_SAR", str(TARGET_CPA_SAR * 2))
)
CAMPAIGN_GROSS_MARGIN_RATE = min(
    1.0,
    max(0.0, float(os.environ.get("MEZAN_CAMPAIGN_GROSS_MARGIN_RATE", "0.55"))),
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


def _status_text(value: Any) -> str:
    if isinstance(value, dict):
        priority = (
            "effective_status", "delivery_state", "delivery_status",
            "configured_status", "status", "state", "code", "label",
        )
        ordered = [value.get(key) for key in priority if value.get(key) is not None]
        ordered.extend(item for key, item in value.items() if key not in priority)
        value = ordered
    if isinstance(value, (list, tuple, set)):
        value = " ".join(_status_text(item) for item in value)
    rendered = _text(value, limit=600).casefold()
    for marker in ("_", "-", "/", "—", "–", ":", "|", "،", ",", "."):
        rendered = rendered.replace(marker, " ")
    return " ".join(rendered.split())


def _normalized_status(value: Any) -> str:
    rendered = _status_text(value)
    inactive = (
        "not delivering", "not active", "inactive", "paused", "disabled",
        "stopped", "ended", "deleted", "archived", "rejected",
        "disapproved", "out of budget", "pending review", "draft",
        "لا يتم التسليم", "لا تسليم", "غير نشط", "غير نشطة",
        "غير فعال", "غير فعالة", "متوقف", "متوقفة", "موقوف",
        "موقوفة", "محذوف", "مرفوض", "منتهي", "قيد المراجعة",
        "بانتظار المراجعة", "نفدت الميزانية",
    )
    if any(marker in rendered for marker in inactive):
        return "inactive"
    active = (
        "active", "enabled", "running", "delivering", "live", "serving",
        "يتم التسليم", "قيد التسليم", "جاري التسليم", "جار التسليم",
        "نشط", "نشطة", "مفعل", "مفعلة", "فعال", "فعالة",
        "مرحلة التعلم",
    )
    return "active" if any(marker in rendered for marker in active) else "unknown"


def _active(value: Any) -> bool:
    return _normalized_status(value) == "active"


def _safe_metric(value: Any, digits: int = 2) -> float | None:
    parsed = _number(value)
    return round(parsed, digits) if parsed is not None and parsed >= 0 else None


class RecommendationItem(BaseModel):
    recommendation_id: str
    provider: Literal["snapchat", "meta"]
    entity_level: Literal["campaign", "ad_group", "ad"]
    entity_id: str
    entity_name: str
    account_id: str | None = None
    account_name: str | None = None
    parent_name: str | None = None
    action: Literal["pause", "reduce", "monitor", "maintain", "scale"]
    change_percent: int | None = Field(default=None, ge=5, le=30)
    priority: Literal["critical", "high", "medium", "low"]
    confidence: Literal["high", "medium", "low"]
    title: str
    rationale: str
    evidence: list[str] = Field(default_factory=list)
    why_now: str
    recommended_wait_hours: int = Field(ge=1, le=24)
    observation_plan: str
    success_criteria: list[str] = Field(default_factory=list)
    risk_if_ignored: str
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
                    "account_id": {"type": ["string", "null"]},
                    "account_name": {"type": ["string", "null"]},
                    "parent_name": {"type": ["string", "null"]},
                    "action": {"type": "string", "enum": ["pause", "reduce", "monitor", "maintain", "scale"]},
                    "change_percent": {"type": ["integer", "null"], "minimum": 5, "maximum": 30},
                    "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "title": {"type": "string"},
                    "rationale": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                    "why_now": {"type": "string"},
                    "recommended_wait_hours": {"type": "integer", "minimum": 1, "maximum": 24},
                    "observation_plan": {"type": "string"},
                    "success_criteria": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                    "risk_if_ignored": {"type": "string"},
                    "guardrail": {"type": "string"},
                    "next_check_at": {"type": "string"},
                },
                "required": [
                    "recommendation_id", "provider", "entity_level", "entity_id",
                    "entity_name", "account_id", "account_name", "parent_name",
                    "action", "priority", "confidence",
                    "change_percent", "title", "rationale", "evidence", "why_now",
                    "recommended_wait_hours", "observation_plan", "success_criteria",
                    "risk_if_ignored", "guardrail", "next_check_at",
                ],
            },
        },
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "recommendations", "limitations"],
}


class CampaignOpenAIError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _openai_error_code(exc: Exception) -> str:
    """Keep API failures distinct from model-output validation failures."""
    if isinstance(exc, CampaignOpenAIError):
        return exc.code
    if isinstance(exc, ValidationError):
        return "openai_response_validation_error"
    raw_code = _text(getattr(exc, "code", ""), limit=100).lower()
    message = _text(exc, limit=500).lower()
    status = getattr(exc, "status_code", None)
    combined = f"{raw_code} {message}"
    if "openai_api_key_missing" in combined:
        return "openai_api_key_missing"
    if "insufficient_quota" in combined or "billing quota" in combined:
        return "openai_insufficient_quota"
    if status == 401 or "invalid_api_key" in combined:
        return "openai_invalid_api_key"
    if status == 403 or "model_not_found" in combined:
        return "openai_model_access_denied"
    if status == 429 or "rate_limit_exceeded" in combined:
        return "openai_rate_limited"
    if raw_code:
        return f"openai_{raw_code}"
    return f"openai_{type(exc).__name__.lower()}"


def _fallback_summary(error_code: str) -> str:
    labels = {
        "openai_api_key_missing": "مفتاح OpenAI غير مهيأ؛ يعرض ميزان توصيات احتياطية محدودة.",
        "openai_invalid_api_key": "مفتاح OpenAI غير صالح؛ يعرض ميزان توصيات احتياطية محدودة.",
        "openai_insufficient_quota": "رصيد OpenAI API أو حد الإنفاق غير متاح؛ يعرض ميزان توصيات احتياطية محدودة.",
        "openai_rate_limited": "OpenAI مزدحم مؤقتًا؛ يعرض ميزان توصيات احتياطية حتى المحاولة التالية.",
        "openai_model_access_denied": "المشروع لا يملك صلاحية نموذج OpenAI المحدد؛ يعرض ميزان توصيات احتياطية.",
        "openai_response_validation_error": "وصل رد OpenAI لكن تعذر اعتماد تنسيق النتيجة؛ يعرض ميزان توصيات احتياطية.",
        "openai_response_invalid_json": "وصل رد OpenAI غير مكتمل؛ يعرض ميزان توصيات احتياطية حتى المحاولة التالية.",
        "openai_response_empty": "لم يصل محتوى قابل للتحليل من OpenAI؛ يعرض ميزان توصيات احتياطية.",
    }
    if error_code.startswith("openai_response_incomplete"):
        return "توقف رد OpenAI قبل اكتمال التوصيات؛ يعرض ميزان توصيات احتياطية حتى المحاولة التالية."
    return labels.get(
        error_code,
        "تعذر تشغيل تحليل OpenAI؛ يعرض ميزان توصيات احتياطية محدودة ومعلّمة بمصدرها.",
    )


def _text_list(value: Any, *, limit: int) -> list[str]:
    values = value if isinstance(value, list) else [value]
    output: list[str] = []
    for item in values:
        normalized = _text(item, limit=260)
        if normalized:
            output.append(normalized)
        if len(output) >= limit:
            break
    return output


def _normalize_openai_output(
    raw_text: str,
    candidates: list[dict[str, Any]],
    *,
    next_check_at: str,
) -> RecommendationOutput:
    """Normalize harmless model variance while preserving the model's decision."""
    raw = (raw_text or "").strip()
    if not raw:
        raise CampaignOpenAIError("openai_response_empty")
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CampaignOpenAIError("openai_response_invalid_json") from exc
    if not isinstance(payload, dict):
        raise CampaignOpenAIError("openai_response_validation_error")

    provider_aliases = {
        "snap": "snapchat", "snapchat_ads": "snapchat", "snapchat": "snapchat",
        "facebook": "meta", "instagram": "meta", "meta_ads": "meta", "meta": "meta",
    }
    level_aliases = {
        "campaign": "campaign", "ad_group": "ad_group", "adgroup": "ad_group",
        "ad_set": "ad_group", "adset": "ad_group", "ad_squad": "ad_group",
        "adsquad": "ad_group", "ad": "ad", "advertisement": "ad",
    }
    action_aliases = {
        "pause": "pause", "stop": "pause", "إيقاف": "pause", "ايقاف": "pause",
        "reduce": "reduce", "decrease": "reduce", "خفض": "reduce",
        "monitor": "monitor", "watch": "monitor", "مراقبة": "monitor",
        "maintain": "maintain", "keep": "maintain", "continue": "maintain",
        "استمرار": "maintain", "إبقاء": "maintain", "ابقاء": "maintain",
        "scale": "scale", "increase": "scale", "توسعة": "scale", "زيادة": "scale",
    }
    priority_aliases = {
        "critical": "critical", "حرج": "critical", "high": "high", "عالي": "high",
        "medium": "medium", "متوسط": "medium", "low": "low", "منخفض": "low",
    }
    confidence_aliases = {
        "high": "high", "عالية": "high", "عالي": "high",
        "medium": "medium", "متوسطة": "medium", "متوسط": "medium",
        "low": "low", "منخفضة": "low", "منخفض": "low",
    }

    candidate_index: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        candidate_index[(
            str(candidate.get("provider") or ""),
            str(candidate.get("entity_level") or ""),
            str(candidate.get("entity_id") or ""),
        )].append(candidate)

    raw_items = payload.get("recommendations")
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        raise CampaignOpenAIError("openai_response_validation_error")
    recommendations: list[RecommendationItem] = []
    rejected = 0
    for raw_item in raw_items[:MAX_RECOMMENDATIONS]:
        if not isinstance(raw_item, dict):
            rejected += 1
            continue
        provider = provider_aliases.get(_text(raw_item.get("provider"), limit=40).lower())
        level = level_aliases.get(_text(raw_item.get("entity_level"), limit=40).lower())
        action = action_aliases.get(_text(raw_item.get("action"), limit=40).lower())
        entity_id = _text(raw_item.get("entity_id"), limit=160)
        if not provider or not level or not action or not entity_id:
            rejected += 1
            continue
        matches = candidate_index.get((provider, level, entity_id), [])
        account_id = _text(raw_item.get("account_id"), limit=160) or None
        if account_id:
            matches = [row for row in matches if str(row.get("account_id") or "") == account_id]
        if len(matches) == 1:
            target = matches[0]
            account_id = str(target.get("account_id") or "") or None
        else:
            target = {}
        change = _number(raw_item.get("change_percent"))
        wait = _number(raw_item.get("recommended_wait_hours"))
        rationale = _text(raw_item.get("rationale") or raw_item.get("why_now"), limit=900)
        why_now = _text(raw_item.get("why_now") or rationale, limit=900)
        try:
            recommendations.append(RecommendationItem(
                recommendation_id=_text(raw_item.get("recommendation_id"), limit=300) or "pending",
                provider=provider,
                entity_level=level,
                entity_id=entity_id,
                entity_name=_text(
                    target.get("entity_name") or raw_item.get("entity_name") or entity_id,
                    limit=300,
                ),
                account_id=account_id,
                account_name=_text(
                    target.get("account_name") or raw_item.get("account_name") or account_id,
                    limit=300,
                ) or None,
                parent_name=_text(
                    target.get("parent_name") or raw_item.get("parent_name"),
                    limit=300,
                ) or None,
                action=action,
                change_percent=(
                    min(30, max(5, int(round(change or 15))))
                    if action in {"reduce", "scale"} else None
                ),
                priority=priority_aliases.get(
                    _text(raw_item.get("priority"), limit=40).lower(), "medium"
                ),
                confidence=confidence_aliases.get(
                    _text(raw_item.get("confidence"), limit=40).lower(), "medium"
                ),
                title=_text(raw_item.get("title"), limit=300) or "توصية OpenAI",
                rationale=rationale or "قرار مبني على تحليل OpenAI للبيانات المرسلة.",
                evidence=_text_list(raw_item.get("evidence"), limit=6),
                why_now=why_now or "هذه هي نقطة المراجعة الحالية ضمن دورة التحليل.",
                recommended_wait_hours=min(24, max(1, int(round(wait or 5)))),
                observation_plan=_text(raw_item.get("observation_plan"), limit=900)
                or "أعد القياس في موعد المراجعة التالي.",
                success_criteria=_text_list(raw_item.get("success_criteria"), limit=4)
                or ["تحسن النتيجة الاقتصادية بعد فترة المراقبة"],
                risk_if_ignored=_text(raw_item.get("risk_if_ignored"), limit=900)
                or "قد يستمر الأداء الحالي دون تصحيح.",
                guardrail=_text(raw_item.get("guardrail"), limit=900)
                or "لا يُنفذ أي تغيير إلا بعد موافقة المالك.",
                next_check_at=next_check_at,
            ))
        except ValidationError:
            rejected += 1
    if raw_items and not recommendations:
        raise CampaignOpenAIError("openai_response_validation_error")
    if rejected:
        logger.warning("Campaign AI normalized response with %s rejected item(s)", rejected)
    return RecommendationOutput(
        summary=_text(payload.get("summary"), limit=1200)
        or "اكتمل تحليل OpenAI دون ملخص نصي.",
        recommendations=recommendations,
        limitations=_text_list(payload.get("limitations"), limit=8),
    )


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
    pagination_complete = True
    async with httpx.AsyncClient(timeout=35.0) as client:
        for account in accounts:
            account_id = _text(account.get("ad_account_id"), limit=120)
            state_by_level: dict[str, dict[str, dict[str, Any]]] = {
                "campaign": {}, "ad_group": {}, "ad": {},
            }
            for edge, state_level, state_fields in (
                ("campaigns", "campaign", "id,name,status,effective_status,updated_time"),
                ("adsets", "ad_group", "id,name,status,effective_status,updated_time,campaign_id"),
                ("ads", "ad", "id,name,status,effective_status,updated_time,campaign_id,adset_id"),
            ):
                try:
                    state_rows, calls = await _paged_get(
                        client,
                        f"{meta_graph_base()}/{account_id}/{edge}",
                        {
                            "access_token": access_token,
                            "appsecret_proof": meta_appsecret_proof(access_token),
                            "fields": state_fields,
                            "limit": 500,
                        },
                        operation=f"meta_ai_{edge}_status",
                    )
                    provider_calls += calls
                    state_by_level[state_level] = {
                        _text(item.get("id"), limit=120): item
                        for item in state_rows
                        if _text(item.get("id"), limit=120)
                    }
                except Exception as exc:
                    pagination_complete = False
                    errors.append({
                        "account_id": account_id,
                        "date": current.date().isoformat(),
                        "level": f"{state_level}_status",
                        "code": _text(getattr(exc, "code", type(exc).__name__), limit=100),
                    })
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
                        pagination_complete = False
                        errors.append({
                            "account_id": account_id,
                            "date": cursor.isoformat(),
                            "level": level,
                            "code": _text(getattr(exc, "code", type(exc).__name__), limit=100),
                        })
                        continue
                    if len(rows) > 2000:
                        pagination_complete = False
                        errors.append({
                            "account_id": account_id,
                            "date": cursor.isoformat(),
                            "level": level,
                            "code": "meta_ai_entity_row_limit_reached",
                        })
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
                        entity_level = "ad_group" if level == "adset" else "ad"
                        entity_state = state_by_level[entity_level].get(entity_id) or {}
                        campaign_id = _text(row.get("campaign_id"), limit=120)
                        ad_group_id = _text(row.get("adset_id"), limit=120)
                        campaign_state = state_by_level["campaign"].get(campaign_id) or {}
                        ad_group_state = state_by_level["ad_group"].get(ad_group_id) or {}
                        campaign_ad_group_count = sum(
                            1 for item in state_by_level["ad_group"].values()
                            if _text(item.get("campaign_id"), limit=120) == campaign_id
                        )
                        campaign_ad_count = sum(
                            1 for item in state_by_level["ad"].values()
                            if _text(item.get("campaign_id"), limit=120) == campaign_id
                        )
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
                            "account_timezone": _text(
                                account.get("timezone"), limit=100
                            ) or None,
                            "entity_level": entity_level,
                            "entity_id": entity_id,
                            "entity_name": _text(
                                row.get("adset_name") if level == "adset" else row.get("ad_name")
                            ) or entity_id,
                            "configured_status": _text(entity_state.get("status"), limit=60) or "unknown",
                            "effective_status": _text(entity_state.get("effective_status"), limit=60) or "unknown",
                            "status": _text(entity_state.get("effective_status") or entity_state.get("status"), limit=60) or "unknown",
                            "status_updated_at": _text(entity_state.get("updated_time"), limit=80) or None,
                            "campaign_id": campaign_id,
                            "campaign_name": _text(row.get("campaign_name")),
                            "campaign_status": _text(campaign_state.get("effective_status") or campaign_state.get("status"), limit=60) or "unknown",
                            "ad_group_id": ad_group_id,
                            "ad_group_name": _text(row.get("adset_name")),
                            "ad_group_status": _text(ad_group_state.get("effective_status") or ad_group_state.get("status"), limit=60) or "unknown",
                            "campaign_ad_group_count": campaign_ad_group_count,
                            "campaign_ad_count": campaign_ad_count,
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
                            "provider_result_source": "meta_ads_api_insights",
                            "result_source": "platform",
                            "action_report_time": "conversion",
                            "source_date_from": start.isoformat(),
                            "source_date_to": end.isoformat(),
                            "pagination_complete": pagination_complete,
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
    return {
        "status": "complete" if not errors and pagination_complete else "partial",
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "rows_saved": saved,
        "provider_calls": provider_calls,
        "errors_count": len(errors),
        "errors": errors[:50],
        "pagination_complete": bool(pagination_complete and not errors),
        "observed_at": observed_at,
    }


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
    account_name: Any = None,
    parent_id: Any = None,
    current_daily_budget_native: Any = None,
    configured_status: Any = None,
    effective_status: Any = None,
    status_updated_at: Any = None,
    campaign_id: Any = None,
    campaign_name: Any = None,
    campaign_status: Any = None,
    ad_group_id: Any = None,
    ad_group_name: Any = None,
    ad_group_status: Any = None,
    campaign_ad_group_count: Any = None,
    campaign_ad_count: Any = None,
    currency_native: Any = None,
    fx_rate_to_sar: Any = None,
    fx_source: Any = None,
    provider_result_source: Any = None,
    action_report_time: Any = None,
    result_source: Any = None,
    source_date_from: Any = None,
    source_date_to: Any = None,
    source_observed_at: Any = None,
    account_timezone: Any = None,
    pagination_complete: Any = None,
    source_mode: Any = None,
    source_fact_collection: Any = None,
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
        "normalized_status": _normalized_status(status),
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
        "account_name": _text(account_name, limit=180) or None,
        "parent_id": _text(parent_id, limit=120) or None,
        "current_daily_budget_native": _safe_metric(current_daily_budget_native, 6),
        "configured_status": _text(configured_status, limit=60) or None,
        "effective_status": _text(effective_status, limit=60) or None,
        "status_updated_at": _text(status_updated_at, limit=80) or None,
        "campaign_id": _text(campaign_id, limit=120) or None,
        "campaign_name": _text(campaign_name) or None,
        "campaign_status": _text(campaign_status, limit=60) or None,
        "ad_group_id": _text(ad_group_id, limit=120) or None,
        "ad_group_name": _text(ad_group_name) or None,
        "ad_group_status": _text(ad_group_status, limit=60) or None,
        "campaign_ad_group_count": int(_safe_metric(campaign_ad_group_count, 0) or 0),
        "campaign_ad_count": int(_safe_metric(campaign_ad_count, 0) or 0),
        "currency_native": _text(currency_native, limit=12).upper() or None,
        "fx_rate_to_sar": _safe_metric(fx_rate_to_sar, 6),
        "fx_source": _text(fx_source, limit=120) or None,
        "provider_result_source": _text(provider_result_source, limit=160) or None,
        "action_report_time": _text(action_report_time, limit=80) or None,
        "result_source": _text(result_source, limit=80) or None,
        "source_date_from": _text(source_date_from, limit=20) or None,
        "source_date_to": _text(source_date_to, limit=20) or None,
        "source_observed_at": _text(source_observed_at, limit=80) or None,
        "account_timezone": _text(account_timezone, limit=100) or None,
        "pagination_complete": pagination_complete if isinstance(pagination_complete, bool) else None,
        "source_mode": _text(source_mode, limit=180) or None,
        "source_fact_collection": _text(source_fact_collection, limit=180) or None,
    }


async def _campaign_entities(
    db: Any,
    user_id: str,
    provider: str,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    from ads_manager.service import AdsManagerService

    accounts = (
        await _snapchat_accounts(db, user_id)
        if provider == "snapchat"
        else await _meta_accounts(db, user_id)
    )
    account_by_id = {
        _text(
            account.get("ad_account_id")
            or account.get("external_account_id")
            or account.get("account_id"),
            limit=120,
        ): account
        for account in accounts
    }
    account_names = {
        account_id: _text(
            account.get("display_name") or account.get("name"), limit=180
        )
        for account_id, account in account_by_id.items()
    }

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
    observed_days = coverage.get("observed_days") or 0
    data_complete = coverage.get("status") == "complete"
    campaign_coverage = provider_summary.get("campaign_coverage") or {}
    overview_coverage = overview.get("coverage") or {}
    overview_pagination = overview.get("campaign_pagination") or {}
    pagination_complete = bool(
        data_complete
        and "source_truncated" not in set(coverage.get("reasons") or [])
        and campaign_coverage.get("status") == "available"
        and not overview_coverage.get("source_row_limit_reached")
        and int(overview_pagination.get("page") or 0) == 1
        and int(overview_pagination.get("pages") or 0) == 1
        and int(overview_pagination.get("total") or 0) > 0
    )
    for item in overview.get("campaigns") or []:
        account_id = _text(item.get("account_id") or item.get("ad_account_id"), limit=120)
        account = account_by_id.get(account_id) or {}
        row = _entity(
            provider=provider,
            level="campaign",
            entity_id=item.get("campaign_id"),
            entity_name=item.get("campaign_name"),
            parent_name=None,
            status=(
                item.get("effective_status")
                or item.get("delivery_state")
                or item.get("delivery_status")
                or item.get("status")
            ),
            spend_sar=item.get("spend_sar_equivalent"),
            revenue_sar=item.get("revenue_sar_equivalent"),
            purchases=item.get("purchases"),
            impressions=item.get("impressions"),
            clicks=item.get("clicks"),
            observed_days=observed_days,
            data_complete=data_complete,
            account_id=account_id,
            account_name=item.get("account_name") or account_names.get(account_id),
            current_daily_budget_native=(item.get("budget") or {}).get("daily_native"),
            currency_native=(
                item.get("display_currency")
                or (item.get("budget") or {}).get("currency")
                or account.get("currency")
            ),
            fx_rate_to_sar=item.get("exchange_rate_to_sar"),
            fx_source=item.get("fx_source"),
            provider_result_source=(
                "meta_ads_manager_reporting" if provider == "meta" else None
            ),
            action_report_time="conversion" if provider == "meta" else None,
            result_source="platform" if provider == "meta" else None,
            source_date_from=start.isoformat(),
            source_date_to=end.isoformat(),
            source_observed_at=(
                (provider_summary.get("freshness") or {}).get("last_observed_at")
            ),
            account_timezone=(
                item.get("account_timezone") or account.get("timezone")
            ),
            pagination_complete=pagination_complete,
            source_mode=(
                "meta_campaign_reporting_v2" if provider == "meta" else None
            ),
            source_fact_collection=(
                "mezan_meta_campaign_performance_daily_v2"
                if provider == "meta"
                else None
            ),
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
        account_name = _text(account.get("display_name") or account.get("name"), limit=180)
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
                parent_name=item.get("campaign_name"),
                status=(
                    item.get("effective_status")
                    or item.get("delivery_state")
                    or item.get("delivery_status")
                    or item.get("status")
                ),
                spend_sar=item.get("spend_sar"), revenue_sar=item.get("sales_sar"),
                purchases=item.get("orders"), impressions=item.get("impressions"), clicks=item.get("swipes"),
                observed_days=item.get("observed_days"), data_complete=item.get("data_complete"),
                account_id=account_id, account_name=account_name,
                parent_id=item.get("campaign_id"),
                current_daily_budget_native=(item.get("budget") or {}).get("daily_native"),
            )
            if row:
                rows.append(row)
        for item in reports[1].get("ads") or []:
            row = _entity(
                provider="snapchat", level="ad",
                entity_id=item.get("ad_id"), entity_name=item.get("ad_name"),
                parent_name=item.get("ad_squad_name") or item.get("campaign_name"),
                status=(
                    item.get("effective_status")
                    or item.get("delivery_state")
                    or item.get("delivery_status")
                    or item.get("status")
                ),
                spend_sar=item.get("spend_sar"), revenue_sar=item.get("sales_sar"),
                purchases=item.get("orders"), impressions=item.get("impressions"), clicks=item.get("swipes"),
                observed_days=item.get("observed_days"), data_complete=item.get("data_complete"),
                account_id=account_id, account_name=account_name,
                parent_id=item.get("ad_squad_id"),
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
    campaign_totals: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"spend": 0.0, "purchases": 0.0})
    ad_group_totals: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"spend": 0.0, "purchases": 0.0})
    campaign_ad_groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    campaign_ads: dict[tuple[str, str], set[str]] = defaultdict(set)
    for fact in documents:
        account_id = str(fact.get("ad_account_id") or "")
        campaign_id = str(fact.get("campaign_id") or "")
        ad_group_id = str(fact.get("ad_group_id") or "")
        if fact.get("entity_level") == "ad_group" and campaign_id:
            campaign_totals[(account_id, campaign_id)]["spend"] += float(fact.get("spend_sar") or 0)
            campaign_totals[(account_id, campaign_id)]["purchases"] += float(fact.get("purchases") or 0)
            if ad_group_id:
                campaign_ad_groups[(account_id, campaign_id)].add(ad_group_id)
                ad_group_totals[(account_id, ad_group_id)]["spend"] += float(fact.get("spend_sar") or 0)
                ad_group_totals[(account_id, ad_group_id)]["purchases"] += float(fact.get("purchases") or 0)
        elif fact.get("entity_level") == "ad" and campaign_id:
            campaign_ads[(account_id, campaign_id)].add(str(fact.get("entity_id") or ""))
    rows: list[dict[str, Any]] = []
    requested_days = (end - start).days + 1
    for (level, entity_id, account_id), facts in groups.items():
        spend = sum(float(item.get("spend_sar") or 0) for item in facts)
        revenue = sum(float(item.get("revenue_sar") or 0) for item in facts)
        purchases = sum(float(item.get("purchases") or 0) for item in facts)
        impressions = sum(int(item.get("impressions") or 0) for item in facts)
        clicks = sum(int(item.get("clicks") or 0) for item in facts)
        latest = max(facts, key=lambda item: (str(item.get("observed_at") or ""), str(item.get("date") or "")))
        live_status = latest.get("effective_status") or latest.get("configured_status") or latest.get("status") or "unknown"
        row = _entity(
            provider="meta", level=level, entity_id=entity_id,
            entity_name=latest.get("entity_name"),
            parent_name=latest.get("ad_group_name") if level == "ad" else latest.get("campaign_name"),
            status=live_status, spend_sar=spend, revenue_sar=revenue,
            purchases=purchases, impressions=impressions, clicks=clicks,
            observed_days=len({item.get("date") for item in facts}),
            data_complete=len({item.get("date") for item in facts}) >= requested_days,
            account_id=account_id,
            account_name=latest.get("account_name"),
            parent_id=latest.get("ad_group_id") if level == "ad" else latest.get("campaign_id"),
            configured_status=latest.get("configured_status"),
            effective_status=latest.get("effective_status"),
            status_updated_at=latest.get("status_updated_at"),
            campaign_id=latest.get("campaign_id"), campaign_name=latest.get("campaign_name"),
            campaign_status=latest.get("campaign_status"),
            ad_group_id=latest.get("ad_group_id"), ad_group_name=latest.get("ad_group_name"),
            ad_group_status=latest.get("ad_group_status"),
            campaign_ad_group_count=latest.get("campaign_ad_group_count"),
            campaign_ad_count=latest.get("campaign_ad_count"),
            currency_native=latest.get("currency_native"),
            fx_rate_to_sar=latest.get("fx_rate_to_sar"),
            fx_source=latest.get("fx_source"),
            provider_result_source=(
                latest.get("provider_result_source") or "meta_ads_api_insights"
            ),
            action_report_time=latest.get("action_report_time") or "conversion",
            result_source=latest.get("result_source") or "platform",
            source_date_from=start.isoformat(),
            source_date_to=end.isoformat(),
            source_observed_at=latest.get("observed_at"),
            account_timezone=latest.get("account_timezone"),
            pagination_complete=latest.get("pagination_complete"),
            source_mode=latest.get("source_mode"),
            source_fact_collection=META_ENTITY_COLLECTION,
        )
        if row:
            campaign_key = (account_id, str(latest.get("campaign_id") or ""))
            ad_group_key = (account_id, str(latest.get("ad_group_id") or ""))
            campaign_total = campaign_totals.get(campaign_key) or {}
            ad_group_total = ad_group_totals.get(ad_group_key) or {}
            row.update({
                "entity_period_spend_sar": spend,
                "entity_period_purchases": int(purchases),
                "ad_group_period_spend_sar": ad_group_total.get("spend"),
                "ad_group_period_purchases": int(ad_group_total.get("purchases") or 0),
                "campaign_period_spend_sar": campaign_total.get("spend"),
                "campaign_period_purchases": int(campaign_total.get("purchases") or 0),
                "campaign_ad_group_count": (
                    len(campaign_ad_groups.get(campaign_key) or set())
                    or int(latest.get("campaign_ad_group_count") or 0)
                ),
                "campaign_ad_count": (
                    len(campaign_ads.get(campaign_key) or set())
                    or int(latest.get("campaign_ad_count") or 0)
                ),
            })
            rows.append(row)
    return rows


def _median(values: list[float]) -> float | None:
    clean = sorted(value for value in values if math.isfinite(value) and value >= 0)
    if not clean:
        return None
    middle = len(clean) // 2
    return clean[middle] if len(clean) % 2 else (clean[middle - 1] + clean[middle]) / 2


def _bounded_account_sample(
    rows: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Keep the highest-spend row from every ad account before filling the cap."""
    ordered = sorted(
        rows,
        key=lambda item: float(item.get("spend_sar") or 0),
        reverse=True,
    )
    if len(ordered) <= limit:
        return ordered
    leaders: list[dict[str, Any]] = []
    seen_accounts: set[tuple[str, str]] = set()
    for row in ordered:
        key = (
            str(row.get("provider") or "unknown"),
            str(row.get("account_id") or "unknown"),
        )
        if key in seen_accounts:
            continue
        seen_accounts.add(key)
        leaders.append(row)
    selected_ids = {id(row) for row in leaders[:limit]}
    selected = leaders[:limit]
    if len(selected) < limit:
        selected.extend(
            row for row in ordered
            if id(row) not in selected_ids
        )
    return sorted(
        selected[:limit],
        key=lambda item: float(item.get("spend_sar") or 0),
        reverse=True,
    )


def deterministic_candidates(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prepare complete active evidence without deciding what action is correct.

    The historical name is kept for compatibility with existing imports.  This
    function performs arithmetic and bounded ordering only: it does not label a
    campaign as waste, a scale opportunity, or prescribe an action.
    """
    prepared: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for source in entities:
        row = dict(source)
        spend = _number(row.get("spend_sar")) or 0.0
        if spend <= 0 or row.get("active") is False:
            continue
        days = max(1, int(_number(row.get("observed_days")) or 1))
        row["spend_per_day_sar"] = round(spend / days, 2)
        row["ctr_pct"] = round(
            float(row.get("clicks") or 0) / float(row.get("impressions") or 1) * 100,
            3,
        ) if int(row.get("impressions") or 0) > 0 else None
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
        baseline = benchmarks[(str(row.get("provider")), str(row.get("entity_level")))]
        row["account_benchmark"] = baseline
        row["data_quality"] = (
            "complete" if row.get("data_complete") else "partial"
        )
        candidates.append(row)
    return _bounded_account_sample(candidates, MAX_AI_CANDIDATES)


def _fingerprint(candidates: list[dict[str, Any]]) -> str:
    stable = [
        {
            "provider": row.get("provider"), "level": row.get("entity_level"),
            "account_id": row.get("account_id"),
            "id": row.get("entity_id"), "spend": round(float(row.get("spend_sar") or 0), 0),
            "pace": round(float(row.get("spend_per_day_sar") or 0), 0),
            "revenue": round(float(row.get("revenue_sar") or 0), 0),
            "roas": round(float(row.get("roas") or 0), 2),
            "cpa": round(float(row.get("cpa_sar") or 0), 1),
            "purchases": row.get("purchases"),
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
    """Validate model references and execution safety without choosing actions."""
    evidence = {
        (
            row.get("provider"), row.get("entity_level"),
            str(row.get("account_id") or ""), str(row.get("entity_id")),
        ): row
        for row in candidates
    }
    governed: list[RecommendationItem] = []
    for item in output.recommendations[:MAX_RECOMMENDATIONS]:
        row = evidence.get((
            item.provider, item.entity_level,
            str(item.account_id or ""), item.entity_id,
        ))
        if not row:
            continue
        if row.get("active") is False:
            continue
        action = item.action
        if action == "scale" and (not row.get("data_complete") or int(row.get("purchases") or 0) < 3):
            action = "monitor"
        governed.append(item.model_copy(update={
            "recommendation_id": (
                f"{item.provider}:{item.entity_level}:"
                f"{row.get('account_id') or 'unknown'}:{item.entity_id}"
            ),
            "entity_name": str(row.get("entity_name") or item.entity_id),
            "account_id": row.get("account_id"),
            "account_name": row.get("account_name") or row.get("account_id"),
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
    summary: str | None = None,
) -> RecommendationOutput:
    """Issue a clearly attributed, conservative Mezan fallback recommendation."""
    recommendations: list[RecommendationItem] = []
    for row in candidates:
        spend = float(row.get("spend_sar") or 0)
        purchases = int(row.get("purchases") or 0)
        cpa = _number(row.get("cpa_sar"))
        action: str | None = None
        change_percent: int | None = None
        priority = "medium"
        title = "مراقبة احتياطية من ميزان"
        why_now = "تعذر تحليل OpenAI، لذلك استخدم ميزان قراءة احتياطية محدودة للأرقام الحالية."
        if purchases == 0 and spend >= TARGET_CPA_SAR * 3:
            action, priority, title = "pause", "critical", "إيقاف احتياطي مقترح"
            why_now = "تجاوز الصرف ثلاثة أضعاف تكلفة الطلب المرجعية دون تسجيل أي شراء."
        elif purchases == 0 and spend >= TARGET_CPA_SAR * 1.5:
            action, change_percent, priority, title = "reduce", 15, "high", "خفض احتياطي مقترح"
            why_now = "تجاوز الصرف مرة ونصف تكلفة الطلب المرجعية دون تسجيل شراء."
        elif purchases > 0 and cpa is not None and cpa >= TARGET_CPA_SAR * 1.5:
            action, change_percent, priority, title = "reduce", 15, "high", "خفض احتياطي مقترح"
            why_now = "تكلفة الشراء الحالية أعلى 50% على الأقل من المرجع الاقتصادي."
        if action is None:
            continue
        entity_id = str(row.get("entity_id") or "")
        evidence = [
            f"الصرف {spend:.2f} ر.س",
            f"المشتريات {purchases}",
        ]
        if cpa is not None:
            evidence.append(f"تكلفة الشراء {cpa:.2f} ر.س")
        recommendations.append(RecommendationItem(
            recommendation_id=(
                f"{row.get('provider')}:{row.get('entity_level')}:"
                f"{row.get('account_id') or 'unknown'}:{entity_id}"
            ),
            provider=str(row.get("provider")),
            entity_level=str(row.get("entity_level")),
            entity_id=entity_id,
            entity_name=str(row.get("entity_name") or entity_id),
            account_id=row.get("account_id"),
            account_name=row.get("account_name") or row.get("account_id"),
            parent_name=row.get("parent_name"),
            action=action,
            change_percent=change_percent,
            priority=priority,
            confidence="medium" if row.get("data_complete") else "low",
            title=title,
            rationale=why_now,
            evidence=evidence,
            why_now=why_now,
            recommended_wait_hours=5,
            observation_plan="أعد التحليل عند عودة OpenAI وقارن الصرف والمشتريات قبل قرار آخر.",
            success_criteria=[
                "يتوقف تسارع الصرف غير المنتج",
                "تظهر مشتريات أو تتحسن تكلفة الشراء",
            ],
            risk_if_ignored="قد يستمر الصرف غير المنتج حتى موعد التحليل التالي.",
            guardrail="توصية احتياطية من ميزان؛ لا تُنفذ إلا بعد موافقة المالك.",
            next_check_at=next_check_at,
        ))
        if len(recommendations) >= MAX_RECOMMENDATIONS:
            break
    return RecommendationOutput(
        summary=summary or _fallback_summary(
            limitation.removeprefix("openai_recommendation:")
        ),
        recommendations=recommendations,
        limitations=[limitation],
    )


def _saudi_calendar_context(current: datetime) -> dict[str, Any]:
    local = current.astimezone(RIYADH_OFFSET)
    today = local.date()

    def adjusted_salary_day(month_day: date) -> date:
        if month_day.weekday() == 4:  # Friday -> Thursday
            return month_day - timedelta(days=1)
        if month_day.weekday() == 5:  # Saturday -> Sunday
            return month_day + timedelta(days=1)
        return month_day

    salary = adjusted_salary_day(date(today.year, today.month, 27))
    if today > salary:
        next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        salary = adjusted_salary_day(date(next_month.year, next_month.month, 27))
    return {
        "local_datetime": local.isoformat(),
        "weekday": local.strftime("%A"),
        "day_of_month": today.day,
        "days_until_adjusted_salary_date": (salary - today).days,
        "adjusted_salary_date": salary.isoformat(),
        "is_thursday_or_friday": today.weekday() in {3, 4},
        "note": (
            "تقويم تشغيلي فقط. أثبت أثر يوم الأسبوع وموعد الراتب من تاريخ "
            "أداء المتجر، ولا تفترض أنه سبب تلقائي."
        ),
    }


async def _prior_ai_context(db: Any, user_id: str) -> dict[str, Any]:
    snapshots = await db[RECOMMENDATION_COLLECTION].find(
        {"user_id": user_id},
        {"_id": 0, "generated_at": 1, "range": 1, "recommendations": 1},
    ).sort("generated_at", -1).limit(8).to_list(length=8)
    executions = await db[EXECUTION_COLLECTION].find(
        {"user_id": user_id},
        {"_id": 0, "recommendation_id": 1, "status": 1, "started_at": 1,
         "finished_at": 1, "result": 1, "error_code": 1},
    ).sort("started_at", -1).limit(30).to_list(length=30)
    compact_snapshots = []
    for snapshot in snapshots:
        compact_snapshots.append({
            "generated_at": snapshot.get("generated_at"),
            "range": snapshot.get("range"),
            "recommendations": [
                {key: item.get(key) for key in (
                    "recommendation_id", "action", "change_percent", "priority",
                    "confidence", "account_id", "account_name", "entity_name",
                    "rationale", "evidence",
                    "decision_facts", "financial_impact", "execution_status",
                )}
                for item in (snapshot.get("recommendations") or [])[:18]
            ],
        })
    return {"recent_recommendations": compact_snapshots, "recent_executions": executions}


async def _campaign_history_context(
    db: Any,
    user_id: str,
    end: date,
) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for days in (7, 30):
        rows: list[dict[str, Any]] = []
        for provider in ("snapchat", "meta"):
            try:
                rows.extend(await _campaign_entities(
                    db, user_id, provider, end - timedelta(days=days - 1), end
                ))
            except Exception:
                logger.exception("Campaign AI %s-day history failed for %s", days, provider)
        output[f"last_{days}_days"] = [
            {key: row.get(key) for key in (
                "provider", "account_id", "account_name", "entity_id",
                "entity_name", "status", "spend_sar",
                "revenue_sar", "purchases", "impressions", "clicks", "roas",
                "cpa_sar", "observed_days", "data_complete",
            )}
            for row in rows[:160]
        ]
    return output


async def _business_profit_context(
    loader: Callable[..., Any] | None,
    user_id: str,
    end: date,
) -> dict[str, Any]:
    if loader is None:
        return {"available": False, "reason": "dashboard_profit_loader_unavailable"}
    windows: dict[str, Any] = {}
    for label, days in (("today", 1), ("last_3_days", 3), ("last_7_days", 7), ("last_30_days", 30)):
        start = end - timedelta(days=days - 1)
        payload = await loader(
            user={"id": user_id},
            from_date=start.isoformat(),
            to_date=end.isoformat(),
            payment_methods=None,
            shipping_companies=None,
            include_legacy_analyses=False,
            allow_self_heal=False,
        )
        totals = (payload or {}).get("totals") or {}
        windows[label] = {
            "from": start.isoformat(),
            "to": end.isoformat(),
            **{key: totals.get(key) for key in (
                "total_sales", "total_orders", "net_profit", "total_ads_cost",
                "total_product_cost", "total_payment_fees", "total_shipping_cost",
                "operating_expenses_total", "overall_roas", "avg_cost_per_order",
                "missing_product_cost_count", "incomplete_profit_orders_count",
            )},
        }
    return {
        "available": True,
        "source": "mezan_dashboard_profit_totals",
        "windows": windows,
    }


def _recommendation_explanation(
    item: RecommendationItem,
    row: dict[str, Any],
) -> dict[str, Any]:
    """Turn the measured signal into an auditable, merchant-facing decision brief."""
    spend = _safe_metric(row.get("spend_sar")) or 0
    purchases = int(_number(row.get("purchases")) or 0)
    pace = _safe_metric(row.get("spend_per_day_sar"))
    cpa = _safe_metric(row.get("cpa_sar"))
    roas = _safe_metric(row.get("roas"))
    observed_days = max(1, int(_number(row.get("observed_days")) or 1))

    facts = [
        f"صرف {spend:.2f} ر.س خلال {observed_days} يوم",
        f"حقق {purchases} مشتريات",
    ]
    if pace is not None:
        facts.append(f"وتيرة الصرف {pace:.2f} ر.س يوميًا")
    if cpa is not None:
        facts.append(f"تكلفة الشراء {cpa:.2f} ر.س مقابل هدف {TARGET_CPA_SAR:.2f} ر.س")
    if roas is not None:
        facts.append(f"العائد {roas:.2f}× مقابل هدف {TARGET_ROAS:.2f}×")

    wait_hours = item.recommended_wait_hours
    if item.action == "pause":
        proposed_action = "أوقف الكيان مؤقتًا، ثم لا تعِد تشغيله قبل مراجعة مصدر التحويل وجودة الإعلان."
    elif item.action == "reduce":
        proposed_action = f"اخفض الميزانية {int(item.change_percent or 15)}% فقط، وتجنب أي خفض ثانٍ خلال فترة المراقبة."
    elif item.action == "scale":
        proposed_action = f"ارفع الميزانية {int(item.change_percent or 15)}% فقط، مع منع أي توسعة ثانية قبل القياس."
    elif item.action == "maintain":
        proposed_action = "استمر بالميزانية الحالية؛ لا يوجد دليل يبرر تغييرها الآن."
    else:
        proposed_action = "راقب دون تغيير الميزانية حتى تكتمل نافذة القياس المطلوبة."

    revenue = _safe_metric(row.get("revenue_sar")) or 0
    hourly_spend = (pace if pace is not None else spend / observed_days) / 24
    hourly_revenue = revenue / observed_days / 24
    future_spend_without_action = hourly_spend * wait_hours
    future_revenue_without_action = hourly_revenue * wait_hours
    future_contribution_without_action = (
        future_revenue_without_action * CAMPAIGN_GROSS_MARGIN_RATE
        - future_spend_without_action
    )
    change_ratio = min(0.30, max(0.05, float(item.change_percent or 15) / 100))
    if item.action == "pause":
        future_spend_with_action = 0.0
        future_revenue_with_action = 0.0
    elif item.action == "reduce":
        future_spend_with_action = future_spend_without_action * (1 - change_ratio)
        # For proven no-result waste, reducing spend is modeled as avoided waste.
        # Otherwise revenue is reduced proportionally to avoid overstating upside.
        future_revenue_with_action = (
            future_revenue_without_action
            if purchases == 0
            else future_revenue_without_action * (1 - change_ratio)
        )
    elif item.action == "scale":
        future_spend_with_action = future_spend_without_action * (1 + change_ratio)
        future_revenue_with_action = future_revenue_without_action * (1 + change_ratio)
    else:
        future_spend_with_action = future_spend_without_action
        future_revenue_with_action = future_revenue_without_action
    future_contribution_with_action = (
        future_revenue_with_action * CAMPAIGN_GROSS_MARGIN_RATE
        - future_spend_with_action
    )
    period_contribution = revenue * CAMPAIGN_GROSS_MARGIN_RATE - spend
    return {
        "decision_signal": "openai_independent_judgment",
        "decision_facts": facts,
        "why_now": item.why_now,
        "proposed_action": proposed_action,
        "recommended_wait_hours": wait_hours,
        "observation_plan": (
            f"المجدول سيعيد التحليل كل 5 ساعات. اصبر {wait_hours} ساعات قبل قرار ثانٍ. "
            f"{item.observation_plan}"
        ),
        "success_criteria": item.success_criteria,
        "risk_if_ignored": item.risk_if_ignored,
        "financial_impact": {
            "basis": "provider_revenue_x_gross_margin_minus_ad_spend",
            "is_estimate": True,
            "gross_margin_rate": round(CAMPAIGN_GROSS_MARGIN_RATE, 4),
            "period_spend_sar": round(spend, 2),
            "period_provider_revenue_sar": round(revenue, 2),
            "period_estimated_gross_profit_sar": round(
                revenue * CAMPAIGN_GROSS_MARGIN_RATE,
                2,
            ),
            "period_estimated_contribution_sar": round(period_contribution, 2),
            "forecast_hours": wait_hours,
            "forecast_without_action_sar": round(future_contribution_without_action, 2),
            "forecast_with_action_sar": round(future_contribution_with_action, 2),
            "forecast_delta_sar": round(
                future_contribution_with_action - future_contribution_without_action,
                2,
            ),
            "confidence": "medium" if bool(row.get("data_complete")) else "low",
            "limitation": (
                "تقدير مبني على إيراد المنصة وهامش إجمالي 55% وثبات وتيرة الأداء؛ "
                "يصبح صافي ربح مؤكدًا فقط عند اكتمال ربط طلب سلة بالحملة وتكاليفه."
            ),
        },
    }


async def _ask_openai(
    candidates: list[dict[str, Any]],
    *,
    now: datetime,
    campaign_history: dict[str, Any],
    prior_decisions: dict[str, Any],
    business_profit: dict[str, Any],
) -> RecommendationOutput:
    if AsyncOpenAI is None:
        raise RuntimeError("openai_sdk_missing")
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("openai_api_key_missing")
    next_check = _iso(now + timedelta(hours=5))
    safe_rows = [
        {key: row.get(key) for key in (
            "provider", "entity_level", "account_id", "account_name", "entity_id",
            "entity_name", "parent_name",
            "status", "configured_status", "effective_status", "status_updated_at", "active",
            "campaign_id", "campaign_name", "campaign_status", "ad_group_id", "ad_group_name",
            "ad_group_status", "campaign_ad_group_count", "campaign_ad_count",
            "entity_period_spend_sar", "entity_period_purchases",
            "ad_group_period_spend_sar", "ad_group_period_purchases",
            "campaign_period_spend_sar", "campaign_period_purchases",
            "spend_sar", "revenue_sar", "purchases", "impressions",
            "clicks", "roas", "cpa_sar", "observed_days", "spend_per_day_sar",
            "ctr_pct", "data_complete", "data_quality", "account_benchmark",
        )}
        for row in candidates
    ]
    # Avoid spending the eight-minute monitor budget on two long SDK attempts;
    # the next scheduled pass is the safe retry boundary.
    client = AsyncOpenAI(
        api_key=api_key,
        max_retries=0,
        timeout=OPENAI_TIMEOUT_SECONDS,
    )
    try:
        response = await client.responses.create(
            model=os.environ.get("MEZAN_CAMPAIGN_AI_MODEL", os.environ.get("MEZAN_OPENAI_MODEL", "gpt-5-mini")),
            instructions=(
                "أنت مدير الأداء المستقل لمتجر أماسي داخل ميزان، وأنت صاحب الحكم التسويقي؛ "
                "لا توجد نتيجة أو توصية مقررة مسبقًا من كود ميزان. ادرس كامل الكيانات النشطة، "
                "تعامل مع status وconfigured_status وeffective_status كحالة Meta الحية: لا تقترح "
                "إيقافًا أو خفضًا أو توسعة لكيان متوقف أو غير نشط، ولا تكرر إيقاف إعلان أوقفه المالك. "
                "افحص كذلك حالة الحملة والمجموعة الأم قبل اقتراح أي إجراء على الإعلان. "
                "مقارنة الحساب، تاريخ 7 و30 يومًا، تقويم السوق السعودي، والقرارات السابقة. "
                "ابدأ بأثر الحملات على صافي ربح أو خسارة ميزان اليوم ثم نوافذ 3 و7 و30 يومًا، "
                "وتعلم من القرارات المنفذة سابقًا بمقارنة أرقامها القديمة بالأداء الحالي. "
                "استنتج بنفسك هل الصواب إيقاف أو خفض أو مراقبة أو إبقاء أو توسعة. ميّز بين "
                "فشل تاريخي مستمر وتذبذب قصير، وبين ضعف الطلب في السوق وضعف الإعلان أو "
                "الاستهداف. CPA %.2f ر.س وROAS %.2f× مرجعان اقتصاديان فقط وليسا قواعد قرار. "
                "لا تعتبر الراتب أو الخميس والجمعة سببًا إلا إذا دعمه أداء المتجر التاريخي. "
                "أعطِ الأولوية للتوصيات الأعلى أثرًا على صافي الربح، وافحص مستوى الإعلان أو "
                "المجموعة قبل إيقاف الحملة الأم كي لا توقف عنصرًا ناجحًا معها. اشرح لماذا الآن، "
                "حلّل كل حساب إعلاني بصورة مستقلة، وخصوصًا حسابات Snapchat، واحتفظ حرفيًا "
                "بـ account_id وaccount_name المرسلين لكل توصية حتى لا تختلط الحسابات. "
                "كم ساعة ننتظر، ما الذي يثبت نجاح القرار، وما خطر تجاهله بطريقة مقنعة وقابلة "
                "للمراجعة. لا تدّعِ تنفيذ أي تعديل؛ التنفيذ لا يتم إلا بعد موافقة المالك. "
                "recommendation_id بصيغة provider:level:account_id:id. اكتب بالعربية وبأرقام إنجليزية، "
                "واجعل next_check_at مساويًا للقيمة المرسلة."
            ) % (TARGET_CPA_SAR, TARGET_ROAS),
            input=json.dumps({
                "next_check_at": next_check,
                "saudi_calendar": _saudi_calendar_context(now),
                "active_entities_last_3_days": safe_rows,
                "campaign_history": campaign_history,
                "mezan_overall_profit_and_loss": business_profit,
                "prior_mezan_ai_decisions": prior_decisions,
            }, ensure_ascii=False, default=str),
            max_output_tokens=OPENAI_MAX_OUTPUT_TOKENS,
            reasoning={"effort": "low"},
            store=False,
            text={"format": {"type": "json_schema", "name": "campaign_monitor_recommendations", "strict": True, "schema": AI_SCHEMA}},
        )
        if getattr(response, "status", None) == "incomplete":
            details = getattr(response, "incomplete_details", None)
            reason = _text(getattr(details, "reason", "unknown"), limit=80) or "unknown"
            raise CampaignOpenAIError(f"openai_response_incomplete_{reason}")
        output = _normalize_openai_output(
            response.output_text,
            candidates,
            next_check_at=next_check,
        )
        return _govern_output(output, candidates, next_check_at=next_check)
    finally:
        await client.close()


async def run_campaign_ai_monitor(
    db: Any,
    user_id: str,
    *,
    now: Callable[[], datetime] = _utcnow,
    refresh_meta: bool = True,
    business_context_loader: Callable[..., Any] | None = None,
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
        entities = _bounded_account_sample(entities, MAX_ENTITY_ROWS)
        candidates = deterministic_candidates(entities)
        fingerprint = _fingerprint(candidates)
        campaign_history = await _campaign_history_context(db, user_id, end)
        prior_decisions = await _prior_ai_context(db, user_id)
        try:
            business_profit = await _business_profit_context(
                business_context_loader, user_id, end
            )
        except Exception as exc:
            errors.append({
                "source": "mezan_business_profit",
                "code": _text(type(exc).__name__, limit=100),
            })
            business_profit = {
                "available": False,
                "reason": "dashboard_profit_context_failed",
            }
        if not candidates:
            recommendation_source = "none"
            result = RecommendationOutput(
                summary="لا توجد كيانات نشطة ذات صرف يمكن للذكاء تحليلها في آخر 3 أيام.",
                recommendations=[],
                limitations=[item["source"] for item in errors],
            )
        else:
            try:
                result = await _ask_openai(
                    candidates,
                    now=current,
                    campaign_history=campaign_history,
                    prior_decisions=prior_decisions,
                    business_profit=business_profit,
                )
                recommendation_source = "openai"
            except Exception as exc:
                error_code = _openai_error_code(exc)
                logger.warning(
                    "Campaign AI model output unavailable for user %s (%s); using governed fallback",
                    user_id,
                    error_code,
                )
                errors.append({"source": "openai_recommendation", "code": error_code})
                result = _deterministic_recommendations(
                    candidates,
                    next_check_at=_iso(current + timedelta(hours=5)),
                    limitation=f"openai_recommendation:{error_code}",
                )
                recommendation_source = "mezan_fallback"
        candidate_by_key = {
            (
                row.get("provider"), row.get("entity_level"),
                str(row.get("account_id") or ""), str(row.get("entity_id")),
            ): row
            for row in candidates
        }
        recommendation_rows = []
        execution_targets: dict[str, dict[str, Any]] = {}
        for item in result.recommendations:
            public_item = item.model_dump()
            target = candidate_by_key.get((
                item.provider, item.entity_level,
                str(item.account_id or ""), item.entity_id,
            )) or {}
            public_item.update(_recommendation_explanation(item, target))
            public_item["generated_at"] = started_at
            public_item["recommendation_source"] = recommendation_source
            public_item["decision_score"] = None
            public_item.update({key: target.get(key) for key in (
                "status", "configured_status", "effective_status", "status_updated_at",
                "campaign_id", "campaign_name", "campaign_status",
                "ad_group_id", "ad_group_name", "ad_group_status",
                "campaign_ad_group_count", "campaign_ad_count",
                "entity_period_spend_sar", "entity_period_purchases",
                "ad_group_period_spend_sar", "ad_group_period_purchases",
                "campaign_period_spend_sar", "campaign_period_purchases",
            )})
            executable = bool(
                item.action in {"pause", "reduce", "scale"}
                and target.get("account_id")
                and target.get("active")
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
                    )
                }
        document = {
            "snapshot_id": str(uuid.uuid4()), "run_id": run_id, "user_id": user_id,
            "generated_at": _iso(current), "next_run_at": _iso(current + timedelta(hours=5)),
            "range": {"from": start.isoformat(), "to": end.isoformat()},
            "summary": result.summary,
            "recommendations": recommendation_rows,
            "execution_targets": execution_targets,
            "limitations": list(dict.fromkeys([*result.limitations, *[item["source"] for item in errors]])),
            "fingerprint": fingerprint,
            "entities_scanned": len(entities), "candidates_scanned": len(candidates),
            "providers": ["snapchat", "meta"], "mode": "recommend_then_approve",
            "decision_authority": recommendation_source,
            "recommendation_source": recommendation_source,
            "decision_interval_hours": 5,
            "context_windows_days": [3, 7, 30],
            "writes_performed": False, "meta_refresh": meta_refresh,
            "business_profit_context_available": bool(business_profit.get("available")),
        }
        await db[RECOMMENDATION_COLLECTION].insert_one(document)
        await db[RUN_COLLECTION].update_one(
            {"run_id": run_id, "user_id": user_id},
            {"$set": {"status": "complete", "finished_at": _iso(), "snapshot_id": document["snapshot_id"], "recommendations": len(document["recommendations"])}},
        )
        return {key: value for key, value in document.items() if key != "user_id"}
    except Exception as exc:
        logger.exception("Campaign AI monitor failed for user %s", user_id)
        await db[RUN_COLLECTION].update_one(
            {"run_id": run_id, "user_id": user_id},
            {"$set": {"status": "failed", "finished_at": _iso(), "error_code": _text(getattr(exc, "code", type(exc).__name__), limit=100)}},
        )
        raise


async def _monitored_user_ids(db: Any) -> list[str]:
    def _owners(values: Any) -> set[str]:
        return {
            owner
            for value in values or []
            if (owner := _text(value, limit=120))
        }

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
    owners = _owners(values)
    if not owners:
        values = await db.mezan_integrations_v2.distinct(
            "user_id",
            {"provider": {"$in": ["snapchat_ads", "meta_ads"]}, "connection_status": "connected"},
        )
        owners = _owners(values)
    if not owners:
        # Existing merchants can have healthy provider credentials and current
        # campaign facts in the legacy connector collections without a V2
        # projection.  Do not make recommendations disappear merely because a
        # user connected before V2 was introduced.  Credentials remain in the
        # legacy stores; this only discovers owner ids and writes nothing.
        snapchat_owners, meta_owners = await asyncio.gather(
            db.snapchat_connections.distinct(
                "user_id",
                {"refresh_token": {"$exists": True, "$nin": ["", None]}},
            ),
            db.meta_connections.distinct(
                "user_id",
                {
                    "access_token": {"$exists": True, "$nin": ["", None]},
                    # Match the legacy reader: an absent status is not proof
                    # of a failed connection, while explicit errors must not
                    # be scheduled as active marketing accounts.
                    "connection_status": {
                        "$nin": ["error", "failed", "last_check_failed"],
                    },
                },
            ),
        )
        owners = _owners(snapchat_owners) | _owners(meta_owners)
    return sorted(owners)[:100]


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


async def run_all_campaign_ai_monitors(
    db: Any,
    *,
    business_context_loader: Callable[..., Any] | None = None,
) -> dict[str, Any]:
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
                    run_campaign_ai_monitor(
                        db,
                        user_id,
                        business_context_loader=business_context_loader,
                    ),
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
    business_context_loader: Callable[..., Any] | None = None,
) -> asyncio.Task:
    async def loop() -> None:
        await asyncio.sleep(max(0.0, initial_delay_seconds))
        while True:
            try:
                summary = await run_all_campaign_ai_monitors(
                    db,
                    business_context_loader=business_context_loader,
                )
                logger.info("Campaign AI 5-hour monitor complete: %s", summary)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Campaign AI scheduler failed")
            await asyncio.sleep(max(60.0, interval_seconds))
    return asyncio.create_task(loop())


async def _execute_snapchat_approval(
    db: Any,
    user_id: str,
    recommendation: dict[str, Any],
    target: dict[str, Any],
    *,
    idempotency_key: str,
    snapshot_id: str,
    recommendation_id: str,
    snapshot_digest: str,
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
            expected_outcome={
                "source": "ai_recommendation_5h",
                "action": requested,
                "snapshot_id": snapshot_id,
                "recommendation_id": recommendation_id,
                "snapshot_digest": snapshot_digest,
                "execution_quality_contract": _execution_quality.CONTRACT_VERSION,
            },
            safety_protocol_version=2,
        ),
    )
    token = proposal.get("confirm_token")
    if not token:
        if proposal.get("status") == "completed":
            return proposal
        raise HTTPException(status_code=409, detail={"code": "recommendation_proposal_not_approvable"})
    proposal_row = await db["mezan_snapchat_campaign_proposals_v1"].find_one(
        {
            "user_id": user_id,
            "proposal_id": str(proposal.get("proposal_id") or ""),
        },
        {"_id": 0, "original_snapshot": 1},
    ) or {}
    _execution_quality.require_provider_state_unchanged(
        "snapchat",
        recommendation,
        target,
        proposal_row.get("original_snapshot"),
    )
    approved = await approve_snapchat_management_proposal(
        db,
        user_id,
        user_id,
        str(proposal["proposal_id"]),
        SnapchatManagementApprovalInput(
            confirm_token=str(token), expected_revision=int(proposal.get("revision") or 1)
        ),
    )
    await _execution_quality.preflight_approved_execution(
        db,
        recommendation_collection=RECOMMENDATION_COLLECTION,
        user_id=user_id,
        snapshot_id=snapshot_id,
        recommendation_id=recommendation_id,
        expected_digest=snapshot_digest,
    )
    return await execute_snapchat_management_proposal(
        db, user_id, user_id, str(approved["proposal_id"])
    )


def _meta_state_matches_mutation(
    mutation: dict[str, Any],
    state: dict[str, Any] | None,
) -> bool:
    """Return True only when a provider read proves the requested mutation."""
    current = state if isinstance(state, dict) else {}
    if "status" in mutation:
        expected = str(mutation.get("status") or "").upper()
        actual = str(
            current.get("status") or current.get("effective_status") or ""
        ).upper()
        return bool(expected) and actual == expected
    if "daily_budget" in mutation:
        expected_budget = str(mutation.get("daily_budget") or "")
        actual_budget = str(current.get("daily_budget") or "")
        return bool(expected_budget) and actual_budget == expected_budget
    return False


async def _reconcile_meta_provider_uncertainty(
    db: Any,
    user_id: str,
    entity_id: str,
    current_state: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve a prior ambiguous Meta write only from provider evidence.

    A previous accepted/possibly-reached write is never retried merely because
    its verification failed.  If the provider now proves the requested state,
    the old execution is closed as completed.  Otherwise the entity stays
    fail-closed and a new financial mutation is blocked.
    """
    unresolved = await db[EXECUTION_COLLECTION].find_one(
        {
            "user_id": user_id,
            "provider": "meta",
            "status": {"$in": ["provider_state_uncertain", "verification_required"]},
            "$or": [
                {"entity_id": entity_id},
                {"result.entity_id": entity_id},
            ],
        },
        {"_id": 0},
        sort=[("finished_at", -1), ("approved_at", -1)],
    )
    if not unresolved:
        return None
    result = unresolved.get("result") or {}
    requested_change = result.get("requested_change") or {}
    if _meta_state_matches_mutation(requested_change, current_state):
        reconciled_at = _iso()
        execution_id = str(unresolved.get("execution_id") or "")
        await db[EXECUTION_COLLECTION].update_one(
            {"execution_id": execution_id},
            {"$set": {
                "status": "completed",
                "finished_at": reconciled_at,
                "result.status": "completed",
                "result.reconciliation": {
                    "status": "provider_state_confirmed",
                    "reconciled_at": reconciled_at,
                    "provider_state": {
                        key: current_state.get(key)
                        for key in ("status", "effective_status", "daily_budget")
                    },
                },
            }},
        )
        snapshot_id = str(unresolved.get("snapshot_id") or "")
        recommendation_id = str(unresolved.get("recommendation_id") or "")
        if snapshot_id and recommendation_id:
            await db[RECOMMENDATION_COLLECTION].update_one(
                {"user_id": user_id, "snapshot_id": snapshot_id},
                {"$set": {
                    "recommendations.$[item].execution_status": "completed",
                    "recommendations.$[item].executed_at": reconciled_at,
                }},
                array_filters=[{"item.recommendation_id": recommendation_id}],
            )
        return {
            "resolved": True,
            "execution_id": execution_id,
            "status": "completed",
        }
    raise HTTPException(
        status_code=409,
        detail={
            "code": "meta_provider_state_uncertain",
            "execution_id": unresolved.get("execution_id"),
            "entity_id": entity_id,
            "requested_change": requested_change,
            "message": "Provider state must be reconciled before another Meta write.",
        },
    )


async def _execute_meta_approval(
    db: Any,
    user_id: str,
    recommendation: dict[str, Any],
    target: dict[str, Any],
    *,
    snapshot_id: str,
    recommendation_id: str,
    snapshot_digest: str,
) -> dict[str, Any]:
    # Revalidate analytical quality first.  No provider mutation may proceed
    # unless the P0-3 quality contract and the current provider state both hold.
    await _execution_quality.preflight_approved_execution(
        db,
        recommendation_collection=RECOMMENDATION_COLLECTION,
        user_id=user_id,
        snapshot_id=snapshot_id,
        recommendation_id=recommendation_id,
        expected_digest=snapshot_digest,
    )
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
        # P0-4: before any new financial write, reconcile an older ambiguous
        # write from provider truth.  If it is still not provable, fail closed.
        await _reconcile_meta_provider_uncertainty(db, user_id, entity_id, before)
        _execution_quality.require_provider_state_unchanged(
            "meta", recommendation, target, before
        )
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
        before_compact = {
            key: before.get(key) for key in ("status", "effective_status", "daily_budget")
        }
        try:
            write = await client.post(
                f"{base}/{entity_id}",
                data={"access_token": access_token, "appsecret_proof": proof, **mutation},
            )
        except Exception as exc:
            # A transport exception does not prove that Meta did not receive the
            # POST.  Treat it as ambiguous and never retry the mutation blindly.
            return {
                "provider": "meta",
                "entity_id": entity_id,
                "status": "provider_state_uncertain",
                "before": before_compact,
                "requested_change": mutation,
                "verification": None,
                "provider_write_reached": None,
                "uncertainty_reason": "meta_write_transport_outcome_unknown",
                "transport_error": type(exc).__name__,
            }
        if write.status_code >= 400:
            raise HTTPException(status_code=502, detail={"code": "meta_recommendation_write_failed"})
        try:
            verify = await client.get(
                f"{base}/{entity_id}",
                params={
                    "access_token": access_token,
                    "appsecret_proof": proof,
                    "fields": "id,name,status,effective_status,daily_budget",
                },
            )
            after = verify.json() if verify.status_code < 400 else {}
            verification_error = None
            if verify.status_code >= 400:
                verification_error = f"http_{verify.status_code}"
        except Exception as exc:
            after = {}
            verification_error = type(exc).__name__
        verified = _meta_state_matches_mutation(mutation, after)
        return {
            "provider": "meta",
            "entity_id": entity_id,
            "status": "completed" if verified else "provider_state_uncertain",
            "before": before_compact,
            "requested_change": mutation,
            "verification": after if after else None,
            "provider_write_reached": True,
            "uncertainty_reason": (
                None if verified else
                ("meta_verification_read_failed" if verification_error else "meta_verification_mismatch")
            ),
            "verification_error": verification_error,
        }


def _execution_quality_http_exception(
    exc: _execution_quality.ExecutionQualityBlocked,
) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "campaign_execution_data_quality_blocked",
            "contract_version": _execution_quality.CONTRACT_VERSION,
            "blockers": exc.blockers,
        },
    )


async def _execute_approved_recommendation(
    db: Any,
    user_id: str,
    *,
    snapshot_id: str,
    recommendation_id: str,
    expected_digest: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Shared fail-closed dispatch for both Campaign-AI providers."""
    if not expected_digest:
        raise _execution_quality.ExecutionQualityBlocked(
            ["execution_snapshot_digest_missing"]
        )
    checked = await _execution_quality.preflight_approved_execution(
        db,
        recommendation_collection=RECOMMENDATION_COLLECTION,
        user_id=user_id,
        snapshot_id=snapshot_id,
        recommendation_id=recommendation_id,
        expected_digest=expected_digest,
    )
    recommendation = checked["recommendation"]
    target = checked["target"]
    provider = recommendation.get("provider")
    if provider == "snapchat":
        return await _execute_snapchat_approval(
            db,
            user_id,
            recommendation,
            target,
            idempotency_key=idempotency_key,
            snapshot_id=snapshot_id,
            recommendation_id=recommendation_id,
            snapshot_digest=expected_digest,
        )
    if provider == "meta":
        return await _execute_meta_approval(
            db,
            user_id,
            recommendation,
            target,
            snapshot_id=snapshot_id,
            recommendation_id=recommendation_id,
            snapshot_digest=expected_digest,
        )
    raise HTTPException(
        status_code=409,
        detail={"code": "recommendation_provider_unsupported"},
    )


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
        if _utcnow() - generated_at.astimezone(timezone.utc) > timedelta(hours=5):
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
        try:
            checked = await _execution_quality.preflight_approved_execution(
                db,
                recommendation_collection=RECOMMENDATION_COLLECTION,
                user_id=user_id,
                snapshot_id=payload.snapshot_id,
                recommendation_id=recommendation_id,
            )
        except _execution_quality.ExecutionQualityBlocked as exc:
            raise _execution_quality_http_exception(exc) from exc
        recommendation = checked["recommendation"]
        target = checked["target"]
        snapshot_digest = checked["snapshot_digest"]
        execution_id = hashlib.sha256(
            f"{user_id}:{payload.snapshot_id}:{recommendation_id}".encode()
        ).hexdigest()
        started = {
            "execution_id": execution_id,
            "user_id": user_id,
            "snapshot_id": payload.snapshot_id,
            "recommendation_id": recommendation_id,
            "provider": recommendation.get("provider"),
            "entity_id": _text(target.get("entity_id"), limit=120),
            "entity_level": recommendation.get("entity_level"),
            "action": recommendation.get("action"),
            "status": "executing",
            "approved_by": user_id,
            "approved_at": _iso(),
            "writes_performed": False,
            "snapshot_digest": snapshot_digest,
            "execution_quality": checked["execution_quality"],
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
                result = await _execute_approved_recommendation(
                    db,
                    user_id,
                    snapshot_id=payload.snapshot_id,
                    recommendation_id=recommendation_id,
                    expected_digest=snapshot_digest,
                    idempotency_key=execution_id,
                )
                result_status = str(result.get("status") or "")
                final_status = (
                    result_status
                    if result_status in {"completed", "provider_state_uncertain", "verification_required"}
                    else "verification_required"
                )
                await db[EXECUTION_COLLECTION].update_one(
                    {"execution_id": execution_id},
                    {"$set": {
                        "status": final_status,
                        "finished_at": _iso(),
                        "writes_performed": result.get("provider_write_reached") is True,
                        "provider_write_outcome_known": result.get("provider_write_reached") is not None,
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
                if isinstance(exc, _execution_quality.ExecutionQualityBlocked):
                    detail = _execution_quality_http_exception(exc).detail
                else:
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
