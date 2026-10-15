"""Bounded OpenAI execution-target repair and loss-coverage review.

The first OpenAI pass owns marketing judgment. A second and final OpenAI pass
reviews two things before a snapshot is saved:

1. action/target pairs rejected by provider execution capabilities; and
2. campaign/ad-group budget owners that may have been omitted from the first
   recommendation set despite carrying material spend/performance evidence.

Mezan never promotes an ad decision to a parent, never creates a fallback
marketing decision, and never decides that a row is good or bad. The second
pass receives the same facts and is explicitly responsible for the judgment.
"""
from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable


UNSUPPORTED_PREFIX = "unsupported_action_removed:"
REPAIR_MARKER = "execution_alignment_repair_pass"
LOSS_COVERAGE_MARKER = "budget_owner_loss_coverage_review"
NO_EXECUTABLE_MARKER = "execution_alignment_no_executable_recommendation"


def _rejected(limitations: list[str] | None) -> list[str]:
    return [
        str(value)
        for value in (limitations or [])
        if str(value).startswith(UNSUPPORTED_PREFIX)
    ]


def _budget_owner_rows(candidates: list[dict[str, Any]], alignment: Any) -> list[dict[str, Any]]:
    """Return directly writable budget owners without labeling their performance."""
    rows: list[dict[str, Any]] = []
    for row in candidates:
        if str(row.get("entity_level") or "") not in {"campaign", "ad_group"}:
            continue
        capabilities = alignment.execution_capabilities(row)
        if capabilities.get("budget_actions_allowed"):
            rows.append(alignment._safe_candidate(row))
    return rows


def _item_key(item: Any) -> tuple[str, str, str, str]:
    return (
        str(getattr(item, "provider", "") or ""),
        str(getattr(item, "entity_level", "") or ""),
        str(getattr(item, "account_id", "") or ""),
        str(getattr(item, "entity_id", "") or ""),
    )


