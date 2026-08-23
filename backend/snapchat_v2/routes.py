"""Versioned read-only APIs for the Snapchat Integration V2 shadow plane."""
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .accounts import get_selected_account, list_accounts
from .entities import list_entities
from .facts import load_hourly_facts
from .models import clean_text
from .projections import (
    RIYADH_TIMEZONE,
    business_day_window,
    list_daily_projections,
)
from .reconciliation import list_reconciliation
from .status import snapchat_v2_status
from .sync_pipeline import MAX_SYNC_DAYS, SnapchatV2SyncPipeline


class SnapchatV2SyncInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ad_account_id: str | None = Field(default=None, max_length=128)
    date_from: date | None = None
    date_to: date | None = None
    action_report_time: Literal["conversion", "impression"] = "conversion"
    run_type: Literal["rolling_refresh", "manual", "backfill", "reconciliation"] = (
        "manual"
    )

    @model_validator(mode="after")
    def validate_range(self):
        if (self.date_from is None) != (self.date_to is None):
            raise ValueError("date_from and date_to must be supplied together")
        if self.date_from and self.date_to:
            if self.date_to < self.date_from:
                raise ValueError("date_to must be on or after date_from")
            if (self.date_to - self.date_from).days + 1 > MAX_SYNC_DAYS:
                raise ValueError(
                    f"Snapchat sync range cannot exceed {MAX_SYNC_DAYS} days"
                )
        return self


def _user_id(user: dict, require_owner: Callable[[Any], dict]) -> str:
    owner = require_owner(user)
    return str(owner["id"])


def _read_days(date_from: date, date_to: date) -> int:
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="invalid_date_range")
    count = (date_to - date_from).days + 1
    if count > MAX_SYNC_DAYS:
        raise HTTPException(status_code=422, detail="date_range_too_large")
    return count


def _shadow_enabled() -> bool:
    return os.environ.get("SNAPCHAT_REPORTING_V2_SHADOW_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _ui_enabled() -> bool:
    return os.environ.get("SNAPCHAT_REPORTING_V2_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


async def _selected_account_or_404(db: Any, user_id: str) -> dict[str, Any]:
    account = await get_selected_account(db, user_id)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "snapchat_v2_selected_account_missing",
                "message": "شغّل مزامنة Snapchat V2 أولًا لاكتشاف الحساب الأساسي.",
            },
        )
    return account


def _projection_timezone(account: dict[str, Any], value: str) -> str:
    if value == "riyadh":
        return RIYADH_TIMEZONE
    if value == "account":
        timezone_name = clean_text(account.get("timezone"), limit=80)
        if not timezone_name:
            raise HTTPException(status_code=409, detail="snapchat_account_timezone_missing")
        return timezone_name
    raise HTTPException(status_code=422, detail="invalid_projection_timezone")


def _sum_projection(rows: list[dict[str, Any]], field: str) -> int | float:
    total = sum(float(row.get(field) or 0) for row in rows)
    if field in {"impressions", "swipes", "video_views", "purchases"}:
        return int(total)
    return round(total, 6)


async def _campaign_report(
    db: Any,
    *,
    user_id: str,
    account: dict[str, Any],
    date_from: date,
    date_to: date,
    timezone_name: str,
    action_report_time: str,
) -> list[dict[str, Any]]:
    start_utc, _ = business_day_window(date_from, timezone_name)
    _, end_utc = business_day_window(date_to, timezone_name)
    facts = await load_hourly_facts(
        db,
        user_id=user_id,
        ad_account_id=str(account["ad_account_id"]),
        start_utc=start_utc,
        end_utc=end_utc,
        entity_type="campaign",
        action_report_time=action_report_time,
    )
    identities = await list_entities(
        db,
        user_id=user_id,
        ad_account_id=str(account["ad_account_id"]),
        entity_type="campaign",
        active_only=False,
    )
    by_id = {str(row.get("external_id")): row for row in identities}
    buckets: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        campaign_id = str(fact.get("campaign_id") or fact.get("external_id") or "")
        if campaign_id:
            buckets.setdefault(campaign_id, []).append(fact)
    output: list[dict[str, Any]] = []
    for campaign_id in sorted(set(by_id) | set(buckets)):
        rows = buckets.get(campaign_id, [])
        identity = by_id.get(campaign_id) or {}
        spend = _sum_projection(rows, "spend_native")
        purchases = _sum_projection(rows, "purchases")
        purchase_value = _sum_projection(rows, "purchase_value_native")
        output.append(
            {
                "campaign_id": campaign_id,
                "name": identity.get("name") or campaign_id,
                "status": identity.get("status"),
                "active": identity.get("active"),
                "spend_native": spend,
                "impressions": _sum_projection(rows, "impressions"),
                "swipes": _sum_projection(rows, "swipes"),
                "video_views": _sum_projection(rows, "video_views"),
                "purchases": purchases,
                "purchase_value_native": purchase_value,
                "roas": (
                    round(float(purchase_value) / float(spend), 6)
                    if float(spend) > 0
                    else None
                ),
                "source_fact_count": len(rows),
                "performance_sync_status": "complete" if rows else "no_facts",
            }
        )
    output.sort(key=lambda row: (-float(row.get("spend_native") or 0), row["name"]))
    return output


