"""Fail-closed selection policy for the Snapchat V2 spend headline.

Hourly facts remain the source for the hourly breakdown.  For the currently
open account-local day, an independently fetched provider total may be newer
than those facts.  This module selects that total only when its reconciliation
proof belongs to the same projection run and was observed after the projection
was generated.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

NATIVE_TOLERANCE = 0.01


def _utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hourly_breakdown_status(projection: dict[str, Any]) -> str:
    statuses = {
        str(row.get("status") or "")
        for row in list(projection.get("hours") or [])
        if isinstance(row, dict)
    }
    if "unknown_incomplete" in statuses or "provisional_unavailable" in statuses:
        return "incomplete"
    if "provisional" in statuses:
        return "provisional"
    return "complete" if projection.get("amount_complete") is True else "incomplete"


def _provider_coverage_is_trusted(coverage: dict[str, Any]) -> bool:
    if coverage.get("status") != "complete":
        return False
    if coverage.get("data_state") not in {"confirmed_data", "confirmed_zero"}:
        return False
    granularity = str(coverage.get("provider_granularity") or "").upper()
    if granularity == "TOTAL":
        return True
    # Snapchat rejects TOTAL for some account/API combinations.  The provider
    # reader then performs a fresh independent HOUR request for the same exact
    # window; accept only that explicit, tested fallback.
    return (
        granularity == "HOUR"
        and coverage.get("fallback_from") == "snapchat_provider_http_400"
    )


def resolve_open_day_headline_spend(
    *,
    projection: dict[str, Any],
    reconciliation: dict[str, Any] | None,
    open_day: bool,
    provider_scope: str = "account",
) -> dict[str, Any]:
    """Return headline and hourly amounts without inventing hourly facts."""
    hourly_sum = round(float(projection.get("base_spend_native") or 0), 6)
    breakdown_status = _hourly_breakdown_status(projection)
    fallback = {
        "headline_spend_native": hourly_sum,
        "hourly_spend_native": hourly_sum,
        "unallocated_spend_native": 0.0,
        "headline_spend_source": "hourly_facts",
        "hourly_breakdown_status": breakdown_status,
        "hourly_breakdown_complete": breakdown_status == "complete",
        "provider_total_checked_at": None,
        "provider_total_coverage": None,
    }
    if not open_day or not isinstance(reconciliation, dict):
        return fallback

    dashboard_scope = provider_scope == "dashboard"
    coverage_key = (
        "dashboard_provider_coverage" if dashboard_scope else "provider_coverage"
    )
    total_key = (
        "provider_riyadh_window_spend_native"
        if dashboard_scope
        else "provider_spend_native"
    )
    coverage = dict(reconciliation.get(coverage_key) or {})
    if not _provider_coverage_is_trusted(coverage):
        return fallback
    if str(reconciliation.get("report_date") or "") != str(
        projection.get("report_date") or ""
    ):
        return fallback
    if str(reconciliation.get("action_report_time") or "") != str(
        projection.get("action_report_time") or ""
    ):
        return fallback
    expected_timezone = str(projection.get("projection_timezone") or "")
    proof_timezone = str(
        reconciliation.get(
            "dashboard_timezone" if dashboard_scope else "account_timezone"
        )
        or ""
    )
    if not expected_timezone or proof_timezone != expected_timezone:
        return fallback
    if not dashboard_scope and expected_timezone != str(
        projection.get("account_timezone") or ""
    ):
        return fallback

    reconciliation_run_id = str(reconciliation.get("sync_run_id") or "")
    if not reconciliation_run_id:
        return fallback
    projection_run_ids = {
        str(value)
        for value in list(projection.get("source_sync_run_ids") or [])
        if value
    }
    projection_sync_run_id = str(projection.get("sync_run_id") or "")
    if projection_sync_run_id and projection_sync_run_id != reconciliation_run_id:
        return fallback
    if projection_run_ids and projection_run_ids != {reconciliation_run_id}:
        return fallback
    if not projection_sync_run_id and not projection_run_ids:
        return fallback

    generated_at = _utc_datetime(projection.get("generated_at"))
    checked_at = _utc_datetime(reconciliation.get("checked_at"))
    if generated_at is None or checked_at is None or checked_at < generated_at:
        return fallback

    try:
        provider_total = float(reconciliation.get(total_key))
    except (TypeError, ValueError, OverflowError):
        return fallback
    if not math.isfinite(provider_total) or provider_total < 0:
        return fallback

    provider_total = round(provider_total, 6)
    unallocated = round(provider_total - hourly_sum, 6)
    breakdown_complete = (
        breakdown_status == "complete" and abs(unallocated) < NATIVE_TOLERANCE
    )
    resolved_breakdown_status = (
        "complete"
        if breakdown_complete
        else "incomplete" if breakdown_status == "complete" else breakdown_status
    )
    return {
        "headline_spend_native": provider_total,
        "hourly_spend_native": hourly_sum,
        "unallocated_spend_native": unallocated,
        "headline_spend_source": "provider_total",
        "hourly_breakdown_status": resolved_breakdown_status,
        "hourly_breakdown_complete": breakdown_complete,
        "provider_total_checked_at": reconciliation.get("checked_at"),
        "provider_total_coverage": coverage,
    }


def resolve_report_headline_spend(
    *,
    projections: list[dict[str, Any]],
    reconciliations: list[dict[str, Any]],
    open_report_date: str,
    provider_scope: str = "account",
) -> dict[str, Any]:
    """Aggregate closed hourly days plus a proven open-day provider total."""
    reconciliation_by_date = {
        str(row.get("report_date") or ""): row
        for row in reconciliations
        if isinstance(row, dict)
    }
    days = [
        resolve_open_day_headline_spend(
            projection=projection,
            reconciliation=reconciliation_by_date.get(
                str(projection.get("report_date") or "")
            ),
            open_day=(str(projection.get("report_date") or "") == open_report_date),
            provider_scope=provider_scope,
        )
        for projection in projections
    ]
    headline = round(
        sum(float(row["headline_spend_native"]) for row in days),
        6,
    )
    hourly = round(sum(float(row["hourly_spend_native"]) for row in days), 6)
    unallocated = round(headline - hourly, 6)
    sources = {str(row["headline_spend_source"]) for row in days}
    source = (
        next(iter(sources))
        if len(sources) == 1
        else "mixed" if sources else "hourly_facts"
    )
    statuses = {str(row["hourly_breakdown_status"]) for row in days}
    breakdown_status = (
        "incomplete"
        if not days or "incomplete" in statuses
        else "provisional" if "provisional" in statuses else "complete"
    )
    checked = [
        row["provider_total_checked_at"]
        for row in days
        if row.get("provider_total_checked_at") is not None
    ]
    return {
        "headline_spend_native": headline,
        "hourly_spend_native": hourly,
        "unallocated_spend_native": unallocated,
        "headline_spend_source": source,
        "hourly_breakdown_status": breakdown_status,
        "hourly_breakdown_complete": (
            breakdown_status == "complete" and abs(unallocated) < NATIVE_TOLERANCE
        ),
        "provider_total_checked_at": checked[-1] if checked else None,
        "days": days,
    }


__all__ = [
    "NATIVE_TOLERANCE",
    "resolve_open_day_headline_spend",
    "resolve_report_headline_spend",
]
