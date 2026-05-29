"""Shared analysis-report builder.

Lives in its own module to break the circular dependency between
`server.py` and `webhook_routes.py`. Both modules import `build_report`
from here directly.
"""
from excel_parser import match_settings


def build_report(parsed: dict, payment_settings, shipping_settings,
                 snapchat_ads: float = 0.0, tiktok_ads: float = 0.0,
                 instagram_ads: float = 0.0, product_costs: float = 0.0) -> dict:
    matched = match_settings(parsed, payment_settings, shipping_settings)
    total_ads = round(float(snapchat_ads) + float(tiktok_ads) + float(instagram_ads), 2)
    net_profit = round(
        float(parsed["total_sales"])
        - float(matched["total_payment_fees"])
        - float(matched["total_shipping_cost"])
        - total_ads
        - float(product_costs),
        2,
    )
    return {
        "summary": {
            "total_sales": parsed["total_sales"],
            "total_orders": parsed["total_orders"],
            "total_payment_fees": matched["total_payment_fees"],
            "total_shipping_cost": matched["total_shipping_cost"],
            "deferred_shipping_cost": matched.get("deferred_shipping_cost", 0.0),
            "total_ads_cost": total_ads,
            "total_product_cost": float(product_costs),
            "net_revenue_after_fees": round(parsed["total_sales"] - matched["total_payment_fees"], 2),
            "net_profit": net_profit,
        },
        "payment_breakdown": matched["payment_breakdown"],
        "shipping_breakdown": matched["shipping_breakdown"],
        "order_sources": parsed.get("order_sources", []),
        "daily_costs": {
            "snapchat_ads": float(snapchat_ads),
            "tiktok_ads": float(tiktok_ads),
            "instagram_ads": float(instagram_ads),
            "product_costs": float(product_costs),
        },
        "detected_columns": parsed.get("detected_columns", {}),
        "orders_sample": parsed.get("orders_sample", []),
    }
