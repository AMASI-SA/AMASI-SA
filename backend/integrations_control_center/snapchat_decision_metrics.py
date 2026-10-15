"""Point-in-time evidence for governed Snapchat advertising decisions.

The snapshot is intentionally descriptive.  It records the exact Salla orders,
current Mezan product cost coverage and Snapchat spend that were available when
the decision was made.  Context such as paydays, seasons or trends belongs in
the decision ledger as separately verified evidence; it is never inferred here.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .snapchat_native_data_common import (
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
    SNAPCHAT_PERFORMANCE_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    _collection,
)
from .snapchat_account_hourly_refresh import (
    CAMPAIGN_FACTS_SCHEMA_VERSION as SCHEDULER_CAMPAIGN_FACTS_SCHEMA_VERSION,
    CAMPAIGN_FACTS_SOURCE_MODE as SCHEDULER_CAMPAIGN_FACTS_SOURCE_MODE,
)


DECISION_WINDOWS = (14, 7, 3, 2, 1)
SNAPSHOT_SOURCE_MODE = "snapchat_salla_decision_snapshot_v1"
SNAPCHAT_SYNC_RUN_COLLECTION = "mezan_integration_sync_runs_v2"


def _number(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if parsed == parsed and abs(parsed) != float("inf") else 0.0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _date(value: Any) -> str:
    return _text(value)[:10]


async def _rows(cursor: Any, limit: int = 100_000) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=limit)
    return [row async for row in cursor]


def _empty_bucket() -> dict[str, Any]:
    return {
        "orders": 0,
        "sales_sar": 0.0,
        "product_cost_sar": 0.0,
        "spend_sar": 0.0,
        "missing_cost_orders": 0,
        "products": {},
    }


def _merge_bucket(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in (
        "orders",
        "sales_sar",
        "product_cost_sar",
        "spend_sar",
        "missing_cost_orders",
    ):
        target[key] += _number(source.get(key))
    for identity, product in (source.get("products") or {}).items():
        current = target["products"].get(identity)
        if current is None:
            target["products"][identity] = dict(product)
            continue
        current["orders"] = int(current.get("orders") or 0) + int(
            product.get("orders") or 0
        )
        current["units"] = _number(current.get("units")) + _number(product.get("units"))
        current["sales_sar"] = _number(current.get("sales_sar")) + _number(
            product.get("sales_sar")
        )


def _empty_product_bucket(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "salla_product_id": _text(product.get("salla_product_id")) or None,
        "mezan_product_id": _text(product.get("mezan_product_id")) or None,
        "name": _text(product.get("name")) or "منتج بدون اسم",
        "sku": _text(product.get("sku")) or None,
        "orders": 0,
        "units": 0.0,
        "sales_sar": 0.0,
        "sources": {},
    }


def _add_product_line(
    bucket: dict[str, dict[str, Any]],
    line: dict[str, Any],
    *,
    source: str,
    count_order: bool,
) -> None:
    identity = _text(line.get("identity")) or _text(line.get("salla_product_id"))
    if not identity:
        return
    product = bucket.setdefault(identity, _empty_product_bucket(line))
    units = _number(line.get("units"))
    sales = _number(line.get("allocated_sales_sar"))
    product["orders"] += int(count_order)
    product["units"] += units
    product["sales_sar"] += sales
    source_key = _text(source) or "unknown"
    source_row = product["sources"].setdefault(
        source_key,
        {
            "source": source_key,
            "orders": 0,
            "units": 0.0,
            "sales_sar": 0.0,
            "source_verified_from_order": source_key != "unknown",
        },
    )
    source_row["orders"] += int(count_order)
    source_row["units"] += units
    source_row["sales_sar"] += sales


def _merge_products(
    target: dict[str, dict[str, Any]],
    source: dict[str, dict[str, Any]],
) -> None:
    for identity, raw in source.items():
        product = target.setdefault(identity, _empty_product_bucket(raw))
        product["orders"] += int(raw.get("orders") or 0)
        product["units"] += _number(raw.get("units"))
        product["sales_sar"] += _number(raw.get("sales_sar"))
        for source_name, raw_source in (raw.get("sources") or {}).items():
            current = product["sources"].setdefault(
                source_name,
                {
                    "source": source_name,
                    "orders": 0,
                    "units": 0.0,
                    "sales_sar": 0.0,
                    "source_verified_from_order": source_name != "unknown",
                },
            )
            current["orders"] += int(raw_source.get("orders") or 0)
            current["units"] += _number(raw_source.get("units"))
            current["sales_sar"] += _number(raw_source.get("sales_sar"))


def _product_sales_comparison(
    campaign_products: dict[str, dict[str, Any]],
    store_products: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for identity, campaign in campaign_products.items():
        total = store_products.get(identity) or {}
        campaign_units = round(_number(campaign.get("units")), 2)
        total_units = round(_number(total.get("units")), 2)
        campaign_sales = round(_number(campaign.get("sales_sar")), 2)
        total_sales = round(_number(total.get("sales_sar")), 2)
        share = (
            min(max(campaign_units / total_units * 100, 0.0), 100.0)
            if total_units > 0
            else None
        )
        non_attributed_share = (
            round(max(100.0 - share, 0.0), 2) if share is not None else None
        )
        sources = []
        for source_row in (total.get("sources") or {}).values():
            sources.append(
                {
                    **source_row,
                    "orders": int(source_row.get("orders") or 0),
                    "units": round(_number(source_row.get("units")), 2),
                    "sales_sar": round(_number(source_row.get("sales_sar")), 2),
                }
            )
        sources.sort(key=lambda row: (-_number(row.get("units")), row["source"]))
        source_units = {
            _text(row.get("source")): _number(row.get("units")) for row in sources
        }
        other_ad_platform_units = round(
            sum(
                units
                for source, units in source_units.items()
                if source in {"meta", "tiktok", "google"}
            ),
            2,
        )
        snapchat_source_units = round(source_units.get("snapchat", 0.0), 2)
        whatsapp_units = round(source_units.get("whatsapp", 0.0), 2)
        manual_entry_units = round(source_units.get("manual", 0.0), 2)
        unknown_or_non_ad_units = round(
            max(
                total_units - other_ad_platform_units - snapchat_source_units,
                0.0,
            ),
            2,
        )
        snapchat_unassigned_units = round(
            max(snapchat_source_units - campaign_units, 0.0), 2
        )
        unresolved_units = round(
            max(total_units - campaign_units - other_ad_platform_units, 0.0), 2
        )
        unresolved_share = (
            round(unresolved_units / total_units * 100, 2) if total_units > 0 else None
        )
        if unresolved_share is None:
            interpretation = "insufficient_product_sales_data"
        elif unresolved_share >= 50:
            interpretation = "large_unresolved_product_sales_share_requires_caution"
        elif unresolved_share >= 20:
            interpretation = "partial_unresolved_product_sales_requires_context"
        else:
            interpretation = "cross_platform_attribution_is_mostly_resolved"
        rows.append(
            {
                "identity": identity,
                "salla_product_id": campaign.get("salla_product_id"),
                "mezan_product_id": campaign.get("mezan_product_id"),
                "name": campaign.get("name"),
                "sku": campaign.get("sku"),
                "campaign_attributed_orders": int(campaign.get("orders") or 0),
                "campaign_attributed_units": campaign_units,
                "campaign_attributed_sales_sar": campaign_sales,
                "whole_store_product_orders": int(total.get("orders") or 0),
                "whole_store_product_units": total_units,
                "whole_store_product_sales_sar": total_sales,
                "units_not_attributed_to_campaign": round(
                    max(total_units - campaign_units, 0.0), 2
                ),
                "campaign_attributed_unit_share_pct": (
                    round(share, 2) if share is not None else None
                ),
                "not_attributed_unit_share_pct": non_attributed_share,
                "observed_order_sources": sources,
                "verified_other_ad_platform_units": other_ad_platform_units,
                "explicit_snapchat_source_units": snapchat_source_units,
                "snapchat_units_without_exact_campaign": snapchat_unassigned_units,
                "explicit_whatsapp_units": whatsapp_units,
                "salla_manual_entry_units": manual_entry_units,
                "manual_entry_note": (
                    "manual_entry_is_observed; whatsapp_origin_is_unverified"
                ),
                "unknown_or_non_ad_source_units": unknown_or_non_ad_units,
                "units_unresolved_for_snapchat_decision": unresolved_units,
                "unresolved_for_snapchat_decision_pct": unresolved_share,
                "cross_platform_interpretation": (
                    "verified_other_platform_units_are_excluded_from_snapchat_"
                    "impact; snapchat_without_campaign_and_unknown_sources_remain_"
                    "unresolved"
                ),
                "interpretation": interpretation,
                "causality_warning": (
                    "units_not_attributed_to_campaign_are_not_assumed_to_be_"
                    "campaign_conversions"
                ),
            }
        )
    rows.sort(
        key=lambda row: (
            -_number(row.get("whole_store_product_units")),
            _text(row.get("name")),
        )
    )
    return rows


def _metric(bucket: dict[str, Any], *, scope: str) -> dict[str, Any]:
    orders = int(bucket.get("orders") or 0)
    sales = round(_number(bucket.get("sales_sar")), 2)
    spend = round(_number(bucket.get("spend_sar")), 2)
    known_cost = round(_number(bucket.get("product_cost_sar")), 2)
    missing = int(bucket.get("missing_cost_orders") or 0)
    complete = missing == 0
    product_cost = known_cost if complete else None
    contribution = round(sales - known_cost - spend, 2) if complete else None
    return {
        "scope": scope,
        "orders": orders,
        "sales_sar": sales,
        "spend_sar": spend,
        "product_cost_sar": product_cost,
        "known_product_cost_sar": known_cost,
        "contribution_profit_sar": contribution,
        "profit_margin_pct": (
            round(contribution / sales * 100, 2)
            if contribution is not None and sales > 0
            else None
        ),
        "roas": round(sales / spend, 4) if spend > 0 else None,
        "cpa_sar": round(spend / orders, 2) if orders > 0 else None,
        "cost_complete": complete,
        "missing_cost_orders": missing,
        "profit_scope": (
            "sales_minus_product_cost_minus_selected_snapchat_spend_before_"
            "payment_shipping_bnpl_and_operating_allocations"
        ),
    }


def _store_metric(bucket: dict[str, Any]) -> dict[str, Any]:
    orders = int(bucket.get("orders") or 0)
    sales = round(_number(bucket.get("sales_sar")), 2)
    known_cost = round(_number(bucket.get("product_cost_sar")), 2)
    missing = int(bucket.get("missing_cost_orders") or 0)
    complete = missing == 0
    gross_profit = round(sales - known_cost, 2) if complete else None
    return {
        "scope": "whole_salla_store",
        "orders": orders,
        "sales_sar": sales,
        "product_cost_sar": known_cost if complete else None,
        "known_product_cost_sar": known_cost,
        "gross_profit_before_marketing_sar": gross_profit,
        "gross_margin_before_marketing_pct": (
            round(gross_profit / sales * 100, 2)
            if gross_profit is not None and sales > 0
            else None
        ),
        "cost_complete": complete,
        "missing_cost_orders": missing,
        "profit_scope": (
            "whole_store_sales_minus_product_cost_before_all_marketing_payment_"
            "shipping_bnpl_and_operating_allocations"
        ),
    }


def detect_recent_improvement(
    windows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Describe a nested-window improvement without claiming causality."""
    by_days = {int(row.get("days") or 0): row for row in windows}
    seven = (by_days.get(7) or {}).get("campaign") or {}
    recent = (by_days.get(3) or {}).get("campaign") or {}
    if not seven or not recent:
        return {
            "recent_improving": False,
            "comparison": "3d_daily_average_vs_7d_daily_average",
            "signals": [],
            "reason": "insufficient_campaign_window_data",
        }

    signals: list[dict[str, Any]] = []
    for key in ("orders", "sales_sar", "contribution_profit_sar"):
        old = seven.get(key)
        new = recent.get(key)
        if old is None or new is None:
            continue
        old_daily = _number(old) / 7
        new_daily = _number(new) / 3
        signals.append(
            {
                "metric": key,
                "seven_day_daily_average": round(old_daily, 4),
                "three_day_daily_average": round(new_daily, 4),
                "improved": new_daily > old_daily,
            }
        )
    for key in ("roas", "profit_margin_pct"):
        old = seven.get(key)
        new = recent.get(key)
        if old is None or new is None:
            continue
        signals.append(
            {
                "metric": key,
                "seven_day_value": old,
                "three_day_value": new,
                "improved": _number(new) > _number(old),
            }
        )
    comparable = [
        row
        for row in signals
        if row.get("metric")
        in {
            "orders",
            "sales_sar",
            "contribution_profit_sar",
            "roas",
        }
    ]
    improving_count = sum(bool(row.get("improved")) for row in comparable)
    return {
        "recent_improving": bool(comparable) and improving_count >= 2,
        "comparison": "3d_daily_average_vs_7d_daily_average",
        "signals": signals,
        "reason": "measured_nested_windows_only",
    }


