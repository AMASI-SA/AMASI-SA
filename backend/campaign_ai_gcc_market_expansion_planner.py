"""GCC Market Expansion Planner for Mezan Store Profit Manager.

This read-only deterministic planner compares Saudi Arabia with GCC expansion
markets using measured market economics. Unknown inputs stay unknown and can
never be promoted into a confident expansion recommendation.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

CONTRACT_VERSION = "gcc_market_expansion_planner_v1"
PLAN_COLLECTION = "mezan_gcc_market_expansion_plan_v1"

SUPPORTED_MARKETS = (
    "Saudi Arabia",
    "United Arab Emirates",
    "Kuwait",
    "Qatar",
    "Bahrain",
    "Oman",
)


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


def _bounded_score(value: Any) -> float | None:
    parsed = _number(value)
    if parsed is None:
        return None
    return round(max(0.0, min(100.0, parsed)), 2)


def _market_name(value: Any) -> str:
    name = _text(value, 80)
    return name if name in SUPPORTED_MARKETS else ""


def _required_economics(row: dict[str, Any]) -> dict[str, float | None]:
    local_price = _number(row.get("local_price_sar"))
    landed_cost = _number(row.get("landed_product_cost_sar"))
    cac = _number(row.get("expected_cac_sar"))
    shipping = _number(row.get("shipping_cost_sar"))
    payment_fee = _number(row.get("payment_fee_sar"))
    return_rate = _number(row.get("expected_return_rate"))
    return_cost = _number(row.get("return_cost_per_return_sar"))
    expected_orders = _number(row.get("expected_monthly_orders"))
    return {
        "local_price_sar": local_price,
        "landed_product_cost_sar": landed_cost,
        "expected_cac_sar": cac,
        "shipping_cost_sar": shipping,
        "payment_fee_sar": payment_fee,
        "expected_return_rate": return_rate,
        "return_cost_per_return_sar": return_cost,
        "expected_monthly_orders": expected_orders,
    }


def _economics_complete(row: dict[str, Any]) -> bool:
    economics = _required_economics(row)
    if any(value is None for value in economics.values()):
        return False
    return_rate = economics["expected_return_rate"]
    expected_orders = economics["expected_monthly_orders"]
    return bool(
        return_rate is not None
        and 0 <= return_rate <= 1
        and expected_orders is not None
        and expected_orders >= 0
    )


def _market_evidence_ready(row: dict[str, Any]) -> bool:
    return (
        _text(row.get("evidence_status"), 40).lower() == "measured"
        and _text(row.get("confidence"), 20).lower() in {"medium", "high"}
        and _economics_complete(row)
    )


def _profit_metrics(row: dict[str, Any]) -> dict[str, float | None]:
    if not _economics_complete(row):
        return {
            "expected_return_cost_per_order_sar": None,
            "expected_net_profit_per_order_sar": None,
            "expected_monthly_net_profit_sar": None,
            "expected_net_margin_ratio": None,
        }

    economics = _required_economics(row)
    local_price = float(economics["local_price_sar"])
    landed_cost = float(economics["landed_product_cost_sar"])
    cac = float(economics["expected_cac_sar"])
    shipping = float(economics["shipping_cost_sar"])
    payment_fee = float(economics["payment_fee_sar"])
    return_rate = float(economics["expected_return_rate"])
    return_cost = float(economics["return_cost_per_return_sar"])
    expected_orders = float(economics["expected_monthly_orders"])

    expected_return_cost = return_rate * return_cost
    profit_per_order = local_price - landed_cost - cac - shipping - payment_fee - expected_return_cost
    monthly_profit = profit_per_order * expected_orders
    margin = profit_per_order / local_price if local_price > 0 else None
    return {
        "expected_return_cost_per_order_sar": round(expected_return_cost, 2),
        "expected_net_profit_per_order_sar": round(profit_per_order, 2),
        "expected_monthly_net_profit_sar": round(monthly_profit, 2),
        "expected_net_margin_ratio": round(margin, 4) if margin is not None else None,
    }


def _blockers(row: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if _text(row.get("evidence_status"), 40).lower() != "measured":
        blockers.append("market_evidence_not_measured")
    if _text(row.get("confidence"), 20).lower() not in {"medium", "high"}:
        blockers.append("market_confidence_insufficient")

    economics = _required_economics(row)
    for key, value in economics.items():
        if value is None:
            blockers.append(f"unknown:{key}")
    return_rate = economics.get("expected_return_rate")
    if return_rate is not None and not 0 <= return_rate <= 1:
        blockers.append("invalid:expected_return_rate")
    orders = economics.get("expected_monthly_orders")
    if orders is not None and orders < 0:
        blockers.append("invalid:expected_monthly_orders")
    return blockers


def _priority_score(row: dict[str, Any], metrics: dict[str, float | None], *, monthly_gap_sar: float | None) -> float:
    if not _market_evidence_ready(row):
        return 0.0

    demand = _bounded_score(row.get("demand_score")) or 0.0
    product_fit = _bounded_score(row.get("product_fit_score")) or 0.0
    competition = _bounded_score(row.get("competition_score"))
    competition_component = 50.0 if competition is None else 100.0 - competition
    monthly_profit = max(0.0, metrics.get("expected_monthly_net_profit_sar") or 0.0)
    margin = metrics.get("expected_net_margin_ratio")

    coverage = 0.0
    if monthly_gap_sar is not None and monthly_gap_sar > 0:
        coverage = min(25.0, (monthly_profit / monthly_gap_sar) * 25.0)
    elif monthly_profit > 0:
        coverage = min(15.0, monthly_profit / 2000.0)

    margin_component = max(0.0, min(20.0, (margin or 0.0) * 40.0))
    score = (
        demand * 0.25
        + product_fit * 0.25
        + competition_component * 0.10
        + coverage
        + margin_component
    )
    return round(max(0.0, min(100.0, score)), 2)


def _market_item(row: dict[str, Any], *, monthly_gap_sar: float | None) -> dict[str, Any]:
    market = _market_name(row.get("market"))
    metrics = _profit_metrics(row)
    blockers = _blockers(row)
    ready = _market_evidence_ready(row) and not blockers
    monthly_profit = metrics.get("expected_monthly_net_profit_sar")
    gap_coverage = None
    if monthly_gap_sar is not None and monthly_gap_sar > 0 and monthly_profit is not None:
        gap_coverage = round(max(0.0, monthly_profit) / monthly_gap_sar, 4)

    economics = _required_economics(row)
    return {
        "market": market,
        "readiness": "analysis_ready" if ready else "evidence_required",
        "evidence_status": _text(row.get("evidence_status"), 40) or "unknown",
        "confidence": _text(row.get("confidence"), 20) or "low",
        "priority_score": _priority_score(row, metrics, monthly_gap_sar=monthly_gap_sar),
        "demand_score": _bounded_score(row.get("demand_score")),
        "competition_score": _bounded_score(row.get("competition_score")),
        "product_fit_score": _bounded_score(row.get("product_fit_score")),
        "price_sensitivity": _text(row.get("price_sensitivity"), 40) or "unknown",
        "delivery_days": _number(row.get("delivery_days")),
        "economics": {**economics, **metrics},
        "estimated_profit_gap_coverage_ratio": gap_coverage,
        "blockers": blockers,
        "source_provenance": row.get("source_provenance") if isinstance(row.get("source_provenance"), list) else [],
    }


def build_gcc_market_expansion_plan(
    *,
    as_of: date,
    opportunity_plan: dict[str, Any],
    market_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare Saudi and GCC market economics without manufacturing missing facts."""
    opportunity = opportunity_plan if isinstance(opportunity_plan, dict) else {}
    monthly_gap = _number(opportunity.get("remaining_profit_gap_after_measured_candidates_sar"))
    if monthly_gap is None:
        monthly_gap = _number(opportunity.get("monthly_profit_gap_sar"))

    rows = [row for row in market_evidence if isinstance(row, dict) and _market_name(row.get("market"))]
    by_market: dict[str, dict[str, Any]] = {}
    for row in rows:
        market = _market_name(row.get("market"))
        if market and market not in by_market:
            by_market[market] = row

    markets = [_market_item(by_market[name], monthly_gap_sar=monthly_gap) for name in SUPPORTED_MARKETS if name in by_market]
    saudi = next((item for item in markets if item["market"] == "Saudi Arabia"), None)
    saudi_profit = None if saudi is None else saudi["economics"].get("expected_monthly_net_profit_sar")

    for item in markets:
        monthly_profit = item["economics"].get("expected_monthly_net_profit_sar")
        delta = None
        if monthly_profit is not None and saudi_profit is not None:
            delta = round(monthly_profit - saudi_profit, 2)
        item["monthly_profit_delta_vs_saudi_sar"] = delta
        item["better_than_saudi_on_measured_profit"] = (
            delta > 0
            if delta is not None
            and item["readiness"] == "analysis_ready"
            and saudi is not None
            and saudi["readiness"] == "analysis_ready"
            else None
        )

    expansion = [item for item in markets if item["market"] != "Saudi Arabia"]
    expansion.sort(
        key=lambda item: (
            item["readiness"] == "analysis_ready",
            item["better_than_saudi_on_measured_profit"] is True,
            item["priority_score"],
            item["economics"].get("expected_monthly_net_profit_sar") or float("-inf"),
        ),
        reverse=True,
    )

    ready_expansion = [item for item in expansion if item["readiness"] == "analysis_ready"]
    best = ready_expansion[0] if ready_expansion else None

    if monthly_gap is None:
        strategy = "profit_gap_context_missing"
    elif monthly_gap <= 0:
        strategy = "protect_saudi_profit_floor_before_expansion"
    elif best is None:
        strategy = "collect_gcc_evidence_before_expansion"
    elif best.get("better_than_saudi_on_measured_profit") is True:
        strategy = "evaluate_best_gcc_market_against_saudi_growth"
    else:
        strategy = "prefer_saudi_until_gcc_measured_profit_is_superior"

    return {
        "contract_version": CONTRACT_VERSION,
        "as_of": as_of.isoformat(),
        "read_only": True,
        "objective": "Compare Saudi and GCC expansion options by measured net-profit contribution to the monthly goal.",
        "monthly_profit_gap_sar": monthly_gap,
        "strategy": strategy,
        "saudi_baseline": saudi,
        "ranked_expansion_markets": expansion,
        "analysis_ready": ready_expansion,
        "evidence_required": [item for item in expansion if item["readiness"] != "analysis_ready"],
        "best_measured_expansion_market": best,
        "guardrails": [
            "Saudi Arabia remains the commercial baseline; expansion must beat or complement measured Saudi economics.",
            "Unknown CAC, shipping, payment fees, returns, price, product cost, or order capacity remain unknown.",
            "No GCC market is promoted from popularity, stereotypes, or provider conversion counts alone.",
            "Market ranking is decision support only and performs no ads, product, price, inventory, supplier, or commerce-platform writes.",
            "Expansion should be tested before material inventory or advertising commitment.",
        ],
    }


async def ensure_indexes(db: Any) -> None:
    await db[PLAN_COLLECTION].create_index(
        [("user_id", 1), ("as_of", 1)],
        unique=True,
        name="gcc_market_expansion_user_day_unique",
    )


async def refresh_gcc_market_expansion_plan(
    db: Any,
    user_id: str,
    *,
    as_of: date,
    opportunity_plan: dict[str, Any],
    market_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    await ensure_indexes(db)
    plan = build_gcc_market_expansion_plan(
        as_of=as_of,
        opportunity_plan=opportunity_plan,
        market_evidence=market_evidence,
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    document = {**plan, "user_id": user_id, "updated_at": now_iso}
    await db[PLAN_COLLECTION].update_one(
        {"user_id": user_id, "as_of": plan["as_of"]},
        {"$set": document, "$setOnInsert": {"created_at": now_iso}},
        upsert=True,
    )
    return {key: value for key, value in document.items() if key != "user_id"}


async def load_recent_gcc_market_expansion_plans(
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
    "SUPPORTED_MARKETS",
    "build_gcc_market_expansion_plan",
    "ensure_indexes",
    "load_recent_gcc_market_expansion_plans",
    "refresh_gcc_market_expansion_plan",
]
