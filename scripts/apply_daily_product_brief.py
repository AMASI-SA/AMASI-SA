from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

module = r'''"""Daily Saudi Product Brief for Mezan Store Profit Manager.

This layer converts the read-only Saudi Product Radar snapshot into a compact,
auditable owner brief. It does not invent demand, profit, or supplier facts and
performs no catalog, price, inventory, supplier, or campaign writes.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

CONTRACT_VERSION = "daily_product_brief_v1"
BRIEF_COLLECTION = "mezan_daily_product_brief_v1"


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


def _trend(item: dict[str, Any]) -> dict[str, Any]:
    trend = item.get("trend_lifecycle")
    return trend if isinstance(trend, dict) else {}


def _confidence_rank(value: Any) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(_text(value, 20).lower(), 0)


def _opportunity_priority(item: dict[str, Any]) -> tuple[Any, ...]:
    trend = _trend(item)
    state = _text(trend.get("state") or item.get("lifecycle", {}).get("state"), 40)
    stage = _text(trend.get("estimated_wave_stage"), 60)
    risk = _text(trend.get("risk"), 60)
    trend_score = _number(item.get("saudi_trend_score")) or -1.0
    opp_score = _number(item.get("saudi_opportunity_score")) or -1.0
    profit = _number(item.get("estimated_monthly_net_profit_sar")) or -1.0
    confidence = _confidence_rank(trend.get("confidence") or item.get("lifecycle", {}).get("confidence"))
    return (
        state == "rising",
        stage == "accelerating",
        risk == "normal",
        confidence,
        trend_score,
        opp_score,
        profit,
    )


def _existing_risk_priority(item: dict[str, Any]) -> tuple[Any, ...]:
    trend = _trend(item)
    stage = _text(trend.get("estimated_wave_stage"), 60)
    risk = _text(trend.get("risk"), 60)
    state = _text(trend.get("state") or item.get("lifecycle", {}).get("state"), 40)
    trend_score = _number(item.get("saudi_trend_score")) or -1.0
    severity = {
        "trend_decay": 4,
        "late_entry": 3,
        "early_uncertainty": 2,
        "normal": 1,
    }.get(risk, 0)
    stage_severity = {
        "ended": 5,
        "declining": 4,
        "cooling": 3,
        "peak_or_plateau": 2,
    }.get(stage, 0)
    return (severity, stage_severity, state in {"falling", "trend_ended"}, trend_score)


def _brief_item(item: dict[str, Any], *, lane: str) -> dict[str, Any]:
    trend = _trend(item)
    lifecycle = item.get("lifecycle") if isinstance(item.get("lifecycle"), dict) else {}
    return {
        "lane": lane,
        "product_key": _text(item.get("product_key"), 180),
        "product_name": _text(item.get("product_name") or item.get("product_key"), 220),
        "store_product_id": _text(item.get("store_product_id"), 160) or None,
        "saudi_trend_score": _number(item.get("saudi_trend_score")),
        "saudi_opportunity_score": _number(item.get("saudi_opportunity_score")),
        "state": _text(trend.get("state") or lifecycle.get("state"), 40) or "unknown",
        "stage": _text(trend.get("estimated_wave_stage"), 60) or "unknown",
        "confidence": _text(trend.get("confidence") or lifecycle.get("confidence"), 20) or "low",
        "risk": _text(trend.get("risk"), 60) or "unknown",
        "momentum": _number(trend.get("momentum")),
        "acceleration": _number(trend.get("acceleration")),
        "estimated_monthly_net_profit_sar": _number(item.get("estimated_monthly_net_profit_sar")),
        "estimated_net_profit_per_order_sar": _number(item.get("estimated_net_profit_per_order_sar")),
        "estimated_monthly_orders": _number(item.get("estimated_monthly_orders")),
        "evidence_status": _text(item.get("evidence_status"), 40) or "evidence_required",
        "evidence_count": int(_number(item.get("evidence_count")) or 0),
        "sources": list(item.get("sources") or [])[:10],
    }


def build_daily_product_brief(
    *,
    as_of: date,
    radar_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Build a compact Saudi-first owner brief from one radar snapshot."""
    radar = radar_snapshot if isinstance(radar_snapshot, dict) else {}
    existing = [x for x in radar.get("existing_products", []) if isinstance(x, dict)]
    opportunities = [x for x in radar.get("saudi_opportunities", []) if isinstance(x, dict)]

    rising_existing = sorted(
        [x for x in existing if _text(_trend(x).get("state") or x.get("lifecycle", {}).get("state"), 40) == "rising"],
        key=_opportunity_priority,
        reverse=True,
    )
    watch_existing = sorted(
        [x for x in existing if _text(_trend(x).get("risk"), 60) in {"trend_decay", "late_entry"}
         or _text(_trend(x).get("state") or x.get("lifecycle", {}).get("state"), 40) in {"falling", "trend_ended"}],
        key=_existing_risk_priority,
        reverse=True,
    )
    new_opportunities = sorted(opportunities, key=_opportunity_priority, reverse=True)

    measured_new = [x for x in new_opportunities if _text(x.get("evidence_status"), 40) == "measured"]
    evidence_required = [x for x in new_opportunities if _text(x.get("evidence_status"), 40) != "measured"]

    gap = _number(radar.get("monthly_profit_gap_sar"))
    coverage = _number(radar.get("measured_saudi_opportunity_profit_coverage_sar")) or 0.0
    gap_after_coverage = max(0.0, gap - coverage) if gap is not None else None
    external = radar.get("external_discovery_policy") if isinstance(radar.get("external_discovery_policy"), dict) else {}

    if gap is None:
        goal_status = "goal_context_missing"
    elif gap <= 0:
        goal_status = "monthly_goal_covered"
    elif coverage >= gap:
        goal_status = "saudi_opportunity_coverage_sufficient"
    else:
        goal_status = "profit_gap_still_open"

    return {
        "contract_version": CONTRACT_VERSION,
        "as_of": as_of.isoformat(),
        "market": "Saudi Arabia",
        "market_priority": "saudi_first",
        "read_only": True,
        "headline": {
            "goal_status": goal_status,
            "monthly_profit_gap_sar": gap,
            "measured_saudi_opportunity_profit_coverage_sar": round(coverage, 2),
            "profit_gap_after_measured_coverage_sar": round(gap_after_coverage, 2) if gap_after_coverage is not None else None,
            "rising_existing_count": len(rising_existing),
            "watch_existing_count": len(watch_existing),
            "new_opportunity_count": len(new_opportunities),
            "measured_new_opportunity_count": len(measured_new),
            "evidence_required_count": len(evidence_required),
        },
        "top_rising_existing_products": [_brief_item(x, lane="rising_existing") for x in rising_existing[:5]],
        "products_to_watch": [_brief_item(x, lane="watch_existing") for x in watch_existing[:5]],
        "top_new_saudi_opportunities": [_brief_item(x, lane="new_saudi_opportunity") for x in new_opportunities[:7]],
        "measured_new_opportunities": [_brief_item(x, lane="measured_new_opportunity") for x in measured_new[:5]],
        "evidence_required": [_brief_item(x, lane="evidence_required") for x in evidence_required[:5]],
        "external_discovery": {
            "allowed": bool(external.get("allowed")),
            "reason": _text(external.get("reason"), 120) or None,
            "sources": list(external.get("sources") or [])[:10],
            "target_customer_market": _text(external.get("target_customer_market"), 80) or "Saudi Arabia",
        },
        "owner_attention": [
            "Protect profitable rising existing products from stock or page failures.",
            "Treat cooling/declining products as risk signals, not automatic stop decisions.",
            "Require measured Saudi evidence before treating a new product as investment-ready.",
            "Use external discovery only when Saudi opportunities are insufficient or cannot cover the profit gap.",
        ],
        "guardrails": [
            "No demand, profit, supplier, or inventory fact is invented by the brief.",
            "Low-confidence opportunities remain evidence-required.",
            "The brief performs no catalog, price, inventory, supplier, or campaign writes.",
        ],
    }


async def ensure_indexes(db: Any) -> None:
    await db[BRIEF_COLLECTION].create_index(
        [("user_id", 1), ("as_of", 1)], unique=True,
        name="daily_product_brief_user_day_unique",
    )


async def refresh_daily_product_brief(
    db: Any,
    user_id: str,
    *,
    as_of: date,
    radar_snapshot: dict[str, Any],
) -> dict[str, Any]:
    await ensure_indexes(db)
    brief = build_daily_product_brief(as_of=as_of, radar_snapshot=radar_snapshot)
    now_iso = datetime.now(timezone.utc).isoformat()
    document = {**brief, "user_id": user_id, "updated_at": now_iso}
    await db[BRIEF_COLLECTION].update_one(
        {"user_id": user_id, "as_of": brief["as_of"]},
        {"$set": document, "$setOnInsert": {"created_at": now_iso}},
        upsert=True,
    )
    return {k: v for k, v in document.items() if k != "user_id"}


async def load_recent_daily_product_briefs(db: Any, user_id: str, *, limit: int = 7) -> list[dict[str, Any]]:
    await ensure_indexes(db)
    cap = max(1, min(30, int(limit)))
    return await db[BRIEF_COLLECTION].find(
        {"user_id": user_id}, {"_id": 0, "user_id": 0}
    ).sort("as_of", -1).limit(cap).to_list(length=cap)


__all__ = [
    "BRIEF_COLLECTION",
    "CONTRACT_VERSION",
    "build_daily_product_brief",
    "ensure_indexes",
    "load_recent_daily_product_briefs",
    "refresh_daily_product_brief",
]
'''

