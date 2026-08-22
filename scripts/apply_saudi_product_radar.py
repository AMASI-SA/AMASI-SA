from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

module = r'''"""Saudi-first Product Radar for Mezan Store Profit Manager.

This layer is deliberately read-only.  It ranks product-market evidence for the
Saudi market, classifies product lifecycle direction, and decides when external
markets/supplier sites may be used as discovery sources.  External discovery is
never treated as a sales-market expansion: its purpose is to find products that
may appeal to Saudi customers.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

CONTRACT_VERSION = "saudi_product_radar_v1"
SNAPSHOT_COLLECTION = "mezan_saudi_product_radar_daily_v1"
MARKET_SIGNAL_COLLECTION = "mezan_saudi_product_market_signals_v1"
SAUDI_MARKET = "SA"
MIN_QUALIFIED_SCORE = 60.0
MIN_SAUDI_OPTIONS_BEFORE_EXTERNAL = 3

EXTERNAL_DISCOVERY_SOURCES = (
    "gcc_public_market",
    "alibaba",
    "shein_public_market",
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


def _day(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = _text(value, 40)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _normalized_signal(row: dict[str, Any]) -> dict[str, Any] | None:
    key = _text(row.get("product_key") or row.get("product_id") or row.get("query"), 180)
    name = _text(row.get("product_name") or row.get("name") or key, 220)
    observed = _day(row.get("observed_on") or row.get("captured_at") or row.get("date"))
    score = _number(row.get("score") or row.get("trend_score") or row.get("demand_score"))
    if not key or not observed or score is None:
        return None
    return {
        "product_key": key,
        "product_name": name or key,
        "market": _text(row.get("market") or SAUDI_MARKET, 12).upper(),
        "source": _text(row.get("source") or "unknown", 120),
        "signal_type": _text(row.get("signal_type") or "market_interest", 100),
        "score": max(0.0, min(100.0, score)),
        "observed_on": observed,
        "price_sar": _number(row.get("price_sar")),
        "store_product_id": _text(row.get("store_product_id"), 160) or None,
        "evidence_ref": _text(row.get("evidence_ref"), 500) or None,
        "estimated_net_profit_per_order_sar": _number(
            row.get("estimated_net_profit_per_order_sar")
        ),
        "estimated_monthly_orders": _number(row.get("estimated_monthly_orders")),
    }


def classify_lifecycle(scores: list[tuple[date, float]], *, as_of: date) -> dict[str, Any]:
    clean = sorted((day, float(score)) for day, score in scores if day <= as_of)
    if len(clean) < 2:
        return {"state": "insufficient_evidence", "delta": None, "confidence": "low"}
    recent_start = as_of - timedelta(days=6)
    prior_start = as_of - timedelta(days=20)
    recent = [score for day, score in clean if day >= recent_start]
    prior = [score for day, score in clean if prior_start <= day < recent_start]
    if not recent:
        return {"state": "trend_ended", "delta": None, "confidence": "medium"}
    recent_avg = sum(recent) / len(recent)
    if not prior:
        first = clean[0][1]
        delta = recent_avg - first
        confidence = "low"
    else:
        prior_avg = sum(prior) / len(prior)
        delta = recent_avg - prior_avg
        confidence = "high" if len(recent) >= 3 and len(prior) >= 3 else "medium"
    if recent_avg < 25:
        state = "trend_ended"
    elif delta >= 10:
        state = "rising"
    elif delta <= -10:
        state = "falling"
    else:
        state = "stable"
    return {"state": state, "delta": round(delta, 2), "confidence": confidence}


def _profit_gap(goal_context: dict[str, Any] | None) -> float | None:
    goal = goal_context if isinstance(goal_context, dict) else {}
    gap = _number(goal.get("remaining_to_target_sar"))
    return max(0.0, gap) if gap is not None else None


def build_saudi_product_radar(
    *,
    as_of: date,
    market_signals: list[dict[str, Any]],
    goal_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one Saudi-first daily radar snapshot from auditable market evidence."""
    normalized = [item for row in market_signals if (item := _normalized_signal(row))]
    saudi = [row for row in normalized if row["market"] in {"SA", "SAU", "SAUDI_ARABIA"}]
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in saudi:
        grouped[row["product_key"]].append(row)

    opportunities: list[dict[str, Any]] = []
    existing_products: list[dict[str, Any]] = []
    for key, rows in grouped.items():
        rows = sorted(rows, key=lambda item: item["observed_on"])
        latest = rows[-1]
        lifecycle = classify_lifecycle(
            [(item["observed_on"], item["score"]) for item in rows], as_of=as_of
        )
        recent = [item["score"] for item in rows if item["observed_on"] >= as_of - timedelta(days=6)]
        score = round(sum(recent) / len(recent), 2) if recent else round(latest["score"], 2)
        profit_per_order = next(
            (item["estimated_net_profit_per_order_sar"] for item in reversed(rows)
             if item["estimated_net_profit_per_order_sar"] is not None),
            None,
        )
        monthly_orders = next(
            (item["estimated_monthly_orders"] for item in reversed(rows)
             if item["estimated_monthly_orders"] is not None),
            None,
        )
        estimated_monthly_profit = (
            profit_per_order * monthly_orders
            if profit_per_order is not None and monthly_orders is not None
            else None
        )
        entry = {
            "product_key": key,
            "product_name": latest["product_name"],
            "store_product_id": latest.get("store_product_id"),
            "saudi_opportunity_score": score,
            "lifecycle": lifecycle,
            "latest_price_sar": latest.get("price_sar"),
            "evidence_count": len(rows),
            "sources": sorted({item["source"] for item in rows}),
            "estimated_net_profit_per_order_sar": profit_per_order,
            "estimated_monthly_orders": monthly_orders,
            "estimated_monthly_net_profit_sar": (
                round(estimated_monthly_profit, 2)
                if estimated_monthly_profit is not None else None
            ),
            "evidence_status": "measured" if len(rows) >= 2 else "evidence_required",
        }
        if latest.get("store_product_id"):
            existing_products.append(entry)
        else:
            opportunities.append(entry)

    opportunities.sort(
        key=lambda item: (
            item["lifecycle"]["state"] == "rising",
            item["saudi_opportunity_score"],
            item["estimated_monthly_net_profit_sar"] or -1,
        ),
        reverse=True,
    )
    existing_products.sort(key=lambda item: item["saudi_opportunity_score"], reverse=True)

    qualified = [
        item for item in opportunities
        if item["saudi_opportunity_score"] >= MIN_QUALIFIED_SCORE
        and item["lifecycle"]["state"] not in {"falling", "trend_ended"}
    ]
    gap = _profit_gap(goal_context)
    measured_coverage = sum(
        item["estimated_monthly_net_profit_sar"] or 0.0 for item in qualified
        if item["evidence_status"] == "measured"
    )
    saudi_options_limited = len(qualified) < MIN_SAUDI_OPTIONS_BEFORE_EXTERNAL
    gap_not_covered = gap is not None and measured_coverage < gap
    external_allowed = bool(saudi_options_limited or gap_not_covered)

    return {
        "contract_version": CONTRACT_VERSION,
        "as_of": as_of.isoformat(),
        "market_priority": "saudi_first",
        "sales_market": "Saudi Arabia",
        "read_only": True,
        "evidence_count": len(saudi),
        "existing_products": existing_products[:40],
        "saudi_opportunities": opportunities[:40],
        "qualified_saudi_opportunities": qualified[:20],
        "monthly_profit_gap_sar": gap,
        "measured_saudi_opportunity_profit_coverage_sar": round(measured_coverage, 2),
        "external_discovery_policy": {
            "allowed": external_allowed,
            "reason": (
                "saudi_options_limited" if saudi_options_limited
                else "saudi_options_do_not_cover_profit_gap" if gap_not_covered
                else "saudi_options_sufficient"
            ),
            "purpose": (
                "Find products outside Saudi sources that are likely to appeal to Saudi customers; "
                "do not treat external markets as target sales markets."
            ),
            "sources": list(EXTERNAL_DISCOVERY_SOURCES) if external_allowed else [],
            "target_customer_market": "Saudi Arabia",
        },
        "guardrails": [
            "Saudi evidence is searched and ranked first.",
            "External discovery is a fallback, not a parallel daily distraction.",
            "External-market popularity is only a lead; Saudi fit must be validated before recommendation.",
            "No catalog, price, inventory, supplier, or campaign writes are performed by the radar.",
        ],
    }


async def ensure_indexes(db: Any) -> None:
    await db[SNAPSHOT_COLLECTION].create_index(
        [("user_id", 1), ("as_of", 1)], unique=True,
        name="saudi_product_radar_user_day_unique",
    )
    await db[MARKET_SIGNAL_COLLECTION].create_index(
        [("user_id", 1), ("market", 1), ("observed_on", -1)],
        name="saudi_product_market_signal_recent",
    )


async def refresh_saudi_product_radar(
    db: Any,
    user_id: str,
    *,
    as_of: date,
    goal_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upsert one daily radar snapshot from tenant-scoped market evidence."""
    await ensure_indexes(db)
    start = (as_of - timedelta(days=30)).isoformat()
    end = as_of.isoformat()
    rows = await db[MARKET_SIGNAL_COLLECTION].find(
        {
            "user_id": user_id,
            "$or": [
                {"observed_on": {"$gte": start, "$lte": end}},
                {"captured_at": {"$gte": start, "$lt": end + "T23:59:59.999999"}},
            ],
        },
        {"_id": 0, "user_id": 0},
    ).sort("observed_on", 1).limit(5000).to_list(length=5000)
    snapshot = build_saudi_product_radar(
        as_of=as_of,
        market_signals=rows,
        goal_context=goal_context,
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    document = {**snapshot, "user_id": user_id, "updated_at": now_iso}
    await db[SNAPSHOT_COLLECTION].update_one(
        {"user_id": user_id, "as_of": snapshot["as_of"]},
        {"$set": document, "$setOnInsert": {"created_at": now_iso}},
        upsert=True,
    )
    return {key: value for key, value in document.items() if key != "user_id"}


async def load_recent_saudi_product_radar(db: Any, user_id: str, *, limit: int = 7) -> list[dict[str, Any]]:
    await ensure_indexes(db)
    cap = max(1, min(30, int(limit)))
    return await db[SNAPSHOT_COLLECTION].find(
        {"user_id": user_id}, {"_id": 0, "user_id": 0}
    ).sort("as_of", -1).limit(cap).to_list(length=cap)


__all__ = [
    "CONTRACT_VERSION",
    "MARKET_SIGNAL_COLLECTION",
    "SNAPSHOT_COLLECTION",
    "build_saudi_product_radar",
    "classify_lifecycle",
    "ensure_indexes",
    "load_recent_saudi_product_radar",
    "refresh_saudi_product_radar",
]
'''


