"""Provider-neutral evidence adapter for Decision Intelligence Phase 5.

All marketing reads cross :mod:`unified_marketing.gateway`.  This module does
not know provider storage names, cannot mutate a provider, and deliberately
fails closed when the Unified Marketing contract is not decision-grade.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import unified_marketing.gateway as unified_gateway
from unified_marketing.meta_shadow import evaluate_meta_unified_readiness
from unified_marketing.contract import CONTRACT_VERSION

ENTITY_LEVELS = ("campaign", "ad_group", "ad")
REQUIRED_DECISION_GATES = (
    "contract",
    "period_closed",
    "coverage",
    "reconciliation",
    "freshness",
    "attribution",
    "financial_coverage",
)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _money_amount(value: Any) -> float | None:
    amount = _mapping(value).get("amount")
    if amount is None or isinstance(amount, bool):
        return None
    try:
        parsed = float(amount)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
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
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _gate(passed: bool, reason: str, **details: Any) -> dict[str, Any]:
    return {"passed": bool(passed), "reason": reason, **details}


def _report_quality(report: dict[str, Any]) -> dict[str, Any]:
    return _mapping(_mapping(report.get("totals")).get("quality"))


def _report_contract_valid(
    report: dict[str, Any],
    *,
    provider: str,
    entity_level: str,
    date_from: date,
    date_to: date,
    timezone_name: str,
) -> bool:
    period = _mapping(report.get("period"))
    return bool(
        report.get("contract_version") == CONTRACT_VERSION
        and str(report.get("provider") or "") == provider
        and str(report.get("entity_level") or "") == entity_level
        and str(period.get("date_from") or "") == date_from.isoformat()
        and str(period.get("date_to") or "") == date_to.isoformat()
        and str(period.get("timezone") or "") == timezone_name
        and str(period.get("action_report_time") or "") == "conversion"
    )


def _report_coverage_complete(report: dict[str, Any]) -> bool:
    quality = _report_quality(report)
    return bool(
        quality.get("sync_status") == "complete"
        and quality.get("coverage_status") == "complete"
        and int(quality.get("source_fact_count") or 0) > 0
    )


def _row_coverage_complete(row: dict[str, Any]) -> bool:
    quality = _mapping(row.get("quality"))
    return bool(
        quality.get("sync_status") == "complete"
        and quality.get("coverage_status") == "complete"
        and int(quality.get("source_fact_count") or 0) > 0
    )


def _row_attribution_complete(row: dict[str, Any]) -> bool:
    commerce = _mapping(row.get("commerce_outcomes"))
    return bool(
        commerce.get("status") == "complete"
        and str(commerce.get("attribution_scope") or "").strip()
        and _number(commerce.get("orders")) is not None
        and _money_amount(commerce.get("revenue")) is not None
    )


def _row_financial_complete(row: dict[str, Any]) -> bool:
    quality = _mapping(row.get("quality"))
    delivery = _mapping(row.get("delivery"))
    profitability = _mapping(row.get("commerce_profitability"))
    return bool(
        quality.get("amount_complete") is True
        and _money_amount(delivery.get("spend")) is not None
        and _money_amount(delivery.get("spend_sar")) is not None
        and profitability.get("status") == "complete"
        and int(profitability.get("missing_cost_orders") or 0) == 0
        and _money_amount(profitability.get("contribution_profit")) is not None
    )


def _candidate_evidence(
    row: dict[str, Any],
    *,
    global_blockers: list[str],
) -> dict[str, Any]:
    entity = _mapping(row.get("entity"))
    delivery = _mapping(row.get("delivery"))
    platform = _mapping(row.get("platform_outcomes"))
    commerce = _mapping(row.get("commerce_outcomes"))
    profitability = _mapping(row.get("commerce_profitability"))
    blockers = list(global_blockers)
    if not _row_coverage_complete(row):
        blockers.append("coverage")
    if not _row_attribution_complete(row):
        blockers.append("attribution")
    if not _row_financial_complete(row):
        blockers.append("financial_coverage")
    blockers = list(dict.fromkeys(blockers))
    return {
        "evidence_id": f"{row.get('provider')}:{entity.get('level')}:{entity.get('id')}",
        "provider": row.get("provider"),
        "entity": {
            "level": entity.get("level"),
            "id": entity.get("id"),
            "name": entity.get("name"),
            "status": entity.get("status"),
            "active": entity.get("active"),
        },
        "metrics": {
            "spend_sar": _money_amount(delivery.get("spend_sar")),
            "impressions": delivery.get("impressions"),
            "clicks": delivery.get("clicks"),
            "conversions": platform.get("conversions"),
            "platform_revenue": _money_amount(platform.get("revenue")),
            "platform_roas": platform.get("roas"),
            "salla_orders": commerce.get("orders"),
            "salla_revenue_sar": _money_amount(commerce.get("revenue")),
            "salla_roas": commerce.get("roas"),
            "contribution_profit_sar": _money_amount(
                profitability.get("contribution_profit")
            ),
            "profit_margin_pct": profitability.get("profit_margin_pct"),
        },
        "quality": _mapping(row.get("quality")),
        "lineage": _mapping(row.get("lineage")),
        "decision_eligible": not blockers,
        "blocked_by": blockers,
    }


def evaluate_decision_evidence(
    *,
    account_identity: dict[str, Any],
    reports: dict[str, dict[str, Any]],
    provider: str,
    date_from: date,
    date_to: date,
    now: datetime | None = None,
    max_freshness_hours: float = 36.0,
    loader_errors: dict[str, str] | None = None,
    shadow_acceptance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate gateway reports and create a fail-closed evidence bundle."""
    timezone_name = str(account_identity.get("timezone") or "").strip()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    errors = dict(loader_errors or {})

    try:
        account_today = current.astimezone(ZoneInfo(timezone_name)).date()
    except Exception:  # noqa: BLE001 - invalid timezone is an explicit gate failure
        account_today = current.date()
        errors.setdefault("account_timezone", "invalid_timezone")
    period_closed = date_to < account_today

    expected_levels = {"account": reports.get("account") or {}}
    expected_levels.update({level: reports.get(level) or {} for level in ENTITY_LEVELS})
    contract_results = {
        level: _report_contract_valid(
            report,
            provider=provider,
            entity_level=level,
            date_from=date_from,
            date_to=date_to,
            timezone_name=timezone_name,
        )
        for level, report in expected_levels.items()
    }
    contract_passed = not errors and all(contract_results.values())

    coverage_results = {
        level: bool(
            _report_coverage_complete(report)
            and (level == "account" or bool(report.get("rows")))
        )
        for level, report in expected_levels.items()
    }
    coverage_passed = not errors and all(coverage_results.values())

    account_report = expected_levels["account"]
    account_totals = _mapping(account_report.get("totals"))
    account_quality = _mapping(account_totals.get("quality"))
    reconciliation_passed = bool(
        account_quality.get("reconciliation_status") == "reconciled"
    )

    last_sync_at = _parse_datetime(account_identity.get("last_sync_at"))
    freshness_hours = (
        max(0.0, (current - last_sync_at).total_seconds() / 3600.0)
        if last_sync_at is not None
        else None
    )
    local_period_end = (
        datetime.combine(
            date_to,
            time.max,
            tzinfo=ZoneInfo(timezone_name),
        ).astimezone(timezone.utc)
        if timezone_name and not errors.get("account_timezone")
        else None
    )
    freshness_passed = bool(
        last_sync_at is not None
        and freshness_hours is not None
        and freshness_hours <= max(0.0, float(max_freshness_hours))
        and local_period_end is not None
        and last_sync_at >= local_period_end
    )

    order_summary = _mapping(account_report.get("order_summary"))
    account_commerce = _mapping(account_totals.get("commerce_outcomes"))
    attribution_passed = bool(
        order_summary.get("status") == "complete"
        and order_summary.get("truncated") is False
        and str(order_summary.get("attribution_policy") or "").strip()
        and order_summary.get("matched_financial_orders") is not None
        and account_commerce.get("status") == "complete"
        and str(account_commerce.get("attribution_scope") or "").strip()
    )
    financial_passed = _row_financial_complete(account_totals)

    gates = {
        "contract": _gate(
            contract_passed,
            "contract_valid" if contract_passed else "contract_invalid_or_load_failed",
            levels=contract_results,
            loader_errors=errors,
        ),
        "period_closed": _gate(
            period_closed,
            "period_closed" if period_closed else "period_is_not_closed",
            account_today=account_today.isoformat(),
        ),
        "coverage": _gate(
            coverage_passed,
            "coverage_complete" if coverage_passed else "coverage_incomplete",
            levels=coverage_results,
        ),
        "reconciliation": _gate(
            reconciliation_passed,
            "reconciled" if reconciliation_passed else "reconciliation_incomplete",
            status=account_quality.get("reconciliation_status"),
        ),
        "freshness": _gate(
            freshness_passed,
            "fresh" if freshness_passed else "freshness_failed",
            last_sync_at=last_sync_at.isoformat() if last_sync_at else None,
            freshness_hours=(
                round(freshness_hours, 3) if freshness_hours is not None else None
            ),
            max_freshness_hours=float(max_freshness_hours),
        ),
        "attribution": _gate(
            attribution_passed,
            "attribution_complete" if attribution_passed else "attribution_incomplete",
            order_summary_status=order_summary.get("status"),
            truncated=bool(order_summary.get("truncated")),
            attribution_policy=order_summary.get("attribution_policy"),
        ),
        "financial_coverage": _gate(
            financial_passed,
            (
                "financial_coverage_complete"
                if financial_passed
                else "financial_coverage_incomplete"
            ),
            amount_complete=account_quality.get("amount_complete"),
            profitability_status=_mapping(
                account_totals.get("commerce_profitability")
            ).get("status"),
            missing_cost_orders=int(
                _mapping(account_totals.get("commerce_profitability")).get(
                    "missing_cost_orders"
                )
                or 0
            ),
        ),
    }
    if provider == "meta_ads":
        accepted = bool(shadow_acceptance and shadow_acceptance.get("ready") is True)
        gates["shadow_acceptance"] = _gate(
            accepted,
            "meta_shadow_accepted" if accepted else "meta_shadow_not_accepted",
            acceptance_reasons=list((shadow_acceptance or {}).get("reasons") or []),
        )
    global_blockers = [
        name for name in REQUIRED_DECISION_GATES if not gates[name]["passed"]
    ]
    if provider == "meta_ads" and not gates["shadow_acceptance"]["passed"]:
        global_blockers.append("shadow_acceptance")
    campaign_rows = list(_mapping(reports.get("campaign")).get("rows") or [])
    campaign_rows.sort(
        key=lambda row: _money_amount(_mapping(row.get("delivery")).get("spend_sar"))
        or 0.0,
        reverse=True,
    )
    candidates = [
        _candidate_evidence(row, global_blockers=global_blockers)
        for row in campaign_rows
    ]
    return {
        "adapter": "decision-evidence-adapter-v1",
        "contract_version": CONTRACT_VERSION,
        "mode": "recommendation_shadow",
        "provider": provider,
        "account": {
            "id": account_identity.get("id"),
            "name": account_identity.get("name"),
            "currency": account_identity.get("currency"),
            "timezone": timezone_name,
        },
        "period": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "timezone": timezone_name,
            "closed": period_closed,
        },
        "gates": gates,
        "decision_ready": not global_blockers,
        "blocked_by": global_blockers,
        "hierarchy": {
            level: {
                "rows": len(_mapping(reports.get(level)).get("rows") or []),
                "source_fact_count": int(
                    _report_quality(_mapping(reports.get(level))).get(
                        "source_fact_count"
                    )
                    or 0
                ),
            }
            for level in ENTITY_LEVELS
        },
        "candidates": candidates,
        "source": {
            "reader": "unified_marketing.gateway",
            "contract_only": True,
        },
        "write_policy": {
            "platform_writes_enabled": False,
            "platform_writes_performed": False,
            "database_writes_performed": False,
        },
        "evaluated_at": current.isoformat(),
    }