def attach_snapchat_v2_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get("/snapchat-v2/status")
    async def snapchat_v2_status_route(
        ad_account_id: str | None = Query(default=None, max_length=128),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = _user_id(user, require_owner)
        result = await snapchat_v2_status(db, user_id, ad_account_id)
        return {
            **result,
            "shadow_enabled": _shadow_enabled(),
            "ui_enabled": _ui_enabled(),
        }

    @router.post("/snapchat-v2/sync")
    async def snapchat_v2_sync_route(
        payload: SnapchatV2SyncInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        if not _shadow_enabled():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "snapchat_v2_shadow_disabled",
                    "message": "Snapchat V2 Shadow Sync غير مفعّل في بيئة Backend.",
                },
            )
        user_id = _user_id(user, require_owner)
        pipeline = SnapchatV2SyncPipeline(db)
        return await pipeline.run(
            user_id,
            payload.ad_account_id,
            date_from=payload.date_from,
            date_to=payload.date_to,
            action_report_time=payload.action_report_time,
            run_type=payload.run_type,
        )

    @router.get("/snapchat-v2/accounts")
    async def snapchat_v2_accounts_route(
        include_stale: bool = Query(default=False),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = _user_id(user, require_owner)
        rows = await list_accounts(db, user_id, active_only=not include_stale)
        return {
            "provider": "snapchat_ads",
            "accounts": rows,
            "selected_account": next(
                (row for row in rows if row.get("selected") is True),
                None,
            ),
            "source_only": True,
        }

    @router.get("/snapchat-v2/report")
    async def snapchat_v2_report_route(
        date_from: date = Query(...),
        date_to: date = Query(...),
        timezone: Literal["account", "riyadh"] = Query(default="riyadh"),
        action_report_time: Literal["conversion", "impression"] = Query(
            default="conversion"
        ),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _read_days(date_from, date_to)
        user_id = _user_id(user, require_owner)
        account = await _selected_account_or_404(db, user_id)
        timezone_name = _projection_timezone(account, timezone)
        rows = await list_daily_projections(
            db,
            user_id=user_id,
            ad_account_id=str(account["ad_account_id"]),
            date_from=date_from,
            date_to=date_to,
            projection_timezone=timezone_name,
            action_report_time=action_report_time,
        )
        reconciliation = await list_reconciliation(
            db,
            user_id=user_id,
            ad_account_id=str(account["ad_account_id"]),
            date_from=date_from,
            date_to=date_to,
            action_report_time=action_report_time,
        )
        return {
            "provider": "snapchat_ads",
            "ad_account_id": account["ad_account_id"],
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "projection_timezone": timezone_name,
            "account_timezone": account.get("timezone"),
            "currency": account.get("currency"),
            "action_report_time": action_report_time,
            "base_spend_native": _sum_projection(rows, "base_spend_native"),
            "impressions": _sum_projection(rows, "impressions"),
            "swipes": _sum_projection(rows, "swipes"),
            "video_views": _sum_projection(rows, "video_views"),
            "purchases": _sum_projection(rows, "purchases"),
            "purchase_value_native": _sum_projection(
                rows,
                "purchase_value_native",
            ),
            "amount_complete": bool(rows)
            and all(row.get("amount_complete") is True for row in rows),
            "days": rows,
            "reconciliation": reconciliation,
            "source_collection": "mezan_snapchat_hourly_facts_v2",
            "shadow_mode": True,
            "ui_enabled": _ui_enabled(),
        }

    @router.get("/snapchat-v2/hourly")
    async def snapchat_v2_hourly_route(
        report_date: date = Query(...),
        timezone: Literal["account", "riyadh"] = Query(default="account"),
        action_report_time: Literal["conversion", "impression"] = Query(
            default="conversion"
        ),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = _user_id(user, require_owner)
        account = await _selected_account_or_404(db, user_id)
        timezone_name = _projection_timezone(account, timezone)
        rows = await list_daily_projections(
            db,
            user_id=user_id,
            ad_account_id=str(account["ad_account_id"]),
            date_from=report_date,
            date_to=report_date,
            projection_timezone=timezone_name,
            action_report_time=action_report_time,
        )
        projection = rows[0] if rows else None
        return {
            "provider": "snapchat_ads",
            "ad_account_id": account["ad_account_id"],
            "report_date": report_date.isoformat(),
            "projection_timezone": timezone_name,
            "account_timezone": account.get("timezone"),
            "currency": account.get("currency"),
            "action_report_time": action_report_time,
            "base_spend_native": (projection or {}).get("base_spend_native"),
            "amount_complete": (projection or {}).get("amount_complete") is True,
            "hours": (projection or {}).get("hours") or [],
            "coverage": (projection or {}).get("coverage") or {},
            "future_hours_are_zero": False,
            "source_collection": "mezan_snapchat_hourly_facts_v2",
        }

    @router.get("/snapchat-v2/campaigns")
    async def snapchat_v2_campaigns_route(
        date_from: date = Query(...),
        date_to: date = Query(...),
        timezone: Literal["account", "riyadh"] = Query(default="account"),
        action_report_time: Literal["conversion", "impression"] = Query(
            default="conversion"
        ),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _read_days(date_from, date_to)
        user_id = _user_id(user, require_owner)
        account = await _selected_account_or_404(db, user_id)
        timezone_name = _projection_timezone(account, timezone)
        rows = await _campaign_report(
            db,
            user_id=user_id,
            account=account,
            date_from=date_from,
            date_to=date_to,
            timezone_name=timezone_name,
            action_report_time=action_report_time,
        )
        return {
            "provider": "snapchat_ads",
            "ad_account_id": account["ad_account_id"],
            "projection_timezone": timezone_name,
            "campaigns": rows,
        }

    @router.get("/snapchat-v2/ad-squads")
    async def snapchat_v2_ad_squads_route(
        include_stale: bool = Query(default=False),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = _user_id(user, require_owner)
        account = await _selected_account_or_404(db, user_id)
        rows = await list_entities(
            db,
            user_id=user_id,
            ad_account_id=str(account["ad_account_id"]),
            entity_type="ad_squad",
            active_only=not include_stale,
        )
        return {
            "provider": "snapchat_ads",
            "ad_account_id": account["ad_account_id"],
            "ad_squads": rows,
            "performance_sync_status": "partial",
            "performance_reason": "ad_squad_performance_shadow_pending",
        }

    @router.get("/snapchat-v2/ads")
    async def snapchat_v2_ads_route(
        include_stale: bool = Query(default=False),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = _user_id(user, require_owner)
        account = await _selected_account_or_404(db, user_id)
        rows = await list_entities(
            db,
            user_id=user_id,
            ad_account_id=str(account["ad_account_id"]),
            entity_type="ad",
            active_only=not include_stale,
        )
        return {
            "provider": "snapchat_ads",
            "ad_account_id": account["ad_account_id"],
            "ads": rows,
            "performance_sync_status": "partial",
            "performance_reason": "ad_performance_shadow_pending",
        }

    @router.get("/snapchat-v2/reconciliation")
    async def snapchat_v2_reconciliation_route(
        date_from: date = Query(...),
        date_to: date = Query(...),
        action_report_time: Literal["conversion", "impression"] = Query(
            default="conversion"
        ),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _read_days(date_from, date_to)
        user_id = _user_id(user, require_owner)
        account = await _selected_account_or_404(db, user_id)
        rows = await list_reconciliation(
            db,
            user_id=user_id,
            ad_account_id=str(account["ad_account_id"]),
            date_from=date_from,
            date_to=date_to,
            action_report_time=action_report_time,
        )
        return {
            "provider": "snapchat_ads",
            "ad_account_id": account["ad_account_id"],
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "rows": rows,
            "reconciled": bool(rows) and all(row.get("reconciled") for row in rows),
        }


__all__ = ["SnapchatV2SyncInput", "attach_snapchat_v2_routes"]
