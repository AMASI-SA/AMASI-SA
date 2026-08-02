"""Read-only Meta campaign catalogue and daily campaign performance for Mezan V2.

The module reads selected Meta ad accounts through the existing encrypted OAuth
credential. It persists only analytical campaign facts in a dedicated V2
collection. It never creates, edits, pauses, or deletes provider campaigns and
never writes legacy advertising tables, accounting, Salla, or Qoyod.
"""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Any

import httpx

from .meta_oauth_security import (
    META_PROVIDER_ID,
    meta_appsecret_proof,
    meta_graph_base,
)

META_CAMPAIGN_REPORTING_COLLECTION = "mezan_meta_campaign_performance_daily_v2"
META_CAMPAIGN_REPORTING_SOURCE_MODE = "meta_campaign_reporting_v2"
META_USD_TO_SAR_RATE_ENV = "META_USD_TO_SAR_RATE"
MAX_META_CAMPAIGN_PAGES = 20
META_CAMPAIGN_PAGE_SIZE = 500

PURCHASE_ACTION_PRIORITY = (
    "omni_purchase",
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
    "onsite_conversion.purchase",
    "mobile_app_purchase",
)


class MetaCampaignReportingError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
        provider_calls: int = 0,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.provider_calls = provider_calls


def _action_value(rows: Any) -> tuple[float, str | None]:
    if not isinstance(rows, list):
        return 0.0, None
    by_type = {
        str(item.get("action_type") or ""): float(item.get("value") or 0)
        for item in rows
        if isinstance(item, dict)
    }
    for action_type in PURCHASE_ACTION_PRIORITY:
        if action_type in by_type:
            return by_type[action_type], action_type
    return 0.0, None


def _fx_to_sar(currency: str | None) -> tuple[float | None, str]:
    normalized = str(currency or "").strip().upper()
    if normalized == "SAR":
        return 1.0, "implicit_sar"
    if normalized == "USD":
        try:
            rate = float(os.environ.get(META_USD_TO_SAR_RATE_ENV, "3.75"))
        except ValueError:
            rate = 0.0
        if rate > 0:
            return rate, "configured_usd_peg"
    return None, "missing"


def _minor_to_native(value: Any, currency: str | None) -> float | None:
    normalized = str(currency or "").strip().upper()
    if normalized not in {"SAR", "USD"} or value in (None, ""):
        return None
    try:
        return round(float(value) / 100.0, 2)
    except (TypeError, ValueError):
        return None


def _graph_error(
    response: httpx.Response,
    operation: str,
    *,
    provider_calls: int,
) -> MetaCampaignReportingError:
    payload: dict[str, Any] = {}
    try:
        parsed = response.json()
        payload = parsed if isinstance(parsed, dict) else {}
    except Exception:  # noqa: BLE001
        payload = {}
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    provider_code = int(error.get("code") or 0)
    if response.status_code in {401, 403} or provider_code in {102, 190}:
        return MetaCampaignReportingError(
            "meta_needs_reauth",
            "Meta rejected or expired the saved access token.",
            status_code=409,
            retryable=False,
            provider_calls=provider_calls,
        )
    if response.status_code == 429 or provider_code in {4, 17, 32, 613}:
        return MetaCampaignReportingError(
            "meta_rate_limited",
            "Meta temporarily rate-limited campaign reporting requests.",
            status_code=429,
            retryable=True,
            provider_calls=provider_calls,
        )
    return MetaCampaignReportingError(
        f"{operation}_http_{response.status_code}",
        "Meta campaign reporting returned an API error.",
        status_code=502,
        retryable=response.status_code >= 500 or provider_code in {1, 2},
        provider_calls=provider_calls,
    )


