from types import SimpleNamespace

import campaign_ai_decision_v3 as decision
import campaign_ai_execution_alignment as alignment
import campaign_ai_monitor_legacy as legacy
from campaign_ai_decision_schema_v3 import (
    DecisionOutputV3,
    DecisionRecommendationV3,
    legacy_execution_action,
)
from campaign_ai_visual_evidence_v3 import product_visuals, responses_input
from tests.campaign_ai_v3_scenarios import SCENARIOS


def analysis(status="UNKNOWN", summary="لا تتوفر إشارة حاسمة"):
    return {
        "status": status,
        "summary": summary,
        "metrics": [],
        "signals": [],
        "limitations": [],
    }


def product_analysis():
    return {
        "page_health": "unknown",
        "url_health": "unknown",
        "visibility": "unknown",
        "inventory_status": "unknown",
        "estimated_days_to_stockout": None,
        "estimated_stockout_at": None,
        "promoted_variant_status": "unknown",
        "product_title_analysis": "unknown",
        "product_description_analysis": "unknown",
        "hero_image_analysis": "unknown",
        "gallery_analysis": "unknown",
        "pricing_analysis": "unknown",
        "competitor_price_context": "unavailable",
        "internal_price_context": "unavailable",
        "offer_analysis": "unknown",
        "reviews_analysis": "unavailable",
        "shipping_analysis": "unknown",
        "ad_page_consistency": "unknown",
        "detected_issues": [],
        "recommendations": [],
        "priority": "medium",
        "confidence": "low",
    }


def recommendation(action, *, entity_level="campaign", action_type=None, executable=False):
    rec_id = f"snapchat:{entity_level}:acct-1:entity-1"
    if action_type is None:
        action_type = "ads_write" if action in {
            "PAUSE_AD", "PAUSE_ADSET", "PAUSE_CAMPAIGN",
            "DECREASE_BUDGET", "INCREASE_BUDGET",
        } else "diagnostic"
    return DecisionRecommendationV3.model_validate({
        "recommendation_id": rec_id,
        "provider": "snapchat",
        "entity_level": entity_level,
        "entity_id": "entity-1",
        "entity_name": "عنصر",
        "account_id": "acct-1",
        "account_name": "Self Service",
        "parent_name": None,
        "product_id": None,
        "destination_url": None,
        "recommendation_type": "diagnostic" if action_type == "diagnostic" else "media_buying",
        "action_type": action_type,
        "executable": executable,
        "recommended_action": action,
        "change_percent": 15 if action in {"DECREASE_BUDGET", "INCREASE_BUDGET"} else None,
        "priority": "high",
        "confidence": "medium",
        "title": "قرار اختبار",
        "diagnosis": "تشخيص مبني على الأدلة",
        "root_cause_category": "UNKNOWN",
        "primary_hypothesis": None,
        "secondary_hypotheses": [],
        "evidence_for": ["دليل"],
        "evidence_against": ["دليل مضاد تمت مراجعته"],
        "today_analysis": analysis(),
        "yesterday_analysis": analysis(),
        "day_minus_2_analysis": analysis(),
        "baseline_7d": analysis(),
        "baseline_30d": analysis(),
        "funnel_analysis": analysis(),
        "video_analysis": analysis(),
        "creative_analysis": analysis(),
        "product_page_analysis": product_analysis(),
        "inventory_analysis": analysis(),
        "abandoned_cart_analysis": analysis(),
        "cross_campaign_analysis": analysis(),
        "cross_platform_analysis": analysis(),
        "business_context": analysis(),
        "external_knowledge_used": [],
        "creative_brief": None,
        "proposed_product_changes": [],
        "why": "السبب",
        "expected_effect": "اختبار السبب الجذري",
        "risks": ["خطر"],
        "what_would_change_the_decision": ["ظهور دليل أقوى معاكس"],
        "recommended_wait_hours": 5,
        "observation_plan": "أعد القياس",
        "success_criteria": ["تحسن المرحلة المتأثرة"],
        "guardrail": "بعد موافقة المالك فقط عند وجود write",
        "next_check_at": "2026-08-19T05:00:00+00:00",
    })


def candidate(*, budget=100.0, level="campaign"):
    return {
        "provider": "snapchat",
        "entity_level": level,
        "account_id": "acct-1",
        "account_name": "Self Service",
        "entity_id": "entity-1",
        "entity_name": "عنصر",
        "active": True,
        "current_daily_budget_native": budget,
    }


def evidence(*, healthy_product=False):
    products = []
    if healthy_product:
        products = [{
            "product_id": "p-1",
            "product_name": "منتج",
            "destination_url": "https://amasi-sa.com/p/1",
            "canonical_product_url": "https://amasi-sa.com/p/1",
            "visibility": "public_status_expected",
            "page_probe": {"status": "PRODUCT_URL_OK"},
            "inventory": {"status": "in_stock"},
            "main_image": "https://cdn.example.com/hero.jpg",
            "images": ["https://cdn.example.com/2.jpg"],
        }]
    return {
        "product_intelligence": {
            "entities": {
                "snapchat|campaign|acct-1|entity-1": {
                    "products": products,
                    "advertised_destination_url": "https://amasi-sa.com/p/1" if products else None,
                }
            }
        },
        "marketing_knowledge": {"retrieved": []},
    }


