"""Coverage-aware Product Health Score for Decision Intelligence V3.

The score summarizes known operational product/page checks as evidence. Unknown
checks are excluded from the denominator instead of being treated as failure.
No score threshold maps to any campaign action; OpenAI receives components,
coverage and the explicit no-rule contract.
"""
from __future__ import annotations

from typing import Any


COMPONENT_WEIGHTS = {
    "url_health": 20.0,
    "visibility": 20.0,
    "inventory": 15.0,
    "variant_availability": 10.0,
    "add_to_cart_presence": 15.0,
    "page_title": 5.0,
    "description": 5.0,
    "hero_image": 5.0,
    "price_present": 5.0,
}
TOTAL_WEIGHT = sum(COMPONENT_WEIGHTS.values())


def _state(value: bool | None) -> str:
    return "pass" if value is True else "fail" if value is False else "unknown"


def score_product(product: dict[str, Any]) -> dict[str, Any]:
    page = product.get("page_probe") if isinstance(product.get("page_probe"), dict) else {}
    inventory = product.get("inventory") if isinstance(product.get("inventory"), dict) else {}
    variants = product.get("variants") if isinstance(product.get("variants"), list) else []

    page_status = str(page.get("status") or "")
    if not page_status or page_status in {"PRODUCT_URL_UNKNOWN", "PRODUCT_URL_NOT_PROBED_DUE_TO_BOUND"}:
        url_ok = None
    else:
        url_ok = page_status in {"PRODUCT_URL_OK", "PRODUCT_URL_REDIRECTED"}

    visibility_value = str(product.get("visibility") or "")
    visibility_ok = (
        True if visibility_value == "public_status_expected"
        else False if visibility_value in {"not_public_or_inactive", "out_of_stock"}
        else None
    )

    inventory_status = str(inventory.get("status") or "")
    inventory_ok = (
        True if inventory_status in {"in_stock", "unlimited"}
        else False if inventory_status in {"out_of_stock", "less_than_one_day_estimated"}
        else None
    )

    finite_variants = [
        row for row in variants
        if isinstance(row, dict)
        and not row.get("unlimited_quantity")
        and row.get("quantity") is not None
    ]
    variant_ok = None
    if finite_variants:
        variant_ok = any(float(row.get("quantity") or 0) > 0 for row in finite_variants)

    add_to_cart = page.get("add_to_cart_marker_present")
    atc_ok = bool(add_to_cart) if isinstance(add_to_cart, bool) else None
    title_ok = True if (page.get("page_title") or product.get("product_name")) else None
    description_ok = True if (product.get("description") or page.get("meta_description") or page.get("visible_text_excerpt")) else None
    hero_ok = True if (product.get("main_image") or page.get("og_image")) else None
    price_ok = True if product.get("price") is not None else None

    raw = {
        "url_health": url_ok,
        "visibility": visibility_ok,
        "inventory": inventory_ok,
        "variant_availability": variant_ok,
        "add_to_cart_presence": atc_ok,
        "page_title": title_ok,
        "description": description_ok,
        "hero_image": hero_ok,
        "price_present": price_ok,
    }
    observed_weight = sum(COMPONENT_WEIGHTS[key] for key, value in raw.items() if value is not None)
    passed_weight = sum(COMPONENT_WEIGHTS[key] for key, value in raw.items() if value is True)
    score = round(passed_weight / observed_weight * 100, 1) if observed_weight else None
    coverage = round(observed_weight / TOTAL_WEIGHT * 100, 1)
    return {
        "score": score,
        "coverage_pct": coverage,
        "components": {
            key: {
                "state": _state(value),
                "weight": COMPONENT_WEIGHTS[key],
            }
            for key, value in raw.items()
        },
        "contract": (
            "Evidence summary only. No product_health_score threshold may automatically pause, scale or classify a campaign."
        ),
    }


def enrich_product_health_scores(evidence_pack: dict[str, Any]) -> dict[str, Any]:
    for block in (((evidence_pack.get("product_intelligence") or {}).get("entities") or {}).values()):
        for product in block.get("products") or []:
            product["product_health_score"] = score_product(product)
    return evidence_pack


__all__ = ["enrich_product_health_scores", "score_product"]
