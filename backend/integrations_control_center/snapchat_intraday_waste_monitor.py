"""Bounded, read-only early-spend monitoring for Snapchat delivery entities.

The monitor is deliberately an evidence service, not an automation rule.  It
observes campaigns, Ad Squads and Ads from the earliest trustworthy management
or provider timestamp, compares current cumulative spend with a dynamically
selected CPA benchmark, and returns a governed proposal *candidate*.  It never
writes to Snapchat or to Mezan's database.

Important limitations are part of the result contract:

* current per-entity facts are cumulative account-local TOTAL-day snapshots;
  they are refreshed frequently, but they are not a minute-by-minute ledger;
* the exact first-spend minute is unknown unless a caller has persisted it;
* an absent immediate conversion is not treated as failure until both the
  reporting window and an evidence-backed conversion-delay window are mature;
* product economics and statistical screens are evidence for adaptive review,
  never a universal pause rule or a proposal-ready decision.

The statistical signal is adaptive.  If the effective target CPA is ``T`` and
matured spend is ``S``, an on-target entity would have an expected purchase
count of ``S / T``.  The Poisson lower-tail probability of the observed count
is then compared with the caller's risk tolerance.  This avoids a fixed
"pause after X riyals/minutes" rule: the spend boundary moves with economics,
historical performance, conversion delay, reporting freshness and risk policy.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .snapchat_account_selection import _load_selected_accounts
from .snapchat_account_timezone_manager import (
    SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
    account_local_source_mode,
)
from .snapchat_ad_performance import ad_source_mode
from .snapchat_adsquad_performance import adsquad_source_mode
from .snapchat_freshness_impl_v6 import FRESHNESS_COLLECTION
from .snapchat_native_data_common import (
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    _collection,
)

SOURCE_MODE = "snapchat_intraday_early_waste_monitor_v1"
PROPOSAL_COLLECTION = "mezan_snapchat_campaign_proposals_v1"

MAX_ENTITY_ROWS = 30_000
MAX_PERFORMANCE_ROWS = 100_000
MAX_PROPOSAL_ROWS = 20_000
MAX_FRESHNESS_ROWS = 1_000

SUPPORTED_ENTITY_TYPES = ("campaign", "ad_squad", "ad")
ACTIVE_STATUSES = {"ACTIVE", "ENABLED"}
COMPLETED_MANAGEMENT_STATUSES = {"completed", "rolled_back"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _aware(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


async def _rows(cursor: Any, limit: int) -> list[dict[str, Any]]:
    if isinstance(cursor, list):
        return list(cursor[:limit])
    if hasattr(cursor, "to_list"):
        return list(await cursor.to_list(length=limit))
    output: list[dict[str, Any]] = []
    async for row in cursor:
        output.append(row)
        if len(output) >= limit:
            break
    return output


def _entity_key(entity_type: Any, entity_id: Any) -> tuple[str, str]:
    return _text(entity_type), _text(entity_id)


def _operation_changes(proposal: dict[str, Any]) -> dict[str, Any]:
    operation = proposal.get("operation")
    operation = operation if isinstance(operation, dict) else {}
    changes = operation.get("changes")
    if isinstance(changes, dict):
        return changes
    plural = _text(operation.get("plural"))
    body = operation.get("body")
    if isinstance(body, dict) and isinstance(body.get(plural), list):
        first = body[plural][0] if body[plural] else None
        return first if isinstance(first, dict) else {}
    return {}


def build_management_anchors(
    proposals: Iterable[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Return exact managed/activation anchors without inventing first spend."""
    grouped: dict[tuple[str, str], list[tuple[datetime, dict[str, Any]]]] = defaultdict(
        list
    )
    for proposal in proposals:
        if _text(proposal.get("status")).lower() not in (COMPLETED_MANAGEMENT_STATUSES):
            continue
        operation = proposal.get("operation")
        operation = operation if isinstance(operation, dict) else {}
        entity_type = _text(operation.get("entity_type"))
        if entity_type not in SUPPORTED_ENTITY_TYPES:
            continue
        entity_id = _text(
            proposal.get("provider_entity_id") or proposal.get("target_id")
        )
        executed_at = _parse_time(proposal.get("executed_at") or proposal.get("created_at"))
        if not entity_id or executed_at is None:
            continue
        grouped[(entity_type, entity_id)].append((executed_at, proposal))
        rollback = proposal.get("rollback")
        rollback = rollback if isinstance(rollback, dict) else {}
        rolled_back_at = _parse_time(
            rollback.get("rolled_back_at") or proposal.get("rolled_back_at")
        )
        rollback_after = rollback.get("after")
        if (
            _text(proposal.get("status")).lower() == "rolled_back"
            and rolled_back_at is not None
            and isinstance(rollback_after, dict)
        ):
            # A rollback is a second verified management event.  Never let the
            # earlier forward operation remain the latest activation anchor.
            grouped[(entity_type, entity_id)].append(
                (
                    rolled_back_at,
                    {
                        **proposal,
                        "action": f"{_text(proposal.get('action'))}.rollback",
                        "operation": {
                            "entity_type": entity_type,
                            "changes": rollback_after,
                        },
                    },
                )
            )

    output: dict[tuple[str, str], dict[str, Any]] = {}
    for key, events in grouped.items():
        events.sort(key=lambda pair: pair[0])
        first_at, first = events[0]
        latest_status: str | None = None
        latest_status_at: datetime | None = None
        latest_activation_at: datetime | None = None
        for happened, proposal in events:
            changes = _operation_changes(proposal)
            status = _text(changes.get("status")).upper()
            action = _text(proposal.get("action"))
            # Delivery creates are forcibly PAUSED by the management plane.
            if action.endswith(".create") and not status:
                status = "PAUSED"
            if status in {"ACTIVE", "PAUSED"}:
                latest_status = status
                latest_status_at = happened
                if status == "ACTIVE":
                    latest_activation_at = happened
        output[key] = {
            "managed_by_mezan": True,
            "managed_from": _iso(first_at),
            "managed_from_proposal_id": first.get("proposal_id"),
            "latest_managed_status": latest_status,
            "latest_managed_status_at": _iso(latest_status_at),
            "latest_activation_at": _iso(latest_activation_at),
            "management_event_count": len(events),
        }
    return output


