"""Adaptive AI judgment over bounded Snapchat and commerce evidence.

The model is a decision layer, not an evidence source.  It may choose which
measured signals matter for the current campaign, but it cannot invent facts,
write to Snapchat, or turn user suggestions into verified evidence.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from typing import Any, Callable

from fastapi import HTTPException
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from ai_provider_status import openai_runtime_status

SOURCE_MODE = "snapchat_adaptive_ai_judgment_v1"
MAX_CONTEXT_BYTES = 180_000
ADAPTIVE_REVIEW_GUARD_COLLECTION = "mezan_snapchat_adaptive_review_guard_v1"
ADAPTIVE_REVIEW_COOLDOWN = timedelta(seconds=30)


class AdaptiveExpectedMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str
    metric: str
    direction: str | None = None
    expected_min: float | None = None
    expected_max: float | None = None
    value_basis: str = "actual"
    basis: str


class AdaptiveDecisionJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommended_action: str
    entity_type: str
    entity_id: str
    confidence: float = Field(ge=0, le=1)
    reason_ar: str
    primary_objective: str
    expected_outcome: list[AdaptiveExpectedMetric] = Field(default_factory=list)
    evidence_used: list[str] = Field(default_factory=list)
    evidence_not_used: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    recent_improvement_treatment: str
    safe_to_prepare_proposal: bool


class AdaptiveDecisionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judgments: list[AdaptiveDecisionJudgment] = Field(default_factory=list, max_length=5)


JUDGMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "recommended_action": {
            "type": "string",
            "enum": [
                "observe",
                "investigate",
                "increase_budget",
                "decrease_budget",
                "pause",
                "activate",
            ],
        },
        "entity_type": {"type": "string", "enum": ["campaign", "ad_squad", "ad"]},
        "entity_id": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason_ar": {"type": "string"},
        "primary_objective": {
            "type": "string",
            "enum": ["grow_sales_while_protecting_contribution_profit"],
        },
        "expected_outcome": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["campaign", "account", "store"],
                    },
                    "metric": {
                        "type": "string",
                        "enum": [
                            "orders",
                            "sales_sar",
                            "contribution_profit_sar",
                            "spend_sar",
                            "roas",
                            "cpa_sar",
                        ],
                    },
                    "direction": {
                        "type": ["string", "null"],
                        "enum": ["increase", "decrease", "stable", None],
                    },
                    "expected_min": {"type": ["number", "null"]},
                    "expected_max": {"type": ["number", "null"]},
                    "value_basis": {
                        "type": "string",
                        "enum": ["actual", "absolute_delta", "delta_pct"],
                    },
                    "basis": {"type": "string"},
                },
                "required": [
                    "scope",
                    "metric",
                    "direction",
                    "expected_min",
                    "expected_max",
                    "value_basis",
                    "basis",
                ],
            },
        },
        "evidence_used": {"type": "array", "items": {"type": "string"}},
        "evidence_not_used": {"type": "array", "items": {"type": "string"}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
        "recent_improvement_treatment": {"type": "string"},
        "safe_to_prepare_proposal": {"type": "boolean"},
    },
    "required": [
        "recommended_action",
        "entity_type",
        "entity_id",
        "confidence",
        "reason_ar",
        "primary_objective",
        "expected_outcome",
        "evidence_used",
        "evidence_not_used",
        "uncertainties",
        "recent_improvement_treatment",
        "safe_to_prepare_proposal",
    ],
}

BATCH_JUDGMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "judgments": {
            "type": "array",
            "maxItems": 5,
            "items": JUDGMENT_SCHEMA,
        }
    },
    "required": ["judgments"],
}


async def acquire_adaptive_review_slot(db: Any, user_id: str) -> None:
    """Distributed tenant cooldown so one click cannot fan out AI cost."""
    tenant = str(user_id or "").strip()
    if not tenant:
        raise HTTPException(status_code=422, detail={"code": "invalid_user_id"})
    collection = db[ADAPTIVE_REVIEW_GUARD_COLLECTION]
    await collection.create_index(
        [("user_id", 1)],
        unique=True,
        name="snapchat_adaptive_review_tenant_unique",
    )
    current = datetime.now(timezone.utc)
    existing = await collection.find_one({"user_id": tenant}, {"_id": 0})
    previous = (existing or {}).get("requested_at")
    try:
        previous_time = datetime.fromisoformat(str(previous or "").replace("Z", "+00:00"))
        if previous_time.tzinfo is None:
            previous_time = previous_time.replace(tzinfo=timezone.utc)
    except ValueError:
        previous_time = None
    if previous_time and current - previous_time.astimezone(timezone.utc) < ADAPTIVE_REVIEW_COOLDOWN:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "adaptive_review_rate_limited",
                "retry_after_seconds": int(ADAPTIVE_REVIEW_COOLDOWN.total_seconds()),
            },
        )
    selector = {"user_id": tenant}
    if existing:
        selector["requested_at"] = previous
    try:
        result = await collection.update_one(
            selector,
            {
                "$set": {"requested_at": current.isoformat()},
                "$setOnInsert": {"user_id": tenant},
            },
            upsert=not bool(existing),
        )
    except Exception as exc:
        if type(exc).__name__ == "DuplicateKeyError":
            raise HTTPException(
                status_code=429,
                detail={"code": "adaptive_review_rate_limited"},
            ) from exc
        raise
    if existing and not getattr(result, "matched_count", 1):
        raise HTTPException(
            status_code=429,
            detail={"code": "adaptive_review_rate_limited"},
        )


def _client() -> AsyncOpenAI:
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise HTTPException(status_code=503, detail={"code": "openai_not_configured"})
    return AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=1, timeout=35)


def _bounded_context(evidence: dict[str, Any]) -> str:
    encoded = json.dumps(
        evidence, ensure_ascii=False, default=str, separators=(",", ":")
    )
    if len(encoded.encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise HTTPException(
            status_code=413, detail={"code": "adaptive_evidence_too_large"}
        )
    return encoded


async def judge_adaptive_snapchat_decision(
    evidence: dict[str, Any],
    *,
    client_factory: Callable[[], Any] = _client,
) -> dict[str, Any]:
    """Return a structured recommendation; never prepare or execute a write."""
    client = client_factory()
    try:
        response = await asyncio.wait_for(
            client.responses.create(
                model=os.environ.get("MEZAN_OPENAI_MODEL", "gpt-5-mini"),
                instructions=(
                    "أنت مدير إعلانات ميزان لمتجر أماسي. الهدف الحاكم هو زيادة "
                    "المبيعات مع حماية المكسب المساهم ثم توسيع المبيعات. لا تستخدم "
                    "قاعدة ثابتة لمبلغ صرف أو عدد أيام أو ROAS أو CPA. اختر وزن كل "
                    "دليل حسب الحملة وعمرها وتاريخها واكتمال البيانات. نتائج سلة "
                    "والمبيعات والمكسب هي الأساس؛ بيانات المنصة والمخزون والسياق "
                    "المتحقق مساندة. كلام المستخدم والمواسم والرواتب غير المتحققة "
                    "اقتراحات فقط ولا تصبح حقائق. إذا تجاهلت تحسنًا حديثًا فاشرح "
                    "لماذا. لا تدّع السببية. لا تسمح باقتراح كتابة عند نقص البيانات، "
                    "ولا تخترع مقدار ميزانية؛ عند غياب مقدار مدعوم اختر investigate."
                ),
                input=_bounded_context(evidence),
                max_output_tokens=1800,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "mezan_adaptive_ad_decision",
                        "strict": True,
                        "schema": JUDGMENT_SCHEMA,
                    }
                },
            ),
            timeout=40,
        )
        result = AdaptiveDecisionJudgment.model_validate_json(response.output_text)
        payload = result.model_dump(mode="json")
        entity_evidence = evidence.get("entity_evidence") or {}
        expected_entity_type = str(entity_evidence.get("entity_type") or "")
        expected_entity_id = str(entity_evidence.get("entity_id") or "")
        if expected_entity_type:
            payload["entity_type"] = expected_entity_type
        if expected_entity_id:
            payload["entity_id"] = expected_entity_id
        # The model recommends; it never authorizes.  Safety/readiness is a
        # deterministic server decision made only after source completeness,
        # entity identity, inventory and current-provider-state checks.
        payload["model_suggested_safe_to_prepare_proposal"] = bool(
            payload.pop("safe_to_prepare_proposal", False)
        )
        payload["safe_to_prepare_proposal"] = False
        return {
            "source_mode": SOURCE_MODE,
            "objective": "grow_sales_while_protecting_contribution_profit",
            "judgment": payload,
            "provider_write_reached": False,
            "proposal_created": False,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "adaptive_ai_judgment_failed",
                "error_type": type(exc).__name__,
            },
        ) from exc
    finally:
        if client_factory is _client:
            try:
                await client.close()
            except Exception:
                pass


async def judge_adaptive_snapchat_decisions(
    evidence_items: list[dict[str, Any]],
    *,
    client_factory: Callable[[], Any] = _client,
) -> list[dict[str, Any]]:
    """Judge up to five entities in one bounded Responses API request."""
    bounded = [item for item in evidence_items[:5] if isinstance(item, dict)]
    if not bounded:
        return []
    client = client_factory()
    try:
        response = await asyncio.wait_for(
            client.responses.create(
                model=os.environ.get("MEZAN_OPENAI_MODEL", "gpt-5-mini"),
                instructions=(
                    "أنت مدير إعلانات ميزان لمتجر أماسي. قيّم كل كيان وارد مرة "
                    "واحدة وأعد قائمة judgments. الهدف الحاكم زيادة المبيعات مع "
                    "حماية المكسب المساهم ثم التوسع. لا تستخدم قواعد ثابتة للصرف "
                    "أو الأيام أو ROAS أو CPA. نتائج سلة والمبيعات والمكسب هي "
                    "الأساس؛ المنصة والمخزون والسياق المتحقق مساندة. اقتراحات "
                    "المستخدم ليست حقائق. لا تدّع السببية ولا تخترع ميزانية. "
                    "لا تسمح بأي كتابة؛ أنت تقدم توصية إشرافية فقط. حافظ على "
                    "entity_type وentity_id كما وردا ولا تضف كيانًا جديدًا."
                ),
                input=_bounded_context({"entities": bounded}),
                max_output_tokens=4200,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "mezan_adaptive_ad_decision_batch",
                        "strict": True,
                        "schema": BATCH_JUDGMENT_SCHEMA,
                    }
                },
            ),
            timeout=45,
        )
        parsed = AdaptiveDecisionBatch.model_validate_json(response.output_text)
        requested = {
            (
                str((item.get("entity_evidence") or {}).get("entity_type") or ""),
                str((item.get("entity_evidence") or {}).get("entity_id") or ""),
            ): item
            for item in bounded
        }
        output: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for judgment in parsed.judgments:
            payload = judgment.model_dump(mode="json")
            key = (payload["entity_type"], payload["entity_id"])
            if key not in requested or key in seen:
                continue
            seen.add(key)
            payload["model_suggested_safe_to_prepare_proposal"] = bool(
                payload.pop("safe_to_prepare_proposal", False)
            )
            payload["safe_to_prepare_proposal"] = False
            output.append(
                {
                    "source_mode": SOURCE_MODE,
                    "objective": "grow_sales_while_protecting_contribution_profit",
                    "judgment": payload,
                    "provider_write_reached": False,
                    "proposal_created": False,
                }
            )
        return output
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "adaptive_ai_judgment_failed",
                "error_type": type(exc).__name__,
            },
        ) from exc
    finally:
        if client_factory is _client:
            try:
                await client.close()
            except Exception:
                pass


def adaptive_ai_status() -> dict[str, Any]:
    status = openai_runtime_status()
    return {
        "configured": status.get("connected") is True,
        "model": (status.get("analysis") or {}).get("model"),
        "mode": "adaptive_recommendation_only",
    }


__all__ = [
    "AdaptiveDecisionJudgment",
    "acquire_adaptive_review_slot",
    "adaptive_ai_status",
    "judge_adaptive_snapchat_decision",
    "judge_adaptive_snapchat_decisions",
]
