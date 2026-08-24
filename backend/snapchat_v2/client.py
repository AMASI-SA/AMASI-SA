"""Bounded, read-only Snapchat Marketing API client for reporting V2."""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from .models import SNAPCHAT_PROVIDER, clean_text, ensure_aware_utc
from .token_store import SnapchatTokenStore, SnapchatTokenStoreError

SNAPCHAT_API_BASE = "https://adsapi.snapchat.com/v1"
MAX_HOUR_WINDOW = timedelta(days=7)
MAX_PAGES = 100
MAX_PROVIDER_CALLS = 500
MAX_ENTITY_ROWS = 20_000
HTTP_RETRIES = 3
HTTP_TIMEOUT_SECONDS = 30.0

STAT_FIELDS = (
    "impressions",
    "swipes",
    "spend",
    "video_views",
    "conversion_purchases",
    "conversion_purchases_value",
)

ENTITY_ENDPOINTS = {
    "campaign": ("campaigns", "campaigns", "campaign"),
    "ad_squad": ("adsquads", "adsquads", "adsquad"),
    "ad": ("ads", "ads", "ad"),
}

PERFORMANCE_BREAKDOWNS = {
    "ad_squad": {
        "request_value": "adsquad",
        "response_keys": ("adsquad", "ad_squad", "ad_squads"),
        "identity_field": "ad_squad_id",
    },
    "ad": {
        "request_value": "ad",
        "response_keys": ("ad", "ads"),
        "identity_field": "ad_id",
    },
}


class SnapchatClientError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        needs_reauth: bool = False,
        coverage: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.needs_reauth = needs_reauth
        self.coverage = dict(coverage or {})


@dataclass(frozen=True)
class HourWindow:
    start_utc: datetime
    end_utc: datetime
    provider_start: datetime
    provider_end: datetime

    def as_dict(self) -> dict[str, datetime]:
        return {
            "start_utc": self.start_utc,
            "end_utc": self.end_utc,
            "provider_start": self.provider_start,
            "provider_end": self.provider_end,
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        current = value
    else:
        text = str(value or "").strip()
        if not text:
            raise SnapchatClientError(
                "snapchat_datetime_missing",
                f"Snapchat response is missing {field}.",
            )
        try:
            current = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SnapchatClientError(
                "snapchat_datetime_invalid",
                f"Snapchat response contains an invalid {field}.",
            ) from exc
    if current.tzinfo is None or current.utcoffset() is None:
        raise SnapchatClientError(
            "snapchat_datetime_naive",
            f"Snapchat response contains a timezone-naive {field}.",
        )
    return current.astimezone(timezone.utc)


def _account_timezone(value: Any) -> ZoneInfo:
    name = clean_text(value, limit=80)
    if not name:
        raise SnapchatClientError(
            "snapchat_account_timezone_missing",
            "Snapchat account timezone is missing.",
        )
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise SnapchatClientError(
            "snapchat_account_timezone_invalid",
            "Snapchat account timezone is invalid.",
        ) from exc


def split_hour_windows(
    start_utc: datetime,
    end_utc: datetime,
    *,
    account_timezone: str,
) -> list[HourWindow]:
    start = ensure_aware_utc(start_utc, field="start_utc")
    end = ensure_aware_utc(end_utc, field="end_utc")
    if start.minute or start.second or start.microsecond:
        raise ValueError("start_utc must be aligned to the hour")
    if end.minute or end.second or end.microsecond:
        raise ValueError("end_utc must be aligned to the hour")
    if end <= start:
        raise ValueError("end_utc must be after start_utc")
    account_tz = _account_timezone(account_timezone)
    windows: list[HourWindow] = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + MAX_HOUR_WINDOW, end)
        windows.append(
            HourWindow(
                start_utc=cursor,
                end_utc=window_end,
                provider_start=cursor.astimezone(account_tz),
                provider_end=window_end.astimezone(account_tz),
            )
        )
        cursor = window_end
    return windows


