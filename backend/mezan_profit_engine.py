"""Authoritative read-only profit envelope for Mezan decision systems.

This module is the single Campaign-AI-facing contract for store P&L. It keeps
financial totals, component provenance, and accounting completeness together so
consumers do not independently reinterpret missing inputs as zero or rebuild the
meaning of net profit in multiple loaders.
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from auth import DEFAULT_PAYMENT_METHODS, DEFAULT_SHIPPING_COMPANIES, ensure_user_settings
from dashboard_v2_routes import (
    FINANCIAL_COST_COMPLETENESS_VERSION,
    _filtered_orders,
    build_mezan_v2_ads,
    build_mezan_v2_product_cost,
)
from excel_parser import match_settings
from expenses_routes import compute_operating_expenses_for_range
from orders_db import orders_to_parsed
from recurring_obligations_routes import compute_recurring_obligations_for_range
from shipping_cost_ssot import aggregate_breakdown, get_company_configs

CONTRACT_VERSION = "mezan_profit_envelope_v1"
SOURCE = "mezan_profit_engine_v2_read_only"
_FINANCIAL_COST_CONTRACT_KEYS = (
    "financial_cost_contract_version",
    "financial_cost_missing_products_count",
    "financial_cost_missing_lines_count",
    "financially_incomplete_orders_count",
)


def _number(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if parsed != parsed or abs(parsed) == float("inf"):
        return 0.0
    return parsed


def _optional_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed != parsed or abs(parsed) == float("inf"):
        return None
    return parsed


def _count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def _strict_count(value: Any) -> int | None:
    """Parse a versioned counter without coercing null/bool/string to zero."""
    return value if type(value) is int and value >= 0 else None


def read_financial_cost_completeness(
    source: dict[str, Any],
    *,
    legacy_missing_key: str,
    legacy_incomplete_key: str,
) -> dict[str, Any]:
    """Read the additive financial contract or conservatively parse legacy data."""
    financial_contract_fields = {
        key: source[key]
        for key in _FINANCIAL_COST_CONTRACT_KEYS
        if key in source
    }
    financial_contract_present = bool(financial_contract_fields)
    if financial_contract_present:
        version = source.get("financial_cost_contract_version")
        missing_products = _strict_count(
            source.get("financial_cost_missing_products_count")
        )
        missing_lines = _strict_count(
            source.get("financial_cost_missing_lines_count")
        )
        incomplete_orders = _strict_count(
            source.get("financially_incomplete_orders_count")
        )
        known = bool(
            version == FINANCIAL_COST_COMPLETENESS_VERSION
            and missing_products is not None
            and missing_lines is not None
            and incomplete_orders is not None
            and missing_products <= missing_lines
            and (missing_lines == 0 or incomplete_orders > 0)
        )
        counter_source = "financial_cost_contract"
    else:
        missing_products = _count(source.get(legacy_missing_key))
        missing_lines = None
        incomplete_orders = _count(source.get(legacy_incomplete_key))
        known = missing_products is not None and incomplete_orders is not None
        counter_source = "legacy_mezan_setup_conservative"
    return {
        "financial_cost_known": known,
        "financial_contract_present": financial_contract_present,
        "resolved_missing_products_count": missing_products,
        "resolved_missing_lines_count": missing_lines,
        "resolved_incomplete_orders_count": incomplete_orders,
        "financial_contract_fields": financial_contract_fields,
        "counter_source": counter_source,
    }


def _advertising_known(ads: dict[str, Any]) -> bool:
    """Return True only when the dashboard's financial ad amount is complete.

    ``build_mezan_v2_ads`` deliberately returns ``total=None`` when Snapchat
    coverage is incomplete. Presence of the key alone is therefore not proof
    that advertising spend is known. If a spend-quality contract is present we
    additionally require its explicit ``amount_complete`` proof.
    """
    if _optional_number(ads.get("total")) is None:
        return False
    spend_quality = ads.get("spend_quality")
    if isinstance(spend_quality, dict):
        return spend_quality.get("amount_complete") is True
    return True


def _accounting_quality(
    *,
    matched: dict[str, Any],
    shipping: dict[str, Any],
    product_cost: dict[str, Any],
    ads: dict[str, Any],
    operating: dict[str, Any],
    recurring: dict[str, Any],
) -> dict[str, Any]:
    cost_completeness = read_financial_cost_completeness(
        product_cost,
        legacy_missing_key="missing_products_count",
        legacy_incomplete_key="incomplete_orders_count",
    )
    missing = cost_completeness["resolved_missing_products_count"]
    incomplete = cost_completeness["resolved_incomplete_orders_count"]
    product_total = _optional_number(product_cost.get("total"))
    component_known = {
        "orders_sales": True,
        "product_cost": (
            product_total is not None
            and cost_completeness["financial_cost_known"] is True
        ),
        "advertising": _advertising_known(ads),
        "payment_fees": "total_payment_fees" in matched,
        "shipping": "total_with_tax" in shipping,
        "payroll": "salaries_total" in operating,
        "recurring_obligations": "total" in recurring,
    }
    known = all(component_known.values())
    complete = bool(known and missing == 0 and incomplete == 0)
    issues: list[str] = []
    for name, is_known in component_known.items():
        if not is_known:
            issues.append(f"unknown_component:{name}")
    if missing is not None and missing > 0:
        issues.append("missing_product_cost")
    if incomplete is not None and incomplete > 0:
        issues.append("incomplete_profit_orders")
    if (
        cost_completeness["financial_contract_present"]
        and cost_completeness["financial_cost_known"] is not True
    ):
        issues.append("invalid_financial_cost_contract")
    return {
        "known": known,
        "complete": complete,
        "scale_safe": complete,
        "missing_product_cost_count": missing,
        "incomplete_profit_orders_count": incomplete,
        "financial_cost_known": cost_completeness["financial_cost_known"],
        "financial_contract_present": cost_completeness[
            "financial_contract_present"
        ],
        "counter_source": cost_completeness["counter_source"],
        **cost_completeness["financial_contract_fields"],
        "mezan_setup_missing_products_count": _count(
            product_cost.get("mezan_setup_missing_products_count")
        ),
        "mezan_setup_missing_lines_count": _count(
            product_cost.get("mezan_setup_missing_lines_count")
        ),
        "mezan_setup_incomplete_orders_count": _count(
            product_cost.get("mezan_setup_incomplete_orders_count")
        ),
        "component_known": component_known,
        "issues": issues,
        "unknown_is_zero": False,
    }


async def build_mezan_profit_envelope(
    db: Any,
    user_id: str,
    *,
    from_date: str,
    to_date: str,
    payment_methods: str | None = None,
    shipping_companies: str | None = None,
) -> dict[str, Any]:
    """Return P&L totals and their accounting-quality contract in one object."""
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
    company_configs = await get_company_configs(db, user_id)
    shipping = aggregate_breakdown(orders, company_configs)

    product_cost, ads, operating, recurring = await asyncio.gather(
        build_mezan_v2_product_cost(db, user_id, orders),
        build_mezan_v2_ads(db, user_id, from_date=start_s, to_date=end_s),
        compute_operating_expenses_for_range(db, user_id, start, end),
        compute_recurring_obligations_for_range(db, user_id, start, end),
    )

    quality = _accounting_quality(
        matched=matched,
        shipping=shipping,
        product_cost=product_cost,
        ads=ads,
        operating=operating,
        recurring=recurring,
    )
    financial_contract_fields = {
        key: quality[key]
        for key in _FINANCIAL_COST_CONTRACT_KEYS
        if key in quality
    }
    advertising_known = quality["component_known"]["advertising"] is True

    payment_fees = _number(matched.get("total_payment_fees"))
    shipping_total = _number(shipping.get("total_with_tax"))
    ad_spend = _optional_number(ads.get("total")) if advertising_known else None
    ad_bank_fee = (
        _number((ads.get("bank_commissions") or {}).get("total_fee_sar"))
        if advertising_known
        else 0.0
    )
    payment_fees_with_ads = payment_fees + ad_bank_fee
    product_total = _number(product_cost.get("total"))
    salary_total = _number(operating.get("salaries_total"))
    recurring_total = _number(recurring.get("total"))
    operating_total = salary_total + recurring_total
    total_sales = round(sum(_number(order.get("total_amount")) for order in orders), 2)
    total_orders = len(orders)

    profit_before_advertising = round(
        total_sales
        - payment_fees
        - shipping_total
        - product_total
        - operating_total,
        2,
    )
    net_profit = (
        round(profit_before_advertising - ad_bank_fee - float(ad_spend), 2)
        if advertising_known and ad_spend is not None
        else None
    )

    source_contract = {
        "orders_sales": "unified_orders:mezan_v2",
        "product_cost": product_cost.get("source_contract") or {},
        "advertising": ads.get("source_contract") or {},
        "payment_fees": "settings.payment_methods + mezan_ad_account_cost_settings_v2",
        "shipping": "shipping_cost_ssot",
        "payroll": "mezan_employee_salary_contracts_v2",
        "recurring_obligations": "operating_recurring_obligations_v2",
    }
    totals = {
        "total_sales": total_sales,
        "total_orders": total_orders,
        "net_profit": net_profit,
        "profit_before_unknown_advertising_sar": (
            profit_before_advertising if not advertising_known else None
        ),
        "total_ads_cost": round(ad_spend, 2) if ad_spend is not None else None,
        "total_product_cost": round(product_total, 2),
        "total_payment_fees": round(payment_fees_with_ads, 2),
        "total_shipping_cost": round(shipping_total, 2),
        "operating_expenses_total": round(operating_total, 2),
        "overall_roas": (
            round(total_sales / ad_spend, 2)
            if ad_spend is not None and ad_spend > 0
            else None
        ),
        "avg_cost_per_order": (
            round(ad_spend / total_orders, 2)
            if ad_spend is not None and ad_spend > 0 and total_orders > 0
            else None
        ),
        "missing_product_cost_count": quality["missing_product_cost_count"],
        "incomplete_profit_orders_count": quality["incomplete_profit_orders_count"],
        "mezan_setup_missing_products_count": quality[
            "mezan_setup_missing_products_count"
        ],
        "mezan_setup_missing_lines_count": quality[
            "mezan_setup_missing_lines_count"
        ],
        "mezan_setup_incomplete_orders_count": quality[
            "mezan_setup_incomplete_orders_count"
        ],
        "profit_accounting_complete": quality["complete"],
        "profit_accounting_quality_known": quality["known"],
        "profit_source": SOURCE,
        "profit_contract_version": CONTRACT_VERSION,
        "profit_source_contract": source_contract,
        **financial_contract_fields,
    }
    return {
        "contract_version": CONTRACT_VERSION,
        "source": SOURCE,
        "period": {"from": start_s, "to": end_s},
        "totals": totals,
        "components": {
            "sales": {"amount_sar": total_sales, "orders": total_orders},
            "product_cost": {
                "amount_sar": round(product_total, 2),
                "mezan_setup_missing_products_count": quality[
                    "mezan_setup_missing_products_count"
                ],
                "mezan_setup_missing_lines_count": quality[
                    "mezan_setup_missing_lines_count"
                ],
                "mezan_setup_incomplete_orders_count": quality[
                    "mezan_setup_incomplete_orders_count"
                ],
                **financial_contract_fields,
            },
            "advertising": {
                "amount_sar": round(ad_spend, 2) if ad_spend is not None else None,
                "known": advertising_known,
                "known_subtotal_sar": ads.get("known_subtotal_sar"),
                "spend_quality": ads.get("spend_quality") or {},
            },
            "payment_fees": {"amount_sar": round(payment_fees_with_ads, 2)},
            "shipping": {"amount_sar": round(shipping_total, 2)},
            "payroll": {"amount_sar": round(salary_total, 2)},
            "recurring_obligations": {"amount_sar": round(recurring_total, 2)},
        },
        "quality": quality,
        "source_contract": source_contract,
        "read_only": True,
    }


async def build_mezan_profit_totals(
    db: Any,
    user_id: str,
    *,
    from_date: str,
    to_date: str,
    payment_methods: str | None = None,
    shipping_companies: str | None = None,
) -> dict[str, Any]:
    """Backward-compatible totals view backed by the consolidated envelope."""
    envelope = await build_mezan_profit_envelope(
        db,
        user_id,
        from_date=from_date,
        to_date=to_date,
        payment_methods=payment_methods,
        shipping_companies=shipping_companies,
    )
    return dict(envelope["totals"])


__all__ = [
    "CONTRACT_VERSION",
    "FINANCIAL_COST_COMPLETENESS_VERSION",
    "SOURCE",
    "build_mezan_profit_envelope",
    "build_mezan_profit_totals",
    "read_financial_cost_completeness",
]
