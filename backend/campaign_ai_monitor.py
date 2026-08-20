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

# Production and live-eval must use one reasoning contract. The historical V3
# module still exports prompt constants for compatibility, so bind them to the
# lightweight shared module before building the runtime OpenAI callable.
_decision_v3.FIRST_PASS_INSTRUCTIONS = _V3_FIRST_PASS_INSTRUCTIONS
_decision_v3.SECOND_PASS_INSTRUCTIONS = _V3_SECOND_PASS_INSTRUCTIONS
# Restocking is an operational recommendation, never an Ads API write.
_decision_v3.OPERATIONAL_ACTIONS.add("RESTOCK_PRODUCT")

# Explicit policy exports used by focused tests/diagnostics.
_account_range = _policy._account_range
_page_aligned_profitability = _policy._page_aligned_profitability
_snapchat_campaign_entities = _policy._snapchat_campaign_entities
_snapchat_child_entities = _policy._snapchat_child_entities
_experiment_outcomes_context = _policy._experiment_outcomes_context
execution_capabilities = _alignment.execution_capabilities

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
    """Use V3 in a tenant monitor task; preserve old direct-test contracts only.

    ContextVar is task-local, so concurrent tenant monitor tasks cannot route one
    another through the wrong decision engine. A missing context means this is a
    direct diagnostic/unit-test call rather than a Production monitor run.
    """
    try:
        _get_v3_context()
    except RuntimeError:
        # Do not overwrite _legacy.AsyncOpenAI here. Established direct tests
        # intentionally inject a fake client into the legacy module itself.
        return await _legacy_test_repairing_ask(*args, **kwargs)
    # Production monitor wrapper installs the public client before setting the
    # task-local context, so this branch is always the V3 runtime path.
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
    try:
        return await _base_run_campaign_ai_monitor(
            db,
            user_id,
            *args,
            **kwargs,
        )
    finally:
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
