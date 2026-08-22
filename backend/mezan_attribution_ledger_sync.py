"""Automatic bridge from Mezan/Salla order ingestion to the attribution ledger.

This module is intentionally fail-open for order ingestion and fail-closed for
financial truth. It may enrich the internal ledger, but it never blocks Salla
order persistence, never writes to ad providers, and never invents per-order
profit from store-level aggregates.
"""
from __future__ import annotations

from typing import Any

from integrations_control_center.campaign_product_associations import (
    CAMPAIGN_PRODUCT_LINK_COLLECTION,
)
from mezan_attribution_order_ledger import upsert_order_attribution_ledger


async def _cursor_rows(cursor: Any, *, limit: int = 5000) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=limit)
    rows: list[dict[str, Any]] = []
    async for row in cursor:
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


async def load_verified_campaign_product_links(
    db: Any,
    user_id: str,
) -> list[dict[str, Any]]:
    """Load active verified campaign-product facts for one tenant only."""
    cursor = db[CAMPAIGN_PRODUCT_LINK_COLLECTION].find(
        {
            "user_id": user_id,
            "state": "active",
            "evidence.verification_status": "verified",
            "campaign_id": {"$nin": [None, ""]},
        },
        {"_id": 0},
    )
    return await _cursor_rows(cursor)


def campaign_identities_from_links(
    links: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse verified link events into unique campaign identities.

    The ledger requires an identity graph before an explicit campaign id can be
    promoted to confirmed attribution. Product links provide that verified
    identity without manufacturing any conversion relationship.
    """
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in links:
        if not isinstance(row, dict):
            continue
        campaign_id = str(row.get("campaign_id") or "").strip()
        if not campaign_id:
            continue
        provider = str(row.get("provider") or "").strip()
        account_id = str(row.get("account_id") or "").strip()
        key = (provider, account_id, campaign_id)
        result[key] = {
            "provider": provider or None,
            "account_id": account_id or None,
            "campaign_id": campaign_id,
            "campaign_name": row.get("campaign_name") or None,
        }
    return list(result.values())


def profit_facts_from_order(order: dict[str, Any]) -> dict[str, Any] | None:
    """Use only explicit per-order Mezan profit facts already persisted.

    Store-level Profit Engine totals are deliberately NOT divided across orders.
    If authoritative per-order profit has not been computed yet, return None so
    the ledger preserves unknown values as null.
    """
    candidates = (
        order.get("mezan_profit"),
        order.get("profit_facts"),
        order.get("profit"),
    )
    for value in candidates:
        if not isinstance(value, dict):
            continue
        known = value.get("known") is True or value.get("profit_accounting_known") is True
        if not known:
            continue
        net = value.get("net_profit_sar")
        if net is None:
            continue
        return {
            "known": True,
            "net_profit_sar": net,
            "revenue_sar": value.get("revenue_sar"),
            "cogs_sar": value.get("cogs_sar"),
            "shipping_sar": value.get("shipping_sar"),
            "fees_sar": value.get("fees_sar"),
            "allocated_ad_spend_sar": value.get("allocated_ad_spend_sar"),
            "source_contract": value.get("source_contract") or "mezan_order_profit_fact",
        }
    return None


async def sync_order_to_attribution_ledger(
    db: Any,
    *,
    user_id: str,
    order: dict[str, Any],
) -> dict[str, Any]:
    """Refresh one order's canonical attribution-ledger row.

    Safe to call after every Salla/Mezan order upsert. The ledger write is
    idempotent on ``(user_id, order_key)``.
    """
    links = await load_verified_campaign_product_links(db, user_id)
    identities = campaign_identities_from_links(links)
    row = await upsert_order_attribution_ledger(
        db,
        user_id,
        order=order,
        campaign_identities=identities,
        campaign_product_links=links,
        profit_facts=profit_facts_from_order(order),
    )
    return {
        "synced": True,
        "order_key": row.get("order_key"),
        "attribution_quality": (row.get("attribution") or {}).get("quality"),
        "decision_safe": (row.get("attribution") or {}).get("decision_safe") is True,
        "profit_known": (row.get("profit") or {}).get("known") is True,
    }


async def safe_sync_order_to_attribution_ledger(
    db: Any,
    *,
    user_id: str,
    order: dict[str, Any],
) -> dict[str, Any]:
    """Fail-open wrapper for ingestion paths.

    Attribution analytics must never make Salla order ingestion fail.
    """
    try:
        return await sync_order_to_attribution_ledger(
            db,
            user_id=user_id,
            order=order,
        )
    except Exception as exc:
        return {
            "synced": False,
            "reason": "ledger_sync_failed",
            "error_type": type(exc).__name__,
        }


__all__ = [
    "campaign_identities_from_links",
    "load_verified_campaign_product_links",
    "profit_facts_from_order",
    "safe_sync_order_to_attribution_ledger",
    "sync_order_to_attribution_ledger",
]
