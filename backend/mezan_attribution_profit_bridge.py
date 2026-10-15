"""Read-only attribution/profit bridge plus internal ledger backfill.

This module connects the order-level attribution ledger to Mezan's authoritative
store Profit Engine without inventing financial allocation. Store P&L remains
store-level truth; campaign net profit is summed only from ledger rows that
already carry authoritative per-order profit facts. Product summaries expose
orders, units and line sales, but do not allocate order net profit across
products unless a future authoritative product-profit contract exists.
"""
from __future__ import annotations

from typing import Any

from mezan_attribution_ledger_sync import safe_sync_order_to_attribution_ledger
from mezan_attribution_order_ledger import LEDGER_COLLECTION

CONTRACT_VERSION = "mezan_attribution_profit_bridge_v1"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed != parsed or abs(parsed) == float("inf"):
        return None
    return parsed


async def _rows(cursor: Any, *, limit: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=limit)
    result: list[dict[str, Any]] = []
    async for row in cursor:
        result.append(row)
        if len(result) >= limit:
            break
    return result


async def refresh_existing_orders_to_ledger(
    db: Any,
    user_id: str,
    *,
    limit: int = 5000,
) -> dict[str, Any]:
    """Idempotently refresh existing unified orders into the internal ledger.

    A single failed order is counted and skipped; later rows continue. This
    performs no provider/Salla/Qoyod writes and never changes order records.
    """
    cap = max(1, min(50_000, int(limit)))
    cursor = db.unified_orders.find({"user_id": user_id}, {"_id": 0}).sort(
        "updated_at", -1
    ).limit(cap)
    orders = await _rows(cursor, limit=cap)
    synced = 0
    failed = 0
    profit_known = 0
    decision_safe = 0
    failures: list[dict[str, Any]] = []
    for order in orders:
        result = await safe_sync_order_to_attribution_ledger(
            db,
            user_id=user_id,
            order=order,
        )
        if result.get("synced") is True:
            synced += 1
            profit_known += int(result.get("profit_known") is True)
            decision_safe += int(result.get("decision_safe") is True)
        else:
            failed += 1
            if len(failures) < 50:
                failures.append({
                    "order_number": order.get("order_number") or order.get("order_id"),
                    "reason": result.get("reason") or "ledger_sync_failed",
                    "error_type": result.get("error_type"),
                })
    return {
        "contract_version": CONTRACT_VERSION,
        "scanned": len(orders),
        "synced": synced,
        "failed": failed,
        "profit_known": profit_known,
        "decision_safe": decision_safe,
        "failures": failures,
        "external_writes": False,
    }


