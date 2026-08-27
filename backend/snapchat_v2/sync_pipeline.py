"""Staged Snapchat Integration V2 shadow-sync orchestrator."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .client import SnapchatClientError, SnapchatV2Client
from .connection import SnapchatConnectionError, SnapchatConnectionManager
from .entities import list_entities, sync_entities
from .facts import upsert_hourly_fact, upsert_hourly_facts
from .lease import (
    acquire_lease,
    build_owner_id,
    ensure_lease_indexes,
    heartbeat_lease,
    recover_expired_leases,
    release_lease,
)
from .models import (
    DEFAULT_SWIPE_ATTRIBUTION_WINDOW,
    DEFAULT_VIEW_ATTRIBUTION_WINDOW,
)
from .projections import (
    RIYADH_TIMEZONE,
    build_and_persist_daily_projections,
    business_day_window,
)
from .provider_total import fetch_provider_total
from .reconciliation import reconcile_day
from .sync_runs import (
    complete_sync_run,
    create_sync_run,
    ensure_sync_run_indexes,
    fail_sync_run,
    heartbeat_sync_run,
    new_sync_run,
    recover_abandoned_sync_runs,
    set_level_status,
    update_sync_stage,
)
from .total_facts import upsert_total_facts

MAX_SYNC_DAYS = 62
HEARTBEAT_SECONDS = 20.0
PROVISIONAL_SPEND_TOLERANCE = 0.000001


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_date(value: date | str | None, *, field: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{field} must use YYYY-MM-DD") from exc


def _date_range(
    date_from: date | str | None,
    date_to: date | str | None,
    *,
    now: datetime,
) -> list[date]:
    riyadh_today = now.astimezone(ZoneInfo(RIYADH_TIMEZONE)).date()
    start = _parse_date(date_from, field="date_from")
    end = _parse_date(date_to, field="date_to")
    if (start is None) != (end is None):
        raise ValueError("date_from and date_to must be supplied together")
    if start is None:
        start, end = riyadh_today - timedelta(days=1), riyadh_today
    assert end is not None
    if end < start:
        raise ValueError("date_to must be on or after date_from")
    if end > riyadh_today:
        raise ValueError("future Snapchat reporting dates are not allowed")
    count = (end - start).days + 1
    if count > MAX_SYNC_DAYS:
        raise ValueError(f"Snapchat reporting range cannot exceed {MAX_SYNC_DAYS} days")
    return [start + timedelta(days=offset) for offset in range(count)]


def _ceil_current_hour(value: datetime) -> datetime:
    current = value.astimezone(timezone.utc)
    floor = current.replace(minute=0, second=0, microsecond=0)
    return floor + timedelta(hours=1)


def _sync_window(
    report_dates: list[date],
    *,
    account_timezone: str,
    now: datetime,
) -> tuple[datetime, datetime]:
    first, last = report_dates[0], report_dates[-1]
    account_start, _ = business_day_window(first, account_timezone)
    _, account_end = business_day_window(last, account_timezone)
    riyadh_start, _ = business_day_window(first, RIYADH_TIMEZONE)
    _, riyadh_end = business_day_window(last, RIYADH_TIMEZONE)
    start = min(account_start, riyadh_start)
    end = max(account_end, riyadh_end)
    current_cap = _ceil_current_hour(now)
    if end > current_cap:
        end = current_cap
    if end <= start:
        raise ValueError("Snapchat sync window is empty")
    return start, end


def _current_hour_provider_total_fact(
    *,
    user_id: str,
    account: dict[str, Any],
    projection: dict[str, Any],
    provider_total: dict[str, Any],
    current: datetime,
    sync_run_id: str,
    action_report_time: str,
) -> dict[str, Any] | None:
    """Materialize live account-day TOTAL spend into the open HOUR bucket.

    Snapchat can advance the account TOTAL while omitting the still-open HOUR
    point. Closed HOUR facts remain authoritative; the non-negative residual
    between the fresh account-day TOTAL and every other known hour is the
    current provisional hour. Existing HOUR metrics are preserved, and a
    provider TOTAL that lags an already-observed HOUR value cannot move the
    bucket backwards.
    """
    coverage = dict(provider_total.get("account_day_coverage") or {})
    if (
        coverage.get("status") != "complete"
        or coverage.get("provider_granularity") != "TOTAL"
    ):
        return None

    window_start = provider_total.get("account_day_window_start_utc")
    window_end = provider_total.get("account_day_window_end_utc")
    if not isinstance(window_start, datetime) or not isinstance(window_end, datetime):
        return None
    window_start = window_start.astimezone(timezone.utc)
    window_end = window_end.astimezone(timezone.utc)
    current_utc = current.astimezone(timezone.utc)
    if not window_start <= current_utc < window_end:
        return None

    current_hour_start = current_utc.replace(minute=0, second=0, microsecond=0)
    current_hour_end = current_hour_start + timedelta(hours=1)
    current_row: dict[str, Any] | None = None
    prior_spend = 0.0
    for row in list(projection.get("hours") or []):
        point = row.get("hour_start_utc")
        if not isinstance(point, datetime):
            continue
        point = point.astimezone(timezone.utc)
        value = row.get("spend_native")
        if point == current_hour_start:
            current_row = row
        elif value is not None:
            prior_spend += float(value)

    provider_spend = provider_total.get("account_day_provider_spend_native")
    if provider_spend is None:
        return None
    provisional_spend = float(provider_spend) - prior_spend
    if provisional_spend < -PROVISIONAL_SPEND_TOLERANCE:
        return None
    provisional_spend = round(max(0.0, provisional_spend), 6)
    existing_spend = (
        float(current_row["spend_native"])
        if current_row is not None and current_row.get("spend_native") is not None
        else None
    )
    if (
        existing_spend is not None
        and provisional_spend <= existing_spend + PROVISIONAL_SPEND_TOLERANCE
    ):
        return None

    metrics = current_row or {}
    return {
        "user_id": str(user_id),
        "ad_account_id": str(account.get("ad_account_id") or "").strip(),
        "campaign_id": None,
        "hour_start_utc": current_hour_start,
        "hour_end_utc": current_hour_end,
        "account_timezone": str(account.get("timezone") or ""),
        "currency": str(account.get("currency") or "").upper(),
        "action_report_time": action_report_time,
        "attribution_windows": {
            "swipe": DEFAULT_SWIPE_ATTRIBUTION_WINDOW,
            "view": DEFAULT_VIEW_ATTRIBUTION_WINDOW,
        },
        "spend_native": provisional_spend,
        "impressions": int(metrics.get("impressions") or 0),
        "swipes": int(metrics.get("swipes") or 0),
        "video_views": int(metrics.get("video_views") or 0),
        "view_completion": float(metrics.get("view_completion") or 0),
        "view_content": int(metrics.get("view_content") or 0),
        "add_to_cart": int(metrics.get("add_to_cart") or 0),
        "start_checkout": int(metrics.get("start_checkout") or 0),
        "add_billing": int(metrics.get("add_billing") or 0),
        "purchases": int(metrics.get("purchases") or 0),
        "purchase_value_native": float(metrics.get("purchase_value_native") or 0),
        "coverage": {
            **coverage,
            "data_state": (
                "confirmed_data" if provisional_spend > 0 else "confirmed_zero"
            ),
            "provisional": True,
        },
        "source": {
            "api": "snapchat_marketing_api",
            "granularity": "TOTAL",
            "materialization": "current_hour_provider_total_residual_v1",
            "account_day_provider_spend_native": round(float(provider_spend), 6),
            "prior_hours_spend_native": round(prior_spend, 6),
            "window_start_utc": window_start,
            "window_end_utc": window_end,
        },
        "provisional": True,
        "sync_run_id": str(sync_run_id),
    }


class SnapchatV2SyncPipeline:
    def __init__(
        self,
        db: Any,
        *,
        now: Callable[[], datetime] = _utcnow,
        connection_manager: SnapchatConnectionManager | None = None,
        client_factory: Callable[..., SnapchatV2Client] | None = None,
    ) -> None:
        self.db = db
        self.now = now
        self.connection = connection_manager or SnapchatConnectionManager(db, now=now)
        self.client_factory = client_factory

    def _client(self, user_id: str) -> SnapchatV2Client:
        if self.client_factory is not None:
            return self.client_factory(self.db, str(user_id))
        return SnapchatV2Client(
            self.db,
            str(user_id),
            token_store=self.connection.tokens,
            now=self.now,
        )

    async def _heartbeat(
        self,
        *,
        user_id: str,
        ad_account_id: str,
        owner_id: str,
        sync_run_id: str,
    ) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await heartbeat_lease(
                self.db,
                user_id,
                ad_account_id,
                owner_id,
                now=self.now,
            )
            alive = await heartbeat_sync_run(
                self.db,
                sync_run_id,
                owner_id=owner_id,
                now=self.now,
            )
            if not alive:
                raise RuntimeError("Snapchat V2 sync run heartbeat was rejected")

    async def _sync_identities(
        self,
        client: SnapchatV2Client,
        *,
        user_id: str,
        account_id: str,
        sync_run_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        summary: dict[str, Any] = {}
        errors: list[dict[str, Any]] = []
        for entity_type in ("campaign", "ad_squad", "ad"):
            await update_sync_stage(
                self.db,
                sync_run_id,
                f"identity_{entity_type}",
                details={"entity_type": entity_type},
                now=self.now,
            )
            try:
                fetched = await client.fetch_entities(account_id, entity_type)
                saved = await sync_entities(
                    self.db,
                    user_id=user_id,
                    ad_account_id=account_id,
                    entity_type=entity_type,
                    rows=fetched["rows"],
                    sync_run_id=sync_run_id,
                    now=self.now(),
                )
                summary[entity_type] = {
                    **saved,
                    "coverage": fetched["coverage"],
                }
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "level": entity_type,
                        "code": str(getattr(exc, "code", type(exc).__name__))[:96],
                        "retryable": bool(getattr(exc, "retryable", False)),
                    }
                )
                summary[entity_type] = {
                    "rows_saved": 0,
                    "coverage": {
                        "status": "incomplete",
                        "data_state": "unknown_incomplete",
                    },
                }
        await set_level_status(
            self.db,
            sync_run_id,
            "identity",
            "complete" if not errors else "partial",
            coverage={
                "status": "complete" if not errors else "partial",
                "errors": errors,
            },
            now=self.now,
        )
        return summary, errors

    async def _sync_breakdown_performance(
        self,
        client: SnapchatV2Client,
        *,
        user_id: str,
        account: dict[str, Any],
        sync_run_id: str,
        entity_type: str,
        campaign_rows: list[dict[str, Any]],
        start_utc: datetime,
        end_utc: datetime,
        action_report_time: str,
        report_dates: list[date] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        campaign_ids = sorted(
            {
                str(row.get("campaign_id") or "").strip()
                for row in campaign_rows
                if str(row.get("campaign_id") or "").strip()
                and any(
                    float(row.get(field) or 0) > 0
                    for field in (
                        "spend_native",
                        "impressions",
                        "swipes",
                        "video_views",
                        "purchases",
                        "purchase_value_native",
                    )
                )
            }
        )
        parent_lookup: dict[str, str] = {}
        if entity_type == "ad":
            identities = await list_entities(
                self.db,
                user_id=user_id,
                ad_account_id=str(account["ad_account_id"]),
                entity_type="ad",
                active_only=False,
                limit=20_000,
            )
            parent_lookup = {
                str(row.get("external_id") or "")
                .strip(): str(row.get("ad_squad_id") or "")
                .strip()
                for row in identities
                if str(row.get("external_id") or "").strip()
                and str(row.get("ad_squad_id") or "").strip()
            }
        await update_sync_stage(
            self.db,
            sync_run_id,
            f"{entity_type}_performance_fetch",
            details={"campaigns_with_activity": len(campaign_ids)},
            now=self.now,
        )
        try:
            selected_dates = list(report_dates or [])
            if not selected_dates:
                account_zone = ZoneInfo(str(account.get("timezone") or "UTC"))
                first = start_utc.astimezone(account_zone).date()
                last = (
                    (end_utc - timedelta(microseconds=1))
                    .astimezone(account_zone)
                    .date()
                )
                selected_dates = [
                    first + timedelta(days=offset)
                    for offset in range((last - first).days + 1)
                ]
            performance = await client.fetch_breakdown_daily_total_facts(
                account,
                campaign_ids=campaign_ids,
                entity_type=entity_type,
                report_dates=selected_dates,
                sync_run_id=sync_run_id,
                action_report_time=action_report_time,
                ad_squad_by_ad_id=parent_lookup,
            )
            write = await upsert_total_facts(
                self.db,
                performance["rows"],
                now=self.now(),
            )
            coverage = {
                **dict(performance.get("coverage") or {}),
                "campaigns_requested": len(campaign_ids),
                "identity_parent_matches": len(parent_lookup),
            }
            await set_level_status(
                self.db,
                sync_run_id,
                entity_type,
                "complete",
                coverage=coverage,
                now=self.now,
            )
            return {
                **write,
                "coverage": coverage,
            }, None
        except Exception as exc:  # noqa: BLE001
            code = str(getattr(exc, "code", type(exc).__name__))[:96]
            coverage = {
                **dict(getattr(exc, "coverage", {}) or {}),
                "status": "partial",
                "data_state": "unknown_incomplete",
                "reason": code,
                "campaigns_requested": len(campaign_ids),
            }
            await set_level_status(
                self.db,
                sync_run_id,
                entity_type,
                "partial",
                coverage=coverage,
                now=self.now,
            )
            error = {
                "level": entity_type,
                "code": code,
                "retryable": bool(getattr(exc, "retryable", False)),
            }
            return {"rows_saved": 0, "coverage": coverage}, error

    async def run(
        self,
        user_id: str,
        ad_account_id: str | None = None,
        *,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
        action_report_time: str = "conversion",
        run_type: str = "rolling_refresh",
        financial_only: bool = False,
    ) -> dict[str, Any]:
        current = self.now().astimezone(timezone.utc)
        report_dates = _date_range(date_from, date_to, now=current)
        await self.connection.ensure_indexes()
        await ensure_lease_indexes(self.db)
        await ensure_sync_run_indexes(self.db)
        recovered_leases = await recover_expired_leases(self.db, now=self.now)
        recovered_runs = await recover_abandoned_sync_runs(self.db, now=self.now)

        try:
            _, account = await self.connection.validate_ready(
                str(user_id),
                ad_account_id=ad_account_id,
            )
        except SnapchatConnectionError as exc:
            return {
                "status": "failed",
                "stage": "connection_validation",
                "error": {
                    "code": exc.code,
                    "retryable": exc.retryable,
                    "needs_reauth": exc.needs_reauth,
                },
            }

        account_id = str(account["ad_account_id"])
        start_utc, end_utc = _sync_window(
            report_dates,
            account_timezone=str(account.get("timezone") or ""),
            now=current,
        )
        owner_id = build_owner_id()
        acquired = await acquire_lease(
            self.db,
            str(user_id),
            account_id,
            owner_id,
            now=self.now,
        )
        if not acquired:
            return {
                "status": "skipped",
                "reason": "lease_unavailable",
                "ad_account_id": account_id,
            }

        run = new_sync_run(
            str(user_id),
            account_id,
            owner_id=owner_id,
            run_type=run_type,
            request_window={
                "start_utc": start_utc,
                "end_utc": end_utc,
                "date_from": report_dates[0].isoformat(),
                "date_to": report_dates[-1].isoformat(),
                "action_report_time": action_report_time,
            },
            now=current,
        )
        await create_sync_run(self.db, run)
        sync_run_id = run["sync_run_id"]
        heartbeat_task = asyncio.create_task(
            self._heartbeat(
                user_id=str(user_id),
                ad_account_id=account_id,
                owner_id=owner_id,
                sync_run_id=sync_run_id,
            )
        )
        outcome = "failed"
        client = self._client(str(user_id))
        warnings: list[dict[str, Any]] = []
        financial_committed = False
        identity_summary: dict[str, Any] = {}
        try:
            await update_sync_stage(
                self.db,
                sync_run_id,
                "connection_validation",
                "complete",
                details={"ad_account_id": account_id},
                now=self.now,
            )
            await update_sync_stage(
                self.db,
                sync_run_id,
                "hourly_performance_fetch",
                details={"window_count_policy_days": 7},
                now=self.now,
            )
            hourly = await client.fetch_hourly_facts(
                account,
                start_utc=start_utc,
                end_utc=end_utc,
                sync_run_id=sync_run_id,
                action_report_time=action_report_time,
            )
            if (hourly.get("coverage") or {}).get("status") != "complete":
                raise SnapchatClientError(
                    "snapchat_hourly_coverage_incomplete",
                    "Snapchat HOUR coverage was not complete.",
                    retryable=True,
                    coverage=hourly.get("coverage") or {},
                )

            await update_sync_stage(
                self.db,
                sync_run_id,
                "hourly_fact_publish",
                details={"rows_received": len(hourly["rows"])},
                now=self.now,
            )
            fact_write = await upsert_hourly_facts(
                self.db,
                hourly["rows"],
                now=self.now(),
            )
            await set_level_status(
                self.db,
                sync_run_id,
                "financial",
                "complete",
                coverage=hourly["coverage"],
                now=self.now,
            )
            financial_committed = True
            if not financial_only:
                await set_level_status(
                    self.db,
                    sync_run_id,
                    "campaign",
                    "complete",
                    coverage={
                        **hourly["coverage"],
                        "campaign_rows": len(hourly["campaign_rows"]),
                    },
                    now=self.now,
                )
            # Publish Ads Manager-compatible campaign DAY facts before the
            # large identity catalog. Production accounts can have more than
            # ten thousand Ads; campaign outcome parity must not depend on the
            # request budget consumed by identity discovery or child levels.
            total_report_dates = report_dates[-7:]
            breakdown_summary: dict[str, Any] = {}
            if not financial_only:
                campaign_summary, campaign_error = await self._sync_breakdown_performance(
                    client,
                    user_id=str(user_id),
                    account=account,
                    sync_run_id=sync_run_id,
                    entity_type="campaign",
                    campaign_rows=hourly["campaign_rows"],
                    start_utc=start_utc,
                    end_utc=end_utc,
                    action_report_time=action_report_time,
                    report_dates=total_report_dates,
                )
                breakdown_summary["campaign"] = campaign_summary
                if campaign_error:
                    warnings.append(campaign_error)

            # Refresh names and parent identities after the account-level
            # campaign report. Identity freshness is useful for presentation,
            # but it is not allowed to gate the headline purchase total.
            if not financial_only:
                identity_summary, identity_errors = await self._sync_identities(
                    client,
                    user_id=str(user_id),
                    account_id=account_id,
                    sync_run_id=sync_run_id,
                )
                warnings.extend(identity_errors)
            # Exact account-day hierarchy facts are the intelligence/read surface,
            # not the financial source. Bound each rolling run to seven days
            # so large historical backfills cannot exhaust Snapchat's request
            # budget; older days retain their previously persisted TOTAL rows
            # and hourly facts remain available as the safe fallback.
            if not financial_only:
                for entity_type in ("ad_squad", "ad"):
                    level_summary, level_error = await self._sync_breakdown_performance(
                        client,
                        user_id=str(user_id),
                        account=account,
                        sync_run_id=sync_run_id,
                        entity_type=entity_type,
                        campaign_rows=hourly["campaign_rows"],
                        start_utc=start_utc,
                        end_utc=end_utc,
                        action_report_time=action_report_time,
                        report_dates=total_report_dates,
                    )
                    breakdown_summary[entity_type] = level_summary
                    if level_error:
                        warnings.append(level_error)

            await update_sync_stage(
                self.db,
                sync_run_id,
                "projection_build",
                details={"report_days": len(report_dates)},
                now=self.now,
            )
            projections = await build_and_persist_daily_projections(
                self.db,
                user_id=str(user_id),
                account=account,
                report_dates=report_dates,
                action_report_time=action_report_time,
                coverage=hourly["coverage"],
                sync_run_id=sync_run_id,
                now=self.now(),
            )
            by_key = {
                (row["report_date"], row["projection_timezone"]): row
                for row in projections
            }

            await update_sync_stage(
                self.db,
                sync_run_id,
                "reconciliation",
                now=self.now,
            )
            reconciliations: list[dict[str, Any]] = []
            provisional_rows_saved = 0
            for report_date in report_dates:
                account_projection = by_key[
                    (report_date.isoformat(), str(account["timezone"]))
                ]
                dashboard_projection = by_key[
                    (report_date.isoformat(), RIYADH_TIMEZONE)
                ]
                window_start = dashboard_projection["window_start_utc"]
                window_end = dashboard_projection["window_end_utc"]
                current_open = window_start <= current < window_end
                try:
                    provider_total = await fetch_provider_total(
                        client,
                        account,
                        start_utc=window_start,
                        end_utc=window_end,
                        action_report_time=action_report_time,
                    )
                    provisional_fact = _current_hour_provider_total_fact(
                        user_id=str(user_id),
                        account=account,
                        projection=account_projection,
                        provider_total=provider_total,
                        current=current,
                        sync_run_id=sync_run_id,
                        action_report_time=action_report_time,
                    )
                    if provisional_fact is not None:
                        await upsert_hourly_fact(
                            self.db,
                            provisional_fact,
                            now=self.now(),
                        )
                        provisional_rows_saved += 1
                        refreshed = await build_and_persist_daily_projections(
                            self.db,
                            user_id=str(user_id),
                            account=account,
                            report_dates=[report_date],
                            action_report_time=action_report_time,
                            coverage=hourly["coverage"],
                            sync_run_id=sync_run_id,
                            now=self.now(),
                        )
                        for row in refreshed:
                            by_key[
                                (row["report_date"], row["projection_timezone"])
                            ] = row
                        account_projection = by_key[
                            (report_date.isoformat(), str(account["timezone"]))
                        ]
                        dashboard_projection = by_key[
                            (report_date.isoformat(), RIYADH_TIMEZONE)
                        ]
                    reconciliation = await reconcile_day(
                        self.db,
                        user_id=str(user_id),
                        account=account,
                        report_date=report_date,
                        provider_total=provider_total,
                        snap_page_projection=account_projection,
                        dashboard_projection=dashboard_projection,
                        action_report_time=action_report_time,
                        current_open_hour=current_open,
                        sync_run_id=sync_run_id,
                        now=self.now(),
                    )
                    reconciliations.append(reconciliation)
                    if not reconciliation.get("reconciled"):
                        warnings.append(
                            {
                                "level": "reconciliation",
                                "date": report_date.isoformat(),
                                "reason_codes": reconciliation.get("reason_codes")
                                or [],
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    warnings.append(
                        {
                            "level": "reconciliation",
                            "date": report_date.isoformat(),
                            "code": str(getattr(exc, "code", type(exc).__name__))[:96],
                            "retryable": bool(getattr(exc, "retryable", False)),
                        }
                    )

            summary = {
                "date_from": report_dates[0].isoformat(),
                "date_to": report_dates[-1].isoformat(),
                "ad_account_id": account_id,
                "action_report_time": action_report_time,
                "rows_received": fact_write["rows_received"],
                "rows_saved": fact_write["rows_saved"],
                "campaign_rows": len(hourly["campaign_rows"]),
                "account_rows": len(hourly["account_rows"]),
                "breakdown_performance": breakdown_summary,
                "provider_calls": client.provider_calls,
                "request_windows": hourly["request_windows"],
                "identity": identity_summary,
                "reconciliation_days": len(reconciliations),
                "reconciled_days": sum(
                    bool(row.get("reconciled")) for row in reconciliations
                ),
                "warnings": warnings,
                "financial_only": bool(financial_only),
                "current_hour_provisional_rows_saved": provisional_rows_saved,
                "recovered_leases": recovered_leases,
                "recovered_runs": recovered_runs,
                "shadow_mode": True,
                "ui_switched": False,
            }
            await complete_sync_run(
                self.db,
                sync_run_id,
                summary=summary,
                now=self.now,
            )
            finished = (
                await self.db["mezan_snapchat_sync_runs_v2"].find_one(
                    {"sync_run_id": sync_run_id},
                    {"_id": 0, "status": 1},
                )
                or {}
            )
            outcome = str(finished.get("status") or "partial")
            success_at = self.now().astimezone(timezone.utc)
            await self.db["mezan_snapchat_connections_v2"].update_one(
                {"user_id": str(user_id), "provider": "snapchat_ads"},
                {
                    "$set": {
                        "financial_last_success_at": success_at,
                        "last_sync_run_id": sync_run_id,
                        "last_sync_status": outcome,
                        "next_due_at": success_at + timedelta(minutes=5),
                        "updated_at": success_at,
                    }
                },
                upsert=True,
            )
            return {
                "status": outcome,
                "sync_run_id": sync_run_id,
                "summary": summary,
            }
        except Exception as exc:  # noqa: BLE001
            if not financial_committed:
                await set_level_status(
                    self.db,
                    sync_run_id,
                    "financial",
                    "failed",
                    coverage=getattr(exc, "coverage", None)
                    or {
                        "status": "incomplete",
                        "data_state": "unknown_incomplete",
                        "reason": str(getattr(exc, "code", type(exc).__name__))[:96],
                    },
                    now=self.now,
                )
            await fail_sync_run(
                self.db,
                sync_run_id,
                exc,
                stage=(
                    "post_financial_sync_failed"
                    if financial_committed
                    else "financial_sync_failed"
                ),
                now=self.now,
            )
            await self.db["mezan_snapchat_connections_v2"].update_one(
                {"user_id": str(user_id), "provider": "snapchat_ads"},
                {
                    "$set": {
                        "last_sync_run_id": sync_run_id,
                        "last_sync_status": "failed",
                        "last_error_code": str(
                            getattr(exc, "code", type(exc).__name__)
                        )[:96],
                        "updated_at": self.now().astimezone(timezone.utc),
                    }
                },
                upsert=True,
            )
            return {
                "status": "failed",
                "sync_run_id": sync_run_id,
                "error": {
                    "code": str(getattr(exc, "code", type(exc).__name__))[:96],
                    "retryable": bool(getattr(exc, "retryable", False)),
                    "needs_reauth": bool(getattr(exc, "needs_reauth", False)),
                },
            }
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
            await release_lease(
                self.db,
                str(user_id),
                account_id,
                owner_id,
                outcome=outcome,
                now=self.now,
            )


__all__ = ["MAX_SYNC_DAYS", "SnapchatV2SyncPipeline"]
