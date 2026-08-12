"""Read-only diagnostics joining Snapchat decisions to measured business change.

The result is deliberately an association report, not a causal model.  It
compares an inclusive selected window with the immediately preceding
equal-length window, then places immutable decision-ledger events on that
timeline.  Salla financially included orders own sales, Mezan owns product
cost, and Snapchat native daily facts own ad spend.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import snapchat_decision_ledger as decision_ledger
from .snapchat_decision_metrics import (
    campaign_performance_sync_coverage,
    resolve_decision_campaign_id,
)
from .snapchat_native_data_common import (
    SNAPCHAT_PERFORMANCE_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    _collection,
)


DIAGNOSTIC_SOURCE_MODE = "snapchat_decision_business_change_diagnostic_v1"
DECISION_LEAD_DAYS = 3
SUPPORTED_METRICS = {
    "sales_sar",
    "orders",
    "contribution_profit_sar",
    "ad_spend_sar",
    "roas",
    "cpa_sar",
}
OTHER_AD_PLATFORMS = {"meta", "tiktok", "google"}
SPEND_DEPENDENT_METRICS = {
    "contribution_profit_sar",
    "ad_spend_sar",
    "roas",
    "cpa_sar",
}


def _number(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if parsed == parsed and abs(parsed) != float("inf") else 0.0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_date(value: Any, name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_text(value)[:10])
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date") from exc


def _periods(date_from: Any, date_to: Any) -> dict[str, dict[str, Any]]:
    start = _parse_date(date_from, "date_from")
    end = _parse_date(date_to, "date_to")
    if end < start:
        raise ValueError("date_to must be on or after date_from")
    days = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)
    return {
        "selected": {
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "days": days,
        },
        "previous": {
            "date_from": previous_start.isoformat(),
            "date_to": previous_end.isoformat(),
            "days": days,
        },
        "decision_window": {
            "date_from": (start - timedelta(days=DECISION_LEAD_DAYS)).isoformat(),
            "date_to": end.isoformat(),
            "lead_days": DECISION_LEAD_DAYS,
        },
    }


def _period_key(value: Any, periods: dict[str, dict[str, Any]]) -> str | None:
    day = _text(value)[:10]
    for key in ("previous", "selected"):
        window = periods[key]
        if window["date_from"] <= day <= window["date_to"]:
            return key
    return None


def _source_bucket(source: Any) -> str:
    """Keep manual, explicit WhatsApp, other ads and unknown disjoint."""
    normalized = _text(source).casefold()
    if normalized == "snapchat":
        return "snapchat"
    if normalized in OTHER_AD_PLATFORMS:
        return "other_ad_platforms"
    if normalized == "manual":
        return "manual"
    if normalized == "whatsapp":
        return "whatsapp_explicit"
    if not normalized or normalized == "unknown":
        return "unknown"
    return "other_or_direct"


def _empty_source_breakdown() -> dict[str, dict[str, Any]]:
    return {
        key: {"orders": 0, "sales_sar": 0.0}
        for key in (
            "snapchat",
            "other_ad_platforms",
            "manual",
            "whatsapp_explicit",
            "unknown",
            "other_or_direct",
        )
    }


def _metric_change(previous: Any, selected: Any) -> dict[str, Any]:
    if previous is None or selected is None:
        return {
            "previous": previous,
            "selected": selected,
            "delta": None,
            "delta_pct": None,
            "direction": "unknown",
        }
    old = _number(previous)
    new = _number(selected)
    delta = round(new - old, 2)
    if abs(delta) < 0.005:
        direction = "flat"
    else:
        direction = "up" if delta > 0 else "down"
    return {
        "previous": round(old, 2),
        "selected": round(new, 2),
        "delta": delta,
        "delta_pct": round(delta / abs(old) * 100, 2) if old else None,
        "direction": direction,
    }


def _scope_delta(previous: dict[str, Any], selected: dict[str, Any]) -> dict[str, Any]:
    metrics = {}
    for key in (
        "orders",
        "sales_sar",
        "product_cost_sar",
        "ad_spend_sar",
        "contribution_profit_sar",
        "roas",
        "cpa_sar",
    ):
        metrics[key] = _metric_change(previous.get(key), selected.get(key))
    return {
        "previous": previous,
        "selected": selected,
        "changes": metrics,
        "sales_fell_but_contribution_profit_rose": bool(
            metrics["sales_sar"]["direction"] == "down"
            and metrics["contribution_profit_sar"]["direction"] == "up"
        ),
    }


def _empty_campaign_raw() -> dict[str, Any]:
    # Kept local so pure tests do not need the full product dependency stack.
    return {
        "orders": 0,
        "sales_sar": 0.0,
        "product_cost_sar": 0.0,
        "allocated_product_sales_sar": 0.0,
        "unallocated_sales_sar": 0.0,
        "missing_cost_orders": 0,
        "fallback_cost_orders": 0,
        "no_products_orders": 0,
        "products": {},
    }


def _aggregate_campaign_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    complete = all(row.get("product_cost_sar") is not None for row in rows)
    sales = round(sum(_number(row.get("sales_sar")) for row in rows), 2)
    known_cost = round(
        sum(_number(row.get("known_product_cost_sar")) for row in rows), 2
    )
    spend = round(sum(_number(row.get("ad_spend_sar")) for row in rows), 2)
    contribution = round(sales - known_cost - spend, 2) if complete else None
    orders = sum(int(row.get("orders") or 0) for row in rows)
    return {
        "orders": orders,
        "sales_sar": sales,
        "product_cost_sar": known_cost if complete else None,
        "known_product_cost_sar": known_cost,
        "ad_spend_sar": spend,
        "contribution_profit_sar": contribution,
        "roas": round(sales / spend, 4) if spend > 0 else None,
        "cpa_sar": round(spend / orders, 2) if orders > 0 else None,
        "cost_complete": complete,
        "campaign_count": len(rows),
        "profit_scope": (
            "exact_campaign_sales_minus_mezan_product_cost_minus_selected_"
            "snapchat_spend_before_payment_shipping_bnpl_and_operating_costs"
        ),
    }


async def _cursor_rows(cursor: Any, limit: int = 100_000) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=limit)
    return [row async for row in cursor]


async def _load_comparison_evidence(
    db: Any,
    user_id: str,
    *,
    periods: dict[str, dict[str, Any]],
    account_id: str | None,
) -> dict[str, Any]:
    """Load the two windows once from the existing authoritative readers."""
    # Lazy imports preserve the light-weight import/test surface of this module.
    from dashboard_v2_routes import _filtered_orders
    from salla_marketing_attribution import canonical_marketing_source
    from .snapchat_account_selection import _load_selected_accounts
    from .snapchat_campaign_profitability import (
        _add_order_to_campaign,
        _finalize_campaign,
        _load_cost_context,
        _order_cost_and_products,
    )
    from .snapchat_campaign_result_source_routes import (
        _campaign_identities,
        _match_order_campaign,
        _unique_lookup,
    )

    if account_id:
        account_ids = [account_id]
    else:
        accounts = await _load_selected_accounts(db, user_id)
        account_ids = sorted(
            {
                _text(row.get("ad_account_id"))
                for row in accounts
                if _text(row.get("ad_account_id"))
            }
        )

    combined_from = periods["previous"]["date_from"]
    combined_to = periods["selected"]["date_to"]
    performance_rows = (
        await _cursor_rows(
            _collection(db, SNAPCHAT_PERFORMANCE_COLLECTION).find(
                {
                    "user_id": user_id,
                    "provider": SNAPCHAT_PROVIDER_ID,
                    "ad_account_id": {"$in": account_ids},
                    "entity_type": "campaign",
                    "date": {"$gte": combined_from, "$lte": combined_to},
                },
                {"_id": 0},
            )
        )
        if account_ids
        else []
    )
    identities = (
        await _campaign_identities(
            db,
            user_id,
            account_ids=account_ids,
            performance_rows=performance_rows,
        )
        if account_ids
        else []
    )
    id_lookup = _unique_lookup(identities, "campaign_id")
    name_lookup = _unique_lookup(identities, "campaign_name")
    orders = await _filtered_orders(
        db,
        user_id,
        from_date=combined_from,
        to_date=combined_to,
        payment_methods=None,
        shipping_companies=None,
        include_marketing_attribution=True,
    )
    cost_context = await _load_cost_context(db, user_id)

    campaign_raw: dict[str, dict[tuple[str, str], dict[str, Any]]] = {
        "previous": defaultdict(_empty_campaign_raw),
        "selected": defaultdict(_empty_campaign_raw),
    }
    store_raw = {"previous": _empty_campaign_raw(), "selected": _empty_campaign_raw()}
    source_breakdown = {
        "previous": _empty_source_breakdown(),
        "selected": _empty_source_breakdown(),
    }
    product_sources: dict[str, dict[str, dict[str, dict[str, float]]]] = {
        "previous": defaultdict(
            lambda: defaultdict(lambda: {"units": 0.0, "sales_sar": 0.0})
        ),
        "selected": defaultdict(
            lambda: defaultdict(lambda: {"units": 0.0, "sales_sar": 0.0})
        ),
    }
    coverage = {
        "eligible_salla_orders": {"previous": 0, "selected": 0},
        "exact_campaign_orders": {"previous": 0, "selected": 0},
        "ambiguous_campaign_orders": {"previous": 0, "selected": 0},
        "unattributed_snapchat_orders": {"previous": 0, "selected": 0},
    }
    campaign_sync_by_account: dict[str, dict[str, Any]] = {}
    for selected_account_id in account_ids:
        try:
            campaign_sync_by_account[selected_account_id] = (
                await campaign_performance_sync_coverage(
                    db,
                    user_id,
                    account_id=selected_account_id,
                    windows={
                        "previous": periods["previous"],
                        "selected": periods["selected"],
                    },
                )
            )
        except Exception as exc:
            # Salla-owned sales/orders remain useful. Provider-spend-derived
            # metrics fail closed later when their durable proof is absent.
            campaign_sync_by_account[selected_account_id] = {
                "account_id": selected_account_id,
                "complete": False,
                "status": "campaign_performance_sync_proof_unavailable",
                "error_type": type(exc).__name__,
                "windows": {
                    "previous": {"complete": False},
                    "selected": {"complete": False},
                },
            }
    coverage["snapchat_campaign_performance_sync"] = {
        "complete": bool(account_ids)
        and all(
            row.get("complete") is True
            for row in campaign_sync_by_account.values()
        ),
        "accounts": campaign_sync_by_account,
        "required_windows": ["previous", "selected"],
        "required_for_metrics": sorted(SPEND_DEPENDENT_METRICS),
        "not_required_for_metrics": ["orders", "sales_sar"],
    }

    for order in orders:
        period = _period_key(
            order.get("order_date") or order.get("created_at") or order.get("date"),
            periods,
        )
        if period is None:
            continue
        coverage["eligible_salla_orders"][period] += 1
        result = _order_cost_and_products(order, cost_context)
        _add_order_to_campaign(store_raw[period], result)
        source = canonical_marketing_source(order)
        bucket_name = _source_bucket(source)
        source_bucket = source_breakdown[period][bucket_name]
        source_bucket["orders"] += 1
        source_bucket["sales_sar"] += _number(result.get("order_sales_sar"))
        for line in result.get("lines") or []:
            identity = _text(line.get("identity") or line.get("salla_product_id"))
            if not identity:
                continue
            product_sources[period][identity][bucket_name]["units"] += _number(
                line.get("units")
            )
            product_sources[period][identity][bucket_name]["sales_sar"] += _number(
                line.get("allocated_sales_sar")
            )

        key, match_kind = _match_order_campaign(
            order,
            id_lookup=id_lookup,
            name_lookup=name_lookup,
        )
        if key is None:
            if match_kind.startswith("ambiguous"):
                coverage["ambiguous_campaign_orders"][period] += 1
            elif _source_bucket(source) == "snapchat":
                coverage["unattributed_snapchat_orders"][period] += 1
            continue
        _add_order_to_campaign(campaign_raw[period][key], result)
        coverage["exact_campaign_orders"][period] += 1

    spend: dict[str, dict[tuple[str, str], float]] = {
        "previous": defaultdict(float),
        "selected": defaultdict(float),
    }
    for row in performance_rows:
        period = _period_key(row.get("date"), periods)
        key = (
            _text(row.get("ad_account_id")),
            _text(row.get("campaign_id") or row.get("external_id")),
        )
        if period and all(key):
            spend[period][key] += _number(row.get("spend_sar"))

    campaign_metrics: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    store_metrics: dict[str, dict[str, Any]] = {}
    account_metrics: dict[str, dict[str, dict[str, Any]]] = {}
    for period in ("previous", "selected"):
        keys = set(campaign_raw[period]) | set(spend[period])
        campaign_metrics[period] = {
            key: _finalize_campaign(
                campaign_raw[period].get(key, _empty_campaign_raw()),
                spend_sar=round(spend[period].get(key, 0.0), 6),
            )
            for key in keys
        }
        selected_spend = sum(spend[period].values())
        store_metrics[period] = _finalize_campaign(
            store_raw[period], spend_sar=round(selected_spend, 6)
        )
        store_metrics[period]["profit_scope"] = (
            "whole_store_sales_minus_mezan_product_cost_minus_selected_"
            "snapchat_spend_only; other_marketing_and_operating_costs_excluded"
        )
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for (row_account, _campaign), metric_row in campaign_metrics[period].items():
            grouped[row_account].append(metric_row)
        account_metrics[period] = {
            row_account: _aggregate_campaign_metrics(rows)
            for row_account, rows in grouped.items()
        }
        for source_row in source_breakdown[period].values():
            source_row["sales_sar"] = round(source_row["sales_sar"], 2)

    return {
        "account_ids": account_ids,
        "campaigns": campaign_metrics,
        "accounts": account_metrics,
        "store": store_metrics,
        "source_breakdown": source_breakdown,
        "product_sources": product_sources,
        "coverage": coverage,
    }


async def _load_decisions(
    db: Any,
    user_id: str,
    *,
    periods: dict[str, dict[str, Any]],
    account_id: str | None,
) -> list[dict[str, Any]]:
    window = periods["decision_window"]
    start = f"{window['date_from']}T00:00:00"
    end = f"{window['date_to']}T23:59:59.999999+00:00"
    query: dict[str, Any] = {
        "user_id": user_id,
        "entry_type": "change",
        "effective_at": {"$gte": start, "$lte": end},
    }
    if account_id:
        query["account_id"] = account_id
    rows = await _cursor_rows(
        _collection(db, decision_ledger.DECISION_LEDGER_COLLECTION).find(
            query, {"_id": 0, "decision_id": 1}
        ),
        limit=10_000,
    )
    decision_ids = list(
        dict.fromkeys(
            _text(row.get("decision_id"))
            for row in rows
            if _text(row.get("decision_id"))
        )
    )
    output = []
    for decision_id in decision_ids:
        detail = await decision_ledger.get_ad_decision(db, user_id, decision_id)
        if detail is not None:
            effective_day = _text(detail.get("effective_at"))[:10]
            if window["date_from"] <= effective_day <= window["date_to"]:
                output.append(detail)
    output.sort(
        key=lambda row: (_text(row.get("effective_at")), _text(row.get("decision_id")))
    )
    return output


def _decision_expected_direction(
    decision: dict[str, Any],
    metric: str = "sales_sar",
) -> tuple[str | None, str]:
    """Infer a bounded metric direction, never a causal outcome forecast."""
    # Less spend can improve or reduce contribution profit depending on the
    # sales and margin response.  The ledger change alone cannot resolve it.
    if metric == "contribution_profit_sar":
        return None, "profit_direction_not_derivable_from_ad_setting_change"
    if metric in {"roas", "cpa_sar"}:
        return None, "efficiency_direction_not_derivable_from_ad_setting_change"
    diffs = decision.get("field_diffs") or []
    for diff in diffs:
        if not isinstance(diff, dict):
            continue
        field = _text(diff.get("field")).casefold()
        before, after = diff.get("before"), diff.get("after")
        if any(token in field for token in ("budget", "bid", "spend_cap")):
            old, new = _number(before), _number(after)
            if new != old:
                return ("up" if new > old else "down"), f"numeric_{field}_change"
        if any(token in field for token in ("status", "state")):
            new = _text(after).upper()
            if new in {"ACTIVE", "ENABLED", "RUNNING"}:
                return "up", f"{field}_enabled"
            if new in {
                "PAUSED",
                "INACTIVE",
                "NOT_ACTIVE",
                "DISABLED",
                "STOPPED",
            }:
                return "down", f"{field}_disabled"
    # Action names alone are not delivery evidence.  In particular every
    # governed create starts PAUSED, and deleting an already-paused entity has
    # no expected sales direction.  Only explicit delivery verbs may provide a
    # fallback when the provider field diff is unavailable.
    action = _text(decision.get("action")).casefold()
    if any(
        token in action
        for token in ("resume", "enable", "activate", "increase")
    ):
        return "up", "action_direction"
    if any(
        token in action for token in ("pause", "disable", "stop", "decrease")
    ):
        return "down", "action_direction"
    return None, "direction_not_derivable_from_ledger_change"


def _context_evidence(
    decision: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Classify each context claim independently; never inherit verification."""
    categories = {
        "salary_or_payday": ("salary", "payday", "راتب", "الرواتب"),
        "season": ("season", "ramadan", "eid", "موسم", "رمضان", "العيد"),
        "trend": ("trend", "اتجاه", "ترند"),
    }
    verified: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def category_for(value: Any) -> str | None:
        content = _text(value).casefold()
        return next(
            (
                category
                for category, tokens in categories.items()
                if any(token in content for token in tokens)
            ),
            None,
        )

    evidence = decision.get("evidence")
    claims = (
        evidence.get("decision_evidence") or [] if isinstance(evidence, dict) else []
    )
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        category = category_for(
            {
                "kind": claim.get("kind"),
                "value": claim.get("value"),
                "source": claim.get("source"),
            }
        )
        if not category:
            continue
        status = _text(claim.get("verification_status")).casefold()
        row = {
            "category": category,
            "used_as_primary_basis": False,
            "used_as_supporting_factor": claim.get("used_in_decision") is True,
            "source": claim.get("source"),
        }
        if status == "verified":
            verified.append({**row, "status": "explicitly_verified_in_ledger_evidence"})
            seen.add((category, "verified"))
        else:
            unverified.append({**row, "status": "unverified_context_not_used"})
            seen.add((category, "unverified"))

    baseline = decision.get("baseline")
    recent_trend = baseline.get("recent_trend") if isinstance(baseline, dict) else None
    if isinstance(recent_trend, dict) and recent_trend.get("signals"):
        verified.append(
            {
                "category": "trend",
                "status": "verified_from_measured_baseline_windows",
                "used_as_primary_basis": False,
                "used_as_supporting_factor": True,
            }
        )
        seen.add(("trend", "verified"))

    free_text = " ".join(
        _text(value)
        for value in (
            decision.get("reason"),
            decision.get("expected"),
            *(
                row.get("text")
                for row in (decision.get("annotations") or [])
                if isinstance(row, dict)
            ),
        )
    )
    for category, tokens in categories.items():
        if not any(token in free_text.casefold() for token in tokens):
            continue
        if (category, "verified") in seen or (category, "unverified") in seen:
            continue
        unverified.append(
            {
                "category": category,
                "status": "unverified_context_not_used",
                "used_as_primary_basis": False,
                "used_as_supporting_factor": False,
            }
        )
    return verified, unverified


