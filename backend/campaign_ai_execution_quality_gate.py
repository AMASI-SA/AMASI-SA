"""Fail-closed quality gate for approved Campaign AI provider writes.

This is the minimal P0-3 Action Gateway contract.  It deliberately does not
replace provider data-quality models or the future P1 DataQualityEnvelope.  It
only proves that the exact recommendation snapshot is still current and that
the existing Meta/Snapchat evidence is complete, fresh, attributable and safe
enough for a deterministic provider connector to execute.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CONTRACT_VERSION = "campaign_ai_execution_quality_p0_3_v1"
INTEGRATIONS_COLLECTION = "mezan_integrations_v2"
ACCOUNTS_COLLECTION = "mezan_integration_accounts_v2"
SYNC_RUNS_COLLECTION = "mezan_integration_sync_runs_v2"
ACCOUNT_COST_SETTINGS_COLLECTION = "mezan_ad_account_cost_settings_v2"
SNAPCHAT_FACT_COLLECTION = "mezan_snapchat_performance_account_day_v3"
META_CAMPAIGN_FACT_COLLECTION = "mezan_meta_campaign_performance_daily_v2"
META_ENTITY_FACT_COLLECTION = "mezan_meta_entity_performance_daily_v1"
SNAPCHAT_ADSQUAD_REFRESH_STATE_COLLECTION = (
    "mezan_snapchat_adsquad_refresh_state_v1"
)
SNAPCHAT_AD_REFRESH_STATE_COLLECTION = "mezan_snapchat_ad_refresh_state_v1"

DEFAULT_MAX_DATA_AGE_MINUTES = 90
DEFAULT_MAX_SNAPSHOT_AGE_HOURS = 5
CONFIRMED_DATA_STATES = {
    "confirmed_data",
    "confirmed_zero",
    "confirmed_no_data",
}
EXECUTABLE_DATA_STATE = "confirmed_data"
EXECUTABLE_ACTIONS = {"pause", "reduce", "scale"}
PROVIDER_IDS = {
    "snapchat": "snapchat_ads",
    "meta": "meta_ads",
}
RUN_TYPES = {
    "snapchat": "analytics_refresh",
    "meta": "meta_reporting_async",
}
RUN_SOURCE_MODES = {
    # Production installs snapchat_freshness_impl_v6 before importing the
    # scheduler, so the scheduler captures this runtime source mode rather than
    # snapchat_account_hourly_refresh.py's import-time v3 default.
    "snapchat": (
        "snapchat_account_hourly_campaign_breakdown_riyadh_"
        "conversion_freshness_nested_v6"
    ),
    "meta": "meta_marketing_reporting_v2",
}
SNAPCHAT_CHILD_REFRESH_CONTRACTS = {
    "ad_group": (
        SNAPCHAT_ADSQUAD_REFRESH_STATE_COLLECTION,
        "snapchat_ads_manager_dual_attribution_ad_squad_active_bounded_total_v4",
    ),
    "ad": (
        SNAPCHAT_AD_REFRESH_STATE_COLLECTION,
        "snapchat_ads_manager_dual_attribution_ad_active_bounded_total_v2",
    ),
}
FACT_SOURCE_MODES = {
    ("snapchat", "campaign"): (
        "snapchat_ads_manager_account_timezone_conversion_v8:account_day_v3"
    ),
    ("snapchat", "ad_group"): (
        "snapchat_ads_manager_account_timezone_conversion_v8:"
        "ad_squad_active_campaign_account_day_bounded_v6"
    ),
    ("snapchat", "ad"): (
        "snapchat_ads_manager_account_timezone_conversion_v8:"
        "ad_active_campaign_account_day_total_v4"
    ),
    ("meta", "campaign"): "meta_campaign_reporting_v2",
    ("meta", "ad_group"): "meta_ai_entity_reporting_v1",
    ("meta", "ad"): "meta_ai_entity_reporting_v1",
}
META_PURCHASE_ACTION_TYPES = {
    "omni_purchase",
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
    "onsite_conversion.purchase",
    "mobile_app_purchase",
}


class ExecutionQualityBlocked(RuntimeError):
    """Raised before a provider write when the P0-3 contract is not proven."""

    def __init__(self, blockers: list[str], evidence: dict[str, Any] | None = None):
        self.blockers = list(dict.fromkeys(blockers))
        self.evidence = evidence or {}
        super().__init__(",".join(self.blockers) or "campaign_execution_quality_blocked")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).astimezone(timezone.utc).isoformat()


def _text(value: Any, *, limit: int = 180) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer(value: Any, default: int = -1) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


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


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None


def _max_data_age_minutes() -> int:
    try:
        configured = int(
            str(
                os.environ.get(
                    "MEZAN_CAMPAIGN_AI_EXECUTION_MAX_DATA_AGE_MINUTES",
                    DEFAULT_MAX_DATA_AGE_MINUTES,
                )
            ).strip()
        )
    except (TypeError, ValueError):
        configured = DEFAULT_MAX_DATA_AGE_MINUTES
    return min(300, max(15, configured))


async def _find_one(
    collection: Any,
    query: dict[str, Any],
    projection: dict[str, Any] | None = None,
    *,
    sort: list[tuple[str, int]] | None = None,
) -> dict[str, Any] | None:
    if sort is None:
        return await collection.find_one(query, projection)
    try:
        return await collection.find_one(query, projection, sort=sort)
    except TypeError:
        # Small test adapters sometimes expose Motor's older two-argument
        # surface.  The production Motor collection always uses the sorted path.
        return await collection.find_one(query, projection)


def _error_codes(values: Any) -> list[str]:
    output: list[str] = []
    for value in values if isinstance(values, list) else []:
        if isinstance(value, dict):
            code = _text(value.get("code") or value.get("error") or value.get("kind"))
            if not code and value:
                code = "provider_error_unclassified"
        else:
            code = _text(value)
        if code:
            output.append(code)
    return list(dict.fromkeys(output))[:20]


def _nested_provider_error_codes(value: Any) -> list[str]:
    output: list[str] = []
    stack: list[tuple[str, Any]] = [("summary", value)]
    while stack:
        path, current = stack.pop()
        if isinstance(current, dict):
            if "errors_count" in current:
                parsed_errors = _integer(current.get("errors_count"))
                if parsed_errors < 0:
                    output.append(f"{path}.errors_count_malformed")
                elif parsed_errors != 0:
                    output.append(f"{path}.errors_count")
            if "errors" in current:
                errors = current.get("errors")
                if not isinstance(errors, list):
                    output.append(f"{path}.errors_malformed")
                elif errors:
                    output.extend(
                        f"{path}.{code}"
                        for code in _error_codes(errors) or ["errors"]
                    )
            if "error_samples" in current:
                samples = current.get("error_samples")
                if not isinstance(samples, list):
                    output.append(f"{path}.error_samples_malformed")
            for key, nested in current.items():
                if isinstance(nested, (dict, list)):
                    stack.append((f"{path}.{key}", nested))
        elif isinstance(current, list):
            for index, nested in enumerate(current[:100]):
                if isinstance(nested, (dict, list)):
                    stack.append((f"{path}[{index}]", nested))
    return list(dict.fromkeys(output))[:20]


def _source_errors(
    provider: str,
    target: dict[str, Any],
    source_context: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
) -> list[str]:
    if source_context is None and baseline:
        return _error_codes(
            (baseline.get("source_validation") or {}).get("errors")
        )
    context = source_context or {}
    output: list[str] = []
    for item in context.get("monitor_errors") or []:
        if not isinstance(item, dict):
            continue
        source = _text(item.get("source"), limit=100)
        relevant = source.startswith(provider)
        if provider == "snapchat" and source == "snapchat_children":
            relevant = target.get("entity_level") in {"ad", "ad_group"}
        if provider == "meta" and source in {"meta_entity_refresh", "meta_children"}:
            relevant = target.get("entity_level") in {"ad", "ad_group"}
        if relevant:
            output.append(_text(item.get("code") or source, limit=120))
    # The Campaign-AI Meta refresh produces only child-entity facts.  Its
    # failure blocks Ad/Ad Group writes without poisoning an independently
    # complete campaign fact from the native reporting run.
    if provider == "meta" and target.get("entity_level") in {"ad", "ad_group"}:
        refresh = context.get("meta_refresh")
        if not isinstance(refresh, dict):
            output.append("meta_entity_refresh_evidence_missing")
        else:
            output.extend(_error_codes(refresh.get("errors")))
            if _integer(refresh.get("errors_count")) != 0 and not refresh.get("errors"):
                output.append("meta_entity_refresh_partial")
            if refresh.get("status") != "complete":
                output.append("meta_entity_refresh_incomplete")
            if refresh.get("pagination_complete") is not True:
                output.append("meta_entity_pagination_unproven")
            if (
                _text(refresh.get("date_from"), limit=10)
                != _text(target.get("source_date_from"), limit=10)
                or _text(refresh.get("date_to"), limit=10)
                != _text(target.get("source_date_to"), limit=10)
            ):
                output.append("meta_entity_refresh_window_mismatch")
    return list(dict.fromkeys(code for code in output if code))[:20]


def _coverage(
    provider: str,
    target: dict[str, Any],
    account: dict[str, Any],
    integration: dict[str, Any],
    run: dict[str, Any],
    snapshot_range: dict[str, Any],
) -> dict[str, Any]:
    if provider == "snapchat":
        raw_sources = {
            "account": account.get("coverage"),
            "integration": integration.get("coverage"),
            "latest_run": (run.get("summary") or {}).get("coverage"),
        }
        source_proofs: dict[str, dict[str, Any]] = {}
        for name, value in raw_sources.items():
            raw = value if isinstance(value, dict) else {}
            expected = _integer(raw.get("expected_requests"))
            completed = _integer(raw.get("completed_requests"))
            data_state = _text(raw.get("data_state"), limit=60)
            source_proofs[name] = {
                "status": raw.get("status") or "missing",
                "data_state": data_state or "unknown_incomplete",
                "expected_requests": expected if expected >= 0 else None,
                "completed_requests": completed if completed >= 0 else None,
                "complete": bool(
                    raw.get("status") == "complete"
                    # A current zero/no-data outcome contradicts the positive-
                    # spend fact that made an entity a Campaign-AI candidate.
                    and data_state == EXECUTABLE_DATA_STATE
                    and expected > 0
                    and completed == expected
                ),
            }
        complete = all(proof["complete"] for proof in source_proofs.values())
        states = {proof["data_state"] for proof in source_proofs.values()}
        consistent = len(states) == 1
        complete = complete and consistent
        return {
            "status": "complete" if complete else "incomplete",
            "data_state": (
                next(iter(states)) if complete and len(states) == 1
                else "unknown_incomplete"
            ),
            "consistent": consistent,
            "sources": source_proofs,
            "source": "snapchat_scheduler_account_integration_run_coverage",
        }

    range_start = _parse_date(snapshot_range.get("from"))
    range_end = _parse_date(snapshot_range.get("to"))
    requested_days = (
        (range_end - range_start).days + 1
        if range_start is not None and range_end is not None and range_end >= range_start
        else 0
    )
    observed_days = _integer(target.get("observed_days"), 0)
    complete = bool(
        target.get("data_complete") is True
        and requested_days > 0
        and observed_days >= requested_days
    )
    return {
        "status": "complete" if complete else "incomplete",
        "data_state": "confirmed_data" if complete else "unknown_incomplete",
        "expected_days": requested_days or None,
        "observed_days": observed_days,
        "source": "meta_entity_observed_dates",
    }


def _freshness(
    account: dict[str, Any],
    integration: dict[str, Any],
    run: dict[str, Any],
    target: dict[str, Any],
    *,
    current: datetime,
) -> dict[str, Any]:
    timestamps = {
        "account_last_sync_at": _parse_datetime(account.get("last_sync_at")),
        "integration_last_sync_at": _parse_datetime(integration.get("last_sync_at")),
        "run_finished_at": _parse_datetime(run.get("finished_at")),
    }
    if target.get("source_observed_at") not in {None, ""}:
        timestamps["target_source_observed_at"] = _parse_datetime(
            target.get("source_observed_at")
        )
    missing = [key for key, value in timestamps.items() if value is None]
    future = [
        key
        for key, value in timestamps.items()
        if value is not None and value > current + timedelta(minutes=5)
    ]
    oldest = min((value for value in timestamps.values() if value is not None), default=None)
    age_minutes = (
        (current - oldest).total_seconds() / 60.0
        if oldest is not None
        else None
    )
    maximum = _max_data_age_minutes()
    fresh = bool(
        not missing
        and not future
        and age_minutes is not None
        and 0 <= age_minutes <= maximum
    )
    return {
        "status": "fresh" if fresh else "stale" if age_minutes is not None else "unknown",
        "observed_at": _iso(oldest) if oldest is not None else None,
        "age_minutes": round(age_minutes, 2) if age_minutes is not None else None,
        "max_age_minutes": maximum,
        "missing_sources": missing,
        "future_sources": future,
        "sources": {
            key: _iso(value) if value is not None else None
            for key, value in timestamps.items()
        },
    }


def _fx_evidence(
    provider: str,
    target: dict[str, Any],
    account: dict[str, Any],
    setting: dict[str, Any] | None,
    entity_facts: dict[str, Any],
) -> dict[str, Any]:
    provider_currency = _text(account.get("currency"), limit=12).upper()
    target_currency = _text(
        target.get("currency_native")
        or target.get("display_currency")
        or entity_facts.get("currency_native"),
        limit=12,
    ).upper()
    currency = target_currency or provider_currency
    mismatch = bool(target_currency and provider_currency and target_currency != provider_currency)
    target_rate = _number(
        target.get("fx_rate_to_sar")
        or target.get("exchange_rate_to_sar")
        or entity_facts.get("fx_rate_to_sar")
    )
    target_source = _text(
        target.get("fx_source") or entity_facts.get("fx_source"), limit=120
    )
    explicit_target_source = _text(target.get("fx_source"), limit=120)
    if (
        currency == "SAR"
        and provider_currency == "SAR"
        and target_currency == "SAR"
        and not mismatch
        and target_rate is not None
        and abs(target_rate - 1.0) <= 0.000001
    ):
        return {
            "status": "documented",
            "currency": "SAR",
            "rate_to_sar": 1.0,
            "source": "provider_currency_identity",
            "configured": True,
        }

    setting = setting if isinstance(setting, dict) else None
    setting_currency = _text((setting or {}).get("native_currency"), limit=12).upper()
    setting_rate = _number((setting or {}).get("exchange_rate_to_sar"))
    rates_match = bool(
        target_rate is not None
        and setting_rate is not None
        and abs(target_rate - setting_rate) <= 0.000001
    )
    configured_documented = bool(
        provider == "snapchat"
        and currency == "USD"
        and provider_currency == "USD"
        and target_currency == "USD"
        and not mismatch
        and setting is not None
        and setting_currency == "USD"
        and setting_rate is not None
        and setting_rate > 0
        and rates_match
    )
    fact_rate = _number(entity_facts.get("fx_rate_to_sar"))
    fact_source = _text(entity_facts.get("fx_source"), limit=120)
    meta_fact_documented = bool(
        provider == "meta"
        and currency == "USD"
        and provider_currency == "USD"
        and target_currency == "USD"
        and not mismatch
        and target_rate is not None
        and target_rate > 0
        and target_source
        and (
            not explicit_target_source
            or explicit_target_source == fact_source
        )
        and fact_rate is not None
        and abs(target_rate - fact_rate) <= 0.000001
        and fact_source == "configured_usd_peg"
        and entity_facts.get("status") == "complete"
    )
    documented = configured_documented or meta_fact_documented
    return {
        "status": "documented" if documented else "unknown",
        "currency": currency or None,
        "rate_to_sar": setting_rate if configured_documented else fact_rate,
        "target_rate_to_sar": target_rate,
        "source": (
            "mezan_ad_account_cost_settings_v2"
            if configured_documented
            else "meta_fact_configured_usd_peg"
            if meta_fact_documented
            else None
        ),
        "configured": setting is not None,
        "currency_mismatch": mismatch,
        "rate_mismatch": not rates_match,
        "updated_at": (setting or {}).get("updated_at"),
    }


def _attribution(
    provider: str,
    target: dict[str, Any],
    account: dict[str, Any],
    snapshot_range: dict[str, Any],
    snapshot_generated_at: Any,
) -> dict[str, Any]:
    source = _text(target.get("provider_result_source"), limit=160)
    action_report_time = _text(target.get("action_report_time"), limit=80)
    source_start = _parse_date(target.get("source_date_from"))
    source_end = _parse_date(target.get("source_date_to"))
    expected_start = _parse_date(snapshot_range.get("from"))
    expected_end = _parse_date(snapshot_range.get("to"))
    source_days = (
        (source_end - source_start).days + 1
        if source_start is not None and source_end is not None and source_end >= source_start
        else 0
    )
    expected_days = (
        (expected_end - expected_start).days + 1
        if expected_start is not None and expected_end is not None and expected_end >= expected_start
        else 0
    )
    allowed_sources = {
        "snapchat": {"snapchat_ads_manager_conversion_reporting"},
        "meta": {"meta_ads_manager_reporting", "meta_ads_api_insights"},
    }
    result_source = _text(target.get("result_source"), limit=80)
    timezone_name = _text(target.get("account_timezone"), limit=100)
    account_timezone = _text(
        account.get("timezone") or account.get("account_timezone"), limit=100
    )
    try:
        ZoneInfo(timezone_name)
        timezone_valid = True
    except (ValueError, ZoneInfoNotFoundError):
        timezone_valid = False
    timezone_bound = bool(
        timezone_valid and account_timezone and account_timezone == timezone_name
    )
    if provider == "snapchat":
        generated_at = _parse_datetime(snapshot_generated_at)
        try:
            local_end = (
                generated_at.astimezone(ZoneInfo(timezone_name)).date()
                if generated_at is not None and timezone_name
                else None
            )
        except (ValueError, ZoneInfoNotFoundError):
            local_end = None
        local_start = (
            local_end - timedelta(days=expected_days - 1)
            if local_end is not None and expected_days > 0
            else None
        )
        window_trusted = bool(
            source_days > 0
            and expected_days > 0
            and source_start == local_start
            and source_end == local_end
        )
    else:
        window_trusted = bool(
            source_days > 0
            and expected_days > 0
            and source_start == expected_start
            and source_end == expected_end
        )
    trusted = bool(
        source in allowed_sources.get(provider, set())
        and action_report_time == "conversion"
        and result_source == "platform"
        and timezone_bound
        and window_trusted
        and provider in PROVIDER_IDS
    )
    return {
        "status": "trusted" if trusted else "untrusted",
        "provider_result_source": source or None,
        "action_report_time": action_report_time or None,
        "result_source": result_source or None,
        "account_timezone": timezone_name or None,
        "provider_account_timezone": account_timezone or None,
        "timezone_bound": timezone_bound,
        "source_window": {
            "from": source_start.isoformat() if source_start else None,
            "to": source_end.isoformat() if source_end else None,
            "days": source_days or None,
        },
        "expected_window_days": expected_days or None,
        "source_window_trusted": window_trusted,
    }


def _pagination(
    provider: str,
    target: dict[str, Any],
    run: dict[str, Any],
    source_context: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    snapshot_complete = target.get("pagination_complete") is True
    if (
        provider == "meta"
        and target.get("entity_level") in {"ad", "ad_group"}
        and source_context is None
        and baseline
    ):
        snapshot_complete = bool(
            snapshot_complete
            and (baseline.get("pagination") or {}).get("status") == "complete"
        )
    elif provider == "meta" and target.get("entity_level") in {"ad", "ad_group"}:
        refresh = (source_context or {}).get("meta_refresh")
        snapshot_complete = bool(
            snapshot_complete
            and isinstance(refresh, dict)
            and refresh.get("pagination_complete") is True
            and not refresh.get("errors")
            and _integer(refresh.get("errors_count")) == 0
        )
    baseline_complete = bool(
        not baseline
        or (baseline.get("pagination") or {}).get("status") == "complete"
    )
    summary = run.get("summary") if isinstance(run.get("summary"), dict) else {}
    run_errors = _integer(summary.get("errors_count"))
    if provider == "snapchat":
        coverage = summary.get("coverage")
        run_complete = bool(
            run.get("status") == "complete"
            and isinstance(coverage, dict)
            and coverage.get("status") == "complete"
            and _integer(coverage.get("expected_requests")) > 0
            and _integer(coverage.get("completed_requests"))
            == _integer(coverage.get("expected_requests"))
        )
        source = "snapchat_report_limits_and_sync_coverage"
    else:
        # Meta's bounded pager raises on an unsafe cursor or page-limit hit;
        # therefore a complete, error-free run is positive pagination evidence.
        run_complete = run.get("status") == "complete" and run_errors == 0
        source = "meta_bounded_paged_get_and_entity_refresh"
    complete = snapshot_complete and baseline_complete and run_complete
    return {
        "status": "complete" if complete else "unresolved",
        "truncated": False if complete else None,
        "source": source,
        "snapshot_source_complete": snapshot_complete,
        "baseline_source_complete": baseline_complete,
        "latest_run_complete": run_complete,
    }


def _date_keys(snapshot_range: dict[str, Any]) -> list[str]:
    start = _parse_date(snapshot_range.get("from"))
    end = _parse_date(snapshot_range.get("to"))
    if start is None or end is None or end < start:
        return []
    values: list[str] = []
    cursor = start
    while cursor <= end:
        values.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return values


async def _cursor_rows(cursor: Any, *, limit: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(limit)
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=limit)
    return [row async for row in cursor]


def _fact_fingerprint(rows: list[dict[str, Any]]) -> str:
    stable = []
    for row in rows:
        stable.append({
            key: row.get(key)
            for key in (
                "date",
                "provider",
                "entity_type",
                "external_id",
                "entity_level",
                "entity_id",
                "campaign_id",
                "spend_native",
                "spend_sar",
                "revenue_native",
                "revenue_sar",
                "purchase_value_native",
                "purchase_value_sar",
                "purchases",
                "impressions",
                "clicks",
                "currency",
                "currency_native",
                "fx_rate_to_sar",
                "fx_source",
                "source_mode",
                "action_report_time",
                "attribution_mode",
                "purchase_action_type",
                "purchase_value_action_type",
                "revenue_action_type",
                "conversion_reporting",
                "metrics",
                "computed",
            )
        })
    encoded = json.dumps(
        sorted(stable, key=lambda item: str(item.get("date") or "")),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


async def _entity_fact_evidence(
    db: Any,
    user_id: str,
    target: dict[str, Any],
    snapshot_range: dict[str, Any],
    run: dict[str, Any],
    *,
    current: datetime,
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    """Prove exact entity/day facts; account-level coverage is insufficient."""
    provider = _text(target.get("provider"), limit=30).lower()
    level = _text(target.get("entity_level"), limit=30).lower()
    entity_id = _text(target.get("entity_id"), limit=120)
    account_id = _text(target.get("account_id"), limit=120)
    expected_dates = _date_keys({
        "from": target.get("source_date_from"),
        "to": target.get("source_date_to"),
    })
    source_collection = _text(target.get("source_fact_collection"), limit=160)
    allowed_collection = {
        ("snapchat", "campaign"): SNAPCHAT_FACT_COLLECTION,
        ("snapchat", "ad_group"): SNAPCHAT_FACT_COLLECTION,
        ("snapchat", "ad"): SNAPCHAT_FACT_COLLECTION,
        ("meta", "campaign"): META_CAMPAIGN_FACT_COLLECTION,
        ("meta", "ad_group"): META_ENTITY_FACT_COLLECTION,
        ("meta", "ad"): META_ENTITY_FACT_COLLECTION,
    }.get((provider, level))
    if (
        not allowed_collection
        or source_collection != allowed_collection
        or not expected_dates
        or not entity_id
        or not account_id
    ):
        return {
            "status": "incomplete",
            "source_collection": source_collection or None,
            "expected_dates": expected_dates,
            "observed_dates": [],
            "fingerprint": None,
            "errors": ["entity_fact_identity_or_source_missing"],
        }

    query: dict[str, Any] = {
        "user_id": user_id,
        "provider": (
            "snapchat_ads"
            if provider == "snapchat"
            else "meta_ads"
            if level == "campaign"
            else "meta"
        ),
        "ad_account_id": account_id,
        "date": {"$gte": expected_dates[0], "$lte": expected_dates[-1]},
    }
    if provider == "snapchat":
        query.update({
            "entity_type": {
                "campaign": "campaign",
                "ad_group": "ad_squad",
                "ad": "ad",
            }[level],
            "external_id": entity_id,
            "action_report_time": "conversion",
        })
    elif level == "campaign":
        query["campaign_id"] = entity_id
    else:
        query.update({"entity_level": level, "entity_id": entity_id})

    # The tenant key is injected by the caller and then verified again below.
    if not user_id:
        return {
            "status": "incomplete",
            "source_collection": source_collection,
            "expected_dates": expected_dates,
            "observed_dates": [],
            "fingerprint": None,
            "errors": ["entity_fact_tenant_missing"],
        }
    try:
        cursor = db[source_collection].find(query, {"_id": 0})
        rows = await _cursor_rows(cursor, limit=max(10, len(expected_dates) * 4))
    except (AttributeError, TypeError):
        rows = []

    child_refresh_state: dict[str, Any] = {}
    child_refresh_observed: datetime | None = None
    child_contract = SNAPCHAT_CHILD_REFRESH_CONTRACTS.get(level)
    if provider == "snapchat" and child_contract:
        state_collection, state_source_mode = child_contract
        child_refresh_state = await _find_one(
            db[state_collection],
            {"user_id": user_id, "ad_account_id": account_id},
            {"_id": 0},
        ) or {}
        state_coverage = (
            child_refresh_state.get("coverage")
            if isinstance(child_refresh_state.get("coverage"), dict)
            else {}
        )
        child_refresh_observed = _parse_datetime(
            child_refresh_state.get("last_success_at")
        )
        state_expected = _integer(state_coverage.get("expected_requests"))
        state_completed = _integer(state_coverage.get("completed_requests"))
        state_fresh = bool(
            child_refresh_observed is not None
            and child_refresh_observed <= current + timedelta(minutes=5)
            and current - child_refresh_observed <= timedelta(minutes=15)
        )
        if not (
            _text(child_refresh_state.get("source_mode"), limit=180)
            == state_source_mode
            and state_coverage.get("status") == "complete"
            and state_coverage.get("data_state") == EXECUTABLE_DATA_STATE
            and state_expected > 0
            and state_completed == state_expected
            and _integer(child_refresh_state.get("errors_count")) == 0
            and child_refresh_state.get("campaign_limit_reached") is not True
            and state_fresh
        ):
            child_refresh_state = {}
            child_refresh_observed = None

    valid_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    expected_entity_type = {
        "campaign": "campaign",
        "ad_group": "ad_squad",
        "ad": "ad",
    }.get(level)
    run_start = _parse_datetime(run.get("started_at"))
    run_finished = _parse_datetime(run.get("finished_at"))
    summary = run.get("summary") if isinstance(run.get("summary"), dict) else {}
    run_dates = set(_date_keys({
        "from": summary.get("date_from"),
        "to": summary.get("date_to"),
    }))
    expected_set = set(expected_dates)
    if not run_dates:
        errors.append("provider_run_window_missing")
    elif (
        provider == "meta"
        and level == "campaign"
        and not expected_set.issubset(run_dates)
    ):
        # A recent two-day refresh cannot prove a three-day recommendation
        # window.  Requiring every source date prevents one older fact from
        # surviving beside two fresh dates and authorizing a provider write.
        errors.append("provider_run_window_incomplete")
    target_timezone = _text(target.get("account_timezone"), limit=100)
    target_source_mode = _text(target.get("source_mode"), limit=180)
    target_currency = _text(target.get("currency_native"), limit=12).upper()
    expected_fact_source_mode = FACT_SOURCE_MODES.get((provider, level))
    if target_source_mode != expected_fact_source_mode:
        errors.append("entity_fact_source_mode_untrusted")
    expected_fact_provider = (
        "snapchat_ads"
        if provider == "snapchat"
        else "meta_ads"
        if level == "campaign"
        else "meta"
    )

    for row in rows:
        if not isinstance(row, dict):
            errors.append("entity_fact_malformed")
            continue
        exact = bool(
            _text(row.get("user_id"), limit=160) == _text(user_id, limit=160)
            and _text(row.get("provider"), limit=30) == expected_fact_provider
            and _text(row.get("ad_account_id"), limit=120) == account_id
            and _text(row.get("date"), limit=10) in expected_dates
        )
        if provider == "snapchat":
            exact = exact and bool(
                _text(row.get("entity_type"), limit=30) == expected_entity_type
                and _text(row.get("external_id"), limit=120) == entity_id
            )
        elif level == "campaign":
            exact = exact and _text(row.get("campaign_id"), limit=120) == entity_id
        else:
            exact = exact and bool(
                _text(row.get("entity_level"), limit=30) == level
                and _text(row.get("entity_id"), limit=120) == entity_id
            )
        if not exact:
            errors.append("entity_fact_identity_mismatch")
            continue

        observed = _parse_datetime(row.get("updated_at") or row.get("observed_at"))
        if observed is None or observed > current + timedelta(minutes=5):
            errors.append("entity_fact_observation_invalid")
        if provider == "snapchat" and level in SNAPCHAT_CHILD_REFRESH_CONTRACTS:
            if (
                child_refresh_observed is None
                or observed is None
                or abs((observed - child_refresh_observed).total_seconds()) > 120
            ):
                errors.append("entity_fact_not_bound_to_child_refresh")
        elif provider == "snapchat" or (
            provider == "meta" and level == "campaign"
        ):
            if (
                (provider == "meta" and row.get("date") not in run_dates)
                or run_start is None
                or run_finished is None
                or observed is None
                or observed < run_start
                or observed > run_finished + timedelta(minutes=2)
            ):
                errors.append("entity_fact_not_refreshed_in_latest_run")

        if provider == "snapchat":
            conversion = row.get("conversion_reporting")
            conversion = conversion if isinstance(conversion, dict) else {}
            provider_window_start = _parse_datetime(row.get("provider_window_start"))
            provider_window_end = _parse_datetime(row.get("provider_window_end"))
            row_date = _parse_date(row.get("date"))
            exact_provider_window = False
            try:
                account_zone = ZoneInfo(target_timezone)
                local_window_date = (
                    provider_window_start.astimezone(account_zone).date()
                    if provider_window_start is not None
                    else None
                )
                local_today = current.astimezone(account_zone).date()
                expected_start = (
                    datetime.combine(row_date, datetime.min.time(), tzinfo=account_zone)
                    if row_date is not None
                    else None
                )
                expected_end = (
                    datetime.combine(
                        row_date + timedelta(days=1),
                        datetime.min.time(),
                        tzinfo=account_zone,
                    )
                    if row_date is not None
                    else None
                )
                starts_at_midnight = bool(
                    provider_window_start is not None
                    and expected_start is not None
                    and abs(
                        (
                            provider_window_start
                            - expected_start.astimezone(timezone.utc)
                        ).total_seconds()
                    ) <= 1
                )
                if row_date is not None and row_date < local_today:
                    exact_provider_window = bool(
                        starts_at_midnight
                        and provider_window_end is not None
                        and expected_end is not None
                        and abs(
                            (
                                provider_window_end
                                - expected_end.astimezone(timezone.utc)
                            ).total_seconds()
                        ) <= 1
                    )
                elif row_date == local_today:
                    if level in SNAPCHAT_CHILD_REFRESH_CONTRACTS:
                        # TOTAL child reports request the whole account-local
                        # calendar day even while it is open.  Recency is
                        # proven separately by the durable child refresh state.
                        exact_provider_window = bool(
                            starts_at_midnight
                            and provider_window_end is not None
                            and expected_end is not None
                            and abs(
                                (
                                    provider_window_end
                                    - expected_end.astimezone(timezone.utc)
                                ).total_seconds()
                            ) <= 1
                        )
                    else:
                        exact_provider_window = bool(
                            starts_at_midnight
                            and provider_window_end is not None
                            and provider_window_start is not None
                            and provider_window_end > provider_window_start
                            and provider_window_end
                            >= current
                            - timedelta(minutes=_max_data_age_minutes())
                            and provider_window_end
                            <= current + timedelta(minutes=65)
                        )
            except (ValueError, ZoneInfoNotFoundError):
                local_window_date = None
                exact_provider_window = False
            if (
                row.get("action_report_time") != "conversion"
                or conversion.get("action_report_time") != "conversion"
                or conversion.get("metric") != "conversion_purchases"
                or conversion.get("source_types") != ["total"]
                or conversion.get("swipe_up_attribution_window") != "28_DAY"
                or conversion.get("view_attribution_window") != "7_DAY"
                or _text(row.get("source_mode"), limit=180) != target_source_mode
                or _text(row.get("account_timezone"), limit=100) != target_timezone
                or _text(row.get("currency"), limit=12).upper() != target_currency
                or local_window_date is None
                or local_window_date.isoformat() != _text(row.get("date"), limit=10)
                or not exact_provider_window
            ):
                errors.append("entity_fact_source_contract_mismatch")
        elif level == "campaign":
            if (
                row.get("source_mode") != "meta_campaign_reporting_v2"
                or row.get("attribution_mode") != "account_setting+unified"
                or _text(row.get("currency_native"), limit=12).upper() != target_currency
                or _text(row.get("date_start"), limit=10)
                != _text(row.get("date"), limit=10)
                or _text(row.get("date_stop"), limit=10)
                != _text(row.get("date"), limit=10)
            ):
                errors.append("entity_fact_source_contract_mismatch")
        else:
            if (
                row.get("source_mode") != "meta_ai_entity_reporting_v1"
                or row.get("action_report_time") != "conversion"
                or _text(row.get("account_timezone"), limit=100) != target_timezone
                or _text(row.get("currency_native"), limit=12).upper() != target_currency
            ):
                errors.append("entity_fact_source_contract_mismatch")
        valid_rows.append(row)

    observed_dates = sorted({
        _text(row.get("date"), limit=10) for row in valid_rows if row.get("date")
    })
    fingerprint = _fact_fingerprint(valid_rows) if valid_rows else None
    observation_times = [
        _parse_datetime(row.get("updated_at") or row.get("observed_at"))
        for row in valid_rows
    ]
    valid_observation_times = [value for value in observation_times if value is not None]
    baseline_fingerprint = _text(
        ((baseline or {}).get("entity_facts") or {}).get("fingerprint"), limit=128
    )
    if baseline_fingerprint and fingerprint != baseline_fingerprint:
        errors.append("entity_fact_fingerprint_changed")
    if observed_dates != expected_dates:
        errors.append("entity_fact_date_coverage_incomplete")
    if len(valid_rows) != len(expected_dates):
        errors.append("entity_fact_duplicate_or_missing_dates")
    currencies = {
        _text(row.get("currency_native") or row.get("currency"), limit=12).upper()
        for row in valid_rows
        if _text(row.get("currency_native") or row.get("currency"), limit=12)
    }
    rates = {
        _number(row.get("fx_rate_to_sar"))
        for row in valid_rows
        if _number(row.get("fx_rate_to_sar")) is not None
    }
    fx_sources = {
        _text(row.get("fx_source"), limit=120)
        for row in valid_rows
        if _text(row.get("fx_source"), limit=120)
    }
    if len(currencies) != 1:
        errors.append("entity_fact_currency_incomplete")
    if provider == "meta" and (
        len(rates) != 1
        or len(fx_sources) != 1
        or any(_number(row.get("fx_rate_to_sar")) is None for row in valid_rows)
        or any(not _text(row.get("fx_source"), limit=120) for row in valid_rows)
    ):
        errors.append("entity_fact_fx_incomplete")
    proof_rate = _number(
        target.get("fx_rate_to_sar")
        or target.get("exchange_rate_to_sar")
        or (next(iter(rates)) if len(rates) == 1 else None)
    )
    if proof_rate is None or proof_rate <= 0:
        errors.append("entity_fact_fx_rate_missing")
    else:
        required_value_pair = (
            ("revenue_native", "revenue_sar")
            if provider == "meta" and level in {"ad", "ad_group"}
            else ("purchase_value_native", "purchase_value_sar")
        )
        for row in valid_rows:
            for native_key, sar_key in (
                ("spend_native", "spend_sar"),
                required_value_pair,
            ):
                native = _number(row.get(native_key))
                sar = _number(row.get(sar_key))
                if native is None or sar is None:
                    errors.append("entity_fact_fx_amount_missing")
                    continue
                if native < 0 or sar < 0:
                    errors.append("entity_fact_metric_domain_invalid")
                    continue
                tolerance = max(0.02, abs(native * proof_rate) * 0.00001)
                if abs(sar - native * proof_rate) > tolerance:
                    errors.append("entity_fact_fx_conversion_mismatch")
            purchases = _number(row.get("purchases"))
            if purchases is None or purchases < 0:
                errors.append("entity_fact_metric_domain_invalid")
            for metric_key in ("impressions", "clicks"):
                metric = _number(row.get(metric_key))
                if metric is not None and metric < 0:
                    errors.append("entity_fact_metric_domain_invalid")
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            for metric_key in ("spend", "impressions", "swipes", "conversion_purchases"):
                metric = _number(metrics.get(metric_key))
                if metric is not None and metric < 0:
                    errors.append("entity_fact_metric_domain_invalid")
            row_rate = _number(row.get("fx_rate_to_sar"))
            row_source = _text(row.get("fx_source"), limit=120)
            if provider == "meta":
                if (
                    row_rate is None
                    or abs(row_rate - proof_rate) > 0.000001
                    or row_source not in {"implicit_sar", "configured_usd_peg"}
                ):
                    errors.append("entity_fact_fx_provenance_mismatch")
            elif row_rate is not None and abs(row_rate - proof_rate) > 0.000001:
                errors.append("entity_fact_fx_provenance_mismatch")

            if provider == "meta":
                purchase_action = _text(row.get("purchase_action_type"), limit=120)
                value_action = _text(
                    row.get(
                        "revenue_action_type"
                        if level in {"ad", "ad_group"}
                        else "purchase_value_action_type"
                    ),
                    limit=120,
                )
                value_native = _number(row.get(required_value_pair[0]))
                if purchases and purchase_action not in META_PURCHASE_ACTION_TYPES:
                    errors.append("entity_fact_purchase_attribution_missing")
                if value_native and value_action not in META_PURCHASE_ACTION_TYPES:
                    errors.append("entity_fact_value_attribution_missing")

    def row_number(row: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            if "." in key:
                outer, inner = key.split(".", 1)
                nested = row.get(outer)
                value = nested.get(inner) if isinstance(nested, dict) else None
            else:
                value = row.get(key)
            parsed = _number(value)
            if parsed is not None:
                return parsed
        return None

    revenue_fact_key = (
        "revenue_sar"
        if provider == "meta" and level in {"ad", "ad_group"}
        else "purchase_value_sar"
    )
    aggregate_pairs = {
        "spend_sar": ("spend_sar",),
        "revenue_sar": (revenue_fact_key,),
        "purchases": ("purchases", "metrics.conversion_purchases"),
        "impressions": ("impressions", "metrics.impressions"),
        "clicks": ("clicks", "metrics.swipes"),
    }
    aggregates: dict[str, float | None] = {}
    for target_key, fact_keys in aggregate_pairs.items():
        values = [row_number(row, *fact_keys) for row in valid_rows]
        aggregates[target_key] = (
            sum(value for value in values if value is not None)
            if values and all(value is not None for value in values)
            else None
        )
        expected_value = _number(target.get(target_key))
        actual_value = aggregates[target_key]
        tolerance = 0.05 if target_key in {"spend_sar", "revenue_sar"} else 0.000001
        if (
            expected_value is None
            or expected_value < 0
            or actual_value is None
            or actual_value < 0
            or abs(expected_value - actual_value) > tolerance
        ):
            errors.append(f"entity_fact_{target_key}_mismatch")
    errors = list(dict.fromkeys(errors))
    return {
        "status": "complete" if not errors else "incomplete",
        "source_collection": source_collection,
        "expected_dates": expected_dates,
        "observed_dates": observed_dates,
        "rows": len(valid_rows),
        "fingerprint": fingerprint,
        "snapshot_fingerprint": baseline_fingerprint or None,
        "latest_observed_at": (
            _iso(max(valid_observation_times)) if valid_observation_times else None
        ),
        "oldest_observed_at": (
            _iso(min(valid_observation_times)) if valid_observation_times else None
        ),
        "currency_native": next(iter(currencies)) if len(currencies) == 1 else None,
        "fx_rate_to_sar": next(iter(rates)) if len(rates) == 1 else None,
        "fx_source": next(iter(fx_sources)) if len(fx_sources) == 1 else None,
        "aggregates": aggregates,
        "child_refresh": (
            {
                "status": "complete",
                "source_mode": child_refresh_state.get("source_mode"),
                "last_success_at": child_refresh_state.get("last_success_at"),
                "coverage": child_refresh_state.get("coverage"),
            }
            if child_refresh_state
            else None
        ),
        "errors": errors,
    }


async def collect_execution_quality_evidence(
    db: Any,
    user_id: str,
    target: dict[str, Any],
    *,
    snapshot_generated_at: Any,
    snapshot_range: dict[str, Any],
    now: Callable[[], datetime] = _utcnow,
    source_context: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect the small, shared Meta/Snapchat execution-quality contract."""
    current = now().astimezone(timezone.utc)
    bound_target = {**target, "user_id": user_id}
    provider = _text(bound_target.get("provider"), limit=30).lower()
    provider_id = PROVIDER_IDS.get(provider)
    account_id = _text(bound_target.get("account_id"), limit=120)

    integration: dict[str, Any] = {}
    account: dict[str, Any] = {}
    run: dict[str, Any] = {}
    setting: dict[str, Any] | None = None
    if provider_id and account_id:
        integration = await _find_one(
            db[INTEGRATIONS_COLLECTION],
            {"user_id": user_id, "provider": provider_id},
            {"_id": 0},
        ) or {}
        account = await _find_one(
            db[ACCOUNTS_COLLECTION],
            {
                "user_id": user_id,
                "provider": provider_id,
                "external_account_id": account_id,
            },
            {"_id": 0},
        ) or {}
        if not account:
            account = await _find_one(
                db[ACCOUNTS_COLLECTION],
                {
                    "user_id": user_id,
                    "provider": provider_id,
                    "ad_account_id": account_id,
                },
                {"_id": 0},
            ) or {}
        run = await _find_one(
            db[SYNC_RUNS_COLLECTION],
            {
                "user_id": user_id,
                "provider": provider_id,
                "run_type": RUN_TYPES.get(provider),
            },
            {"_id": 0},
            sort=[("started_at", -1), ("created_at", -1)],
        ) or {}
        setting = await _find_one(
            db[ACCOUNT_COST_SETTINGS_COLLECTION],
            {
                "user_id": user_id,
                "provider": provider_id,
                "external_account_id": account_id,
            },
            {"_id": 0},
        )

    summary = run.get("summary") if isinstance(run.get("summary"), dict) else {}
    run_started_at = _parse_datetime(run.get("started_at"))
    run_finished_at = _parse_datetime(run.get("finished_at"))
    run_chronology_valid = bool(
        run_started_at is not None
        and run_finished_at is not None
        and run_started_at <= run_finished_at
        and run_finished_at <= current + timedelta(minutes=5)
    )
    run_error_codes = list(dict.fromkeys([
        *_error_codes(summary.get("error_samples")),
        *_nested_provider_error_codes(summary),
    ]))
    if _integer(summary.get("errors_count")) != 0 and not run_error_codes:
        run_error_codes.append("provider_sync_errors_present")
    run_error = run.get("error")
    if isinstance(run_error, dict) and run_error:
        run_error_codes.extend(_error_codes([run_error]) or ["run_error_present"])
    elif run_error not in (None, ""):
        run_error_codes.append("run_error_malformed")
    source_error_codes = _source_errors(
        provider, bound_target, source_context, baseline
    )
    all_error_codes = list(dict.fromkeys([*source_error_codes, *run_error_codes]))[:20]

    coverage = _coverage(
        provider, bound_target, account, integration, run, snapshot_range
    )
    target_complete = bound_target.get("data_complete") is True
    canonical_source_mode = RUN_SOURCE_MODES.get(provider)
    account_sync = _parse_datetime(account.get("last_sync_at"))
    integration_sync = _parse_datetime(integration.get("last_sync_at"))

    def within_run(value: datetime | None) -> bool:
        return bool(
            value is not None
            and run_started_at is not None
            and run_finished_at is not None
            and run_started_at <= value <= run_finished_at + timedelta(minutes=2)
        )

    integration_complete = bool(
        integration.get("data_quality") == "complete"
        and _text(integration.get("source_mode"), limit=180)
        == canonical_source_mode
        and within_run(integration_sync)
    )
    if provider == "snapchat":
        account_complete = bool(
            account.get("data_quality") == "complete"
            and _text(account.get("source_mode"), limit=180)
            == canonical_source_mode
            and within_run(account_sync)
        )
    else:
        account_complete = bool(
            _text(account.get("source_mode"), limit=180)
            == canonical_source_mode
            and within_run(account_sync)
            and _integer(account.get("performance_rows_saved"), 0) > 0
        )
    accounts_attempted = _integer(summary.get("accounts_attempted"))
    accounts_complete = _integer(summary.get("accounts_complete"))
    account_run_rows = summary.get("account_provider_calls")
    account_run_rows = account_run_rows if isinstance(account_run_rows, list) else []
    summary_account_listed = any(
        isinstance(item, dict)
        and _text(item.get("ad_account_id"), limit=120) == account_id
        and _integer(item.get("provider_calls"), 0) > 0
        for item in account_run_rows
    )
    # P0-1 intentionally bounds account_provider_calls in the persisted run
    # summary.  Exact account membership is therefore proven by the canonical
    # writer's source mode plus both account timestamps inside this exact run;
    # OAuth/native projections use a different source mode and cannot satisfy
    # the conjunction.
    run_account_bound = bool(account_complete and integration_complete)
    run_complete = bool(
        run.get("status") == "complete"
        and run.get("source_mode") == canonical_source_mode
        and accounts_attempted > 0
        and accounts_complete == accounts_attempted
    )
    quality_complete = bool(
        provider_id
        and account_id
        and target_complete
        and integration_complete
        and account_complete
        and run_complete
    )

    entity_facts = await _entity_fact_evidence(
        db,
        user_id,
        bound_target,
        snapshot_range,
        run,
        current=current,
        baseline=baseline,
    )
    baseline_fingerprint = _text(
        ((baseline or {}).get("entity_facts") or {}).get("fingerprint"), limit=128
    )
    current_fingerprint = _text(entity_facts.get("fingerprint"), limit=128)
    revision_unchanged = bool(
        not baseline_fingerprint or current_fingerprint == baseline_fingerprint
    )

    evidence = {
        "contract_version": CONTRACT_VERSION,
        "captured_at": _iso(current),
        "provider": provider or None,
        "provider_id": provider_id,
        "account_id": account_id or None,
        "entity_level": bound_target.get("entity_level"),
        "entity_id": bound_target.get("entity_id"),
        "snapshot_generated_at": snapshot_generated_at,
        "data_quality": "complete" if quality_complete else "incomplete",
        "data_state": coverage.get("data_state") or "unknown_incomplete",
        "coverage": coverage,
        "freshness": _freshness(
            account,
            integration,
            run,
            {
                **bound_target,
                # Freshness is bounded by the oldest fact that influenced the
                # recommendation, never by the newest row in a mixed window.
                "source_observed_at": entity_facts.get("oldest_observed_at"),
            },
            current=current,
        ),
        "provider_sync": {
            "status": run.get("status") or "missing",
            "source_mode": run.get("source_mode"),
            "run_id": run.get("run_id"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "chronology_valid": run_chronology_valid,
            "errors_count": len(all_error_codes),
            "error_codes": all_error_codes,
            "accounts_attempted": accounts_attempted,
            "accounts_complete": accounts_complete,
            "account_bound": run_account_bound,
            "summary_account_listed": summary_account_listed,
        },
        "pagination": _pagination(
            provider, bound_target, run, source_context, baseline
        ),
        "fx": _fx_evidence(
            provider, bound_target, account, setting, entity_facts
        ),
        "attribution": _attribution(
            provider,
            bound_target,
            account,
            snapshot_range,
            snapshot_generated_at,
        ),
        "entity_facts": entity_facts,
        "source_validation": {
            "status": "complete" if not source_error_codes else "incomplete",
            "errors": source_error_codes,
        },
        "source_revision": {
            "status": "unchanged" if revision_unchanged else "changed",
            "snapshot_fingerprint": baseline_fingerprint or None,
            "current_fingerprint": current_fingerprint or None,
        },
    }
    decision = evaluate_execution_quality(evidence)
    evidence["status"] = decision["status"]
    evidence["blockers"] = decision["blockers"]
    return evidence


def evaluate_execution_quality(
    evidence: dict[str, Any] | None,
    *,
    action: str | None = None,
) -> dict[str, Any]:
    """Evaluate the shared contract; missing/unknown evidence always blocks."""
    value = evidence if isinstance(evidence, dict) else {}
    blockers: list[str] = []
    if value.get("contract_version") != CONTRACT_VERSION:
        blockers.append("execution_quality_contract_missing")
    if action is not None and action not in EXECUTABLE_ACTIONS:
        blockers.append("execution_action_not_allowlisted")
    if value.get("data_quality") != "complete":
        blockers.append("execution_data_quality_incomplete")
    coverage = value.get("coverage") if isinstance(value.get("coverage"), dict) else {}
    if coverage.get("status") != "complete":
        blockers.append("execution_coverage_incomplete")
    if value.get("data_state") != EXECUTABLE_DATA_STATE:
        blockers.append("execution_data_state_unknown")
    freshness = value.get("freshness") if isinstance(value.get("freshness"), dict) else {}
    if freshness.get("status") != "fresh":
        blockers.append("execution_data_stale")
    provider_sync = (
        value.get("provider_sync") if isinstance(value.get("provider_sync"), dict) else {}
    )
    if (
        provider_sync.get("status") != "complete"
        or provider_sync.get("source_mode")
        != RUN_SOURCE_MODES.get(str(value.get("provider") or ""))
        or _integer(provider_sync.get("errors_count")) != 0
        or provider_sync.get("error_codes")
        or _integer(provider_sync.get("accounts_attempted")) <= 0
        or _integer(provider_sync.get("accounts_complete"))
        != _integer(provider_sync.get("accounts_attempted"))
        or provider_sync.get("account_bound") is not True
        or provider_sync.get("chronology_valid") is not True
    ):
        blockers.append("execution_provider_sync_errors")
    pagination = value.get("pagination") if isinstance(value.get("pagination"), dict) else {}
    if pagination.get("status") != "complete" or pagination.get("truncated") is not False:
        blockers.append("execution_pagination_unresolved")
    fx = value.get("fx") if isinstance(value.get("fx"), dict) else {}
    if fx.get("status") != "documented":
        blockers.append("execution_fx_unknown")
    attribution = (
        value.get("attribution") if isinstance(value.get("attribution"), dict) else {}
    )
    if attribution.get("status") != "trusted":
        blockers.append("execution_attribution_untrusted")
    if attribution.get("source_window_trusted") is not True:
        blockers.append("execution_source_window_untrusted")
    source_validation = (
        value.get("source_validation")
        if isinstance(value.get("source_validation"), dict)
        else {}
    )
    if source_validation.get("status") != "complete" or source_validation.get("errors"):
        blockers.append("execution_source_evidence_incomplete")
    entity_facts = (
        value.get("entity_facts") if isinstance(value.get("entity_facts"), dict) else {}
    )
    if entity_facts.get("status") != "complete" or entity_facts.get("errors"):
        blockers.append("execution_entity_facts_incomplete")
    source_revision = (
        value.get("source_revision")
        if isinstance(value.get("source_revision"), dict)
        else {}
    )
    if source_revision.get("status") != "unchanged":
        blockers.append("execution_source_revision_changed")
    blockers = list(dict.fromkeys(blockers))
    return {
        "allowed": not blockers,
        "status": "complete" if not blockers else "blocked",
        "blockers": blockers,
    }


def require_execution_quality(
    evidence: dict[str, Any] | None,
    *,
    action: str,
) -> dict[str, Any]:
    decision = evaluate_execution_quality(evidence, action=action)
    if not decision["allowed"]:
        raise ExecutionQualityBlocked(decision["blockers"], evidence)
    return decision


def execution_snapshot_digest(
    snapshot_id: Any,
    recommendation: dict[str, Any],
    target: dict[str, Any],
) -> str:
    """Fingerprint exactly what the owner approved, including quality evidence."""
    stable = {
        "snapshot_id": _text(snapshot_id, limit=160),
        "recommendation": {
            key: recommendation.get(key)
            for key in (
                "recommendation_id",
                "provider",
                "entity_level",
                "entity_id",
                "account_id",
                "action",
                "change_percent",
                "approval_available",
            )
        },
        "target": target,
    }
    encoded = json.dumps(
        stable,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


async def preflight_approved_execution(
    db: Any,
    *,
    recommendation_collection: str,
    user_id: str,
    snapshot_id: str,
    recommendation_id: str,
    expected_digest: str | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Re-read the latest snapshot and current sync quality before any write."""
    latest = await _find_one(
        db[recommendation_collection],
        {"user_id": user_id},
        {"_id": 0},
        sort=[("generated_at", -1)],
    )
    if not latest or latest.get("snapshot_id") != snapshot_id:
        raise ExecutionQualityBlocked(["execution_snapshot_drift"])
    generated_at = _parse_datetime(latest.get("generated_at"))
    current = now().astimezone(timezone.utc)
    if (
        generated_at is None
        or generated_at > current + timedelta(minutes=5)
        or current - generated_at > timedelta(hours=DEFAULT_MAX_SNAPSHOT_AGE_HOURS)
    ):
        raise ExecutionQualityBlocked(["execution_snapshot_stale"])
    recommendation = next(
        (
            item
            for item in latest.get("recommendations") or []
            if isinstance(item, dict)
            and item.get("recommendation_id") == recommendation_id
        ),
        None,
    )
    target = (latest.get("execution_targets") or {}).get(recommendation_id)
    if isinstance(recommendation, dict) and generated_at is not None:
        from campaign_ai_time_window_quality import snapshot_max_age_minutes
        max_age_minutes = snapshot_max_age_minutes(recommendation.get("action"))
        snapshot_age_minutes = (current - generated_at).total_seconds() / 60.0
        if snapshot_age_minutes > max_age_minutes:
            raise ExecutionQualityBlocked([
                "execution_scale_snapshot_stale"
                if str(recommendation.get("action") or "") == "scale"
                else "execution_snapshot_stale"
            ], {
                "snapshot_age_minutes": round(snapshot_age_minutes, 2),
                "max_age_minutes": max_age_minutes,
                "action": recommendation.get("action"),
            })
    if (
        not isinstance(recommendation, dict)
        or not isinstance(target, dict)
        or recommendation.get("approval_available") is not True
    ):
        raise ExecutionQualityBlocked(["execution_snapshot_target_unavailable"])
    baseline = target.get("execution_quality")
    if (
        not isinstance(baseline, dict)
        or baseline.get("contract_version") != CONTRACT_VERSION
        or baseline.get("status") != "complete"
        or baseline.get("blockers")
        or not _text(
            (baseline.get("entity_facts") or {}).get("fingerprint"), limit=128
        )
    ):
        raise ExecutionQualityBlocked(["execution_snapshot_quality_proof_missing"])
    identity_fields = ("provider", "entity_level", "entity_id", "account_id")
    if any(
        _text(recommendation.get(key), limit=160)
        != _text(target.get(key), limit=160)
        for key in identity_fields
    ) or any(
        _text(baseline.get(key), limit=160)
        != _text(target.get(key), limit=160)
        for key in identity_fields
    ):
        raise ExecutionQualityBlocked(["execution_snapshot_identity_mismatch"])
    digest = execution_snapshot_digest(snapshot_id, recommendation, target)
    if expected_digest is not None and digest != expected_digest:
        raise ExecutionQualityBlocked(["execution_snapshot_drift"])
    evidence = await collect_execution_quality_evidence(
        db,
        user_id,
        target,
        snapshot_generated_at=latest.get("generated_at"),
        snapshot_range=latest.get("range") or {},
        now=now,
        baseline=baseline,
    )
    require_execution_quality(evidence, action=str(recommendation.get("action") or ""))
    return {
        "snapshot": latest,
        "recommendation": recommendation,
        "target": target,
        "execution_quality": evidence,
        "snapshot_digest": digest,
    }


def provider_state_drift_blockers(
    provider: str,
    recommendation: dict[str, Any],
    target: dict[str, Any],
    current_state: dict[str, Any] | None,
) -> list[str]:
    """Compare provider preflight state with the exact approved target."""
    current = current_state if isinstance(current_state, dict) else {}
    blockers: list[str] = []
    expected_id = _text(target.get("entity_id"), limit=120)
    current_id = _text(current.get("id") or current.get("entity_id"), limit=120)
    if not expected_id or current_id != expected_id:
        blockers.append("execution_provider_entity_drift")
    statuses = [
        _text(current.get(key), limit=60).upper()
        for key in ("status", "effective_status")
        if _text(current.get(key), limit=60)
    ]
    if not statuses or any(status not in {"ACTIVE", "ENABLED"} for status in statuses):
        blockers.append("execution_provider_status_drift")
    expected_native = _number(target.get("current_daily_budget_native"))
    action = _text(recommendation.get("action"), limit=30).lower()
    if action in {"reduce", "scale"} and (
        expected_native is None or expected_native <= 0
    ):
        blockers.append("execution_provider_budget_unproven")
    elif expected_native is not None:
        if provider == "snapchat":
            current_native = _number(current.get("daily_budget_micro"))
            current_native = current_native / 1_000_000 if current_native is not None else None
        elif provider == "meta":
            current_native = _number(current.get("daily_budget"))
            current_native = current_native / 100 if current_native is not None else None
        else:
            current_native = None
        if (
            current_native is None
            or abs(expected_native - current_native) > 0.000001
        ):
            blockers.append("execution_provider_budget_drift")
    return blockers


def require_provider_state_unchanged(
    provider: str,
    recommendation: dict[str, Any],
    target: dict[str, Any],
    current_state: dict[str, Any] | None,
) -> None:
    blockers = provider_state_drift_blockers(
        provider, recommendation, target, current_state
    )
    if blockers:
        raise ExecutionQualityBlocked(blockers)


__all__ = [
    "CONTRACT_VERSION",
    "ExecutionQualityBlocked",
    "collect_execution_quality_evidence",
    "evaluate_execution_quality",
    "execution_snapshot_digest",
    "preflight_approved_execution",
    "provider_state_drift_blockers",
    "require_execution_quality",
    "require_provider_state_unchanged",
]