def _public_items(items: list[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in items:
        if hasattr(item, "model_dump"):
            output.append(item.model_dump())
        elif isinstance(item, dict):
            output.append(dict(item))
    return output


def _merge_reviewed(first_items: list[Any], review_items: list[Any]) -> list[Any]:
    """Use the review pass as the final judgment for any entity it re-evaluated."""
    merged: dict[tuple[str, str, str, str], Any] = {}
    order: list[tuple[str, str, str, str]] = []
    for item in first_items:
        key = _item_key(item)
        if key not in merged:
            order.append(key)
        merged[key] = item
    for item in review_items:
        key = _item_key(item)
        if key not in merged:
            order.append(key)
        merged[key] = item
    return [merged[key] for key in order if key in merged]


def _clean_summary(policy: Any, limitations: list[str]) -> Any:
    return policy.RecommendationOutput(
        summary=(
            "لا توجد حاليًا توصية تغيير قابلة للتنفيذ مباشرة بعد مراجعة الحملات "
            "والمجموعات والإعلانات. لم ينفذ ميزان أي تغيير، وسيعاد التحليل في الدورة التالية."
        ),
        recommendations=[],
        limitations=list(dict.fromkeys([*limitations, NO_EXECUTABLE_MARKER])),
    )


def build_repairing_ask_openai(
    aligned_ask: Callable[..., Awaitable[Any]],
    legacy: Any,
    policy: Any,
    alignment: Any,
):
    """Wrap the first model pass with one final model-owned loss/target review."""

    async def ask_openai(
        candidates: list[dict[str, Any]],
        *,
        now: Any,
        campaign_history: dict[str, Any],
        prior_decisions: dict[str, Any],
        business_profit: dict[str, Any],
    ) -> Any:
        first = await aligned_ask(
            candidates,
            now=now,
            campaign_history=campaign_history,
            prior_decisions=prior_decisions,
            business_profit=business_profit,
        )
        first_items = list(getattr(first, "recommendations", None) or [])
        rejected = _rejected(getattr(first, "limitations", None))
        budget_owners = _budget_owner_rows(candidates, alignment)

        if not rejected and not budget_owners:
            return first

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key or legacy.AsyncOpenAI is None:
            if first_items:
                return policy.RecommendationOutput(
                    summary=(
                        "التوصيات الظاهرة هي فقط القرارات القابلة للتنفيذ مباشرة بعد "
                        "موافقة المالك؛ لم تكتمل جولة تدقيق إضافية لأصحاب الميزانيات."
                    ),
                    recommendations=first_items,
                    limitations=list(dict.fromkeys([
                        *first.limitations,
                        LOSS_COVERAGE_MARKER,
                    ])),
                )
            return _clean_summary(policy, list(first.limitations))

        next_check = legacy._iso(now + policy.timedelta(hours=5))
        safe_rows = [alignment._safe_candidate(row) for row in candidates]
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
                    "هذه جولة المراجعة النهائية قبل حفظ توصيات الحملات. أنت وحدك صاحب الحكم "
                    "التسويقي؛ ميزان لا يقرر أن عنصرًا رابح أو خاسر ولا يرفع قرار إعلان إلى الأب. "
                    "راجع جميع صفوف campaign وad_group التي تملك ميزانية مباشرة، وليس فقط العناصر "
                    "التي اختيرت في الجولة الأولى. قارن الصرف والنتائج وROAS وCPA وسياق الحملة "
                    "والتاريخ والتجارب المنفذة. أعطِ الأولوية للأثر المالي الأكبر: لا تُنهِ المراجعة "
                    "بفرصة توسعة صغيرة بينما توجد حملة أو مجموعة ذات صرف أكبر وأداء أسوأ من دون أن "
                    "تحكم عليها صراحةً. الحكم قد يكون pause أو reduce أو maintain أو monitor حسب "
                    "الأدلة؛ لا توجد عتبة ميزان ثابتة تجبرك على الإيقاف. "
                    "إذا كانت first_pass_recommendations جيدة احتفظ بها، ويمكنك تعديل قرار نفس "
                    "الكيان إذا كشفت المراجعة الشاملة قرارًا أفضل. أضف توصيات لأصحاب الميزانية الذين "
                    "تم إغفالهم عندما ترى أن بياناتهم تستحق إجراء أو مراقبة مهمة. "
                    "لا تُرجع reduce أو scale على entity_level=ad مطلقًا. إذا كان الإعلان هو سبب "
                    "الإشارة فلا تختَر ad_group أو campaign إلا إذا حقائق ذلك الأب نفسه تبرر القرار "
                    "وexecution_capabilities.budget_actions_allowed=true. pause للإعلان مسموح إذا "
                    "كان الإعلان نفسه يستحق الإيقاف. لا تخترع parent ولا تنقل قرارًا آليًا. "
                    "Snapchat يعتمد conversion-time، وربحية Salla للحملة فقط ولا تنسب ربح Salla "
                    "للمجموعة أو الإعلان. توقيت الراتب والموسم سياق احتمالي فقط. "
                    "summary يجب أن يصف مجموعة القرارات النهائية كاملة بعد هذه المراجعة، لا القرارات "
                    "المرفوضة أو المحذوفة. لا تدّع تنفيذ أي تغيير؛ كل write يحتاج موافقة المالك."
                ),
                input=json.dumps({
                    "next_check_at": next_check,
                    "review_reason": (
                        "execution_target_repair_and_budget_owner_loss_coverage"
                        if rejected
                        else "budget_owner_loss_coverage"
                    ),
                    "rejected_first_pass_actions": rejected,
                    "first_pass_recommendations": _public_items(first_items),
                    "active_entities_last_3_days": safe_rows,
                    "direct_budget_owners_to_review": budget_owners,
                    "saudi_market_timing_context": legacy._saudi_calendar_context(now),
                    "campaign_history": campaign_history,
                    "overall_store_profit_context": business_profit,
                    "executed_experiments": prior_decisions,
                    "source_contract": {
                        "snapchat_action_report_time": "conversion",
                        "salla_child_attribution_allowed": False,
                        "mezan_fallback_decisions_allowed": False,
                    },
                    "execution_capability_contract": {
                        "ad_budget_actions_allowed": False,
                        "budget_change_levels": ["campaign", "ad_group"],
                        "budget_change_requires_direct_budget": True,
                        "automatic_parent_retargeting_allowed": False,
                        "ad_pause_allowed": True,
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
                if first_items:
                    return policy.RecommendationOutput(
                        summary=(
                            "التوصيات الظاهرة اجتازت قابلية التنفيذ، لكن جولة تدقيق أصحاب "
                            "الميزانيات لم تكتمل في هذه الدورة."
                        ),
                        recommendations=first_items,
                        limitations=list(dict.fromkeys([
                            *first.limitations,
                            LOSS_COVERAGE_MARKER,
                        ])),
                    )
                return _clean_summary(policy, list(first.limitations))

            reviewed_raw = legacy._normalize_openai_output(
                response.output_text,
                candidates,
                next_check_at=next_check,
            )

            # Preserve the exact provider execution error. The generic legacy
            # governor can convert a low-sample `ad + scale` to `monitor`; doing
            # that first would hide an invalid budget target. Therefore apply the
            # provider contract before governance, then govern, then verify again.
            reviewed_executable = alignment._filter_to_executable_contract(
                reviewed_raw,
                candidates,
                policy,
            )
            reviewed_governed = legacy._govern_output(
                reviewed_executable,
                candidates,
                next_check_at=next_check,
            )
            reviewed = alignment._filter_to_executable_contract(
                reviewed_governed,
                candidates,
                policy,
            )
            final_items = _merge_reviewed(first_items, list(reviewed.recommendations))
            final_limitations = list(dict.fromkeys([
                *first.limitations,
                *reviewed.limitations,
                LOSS_COVERAGE_MARKER,
                *([REPAIR_MARKER] if rejected else []),
            ]))

            if final_items:
                return policy.RecommendationOutput(
                    summary=reviewed.summary,
                    recommendations=final_items,
                    limitations=final_limitations,
                )
            return _clean_summary(policy, final_limitations)
        except Exception:
            if first_items:
                return policy.RecommendationOutput(
                    summary=(
                        "التوصيات الظاهرة اجتازت قابلية التنفيذ؛ تعذر إكمال جولة تدقيق "
                        "إضافية لأصحاب الميزانيات في هذه الدورة."
                    ),
                    recommendations=first_items,
                    limitations=list(dict.fromkeys([
                        *first.limitations,
                        LOSS_COVERAGE_MARKER,
                    ])),
                )
            return _clean_summary(policy, list(first.limitations))
        finally:
            await client.close()

    return ask_openai


__all__ = [
    "LOSS_COVERAGE_MARKER",
    "NO_EXECUTABLE_MARKER",
    "REPAIR_MARKER",
    "build_repairing_ask_openai",
]
