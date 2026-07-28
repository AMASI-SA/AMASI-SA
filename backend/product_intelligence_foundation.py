"""Deterministic Product Intelligence foundation for Mezan OS V2.

The foundation is intentionally provider-independent.  It can collect facts,
evaluate readiness, and prepare governed action candidates while OpenAI billing
is unavailable.  It never publishes to Salla, changes prices, mutates stock, or
changes campaigns.
"""
from __future__ import annotations

from typing import Any


FOUNDATION_VERSION = 1
CURRENT_OPERATING_LEVEL = "rules_only"

PRODUCT_INTELLIGENCE_COLLECTIONS = {
    "signals": "mezan_product_intelligence_signals_v2",
    "decisions": "mezan_product_intelligence_decisions_v2",
    "outcomes": "mezan_product_intelligence_outcomes_v2",
    "source_state": "mezan_product_intelligence_source_state_v2",
}

SIGNAL_SOURCE_CATALOG = {
    "salla_catalog": {
        "label": "Salla catalog",
        "facts": ["product", "variant", "price", "availability", "image", "category"],
        "contains_customer_pii": False,
    },
    "salla_orders": {
        "label": "Salla orders",
        "facts": ["units", "revenue", "refund", "return", "conversion_outcome"],
        "contains_customer_pii": True,
        "storage_policy": "aggregate_product_metrics_only",
    },
    "ga4": {
        "label": "Google Analytics 4",
        "facts": ["view_item", "add_to_cart", "begin_checkout", "purchase", "funnel_dropoff"],
        "contains_customer_pii": False,
    },
    "search_console": {
        "label": "Google Search Console",
        "facts": ["query", "impressions", "clicks", "ctr", "average_position"],
        "contains_customer_pii": False,
    },
    "merchant_center": {
        "label": "Google Merchant Center",
        "facts": ["feed_status", "attribute_issue", "listing_eligibility"],
        "contains_customer_pii": False,
    },
    "customer_intelligence": {
        "label": "Customer Intelligence",
        "facts": ["question_theme", "objection_theme", "requested_attribute", "product_gap"],
        "contains_customer_pii": True,
        "storage_policy": "aggregate_redacted_themes_only",
    },
    "ads": {
        "label": "Advertising platforms",
        "facts": ["spend", "click", "conversion", "cpa", "roas"],
        "contains_customer_pii": False,
    },
    "mezan_economics": {
        "label": "Mezan costs and margins",
        "facts": ["base_cost", "option_cost", "shipping_cost", "fee", "margin_floor"],
        "contains_customer_pii": False,
    },
    "inventory": {
        "label": "Mezan inventory",
        "facts": ["available_quantity", "reserved_quantity", "stockout"],
        "contains_customer_pii": False,
    },
    "openai_product_feed": {
        "label": "OpenAI product discovery feed",
        "facts": ["feed_status", "freshness", "attribute_issue"],
        "contains_customer_pii": False,
    },
    "public_market": {
        "label": "Public market evidence",
        "facts": ["public_price", "public_offer", "public_ad", "public_rank_signal"],
        "contains_customer_pii": False,
        "storage_policy": "public_evidence_with_timestamp_and_url",
    },
}

SIGNAL_CONTRACT = {
    "required": [
        "signal_id",
        "user_id",
        "product_id",
        "source",
        "metric",
        "value",
        "window_start",
        "window_end",
        "captured_at",
        "evidence_ref",
    ],
    "optional": ["unit", "dimensions", "confidence", "source_revision"],
    "forbidden": [
        "customer_name",
        "customer_phone",
        "customer_email",
        "payment_identifier",
        "raw_customer_message",
    ],
    "deduplication_key": [
        "user_id",
        "product_id",
        "source",
        "metric",
        "window_start",
        "window_end",
        "source_revision",
    ],
}

DECISION_LIFECYCLE = [
    "observe",
    "collect_evidence",
    "diagnose",
    "propose",
    "approve",
    "execute",
    "verify",
    "measure",
    "rollback_or_keep",
]

