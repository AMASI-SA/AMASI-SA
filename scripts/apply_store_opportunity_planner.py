from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MODULE = r'''"""Read-only store-wide diagnosis and profit opportunity planner for Campaign AI.

This layer turns verified Mezan profit context, campaign facts, and monthly causal
memory into a deterministic planning envelope. It prioritizes where evidence
should be investigated; it does not mutate campaigns, products, prices, stock,
or Salla, and it never converts missing evidence into a business fact.
"""
from __future__ import annotations

from typing import Any

CONTRACT_VERSION = "store_opportunity_planner_v1"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _priority(rank: int) -> str:
    return {1: "critical", 2: "high", 3: "medium"}.get(rank, "low")


def build_store_opportunity_plan(
    *,
    goal_context: dict[str, Any] | None,
    business_profit: dict[str, Any] | None,
    monthly_memory: dict[str, Any] | None,
    candidates: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Build a bounded store-wide plan from observed evidence only."""
    goal = goal_context if isinstance(goal_context, dict) else {}
    profit = business_profit if isinstance(business_profit, dict) else {}
    memory = monthly_memory if isinstance(monthly_memory, dict) else {}
    rows = [row for row in (candidates or []) if isinstance(row, dict)]

    status = str(goal.get("status") or "unknown")
    phase = str(goal.get("phase") or "unknown")
    gap = _number(goal.get("remaining_to_target_sar"))
    required_daily = _number(goal.get("required_daily_net_profit_sar"))
    projected = _number(goal.get("projected_month_end_net_profit_sar"))
    target = _number(goal.get("minimum_net_profit_sar"))
    accounting_known = goal.get("profit_accounting_quality_known") is True
    accounting_complete = goal.get("profit_accounting_complete") is True

    zero_purchase_spend = 0.0
    active_spend = 0.0
    strong_signals = 0
    weak_signals = 0
    incomplete_entities = 0
    for row in rows:
        spend = max(0.0, _number(row.get("spend_sar")) or 0.0)
        purchases = int(max(0.0, _number(row.get("purchases")) or 0.0))
        roas = _number(row.get("roas"))
        cpa = _number(row.get("cpa_sar"))
        active_spend += spend
        if row.get("data_complete") is not True:
            incomplete_entities += 1
        if purchases == 0:
            zero_purchase_spend += spend
        if row.get("data_complete") is True and purchases >= 3:
            if (roas is not None and roas >= 2.5) or (cpa is not None and cpa <= 56.25):
                strong_signals += 1
            if (roas is not None and roas < 1.5) or (cpa is not None and cpa >= 84.375):
                weak_signals += 1

    repeated_failures = (
        (memory.get("repeated_patterns") or {}).get("repeated_failed_or_uncertain")
        if isinstance(memory.get("repeated_patterns"), dict)
        else []
    ) or []

    lanes: list[dict[str, Any]] = []
    rank = 1

    if not accounting_known or not accounting_complete:
        lanes.append({
            "lane": "profit_data_quality",
            "priority": "critical",
            "state": "blocked",
            "why": "صحة/اكتمال محاسبة الربح غير مثبتة؛ يمنع التخطيط المالي للتوسع قبل إصلاح الدليل.",
            "evidence": [
                f"profit_accounting_quality_known={accounting_known}",
                f"profit_accounting_complete={accounting_complete}",
            ],
            "next_evidence": ["إكمال تكلفة المنتجات والطلبات غير المكتملة وإعادة بناء Profit Envelope"],
        })
        rank += 1

    if zero_purchase_spend > 0:
        lanes.append({
            "lane": "stop_verified_ad_waste",
            "priority": _priority(rank),
            "state": "ready_for_analysis",
            "why": "يوجد صرف على كيانات لم تسجل مشتريات في نافذة الأدلة الحالية؛ افحص الهدر قبل شراء نمو إضافي.",
            "evidence": [f"zero_purchase_spend_sar={round(zero_purchase_spend, 2)}"],
            "next_evidence": ["فحص مستوى الإعلان/المجموعة قبل أي إيقاف للحملة الأم", "التحقق من اكتمال التحويلات قبل التنفيذ"],
        })
        rank += 1

    if accounting_complete and strong_signals > 0:
        lanes.append({
            "lane": "protect_and_scale_proven_demand",
            "priority": _priority(rank),
            "state": "ready_for_analysis",
            "why": "توجد إشارات أداء مكتملة ذات مشتريات كافية؛ يمكن تحليل التوسع فقط إذا زاد صافي الربح ويحمي مسار الهدف.",
            "evidence": [f"strong_complete_campaign_signals={strong_signals}"],
            "next_evidence": ["تطبيق بوابات scale الحالية", "قياس أثر القرار قبل توسعة ثانية"],
        })
        rank += 1

    behind = status == "behind_target" or (gap is not None and gap > 0 and projected is not None and target is not None and projected < target)
    if behind:
        lanes.extend([
            {
                "lane": "conversion_friction",
                "priority": _priority(rank),
                "state": "evidence_required",
                "why": "المتجر دون مسار هدف الربح؛ لا يجوز افتراض أن المشكلة إعلانية فقط. يلزم فحص رحلة المنتج والسلة والدفع.",
                "evidence": [f"remaining_profit_gap_sar={round(gap, 2) if gap is not None else None}"],
                "next_evidence": ["GA4 funnel by product", "abandoned carts by campaign/product", "checkout/payment/shipping friction"],
            },
            {
                "lane": "product_margin_and_offer",
                "priority": _priority(rank + 1),
                "state": "evidence_required",
                "why": "سد فجوة الربح قد يأتي من المنتج/السعر/الباقة بدل زيادة الصرف؛ لا يوجد في هذا العقد دليل منتج تفصيلي كافٍ بعد.",
                "evidence": [],
                "next_evidence": ["product-level Mezan profit", "verified cost and margin", "price/offer comparison", "product page quality"],
            },
            {
                "lane": "inventory_readiness",
                "priority": _priority(rank + 2),
                "state": "evidence_required",
                "why": "أي نمو يجب أن يراعي المخزون وقرب النفاد؛ لا تعتبر توفر المخزون حقيقة قبل قراءة مصدر المخزون.",
                "evidence": [],
                "next_evidence": ["available/reserved quantity", "stockout risk", "lead time / replenishment"],
            },
        ])
        rank += 3

    if behind and strong_signals == 0:
        lanes.append({
            "lane": "new_growth_engine",
            "priority": _priority(rank),
            "state": "evidence_required",
            "why": "لا توجد حاليًا إشارة مكتملة كافية تثبت أن توسيع الحملات الحالية وحده قادر على سد فجوة الربح؛ ابحث عن محرك ربح جديد بدل إجبار الصرف.",
            "evidence": [f"strong_complete_campaign_signals={strong_signals}"],
            "next_evidence": ["Saudi Product Radar", "bundle/price opportunities", "seasonal demand", "new product test economics"],
        })

    if not lanes:
        lanes.append({
            "lane": "protect_profit_path",
            "priority": "medium",
            "state": "ready_for_analysis",
            "why": "لا توجد إشارة حاسمة لتغيير واسع؛ احمِ مسار الربح واجمع أدلة إضافية قبل التوسع.",
            "evidence": [f"goal_status={status}"],
            "next_evidence": ["continue 3/7/30 day measurement"],
        })

    evidence_gaps = []
    for lane in lanes:
        if lane["state"] == "evidence_required":
            evidence_gaps.extend(lane.get("next_evidence") or [])
    evidence_gaps = list(dict.fromkeys(evidence_gaps))[:20]

    return {
        "contract_version": CONTRACT_VERSION,
        "read_only": True,
        "objective": "Maximize verified net profit while protecting the owner's monthly minimum profit floor.",
        "store_state": {
            "goal_status": status,
            "phase": phase,
            "target_net_profit_sar": target,
            "remaining_profit_gap_sar": gap,
            "required_daily_net_profit_sar": required_daily,
            "projected_month_end_net_profit_sar": projected,
            "profit_accounting_known": accounting_known,
            "profit_accounting_complete": accounting_complete,
        },
        "diagnosis": {
            "active_candidate_entities": len(rows),
            "active_candidate_spend_sar": round(active_spend, 2),
            "zero_purchase_spend_sar": round(zero_purchase_spend, 2),
            "strong_complete_campaign_signals": strong_signals,
            "weak_complete_campaign_signals": weak_signals,
            "incomplete_entity_count": incomplete_entities,
            "repeated_failed_or_uncertain_actions": len(repeated_failures),
            "business_profit_available": profit.get("available") is True,
        },
        "opportunity_lanes": lanes[:10],
        "evidence_gaps": evidence_gaps,
        "guardrails": [
            "Do not optimize sales, ROAS, or spend as standalone goals; optimize verified net profit.",
            "Do not treat evidence_required lanes as diagnosed facts.",
            "Do not infer product, inventory, page, price, or market problems without their source evidence.",
            "Do not claim causality from correlation or provider execution completion.",
            "This planner performs no provider, product, price, inventory, or Salla writes.",
        ],
    }


__all__ = ["CONTRACT_VERSION", "build_store_opportunity_plan"]
'''

