"""Independent Snapchat provider-total reads used only for reconciliation.

The Dashboard uses an Asia/Riyadh calendar day while the Snapchat page uses the
ad-account calendar. A single UTC total cannot prove both views when those
windows differ. This module therefore reads the requested Dashboard window and,
when necessary, a second account-local window for the same Riyadh date.

Snapchat's ad-account stats endpoint does not accept ``granularity=TOTAL`` for
all account/API combinations. Reconciliation therefore prefers TOTAL, but when
Snapchat explicitly rejects that request with HTTP 400 it performs a fresh
provider-side HOUR read for the exact same window and sums campaign spend. The
fallback is still independent from the persisted V2 facts/projections and is
used only as reconciliation evidence.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .client import (
    HTTP_TIMEOUT_SECONDS,
    SNAPCHAT_API_BASE,
    SnapchatClientError,
    SnapchatV2Client,
    _account_timezone,
    _coverage,
    _number,
)
from .models import clean_text, ensure_aware_utc

RIYADH_TZ = ZoneInfo("Asia/Riyadh")


def _total_metric_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    wrappers = payload.get("total_stats")
    if not isinstance(wrappers, list) or not wrappers:
        raise SnapchatClientError(
            "snapchat_total_envelope_missing",
            "Snapchat TOTAL response is missing total_stats.",
        )
    rows: list[dict[str, Any]] = []
    for wrapper in wrappers:
        if not isinstance(wrapper, dict):
            raise SnapchatClientError(
                "snapchat_total_wrapper_invalid",
                "Snapchat TOTAL wrapper is invalid.",
            )
        sub_status = clean_text(wrapper.get("sub_request_status"), limit=40).upper()
        if sub_status and sub_status != "SUCCESS":
            raise SnapchatClientError(
                "snapchat_total_subrequest_failed",
                "Snapchat TOTAL sub-request failed.",
                retryable=True,
            )
        stat = wrapper.get("total_stat")
        if not isinstance(stat, dict):
            raise SnapchatClientError(
                "snapchat_total_stat_missing",
                "Snapchat TOTAL response is missing total_stat.",
            )
        direct = stat.get("stats")
        if isinstance(direct, dict):
            rows.append(direct)
            continue
        breakdown = stat.get("breakdown_stats")
        if isinstance(breakdown, dict):
            for entities in breakdown.values():
                if not isinstance(entities, list):
                    raise SnapchatClientError(
                        "snapchat_total_breakdown_invalid",
                        "Snapchat TOTAL breakdown is invalid.",
                    )
                for entity in entities:
                    if not isinstance(entity, dict):
                        raise SnapchatClientError(
                            "snapchat_total_entity_invalid",
                            "Snapchat TOTAL entity is invalid.",
                        )
                    stats = entity.get("stats")
                    if isinstance(stats, dict):
                        rows.append(stats)
    if not rows:
        raise SnapchatClientError(
            "snapchat_total_metrics_missing",
            "Snapchat TOTAL response contains no metrics.",
        )
    return rows


def _hour_spend_micro(client: SnapchatV2Client, payload: dict[str, Any]) -> float:
    rows = client._extract_hour_rows(payload)
    spend_micro = 0.0
    for row in rows:
        metrics = row.get("metrics") or {}
        if "spend" not in metrics:
            raise SnapchatClientError(
                "snapchat_hour_spend_missing",
                "Snapchat HOUR reconciliation response is missing spend.",
            )
        spend_micro += float(_number(metrics.get("spend"), field="spend"))
    return spend_micro


def _ceil_current_hour(value: datetime) -> datetime:
    current = ensure_aware_utc(value, field="now")
    floor = current.replace(minute=0, second=0, microsecond=0)
    return floor if current == floor else floor + timedelta(hours=1)


def _local_day_window(report_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    local_tz = _account_timezone(timezone_name)
    local_start = datetime.combine(report_date, time.min, tzinfo=local_tz)
    local_end = datetime.combine(report_date + timedelta(days=1), time.min, tzinfo=local_tz)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def _clamp_open_window(
    start: datetime,
    end: datetime,
    *,
    now: datetime,
) -> tuple[datetime, datetime]:
    cap = _ceil_current_hour(now)
    if end > cap:
        end = cap
    if end <= start:
        raise SnapchatClientError(
            "snapchat_total_window_not_open",
            "Snapchat TOTAL reconciliation window has not started.",
        )
    return start, end


def _stats_params(
    *,
    start: datetime,
    end: datetime,
    account_tz: Any,
    granularity: str,
    action_report_time: str,
    swipe_attribution_window: str,
    view_attribution_window: str,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "start_time": start.astimezone(account_tz).isoformat(timespec="seconds"),
        "end_time": end.astimezone(account_tz).isoformat(timespec="seconds"),
        "granularity": granularity,
        "fields": "spend",
        "limit": 200,
        "omit_empty": "false",
        "conversion_source_types": "total",
        "swipe_up_attribution_window": swipe_attribution_window,
        "view_attribution_window": view_attribution_window,
        "action_report_time": clean_text(action_report_time, limit=32).lower(),
    }
    if granularity == "HOUR":
        params["breakdown"] = "campaign"
    return params


async def _fetch_window_total(
    client: SnapchatV2Client,
    account: dict[str, Any],
    *,
    start_utc: datetime,
    end_utc: datetime,
    action_report_time: str,
    swipe_attribution_window: str,
    view_attribution_window: str,
) -> dict[str, Any]:
    start = ensure_aware_utc(start_utc, field="start_utc")
    end = ensure_aware_utc(end_utc, field="end_utc")
    if end <= start:
        raise ValueError("end_utc must be after start_utc")
    if end - start > timedelta(days=7):
        raise ValueError("provider TOTAL reconciliation window cannot exceed seven days")
    account_id = clean_text(
        account.get("ad_account_id") or account.get("external_account_id"),
        limit=128,
    )
    timezone_name = clean_text(account.get("timezone"), limit=80)
    if not account_id or not timezone_name:
        raise ValueError("Snapchat account ID and timezone are required")
    account_tz = _account_timezone(timezone_name)
    url = f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/stats"
    completed = 0
    spend_micro = 0.0
    provider_granularity = "TOTAL"
    fallback_from: str | None = None
    try:
        async with client.client_factory(timeout=HTTP_TIMEOUT_SECONDS) as http_client:
            try:
                pages, completed = await client._pages(
                    http_client,
                    url,
                    params=_stats_params(
                        start=start,
                        end=end,
                        account_tz=account_tz,
                        granularity="TOTAL",
                        action_report_time=action_report_time,
                        swipe_attribution_window=swipe_attribution_window,
                        view_attribution_window=view_attribution_window,
                    ),
                )
            except SnapchatClientError as exc:
                if exc.code != "snapchat_provider_http_400":
                    raise
                fallback_from = exc.code
                provider_granularity = "HOUR"
                pages, completed = await client._pages(
                    http_client,
                    url,
                    params=_stats_params(
                        start=start,
                        end=end,
                        account_tz=account_tz,
                        granularity="HOUR",
                        action_report_time=action_report_time,
                        swipe_attribution_window=swipe_attribution_window,
                        view_attribution_window=view_attribution_window,
                    ),
                )

        observed = 0
        if provider_granularity == "TOTAL":
            for payload in pages:
                for metrics in _total_metric_rows(payload):
                    if "spend" not in metrics:
                        raise SnapchatClientError(
                            "snapchat_total_spend_missing",
                            "Snapchat TOTAL response is missing spend.",
                        )
                    spend_micro += float(_number(metrics.get("spend"), field="spend"))
                    observed += 1
        else:
            for payload in pages:
                spend_micro += _hour_spend_micro(client, payload)
                observed += 1

        if observed == 0:
            raise SnapchatClientError(
                "snapchat_total_spend_unobserved",
                "Snapchat reconciliation spend was not observed.",
            )
    except SnapchatClientError as exc:
        exc.coverage = _coverage(
            status="incomplete",
            data_state="unknown_incomplete",
            expected_requests=max(completed + 1, 1),
            completed_requests=completed,
            rows_received=0,
            reason=exc.code,
        )
        raise

    spend_native = round(spend_micro / 1_000_000, 6)
    coverage = _coverage(
        status="complete",
        data_state="confirmed_data" if spend_native > 0 else "confirmed_zero",
        expected_requests=completed,
        completed_requests=completed,
        rows_received=1,
    )
    coverage["provider_granularity"] = provider_granularity
    if fallback_from:
        coverage["fallback_from"] = fallback_from
    return {
        "provider_spend_native": spend_native,
        "window_start_utc": start,
        "window_end_utc": end,
        "provider_granularity": provider_granularity,
        "fallback_from": fallback_from,
        "coverage": coverage,
    }


async def fetch_provider_total(
    client: SnapchatV2Client,
    account: dict[str, Any],
    *,
    start_utc: datetime,
    end_utc: datetime,
    action_report_time: str = "conversion",
    swipe_attribution_window: str = "28_DAY",
    view_attribution_window: str = "1_DAY",
) -> dict[str, Any]:
    """Return Dashboard-window total plus an account-day comparison total.

    ``provider_spend_native`` remains the requested-window result for backward
    compatibility. The additional ``account_day_*`` fields are consumed by
    reconciliation so the Snapchat page is never compared against a Riyadh UTC
    window by mistake.
    """
    current = client.now().astimezone(timezone.utc)
    dashboard_start, dashboard_end = _clamp_open_window(
        ensure_aware_utc(start_utc, field="start_utc"),
        ensure_aware_utc(end_utc, field="end_utc"),
        now=current,
    )
    dashboard = await _fetch_window_total(
        client,
        account,
        start_utc=dashboard_start,
        end_utc=dashboard_end,
        action_report_time=action_report_time,
        swipe_attribution_window=swipe_attribution_window,
        view_attribution_window=view_attribution_window,
    )

    report_date = dashboard_start.astimezone(RIYADH_TZ).date()
    account_timezone = clean_text(account.get("timezone"), limit=80)
    account_start, account_end = _local_day_window(report_date, account_timezone)
    account_start, account_end = _clamp_open_window(
        account_start,
        account_end,
        now=current,
    )
    if account_start == dashboard_start and account_end == dashboard_end:
        account_day = dashboard
    else:
        account_day = await _fetch_window_total(
            client,
            account,
            start_utc=account_start,
            end_utc=account_end,
            action_report_time=action_report_time,
            swipe_attribution_window=swipe_attribution_window,
            view_attribution_window=view_attribution_window,
        )

    return {
        **dashboard,
        "dashboard_provider_spend_native": dashboard["provider_spend_native"],
        "dashboard_coverage": dashboard["coverage"],
        "account_day_provider_spend_native": account_day["provider_spend_native"],
        "account_day_coverage": account_day["coverage"],
        "account_day_window_start_utc": account_day["window_start_utc"],
        "account_day_window_end_utc": account_day["window_end_utc"],
        "provider_calls": client.provider_calls,
    }


__all__ = ["fetch_provider_total"]