ACTION_POLICY = {
    "catalog_fact_enrichment": {
        "risk": "low",
        "required_approval": "product_manager",
        "execution_allowed": False,
    },
    "seo_metadata_draft": {
        "risk": "low",
        "required_approval": "product_manager",
        "execution_allowed": False,
    },
    "structured_product_feed_draft": {
        "risk": "low",
        "required_approval": "product_manager",
        "execution_allowed": False,
    },
    "description_rewrite": {
        "risk": "medium",
        "required_approval": "product_manager",
        "execution_allowed": False,
    },
    "title_change": {
        "risk": "medium",
        "required_approval": "product_manager",
        "execution_allowed": False,
    },
    "image_change": {
        "risk": "medium",
        "required_approval": "owner",
        "execution_allowed": False,
    },
    "price_change": {
        "risk": "high",
        "required_approval": "owner",
        "execution_allowed": False,
        "required_guards": ["verified_cost", "margin_floor", "max_change", "measurement_plan"],
    },
    "sale_price_change": {
        "risk": "high",
        "required_approval": "owner",
        "execution_allowed": False,
        "required_guards": ["verified_cost", "margin_floor", "discount_stack", "measurement_plan"],
    },
    "inventory_change": {
        "risk": "critical",
        "required_approval": "owner",
        "execution_allowed": False,
        "required_guards": ["live_inventory", "branch_scope", "explicit_confirmation"],
    },
    "campaign_change": {
        "risk": "high",
        "required_approval": "owner",
        "execution_allowed": False,
        "required_guards": ["budget_ceiling", "attribution_quality", "rollback_plan"],
    },
    "product_feed_publish": {
        "risk": "high",
        "required_approval": "owner",
        "execution_allowed": False,
        "required_guards": ["feed_validation", "freshness_check", "explicit_confirmation"],
    },
    "salla_publish": {
        "risk": "critical",
        "required_approval": "owner",
        "execution_allowed": False,
        "required_guards": ["approved_draft", "explicit_confirmation", "verification", "rollback"],
    },
}

PROTECTED_AUTOMATION_AREAS = [
    "price",
    "sale_price",
    "cost",
    "inventory",
    "product_status",
    "campaign_budget",
    "salla_publish",
    "product_feed_publish",
]

