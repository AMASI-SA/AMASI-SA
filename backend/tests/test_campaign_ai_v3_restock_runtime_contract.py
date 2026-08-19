import campaign_ai_decision_v3 as decision
import campaign_ai_monitor  # noqa: F401 - installs the Production V3 runtime bindings
from campaign_ai_decision_prompts_v3 import (
    FIRST_PASS_INSTRUCTIONS,
    SECOND_PASS_INSTRUCTIONS,
)
from campaign_ai_decision_schema_v3 import DecisionOutputV3, DecisionRecommendationV3
import campaign_ai_execution_alignment as alignment
import campaign_ai_monitor_legacy as legacy


def _analysis():
    return {
        "status": "UNKNOWN",
        "summary": "not enough detail for this focused contract test",
        "metrics": [],
        "signals": [],
        "limitations": [],
    }


def _product_analysis():
    return {
        "page_health": "PRODUCT_URL_OK",
        "url_health": "PRODUCT_URL_OK",
        "visibility": "public_status_expected",
        "inventory_status": "low_stock_estimated",
        "estimated_days_to_stockout": 0.5,
        "estimated_stockout_at": None,
        "promoted_variant_status": "available",
        "product_title_analysis": "ok",
        "product_description_analysis": "ok",
        "hero_image_analysis": "ok",
        "gallery_analysis": "ok",
        "pricing_analysis": "ok",
        "competitor_price_context": "unavailable",
        "internal_price_context": "available",
        "offer_analysis": "ok",
        "reviews_analysis": "unavailable",
        "shipping_analysis": "ok",
        "ad_page_consistency": "ok",
        "detected_issues": ["low stock while demand is commercially promising"],
        "recommendations": ["restock before scaling spend"],
        "priority": "high",
        "confidence": "medium",
    }


def _restock_recommendation():
    return DecisionRecommendationV3.model_validate({
        "recommendation_id": "snapchat:campaign:acct-1:campaign-1",
        "provider": "snapchat",
        "entity_level": "campaign",
        "entity_id": "campaign-1",
        "entity_name": "Campaign",
        "account_id": "acct-1",
        "account_name": "Account",
        "parent_name": None,
        "product_id": "p-1",
        "destination_url": "https://amasi-sa.com/p/1",
        "recommendation_type": "inventory",
        "action_type": "operational_alert",
        "executable": False,
        "recommended_action": "RESTOCK_PRODUCT",
        "change_percent": None,
        "priority": "high",
        "confidence": "medium",
        "title": "زيادة مخزون المنتج قبل التوسع",
        "diagnosis": "المخزون هو قيد النمو بينما الطلب التجاري واعد.",
        "root_cause_category": "INVENTORY",
        "primary_hypothesis": None,
        "secondary_hypotheses": [],
        "evidence_for": ["low stock", "commercial demand signal"],
        "evidence_against": ["replenishment lead time not yet known"],
        "today_analysis": _analysis(),
        "yesterday_analysis": _analysis(),
        "day_minus_2_analysis": _analysis(),
        "baseline_7d": _analysis(),
        "baseline_30d": _analysis(),
        "funnel_analysis": _analysis(),
        "video_analysis": _analysis(),
        "creative_analysis": _analysis(),
        "product_page_analysis": _product_analysis(),
        "inventory_analysis": _analysis(),
        "abandoned_cart_analysis": _analysis(),
        "cross_campaign_analysis": _analysis(),
        "cross_platform_analysis": _analysis(),
        "business_context": _analysis(),
        "external_knowledge_used": [],
        "creative_brief": None,
        "proposed_product_changes": [{
            "field": "inventory",
            "current": "low_stock_estimated",
            "proposed": "replenish inventory before scaling paid demand",
            "reason": "avoid stockout becoming the growth bottleneck",
            "requires_owner_approval": True,
        }],
        "why": "protect proven demand and unlock safe scaling",
        "expected_effect": "more sellable capacity without confusing inventory with campaign failure",
        "risks": ["over-ordering if demand signal is too young"],
        "what_would_change_the_decision": ["demand signal disappears before reorder commitment"],
        "recommended_wait_hours": 5,
        "observation_plan": "confirm sales velocity, lead time and safety stock before quantity commitment",
        "success_criteria": ["inventory covers the intended growth window"],
        "guardrail": "inventory action requires owner/operations approval",
        "next_check_at": "2026-08-19T05:00:00+00:00",
    })


def test_production_runtime_uses_same_prompts_as_live_eval():
    assert decision.FIRST_PASS_INSTRUCTIONS == FIRST_PASS_INSTRUCTIONS
    assert decision.SECOND_PASS_INSTRUCTIONS == SECOND_PASS_INSTRUCTIONS


def test_restock_is_operational_and_never_an_ads_write():
    assert "RESTOCK_PRODUCT" in decision.OPERATIONAL_ACTIONS
    rec = _restock_recommendation()
    row = {
        "provider": "snapchat",
        "entity_level": "campaign",
        "account_id": "acct-1",
        "account_name": "Account",
        "entity_id": "campaign-1",
        "entity_name": "Campaign",
        "active": True,
        "current_daily_budget_native": 100,
    }
    pack = {
        "product_intelligence": {
            "entities": {
                "snapchat|campaign|acct-1|campaign-1": {
                    "advertised_destination_url": "https://amasi-sa.com/p/1",
                    "products": [{
                        "product_id": "p-1",
                        "destination_url": "https://amasi-sa.com/p/1",
                        "visibility": "public_status_expected",
                        "page_probe": {"status": "PRODUCT_URL_OK"},
                        "inventory": {"status": "low_stock_estimated"},
                    }],
                }
            }
        },
        "marketing_knowledge": {"retrieved": []},
    }
    output = DecisionOutputV3(summary="ok", recommendations=[rec], limitations=[])
    normalized, _ = decision._normalize_decision_output(
        output,
        [row],
        evidence_pack=pack,
        next_check_at="2026-08-19T05:00:00+00:00",
        reviewed_budget_owners={"snapchat|campaign|acct-1|campaign-1"},
        counterfactual_reviewed={"snapchat:campaign:acct-1:campaign-1"},
        alignment=alignment,
        legacy=legacy,
    )
    assert normalized.recommendations[0].action == "monitor"
    assert row["_decision_v3"]["recommended_action"] == "RESTOCK_PRODUCT"
    assert row["_decision_v3"]["action_type"] == "operational_alert"
    assert row["_decision_v3"]["executable"] is False
