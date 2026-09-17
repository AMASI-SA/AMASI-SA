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
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from salla_marketing_attribution import (
    SALLA_RAW_ATTRIBUTION_PROJECTION,
    campaign_id_candidates,
    campaign_name_candidates,
    canonical_ad_platform,
    meaningful_source_label,
)

MAX_AUDIT_ROWS = 500
MAX_ORDER_ROWS = 100_000

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
    "raw_by_source.salla_direct.date.date": 1,
    "raw_by_source.salla_direct.date.timezone": 1,
    "raw_by_source.salla_direct.created_at": 1,
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
    raw = raw_by_source.get("salla_direct") if isinstance(raw_by_source, dict) else None
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
    provider_key: str = "snapchat",
) -> tuple[tuple[str, str] | None, str]:
    platform = canonical_ad_platform(order)
    if platform and platform != provider_key:
        return None, "foreign_platform"
    for candidate in campaign_id_candidates(order):
        normalized = _norm(candidate)
        if normalized and normalized in id_lookup:
            key = id_lookup[normalized]
            return (key, "campaign_id") if key else (None, "ambiguous_id")
    if platform == provider_key:
        for candidate in campaign_name_candidates(order):
            normalized = _norm(candidate)
            if normalized and normalized in name_lookup:
                key = name_lookup[normalized]
                return (key, "campaign_name") if key else (None, "ambiguous_name")
    return None, "unmatched"


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
    provider: str = "snapchat_ads",
) -> dict[str, Any]:
    provider_key = {
        "snapchat_ads": "snapchat",
        "meta_ads": "meta",
    }.get(str(provider or "").strip().lower())
    if provider_key is None:
        raise ValueError(f"unsupported_salla_marketing_provider:{provider}")
    settings = await _load_report_settings(db, str(user_id))
    query: dict[str, Any] = {
        "user_id": str(user_id),
        "order_date": {
            "$gte": (date_from - timedelta(days=1)).isoformat(),
            "$lte": (date_to + timedelta(days=1)).isoformat(),
        },
    }
    if settings.get("hide_inferred_date_orders"):
        query["order_date_inferred"] = {"$ne": True}
    orders = await _to_list(
        db.unified_orders.find(query, ORDER_PROJECTION),
        MAX_ORDER_ROWS,
    )
    if len(orders) > MAX_ORDER_ROWS:
        raise ValueError("Salla order audit exceeded the safe row limit")

    id_lookup = _unique_lookup(identities, "campaign_id")
    name_lookup = _unique_lookup(identities, "campaign_name")
    identity_by_key = {
        (_text(row.get("account_id")), _text(row.get("campaign_id"))): row
        for row in identities
    }
    zone = ZoneInfo(timezone_name)
    from_value = date_from.isoformat()
    to_value = date_to.isoformat()
    included_statuses = list(settings.get("report_included_statuses") or [])
    counters: Counter[str] = Counter()
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
        local_date, local_created_at, date_source = _localized_order_period_date(
            order,
            zone=zone,
        )
        salla_order_date = _text(order.get("order_date"))[:10]
        legacy_account_date = salla_order_date or local_date
        financial = _matches_any(order.get("order_status"), included_statuses)
        amount = _number(order.get("total_amount") or order.get("total"))
        source_platform = canonical_ad_platform(order)
        key, match_method = _match_order_campaign(
            order,
            id_lookup=id_lookup,
            name_lookup=name_lookup,
            provider_key=provider_key,
        )
        reported_snapchat_source = source_platform == provider_key
        snapchat_attributed = reported_snapchat_source or key is not None

        legacy_account_period_included = bool(
            legacy_account_date and from_value <= legacy_account_date <= to_value
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
                    counters[
                        "account_period_campaign_matched_orders_account_timezone"
                    ] += 1
                if financial:
                    counters[
                        "snapchat_attributed_financial_orders_account_timezone"
                    ] += 1
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
                    _add_order_to_campaign(
                        profitability_raw[key],
                        _order_cost_and_products(order, cost_context),
                    )
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
            account_period_campaign_matched_orders / snapchat_attributed_orders * 100,
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
            value["profitability"] = profitability
    return {
        "provider": str(provider),
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
                f"exact_campaign_id_or_unique_{provider_key}_campaign_name"
            ),
            "account_attribution_policy": (
                f"salla_reported_{provider_key}_source_or_exact_campaign_match"
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