SOURCE_DEPENDENCIES = {
    "catalog_quality": ["salla_catalog"],
    "conversion_analysis": ["ga4", "salla_orders"],
    "google_discovery": ["search_console", "merchant_center"],
    "ai_discovery": ["openai_product_feed"],
    "customer_demand": ["customer_intelligence"],
    "profit_guard": ["mezan_economics"],
    "campaign_efficiency": ["ads", "salla_orders"],
    "inventory_guard": ["inventory"],
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _has_images(product: dict[str, Any]) -> bool:
    images = product.get("images")
    if isinstance(images, list) and any(images):
        return True
    return bool(product.get("main_image"))


def _seo(product: dict[str, Any]) -> dict[str, Any]:
    value = product.get("seo")
    return value if isinstance(value, dict) else {}


def _source_connected(source_states: dict[str, Any], source: str) -> bool:
    value = source_states.get(source)
    if isinstance(value, dict):
        return value.get("status") in {"connected", "ready", "healthy"}
    return value is True


def product_intelligence_readiness(
    product: dict[str, Any],
    cost_profile: dict[str, Any] | None = None,
    source_states: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate deterministic readiness without calling an AI provider."""
    cost_profile = cost_profile or {}
    source_states = source_states or {}
    seo = _seo(product)
    price = _number(product.get("price"))
    base_cost = _number(cost_profile.get("base_cost"))
    options = product.get("options")
    options_count = int(product.get("options_count") or len(options or []))
    option_links = int(product.get("option_cost_links_count") or 0)

    checks = {
        "catalog.name": bool(_text(product.get("name"))),
        "catalog.sku": bool(_text(product.get("sku"))),
        "catalog.description": bool(
            _text(product.get("description_html") or product.get("description"))
        ),
        "catalog.images": _has_images(product),
        "catalog.category": bool(product.get("categories")),
        "catalog.price": price is not None and price >= 0,
        "catalog.availability": bool(_text(product.get("status"))),
        "catalog.option_costs": options_count == 0 or option_links > 0,
        "economics.base_cost": base_cost is not None and base_cost >= 0,
        "economics.price_above_cost": (
            price is not None and base_cost is not None and price >= base_cost
        ),
        "discovery.seo_title": bool(
            _text(seo.get("title") or product.get("seo_title"))
        ),
        "discovery.seo_description": bool(
            _text(seo.get("description") or product.get("seo_description"))
        ),
        "discovery.google_category": bool(
            _text(
                product.get("google_category")
                or product.get("google_product_category")
            )
        ),
        "measurement.ga4": _source_connected(source_states, "ga4"),
        "measurement.search_console": _source_connected(
            source_states, "search_console"
        ),
        "measurement.orders": _source_connected(source_states, "salla_orders"),
        "measurement.customer_intelligence": _source_connected(
            source_states, "customer_intelligence"
        ),
        "distribution.merchant_center": _source_connected(
            source_states, "merchant_center"
        ),
        "distribution.openai_product_feed": _source_connected(
            source_states, "openai_product_feed"
        ),
    }

    weights = {
        "catalog.name": 4,
        "catalog.sku": 4,
        "catalog.description": 6,
        "catalog.images": 5,
        "catalog.category": 4,
        "catalog.price": 4,
        "catalog.availability": 3,
        "catalog.option_costs": 5,
        "economics.base_cost": 10,
        "economics.price_above_cost": 10,
        "discovery.seo_title": 5,
        "discovery.seo_description": 5,
        "discovery.google_category": 5,
        "measurement.ga4": 7,
        "measurement.search_console": 7,
        "measurement.orders": 5,
        "measurement.customer_intelligence": 4,
        "distribution.merchant_center": 4,
        "distribution.openai_product_feed": 3,
    }
    score = sum(weights[key] for key, passed in checks.items() if passed)
    gaps = [key for key, passed in checks.items() if not passed]
    critical_gaps = [
        key
        for key in (
            "catalog.name",
            "catalog.price",
            "economics.base_cost",
            "economics.price_above_cost",
        )
        if not checks[key]
    ]
    return {
        "score": score,
        "checks": checks,
        "gaps": gaps,
        "critical_gaps": critical_gaps,
        "rules_ready": not critical_gaps,
        "proposal_generation_ready": False,
        "automatic_execution_ready": False,
        "operating_level": CURRENT_OPERATING_LEVEL,
    }


def action_candidates(readiness: dict[str, Any]) -> list[dict[str, Any]]:
    """Map objective evidence gaps to governed proposal candidates."""
    gaps = set(readiness.get("gaps") or [])
    candidates: list[tuple[str, str]] = []
    if gaps.intersection(
        {
            "catalog.name",
            "catalog.sku",
            "catalog.category",
            "discovery.google_category",
        }
    ):
        candidates.append(("catalog_fact_enrichment", "catalog_or_category_gap"))
    if gaps.intersection(
        {"discovery.seo_title", "discovery.seo_description"}
    ):
        candidates.append(("seo_metadata_draft", "seo_metadata_gap"))
    if "catalog.description" in gaps:
        candidates.append(("description_rewrite", "description_gap"))
    if gaps.intersection(
        {
            "distribution.merchant_center",
            "distribution.openai_product_feed",
        }
    ):
        candidates.append(
            ("structured_product_feed_draft", "distribution_feed_gap")
        )

    return [
        {
            "action": action,
            "reason": reason,
            **ACTION_POLICY[action],
            "status": "candidate_only",
        }
        for action, reason in candidates
    ]


def product_intelligence_foundation() -> dict[str, Any]:
    """Return the provider-independent contract exposed to V2 clients."""
    return {
        "ok": True,
        "schema_version": FOUNDATION_VERSION,
        "mode": "foundation_only",
        "operating_level": CURRENT_OPERATING_LEVEL,
        "legacy_dependency": False,
        "openai_required": {
            "data_collection": False,
            "rule_evaluation": False,
            "ai_proposal_generation": True,
            "ai_image_generation": True,
        },
        "writes_allowed": False,
        "external_calls_allowed": False,
        "automatic_execution_allowed": False,
        "collections": PRODUCT_INTELLIGENCE_COLLECTIONS,
        "signal_sources": SIGNAL_SOURCE_CATALOG,
        "signal_contract": SIGNAL_CONTRACT,
        "source_dependencies": SOURCE_DEPENDENCIES,
        "decision_lifecycle": DECISION_LIFECYCLE,
        "action_policy": ACTION_POLICY,
        "protected_automation_areas": PROTECTED_AUTOMATION_AREAS,
    }
