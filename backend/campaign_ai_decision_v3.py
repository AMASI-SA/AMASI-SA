"""OpenAI-owned root-cause decision intelligence for Campaign AI V3.

The existing scheduler, source collection, snapshot and approval path remain
unchanged.  This module replaces only the marketing reasoning boundary:

1. first OpenAI pass diagnoses the ordered temporal/funnel/product evidence;
2. second OpenAI pass reviews every direct budget owner and counterfactually
   challenges the complete proposed recommendation set;
3. code validates references and write safety, but never chooses a marketing
   action from ROAS/CPA thresholds.

Business recommendations remain visible even when they cannot be executed by an
Ads API.  In that case the internal legacy action is projected to ``monitor``
only to keep the old execution endpoint fail-closed; the rich
``recommended_action`` remains unchanged in the public snapshot.
"""
from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable

from pydantic import ValidationError

from campaign_ai_decision_review_schema_v3 import (
    DecisionReviewOutputV3,
    review_json_schema,
)
from campaign_ai_decision_schema_v3 import (
    ADS_WRITE_ACTIONS,
    DecisionOutputV3,
    DecisionRecommendationV3,
    legacy_execution_action,
    v3_json_schema,
)
from campaign_ai_evidence_v3 import build_decision_evidence_pack_v3
from campaign_ai_runtime_context_v3 import get_runtime_context


V3_MARKER = "decision_intelligence_v3"
COUNTERFACTUAL_MARKER = "counterfactual_review_v3"
BUDGET_OWNER_REVIEW_MARKER = "budget_owner_review_v3"
REVIEW_INCOMPLETE_MARKER = "decision_v3_review_incomplete"

CREATIVE_ACTIONS = {
    "TEST_NEW_CREATIVE", "REFRESH_CREATIVE", "TEST_NEW_HOOK", "SHORTEN_VIDEO",
    "LONGER_DEMO_VIDEO", "PRODUCT_DEMO", "PROBLEM_SOLUTION_VIDEO",
    "UGC_STYLE_VIDEO", "TESTIMONIAL_VIDEO", "BEFORE_AFTER", "STORYTELLING_VIDEO",
    "FAQ_VIDEO", "OBJECTION_HANDLING_VIDEO", "PRICE_OFFER_VIDEO", "UNBOXING_VIDEO",
    "PRODUCT_CLOSEUP", "LIFESTYLE_VIDEO", "COMPARISON_VIDEO", "STORY_AD",
    "STATIC_IMAGE_TEST", "CAROUSEL_TEST", "CHANGE_VALUE_PROPOSITION",
    "ADD_STRONGER_CTA", "SHOW_PRODUCT_EARLIER", "SHOW_PRICE_OR_OFFER",
}
PRODUCT_CHANGE_ACTIONS = {
    "CHANGE_PRODUCT_TITLE", "CHANGE_PRODUCT_DESCRIPTION", "CHANGE_HERO_IMAGE",
    "REORDER_PRODUCT_IMAGES",
}
OPERATIONAL_ACTIONS = {
    "FIX_DESTINATION_URL", "RESTORE_PRODUCT_VISIBILITY", "REVIEW_INVENTORY",
}
DIAGNOSTIC_ACTIONS = {
    "REVIEW_AUDIENCE", "REVIEW_PRODUCT", "REVIEW_OFFER", "REVIEW_PRODUCT_PAGE",
    "REVIEW_PRICE", "REVIEW_SHIPPING_COST", "REVIEW_CHECKOUT", "REVIEW_PAYMENT",
    "INVESTIGATE_ABANDONED_CARTS", "INVESTIGATE_WEBSITE", "INVESTIGATE_TRACKING",
    "FIX_TRACKING",
}


def _candidate_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("provider") or ""),
        str(row.get("entity_level") or ""),
        str(row.get("account_id") or ""),
        str(row.get("entity_id") or ""),
    )


def _key_text(row: dict[str, Any]) -> str:
    return "|".join(_candidate_key(row))


def _decision_key(item: DecisionRecommendationV3) -> tuple[str, str, str, str]:
    return (
        str(item.provider),
        str(item.entity_level),
        str(item.account_id or ""),
        str(item.entity_id),
    )