(ROOT / "backend/campaign_ai_daily_product_brief.py").write_text(module, encoding="utf-8")

tests = r'''from datetime import date

from campaign_ai_daily_product_brief import build_daily_product_brief


def _item(name, *, state="rising", stage="accelerating", risk="normal", confidence="high",
          trend_score=80, opportunity_score=75, measured=True, store_product_id=None,
          monthly_profit=5000):
    return {
        "product_key": name.lower().replace(" ", "-"),
        "product_name": name,
        "store_product_id": store_product_id,
        "saudi_trend_score": trend_score,
        "saudi_opportunity_score": opportunity_score,
        "trend_lifecycle": {
            "state": state,
            "estimated_wave_stage": stage,
            "risk": risk,
            "confidence": confidence,
            "momentum": 14,
            "acceleration": 5,
        },
        "lifecycle": {"state": state, "confidence": confidence},
        "estimated_monthly_net_profit_sar": monthly_profit,
        "estimated_net_profit_per_order_sar": 50,
        "estimated_monthly_orders": 100,
        "evidence_status": "measured" if measured else "evidence_required",
        "evidence_count": 6 if measured else 1,
        "sources": ["saudi_search", "saudi_competitor"] if measured else ["saudi_search"],
    }


def test_brief_prioritizes_accelerating_measured_opportunity():
    radar = {
        "saudi_opportunities": [
            _item("Stable Item", state="stable", stage="developing", trend_score=70),
            _item("Fast Item", state="rising", stage="accelerating", trend_score=90),
        ],
        "existing_products": [],
        "monthly_profit_gap_sar": 20000,
        "measured_saudi_opportunity_profit_coverage_sar": 10000,
    }
    brief = build_daily_product_brief(as_of=date(2026, 8, 22), radar_snapshot=radar)
    assert brief["top_new_saudi_opportunities"][0]["product_name"] == "Fast Item"


def test_brief_surfaces_existing_decay_risk():
    radar = {
        "existing_products": [
            _item("Cooling Existing", state="falling", stage="cooling", risk="trend_decay", store_product_id="p1"),
        ],
        "saudi_opportunities": [],
    }
    brief = build_daily_product_brief(as_of=date(2026, 8, 22), radar_snapshot=radar)
    assert brief["products_to_watch"][0]["risk"] == "trend_decay"


def test_brief_keeps_low_confidence_opportunity_in_evidence_required_lane():
    radar = {
        "existing_products": [],
        "saudi_opportunities": [
            _item("One Signal", confidence="low", measured=False, trend_score=88),
        ],
    }
    brief = build_daily_product_brief(as_of=date(2026, 8, 22), radar_snapshot=radar)
    assert brief["headline"]["evidence_required_count"] == 1
    assert brief["evidence_required"][0]["product_name"] == "One Signal"


def test_brief_connects_measured_coverage_to_monthly_profit_gap():
    radar = {
        "existing_products": [],
        "saudi_opportunities": [],
        "monthly_profit_gap_sar": 30000,
        "measured_saudi_opportunity_profit_coverage_sar": 12000,
    }
    brief = build_daily_product_brief(as_of=date(2026, 8, 22), radar_snapshot=radar)
    assert brief["headline"]["goal_status"] == "profit_gap_still_open"
    assert brief["headline"]["profit_gap_after_measured_coverage_sar"] == 18000


def test_brief_marks_saudi_coverage_sufficient_when_gap_is_covered():
    radar = {
        "existing_products": [],
        "saudi_opportunities": [],
        "monthly_profit_gap_sar": 10000,
        "measured_saudi_opportunity_profit_coverage_sar": 14000,
    }
    brief = build_daily_product_brief(as_of=date(2026, 8, 22), radar_snapshot=radar)
    assert brief["headline"]["goal_status"] == "saudi_opportunity_coverage_sufficient"
    assert brief["headline"]["profit_gap_after_measured_coverage_sar"] == 0


def test_external_discovery_stays_saudi_customer_oriented():
    radar = {
        "existing_products": [],
        "saudi_opportunities": [],
        "external_discovery_policy": {
            "allowed": True,
            "reason": "saudi_options_limited",
            "sources": ["alibaba", "shein_public_market"],
            "target_customer_market": "Saudi Arabia",
        },
    }
    brief = build_daily_product_brief(as_of=date(2026, 8, 22), radar_snapshot=radar)
    assert brief["external_discovery"]["allowed"] is True
    assert brief["external_discovery"]["target_customer_market"] == "Saudi Arabia"


def test_contract_is_read_only_and_does_not_emit_actions():
    brief = build_daily_product_brief(as_of=date(2026, 8, 22), radar_snapshot={})
    assert brief["contract_version"] == "daily_product_brief_v1"
    assert brief["read_only"] is True
    assert "actions" not in brief
'''

(ROOT / "backend/tests/test_campaign_ai_daily_product_brief.py").write_text(tests, encoding="utf-8")

print("wrote backend/campaign_ai_daily_product_brief.py")
print("wrote backend/tests/test_campaign_ai_daily_product_brief.py")
