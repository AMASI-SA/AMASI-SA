from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

module = r'''"""Opportunity-to-Profit Planner for Mezan Store Profit Manager.

This read-only layer converts the Daily Saudi Product Brief into a ranked set of
profit opportunities. It never promotes unknown economics to measured profit and
never performs product, price, supplier, inventory, catalog, or campaign writes.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

CONTRACT_VERSION = "opportunity_to_profit_planner_v1"
PLAN_COLLECTION = "mezan_opportunity_to_profit_plan_v1"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _text(value: Any, limit: int = 220) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _confidence_rank(value: Any) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(_text(value, 20).lower(), 0)


def _risk_penalty(value: Any) -> int:
    return {
        "normal": 0,
        "early_uncertainty": 1,
        "late_entry": 2,
        "trend_decay": 4,
        "unknown": 3,
    }.get(_text(value, 60).lower(), 3)


def _economics_status(item: dict[str, Any]) -> str:
    profit = _number(item.get("estimated_monthly_net_profit_sar"))
    profit_per_order = _number(item.get("estimated_net_profit_per_order_sar"))
    orders = _number(item.get("estimated_monthly_orders"))
    measured = _text(item.get("evidence_status"), 40) == "measured"
    if measured and profit is not None and profit_per_order is not None and orders is not None:
        return "measured"
    if profit is not None or profit_per_order is not None or orders is not None:
        return "partial"
    return "unknown"


def _priority_score(item: dict[str, Any], *, monthly_gap_sar: float | None) -> float:
    trend_score = _number(item.get("saudi_trend_score")) or 0.0
    opportunity_score = _number(item.get("saudi_opportunity_score")) or 0.0
    profit = max(0.0, _number(item.get("estimated_monthly_net_profit_sar")) or 0.0)
    confidence = _confidence_rank(item.get("confidence"))
    risk_penalty = _risk_penalty(item.get("risk"))
    economics = _economics_status(item)

    coverage_component = 0.0
    if monthly_gap_sar is not None and monthly_gap_sar > 0 and profit > 0:
        coverage_component = min(30.0, (profit / monthly_gap_sar) * 30.0)
    elif profit > 0:
        coverage_component = min(20.0, profit / 1000.0)

    score = (
        trend_score * 0.30
        + opportunity_score * 0.25
        + coverage_component
        + confidence * 5.0
        + (15.0 if economics == "measured" else 5.0 if economics == "partial" else 0.0)
        - risk_penalty * 7.0
    )
    return round(max(0.0, min(100.0, score)), 2)


def _blockers(item: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    economics = _economics_status(item)
    confidence = _text(item.get("confidence"), 20) or "low"
    risk = _text(item.get("risk"), 60) or "unknown"
    if economics != "measured":
        blockers.append("economics_not_fully_measured")
    if confidence == "low":
        blockers.append("low_evidence_confidence")
    if risk in {"late_entry", "trend_decay"}:
        blockers.append(risk)
    if _number(item.get("estimated_monthly_net_profit_sar")) is None:
        blockers.append("monthly_profit_contribution_unknown")
    return blockers


def _next_evidence(item: dict[str, Any]) -> list[str]:
    needs: list[str] = []
    if _text(item.get("evidence_status"), 40) != "measured":
        needs.append("validate Saudi demand with repeated measured evidence")
    if _number(item.get("estimated_net_profit_per_order_sar")) is None:
        needs.append("verify landed/product cost and net profit per order")
    if _number(item.get("estimated_monthly_orders")) is None:
        needs.append("estimate monthly order capacity from Saudi evidence")
    risk = _text(item.get("risk"), 60)
    if risk == "late_entry":
        needs.append("revalidate remaining trend window before inventory commitment")
    elif risk == "trend_decay":
        needs.append("confirm whether demand decay is reversible before any growth investment")
    return needs[:6]


def _plan_item(item: dict[str, Any], *, lane: str, monthly_gap_sar: float | None) -> dict[str, Any]:
    profit = _number(item.get("estimated_monthly_net_profit_sar"))
    gap_coverage = None
    if monthly_gap_sar is not None and monthly_gap_sar > 0 and profit is not None:
        gap_coverage = round(min(1.0, max(0.0, profit) / monthly_gap_sar), 4)
    blockers = _blockers(item)
    economics = _economics_status(item)
    readiness = (
        "analysis_ready"
        if economics == "measured" and not blockers
        else "evidence_required"
    )
    return {
        "lane": lane,
        "product_key": _text(item.get("product_key"), 180),
        "product_name": _text(item.get("product_name") or item.get("product_key"), 220),
        "store_product_id": _text(item.get("store_product_id"), 160) or None,
        "priority_score": _priority_score(item, monthly_gap_sar=monthly_gap_sar),
        "readiness": readiness,
        "economics_status": economics,
        "saudi_trend_score": _number(item.get("saudi_trend_score")),
        "saudi_opportunity_score": _number(item.get("saudi_opportunity_score")),
        "state": _text(item.get("state"), 40) or "unknown",
        "stage": _text(item.get("stage"), 60) or "unknown",
        "confidence": _text(item.get("confidence"), 20) or "low",
        "risk": _text(item.get("risk"), 60) or "unknown",
        "estimated_monthly_net_profit_sar": profit,
        "estimated_net_profit_per_order_sar": _number(item.get("estimated_net_profit_per_order_sar")),
        "estimated_monthly_orders": _number(item.get("estimated_monthly_orders")),
        "estimated_profit_gap_coverage_ratio": gap_coverage,
        "blockers": blockers,
        "next_evidence": _next_evidence(item),
    }


def build_opportunity_to_profit_plan(
    *,
    as_of: date,
    daily_brief: dict[str, Any],
) -> dict[str, Any]:
    """Turn one Daily Product Brief into a bounded profit-priority plan."""
    brief = daily_brief if isinstance(daily_brief, dict) else {}
    headline = brief.get("headline") if isinstance(brief.get("headline"), dict) else {}
    monthly_gap = _number(headline.get("monthly_profit_gap_sar"))

    measured = [x for x in brief.get("measured_new_opportunities", []) if isinstance(x, dict)]
    evidence_required = [x for x in brief.get("evidence_required", []) if isinstance(x, dict)]
    rising_existing = [x for x in brief.get("top_rising_existing_products", []) if isinstance(x, dict)]
    watch_existing = [x for x in brief.get("products_to_watch", []) if isinstance(x, dict)]

    candidates: list[dict[str, Any]] = []
    candidates.extend(_plan_item(x, lane="new_measured_growth", monthly_gap_sar=monthly_gap) for x in measured)
    candidates.extend(_plan_item(x, lane="new_evidence_required", monthly_gap_sar=monthly_gap) for x in evidence_required)
    candidates.extend(_plan_item(x, lane="protect_rising_existing", monthly_gap_sar=monthly_gap) for x in rising_existing)
    candidates.extend(_plan_item(x, lane="protect_or_decelerate_risk", monthly_gap_sar=monthly_gap) for x in watch_existing)
    candidates.sort(
        key=lambda x: (
            x["readiness"] == "analysis_ready",
            x["priority_score"],
            x["estimated_monthly_net_profit_sar"] or -1,
        ),
        reverse=True,
    )

    measured_profit = sum(
        max(0.0, _number(x.get("estimated_monthly_net_profit_sar")) or 0.0)
        for x in candidates
        if x["economics_status"] == "measured"
        and x["lane"] in {"new_measured_growth", "protect_rising_existing"}
    )
    remaining_gap = max(0.0, monthly_gap - measured_profit) if monthly_gap is not None else None

    if monthly_gap is None:
        strategy = "goal_context_missing"
    elif monthly_gap <= 0:
        strategy = "protect_monthly_profit_floor"
    elif measured_profit >= monthly_gap:
        strategy = "protect_and_sequence_measured_profit"
    elif measured_profit > 0:
        strategy = "close_gap_with_measured_profit_then_validate_next_best"
    else:
        strategy = "validate_profit_economics_before_growth"

    external = brief.get("external_discovery") if isinstance(brief.get("external_discovery"), dict) else {}
    return {
        "contract_version": CONTRACT_VERSION,
        "as_of": as_of.isoformat(),
        "market": "Saudi Arabia",
        "market_priority": "saudi_first",
        "read_only": True,
        "objective": "Close the verified monthly net-profit gap without sacrificing the owner's profit floor.",
        "strategy": strategy,
        "monthly_profit_gap_sar": monthly_gap,
        "measured_candidate_profit_sar": round(measured_profit, 2),
        "remaining_profit_gap_after_measured_candidates_sar": (
            round(remaining_gap, 2) if remaining_gap is not None else None
        ),
        "ranked_opportunities": candidates[:20],
        "analysis_ready": [x for x in candidates if x["readiness"] == "analysis_ready"][:10],
        "evidence_required": [x for x in candidates if x["readiness"] != "analysis_ready"][:10],
        "external_discovery": {
            "allowed": bool(external.get("allowed")),
            "reason": _text(external.get("reason"), 120) or None,
            "target_customer_market": _text(external.get("target_customer_market"), 80) or "Saudi Arabia",
        },
        "guardrails": [
            "Measured and unknown economics are never mixed.",
            "A high trend score cannot override missing profit economics.",
            "Late-entry and decaying trends are penalized before inventory commitment.",
            "External discovery remains a fallback for Saudi-market product discovery only.",
            "This planner performs no product, price, inventory, supplier, catalog, or campaign writes.",
        ],
    }


async def ensure_indexes(db: Any) -> None:
    await db[PLAN_COLLECTION].create_index(
        [("user_id", 1), ("as_of", 1)], unique=True,
        name="opportunity_to_profit_user_day_unique",
    )


async def refresh_opportunity_to_profit_plan(
    db: Any,
    user_id: str,
    *,
    as_of: date,
    daily_brief: dict[str, Any],
) -> dict[str, Any]:
    await ensure_indexes(db)
    plan = build_opportunity_to_profit_plan(as_of=as_of, daily_brief=daily_brief)
    now_iso = datetime.now(timezone.utc).isoformat()
    document = {**plan, "user_id": user_id, "updated_at": now_iso}
    await db[PLAN_COLLECTION].update_one(
        {"user_id": user_id, "as_of": plan["as_of"]},
        {"$set": document, "$setOnInsert": {"created_at": now_iso}},
        upsert=True,
    )
    return {k: v for k, v in document.items() if k != "user_id"}


async def load_recent_opportunity_to_profit_plans(
    db: Any,
    user_id: str,
    *,
    limit: int = 7,
) -> list[dict[str, Any]]:
    await ensure_indexes(db)
    cap = max(1, min(30, int(limit)))
    return await db[PLAN_COLLECTION].find(
        {"user_id": user_id}, {"_id": 0, "user_id": 0}
    ).sort("as_of", -1).limit(cap).to_list(length=cap)


__all__ = [
    "CONTRACT_VERSION",
    "PLAN_COLLECTION",
    "build_opportunity_to_profit_plan",
    "ensure_indexes",
    "load_recent_opportunity_to_profit_plans",
    "refresh_opportunity_to_profit_plan",
]
'''

