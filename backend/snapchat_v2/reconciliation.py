"""Daily Snapchat V2 reconciliation across provider, facts, and both UIs."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from ads_manager.account_cost_settings import COLLECTION as COST_SETTINGS_COLLECTION
from dashboard_v2_ad_costs import apply_cost_settings_to_fact_rows

from .models import SNAPCHAT_PROVIDER, clean_text

SNAPCHAT_RECONCILIATION_COLLECTION = "mezan_snapchat_reconciliation_v2"
INTEGRATION_ACCOUNTS_COLLECTION = "mezan_integration_accounts_v2"
NATIVE_TOLERANCE = 0.01


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_reconciliation_indexes(db: Any) -> None:
    collection = db[SNAPCHAT_RECONCILIATION_COLLECTION]
    await collection.create_index(
        [
            ("user_id", 1),
            ("provider", 1),
            ("ad_account_id", 1),
            ("report_date", 1),
            ("action_report_time", 1),
        ],
        unique=True,
        name="snapchat_v2_reconciliation_identity_unique",
    )
    await collection.create_index(
        [("user_id", 1), ("report_date", -1), ("reconciled", 1)],
        name="snapchat_v2_reconciliation_date_status",
    )


async def _to_list(cursor: Any, limit: int = 100) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        try:
            return list(await cursor.to_list(length=limit))
        except TypeError:
            return list(await cursor.to_list(limit))
    rows: list[dict[str, Any]] = []
    async for row in cursor:
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


async def _cost_context(
    db: Any,
    *,
    user_id: str,
    account: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    account_id = clean_text(
        account.get("ad_account_id") or account.get("external_account_id"),
        limit=128,
    )
    integration = await db[INTEGRATION_ACCOUNTS_COLLECTION].find_one(
        {
            "user_id": str(user_id),
            "provider": SNAPCHAT_PROVIDER,
            "$or": [
                {"external_account_id": account_id},
                {"ad_account_id": account_id},
            ],
        },
        {"_id": 0},
    )
    integration_account = {
        **dict(integration or {}),
        "user_id": str(user_id),
        "provider": SNAPCHAT_PROVIDER,
        "external_account_id": account_id,
        "ad_account_id": account_id,
        "display_name": account.get("display_name") or account_id,
        "currency": account.get("currency"),
        "timezone": account.get("timezone"),
    }
    identity = clean_text(
        integration_account.get("mezan_integration_account_id"),
        limit=128,
    )
    clauses: list[dict[str, Any]] = [
        {"provider": SNAPCHAT_PROVIDER, "external_account_id": account_id}
    ]
    if identity:
        clauses.append({"mezan_integration_account_id": identity})
    settings = await _to_list(
        db[COST_SETTINGS_COLLECTION].find(
            {"user_id": str(user_id), "$or": clauses},
            {"_id": 0},
        ),
        10,
    )
    return [integration_account], settings


async def calculate_cost_components(
    db: Any,
    *,
    user_id: str,
    account: dict[str, Any],
    spend_native: float,
) -> dict[str, Any]:
    integration_accounts, settings = await _cost_context(
        db,
        user_id=user_id,
        account=account,
    )
    account_id = clean_text(
        account.get("ad_account_id") or account.get("external_account_id"),
        limit=128,
    )
    result = apply_cost_settings_to_fact_rows(
        {
            "snapchat": [
                {
                    "user_id": str(user_id),
                    "provider": SNAPCHAT_PROVIDER,
                    "ad_account_id": account_id,
                    "currency": account.get("currency"),
                    "currency_native": account.get("currency"),
                    "spend_native": max(float(spend_native), 0.0),
                }
            ],
            "meta": [],
            "tiktok": [],
        },
        integration_accounts,
        settings,
    )
    accounts = list(result.get("accounts") or [])
    if len(accounts) != 1:
        raise ValueError("Snapchat cost settings could not be resolved uniquely")
    resolved = accounts[0]
    base_spend_sar = round(float(resolved.get("spend_sar") or 0), 2)
    commission_sar = round(
        float(resolved.get("bank_commission_fee_sar") or 0),
        2,
    )
    return {
        "native_currency": resolved.get("native_currency"),
        "exchange_rate_to_sar": resolved.get("exchange_rate_to_sar"),
        "cost_setting_configured": resolved.get("configured") is True,
        "bank_commission_pct": resolved.get("bank_commission_pct"),
        "apply_bank_commission": resolved.get("apply_bank_commission") is True,
        "base_spend_sar": base_spend_sar,
        "commission_sar": commission_sar,
        "final_cost_sar": round(base_spend_sar + commission_sar, 2),
        "cost_coverage": result.get("coverage") or {},
    }


def _within_tolerance(value: float) -> bool:
    return abs(value) < NATIVE_TOLERANCE


def _split_dual_window_total(
    provider_total: dict[str, Any],
    dashboard_provider_total: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize the dual-window result emitted by ``fetch_provider_total``.

    Older callers may still pass two explicit totals.  The shadow pipeline
    passes one backward-compatible object containing both account-day and
    Riyadh-window totals, so this helper separates them before comparison.
    """
    if dashboard_provider_total is not None:
        return provider_total, dashboard_provider_total
    account_day_native = provider_total.get("account_day_provider_spend_native")
    dashboard_native = provider_total.get("dashboard_provider_spend_native")
    if account_day_native is None or dashboard_native is None:
        return provider_total, provider_total
    account_day_total = {
        "provider_spend_native": account_day_native,
        "coverage": provider_total.get("account_day_coverage") or {},
        "window_start_utc": provider_total.get("account_day_window_start_utc"),
        "window_end_utc": provider_total.get("account_day_window_end_utc"),
    }
    dashboard_total = {
        "provider_spend_native": dashboard_native,
        "coverage": provider_total.get("dashboard_coverage")
        or provider_total.get("coverage")
        or {},
        "window_start_utc": provider_total.get("window_start_utc"),
        "window_end_utc": provider_total.get("window_end_utc"),
    }
    return account_day_total, dashboard_total


