"""Align OpenAI campaign recommendations with provider execution capabilities.

Marketing judgment remains with OpenAI. This module only exposes the provider
write boundary as evidence and rejects impossible action/target combinations.
It never promotes an ad recommendation to a parent entity on its own.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any


BUDGET_ACTIONS = {"reduce", "scale"}
WRITE_ACTIONS = {"pause", "reduce", "scale"}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def execution_capabilities(row: dict[str, Any]) -> dict[str, Any]:
    """Describe what can be written directly to this exact provider entity."""
    level = str(row.get("entity_level") or "")
    active = bool(row.get("active"))
    budget = _number(row.get("current_daily_budget_native"))
    direct_budget_available = bool(
        active and level in {"campaign", "ad_group"} and budget is not None and budget > 0
    )

    allowed = ["monitor", "maintain"]
    if active:
        allowed.insert(0, "pause")
    if direct_budget_available:
        allowed[1:1] = ["reduce", "scale"]

    if level == "ad":
        budget_owner_level = "ad_group"
        budget_owner_id = row.get("ad_group_id") or row.get("parent_id")
        budget_owner_name = row.get("ad_group_name") or row.get("parent_name")
    else:
        budget_owner_level = level or None
        budget_owner_id = row.get("entity_id")
        budget_owner_name = row.get("entity_name")

    return {
        "allowed_actions": allowed,
        "budget_actions_allowed": direct_budget_available,
        "direct_budget_available": direct_budget_available,
        "budget_owner_level": budget_owner_level,
        "budget_owner_id": budget_owner_id,
        "budget_owner_name": budget_owner_name,
        "automatic_parent_retargeting_allowed": False,
    }


def _candidate_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("provider") or ""),
        str(row.get("entity_level") or ""),
        str(row.get("account_id") or ""),
        str(row.get("entity_id") or ""),
    )


def _safe_candidate(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "provider",
        "entity_level",
        "account_id",
        "account_name",
        "entity_id",
        "entity_name",
        "parent_id",
        "parent_name",
        "status",
        "configured_status",
        "effective_status",
        "status_updated_at",
        "active",
        "campaign_id",
        "campaign_name",
        "campaign_status",
        "ad_group_id",
        "ad_group_name",
        "ad_group_status",
        "campaign_ad_group_count",
        "campaign_ad_count",
        "entity_period_spend_sar",
        "entity_period_purchases",
        "ad_group_period_spend_sar",
        "ad_group_period_purchases",
        "campaign_period_spend_sar",
        "campaign_period_purchases",
        "spend_sar",
        "revenue_sar",
        "purchases",
        "impressions",
        "clicks",
        "roas",
        "cpa_sar",
        "observed_days",
        "spend_per_day_sar",
        "ctr_pct",
        "data_complete",
        "data_quality",
        "account_benchmark",
        "current_daily_budget_native",
        "provider_result_source",
        "action_report_time",
        "result_source",
        "source_date_from",
        "source_date_to",
        "account_timezone",
        "salla_attribution_applied_to_entity_metrics",
        "salla_campaign_results",
        "campaign_profitability",
        "parent_campaign_salla_results",
        "parent_campaign_profitability",
        "commercial_context_scope",
    )
    return {
        **{key: row.get(key) for key in keys},
        "execution_capabilities": execution_capabilities(row),
    }


def _filter_to_executable_contract(output: Any, candidates: list[dict[str, Any]], policy: Any) -> Any:
    """Drop impossible model actions without changing their target or inventing a parent."""
    candidate_by_key = {_candidate_key(row): row for row in candidates}
    kept = []
    limitations = list(getattr(output, "limitations", None) or [])

    for item in getattr(output, "recommendations", None) or []:
        row = candidate_by_key.get((
            str(item.provider),
            str(item.entity_level),
            str(item.account_id or ""),
            str(item.entity_id),
        ))
        if not row:
            limitations.append(
                f"execution_target_missing:{item.provider}:{item.entity_level}:{item.entity_id}"
            )
            continue
        capabilities = execution_capabilities(row)
        if item.action not in set(capabilities["allowed_actions"]):
            limitations.append(
                "unsupported_action_removed:%s:%s:%s:%s:%s"
                % (
                    item.provider,
                    item.entity_level,
                    item.account_id or "",
                    item.entity_id,
                    item.action,
                )
            )
            continue
        kept.append(item)

    return policy.RecommendationOutput(
        summary=output.summary,
        recommendations=kept,
        limitations=list(dict.fromkeys(limitations)),
    )


def build_aligned_ask_openai(legacy: Any, policy: Any):
    """Build the policy's OpenAI call with an explicit execution-capability contract."""

    async def ask_openai(
        candidates: list[dict[str, Any]],
        *,
        now: Any,
        campaign_history: dict[str, Any],
        prior_decisions: dict[str, Any],
        business_profit: dict[str, Any],
    ) -> Any:
        if legacy.AsyncOpenAI is None:
            raise RuntimeError("openai_sdk_missing")
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("openai_api_key_missing")

        next_check = legacy._iso(now + policy.timedelta(hours=5))
        safe_rows = [_safe_candidate(row) for row in candidates]
        client = legacy.AsyncOpenAI(
            api_key=api_key,
            max_retries=0,
            timeout=legacy.OPENAI_TIMEOUT_SECONDS,
        )
        try:
            response = await client.responses.create(
                model=os.environ.get(
                    "MEZAN_CAMPAIGN_AI_MODEL",
                    os.environ.get("MEZAN_OPENAI_MODEL", "gpt-5-mini"),
                ),
                instructions=(
                    "أنت مدير أداء مستقل لمتجر أماسي وصاحب الحكم التسويقي الوحيد. "
                    "لا تستخدم قواعد قرار أو توصيات قديمة صادرة من كود ميزان. "
                    "بالنسبة إلى Snapchat استخدم نتائج campaign/ad_group/ad المرسلة كحقائق من "
                    "مدير سناب بوضع وقت التحويل conversion. سلة وMezan V2 يقدمان مبيعات وربحية "
                    "الحملة فقط، ولا يجوز اختراع مبيعات أو ربح سلة للمجموعة أو الإعلان. "
                    "نفّذ الحكم على مستوى الكيان الصحيح: pause مسموح للحملة والمجموعة والإعلان "
                    "النشط، أما reduce وscale فهما تعديل ميزانية ومسموحان فقط لكيان يملك ميزانية "
                    "مباشرة من نوع campaign أو ad_group عندما execution_capabilities تقول إن "
                    "budget_actions_allowed=true. لا تُرجع reduce أو scale على entity_level=ad مطلقًا. "
                    "إذا كشف إعلان فرصة توسعة أو هدرًا في الميزانية، افحص صف المجموعة أو الحملة الأم "
                    "الموجودة ضمن البيانات، ولا توجّه تعديل الميزانية إلى الأم إلا إذا كانت حقائقها "
                    "المجمعة نفسها تبرر القرار. إذا لم تدعم بيانات الأم التغيير فابقِ قرار الإعلان "
                    "pause أو monitor أو maintain حسب الأدلة. لا تخترع parent ولا تفترض أن إشارة إعلان "
                    "واحد تبرر تعديل ميزانية نطاق أكبر. automatic_parent_retargeting_allowed=false. "
                    "تعلم فقط من executed_experiments التي وافق عليها المالك ووصلت للتنفيذ. تعامل "
                    "مع الراتب ويوم الشهر ونهاية الأسبوع والمواسم كسياق احتمالي لا كقواعد ثابتة. "
                    f"CPA {policy.TARGET_CPA_SAR:.2f} ر.س وROAS {policy.TARGET_ROAS:.2f}× مراجع اقتصادية "
                    "وليست قواعد قرار. اشرح لماذا الآن، والثقة، ومدة الانتظار، ومعيار نجاح التجربة. "
                    "لا تدّع تنفيذ شيء؛ كل write يحتاج موافقة المالك. احتفظ حرفيًا بـ account_id "
                    "وaccount_name. recommendation_id بصيغة provider:level:account_id:id، واكتب بالعربية "
                    "بأرقام إنجليزية واجعل next_check_at مساويًا للقيمة المرسلة."
                ),
                input=json.dumps({
                    "next_check_at": next_check,
                    "saudi_market_timing_context": legacy._saudi_calendar_context(now),
                    "active_entities_last_3_days": safe_rows,
                    "campaign_history": campaign_history,
                    "overall_store_profit_context": business_profit,
                    "executed_experiments": prior_decisions,
                    "source_contract": {
                        "snapchat_entity_metrics": policy.SNAPCHAT_AI_PLATFORM_SOURCE,
                        "snapchat_action_report_time": policy.SNAPCHAT_AI_ACTION_REPORT_TIME,
                        "campaign_salla_profit": policy.SNAPCHAT_AI_SALLA_SOURCE,
                        "salla_child_attribution_allowed": False,
                        "mezan_previous_recommendations_allowed": False,
                        "mezan_fallback_decisions_allowed": False,
                    },
                    "execution_capability_contract": {
                        "ad_budget_actions_allowed": False,
                        "budget_change_levels": ["campaign", "ad_group"],
                        "ad_pause_allowed": True,
                        "automatic_parent_retargeting_allowed": False,
                        "budget_change_requires_direct_budget": True,
                    },
                }, ensure_ascii=False, default=str),
                max_output_tokens=legacy.OPENAI_MAX_OUTPUT_TOKENS,
                reasoning={"effort": "low"},
                store=False,
                text={"format": {
                    "type": "json_schema",
                    "name": "campaign_monitor_recommendations",
                    "strict": True,
                    "schema": legacy.AI_SCHEMA,
                }},
            )
            if getattr(response, "status", None) == "incomplete":
                details = getattr(response, "incomplete_details", None)
                reason = legacy._text(
                    getattr(details, "reason", "unknown"), limit=80
                ) or "unknown"
                raise policy.CampaignOpenAIError(
                    f"openai_response_incomplete_{reason}"
                )

            output = legacy._normalize_openai_output(
                response.output_text,
                candidates,
                next_check_at=next_check,
            )

            # Enforce the real provider write contract BEFORE legacy governance.
            # Otherwise a model's impossible `ad + scale` can be transformed into
            # `monitor` by a generic safety rule, hiding the fact that the model
            # targeted the wrong execution level and preventing the repair pass.
            executable_first = _filter_to_executable_contract(
                output,
                candidates,
                policy,
            )
            governed = legacy._govern_output(
                executable_first,
                candidates,
                next_check_at=next_check,
            )
            return _filter_to_executable_contract(governed, candidates, policy)
        finally:
            await client.close()

    return ask_openai


__all__ = [
    "BUDGET_ACTIONS",
    "WRITE_ACTIONS",
    "build_aligned_ask_openai",
    "execution_capabilities",
]