async def _paged_get(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
    *,
    operation: str,
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    provider_calls = 0
    after: str | None = None
    seen_cursors: set[str] = set()

    for _ in range(MAX_META_CAMPAIGN_PAGES):
        request_params = dict(params)
        if after:
            request_params["after"] = after
        response = await client.get(url, params=request_params)
        provider_calls += 1
        if response.status_code >= 400:
            raise _graph_error(
                response,
                operation,
                provider_calls=provider_calls,
            )
        payload = response.json() or {}
        if not isinstance(payload, dict):
            raise MetaCampaignReportingError(
                f"{operation}_invalid_payload",
                "Meta campaign reporting returned an invalid response.",
                status_code=502,
                retryable=True,
                provider_calls=provider_calls,
            )
        page_rows = payload.get("data") or []
        if not isinstance(page_rows, list):
            raise MetaCampaignReportingError(
                f"{operation}_invalid_rows",
                "Meta campaign reporting returned invalid rows.",
                status_code=502,
                retryable=True,
                provider_calls=provider_calls,
            )
        rows.extend(row for row in page_rows if isinstance(row, dict))

        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        if not paging.get("next"):
            return rows, provider_calls
        cursors = paging.get("cursors") if isinstance(paging.get("cursors"), dict) else {}
        next_after = str(cursors.get("after") or "").strip()
        if not next_after or next_after in seen_cursors:
            raise MetaCampaignReportingError(
                f"{operation}_paging_invalid",
                "Meta campaign pagination did not provide a safe next cursor.",
                status_code=502,
                retryable=True,
                provider_calls=provider_calls,
            )
        seen_cursors.add(next_after)
        after = next_after

    raise MetaCampaignReportingError(
        f"{operation}_page_limit_reached",
        "Meta campaign reporting exceeded the safe page limit.",
        status_code=502,
        retryable=True,
        provider_calls=provider_calls,
    )


async def ensure_meta_campaign_reporting_indexes(db: Any) -> None:
    await db[META_CAMPAIGN_REPORTING_COLLECTION].create_index(
        [
            ("user_id", 1),
            ("ad_account_id", 1),
            ("campaign_id", 1),
            ("date", 1),
        ],
        unique=True,
        name="meta_campaign_user_account_campaign_date_unique",
    )
    await db[META_CAMPAIGN_REPORTING_COLLECTION].create_index(
        [("user_id", 1), ("date", -1)],
        name="meta_campaign_user_date",
    )


async def fetch_meta_campaign_catalog(
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
) -> dict[str, Any]:
    account_id = str(account.get("ad_account_id") or "").strip()
    rows, provider_calls = await _paged_get(
        client,
        f"{meta_graph_base()}/{account_id}/campaigns",
        {
            "access_token": access_token,
            "appsecret_proof": meta_appsecret_proof(access_token),
            "fields": (
                "id,name,objective,status,effective_status,daily_budget,"
                "lifetime_budget,start_time,stop_time,created_time,updated_time"
            ),
            "limit": META_CAMPAIGN_PAGE_SIZE,
        },
        operation="meta_campaign_catalog",
    )
    campaigns: dict[str, dict[str, Any]] = {}
    for row in rows:
        campaign_id = str(row.get("id") or "").strip()
        if not campaign_id:
            continue
        campaigns[campaign_id] = {
            "campaign_id": campaign_id,
            "campaign_name": row.get("name") or campaign_id,
            "objective": row.get("objective"),
            "status": row.get("status"),
            "effective_status": row.get("effective_status"),
            "daily_budget_minor": row.get("daily_budget"),
            "lifetime_budget_minor": row.get("lifetime_budget"),
            "start_time": row.get("start_time"),
            "stop_time": row.get("stop_time"),
            "created_time": row.get("created_time"),
            "updated_time": row.get("updated_time"),
        }
    return {"campaigns": campaigns, "provider_calls": provider_calls}


async def sync_meta_campaign_day(
    db: Any,
    user_id: str,
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
    day: date,
    *,
    campaign_catalog: dict[str, dict[str, Any]],
    observed_at: str,
) -> dict[str, Any]:
    account_id = str(account.get("ad_account_id") or "").strip()
    rows, provider_calls = await _paged_get(
        client,
        f"{meta_graph_base()}/{account_id}/insights",
        {
            "access_token": access_token,
            "appsecret_proof": meta_appsecret_proof(access_token),
            "fields": (
                "campaign_id,campaign_name,spend,impressions,clicks,actions,"
                "action_values,account_currency,date_start,date_stop"
            ),
            "time_range": json.dumps(
                {"since": day.isoformat(), "until": day.isoformat()},
                separators=(",", ":"),
            ),
            "time_increment": 1,
            "level": "campaign",
            "action_report_time": "conversion",
            "use_account_attribution_setting": "true",
            "use_unified_attribution_setting": "true",
            "limit": META_CAMPAIGN_PAGE_SIZE,
        },
        operation="meta_campaign_insights",
    )

    documents: list[dict[str, Any]] = []
    for row in rows:
        campaign_id = str(row.get("campaign_id") or "").strip()
        if not campaign_id:
            continue
        metadata = campaign_catalog.get(campaign_id) or {}
        purchases, purchase_action_type = _action_value(row.get("actions"))
        purchase_value, purchase_value_action_type = _action_value(
            row.get("action_values")
        )
        currency = str(
            row.get("account_currency") or account.get("currency") or ""
        ).strip().upper() or None
        fx_rate, fx_source = _fx_to_sar(currency)
        spend_native = float(row.get("spend") or 0)
        spend_sar = round(spend_native * fx_rate, 2) if fx_rate is not None else None
        purchase_value_sar = (
            round(purchase_value * fx_rate, 2) if fx_rate is not None else None
        )
        documents.append(
            {
                "user_id": user_id,
                "provider": META_PROVIDER_ID,
                "ad_account_id": account_id,
                "display_name": account.get("display_name"),
                "campaign_id": campaign_id,
                "campaign_name": (
                    row.get("campaign_name")
                    or metadata.get("campaign_name")
                    or campaign_id
                ),
                "objective": metadata.get("objective"),
                "status": metadata.get("status"),
                "effective_status": metadata.get("effective_status"),
                "date": day.isoformat(),
                "date_start": row.get("date_start") or day.isoformat(),
                "date_stop": row.get("date_stop") or day.isoformat(),
                "currency_native": currency,
                "spend_native": spend_native,
                "fx_rate_to_sar": fx_rate,
                "fx_source": fx_source,
                "spend_sar": spend_sar,
                "impressions": int(float(row.get("impressions") or 0)),
                "clicks": int(float(row.get("clicks") or 0)),
                "purchases": purchases,
                "purchase_value_native": purchase_value,
                "purchase_value_sar": purchase_value_sar,
                "purchase_action_type": purchase_action_type,
                "purchase_value_action_type": purchase_value_action_type,
                "daily_budget_native": _minor_to_native(
                    metadata.get("daily_budget_minor"), currency
                ),
                "lifetime_budget_native": _minor_to_native(
                    metadata.get("lifetime_budget_minor"), currency
                ),
                "budget_minor_units_source": "provider_campaign_catalog",
                "start_time": metadata.get("start_time"),
                "stop_time": metadata.get("stop_time"),
                "created_time": metadata.get("created_time"),
                "updated_time": metadata.get("updated_time"),
                "attribution_mode": "account_setting+unified",
                "source_mode": META_CAMPAIGN_REPORTING_SOURCE_MODE,
                "source_only": True,
                "accounting_eligible": False,
                "observed_at": observed_at,
                "updated_at": observed_at,
            }
        )

    await db[META_CAMPAIGN_REPORTING_COLLECTION].delete_many(
        {
            "user_id": user_id,
            "provider": META_PROVIDER_ID,
            "ad_account_id": account_id,
            "date": day.isoformat(),
        }
    )
    for document in documents:
        await db[META_CAMPAIGN_REPORTING_COLLECTION].update_one(
            {
                "user_id": user_id,
                "provider": META_PROVIDER_ID,
                "ad_account_id": account_id,
                "campaign_id": document["campaign_id"],
                "date": day.isoformat(),
            },
            {"$set": document, "$setOnInsert": {"created_at": observed_at}},
            upsert=True,
        )

    return {
        "rows_saved": len(documents),
        "provider_calls": provider_calls,
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


__all__ = [
    "META_CAMPAIGN_REPORTING_COLLECTION",
    "META_CAMPAIGN_REPORTING_SOURCE_MODE",
    "MetaCampaignReportingError",
    "ensure_meta_campaign_reporting_indexes",
    "fetch_meta_campaign_catalog",
    "sync_meta_campaign_day",
]