def normalize(rec, *, pack, reviewed=True):
    row = candidate()
    output = DecisionOutputV3(summary="ok", recommendations=[rec], limitations=[])
    normalized, _ = decision._normalize_decision_output(
        output,
        [row],
        evidence_pack=pack,
        next_check_at="2026-08-19T05:00:00+00:00",
        reviewed_budget_owners={"snapchat|campaign|acct-1|entity-1"} if reviewed else set(),
        counterfactual_reviewed={"snapchat:campaign:acct-1:entity-1"} if reviewed else set(),
        alignment=alignment,
        legacy=legacy,
    )
    return row, normalized


def test_scenario_corpus_has_exact_requested_33_unique_cases():
    assert len(SCENARIOS) == 33
    ids = [row["id"] for row in SCENARIOS]
    assert len(ids) == len(set(ids))
    assert "product_url_404_spending" in ids
    assert "employee_hid_product_while_ads_run" in ids
    assert "api_product_ok_public_page_broken" in ids
    assert "meta_snap_conversion_drop_same_time" in ids


def test_prompt_contract_is_today_first_and_root_cause_before_action():
    prompt = decision.FIRST_PASS_INSTRUCTIONS
    assert "Today" in prompt
    assert "Yesterday" in prompt
    assert "Day-2" in prompt
    assert prompt.index("Today") < prompt.index("Yesterday") < prompt.index("Day-2")
    assert "7d و30d كخط أساس" in prompt
    assert "ثم فقط Campaign action" in prompt
    assert "انخفاض Purchase لا يعني أن الإعلان سيئ" in prompt
    assert "قبل PAUSE" in prompt
    assert "لا توجد عتبة" in decision.SECOND_PASS_INSTRUCTIONS


def test_non_ads_diagnostic_recommendation_is_preserved_not_dropped():
    rec = recommendation("REVIEW_CHECKOUT", action_type="diagnostic")
    row, normalized = normalize(rec, pack=evidence())
    assert len(normalized.recommendations) == 1
    assert normalized.recommendations[0].action == "monitor"
    assert row["_decision_v3"]["recommended_action"] == "REVIEW_CHECKOUT"
    assert row["_decision_v3"]["action_type"] == "diagnostic"
    assert row["_decision_v3"]["executable"] is False
    assert row["_decision_v3"]["recommendation_execution_separated"] is True


def test_scale_write_is_blocked_when_product_capacity_is_not_verified():
    rec = recommendation("INCREASE_BUDGET", action_type="ads_write")
    row, normalized = normalize(rec, pack=evidence(healthy_product=False))
    assert normalized.recommendations[0].action == "monitor"
    assert row["_decision_v3"]["recommended_action"] == "INCREASE_BUDGET"
    assert row["_decision_v3"]["executable"] is False
    assert "product_availability_not_verified_for_scale" in row["_decision_v3"]["execution_blockers"]


def test_scale_write_can_be_executable_only_after_health_and_reviews():
    rec = recommendation("INCREASE_BUDGET", action_type="ads_write")
    row, normalized = normalize(rec, pack=evidence(healthy_product=True), reviewed=True)
    assert normalized.recommendations[0].action == "scale"
    assert row["_decision_v3"]["recommended_action"] == "INCREASE_BUDGET"
    assert row["_decision_v3"]["executable"] is True
    assert row["_decision_v3"]["execution_blockers"] == []


def test_ads_write_fails_closed_without_counterfactual_review():
    rec = recommendation("DECREASE_BUDGET", action_type="ads_write")
    row, normalized = normalize(rec, pack=evidence(healthy_product=True), reviewed=False)
    assert normalized.recommendations[0].action == "monitor"
    assert row["_decision_v3"]["executable"] is False
    assert "budget_owner_not_confirmed_in_final_review" in row["_decision_v3"]["execution_blockers"]
    assert "counterfactual_review_not_confirmed" in row["_decision_v3"]["execution_blockers"]


def test_legacy_translation_never_turns_diagnostic_into_budget_write():
    assert legacy_execution_action("REVIEW_CHECKOUT", entity_level="campaign") == "monitor"
    assert legacy_execution_action("TEST_NEW_HOOK", entity_level="ad") == "monitor"
    assert legacy_execution_action("CHANGE_PRODUCT_DESCRIPTION", entity_level="campaign") == "monitor"
    assert legacy_execution_action("INCREASE_BUDGET", entity_level="ad") == "monitor"


def test_multimodal_visuals_are_bounded_and_salla_product_scoped():
    pack = evidence(healthy_product=True)
    visuals = product_visuals(pack)
    assert len(visuals) == 2
    assert visuals[0]["product_id"] == "p-1"
    payload, count = responses_input({"test": True}, pack, include_images=True)
    assert count == 2
    content = payload[0]["content"]
    assert sum(1 for item in content if item["type"] == "input_image") == 2
    assert all(item.get("detail") == "low" for item in content if item["type"] == "input_image")