def aggregate_attribution_ledger(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate ledger facts while preserving unknown financial truth."""
    campaigns: dict[tuple[str, str, str], dict[str, Any]] = {}
    products: dict[tuple[str, str, str], dict[str, Any]] = {}
    total = 0
    confirmed = 0
    decision_safe = 0
    profit_known = 0
    known_net_profit = 0.0

    for row in rows:
        if not isinstance(row, dict):
            continue
        total += 1
        attribution = row.get("attribution") if isinstance(row.get("attribution"), dict) else {}
        quality = str(attribution.get("quality") or "")
        safe = attribution.get("decision_safe") is True
        confirmed += int(quality == "confirmed")
        decision_safe += int(safe)

        profit = row.get("profit") if isinstance(row.get("profit"), dict) else {}
        net = _number(profit.get("net_profit_sar"))
        known = profit.get("known") is True and net is not None
        if known:
            profit_known += 1
            known_net_profit += net

        campaign_id = str(attribution.get("campaign_id") or "").strip()
        if campaign_id:
            provider = str(attribution.get("provider") or "unknown")
            account_id = str(attribution.get("account_id") or "")
            key = (provider, account_id, campaign_id)
            item = campaigns.setdefault(key, {
                "provider": provider,
                "account_id": account_id or None,
                "campaign_id": campaign_id,
                "campaign_name": attribution.get("campaign_name"),
                "orders": 0,
                "confirmed_orders": 0,
                "decision_safe_orders": 0,
                "profit_known_orders": 0,
                "known_net_profit_sar": 0.0,
            })
            item["orders"] += 1
            item["confirmed_orders"] += int(quality == "confirmed")
            item["decision_safe_orders"] += int(safe)
            if known:
                item["profit_known_orders"] += 1
                item["known_net_profit_sar"] += net

        seen_product_keys: set[tuple[str, str, str]] = set()
        for line in row.get("line_items") or []:
            if not isinstance(line, dict):
                continue
            product_id = str(line.get("product_id") or "").strip()
            variant_id = str(line.get("product_variant_id") or "").strip()
            sku = str(line.get("sku") or "").strip()
            if not product_id and not variant_id and not sku:
                continue
            pkey = (product_id, variant_id, sku)
            product = products.setdefault(pkey, {
                "product_id": product_id or None,
                "product_variant_id": variant_id or None,
                "sku": sku or None,
                "product_name": line.get("product_name"),
                "orders": 0,
                "units": 0.0,
                "line_sales_sar": 0.0,
                "line_sales_known": True,
                "net_profit_sar": None,
                "profit_allocation": "not_allocated_without_authoritative_product_profit",
            })
            if pkey not in seen_product_keys:
                product["orders"] += 1
                seen_product_keys.add(pkey)
            quantity = _number(line.get("quantity"))
            if quantity is not None:
                product["units"] += quantity
            line_sales = _number(line.get("line_total_sar"))
            if line_sales is None:
                product["line_sales_known"] = False
            else:
                product["line_sales_sar"] += line_sales

    campaign_rows = []
    for item in campaigns.values():
        item["known_net_profit_sar"] = round(item["known_net_profit_sar"], 2)
        item["profit_coverage_pct"] = round(
            (item["profit_known_orders"] / item["orders"]) * 100, 2
        ) if item["orders"] else 0.0
        item["known_net_profit_is_partial"] = item["profit_known_orders"] < item["orders"]
        campaign_rows.append(item)
    campaign_rows.sort(key=lambda item: (-item["known_net_profit_sar"], item["campaign_id"]))

    product_rows = []
    for item in products.values():
        item["units"] = round(item["units"], 2)
        item["line_sales_sar"] = round(item["line_sales_sar"], 2) if item["line_sales_known"] else None
        product_rows.append(item)
    product_rows.sort(key=lambda item: (-item["units"], str(item.get("product_name") or "")))

    return {
        "contract_version": CONTRACT_VERSION,
        "coverage": {
            "ledger_orders": total,
            "confirmed_orders": confirmed,
            "decision_safe_orders": decision_safe,
            "profit_known_orders": profit_known,
            "profit_unknown_orders": max(0, total - profit_known),
            "profit_coverage_pct": round((profit_known / total) * 100, 2) if total else 0.0,
        },
        "known_order_profit": {
            "net_profit_sar": round(known_net_profit, 2),
            "partial": profit_known < total,
            "unknown_is_zero": False,
        },
        "campaigns": campaign_rows,
        "products": product_rows,
        "guardrails": {
            "store_profit_redistributed_to_orders": False,
            "order_profit_allocated_to_products": False,
            "unknown_profit_treated_as_zero": False,
        },
    }


async def load_ledger_rows_for_period(
    db: Any,
    user_id: str,
    *,
    from_date: str,
    to_date: str,
    limit: int = 20_000,
) -> list[dict[str, Any]]:
    cap = max(1, min(50_000, int(limit)))
    start, end = sorted((str(from_date), str(to_date)))
    query = {
        "user_id": user_id,
        "order_created_at": {
            "$gte": f"{start}T00:00:00+00:00",
            "$lte": f"{end}T23:59:59.999999+00:00",
        },
    }
    cursor = db[LEDGER_COLLECTION].find(query, {"_id": 0}).sort("order_created_at", 1).limit(cap)
    return await _rows(cursor, limit=cap)


async def build_attribution_profit_bridge(
    db: Any,
    user_id: str,
    *,
    from_date: str,
    to_date: str,
    limit: int = 20_000,
) -> dict[str, Any]:
    """Return authoritative store P&L beside coverage-safe attribution facts."""
    from mezan_profit_engine import build_mezan_profit_envelope

    envelope = await build_mezan_profit_envelope(
        db,
        user_id,
        from_date=from_date,
        to_date=to_date,
    )
    rows = await load_ledger_rows_for_period(
        db,
        user_id,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
    )
    attribution = aggregate_attribution_ledger(rows)
    store_net = _number((envelope.get("totals") or {}).get("net_profit"))
    known_order_net = _number((attribution.get("known_order_profit") or {}).get("net_profit_sar"))
    return {
        "contract_version": CONTRACT_VERSION,
        "period": {"from": min(from_date, to_date), "to": max(from_date, to_date)},
        "store_profit_envelope": envelope,
        "attribution": attribution,
        "reconciliation": {
            "store_net_profit_sar": store_net,
            "known_order_net_profit_sar": known_order_net,
            "comparable_as_full_total": (
                attribution["coverage"]["profit_unknown_orders"] == 0
                and attribution["coverage"]["ledger_orders"] > 0
            ),
            "difference_sar": (
                round(store_net - known_order_net, 2)
                if store_net is not None
                and known_order_net is not None
                and attribution["coverage"]["profit_unknown_orders"] == 0
                and attribution["coverage"]["ledger_orders"] > 0
                else None
            ),
        },
        "read_only_external_systems": True,
    }


__all__ = [
    "CONTRACT_VERSION",
    "aggregate_attribution_ledger",
    "build_attribution_profit_bridge",
    "load_ledger_rows_for_period",
    "refresh_existing_orders_to_ledger",
]