async def _campaign_identity(
    db: Any,
    user_id: str,
    account_id: str,
    campaign_id: str | None,
) -> dict[str, Any] | None:
    if not campaign_id:
        return None
    return await _collection(db, SNAPCHAT_ENTITY_COLLECTION).find_one(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": account_id,
            "entity_type": "campaign",
            "external_id": campaign_id,
        },
        {"_id": 0, "external_id": 1, "display_name": 1},
    )


async def resolve_decision_campaign_id(
    db: Any,
    user_id: str,
    *,
    account_id: str,
    entity_type: str,
    entity_id: str | None,
    parent_id: str | None,
) -> str | None:
    if entity_type == "campaign":
        return entity_id
    if entity_type == "ad_squad":
        if parent_id:
            return parent_id
        lookup_id = entity_id
    elif entity_type == "ad":
        lookup_id = parent_id or entity_id
    else:
        return None
    if not lookup_id:
        return None
    row = await _collection(db, SNAPCHAT_ENTITY_COLLECTION).find_one(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": account_id,
            "external_id": lookup_id,
        },
        {"_id": 0, "campaign_id": 1},
    )
    return _text((row or {}).get("campaign_id")) or None


def _iso_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(_text(value))
    except ValueError:
        return None


def _last_completed_business_date(value: Any) -> date | None:
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(ZoneInfo("Asia/Riyadh")).date() - timedelta(days=1)


