"""Campaign report costs, using the existing merchant shipping/payment rules.

No writes. Missing cost evidence is not a zero. Allocations are in integer
halalas and reconcile exactly to the order/campaign amount.
"""
from decimal import Decimal, ROUND_HALF_UP
from math import isfinite

from excel_parser import match_settings
from order_currency import order_total_sar
from orders_db import orders_to_parsed
from shipping_cost_ssot import shipping_breakdown


def number(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if isfinite(value) else None
    except (TypeError, ValueError):
        return None


def allocate(amount, weights):
    """Deterministic largest remainder allocation; unknown remains unknown."""
    if amount is None:
        return [None] * len(weights)
    if not weights:
        return []
    cents = int((Decimal(str(amount)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    positive = [Decimal(str(max(number(w) or 0, 0))) for w in weights]
    if not sum(positive):
        positive = [Decimal(1)] * len(weights)
    exact = [abs(cents) * w / sum(positive) for w in positive]
    parts = [int(v) for v in exact]
    for i in sorted(range(len(parts)), key=lambda i: (-(exact[i] - parts[i]), i))[:abs(cents) - sum(parts)]:
        parts[i] += 1
    return [p / 100 * (-1 if cents < 0 else 1) for p in parts]


def order_costs(order, settings, company_configs):
    shipping = shipping_breakdown(order, company_configs)
    shipping_cost = number(shipping.get("total")) if shipping.get("source") != "none" else None
    # An explicit free rate is evidence; an absent company/rate is not.
    company = str(order.get("shipping_company") or "").strip().strip("'\"")
    cfg = company_configs.get(company) or company_configs.get(company.lower()) or {}
    if shipping_cost is None and any(number(cfg.get(k)) == 0 for k in ("cost_per_order", "cost")):
        shipping_cost = 0.0
    fee = None
    fee_source = "missing"
    if order.get("payment_fee_status") == "actual":
        fee = number(order.get("actual_payment_fee"))
        fee_source = "actual" if fee is not None else "missing"
    elif order_total_sar(order) is not None:
        matched = match_settings(orders_to_parsed([order]), settings.get("payment_methods") or [], [])
        breakdown = matched.get("payment_breakdown") or []
        if breakdown and all(row.get("matched") is True for row in breakdown):
            fee = number(matched.get("total_payment_fees"))
            fee_source = "merchant_settings"
    return {"shipping_cost_sar": shipping_cost, "payment_fees_sar": fee,
            "payment_fee_source": fee_source, "shipping_cost_source": shipping.get("source")}


def add_order_report_costs(bucket, order_result, costs):
    """Called after the product engine has added this exact matched order."""
    lines = order_result.get("lines") or []
    for field in ("shipping_cost_sar", "payment_fees_sar"):
        value = costs[field]
        bucket.setdefault(field, 0.0)
        if bucket[field] is None or value is None:
            bucket[field] = None
        else:
            bucket[field] += value
        parts = allocate(value, [line.get("allocated_sales_sar") for line in lines])
        for line, part in zip(lines, parts):
            product = bucket["products"][line["identity"]]
            product.setdefault(field, 0.0)
            product[field] = None if product[field] is None or part is None else product[field] + part
    bucket.setdefault("report_cost_sources", set()).add(costs["payment_fee_source"])


def finalize_report_costs(result, raw, spend_sar):
    """Add the requested campaign net metric without redefining store P&L."""
    products = result.get("products") or []
    ad_parts = allocate(spend_sar, [p.get("sales_sar") for p in products])
    for product, ad in zip(products, ad_parts):
        source = raw.get("products", {}).get(product["identity"], {})
        product["allocated_ad_spend_sar"] = ad
        for field in ("shipping_cost_sar", "payment_fees_sar"):
            value = source.get(field, 0.0 if not raw.get("orders") else None)
            product[field] = round(value, 2) if value is not None else None
        _net(product, ad)
    for field in ("shipping_cost_sar", "payment_fees_sar"):
        value = raw.get(field, 0.0 if not raw.get("orders") else None)
        result[field] = round(value, 2) if value is not None else None
    result["ad_spend_sar"] = spend_sar
    _net(result, spend_sar)
    result["payment_fee_sources"] = sorted(raw.get("report_cost_sources") or [])
    result["profit_scope"] = "sales_minus_product_advertising_shipping_and_payment_costs"
    result["allocation_method"] = "order_costs_by_line_sales_then_campaign_ads_by_product_sales_largest_remainder"
    result["operating_expenses_included"] = False
    return result


def _net(row, spend):
    parts = [number(row.get(k)) for k in ("sales_sar", "product_cost_sar", "shipping_cost_sar", "payment_fees_sar")]
    net = round(parts[0] - sum(parts[1:]) - spend, 2) if None not in parts and spend is not None else None
    row["net_profit_sar"] = net
    row["net_margin_pct"] = round(net / parts[0] * 100, 2) if net is not None and parts[0] > 0 else None
    row["net_profit_available"] = net is not None
