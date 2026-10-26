"""Read-only execution proof for candidates from the shared marketing gateway.

The unified sync contract replaces, rather than impersonates, the legacy
scheduler's account/integration/run proof. Approval, permissions, provider
preflight and write limits remain in the existing execution path.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from decision_intelligence.evidence_adapter import (
    REQUIRED_DECISION_GATES, load_decision_evidence,
)
from unified_marketing import gateway
from unified_marketing.contract import CONTRACT_VERSION as UNIFIED_CONTRACT
from unified_marketing.providers import PROVIDERS
from unified_marketing.decision_binding import source_evidence_fingerprint

SOURCE_MODE = "unified_marketing_entity_report"
SYNC_CONTRACT = "unified_marketing_execution_evidence_v1"


def _snapchat_commercial_scale_proof(row, parent_campaign, target, current):
    """Prove campaign economics; child delivery never acquires invented sales."""
    from campaign_ai_execution_quality_gate import _number, _parse_datetime, _max_data_age_minutes

    source = parent_campaign if target.get("entity_level") in {"ad_group", "ad"} else row
    source = source if isinstance(source, dict) else {}
    commerce = source.get("commerce_outcomes") or {}
    profit = source.get("commerce_profitability") or {}
    # The current unified commerce contract has no Salla synchronization
    # watermark/coverage or cost-calculation revision timestamp. A freshly read
    # local cohort, or a recent Snapchat sync, cannot prove missing upstream
    # orders have arrived. Keep expansion blocked until that real provenance
    # is exposed and independently validated; never mint it from this clock.
    errors = ["campaign_commerce_freshness_unproven"]
    campaign_id = target.get("campaign_id") or target.get("entity_id")
    if ((source.get("entity") or {}).get("level") != "campaign"
            or (source.get("entity") or {}).get("id") != campaign_id
            or source.get("provider") != "snapchat_ads"
            or (source.get("account") or {}).get("id") != target.get("account_id")
            or (source.get("account") or {}).get("timezone") != target.get("account_timezone")
            or (source.get("account") or {}).get("currency") != target.get("currency_native")
            or (source.get("period") or {}) != (row.get("period") or {})):
        errors.append("campaign_commerce_scope_mismatch")
    if commerce.get("status") != "complete" or commerce.get("attribution_scope") != "exact_campaign_match":
        errors.append("campaign_commerce_attribution_unproven")
    if (profit.get("status") != "complete"
            or profit.get("cost_status") not in {"complete", "salla_fallback"}
            or type(profit.get("missing_cost_orders")) is not int
            or profit.get("missing_cost_orders") != 0):
        errors.append("campaign_product_cost_incomplete")
    orders, profit_orders = commerce.get("orders"), profit.get("orders")
    if (type(orders) is not int or orders < 0 or type(profit_orders) is not int
            or orders != profit_orders):
        errors.append("campaign_commerce_cohort_mismatch")
    amounts = {}
    for key in ("sales", "product_cost", "ad_spend", "contribution_profit"):
        money = profit.get(key) or {}
        amounts[key] = _number(money.get("amount"))
        if money.get("currency") != "SAR" or amounts[key] is None:
            errors.append("campaign_commerce_amount_unproven")
    revenue = commerce.get("revenue") or {}
    sales = _number(revenue.get("amount"))
    spend = _number(((source.get("delivery") or {}).get("spend_sar") or {}).get("amount"))
    if (revenue.get("currency") != "SAR" or sales is None or spend is None
            or amounts["sales"] is None or amounts["ad_spend"] is None
            or abs(sales - amounts["sales"]) > 0.05 or abs(spend - amounts["ad_spend"]) > 0.05):
        errors.append("campaign_commerce_cohort_mismatch")
    if all(value is not None for value in amounts.values()):
        if (amounts["sales"] < 0 or amounts["product_cost"] < 0 or amounts["ad_spend"] < 0
                or abs(amounts["sales"] - amounts["product_cost"] - amounts["ad_spend"]
                       - amounts["contribution_profit"]) > 0.05):
            errors.append("campaign_commerce_arithmetic_mismatch")
    observed = _parse_datetime((source.get("account") or {}).get("last_sync_at"))
    age = (current - observed).total_seconds() / 60 if observed is not None else None
    if age is None or not 0 <= age <= _max_data_age_minutes():
        errors.append("campaign_commerce_source_stale")
    return {
        "status": "complete" if not errors else "blocked",
        "source": "unified_marketing_contract:mezan_exact_campaign_attribution",
        "campaign_id": campaign_id,
        "account_id": target.get("account_id"),
        "period": source.get("period"),
        "provider_observed_at": observed.isoformat() if observed else None,
        "commerce_freshness": {
            "status": "unproven",
            "source": "upstream_provenance_not_exposed_by_unified_commerce_contract",
            "observed_at": None,
            "recovery_action": "expose_and_validate_salla_sync_coverage_and_cost_calculation_provenance",
        },
        "cost_status": profit.get("cost_status"),
        "profit_metric": "contribution_profit",
        "net_profit_available": False,
        "errors": list(dict.fromkeys(errors)),
    }


async def collect_unified_execution_evidence(
    db: Any, user_id: str, target: dict[str, Any], *,
    snapshot_generated_at: Any, snapshot_range: dict[str, Any],
    current: Any, baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    from campaign_ai_execution_quality_gate import (
        CONTRACT_VERSION, _integer, _max_data_age_minutes, _number, _parse_datetime,
        evaluate_execution_quality,
    )

    errors: list[str] = []
    identity: dict[str, Any] = {}
    decision: dict[str, Any] = {}
    row: dict[str, Any] = {}
    parent_campaign: dict[str, Any] | None = None
    management: dict[str, Any] = {}
    spec = next((p for p in PROVIDERS.values() if p.ai_provider == target.get("provider")), None)
    provider_id = spec.provider if spec else None
    start = end = None
    try:
        start = date.fromisoformat(str(target.get("source_date_from")))
        end = date.fromisoformat(str(target.get("source_date_to")))
        range_start = date.fromisoformat(str(snapshot_range.get("from")))
        range_end = date.fromisoformat(str(snapshot_range.get("to")))
        # The UI range can be in a different timezone; duration must still match.
        if end < start or end - start != range_end - range_start:
            errors.append("unified_source_window_mismatch")
    except (TypeError, ValueError):
        errors.append("unified_source_window_missing")
    try:
        generated = _parse_datetime(snapshot_generated_at)
        local_end = generated.astimezone(ZoneInfo(target.get("account_timezone"))).date() - timedelta(days=1)
        if end != local_end:
            errors.append("unified_source_not_latest_closed_day")
    except (AttributeError, TypeError, ValueError, ZoneInfoNotFoundError):
        errors.append("unified_source_window_missing")
    if not spec:
        errors.append("unified_provider_unsupported")
    if not target.get("account_id"):
        errors.append("unified_account_mismatch")
    if spec and target.get("account_id") and start is not None and end is not None and end >= start:
        try:
            identity = await gateway.load_unified_marketing_account_identity(
                db, user_id, provider=provider_id, account_id=target["account_id"],
            ) or {}
            decision = await load_decision_evidence(
                db, user_id, provider=provider_id, account_id=target["account_id"], date_from=start, date_to=end,
                now=current, max_freshness_hours=_max_data_age_minutes() / 60,
            )
            report = await gateway.load_unified_marketing_entity_report(
                db, user_id, provider=provider_id, account_id=target["account_id"],
                entity_level=target.get("entity_level"), date_from=start, date_to=end,
                timezone_name=identity.get("timezone") or "", include_stale=False,
            )
            matches = [item for item in report.get("rows") or []
                       if (item.get("entity") or {}).get("id") == target.get("entity_id")]
            if len(matches) != 1:
                errors.append("unified_entity_missing_or_duplicate")
            else:
                row = matches[0]
            management = (report.get("management_context") or {}).get(target.get("entity_id")) or {}
            if target.get("entity_level") in {"ad_group", "ad"}:
                parent_report = await gateway.load_unified_marketing_entity_report(
                    db, user_id, provider=provider_id, account_id=target["account_id"], entity_level="campaign",
                    date_from=start, date_to=end,
                    timezone_name=identity.get("timezone") or "", include_stale=False,
                )
                parents = [item for item in parent_report.get("rows") or []
                           if (item.get("entity") or {}).get("id") == target.get("campaign_id")]
                if len(parents) == 1:
                    parent_campaign = parents[0]
                else:
                    errors.append("unified_parent_campaign_missing_or_duplicate")
        except Exception:  # Unavailable/invalid evidence never enables a write.
            errors.append("unified_evidence_load_failed")

    gates = decision.get("gates") or {}
    if (decision.get("contract_version") != UNIFIED_CONTRACT
            or decision.get("provider") != provider_id
            or decision.get("decision_ready") is not True
            or decision.get("blocked_by")
            or any((gates.get(name) or {}).get("passed") is not True for name in REQUIRED_DECISION_GATES)):
        errors.append("unified_decision_gates_incomplete")
    account_id = target.get("account_id")
    accounts = (identity, decision.get("account") or {}, row.get("account") or {})
    if not account_id or any(item.get("id") != account_id for item in accounts):
        errors.append("unified_account_mismatch")
    if not target.get("account_timezone") or any(
        item.get("timezone") != target.get("account_timezone") for item in accounts
    ):
        errors.append("unified_timezone_mismatch")
    currency = target.get("currency_native")
    if not currency or any(item.get("currency") != currency for item in accounts):
        errors.append("unified_currency_mismatch")
    entity = row.get("entity") or {}
    if entity.get("level") != target.get("entity_level"):
        errors.append("unified_entity_level_mismatch")
    for key in ("campaign_id", "ad_group_id"):
        expected = target.get(key)
        actual = entity.get(key)
        if key == "campaign_id" and entity.get("level") == "campaign":
            actual = entity.get("id")
        if key == "ad_group_id" and entity.get("level") == "ad_group":
            actual = entity.get("id")
        if (expected or "") != (actual or ""):
            errors.append("unified_parent_identity_mismatch")
    for period in (decision.get("period") or {}, row.get("period") or {}):
        if (period.get("date_from") != target.get("source_date_from")
                or period.get("date_to") != target.get("source_date_to")
                or period.get("timezone") != target.get("account_timezone")):
            errors.append("unified_source_window_mismatch")
    lineage = row.get("lineage") or {}
    if (row.get("provider") != provider_id
            or target.get("unified_contract_version") != UNIFIED_CONTRACT
            or target.get("provider_result_source") != (spec.evidence_source if spec else None)
            or not target.get("source_fact_collection")
            or lineage.get("source_collection") != target.get("source_fact_collection")
            or not lineage.get("adapter") or not lineage.get("source_version")
            or target.get("result_source") != "platform"
            or target.get("action_report_time") != "conversion"
            or (row.get("period") or {}).get("action_report_time") != "conversion"):
        errors.append("unified_source_provenance_mismatch")

    delivery = row.get("delivery") or {}
    platform = row.get("platform_outcomes") or {}
    native = _number((delivery.get("spend") or {}).get("amount"))
    sar = _number((delivery.get("spend_sar") or {}).get("amount"))
    rate = _number(target.get("fx_rate_to_sar"))
    fx_ok = bool(native is not None and native > 0 and sar is not None and sar > 0
                 and rate is not None and rate > 0 and abs(sar / native - rate) <= 0.000001
                 and (delivery.get("spend") or {}).get("currency") == currency
                 and (delivery.get("spend_sar") or {}).get("currency") == "SAR"
                 and (platform.get("revenue") or {}).get("currency") == currency
                 and target.get("fx_source") == "unified_marketing_cost_contract")
    revenue = _number((platform.get("revenue") or {}).get("amount"))
    expected_metrics = {
        "spend_sar": sar,
        "revenue_sar": round(revenue * sar / native, 2) if revenue is not None and native and sar is not None else None,
        "purchases": platform.get("conversions"),
        "impressions": delivery.get("impressions"), "clicks": delivery.get("clicks"),
    }
    if any(_number(target.get(key)) is None or _number(value) is None
           or abs(_number(target.get(key)) - _number(value)) > 0.000001
           for key, value in expected_metrics.items()):
        errors.append("unified_entity_metrics_changed")
    quality = row.get("quality") or {}
    if (target.get("data_complete") is not True
            or target.get("pagination_complete") is not True
            or _integer(quality.get("source_fact_count")) <= 0
            or quality.get("sync_status") != "complete"
            or quality.get("coverage_status") != "complete"):
        errors.append("unified_entity_facts_incomplete")
    status = str(management.get("status") or "").upper()
    if status not in {"ACTIVE", "ENABLED"} or status != str(target.get("status") or "").upper():
        errors.append("unified_provider_status_changed")
    if _number(management.get("daily_budget_native")) != _number(target.get("current_daily_budget_native")):
        errors.append("unified_provider_budget_changed")
    timestamps = [identity.get("last_sync_at"), target.get("source_observed_at"),
                  management.get("updated_at"), (gates.get("freshness") or {}).get("last_sync_at")]
    parsed = [_parse_datetime(value) for value in timestamps]
    ages = [(current - value).total_seconds() / 60 if value else None for value in parsed]
    fresh = all(age is not None and 0 <= age <= _max_data_age_minutes() for age in ages)
    fingerprint = None
    try:
        fingerprint = source_evidence_fingerprint(
            user_id, row, management, parent_campaign=parent_campaign,
        )
    except ValueError:
        errors.append("unified_parent_campaign_evidence_unproven")
    captured_fingerprint = target.get("source_evidence_fingerprint")
    if not captured_fingerprint or captured_fingerprint != fingerprint:
        errors.append("unified_candidate_source_revision_changed")
    previous = ((baseline or {}).get("entity_facts") or {}).get("fingerprint")
    unchanged = previous is None or previous == fingerprint
    errors = list(dict.fromkeys(errors))
    complete = not errors
    evidence = {
        "contract_version": CONTRACT_VERSION, "captured_at": current.isoformat(),
        "provider": target.get("provider"), "provider_id": provider_id,
        "account_id": account_id, "entity_level": target.get("entity_level"),
        "entity_id": target.get("entity_id"), "snapshot_generated_at": snapshot_generated_at,
        "data_quality": "complete" if complete else "incomplete",
        "data_state": "confirmed_data" if complete else "unknown_incomplete",
        "coverage": {"status": "complete" if complete else "incomplete"},
        "freshness": {"status": "fresh" if fresh else "stale", "max_age_minutes": _max_data_age_minutes()},
        "provider_sync": {"contract": SYNC_CONTRACT, "source_mode": SOURCE_MODE,
                          "status": "complete" if complete else "incomplete",
                          "account_bound": complete, "errors_count": len(errors), "error_codes": errors},
        "unified_decision": {"contract_version": UNIFIED_CONTRACT, "gates": gates,
                             "decision_ready": decision.get("decision_ready")},
        "pagination": {"status": "complete" if complete else "incomplete", "truncated": not complete},
        "fx": {"status": "documented" if fx_ok else "unknown", "rate_to_sar": rate, "currency": currency},
        "attribution": {"status": "trusted" if complete else "unknown", "source_window_trusted": complete},
        "commercial_scale": (_snapchat_commercial_scale_proof(row, parent_campaign, target, current)
                             if target.get("provider") == "snapchat" else None),
        "entity_facts": {"status": "complete" if complete else "incomplete", "errors": errors, "fingerprint": fingerprint},
        "source_validation": {"status": "complete" if complete else "incomplete", "errors": errors},
        "source_revision": {"status": "unchanged" if unchanged else "changed", "snapshot_fingerprint": previous,
                            "current_fingerprint": fingerprint},
    }
    result = evaluate_execution_quality(evidence)
    evidence.update(status=result["status"], blockers=result["blockers"])
    return evidence
