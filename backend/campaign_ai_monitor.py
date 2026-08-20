"""Public facade for Campaign AI source-of-truth and Decision Intelligence V3.

The established V2 pipeline remains responsible for provider evidence,
profitability, persistence, scheduling, snapshot lifecycle and approval/write
safety. Decision Intelligence V3 replaces only the runtime OpenAI reasoning
boundary and adds task-local access to the tenant database for deeper evidence.
"""
from __future__ import annotations

from typing import Any

import campaign_ai_context_budget_v3 as _context_budget
import campaign_ai_decision_v3 as _decision_v3
import campaign_ai_evidence_runtime_enrichment_v3 as _evidence_enrichment
import campaign_ai_execution_alignment as _alignment
import campaign_ai_execution_retry as _execution_retry
import campaign_ai_monitor_legacy as _legacy
import campaign_ai_monthly_profit_goal_v1 as _monthly_goal
import campaign_ai_policy_v2 as _policy
from campaign_ai_decision_prompts_v3 import (
    FIRST_PASS_INSTRUCTIONS as _V3_FIRST_PASS_INSTRUCTIONS,
    SECOND_PASS_INSTRUCTIONS as _V3_SECOND_PASS_INSTRUCTIONS,
)
from campaign_ai_runtime_context_v3 import (
    get_runtime_context as _get_v3_context,
    reset_runtime_context as _reset_v3_context,
    set_runtime_context as _set_v3_context,
)

# Keep the established monkeypatch/test surface for the OpenAI client.
AsyncOpenAI = _legacy.AsyncOpenAI

RecommendationItem = _policy.RecommendationItem
RecommendationOutput = _policy.RecommendationOutput
RecommendationApprovalInput = _policy.RecommendationApprovalInput
CampaignOpenAIError = _policy.CampaignOpenAIError
DEFAULT_INITIAL_DELAY_SECONDS = _policy.DEFAULT_INITIAL_DELAY_SECONDS
DEFAULT_INTERVAL_SECONDS = _policy.DEFAULT_INTERVAL_SECONDS
MONITOR_TIMEOUT_SECONDS = _policy.MONITOR_TIMEOUT_SECONDS
MAX_ENTITY_ROWS = _policy.MAX_ENTITY_ROWS
MAX_AI_CANDIDATES = _policy.MAX_AI_CANDIDATES
MAX_RECOMMENDATIONS = _policy.MAX_RECOMMENDATIONS
RECOMMENDATION_COLLECTION = _policy.RECOMMENDATION_COLLECTION
RUN_COLLECTION = _policy.RUN_COLLECTION
EXECUTION_COLLECTION = _policy.EXECUTION_COLLECTION
TARGET_CPA_SAR = _policy.TARGET_CPA_SAR
TARGET_ROAS = _policy.TARGET_ROAS
SNAPCHAT_AI_ACTION_REPORT_TIME = _policy.SNAPCHAT_AI_ACTION_REPORT_TIME
SNAPCHAT_AI_PLATFORM_SOURCE = _policy.SNAPCHAT_AI_PLATFORM_SOURCE
SNAPCHAT_AI_SALLA_SOURCE = _policy.SNAPCHAT_AI_SALLA_SOURCE
RESULT_SOURCE_PLATFORM = _policy.RESULT_SOURCE_PLATFORM
RESULT_SOURCE_SALLA = _policy.RESULT_SOURCE_SALLA
OPENAI_TIMEOUT_SECONDS = _legacy.OPENAI_TIMEOUT_SECONDS
OPENAI_MAX_OUTPUT_TOKENS = _legacy.OPENAI_MAX_OUTPUT_TOKENS

