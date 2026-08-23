"""GCC market evidence adapters for Store Profit Manager.

The adapter combines tenant-scoped first-party Mezan order/profit evidence with
explicitly sourced market observations. It never manufactures missing economics,
market scores, or return costs. The output is shaped for
``campaign_ai_gcc_market_expansion_planner``.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from mezan_attribution_order_ledger import LEDGER_COLLECTION

CONTRACT_VERSION = "gcc_market_evidence_v1"
OBSERVATION_COLLECTION = "mezan_gcc_market_observation_v1"
ORDER_COLLECTION = "unified_orders"

MARKET_BY_CODE = {
    "SA": "Saudi Arabia",
    "AE": "United Arab Emirates",
    "KW": "Kuwait",
    "QA": "Qatar",
    "BH": "Bahrain",
    "OM": "Oman",
}

MARKET_ALIASES = {
    "sa": "Saudi Arabia",
    "ksa": "Saudi Arabia",
    "saudi": "Saudi Arabia",
    "saudi arabia": "Saudi Arabia",
    "السعودية": "Saudi Arabia",
    "المملكة العربية السعودية": "Saudi Arabia",
    "ae": "United Arab Emirates",
    "uae": "United Arab Emirates",
    "united arab emirates": "United Arab Emirates",
    "الإمارات": "United Arab Emirates",
    "الامارات": "United Arab Emirates",
    "kw": "Kuwait",
    "kuwait": "Kuwait",
    "الكويت": "Kuwait",
    "qa": "Qatar",
    "qatar": "Qatar",
    "قطر": "Qatar",
    "bh": "Bahrain",
    "bahrain": "Bahrain",
    "البحرين": "Bahrain",
    "om": "Oman",
    "oman": "Oman",
    "عمان": "Oman",
    "سلطنة عمان": "Oman",
}

MEASURED_FIELDS = (
    "local_price_sar",
    "landed_product_cost_sar",
    "expected_cac_sar",
    "shipping_cost_sar",
    "payment_fee_sar",
    "expected_return_rate",
    "return_cost_per_return_sar",
    "expected_monthly_orders",
    "delivery_days",
    "demand_score",
    "competition_score",
    "product_fit_score",
    "price_sensitivity",
)


def _text(value: Any, limit: int = 240) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return " ".join(str(value).split()).strip()[:limit]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _market(value: Any) -> str | None:
    raw = _text(value, 120)
    if not raw:
        return None
    upper = raw.upper()
    if upper in MARKET_BY_CODE:
        return MARKET_BY_CODE[upper]
    return MARKET_ALIASES.get(raw.casefold())


def _order_key(order: dict[str, Any]) -> str:
    for key in ("order_id", "id", "reference_id", "order_number"):
        value = _text(order.get(key), 180)
        if value:
            return value
    raw = _dict(_dict(order.get("raw_by_source")).get("salla_direct"))
    for key in ("id", "reference_id", "order_number"):
        value = _text(raw.get(key), 180)
        if value:
            return value
    return ""


def _order_market(order: dict[str, Any]) -> str | None:
    raw = _dict(_dict(order.get("raw_by_source")).get("salla_direct"))
    shipping = _dict(raw.get("shipping"))
    customer = _dict(raw.get("customer"))
    addresses = [
        _dict(shipping.get("address")),
        _dict(raw.get("shipping_address")),
        _dict(order.get("shipping_address_raw")),
        _dict(order.get("shipping_address")),
        customer,
    ]
    candidates = [
        order.get("shipping_country_code"),
        order.get("shipping_country"),
        raw.get("shipping_country_code"),
        raw.get("shipping_country"),
    ]
    for address in addresses:
        candidates.extend((address.get("country_code"), address.get("country")))
    for candidate in candidates:
        market = _market(candidate)
        if market:
            return market
    return None


def _status(order: dict[str, Any]) -> str:
    raw = _dict(_dict(order.get("raw_by_source")).get("salla_direct"))
    status = raw.get("status")
    if isinstance(status, dict):
        status = status.get("slug") or status.get("name") or status.get("label")
    value = _text(order.get("order_status") or order.get("status") or status, 80)
    return value.casefold().replace("_", " ")


def _is_returned(order: dict[str, Any]) -> bool:
    status = _status(order)
    return any(token in status for token in ("refund", "return", "restored", "مسترجع", "استرجاع"))


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _delivery_days(order: dict[str, Any]) -> float | None:
    raw = _dict(_dict(order.get("raw_by_source")).get("salla_direct"))
    shipping = _dict(raw.get("shipping"))
    shipped = None
    delivered = None
    for value in (
        order.get("shipped_at"),
        shipping.get("shipped_at"),
        raw.get("shipped_at"),
    ):
        shipped = _datetime(value)
        if shipped:
            break
    for value in (
        order.get("delivered_at"),
        shipping.get("delivered_at"),
        raw.get("delivered_at"),
    ):
        delivered = _datetime(value)
        if delivered:
            break
    if not shipped or not delivered or delivered < shipped:
        return None
    return (delivered - shipped).total_seconds() / 86400.0


def _known_profit(ledger: dict[str, Any]) -> dict[str, float] | None:
    profit = _dict(ledger.get("profit"))
    if profit.get("known") is not True:
        return None
    required = {
        "revenue_sar": _number(profit.get("revenue_sar")),
        "cogs_sar": _number(profit.get("cogs_sar")),
        "shipping_sar": _number(profit.get("shipping_sar")),
        "fees_sar": _number(profit.get("fees_sar")),
        "allocated_ad_spend_sar": _number(profit.get("allocated_ad_spend_sar")),
    }
    if any(value is None for value in required.values()):
        return None
    return {key: float(value) for key, value in required.items() if value is not None}


def build_first_party_market_evidence(
    *,
    orders: list[dict[str, Any]],
    ledger_rows: list[dict[str, Any]],
    observed_days: int = 30,
) -> list[dict[str, Any]]:
    """Aggregate geographic Mezan evidence without inventing missing costs."""
    days = max(1, min(365, int(observed_days)))
    ledger_by_key = {
        _text(row.get("order_key"), 180): row
        for row in ledger_rows
        if isinstance(row, dict) and _text(row.get("order_key"), 180)
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for order in orders:
        if not isinstance(order, dict):
            continue
        market = _order_market(order)
        key = _order_key(order)
        if market and key:
            grouped[market].append(order)

    evidence: list[dict[str, Any]] = []
    for market, market_orders in grouped.items():
        known_rows: list[dict[str, float]] = []
        delivery_samples: list[float] = []
        returned_count = 0
        for order in market_orders:
            key = _order_key(order)
            profit = _known_profit(ledger_by_key.get(key, {}))
            if profit is not None:
                known_rows.append(profit)
            delivery = _delivery_days(order)
            if delivery is not None:
                delivery_samples.append(delivery)
            if _is_returned(order):
                returned_count += 1

        order_count = len(market_orders)
        known_count = len(known_rows)
        complete_profit_coverage = order_count > 0 and known_count == order_count
        confidence = "high" if order_count >= 20 else "medium" if order_count >= 5 else "low"
        measured = complete_profit_coverage and confidence in {"medium", "high"}

        def average(field: str) -> float | None:
            if not complete_profit_coverage:
                return None
            values = [row[field] for row in known_rows]
            return round(sum(values) / len(values), 2) if values else None

        expected_orders = round(order_count * (30.0 / days), 2) if order_count else None
        delivery_days = (
            round(sum(delivery_samples) / len(delivery_samples), 2)
            if delivery_samples
            else None
        )
        evidence.append({
            "market": market,
            "evidence_status": "measured" if measured else "partial",
            "confidence": confidence,
            "local_price_sar": average("revenue_sar"),
            "landed_product_cost_sar": average("cogs_sar"),
            "expected_cac_sar": average("allocated_ad_spend_sar"),
            "shipping_cost_sar": average("shipping_sar"),
            "payment_fee_sar": average("fees_sar"),
            "expected_return_rate": round(returned_count / order_count, 4) if order_count else None,
            "return_cost_per_return_sar": None,
            "expected_monthly_orders": expected_orders,
            "delivery_days": delivery_days,
            "demand_score": None,
            "competition_score": None,
            "product_fit_score": None,
            "price_sensitivity": "unknown",
            "first_party": {
                "orders": order_count,
                "known_profit_orders": known_count,
                "profit_coverage_ratio": round(known_count / order_count, 4) if order_count else 0.0,
                "returned_orders": returned_count,
                "delivery_samples": len(delivery_samples),
                "observed_days": days,
            },
            "source_provenance": [
                {
                    "source": "unified_orders",
                    "kind": "first_party_store",
                    "status": "measured",
                    "orders": order_count,
                    "observed_days": days,
                },
                {
                    "source": LEDGER_COLLECTION,
                    "kind": "first_party_profit",
                    "status": "measured" if complete_profit_coverage else "partial",
                    "known_profit_orders": known_count,
                    "orders": order_count,
                },
            ],
        })
    return evidence


def _observation_value(observation: dict[str, Any], field: str) -> Any:
    values = _dict(observation.get("values"))
    return values.get(field) if field in values else observation.get(field)


def merge_market_observations(
    *,
    first_party: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fill unknown fields only from explicit source-backed observations."""
    merged = {
        row["market"]: dict(row)
        for row in first_party
        if isinstance(row, dict) and _market(row.get("market"))
    }
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        market = _market(observation.get("market") or observation.get("country_code"))
        if not market:
            continue
        reliability = _text(observation.get("reliability"), 40).casefold()
        observed_at = _text(observation.get("observed_at"), 80)
        source_name = _text(observation.get("source_name"), 160)
        if reliability not in {"official", "first_party", "high"} or not observed_at or not source_name:
            continue

        row = merged.setdefault(market, {
            "market": market,
            "evidence_status": "partial",
            "confidence": "low",
            "local_price_sar": None,
            "landed_product_cost_sar": None,
            "expected_cac_sar": None,
            "shipping_cost_sar": None,
            "payment_fee_sar": None,
            "expected_return_rate": None,
            "return_cost_per_return_sar": None,
            "expected_monthly_orders": None,
            "delivery_days": None,
            "demand_score": None,
            "competition_score": None,
            "product_fit_score": None,
            "price_sensitivity": "unknown",
            "source_provenance": [],
        })
        provided = []
        for field in MEASURED_FIELDS:
            value = _observation_value(observation, field)
            if value is None:
                continue
            current = row.get(field)
            if current in (None, "", "unknown"):
                row[field] = value
                provided.append(field)

        provenance = row.setdefault("source_provenance", [])
        provenance.append({
            "source": source_name,
            "kind": _text(observation.get("source_type"), 80) or "external_observation",
            "reliability": reliability,
            "observed_at": observed_at,
            "url": _text(observation.get("url"), 500) or None,
            "fields": provided,
            "limitations": [
                _text(item, 240)
                for item in _list(observation.get("limitations"))
                if _text(item, 240)
            ][:20],
        })

        required = (
            "local_price_sar",
            "landed_product_cost_sar",
            "expected_cac_sar",
            "shipping_cost_sar",
            "payment_fee_sar",
            "expected_return_rate",
            "return_cost_per_return_sar",
            "expected_monthly_orders",
        )
        if all(row.get(field) is not None for field in required):
            row["evidence_status"] = "measured"
            if row.get("confidence") == "low":
                row["confidence"] = "medium"
    return list(merged.values())


