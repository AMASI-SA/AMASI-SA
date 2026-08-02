"""Snapchat conversion-time reporting plus provider freshness observability.

Mezan keeps the merchant business day in Asia/Riyadh and converts the same
instants to each ad account's native timezone. Purchases and purchase value are
reported by conversion time so the number means conversions that happened
inside the Riyadh day.

Snapchat returns separate finalization timestamps for non-conversion metrics and
conversion metrics. This module captures those timestamps without affecting the
analytics request, then augments Dashboard V2 with a conservative, account-aware
freshness status. All behavior remains provider-read-only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Final
from urllib.parse import urlsplit

SNAPCHAT_ACTION_REPORT_TIME: Final[str] = "conversion"
SNAPCHAT_SOURCE_MODE: Final[str] = (
    "snapchat_account_hourly_campaign_breakdown_riyadh_conversion_freshness_v5"
)
FRESHNESS_COLLECTION: Final[str] = "mezan_snapchat_reporting_freshness_v2"

# Backward-compatible names retained for the focused reporting contract tests.
ADS_MANAGER_ACTION_REPORT_TIME = SNAPCHAT_ACTION_REPORT_TIME
ADS_MANAGER_SOURCE_MODE = SNAPCHAT_SOURCE_MODE


def _parse_provider_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _account_id_from_stats_url(url: Any) -> str | None:
    try:
        parts = [part for part in urlsplit(str(url or "")).path.split("/") if part]
    except ValueError:
        return None
    for index, part in enumerate(parts[:-2]):
        if part == "adaccounts" and parts[index + 2] == "stats":
            account_id = parts[index + 1].strip()
            return account_id or None
    return None


def summarize_conversion_freshness(
    rows: list[dict[str, Any]],
    *,
    expected_account_ids: list[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the conservative freshness view across selected ad accounts."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    expected = [str(item).strip() for item in (expected_account_ids or []) if str(item).strip()]
    per_account: list[dict[str, Any]] = []
    processed_values: list[datetime] = []
    finalized_values: list[datetime] = []
    complete_flags: list[bool] = []

    for row in rows:
        account_id = str(row.get("ad_account_id") or "").strip()
        processed = _parse_provider_time(row.get("conversion_data_processed_end_time"))
        finalized = _parse_provider_time(row.get("finalized_data_end_time"))
        requested_end = _parse_provider_time(row.get("request_end_time"))
        complete_for_request = bool(
            processed is not None
            and requested_end is not None
            and processed >= requested_end
        )
        if processed is not None:
            processed_values.append(processed)
            complete_flags.append(complete_for_request)
        if finalized is not None:
            finalized_values.append(finalized)
        per_account.append({
            "ad_account_id": account_id or None,
            "conversion_data_processed_end_time": (
                processed.isoformat() if processed is not None else None
            ),
            "finalized_data_end_time": (
                finalized.isoformat() if finalized is not None else None
            ),
            "requested_end_time": (
                requested_end.isoformat() if requested_end is not None else None
            ),
            "complete_for_requested_window": complete_for_request,
            "observed_at": row.get("observed_at"),
        })

    conservative_processed = min(processed_values) if processed_values else None
    conservative_finalized = min(finalized_values) if finalized_values else None
    accounts_reporting = len({
        str(item.get("ad_account_id") or "").strip()
        for item in per_account
        if item.get("conversion_data_processed_end_time")
    })
    accounts_expected = len(set(expected)) if expected else len({
        str(item.get("ad_account_id") or "").strip()
        for item in per_account
        if item.get("ad_account_id")
    })
    all_accounts_reported = accounts_expected > 0 and accounts_reporting >= accounts_expected
    complete = bool(
        all_accounts_reported
        and complete_flags
        and len(complete_flags) >= accounts_expected
        and all(complete_flags)
    )
    if not processed_values:
        status = "unknown"
        provisional: bool | None = None
    elif complete:
        status = "complete"
        provisional = False
    else:
        status = "processing"
        provisional = True

    lag_minutes = None
    if conservative_processed is not None:
        lag_minutes = round(
            max(0.0, (current - conservative_processed).total_seconds() / 60.0),
            1,
        )

    return {
        "status": status,
        "provisional": provisional,
        "action_report_time": SNAPCHAT_ACTION_REPORT_TIME,
        "source_mode": SNAPCHAT_SOURCE_MODE,
        "accounts_expected": accounts_expected,
        "accounts_reporting": accounts_reporting,
        "conversion_data_processed_end_time": (
            conservative_processed.isoformat()
            if conservative_processed is not None
            else None
        ),
        "finalized_data_end_time": (
            conservative_finalized.isoformat()
            if conservative_finalized is not None
            else None
        ),
        "conversion_lag_minutes": lag_minutes,
        "per_account": per_account,
    }


async def _capture_provider_freshness(
    context: Any,
    *,
    url: str,
    params: dict[str, Any] | None,
    payload: dict[str, Any],
) -> None:
    account_id = _account_id_from_stats_url(url)
    processed = payload.get("conversion_data_processed_end_time")
    finalized = payload.get("finalized_data_end_time")
    if not account_id or (not processed and not finalized):
        return

    from .snapchat_native_data_common import (
        SNAPCHAT_PROVIDER_ID,
        _collection,
    )

    now_iso = context.now_iso()
    patch: dict[str, Any] = {
        "user_id": context.user_id,
        "provider": SNAPCHAT_PROVIDER_ID,
        "ad_account_id": account_id,
        "action_report_time": str(
            (params or {}).get("action_report_time")
            or SNAPCHAT_ACTION_REPORT_TIME
        ),
        "source_mode": SNAPCHAT_SOURCE_MODE,
        "observed_at": now_iso,
    }
    optional = {
        "conversion_data_processed_end_time": processed,
        "finalized_data_end_time": finalized,
        "request_id": payload.get("request_id"),
        "request_start_time": (params or {}).get("start_time"),
        "request_end_time": (params or {}).get("end_time"),
    }
    patch.update({key: value for key, value in optional.items() if value is not None})
    await _collection(context.db, FRESHNESS_COLLECTION).update_one(
        {
            "user_id": context.user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": account_id,
        },
        {
            "$set": patch,
            "$setOnInsert": {"created_at": now_iso},
        },
        upsert=True,
    )


def _install_provider_response_capture() -> None:
    from . import snapchat_native_data_common as common

    current = common.SnapchatSyncContext.get_json
    if getattr(current, "_mezan_snapchat_freshness_v5", False):
        return

    async def wrapped_get_json(
        self: Any,
        client: Any,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = await current(
            self,
            client,
            url,
            headers=headers,
            params=params,
        )
        try:
            await _capture_provider_freshness(
                self,
                url=url,
                params=params,
                payload=payload,
            )
        except Exception:  # noqa: BLE001 - observability must not block stats
            pass
        return payload

    wrapped_get_json._mezan_snapchat_freshness_v5 = True  # type: ignore[attr-defined]
    common.SnapchatSyncContext.get_json = wrapped_get_json


def _install_dashboard_freshness() -> None:
    import dashboard_v2_routes as dashboard

    current = dashboard.build_provider_summary
    if getattr(current, "_mezan_snapchat_freshness_v5", False):
        return

    async def wrapped_summary(
        db: Any,
        user_id: str,
        provider: str,
    ) -> dict[str, Any]:
        result = await current(db, user_id, provider)
        if provider != "snapchat":
            return result

        selected_ids = await dashboard._selected_account_ids(
            db,
            user_id,
            provider,
        )
        rows: list[dict[str, Any]] = []
        if selected_ids:
            cursor = db[FRESHNESS_COLLECTION].find(
                {
                    "user_id": user_id,
                    "provider": "snapchat_ads",
                    "ad_account_id": {"$in": selected_ids},
                },
                {"_id": 0},
            )
            rows = await dashboard._to_list(cursor, 100)
        freshness = summarize_conversion_freshness(
            rows,
            expected_account_ids=selected_ids,
        )
        result["conversion_freshness"] = freshness
        today = result.get("today")
        if isinstance(today, dict):
            today["conversion_data_provisional"] = freshness["provisional"]
            today["conversion_data_processed_end_time"] = freshness[
                "conversion_data_processed_end_time"
            ]
        return result

    wrapped_summary._mezan_snapchat_freshness_v5 = True  # type: ignore[attr-defined]
    dashboard.build_provider_summary = wrapped_summary


def install_snapchat_ads_manager_attribution() -> None:
    """Install conversion-time reporting and freshness capture idempotently."""
    from . import snapchat_account_hourly_refresh as account_refresh
    from . import snapchat_dashboard_summary_routes as dashboard_summary
    from . import snapchat_native_performance_sync as performance_sync

    account_refresh.ACTION_REPORT_TIME = SNAPCHAT_ACTION_REPORT_TIME
    account_refresh.ACCOUNT_REFRESH_SOURCE_MODE = SNAPCHAT_SOURCE_MODE
    performance_sync.ACTION_REPORT_TIME = SNAPCHAT_ACTION_REPORT_TIME
    dashboard_summary.ACTION_REPORT_TIME = SNAPCHAT_ACTION_REPORT_TIME
    _install_provider_response_capture()
    _install_dashboard_freshness()


__all__ = [
    "ADS_MANAGER_ACTION_REPORT_TIME",
    "ADS_MANAGER_SOURCE_MODE",
    "FRESHNESS_COLLECTION",
    "SNAPCHAT_ACTION_REPORT_TIME",
    "SNAPCHAT_SOURCE_MODE",
    "_account_id_from_stats_url",
    "install_snapchat_ads_manager_attribution",
    "summarize_conversion_freshness",
]