tests = r'''from datetime import date, timedelta

import campaign_ai_saudi_product_radar as radar


def signal(day, score, **extra):
    return {
        "product_key": extra.pop("product_key", "p1"),
        "product_name": extra.pop("product_name", "منتج 1"),
        "market": extra.pop("market", "SA"),
        "source": extra.pop("source", "public_market"),
        "score": score,
        "observed_on": day.isoformat(),
        **extra,
    }


def test_lifecycle_rising():
    end = date(2026, 8, 22)
    rows = [(end - timedelta(days=14), 30), (end - timedelta(days=10), 35), (end - timedelta(days=2), 70), (end, 80)]
    assert radar.classify_lifecycle(rows, as_of=end)["state"] == "rising"


def test_lifecycle_falling():
    end = date(2026, 8, 22)
    rows = [(end - timedelta(days=14), 80), (end - timedelta(days=10), 75), (end - timedelta(days=2), 40), (end, 35)]
    assert radar.classify_lifecycle(rows, as_of=end)["state"] == "falling"


def test_saudi_first_filters_non_saudi_from_primary_ranking():
    end = date(2026, 8, 22)
    result = radar.build_saudi_product_radar(
        as_of=end,
        market_signals=[
            signal(end, 70, product_key="sa"),
            signal(end, 99, product_key="ae", market="AE"),
        ],
        goal_context={},
    )
    keys = [item["product_key"] for item in result["saudi_opportunities"]]
    assert keys == ["sa"]
    assert result["sales_market"] == "Saudi Arabia"


def test_external_discovery_only_when_saudi_options_limited():
    end = date(2026, 8, 22)
    result = radar.build_saudi_product_radar(
        as_of=end,
        market_signals=[signal(end - timedelta(days=1), 75), signal(end, 80)],
        goal_context={},
    )
    policy = result["external_discovery_policy"]
    assert policy["allowed"] is True
    assert policy["target_customer_market"] == "Saudi Arabia"
    assert "alibaba" in policy["sources"]


def test_external_discovery_stays_off_when_saudi_options_are_sufficient():
    end = date(2026, 8, 22)
    signals = []
    for idx in range(3):
        key = f"p{idx}"
        signals += [
            signal(end - timedelta(days=2), 70 + idx, product_key=key),
            signal(end, 75 + idx, product_key=key),
        ]
    result = radar.build_saudi_product_radar(
        as_of=end, market_signals=signals, goal_context={}
    )
    assert result["external_discovery_policy"]["allowed"] is False
    assert result["external_discovery_policy"]["sources"] == []


def test_profit_gap_can_trigger_external_discovery_even_with_three_options():
    end = date(2026, 8, 22)
    signals = []
    for idx in range(3):
        key = f"p{idx}"
        signals += [
            signal(end - timedelta(days=2), 75, product_key=key, estimated_net_profit_per_order_sar=20, estimated_monthly_orders=10),
            signal(end, 80, product_key=key, estimated_net_profit_per_order_sar=20, estimated_monthly_orders=10),
        ]
    result = radar.build_saudi_product_radar(
        as_of=end,
        market_signals=signals,
        goal_context={"remaining_to_target_sar": 5000},
    )
    assert result["measured_saudi_opportunity_profit_coverage_sar"] == 600
    assert result["external_discovery_policy"]["allowed"] is True
    assert result["external_discovery_policy"]["reason"] == "saudi_options_do_not_cover_profit_gap"


def test_existing_product_is_classified_separately():
    end = date(2026, 8, 22)
    result = radar.build_saudi_product_radar(
        as_of=end,
        market_signals=[
            signal(end - timedelta(days=1), 70, store_product_id="salla-1"),
            signal(end, 72, store_product_id="salla-1"),
        ],
        goal_context={},
    )
    assert len(result["existing_products"]) == 1
    assert result["saudi_opportunities"] == []
    assert result["existing_products"][0]["store_product_id"] == "salla-1"
'''

