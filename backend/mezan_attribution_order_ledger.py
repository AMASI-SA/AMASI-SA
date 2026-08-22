"""Canonical Mezan attribution + order ledger foundation.

This layer records what Mezan can prove about an order's marketing source and
profit identity without inventing attribution from provider purchase counts.
It is an internal ledger only: it performs no provider, catalog, price, stock,
or commerce writes.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from salla_marketing_attribution import (
    campaign_id_candidates,
    campaign_name_candidates,
    canonical_ad_platform,
    canonical_marketing_source,
    field_values,
)

CONTRACT_VERSION = "mezan_attribution_order_ledger_v1"
LEDGER_COLLECTION = "mezan_attribution_order_ledger_v1"


def _text(value: Any, limit: int = 240) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return " ".join(str(value).split()).strip()[:limit]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _provider(value: Any) -> str | None:
    text = _text(value, 80).casefold().replace("-", "_").replace(" ", "_")
    if not text:
        return None
    if text in {"snap", "snapchat", "snapchat_ads"}:
        return "snapchat"
    if text in {"facebook", "instagram", "meta", "meta_ads"}:
        return "meta"
    if text in {"tiktok", "tik_tok", "tiktok_ads"}:
        return "tiktok"
    if text in {"google", "google_ads", "adwords"}:
        return "google"
    return text


def _order_key(order: dict[str, Any]) -> str:
    for key in ("order_id", "id", "reference_id", "order_number"):
        value = _text(order.get(key), 180)
        if value:
            return value
    raw = _dict(_dict(order.get("raw_by_source")).get("salla_direct"))
    for key in ("id", "reference_id", "order_number"):
        value = _text(raw.get(key), 180)
        if value:
            return value
    return ""


def _order_created_at(order: dict[str, Any]) -> str | None:
    for key in ("created_at_utc", "order_created_at", "created_at", "source_created_at"):
        value = order.get(key)
        if not value:
            continue
        if isinstance(value, datetime):
            dt = value
        else:
            try:
                dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return None


def _identity_provider(row: dict[str, Any]) -> str | None:
    return _provider(row.get("provider") or row.get("platform"))


def _unique_identity_match(
    candidates: list[str],
    identities: list[dict[str, Any]],
    *,
    field: str,
    provider: str | None,
) -> tuple[dict[str, Any] | None, str]:
    for candidate in candidates:
        needle = _text(candidate, 240).casefold()
        if not needle:
            continue
        matches = []
        for row in identities:
            row_provider = _identity_provider(row)
            if provider and row_provider and row_provider != provider:
                continue
            value = _text(row.get(field), 240).casefold()
            if value and value == needle:
                matches.append(row)
        unique = {
            (
                _text(row.get("account_id"), 160),
                _text(row.get("campaign_id"), 160),
            ): row
            for row in matches
        }
        if len(unique) == 1:
            return next(iter(unique.values())), "unique"
        if len(unique) > 1:
            return None, "ambiguous"
    return None, "none"


def _explicit_hierarchy(order: dict[str, Any]) -> dict[str, str | None]:
    aliases = {
        "ad_group_id": ("ad_group_id", "adgroup_id", "ad_squad_id", "source_ad_group_id"),
        "ad_id": ("ad_id", "source_ad_id", "advertisement_id"),
        "creative_id": ("creative_id", "source_creative_id", "ad_creative_id"),
    }
    result: dict[str, str | None] = {}
    for key, fields in aliases.items():
        values = field_values(order, *fields)
        result[key] = _text(values[0], 180) if values else None
    return result


def _line_sources(order: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    raw = _dict(_dict(order.get("raw_by_source")).get("salla_direct"))
    for container in (order, raw):
        for key in ("items", "products", "order_items"):
            values = container.get(key)
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, dict):
                        sources.append(item)
    return sources


def _extract_lines(order: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in _line_sources(order):
        product = _dict(item.get("product"))
        variant = _dict(item.get("variant"))
        product_id = _text(
            item.get("product_id") or product.get("id") or item.get("productId"), 160
        ) or None
        variant_id = _text(
            item.get("product_variant_id")
            or item.get("variant_id")
            or variant.get("id")
            or product.get("variant_id"),
            160,
        ) or None
        sku = _text(item.get("sku") or variant.get("sku") or product.get("sku"), 160) or None
        name = _text(item.get("name") or product.get("name") or item.get("product_name"), 300) or None
        quantity = _number(item.get("quantity") if item.get("quantity") is not None else item.get("qty"))
        line_total = _number(
            item.get("total_amount")
            if item.get("total_amount") is not None
            else item.get("total")
            if item.get("total") is not None
            else item.get("amount")
        )
        key = (product_id, variant_id, sku, name, quantity, line_total)
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "product_id": product_id,
            "product_variant_id": variant_id,
            "sku": sku,
            "product_name": name,
            "quantity": quantity,
            "line_total_sar": line_total,
        })
    return result[:200]


def _verified_product_links(
    campaign_id: str | None,
    lines: list[dict[str, Any]],
    links: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not campaign_id:
        return []
    line_ids = {
        (line.get("product_id"), line.get("product_variant_id"))
        for line in lines
        if line.get("product_id")
    }
    result = []
    for link in links:
        if _text(link.get("campaign_id"), 160) != campaign_id:
            continue
        evidence = _dict(link.get("evidence"))
        verification = _text(
            link.get("verification_status") or evidence.get("verification_status"), 40
        ).casefold()
        if verification != "verified":
            continue
        product_id = _text(link.get("product_id"), 160) or None
        variant_id = _text(link.get("product_variant_id"), 160) or None
        if (product_id, variant_id) not in line_ids and (product_id, None) not in line_ids:
            continue
        result.append({
            "product_id": product_id,
            "product_variant_id": variant_id,
            "association_id": _text(link.get("association_id") or link.get("event_id"), 180) or None,
            "source": _text(evidence.get("source") or link.get("source"), 80) or None,
        })
    return result[:100]


def _profit_payload(profit_facts: dict[str, Any] | None) -> dict[str, Any]:
    facts = profit_facts if isinstance(profit_facts, dict) else {}
    known = facts.get("known") is True or facts.get("profit_accounting_known") is True
    return {
        "known": known,
        "revenue_sar": _number(facts.get("revenue_sar")) if known else None,
        "cogs_sar": _number(facts.get("cogs_sar")) if known else None,
        "shipping_sar": _number(facts.get("shipping_sar")) if known else None,
        "fees_sar": _number(facts.get("fees_sar")) if known else None,
        "allocated_ad_spend_sar": _number(facts.get("allocated_ad_spend_sar")) if known else None,
        "net_profit_sar": _number(facts.get("net_profit_sar")) if known else None,
        "source_contract": _text(facts.get("source_contract"), 120) or None,
    }


def build_order_attribution_ledger_row(
    *,
    order: dict[str, Any],
    campaign_identities: list[dict[str, Any]] | None = None,
    campaign_product_links: list[dict[str, Any]] | None = None,
    profit_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one canonical order-ledger row from explicit evidence only."""
    identities = [x for x in (campaign_identities or []) if isinstance(x, dict)]
    links = [x for x in (campaign_product_links or []) if isinstance(x, dict)]
    order_key = _order_key(order)
    if not order_key:
        raise ValueError("order identity is required")

    provider = _provider(canonical_ad_platform(order))
    marketing_source = canonical_marketing_source(order)
    id_candidates = campaign_id_candidates(order)
    name_candidates = campaign_name_candidates(order)

    identity, id_result = _unique_identity_match(
        id_candidates, identities, field="campaign_id", provider=provider
    )
    match_method = None
    attribution_quality = "unattributed"
    decision_safe = False
    if identity is not None:
        match_method = "exact_campaign_id"
        attribution_quality = "confirmed"
        decision_safe = True
    elif id_result == "ambiguous":
        match_method = "ambiguous_campaign_id"
        attribution_quality = "ambiguous"
    else:
        identity, name_result = _unique_identity_match(
            name_candidates, identities, field="campaign_name", provider=provider
        )
        if identity is not None:
            match_method = "unique_campaign_name"
            attribution_quality = "inferred"
        elif name_result == "ambiguous":
            match_method = "ambiguous_campaign_name"
            attribution_quality = "ambiguous"

    campaign_id = _text(_dict(identity).get("campaign_id"), 160) or None
    campaign_name = _text(_dict(identity).get("campaign_name"), 240) or None
    account_id = _text(_dict(identity).get("account_id"), 160) or None
    hierarchy = _explicit_hierarchy(order)
    lines = _extract_lines(order)
    product_links = _verified_product_links(campaign_id, lines, links)

    if attribution_quality == "unattributed" and marketing_source in {
        "direct", "whatsapp", "manual", "gift"
    }:
        match_method = f"explicit_non_campaign_{marketing_source}"

    evidence = {
        "marketing_source": marketing_source,
        "campaign_id_candidates": id_candidates[:20],
        "campaign_name_candidates": name_candidates[:20],
        "explicit_hierarchy": hierarchy,
        "provider_purchase_counts_used_for_attribution": False,
    }
    source_digest = hashlib.sha256(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    return {
        "contract_version": CONTRACT_VERSION,
        "order_key": order_key,
        "order_created_at": _order_created_at(order),
        "business_effects_read_only": True,
        "attribution": {
            "quality": attribution_quality,
            "decision_safe": decision_safe,
            "provider": provider,
            "marketing_source": marketing_source,
            "account_id": account_id,
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "ad_group_id": hierarchy.get("ad_group_id"),
            "ad_id": hierarchy.get("ad_id"),
            "creative_id": hierarchy.get("creative_id"),
            "match_method": match_method,
        },
        "line_items": lines,
        "verified_campaign_product_links": product_links,
        "profit": _profit_payload(profit_facts),
        "evidence": evidence,
        "source_digest": source_digest,
        "guardrails": [
            "Provider purchase counts never create or distribute order attribution.",
            "Direct, WhatsApp, manual, gift, ambiguous, and unknown orders are never distributed across campaigns.",
            "Campaign-product associations describe intended scope and never manufacture conversion attribution.",
            "Unique campaign-name matching is visible but not decision-safe for financial automation.",
            "Unknown profit components remain null; unknown is never converted to zero.",
        ],
    }


async def ensure_indexes(db: Any) -> None:
    await db[LEDGER_COLLECTION].create_index(
        [("user_id", 1), ("order_key", 1)], unique=True,
        name="mezan_attribution_order_ledger_user_order_unique",
    )
    await db[LEDGER_COLLECTION].create_index(
        [("user_id", 1), ("attribution.provider", 1), ("attribution.campaign_id", 1), ("order_created_at", -1)],
        name="mezan_attribution_order_ledger_campaign_recent",
    )


async def upsert_order_attribution_ledger(
    db: Any,
    user_id: str,
    *,
    order: dict[str, Any],
    campaign_identities: list[dict[str, Any]] | None = None,
    campaign_product_links: list[dict[str, Any]] | None = None,
    profit_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    await ensure_indexes(db)
    row = build_order_attribution_ledger_row(
        order=order,
        campaign_identities=campaign_identities,
        campaign_product_links=campaign_product_links,
        profit_facts=profit_facts,
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    document = {**row, "user_id": user_id, "updated_at": now_iso}
    await db[LEDGER_COLLECTION].update_one(
        {"user_id": user_id, "order_key": row["order_key"]},
        {"$set": document, "$setOnInsert": {"created_at": now_iso}},
        upsert=True,
    )
    return {k: v for k, v in document.items() if k != "user_id"}


async def load_order_attribution_ledger(
    db: Any,
    user_id: str,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    await ensure_indexes(db)
    cap = max(1, min(1000, int(limit)))
    return await db[LEDGER_COLLECTION].find(
        {"user_id": user_id}, {"_id": 0, "user_id": 0}
    ).sort("order_created_at", -1).limit(cap).to_list(length=cap)


__all__ = [
    "CONTRACT_VERSION",
    "LEDGER_COLLECTION",
    "build_order_attribution_ledger_row",
    "ensure_indexes",
    "load_order_attribution_ledger",
    "upsert_order_attribution_ledger",
]