TEST = r'''from campaign_ai_store_opportunity_planner import build_store_opportunity_plan


def _goal(**overrides):
    base = {
        "status": "behind_target",
        "phase": "recover_profit_gap",
        "minimum_net_profit_sar": 100000,
        "remaining_to_target_sar": 42000,
        "required_daily_net_profit_sar": 4200,
        "projected_month_end_net_profit_sar": 78000,
        "profit_accounting_quality_known": True,
        "profit_accounting_complete": True,
    }
    base.update(overrides)
    return base


def test_behind_target_opens_store_wide_evidence_lanes():
    plan = build_store_opportunity_plan(
        goal_context=_goal(),
        business_profit={"available": True},
        monthly_memory={},
        candidates=[],
    )
    lanes = {item["lane"]: item for item in plan["opportunity_lanes"]}
    assert lanes["conversion_friction"]["state"] == "evidence_required"
    assert lanes["product_margin_and_offer"]["state"] == "evidence_required"
    assert lanes["inventory_readiness"]["state"] == "evidence_required"
    assert lanes["new_growth_engine"]["state"] == "evidence_required"


def test_unknown_accounting_blocks_financial_planning():
    plan = build_store_opportunity_plan(
        goal_context=_goal(profit_accounting_quality_known=False, profit_accounting_complete=False),
        business_profit={"available": True},
        monthly_memory={},
        candidates=[],
    )
    first = plan["opportunity_lanes"][0]
    assert first["lane"] == "profit_data_quality"
    assert first["state"] == "blocked"


def test_zero_purchase_spend_is_observed_not_fabricated():
    plan = build_store_opportunity_plan(
        goal_context=_goal(),
        business_profit={"available": True},
        monthly_memory={},
        candidates=[
            {"spend_sar": 120, "purchases": 0, "data_complete": True},
            {"spend_sar": 30, "purchases": 1, "data_complete": True},
        ],
    )
    assert plan["diagnosis"]["zero_purchase_spend_sar"] == 120.0
    assert any(x["lane"] == "stop_verified_ad_waste" for x in plan["opportunity_lanes"])


def test_proven_demand_requires_complete_accounting_and_campaign_evidence():
    plan = build_store_opportunity_plan(
        goal_context=_goal(status="on_track", phase="protect_target_path", remaining_to_target_sar=10000),
        business_profit={"available": True},
        monthly_memory={},
        candidates=[{"spend_sar": 300, "purchases": 5, "roas": 3.1, "cpa_sar": 60, "data_complete": True}],
    )
    assert plan["diagnosis"]["strong_complete_campaign_signals"] == 1
    assert any(x["lane"] == "protect_and_scale_proven_demand" for x in plan["opportunity_lanes"])


def test_incomplete_campaign_signal_does_not_become_scale_evidence():
    plan = build_store_opportunity_plan(
        goal_context=_goal(),
        business_profit={"available": True},
        monthly_memory={},
        candidates=[{"spend_sar": 300, "purchases": 5, "roas": 4, "data_complete": False}],
    )
    assert plan["diagnosis"]["strong_complete_campaign_signals"] == 0


def test_repeated_failed_actions_are_counted_from_monthly_memory():
    plan = build_store_opportunity_plan(
        goal_context=_goal(),
        business_profit={"available": True},
        monthly_memory={"repeated_patterns": {"repeated_failed_or_uncertain": [{"recommendation_id": "a", "failed_or_uncertain_executions": 2}]}},
        candidates=[],
    )
    assert plan["diagnosis"]["repeated_failed_or_uncertain_actions"] == 1


def test_plan_is_read_only_and_marks_evidence_gaps():
    plan = build_store_opportunity_plan(
        goal_context=_goal(),
        business_profit={"available": True},
        monthly_memory={},
        candidates=[],
    )
    assert plan["read_only"] is True
    assert "product-level Mezan profit" in plan["evidence_gaps"]
    assert any("performs no" in item for item in plan["guardrails"])
'''