async def reconcile_day(
    db: Any,
    *,
    user_id: str,
    account: dict[str, Any],
    report_date: date,
    provider_total: dict[str, Any],
    snap_page_projection: dict[str, Any],
    dashboard_projection: dict[str, Any],
    dashboard_provider_total: dict[str, Any] | None = None,
    action_report_time: str = "conversion",
    current_open_hour: bool = False,
    sync_run_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reconcile each UI against the matching UTC window.

    The Snapchat page uses the account-local calendar while accounting uses
    Asia/Riyadh. Those calendar days are different UTC windows, so each is
    compared to a provider TOTAL request over its own exact window. Both still
    read the same immutable UTC facts collection.
    """
    current = (now or _utcnow()).astimezone(timezone.utc)
    account_id = clean_text(
        account.get("ad_account_id") or account.get("external_account_id"),
        limit=128,
    )
    currency = clean_text(account.get("currency"), limit=12).upper()
    provider_total, dashboard_provider_total = _split_dual_window_total(
        provider_total,
        dashboard_provider_total,
    )

    provider_page_native = float(provider_total.get("provider_spend_native") or 0)
    provider_dashboard_native = float(
        dashboard_provider_total.get("provider_spend_native") or 0
    )
    page_spend_native = float(snap_page_projection.get("base_spend_native") or 0)
    dashboard_spend_native = float(
        dashboard_projection.get("base_spend_native") or 0
    )
    page_difference_native = round(page_spend_native - provider_page_native, 6)
    dashboard_difference_native = round(
        dashboard_spend_native - provider_dashboard_native,
        6,
    )
    same_window = bool(
        snap_page_projection.get("window_start_utc")
        == dashboard_projection.get("window_start_utc")
        and snap_page_projection.get("window_end_utc")
        == dashboard_projection.get("window_end_utc")
    )
    projection_numeric_match = abs(page_spend_native - dashboard_spend_native) < 0.000001
    projection_source_match = bool(
        snap_page_projection.get("provider") == SNAPCHAT_PROVIDER
        and dashboard_projection.get("provider") == SNAPCHAT_PROVIDER
        and snap_page_projection.get("ad_account_id") == account_id
        and dashboard_projection.get("ad_account_id") == account_id
        and snap_page_projection.get("action_report_time")
        == dashboard_projection.get("action_report_time")
    )

    costs = await calculate_cost_components(
        db,
        user_id=str(user_id),
        account=account,
        spend_native=dashboard_spend_native,
    )
    exchange_rate = float(costs.get("exchange_rate_to_sar") or 0)
    page_difference_sar = round(page_difference_native * exchange_rate, 2)
    dashboard_difference_sar = round(
        dashboard_difference_native * exchange_rate,
        2,
    )
    page_provider_complete = (
        (provider_total.get("coverage") or {}).get("status") == "complete"
    )
    dashboard_provider_complete = (
        (dashboard_provider_total.get("coverage") or {}).get("status") == "complete"
    )
    projections_complete = bool(
        snap_page_projection.get("amount_complete")
        and dashboard_projection.get("amount_complete")
    )
    page_within = _within_tolerance(page_difference_native)
    dashboard_within = _within_tolerance(dashboard_difference_native)
    explained_open_hour = bool(
        current_open_hour and (not page_within or not dashboard_within)
    )
    reconciled = bool(
        page_provider_complete
        and dashboard_provider_complete
        and projections_complete
        and projection_source_match
        and (page_within or explained_open_hour)
        and (dashboard_within or explained_open_hour)
        and (projection_numeric_match if same_window else True)
    )

    reasons: list[str] = []
    if not page_provider_complete:
        reasons.append("provider_account_day_total_incomplete")
    if not dashboard_provider_complete:
        reasons.append("provider_riyadh_window_total_incomplete")
    if not projections_complete:
        reasons.append("projection_incomplete")
    if not projection_source_match:
        reasons.append("projection_source_mismatch")
    if same_window and not projection_numeric_match:
        reasons.append("same_window_projection_mismatch")
    if not same_window:
        reasons.append("timezone_window_difference_expected")
    if not page_within and not explained_open_hour:
        reasons.append("account_day_native_difference_exceeds_tolerance")
    if not dashboard_within and not explained_open_hour:
        reasons.append("riyadh_window_native_difference_exceeds_tolerance")
    if explained_open_hour:
        reasons.append("current_open_hour_difference")

    result = {
        "user_id": str(user_id),
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": account_id,
        "report_date": report_date.isoformat(),
        "account_timezone": account.get("timezone"),
        "dashboard_timezone": dashboard_projection.get("projection_timezone"),
        "currency": currency,
        "action_report_time": clean_text(action_report_time, limit=32).lower(),
        "provider_spend_native": round(provider_page_native, 6),
        "provider_riyadh_window_spend_native": round(
            provider_dashboard_native,
            6,
        ),
        "hourly_facts_spend_native": round(dashboard_spend_native, 6),
        "snap_page_spend_native": round(page_spend_native, 6),
        "dashboard_spend_native": round(dashboard_spend_native, 6),
        "base_spend_sar": costs["base_spend_sar"],
        "commission_sar": costs["commission_sar"],
        "final_cost_sar": costs["final_cost_sar"],
        "bank_commission_pct": costs["bank_commission_pct"],
        "apply_bank_commission": costs["apply_bank_commission"],
        "exchange_rate_to_sar": costs["exchange_rate_to_sar"],
        "cost_setting_configured": costs["cost_setting_configured"],
        "difference_native": page_difference_native,
        "difference_sar": page_difference_sar,
        "dashboard_difference_native": dashboard_difference_native,
        "dashboard_difference_sar": dashboard_difference_sar,
        "native_tolerance": NATIVE_TOLERANCE,
        "same_utc_window": same_window,
        "projection_numeric_match": projection_numeric_match,
        "projection_source_match": projection_source_match,
        "provider_coverage": provider_total.get("coverage") or {},
        "dashboard_provider_coverage": dashboard_provider_total.get("coverage")
        or {},
        "snap_page_coverage": snap_page_projection.get("coverage") or {},
        "dashboard_coverage": dashboard_projection.get("coverage") or {},
        "cost_coverage": costs.get("cost_coverage") or {},
        "current_open_hour": bool(current_open_hour),
        "reconciled": reconciled,
        "reason_codes": reasons,
        "sync_run_id": sync_run_id,
        "checked_at": current,
    }
    await ensure_reconciliation_indexes(db)
    identity = {
        "user_id": str(user_id),
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": account_id,
        "report_date": report_date.isoformat(),
        "action_report_time": result["action_report_time"],
    }
    await db[SNAPCHAT_RECONCILIATION_COLLECTION].update_one(
        identity,
        {
            "$set": {**result, "updated_at": current},
            "$setOnInsert": {"created_at": current},
        },
        upsert=True,
    )
    return result


async def list_reconciliation(
    db: Any,
    *,
    user_id: str,
    ad_account_id: str,
    date_from: date,
    date_to: date,
    action_report_time: str = "conversion",
) -> list[dict[str, Any]]:
    cursor = db[SNAPCHAT_RECONCILIATION_COLLECTION].find(
        {
            "user_id": str(user_id),
            "provider": SNAPCHAT_PROVIDER,
            "ad_account_id": str(ad_account_id),
            "report_date": {
                "$gte": date_from.isoformat(),
                "$lte": date_to.isoformat(),
            },
            "action_report_time": clean_text(action_report_time, limit=32).lower(),
        },
        {"_id": 0},
    )
    if hasattr(cursor, "sort"):
        cursor = cursor.sort("report_date", 1)
    return await _to_list(cursor, 400)


__all__ = [
    "NATIVE_TOLERANCE",
    "SNAPCHAT_RECONCILIATION_COLLECTION",
    "calculate_cost_components",
    "list_reconciliation",
    "reconcile_day",
]
