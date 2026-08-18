"""Compose Decision Intelligence V3 evidence without changing the V2 pipeline.

The pack is keyed by the exact candidates already selected by the established
Campaign AI run.  Every optional evidence source degrades independently; a page
probe or store-friction source cannot turn a valid provider analysis into a total
AI outage.  Arithmetic remains descriptive and never selects pause/scale.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Awaitable

from advertising_product_watch_v3 import ALERT_COLLECTION
from campaign_ai_abandoned_cart_evidence_v3 import build_abandoned_cart_evidence
from campaign_ai_funnel_evidence_v3 import build_funnel_evidence
from campaign_ai_knowledge_retrieval_v3 import (
    infer_retrieval_topics,
    retrieve_marketing_knowledge,
)
from campaign_ai_product_intelligence_v3 import build_product_intelligence
from campaign_ai_public_page_probe_v3 import probe_product_page
from campaign_ai_store_friction_evidence_v3 import build_store_friction_evidence
from campaign_ai_temporal_evidence_v3 import build_sequential_temporal_evidence


MAX_PUBLIC_PAGE_PROBES_PER_CYCLE = 20
MAX_PRODUCTS_IN_MODEL_ENTITY = 4
MAX_IMAGES_IN_MODEL_PRODUCT = 6
MAX_OPTIONS_IN_MODEL_PRODUCT = 10
MAX_VARIANTS_IN_MODEL_PRODUCT = 16


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _entity_key(row: dict[str, Any]) -> str:
    return "|".join((
        str(row.get("provider") or ""),
        str(row.get("entity_level") or ""),
        str(row.get("account_id") or ""),
        str(row.get("entity_id") or ""),
    ))


async def _capture(label: str, awaitable: Awaitable[Any], *, default: Any) -> tuple[Any, str | None]:
    try:
        return await awaitable, None
    except Exception as exc:
        return default, f"{label}_unavailable:{type(exc).__name__}"


def _metric_totals(entity_rows: dict[str, Any], window: str) -> dict[str, dict[str, float]]:
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    observed: dict[str, set[str]] = defaultdict(set)
    for entity in entity_rows.values():
        provider = str(entity.get("provider") or "unknown")
        block = entity.get(window) if isinstance(entity.get(window), dict) else {}
        metrics = block.get("metrics") if isinstance(block.get("metrics"), dict) else {}
        for key in ("spend_sar", "revenue_sar", "purchases", "impressions", "clicks"):
            value = _number(metrics.get(key))
            if value is not None:
                totals[provider][key] += value
                observed[provider].add(key)
    return {
        provider: {
            key: round(value, 4)
            for key, value in values.items()
            if key in observed[provider]
        }
        for provider, values in totals.items()
    }


def _funnel_totals(funnel_entities: dict[str, Any], window: str) -> dict[str, dict[str, float]]:
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    seen: dict[str, set[str]] = defaultdict(set)
    for key, entity in funnel_entities.items():
        provider = key.split("|", 1)[0]
        block = ((entity.get("windows") or {}).get(window) or {})
        metrics = block.get("metrics") if isinstance(block.get("metrics"), dict) else {}
        for metric in (
            "spend_sar", "impressions", "clicks", "landing_page_views", "view_content",
            "add_to_cart", "initiate_checkout", "add_payment_info", "purchases",
        ):
            value = _number(metrics.get(metric))
            if value is not None:
                totals[provider][metric] += value
                seen[provider].add(metric)
    output = {}
    for provider, values in totals.items():
        clean = {key: round(value, 4) for key, value in values.items() if key in seen[provider]}
        clicks = clean.get("clicks")
        atc = clean.get("add_to_cart")
        checkout = clean.get("initiate_checkout")
        purchases = clean.get("purchases")
        clean["atc_rate_from_click_pct"] = round(atc / clicks * 100, 4) if atc is not None and clicks else None
        clean["checkout_rate_from_atc_pct"] = round(checkout / atc * 100, 4) if checkout is not None and atc else None
        clean["purchase_rate_from_checkout_pct"] = round(purchases / checkout * 100, 4) if purchases is not None and checkout else None
        output[provider] = clean
    return output


def _cross_evidence(temporal: dict[str, Any], funnel: dict[str, Any]) -> dict[str, Any]:
    temporal_entities = temporal.get("entities") or {}
    funnel_entities = funnel.get("entities") or {}
    return {
        "today_provider_totals": _metric_totals(temporal_entities, "today"),
        "yesterday_provider_totals": _metric_totals(temporal_entities, "yesterday"),
        "baseline_7d_provider_totals": _metric_totals(temporal_entities, "baseline_7d"),
        "today_funnel_totals": _funnel_totals(funnel_entities, "today"),
        "yesterday_funnel_totals": _funnel_totals(funnel_entities, "yesterday"),
        "baseline_7d_funnel_totals": _funnel_totals(funnel_entities, "baseline_7d"),
        "interpretation_contract": (
            "These are simultaneous platform/store facts for OpenAI comparison. "
            "The code does not infer that a shared movement is a website, payment, tracking or traffic problem."
        ),
    }


def _compact_product(product: dict[str, Any]) -> dict[str, Any]:
    inventory = dict(product.get("inventory") or {})
    inventory["variants"] = (inventory.get("variants") or [])[:MAX_VARIANTS_IN_MODEL_PRODUCT]
    return {
        **{key: product.get(key) for key in (
            "product_id", "product_name", "destination_url", "canonical_product_url",
            "page_probe", "status", "visibility", "archived", "sku", "price", "sale_price",
            "currency", "description", "short_description", "main_image", "details_loaded",
            "last_synced_at", "details_synced_at", "source_updated_at", "association",
            "data_limitations",
        )},
        "images": (product.get("images") or [])[:MAX_IMAGES_IN_MODEL_PRODUCT],
        "options": (product.get("options") or [])[:MAX_OPTIONS_IN_MODEL_PRODUCT],
        "variants": (product.get("variants") or [])[:MAX_VARIANTS_IN_MODEL_PRODUCT],
        "inventory": inventory,
    }


async def _bounded_public_probes(product_evidence: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    entities = product_evidence.get("entities") or {}
    ordered = sorted(candidates, key=lambda row: float(row.get("spend_sar") or 0), reverse=True)
    probes = 0
    cache: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in ordered:
        block = entities.get(_entity_key(candidate)) or {}
        for product in block.get("products") or []:
            destination = str(product.get("destination_url") or "")
            canonical = str(product.get("canonical_product_url") or "")
            key = (destination, canonical)
            if not destination or not canonical:
                product["page_probe"] = {
                    "checked": False,
                    "status": "PRODUCT_URL_UNKNOWN",
                    "reason": "destination_or_canonical_url_missing",
                }
                continue
            if key in cache:
                product["page_probe"] = cache[key]
                continue
            if probes >= MAX_PUBLIC_PAGE_PROBES_PER_CYCLE:
                product["page_probe"] = {
                    "checked": False,
                    "status": "PRODUCT_URL_NOT_PROBED_DUE_TO_BOUND",
                    "reason": "per_cycle_probe_limit_reached",
                }
                continue
            cache[key] = await probe_product_page(destination, canonical_url=canonical)
            product["page_probe"] = cache[key]
            probes += 1
    product_evidence["public_page_probes_performed"] = probes
    product_evidence["public_page_probe_limit"] = MAX_PUBLIC_PAGE_PROBES_PER_CYCLE


def _compact_product_evidence(product_evidence: dict[str, Any]) -> dict[str, Any]:
    entities = {}
    for key, block in (product_evidence.get("entities") or {}).items():
        entities[key] = {
            **{name: block.get(name) for name in (
                "advertised_destination_url", "product_count", "source_contract",
            )},
            "products": [
                _compact_product(product)
                for product in (block.get("products") or [])[:MAX_PRODUCTS_IN_MODEL_ENTITY]
            ],
        }
    return {
        "schema_version": product_evidence.get("schema_version"),
        "public_page_probes_performed": product_evidence.get("public_page_probes_performed"),
        "public_page_probe_limit": product_evidence.get("public_page_probe_limit"),
        "entities": entities,
    }


async def _active_product_watch_alerts(db: Any, user_id: str) -> list[dict[str, Any]]:
    rows = await db[ALERT_COLLECTION].find(
        {"user_id": user_id, "status": "active"},
        {"_id": 0, "user_id": 0},
    ).sort("last_seen_at", -1).limit(100).to_list(length=100)
    for row in rows:
        for key in ("first_seen_at", "last_seen_at", "updated_at"):
            if hasattr(row.get(key), "isoformat"):
                row[key] = row[key].isoformat()
    return rows


def _retrieval_query(providers: list[str]) -> str:
    provider_text = " ".join(providers)
    return (
        f"{provider_text} performance marketing ecommerce diagnosis full funnel creative video "
        "landing product page offer inventory checkout payment shipping abandoned cart tracking "
        "attribution learning scaling contribution profit counterfactual analysis"
    )


async def build_decision_evidence_pack_v3(
    db: Any,
    user_id: str,
    candidates: list[dict[str, Any]],
    *,
    end: date,
    current: datetime,
) -> dict[str, Any]:
    limitations: list[str] = []
    temporal, error = await _capture(
        "temporal_evidence",
        build_sequential_temporal_evidence(db, user_id, candidates, end=end, current=current),
        default={"schema_version": "campaign_ai_temporal_evidence_v3", "entities": {}},
    )
    if error: limitations.append(error)
    funnel, error = await _capture(
        "funnel_evidence",
        build_funnel_evidence(db, user_id, candidates, end=end),
        default={"schema_version": "campaign_ai_funnel_evidence_v3", "entities": {}, "limitations": []},
    )
    if error: limitations.append(error)
    products, error = await _capture(
        "product_intelligence",
        build_product_intelligence(db, user_id, candidates, probe_pages=False),
        default={"schema_version": "campaign_ai_product_intelligence_v3", "entities": {}},
    )
    if error:
        limitations.append(error)
    else:
        try:
            await _bounded_public_probes(products, candidates)
        except Exception as exc:
            limitations.append(f"public_product_page_probe_unavailable:{type(exc).__name__}")
    products = _compact_product_evidence(products)
    carts, error = await _capture(
        "abandoned_cart_evidence",
        build_abandoned_cart_evidence(db, user_id, candidates, end=end),
        default={"schema_version": "campaign_ai_abandoned_cart_evidence_v3", "entities": {}, "limitations": []},
    )
    if error: limitations.append(error)
    store_friction, error = await _capture(
        "store_friction_evidence",
        build_store_friction_evidence(db, user_id, end=end),
        default={"schema_version": "campaign_ai_store_friction_evidence_v3", "windows": {}, "scope": "unavailable"},
    )
    if error: limitations.append(error)
    watch_alerts, error = await _capture(
        "product_watch_alerts",
        _active_product_watch_alerts(db, user_id),
        default=[],
    )
    if error: limitations.append(error)

    providers = sorted({str(row.get("provider") or "") for row in candidates if row.get("provider")})
    cross = _cross_evidence(temporal, funnel)
    feature_flags = {
        "providers": providers,
        "funnel_evidence_available": bool(funnel.get("entities")),
        "creative_evidence_available": bool(funnel.get("entities")),
        "product_evidence_available": any(
            (block.get("products") or [])
            for block in (products.get("entities") or {}).values()
        ),
        "abandoned_cart_evidence_available": bool(carts.get("entities")),
    }
    topics = infer_retrieval_topics(feature_flags)
    knowledge, error = await _capture(
        "marketing_knowledge_retrieval",
        retrieve_marketing_knowledge(db, query=_retrieval_query(providers), topics=topics, limit=10),
        default=[],
    )
    if error: limitations.append(error)

    return {
        "schema_version": "campaign_ai_decision_evidence_pack_v3",
        "providers": providers,
        "temporal": temporal,
        "funnel_and_creative": funnel,
        "product_intelligence": products,
        "abandoned_carts": carts,
        "store_checkout_payment_shipping": store_friction,
        "operational_product_watch_alerts": watch_alerts,
        "cross_campaign_cross_platform": cross,
        "marketing_knowledge": {
            "retrieval_topics": topics,
            "retrieved": knowledge,
            "contract": (
                "Knowledge is supporting methodology/context only. It cannot override store/provider facts or force an action."
            ),
        },
        "root_cause_tree": [
            "A_DATA_QUALITY",
            "B_DELIVERY",
            "C_CREATIVE",
            "D_CLICK_INTENT",
            "E_DESTINATION_HEALTH",
            "F_PRODUCT_AVAILABILITY",
            "G_PRODUCT_PAGE",
            "H_ADD_TO_CART",
            "I_CHECKOUT",
            "J_PAYMENT",
            "K_SHIPPING",
            "L_INVENTORY",
            "M_PROFITABILITY",
            "CAMPAIGN_ACTION_ONLY_AFTER_ROOT_CAUSE_REVIEW",
        ],
        "contracts": {
            "diagnose_before_action": True,
            "recommendation_is_separate_from_execution": True,
            "store_level_carts_must_not_become_campaign_revenue": True,
            "store_payment_shipping_must_not_become_campaign_attribution": True,
            "salla_child_attribution_allowed": False,
            "context_is_explanatory_not_rule": True,
            "openai_is_final_marketing_decision_authority": True,
            "optional_evidence_sources_degrade_independently": True,
        },
        "limitations": list(dict.fromkeys(limitations)),
    }


__all__ = ["build_decision_evidence_pack_v3"]
