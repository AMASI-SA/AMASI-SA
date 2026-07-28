"""V2-owned, analytics-only Snapchat multi-account backfill.

The legacy Snapchat collections remain transitional credential/account
sources during Phase 1.  Operations are owned here: this module fetches
provider analytics and writes only the bounded Snapchat fact collections
plus per-account sync metadata.  It has no dependency on either legacy
router and no accounting or campaign bridge.
"""
from __future__ import annotations

import logging
import math
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

import httpx
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)

SNAPCHAT_TOKEN_URL = "https://accounts.snapchat.com/login/oauth2/access_token"
SNAPCHAT_API_BASE = "https://adsapi.snapchat.com/v1"
SYNC_ENABLED_ENV = "MEZAN_SNAPCHAT_ANALYTICS_SYNC_V2_ENABLED"
BUSINESS_TIMEZONE = "Asia/Riyadh"
MAX_SYNC_DAYS = 62
MAX_SYNC_ACCOUNTS = 5
MAX_PROVIDER_CALLS = 400
USD_TO_SAR = 3.75


class SnapchatAnalyticsSyncInput(BaseModel):
    """Bounded V2 request: trailing days, or an explicit inclusive range."""

    days: int = Field(default=30, ge=1, le=MAX_SYNC_DAYS)
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=128)


