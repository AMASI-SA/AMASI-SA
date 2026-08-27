"""Fail-closed readiness proof for the Snapchat Unified Marketing adapter.

This module deliberately reads Snapchat through ``unified_marketing.gateway``.
It never exposes a provider collection as the consumer contract and it never
enables Decision Intelligence or any provider/accounting write path.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .contract import CONTRACT_VERSION
from .gateway import (
    load_unified_marketing_account_identity,
    load_unified_marketing_account_report,
    load_unified_marketing_entity_readiness_evidence,
)

PROVIDER = "snapchat_ads"
ENTITY_LEVELS = ("campaign", "ad_group", "ad")
CONSUMABLE_METRICS = {
    "delivery": (
        "spend",
        "spend_sar",
        "impressions",
        "clicks",
        "views",
        "ctr_pct",
        "reach",
        "frequency",
        "video_completion",
    ),
    "platform_outcomes": (
        "conversions",
        "revenue",
        "roas",
        "view_content",
        "add_to_cart",
        "start_checkout",
        "add_billing",
    ),
    "commerce_outcomes": ("status", "orders", "revenue", "roas"),
    "quality": (
        "sync_status",
        "coverage_status",
        "source_fact_count",
        "amount_complete",
        "reconciliation_status",
    ),
}


def _quality(report: dict[str, Any]) -> dict[str, Any]:
    return dict(((report.get("totals") or {}).get("quality") or {}))


def _contract_valid(report: dict[str, Any], level: str) -> bool:
    return bool(
        report.get("contract_version") == CONTRACT_VERSION
        and report.get("provider") == PROVIDER
        and report.get("entity_level") == level
    )


def _level_summary(report: dict[str, Any], level: str) -> dict[str, Any]:
    if "complete" in report and "contract_valid" in report:
        return {
            "entity_level": level,
            "contract_valid": bool(report.get("contract_valid")),
            "complete": bool(report.get("complete")),
            "row_count": int(report.get("row_count") or 0),
            "source_fact_count": int(report.get("source_fact_count") or 0),
            "sync_status": report.get("sync_status"),
            "coverage_status": report.get("coverage_status"),
        }
    quality = _quality(report)
    contract_valid = _contract_valid(report, level)
    complete = bool(
        contract_valid
        and quality.get("sync_status") == "complete"
        and quality.get("coverage_status") == "complete"
    )
    return {
        "entity_level": level,
        "contract_valid": contract_valid,
        "complete": complete,
        "row_count": len(report.get("rows") or []),
        "source_fact_count": int(quality.get("source_fact_count") or 0),
        "sync_status": quality.get("sync_status"),
        "coverage_status": quality.get("coverage_status"),
    }


def evaluate_snapchat_unified_readiness(
    *,
    account_report: dict[str, Any],
    entity_reports: dict[str, dict[str, Any]],
    period_closed: bool,
) -> dict[str, Any]:
    """Evaluate an already-loaded proof without reaching provider storage."""
    account_quality = _quality(account_report)
    account_contract_valid = _contract_valid(account_report, "account")
    account_complete = bool(
        account_contract_valid
        and account_quality.get("sync_status") == "complete"
        and account_quality.get("coverage_status") == "complete"
        and account_quality.get("amount_complete") is True
    )
    reconciliation_complete = bool(
        account_quality.get("reconciliation_status") == "reconciled"
    )
    order_summary = dict(account_report.get("order_summary") or {})
    commerce_complete = bool(
        order_summary.get("status") == "complete"
        and not bool(order_summary.get("truncated"))
    )
    levels = {
        level: _level_summary(entity_reports.get(level) or {}, level)
        for level in ENTITY_LEVELS
    }
    hierarchy_complete = all(value["complete"] for value in levels.values())
    decision_disabled = bool(
        (account_report.get("decision_eligibility") or {}).get("eligible") is False
        and all(
            (report.get("decision_eligibility") or {}).get("eligible") is False
            for report in entity_reports.values()
        )
    )
    ready = bool(
        period_closed
        and account_complete
        and reconciliation_complete
        and hierarchy_complete
        and commerce_complete
        and decision_disabled
    )
    reasons: list[str] = []
    if not period_closed:
        reasons.append("period_is_not_closed")
    if not account_complete:
        reasons.append("account_contract_incomplete")
    if not reconciliation_complete:
        reasons.append("provider_reconciliation_incomplete")
    if not hierarchy_complete:
        reasons.append("entity_hierarchy_incomplete")
    if not commerce_complete:
        reasons.append("salla_comparison_incomplete")
    if not decision_disabled:
        reasons.append("decision_isolation_guard_failed")
    return {
        "ready": ready,
        "reasons": reasons,
        "account": {
            "contract_valid": account_contract_valid,
            "complete": account_complete,
            "sync_status": account_quality.get("sync_status"),
            "coverage_status": account_quality.get("coverage_status"),
            "amount_complete": account_quality.get("amount_complete"),
            "reconciliation_status": account_quality.get(
                "reconciliation_status"
            ),
            "source_fact_count": int(account_quality.get("source_fact_count") or 0),
        },
        "hierarchy": levels,
        "salla_comparison": {
            "complete": commerce_complete,
            "status": order_summary.get("status"),
            "truncated": bool(order_summary.get("truncated")),
        },
        "decision_isolation": {
            "passed": decision_disabled,
            "connected": False,
            "eligible": False,
        },
    }


async def build_snapchat_unified_readiness(
    db: Any,
    user_id: str,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build one auditable, read-only readiness response for Snapchat V2."""
    account = await load_unified_marketing_account_identity(
        db,
        str(user_id),
        provider=PROVIDER,
    )
    if not account:
        return {
            "provider": PROVIDER,
            "contract_version": CONTRACT_VERSION,
            "ready": False,
            "reasons": ["selected_account_missing"],
            "decision_isolation": {
                "passed": True,
                "connected": False,
                "eligible": False,
            },
        }
    timezone_name = str(account.get("timezone") or "").strip()
    try:
        zone = ZoneInfo(timezone_name)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("unified_marketing_account_timezone_invalid") from exc
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    account_today = current.astimezone(zone).date()
    last_closed_day = account_today - timedelta(days=1)
    start = date_from or last_closed_day
    end = date_to or last_closed_day
    if (date_from is None) != (date_to is None):
        raise ValueError("date_from_and_date_to_must_be_supplied_together")
    if end < start:
        raise ValueError("invalid_date_range")
    period_closed = end < account_today

    account_task = load_unified_marketing_account_report(
        db,
        str(user_id),
        provider=PROVIDER,
        date_from=start,
        date_to=end,
        timezone_name=timezone_name,
    )
    entity_tasks = [
        load_unified_marketing_entity_readiness_evidence(
            db,
            str(user_id),
            provider=PROVIDER,
            entity_level=level,
            date_from=start,
            date_to=end,
            timezone_name=timezone_name,
        )
        for level in ENTITY_LEVELS
    ]
    results = await asyncio.gather(
        account_task,
        *entity_tasks,
        return_exceptions=True,
    )
    entity_reports: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    account_result = results[0]
    if isinstance(account_result, BaseException):
        account_report: dict[str, Any] = {}
        errors["account"] = type(account_result).__name__
    else:
        account_report = account_result
    for level, result in zip(ENTITY_LEVELS, results[1:], strict=True):
        if isinstance(result, BaseException):
            entity_reports[level] = {}
            errors[level] = type(result).__name__
        else:
            entity_reports[level] = result

    proof = evaluate_snapchat_unified_readiness(
        account_report=account_report,
        entity_reports=entity_reports,
        period_closed=period_closed,
    )
    if errors:
        proof["ready"] = False
        proof["reasons"] = list(
            dict.fromkeys([*proof["reasons"], "readiness_evidence_load_failed"])
        )
    totals = dict(account_report.get("totals") or {})
    return {
        "provider": PROVIDER,
        "contract_version": CONTRACT_VERSION,
        "period": {
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "timezone": timezone_name,
            "closed": period_closed,
            "last_closed_account_day": last_closed_day.isoformat(),
        },
        **proof,
        "errors": errors,
        "consumable": {
            "gateway": "unified_marketing.gateway",
            "account": account,
            "metrics": {
                key: list(value) for key, value in CONSUMABLE_METRICS.items()
            },
            "account_totals": {
                "delivery": totals.get("delivery") or {},
                "platform_outcomes": totals.get("platform_outcomes") or {},
                "commerce_outcomes": totals.get("commerce_outcomes") or {},
                "quality": totals.get("quality") or {},
            },
            "entity_levels": ["account", *ENTITY_LEVELS],
            "daily_series_supported": [*ENTITY_LEVELS],
        },
        "source_contract_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
        "checked_at": current.astimezone(timezone.utc).isoformat(),
    }


__all__ = [
    "CONSUMABLE_METRICS",
    "ENTITY_LEVELS",
    "build_snapchat_unified_readiness",
    "evaluate_snapchat_unified_readiness",
]