def _campaign_product_evidence(
    evidence: dict[str, Any],
    account_id: str,
    campaign_id: str,
) -> list[dict[str, Any]]:
    key = (account_id, campaign_id)
    previous = evidence["campaigns"]["previous"].get(key) or {}
    selected = evidence["campaigns"]["selected"].get(key) or {}
    previous_store = {
        row.get("identity"): row
        for row in (evidence["store"]["previous"].get("products") or [])
    }
    selected_store = {
        row.get("identity"): row
        for row in (evidence["store"]["selected"].get("products") or [])
    }
    previous_campaign = {
        row.get("identity"): row for row in (previous.get("products") or [])
    }
    selected_campaign = {
        row.get("identity"): row for row in (selected.get("products") or [])
    }
    identities = set(previous_campaign) | set(selected_campaign)
    rows = []
    for identity in identities:
        old = previous_campaign.get(identity) or {}
        new = selected_campaign.get(identity) or {}
        store_old = previous_store.get(identity) or {}
        store_new = selected_store.get(identity) or {}
        source_rows = (
            evidence.get("product_sources", {}).get("selected", {}).get(identity, {})
        )
        rows.append(
            {
                "identity": identity,
                "salla_product_id": new.get("salla_product_id")
                or old.get("salla_product_id"),
                "name": new.get("name") or old.get("name"),
                "campaign_exact_attribution": {
                    "units": _metric_change(old.get("units", 0), new.get("units", 0)),
                    "sales_sar": _metric_change(
                        old.get("sales_sar", 0), new.get("sales_sar", 0)
                    ),
                },
                "whole_store_product": {
                    "units": _metric_change(
                        store_old.get("units", 0), store_new.get("units", 0)
                    ),
                    "sales_sar": _metric_change(
                        store_old.get("sales_sar", 0), store_new.get("sales_sar", 0)
                    ),
                },
                "selected_period_source_units": {
                    source: round(_number(values.get("units")), 2)
                    for source, values in source_rows.items()
                },
                "manual_source_policy": "manual_is_not_whatsapp_without_explicit_whatsapp_evidence",
            }
        )
    rows.sort(
        key=lambda row: -_number(row["whole_store_product"]["sales_sar"]["selected"])
    )
    return rows


