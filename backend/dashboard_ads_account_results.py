"""Account-scoped Salla outcomes for the existing executive snapshot.

The caller supplies the already filtered executive orders and the Snapchat
account IDs already read for spend. Only a bounded campaign identity catalog
is read here; no order, provider, cost, or background work is started.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from dashboard_cpu_budget import ADVERTISING_BUDGET_MS, CPUWorkBudget
from dashboard_v2_ads_executive import resolve_salla_ad_platform
from order_currency import order_total_sar
from snapchat_v2.salla_outcomes import (
    _exact_order_account,
    _match_order_campaign,
    _unique_lookup,
    _unmatched_reason,
)


CAMPAIGN_COLLECTION = "mezan_snapchat_entity_facts_v2"
MAX_CAMPAIGN_ROWS = 5_000
CATALOG_READ_TIMEOUT_MS = 1_500


async def _campaign_catalog(db: Any, user_id: str) -> tuple[list[dict], str | None]:
    """Read owner-wide identities so cross-account names remain ambiguous."""
    try:
        collection = (db[CAMPAIGN_COLLECTION] if hasattr(db, "__getitem__")
                      else getattr(db, CAMPAIGN_COLLECTION))
        cursor = collection.find(
            {"user_id": str(user_id), "provider": "snapchat_ads", "entity_type": "campaign"},
            {"_id": 0, "ad_account_id": 1, "external_id": 1, "name": 1},
        )
        cursor = cursor.limit(MAX_CAMPAIGN_ROWS + 1)
        if hasattr(cursor, "max_time_ms"):
            cursor = cursor.max_time_ms(CATALOG_READ_TIMEOUT_MS)
        rows = await cursor.to_list(length=MAX_CAMPAIGN_ROWS + 1)
    except Exception:  # A catalog outage must not hide the financial snapshot.
        return [], "campaign_catalog_unavailable"
    if len(rows) > MAX_CAMPAIGN_ROWS:
        return [], "campaign_catalog_truncated"
    return [{"account_id": str(row.get("ad_account_id") or "").strip(),
             "campaign_id": str(row.get("external_id") or "").strip(),
             "campaign_name": row.get("name")} for row in rows], None


async def build_dashboard_ads_account_results(
    db: Any,
    user_id: str,
    orders: list[dict[str, Any]],
    *,
    snapchat_account_ids: list[str],
    date_from: str | None,
    date_to: str | None,
) -> dict[str, Any]:
    """Partition proven Snapchat orders once without guessing any account.

    Canonical account totals are unknown when a Snapchat order cannot be
    assigned. Known matched subtotals remain explicit. Orders proven to belong
    to a different account are excluded, not redistributed to the shown ones.
    """
    account_ids = sorted({str(value).strip() for value in snapchat_account_ids
                          if value is not None and str(value).strip()})
    buckets = {account_id: {"orders": 0, "sales": 0.0, "currency_complete": True}
               for account_id in account_ids}
    budget = CPUWorkBudget(ADVERTISING_BUDGET_MS)
    catalog_loaded = False
    catalog_complete = True
    id_lookup: dict = {}
    name_lookup: dict = {}
    exact_owners: dict[str, set[str]] = defaultdict(set)
    reasons: Counter[str] = Counter()
    unassigned_orders = 0
    outside_scope_orders = 0

    for order in orders:
        await budget.checkpoint()
        # Use the same platform classifier and the same prefiltered population
        # as the executive breakdown; this view cannot change its source policy.
        if resolve_salla_ad_platform(order) != "snapchat":
            continue
        if not catalog_loaded:
            catalog_loaded = True
            if account_ids:
                identities, catalog_error = await _campaign_catalog(db, user_id)
            else:
                identities, catalog_error = [], "snapchat_accounts_unavailable"
            catalog_complete = catalog_error is None
            if catalog_error:
                reasons[catalog_error] += 1
            id_lookup = _unique_lookup(identities, "campaign_id")
            name_lookup = _unique_lookup(identities, "campaign_name")
            for identity in identities:
                await budget.checkpoint()
                if identity["campaign_id"] and identity["account_id"]:
                    exact_owners[identity["campaign_id"]].add(identity["account_id"])

        if not catalog_complete:
            unassigned_orders += 1
            continue
        exact_account, ambiguous_account = _exact_order_account(order, exact_owners)
        if exact_account is not None and exact_account not in buckets:
            outside_scope_orders += 1
            continue
        key, method = _match_order_campaign(order, id_lookup=id_lookup, name_lookup=name_lookup)
        if ambiguous_account:
            key, method = None, "ambiguous_id"
        if key is None:
            unassigned_orders += 1
            reasons[_unmatched_reason(order, method)] += 1
            continue
        if key[0] not in buckets:
            outside_scope_orders += 1
            continue
        bucket = buckets[key[0]]
        bucket["orders"] += 1
        amount = order_total_sar(order)
        if amount is None:
            bucket["currency_complete"] = False
        else:
            bucket["sales"] += amount

    attribution_complete = catalog_complete and not unassigned_orders
    accounts = []
    for account_id, bucket in buckets.items():
        known_sales = round(bucket["sales"], 2)
        accounts.append({
            "provider": "snapchat",
            "account_id": account_id,
            "salla_orders": bucket["orders"] if attribution_complete else None,
            "salla_sales_sar": (known_sales if attribution_complete
                                and bucket["currency_complete"] else None),
            "known_salla_orders": bucket["orders"],
            "known_salla_sales_sar": known_sales,
            "sales_currency_conversion_complete": bucket["currency_complete"],
            "attribution_complete": attribution_complete,
        })
    return {
        "date_from": date_from,
        "date_to": date_to,
        "timezone": "Asia/Riyadh",
        "source_only": True,
        "accounts": accounts,
        "coverage": {
            "status": ("unavailable" if not catalog_complete
                       else "partial" if unassigned_orders else "complete"),
            "unassigned_orders": unassigned_orders,
            "outside_account_scope_orders": outside_scope_orders,
            "catalog_complete": catalog_complete,
            "reason_codes": sorted(reasons),
        },
    }
