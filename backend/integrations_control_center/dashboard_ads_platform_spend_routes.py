"""Four-platform advertising spend for the Mezan 2 Dashboard.

The read route combines provider-reported Snapchat, Meta, TikTok, and Google Ads
facts. The refresh route performs bounded analytical reads from connected
providers and only writes isolated V2 reporting projections.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, model_validator

from dashboard_snapchat_spend import load_snapchat_dashboard_spend
from unified_marketing.dashboard_shadow import (
    build_dashboard_unified_shadow,
    load_dashboard_unified_shadow,
    persist_dashboard_unified_shadow,
)
from unified_marketing.gateway import (
    load_unified_marketing_account_report,
    load_unified_marketing_dashboard_spend,
)

from .ads_platform_hourly import PLATFORM_HOURLY_COLLECTION
from .dashboard_ads_platform_refresh import (
    FOUR_PLATFORM_KEYS,
    MAX_REFRESH_DAYS,
    refresh_dashboard_platform_spend,
)
from .google_ads_reporting import (
    GOOGLE_ADS_DAILY_COLLECTION,
    GOOGLE_ADS_PROVIDER_ID,
)
from .meta_native_reporting import META_REPORTING_COLLECTION
from .meta_oauth_security import META_PROVIDER_ID
from .snapchat_native_data_common import (
    SNAPCHAT_PERFORMANCE_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
)
from .tiktok_native_reporting import TIKTOK_REPORTING_COLLECTION
from .tiktok_oauth_security import TIKTOK_PROVIDER_ID

RIYADH_TZ = ZoneInfo("Asia/Riyadh")
MAX_READ_DAYS = 90
MAX_DAILY_ROWS = 20_000
MAX_HOURLY_ROWS = 20_000

DAILY_COLLECTION_BY_PROVIDER = {
    "snapchat": SNAPCHAT_PERFORMANCE_COLLECTION,
    "meta": META_REPORTING_COLLECTION,
    "tiktok": TIKTOK_REPORTING_COLLECTION,
    "google": GOOGLE_ADS_DAILY_COLLECTION,
}
INTEGRATION_PROVIDER_BY_KEY = {
    "snapchat": SNAPCHAT_PROVIDER_ID,
    "meta": META_PROVIDER_ID,
    "tiktok": TIKTOK_PROVIDER_ID,
    "google": GOOGLE_ADS_PROVIDER_ID,
}


class DashboardPlatformSpendRefreshInput(BaseModel):
    date_from: str
    date_to: str

    @model_validator(mode="after")
    def validate_range(self):
        start = date.fromisoformat(self.date_from)
        end = date.fromisoformat(self.date_to)
        days = (end - start).days + 1
        if end < start or days > MAX_REFRESH_DAYS:
            raise ValueError(
                f"Dashboard advertising refresh supports at most {MAX_REFRESH_DAYS} days"
            )
        return self


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed != parsed or abs(parsed) == float("inf") or parsed < 0:
        return None
    return parsed


def _ledger_number(value: Any) -> float | None:
    """Parse signed ledger amounts, including cumulative correction rows."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed != parsed or abs(parsed) == float("inf"):
        return None
    return parsed


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _to_list(cursor: Any, length: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=length)
    return [row async for row in cursor]