def _classify_association(
    *,
    expected_direction: str | None,
    measured: dict[str, Any],
    scope: str,
    timing: str,
    cost_complete: bool,
) -> tuple[str, float, list[str]]:
    direction = measured.get("direction")
    caveats = ["temporal_association_is_not_causation"]
    if direction == "unknown" or expected_direction is None:
        return (
            "insufficient",
            0.2,
            caveats + ["decision_or_metric_direction_unavailable"],
        )
    if not cost_complete:
        return "insufficient", 0.25, caveats + ["mezan_product_cost_incomplete"]
    exact = scope == "campaign_exact"
    timing_score = 0.08 if timing == "inside_selected_period" else 0.0
    if direction == "flat":
        return (
            "association",
            round(0.35 + timing_score, 2),
            caveats + ["measured_metric_was_flat"],
        )
    if direction != expected_direction:
        return (
            "contradictory",
            round((0.62 if exact else 0.45) + timing_score, 2),
            caveats,
        )
    if exact:
        return "likely_contributor", round(0.68 + timing_score, 2), caveats
    return (
        "association",
        round(0.45 + timing_score, 2),
        caveats + ["scope_not_isolated_to_one_campaign"],
    )


def _campaign_fact_windows_complete(
    coverage: dict[str, Any],
    account_ids: list[str] | set[str] | tuple[str, ...],
) -> bool:
    sync = coverage.get("snapchat_campaign_performance_sync")
    accounts = sync.get("accounts") if isinstance(sync, dict) else None
    requested = [_text(account_id) for account_id in account_ids if _text(account_id)]
    if not requested or not isinstance(accounts, dict):
        return False
    for account_id in requested:
        account = accounts.get(account_id)
        windows = account.get("windows") if isinstance(account, dict) else None
        if not (
            isinstance(account, dict)
            and account.get("complete") is True
            and isinstance(windows, dict)
            and all(
                isinstance(windows.get(name), dict)
                and windows[name].get("complete") is True
                for name in ("previous", "selected")
            )
        ):
            return False
    return True