module_path = ROOT / "backend" / "campaign_ai_saudi_product_radar.py"
test_path = ROOT / "backend" / "tests" / "test_campaign_ai_saudi_product_radar.py"
monitor_path = ROOT / "backend" / "campaign_ai_monitor_legacy.py"
module_path.write_text(module, encoding="utf-8")
test_path.write_text(tests, encoding="utf-8")

monitor = monitor_path.read_text(encoding="utf-8")
needle = '''        if not candidates:\n            recommendation_source = "none"\n'''
insert = '''        try:\n            from campaign_ai_saudi_product_radar import (\n                load_recent_saudi_product_radar,\n                refresh_saudi_product_radar,\n            )\n            saudi_product_radar = await refresh_saudi_product_radar(\n                db,\n                user_id,\n                as_of=end,\n                goal_context=business_profit.get("monthly_profit_goal"),\n            )\n            prior_decisions["saudi_product_radar"] = {\n                "current": saudi_product_radar,\n                "recent": await load_recent_saudi_product_radar(db, user_id, limit=7),\n            }\n        except Exception as exc:\n            saudi_product_radar = {\n                "contract_version": "saudi_product_radar_v1",\n                "read_only": True,\n                "available": False,\n                "reason": type(exc).__name__,\n            }\n            errors.append({\n                "source": "saudi_product_radar",\n                "code": _text(type(exc).__name__, limit=100),\n            })\n        if not candidates:\n            recommendation_source = "none"\n'''
if needle not in monitor:
    raise SystemExit("monitor insertion point not found")
monitor = monitor.replace(needle, insert, 1)

doc_needle = '''            "monthly_causal_memory_available": current_month_memory is not None,\n'''
if doc_needle not in monitor:
    raise SystemExit("monitor document insertion point not found")
monitor = monitor.replace(
    doc_needle,
    doc_needle + '''            "saudi_product_radar": saudi_product_radar,\n            "saudi_product_radar_available": saudi_product_radar.get("available", True) is not False,\n''',
    1,
)
monitor_path.write_text(monitor, encoding="utf-8")

print("wrote backend/campaign_ai_saudi_product_radar.py")
print("wrote backend/tests/test_campaign_ai_saudi_product_radar.py")
print("patched backend/campaign_ai_monitor_legacy.py")