async def ensure_indexes(db: Any) -> None:
    await db[OBSERVATION_COLLECTION].create_index(
        [("user_id", 1), ("market", 1), ("observed_at", -1)],
        name="gcc_market_observation_user_market_recent",
    )


async def load_gcc_market_evidence(
    db: Any,
    user_id: str,
    *,
    observed_days: int = 30,
    order_limit: int = 5000,
    observation_limit: int = 500,
) -> list[dict[str, Any]]:
    """Load tenant-scoped first-party + governed external evidence from Mezan."""
    await ensure_indexes(db)
    uid = str(user_id)
    orders = await db[ORDER_COLLECTION].find(
        {"user_id": uid, "raw_by_source.salla_direct": {"$exists": True}},
        {"_id": 0},
    ).sort("order_date", -1).limit(max(1, min(10000, int(order_limit)))).to_list(
        length=max(1, min(10000, int(order_limit)))
    )
    ledger_rows = await db[LEDGER_COLLECTION].find(
        {"user_id": uid}, {"_id": 0, "user_id": 0}
    ).sort("order_created_at", -1).limit(max(1, min(10000, int(order_limit)))).to_list(
        length=max(1, min(10000, int(order_limit)))
    )
    observations = await db[OBSERVATION_COLLECTION].find(
        {"user_id": uid}, {"_id": 0, "user_id": 0}
    ).sort("observed_at", -1).limit(max(1, min(2000, int(observation_limit)))).to_list(
        length=max(1, min(2000, int(observation_limit)))
    )
    first_party = build_first_party_market_evidence(
        orders=orders,
        ledger_rows=ledger_rows,
        observed_days=observed_days,
    )
    return merge_market_observations(first_party=first_party, observations=observations)


__all__ = [
    "CONTRACT_VERSION",
    "MARKET_BY_CODE",
    "OBSERVATION_COLLECTION",
    "build_first_party_market_evidence",
    "ensure_indexes",
    "load_gcc_market_evidence",
    "merge_market_observations",
]