async def diagnose_ad_business_change(
    db: Any,
    user_id: str,
    *,
    date_from: Any,
    date_to: Any,
    metric: str = "sales_sar",
    account_id: str | None = None,
) -> dict[str, Any]:
    """Explain measured change around decisions without writing or claiming cause."""
    tenant = _text(user_id)
    if not tenant:
        raise ValueError("user_id is required")
    metric_name = _text(metric) or "sales_sar"
    if metric_name not in SUPPORTED_METRICS:
        raise ValueError(f"metric must be one of {sorted(SUPPORTED_METRICS)}")
    account = _text(account_id) or None
    periods = _periods(date_from, date_to)
    evidence = await _load_comparison_evidence(
        db, tenant, periods=periods, account_id=account
    )
    decisions = await _load_decisions(db, tenant, periods=periods, account_id=account)
    measured_account_ids = set(evidence.get("account_ids") or [])
    if account is None:
        decisions = [
            row
            for row in decisions
            if _text(row.get("account_id")) in measured_account_ids
        ]

    campaign_changes: dict[tuple[str, str], dict[str, Any]] = {}
    campaign_keys = set(evidence["campaigns"]["previous"]) | set(
        evidence["campaigns"]["selected"]
    )
    for key in campaign_keys:
        campaign_changes[key] = _scope_delta(
            evidence["campaigns"]["previous"].get(key, _aggregate_campaign_metrics([])),
            evidence["campaigns"]["selected"].get(key, _aggregate_campaign_metrics([])),
        )
    account_changes: dict[str, dict[str, Any]] = {}
    account_keys = set(evidence["accounts"]["previous"]) | set(
        evidence["accounts"]["selected"]
    )
    for key in account_keys:
        account_changes[key] = _scope_delta(
            evidence["accounts"]["previous"].get(key, _aggregate_campaign_metrics([])),
            evidence["accounts"]["selected"].get(key, _aggregate_campaign_metrics([])),
        )
    store_change = _scope_delta(
        evidence["store"]["previous"], evidence["store"]["selected"]
    )

    evidence_coverage = evidence.get("coverage", {})
    evaluated = []
    selected_from = periods["selected"]["date_from"]
    previous_from = periods["previous"]["date_from"]
    for decision in decisions:
        decision_account = _text(decision.get("account_id"))
        entity_type = _text(
            decision.get("entity_type") or (decision.get("entity") or {}).get("type")
        )
        entity_id = (
            _text(decision.get("entity_id") or (decision.get("entity") or {}).get("id"))
            or None
        )
        parent_id = None
        for snapshot in (decision.get("after"), decision.get("before")):
            if isinstance(snapshot, dict):
                parent_id = (
                    _text(snapshot.get("parent_id") or snapshot.get("campaign_id"))
                    or parent_id
                )
        campaign_id = await resolve_decision_campaign_id(
            db,
            tenant,
            account_id=decision_account,
            entity_type=entity_type,
            entity_id=entity_id,
            parent_id=parent_id,
        )
        if campaign_id and (decision_account, campaign_id) in campaign_changes:
            scope = "campaign_exact"
            scoped = campaign_changes[(decision_account, campaign_id)]
            product_evidence = _campaign_product_evidence(
                evidence, decision_account, campaign_id
            )
        elif decision_account in account_changes:
            scope = "account_association"
            scoped = account_changes[decision_account]
            product_evidence = []
        else:
            scope = "store_context"
            scoped = store_change
            product_evidence = []
        measured = scoped["changes"][metric_name]
        expected_direction, direction_basis = _decision_expected_direction(
            decision, metric_name
        )
        effective_day = _text(decision.get("effective_at"))[:10]
        if effective_day >= selected_from:
            timing = "inside_selected_period"
        elif effective_day >= previous_from:
            timing = "inside_previous_period"
        else:
            timing = "before_comparison_periods"
        cost_complete = True
        if metric_name == "contribution_profit_sar":
            cost_complete = bool(scoped["selected"].get("product_cost_sar") is not None)
        execution_status = _text(decision.get("execution_status")).casefold()
        if execution_status == "failed":
            classification, confidence, caveats = (
                "insufficient",
                0.05,
                [
                    "temporal_association_is_not_causation",
                    "decision_execution_failed_no_provider_effect_assumed",
                ],
            )
        elif execution_status == "rolled_back":
            classification, confidence, caveats = (
                "insufficient",
                0.15,
                [
                    "temporal_association_is_not_causation",
                    "decision_was_rolled_back_and_exposure_is_not_isolated",
                ],
            )
        else:
            classification, confidence, caveats = _classify_association(
                expected_direction=expected_direction,
                measured=measured,
                scope=scope,
                timing=timing,
                cost_complete=cost_complete,
            )
        spend_coverage_complete = _campaign_fact_windows_complete(
            evidence_coverage,
            [decision_account],
        )
        if metric_name in SPEND_DEPENDENT_METRICS and not spend_coverage_complete:
            classification = "insufficient"
            confidence = min(confidence, 0.1)
            caveats = list(caveats) + [
                "snapchat_campaign_performance_sync_incomplete_for_previous_and_selected_windows"
            ]
            measured = _metric_change(None, None)
        if timing == "inside_selected_period" and execution_status not in {
            "failed",
            "rolled_back",
        }:
            # The selected aggregate contains outcomes from before and after
            # this decision.  Without a post-decision-only slice it cannot
            # truthfully rank the decision as a likely contributor (or as
            # contradictory), even when the directions happen to align.
            classification = "insufficient"
            confidence = min(confidence, 0.2)
            caveats = list(caveats) + [
                "selected_period_contains_pre_decision_results",
                "post_decision_only_measurement_required",
            ]
        elif timing == "inside_previous_period" and execution_status not in {
            "failed",
            "rolled_back",
        }:
            # The baseline itself contains both pre- and post-decision days, so
            # comparing it with the selected aggregate cannot isolate impact.
            classification = "insufficient"
            confidence = min(confidence, 0.2)
            caveats = list(caveats) + [
                "baseline_contains_post_decision_results",
                "pre_and_post_decision_slices_required",
            ]
        unresolved = evidence.get("coverage", {}).get(
            "ambiguous_campaign_orders", {}
        ).get("selected", 0) + evidence.get("coverage", {}).get(
            "unattributed_snapchat_orders", {}
        ).get(
            "selected", 0
        )
        if unresolved:
            confidence = round(max(confidence - 0.08, 0.0), 2)
            caveats.append("selected_period_has_unresolved_snapchat_attribution")
        verified_context, unverified_context = _context_evidence(decision)
        evaluated.append(
            {
                "decision_id": decision.get("decision_id"),
                "account_id": decision_account,
                "campaign_id": campaign_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "action": decision.get("action"),
                "execution_status": decision.get("execution_status"),
                "effective_at": decision.get("effective_at"),
                "timing": timing,
                "measurement_scope": scope,
                "metric": metric_name,
                "measured_change": measured,
                "expected_operational_direction": expected_direction,
                "direction_basis": direction_basis,
                "classification": classification,
                "confidence": confidence,
                "association_not_causation": True,
                "verified_context_not_used_as_primary_basis": verified_context,
                "unverified_context_not_used": unverified_context,
                "product_evidence": product_evidence,
                "caveats": caveats,
            }
        )

    # Several settings changed on the same campaign in one evaluation window
    # cannot be separated by a before/after comparison.  Keep the association,
    # but do not rank one of the overlapping changes as the likely contributor.
    campaign_decision_counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in evaluated:
        if row.get("campaign_id"):
            campaign_decision_counts[(row["account_id"], row["campaign_id"])] += 1
    for row in evaluated:
        key = (row["account_id"], row.get("campaign_id"))
        if row.get("campaign_id") and campaign_decision_counts[key] > 1:
            if row["classification"] == "likely_contributor":
                row["classification"] = "association"
            row["confidence"] = round(max(_number(row["confidence"]) - 0.15, 0.0), 2)
            row["caveats"].append("multiple_campaign_decisions_overlap_the_same_window")

    priority = {
        "likely_contributor": 3,
        "association": 2,
        "contradictory": 1,
        "insufficient": 0,
    }
    evaluated.sort(
        key=lambda row: (
            -priority[row["classification"]],
            -_number(row["confidence"]),
            -abs(_number(row["measured_change"].get("delta"))),
        )
    )
    likely = [
        {
            "decision_id": row["decision_id"],
            "classification": row["classification"],
            "confidence": row["confidence"],
            "measurement_scope": row["measurement_scope"],
            "measured_change": row["measured_change"],
            "caveats": row["caveats"],
        }
        for row in evaluated
        if row["classification"] in {"likely_contributor", "association"}
    ]

    source_change = {
        bucket: _scope_delta(
            evidence["source_breakdown"]["previous"][bucket],
            evidence["source_breakdown"]["selected"][bucket],
        )
        for bucket in evidence["source_breakdown"]["selected"]
    }
    if account:
        headline_scope = account_changes.get(account) or _scope_delta(
            _aggregate_campaign_metrics([]), _aggregate_campaign_metrics([])
        )
        headline_name = "selected_snapchat_account_exact_attribution"
    else:
        previous_total = _aggregate_campaign_metrics(
            list(evidence["campaigns"]["previous"].values())
        )
        selected_total = _aggregate_campaign_metrics(
            list(evidence["campaigns"]["selected"].values())
        )
        headline_scope = _scope_delta(previous_total, selected_total)
        headline_name = "selected_snapchat_accounts_exact_attribution"

    headline_account_ids = [account] if account else sorted(measured_account_ids)
    campaign_fact_coverage_complete = _campaign_fact_windows_complete(
        evidence_coverage,
        headline_account_ids,
    )
    requested_metric_complete = bool(
        metric_name not in SPEND_DEPENDENT_METRICS
        or campaign_fact_coverage_complete
    )
    headline_change = (
        headline_scope["changes"][metric_name]
        if requested_metric_complete
        else _metric_change(None, None)
    )
    top_level_caveats = [
        "decision_timing_and_direction_support_association_not_causation",
        "salary_season_and_trend_context_is_not_a_basis_without_verified_ledger_evidence",
        "contribution_profit_excludes_payment_shipping_bnpl_and_operating_allocations",
        "historical_periods_use_current_mezan_catalog_cost_resolution",
        "whole_store_contribution_subtracts_selected_snapchat_spend_only",
    ]
    if not requested_metric_complete:
        top_level_caveats.append(
            "snapchat_campaign_performance_sync_incomplete_requested_metric_not_measurable"
        )

    return {
        "source_mode": DIAGNOSTIC_SOURCE_MODE,
        "read_only": True,
        "provider": "snapchat_ads",
        "metric": metric_name,
        "periods": periods,
        "headline": {
            "scope": headline_name,
            **headline_change,
            "data_complete": requested_metric_complete,
            "sales_fell_but_contribution_profit_rose": (
                headline_scope["sales_fell_but_contribution_profit_rose"]
                if campaign_fact_coverage_complete
                else None
            ),
        },
        "measured_changes": {
            "store": store_change,
            "accounts": [
                {"account_id": key, **value}
                for key, value in sorted(account_changes.items())
            ],
            "campaigns": [
                {"account_id": key[0], "campaign_id": key[1], **value}
                for key, value in sorted(campaign_changes.items())
            ],
        },
        "store_order_sources": source_change,
        "decisions": evaluated,
        "likely_contributors": likely,
        "coverage": {
            **evidence["coverage"],
            "selected_account_ids": evidence.get("account_ids") or [],
            "sales_source": "financially_included_salla_orders",
            "product_cost_source": "mezan_v2_cost_engine",
            "product_cost_temporality": "current_mezan_catalog_cost_resolution",
            "spend_source": "snapchat_native_daily_facts",
            "spend_dependent_metrics_require_complete_campaign_fact_proof": True,
            "manual_source_policy": (
                "salla_manual_is_a_separate_observed_source_and_is_never_"
                "labelled_whatsapp_without_explicit_order_evidence"
            ),
            "other_platform_policy": (
                "meta_tiktok_google_are_separate_from_snapchat; unknown_and_"
                "manual_remain_unresolved_for_snapchat_decisions"
            ),
        },
        "caveats": top_level_caveats,
    }


__all__ = [
    "DECISION_LEAD_DAYS",
    "DIAGNOSTIC_SOURCE_MODE",
    "SUPPORTED_METRICS",
    "diagnose_ad_business_change",
]