class SnapchatAnalyticsSyncError(Exception):
    """Safe operational failure suitable for an API error envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        retryable: bool = False,
        result: Optional[dict] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.result = result or {}


def snapchat_analytics_sync_enabled() -> bool:
    """Fail closed for every value except the documented true allowlist."""
    value = str(os.environ.get(SYNC_ENABLED_ENV, "true")).strip().lower()
    return value in {"1", "true", "on", "yes", "enabled"}


def _riyadh_timezone():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(BUSINESS_TIMEZONE)
    except ImportError:  # pragma: no cover - Python 3.9+ in production
        return timezone(timedelta(hours=3))


def _parse_date(value: str, *, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise SnapchatAnalyticsSyncError(
            "invalid_date",
            f"{field} must use YYYY-MM-DD.",
            status_code=400,
        ) from exc


def enumerate_sync_dates(
    payload: SnapchatAnalyticsSyncInput,
    *,
    today: Optional[date] = None,
) -> list[date]:
    """Resolve the request to an inclusive, Riyadh-anchored date list."""
    today_local = today or datetime.now(_riyadh_timezone()).date()
    has_from = bool(payload.from_date)
    has_to = bool(payload.to_date)
    if has_from != has_to:
        raise SnapchatAnalyticsSyncError(
            "date_range_incomplete",
            "from_date and to_date must be supplied together.",
            status_code=400,
        )
    if has_from and has_to:
        start_date = _parse_date(payload.from_date or "", field="from_date")
        end_date = _parse_date(payload.to_date or "", field="to_date")
        if end_date < start_date:
            raise SnapchatAnalyticsSyncError(
                "invalid_date_range",
                "to_date must be on or after from_date.",
                status_code=400,
            )
        span = (end_date - start_date).days + 1
        if span > MAX_SYNC_DAYS:
            raise SnapchatAnalyticsSyncError(
                "date_range_too_wide",
                f"Date range cannot exceed {MAX_SYNC_DAYS} days.",
                status_code=400,
            )
        return [start_date + timedelta(days=offset) for offset in range(span)]
    start_date = today_local - timedelta(days=payload.days - 1)
    return [start_date + timedelta(days=offset) for offset in range(payload.days)]


def _parse_snap_conversion_payload(payload: object) -> dict:
    """Parse conversions without treating omitted metrics as provider zero."""
    if not isinstance(payload, dict):
        return {
            "purchases": None,
            "revenue_value_micro": None,
            "conversion_data_status": "unavailable",
            "conversion_data_error": "invalid_payload",
        }

    paging = payload.get("paging")
    if isinstance(paging, dict) and paging.get("next_link"):
        return {
            "purchases": None,
            "revenue_value_micro": None,
            "conversion_data_status": "unavailable",
            "conversion_data_error": "pagination_incomplete",
        }

    wrapped_stats: list[tuple[str, dict, dict]] = []
    structural_invalid = False
    for group_key, stat_key in (
        ("timeseries_stats", "timeseries_stat"),
        ("total_stats", "total_stat"),
    ):
        group = payload.get(group_key)
        if group is None:
            continue
        if not isinstance(group, list):
            structural_invalid = True
            continue
        for wrapped in group:
            if not isinstance(wrapped, dict):
                structural_invalid = True
                continue
            stat = wrapped.get(stat_key, wrapped)
            if isinstance(stat, dict):
                wrapped_stats.append((group_key, wrapped, stat))
            else:
                structural_invalid = True

    status_containers = [payload] + [
        candidate
        for _, wrapped, stat in wrapped_stats
        for candidate in (wrapped, stat)
    ]
    for _, _, stat in wrapped_stats:
        breakdown = stat.get("breakdown_stats")
        if not isinstance(breakdown, dict):
            continue
        for entities in breakdown.values():
            if isinstance(entities, list):
                status_containers.extend(
                    entity for entity in entities if isinstance(entity, dict)
                )

    failed_statuses = []
    for container in status_containers:
        for status_key in ("request_status", "sub_request_status"):
            status_value = container.get(status_key)
            if status_value is None:
                continue
            if isinstance(status_value, dict):
                status_value = (
                    status_value.get("status")
                    or status_value.get("code")
                    or status_value.get("message")
                )
            normalized = str(status_value or "").strip().upper()
            if normalized and (
                "FAIL" in normalized
                or "ERROR" in normalized
                or normalized in {"INVALID", "CANCELLED"}
            ):
                failed_statuses.append(normalized[:80])
    if failed_statuses:
        return {
            "purchases": None,
            "revenue_value_micro": None,
            "conversion_data_status": "unavailable",
            "conversion_data_error": (
                "provider_status:" + ",".join(sorted(set(failed_statuses)))
            )[:240],
        }

    totals = {
        "conversion_purchases": 0,
        "conversion_purchases_value": 0,
    }
    seen = {key: False for key in totals}
    invalid = {key: structural_invalid for key in totals}
    metric_points: list[dict] = []
    for group_key, _, stat in wrapped_stats:
        breakdown = stat.get("breakdown_stats")
        breakdown_points: list[Any] = []
        if isinstance(breakdown, dict):
            for entities in breakdown.values():
                if not isinstance(entities, list):
                    for key in invalid:
                        invalid[key] = True
                    continue
                for entity in entities:
                    if not isinstance(entity, dict):
                        for key in invalid:
                            invalid[key] = True
                        continue
                    if group_key == "timeseries_stats":
                        points = entity.get("timeseries")
                        if isinstance(points, list):
                            breakdown_points.extend(points)
                        else:
                            for key in invalid:
                                invalid[key] = True
                    else:
                        breakdown_points.append(entity)
        if breakdown_points:
            points = breakdown_points
        elif group_key == "timeseries_stats":
            points = stat.get("timeseries")
        else:
            points = [stat]
        if not isinstance(points, list):
            for key in invalid:
                invalid[key] = True
            continue
        for point in points:
            stats = point.get("stats") if isinstance(point, dict) else None
            if isinstance(stats, dict):
                metric_points.append(stats)
                for key in invalid:
                    if key not in stats:
                        invalid[key] = True
            else:
                for key in invalid:
                    invalid[key] = True

    if not metric_points:
        return {
            "purchases": None,
            "revenue_value_micro": None,
            "conversion_data_status": "unavailable",
            "conversion_data_error": "no_conversion_stats",
        }

    for stats in metric_points:
        for key in totals:
            if key not in stats:
                invalid[key] = True
                continue
            value = stats.get(key)
            if isinstance(value, bool) or value is None:
                invalid[key] = True
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError, OverflowError):
                invalid[key] = True
                continue
            if parsed < 0:
                invalid[key] = True
                continue
            totals[key] += parsed
            seen[key] = True

    purchases_known = (
        seen["conversion_purchases"] and not invalid["conversion_purchases"]
    )
    revenue_known = (
        seen["conversion_purchases_value"]
        and not invalid["conversion_purchases_value"]
    )
    if purchases_known and revenue_known:
        status = "available"
        error = None
    elif purchases_known or revenue_known:
        status = "partial"
        missing = []
        if not purchases_known:
            missing.append("conversion_purchases")
        if not revenue_known:
            missing.append("conversion_purchases_value")
        error = "missing_or_invalid:" + ",".join(missing)
    else:
        status = "unavailable"
        error = "conversion_fields_missing_or_invalid"
    return {
        "purchases": totals["conversion_purchases"] if purchases_known else None,
        "revenue_value_micro": (
            totals["conversion_purchases_value"] if revenue_known else None
        ),
        "conversion_data_status": status,
        "conversion_data_error": error,
    }


def _parse_snap_spend_payload(payload: object) -> dict:
    """Parse provider-proven spend without turning missing data into zero."""
    if not isinstance(payload, dict):
        return {
            "spend_micro": None,
            "spend_data_status": "unavailable",
            "spend_data_error": "invalid_spend_payload",
        }
    paging = payload.get("paging")
    if isinstance(paging, dict) and paging.get("next_link"):
        return {
            "spend_micro": None,
            "spend_data_status": "unavailable",
            "spend_data_error": "spend_pagination_incomplete",
        }
    timeseries = payload.get("timeseries_stats")
    legacy_stats = payload.get("stats")
    if timeseries is not None and not isinstance(timeseries, list):
        return {
            "spend_micro": None,
            "spend_data_status": "unavailable",
            "spend_data_error": "invalid_spend_timeseries",
        }
    if legacy_stats is not None and not isinstance(legacy_stats, list):
        return {
            "spend_micro": None,
            "spend_data_status": "unavailable",
            "spend_data_error": "invalid_spend_stats",
        }

    status_containers = [payload]
    metric_points: list[dict] = []
    invalid = False
    for wrapped in timeseries or []:
        if not isinstance(wrapped, dict):
            invalid = True
            continue
        status_containers.append(wrapped)
        stat = wrapped.get("timeseries_stat", wrapped)
        if not isinstance(stat, dict):
            invalid = True
            continue
        status_containers.append(stat)
        points = stat.get("timeseries")
        if not isinstance(points, list):
            invalid = True
            continue
        for point in points:
            stats = point.get("stats") if isinstance(point, dict) else None
            if not isinstance(stats, dict) or "spend" not in stats:
                invalid = True
                continue
            metric_points.append(stats)
    if not metric_points:
        for entry in legacy_stats or []:
            if not isinstance(entry, dict) or "spend" not in entry:
                invalid = True
                continue
            metric_points.append(entry)

    failed_statuses = []
    for container in status_containers:
        for status_key in ("request_status", "sub_request_status"):
            status_value = container.get(status_key)
            if isinstance(status_value, dict):
                status_value = (
                    status_value.get("status")
                    or status_value.get("code")
                    or status_value.get("message")
                )
            normalized = str(status_value or "").strip().upper()
            if normalized and (
                "FAIL" in normalized
                or "ERROR" in normalized
                or normalized in {"INVALID", "CANCELLED"}
            ):
                failed_statuses.append(normalized[:80])
    if failed_statuses:
        return {
            "spend_micro": None,
            "spend_data_status": "unavailable",
            "spend_data_error": (
                "spend_provider_status:"
                + ",".join(sorted(set(failed_statuses)))
            )[:240],
        }

    total_micro = 0
    seen = False
    for stats in metric_points:
        value = stats.get("spend")
        if isinstance(value, bool) or value is None:
            invalid = True
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            invalid = True
            continue
        if parsed < 0:
            invalid = True
            continue
        total_micro += parsed
        seen = True
    if invalid:
        return {
            "spend_micro": None,
            "spend_data_status": "unavailable",
            "spend_data_error": "invalid_spend_metric",
        }
    if not seen:
        return {
            "spend_micro": None,
            "spend_data_status": "unavailable",
            "spend_data_error": "spend_metric_missing",
        }
    return {
        "spend_micro": total_micro,
        "spend_data_status": "available",
        "spend_data_error": None,
    }


async def _fetch_snap_conversion_metrics(
    http: httpx.AsyncClient,
    url: str,
    headers: dict,
    params: dict,
) -> dict:
    """Fetch conversion metrics fail-closed while leaving spend usable."""
    try:
        response = await http.get(url, headers=headers, params=params)
    except httpx.HTTPError as exc:
        return {
            "purchases": None,
            "revenue_value_micro": None,
            "conversion_data_status": "unavailable",
            "conversion_data_error": f"network_error:{type(exc).__name__}",
        }
    if response.status_code >= 400:
        return {
            "purchases": None,
            "revenue_value_micro": None,
            "conversion_data_status": "unavailable",
            "conversion_data_error": f"http_{response.status_code}",
        }
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return {
            "purchases": None,
            "revenue_value_micro": None,
            "conversion_data_status": "unavailable",
            "conversion_data_error": "invalid_json",
        }
    return _parse_snap_conversion_payload(payload)


def _merge_snap_conversion_metrics(
    existing_row: Optional[dict],
    conversion: dict,
    *,
    revenue_native: Optional[float],
    revenue_sar: Optional[float],
) -> dict:
    """Merge a refresh without erasing previously proven conversion facts."""
    existing = existing_row or {}
    existing_status = existing.get("conversion_data_status")
    existing_purchases_known = (
        existing_status in {"available", "partial"}
        and existing.get("purchases") is not None
    )
    existing_revenue_known = (
        existing_status in {"available", "partial"}
        and existing.get("revenue_sar") is not None
    )
    if existing_status not in {"available", "partial", "unavailable"}:
        try:
            existing_purchases_known = float(existing.get("purchases")) > 0
        except (TypeError, ValueError):
            existing_purchases_known = False
        try:
            existing_revenue_known = float(existing.get("revenue_sar")) > 0
        except (TypeError, ValueError):
            existing_revenue_known = False
    stored_purchases = (
        conversion.get("purchases")
        if conversion.get("purchases") is not None
        else existing.get("purchases")
        if existing_purchases_known
        else None
    )
    stored_revenue_native = (
        revenue_native
        if revenue_native is not None
        else existing.get("revenue_native")
        if existing_revenue_known
        else None
    )
    stored_revenue_sar = (
        revenue_sar
        if revenue_sar is not None
        else existing.get("revenue_sar")
        if existing_revenue_known
        else None
    )
    purchases_known = stored_purchases is not None
    revenue_known = stored_revenue_sar is not None
    if purchases_known and revenue_known:
        stored_status = "available"
        stored_error = None
    elif purchases_known or revenue_known:
        stored_status = "partial"
        stored_error = (
            conversion.get("conversion_data_error")
            or "one_conversion_metric_unknown"
        )
    else:
        stored_status = "unavailable"
        stored_error = conversion.get("conversion_data_error")
    return {
        "purchases": stored_purchases,
        "revenue_native": stored_revenue_native,
        "revenue_sar": stored_revenue_sar,
        "conversion_data_status": stored_status,
        "conversion_data_error": stored_error,
        "conversion_refresh_status": conversion.get("conversion_data_status"),
        "conversion_refresh_error": conversion.get("conversion_data_error"),
    }


def _aggregate_snap_conversion_rows(account_rows: list[dict]) -> dict:
    """Aggregate conversions only when every account metric is known."""

    def metric_known(row: dict, key: str) -> bool:
        status = row.get("conversion_data_status")
        if status in {"available", "partial"}:
            return row.get(key) is not None
        if status == "unavailable":
            return False
        try:
            return float(row.get(key)) > 0
        except (TypeError, ValueError):
            return False

    purchases_complete = bool(account_rows) and all(
        metric_known(row, "purchases") for row in account_rows
    )
    revenue_complete = bool(account_rows) and all(
        metric_known(row, "revenue_sar") for row in account_rows
    )
    purchases = (
        sum(int(row["purchases"]) for row in account_rows)
        if purchases_complete
        else None
    )
    revenue = (
        round(sum(float(row["revenue_sar"]) for row in account_rows), 2)
        if revenue_complete
        else None
    )
    if purchases_complete and revenue_complete:
        status = "available"
    elif purchases_complete or revenue_complete:
        status = "partial"
    else:
        status = "unavailable"
    errors = sorted(
        {
            str(row.get("conversion_data_error"))[:120]
            for row in account_rows
            if row.get("conversion_data_error")
        }
    )
    return {
        "purchases": purchases,
        "revenue": revenue,
        "conversion_data_status": status,
        "conversion_data_error": (
            ";".join(errors)[:240]
            if errors
            else None
            if status == "available"
            else "one_or_more_accounts_unknown"
        ),
        "conversion_accounts_total": len(account_rows),
        "conversion_accounts_complete": sum(
            metric_known(row, "purchases") and metric_known(row, "revenue_sar")
            for row in account_rows
        ),
    }


class SnapchatAnalyticsBackfill:
    """Execute one bounded, multi-account analytics refresh."""

    def __init__(
        self,
        db: Any,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.db = db
        self._now = now
        self._usd_rate_cache: dict[str, float] = {}

    def _now_iso(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat()

    async def _ensure_access_token(self, user_id: str) -> str:
        connection = await self.db.snapchat_connections.find_one(
            {"user_id": user_id},
            {"_id": 0},
        )
        if not connection or not connection.get("refresh_token"):
            raise SnapchatAnalyticsSyncError(
                "snapchat_not_connected",
                "Snapchat is not connected.",
                status_code=409,
            )
        try:
            expires_at = datetime.fromisoformat(
                str(connection.get("access_token_expires_at") or "")
            )
        except (TypeError, ValueError):
            expires_at = None
        if (
            connection.get("access_token")
            and expires_at
            and expires_at
            > datetime.now(timezone.utc) + timedelta(seconds=60)
        ):
            return str(connection["access_token"])

        request_data = {
            "refresh_token": connection["refresh_token"],
            "client_id": connection.get("client_id"),
            "client_secret": connection.get("client_secret"),
            "grant_type": "refresh_token",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as http:
                response = await http.post(SNAPCHAT_TOKEN_URL, data=request_data)
                if (
                    response.status_code == 400
                    and "invalid_client" in (response.text or "").lower()
                ):
                    response = await http.post(
                        SNAPCHAT_TOKEN_URL,
                        data={
                            "refresh_token": connection["refresh_token"],
                            "grant_type": "refresh_token",
                        },
                        auth=(
                            connection.get("client_id") or "",
                            connection.get("client_secret") or "",
                        ),
                    )
                response.raise_for_status()
                token_payload = response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise SnapchatAnalyticsSyncError(
                    "snapchat_needs_reauth",
                    "Snapchat authorization must be renewed.",
                    status_code=409,
                    retryable=False,
                ) from exc
            raise SnapchatAnalyticsSyncError(
                "snapchat_token_refresh_rejected",
                "Snapchat rejected the token refresh.",
                status_code=502,
                retryable=exc.response.status_code >= 500,
            ) from exc
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise SnapchatAnalyticsSyncError(
                "snapchat_token_refresh_failed",
                "Snapchat token refresh failed.",
                status_code=502,
                retryable=True,
            ) from exc

        access_token = token_payload.get("access_token")
        if not access_token:
            raise SnapchatAnalyticsSyncError(
                "snapchat_token_missing",
                "Snapchat token response was incomplete.",
                status_code=502,
                retryable=True,
            )
        refresh_token = (
            token_payload.get("refresh_token") or connection["refresh_token"]
        )
        expires_in = int(token_payload.get("expires_in", 3600))
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).isoformat()
        # Transitional exception: provider token rotation remains in the
        # existing credential row until credentials are migrated to V2.
        await self.db.snapchat_connections.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "access_token_expires_at": expires_at,
                    "updated_at": self._now_iso(),
                }
            },
        )
        return str(access_token)

    async def _usd_to_sar(self, user_id: str) -> float:
        if user_id in self._usd_rate_cache:
            return self._usd_rate_cache[user_id]
        try:
            row = await self.db.ads_currency_settings.find_one(
                {"user_id": user_id},
                {"_id": 0, "usd_to_sar_rate": 1},
            )
        except Exception:  # noqa: BLE001 - bounded fallback, read only
            row = None
        if row and row.get("usd_to_sar_rate") is not None:
            try:
                rate = float(row["usd_to_sar_rate"])
            except (TypeError, ValueError, OverflowError) as exc:
                raise SnapchatAnalyticsSyncError(
                    "snapchat_usd_rate_unverified",
                    "The configured USD to SAR rate is invalid.",
                    status_code=409,
                ) from exc
            if not math.isfinite(rate) or rate <= 0:
                raise SnapchatAnalyticsSyncError(
                    "snapchat_usd_rate_unverified",
                    "The configured USD to SAR rate is invalid.",
                    status_code=409,
                )
            self._usd_rate_cache[user_id] = rate
            return rate
        self._usd_rate_cache[user_id] = USD_TO_SAR
        return USD_TO_SAR

    async def _to_sar(
        self,
        amount: float,
        currency: str,
        *,
        user_id: str,
    ) -> tuple[float, float]:
        normalized = (currency or "").strip().upper()
        if normalized in {"SAR", "ر.س"}:
            return round(amount, 2), 1.0
        if normalized == "USD":
            rate = await self._usd_to_sar(user_id)
            return round(amount * rate, 2), rate
        raise SnapchatAnalyticsSyncError(
            "snapchat_currency_unverified",
            "Snapchat account currency is missing or unsupported.",
            status_code=409,
        )

    async def _sync_one_account(
        self,
        http: httpx.AsyncClient,
        access_token: str,
        user_id: str,
        account: dict,
        dates: list[date],
        riyadh_tz: Any,
    ) -> tuple[int, list[dict], list[str]]:
        account_id = str(account["ad_account_id"])
        raw_currency = str(account.get("currency_native") or "").strip().upper()
        currency = "SAR" if raw_currency == "ر.س" else raw_currency
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        stats_url = f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/stats"
        saved = 0
        errors: list[dict] = []
        successful_dates: list[str] = []
        if currency not in {"SAR", "USD"}:
            currency_error = (
                "currency_missing"
                if not currency
                else "currency_unsupported"
            )
            errors.append(
                {
                    "ad_account_id": account_id,
                    "date": None,
                    "kind": "currency_quality",
                    "error": currency_error,
                }
            )
            finished_at = self._now_iso()
            await self.db.snapchat_ad_accounts.update_one(
                {"user_id": user_id, "ad_account_id": account_id},
                {
                    "$set": {
                        "last_sync_attempt_at": finished_at,
                        "last_sync_status": "failed",
                        "last_sync_rows_saved": 0,
                        "last_sync_error_count": 1,
                        "last_sync_error_code": currency_error,
                        "sync_owner": "integrations_v2",
                    }
                },
            )
            return 0, errors, successful_dates
        for day in dates:
            start_local = datetime(
                day.year,
                day.month,
                day.day,
                tzinfo=riyadh_tz,
            )
            end_local = start_local + timedelta(days=1)
            spend_params = {
                "start_time": start_local.isoformat(timespec="seconds"),
                "end_time": end_local.isoformat(timespec="seconds"),
                "granularity": "HOUR",
                "fields": "spend",
            }
            try:
                response = await http.get(
                    stats_url,
                    headers=headers,
                    params=spend_params,
                )
                response.raise_for_status()
                spend_payload = response.json()
            except httpx.HTTPStatusError as exc:
                errors.append(
                    {
                        "ad_account_id": account_id,
                        "date": day.isoformat(),
                        "error": f"provider_http_{exc.response.status_code}",
                    }
                )
                continue
            except httpx.HTTPError as exc:
                errors.append(
                    {
                        "ad_account_id": account_id,
                        "date": day.isoformat(),
                        "error": f"network_error:{type(exc).__name__}",
                    }
                )
                continue
            except (TypeError, ValueError):
                errors.append(
                    {
                        "ad_account_id": account_id,
                        "date": day.isoformat(),
                        "error": "invalid_spend_json",
                    }
                )
                continue
            parsed_spend = _parse_snap_spend_payload(spend_payload)
            if parsed_spend["spend_data_status"] != "available":
                errors.append(
                    {
                        "ad_account_id": account_id,
                        "date": day.isoformat(),
                        "error": parsed_spend["spend_data_error"],
                    }
                )
                continue

            conversion = await _fetch_snap_conversion_metrics(
                http,
                stats_url,
                headers,
                {
                    "start_time": start_local.isoformat(timespec="seconds"),
                    "end_time": end_local.isoformat(timespec="seconds"),
                    "granularity": "HOUR",
                    "fields": (
                        "conversion_purchases,"
                        "conversion_purchases_value"
                    ),
                    "breakdown": "campaign",
                    "limit": 200,
                    "swipe_up_attribution_window": "28_DAY",
                    "view_attribution_window": "1_DAY",
                },
            )
            if conversion["conversion_data_status"] != "available":
                errors.append(
                    {
                        "ad_account_id": account_id,
                        "date": day.isoformat(),
                        "kind": "conversion_quality",
                        "error": conversion["conversion_data_error"],
                    }
                )

            spend_native = round(
                parsed_spend["spend_micro"] / 1_000_000,
                2,
            )
            revenue_micro = conversion["revenue_value_micro"]
            revenue_native = (
                round(revenue_micro / 1_000_000, 2)
                if revenue_micro is not None
                else None
            )
            spend_sar, fx_rate = await self._to_sar(
                spend_native,
                currency,
                user_id=user_id,
            )
            revenue_sar = (
                (
                    await self._to_sar(
                        revenue_native,
                        currency,
                        user_id=user_id,
                    )
                )[0]
                if revenue_native is not None
                else None
            )
            date_string = day.isoformat()
            existing = await self.db.snapchat_account_daily.find_one(
                {
                    "user_id": user_id,
                    "ad_account_id": account_id,
                    "date": date_string,
                },
                {
                    "_id": 0,
                    "spend": 1,
                    "spend_sar": 1,
                    "accounting_eligible": 1,
                    "accounting_spend_snapshot": 1,
                    "updated_at": 1,
                    "purchases": 1,
                    "revenue_native": 1,
                    "revenue_sar": 1,
                    "conversion_data_status": 1,
                },
            )
            conversion_patch = _merge_snap_conversion_metrics(
                existing,
                conversion,
                revenue_native=revenue_native,
                revenue_sar=revenue_sar,
            )
            snapshot_patch: dict[str, Any] = {}
            if existing and existing.get("accounting_eligible") is not False:
                prior_spend = existing.get("spend")
                if prior_spend is None:
                    prior_spend = existing.get("spend_sar")
                if prior_spend is not None:
                    snapshot_patch["accounting_spend_snapshot"] = prior_spend
            previous_updated_at: Any = (
                existing.get("updated_at")
                if existing
                else {"$exists": False}
            )
            write_query = {
                "user_id": user_id,
                "ad_account_id": account_id,
                "date": date_string,
                "updated_at": previous_updated_at,
            }
            now_iso = self._now_iso()
            try:
                result = await self.db.snapchat_account_daily.update_one(
                    write_query,
                    {
                        "$set": {
                            "user_id": user_id,
                            "ad_account_id": account_id,
                            "account_name": account.get("name") or "",
                            "date": date_string,
                            "spend_native": spend_native,
                            "currency_native": currency,
                            "fx_rate": fx_rate,
                            "spend_sar": spend_sar,
                            "spend": spend_sar,
                            **conversion_patch,
                            **snapshot_patch,
                            "ingestion_mode": "analytics_backfill",
                            "sync_owner": "integrations_v2",
                            "accounting_eligible": False,
                            "business_timezone": BUSINESS_TIMEZONE,
                            "ad_account_timezone": (
                                account.get("timezone") or ""
                            ),
                            "snap_day_start_riyadh": start_local.strftime(
                                "%Y-%m-%d %H:%M"
                            ),
                            "snap_day_end_riyadh": end_local.strftime(
                                "%Y-%m-%d %H:%M"
                            ),
                            "updated_at": now_iso,
                        },
                        "$setOnInsert": {"created_at": now_iso},
                    },
                    upsert=existing is None,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "ad_account_id": account_id,
                        "date": date_string,
                        "error": (
                            "concurrent_or_write_error:"
                            f"{type(exc).__name__}"
                        ),
                    }
                )
                continue
            if (
                existing is not None
                and getattr(result, "matched_count", 1) != 1
            ):
                errors.append(
                    {
                        "ad_account_id": account_id,
                        "date": date_string,
                        "error": "concurrent_update_detected",
                    }
                )
                continue
            saved += 1
            successful_dates.append(date_string)

        finished_at = self._now_iso()
        complete = saved == len(dates) and not errors
        sync_patch = {
            "last_sync_attempt_at": finished_at,
            "last_sync_status": "complete" if complete else "partial",
            "last_sync_rows_saved": saved,
            "last_sync_error_count": len(errors),
            "sync_owner": "integrations_v2",
        }
        if complete:
            sync_patch["last_sync_at"] = finished_at
            sync_patch["last_successful_sync_at"] = finished_at
        await self.db.snapchat_ad_accounts.update_one(
            {"user_id": user_id, "ad_account_id": account_id},
            {"$set": sync_patch},
        )
        return saved, errors, successful_dates

    async def _aggregate_day(
        self,
        user_id: str,
        date_string: str,
        *,
        expected_account_ids: list[str],
        successful_account_ids: list[str],
    ) -> Optional[dict]:
        successful = set(successful_account_ids)
        if not successful:
            return None
        expected = set(expected_account_ids)
        aggregate_ids = sorted(successful & expected)
        if not aggregate_ids:
            return None
        rows = await self.db.snapchat_account_daily.find(
            {
                "user_id": user_id,
                "date": date_string,
                "ad_account_id": {"$in": aggregate_ids},
            },
            {
                "_id": 0,
                "ad_account_id": 1,
                "spend_sar": 1,
                "revenue_sar": 1,
                "purchases": 1,
                "conversion_data_status": 1,
                "conversion_data_error": 1,
                "conversion_refresh_status": 1,
                "conversion_refresh_error": 1,
            },
        ).to_list(50)
        if not rows:
            return None
        present_ids = {
            str(row.get("ad_account_id"))
            for row in rows
            if row.get("ad_account_id")
        }
        quality_rows = list(rows)
        quality_rows.extend(
            {
                "purchases": None,
                "revenue_sar": None,
                "conversion_data_status": "unavailable",
                "conversion_data_error": "account_day_missing",
                "conversion_refresh_status": "unavailable",
                "conversion_refresh_error": "account_day_missing",
            }
            for _ in sorted(expected - present_ids)
        )
        conversions = _aggregate_snap_conversion_rows(quality_rows)
        refresh_statuses = [
            row.get("conversion_refresh_status")
            or row.get("conversion_data_status")
            or "unavailable"
            for row in quality_rows
        ]
        refresh_complete = sum(
            status == "available" for status in refresh_statuses
        )
        if refresh_statuses and refresh_complete == len(refresh_statuses):
            refresh_status = "available"
        elif refresh_complete or any(
            status == "partial" for status in refresh_statuses
        ):
            refresh_status = "partial"
        else:
            refresh_status = "unavailable"
        refresh_errors = sorted(
            {
                str(
                    row.get("conversion_refresh_error")
                    or row.get("conversion_data_error")
                )[:120]
                for row in quality_rows
                if (
                    row.get("conversion_refresh_error")
                    or row.get("conversion_data_error")
                )
            }
        )
        total_accounts = len(expected) or len(rows)
        complete_spend_accounts = sum(
            row.get("spend_sar") is not None for row in rows
        )
        spend_status = (
            "available"
            if complete_spend_accounts == total_accounts
            else "partial"
            if complete_spend_accounts
            else "unavailable"
        )
        now_iso = self._now_iso()
        await self.db.snapchat_daily_stats.update_one(
            {"user_id": user_id, "date": date_string},
            {
                "$set": {
                    "user_id": user_id,
                    "date": date_string,
                    "spend": round(
                        sum(float(row.get("spend_sar") or 0) for row in rows),
                        2,
                    ),
                    "spend_data_status": spend_status,
                    "spend_accounts_total": total_accounts,
                    "spend_accounts_complete": complete_spend_accounts,
                    **conversions,
                    "conversion_refresh_status": refresh_status,
                    "conversion_refresh_error": (
                        ";".join(refresh_errors)[:240]
                        if refresh_errors
                        else None
                    ),
                    "conversion_refresh_accounts_complete": refresh_complete,
                    "ingestion_mode": "v2_analytics_backfill",
                    "updated_at": now_iso,
                }
            },
            upsert=True,
        )
        return {
            "sum_spend": round(
                sum(float(row.get("spend_sar") or 0) for row in rows),
                2,
            ),
            "sum_revenue": conversions["revenue"],
            "sum_purchases": conversions["purchases"],
            "conversion_data_status": conversions["conversion_data_status"],
            "conversion_refresh_status": refresh_status,
            "spend_data_status": spend_status,
            "account_count": len(rows),
        }

    async def run(
        self,
        user_id: str,
        payload: SnapchatAnalyticsSyncInput,
    ) -> dict:
        """Run the bounded refresh; the kill switch is checked first."""
        if not snapchat_analytics_sync_enabled():
            raise SnapchatAnalyticsSyncError(
                "snapchat_analytics_sync_disabled",
                "Snapchat analytics refresh is temporarily disabled.",
                status_code=503,
            )
        dates = enumerate_sync_dates(
            payload,
            today=self._now().astimezone(_riyadh_timezone()).date(),
        )
        accounts = await self.db.snapchat_ad_accounts.find(
            {"user_id": user_id, "enabled": True},
            {"_id": 0},
        ).to_list(MAX_SYNC_ACCOUNTS + 1)
        accounts = [
            account for account in accounts if account.get("ad_account_id")
        ]
        if not accounts:
            raise SnapchatAnalyticsSyncError(
                "snapchat_accounts_not_selected",
                "No enabled Snapchat ad accounts were found.",
                status_code=409,
            )
        if len(accounts) > MAX_SYNC_ACCOUNTS:
            raise SnapchatAnalyticsSyncError(
                "snapchat_account_limit_exceeded",
                (
                    "Snapchat analytics refresh supports at most "
                    f"{MAX_SYNC_ACCOUNTS} enabled accounts per run."
                ),
                status_code=409,
            )
        estimated_provider_calls = len(accounts) * len(dates) * 2 + 1
        if estimated_provider_calls > MAX_PROVIDER_CALLS:
            raise SnapchatAnalyticsSyncError(
                "snapchat_provider_call_budget_exceeded",
                (
                    "Requested Snapchat range exceeds the per-run provider "
                    f"call budget ({MAX_PROVIDER_CALLS})."
                ),
                status_code=400,
            )
        if any(
            str(account.get("currency_native") or "").strip().upper() == "USD"
            for account in accounts
        ):
            # Validate and cache the conversion rate before any provider call
            # or fact write, so a broken rate cannot leave a half-written run.
            await self._usd_to_sar(user_id)
        access_token = await self._ensure_access_token(user_id)
        successful_by_date: dict[str, set[str]] = {}
        account_summaries: list[dict] = []
        all_errors: list[dict] = []
        async with httpx.AsyncClient(timeout=30.0) as http:
            for account in accounts:
                saved, errors, successful_dates = await self._sync_one_account(
                    http,
                    access_token,
                    user_id,
                    account,
                    dates,
                    _riyadh_timezone(),
                )
                account_id = str(account["ad_account_id"])
                account_summaries.append(
                    {
                        "ad_account_id": account_id,
                        "name": account.get("name"),
                        "currency_native": account.get("currency_native"),
                        "rows_saved": saved,
                        "errors": len(errors),
                    }
                )
                all_errors.extend(errors)
                for successful_date in successful_dates:
                    successful_by_date.setdefault(
                        successful_date,
                        set(),
                    ).add(account_id)
        expected_ids = [
            str(account["ad_account_id"]) for account in accounts
        ]
        for day in dates:
            await self._aggregate_day(
                user_id,
                day.isoformat(),
                expected_account_ids=expected_ids,
                successful_account_ids=sorted(
                    successful_by_date.get(day.isoformat(), set())
                ),
            )

        complete_accounts = sum(
            item["rows_saved"] == len(dates) and item["errors"] == 0
            for item in account_summaries
        )
        rows_saved = sum(item["rows_saved"] for item in account_summaries)
        needs_reauth = any(
            error.get("error")
            in {
                "provider_http_401",
                "provider_http_403",
                "http_401",
                "http_403",
            }
            for error in all_errors
        )
        result = {
            "provider": "snapchat_ads",
            "accounts_synced": len(account_summaries),
            "accounts_complete": complete_accounts,
            "rows_saved": rows_saved,
            "days_requested": len(dates),
            "date_from": dates[0].isoformat(),
            "date_to": dates[-1].isoformat(),
            "sync_status": (
                "complete"
                if complete_accounts == len(account_summaries)
                else "partial"
            ),
            "items": account_summaries,
            "errors": all_errors[:200],
            "errors_count": len(all_errors),
            "errors_truncated": len(all_errors) > 200,
            "needs_reauth": needs_reauth,
            "estimated_provider_calls": estimated_provider_calls,
            "currency": "SAR",
            "business_timezone": BUSINESS_TIMEZONE,
            "source_only": True,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
            "campaign_write_reached": False,
            "fetched_at": self._now_iso(),
        }
        if rows_saved == 0:
            currency_only_failure = bool(all_errors) and all(
                error.get("kind") == "currency_quality"
                for error in all_errors
            )
            raise SnapchatAnalyticsSyncError(
                (
                    "snapchat_needs_reauth"
                    if needs_reauth
                    else "snapchat_currency_unverified"
                    if currency_only_failure
                    else "snapchat_analytics_no_rows"
                ),
                (
                    "Snapchat authorization must be renewed."
                    if needs_reauth
                    else "Snapchat account currency is missing or unsupported."
                    if currency_only_failure
                    else "Snapchat returned no usable analytics rows."
                ),
                status_code=(
                    409 if needs_reauth or currency_only_failure else 502
                ),
                retryable=not (needs_reauth or currency_only_failure),
                result=result,
            )
        return result


__all__ = [
    "BUSINESS_TIMEZONE",
    "MAX_SYNC_DAYS",
    "MAX_SYNC_ACCOUNTS",
    "MAX_PROVIDER_CALLS",
    "SYNC_ENABLED_ENV",
    "SnapchatAnalyticsBackfill",
    "SnapchatAnalyticsSyncError",
    "SnapchatAnalyticsSyncInput",
    "_aggregate_snap_conversion_rows",
    "_fetch_snap_conversion_metrics",
    "_merge_snap_conversion_metrics",
    "_parse_snap_conversion_payload",
    "_parse_snap_spend_payload",
    "enumerate_sync_dates",
    "snapchat_analytics_sync_enabled",
]
