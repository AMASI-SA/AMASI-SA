"""Public read guard for Campaign AI recommendations.

Legacy Campaign AI code is kept for route/execution compatibility, but its
historical deterministic fallback is no longer a valid recommendation source.
This guard is registered before the legacy read routes so an old or overlapping
worker can never surface ``mezan_fallback`` recommendations to the dashboard.

If a legacy fallback snapshot races with a still-valid OpenAI snapshot, the
recent OpenAI snapshot remains visible for its normal five-hour validity window
instead of disappearing from the dashboard.

The guard also removes historical OpenAI budget actions aimed directly at an ad.
Ads do not own a daily budget in the current Snapchat/Meta execution contracts;
showing such a card without an approval button is misleading. New analysis is
prevented from creating those combinations by ``campaign_ai_execution_alignment``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, Query


RECOMMENDATION_COLLECTION = "mezan_campaign_ai_recommendations_v1"
BLOCKED_RECOMMENDATION_SOURCES = {"mezan_fallback"}
VALID_OPENAI_SOURCE = "openai"
OPENAI_SNAPSHOT_VALIDITY = timedelta(hours=5)
BUDGET_ACTIONS = {"reduce", "scale"}


def _text(value: Any, *, limit: int = 120) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = _text(value, limit=80)
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fallback_present(document: dict[str, Any] | None) -> bool:
    if not document:
        return False
    source = _text(document.get("recommendation_source"), limit=80)
    item_sources = {
        _text(item.get("recommendation_source"), limit=80)
        for item in (document.get("recommendations") or [])
        if isinstance(item, dict)
    }
    return (
        source in BLOCKED_RECOMMENDATION_SOURCES
        or bool(item_sources & BLOCKED_RECOMMENDATION_SOURCES)
    )


def _recent_valid_openai(
    document: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> bool:
    if not document or _fallback_present(document):
        return False
    if _text(document.get("recommendation_source"), limit=80) != VALID_OPENAI_SOURCE:
        return False
    generated_at = _parse_datetime(document.get("generated_at"))
    if generated_at is None:
        return False
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = current - generated_at
    return timedelta(0) <= age <= OPENAI_SNAPSHOT_VALIDITY


def _strip_unexecutable_budget_recommendations(
    public: dict[str, Any],
) -> dict[str, Any]:
    """Remove stale ad-level reduce/scale cards; never retarget them to a parent."""
    recommendations = public.get("recommendations") or []
    kept: list[Any] = []
    removed = 0
    for item in recommendations:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        if (
            _text(item.get("entity_level"), limit=30) == "ad"
            and _text(item.get("action"), limit=30) in BUDGET_ACTIONS
        ):
            removed += 1
            continue
        kept.append(item)

    if not removed:
        return public

    sanitized = dict(public)
    sanitized["recommendations"] = kept
    sanitized["execution_alignment_suppressed"] = removed
    sanitized["summary_adjusted_after_execution_filter"] = True
    limitations = list(sanitized.get("limitations") or [])
    if "non_executable_ad_budget_recommendation_suppressed" not in limitations:
        limitations.append("non_executable_ad_budget_recommendation_suppressed")
    sanitized["limitations"] = limitations

    # The old summary can mention cards that were just removed. Never show a
    # narrative that contradicts the visible/executable recommendation set.
    if kept:
        sanitized["summary"] = (
            f"تم استبعاد {removed} توصية قديمة استهدفت تعديل ميزانية إعلان لا يملك "
            "ميزانية مستقلة. التوصيات الظاهرة فقط هي القرارات المتوافقة مع مستوى "
            "التنفيذ الفعلي، وسيعيد OpenAI مراجعة الحملات والمجموعات في الدورة التالية."
        )
    else:
        sanitized["summary"] = (
            f"لا توجد الآن توصية قابلة للتنفيذ مباشرة. تم استبعاد {removed} توصية "
            "قديمة استهدفت مستوى إعلان لا يملك ميزانية مستقلة، وسيعيد OpenAI مراجعة "
            "الحملات والمجموعات والإعلانات دون تنفيذ أي تغيير تلقائي."
        )
    return sanitized


def _public_document(document: dict[str, Any] | None) -> dict[str, Any]:
    if not document:
        return {
            "available": False,
            "mode": "recommend_then_approve",
            "writes_performed": False,
            "summary": "سيظهر أول تحليل بعد اكتمال التشغيل الدوري.",
            "recommendations": [],
            "next_run_at": None,
        }

    public = {
        key: value
        for key, value in document.items()
        if key not in {"_id", "user_id", "execution_targets"}
    }
    if not _fallback_present(public):
        return {"available": True, **_strip_unexecutable_budget_recommendations(public)}

    limitations = list(public.get("limitations") or [])
    if "legacy_mezan_fallback_suppressed" not in limitations:
        limitations.append("legacy_mezan_fallback_suppressed")

    # Never expose deterministic Mezan decisions as if they came from AI.
    public.update({
        "available": True,
        "summary": "تعذر تشغيل تحليل الذكاء، لا توجد توصيات جديدة. سيعاد التحليل تلقائيًا.",
        "recommendations": [],
        "recommendation_source": "openai_unavailable",
        "decision_authority": "openai_unavailable",
        "limitations": limitations,
        "writes_performed": False,
        "legacy_fallback_suppressed": True,
    })
    return public


def _public_recent_openai_after_fallback(
    openai_document: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return a still-valid OpenAI snapshot with explicit suppression metadata."""
    if not _recent_valid_openai(openai_document, now=now):
        return None
    public = _public_document(openai_document)
    public["legacy_fallback_suppressed"] = True
    public["serving_previous_valid_openai_snapshot"] = True
    limitations = list(public.get("limitations") or [])
    if "newer_legacy_mezan_fallback_suppressed" not in limitations:
        limitations.append("newer_legacy_mezan_fallback_suppressed")
    public["limitations"] = limitations
    return public


