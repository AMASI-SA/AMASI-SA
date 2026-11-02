"""Read-only attribution/profit bridge plus internal ledger backfill.

This module connects the order-level attribution ledger to Mezan's authoritative
store Profit Engine without inventing financial allocation. Store P&L remains
store-level truth; campaign net profit is summed only from ledger rows that
already carry authoritative per-order profit facts. Product summaries expose
orders, units and line sales, but do not allocate order net profit across
products unless a future authoritative product-profit contract exists.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal
from typing import Any

from mezan_attribution_ledger_sync import safe_sync_order_to_attribution_ledger
from mezan_attribution_order_ledger import LEDGER_COLLECTION

CONTRACT_VERSION = "mezan_attribution_profit_bridge_v1"
COST_PROJECTION_VERSION = "mezan_historical_cost_report_projection_v1"

# No customer payload is required to join the attribution key to a canonical
# order number or to resolve its immutable cost and existing return policy.
COST_ORDER_PROJECTION = {key: 1 for key in (
    "user_id", "order_id", "id", "reference_id", "order_number", "products", "items",
    "order_status", "total_amount", "total", "total_amount_sar", "currency", "original_currency",
    "original_total_amount", "accounting_currency", "exchange_rate_to_sar", "exchange_rate",
    "currency_conversion_status", "actual_refund_amount", "actual_partial_refund_amount",
    "raw_by_source.salla_direct.id", "raw_by_source.salla_direct.reference_id",
    "raw_by_source.salla_direct.order_number", "raw_by_source.salla_direct.amounts.total",
    "raw_by_source.salla_direct.total_amount", "raw_by_source.salla_direct.currency", "raw_by_source.salla_direct.exchange_rate",
)}
COST_ORDER_PROJECTION["_id"] = 0


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


async def project_ledger_historical_costs(
    db: Any, user_id: str, ledger_rows: list[dict[str, Any]], *, orders: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Overlay frozen COGS on a report copy; never rewrite attribution facts.

    The ledger's key uses the producer's exact canonical identity precedence.
    An arbitrary matching alias, SKU or customer name cannot establish a join.
    Only complete historical order costs can replace a saved per-order COGS.
    The existing net amount changes by old COGS minus new COGS once; other
    already-established financial facts keep their original values.
    """
    from dashboard_cpu_budget import CPUWorkBudget, COST_PROFIT_BUDGET_MS
    from mezan_attribution_order_ledger import _order_key
    from order_cost_authority import (
        decimal, line_aliases, line_id, load_order_cost_authority, order_items, resolve_order_line_cost,
    )
    from order_currency import order_total_sar
    from order_status_policy import effective_product_cost

    projected = deepcopy(ledger_rows)
    coverage = {"contract_version": COST_PROJECTION_VERSION, "currency": "SAR",
                "ledger_orders": len(projected), "historical_cost_verified_orders": 0,
                "adjusted_orders": 0, "unresolved_orders": 0, "legacy_unverified_orders": 0,
                "reasons": {}, "read_only": True, "return_policy": "once_per_order"}
    reasons = Counter()
    eligible = []
    key_counts = Counter(str(row.get("order_key") or "") for row in projected)

    def unresolved(row, reason, *, preserve_legacy=False):
        profit = row.get("profit") if isinstance(row.get("profit"), dict) else {}
        if not preserve_legacy:
            profit.update(known=False, cogs_sar=None, net_profit_sar=None)
        profit["cost_projection"] = {"contract_version": COST_PROJECTION_VERSION,
            "status": "legacy_unverified" if preserve_legacy else "unresolved", "reason": reason,
            "historical_cost_verified": False, "read_only": True}
        row["profit"] = profit
        coverage["legacy_unverified_orders" if preserve_legacy else "unresolved_orders"] += 1
        reasons[reason] += 1

    for row in projected:
        key = str(row.get("order_key") or "")
        profit = row.get("profit") if isinstance(row.get("profit"), dict) else {}
        cogs = _number(profit.get("cogs_sar"))
        if row.get("user_id") not in (None, user_id):
            unresolved(row, "ledger_owner_mismatch")
        elif not key or key_counts[key] != 1:
            unresolved(row, "ledger_order_identity_ambiguous")
        elif cogs is None or cogs < 0:
            # A legacy independently-established net cannot be adjusted without
            # its original COGS. Keep it visibly unverified, never certify it as
            # reconciled to the shared historical authority.
            unresolved(row, "ledger_cogs_missing", preserve_legacy=True)
        else:
            eligible.append(row)

    if eligible and orders is None:
        orders = []
        keys = sorted({str(row["order_key"]) for row in eligible})
        identity_fields = ("order_id", "id", "reference_id", "order_number",
                           "raw_by_source.salla_direct.id", "raw_by_source.salla_direct.reference_id",
                           "raw_by_source.salla_direct.order_number")
        for offset in range(0, len(keys), 500):
            selected = keys[offset:offset + 500]
            values = selected + [int(key) for key in selected if key.isascii() and key.isdigit() and len(key) < 19]
            limit = max(2000, len(selected) * 4)
            batch = await _rows(db.unified_orders.find({"user_id": user_id,
                "$or": [{field: {"$in": values}} for field in identity_fields]}, COST_ORDER_PROJECTION), limit=limit + 1)
            if len(batch) > limit:
                raise ValueError("historical_cost_order_identity_read_limit")
            # The broad aliases only find candidates. Keep the producer's
            # exact key so one order matched by aliases in separate batches
            # cannot masquerade as two canonical documents.
            selected_keys = set(selected)
            orders.extend(order for order in batch if _order_key(order) in selected_keys)
    order_map, number_counts = defaultdict(list), Counter()
    for order in orders or []:
        if order.get("user_id") not in (None, user_id):
            continue
        order_map[_order_key(order)].append(order)
        number_counts[str(order.get("order_number") or "")] += 1
    matched, selected_orders = {}, []
    for row in eligible:
        candidates = order_map.get(str(row["order_key"])) or []
        if len(candidates) != 1:
            unresolved(row, "canonical_order_unmapped" if not candidates else "canonical_order_ambiguous")
            continue
        order = candidates[0]
        number = str(order.get("order_number") or "")
        if not number or number_counts[number] != 1:
            unresolved(row, "canonical_order_number_ambiguous")
            continue
        items = order_items(order)
        aliases = [alias for item in items for alias in line_aliases(item)]
        if (not items or any(not line_id(item) or not decimal(item.get("quantity", 1)) for item in items)
                or len(set(aliases)) != len(aliases)):
            unresolved(row, "canonical_order_lines_unproven")
            continue
        matched[str(row["order_key"])] = order
        selected_orders.append(order)
    if selected_orders:
        budget = CPUWorkBudget(COST_PROFIT_BUDGET_MS)
        authority = await load_order_cost_authority(db, user_id, selected_orders, budget=budget)
        policy_rows = await _rows(db["order_status_policy"].find({"user_id": user_id},
            {"_id": 0, "status": 1, "category": 1}), limit=1001)
        if len(policy_rows) > 1000:
            raise ValueError("historical_cost_status_policy_read_limit")
        policy = {str(row["status"]).strip(): row["category"] for row in policy_rows
                  if str(row.get("status") or "").strip() and row.get("category") in {"confirmed", "pending", "cancelled", "refunded"}}
        for row in eligible:
            order = matched.get(str(row["order_key"]))
            if order is None:
                continue
            await budget.checkpoint()
            raw_cost, complete = Decimal(0), True
            for item in order_items(order):
                cost = resolve_order_line_cost(item, order_number=str(order["order_number"]),
                    catalog_cost={"historical_cost_verified": False, "calculation_cost_available": False},
                    authority=authority, product_id=item.get("parent_product_id") or item.get("product_id"))
                amount = decimal(cost.get("line_total"))
                if not cost.get("historical_cost_verified") or not cost.get("calculation_cost_available") or amount is None:
                    complete = False
                    break
                raw_cost += amount
            if not complete:
                unresolved(row, "historical_order_cost_unresolved")
                continue
            if (_number(order.get("actual_partial_refund_amount")) or 0) > 0 and order_total_sar(order) is None:
                unresolved(row, "refund_currency_unverified")
                continue
            new_cost = effective_product_cost({**order, "total_product_cost": float(raw_cost)}, policy)
            profit = row["profit"]
            previous_cost = _number(profit["cogs_sar"])
            delta = Decimal(str(new_cost)) - Decimal(str(previous_cost))
            net = _number(profit.get("net_profit_sar"))
            if net is not None:
                profit["net_profit_sar"] = round(float(Decimal(str(net)) - delta), 2)
            profit["cogs_sar"] = new_cost
            profit["cost_projection"] = {"contract_version": COST_PROJECTION_VERSION, "status": "verified",
                "historical_cost_verified": True, "previous_cogs_sar": previous_cost,
                "effective_cogs_sar": new_cost, "cost_change_sar": round(float(delta), 2),
                "source": "order_cost_authority", "return_policy": "once_per_order", "read_only": True}
            coverage["historical_cost_verified_orders"] += 1
            coverage["adjusted_orders"] += int(delta != 0)
    coverage["reasons"] = dict(sorted(reasons.items()))
    coverage["complete"] = coverage["historical_cost_verified_orders"] == len(projected)
    return projected, coverage


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
    cost_unverified = 0
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
        unverified_cost = (profit.get("cost_projection") or {}).get("status") in {"unresolved", "legacy_unverified"}
        cost_unverified += int(unverified_cost)
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
                "cost_unverified_orders": 0,
                "known_net_profit_sar": 0.0,
            })
            item["orders"] += 1
            item["confirmed_orders"] += int(quality == "confirmed")
            item["decision_safe_orders"] += int(safe)
            item["cost_unverified_orders"] += int(unverified_cost)
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
        item["known_net_profit_is_partial"] = item["profit_known_orders"] < item["orders"] or item["cost_unverified_orders"] > 0
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
            "cost_unverified_orders": cost_unverified,
            "profit_coverage_pct": round((profit_known / total) * 100, 2) if total else 0.0,
        },
        "known_order_profit": {
            "net_profit_sar": round(known_net_profit, 2),
            "partial": profit_known < total or cost_unverified > 0,
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
    rows, cost_coverage = await project_ledger_historical_costs(db, user_id, rows)
    attribution = aggregate_attribution_ledger(rows)
    attribution["coverage"]["historical_order_cost"] = cost_coverage
    if not cost_coverage["complete"]:
        attribution["known_order_profit"]["partial"] = True
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
                and cost_coverage["complete"]
            ),
            "difference_sar": (
                round(store_net - known_order_net, 2)
                if store_net is not None
                and known_order_net is not None
                and attribution["coverage"]["profit_unknown_orders"] == 0
                and attribution["coverage"]["ledger_orders"] > 0
                and cost_coverage["complete"]
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
    "project_ledger_historical_costs",
    "refresh_existing_orders_to_ledger",
]