def _run_account_ids(summary: dict[str, Any]) -> set[str]:
    account_ids: set[str] = set()
    entity_counts = summary.get("entity_counts")
    if isinstance(entity_counts, dict):
        account_ids.update(
            _text(identity) for identity in entity_counts if _text(identity)
        )
    for row in summary.get("account_provider_calls") or []:
        if not isinstance(row, dict):
            continue
        identity = _text(row.get("ad_account_id"))
        if identity:
            account_ids.add(identity)
    return account_ids


async def _campaign_performance_sync_intervals(
    db: Any,
    user_id: str,
    *,
    account_id: str,
    relevant_date_from: str,
    relevant_date_to: str,
) -> dict[str, Any]:
    """Load durable intervals that can prove campaign-performance coverage.

    A missing performance row can mean either a real zero or a failed/absent
    provider refresh, so row count is deliberately not the completeness gate.
    Accepted evidence is either a full native campaign refresh or the scheduler
    v4 campaign-fact writer. Older scheduler modes only stored account totals
    and are intentionally rejected. Multiple complete intervals may jointly
    prove a window; no single 14-day run is required.
    """

    relevant_start = _iso_date(relevant_date_from)
    relevant_end = _iso_date(relevant_date_to)
    if relevant_start is None or relevant_end is None or relevant_start > relevant_end:
        return {
            "status": "invalid_required_date_range",
            "relevant_date_from": relevant_date_from,
            "relevant_date_to": relevant_date_to,
            "intervals": [],
        }

    cursor = _collection(db, SNAPCHAT_SYNC_RUN_COLLECTION).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "run_type": "analytics_refresh",
            "status": "complete",
            "summary.date_to": {"$gte": relevant_start.isoformat()},
            "summary.date_from": {"$lte": relevant_end.isoformat()},
        },
        {
            "_id": 0,
            "user_id": 1,
            "provider": 1,
            "run_type": 1,
            "status": 1,
            "source_mode": 1,
            "run_id": 1,
            "finished_at": 1,
            "summary": 1,
        },
    )
    rows = await _rows(cursor, limit=10_000)
    by_interval: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        if not (
            _text(row.get("user_id")) == user_id
            and _text(row.get("provider")) == SNAPCHAT_PROVIDER_ID
            and _text(row.get("run_type")) == "analytics_refresh"
            and _text(row.get("status")) == "complete"
        ):
            continue
        summary = row.get("summary")
        if not isinstance(summary, dict):
            continue
        run_source_mode = _text(row.get("source_mode"))
        scheduler_campaign_source = _text(summary.get("campaign_facts_source_mode"))
        scheduler_campaign_schema = int(
            _number(summary.get("campaign_facts_schema_version"))
        )
        if run_source_mode == SNAPCHAT_NATIVE_SYNC_SOURCE_MODE:
            proof_kind = "native_full_campaign_refresh"
            proof_source_mode = SNAPCHAT_NATIVE_SYNC_SOURCE_MODE
        elif (
            scheduler_campaign_source == SCHEDULER_CAMPAIGN_FACTS_SOURCE_MODE
            and scheduler_campaign_schema
            == SCHEDULER_CAMPAIGN_FACTS_SCHEMA_VERSION
        ):
            # The scheduler's top-level source describes its account refresh
            # and may be changed by freshness installers. Campaign proof lives
            # only in the independently versioned summary marker.
            proof_kind = "scheduler_campaign_facts"
            proof_source_mode = SCHEDULER_CAMPAIGN_FACTS_SOURCE_MODE
        else:
            continue
        covered_start = _iso_date(summary.get("date_from"))
        covered_end = _iso_date(summary.get("date_to"))
        last_completed_date = _last_completed_business_date(row.get("finished_at"))
        attempted = int(_number(summary.get("accounts_attempted")))
        completed = int(_number(summary.get("accounts_complete")))
        errors = int(_number(summary.get("errors_count")))
        account_in_run = account_id in _run_account_ids(summary)
        if not (
            covered_start is not None
            and covered_end is not None
            and last_completed_date is not None
            and covered_start <= covered_end
            and covered_start <= relevant_end
            and covered_end >= relevant_start
            and attempted > 0
            and completed == attempted
            and errors == 0
            and account_in_run
        ):
            continue
        # A successful current-day refresh still represents only the elapsed
        # hours. Never let its summary prove a complete business day until a
        # later run has crossed that Riyadh midnight.
        covered_end = min(covered_end, last_completed_date)
        if covered_start > covered_end or covered_end < relevant_start:
            continue
        interval = {
            "run_id": _text(row.get("run_id")) or None,
            "proof_kind": proof_kind,
            "source_mode": proof_source_mode,
            "run_source_mode": run_source_mode or None,
            "finished_at": row.get("finished_at"),
            "covered_date_from": max(covered_start, relevant_start).isoformat(),
            "covered_date_to": min(covered_end, relevant_end).isoformat(),
            "accounts_attempted": attempted,
            "accounts_complete": completed,
        }
        interval_key = (
            proof_source_mode,
            interval["covered_date_from"],
            interval["covered_date_to"],
        )
        previous = by_interval.get(interval_key)
        if previous is None or _text(interval.get("finished_at")) > _text(
            previous.get("finished_at")
        ):
            by_interval[interval_key] = interval
    intervals = sorted(
        by_interval.values(),
        key=lambda row: (
            row["covered_date_from"],
            row["covered_date_to"],
            row["source_mode"],
        ),
    )
    return {
        "status": "evidence_loaded",
        "relevant_date_from": relevant_start.isoformat(),
        "relevant_date_to": relevant_end.isoformat(),
        "account_id": account_id,
        "accepted_source_modes": [
            SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
            SCHEDULER_CAMPAIGN_FACTS_SOURCE_MODE,
        ],
        "intervals": intervals,
    }


