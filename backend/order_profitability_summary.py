"""Bounded, read-only single-order profitability; no provider calls or refresh jobs."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException

from dashboard_cpu_budget import CPUWorkBudget, COST_PROFIT_BUDGET_MS
from marketing_report_costs import number, order_costs
from mezan_attribution_ledger_sync import profit_facts_from_order
from order_cost_authority import calculate_catalog_line_cost, load_order_cost_authority, order_items, resolve_order_line_cost
from order_cost_reporting_repair import _catalog_context
from order_currency import hydrate_order_currency_fields, order_total_sar
from order_status_policy import default_category_for
from product_catalog_cost_resolution import resolve_current_catalog_line_product
from report_option_recovery import attach_report_option_evidence


def money_sum(values):
    return float(sum((Decimal(str(value)) for value in values), Decimal(0)).quantize(Decimal("0.01")))


def summarize_order_profitability(order, lines, costs):
    reasons = sorted({reason for line in lines for reason in line.get("missing_cost_reasons", [])})
    products_complete = bool(lines) and all(line.get("mezan_cost_complete") is True
        and number(line.get("line_total")) is not None for line in lines)
    known_products = money_sum(number(line.get("line_total")) or 0 for line in lines)
    if not products_complete:
        reasons.append("product_cost_incomplete")
    shipping, fees = number(costs.get("shipping_cost_sar")), number(costs.get("payment_fees_sar"))
    if shipping is None:
        reasons.append("shipping_cost_missing")
    if fees is None:
        reasons.append("payment_fees_missing")
    revenue = order_total_sar(order)
    if revenue is None:
        reasons.append("order_currency_unverified")
    # Do not invent reversal/cost allocations for returned or cancelled orders.
    adjusted = default_category_for(order.get("order_status")) in {"cancelled", "refunded"} or any(
        (number(order.get(field)) or 0) > 0 for field in ("actual_refund_amount", "actual_partial_refund_amount"))
    if adjusted:
        reasons.append("refund_cost_reconciliation_required")
    facts = profit_facts_from_order(order) or {}
    advertising = number(facts.get("allocated_ad_spend_sar"))
    # Saved allocation is usable only for the exact current revenue; it remains
    # a disclosed allocation, never a platform-reported individual purchase cost.
    if revenue is None or number(facts.get("revenue_sar")) != revenue or advertising is not None and advertising < 0:
        advertising = None
    direct_complete = products_complete and shipping is not None and fees is not None and not adjusted
    direct_known = money_sum([known_products, shipping or 0, fees or 0])
    direct_total = direct_known if direct_complete else None
    total = money_sum([direct_total, advertising]) if direct_total is not None and advertising is not None else None
    before_ads = money_sum([revenue, -direct_total]) if revenue is not None and direct_total is not None else None
    profit = money_sum([revenue, -total]) if revenue is not None and total is not None else None
    return {"order_number": str(order["order_number"]), "currency": "SAR", "read_only": True,
        "basis": "current_mezan_costs_with_frozen_actual_precedence", "revenue_sar": revenue,
        "product_cost_sar": known_products if products_complete else None,
        "known_product_cost_sar": known_products, "shipping_cost_sar": shipping, "payment_fees_sar": fees,
        "advertising_cost_sar": advertising, "advertising_basis": "saved_order_allocation" if advertising is not None else "unavailable",
        "direct_cost_sar": direct_total, "known_direct_cost_sar": direct_known,
        "known_total_cost_sar": money_sum([direct_known, advertising or 0]),
        "total_cost_sar": total, "profit_before_advertising_sar": before_ads, "profit_sar": profit,
        "direct_cost_complete": direct_complete, "complete": total is not None and revenue is not None,
        "reasons": sorted(set(reasons)), "cost_sources": costs,
        "profit_scope": "order_margin_before_shared_operating_expenses",
        "generated_at": datetime.now(timezone.utc).isoformat()}


async def build_order_profitability_summary(db, owner, order_number):
    order = await db["unified_orders"].find_one({"user_id": owner, "order_number": order_number}, {"_id": 0})
    if not order:
        raise HTTPException(404, detail={"code": "order_not_found"})
    order = hydrate_order_currency_fields([order])[0]
    attach_report_option_evidence([order], [order])
    budget = CPUWorkBudget(COST_PROFIT_BUDGET_MS)
    context, authority, settings = await asyncio.gather(
        _catalog_context(db, owner, [order]),
        load_order_cost_authority(db, owner, [order], budget=budget, current_profitability_estimate=True),
        db["settings"].find_one({"user_id": owner}, {"_id": 0, "payment_methods": 1, "shipping_companies": 1}),
    )
    settings = settings or {}
    by_id, by_variant, by_sku = context["indexes"]
    lines = []
    for item in order_items(order):
        await budget.checkpoint()
        product = resolve_current_catalog_line_product(item, products_by_id=by_id,
            products_by_variant=by_variant, products_by_sku=by_sku)
        identity = str((product or {}).get("salla_product_id") or "")
        cost = calculate_catalog_line_cost(item, product=product, profile=context["profiles"].get(identity),
            product_bindings=context["product_bindings"].get(identity, []),
            option_bindings=context["option_bindings"].get(identity, []), resources=context["resources"], current_report=True)
        lines.append(resolve_order_line_cost(item, order_number=order_number, catalog_cost=cost,
            authority=authority, product_id=identity))
    configs = {}
    for row in settings.get("shipping_companies") or []:
        name = str(row.get("name") or "").strip().strip("'\"")
        if name:
            configs[name] = configs[name.lower()] = row
    return summarize_order_profitability(order, lines, order_costs(order, settings, configs))
