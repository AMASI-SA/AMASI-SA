"""Public facade for Campaign AI source-of-truth policy.

The historical implementation remains in ``campaign_ai_monitor_legacy`` so
existing route/scheduler/execution contracts stay stable. The marketing
source/policy lives in ``campaign_ai_policy_v2``. The execution-alignment layer
adds the real provider write capabilities to OpenAI evidence and rejects
impossible action/target pairs without inventing a parent target. A bounded
OpenAI repair pass may then reconsider the same evidence once when the first
model response targeted an impossible provider write.
"""
from __future__ import annotations

from typing import Any

import campaign_ai_execution_alignment as _alignment
import campaign_ai_execution_retry as _execution_retry
import campaign_ai_monitor_legacy as _legacy
import campaign_ai_policy_v2 as _policy

# Keep the established monkeypatch/test surface for the OpenAI client. The
# policy runtime uses the legacy module's client reference; the public wrapper
# synchronizes an explicitly replaced client before a direct _ask_openai call.
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

# Explicit policy exports used by focused tests/diagnostics.
_account_range = _policy._account_range
_page_aligned_profitability = _policy._page_aligned_profitability
_snapchat_campaign_entities = _policy._snapchat_campaign_entities
_snapchat_child_entities = _policy._snapchat_child_entities
_experiment_outcomes_context = _policy._experiment_outcomes_context
_recommendation_explanation = _policy._recommendation_explanation
execution_capabilities = _alignment.execution_capabilities

# Install the aligned OpenAI boundary first, then wrap it with one bounded
# OpenAI-owned correction pass. Mezan still never selects or promotes a target.
_aligned_ask_openai = _alignment.build_aligned_ask_openai(_legacy, _policy)
_repairing_ask_openai = _execution_retry.build_repairing_ask_openai(
    _aligned_ask_openai,
    _legacy,
    _policy,
    _alignment,
)
_policy._ask_openai = _repairing_ask_openai
_legacy._ask_openai = _repairing_ask_openai
run_campaign_ai_monitor = _policy.run_campaign_ai_monitor


async def _ask_openai(*args: Any, **kwargs: Any):
    _legacy.AsyncOpenAI = AsyncOpenAI
    return await _repairing_ask_openai(*args, **kwargs)


# Existing route/scheduler helpers remain delegated to the legacy module; the
# policy module patches their runtime globals during import.
def __getattr__(name: str) -> Any:
    if hasattr(_policy, name):
        return getattr(_policy, name)
    return getattr(_legacy, name)


__all__ = list(dict.fromkeys([
    *getattr(_legacy, "__all__", []),
    "execution_capabilities",
    "run_campaign_ai_monitor",
]))