def _lineage(
    entity: dict[str, Any],
    entities: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, str | None]:
    entity_type = _text(entity.get("entity_type"))
    entity_id = _text(entity.get("external_id"))
    campaign_id = _text(entity.get("campaign_id"))
    ad_squad_id = _text(entity.get("ad_squad_id"))
    if entity_type == "campaign":
        campaign_id = entity_id
    elif entity_type == "ad_squad":
        ad_squad_id = entity_id
    elif entity_type == "ad" and ad_squad_id:
        parent = entities.get(("ad_squad", ad_squad_id), {})
        campaign_id = campaign_id or _text(parent.get("campaign_id"))
    return {
        "campaign_id": campaign_id or None,
        "ad_squad_id": ad_squad_id or None,
    }


def _metric(row: dict[str, Any], key: str) -> float | None:
    direct_key = {
        "orders": "purchases",
        "sales_sar": "purchase_value_sar",
        "spend_sar": "spend_sar",
    }.get(key)
    if direct_key:
        direct = _number(row.get(direct_key))
        if direct is not None:
            return direct
    metrics = row.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    provider_key = {
        "orders": "conversion_purchases",
        "sales_sar": "conversion_purchases_value",
        "impressions": "impressions",
        "swipes": "swipes",
        "view_content": "conversion_view_content",
        "add_to_cart": "conversion_add_cart",
        "start_checkout": "conversion_start_checkout",
        "add_billing": "conversion_add_billing",
    }.get(key, key)
    value = _number(metrics.get(provider_key))
    if key == "sales_sar" and value is not None:
        # Prefer the converted direct field.  Raw provider value is micro
        # currency and is intentionally not exposed as SAR.
        return None
    return value


def _sum_metric(rows: Iterable[dict[str, Any]], key: str) -> float | None:
    values = [value for row in rows if (value := _metric(row, key)) is not None]
    return round(sum(values), 6) if values else None


