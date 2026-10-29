"""Read-only Salla outcomes for Snapchat V2 account and campaign reporting.

Two date scopes are kept deliberately separate:

* legacy Salla account-audit totals keep their existing ``order_date`` calendar;
* Snapchat comparison totals and campaign rows use the selected ad-account
  timezone, localizing an authoritative order timestamp whenever available.

``order_date`` is only a fallback for the account-timezone comparison when no
usable timestamp exists. Source-only orders are never distributed or guessed
across campaigns.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo
from .period_candidates import bounded_period_cursor, period_candidate_query

from salla_marketing_attribution import (
    SALLA_RAW_ATTRIBUTION_PROJECTION,
    campaign_id_candidates,
    campaign_name_candidates,
    canonical_ad_platform,
    is_salla_clickid_campaign_placeholder,
    meaningful_source_label,
)

MAX_AUDIT_ROWS = 500
MAX_ORDER_ROWS = 100_000
MAX_OWNER_CAMPAIGN_ROWS = 100_000

ORDER_PROJECTION = {
    **SALLA_RAW_ATTRIBUTION_PROJECTION,
    "order_number": 1,
    "reference_id": 1,
    "order_id": 1,
    "id": 1,
    "created_at": 1,
    "order_created_at": 1,
    "created_at_utc": 1,
    "source_created_at": 1,
    "updated_at": 1,
    "order_date": 1,
    "order_date_inferred": 1,
    "order_status_native": 1,
    "status_native": 1,
    "order_status": 1,
    "status": 1,
    "total_amount": 1,
    "total": 1,
    "source_native": 1,
    "source": 1,
    "order_source": 1,
    "order_type": 1,
    "order_kind": 1,
    "type_of_order": 1,
    "is_gift": 1,
    "products": 1,
    # Older normalized snapshots can retain identity after a light provider
    # payload omits it. Order Engine already reads these same stored fields.
    "campaign_id": 1,
    "source_campaign_id": 1,
    "utm_campaign_id": 1,
    "ad_campaign_id": 1,
    "utm_campaign": 1,
    "campaign_name": 1,
    "source_campaign_name": 1,
    "ad_campaign_name": 1,
    "raw_by_source.salla_direct.date.date": 1,
    "raw_by_source.salla_direct.date.value": 1,
    "raw_by_source.salla_direct.date.timezone": 1,
    "raw_by_source.salla_direct.created_at": 1,
}


# These extra fields are only read by the full-cost Reports consumer.
from order_currency import SALLA_RAW_CURRENCY_PROJECTION
REPORT_COST_PROJECTION = {
    **SALLA_RAW_CURRENCY_PROJECTION,
    "shipping_company": 1, "shipping_cost": 1, "payment_method": 1,
    "payment_fee_status": 1, "actual_payment_fee": 1,
    "original_currency": 1, "currency": 1, "total_amount_sar": 1,
    "original_total_amount": 1, "exchange_rate": 1, "currency_conversion_status": 1,
    "accounting_currency": 1, "exchange_rate_to_sar": 1,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return " ".join(_text(value).casefold().replace("_", " ").split())


def _number(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if parsed == parsed and abs(parsed) != float("inf") else 0.0


def _matches_any(value: Any, allowed: list[str]) -> bool:
    if not allowed:
        return True
    normalized = _norm(value)
    return any(
        candidate
        and (
            candidate == normalized
            or candidate in normalized
            or normalized in candidate
        )
        for candidate in (_norm(item) for item in allowed)
    )


def _first_text(order: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = _text(order.get(field))
        if value:
            return value
    return ""


def _parse_datetime(value: Any) -> datetime | None:
    timezone_name = ""
    if isinstance(value, dict):
        timezone_name = _text(value.get("timezone"))
        value = value.get("date") or value.get("value")
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = _text(value)
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        if not timezone_name:
            return None
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        except Exception:
            return None
    return parsed.astimezone(timezone.utc)


def _order_timestamp(order: dict[str, Any]) -> datetime | None:
    for field in (
        "created_at",
        "order_created_at",
        "created_at_utc",
        "source_created_at",
    ):
        parsed = _parse_datetime(order.get(field))
        if parsed is not None:
            return parsed
    raw_by_source = order.get("raw_by_source")
    raw = (
        raw_by_source.get("salla_direct")
        if isinstance(raw_by_source, dict)
        else None
    )
    if isinstance(raw, dict):
        for value in (raw.get("created_at"), raw.get("date")):
            parsed = _parse_datetime(value)
            if parsed is not None:
                return parsed
    return None


def _localized_order_period_date(
    order: dict[str, Any],
    *,
    zone: ZoneInfo,
) -> tuple[str, str | None, str]:
    """Return the order date in the advertising-account timezone."""
    timestamp = _order_timestamp(order)
    if timestamp is not None:
        localized = timestamp.astimezone(zone)
        return (
            localized.date().isoformat(),
            localized.isoformat(),
            "created_at_localized_to_account_timezone",
        )
    fallback = _text(order.get("order_date"))[:10]
    return fallback, fallback or None, "order_date_fallback"


def _unique_lookup(
    identities: list[dict[str, Any]],
    field: str,
) -> dict[str, tuple[str, str] | None]:
    grouped: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in identities:
        value = _norm(row.get(field))
        key = (_text(row.get("account_id")), _text(row.get("campaign_id")))
        if value and all(key):
            grouped[value].add(key)
    return {
        value: next(iter(keys)) if len(keys) == 1 else None
        for value, keys in grouped.items()
    }


def _match_order_campaign(
    order: dict[str, Any],
    *,
    id_lookup: dict[str, tuple[str, str] | None],
    name_lookup: dict[str, tuple[str, str] | None],
) -> tuple[tuple[str, str] | None, str]:
    raw_by_source = order.get("raw_by_source")
    raw = raw_by_source.get("salla_direct") if isinstance(raw_by_source, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    platform = canonical_ad_platform(raw) or canonical_ad_platform(order)
    if platform and platform != "snapchat":
        return None, "foreign_platform"
    # An explicit provider identity/correction wins over a normalized fallback.
    # Click-only descriptions do not constitute campaign identity.
    raw_candidates = campaign_id_candidates(raw) + campaign_name_candidates(raw)
    evidence = raw if any(
        not is_salla_clickid_campaign_placeholder(value) for value in raw_candidates
    ) else order
    for candidate in campaign_id_candidates(evidence):
        normalized = _norm(candidate)
        if normalized and normalized in id_lookup:
            key = id_lookup[normalized]
            return (key, "campaign_id") if key else (None, "ambiguous_id")
    if platform == "snapchat":
        for candidate in campaign_name_candidates(evidence):
            normalized = _norm(candidate)
            if normalized and normalized in name_lookup:
                key = name_lookup[normalized]
                return (key, "campaign_name") if key else (None, "ambiguous_name")
    return None, "unmatched"


def _unmatched_reason(order: dict[str, Any], method: str) -> str:
    """Explain a failed match without inventing a campaign from a click ID."""
    if method != "unmatched":
        return method
    sources = order.get("raw_by_source")
    raw = sources.get("salla_direct") if isinstance(sources, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    raw_candidates = campaign_id_candidates(raw) + campaign_name_candidates(raw)
    candidates = raw_candidates if any(
        not is_salla_clickid_campaign_placeholder(value) for value in raw_candidates
    ) else campaign_id_candidates(order) + campaign_name_candidates(order) + raw_candidates
    if any(not is_salla_clickid_campaign_placeholder(value) for value in candidates):
        return "campaign_not_in_catalog"
    return "click_reference_only" if candidates else "campaign_identity_missing"


async def _to_list(cursor: Any, limit: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        try:
            return list(await cursor.to_list(length=limit + 1))
        except TypeError:
            return list(await cursor.to_list(limit + 1))
    rows: list[dict[str, Any]] = []
    async for row in cursor:
        rows.append(row)
        if len(rows) > limit:
            break
    return rows


async def _owner_campaign_catalog(db: Any, user_id: str) -> list[dict[str, Any]]:
    """Bounded owner/provider identities; never infer ownership from absence."""
    from .entities import SNAPCHAT_ENTITY_FACTS_COLLECTION

    collection = (db[SNAPCHAT_ENTITY_FACTS_COLLECTION] if hasattr(db, "__getitem__")
                  else getattr(db, SNAPCHAT_ENTITY_FACTS_COLLECTION, None))
    if collection is None:
        # Lightweight legacy callers have no identity store. No foreign order
        # can be excluded on that basis; their existing conservative proof stays.
        return []
    cursor = collection.find(
        {"user_id": str(user_id), "provider": "snapchat_ads", "entity_type": "campaign"},
        {"_id": 0, "ad_account_id": 1, "external_id": 1, "name": 1},
    )
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(MAX_OWNER_CAMPAIGN_ROWS + 1)
    if hasattr(cursor, "max_time_ms"):
        cursor = cursor.max_time_ms(15_000)
    rows = await _to_list(cursor, MAX_OWNER_CAMPAIGN_ROWS)
    if len(rows) > MAX_OWNER_CAMPAIGN_ROWS:
        raise ValueError("Snapchat campaign catalog exceeded the safe row limit")
    return [{"account_id": _text(row.get("ad_account_id")),
             "campaign_id": _text(row.get("external_id")),
             "campaign_name": row.get("name")} for row in rows]


def _exact_order_account(order: dict[str, Any], owners: dict[str, set[str]]) -> tuple[str | None, bool]:
    """Only exact ID evidence can exclude an order from another account."""
    sources = order.get("raw_by_source")
    raw = sources.get("salla_direct") if isinstance(sources, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    platform = canonical_ad_platform(raw) or canonical_ad_platform(order)
    if platform and platform != "snapchat":
        return None, False
    raw_candidates = campaign_id_candidates(raw) + campaign_name_candidates(raw)
    evidence = raw if any(not is_salla_clickid_campaign_placeholder(value)
                          for value in raw_candidates) else order
    matches = [owners[_text(value)] for value in campaign_id_candidates(evidence)
               if _text(value) in owners]
    matched_owners = set().union(*matches) if matches else set()
    return (next(iter(matched_owners)), False) if len(matched_owners) == 1 else (None, bool(matches))


async def _load_report_settings(db: Any, user_id: str) -> dict[str, Any]:
    """Read the existing report policy without creating or mutating settings."""
    collection = getattr(db, "settings", None)
    if collection is None and hasattr(db, "__getitem__"):
        collection = db["settings"]
    if collection is None:
        return {}
    projection = {
        "_id": 0,
        "report_included_statuses": 1,
        "hide_inferred_date_orders": 1,
    }
    try:
        row = await collection.find_one({"user_id": str(user_id)}, projection)
    except TypeError:
        row = await collection.find_one({"user_id": str(user_id)})
    return dict(row or {})


async def load_salla_campaign_outcomes(
    db: Any,
    user_id: str,
    *,
    account_id: str,
    date_from: date,
    date_to: date,
    timezone_name: str,
    identities: list[dict[str, Any]],
    platform_purchases: int = 0,
    campaign_spend_sar: dict[str, float] | None = None,
    include_report_costs: bool = False,
) -> dict[str, Any]:
    settings = await _load_report_settings(db, str(user_id))
    report_company_configs = {}
    if include_report_costs:
        from shipping_cost_ssot import get_company_configs
        from marketing_report_costs import order_costs, add_order_report_costs, finalize_report_costs
        from order_currency import order_total_sar
        report_company_configs = await get_company_configs(db, str(user_id))
        settings.update(await db.settings.find_one({"user_id": str(user_id)}, {"_id": 0, "payment_methods": 1}) or {})
    query = period_candidate_query(
        user_id, date_from, date_to,
        hide_inferred=settings.get("hide_inferred_date_orders"),
    )
    orders = await _to_list(
        bounded_period_cursor(db.unified_orders, query, ({**ORDER_PROJECTION, **REPORT_COST_PROJECTION} if include_report_costs else ORDER_PROJECTION), MAX_ORDER_ROWS),
        MAX_ORDER_ROWS,
    )
    if len(orders) > MAX_ORDER_ROWS:
        raise ValueError("Salla order audit exceeded the safe row limit")

    catalog = await _owner_campaign_catalog(db, str(user_id))
    all_identities = [*catalog, *identities]
    exact_owners: dict[str, set[str]] = defaultdict(set)
    for identity in all_identities:
        if _text(identity.get("campaign_id")) and _text(identity.get("account_id")):
            exact_owners[_text(identity["campaign_id"])].add(_text(identity["account_id"]))
    target_identities = [row for row in identities if _text(row.get("account_id")) == str(account_id)]
    id_lookup = _unique_lookup(target_identities, "campaign_id")
    name_lookup = _unique_lookup(target_identities, "campaign_name")
    # A name repeated across accounts cannot become a unique target match.
    global_names = _unique_lookup(all_identities, "campaign_name")
    for name in name_lookup:
        if global_names.get(name) is None:
            name_lookup[name] = None
    identity_by_key = {
        (_text(row.get("account_id")), _text(row.get("campaign_id"))): row
        for row in identities
    }
    zone = ZoneInfo(timezone_name)
    from_value = date_from.isoformat()
    to_value = date_to.isoformat()
    included_statuses = list(settings.get("report_included_statuses") or [])
    counters: Counter[str] = Counter()
    attribution_reasons: Counter[str] = Counter()
    by_campaign: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"orders": 0, "sales_sar": 0.0}
    )
    profitability_enabled = hasattr(db, "__getitem__")
    profitability_raw: dict[tuple[str, str], dict[str, Any]] = {}
    cost_context = None
    if profitability_enabled:
        from integrations_control_center.snapchat_campaign_profitability import (
            _add_order_to_campaign,
            _finalize_campaign,
            _load_cost_context,
            _new_campaign_bucket,
            _order_cost_and_products,
        )

        profitability_raw = defaultdict(_new_campaign_bucket)
        cost_context = await _load_cost_context(db, str(user_id))
    audit_rows: list[dict[str, Any]] = []
    total_financial_sales = 0.0
    snapchat_attributed_sales = 0.0
    snapchat_attributed_financial_sales = 0.0
    account_timezone_snapchat_attributed_sales = 0.0
    account_timezone_snapchat_attributed_financial_sales = 0.0

    for order in orders:
        exact_account, ambiguous_account = _exact_order_account(order, exact_owners)
        if exact_account is not None and exact_account != str(account_id):
            continue
        local_date, local_created_at, date_source = _localized_order_period_date(
            order,
            zone=zone,
        )
        salla_order_date = _text(order.get("order_date"))[:10]
        legacy_account_date = salla_order_date or local_date
        financial = _matches_any(order.get("order_status"), included_statuses)
        resolved_sar = order_total_sar(order) if include_report_costs else None
        amount = (resolved_sar or 0) if include_report_costs else _number(order.get("total_amount") or order.get("total"))
        source_platform = canonical_ad_platform(order)
        key, match_method = _match_order_campaign(
            order,
            id_lookup=id_lookup,
            name_lookup=name_lookup,
        )
        if ambiguous_account:
            key, match_method = None, "ambiguous_id"
        reported_snapchat_source = source_platform == "snapchat"
        snapchat_attributed = reported_snapchat_source or key is not None

        legacy_account_period_included = bool(
            legacy_account_date
            and from_value <= legacy_account_date <= to_value
        )
        if legacy_account_period_included:
            if reported_snapchat_source:
                counters["salla_reported_snapchat_orders"] += 1
            if snapchat_attributed:
                counters["snapchat_attributed_orders"] += 1
                snapchat_attributed_sales += amount
                if key is not None:
                    counters["account_period_campaign_matched_orders"] += 1
                if financial:
                    counters["snapchat_attributed_financial_orders"] += 1
                    snapchat_attributed_financial_sales += amount

        account_timezone_period_included = bool(
            local_date and from_value <= local_date <= to_value
        )
        if account_timezone_period_included:
            if reported_snapchat_source:
                counters["salla_reported_snapchat_orders_account_timezone"] += 1
            if snapchat_attributed:
                counters["snapchat_attributed_orders_account_timezone"] += 1
                account_timezone_snapchat_attributed_sales += amount
                if key is not None:
                    counters["account_period_campaign_matched_orders_account_timezone"] += 1
                else:
                    attribution_reasons[_unmatched_reason(order, match_method)] += 1
                if financial:
                    counters["snapchat_attributed_financial_orders_account_timezone"] += 1
                    account_timezone_snapchat_attributed_financial_sales += amount

        if not account_timezone_period_included:
            continue

        counters["total_salla_created_orders"] += 1
        if financial:
            counters["total_financial_orders"] += 1
            total_financial_sales += amount
        campaign_id = None
        campaign_name = None
        if key is not None:
            classification = "matched"
            counters["campaign_matched_orders"] += 1
            identity = identity_by_key.get(key, {})
            campaign_id = key[1]
            campaign_name = _text(identity.get("campaign_name")) or campaign_id
            by_campaign[key]["orders"] += 1
            if financial:
                counters["campaign_matched_financial_orders"] += 1
                by_campaign[key]["sales_sar"] += amount
                if cost_context is not None:
                    cost_order = {**order, "total_amount": resolved_sar or 0} if include_report_costs else order
                    order_result = _order_cost_and_products(cost_order, cost_context)
                    if include_report_costs and resolved_sar is None:
                        profitability_raw[key]["unknown_currency"] = True
                    _add_order_to_campaign(profitability_raw[key], order_result)
                    if include_report_costs:
                        add_order_report_costs(profitability_raw[key], order_result,
                                              order_costs(order, settings, report_company_configs))
        elif match_method.startswith("ambiguous"):
            classification = "ambiguous"
            counters["ambiguous_orders"] += 1
        else:
            classification = "non_campaign"
            counters["non_campaign_orders"] += 1

        audit_rows.append(
            {
                "order_number": _first_text(
                    order,
                    ("order_number", "reference_id", "order_id", "id"),
                ),
                "local_created_at": local_created_at,
                "local_date": local_date,
                "date_source": date_source,
                "timezone": timezone_name,
                "status": _first_text(
                    order,
                    ("order_status_native", "status_native", "order_status", "status"),
                ),
                "amount_sar": round(amount, 2),
                "financially_included": bool(financial),
                "source_label": meaningful_source_label(order) or None,
                "classification": classification,
                "match_method": match_method,
                "campaign_id": campaign_id,
                "campaign_name": campaign_name,
            }
        )

    for value in by_campaign.values():
        value["sales_sar"] = round(float(value["sales_sar"]), 2)
    audit_rows.sort(
        key=lambda row: (
            _text(row.get("local_created_at")),
            _text(row.get("order_number")),
        ),
        reverse=True,
    )
    matched_financial_sales = round(
        sum(float(value["sales_sar"]) for value in by_campaign.values()),
        2,
    )
    matched_financial_orders = int(counters["campaign_matched_financial_orders"])
    snapchat_attributed_orders = int(counters["snapchat_attributed_orders"])
    account_period_campaign_matched_orders = int(
        counters["account_period_campaign_matched_orders"]
    )
    campaign_match_coverage_pct = (
        round(
            account_period_campaign_matched_orders
            / snapchat_attributed_orders
            * 100,
            2,
        )
        if snapchat_attributed_orders > 0
        else None
    )
    account_timezone_snapchat_attributed_orders = int(
        counters["snapchat_attributed_orders_account_timezone"]
    )
    account_timezone_campaign_matched_orders = int(
        counters["account_period_campaign_matched_orders_account_timezone"]
    )
    account_timezone_campaign_match_coverage_pct = (
        round(
            account_timezone_campaign_matched_orders
            / account_timezone_snapchat_attributed_orders
            * 100,
            2,
        )
        if account_timezone_snapchat_attributed_orders > 0
        else None
    )
    comparison_mode = campaign_spend_sar is not None
    selected_snapchat_orders = (
        account_timezone_snapchat_attributed_orders
        if comparison_mode
        else snapchat_attributed_orders
    )
    selected_snapchat_sales = (
        account_timezone_snapchat_attributed_sales
        if comparison_mode
        else snapchat_attributed_sales
    )
    selected_snapchat_financial_orders = (
        int(counters["snapchat_attributed_financial_orders_account_timezone"])
        if comparison_mode
        else int(counters["snapchat_attributed_financial_orders"])
    )
    selected_snapchat_financial_sales = (
        account_timezone_snapchat_attributed_financial_sales
        if comparison_mode
        else snapchat_attributed_financial_sales
    )
    selected_campaign_matched_orders = (
        account_timezone_campaign_matched_orders
        if comparison_mode
        else account_period_campaign_matched_orders
    )
    selected_match_coverage = (
        account_timezone_campaign_match_coverage_pct
        if comparison_mode
        else campaign_match_coverage_pct
    )
    selected_date_scope = (
        "created_at_localized_to_ad_account_timezone_or_order_date_fallback"
        if comparison_mode
        else "salla_order_date"
    )
    spend_by_campaign = dict(campaign_spend_sar or {})
    profitability_by_campaign = (
        {
            campaign_id: _finalize_campaign(
                raw,
                spend_sar=_number(spend_by_campaign.get(campaign_id)),
            )
            for (row_account_id, campaign_id), raw in profitability_raw.items()
            if row_account_id == account_id
        }
        if profitability_enabled
        else {}
    )
    for (row_account_id, campaign_id), value in by_campaign.items():
        if row_account_id != account_id:
            continue
        profitability = profitability_by_campaign.get(campaign_id)
        if profitability is not None:
            if include_report_costs:
                finalize_report_costs(profitability, profitability_raw[(row_account_id, campaign_id)],
                                      spend_by_campaign.get(campaign_id))
                if profitability_raw[(row_account_id, campaign_id)].get("unknown_currency"):
                    profitability.update(sales_sar=None, net_profit_sar=None, net_margin_pct=None, net_profit_available=False)
                    value["sales_sar"] = None
                    for product in profitability["products"]:
                        product.update(sales_sar=None, net_profit_sar=None, net_margin_pct=None, net_profit_available=False)
            value["profitability"] = profitability
    return {
        "provider": "snapchat_ads",
        "account_id": account_id,
        "date_from": from_value,
        "date_to": to_value,
        "timezone": timezone_name,
        "by_campaign": {
            campaign_id: value
            for (row_account_id, campaign_id), value in by_campaign.items()
            if row_account_id == account_id
        },
        "summary": {
            "coverage_status": "complete",
            "campaign_attribution": {
                "status": "partial" if attribution_reasons else "complete",
                "evaluated_orders": account_timezone_snapchat_attributed_orders,
                "matched_orders": account_timezone_campaign_matched_orders,
                "unmatched_orders": sum(attribution_reasons.values()),
                "coverage_pct": account_timezone_campaign_match_coverage_pct,
                "reason_counts": dict(attribution_reasons),
                "date_scope": "account_timezone",
                "complete_population": True,
            },
            "total_salla_created_orders": int(counters["total_salla_created_orders"]),
            "total_financial_orders": int(counters["total_financial_orders"]),
            "total_financial_sales_sar": round(total_financial_sales, 2),
            "campaign_matched_orders": int(counters["campaign_matched_orders"]),
            "campaign_matched_financial_orders": matched_financial_orders,
            "campaign_matched_financial_sales_sar": matched_financial_sales,
            "salla_reported_snapchat_orders": (
                int(counters["salla_reported_snapchat_orders_account_timezone"])
                if comparison_mode
                else int(counters["salla_reported_snapchat_orders"])
            ),
            "snapchat_attributed_orders": selected_snapchat_orders,
            "snapchat_attributed_sales_sar": round(selected_snapchat_sales, 2),
            "snapchat_attributed_financial_orders": selected_snapchat_financial_orders,
            "snapchat_attributed_financial_sales_sar": round(
                selected_snapchat_financial_sales,
                2,
            ),
            "account_period_campaign_matched_orders": selected_campaign_matched_orders,
            "snapchat_attribution_gap_orders": max(
                0,
                selected_snapchat_orders - selected_campaign_matched_orders,
            ),
            "campaign_match_coverage_pct": selected_match_coverage,
            "salla_reported_snapchat_orders_salla_calendar": int(
                counters["salla_reported_snapchat_orders"]
            ),
            "snapchat_attributed_orders_salla_calendar": snapchat_attributed_orders,
            "snapchat_attributed_sales_sar_salla_calendar": round(
                snapchat_attributed_sales,
                2,
            ),
            "account_period_campaign_matched_orders_salla_calendar": (
                account_period_campaign_matched_orders
            ),
            "salla_reported_snapchat_orders_account_timezone": int(
                counters["salla_reported_snapchat_orders_account_timezone"]
            ),
            "snapchat_attributed_orders_account_timezone": (
                account_timezone_snapchat_attributed_orders
            ),
            "snapchat_attributed_sales_sar_account_timezone": round(
                account_timezone_snapchat_attributed_sales,
                2,
            ),
            "snapchat_attributed_financial_orders_account_timezone": int(
                counters["snapchat_attributed_financial_orders_account_timezone"]
            ),
            "snapchat_attributed_financial_sales_sar_account_timezone": round(
                account_timezone_snapchat_attributed_financial_sales,
                2,
            ),
            "account_period_campaign_matched_orders_account_timezone": (
                account_timezone_campaign_matched_orders
            ),
            "snapchat_attribution_gap_orders_account_timezone": max(
                0,
                account_timezone_snapchat_attributed_orders
                - account_timezone_campaign_matched_orders,
            ),
            "campaign_match_coverage_pct_account_timezone": (
                account_timezone_campaign_match_coverage_pct
            ),
            "non_campaign_orders": int(counters["non_campaign_orders"]),
            "ambiguous_orders": int(counters["ambiguous_orders"]),
            "platform_attributed_purchases": int(platform_purchases or 0),
            "platform_minus_confirmed_campaign_orders": int(platform_purchases or 0)
            - matched_financial_orders,
            "date_timezone": timezone_name,
            "campaign_attribution_policy": (
                "exact_campaign_id_or_unique_snapchat_campaign_name"
            ),
            "account_attribution_policy": (
                "salla_reported_snapchat_source_or_exact_campaign_match"
            ),
            "account_order_scope": "all_orders_created_in_period",
            "account_sales_scope": "gross_order_total_all_statuses",
            "account_date_scope": selected_date_scope,
            "snapchat_comparison_date_scope": (
                "created_at_localized_to_ad_account_timezone_or_order_date_fallback"
            ),
            "non_campaign_distribution_allowed": False,
            "profitability_scope": (
                "sales_minus_product_cost_minus_ad_spend_before_payment_shipping_bnpl_and_operating_allocations"
            ),
        },
        "orders": audit_rows[:MAX_AUDIT_ROWS],
        "orders_total": len(audit_rows),
        "orders_returned": min(len(audit_rows), MAX_AUDIT_ROWS),
        "truncated": len(audit_rows) > MAX_AUDIT_ROWS,
        "source_collection": "unified_orders",
        "source_only": True,
    }


__all__ = [
    "load_salla_campaign_outcomes",
    "_localized_order_period_date",
]