module_path = ROOT / "backend" / "campaign_ai_store_opportunity_planner.py"
module_path.write_text(MODULE, encoding="utf-8")
print(f"wrote {module_path.relative_to(ROOT)}")

test_path = ROOT / "backend" / "tests" / "test_campaign_ai_store_opportunity_planner.py"
test_path.write_text(TEST, encoding="utf-8")
print(f"wrote {test_path.relative_to(ROOT)}")

monitor = ROOT / "backend" / "campaign_ai_monitor_legacy.py"
text = monitor.read_text(encoding="utf-8")

old = '''async def _ask_openai(\n    candidates: list[dict[str, Any]],\n    *,\n    now: datetime,\n    campaign_history: dict[str, Any],\n    prior_decisions: dict[str, Any],\n    business_profit: dict[str, Any],\n) -> RecommendationOutput:'''
new = '''async def _ask_openai(\n    candidates: list[dict[str, Any]],\n    *,\n    now: datetime,\n    campaign_history: dict[str, Any],\n    prior_decisions: dict[str, Any],\n    business_profit: dict[str, Any],\n    store_opportunity_plan: dict[str, Any],\n) -> RecommendationOutput:'''
if old not in text:
    raise SystemExit("ask_openai signature anchor missing")
text = text.replace(old, new, 1)

