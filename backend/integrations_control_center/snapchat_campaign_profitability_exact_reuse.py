"""Reuse the exact Salla campaign matches for campaign profitability.

The campaign result-source report already establishes the authoritative set of
Salla orders matched to each Snapchat campaign. This adapter captures that exact
set during the same report request and calculates product cost/profit from those
orders, preventing a second attribution pass from diverging from visible Salla
sales and order counts.
"""
from __future__ import annotations

from collections import defaultdict
from contextvars import ContextVar
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any
from bson import ObjectId
from bson.decimal128 import Decimal128
from bson.int64 import Int64

from dashboard_v2_routes import _filtered_orders
from product_cost_revision import get_product_cost_revision
from order_status_policy import DEFAULT_POLICY, VALID_CATEGORIES, get_policy_map

from . import snapchat_campaign_profitability as profitability
from . import snapchat_campaign_result_source_routes as routes

SOURCE_MODE = "snapchat_salla_visible_matches_profitability_v2"
CACHE_TTL_SECONDS = 5 * 60
_MATCHED_ORDERS: ContextVar[dict[tuple[str, str], list[dict[str, Any]]]] = (
    ContextVar("snapchat_campaign_matched_orders", default={})
)
_CACHE: dict[
    tuple[str, str, str, int, str, str],
    tuple[datetime, dict[tuple[str, str], dict[str, Any]], dict[str, Any]],
] = {}


class _CostStateChanged(Exception):
    """An observed revision/policy race, never a permission to use stale data."""


def _cache_value(value):
    """Typed canonical values; unsupported BSON/Python values bypass caching."""
    if value is None or type(value) in (str, bool, int, float):
        return [type(value).__name__, value]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise TypeError("unsupported cache document key")
        return ["dict", [[key, _cache_value(item)] for key, item in sorted(value.items())]]
    if type(value) in (list, tuple):
        return [type(value).__name__, [_cache_value(item) for item in value]]
    if type(value) in (datetime, date):
        return [type(value).__name__, value.isoformat()]
    if type(value) in (ObjectId, Decimal128, Decimal):
        return [type(value).__name__, str(value)]
    if type(value) is Int64:
        return ["bson.Int64", int(value)]
    raise TypeError("unsupported cache value")


