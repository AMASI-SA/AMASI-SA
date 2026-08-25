"""Versioned read-only APIs for the Snapchat Integration V2 shadow plane."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from unified_marketing.adapters.snapchat_v2 import build_snapchat_v2_unified_report
from unified_marketing.commerce_carts import load_abandoned_cart_outcomes

from .accounts import get_selected_account, list_accounts
from .entities import list_entities
from .facts import load_hourly_facts
from .models import clean_text
from .projections import (
    RIYADH_TIMEZONE,
    business_day_window,
    list_daily_projections,
)
from .reconciliation import calculate_cost_components, list_reconciliation
from .salla_outcomes import load_salla_campaign_outcomes
from .status import snapchat_v2_status
from .sync_pipeline import MAX_SYNC_DAYS, SnapchatV2SyncPipeline
from .total_facts import (
    SNAPCHAT_TOTAL_FACTS_COLLECTION,
    load_total_facts,
)


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
    if field in {
        "impressions",
        "swipes",
        "video_views",
        "view_content",
        "add_to_cart",
        "start_checkout",
        "add_billing",
        "purchases",
    }:
        return int(total)
    return round(total, 6)


def _resolved_range(
    account: dict[str, Any],
    date_from: date | None,
    date_to: date | None,
) -> tuple[date, date]:
    if (date_from is None) != (date_to is None):
        raise HTTPException(status_code=422, detail="invalid_date_range")
    if date_from is None:
        timezone_name = clean_text(account.get("timezone"), limit=80)
        current = datetime.now(ZoneInfo(timezone_name)).date()
        return current, current
    _read_days(date_from, date_to)
    return date_from, date_to


async def _latest_level_status(
    db: Any,
    *,
    user_id: str,
    ad_account_id: str,
    entity_type: str,
) -> str:
    field = {
        "campaign": "campaign_sync_status",
        "ad_squad": "ad_squad_sync_status",
        "ad": "ad_sync_status",
    }[entity_type]
    row = await db["mezan_snapchat_sync_runs_v2"].find_one(
        {
            "user_id": str(user_id),
            "ad_account_id": str(ad_account_id),
        },
        {"_id": 0, field: 1, "finished_at": 1, "started_at": 1},
        sort=[("started_at", -1)],
    )
    return str((row or {}).get(field) or "partial")


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    spend = _sum_projection(rows, "spend_native")
    purchases = _sum_projection(rows, "purchases")
    purchase_value = _sum_projection(rows, "purchase_value_native")
    impressions = _sum_projection(rows, "impressions")
    swipes = _sum_projection(rows, "swipes")
    video_views = _sum_projection(rows, "video_views")
    view_completion = _sum_projection(rows, "view_completion")
    view_content = _sum_projection(rows, "view_content")
    add_to_cart = _sum_projection(rows, "add_to_cart")
    start_checkout = _sum_projection(rows, "start_checkout")
    add_billing = _sum_projection(rows, "add_billing")
    exact_audience_row = len(rows) == 1 and str(
        rows[0].get("reach_frequency_scope") or ""
    ) == "exact_one_day_total"
    return {
        "spend_native": spend,
        "impressions": impressions,
        "swipes": swipes,
        "video_views": video_views,
        "view_completion": view_completion,
        "view_content": view_content,
        "add_to_cart": add_to_cart,
        "start_checkout": start_checkout,
        "add_billing": add_billing,
        "paid_reach": (
            int(rows[0].get("paid_reach"))
            if exact_audience_row and rows[0].get("paid_reach") is not None
            else None
        ),
        "paid_frequency": (
            float(rows[0].get("paid_frequency"))
            if exact_audience_row and rows[0].get("paid_frequency") is not None
            else None
        ),
        "reach_frequency_scope": (
            "exact_one_day_total"
            if exact_audience_row
            else "exact_total_window_required"
        ),
        "purchases": purchases,
        "purchase_value_native": purchase_value,
        "roas": (
            round(float(purchase_value) / float(spend), 6)
            if float(spend) > 0
            else None
        ),
        "ctr_pct": (
            round((float(swipes) / float(impressions)) * 100, 6)
            if float(impressions) > 0
            else None
        ),
        "source_fact_count": len(rows),
    }


def _total_fact_date_coverage_complete(
    rows: list[dict[str, Any]],
    *,
    date_from: date,
    date_to: date,
) -> bool:
    expected = {
        (date_from + timedelta(days=offset)).isoformat()
        for offset in range((date_to - date_from).days + 1)
    }
    observed = {
        str(row.get("report_date") or "")
        for row in rows
        if row.get("report_date")
    }
    return bool(expected) and expected.issubset(observed)


async def _entity_performance_report(
    db: Any,
    *,
    user_id: str,
    account: dict[str, Any],
    date_from: date,
    date_to: date,
    timezone_name: str,
    action_report_time: str,
    entity_type: Literal["campaign", "ad_squad", "ad"],
    campaign_id: str | None = None,
    ad_squad_id: str | None = None,
    include_stale: bool = True,
) -> dict[str, Any]:
    start_utc, _ = business_day_window(date_from, timezone_name)
    _, end_utc = business_day_window(date_to, timezone_name)
    account_id = str(account["ad_account_id"])
    level_status = await _latest_level_status(
        db,
        user_id=user_id,
        ad_account_id=account_id,
        entity_type=entity_type,
    )
    facts = await load_hourly_facts(
        db,
        user_id=user_id,
        ad_account_id=account_id,
        start_utc=start_utc,
        end_utc=end_utc,
        entity_type=entity_type,
        action_report_time=action_report_time,
    )
    source_collection = "mezan_snapchat_hourly_facts_v2"
    if timezone_name == str(account.get("timezone") or ""):
        try:
            total_facts = await load_total_facts(
                db,
                user_id=user_id,
                ad_account_id=account_id,
                entity_type=entity_type,
                date_from=date_from,
                date_to=date_to,
                account_timezone=timezone_name,
                action_report_time=action_report_time,
            )
        except (AttributeError, KeyError, TypeError):
            total_facts = []
        if (
            total_facts
            and level_status == "complete"
            and _total_fact_date_coverage_complete(
                total_facts,
                date_from=date_from,
                date_to=date_to,
            )
        ):
            facts = total_facts
            source_collection = SNAPCHAT_TOTAL_FACTS_COLLECTION
    identities = await list_entities(
        db,
        user_id=user_id,
        ad_account_id=account_id,
        entity_type=entity_type,
        active_only=not include_stale,
        limit=20_000,
    )
    campaigns = await list_entities(
        db,
        user_id=user_id,
        ad_account_id=account_id,
        entity_type="campaign",
        active_only=False,
        limit=20_000,
    )
    campaign_by_id = {
        str(row.get("external_id")): row for row in campaigns
    }
    ad_squads: list[dict[str, Any]] = []
    ad_squad_by_id: dict[str, dict[str, Any]] = {}
    if entity_type == "ad":
        ad_squads = await list_entities(
            db,
            user_id=user_id,
            ad_account_id=account_id,
            entity_type="ad_squad",
            active_only=False,
            limit=20_000,
        )
        ad_squad_by_id = {
            str(row.get("external_id")): row for row in ad_squads
        }

    identity_by_id = {
        str(row.get("external_id")): row for row in identities
    }
    buckets: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        external_id = str(fact.get("external_id") or "")
        if external_id:
            buckets.setdefault(external_id, []).append(fact)

    rows: list[dict[str, Any]] = []
    selected_facts: list[dict[str, Any]] = []
    for external_id in sorted(set(identity_by_id) | set(buckets)):
        identity = identity_by_id.get(external_id) or {}
        fact_rows = buckets.get(external_id, [])
        row_campaign_id = str(identity.get("campaign_id") or "")
        row_ad_squad_id = str(identity.get("ad_squad_id") or "")
        if fact_rows:
            row_campaign_id = str(
                fact_rows[0].get("campaign_id") or row_campaign_id
            )
            row_ad_squad_id = str(
                fact_rows[0].get("ad_squad_id") or row_ad_squad_id
            )
        if entity_type == "ad" and row_ad_squad_id:
            parent = ad_squad_by_id.get(row_ad_squad_id) or {}
            row_campaign_id = str(parent.get("campaign_id") or row_campaign_id)
        if campaign_id and row_campaign_id != campaign_id:
            continue
        if ad_squad_id and row_ad_squad_id != ad_squad_id:
            continue
        selected_facts.extend(fact_rows)
        campaign = campaign_by_id.get(row_campaign_id) or {}
        parent_ad_squad = ad_squad_by_id.get(row_ad_squad_id) or {}
        row = {
            "account_id": account_id,
            "entity_type": entity_type,
            "external_id": external_id,
            "name": identity.get("name") or external_id,
            "status": identity.get("status"),
            "active": identity.get("active"),
            "campaign_id": row_campaign_id or None,
            "campaign_name": campaign.get("name") or row_campaign_id or None,
            "ad_squad_id": row_ad_squad_id or None,
            "ad_squad_name": (
                parent_ad_squad.get("name") or row_ad_squad_id or None
            ),
            **_metrics(fact_rows),
            "source_collection": source_collection,
            "performance_sync_status": (
                "complete"
                if fact_rows and level_status == "complete"
                else "partial"
                if fact_rows
                else "no_facts"
            ),
        }
        if entity_type == "campaign":
            row["campaign_id"] = external_id
            row["campaign_name"] = row["name"]
        elif entity_type == "ad_squad":
            row["ad_squad_id"] = external_id
            row["ad_squad_name"] = row["name"]
        else:
            row["ad_id"] = external_id
            row["ad_name"] = row["name"]
        rows.append(row)

    rows.sort(
        key=lambda row: (
            -int(row.get("active") is True),
            -float(row.get("spend_native") or 0),
            str(row.get("name") or "").casefold(),
        )
    )
    return {
        "rows": rows,
        "totals": {
            **_metrics(selected_facts),
            "source_collection": source_collection,
        },
        "performance_sync_status": level_status,
        "source_collection": source_collection,
    }


async def _add_sar_spend(
    db: Any,
    *,
    user_id: str,
    account: dict[str, Any],
    rows: list[dict[str, Any]],
    totals: dict[str, Any],
) -> tuple[float | None, dict[str, Any]]:
    try:
        cost = await calculate_cost_components(
            db,
            user_id=user_id,
            account=account,
            spend_native=1.0,
        )
        exchange_rate = float(cost.get("exchange_rate_to_sar") or 0) or None
        coverage = dict(cost.get("cost_coverage") or {})
    except Exception as exc:  # noqa: BLE001
        exchange_rate = None
        coverage = {
            "status": "incomplete",
            "reason": str(type(exc).__name__)[:96],
        }
    for value in rows:
        value["exchange_rate_to_sar"] = exchange_rate
        value["spend_sar"] = (
            round(float(value.get("spend_native") or 0) * exchange_rate, 2)
            if exchange_rate is not None
            and int(value.get("source_fact_count") or 0) > 0
            else None
        )
    totals["exchange_rate_to_sar"] = exchange_rate
    totals["spend_sar"] = (
        round(float(totals.get("spend_native") or 0) * exchange_rate, 2)
        if exchange_rate is not None
        else None
    )
    return exchange_rate, coverage


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
            "view_completion": _sum_projection(rows, "view_completion"),
            "view_content": _sum_projection(rows, "view_content"),
            "add_to_cart": _sum_projection(rows, "add_to_cart"),
            "start_checkout": _sum_projection(rows, "start_checkout"),
            "add_billing": _sum_projection(rows, "add_billing"),
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
        performance = await _entity_performance_report(
            db,
            user_id=user_id,
            account=account,
            date_from=date_from,
            date_to=date_to,
            timezone_name=timezone_name,
            action_report_time=action_report_time,
            entity_type="campaign",
            include_stale=True,
        )
        rows = performance["rows"]
        totals = dict(performance["totals"])
        _, cost_coverage = await _add_sar_spend(
            db,
            user_id=user_id,
            account=account,
            rows=rows,
            totals=totals,
        )
        account_id = str(account["ad_account_id"])
        identities = [
            {
                "account_id": account_id,
                "campaign_id": row["campaign_id"],
                "campaign_name": row["campaign_name"],
            }
            for row in rows
        ]
        try:
            salla = await load_salla_campaign_outcomes(
                db,
                user_id,
                account_id=account_id,
                date_from=date_from,
                date_to=date_to,
                timezone_name=timezone_name,
                identities=identities,
                platform_purchases=int(
                    performance["totals"].get("purchases") or 0
                ),
                campaign_spend_sar={
                    str(row.get("campaign_id") or ""): float(
                        row.get("spend_sar") or 0
                    )
                    for row in rows
                    if row.get("campaign_id")
                },
            )
            salla_available = True
        except Exception as exc:  # noqa: BLE001
            salla_available = False
            salla = {
                "by_campaign": {},
                "summary": {
                    "campaign_matched_financial_orders": None,
                    "campaign_matched_financial_sales_sar": None,
                    "platform_attributed_purchases": int(
                        performance["totals"].get("purchases") or 0
                    ),
                    "coverage_status": "partial",
                    "reason": str(type(exc).__name__)[:96],
                },
                "orders": [],
                "orders_total": 0,
                "orders_returned": 0,
                "truncated": False,
                "source_collection": "unified_orders",
                "source_only": True,
            }
        try:
            carts = await load_abandoned_cart_outcomes(
                db,
                user_id,
                provider="snapchat_ads",
                campaign_ids=[row["campaign_id"] for row in identities],
                date_from=date_from,
                date_to=date_to,
            )
        except Exception as exc:  # noqa: BLE001
            carts = {
                "by_campaign": {},
                "store_level": None,
                "coverage": {
                    "status": "partial",
                    "reason": str(type(exc).__name__)[:96],
                    "read_only": True,
                },
            }
        for row in rows:
            salla_result = (
                {
                    **dict(
                        salla["by_campaign"].get(
                            row["campaign_id"],
                            {"orders": 0, "sales_sar": 0.0},
                        )
                    ),
                    "status": "complete",
                }
                if salla_available
                else {
                    "status": "partial",
                    "orders": None,
                    "sales_sar": None,
                    "roas": None,
                }
            )
            spend_sar = row.get("spend_sar")
            salla_result["abandoned_carts"] = carts["by_campaign"].get(
                str(row.get("campaign_id") or "")
            )
            salla_result["roas"] = (
                round(float(salla_result.get("sales_sar") or 0) / spend_sar, 6)
                if salla_available and spend_sar and spend_sar > 0
                else None
            )
            row.update(
                {
                    "snapchat_results": {
                        "purchases": row.get("purchases"),
                        "purchase_value_native": row.get("purchase_value_native"),
                        "roas": row.get("roas"),
                    },
                    "salla_results": salla_result,
                }
            )

        totals_spend_sar = totals.get("spend_sar")
        salla_summary = salla["summary"]
        totals.update(
            {
                "snapchat_results": {
                    "purchases": totals.get("purchases"),
                    "purchase_value_native": totals.get("purchase_value_native"),
                    "roas": totals.get("roas"),
                },
                "salla_results": {
                    "status": "complete" if salla_available else "partial",
                    "orders": (
                        salla_summary.get("snapchat_attributed_orders")
                        if salla_available
                        else None
                    ),
                    "sales_sar": (
                        salla_summary.get("snapchat_attributed_sales_sar")
                        if salla_available
                        else None
                    ),
                    "matched_orders": (
                        salla_summary.get("campaign_matched_orders")
                        if salla_available
                        else None
                    ),
                    "matched_sales_sar": (
                        salla_summary.get("campaign_matched_financial_sales_sar")
                        if salla_available
                        else None
                    ),
                    "attribution_gap_orders": (
                        salla_summary.get("snapchat_attribution_gap_orders")
                        if salla_available
                        else None
                    ),
                    "campaign_match_coverage_pct": (
                        salla_summary.get("campaign_match_coverage_pct")
                        if salla_available
                        else None
                    ),
                    "attribution_scope": (
                        "salla_reported_snapchat_source_or_exact_campaign_match"
                    ),
                    "roas": (
                        round(
                            float(
                                salla_summary["snapchat_attributed_sales_sar"]
                            )
                            / totals_spend_sar,
                            6,
                        )
                        if salla_available
                        and totals_spend_sar
                        and totals_spend_sar > 0
                        else None
                    ),
                },
            }
        )
        unified = build_snapchat_v2_unified_report(
            account_value=account,
            period_value={
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "timezone": timezone_name,
                "action_report_time": action_report_time,
            },
            entity_type="campaign",
            rows=rows,
            totals=totals,
            sync_status=performance["performance_sync_status"],
            orders=salla["orders"],
            order_summary={
                **salla["summary"],
                "orders_total": salla["orders_total"],
                "orders_returned": salla["orders_returned"],
                "truncated": salla["truncated"],
            },
        )
        return {
            "provider": "snapchat_ads",
            "ad_account_id": account_id,
            "projection_timezone": timezone_name,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "currency": account.get("currency"),
            "campaigns": rows,
            "totals": totals,
            "salla": salla,
            "abandoned_carts": carts,
            "cost_coverage": cost_coverage,
            "performance_sync_status": performance["performance_sync_status"],
            "source_collection": performance["source_collection"],
            "unified": unified,
        }

    @router.get("/snapchat-v2/ad-squads")
    async def snapchat_v2_ad_squads_route(
        date_from: date | None = Query(default=None),
        date_to: date | None = Query(default=None),
        timezone: Literal["account", "riyadh"] = Query(default="account"),
        action_report_time: Literal["conversion", "impression"] = Query(
            default="conversion"
        ),
        campaign_id: str | None = Query(default=None, max_length=128),
        include_stale: bool = Query(default=False),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = _user_id(user, require_owner)
        account = await _selected_account_or_404(db, user_id)
        start, end = _resolved_range(account, date_from, date_to)
        timezone_name = _projection_timezone(account, timezone)
        performance = await _entity_performance_report(
            db,
            user_id=user_id,
            account=account,
            date_from=start,
            date_to=end,
            timezone_name=timezone_name,
            action_report_time=action_report_time,
            entity_type="ad_squad",
            campaign_id=campaign_id,
            include_stale=include_stale,
        )
        rows = performance["rows"]
        totals = dict(performance["totals"])
        _, cost_coverage = await _add_sar_spend(
            db,
            user_id=user_id,
            account=account,
            rows=rows,
            totals=totals,
        )
        unified = build_snapchat_v2_unified_report(
            account_value=account,
            period_value={
                "date_from": start.isoformat(),
                "date_to": end.isoformat(),
                "timezone": timezone_name,
                "action_report_time": action_report_time,
            },
            entity_type="ad_squad",
            rows=rows,
            totals=totals,
            sync_status=performance["performance_sync_status"],
        )
        return {
            "provider": "snapchat_ads",
            "ad_account_id": account["ad_account_id"],
            "projection_timezone": timezone_name,
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "currency": account.get("currency"),
            "campaign_id": campaign_id,
            "ad_squads": rows,
            "totals": totals,
            "performance_sync_status": performance["performance_sync_status"],
            "cost_coverage": cost_coverage,
            "source_collection": performance["source_collection"],
            "unified": unified,
        }

    @router.get("/snapchat-v2/ads")
    async def snapchat_v2_ads_route(
        date_from: date | None = Query(default=None),
        date_to: date | None = Query(default=None),
        timezone: Literal["account", "riyadh"] = Query(default="account"),
        action_report_time: Literal["conversion", "impression"] = Query(
            default="conversion"
        ),
        campaign_id: str | None = Query(default=None, max_length=128),
        ad_squad_id: str | None = Query(default=None, max_length=128),
        include_stale: bool = Query(default=False),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = _user_id(user, require_owner)
        account = await _selected_account_or_404(db, user_id)
        start, end = _resolved_range(account, date_from, date_to)
        timezone_name = _projection_timezone(account, timezone)
        performance = await _entity_performance_report(
            db,
            user_id=user_id,
            account=account,
            date_from=start,
            date_to=end,
            timezone_name=timezone_name,
            action_report_time=action_report_time,
            entity_type="ad",
            campaign_id=campaign_id,
            ad_squad_id=ad_squad_id,
            include_stale=include_stale,
        )
        rows = performance["rows"]
        totals = dict(performance["totals"])
        _, cost_coverage = await _add_sar_spend(
            db,
            user_id=user_id,
            account=account,
            rows=rows,
            totals=totals,
        )
        unified = build_snapchat_v2_unified_report(
            account_value=account,
            period_value={
                "date_from": start.isoformat(),
                "date_to": end.isoformat(),
                "timezone": timezone_name,
                "action_report_time": action_report_time,
            },
            entity_type="ad",
            rows=rows,
            totals=totals,
            sync_status=performance["performance_sync_status"],
        )
        return {
            "provider": "snapchat_ads",
            "ad_account_id": account["ad_account_id"],
            "projection_timezone": timezone_name,
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "currency": account.get("currency"),
            "campaign_id": campaign_id,
            "ad_squad_id": ad_squad_id,
            "ads": rows,
            "totals": totals,
            "performance_sync_status": performance["performance_sync_status"],
            "cost_coverage": cost_coverage,
            "source_collection": performance["source_collection"],
            "unified": unified,
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