# Owner-facing contract: the model may reason with technical evidence internally,
# but the merchant should see a short Arabic management decision.  The monthly
# net-profit floor is the primary business objective; ad-platform metrics are
# means to that objective, never the objective themselves.
_MERCHANT_GOAL_INSTRUCTIONS = """
عقد المدير التجاري للمخرجات:
- الهدف الأعلى هو monthly_profit_goal داخل overall_store_profit_context: تحقيق الحد الأدنى لصافي
  ربح الشهر وحماية مساره قبل أي توسع اختياري. ROAS وCPA والصرف والمبيعات أدوات تشخيص وليست الهدف.
- إذا كان status=behind_target فابحث عن أفضل خطة واقعية لسد فجوة صافي الربح: حماية الهدر، توسيع
  الربح المثبت، إصلاح المنتج/الصفحة/المخزون/الكرياتيف، أو الانتظار عندما تكون زيادة الصرف مخاطرة.
- إذا كان status=on_track فاحمِ مسار الحد الأدنى ولا تخاطر به لأجل نمو المبيعات فقط.
- إذا كان status=minimum_target_covered يمكن البحث عن ربح إضافي فوق الحد الأدنى مع بقاء الحماية.
- لا تدّع أن هدف الشهر قابل للتحقيق إذا الأدلة لا تدعم ذلك. قل بوضوح عندما يكون المسار الحالي غير كافٍ.

أسلوب الشرح للمستخدم:
- جميع النصوص التي يراها المستخدم (summary/title/diagnosis/why/evidence/risks/observation_plan/
  success_criteria والشرح) يجب أن تكون عربية واضحة لشخص غير متخصص بالإعلانات.
- لا تستخدم كلمات إنجليزية غير ضرورية مثل Funnel, Baseline, Creative, Inventory, Unknown, Normal
  Variance, Hook, CTA, Attribution داخل النص البشري. ترجم المعنى: مسار الشراء، خط الأساس، المادة
  الإعلانية، المخزون، غير معروف، تذبذب طبيعي، بداية الفيديو، دعوة لاتخاذ إجراء، إسناد التحويل.
- ابدأ كل توصية بالقرار العملي الآن ثم السبب بجملة قصيرة، ثم أهم الأرقام فقط. لا تكرر نفس الفكرة.
- عندما تستخدم اختصارًا مشهورًا مثل ROAS أو CPA، اذكر المعنى العربي أولًا، مثال: العائد على الإنفاق
  الإعلاني (ROAS). لا تجعل المستخدم يحتاج فهم المصطلح لاتخاذ القرار.
- التفاصيل التقنية مكانها التحليل المتقدم؛ لا تجعلها تحجب القرار الأساسي.
"""

# Production and live-eval must use one reasoning contract. The historical V3
# module still exports prompt constants for compatibility, so bind them to the
# lightweight shared module before building the runtime OpenAI callable.
_decision_v3.FIRST_PASS_INSTRUCTIONS = (
    _MERCHANT_GOAL_INSTRUCTIONS + "\n" + _V3_FIRST_PASS_INSTRUCTIONS
)
_decision_v3.SECOND_PASS_INSTRUCTIONS = (
    _MERCHANT_GOAL_INSTRUCTIONS + "\n" + _V3_SECOND_PASS_INSTRUCTIONS
)
# Restocking is an operational recommendation, never an Ads API write.
_decision_v3.OPERATIONAL_ACTIONS.add("RESTOCK_PRODUCT")

# Explicit policy exports used by focused tests/diagnostics.
_account_range = _policy._account_range
_page_aligned_profitability = _policy._page_aligned_profitability
_snapchat_campaign_entities = _policy._snapchat_campaign_entities
_snapchat_child_entities = _policy._snapchat_child_entities
_experiment_outcomes_context = _policy._experiment_outcomes_context
execution_capabilities = _alignment.execution_capabilities

# Inject owner objective + true month-to-date dashboard net profit into the same
# business-profit context that reaches OpenAI.  ContextVar keeps this tenant/task
# local when multiple stores are analyzed concurrently.
_base_business_profit_context = _legacy._business_profit_context
_goal_aware_business_profit_context = _monthly_goal.wrap_business_profit_context(
    _base_business_profit_context,
    _get_v3_context,
)
_legacy._business_profit_context = _goal_aware_business_profit_context

# Preserve the existing Salla/profit explanation enrichment, then append the
# rich V3 business diagnosis captured on the exact candidate row. V2 later
# computes approval_available from the fail-closed legacy action projection.
_base_recommendation_explanation = _policy._recommendation_explanation


def _recommendation_explanation(item: Any, row: dict[str, Any]) -> dict[str, Any]:
    brief = dict(_base_recommendation_explanation(item, row) or {})
    rich = row.get("_decision_v3") if isinstance(row, dict) else None
    if isinstance(rich, dict):
        brief.update(rich)
    return brief


_policy._recommendation_explanation = _recommendation_explanation
_legacy._recommendation_explanation = _recommendation_explanation