def _policy_fingerprint(policy):
    if not isinstance(policy, dict) or any(type(key) is not str or type(value) is not str or value not in VALID_CATEGORIES for key, value in policy.items()):
        raise ValueError("campaign_profitability_snapshot_unproven")
    content = json.dumps(_cache_value({"defaults": DEFAULT_POLICY, "overrides": policy}), separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(content.encode()).hexdigest()


async def _cost_state(db, user_id):
    before = await get_product_cost_revision(db, user_id)
    policy = await get_policy_map(db, user_id)
    after = await get_product_cost_revision(db, user_id)
    if before != after:
        raise _CostStateChanged()
    return after, _policy_fingerprint(policy)


def _input_fingerprint(matched_orders, campaign_spend) -> str | None:
    """Bind the cache to the actual local cohort and current selected spend.

    Whole order documents are included because the existing cost/attribution
    helpers consume several aliases and refund/status fields. Hashes remain
    process-local; no customer fields or digests are logged or persisted.
    An input which cannot be serialized safely simply bypasses this cache.
    """
    try:
        campaigns = []
        for key, orders in sorted(matched_orders.items()):
            order_hashes = sorted(hashlib.sha256(json.dumps(
                _cache_value(order), separators=(",", ":"),
                allow_nan=False,
            ).encode()).hexdigest() for order in orders)
            campaigns.append([list(key), order_hashes])
        spend = [[list(key), _float(value)] for key, value in sorted(campaign_spend.items())]
        content = json.dumps([campaigns, spend], separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(content.encode()).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if parsed == parsed and abs(parsed) != float("inf") else 0.0


async def capture_exact_matched_orders(
    db: Any,
    user_id: str,
    *,
    date_from: str,
    date_to: str,
    identities: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Run the same matcher and retain only the exact orders used by Salla results."""
    orders = await _filtered_orders(
        db,
        user_id,
        from_date=date_from,
        to_date=date_to,
        payment_methods=None,
        shipping_companies=None,
        include_marketing_attribution=True,
    )
    id_lookup = routes._unique_lookup(identities, "campaign_id")
    name_lookup = routes._unique_lookup(identities, "campaign_name")
    matched: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for order in orders:
        key, _match_kind = routes._match_order_campaign(
            order,
            id_lookup=id_lookup,
            name_lookup=name_lookup,
        )
        if key is not None:
            matched[key].append(order)
    return dict(matched)


async def _calculate_profitability_attempt(
    db: Any,
    user_id: str,
    *,
    date_from: str,
    date_to: str,
    matched_orders: dict[tuple[str, str], list[dict[str, Any]]],
    campaign_spend: dict[tuple[str, str], float],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    cost_revision, policy_fingerprint = await _cost_state(db, user_id)
    input_fingerprint = _input_fingerprint(matched_orders, campaign_spend)
    cache_key = (user_id, date_from, date_to, cost_revision, policy_fingerprint, input_fingerprint)
    now = datetime.now(timezone.utc)
    cached = _CACHE.get(cache_key) if input_fingerprint is not None else None
    if cached and now - cached[0] < timedelta(seconds=CACHE_TTL_SECONDS):
        if (cost_revision, policy_fingerprint) != await _cost_state(db, user_id):
            raise _CostStateChanged()
        return deepcopy(cached[1]), deepcopy(cached[2])

    cost_context = await profitability._load_cost_context(db, user_id)
    if (_policy_fingerprint(cost_context.get("policy")) != policy_fingerprint
            or (cost_revision, policy_fingerprint) != await _cost_state(db, user_id)):
        raise _CostStateChanged()
    by_campaign: dict[tuple[str, str], dict[str, Any]] = {}
    for key, orders in matched_orders.items():
        bucket = profitability._new_campaign_bucket()
        for order in orders:
            profitability._add_order_to_campaign(
                bucket,
                profitability._order_cost_and_products(order, cost_context),
            )
        by_campaign[key] = profitability._finalize_campaign(
            bucket,
            spend_sar=round(_float(campaign_spend.get(key)), 6),
        )

    all_complete = all(
        row.get("product_cost_sar") is not None
        for row in by_campaign.values()
    )
    total_sales = round(sum(_float(row.get("sales_sar")) for row in by_campaign.values()), 2)
    total_known_cost = round(sum(_float(row.get("known_product_cost_sar")) for row in by_campaign.values()), 2)
    total_spend = round(sum(_float(row.get("ad_spend_sar")) for row in by_campaign.values()), 2)
    total_profit = (
        round(total_sales - total_known_cost - total_spend, 2)
        if all_complete else None
    )
    totals = {
        "orders": sum(int(row.get("orders") or 0) for row in by_campaign.values()),
        "sales_sar": total_sales,
        "product_cost_sar": total_known_cost if all_complete else None,
        "known_product_cost_sar": total_known_cost,
        "ad_spend_sar": total_spend,
        "contribution_profit_sar": total_profit,
        "profit_margin_pct": (
            round(total_profit / total_sales * 100, 2)
            if total_profit is not None and total_sales > 0 else None
        ),
        "campaigns_with_orders": len(by_campaign),
        "campaigns_with_missing_cost": sum(
            int(row.get("cost_status") == "missing")
            for row in by_campaign.values()
        ),
    }
    if (cost_revision, policy_fingerprint) != await _cost_state(db, user_id):
        raise _CostStateChanged()
    if input_fingerprint is not None:
        _CACHE[cache_key] = (now, deepcopy(by_campaign), deepcopy(totals))
    if len(_CACHE) > 32:
        oldest = min(_CACHE, key=lambda key: _CACHE[key][0])
        _CACHE.pop(oldest, None)
    return by_campaign, totals


async def calculate_profitability_from_exact_matches(
    db: Any,
    user_id: str,
    *,
    date_from: str,
    date_to: str,
    matched_orders: dict[tuple[str, str], list[dict[str, Any]]],
    campaign_spend: dict[tuple[str, str], float],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    """Retry one observed local race; never return/cache a known mixed state.

    Optimistic content/revision fences do not establish an atomic Mongo snapshot
    or upstream Salla freshness. Existing scale guards remain authoritative.
    """
    # Detach caller-owned dictionaries before any await so the input digest and
    # calculation use the same local cohort even if another task mutates them.
    orders = deepcopy(matched_orders)
    spend = deepcopy(campaign_spend)
    for _ in range(2):
        try:
            return await _calculate_profitability_attempt(
                db, user_id, date_from=date_from, date_to=date_to,
                matched_orders=orders, campaign_spend=spend,
            )
        except _CostStateChanged:
            continue
    raise ValueError("campaign_profitability_snapshot_unproven")


def install_exact_salla_profitability_reuse() -> None:
    if getattr(routes._salla_outcomes, "_mezan_exact_match_capture", False):
        return

    original_outcomes = routes._salla_outcomes

    async def outcomes_with_capture(*args: Any, **kwargs: Any):
        result = await original_outcomes(*args, **kwargs)
        db = args[0] if args else kwargs.get("db")
        user_id = args[1] if len(args) > 1 else kwargs.get("user_id")
        identities = kwargs.get("identities") or []
        date_from = _text(kwargs.get("date_from"))
        date_to = _text(kwargs.get("date_to"))
        if db is not None and user_id and date_from and date_to:
            matched = await capture_exact_matched_orders(
                db,
                str(user_id),
                date_from=date_from,
                date_to=date_to,
                identities=identities,
            )
            _MATCHED_ORDERS.set(matched)
        return result

    outcomes_with_capture._mezan_exact_match_capture = True  # type: ignore[attr-defined]
    routes._salla_outcomes = outcomes_with_capture

    current_report = routes.build_snapchat_result_source_report
    if getattr(current_report, "_mezan_exact_profitability_reuse", False):
        return

    async def report_with_exact_profitability(*args: Any, **kwargs: Any):
        token = _MATCHED_ORDERS.set({})
        try:
            result = await current_report(*args, **kwargs)
            matched = _MATCHED_ORDERS.get()
            if not matched:
                return result
            db = args[0] if args else kwargs.get("db")
            user_id = args[1] if len(args) > 1 else kwargs.get("user_id")
            date_from = _text(result.get("date_from"))
            date_to = _text(result.get("date_to"))
            if db is None or not user_id or not date_from or not date_to:
                return result

            campaign_spend = {
                (
                    _text(campaign.get("account_id")),
                    _text(campaign.get("campaign_id")),
                ): _float(campaign.get("spend_sar"))
                for campaign in result.get("campaigns") or []
                if _text(campaign.get("account_id"))
                and _text(campaign.get("campaign_id"))
            }
            by_campaign, totals = await calculate_profitability_from_exact_matches(
                db,
                str(user_id),
                date_from=date_from,
                date_to=date_to,
                matched_orders=matched,
                campaign_spend=campaign_spend,
            )
            for campaign in result.get("campaigns") or []:
                key = (
                    _text(campaign.get("account_id")),
                    _text(campaign.get("campaign_id")),
                )
                if key in by_campaign:
                    campaign["profitability"] = by_campaign[key]

            result.setdefault("totals", {})["profitability"] = totals
            result.setdefault("source", {}).setdefault(
                "campaign_profitability", {}
            ).update({
                "source_mode": SOURCE_MODE,
                "reuses_visible_salla_matches": True,
                "matched_orders": sum(len(rows) for rows in matched.values()),
                "campaigns_with_orders": len(matched),
                "cache_ttl_seconds": CACHE_TTL_SECONDS,
                "read_only": True,
            })
            return result
        finally:
            _MATCHED_ORDERS.reset(token)

    report_with_exact_profitability._mezan_exact_profitability_reuse = True  # type: ignore[attr-defined]
    routes.build_snapchat_result_source_report = report_with_exact_profitability


__all__ = [
    "CACHE_TTL_SECONDS",
    "SOURCE_MODE",
    "calculate_profitability_from_exact_matches",
    "capture_exact_matched_orders",
    "install_exact_salla_profitability_reuse",
]