def _window_performance_sync_coverage(
    evidence: dict[str, Any],
    *,
    required_date_from: str,
    required_date_to: str,
) -> dict[str, Any]:
    required_start = _iso_date(required_date_from)
    required_end = _iso_date(required_date_to)
    if required_start is None or required_end is None or required_start > required_end:
        return {
            "complete": False,
            "status": "invalid_required_date_range",
            "required_date_from": required_date_from,
            "required_date_to": required_date_to,
            "missing_dates": [],
            "proofs": [],
        }
    required_dates = {
        (required_start + timedelta(days=offset)).isoformat()
        for offset in range((required_end - required_start).days + 1)
    }
    covered_dates: set[str] = set()
    proofs: list[dict[str, Any]] = []
    for raw in evidence.get("intervals") or []:
        if not isinstance(raw, dict):
            continue
        interval_start = _iso_date(raw.get("covered_date_from"))
        interval_end = _iso_date(raw.get("covered_date_to"))
        if (
            interval_start is None
            or interval_end is None
            or interval_start > required_end
            or interval_end < required_start
        ):
            continue
        clipped_start = max(interval_start, required_start)
        clipped_end = min(interval_end, required_end)
        covered_dates.update(
            (clipped_start + timedelta(days=offset)).isoformat()
            for offset in range((clipped_end - clipped_start).days + 1)
        )
        proofs.append(
            {
                **raw,
                "covered_date_from": clipped_start.isoformat(),
                "covered_date_to": clipped_end.isoformat(),
            }
        )
    missing_dates = sorted(required_dates - covered_dates)
    return {
        "complete": not missing_dates,
        "status": (
            "verified_complete_campaign_performance_sync_union"
            if not missing_dates
            else "campaign_performance_sync_dates_missing"
        ),
        "required_date_from": required_start.isoformat(),
        "required_date_to": required_end.isoformat(),
        "covered_dates": sorted(required_dates & covered_dates),
        "missing_dates": missing_dates,
        "proof_source": "union_of_durable_complete_campaign_fact_runs",
        "proofs": proofs,
    }