(ROOT / "backend/campaign_ai_opportunity_to_profit_planner.py").write_text(module, encoding="utf-8")

tests = r'''from datetime import date

from campaign_ai_opportunity_to_profit_planner import build_opportunity_to_profit_plan


def _item(**overrides):
    base = {
        "product_key": "p1",
        "product_name": "منتج سعودي واعد",
        "saudi_trend_score": 84,
        "saudi_opportunity_score": 80,
        "state": "rising",
        "stage": "accelerating",
        "confidence": "high",
        "risk": "normal",
        "estimated_monthly_net_profit_sar": 30000,
        "estimated_net_profit_per_order_sar": 75,
        "estimated_monthly_orders": 400,
        "evidence_status": "measured",
    }
    base.update(overrides)
    return base


def _brief(**overrides):
    base = {
        "headline": {"monthly_profit_gap_sar": 50000},
        "measured_new_opportunities": [_item()],
        "evidence_required": [],
        "top_rising_existing_products": [],
        "products_to_watch": [],
        "external_discovery": {"allowed": False, "target_customer_market": "Saudi Arabia"},
    }
    base.update(overrides)
    return base


def test_measured_profitable_opportunity_is_analysis_ready():
    plan = build_opportunity_to_profit_plan(as_of=date(2026, 8, 22), daily_brief=_brief())
    item = plan["analysis_ready"][0]
    assert item["readiness"] == "analysis_ready"
    assert item["economics_status"] == "measured"
    assert item["estimated_profit_gap_coverage_ratio"] == 0.6


def test_unknown_economics_never_become_measured_profit():
    lead = _item(
        product_key="lead",
        evidence_status="evidence_required",
        estimated_monthly_net_profit_sar=None,
        estimated_net_profit_per_order_sar=None,
        estimated_monthly_orders=None,
    )
    plan = build_opportunity_to_profit_plan(
        as_of=date(2026, 8, 22),
        daily_brief=_brief(measured_new_opportunities=[], evidence_required=[lead]),
    )
    item = plan["evidence_required"][0]
    assert item["economics_status"] == "unknown"
    assert "monthly_profit_contribution_unknown" in item["blockers"]
    assert plan["measured_candidate_profit_sar"] == 0


def test_late_entry_is_penalized_and_blocked():
    late = _item(product_key="late", risk="late_entry", stage="peak_or_plateau")
    plan = build_opportunity_to_profit_plan(
        as_of=date(2026, 8, 22),
        daily_brief=_brief(measured_new_opportunities=[late]),
    )
    item = plan["evidence_required"][0]
    assert item["readiness"] == "evidence_required"
    assert "late_entry" in item["blockers"]


def test_measured_candidates_reduce_profit_gap():
    first = _item(product_key="a", estimated_monthly_net_profit_sar=30000)
    second = _item(product_key="b", estimated_monthly_net_profit_sar=25000)
    plan = build_opportunity_to_profit_plan(
        as_of=date(2026, 8, 22),
        daily_brief=_brief(measured_new_opportunities=[first, second]),
    )
    assert plan["measured_candidate_profit_sar"] == 55000
    assert plan["remaining_profit_gap_after_measured_candidates_sar"] == 0
    assert plan["strategy"] == "protect_and_sequence_measured_profit"


def test_no_goal_context_does_not_invent_gap():
    brief = _brief()
    brief["headline"] = {}
    plan = build_opportunity_to_profit_plan(as_of=date(2026, 8, 22), daily_brief=brief)
    assert plan["monthly_profit_gap_sar"] is None
    assert plan["remaining_profit_gap_after_measured_candidates_sar"] is None
    assert plan["strategy"] == "goal_context_missing"


def test_external_discovery_remains_saudi_targeted():
    brief = _brief(external_discovery={
        "allowed": True,
        "reason": "saudi_options_limited",
        "target_customer_market": "Saudi Arabia",
    })
    plan = build_opportunity_to_profit_plan(as_of=date(2026, 8, 22), daily_brief=brief)
    assert plan["external_discovery"]["allowed"] is True
    assert plan["external_discovery"]["target_customer_market"] == "Saudi Arabia"


def test_contract_is_read_only():
    plan = build_opportunity_to_profit_plan(as_of=date(2026, 8, 22), daily_brief=_brief())
    assert plan["contract_version"] == "opportunity_to_profit_planner_v1"
    assert plan["read_only"] is True
    assert "action" not in plan
'''

(ROOT / "backend/tests/test_campaign_ai_opportunity_to_profit_planner.py").write_text(tests, encoding="utf-8")

print("wrote backend/campaign_ai_opportunity_to_profit_planner.py")
print("wrote backend/tests/test_campaign_ai_opportunity_to_profit_planner.py")