def _historical_benchmarks(
    rows: Iterable[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = _entity_key(row.get("entity_type"), row.get("external_id"))
        if key[0] in SUPPORTED_ENTITY_TYPES and key[1]:
            grouped[key].append(row)
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for key, facts in grouped.items():
        daily_cpas: list[float] = []
        for fact in facts:
            spend = _metric(fact, "spend_sar")
            orders = _metric(fact, "orders")
            if spend is not None and orders is not None and orders > 0:
                daily_cpas.append(spend / orders)
        spend = _sum_metric(facts, "spend_sar")
        orders = _sum_metric(facts, "orders")
        output[key] = {
            "days_observed": len({_text(row.get("date")) for row in facts}),
            "days_with_conversions": len(daily_cpas),
            "spend_sar": spend,
            "orders": int(round(orders)) if orders is not None else None,
            "weighted_cpa_sar": (
                round(spend / orders, 2)
                if spend is not None and orders not in {None, 0}
                else None
            ),
            "median_converting_day_cpa_sar": (
                round(statistics.median(daily_cpas), 2) if daily_cpas else None
            ),
        }
    return output


def _economics_for(
    economics_by_campaign: dict[str, dict[str, Any]] | None,
    campaign_id: str | None,
) -> dict[str, Any]:
    raw = (economics_by_campaign or {}).get(_text(campaign_id), {})
    return raw if isinstance(raw, dict) else {}


def select_effective_target_cpa(
    *,
    economics: dict[str, Any] | None,
    historical: dict[str, Any] | None,
) -> dict[str, Any]:
    """Select a CPA target with explicit provenance and profit authority."""
    economics = economics or {}
    historical = historical or {}
    verified = economics.get("verified") is True
    candidates = (
        ("verified_target_cpa", economics.get("target_cpa_sar")),
        ("verified_max_profitable_cpa", economics.get("max_profitable_cpa_sar")),
        (
            "verified_contribution_margin_per_order",
            economics.get("contribution_margin_per_order_before_ads_sar"),
        ),
        ("verified_break_even_cpa", economics.get("break_even_cpa_sar")),
    )
    if verified:
        verified_values = [
            (source, value)
            for source, raw in candidates
            if (value := _number(raw)) is not None and value > 0
        ]
        if verified_values:
            # If a configured target accidentally exceeds a verified
            # break-even ceiling, the most conservative verified ceiling wins.
            source, value = min(verified_values, key=lambda pair: pair[1])
            return {
                "target_cpa_sar": round(value, 2),
                "source": source,
                "verified_product_economics": True,
                "profit_authoritative": True,
                "verified_candidates_sar": {
                    candidate_source: round(candidate_value, 2)
                    for candidate_source, candidate_value in verified_values
                },
            }

    for source, key in (
        ("historical_weighted_cpa", "weighted_cpa_sar"),
        ("historical_median_converting_day_cpa", "median_converting_day_cpa_sar"),
    ):
        value = _number(historical.get(key))
        if value is not None and value > 0:
            return {
                "target_cpa_sar": round(value, 2),
                "source": source,
                "verified_product_economics": False,
                "profit_authoritative": False,
            }
    return {
        "target_cpa_sar": None,
        "source": "unavailable",
        "verified_product_economics": False,
        "profit_authoritative": False,
    }


def poisson_lower_tail(observed: int, expected: float) -> float:
    """Return P(X <= observed) for X~Poisson(expected), without scipy."""
    if expected <= 0:
        return 1.0
    observed = max(0, int(observed))
    # The recursive sum is stable for the early-decision range.  For very large
    # lambdas the zero term underflows, which correctly tends to zero here.
    term = math.exp(-expected)
    total = term
    for value in range(1, observed + 1):
        term *= expected / value
        total += term
    return min(max(total, 0.0), 1.0)


def _freshness_for_fact(
    fact: dict[str, Any],
    freshness: dict[str, Any] | None,
    *,
    now: datetime,
    expected_refresh_minutes: float,
) -> dict[str, Any]:
    freshness = freshness or {}
    processed = _parse_time(freshness.get("conversion_data_processed_end_time"))
    observed_end = _parse_time(fact.get("provider_window_end"))
    updated = _parse_time(fact.get("updated_at"))
    complete_for_fact = bool(
        processed is not None and observed_end is not None and processed >= observed_end
    )
    data_age_minutes = (
        max(0.0, (now - updated).total_seconds() / 60.0)
        if updated is not None
        else None
    )
    # Staleness is a safety/completeness guard, not a performance rule.
    fresh_enough = bool(
        data_age_minutes is not None
        and data_age_minutes <= max(expected_refresh_minutes * 2, 1)
    )
    reporting_lag_minutes = (
        max(0.0, (now - processed).total_seconds() / 60.0)
        if processed is not None
        else None
    )
    return {
        "complete_for_observed_performance_window": complete_for_fact,
        "fresh_enough_for_adverse_recommendation": fresh_enough,
        "conversion_data_processed_end_time": _iso(processed),
        "performance_window_end": _iso(observed_end),
        "performance_updated_at": _iso(updated),
        "data_age_minutes": (
            round(data_age_minutes, 1) if data_age_minutes is not None else None
        ),
        "reporting_lag_minutes": (
            round(reporting_lag_minutes, 1)
            if reporting_lag_minutes is not None
            else None
        ),
    }


def _tracking_anchor(
    entity: dict[str, Any],
    management: dict[str, Any],
) -> tuple[datetime | None, str]:
    managed_activation = _parse_time(management.get("latest_activation_at"))
    if (
        managed_activation is not None
        and _text(management.get("latest_managed_status")).upper() == "ACTIVE"
    ):
        return managed_activation, "mezan_verified_activation"
    provider_start = _parse_time(entity.get("start_time"))
    if provider_start is not None:
        return provider_start, "provider_delivery_start"
    provider_created = _parse_time(entity.get("created_at_provider"))
    if provider_created is not None:
        return provider_created, "provider_created_at"
    managed_from = _parse_time(management.get("managed_from"))
    if managed_from is not None:
        return managed_from, "mezan_first_management_event"
    observed = _parse_time(entity.get("created_at") or entity.get("last_observed_at"))
    return observed, "catalog_observation" if observed is not None else "unknown"


def _conversion_delay(
    economics: dict[str, Any],
    historical: dict[str, Any],
) -> dict[str, Any]:
    candidates: list[tuple[str, Any]] = []
    if economics.get("verified") is True:
        candidates.append(
            (
                "verified_campaign_conversion_delay",
                economics.get("expected_conversion_delay_minutes"),
            )
        )
    if historical.get("conversion_delay_verified") is True:
        candidates.append(
            (
                "verified_historical_time_to_conversion",
                historical.get("expected_conversion_delay_minutes"),
            )
        )
    for source, raw in candidates:
        value = _number(raw)
        if value is not None and value >= 0:
            return {"minutes": round(value, 1), "source": source}
    return {"minutes": None, "source": "unavailable"}


def _observed_metrics(fact: dict[str, Any]) -> dict[str, Any]:
    values = {
        key: _metric(fact, key)
        for key in (
            "spend_sar",
            "orders",
            "sales_sar",
            "impressions",
            "swipes",
            "view_content",
            "add_to_cart",
            "start_checkout",
            "add_billing",
        )
    }
    spend = values["spend_sar"]
    orders = values["orders"]
    values["orders"] = int(round(orders)) if orders is not None else None
    values["cpa_sar"] = (
        round(spend / orders, 2)
        if spend is not None and orders not in {None, 0}
        else None
    )
    return values


def evaluate_intraday_entity(
    *,
    entity: dict[str, Any],
    fact: dict[str, Any] | None,
    historical: dict[str, Any] | None,
    economics: dict[str, Any] | None,
    management: dict[str, Any] | None,
    freshness: dict[str, Any] | None,
    now: datetime | None = None,
    risk_tolerance: float = 0.05,
    expected_refresh_minutes: float = 15.0,
    global_coverage_complete: bool = True,
) -> dict[str, Any]:
    """Evaluate one entity and return advice plus an optional proposal candidate."""
    current = _aware(now)
    if not 0 < risk_tolerance < 0.5:
        raise ValueError("risk_tolerance must be between 0 and 0.5")
    if expected_refresh_minutes <= 0:
        raise ValueError("expected_refresh_minutes must be positive")

    fact = fact or {}
    historical = historical or {}
    economics = economics or {}
    management = management or {}
    entity_type = _text(entity.get("entity_type"))
    entity_id = _text(entity.get("external_id"))
    campaign_id = _text(fact.get("campaign_id") or entity.get("campaign_id"))
    if entity_type == "campaign":
        campaign_id = entity_id
    parent_id = (
        _text(entity.get("campaign_id"))
        if entity_type == "ad_squad"
        else _text(entity.get("ad_squad_id")) if entity_type == "ad" else ""
    )
    target = select_effective_target_cpa(
        economics=economics,
        historical=historical,
    )
    metrics = _observed_metrics(fact)
    reporting = _freshness_for_fact(
        fact,
        freshness,
        now=current,
        expected_refresh_minutes=expected_refresh_minutes,
    )
    anchor, anchor_source = _tracking_anchor(entity, management)
    age_minutes = (
        max(0.0, (current - anchor).total_seconds() / 60.0)
        if anchor is not None
        else None
    )
    delay = _conversion_delay(economics, historical)
    delay_mature = bool(
        age_minutes is not None
        and delay["minutes"] is not None
        and age_minutes >= delay["minutes"]
    )
    spend = metrics.get("spend_sar")
    orders = metrics.get("orders")
    target_cpa = target.get("target_cpa_sar")
    expected_orders = (
        spend / target_cpa
        if spend is not None and target_cpa not in {None, 0}
        else None
    )
    lower_tail = (
        poisson_lower_tail(int(orders or 0), expected_orders)
        if expected_orders is not None
        else None
    )
    underperformance_confidence = (
        round(1.0 - lower_tail, 6) if lower_tail is not None else None
    )
    evidence_threshold = 1.0 - risk_tolerance
    statistically_adverse = bool(
        underperformance_confidence is not None
        and underperformance_confidence >= evidence_threshold
    )
    downstream = sum(
        float(metrics.get(key) or 0)
        for key in ("add_to_cart", "start_checkout", "add_billing")
    )
    safety_complete = bool(
        global_coverage_complete
        and reporting["complete_for_observed_performance_window"]
        and reporting["fresh_enough_for_adverse_recommendation"]
        and delay_mature
    )

    code = "continue_observing"
    label = "استمرار المراقبة"
    reasons: list[str] = []
    caveats: list[str] = []
    candidate: dict[str, Any] | None = None

    effective_status = _text(
        management.get("latest_managed_status") or entity.get("status")
    ).upper()
    if effective_status and effective_status not in ACTIVE_STATUSES:
        code, label = "inactive_observation", "الكيان غير نشط — لا يوجد هدر جارٍ"
        reasons.append("latest_verified_management_or_provider_status_is_not_active")
    elif not global_coverage_complete:
        code, label = "withhold_incomplete_scan", "حجب القرار حتى يكتمل المسح"
        caveats.append("bounded_query_limit_reached")
    elif spend is None or spend <= 0:
        code, label = "no_spend_yet", "لا يوجد صرف بعد"
        reasons.append("monitoring_from_start_before_first_observed_spend")
    elif target_cpa is None:
        code, label = "investigate_target_missing", "استكمال اقتصاديات المنتج"
        caveats.append("no_verified_economic_or_historical_cpa_target")
    elif not reporting["complete_for_observed_performance_window"]:
        code, label = "wait_reporting_completion", "انتظار اكتمال بيانات التحويل"
        caveats.append("conversion_reporting_has_not_caught_up_to_performance")
    elif not reporting["fresh_enough_for_adverse_recommendation"]:
        code, label = "wait_fresh_snapshot", "انتظار لقطة حديثة"
        caveats.append("performance_snapshot_is_stale_or_timestamp_missing")
    elif delay["minutes"] is None:
        code, label = "learn_conversion_delay", "تعلم مهلة التحويل أولًا"
        caveats.append("conversion_delay_model_unavailable")
    elif not delay_mature:
        code, label = "observe_conversion_lag", "المراقبة خلال مهلة التحويل"
        reasons.append("entity_is_younger_than_observed_conversion_delay")
    elif (
        orders
        and metrics.get("cpa_sar") is not None
        and metrics["cpa_sar"] <= target_cpa
    ):
        code, label = "continue_efficient", "الاستمرار — الكفاءة ضمن الهدف"
        reasons.append("observed_cpa_is_at_or_below_effective_target")
    elif not statistically_adverse:
        code, label = "watch_accumulating_evidence", "مراقبة مع تراكم الدليل"
        reasons.append("underperformance_confidence_below_selected_risk_threshold")
    elif not target["profit_authoritative"]:
        code, label = "investigate_efficiency", "تحقق قبل قرار يؤثر على المبيعات"
        caveats.append("historical_cpa_is_not_a_verified_profit_guardrail")
    elif downstream > 0 or bool(orders):
        code, label = "adaptive_review", "إشارة تستحق مراجعة تكيفية"
        reasons.append(
            "statistical_underperformance_with_observed_conversion_or_funnel_potential"
        )
        candidate = {
            "ready_for_proposal": False,
            "requested_change": None,
            "suggested_options": ["observe", "decrease_budget", "investigate"],
            "action": None,
            "account_id": entity.get("ad_account_id"),
            "target_id": entity_id,
            "parent_id": parent_id or None,
            "payload": None,
            "requires_budget_choice": True,
            "reason": "إشارة إحصائية مع مبيعات أو قمع قائم؛ تُرسل للحكم التكيفي دون فرض تخفيض.",
        }
    else:
        code, label = "adaptive_review", "إشارة تستحق مراجعة تكيفية"
        reasons.append("mature_spend_is_unlikely_under_the_verified_cpa_target")
        candidate = {
            "ready_for_proposal": False,
            "requested_change": None,
            "suggested_options": ["observe", "pause", "decrease_budget", "investigate"],
            "action": None,
            "account_id": entity.get("ad_account_id"),
            "target_id": entity_id,
            "parent_id": parent_id or None,
            "payload": None,
            "activation_acknowledged": False,
            "reason": (
                "صرف ناضج دون تحويل أو قمع ظاهر وفق شاشة إحصائية؛ هذه إشارة "
                "للحكم التكيفي وليست قاعدة إيقاف."
            ),
            "expected_outcome": {
                "primary_goal": "protect_contribution_profit",
                "sales_tradeoff": "possible_sales_reduction_requires_follow_up",
                "evaluation_horizons_hours": [24, 72, 168],
            },
        }

    if fact and _text(fact.get("action_report_time")) != "conversion":
        caveats.append("performance_is_not_conversion_time_reporting")
        candidate = None
        code, label = (
            "withhold_wrong_reporting_mode",
            "حجب القرار — وضع الإسناد غير صحيح",
        )
    if not safety_complete and candidate is not None:
        # Defensive invariant if classification branches change later.
        candidate = None
        code, label = "withhold_safety_guard", "حجب القرار حتى تكتمل الحماية"

    first_spend_observed = _parse_time(fact.get("first_spend_observed_at"))
    first_spend_precision = (
        "persisted_observation"
        if first_spend_observed is not None
        else "unavailable_from_cumulative_day_snapshot"
    )
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "entity_name": entity.get("display_name") or entity_id,
        "account_id": entity.get("ad_account_id"),
        "campaign_id": campaign_id or None,
        "ad_squad_id": entity.get("ad_squad_id"),
        "status": entity.get("status"),
        "daily_budget_micro": entity.get("daily_budget_micro"),
        "tracking": {
            **management,
            "monitoring_started_at": _iso(anchor),
            "monitoring_anchor_source": anchor_source,
            "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
            "first_spend_observed_at": _iso(first_spend_observed),
            "first_spend_time_precision": first_spend_precision,
        },
        "metrics": metrics,
        "historical_benchmark": historical,
        "effective_target": target,
        "conversion_delay": {
            **delay,
            "mature": delay_mature,
        },
        "reporting": reporting,
        "evidence": {
            "expected_orders_at_target": (
                round(expected_orders, 4) if expected_orders is not None else None
            ),
            "poisson_lower_tail_probability": (
                round(lower_tail, 6) if lower_tail is not None else None
            ),
            "underperformance_confidence": underperformance_confidence,
            "required_confidence": round(evidence_threshold, 6),
            "risk_tolerance": risk_tolerance,
            "statistically_adverse": statistically_adverse,
            "downstream_funnel_events": round(downstream, 2),
            "safety_complete": safety_complete,
            "model_assumption": (
                "poisson_rate_is_a_screening_model_not_a_causal_forecast"
            ),
        },
        "recommendation": {
            "code": code,
            "label_ar": label,
            "is_observation_not_certain_causality": True,
            "reasons": reasons,
            "caveats": caveats,
        },
        "proposal_candidate": candidate,
        "provider_write_reached": False,
    }


def evaluate_intraday_facts(
    *,
    entities: list[dict[str, Any]],
    current_facts: list[dict[str, Any]],
    historical_facts: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    freshness_by_account: dict[str, dict[str, Any]],
    economics_by_campaign: dict[str, dict[str, Any]] | None = None,
    now: datetime | None = None,
    risk_tolerance: float = 0.05,
    expected_refresh_minutes: float = 15.0,
    global_coverage_complete: bool = True,
) -> list[dict[str, Any]]:
    """Evaluate an already-loaded bounded fact set at all delivery levels."""
    entity_map = {
        _entity_key(row.get("entity_type"), row.get("external_id")): row
        for row in entities
        if _text(row.get("entity_type")) in SUPPORTED_ENTITY_TYPES
        and _text(row.get("external_id"))
    }
    anchors = build_management_anchors(proposals)
    benchmarks = _historical_benchmarks(historical_facts)
    current_map = {
        _entity_key(row.get("entity_type"), row.get("external_id")): row
        for row in current_facts
    }
    results: list[dict[str, Any]] = []
    for key, entity in entity_map.items():
        lineage = _lineage(entity, entity_map)
        fact = current_map.get(key)
        # Include all managed/active entities before first spend, plus any
        # entity with a current performance fact.  This is what makes a newly
        # managed campaign visible from its first trustworthy timestamp.
        managed = anchors.get(key, {})
        if (
            not managed
            and _text(entity.get("status")).upper() not in ACTIVE_STATUSES
            and fact is None
        ):
            continue
        enriched = {**entity, **lineage}
        campaign_id = lineage.get("campaign_id")
        results.append(
            evaluate_intraday_entity(
                entity=enriched,
                fact=fact,
                historical=benchmarks.get(key),
                economics=_economics_for(economics_by_campaign, campaign_id),
                management=managed,
                freshness=freshness_by_account.get(
                    _text(entity.get("ad_account_id")), {}
                ),
                now=now,
                risk_tolerance=risk_tolerance,
                expected_refresh_minutes=expected_refresh_minutes,
                global_coverage_complete=global_coverage_complete,
            )
        )

    # A single underlying spend path can appear at campaign, Ad Squad and Ad
    # levels.  Keep every diagnosis visible.  The adaptive decision layer later
    # chooses one scope; this evidence service never marks a write ready.
    ready = [
        row
        for row in results
        if bool((row.get("proposal_candidate") or {}).get("ready_for_proposal"))
    ]
    for row in ready:
        descendants = []
        if row["entity_type"] == "campaign":
            descendants = [
                child
                for child in ready
                if child["entity_type"] in {"ad_squad", "ad"}
                and child.get("campaign_id") == row.get("campaign_id")
            ]
        elif row["entity_type"] == "ad_squad":
            descendants = [
                child
                for child in ready
                if child["entity_type"] == "ad"
                and child.get("ad_squad_id") == row.get("entity_id")
            ]
        if not descendants:
            continue
        candidate = row["proposal_candidate"]
        candidate["ready_for_proposal"] = False
        candidate["requires_hierarchy_choice"] = True
        candidate["overlapping_descendant_ids"] = [
            child["entity_id"] for child in descendants
        ]
        row["recommendation"]["caveats"].append(
            "ancestor_and_descendant_share_spend_path_choose_one_scope"
        )

    priority = {
        "adaptive_review": 0,
        "investigate_efficiency": 2,
        "wait_reporting_completion": 3,
    }
    results.sort(
        key=lambda row: (
            priority.get(row["recommendation"]["code"], 9),
            -float(row["metrics"].get("spend_sar") or 0),
            row["entity_type"],
            row["entity_id"],
        )
    )
    return results


async def monitor_snapchat_intraday_waste(
    db: Any,
    user_id: str,
    *,
    account_id: str | None = None,
    economics_by_campaign: dict[str, dict[str, Any]] | None = None,
    now: datetime | None = None,
    history_days: int = 14,
    risk_tolerance: float = 0.05,
    expected_refresh_minutes: float = 15.0,
) -> dict[str, Any]:
    """Load bounded local facts and return read-only intraday advice."""
    current = _aware(now)
    if not 1 <= history_days <= 90:
        raise ValueError("history_days must be between 1 and 90")
    selected = await _load_selected_accounts(db, user_id)
    accounts = [
        row
        for row in selected
        if not account_id or _text(row.get("ad_account_id")) == _text(account_id)
    ]
    account_ids = [_text(row.get("ad_account_id")) for row in accounts]
    account_ids = [value for value in account_ids if value]
    if not account_ids:
        return {
            "source_mode": SOURCE_MODE,
            "generated_at": current.isoformat(),
            "items": [],
            "summary": {"entities_evaluated": 0, "proposal_candidates": 0},
            "coverage": {"complete": True, "reason": "no_selected_accounts"},
            "provider_write_reached": False,
        }

    entity_query: dict[str, Any] = {
        "user_id": user_id,
        "provider": SNAPCHAT_PROVIDER_ID,
        "ad_account_id": {"$in": account_ids},
        "entity_type": {"$in": list(SUPPORTED_ENTITY_TYPES)},
    }
    entities = await _rows(
        _collection(db, SNAPCHAT_ENTITY_COLLECTION).find(entity_query, {"_id": 0}),
        MAX_ENTITY_ROWS,
    )

    local_dates: dict[str, str] = {}
    for account in accounts:
        timezone_name = _text(account.get("timezone")) or "UTC"
        try:
            local_dates[_text(account.get("ad_account_id"))] = (
                current.astimezone(ZoneInfo(timezone_name)).date().isoformat()
            )
        except ZoneInfoNotFoundError:
            local_dates[_text(account.get("ad_account_id"))] = (
                current.date().isoformat()
            )
    local_history_starts = [
        datetime.fromisoformat(local_date).date() - timedelta(days=history_days)
        for local_date in local_dates.values()
    ]
    earliest = min(local_history_starts).isoformat()
    performance = await _rows(
        _collection(db, SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION).find(
            {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "ad_account_id": {"$in": account_ids},
                "entity_type": {"$in": list(SUPPORTED_ENTITY_TYPES)},
                "date": {"$gte": earliest},
                "action_report_time": "conversion",
            },
            {"_id": 0},
        ),
        MAX_PERFORMANCE_ROWS,
    )
    expected_sources = {
        "campaign": account_local_source_mode("conversion"),
        "ad_squad": adsquad_source_mode("conversion"),
        "ad": ad_source_mode("conversion"),
    }
    performance = [
        row
        for row in performance
        if _text(row.get("source_mode"))
        == expected_sources.get(_text(row.get("entity_type")))
    ]
    current_facts = [
        row
        for row in performance
        if _text(row.get("date")) == local_dates.get(_text(row.get("ad_account_id")))
    ]
    historical_facts = [
        row
        for row in performance
        if _text(row.get("date")) != local_dates.get(_text(row.get("ad_account_id")))
    ]

    proposals = await _rows(
        _collection(db, PROPOSAL_COLLECTION).find(
            {
                "user_id": user_id,
                "account_id": {"$in": account_ids},
                "status": {"$in": sorted(COMPLETED_MANAGEMENT_STATUSES)},
            },
            {"_id": 0},
        ),
        MAX_PROPOSAL_ROWS,
    )
    freshness_rows = await _rows(
        _collection(db, FRESHNESS_COLLECTION).find(
            {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "ad_account_id": {"$in": account_ids},
            },
            {"_id": 0},
        ),
        MAX_FRESHNESS_ROWS,
    )
    freshness_by_account = {
        _text(row.get("ad_account_id")): row for row in freshness_rows
    }
    limits = {
        "entities": len(entities) >= MAX_ENTITY_ROWS,
        "performance": len(performance) >= MAX_PERFORMANCE_ROWS,
        "proposals": len(proposals) >= MAX_PROPOSAL_ROWS,
        "freshness": len(freshness_rows) >= MAX_FRESHNESS_ROWS,
    }
    coverage_complete = not any(limits.values())
    items = evaluate_intraday_facts(
        entities=entities,
        current_facts=current_facts,
        historical_facts=historical_facts,
        proposals=proposals,
        freshness_by_account=freshness_by_account,
        economics_by_campaign=economics_by_campaign,
        now=current,
        risk_tolerance=risk_tolerance,
        expected_refresh_minutes=expected_refresh_minutes,
        global_coverage_complete=coverage_complete,
    )
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        counts[item["recommendation"]["code"]] += 1
    return {
        "source_mode": SOURCE_MODE,
        "generated_at": current.isoformat(),
        "objective": "grow_sales_while_protecting_contribution_profit",
        "items": items,
        "summary": {
            "entities_evaluated": len(items),
            "adaptive_review_signals": sum(
                item.get("proposal_candidate") is not None for item in items
            ),
            "proposal_ready": sum(
                bool((item.get("proposal_candidate") or {}).get("ready_for_proposal"))
                for item in items
            ),
            "by_recommendation": dict(counts),
        },
        "coverage": {
            "complete": coverage_complete,
            "query_limits_reached": limits,
            "entities_loaded": len(entities),
            "performance_rows_loaded": len(performance),
            "management_events_loaded": len(proposals),
            "freshness_rows_loaded": len(freshness_rows),
            "history_days": history_days,
        },
        "policy": {
            "mode": "recommendation_only",
            "fixed_spend_stop_rule": False,
            "risk_tolerance": risk_tolerance,
            "requires_reporting_completeness": True,
            "requires_conversion_delay_maturity": True,
            "statistical_screen_never_authorizes_a_write": True,
        },
        "limitations": [
            "first_spend_minute_requires_a_persisted_observation",
            "current_entity_metrics_are_cumulative_day_totals_not_intraday_history",
            "recommendations_are_probabilistic_and_not_certain_causality",
        ],
        "provider_read_reached": False,
        "provider_write_reached": False,
    }


__all__ = [
    "SOURCE_MODE",
    "build_management_anchors",
    "evaluate_intraday_entity",
    "evaluate_intraday_facts",
    "monitor_snapchat_intraday_waste",
    "poisson_lower_tail",
    "select_effective_target_cpa",
]