async def campaign_performance_sync_coverage(
    db: Any,
    user_id: str,
    *,
    account_id: str,
    windows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Prove one or more campaign-fact windows from the durable sync ledger.

    Decision outcomes and business diagnostics must share this exact gate.  A
    missing campaign performance row can represent either a true zero or a
    failed/absent provider refresh, so callers may not infer completeness from
    row counts.
    """

    normalized: dict[str, tuple[str, str]] = {}
    for name, raw in (windows or {}).items():
        if not isinstance(raw, dict):
            continue
        date_from = _text(raw.get("date_from"))
        date_to = _text(raw.get("date_to"))
        start = _iso_date(date_from)
        end = _iso_date(date_to)
        if start is None or end is None or start > end:
            normalized[str(name)] = (date_from, date_to)
        else:
            normalized[str(name)] = (start.isoformat(), end.isoformat())

    valid_ranges = [
        (start, end)
        for start, end in normalized.values()
        if _iso_date(start) is not None
        and _iso_date(end) is not None
        and _iso_date(start) <= _iso_date(end)
    ]
    if not valid_ranges:
        return {
            "account_id": account_id,
            "complete": False,
            "windows": {
                name: _window_performance_sync_coverage(
                    {"intervals": []},
                    required_date_from=date_from,
                    required_date_to=date_to,
                )
                for name, (date_from, date_to) in normalized.items()
            },
            "status": "no_valid_required_windows",
        }

    relevant_date_from = min(start for start, _end in valid_ranges)
    relevant_date_to = max(end for _start, end in valid_ranges)
    evidence = await _campaign_performance_sync_intervals(
        db,
        user_id,
        account_id=account_id,
        relevant_date_from=relevant_date_from,
        relevant_date_to=relevant_date_to,
    )
    coverage_windows = {
        name: _window_performance_sync_coverage(
            evidence,
            required_date_from=date_from,
            required_date_to=date_to,
        )
        for name, (date_from, date_to) in normalized.items()
    }
    return {
        "account_id": account_id,
        "complete": bool(coverage_windows)
        and all(row.get("complete") is True for row in coverage_windows.values()),
        "windows": coverage_windows,
        "status": (
            "verified_complete_campaign_performance_sync_windows"
            if coverage_windows
            and all(row.get("complete") is True for row in coverage_windows.values())
            else "campaign_performance_sync_windows_incomplete"
        ),
        "accepted_source_modes": evidence.get("accepted_source_modes") or [],
    }


async def capture_decision_baseline(
    db: Any,
    user_id: str,
    *,
    account_id: str,
    campaign_id: str | None,
    ad_squad_id: str | None = None,
    ad_id: str | None = None,
    product_ids: list[str] | None = None,
    product_refs: list[dict[str, Any]] | None = None,
    management_proposal_id: str | None = None,
    account_timezone: str | None = None,
    captured_at: datetime | None = None,
    completed_days_only: bool = False,
) -> dict[str, Any]:
    """Capture immutable evidence windows used by a later decision record."""
    # Imports stay lazy so the write control plane remains testable without the
    # full Dashboard/product dependency stack.
    from dashboard_v2_routes import _filtered_orders
    from product_v2_routes import PRODUCTS
    from salla_marketing_attribution import (
        canonical_ad_platform,
        canonical_marketing_source,
    )
    from .snapchat_campaign_profitability import (
        _load_cost_context,
        _order_cost_and_products,
    )
    from .snapchat_campaign_result_source_routes import (
        _campaign_identities,
        _match_order_campaign,
        _unique_lookup,
    )
    from .campaign_product_associations import list_effective_campaign_products

    now = captured_at or datetime.now(timezone.utc)
    timezone_name = account_timezone or "Asia/Riyadh"
    try:
        local_tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone_name = "Asia/Riyadh"
        local_tz = ZoneInfo(timezone_name)
    local_now = now.astimezone(local_tz)
    end_date = local_now.date() - (
        timedelta(days=1) if completed_days_only else timedelta(0)
    )
    start_date = end_date - timedelta(
        days=max(DECISION_WINDOWS) - (1 if completed_days_only else 0)
    )
    date_from = start_date.isoformat()
    date_to = end_date.isoformat()

    requested_product_refs = [
        row for row in (product_refs or []) if isinstance(row, dict)
    ]
    explicit_product_ids = {
        _text(product_id) for product_id in (product_ids or []) if _text(product_id)
    } | {
        _text(row.get("product_id"))
        for row in requested_product_refs
        if _text(row.get("product_id"))
    }
    confirmed_product_links: list[dict[str, Any]] = []
    product_link_lookup_status = "not_requested"
    product_link_lookup_error: str | None = None
    if campaign_id or management_proposal_id:
        try:
            confirmed_product_links = await list_effective_campaign_products(
                db,
                user_id,
                provider=SNAPCHAT_PROVIDER_ID,
                account_id=account_id,
                campaign_id=campaign_id,
                ad_squad_id=ad_squad_id,
                ad_id=ad_id,
                management_proposal_id=(
                    management_proposal_id if not campaign_id else None
                ),
                as_of=now,
                include_unverified=False,
            )
            product_link_lookup_status = "verified"
        except Exception as exc:
            # A product-link read failure stays visible in coverage below.  It
            # does not turn an advertising performance snapshot into invented
            # product knowledge.
            confirmed_product_links = []
            product_link_lookup_status = "error"
            product_link_lookup_error = type(exc).__name__
    variants_by_product: dict[str, set[str]] = defaultdict(set)
    for product_ref in [*requested_product_refs, *confirmed_product_links]:
        product_identity = _text(product_ref.get("product_id"))
        variant_identity = _text(
            product_ref.get("product_variant_id") or product_ref.get("variant_id")
        )
        if product_identity and variant_identity:
            variants_by_product[product_identity].add(variant_identity)
    linked_product_ids = explicit_product_ids | {
        _text(row.get("product_id"))
        for row in confirmed_product_links
        if _text(row.get("product_id"))
    }

    performance_rows = await _rows(
        _collection(db, SNAPCHAT_PERFORMANCE_COLLECTION).find(
            {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "ad_account_id": account_id,
                "entity_type": "campaign",
                "date": {"$gte": date_from, "$lte": date_to},
            },
            {"_id": 0},
        )
    )
    # Load enough evidence for both the current and completed-day window sets.
    # When the current day is partial, the completed 14-day set starts one day
    # earlier than the visible current-day set.
    performance_relevant_date_from = (
        end_date - timedelta(days=max(DECISION_WINDOWS))
    ).isoformat()
    performance_sync_intervals = await _campaign_performance_sync_intervals(
        db,
        user_id,
        account_id=account_id,
        relevant_date_from=performance_relevant_date_from,
        relevant_date_to=date_to,
    )
    identities = await _campaign_identities(
        db,
        user_id,
        account_ids=[account_id],
        performance_rows=performance_rows,
    )
    identity = await _campaign_identity(db, user_id, account_id, campaign_id)
    if campaign_id and not any(
        _text(row.get("campaign_id")) == campaign_id for row in identities
    ):
        identities.append(
            {
                "account_id": account_id,
                "campaign_id": campaign_id,
                "campaign_name": _text((identity or {}).get("display_name"))
                or campaign_id,
            }
        )
    id_lookup = _unique_lookup(identities, "campaign_id")
    name_lookup = _unique_lookup(identities, "campaign_name")
    orders = await _filtered_orders(
        db,
        user_id,
        from_date=date_from,
        to_date=date_to,
        payment_methods=None,
        shipping_companies=None,
        include_marketing_attribution=True,
    )
    cost_context = await _load_cost_context(db, user_id)

    campaign_by_date: dict[str, dict[str, Any]] = defaultdict(_empty_bucket)
    account_by_date: dict[str, dict[str, Any]] = defaultdict(_empty_bucket)
    store_by_date: dict[str, dict[str, Any]] = defaultdict(_empty_bucket)
    store_products_by_date: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    matched = ambiguous = unattributed_snapchat = 0
    observed_campaign_product_ids: set[str] = set()
    for order in orders:
        order_date = _date(
            order.get("order_date") or order.get("created_at") or order.get("date")
        )
        if not order_date:
            continue
        result = _order_cost_and_products(order, cost_context)
        source = canonical_marketing_source(order) or "unknown"
        seen_store_products: set[str] = set()
        for line in result.get("lines") or []:
            identity_key = _text(line.get("identity")) or _text(
                line.get("salla_product_id")
            )
            _add_product_line(
                store_products_by_date[order_date],
                line,
                source=source,
                count_order=identity_key not in seen_store_products,
            )
            if identity_key:
                seen_store_products.add(identity_key)
        store = store_by_date[order_date]
        store["orders"] += 1
        store["sales_sar"] += _number(result.get("order_sales_sar"))
        store["product_cost_sar"] += _number(result.get("product_cost_sar"))
        store["missing_cost_orders"] += int(bool(result.get("missing_everywhere")))
        key, match_kind = _match_order_campaign(
            order, id_lookup=id_lookup, name_lookup=name_lookup
        )
        if key is None:
            if match_kind.startswith("ambiguous"):
                ambiguous += 1
            # Canonical source classification is already part of match_kind's
            # exact resolver; keep unclassified orders visible, not guessed.
            elif canonical_ad_platform(order) == "snapchat":
                unattributed_snapchat += 1
            continue
        if key[0] != account_id:
            continue
        matched += 1
        account = account_by_date[order_date]
        account["orders"] += 1
        account["sales_sar"] += _number(result.get("order_sales_sar"))
        account["product_cost_sar"] += _number(result.get("product_cost_sar"))
        account["missing_cost_orders"] += int(bool(result.get("missing_everywhere")))
        if campaign_id and key[1] == campaign_id:
            campaign = campaign_by_date[order_date]
            campaign["orders"] += 1
            campaign["sales_sar"] += _number(result.get("order_sales_sar"))
            campaign["product_cost_sar"] += _number(result.get("product_cost_sar"))
            campaign["missing_cost_orders"] += int(
                bool(result.get("missing_everywhere"))
            )
            seen_campaign_products: set[str] = set()
            for line in result.get("lines") or []:
                product_id = _text(line.get("salla_product_id"))
                if product_id:
                    observed_campaign_product_ids.add(product_id)
                identity_key = _text(line.get("identity")) or product_id
                if not identity_key:
                    continue
                _add_product_line(
                    campaign["products"],
                    line,
                    source="campaign_exact_attribution",
                    count_order=identity_key not in seen_campaign_products,
                )
                seen_campaign_products.add(identity_key)

    for row in performance_rows:
        row_date = _date(row.get("date"))
        spend = _number(row.get("spend_sar"))
        account_by_date[row_date]["spend_sar"] += spend
        if (
            campaign_id
            and _text(row.get("campaign_id") or row.get("external_id")) == campaign_id
        ):
            campaign_by_date[row_date]["spend_sar"] += spend

    inventory_rows: list[dict[str, Any]] = []
    catalog_lookup_ids = linked_product_ids | observed_campaign_product_ids
    if catalog_lookup_ids:
        cursor = db[PRODUCTS].find(
            {
                "user_id": user_id,
                "$or": [
                    {"salla_product_id": {"$in": sorted(catalog_lookup_ids)}},
                    {"mezan_product_id": {"$in": sorted(catalog_lookup_ids)}},
                ],
            },
            {
                "_id": 0,
                "salla_product_id": 1,
                "mezan_product_id": 1,
                "name": 1,
                "sku": 1,
                "status": 1,
                "archived": 1,
                "quantity": 1,
                "unlimited_quantity": 1,
                "variants": 1,
                "last_synced_at": 1,
                "details_synced_at": 1,
            },
        )
        inventory_rows = await _rows(cursor, limit=max(1, len(catalog_lookup_ids) * 2))
    resolved_link_ids: set[str] = set()
    linked_product_templates: dict[str, dict[str, Any]] = {}
    for row in inventory_rows:
        salla_id = _text(row.get("salla_product_id"))
        mezan_id = _text(row.get("mezan_product_id"))
        if salla_id in linked_product_ids or mezan_id in linked_product_ids:
            resolved_link_ids.update(
                identity
                for identity in (salla_id, mezan_id)
                if identity in linked_product_ids
            )
            product_identity = salla_id or mezan_id
            if product_identity:
                linked_product_templates[product_identity.casefold()] = {
                    "salla_product_id": salla_id or None,
                    "mezan_product_id": mezan_id or None,
                    "name": _text(row.get("name")) or "منتج مرتبط",
                    "sku": _text(row.get("sku")) or None,
                }
    for product_id in linked_product_ids - resolved_link_ids:
        linked_product_templates[product_id.casefold()] = {
            "salla_product_id": product_id,
            "mezan_product_id": None,
            "name": product_id,
            "sku": None,
        }
    missing_linked_product_ids = sorted(linked_product_ids - resolved_link_ids)
    inventory = []
    for row in inventory_rows:
        variants = [
            item for item in (row.get("variants") or []) if isinstance(item, dict)
        ]
        variant_quantity = sum(
            _number(
                item.get("quantity")
                if item.get("quantity") is not None
                else item.get("stock_quantity")
            )
            for item in variants
            if item.get("quantity") is not None
            or item.get("stock_quantity") is not None
        )
        variants_unlimited = any(
            item.get("unlimited_quantity") is True or item.get("is_infinite") is True
            for item in variants
        )
        unlimited = row.get("unlimited_quantity") is True or variants_unlimited
        product_quantity = row.get("quantity")
        quantity = product_quantity
        if quantity is None and variants:
            quantity = variant_quantity
        status_value = _text(row.get("status")) or "unknown"
        archived = row.get("archived") is True
        in_decision_product_scope = bool(
            {
                _text(row.get("salla_product_id")),
                _text(row.get("mezan_product_id")),
            }
            & linked_product_ids
        )
        product_aliases = (
            _text(row.get("salla_product_id")),
            _text(row.get("mezan_product_id")),
        )
        requested_variant_ids = sorted(
            {
                variant_id
                for alias in product_aliases
                for variant_id in variants_by_product.get(alias, set())
            }
        )
        last_synced_at = row.get("last_synced_at")
        details_synced_at = row.get("details_synced_at")
        inventory_uses_variant_details = bool(requested_variant_ids) or bool(
            product_quantity is None and variants
        )
        inventory_synced_at = (
            details_synced_at if inventory_uses_variant_details else last_synced_at
        )
        inventory_fresh = False
        inventory_observed_after_capture = False
        try:
            synced = datetime.fromisoformat(
                str(inventory_synced_at or "").replace("Z", "+00:00")
            )
            if synced.tzinfo is None:
                synced = synced.replace(tzinfo=timezone.utc)
            inventory_age = now.astimezone(timezone.utc) - synced.astimezone(
                timezone.utc
            )
            inventory_observed_after_capture = inventory_age < timedelta(0)
            inventory_fresh = (
                not inventory_observed_after_capture
                and inventory_age <= timedelta(hours=24)
            )
        except (TypeError, ValueError):
            inventory_fresh = False
        common_inventory = {
            "salla_product_id": _text(row.get("salla_product_id")),
            "mezan_product_id": _text(row.get("mezan_product_id")) or None,
            "name": _text(row.get("name")) or "منتج بدون اسم",
            "sku": _text(row.get("sku")) or None,
            "status": status_value,
            "archived": archived,
            "variants_unlimited_quantity": variants_unlimited,
            "in_decision_product_scope": in_decision_product_scope,
            "last_synced_at": last_synced_at,
            "details_synced_at": details_synced_at,
            "inventory_synced_at": inventory_synced_at,
            "inventory_freshness_source": (
                "details_synced_at"
                if inventory_uses_variant_details
                else "last_synced_at"
            ),
            "freshness_status": (
                "observed_after_capture"
                if inventory_observed_after_capture
                else "fresh" if inventory_fresh else "stale_or_unknown"
            ),
            "observed_after_capture": inventory_observed_after_capture,
        }
        status_blocked = archived or status_value in {
            "out",
            "out_of_stock",
            "inactive",
            "hidden",
            "archived",
        }
        if requested_variant_ids:
            variants_by_id = {
                _text(item.get("id") or item.get("variant_id")): item
                for item in variants
                if _text(item.get("id") or item.get("variant_id"))
            }
            for requested_variant_id in requested_variant_ids:
                selected_variant = variants_by_id.get(requested_variant_id)
                variant_found = selected_variant is not None
                variant_quantity_value = None
                variant_unlimited = False
                if selected_variant is not None:
                    variant_quantity_value = selected_variant.get("quantity")
                    if variant_quantity_value is None:
                        variant_quantity_value = selected_variant.get("stock_quantity")
                    variant_unlimited = bool(
                        selected_variant.get("unlimited_quantity") is True
                        or selected_variant.get("is_infinite") is True
                    )
                inventory.append(
                    {
                        **common_inventory,
                        "quantity": variant_quantity_value,
                        "unlimited_quantity": variant_unlimited,
                        "product_variant_id": requested_variant_id,
                        "product_variant_ids": [requested_variant_id],
                        "variant_found": variant_found,
                        "delivery_blocked": status_blocked
                        or not variant_found
                        or not inventory_fresh
                        or (variant_quantity_value is None and not variant_unlimited)
                        or (
                            variant_quantity_value is not None
                            and _number(variant_quantity_value) <= 0
                            and not variant_unlimited
                        ),
                    }
                )
        else:
            inventory.append(
                {
                    **common_inventory,
                    "quantity": quantity,
                    "unlimited_quantity": unlimited,
                    "product_variant_id": None,
                    "product_variant_ids": [],
                    "variant_found": True,
                    "delivery_blocked": status_blocked
                    or not inventory_fresh
                    or (quantity is None and not unlimited)
                    or (
                        quantity is not None
                        and _number(quantity) <= 0
                        and not unlimited
                    ),
                }
            )

    def build_windows(
        window_end_date: Any, *, includes_partial_current_day: bool
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for days in DECISION_WINDOWS:
            window_start = window_end_date - timedelta(days=days - 1)
            campaign_bucket = _empty_bucket()
            account_bucket = _empty_bucket()
            store_bucket = _empty_bucket()
            store_products: dict[str, dict[str, Any]] = {}
            for day_offset in range(days):
                key = (window_start + timedelta(days=day_offset)).isoformat()
                _merge_bucket(campaign_bucket, campaign_by_date.get(key, {}))
                _merge_bucket(account_bucket, account_by_date.get(key, {}))
                _merge_bucket(store_bucket, store_by_date.get(key, {}))
                _merge_products(store_products, store_products_by_date.get(key, {}))
            for identity_key, product in linked_product_templates.items():
                campaign_bucket["products"].setdefault(
                    identity_key,
                    _empty_product_bucket(product),
                )
            product_comparison = _product_sales_comparison(
                campaign_bucket.get("products") or {}, store_products
            )
            output.append(
                {
                    "days": days,
                    "date_from": window_start.isoformat(),
                    "date_to": window_end_date.isoformat(),
                    "includes_partial_current_day": includes_partial_current_day,
                    "coverage": _window_performance_sync_coverage(
                        performance_sync_intervals,
                        required_date_from=window_start.isoformat(),
                        required_date_to=window_end_date.isoformat(),
                    ),
                    "campaign": _metric(campaign_bucket, scope="campaign"),
                    "account": _metric(
                        account_bucket, scope="selected_snapchat_account"
                    ),
                    "store": _store_metric(store_bucket),
                    "product_sales_comparison": product_comparison,
                    "attribution_caution": any(
                        row.get("interpretation")
                        == "large_unresolved_product_sales_share_requires_caution"
                        for row in product_comparison
                    ),
                }
            )
        return output

    windows = build_windows(
        end_date, includes_partial_current_day=not completed_days_only
    )
    completed_windows = build_windows(
        end_date if completed_days_only else end_date - timedelta(days=1),
        includes_partial_current_day=False,
    )
    global_performance_coverage = next(
        (
            deepcopy(row.get("coverage") or {})
            for row in completed_windows
            if int(row.get("days") or 0) == max(DECISION_WINDOWS)
        ),
        {
            "complete": False,
            "status": "completed_14d_window_missing",
            "missing_dates": [],
            "proofs": [],
        },
    )

    inventory_delivery_blocked = (
        bool(missing_linked_product_ids)
        or product_link_lookup_status == "error"
        or any(
            row.get("in_decision_product_scope") is True
            and row.get("delivery_blocked") is True
            for row in inventory
        )
    )
    inventory_verification_status = (
        "incomplete"
        if missing_linked_product_ids
        or product_link_lookup_status == "error"
        or any(
            row.get("in_decision_product_scope") is True
            and (
                row.get("freshness_status") != "fresh"
                or row.get("archived") is True
                or row.get("variant_found") is False
                or row.get("delivery_blocked") is True
                or (
                    row.get("quantity") is None
                    and row.get("unlimited_quantity") is not True
                )
            )
            for row in inventory
        )
        else "verified" if linked_product_ids else "not_linked"
    )

    return {
        "source_mode": SNAPSHOT_SOURCE_MODE,
        "captured_at": now.astimezone(timezone.utc).isoformat(),
        "as_of_date": end_date.isoformat(),
        "account_timezone": timezone_name,
        "account_id": account_id,
        "campaign_id": campaign_id,
        "campaign_name": _text((identity or {}).get("display_name")) or None,
        "windows": windows,
        "completed_windows": completed_windows,
        "recent_trend": detect_recent_improvement(windows),
        "inventory": inventory,
        "inventory_delivery_blocked": inventory_delivery_blocked,
        "inventory_verification_status": inventory_verification_status,
        "confirmed_product_links": confirmed_product_links,
        "product_link_lookup_status": product_link_lookup_status,
        "product_link_lookup_error": product_link_lookup_error,
        "explicit_product_ids": sorted(explicit_product_ids),
        "linked_product_ids": sorted(linked_product_ids),
        "missing_linked_product_ids": missing_linked_product_ids,
        "coverage": {
            "complete": global_performance_coverage.get("complete") is True,
            "incomplete_reasons": (
                []
                if global_performance_coverage.get("complete") is True
                else ["snapchat_campaign_performance_sync_range_unverified"]
            ),
            "eligible_salla_orders": len(orders),
            "exact_matched_account_orders": matched,
            "ambiguous_orders": ambiguous,
            "unclassified_orders": unattributed_snapchat,
            "campaign_result_source": "salla_exact_campaign_match",
            "product_scope_source": (
                "explicit_management_or_verified_association"
                if linked_product_ids
                else "exact_campaign_orders_only"
            ),
            "product_link_means_intended_product_not_sales_attribution": True,
            "whole_store_product_sales_source": (
                "same_financially_included_salla_orders_all_marketing_sources"
            ),
            "order_source_policy": (
                "whatsapp_snapchat_or_other_source_only_when_explicit_in_salla"
            ),
            "spend_source": "snapchat_native_daily_facts",
            "campaign_performance_rows_observed": len(performance_rows),
            "campaign_performance_sync": global_performance_coverage,
            "campaign_performance_sync_intervals": performance_sync_intervals,
            "current_day_partial": True,
            "profit_excludes": [
                "payment_gateway_fees",
                "bnpl_fees",
                "merchant_shipping_cost",
                "operating_expenses",
                "non_snapchat_marketing_spend",
            ],
        },
        "primary_basis": [
            "measured_results",
            *(
                ["verified_inventory"]
                if inventory_verification_status == "verified"
                else []
            ),
        ],
        "context_policy": (
            "external_context_is_supporting_only_until_independently_verified"
        ),
    }


def unavailable_decision_baseline(
    *,
    account_id: str,
    campaign_id: str | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "source_mode": SNAPSHOT_SOURCE_MODE,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "account_id": account_id,
        "campaign_id": campaign_id,
        "windows": [],
        "recent_trend": {
            "recent_improving": False,
            "reason": "baseline_unavailable",
        },
        "inventory": [],
        "inventory_delivery_blocked": False,
        "coverage": {
            "complete": False,
            "reason": _text(reason)[:240] or "baseline_unavailable",
        },
        "primary_basis": ["provider_before_snapshot"],
        "context_policy": (
            "external_context_is_supporting_only_until_independently_verified"
        ),
    }


__all__ = [
    "DECISION_WINDOWS",
    "SNAPSHOT_SOURCE_MODE",
    "campaign_performance_sync_coverage",
    "capture_decision_baseline",
    "detect_recent_improvement",
    "resolve_decision_campaign_id",
    "unavailable_decision_baseline",
]
