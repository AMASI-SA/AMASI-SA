#!/usr/bin/env python3
"""Live pre-Production OpenAI evaluation for Decision Intelligence V3.

This script is intentionally outside the runtime worker. It calls the same V3
first/second-pass instructions and structured schemas against the 33 requested
synthetic scenarios, then checks broad safety/diagnostic invariants. It does not
use deterministic ROAS thresholds and does not require a database.

Usage:
  cd backend
  python scripts/campaign_ai_v3_live_eval.py

Environment:
  OPENAI_API_KEY                    required
  MEZAN_CAMPAIGN_AI_MODEL           optional (default gpt-5-mini)
  MEZAN_V3_EVAL_LIMIT               optional; default all 33
  MEZAN_V3_EVAL_START               optional; default 0
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
load_dotenv(BACKEND / ".env")

from campaign_ai_decision_review_schema_v3 import (  # noqa: E402
    DecisionReviewOutputV3,
    review_json_schema,
)
from campaign_ai_decision_schema_v3 import (  # noqa: E402
    DecisionOutputV3,
    v3_json_schema,
)
from campaign_ai_decision_v3 import (  # noqa: E402
    FIRST_PASS_INSTRUCTIONS,
    SECOND_PASS_INSTRUCTIONS,
)
from tests.campaign_ai_v3_scenarios import SCENARIOS  # noqa: E402


MODEL = os.environ.get("MEZAN_CAMPAIGN_AI_MODEL", os.environ.get("MEZAN_OPENAI_MODEL", "gpt-5-mini"))


def analysis_block(summary: str) -> dict[str, Any]:
    return {
        "status": "UNKNOWN",
        "summary": summary,
        "metrics": [],
        "signals": [],
        "limitations": [],
    }


def product_block(case: dict[str, Any]) -> dict[str, Any]:
    case_id = case["id"]
    url_health = "PRODUCT_URL_OK"
    visibility = "public_status_expected"
    inventory = "in_stock"
    variant = "available"
    if case_id == "product_url_404_spending":
        url_health = "PRODUCT_URL_BROKEN"
    if case_id == "api_product_ok_public_page_broken":
        url_health = "PRODUCT_PAGE_UNAVAILABLE"
    if case_id in {"product_hidden_spending", "employee_hid_product_while_ads_run"}:
        visibility = "not_public_or_inactive"
    if case_id == "product_out_of_stock_spending":
        inventory = "out_of_stock"
    if case_id == "low_stock_scale_candidate":
        inventory = "less_than_one_day_estimated"
    if case_id == "promoted_variant_out_of_stock":
        variant = "out_of_stock"
    return {
        "page_health": url_health,
        "url_health": url_health,
        "visibility": visibility,
        "inventory_status": inventory,
        "estimated_days_to_stockout": 0.5 if inventory == "less_than_one_day_estimated" else None,
        "estimated_stockout_at": None,
        "promoted_variant_status": variant,
        "product_title_analysis": "synthetic eval evidence",
        "product_description_analysis": "synthetic eval evidence",
        "hero_image_analysis": "synthetic eval evidence",
        "gallery_analysis": "synthetic eval evidence",
        "pricing_analysis": "synthetic eval evidence",
        "competitor_price_context": "synthetic eval evidence where scenario mentions market price",
        "internal_price_context": "synthetic eval evidence",
        "offer_analysis": "synthetic eval evidence",
        "reviews_analysis": "synthetic eval evidence",
        "shipping_analysis": "synthetic eval evidence",
        "ad_page_consistency": "scenario-specific",
        "detected_issues": [case["evidence"]],
        "recommendations": [],
        "priority": "high",
        "confidence": "medium",
    }


def scenario_payload(case: dict[str, Any]) -> dict[str, Any]:
    entity_level = "ad" if case["id"] in {
        "first_two_seconds_drop", "good_watch_low_ctr", "creative_fatigue",
        "high_watch_video_does_not_sell", "average_watch_profitable_sales",
        "promoted_variant_out_of_stock", "ad_old_price_product_new_price",
        "expired_discount_in_ad", "weak_hero_good_traffic",
    } else "campaign"
    entity_id = f"eval-{case['id']}"
    key = f"snapchat|{entity_level}|eval-account|{entity_id}"
    base_analysis = analysis_block(case["evidence"])
    return {
        "next_check_at": "2026-08-19T05:00:00+00:00",
        "current_market_context": {
            "local_datetime": "2026-08-19T01:00:00+03:00",
            "note": "context is explanatory evidence only",
        },
        "active_entities": [{
            "provider": "snapchat",
            "entity_level": entity_level,
            "account_id": "eval-account",
            "account_name": "Eval",
            "entity_id": entity_id,
            "entity_name": case["description"],
            "active": True,
            "current_daily_budget_native": 100 if entity_level == "campaign" else None,
            "execution_capabilities": {
                "allowed_actions": ["pause", "reduce", "scale", "monitor", "maintain"] if entity_level == "campaign" else ["pause", "monitor", "maintain"],
                "budget_actions_allowed": entity_level == "campaign",
                "direct_budget_available": entity_level == "campaign",
                "automatic_parent_retargeting_allowed": False,
            },
        }],
        "decision_evidence_v3": {
            "schema_version": "synthetic_live_eval_v3",
            "scenario": case,
            "temporal": {
                "contract": {
                    "reasoning_order": ["today", "yesterday", "day_minus_2", "baseline_7d", "baseline_30d"],
                    "three_day_aggregate_is_primary_rule": False,
                },
                "entities": {key: {
                    "today": base_analysis,
                    "yesterday": base_analysis,
                    "day_minus_2": base_analysis,
                    "baseline_7d": base_analysis,
                    "baseline_30d": base_analysis,
                }},
            },
            "funnel_and_creative": {"entities": {key: {"scenario_evidence": case["evidence"]}}},
            "product_intelligence": {"entities": {key: {"products": [{
                "product_id": "eval-product",
                "product_name": "منتج اختبار",
                "destination_url": "https://amasi-sa.com/eval-product",
                "canonical_product_url": "https://amasi-sa.com/eval-product",
                "visibility": product_block(case)["visibility"],
                "page_probe": {"status": product_block(case)["url_health"]},
                "inventory": {"status": product_block(case)["inventory_status"]},
            }]}}},
            "abandoned_carts": {"scenario_evidence": case["evidence"]},
            "store_checkout_payment_shipping": {"scenario_evidence": case["evidence"]},
            "cross_campaign_cross_platform": {"scenario_evidence": case["evidence"]},
            "marketing_knowledge": {"retrieved": [], "contract": "context only"},
            "root_cause_tree": [
                "A_DATA_QUALITY", "B_DELIVERY", "C_CREATIVE", "D_CLICK_INTENT",
                "E_DESTINATION_HEALTH", "F_PRODUCT_AVAILABILITY", "G_PRODUCT_PAGE",
                "H_ADD_TO_CART", "I_CHECKOUT", "J_PAYMENT", "K_SHIPPING",
                "L_INVENTORY", "M_PROFITABILITY",
            ],
            "contracts": {
                "diagnose_before_action": True,
                "recommendation_is_separate_from_execution": True,
                "context_is_explanatory_not_rule": True,
                "openai_is_final_marketing_decision_authority": True,
            },
        },
        "overall_store_profit_context": {"available": True, "synthetic": True},
        "executed_experiments": {"source": "owner_approved_executed_changes_only", "experiments": []},
        "legacy_campaign_history_context": {},
        "source_contract": {
            "snapchat_action_report_time": "conversion",
            "salla_child_attribution_allowed": False,
            "mezan_fallback_decisions_allowed": False,
        },
    }


def _actions(output: DecisionOutputV3) -> set[str]:
    return {item.recommended_action for item in output.recommendations}


def _roots(output: DecisionOutputV3) -> set[str]:
    return {item.root_cause_category for item in output.recommendations}


def evaluate_case(case: dict[str, Any], output: DecisionOutputV3) -> list[str]:
    failures = []
    actions = _actions(output)
    roots = _roots(output)
    forbidden = set(case.get("must_not_actions") or [])
    if actions & forbidden:
        failures.append(f"forbidden_actions:{sorted(actions & forbidden)}")
    acceptable_actions = set(case.get("acceptable_actions") or [])
    if acceptable_actions and not (actions & acceptable_actions):
        failures.append(f"no_expected_action_family:got={sorted(actions)}")
    acceptable_roots = set(case.get("acceptable_root_causes") or [])
    if acceptable_roots and not (roots & acceptable_roots):
        failures.append(f"no_expected_root_cause_family:got={sorted(roots)}")
    if not output.recommendations:
        failures.append("no_recommendation_or_explicit_no_action")
    for item in output.recommendations:
        if item.recommended_action.startswith("PAUSE_"):
            if not item.evidence_against or not item.what_would_change_the_decision:
                failures.append(f"pause_without_counterfactual:{item.recommendation_id}")
        if item.recommended_action == "TEST_NEW_CREATIVE" and item.creative_brief is None:
            failures.append(f"creative_test_without_brief:{item.recommendation_id}")
    return failures


async def run_case(client: AsyncOpenAI, case: dict[str, Any]) -> dict[str, Any]:
    payload = scenario_payload(case)
    first_response = await client.responses.create(
        model=MODEL,
        instructions=FIRST_PASS_INSTRUCTIONS,
        input=json.dumps(payload, ensure_ascii=False),
        max_output_tokens=24000,
        reasoning={"effort": "medium"},
        store=False,
        text={"format": {
            "type": "json_schema",
            "name": "campaign_decision_intelligence_v3_eval",
            "strict": True,
            "schema": v3_json_schema(),
        }},
    )
    first = DecisionOutputV3.model_validate_json(first_response.output_text)
    required = []
    entity = payload["active_entities"][0]
    if entity["entity_level"] in {"campaign", "ad_group"}:
        required = [
            "|".join((
                entity["provider"], entity["entity_level"], entity["account_id"], entity["entity_id"]
            ))
        ]
    review_response = await client.responses.create(
        model=MODEL,
        instructions=SECOND_PASS_INSTRUCTIONS,
        input=json.dumps({
            "next_check_at": payload["next_check_at"],
            "required_budget_owner_keys": required,
            "first_pass_recommendations": first.model_dump(),
            "active_entities": payload["active_entities"],
            "decision_evidence_v3": payload["decision_evidence_v3"],
            "overall_store_profit_context": payload["overall_store_profit_context"],
            "executed_experiments": payload["executed_experiments"],
            "current_market_context": payload["current_market_context"],
        }, ensure_ascii=False, default=str),
        max_output_tokens=24000,
        reasoning={"effort": "medium"},
        store=False,
        text={"format": {
            "type": "json_schema",
            "name": "campaign_decision_review_v3_eval",
            "strict": True,
            "schema": review_json_schema(),
        }},
    )
    review = DecisionReviewOutputV3.model_validate_json(review_response.output_text)
    failures = evaluate_case(case, review.final_decision)
    if required and not set(required).issubset(set(review.reviewed_budget_owner_keys)):
        failures.append("required_budget_owner_not_reviewed")
    final_ids = {item.recommendation_id for item in review.final_decision.recommendations}
    if not final_ids.issubset(set(review.counterfactual_reviewed_recommendation_ids)):
        failures.append("final_recommendation_missing_counterfactual_review")
    return {
        "id": case["id"],
        "description": case["description"],
        "actions": sorted(_actions(review.final_decision)),
        "roots": sorted(_roots(review.final_decision)),
        "failures": failures,
        "summary": review.final_decision.summary,
    }


async def main_async() -> int:
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        print("V3_LIVE_EVAL_REFUSED: OPENAI_API_KEY missing")
        return 2
    start = max(0, int(os.environ.get("MEZAN_V3_EVAL_START", "0")))
    requested_limit = int(os.environ.get("MEZAN_V3_EVAL_LIMIT", str(len(SCENARIOS))))
    cases = SCENARIOS[start:start + max(1, requested_limit)]
    client = AsyncOpenAI(api_key=api_key, max_retries=1, timeout=180.0)
    results = []
    try:
        for index, case in enumerate(cases, start=start + 1):
            print(f"[{index}/{len(SCENARIOS)}] {case['id']} ...", flush=True)
            try:
                result = await run_case(client, case)
            except Exception as exc:
                result = {
                    "id": case["id"],
                    "description": case["description"],
                    "actions": [],
                    "roots": [],
                    "failures": [f"eval_runtime_error:{type(exc).__name__}"],
                    "summary": "",
                }
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        await client.close()

    failed = [row for row in results if row["failures"]]
    report = {
        "model": MODEL,
        "start": start,
        "evaluated": len(results),
        "total_corpus": len(SCENARIOS),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "failed_ids": [row["id"] for row in failed],
        "all_requested_cases_passed": not failed,
    }
    print("\nV3_LIVE_EVAL_SUMMARY")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
