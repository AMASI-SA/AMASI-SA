"""Reuse the exact Salla campaign matches for campaign profitability.

The campaign result-source report already establishes the authoritative set of
Salla orders matched to each Snapchat campaign. This adapter captures that exact
set during the same report request and calculates product cost/profit from those
orders, preventing a second attribution pass from diverging from visible Salla
sales and order counts.
"""
from __future__ import annotations

from collections import defaultdict
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import hashlib
import json
from typing import Any
from weakref import WeakKeyDictionary, ref

from dashboard_v2_routes import _filtered_orders
from product_cost_revision import get_product_cost_revision
from order_status_policy import DEFAULT_POLICY, VALID_CATEGORIES, get_policy_map

from . import snapchat_campaign_profitability as profitability
from . import snapchat_campaign_result_source_routes as routes

SOURCE_MODE = "snapchat_salla_visible_matches_profitability_v2"
CACHE_TTL_SECONDS = 0
CACHE_REUSE_DISABLED_REASON = "catalog_identity_and_component_writes_lack_a_complete_snapshot_revision"
_MATCHED_ORDERS: ContextVar[dict[tuple[str, str], list[dict[str, Any]]] | None] = (
    ContextVar("snapchat_campaign_matched_orders", default=None)
)
# Compatibility diagnostic only. No calculation reads or writes this cache:
# product-cost revision does not cover all identity/component/policy writers.
_CACHE: dict = {}
MAX_CALCULATION_WORKERS = 2
_WORK_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_CALCULATION_WORKERS, thread_name_prefix="snap-exact-profit")
_WORK_LIMITERS = WeakKeyDictionary()


def _work_limiter():
    loop = asyncio.get_running_loop()
    existing = _WORK_LIMITERS.get(loop)
    limiter = existing() if existing is not None else None
    if limiter is None:
        limiter = asyncio.Semaphore(MAX_CALCULATION_WORKERS)
        _WORK_LIMITERS[loop] = ref(limiter)
    return limiter


async def _run_cpu(function, *args):
    future = asyncio.get_running_loop().run_in_executor(_WORK_EXECUTOR, function, *args)
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        # Do not free admission while a cancelled request's thread still owns
        # its large input/context. Repeated cancellation cannot orphan workers.
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if future.done() and not future.cancelled():
            future.exception()
        raise


def _copy_inputs(matched_orders, campaign_spend):
    try:
        return deepcopy(matched_orders), deepcopy(campaign_spend)
    except Exception:
        raise ValueError("campaign_profitability_snapshot_unproven") from None


class _CostStateChanged(Exception):
    """An observed revision/policy race, never a permission to use stale data."""


def _policy_fingerprint(policy):
    if not isinstance(policy, dict) or any(type(key) is not str or type(value) is not str or value not in VALID_CATEGORIES for key, value in policy.items()):
        raise ValueError("campaign_profitability_snapshot_unproven")
    content = json.dumps({"defaults": DEFAULT_POLICY, "overrides": policy}, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(content.encode()).hexdigest()


async def _cost_state(db, user_id):
    before = await get_product_cost_revision(db, user_id)
    policy = await get_policy_map(db, user_id)
    after = await get_product_cost_revision(db, user_id)
    if before != after:
        raise _CostStateChanged()
    return after, _policy_fingerprint(policy)


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
    cost_context = await profitability._load_cost_context(db, user_id)
    if (_policy_fingerprint(cost_context.get("policy")) != policy_fingerprint
            or (cost_revision, policy_fingerprint) != await _cost_state(db, user_id)):
        raise _CostStateChanged()
    result = await _run_cpu(_calculate_current_cohort, cost_context, matched_orders, campaign_spend)
    if (cost_revision, policy_fingerprint) != await _cost_state(db, user_id):
        raise _CostStateChanged()
    return result


def _calculate_current_cohort(cost_context, matched_orders, campaign_spend):
    """Pure calculation only: no database, provider or financial write seam."""
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
    # This exported compatibility seam accepts caller-owned mutable dicts.
    # Capture them before the first await, including admission waits. Callers
    # must not mutate them from OS threads during this synchronous capture.
    orders, spend = _copy_inputs(matched_orders, campaign_spend)
    async with _work_limiter():
        return await _calculate_admitted(db, user_id, date_from, date_to, orders, spend)


async def _calculate_owned_profitability(db, user_id, *, date_from, date_to, matched_orders, campaign_spend):
    """Only for the private report capture, whose records never leave this request.

    Unlike the exported dict seam, these producer-owned inputs have no external
    mutator while waiting or copying, so their copy may run off the event loop.
    """
    async with _work_limiter():
        orders, spend = await _run_cpu(_copy_inputs, matched_orders, campaign_spend)
        return await _calculate_admitted(db, user_id, date_from, date_to, orders, spend)


async def _calculate_admitted(db, user_id, date_from, date_to, orders, spend):
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

    @wraps(original_outcomes)
    async def outcomes_with_capture(*args: Any, **kwargs: Any):
        matched = kwargs.get("matched_orders_out")
        if matched is None:
            matched = {}
        result = await original_outcomes(*args, **{**kwargs, "matched_orders_out": matched})
        _MATCHED_ORDERS.set(matched)
        return result

    outcomes_with_capture._mezan_exact_match_capture = True  # type: ignore[attr-defined]
    routes._salla_outcomes = outcomes_with_capture

    current_report = routes.build_snapchat_result_source_report
    if getattr(current_report, "_mezan_exact_profitability_reuse", False):
        return

    # Preserve the other installers' markers. Otherwise a later router
    # composition wraps this report again and nested ContextVar resets hide
    # the captured cohort from the outer exact-profitability wrapper.
    @wraps(current_report)
    async def report_with_exact_profitability(*args: Any, **kwargs: Any):
        token = _MATCHED_ORDERS.set(None)
        try:
            result = await current_report(*args, **kwargs)
            matched = _MATCHED_ORDERS.get()
            if matched is None:
                raise ValueError("campaign_profitability_snapshot_unproven")
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
            # A captured empty cohort is different from a missing snapshot.
            # Replace any older report-layer profitability for empty campaigns
            # too; otherwise its independent query/cache could leak through.
            matched = dict(matched)
            for key in campaign_spend:
                matched.setdefault(key, [])
            by_campaign, totals = await _calculate_owned_profitability(
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
                "campaigns_with_orders": sum(bool(rows) for rows in matched.values()),
                "cache_ttl_seconds": CACHE_TTL_SECONDS,
                "cache_reuse_enabled": False,
                "cache_reuse_disabled_reason": CACHE_REUSE_DISABLED_REASON,
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