# Keep the former aligned/repairing call for established direct unit tests and
# diagnostics that intentionally invoke the OpenAI boundary without a tenant
# runtime context. It is never used by the Production monitor because the
# monitor wrapper below always installs ContextVar(db,user_id) first.
_legacy_test_aligned_ask = _alignment.build_aligned_ask_openai(_legacy, _policy)
_legacy_test_repairing_ask = _execution_retry.build_repairing_ask_openai(
    _legacy_test_aligned_ask,
    _legacy,
    _policy,
    _alignment,
)

# Enrich the V3 evidence pack with field-level product change chronology. The
# wrapper is installed once at module import and still delegates to the same
# base evidence builder; it does not alter scheduler/source/snapshot contracts.
_decision_evidence_builder = _evidence_enrichment.wrap_evidence_builder(
    _decision_v3.build_decision_evidence_pack_v3,
)
_decision_v3.build_decision_evidence_pack_v3 = _decision_evidence_builder

# Bound the model representation after all evidence enrichment and before the
# Responses API call. The original evidence object remains available to the
# normalizer/execution guards; only the OpenAI view is compacted. A genuine
# context-length rejection receives one stronger bounded retry.
_base_v3_structured_response = _decision_v3._structured_response
_decision_v3._structured_response = _context_budget.wrap_structured_response(
    _base_v3_structured_response,
)

# Runtime authority: V3 owns diagnosis + marketing judgment, including its own
# mandatory second pass for budget-owner coverage and counterfactual review.
_v3_ask_openai = _decision_v3.build_decision_v3_ask_openai(
    _legacy,
    _policy,
    _alignment,
)


async def _runtime_ask_dispatch(*args: Any, **kwargs: Any):
    """Use V3 in a tenant monitor task; preserve old direct-test contracts only."""
    try:
        _get_v3_context()
    except RuntimeError:
        # Do not overwrite _legacy.AsyncOpenAI here. Established direct tests
        # intentionally inject a fake client into the legacy module itself.
        return await _legacy_test_repairing_ask(*args, **kwargs)
    return await _v3_ask_openai(*args, **kwargs)


_policy._ask_openai = _runtime_ask_dispatch
_legacy._ask_openai = _runtime_ask_dispatch

# The V2 monitor signature remains unchanged. ContextVar supplies db/user_id to
# V3 without process-global tenant state or changes to worker/cadence/snapshot.
_base_run_campaign_ai_monitor = _policy.run_campaign_ai_monitor


async def run_campaign_ai_monitor(
    db: Any,
    user_id: str,
    *args: Any,
    **kwargs: Any,
):
    _legacy.AsyncOpenAI = AsyncOpenAI
    token = _set_v3_context(db, user_id)
    _monthly_goal.clear_goal_context()
    try:
        result = await _base_run_campaign_ai_monitor(
            db,
            user_id,
            *args,
            **kwargs,
        )
        goal_context = _monthly_goal.current_goal_context()
        snapshot_id = result.get("snapshot_id") if isinstance(result, dict) else None
        if goal_context and snapshot_id:
            await db[RECOMMENDATION_COLLECTION].update_one(
                {"user_id": user_id, "snapshot_id": snapshot_id},
                {"$set": {"monthly_profit_goal": goal_context}},
            )
            result = {**result, "monthly_profit_goal": goal_context}
        return result
    finally:
        _monthly_goal.clear_goal_context()
        _reset_v3_context(token)


# run_all_campaign_ai_monitors lives in the compatibility module and resolves
# this global at runtime, so the established worker automatically enters V3.
_policy.run_campaign_ai_monitor = run_campaign_ai_monitor
_legacy.run_campaign_ai_monitor = run_campaign_ai_monitor


async def _ask_openai(*args: Any, **kwargs: Any):
    """Established direct-test helper; Production monitor uses the dispatcher."""
    _legacy.AsyncOpenAI = AsyncOpenAI
    return await _legacy_test_repairing_ask(*args, **kwargs)


async def _ask_openai_v3(*args: Any, **kwargs: Any):
    """Direct V3 diagnostic hook; caller must establish runtime context."""
    _legacy.AsyncOpenAI = AsyncOpenAI
    return await _v3_ask_openai(*args, **kwargs)


# Existing route/scheduler helpers remain delegated to the legacy module.
def __getattr__(name: str) -> Any:
    if hasattr(_policy, name):
        return getattr(_policy, name)
    return getattr(_legacy, name)


__all__ = list(dict.fromkeys([
    *getattr(_legacy, "__all__", []),
    "execution_capabilities",
    "run_campaign_ai_monitor",
]))