def _date_list(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


async def _selected_account_ids(
    db: Any,
    user_id: str,
    provider: str,
) -> list[str]:
    query: dict[str, Any] = {
        "user_id": user_id,
        "provider": provider,
        "connection_status": "connected",
    }
    if provider == SNAPCHAT_PROVIDER_ID:
        query["mezan_selected"] = True
    cursor = db.mezan_integration_accounts_v2.find(
        query,
        {"_id": 0, "external_account_id": 1, "ad_account_id": 1},
    )
    ids = {
        str(row.get("ad_account_id") or row.get("external_account_id") or "").strip()
        for row in await _to_list(cursor, 250)
    }
    return sorted(account_id for account_id in ids if account_id)


async def _connection_states(db: Any, user_id: str) -> dict[str, dict[str, Any]]:
    cursor = db.mezan_integrations_v2.find(
        {
            "user_id": user_id,
            "provider": {"$in": list(INTEGRATION_PROVIDER_BY_KEY.values())},
        },
        {
            "_id": 0,
            "provider": 1,
            "connection_status": 1,
            "connection_provenance": 1,
            "data_quality": 1,
            "last_sync_at": 1,
            "data_delay_minutes": 1,
        },
    )
    by_integration = {
        str(row.get("provider") or ""): row
        for row in await _to_list(cursor, 20)
    }
    return {
        key: by_integration.get(provider_id, {})
        for key, provider_id in INTEGRATION_PROVIDER_BY_KEY.items()
    }


async def _daily_spend(
    db: Any,
    user_id: str,
    start: date,
    end: date,
    snapchat: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[
    list[dict[str, Any]],
    dict[str, bool],
    dict[str, dict[str, str]],
]:
    dates = _date_list(start, end)
    current_day = (now or datetime.now(timezone.utc)).astimezone(RIYADH_TZ).date()
    by_date: dict[str, dict[str, Any]] = {
        day.isoformat(): {
            "date": day.isoformat(),
            **{provider: None for provider in FOUR_PLATFORM_KEYS},
        }
        for day in dates
    }
    facts = {provider: False for provider in FOUR_PLATFORM_KEYS}
    daily_states = {
        provider: {day.isoformat(): "waiting_incomplete" for day in dates}
        for provider in FOUR_PLATFORM_KEYS
    }
    for day in dates:
        day_text = day.isoformat()
        by_date[day_text]["snapchat"] = (
            snapchat.get("daily_sar") or {}
        ).get(day_text)
        daily_states["snapchat"][day_text] = str(
            (snapchat.get("daily_state") or {}).get(day_text)
            or "waiting_incomplete"
        )
    facts["snapchat"] = (
        (snapchat.get("quality") or {}).get("amount_available") is True
        or (snapchat.get("quality") or {}).get("amount_complete") is True
    )

    for provider, collection_name in DAILY_COLLECTION_BY_PROVIDER.items():
        if provider == "snapchat":
            continue
        query: dict[str, Any] = {
            "user_id": user_id,
            "provider": INTEGRATION_PROVIDER_BY_KEY[provider],
            "date": {
                "$gte": start.isoformat(),
                "$lte": end.isoformat(),
            },
        }
        cursor = db[collection_name].find(
            query,
            {
                "_id": 0,
                "date": 1,
                "spend_sar": 1,
                "empty_provider_row": 1,
                "observed_at": 1,
            },
        )
        rows = await _to_list(cursor, MAX_DAILY_ROWS)
        totals: dict[str, float] = defaultdict(float)
        observed_dates: set[str] = set()
        for row in rows:
            day_text = str(row.get("date") or "")[:10]
            spend = _number(row.get("spend_sar"))
            if day_text not in by_date or spend is None:
                continue
            # An empty first provider payload after the Riyadh date rollover is
            # not proof of a real zero. Keep the open day waiting until at least
            # one provider row is returned. Closed dates retain their existing
            # historical zero semantics.
            if (
                day_text == current_day.isoformat()
                and row.get("empty_provider_row") is True
            ):
                continue
            totals[day_text] += spend
            observed_dates.add(day_text)
        for day_text in by_date:
            if day_text in observed_dates:
                by_date[day_text][provider] = round(totals.get(day_text, 0.0), 2)
                daily_states[provider][day_text] = (
                    "confirmed_data" if totals.get(day_text, 0.0) > 0 else "confirmed_zero"
                )

    # Make.com is the temporary TikTok transport until the merchant completes
    # the direct Marketing API connection. The financial SSOT for that bridge
    # is ad_account_ledger. While Make has a ledger amount for a date, keep it
    # authoritative even if partial native reporting also exists. Native data
    # remains the fallback for dates without Make rows, so the transition
    # to direct TikTok can never double-count spend.
    tiktok_accounts = await _to_list(
        db.counterparties.find(
            {
                "user_id": user_id,
                "kind": "ad_account",
                "ad_provider": "tiktok",
            },
            {"_id": 0, "id": 1},
        ),
        200,
    )
    tiktok_account_ids = [row.get("id") for row in tiktok_accounts if row.get("id")]
    if tiktok_account_ids:
        ledger_rows = await _to_list(
            db.ad_account_ledger.find(
                {
                    "user_id": user_id,
                    "counterparty_id": {"$in": tiktok_account_ids},
                    "type": "spend",
                    "date": {
                        "$gte": start.isoformat(),
                        "$lte": end.isoformat(),
                    },
                },
                {"_id": 0, "date": 1, "amount": 1},
            ),
            MAX_DAILY_ROWS,
        )
        ledger_totals: dict[str, float] = defaultdict(float)
        ledger_dates: set[str] = set()
        for row in ledger_rows:
            day_text = str(row.get("date") or "")[:10]
            amount = _ledger_number(row.get("amount"))
            if day_text not in by_date or amount is None:
                continue
            ledger_totals[day_text] += amount
            ledger_dates.add(day_text)
        for day_text in ledger_dates:
            by_date[day_text]["tiktok"] = round(ledger_totals[day_text], 2)
            daily_states["tiktok"][day_text] = (
                "confirmed_data" if ledger_totals[day_text] > 0 else "confirmed_zero"
            )

    for provider in FOUR_PLATFORM_KEYS:
        facts[provider] = all(
            by_date[day.isoformat()].get(provider) is not None for day in dates
        )

    return [by_date[day.isoformat()] for day in dates], facts, daily_states


async def _hourly_spend(
    db: Any,
    user_id: str,
    selected_date: date,
    snapchat: dict[str, Any],
    *,
    tiktok_daily_total: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, bool], dict[str, str]]:
    local_start = datetime.combine(selected_date, time.min, tzinfo=RIYADH_TZ)
    local_end = local_start + timedelta(days=1)
    utc_start = local_start.astimezone(timezone.utc)
    utc_end = local_end.astimezone(timezone.utc)

    buckets: dict[str, list[float]] = {
        provider: [0.0 for _ in range(24)] for provider in FOUR_PLATFORM_KEYS
    }
    facts = {provider: False for provider in FOUR_PLATFORM_KEYS}
    sources = {provider: "" for provider in FOUR_PLATFORM_KEYS}
    snap_hours = {
        int(row.get("hour_index") or 0): row
        for row in list((snapchat.get("hourly_sar") or {}).get(
            selected_date.isoformat()
        ) or [])
        if 0 <= int(row.get("hour_index") or 0) <= 23
    }
    facts["snapchat"] = bool(snap_hours)
    if facts["snapchat"]:
        sources["snapchat"] = "provider_native"

    generic_cursor = db[PLATFORM_HOURLY_COLLECTION].find(
        {
            "user_id": user_id,
            "provider": {"$in": ["meta", "tiktok", "google"]},
            "hour_start_utc": {
                "$gte": utc_start.isoformat(timespec="seconds"),
                "$lt": utc_end.isoformat(timespec="seconds"),
            },
        },
        {"_id": 0, "provider": 1, "hour_start_utc": 1, "spend_sar": 1},
    )
    for row in await _to_list(generic_cursor, MAX_HOURLY_ROWS):
        provider = str(row.get("provider") or "")
        if provider not in {"meta", "tiktok", "google"}:
            continue
        point = _parse_utc(row.get("hour_start_utc"))
        spend = _number(row.get("spend_sar"))
        if point is None or spend is None:
            continue
        local = point.astimezone(RIYADH_TZ)
        if local.date() != selected_date:
            continue
        buckets[provider][local.hour] += spend
        facts[provider] = True

    for provider in ("meta", "tiktok", "google"):
        if facts[provider]:
            sources[provider] = "provider_native"

    # Make.com supplies a cumulative daily TikTok total, not hourly spend.
    # Show the exact total as one labelled marker at its latest update hour.
    # Native hourly facts remain authoritative once the direct API is live.
    tiktok_marker_hour: int | None = None
    if not facts["tiktok"] and tiktok_daily_total is not None:
        make_rows = await _to_list(
            db.tiktok_ads_daily.find(
                {"user_id": user_id, "date": selected_date.isoformat()},
                {"_id": 0, "updated_at": 1, "created_at": 1},
            ),
            MAX_DAILY_ROWS,
        )
        observed_points = [
            point
            for row in make_rows
            if (point := _parse_utc(row.get("updated_at") or row.get("created_at")))
            is not None
        ]
        if observed_points:
            latest_local = max(observed_points).astimezone(RIYADH_TZ)
            if latest_local.date() < selected_date:
                tiktok_marker_hour = 0
            elif latest_local.date() > selected_date:
                tiktok_marker_hour = 23
            else:
                tiktok_marker_hour = latest_local.hour
            facts["tiktok"] = True
            sources["tiktok"] = "make_daily_total_marker"

    hourly = []
    for hour_index in range(24):
        snap_hour = snap_hours.get(hour_index) or {}
        provider_values = {}
        for provider in ("meta", "tiktok", "google"):
            if provider == "tiktok" and sources[provider] == "make_daily_total_marker":
                provider_values[provider] = (
                    round(tiktok_daily_total, 2)
                    if hour_index == tiktok_marker_hour
                    else None
                )
                continue
            provider_values[provider] = (
                round(buckets[provider][hour_index], 2)
                if facts[provider]
                else None
            )
        hourly.append(
            {
                "date": selected_date.isoformat(),
                "hour_index": hour_index,
                "hour": f"{hour_index:02d}:00",
                "snapchat": _number(snap_hour.get("spend_sar")),
                "snapchat_status": snap_hour.get("status"),
                **provider_values,
            }
        )
    return hourly, facts, sources


async def build_dashboard_platform_spend(
    db: Any,
    user_id: str,
    *,
    date_from: str,
    date_to: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    try:
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_date_range") from exc
    if end < start or (end - start).days + 1 > MAX_READ_DAYS:
        raise HTTPException(status_code=422, detail="invalid_date_range")

    snapchat = await load_unified_marketing_dashboard_spend(
        db,
        user_id,
        provider="snapchat_ads",
        date_from=start,
        date_to=end,
        timezone_name="Asia/Riyadh",
    )
    try:
        unified_shadow = await load_dashboard_unified_shadow(
            db,
            user_id=user_id,
            date_from=start.isoformat(),
            date_to=end.isoformat(),
        )
    except Exception:  # noqa: BLE001 - legacy output stays authoritative
        unified_shadow = None
    if unified_shadow is None:
        unified_shadow = {
            "mode": "shadow",
            "provider": "snapchat_ads",
            "shadow_passed": False,
            "cutover_ready": False,
            "reason": "dashboard_shadow_not_refreshed",
            "decision_eligibility": {
                "eligible": False,
                "reason": "dashboard_shadow_unavailable",
            },
            "source_only": True,
            "provider_write_reached": False,
            "campaign_write_reached": False,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    daily, daily_facts, daily_states = await _daily_spend(
        db,
        user_id,
        start,
        end,
        snapchat,
        now=current,
    )
    states = await _connection_states(db, user_id)
    single_day = start == end
    hourly: list[dict[str, Any]] = []
    hourly_facts = {provider: False for provider in FOUR_PLATFORM_KEYS}
    hourly_sources = {provider: "" for provider in FOUR_PLATFORM_KEYS}
    if single_day:
        hourly, hourly_facts, hourly_sources = await _hourly_spend(
            db,
            user_id,
            start,
            snapchat,
            tiktok_daily_total=_number(daily[0].get("tiktok")),
        )
        for provider in FOUR_PLATFORM_KEYS:
            if daily_facts[provider]:
                continue
            hourly_facts[provider] = False
            hourly_sources[provider] = ""
            for row in hourly:
                row[provider] = None

    totals = {
        provider: round(
            sum(float(row.get(provider) or 0) for row in daily),
            2,
        )
        if daily_facts[provider]
        else None
        for provider in FOUR_PLATFORM_KEYS
    }
    totals["snapchat"] = snapchat.get("total_sar")
    provider_rows = {}
    for provider in FOUR_PLATFORM_KEYS:
        state = states.get(provider) or {}
        connection_status = str(state.get("connection_status") or "not_connected")
        if provider == "snapchat":
            snap_quality = snapchat.get("quality") or {}
            snap_amount_available = (
                snap_quality.get("amount_available") is True
                or snap_quality.get("amount_complete") is True
            )
            provider_rows[provider] = {
                "provider": provider,
                "integration_provider": SNAPCHAT_PROVIDER_ID,
                "connection_status": connection_status,
                "connected": snap_quality.get("connected") is True,
                "daily_available": snap_amount_available,
                "hourly_available": hourly_facts[provider],
                "hourly_source": hourly_sources[provider] or None,
                "total_sar": totals[provider],
                "data_quality": snap_quality.get("status"),
                "data_state": snap_quality.get("data_state"),
                "coverage_complete": snap_quality.get("coverage_complete") is True,
                "amount_complete": snap_quality.get("amount_complete") is True,
                "amount_available": snap_amount_available,
                "provisional": snap_quality.get("provisional") is True,
                "reason_codes": list(snap_quality.get("reason_codes") or []),
                "last_sync_at": state.get("last_sync_at"),
                "data_delay_minutes": state.get("data_delay_minutes"),
            }
            continue
        provider_rows[provider] = {
            "provider": provider,
            "integration_provider": INTEGRATION_PROVIDER_BY_KEY[provider],
            "connection_status": connection_status,
            "connected": connection_status in {
                "connected",
                "active",
                "healthy",
                "data_available",
            },
            "daily_available": daily_facts[provider],
            "hourly_available": hourly_facts[provider],
            "hourly_source": hourly_sources[provider] or None,
            "total_sar": totals[provider],
            "data_quality": state.get("data_quality"),
            "data_state": (
                "confirmed_data"
                if daily_facts[provider] and float(totals[provider] or 0) > 0
                else "confirmed_zero"
                if daily_facts[provider]
                else "waiting_incomplete"
                if connection_status in {"connected", "active", "healthy", "data_available"}
                else "not_connected"
            ),
            "coverage_complete": daily_facts[provider],
            "amount_complete": daily_facts[provider],
            "amount_available": daily_facts[provider],
            "last_sync_at": state.get("last_sync_at"),
            "data_delay_minutes": state.get("data_delay_minutes"),
        }

    known_total_sar = round(
        sum(value for value in totals.values() if value is not None), 2
    )
    snap_amount_complete = (
        (snapchat.get("quality") or {}).get("amount_complete") is True
    )
    snap_amount_available = (
        snap_amount_complete
        or (snapchat.get("quality") or {}).get("amount_available") is True
    )
    snap_amount_provisional = (
        snap_amount_available
        and not snap_amount_complete
        and (snapchat.get("quality") or {}).get("provisional") is True
    )
    connected_waiting = any(
        row.get("connected") is True and row.get("amount_available") is not True
        for row in provider_rows.values()
    )
    any_provider_fact = any(daily_facts.values())
    total_sar = known_total_sar if any_provider_fact else None
    total_amount_available = total_sar is not None
    return {
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "timezone": "Asia/Riyadh",
        "chart_granularity": "hour" if single_day else "day",
        "daily_spend": daily,
        "hourly_spend": hourly,
        "providers": provider_rows,
        "provider_totals_sar": totals,
        "total_sar": total_sar,
        "known_total_sar": known_total_sar,
        "spend_quality": {
            "status": (
                "complete"
                if snap_amount_complete and not connected_waiting
                else "provisional"
                if snap_amount_provisional and not connected_waiting
                else "incomplete"
            ),
            "amount_complete": snap_amount_complete and not connected_waiting,
            "amount_available": total_amount_available,
            "provisional": snap_amount_provisional and not connected_waiting,
            "known_total_sar": known_total_sar,
            "snapchat": snapchat.get("quality") or {},
            "providers": {
                provider: {
                    "data_state": row.get("data_state"),
                    "amount_available": row.get("amount_available") is True,
                    "coverage_complete": row.get("coverage_complete") is True,
                }
                for provider, row in provider_rows.items()
            },
            "reason_codes": (["open_day_provider_payload_waiting"] if connected_waiting else []),
        },
        "unified_marketing_shadow": unified_shadow,
        "source_contract": {
            "snapchat": snapchat.get("source_contract")
            or "unified-marketing-data-v1:snapchat-v2:riyadh-dashboard-spend",
            "meta": META_REPORTING_COLLECTION,
            "tiktok": TIKTOK_REPORTING_COLLECTION,
            "google": GOOGLE_ADS_DAILY_COLLECTION,
        },
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def attach_dashboard_ads_platform_spend_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get("/dashboard/ads-platform-spend")
    async def dashboard_ads_platform_spend(
        date_from: str = Query(...),
        date_to: str = Query(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await build_dashboard_platform_spend(
            db,
            str(owner["id"]),
            date_from=date_from,
            date_to=date_to,
        )

    @router.post("/dashboard/ads-platform-spend/refresh")
    async def dashboard_ads_platform_spend_refresh(
        payload: DashboardPlatformSpendRefreshInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner["id"])
        refresh = await refresh_dashboard_platform_spend(
            db,
            user_id,
            date_from=payload.date_from,
            date_to=payload.date_to,
        )
        spend = await build_dashboard_platform_spend(
            db,
            user_id,
            date_from=payload.date_from,
            date_to=payload.date_to,
        )
        try:
            start = date.fromisoformat(payload.date_from)
            end = date.fromisoformat(payload.date_to)
            unified_snapchat = await load_unified_marketing_account_report(
                db,
                user_id,
                provider="snapchat_ads",
                date_from=start,
                date_to=end,
                timezone_name="Asia/Riyadh",
            )
            unified_shadow = build_dashboard_unified_shadow(
                {"total_sar": spend.get("provider_totals_sar", {}).get("snapchat"),
                 "quality": (spend.get("spend_quality") or {}).get("snapchat") or {}},
                unified_snapchat,
                period_closed=end < datetime.now(RIYADH_TZ).date(),
            )
            await persist_dashboard_unified_shadow(
                db,
                user_id=user_id,
                date_from=payload.date_from,
                date_to=payload.date_to,
                shadow=unified_shadow,
            )
            spend["unified_marketing_shadow"] = unified_shadow
        except Exception as exc:  # noqa: BLE001 - refresh remains fail-closed
            spend["unified_marketing_shadow"] = {
                "mode": "shadow",
                "provider": "snapchat_ads",
                "shadow_passed": False,
                "cutover_ready": False,
                "reason": str(type(exc).__name__)[:96],
                "decision_eligibility": {
                    "eligible": False,
                    "reason": "dashboard_shadow_unavailable",
                },
            }
        return {**spend, "refresh": refresh}


__all__ = [
    "DashboardPlatformSpendRefreshInput",
    "MAX_READ_DAYS",
    "attach_dashboard_ads_platform_spend_routes",
    "build_dashboard_platform_spend",
]