async def load_decision_evidence(
    db: Any,
    user_id: str,
    *,
    provider: str,
    date_from: date,
    date_to: date,
    now: datetime | None = None,
    max_freshness_hours: float = 36.0,
) -> dict[str, Any]:
    """Load and validate one evidence window exclusively through the gateway."""
    if date_to < date_from:
        raise ValueError("invalid_date_range")
    identity = await unified_gateway.load_unified_marketing_account_identity(
        db,
        str(user_id),
        provider=provider,
    )
    if not identity:
        raise ValueError("unified_marketing_account_missing")
    timezone_name = str(identity.get("timezone") or "").strip()
    calls = {
        "account": unified_gateway.load_unified_marketing_account_report(
            db,
            str(user_id),
            provider=provider,
            date_from=date_from,
            date_to=date_to,
            timezone_name=timezone_name,
        ),
        **{
            level: unified_gateway.load_unified_marketing_entity_report(
                db,
                str(user_id),
                provider=provider,
                entity_level=level,
                date_from=date_from,
                date_to=date_to,
                timezone_name=timezone_name,
                include_stale=False,
            )
            for level in ENTITY_LEVELS
        },
    }
    results = await asyncio.gather(*calls.values(), return_exceptions=True)
    reports: dict[str, dict[str, Any]] = {}
    loader_errors: dict[str, str] = {}
    for name, result in zip(calls, results, strict=True):
        if isinstance(result, BaseException):
            reports[name] = {}
            loader_errors[name] = type(result).__name__
        else:
            reports[name] = result
    shadow_acceptance = None
    if str(provider or "").strip().lower() == "meta_ads" and not loader_errors:
        shadow_acceptance = evaluate_meta_unified_readiness(
            account_identity=identity,
            reports=reports,
            date_from=date_from,
            date_to=date_to,
            now=now,
            max_freshness_hours=max_freshness_hours,
        )
    return evaluate_decision_evidence(
        account_identity=identity,
        reports=reports,
        provider=provider,
        date_from=date_from,
        date_to=date_to,
        now=now,
        max_freshness_hours=max_freshness_hours,
        loader_errors=loader_errors,
        shadow_acceptance=shadow_acceptance,
    )


__all__ = [
    "ENTITY_LEVELS",
    "REQUIRED_DECISION_GATES",
    "evaluate_decision_evidence",
    "load_decision_evidence",
]