def _safe_next_link(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.hostname != "adsapi.snapchat.com":
        return None
    if not parsed.path.startswith("/v1/"):
        return None
    return text


def _number(value: Any, *, field: str, integer: bool = False) -> int | float:
    if value is None:
        return 0
    if isinstance(value, bool):
        raise SnapchatClientError(
            "snapchat_metric_invalid",
            f"Snapchat metric {field} is invalid.",
        )
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SnapchatClientError(
            "snapchat_metric_invalid",
            f"Snapchat metric {field} is invalid.",
        ) from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise SnapchatClientError(
            "snapchat_metric_invalid",
            f"Snapchat metric {field} is invalid.",
        )
    if integer:
        rounded = int(parsed)
        if abs(parsed - rounded) > 1e-9:
            raise SnapchatClientError(
                "snapchat_metric_invalid",
                f"Snapchat metric {field} is not an integer.",
            )
        return rounded
    return parsed


def _coverage(
    *,
    status: str,
    data_state: str,
    expected_requests: int,
    completed_requests: int,
    rows_received: int,
    reason: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "data_state": data_state,
        "expected_requests": max(int(expected_requests), 0),
        "completed_requests": max(int(completed_requests), 0),
        "rows_received": max(int(rows_received), 0),
    }
    if reason:
        result["reason"] = clean_text(reason, limit=128)
    return result


class SnapchatV2Client:
    def __init__(
        self,
        db: Any,
        user_id: str,
        *,
        token_store: SnapchatTokenStore | None = None,
        client_factory: Callable[..., Any] = httpx.AsyncClient,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.db = db
        self.user_id = str(user_id)
        self.tokens = token_store or SnapchatTokenStore(db, now=now)
        self.client_factory = client_factory
        self.now = now
        self.provider_calls = 0

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        raw = str(response.headers.get("Retry-After") or "").strip()
        try:
            delay = float(raw)
        except (TypeError, ValueError, OverflowError):
            delay = float(2**attempt)
        if not math.isfinite(delay):
            delay = float(2**attempt)
        return min(max(delay, 0.25), 8.0)

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: dict[str, Any] | None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        access_token = await self.tokens.get_access_token(
            self.user_id,
            force_refresh=force_refresh,
        )
        response: httpx.Response | None = None
        refreshed_after_401 = force_refresh
        for attempt in range(HTTP_RETRIES + 1):
            self.provider_calls += 1
            if self.provider_calls > MAX_PROVIDER_CALLS:
                raise SnapchatClientError(
                    "snapchat_provider_call_budget_exceeded",
                    "Snapchat sync exceeded its provider call budget.",
                )
            try:
                response = await client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                    params=params,
                )
            except httpx.HTTPError as exc:
                if attempt < HTTP_RETRIES:
                    await asyncio.sleep(min(2**attempt, 4))
                    continue
                raise SnapchatClientError(
                    "snapchat_provider_network_error",
                    "Snapchat provider request failed.",
                    retryable=True,
                ) from exc

            if response.status_code == 401 and not refreshed_after_401:
                try:
                    access_token = await self.tokens.get_access_token(
                        self.user_id,
                        force_refresh=True,
                    )
                except SnapchatTokenStoreError as exc:
                    raise SnapchatClientError(
                        exc.code,
                        exc.message,
                        retryable=exc.retryable,
                        needs_reauth=exc.needs_reauth,
                    ) from exc
                refreshed_after_401 = True
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < HTTP_RETRIES:
                    await asyncio.sleep(self._retry_delay(response, attempt))
                    continue
            break

        if response is None:
            raise SnapchatClientError(
                "snapchat_provider_missing_response",
                "Snapchat provider request failed.",
                retryable=True,
            )
        if response.status_code == 401:
            raise SnapchatClientError(
                "snapchat_needs_reauth",
                "Snapchat authorization must be renewed.",
                needs_reauth=True,
            )
        if response.status_code >= 400:
            raise SnapchatClientError(
                f"snapchat_provider_http_{response.status_code}",
                "Snapchat rejected a read-only reporting request.",
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        try:
            payload = response.json() or {}
        except (TypeError, ValueError) as exc:
            raise SnapchatClientError(
                "snapchat_provider_invalid_json",
                "Snapchat returned invalid JSON.",
                retryable=True,
            ) from exc
        if not isinstance(payload, dict):
            raise SnapchatClientError(
                "snapchat_provider_invalid_payload",
                "Snapchat returned an invalid payload.",
                retryable=True,
            )
        request_status = clean_text(payload.get("request_status"), limit=40).upper()
        if request_status and request_status != "SUCCESS":
            raise SnapchatClientError(
                "snapchat_provider_request_failed",
                "Snapchat reported a failed request.",
                retryable=True,
            )
        return payload

    async def _pages(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], int]:
        pages: list[dict[str, Any]] = []
        next_url: str | None = url
        next_params = params
        seen_urls: set[str] = set()
        for _ in range(MAX_PAGES):
            if next_url is None:
                break
            if next_url in seen_urls:
                raise SnapchatClientError(
                    "snapchat_pagination_cycle",
                    "Snapchat returned a pagination cycle.",
                )
            seen_urls.add(next_url)
            payload = await self._request_json(client, next_url, params=next_params)
            pages.append(payload)
            paging = payload.get("paging")
            if paging is not None and not isinstance(paging, dict):
                raise SnapchatClientError(
                    "snapchat_paging_invalid",
                    "Snapchat returned invalid pagination metadata.",
                )
            raw_next = (paging or {}).get("next_link")
            if not str(raw_next or "").strip():
                next_url = None
                break
            next_url = _safe_next_link(raw_next)
            if next_url is None:
                raise SnapchatClientError(
                    "snapchat_next_link_invalid",
                    "Snapchat returned an unsafe next-page URL.",
                )
            next_params = None
        if next_url is not None:
            raise SnapchatClientError(
                "snapchat_pagination_incomplete",
                "Snapchat response exceeded the pagination safety limit.",
            )
        return pages, len(pages)

    async def fetch_entities(
        self,
        ad_account_id: str,
        entity_type: str,
    ) -> dict[str, Any]:
        if entity_type not in ENTITY_ENDPOINTS:
            raise ValueError(f"Unsupported Snapchat entity type: {entity_type}")
        endpoint, response_key, wrapper_key = ENTITY_ENDPOINTS[entity_type]
        url = f"{SNAPCHAT_API_BASE}/adaccounts/{ad_account_id}/{endpoint}"
        rows: list[dict[str, Any]] = []
        async with self.client_factory(timeout=HTTP_TIMEOUT_SECONDS) as client:
            pages, completed = await self._pages(
                client,
                url,
                params={"limit": 200},
            )
        for payload in pages:
            wrappers = payload.get(response_key)
            if not isinstance(wrappers, list):
                raise SnapchatClientError(
                    "snapchat_entity_payload_invalid",
                    f"Snapchat {entity_type} payload is invalid.",
                )
            for wrapper in wrappers:
                if not isinstance(wrapper, dict):
                    raise SnapchatClientError(
                        "snapchat_entity_wrapper_invalid",
                        f"Snapchat {entity_type} wrapper is invalid.",
                    )
                entity = wrapper.get(wrapper_key)
                if not isinstance(entity, dict):
                    entity = wrapper
                external_id = clean_text(entity.get("id"), limit=128)
                if not external_id:
                    raise SnapchatClientError(
                        "snapchat_entity_id_missing",
                        f"Snapchat {entity_type} is missing its ID.",
                    )
                rows.append(
                    {
                        "provider": SNAPCHAT_PROVIDER,
                        "ad_account_id": str(ad_account_id),
                        "entity_type": entity_type,
                        "external_id": external_id,
                        "name": clean_text(entity.get("name") or external_id, limit=300),
                        "status": clean_text(entity.get("status"), limit=64) or None,
                        "campaign_id": clean_text(entity.get("campaign_id"), limit=128) or None,
                        "ad_squad_id": clean_text(
                            entity.get("ad_squad_id") or entity.get("adsquad_id"),
                            limit=128,
                        )
                        or None,
                        "creative_id": clean_text(entity.get("creative_id"), limit=128)
                        or None,
                        "raw": {
                            key: entity.get(key)
                            for key in (
                                "daily_budget_micro",
                                "lifetime_spend_cap_micro",
                                "start_time",
                                "end_time",
                            )
                            if key in entity
                        },
                    }
                )
                if len(rows) > MAX_ENTITY_ROWS:
                    raise SnapchatClientError(
                        "snapchat_entity_rows_truncated",
                        "Snapchat entity discovery exceeded the safety limit.",
                    )
        return {
            "rows": rows,
            "coverage": _coverage(
                status="complete",
                data_state="confirmed_data" if rows else "confirmed_no_data",
                expected_requests=completed,
                completed_requests=completed,
                rows_received=len(rows),
            ),
        }

    @staticmethod
    def _extract_hour_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        wrappers = payload.get("timeseries_stats")
        if not isinstance(wrappers, list) or not wrappers:
            raise SnapchatClientError(
                "snapchat_hour_envelope_missing",
                "Snapchat HOUR response is missing timeseries_stats.",
            )
        rows: list[dict[str, Any]] = []
        for wrapper in wrappers:
            if not isinstance(wrapper, dict):
                raise SnapchatClientError(
                    "snapchat_hour_wrapper_invalid",
                    "Snapchat HOUR wrapper is invalid.",
                )
            sub_status = clean_text(wrapper.get("sub_request_status"), limit=40).upper()
            if sub_status and sub_status != "SUCCESS":
                raise SnapchatClientError(
                    "snapchat_hour_subrequest_failed",
                    "Snapchat HOUR sub-request failed.",
                    retryable=True,
                )
            stat = wrapper.get("timeseries_stat")
            if not isinstance(stat, dict):
                raise SnapchatClientError(
                    "snapchat_hour_stat_missing",
                    "Snapchat HOUR response is missing timeseries_stat.",
                )
            granularity = clean_text(stat.get("granularity"), limit=20).upper()
            if granularity and granularity != "HOUR":
                raise SnapchatClientError(
                    "snapchat_hour_granularity_invalid",
                    "Snapchat returned an unexpected reporting granularity.",
                )
            breakdown = stat.get("breakdown_stats")
            campaigns = breakdown.get("campaign") if isinstance(breakdown, dict) else None
            if not isinstance(campaigns, list):
                raise SnapchatClientError(
                    "snapchat_hour_campaign_breakdown_missing",
                    "Snapchat HOUR response is missing campaign breakdown.",
                )
            for campaign in campaigns:
                if not isinstance(campaign, dict):
                    raise SnapchatClientError(
                        "snapchat_hour_campaign_invalid",
                        "Snapchat HOUR campaign row is invalid.",
                    )
                campaign_id = clean_text(campaign.get("id"), limit=128)
                points = campaign.get("timeseries")
                if not campaign_id or not isinstance(points, list):
                    raise SnapchatClientError(
                        "snapchat_hour_campaign_identity_invalid",
                        "Snapchat HOUR campaign identity is invalid.",
                    )
                for point in points:
                    if not isinstance(point, dict) or not isinstance(point.get("stats"), dict):
                        raise SnapchatClientError(
                            "snapchat_hour_point_invalid",
                            "Snapchat HOUR point is invalid.",
                        )
                    metrics = dict(point["stats"])
                    if "spend" not in metrics:
                        raise SnapchatClientError(
                            "snapchat_hour_spend_missing",
                            "Snapchat HOUR point is missing financial spend.",
                        )
                    rows.append(
                        {
                            "campaign_id": campaign_id,
                            "start_time": point.get("start_time"),
                            "end_time": point.get("end_time"),
                            "metrics": metrics,
                        }
                    )
        return rows

    @staticmethod
    def _extract_breakdown_hour_rows(
        payload: dict[str, Any],
        *,
        entity_type: str,
        campaign_id: str,
    ) -> list[dict[str, Any]]:
        config = PERFORMANCE_BREAKDOWNS.get(entity_type)
        if config is None:
            raise ValueError(f"Unsupported Snapchat performance level: {entity_type}")
        wrappers = payload.get("timeseries_stats")
        if not isinstance(wrappers, list) or not wrappers:
            raise SnapchatClientError(
                f"snapchat_{entity_type}_hour_envelope_missing",
                f"Snapchat {entity_type} HOUR response is missing timeseries_stats.",
            )
        rows: list[dict[str, Any]] = []
        for wrapper in wrappers:
            if not isinstance(wrapper, dict):
                raise SnapchatClientError(
                    f"snapchat_{entity_type}_hour_wrapper_invalid",
                    f"Snapchat {entity_type} HOUR wrapper is invalid.",
                )
            sub_status = clean_text(wrapper.get("sub_request_status"), limit=40).upper()
            if sub_status and sub_status != "SUCCESS":
                raise SnapchatClientError(
                    f"snapchat_{entity_type}_hour_subrequest_failed",
                    f"Snapchat {entity_type} HOUR sub-request failed.",
                    retryable=True,
                )
            stat = wrapper.get("timeseries_stat")
            if not isinstance(stat, dict):
                raise SnapchatClientError(
                    f"snapchat_{entity_type}_hour_stat_missing",
                    f"Snapchat {entity_type} HOUR response is missing timeseries_stat.",
                )
            granularity = clean_text(stat.get("granularity"), limit=20).upper()
            if granularity and granularity != "HOUR":
                raise SnapchatClientError(
                    f"snapchat_{entity_type}_hour_granularity_invalid",
                    f"Snapchat returned an unexpected {entity_type} granularity.",
                )
            breakdown = stat.get("breakdown_stats")
            entities: list[dict[str, Any]] | None = None
            if isinstance(breakdown, dict):
                for key in config["response_keys"]:
                    value = breakdown.get(key)
                    if isinstance(value, list):
                        entities = value
                        break
            if entities is None:
                raise SnapchatClientError(
                    f"snapchat_{entity_type}_hour_breakdown_missing",
                    f"Snapchat HOUR response is missing the {entity_type} breakdown.",
                )
            for entity in entities:
                if not isinstance(entity, dict):
                    raise SnapchatClientError(
                        f"snapchat_{entity_type}_hour_row_invalid",
                        f"Snapchat {entity_type} HOUR row is invalid.",
                    )
                external_id = clean_text(entity.get("id"), limit=128)
                points = entity.get("timeseries")
                if not external_id or not isinstance(points, list):
                    raise SnapchatClientError(
                        f"snapchat_{entity_type}_hour_identity_invalid",
                        f"Snapchat {entity_type} HOUR identity is invalid.",
                    )
                for point in points:
                    if not isinstance(point, dict) or not isinstance(point.get("stats"), dict):
                        raise SnapchatClientError(
                            f"snapchat_{entity_type}_hour_point_invalid",
                            f"Snapchat {entity_type} HOUR point is invalid.",
                        )
                    metrics = dict(point["stats"])
                    if "spend" not in metrics:
                        raise SnapchatClientError(
                            f"snapchat_{entity_type}_hour_spend_missing",
                            f"Snapchat {entity_type} HOUR point is missing financial spend.",
                        )
                    rows.append(
                        {
                            "campaign_id": campaign_id,
                            config["identity_field"]: external_id,
                            "start_time": point.get("start_time"),
                            "end_time": point.get("end_time"),
                            "metrics": metrics,
                        }
                    )
        return rows

    async def fetch_breakdown_hourly_facts(
        self,
        account: dict[str, Any],
        *,
        campaign_ids: Iterable[str],
        entity_type: str,
        start_utc: datetime,
        end_utc: datetime,
        sync_run_id: str,
        action_report_time: str = "conversion",
        swipe_attribution_window: str = "28_DAY",
        view_attribution_window: str = "1_DAY",
        ad_squad_by_ad_id: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        config = PERFORMANCE_BREAKDOWNS.get(entity_type)
        if config is None:
            raise ValueError(f"Unsupported Snapchat performance level: {entity_type}")
        account_id = clean_text(
            account.get("ad_account_id") or account.get("external_account_id"),
            limit=128,
        )
        account_timezone = clean_text(account.get("timezone"), limit=80)
        currency = clean_text(account.get("currency"), limit=12).upper()
        if not account_id or not currency:
            raise SnapchatClientError(
                "snapchat_account_identity_incomplete",
                "Snapchat account ID or currency is missing.",
            )
        normalized_campaign_ids = sorted(
            {
                clean_text(value, limit=128)
                for value in campaign_ids
                if clean_text(value, limit=128)
            }
        )
        windows = split_hour_windows(
            start_utc,
            end_utc,
            account_timezone=account_timezone,
        )
        expected_requests = len(normalized_campaign_ids) * len(windows)
        remaining_budget = max(0, MAX_PROVIDER_CALLS - self.provider_calls)
        if expected_requests > remaining_budget:
            raise SnapchatClientError(
                f"snapchat_{entity_type}_provider_call_budget_exceeded",
                f"Snapchat {entity_type} sync exceeds the remaining provider call budget.",
                coverage=_coverage(
                    status="incomplete",
                    data_state="unknown_incomplete",
                    expected_requests=expected_requests,
                    completed_requests=0,
                    rows_received=0,
                    reason="provider_call_budget_exceeded",
                ),
            )

        facts: dict[tuple[str, datetime], dict[str, Any]] = {}
        completed_requests = 0
        source_windows: list[dict[str, datetime]] = []
        parent_lookup = {
            clean_text(key, limit=128): clean_text(value, limit=128)
            for key, value in dict(ad_squad_by_ad_id or {}).items()
            if clean_text(key, limit=128) and clean_text(value, limit=128)
        }
        try:
            async with self.client_factory(timeout=HTTP_TIMEOUT_SECONDS) as client:
                for window in windows:
                    source_windows.append(window.as_dict())
                    for campaign_id in normalized_campaign_ids:
                        url = f"{SNAPCHAT_API_BASE}/campaigns/{campaign_id}/stats"
                        params = {
                            "start_time": window.provider_start.isoformat(timespec="seconds"),
                            "end_time": window.provider_end.isoformat(timespec="seconds"),
                            "granularity": "HOUR",
                            "breakdown": config["request_value"],
                            "fields": ",".join(STAT_FIELDS),
                            "limit": 200,
                            "omit_empty": "true",
                            "conversion_source_types": "total",
                            "swipe_up_attribution_window": swipe_attribution_window,
                            "view_attribution_window": view_attribution_window,
                            "action_report_time": clean_text(
                                action_report_time,
                                limit=32,
                            ).lower(),
                        }
                        pages, page_count = await self._pages(client, url, params=params)
                        completed_requests += page_count
                        for payload in pages:
                            rows = self._extract_breakdown_hour_rows(
                                payload,
                                entity_type=entity_type,
                                campaign_id=campaign_id,
                            )
                            for row in rows:
                                point_start = _parse_datetime(
                                    row.get("start_time"),
                                    field="start_time",
                                )
                                point_end = _parse_datetime(
                                    row.get("end_time"),
                                    field="end_time",
                                )
                                if point_end != point_start + timedelta(hours=1):
                                    raise SnapchatClientError(
                                        f"snapchat_{entity_type}_hour_window_invalid",
                                        f"Snapchat returned a non-hourly {entity_type} point.",
                                    )
                                if point_start < window.start_utc or point_end > window.end_utc:
                                    raise SnapchatClientError(
                                        f"snapchat_{entity_type}_hour_window_mismatch",
                                        f"Snapchat returned {entity_type} data outside the requested window.",
                                    )
                                external_id = str(row[config["identity_field"]])
                                metrics = row["metrics"]
                                fact = {
                                    "user_id": self.user_id,
                                    "provider": SNAPCHAT_PROVIDER,
                                    "ad_account_id": account_id,
                                    "campaign_id": campaign_id,
                                    "hour_start_utc": point_start,
                                    "hour_end_utc": point_end,
                                    "account_timezone": account_timezone,
                                    "currency": currency,
                                    "action_report_time": clean_text(
                                        action_report_time,
                                        limit=32,
                                    ).lower(),
                                    "attribution_windows": {
                                        "swipe": swipe_attribution_window,
                                        "view": view_attribution_window,
                                    },
                                    "spend_native": float(
                                        _number(metrics.get("spend"), field="spend")
                                    )
                                    / 1_000_000,
                                    "impressions": _number(
                                        metrics.get("impressions"),
                                        field="impressions",
                                        integer=True,
                                    ),
                                    "swipes": _number(
                                        metrics.get("swipes"),
                                        field="swipes",
                                        integer=True,
                                    ),
                                    "video_views": _number(
                                        metrics.get("video_views"),
                                        field="video_views",
                                        integer=True,
                                    ),
                                    "purchases": _number(
                                        metrics.get("conversion_purchases"),
                                        field="conversion_purchases",
                                        integer=True,
                                    ),
                                    "purchase_value_native": float(
                                        _number(
                                            metrics.get("conversion_purchases_value"),
                                            field="conversion_purchases_value",
                                        )
                                    )
                                    / 1_000_000,
                                    "sync_run_id": str(sync_run_id),
                                    "source": {
                                        "api": "snapchat_marketing_api",
                                        "granularity": "HOUR",
                                        "breakdown": config["request_value"],
                                    },
                                }
                                if entity_type == "ad_squad":
                                    fact["ad_squad_id"] = external_id
                                else:
                                    fact["ad_id"] = external_id
                                    fact["ad_squad_id"] = parent_lookup.get(external_id) or None
                                key = (external_id, point_start)
                                existing = facts.get(key)
                                if existing is not None and existing != fact:
                                    raise SnapchatClientError(
                                        f"snapchat_{entity_type}_hour_duplicate_conflict",
                                        f"Snapchat returned conflicting duplicate {entity_type} rows.",
                                    )
                                facts[key] = fact
        except SnapchatClientError as exc:
            if not exc.coverage:
                exc.coverage = _coverage(
                    status="incomplete",
                    data_state="unknown_incomplete",
                    expected_requests=max(expected_requests, completed_requests + 1),
                    completed_requests=completed_requests,
                    rows_received=len(facts),
                    reason=exc.code,
                )
            raise
        except SnapchatTokenStoreError as exc:
            raise SnapchatClientError(
                exc.code,
                exc.message,
                retryable=exc.retryable,
                needs_reauth=exc.needs_reauth,
                coverage=_coverage(
                    status="incomplete",
                    data_state="unknown_incomplete",
                    expected_requests=max(expected_requests, completed_requests + 1),
                    completed_requests=completed_requests,
                    rows_received=len(facts),
                    reason=exc.code,
                ),
            ) from exc

        rows = list(facts.values())
        all_zero = bool(rows) and all(
            float(row["spend_native"]) == 0
            and int(row["impressions"]) == 0
            and int(row["swipes"]) == 0
            for row in rows
        )
        data_state = (
            "confirmed_no_data"
            if not rows
            else "confirmed_zero"
            if all_zero
            else "confirmed_data"
        )
        coverage = _coverage(
            status="complete",
            data_state=data_state,
            expected_requests=completed_requests,
            completed_requests=completed_requests,
            rows_received=len(rows),
        )
        for row in rows:
            row["coverage"] = dict(coverage)
            row["source"] = {
                **dict(row.get("source") or {}),
                "request_windows": source_windows,
            }
        return {
            "rows": rows,
            "coverage": coverage,
            "provider_calls": self.provider_calls,
            "request_windows": source_windows,
            "campaigns_requested": len(normalized_campaign_ids),
        }

    async def fetch_hourly_facts(
        self,
        account: dict[str, Any],
        *,
        start_utc: datetime,
        end_utc: datetime,
        sync_run_id: str,
        action_report_time: str = "conversion",
        swipe_attribution_window: str = "28_DAY",
        view_attribution_window: str = "1_DAY",
    ) -> dict[str, Any]:
        account_id = clean_text(
            account.get("ad_account_id") or account.get("external_account_id"),
            limit=128,
        )
        account_timezone = clean_text(account.get("timezone"), limit=80)
        currency = clean_text(account.get("currency"), limit=12).upper()
        if not account_id or not currency:
            raise SnapchatClientError(
                "snapchat_account_identity_incomplete",
                "Snapchat account ID or currency is missing.",
            )
        windows = split_hour_windows(
            start_utc,
            end_utc,
            account_timezone=account_timezone,
        )
        completed_requests = 0
        campaign_facts: dict[tuple[str, datetime], dict[str, Any]] = {}
        source_windows: list[dict[str, datetime]] = []
        try:
            async with self.client_factory(timeout=HTTP_TIMEOUT_SECONDS) as client:
                for window in windows:
                    url = f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/stats"
                    params = {
                        "start_time": window.provider_start.isoformat(timespec="seconds"),
                        "end_time": window.provider_end.isoformat(timespec="seconds"),
                        "granularity": "HOUR",
                        "breakdown": "campaign",
                        "fields": ",".join(STAT_FIELDS),
                        "limit": 200,
                        "omit_empty": "false",
                        "conversion_source_types": "total",
                        "swipe_up_attribution_window": swipe_attribution_window,
                        "view_attribution_window": view_attribution_window,
                        "action_report_time": clean_text(action_report_time, limit=32).lower(),
                    }
                    pages, page_count = await self._pages(client, url, params=params)
                    completed_requests += page_count
                    source_windows.append(window.as_dict())
                    for payload in pages:
                        for row in self._extract_hour_rows(payload):
                            point_start = _parse_datetime(row.get("start_time"), field="start_time")
                            point_end = _parse_datetime(row.get("end_time"), field="end_time")
                            if point_end != point_start + timedelta(hours=1):
                                raise SnapchatClientError(
                                    "snapchat_hour_window_invalid",
                                    "Snapchat returned a non-hourly point.",
                                )
                            if point_start < window.start_utc or point_end > window.end_utc:
                                raise SnapchatClientError(
                                    "snapchat_hour_window_mismatch",
                                    "Snapchat returned data outside the requested window.",
                                )
                            metrics = row["metrics"]
                            fact = {
                                "user_id": self.user_id,
                                "provider": SNAPCHAT_PROVIDER,
                                "ad_account_id": account_id,
                                "campaign_id": row["campaign_id"],
                                "hour_start_utc": point_start,
                                "hour_end_utc": point_end,
                                "account_timezone": account_timezone,
                                "currency": currency,
                                "action_report_time": clean_text(
                                    action_report_time,
                                    limit=32,
                                ).lower(),
                                "attribution_windows": {
                                    "swipe": swipe_attribution_window,
                                    "view": view_attribution_window,
                                },
                                "spend_native": float(
                                    _number(metrics.get("spend"), field="spend")
                                )
                                / 1_000_000,
                                "impressions": _number(
                                    metrics.get("impressions"),
                                    field="impressions",
                                    integer=True,
                                ),
                                "swipes": _number(
                                    metrics.get("swipes"),
                                    field="swipes",
                                    integer=True,
                                ),
                                "video_views": _number(
                                    metrics.get("video_views"),
                                    field="video_views",
                                    integer=True,
                                ),
                                "purchases": _number(
                                    metrics.get("conversion_purchases"),
                                    field="conversion_purchases",
                                    integer=True,
                                ),
                                "purchase_value_native": float(
                                    _number(
                                        metrics.get("conversion_purchases_value"),
                                        field="conversion_purchases_value",
                                    )
                                )
                                / 1_000_000,
                                "sync_run_id": str(sync_run_id),
                                "source": {
                                    "api": "snapchat_marketing_api",
                                    "granularity": "HOUR",
                                    "breakdown": "campaign",
                                },
                            }
                            key = (str(row["campaign_id"]), point_start)
                            existing = campaign_facts.get(key)
                            if existing is not None and existing != fact:
                                raise SnapchatClientError(
                                    "snapchat_hour_duplicate_conflict",
                                    "Snapchat returned conflicting duplicate HOUR rows.",
                                )
                            campaign_facts[key] = fact
        except SnapchatClientError as exc:
            exc.coverage = _coverage(
                status="incomplete",
                data_state="unknown_incomplete",
                expected_requests=max(len(windows), completed_requests + 1),
                completed_requests=completed_requests,
                rows_received=len(campaign_facts),
                reason=exc.code,
            )
            raise
        except SnapchatTokenStoreError as exc:
            raise SnapchatClientError(
                exc.code,
                exc.message,
                retryable=exc.retryable,
                needs_reauth=exc.needs_reauth,
                coverage=_coverage(
                    status="incomplete",
                    data_state="unknown_incomplete",
                    expected_requests=max(len(windows), completed_requests + 1),
                    completed_requests=completed_requests,
                    rows_received=len(campaign_facts),
                    reason=exc.code,
                ),
            ) from exc

        campaign_rows = list(campaign_facts.values())
        account_buckets: dict[datetime, dict[str, Any]] = {}
        for fact in campaign_rows:
            bucket = account_buckets.setdefault(
                fact["hour_start_utc"],
                {
                    **fact,
                    "campaign_id": None,
                    "spend_native": 0.0,
                    "impressions": 0,
                    "swipes": 0,
                    "video_views": 0,
                    "purchases": 0,
                    "purchase_value_native": 0.0,
                },
            )
            for field in (
                "spend_native",
                "impressions",
                "swipes",
                "video_views",
                "purchases",
                "purchase_value_native",
            ):
                bucket[field] += fact[field]
        account_rows = list(account_buckets.values())
        rows = [*account_rows, *campaign_rows]
        all_zero = bool(account_rows) and all(
            float(row["spend_native"]) == 0
            and int(row["impressions"]) == 0
            and int(row["swipes"]) == 0
            for row in account_rows
        )
        data_state = (
            "confirmed_no_data"
            if not rows
            else "confirmed_zero"
            if all_zero
            else "confirmed_data"
        )
        coverage = _coverage(
            status="complete",
            data_state=data_state,
            expected_requests=completed_requests,
            completed_requests=completed_requests,
            rows_received=len(rows),
        )
        for row in rows:
            row["coverage"] = dict(coverage)
            row["source"] = {
                **dict(row.get("source") or {}),
                "request_windows": source_windows,
            }
        return {
            "rows": rows,
            "campaign_rows": campaign_rows,
            "account_rows": account_rows,
            "coverage": coverage,
            "provider_calls": self.provider_calls,
            "request_windows": source_windows,
        }


__all__ = [
    "HourWindow",
    "MAX_HOUR_WINDOW",
    "SnapchatClientError",
    "SnapchatV2Client",
    "split_hour_windows",
]
