"""Staged Snapchat Integration V2 shadow-sync orchestrator."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .client import SnapchatClientError, SnapchatV2Client
from .connection import SnapchatConnectionError, SnapchatConnectionManager
from .entities import sync_entities
from .facts import upsert_hourly_facts
from .lease import (
    acquire_lease,
    build_owner_id,
    ensure_lease_indexes,
    heartbeat_lease,
    recover_expired_leases,
    release_lease,
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

MAX_SYNC_DAYS = 62
HEARTBEAT_SECONDS = 20.0


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

    async def run(
        self,
        user_id: str,
        ad_account_id: str | None = None,
        *,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
        action_report_time: str = "conversion",
        run_type: str = "rolling_refresh",
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
        try:
            await update_sync_stage(
                self.db,
                sync_run_id,
                "connection_validation",
                "complete",
                details={"ad_account_id": account_id},
                now=self.now,
            )
            identity_summary, identity_errors = await self._sync_identities(
                client,
                user_id=str(user_id),
                account_id=account_id,
                sync_run_id=sync_run_id,
            )
            warnings.extend(identity_errors)

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
            await set_level_status(
                self.db,
                sync_run_id,
                "ad_squad",
                "partial",
                coverage={
                    "status": "partial",
                    "reason": "ad_squad_performance_shadow_pending",
                    "identity": identity_summary.get("ad_squad") or {},
                },
                now=self.now,
            )
            await set_level_status(
                self.db,
                sync_run_id,
                "ad",
                "partial",
                coverage={
                    "status": "partial",
                    "reason": "ad_performance_shadow_pending",
                    "identity": identity_summary.get("ad") or {},
                },
                now=self.now,
            )

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
                                "reason_codes": reconciliation.get("reason_codes") or [],
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
                "provider_calls": client.provider_calls,
                "request_windows": hourly["request_windows"],
                "identity": identity_summary,
                "reconciliation_days": len(reconciliations),
                "reconciled_days": sum(
                    bool(row.get("reconciled")) for row in reconciliations
                ),
                "warnings": warnings,
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
            finished = await self.db["mezan_snapchat_sync_runs_v2"].find_one(
                {"sync_run_id": sync_run_id},
                {"_id": 0, "status": 1},
            ) or {}
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
                stage="financial_sync_failed",
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