def _expected_action_type(action: str) -> str:
    if action in ADS_WRITE_ACTIONS:
        return "ads_write"
    if action in CREATIVE_ACTIONS:
        return "creative"
    if action in PRODUCT_CHANGE_ACTIONS:
        return "product_change"
    if action in OPERATIONAL_ACTIONS:
        return "operational_alert"
    if action in DIAGNOSTIC_ACTIONS:
        return "diagnostic"
    if action in {"CONTINUE", "MONITOR", "NO_ACTION_INSUFFICIENT_DATA"}:
        return "no_action" if action == "NO_ACTION_INSUFFICIENT_DATA" else "diagnostic"
    return "diagnostic"


def _product_block(evidence_pack: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return (
        ((evidence_pack.get("product_intelligence") or {}).get("entities") or {})
        .get(_key_text(row), {})
    )


def _scale_execution_blockers(evidence_pack: dict[str, Any], row: dict[str, Any]) -> list[str]:
    """Safety-only capacity validation before an owner can approve a scale write."""
    block = _product_block(evidence_pack, row)
    products = block.get("products") if isinstance(block.get("products"), list) else []
    if not products:
        return ["product_availability_not_verified_for_scale"]
    blockers: list[str] = []
    for product in products:
        product_id = str(product.get("product_id") or "unknown")
        visibility = str(product.get("visibility") or "unknown")
        page = product.get("page_probe") if isinstance(product.get("page_probe"), dict) else {}
        page_status = str(page.get("status") or "PRODUCT_URL_UNKNOWN")
        inventory = product.get("inventory") if isinstance(product.get("inventory"), dict) else {}
        inventory_status = str(inventory.get("status") or "unknown")
        if visibility != "public_status_expected":
            blockers.append(f"product_visibility_not_verified:{product_id}:{visibility}")
        if page_status not in {"PRODUCT_URL_OK", "PRODUCT_URL_REDIRECTED"}:
            blockers.append(f"product_page_not_verified:{product_id}:{page_status}")
        if inventory_status in {
            "out_of_stock",
            "less_than_one_day_estimated",
            "low_stock_estimated",
            "unknown",
        }:
            blockers.append(f"product_capacity_constraint:{product_id}:{inventory_status}")
    return list(dict.fromkeys(blockers))


def _canonical_product_identity(
    evidence_pack: dict[str, Any],
    row: dict[str, Any],
    requested_product_id: str | None,
) -> tuple[str | None, str | None, list[str]]:
    block = _product_block(evidence_pack, row)
    products = block.get("products") if isinstance(block.get("products"), list) else []
    by_id = {
        str(product.get("product_id")): product
        for product in products
        if product.get("product_id") is not None
    }
    limitations: list[str] = []
    chosen = None
    if requested_product_id and str(requested_product_id) in by_id:
        chosen = by_id[str(requested_product_id)]
    elif requested_product_id:
        limitations.append("model_product_id_not_in_verified_association")
    elif len(by_id) == 1:
        chosen = next(iter(by_id.values()))
    if not chosen:
        return None, block.get("advertised_destination_url"), limitations
    return (
        str(chosen.get("product_id") or "") or None,
        chosen.get("destination_url") or block.get("advertised_destination_url"),
        limitations,
    )


def _knowledge_refs(
    evidence_pack: dict[str, Any],
    requested: list[Any],
) -> list[dict[str, Any]]:
    retrieved = ((evidence_pack.get("marketing_knowledge") or {}).get("retrieved") or [])
    by_id = {str(row.get("source_id") or ""): row for row in retrieved}
    output = []
    for item in requested:
        source_id = str(getattr(item, "source_id", "") or "")
        source = by_id.get(source_id)
        if not source:
            continue
        output.append({
            "source_id": source_id,
            "title": source.get("title") or "",
            "source_tier": source.get("source_tier") or 3,
            "published_at": source.get("published_at"),
            "last_reviewed_at": source.get("last_reviewed_at") or "",
            "reliability": source.get("reliability") or "contextual",
            "topics": source.get("topics") or [],
            "insight_summary": source.get("insight_summary") or "",
        })
    return output


def _normalize_decision_output(
    output: DecisionOutputV3,
    candidates: list[dict[str, Any]],
    *,
    evidence_pack: dict[str, Any],
    next_check_at: str,
    reviewed_budget_owners: set[str] | None,
    counterfactual_reviewed: set[str] | None,
    alignment: Any,
    legacy: Any,
) -> tuple[Any, list[str]]:
    candidate_by_key = {_candidate_key(row): row for row in candidates}
    recommendations = []
    limitations = list(output.limitations)
    seen: set[tuple[str, str, str, str]] = set()

    for rich in output.recommendations:
        key = _decision_key(rich)
        row = candidate_by_key.get(key)
        if not row:
            limitations.append(
                f"decision_v3_target_missing:{rich.provider}:{rich.entity_level}:{rich.entity_id}"
            )
            continue
        if key in seen:
            limitations.append(f"decision_v3_duplicate_target:{'|'.join(key)}")
            continue
        seen.add(key)

        recommendation_id = f"{rich.provider}:{rich.entity_level}:{row.get('account_id') or 'unknown'}:{rich.entity_id}"
        product_id, destination_url, product_limits = _canonical_product_identity(
            evidence_pack,
            row,
            rich.product_id,
        )
        limitations.extend(product_limits)
        expected_type = _expected_action_type(rich.recommended_action)
        legacy_action = legacy_execution_action(
            rich.recommended_action,
            entity_level=rich.entity_level,
        )
        capabilities = alignment.execution_capabilities(row)
        blockers: list[str] = []
        ads_write_requested = rich.recommended_action in ADS_WRITE_ACTIONS
        if expected_type != rich.action_type:
            limitations.append(
                f"decision_v3_action_type_normalized:{recommendation_id}:{rich.action_type}->{expected_type}"
            )
        if ads_write_requested and legacy_action not in set(capabilities.get("allowed_actions") or []):
            blockers.append("provider_execution_capability_mismatch")
        if rich.recommended_action == "INCREASE_BUDGET":
            blockers.extend(_scale_execution_blockers(evidence_pack, row))
        if reviewed_budget_owners is not None and rich.recommended_action in {"INCREASE_BUDGET", "DECREASE_BUDGET"}:
            if _key_text(row) not in reviewed_budget_owners:
                blockers.append("budget_owner_not_confirmed_in_final_review")
        if counterfactual_reviewed is not None and ads_write_requested:
            if recommendation_id not in counterfactual_reviewed:
                blockers.append("counterfactual_review_not_confirmed")

        code_executable = bool(ads_write_requested and not blockers)
        # Existing V2 code derives approval availability from the legacy action.
        # Project a blocked write to monitor while preserving the rich business
        # recommendation unchanged in _decision_v3.
        execution_action = legacy_action if code_executable else "monitor"
        change_percent = (
            rich.change_percent
            if execution_action in {"reduce", "scale"}
            else None
        )

        update = rich.model_copy(update={
            "recommendation_id": recommendation_id,
            "entity_name": str(row.get("entity_name") or rich.entity_id),
            "account_id": row.get("account_id"),
            "account_name": row.get("account_name") or row.get("account_id"),
            "parent_name": row.get("parent_name"),
            "product_id": product_id,
            "destination_url": destination_url,
            "action_type": expected_type,
            "executable": code_executable,
            "next_check_at": next_check_at,
            "external_knowledge_used": _knowledge_refs(
                evidence_pack,
                rich.external_knowledge_used,
            ),
        })
        rich_public = update.model_dump()
        rich_public["execution_blockers"] = list(dict.fromkeys(blockers))
        rich_public["decision_schema_version"] = V3_MARKER
        rich_public["recommendation_execution_separated"] = True
        row["_decision_v3"] = rich_public

        if rich.recommended_action == "TEST_NEW_CREATIVE" and rich.creative_brief is None:
            limitations.append(f"creative_brief_missing:{recommendation_id}")
        recommendations.append(legacy.RecommendationItem(
            recommendation_id=recommendation_id,
            provider=rich.provider,
            entity_level=rich.entity_level,
            entity_id=rich.entity_id,
            entity_name=str(row.get("entity_name") or rich.entity_id),
            account_id=row.get("account_id"),
            account_name=row.get("account_name") or row.get("account_id"),
            parent_name=row.get("parent_name"),
            action=execution_action,
            change_percent=change_percent,
            priority=rich.priority,
            confidence=rich.confidence,
            title=rich.title,
            rationale=rich.diagnosis,
            evidence=list(rich.evidence_for)[:6],
            why_now=rich.why,
            recommended_wait_hours=min(24, max(1, int(rich.recommended_wait_hours))),
            observation_plan=rich.observation_plan,
            success_criteria=list(rich.success_criteria)[:4],
            risk_if_ignored="؛ ".join(rich.risks[:4]) or "قد يستمر السبب الجذري دون معالجة.",
            guardrail=rich.guardrail,
            next_check_at=next_check_at,
        ))

    return legacy.RecommendationOutput(
        summary=output.summary,
        recommendations=recommendations,
        limitations=list(dict.fromkeys([*limitations, V3_MARKER])),
    ), limitations


def _budget_owner_keys(candidates: list[dict[str, Any]], alignment: Any) -> list[str]:
    return [
        _key_text(row)
        for row in candidates
        if str(row.get("entity_level") or "") in {"campaign", "ad_group"}
        and alignment.execution_capabilities(row).get("budget_actions_allowed")
    ]


def _openai_client(legacy: Any) -> Any:
    if legacy.AsyncOpenAI is None:
        raise RuntimeError("openai_sdk_missing")
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("openai_api_key_missing")
    return legacy.AsyncOpenAI(
        api_key=api_key,
        max_retries=0,
        timeout=legacy.OPENAI_TIMEOUT_SECONDS,
    )


def _model() -> str:
    return os.environ.get(
        "MEZAN_CAMPAIGN_AI_MODEL",
        os.environ.get("MEZAN_OPENAI_MODEL", "gpt-5-mini"),
    )


FIRST_PASS_INSTRUCTIONS = """
أنت محلل أداء وتسويق إلكتروني مستقل لمتجر أماسي وصاحب الحكم التسويقي النهائي.
الكود يجمع الحقائق ويضمن الجودة والأمان والتنفيذ، لكنه لا يقرر أن ROAS أو CPA معين يعني Pause.

اتبع التسلسل التشخيصي قبل أي Action:
A جودة البيانات → B Delivery → C Creative → D Click intent → E Destination health →
F Product availability → G Product page → H Add To Cart → I Checkout → J Payment →
K Shipping → L Inventory → M Profitability → ثم فقط Campaign action.

الزمن ليس Aggregate 3 أيام كقاعدة. ابدأ Today وحده: مقدار الصرف، الجزء المنقضي من اليوم،
كفاية العينة، والانحراف عن baseline. إذا كانت بيانات اليوم قليلة استخدم INSUFFICIENT_DATA /
NO_ACTION_INSUFFICIENT_DATA أو MONITOR. إذا كانت إشارة اليوم سلبية وكافية انتقل إلى Yesterday.
إذا أمس جيد واليوم فقط سيئ فافحص احتمال NORMAL_VARIANCE. إذا اليوم وأمس سيئان افحص Day-2.
استخدم 7d و30d كخط أساس لفهم السلوك الطبيعي، لا كقواعد قرار.

شخّص الـFunnel كاملًا. انخفاض Purchase لا يعني أن الإعلان سيئ. CTR/traffic/ATC/Checkout قوية مع
Purchase ضعيف ترفع فرضيات Checkout/Payment/Shipping/Website/Tracking بدل معاقبة مصدر الزيارات.
قارن Snapchat وMeta ومتجر Salla والسلات المتروكة في نفس الفترة. Store-level carts هي corroborating
 evidence فقط ولا تصبح Campaign revenue ما لم تحمل Attribution مطابقًا.

افحص المنتج قبل لوم الإعلان: Destination URL، الصفحة العامة، Visibility، السعر والعرض، الخيارات
والـVariants، المخزون، قابلية Add To Cart، وتناسق Ad↔Product Page. لا توصي Scale إذا الأدلة لا تثبت
أن المنتج/الصفحة/المخزون يستطيع استيعاب الزيادة. إذا الرابط أو المنتج نفسه معطل فشخّص السبب الحقيقي.

استخدم Video/Funnel metrics كEvidence Framework لا كقوانين: drop مبكر قد يعني Hook، drop وسط قد
يعني pacing/relevance، completion جيد مع CTR ضعيف قد يعني CTA/Offer، CTR جيد مع ATC ضعيف قد يعني
Landing/Product Page، ATC جيد مع Purchase ضعيف قد يعني Checkout/Payment/Shipping. المشاهدات وحدها
ليست نجاحًا؛ اربط attention → traffic quality → shopping intent → purchase → revenue → profit.

عند مشكلة Creative اختر نوع الاختبار المناسب بدل عبارة عامة. كل TEST_NEW_CREATIVE يجب أن يحتوي
Creative Brief كاملًا. STORY_AD يجب أن يصف ما نصوره وتسلسل المشاهد/المدة/النص/CTA والفرضية.

Recommendation منفصلة عن Execution. يمكنك إصدار REVIEW_CHECKOUT أو TEST_NEW_HOOK أو
CHANGE_PRODUCT_DESCRIPTION حتى لو لا يستطيع Ads API تنفيذها. action_type يصف طبيعة الإجراء.
لا تدّع تنفيذ شيء. تعديلات المنتج/السعر/المحتوى اقتراح فقط في هذه المرحلة.

Context مثل الراتب/نهاية الأسبوع/الموسم/رمضان/العيد/اليوم الوطني تفسير احتمالي لا Rule.
المعرفة المسترجعة منهج مساند، وليست سلطة فوق بيانات أماسي ولا تُقلد المصادر حرفيًا.

قبل PAUSE كوّن Root Cause Investigation: هل توجد مشكلة صفحة/منتج/Checkout/Payment/Tracking/
Learning/partial-day/attribution delay/creative count تفسر النتائج أفضل من أن Traffic نفسه سيئ؟
إذا نعم، عالج/حقق في السبب أولًا. evidence_against وwhat_would_change_the_decision إلزاميان لكل قرار.
لا تختلق بيانات غير موجودة؛ استخدم UNKNOWN/INSUFFICIENT_DATA وlimitations عند الحاجة.
""".strip()


SECOND_PASS_INSTRUCTIONS = """
هذه الجولة النهائية Counterfactual + Budget-owner Review وليست إعادة قواعد من ميزان.
راجع القرار الأول من الصفر أمام الأدلة نفسها. يجب:
1) مراجعة كل key في required_budget_owner_keys حتى لا تُغفل حملة/مجموعة ذات صرف أو أثر كبير.
2) مراجعة كل توصية أولية وتسأل: ما الدليل الذي قد يجعلها خاطئة؟
3) قبل أي PAUSE راجع تحديدًا CTR/ATC/Checkout/carts/payment/learning/partial-day/attribution/history/
creative-count/product/page/inventory. إذا كان تفسير Downstream أقوى غيّر القرار إلى علاج السبب.
4) قبل INCREASE_BUDGET راجع الربحية + Product availability + page health + inventory/capacity evidence.
5) final_decision يجب أن يكون المجموعة النهائية الكاملة، وليس Delta. يمكنك الاحتفاظ أو تعديل أو حذف
أي توصية أولية وإضافة توصية أغفلها المرور الأول.
6) reviewed_budget_owner_keys يجب أن يسرد كل owner تمت مراجعته، و
counterfactual_reviewed_recommendation_ids يجب أن يسرد recommendation_id لكل توصية نهائية راجعتها.
OpenAI وحده صاحب الحكم التسويقي؛ لا توجد عتبة ROAS/CPA برمجية تجبر قرارًا.
""".strip()


def build_decision_v3_ask_openai(legacy: Any, policy: Any, alignment: Any) -> Callable[..., Awaitable[Any]]:
    async def ask_openai(
        candidates: list[dict[str, Any]],
        *,
        now: Any,
        campaign_history: dict[str, Any],
        prior_decisions: dict[str, Any],
        business_profit: dict[str, Any],
    ) -> Any:
        runtime = get_runtime_context()
        next_check = legacy._iso(now + policy.timedelta(hours=5))
        evidence_pack = await build_decision_evidence_pack_v3(
            runtime.db,
            runtime.user_id,
            candidates,
            end=now.astimezone(legacy.RIYADH_OFFSET).date(),
            current=now,
        )
        safe_rows = [alignment._safe_candidate(row) for row in candidates]
        client = _openai_client(legacy)
        try:
            first_response = await client.responses.create(
                model=_model(),
                instructions=FIRST_PASS_INSTRUCTIONS,
                input=json.dumps({
                    "next_check_at": next_check,
                    "current_market_context": legacy._saudi_calendar_context(now),
                    "active_entities": safe_rows,
                    "decision_evidence_v3": evidence_pack,
                    "overall_store_profit_context": business_profit,
                    "executed_experiments": prior_decisions,
                    "legacy_campaign_history_context": campaign_history,
                    "source_contract": {
                        "snapchat_entity_metrics": policy.SNAPCHAT_AI_PLATFORM_SOURCE,
                        "snapchat_action_report_time": policy.SNAPCHAT_AI_ACTION_REPORT_TIME,
                        "campaign_salla_profit": policy.SNAPCHAT_AI_SALLA_SOURCE,
                        "salla_child_attribution_allowed": False,
                        "mezan_previous_recommendations_allowed": False,
                        "mezan_fallback_decisions_allowed": False,
                    },
                }, ensure_ascii=False, default=str),
                max_output_tokens=max(legacy.OPENAI_MAX_OUTPUT_TOKENS, 24000),
                reasoning={"effort": "medium"},
                store=False,
                text={"format": {
                    "type": "json_schema",
                    "name": "campaign_decision_intelligence_v3",
                    "strict": True,
                    "schema": v3_json_schema(),
                }},
            )
            if getattr(first_response, "status", None) == "incomplete":
                details = getattr(first_response, "incomplete_details", None)
                reason = legacy._text(getattr(details, "reason", "unknown"), limit=80) or "unknown"
                raise policy.CampaignOpenAIError(f"openai_v3_first_incomplete_{reason}")
            try:
                first = DecisionOutputV3.model_validate_json(first_response.output_text)
            except ValidationError as exc:
                raise policy.CampaignOpenAIError("openai_v3_first_validation_error") from exc

            required_budget_owners = _budget_owner_keys(candidates, alignment)
            first_ids = [str(item.recommendation_id) for item in first.recommendations]
            review_response = await client.responses.create(
                model=_model(),
                instructions=SECOND_PASS_INSTRUCTIONS,
                input=json.dumps({
                    "next_check_at": next_check,
                    "required_budget_owner_keys": required_budget_owners,
                    "first_pass_recommendations": first.model_dump(),
                    "active_entities": safe_rows,
                    "decision_evidence_v3": evidence_pack,
                    "overall_store_profit_context": business_profit,
                    "executed_experiments": prior_decisions,
                    "current_market_context": legacy._saudi_calendar_context(now),
                }, ensure_ascii=False, default=str),
                max_output_tokens=max(legacy.OPENAI_MAX_OUTPUT_TOKENS, 24000),
                reasoning={"effort": "medium"},
                store=False,
                text={"format": {
                    "type": "json_schema",
                    "name": "campaign_decision_review_v3",
                    "strict": True,
                    "schema": review_json_schema(),
                }},
            )
            if getattr(review_response, "status", None) == "incomplete":
                # First pass is still useful diagnostically, but no Ads write may
                # bypass the mandatory final review.
                final_legacy, _ = _normalize_decision_output(
                    first,
                    candidates,
                    evidence_pack=evidence_pack,
                    next_check_at=next_check,
                    reviewed_budget_owners=set(),
                    counterfactual_reviewed=set(),
                    alignment=alignment,
                    legacy=legacy,
                )
                final_legacy.limitations = list(dict.fromkeys([
                    *final_legacy.limitations,
                    REVIEW_INCOMPLETE_MARKER,
                ]))
                return final_legacy
            try:
                review = DecisionReviewOutputV3.model_validate_json(review_response.output_text)
            except ValidationError:
                final_legacy, _ = _normalize_decision_output(
                    first,
                    candidates,
                    evidence_pack=evidence_pack,
                    next_check_at=next_check,
                    reviewed_budget_owners=set(),
                    counterfactual_reviewed=set(),
                    alignment=alignment,
                    legacy=legacy,
                )
                final_legacy.limitations = list(dict.fromkeys([
                    *final_legacy.limitations,
                    REVIEW_INCOMPLETE_MARKER,
                    "openai_v3_review_validation_error",
                ]))
                return final_legacy

            reviewed_budget = set(review.reviewed_budget_owner_keys)
            counterfactual = set(review.counterfactual_reviewed_recommendation_ids)
            missing_budget = [key for key in required_budget_owners if key not in reviewed_budget]
            final_legacy, _ = _normalize_decision_output(
                review.final_decision,
                candidates,
                evidence_pack=evidence_pack,
                next_check_at=next_check,
                reviewed_budget_owners=reviewed_budget,
                counterfactual_reviewed=counterfactual,
                alignment=alignment,
                legacy=legacy,
            )
            review_limits = list(review.review_limitations)
            if missing_budget:
                review_limits.append(f"budget_owner_review_missing_count:{len(missing_budget)}")
            final_legacy.limitations = list(dict.fromkeys([
                *final_legacy.limitations,
                *review_limits,
                COUNTERFACTUAL_MARKER,
                BUDGET_OWNER_REVIEW_MARKER,
            ]))
            return final_legacy
        finally:
            await client.close()

    return ask_openai


__all__ = [
    "BUDGET_OWNER_REVIEW_MARKER",
    "COUNTERFACTUAL_MARKER",
    "REVIEW_INCOMPLETE_MARKER",
    "V3_MARKER",
    "build_decision_v3_ask_openai",
]
