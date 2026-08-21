"""Read-only Mezan profit loader for Campaign AI.

Campaign AI runs in a separate child process, so it cannot rely on the FastAPI
route closure that builds Dashboard V2.  This module reconstructs the same
merchant P&L inputs from Mezan's authoritative read paths and exposes the
legacy dashboard loader contract expected by Campaign AI.

No provider, order, accounting, Qoyod, or catalog writes occur here.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Awaitable, Callable

from auth import DEFAULT_PAYMENT_METHODS, DEFAULT_SHIPPING_COMPANIES, ensure_user_settings
from dashboard_v2_routes import (
    _filtered_orders,
    build_mezan_v2_ads,
    build_mezan_v2_product_cost,
)
from excel_parser import match_settings
from expenses_routes import compute_operating_expenses_for_range
from orders_db import orders_to_parsed
from recurring_obligations_routes import compute_recurring_obligations_for_range
from shipping_cost_ssot import aggregate_breakdown, get_company_configs


def _number(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if parsed != parsed or abs(parsed) == float("inf"):
        return 0.0
    return parsed


async def build_mezan_profit_totals(
    db: Any,
    user_id: str,
    *,
    from_date: str,
    to_date: str,
    payment_methods: str | None = None,
    shipping_companies: str | None = None,
) -> dict[str, Any]:
    """Build the Campaign AI P&L totals from Mezan-owned data sources.

    The semantics intentionally match Dashboard V2's current net-profit model:
    sales minus payment fees, shipping, Mezan V2 product cost, Mezan V2 ad
    spend, payroll, and V2 recurring obligations.  Ad-account bank commissions
    are included once in payment fees, as Dashboard V2 does.
    """
    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date)
    if end < start:
        start, end = end, start
    start_s = start.isoformat()
    end_s = end.isoformat()

    orders = await _filtered_orders(
        db,
        user_id,
        from_date=start_s,
        to_date=end_s,
        payment_methods=payment_methods,
        shipping_companies=shipping_companies,
        include_marketing_attribution=False,
    )
    settings = await ensure_user_settings(db, user_id)

    parsed = orders_to_parsed(orders)
    matched = match_settings(
        parsed,
        settings.get("payment_methods", DEFAULT_PAYMENT_METHODS),
        settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES),
    )
    payment_fees = _number(matched.get("total_payment_fees"))

    company_configs = await get_company_configs(db, user_id)
    shipping = aggregate_breakdown(orders, company_configs)
    shipping_total = _number(shipping.get("total_with_tax"))

    product_cost, ads, operating, recurring = await __import__("asyncio").gather(
        build_mezan_v2_product_cost(db, user_id, orders),
        build_mezan_v2_ads(
            db,
            user_id,
            from_date=start_s,
            to_date=end_s,
        ),
        compute_operating_expenses_for_range(db, user_id, start, end),
        compute_recurring_obligations_for_range(db, user_id, start, end),
    )

    ad_spend = _number(ads.get("total"))
    ad_bank_fee = _number((ads.get("bank_commissions") or {}).get("total_fee_sar"))
    payment_fees_with_ads = payment_fees + ad_bank_fee
    product_total = _number(product_cost.get("total"))

    # Dashboard V2 retains payroll from the operating-expense engine and uses
    # V2 recurring obligations as the authoritative non-payroll recurring cost.
    salary_total = _number(operating.get("salaries_total"))
    recurring_total = _number(recurring.get("total"))
    operating_total = salary_total + recurring_total

    total_sales = round(sum(_number(order.get("total_amount")) for order in orders), 2)
    total_orders = len(orders)
    net_profit = round(
        total_sales
        - payment_fees_with_ads
        - shipping_total
        - product_total
        - ad_spend
        - operating_total,
        2,
    )

    return {
        "total_sales": total_sales,
        "total_orders": total_orders,
        "net_profit": net_profit,
        "total_ads_cost": round(ad_spend, 2),
        "total_product_cost": round(product_total, 2),
        "total_payment_fees": round(payment_fees_with_ads, 2),
        "total_shipping_cost": round(shipping_total, 2),
        "operating_expenses_total": round(operating_total, 2),
        "overall_roas": round(total_sales / ad_spend, 2) if ad_spend > 0 else None,
        "avg_cost_per_order": (
            round(ad_spend / total_orders, 2)
            if ad_spend > 0 and total_orders > 0
            else None
        ),
        "missing_product_cost_count": int(product_cost.get("missing_products_count") or 0),
        "incomplete_profit_orders_count": int(product_cost.get("incomplete_orders_count") or 0),
        "profit_source": "mezan_profit_engine_v2_read_only",
        "profit_source_contract": {
            "orders_sales": "unified_orders:mezan_v2",
            "product_cost": (product_cost.get("source_contract") or {}),
            "advertising": (ads.get("source_contract") or {}),
            "payment_fees": "settings.payment_methods + mezan_ad_account_cost_settings_v2",
            "shipping": "shipping_cost_ssot",
            "payroll": "mezan_employee_salary_contracts_v2",
            "recurring_obligations": "operating_recurring_obligations_v2",
        },
    }


def make_mezan_campaign_profit_loader(db: Any) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the loader contract consumed by Campaign AI profit context."""

    async def loader(
        *,
        user: dict[str, Any],
        from_date: str,
        to_date: str,
        payment_methods: str | None = None,
        shipping_companies: str | None = None,
        include_legacy_analyses: bool = False,
        allow_self_heal: bool = False,
    ) -> dict[str, Any]:
        # The final two flags are accepted for compatibility with the legacy
        # dashboard loader contract.  This loader is read-only by design.
        del include_legacy_analyses, allow_self_heal
        user_id = str(user.get("id") or "").strip()
        if not user_id:
            raise ValueError("mezan_profit_loader_user_required")
        totals = await build_mezan_profit_totals(
            db,
            user_id,
            from_date=from_date,
            to_date=to_date,
            payment_methods=payment_methods,
            shipping_companies=shipping_companies,
        )
        return {
            "totals": totals,
            "dashboard_source": "mezan_profit_engine_v2_read_only",
            "source_only": True,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }

    return loader


__all__ = ["build_mezan_profit_totals", "make_mezan_campaign_profit_loader"]