old = '''                "mezan_overall_profit_and_loss": business_profit,\n                "prior_mezan_ai_decisions": prior_decisions,'''
new = '''                "mezan_overall_profit_and_loss": business_profit,\n                "store_wide_diagnosis_and_opportunity_plan": store_opportunity_plan,\n                "prior_mezan_ai_decisions": prior_decisions,'''
if old not in text:
    raise SystemExit("openai input anchor missing")
text = text.replace(old, new, 1)

old = '''            current_month_memory = None\n            errors.append({\n                "source": "monthly_causal_memory",\n                "code": _text(type(exc).__name__, limit=100),\n            })\n        if not candidates:'''
new = '''            current_month_memory = None\n            errors.append({\n                "source": "monthly_causal_memory",\n                "code": _text(type(exc).__name__, limit=100),\n            })\n        try:\n            from campaign_ai_store_opportunity_planner import build_store_opportunity_plan\n            store_opportunity_plan = build_store_opportunity_plan(\n                goal_context=business_profit.get("monthly_profit_goal"),\n                business_profit=business_profit,\n                monthly_memory=current_month_memory,\n                candidates=candidates,\n            )\n        except Exception as exc:\n            store_opportunity_plan = {\n                "contract_version": "store_opportunity_planner_v1",\n                "read_only": True,\n                "available": False,\n                "reason": type(exc).__name__,\n            }\n            errors.append({\n                "source": "store_opportunity_planner",\n                "code": _text(type(exc).__name__, limit=100),\n            })\n        if not candidates:'''
if old not in text:
    raise SystemExit("planner build anchor missing")
text = text.replace(old, new, 1)

old = '''                    campaign_history=campaign_history,\n                    prior_decisions=prior_decisions,\n                    business_profit=business_profit,\n                )'''
new = '''                    campaign_history=campaign_history,\n                    prior_decisions=prior_decisions,\n                    business_profit=business_profit,\n                    store_opportunity_plan=store_opportunity_plan,\n                )'''
if old not in text:
    raise SystemExit("ask_openai caller anchor missing")
text = text.replace(old, new, 1)

old = '''            "monthly_causal_memory": current_month_memory,\n            "monthly_causal_memory_available": current_month_memory is not None,\n        }'''
new = '''            "monthly_causal_memory": current_month_memory,\n            "monthly_causal_memory_available": current_month_memory is not None,\n            "store_opportunity_plan": store_opportunity_plan,\n            "store_opportunity_plan_available": store_opportunity_plan.get("available") is not False,\n        }'''
if old not in text:
    raise SystemExit("snapshot anchor missing")
text = text.replace(old, new, 1)

old = '''                "ابدأ بأثر الحملات على صافي ربح أو خسارة ميزان اليوم ثم نوافذ 3 و7 و30 يومًا، "'''
new = '''                "ابدأ بأثر الحملات على صافي ربح أو خسارة ميزان اليوم ثم نوافذ 3 و7 و30 يومًا، "\n                "واستخدم خطة store-wide كخريطة أولويات فقط: الحقول evidence_required ليست تشخيصًا مثبتًا، "\n                "بل تطلب منك تحديد الدليل الناقص قبل اقتراح حل على المنتج أو السعر أو المخزون أو صفحة المنتج. "'''
if old not in text:
    raise SystemExit("instruction anchor missing")
text = text.replace(old, new, 1)

monitor.write_text(text, encoding="utf-8")
print(f"patched {monitor.relative_to(ROOT)}")