def attach_campaign_ai_public_guard(
    router: APIRouter,
    db: Any,
    current_user: Callable,
) -> None:
    """Register guarded read routes before the legacy compatibility routes."""

    @router.get("/ai-monitor/latest", include_in_schema=False)
    async def guarded_latest_campaign_recommendations(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = _text(user.get("id") or user.get("_id"))
        collection = db[RECOMMENDATION_COLLECTION]
        latest = await collection.find_one(
            {"user_id": user_id},
            {"execution_targets": 0},
            sort=[("generated_at", -1)],
        )
        if not _fallback_present(latest):
            return _public_document(latest)

        # A racing legacy writer must not make a valid OpenAI recommendation
        # disappear. Reuse only a recent OpenAI snapshot inside the exact same
        # five-hour recommendation validity window used by execution approval.
        recent_openai = await collection.find_one(
            {
                "user_id": user_id,
                "recommendation_source": VALID_OPENAI_SOURCE,
            },
            {"execution_targets": 0},
            sort=[("generated_at", -1)],
        )
        preserved = _public_recent_openai_after_fallback(recent_openai)
        if preserved is not None:
            return preserved
        return _public_document(latest)

    @router.get("/ai-monitor/history", include_in_schema=False)
    async def guarded_campaign_recommendation_history(
        limit: int = Query(default=12, ge=1, le=48),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = _text(user.get("id") or user.get("_id"))
        cursor = (
            db[RECOMMENDATION_COLLECTION]
            .find(
                {
                    "user_id": user_id,
                    "recommendation_source": {"$ne": "mezan_fallback"},
                },
                {"_id": 0, "user_id": 0, "execution_targets": 0},
            )
            .sort("generated_at", -1)
            .limit(limit)
        )
        documents = await cursor.to_list(length=limit)
        items = []
        for document in documents:
            public = _public_document(document)
            public.pop("available", None)
            items.append(public)
        return {
            "items": items,
            "mode": "recommend_then_approve",
        }


__all__ = [
    "attach_campaign_ai_public_guard",
    "_fallback_present",
    "_public_document",
    "_public_recent_openai_after_fallback",
    "_recent_valid_openai",
    "_strip_unexecutable_budget_recommendations",
]
