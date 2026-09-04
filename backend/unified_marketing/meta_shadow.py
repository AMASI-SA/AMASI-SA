"""Fail-closed native-vs-unified acceptance for persisted Meta evidence."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from unified_marketing.contract import CONTRACT_VERSION

LEVELS = ("account", "campaign", "ad_group", "ad")


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _amount(value: Any) -> float | None:
    value = _mapping(value).get("amount")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _unified_metrics(report: dict[str, Any]) -> dict[str, Any]:
    row = _mapping(report.get("totals"))
    delivery = _mapping(row.get("delivery"))
    platform = _mapping(row.get("platform_outcomes"))
    return {
        "spend_native": _amount(delivery.get("spend")),
        "spend_sar": _amount(delivery.get("spend_sar")),
        "impressions": delivery.get("impressions"),
        "clicks": delivery.get("clicks"),
        "purchases": platform.get("conversions"),
        "purchase_value_native": _amount(platform.get("revenue")),
    }


def evaluate_meta_unified_readiness(
    *,
    account_identity: dict[str, Any],
    reports: dict[str, dict[str, Any]],
    date_from: date,
    date_to: date,
    now: datetime | None = None,
    max_freshness_hours: float = 36.0,
) -> dict[str, Any]:
    """Compare raw persisted evidence embedded by the reader with its contract."""
    reasons: list[str] = []
    mismatches: list[dict[str, Any]] = []
    timezone_name = str(account_identity.get("timezone") or "")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    expected_dates = {
        (date_from + timedelta(days=offset)).isoformat()
        for offset in range((date_to - date_from).days + 1)
    }

    try:
        zone = ZoneInfo(timezone_name)
        period_closed = date_to < current.astimezone(zone).date()
        local_end = datetime.combine(date_to, time.max, tzinfo=zone).astimezone(
            timezone.utc
        )
    except Exception:
        period_closed = False
        local_end = None
        reasons.append("timezone_invalid")

    identity_ok = bool(account_identity.get("id"))
    hierarchy_by_level: dict[str, list[dict[str, Any]]] = {}
    contract_ok = True
    coverage_ok = True
    timezone_ok = True
    settings_ok = True
    for level in LEVELS:
        report = _mapping(reports.get(level))
        native = _mapping(report.get("native_evidence"))
        contract_ok = contract_ok and bool(
            report.get("contract_version") == CONTRACT_VERSION
            and report.get("provider") == "meta_ads"
            and report.get("entity_level") == level
        )
        timezone_ok = timezone_ok and bool(
            _mapping(report.get("account")).get("id") == account_identity.get("id")
            and _mapping(report.get("account")).get("currency")
            == account_identity.get("currency")
            and _mapping(report.get("period")).get("timezone") == timezone_name
            and native.get("timezone") == timezone_name
            and native.get("account_id") == account_identity.get("id")
        )
        observed = {str(item) for item in native.get("observed_dates") or []}
        declared_expected = {str(item) for item in native.get("expected_dates") or []}
        quality = _mapping(_mapping(report.get("totals")).get("quality"))
        level_coverage = bool(
            observed == expected_dates
            and declared_expected == expected_dates
            and quality.get("sync_status") == "complete"
            and quality.get("coverage_status") == "complete"
            and int(quality.get("source_fact_count") or 0) > 0
            and (level == "account" or bool(report.get("rows")))
        )
        coverage_ok = coverage_ok and level_coverage
        native_metrics = _mapping(native.get("metric_totals"))
        unified_metrics = _unified_metrics(report)
        if set(native_metrics) != set(unified_metrics):
            mismatches.append({"level": level, "kind": "metric_shape"})
        for metric, native_value in native_metrics.items():
            unified_value = unified_metrics.get(metric)
            if native_value != unified_value:
                mismatches.append(
                    {
                        "level": level,
                        "kind": "metric",
                        "field": metric,
                        "native": native_value,
                        "unified": unified_value,
                    }
                )
        native_hierarchy = sorted(
            list(native.get("hierarchy") or []), key=lambda item: item.get("id") or ""
        )
        unified_hierarchy = sorted(
            [
                {
                    "id": _mapping(row.get("entity")).get("id"),
                    "campaign_id": _mapping(row.get("entity")).get("campaign_id"),
                    "ad_group_id": _mapping(row.get("entity")).get("ad_group_id"),
                }
                for row in report.get("rows") or []
            ],
            key=lambda item: item.get("id") or "",
        )
        hierarchy_by_level[level] = unified_hierarchy
        if native_hierarchy != unified_hierarchy:
            mismatches.append({"level": level, "kind": "hierarchy"})
        if level != "account":
            settings_ok = (
                settings_ok
                and bool(report.get("management_context"))
                and all(
                    _mapping(value).get("settings_evidence_status") == "complete"
                    for value in _mapping(report.get("management_context")).values()
                )
            )

    campaign_ids = {item.get("id") for item in hierarchy_by_level.get("campaign", [])}
    ad_group_ids = {item.get("id") for item in hierarchy_by_level.get("ad_group", [])}
    hierarchy_ok = bool(campaign_ids and ad_group_ids and hierarchy_by_level.get("ad"))
    hierarchy_ok = (
        hierarchy_ok
        and all(
            item.get("campaign_id") in campaign_ids
            for item in hierarchy_by_level.get("ad_group", [])
        )
        and all(
            item.get("campaign_id") in campaign_ids
            and item.get("ad_group_id") in ad_group_ids
            for item in hierarchy_by_level.get("ad", [])
        )
    )
    if any(item.get("kind") == "hierarchy" for item in mismatches):
        hierarchy_ok = False

    account = _mapping(reports.get("account"))
    account_totals = _mapping(account.get("totals"))
    reconciliation_ok = all(
        _mapping(_mapping(reports.get(level)).get("totals"))
        .get("quality", {})
        .get("reconciliation_status")
        == "reconciled"
        for level in LEVELS
    )
    order_summary = _mapping(account.get("order_summary"))
    attribution_ok = bool(
        order_summary.get("status") == "complete"
        and order_summary.get("truncated") is False
        and int(order_summary.get("ambiguous_orders") or 0) == 0
        and order_summary.get("matched_financial_orders") is not None
        and str(order_summary.get("attribution_policy") or "")
        and order_summary.get("timezone") == timezone_name
        and _mapping(account_totals.get("commerce_outcomes")).get("status")
        == "complete"
        and all(
            _mapping(row.get("commerce_outcomes")).get("status") == "complete"
            and _mapping(row.get("commerce_outcomes")).get("attribution_scope")
            == "exact_campaign_match"
            for row in _mapping(reports.get("campaign")).get("rows") or []
        )
    )
    profitability = _mapping(account_totals.get("commerce_profitability"))
    profitability_ok = bool(
        profitability.get("status") == "complete"
        and int(profitability.get("missing_cost_orders") or 0) == 0
        and _amount(profitability.get("contribution_profit")) is not None
    )
    last_sync = _parse_datetime(account_identity.get("last_sync_at"))
    freshness_hours = (
        max(0.0, (current - last_sync).total_seconds() / 3600)
        if last_sync is not None
        else None
    )
    freshness_ok = bool(
        last_sync is not None
        and local_end is not None
        and last_sync >= local_end
        and freshness_hours is not None
        and freshness_hours <= max_freshness_hours
    )
    isolation_ok = all(
        _mapping(_mapping(reports.get(level)).get("decision_eligibility")).get(
            "eligible"
        )
        is False
        for level in LEVELS
    )

    checks = (
        (period_closed, "period_is_not_closed"),
        (identity_ok and contract_ok, "contract_or_identity_incomplete"),
        (coverage_ok, "daily_coverage_incomplete"),
        (hierarchy_ok, "hierarchy_incomplete"),
        (timezone_ok, "timezone_mismatch"),
        (freshness_ok, "freshness_incomplete"),
        (reconciliation_ok, "provider_reconciliation_incomplete"),
        (settings_ok, "settings_evidence_incomplete"),
        (attribution_ok, "salla_attribution_incomplete"),
        (profitability_ok, "profitability_incomplete"),
        (not mismatches, "shadow_mismatch"),
        (isolation_ok, "decision_isolation_guard_failed"),
    )
    reasons.extend(
        reason for passed, reason in checks if not passed and reason not in reasons
    )
    return {
        "provider": "meta_ads",
        "ready": not reasons,
        "reasons": reasons,
        "period": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "timezone": timezone_name,
            "closed": period_closed,
        },
        "freshness": {
            "last_sync_at": last_sync.isoformat() if last_sync else None,
            "freshness_hours": (
                round(freshness_hours, 3) if freshness_hours is not None else None
            ),
            "passed": freshness_ok,
        },
        "shadow_comparison": {
            "passed": not mismatches,
            "mismatches": mismatches,
            "native_source_only": True,
            "unified_gateway_only": True,
        },
        "checks": {
            "contract_identity": identity_ok and contract_ok,
            "daily_coverage": coverage_ok,
            "hierarchy": hierarchy_ok,
            "timezone": timezone_ok,
            "reconciliation": reconciliation_ok,
            "settings": settings_ok,
            "attribution": attribution_ok,
            "profitability": profitability_ok,
            "decision_isolation": isolation_ok,
        },
        "write_policy": {
            "provider_calls": 0,
            "provider_writes": 0,
            "database_writes": 0,
        },
    }


__all__ = ["evaluate_meta_unified_readiness"]
