"""Independent Snapchat provider-total reads used only for reconciliation."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .client import (
    HTTP_TIMEOUT_SECONDS,
    SNAPCHAT_API_BASE,
    SnapchatClientError,
    SnapchatV2Client,
    _coverage,
    _number,
)
from .models import clean_text, ensure_aware_utc


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
    from .client import _account_timezone

    account_tz = _account_timezone(timezone_name)
    url = f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/stats"
    params = {
        "start_time": start.astimezone(account_tz).isoformat(timespec="seconds"),
        "end_time": end.astimezone(account_tz).isoformat(timespec="seconds"),
        "granularity": "TOTAL",
        "fields": "spend",
        "limit": 200,
        "omit_empty": "false",
        "conversion_source_types": "total",
        "swipe_up_attribution_window": swipe_attribution_window,
        "view_attribution_window": view_attribution_window,
        "action_report_time": clean_text(action_report_time, limit=32).lower(),
    }
    completed = 0
    spend_micro = 0.0
    try:
        async with client.client_factory(timeout=HTTP_TIMEOUT_SECONDS) as http_client:
            pages, completed = await client._pages(http_client, url, params=params)
        observed = 0
        for payload in pages:
            for metrics in _total_metric_rows(payload):
                if "spend" not in metrics:
                    raise SnapchatClientError(
                        "snapchat_total_spend_missing",
                        "Snapchat TOTAL response is missing spend.",
                    )
                spend_micro += float(_number(metrics.get("spend"), field="spend"))
                observed += 1
        if observed == 0:
            raise SnapchatClientError(
                "snapchat_total_spend_unobserved",
                "Snapchat TOTAL spend was not observed.",
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
    return {
        "provider_spend_native": spend_native,
        "coverage": _coverage(
            status="complete",
            data_state="confirmed_data" if spend_native > 0 else "confirmed_zero",
            expected_requests=completed,
            completed_requests=completed,
            rows_received=1,
        ),
        "provider_calls": client.provider_calls,
    }


__all__ = ["fetch_provider_total"]
