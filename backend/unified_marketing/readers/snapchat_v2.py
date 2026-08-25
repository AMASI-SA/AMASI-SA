"""Read Snapchat V2 through the provider-neutral marketing contract."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from snapchat_v2.accounts import get_selected_account
from snapchat_v2.entities import list_entities
from snapchat_v2.projections import (
    RIYADH_TIMEZONE,
    SNAPCHAT_DAILY_PROJECTIONS_COLLECTION,
    list_daily_projections,
)
from snapchat_v2.reconciliation import (
    calculate_cost_components,
    list_reconciliation,
)
from snapchat_v2.salla_outcomes import load_salla_campaign_outcomes
from snapchat_v2.sync_runs import SNAPCHAT_SYNC_RUNS_COLLECTION
from snapchat_v2.routes import _add_sar_spend, _entity_performance_report
from unified_marketing.adapters.snapchat_v2 import build_snapchat_v2_unified_report
from unified_marketing.commerce_carts import load_abandoned_cart_outcomes

INT_FIELDS = (
    "impressions",
    "swipes",
    "video_views",
    "view_content",
    "add_to_cart",
    "start_checkout",
    "add_billing",
    "purchases",
)
FLOAT_FIELDS = (
    "base_spend_native",
    "view_completion",
    "purchase_value_native",
)


async def load_snapchat_v2_account_identity(
    db: Any,
    user_id: str,
) -> dict[str, Any] | None:
    account = await get_selected_account(db, str(user_id))
    if not account:
        return None
    return {
        "provider": "snapchat_ads",
        "id": str(account.get("ad_account_id") or ""),
        "name": account.get("display_name") or account.get("ad_account_id"),
        "currency": str(account.get("currency") or "").upper(),
        "timezone": str(account.get("timezone") or ""),
        "last_sync_at": account.get("last_sync_at"),
    }


def _sum(rows: list[dict[str, Any]], field: str) -> float:
    return round(sum(float(row.get(field) or 0) for row in rows), 6)


async def _projection_financial_status(
    db: Any,
    *,
    user_id: str,
    ad_account_id: str,
    projections: list[dict[str, Any]],
) -> str:
    """Prove the selected range from the runs that produced its facts.

    A newer rolling run can legitimately be partial while the current local
    day is open. It must not downgrade an already closed historical range.
    Daily projections retain immutable source run ids for this proof.
    """
    run_ids = sorted({
        str(run_id)
        for projection in projections
        for run_id in list(projection.get("source_sync_run_ids") or [])
        if run_id
    })
    if not projections or not run_ids:
        return "partial"
    cursor = db[SNAPCHAT_SYNC_RUNS_COLLECTION].find(
        {
            "user_id": str(user_id),
            "ad_account_id": str(ad_account_id),
            "sync_run_id": {"$in": run_ids},
        },
        {"_id": 0, "sync_run_id": 1, "financial_sync_status": 1},
    )
    try:
        rows = list(await cursor.to_list(length=len(run_ids)))
    except TypeError:
        rows = list(await cursor.to_list(len(run_ids)))
    complete_ids = {
        str(row.get("sync_run_id"))
        for row in rows
        if row.get("financial_sync_status") == "complete"
    }
    return "complete" if set(run_ids).issubset(complete_ids) else "partial"


def _reconciliation_status(
    reconciliations: list[dict[str, Any]],
    *,
    date_from: date,
    date_to: date,
) -> str:
    expected_days = (date_to - date_from).days + 1
    reconciled_dates = {
        str(row.get("report_date"))
        for row in reconciliations
        if row.get("reconciled") is True
    }
    return "reconciled" if len(reconciled_dates) == expected_days else "partial"


async def load_snapchat_v2_account_report(
    db: Any,
    user_id: str,
    *,
    date_from: date,
    date_to: date,
    timezone_name: str = RIYADH_TIMEZONE,
) -> dict[str, Any]:
    account = await get_selected_account(db, str(user_id))
    if not account:
        raise ValueError("unified_marketing_snapchat_selected_account_missing")
    account_id = str(account["ad_account_id"])
    projections = await list_daily_projections(
        db,
        user_id=str(user_id),
        ad_account_id=account_id,
        date_from=date_from,
        date_to=date_to,
        projection_timezone=timezone_name,
        action_report_time="conversion",
    )
    expected_days = (date_to - date_from).days + 1
    financial_status = await _projection_financial_status(
        db,
        user_id=str(user_id),
        ad_account_id=account_id,
        projections=projections,
    )
    reconciliations = await list_reconciliation(
        db,
        user_id=str(user_id),
        ad_account_id=account_id,
        date_from=date_from,
        date_to=date_to,
        action_report_time="conversion",
    )
    reconciliation_status = _reconciliation_status(
        reconciliations,
        date_from=date_from,
        date_to=date_to,
    )
    amount_complete = (
        len(projections) == expected_days
        and all(row.get("amount_complete") is True for row in projections)
    )
    sync_status = (
        "complete"
        if amount_complete
        and financial_status == "complete"
        and reconciliation_status == "reconciled"
        else "partial"
    )
    totals: dict[str, Any] = {
        "source_collection": SNAPCHAT_DAILY_PROJECTIONS_COLLECTION,
        "source_fact_count": sum(
            int(row.get("source_fact_count") or 0) for row in projections
        ),
        "performance_sync_status": sync_status,
        "amount_complete": amount_complete,
        "reconciliation_status": reconciliation_status,
        "performance_reason": (
            None
            if sync_status == "complete"
            else "riyadh_projection_financial_or_reconciliation_incomplete"
        ),
        "reach_frequency_scope": "exact_total_window_required",
    }
    for field in INT_FIELDS:
        totals[field] = int(_sum(projections, field))
    for field in FLOAT_FIELDS:
        totals[field] = _sum(projections, field)
    totals["spend_native"] = totals.pop("base_spend_native")
    totals["ctr_pct"] = (
        round((totals["swipes"] / totals["impressions"]) * 100, 6)
        if totals["impressions"] > 0
        else None
    )
    totals["roas"] = (
        round(totals["purchase_value_native"] / totals["spend_native"], 6)
        if totals["spend_native"] > 0
        else None
    )

    try:
        cost = await calculate_cost_components(
            db,
            user_id=str(user_id),
            account=account,
            spend_native=1.0,
        )
        exchange_rate = float(cost.get("exchange_rate_to_sar") or 0) or None
    except Exception:  # noqa: BLE001 - contract remains partial and read-only
        exchange_rate = None
    totals["exchange_rate_to_sar"] = exchange_rate
    totals["spend_sar"] = (
        round(totals["spend_native"] * exchange_rate, 2)
        if exchange_rate is not None and amount_complete
        else None
    )

    campaigns = await list_entities(
        db,
        user_id=str(user_id),
        ad_account_id=account_id,
        entity_type="campaign",
        active_only=False,
        limit=20_000,
    )
    identities = [
        {
            "account_id": account_id,
            "campaign_id": str(row.get("external_id") or ""),
            "campaign_name": row.get("name") or row.get("external_id"),
        }
        for row in campaigns
        if row.get("external_id")
    ]
    try:
        salla = await load_salla_campaign_outcomes(
            db,
            str(user_id),
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
            timezone_name=timezone_name,
            identities=identities,
            platform_purchases=int(totals["purchases"]),
        )
        salla_summary = dict(salla.get("summary") or {})
        salla_available = salla_summary.get("coverage_status") == "complete"
    except Exception as exc:  # noqa: BLE001
        salla_available = False
        salla = {"orders": [], "orders_total": 0, "orders_returned": 0, "truncated": False}
        salla_summary = {
            "coverage_status": "partial",
            "reason": str(type(exc).__name__)[:96],
            "platform_attributed_purchases": int(totals["purchases"]),
        }
    totals["salla_results"] = {
        "status": "complete" if salla_available else "partial",
        "orders": (
            int(salla_summary.get("campaign_matched_orders") or 0)
            if salla_available
            else None
        ),
        "sales_sar": (
            float(salla_summary.get("campaign_matched_financial_sales_sar") or 0)
            if salla_available
            else None
        ),
        "roas": None,
    }
    if (
        salla_available
        and totals["spend_sar"] is not None
        and totals["spend_sar"] > 0
    ):
        totals["salla_results"]["roas"] = round(
            totals["salla_results"]["sales_sar"] / totals["spend_sar"],
            6,
        )

    report = build_snapchat_v2_unified_report(
        account_value=account,
        period_value={
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "timezone": timezone_name,
            "action_report_time": "conversion",
        },
        entity_type="account",
        rows=[totals],
        totals=totals,
        sync_status=sync_status,
        orders=list(salla.get("orders") or []),
        order_summary={
            **salla_summary,
            "orders_total": int(salla.get("orders_total") or 0),
            "orders_returned": int(salla.get("orders_returned") or 0),
            "truncated": bool(salla.get("truncated")),
        },
    )
    report["decision_eligibility"] = {
        "eligible": False,
        "reason": "dashboard_shadow_not_accepted",
    }
    return report


def _management_context(
    identities: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Expose provider management metadata without leaking provider shape.

    The public contract remains provider neutral.  Budget/status metadata is
    carried beside the analytical rows because Decision Intelligence needs it
    to describe an existing entity, while all mutation code remains outside
    Unified Marketing.
    """
    output: dict[str, dict[str, Any]] = {}
    for identity in identities:
        entity_id = str(identity.get("external_id") or "").strip()
        if not entity_id:
            continue
        raw = identity.get("raw") if isinstance(identity.get("raw"), dict) else {}
        daily_micro = raw.get("daily_budget_micro")
        try:
            daily_budget_native = (
                round(float(daily_micro) / 1_000_000, 6)
                if daily_micro is not None
                else None
            )
        except (TypeError, ValueError, OverflowError):
            daily_budget_native = None
        output[entity_id] = {
            "status": identity.get("status"),
            "active": identity.get("active"),
            "campaign_id": identity.get("campaign_id"),
            "ad_group_id": identity.get("ad_squad_id"),
            "daily_budget_native": daily_budget_native,
            "currency_scope": "account_native",
            "updated_at": identity.get("updated_at"),
        }
    return output


async def load_snapchat_v2_entity_report(
    db: Any,
    user_id: str,
    *,
    entity_level: str,
    date_from: date,
    date_to: date,
    timezone_name: str,
    include_stale: bool = True,
) -> dict[str, Any]:
    """Read Campaign/Ad Group/Ad evidence through one V2 contract.

    This adapter is read-only: it uses persisted V2 identity/performance facts,
    exact campaign-level Salla attribution and abandoned-cart evidence.  Child
    rows intentionally never receive fabricated Salla outcomes.
    """
    provider_types = {
        "campaign": "campaign",
        "ad_group": "ad_squad",
        "ad": "ad",
    }
    provider_type = provider_types.get(str(entity_level or "").strip().lower())
    if provider_type is None:
        raise ValueError(f"unsupported_unified_marketing_entity_level:{entity_level}")
    account = await get_selected_account(db, str(user_id))
    if not account:
        raise ValueError("unified_marketing_snapchat_selected_account_missing")
    account_id = str(account["ad_account_id"])
    performance = await _entity_performance_report(
        db,
        user_id=str(user_id),
        account=account,
        date_from=date_from,
        date_to=date_to,
        timezone_name=timezone_name,
        action_report_time="conversion",
        entity_type=provider_type,
        include_stale=include_stale,
    )
    rows = list(performance.get("rows") or [])
    totals = dict(performance.get("totals") or {})
    _, cost_coverage = await _add_sar_spend(
        db,
        user_id=str(user_id),
        account=account,
        rows=rows,
        totals=totals,
    )
    identities = await list_entities(
        db,
        user_id=str(user_id),
        ad_account_id=account_id,
        entity_type=provider_type,
        active_only=False,
        limit=20_000,
    )

    orders: list[dict[str, Any]] = []
    order_summary: dict[str, Any] = {}
    if provider_type == "campaign":
        campaign_identities = [
            {
                "account_id": account_id,
                "campaign_id": str(row.get("campaign_id") or ""),
                "campaign_name": row.get("campaign_name"),
            }
            for row in rows
            if row.get("campaign_id")
        ]
        try:
            salla = await load_salla_campaign_outcomes(
                db,
                str(user_id),
                account_id=account_id,
                date_from=date_from,
                date_to=date_to,
                timezone_name=timezone_name,
                identities=campaign_identities,
                platform_purchases=int(totals.get("purchases") or 0),
                campaign_spend_sar={
                    str(row.get("campaign_id") or ""): float(
                        row.get("spend_sar") or 0
                    )
                    for row in rows
                    if row.get("campaign_id")
                },
            )
            salla_available = True
        except Exception as exc:  # noqa: BLE001 - fail closed, never invent Salla
            salla_available = False
            salla = {
                "by_campaign": {},
                "orders": [],
                "orders_total": 0,
                "orders_returned": 0,
                "truncated": False,
                "summary": {
                    "coverage_status": "partial",
                    "reason": str(type(exc).__name__)[:96],
                    "platform_attributed_purchases": int(
                        totals.get("purchases") or 0
                    ),
                },
            }
        try:
            carts = await load_abandoned_cart_outcomes(
                db,
                str(user_id),
                provider="snapchat_ads",
                campaign_ids=[
                    str(value.get("campaign_id") or "")
                    for value in campaign_identities
                    if value.get("campaign_id")
                ],
                date_from=date_from,
                date_to=date_to,
            )
        except Exception as exc:  # noqa: BLE001
            carts = {
                "by_campaign": {},
                "coverage": {
                    "status": "partial",
                    "reason": str(type(exc).__name__)[:96],
                    "read_only": True,
                },
            }
        for row in rows:
            campaign_id = str(row.get("campaign_id") or "")
            if salla_available:
                salla_result = {
                    **dict(
                        (salla.get("by_campaign") or {}).get(
                            campaign_id,
                            {"orders": 0, "sales_sar": 0.0},
                        )
                    ),
                    "status": "complete",
                }
            else:
                salla_result = {
                    "status": "partial",
                    "orders": None,
                    "sales_sar": None,
                    "roas": None,
                }
            spend_sar = row.get("spend_sar")
            salla_result["abandoned_carts"] = (
                carts.get("by_campaign") or {}
            ).get(campaign_id)
            salla_result["roas"] = (
                round(float(salla_result.get("sales_sar") or 0) / spend_sar, 6)
                if salla_available and spend_sar and spend_sar > 0
                else None
            )
            row["salla_results"] = salla_result
        orders = list(salla.get("orders") or [])
        order_summary = {
            **dict(salla.get("summary") or {}),
            "orders_total": int(salla.get("orders_total") or 0),
            "orders_returned": int(salla.get("orders_returned") or 0),
            "truncated": bool(salla.get("truncated")),
        }

    report = build_snapchat_v2_unified_report(
        account_value=account,
        period_value={
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "timezone": timezone_name,
            "action_report_time": "conversion",
        },
        entity_type=provider_type,
        rows=rows,
        totals=totals,
        sync_status=str(performance.get("performance_sync_status") or "partial"),
        orders=orders,
        order_summary=order_summary,
    )
    report["management_context"] = _management_context(identities)
    report["cost_coverage"] = cost_coverage
    report["decision_eligibility"] = {
        "eligible": False,
        "reason": "ai_shadow_not_accepted",
    }
    return report


def _dashboard_bank_commissions(
    account: dict[str, Any],
    cost: dict[str, Any],
    *,
    spend_native: float,
) -> dict[str, Any]:
    base_spend_sar = round(float(cost.get("base_spend_sar") or 0), 2)
    commission_sar = round(float(cost.get("commission_sar") or 0), 2)
    applies = cost.get("apply_bank_commission") is True
    account_row = {
        "provider": "snapchat_ads",
        "provider_label": "Snapchat",
        "external_account_id": str(account.get("ad_account_id") or ""),
        "mezan_integration_account_id": account.get(
            "mezan_integration_account_id"
        ),
        "display_name": account.get("display_name")
        or account.get("ad_account_id"),
        "native_currency": cost.get("native_currency")
        or account.get("currency"),
        "exchange_rate_to_sar": cost.get("exchange_rate_to_sar"),
        "spend_native": round(float(spend_native), 6),
        "spend_sar": base_spend_sar,
        "bank_commission_pct": cost.get("bank_commission_pct"),
        "apply_bank_commission": applies,
        "configured": cost.get("cost_setting_configured") is True,
        "native_spend_complete": True,
        "source_rows": 1,
        "bank_commission_fee_sar": commission_sar,
        "source_mode": "mezan2_ad_account_cost_settings_v1",
    }
    return {
        "accounts": [account_row],
        "total_fee_sar": commission_sar,
        "fee_subject_spend_sar": base_spend_sar if applies else 0.0,
        "total_effective_spend_sar": base_spend_sar,
        "coverage": {
            **dict(cost.get("cost_coverage") or {}),
            "complete": True,
            "legacy_ads_currency_settings_read": False,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        },
    }


async def load_snapchat_v2_dashboard_spend(
    db: Any,
    user_id: str,
    *,
    date_from: date,
    date_to: date,
    timezone_name: str = RIYADH_TIMEZONE,
) -> dict[str, Any]:
    """Return a provider-neutral Dashboard snapshot from Snapchat V2 facts.

    This is a read-only projection adapter. It never calls Snapchat and never
    reaches campaign, Salla, accounting, or Qoyod write paths.
    """
    account = await get_selected_account(db, str(user_id))
    if not account:
        integration = await db["mezan_integrations_v2"].find_one(
            {"user_id": str(user_id), "provider": "snapchat_ads"},
            {"_id": 1},
        )
        disconnected = not integration
        days = [
            (date_from + timedelta(days=offset)).isoformat()
            for offset in range((date_to - date_from).days + 1)
        ]
        return {
            "rows": [],
            "daily_sar": {day: (0.0 if disconnected else None) for day in days},
            "daily_state": {
                day: ("not_connected" if disconnected else "unknown_incomplete")
                for day in days
            },
            "hourly_sar": {day: [] for day in days},
            "total_sar": 0.0 if disconnected else None,
            "bank_commissions": (
                {
                    "accounts": [],
                    "total_fee_sar": 0.0,
                    "fee_subject_spend_sar": 0.0,
                    "total_effective_spend_sar": 0.0,
                    "coverage": {"complete": True},
                }
                if disconnected
                else None
            ),
            "quality": {
                "status": "complete" if disconnected else "incomplete",
                "data_state": "not_connected" if disconnected else "unknown_incomplete",
                "coverage_complete": disconnected,
                "amount_complete": disconnected,
                "complete": disconnected,
                "connected": not disconnected,
                "reason_codes": [] if disconnected else ["selected_account_missing"],
                "timezone": timezone_name,
                "source_collection": SNAPCHAT_DAILY_PROJECTIONS_COLLECTION,
            },
            "source_contract": "unified-marketing-data-v1:dashboard-spend",
            "contract_version": "unified-marketing-data-v1",
            "source_only": True,
            "provider_write_reached": False,
            "campaign_write_reached": False,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }
    account_id = str(account["ad_account_id"])
    projections = await list_daily_projections(
        db,
        user_id=str(user_id),
        ad_account_id=account_id,
        date_from=date_from,
        date_to=date_to,
        projection_timezone=timezone_name,
        action_report_time="conversion",
    )
    expected_days = (date_to - date_from).days + 1
    financial_status = await _projection_financial_status(
        db,
        user_id=str(user_id),
        ad_account_id=account_id,
        projections=projections,
    )
    reconciliations = await list_reconciliation(
        db,
        user_id=str(user_id),
        ad_account_id=account_id,
        date_from=date_from,
        date_to=date_to,
        action_report_time="conversion",
    )
    reconciliation_status = _reconciliation_status(
        reconciliations,
        date_from=date_from,
        date_to=date_to,
    )
    projection_complete = (
        len(projections) == expected_days
        and all(row.get("amount_complete") is True for row in projections)
    )
    total_native = _sum(projections, "base_spend_native")
    try:
        cost = await calculate_cost_components(
            db,
            user_id=str(user_id),
            account=account,
            spend_native=total_native,
        )
        exchange_rate = float(cost.get("exchange_rate_to_sar") or 0) or None
    except Exception:  # noqa: BLE001 - fail closed on unresolved FX
        cost = {}
        exchange_rate = None
    amount_complete = bool(
        projection_complete
        and financial_status == "complete"
        and reconciliation_status == "reconciled"
        and exchange_rate is not None
    )

    rows: list[dict[str, Any]] = []
    daily_sar: dict[str, float | None] = {}
    daily_state: dict[str, str] = {}
    hourly_sar: dict[str, list[dict[str, Any]]] = {}
    for projection in projections:
        day = str(projection.get("report_date") or "")
        spend_native = float(projection.get("base_spend_native") or 0)
        day_known = (
            projection.get("amount_complete") is True
            and exchange_rate is not None
        )
        spend_sar = round(spend_native * exchange_rate, 2) if day_known else None
        state = (
            str(projection.get("data_state") or "unknown_incomplete")
            if day_known
            else "unknown_incomplete"
        )
        daily_sar[day] = spend_sar
        daily_state[day] = state
        day_hours: list[dict[str, Any]] = []
        for hour in list(projection.get("hours") or []):
            native_value = hour.get("spend_native")
            hour_spend_sar = (
                round(float(native_value) * exchange_rate, 2)
                if native_value is not None and exchange_rate is not None
                else None
            )
            day_hours.append({
                "date": day,
                "hour_index": int(hour.get("sequence") or 0),
                "hour": str(hour.get("local_hour") or ""),
                "spend_sar": hour_spend_sar,
                "status": hour.get("status"),
            })
        hourly_sar[day] = day_hours
        rows.append({
            "provider": "snapchat_ads",
            "ad_account_id": account_id,
            "date": day,
            "currency": account.get("currency"),
            "currency_native": account.get("currency"),
            "account_timezone": account.get("timezone"),
            "spend_native": round(spend_native, 6),
            "spend_sar": spend_sar,
            "effective_spend_sar": spend_sar,
            "effective_exchange_rate_to_sar": exchange_rate,
            "effective_native_currency": account.get("currency"),
            "effective_spend_source": "native_spend_x_account_rate",
            "impressions": int(projection.get("impressions") or 0),
            "clicks": int(projection.get("swipes") or 0),
            "purchases": int(projection.get("purchases") or 0),
            "purchase_value_sar": (
                round(
                    float(projection.get("purchase_value_native") or 0)
                    * exchange_rate,
                    2,
                )
                if exchange_rate is not None
                else None
            ),
            "updated_at": projection.get("updated_at")
            or projection.get("generated_at"),
        })

    total_sar = (
        round(float(cost.get("base_spend_sar") or 0), 2)
        if amount_complete
        else None
    )
    data_state = (
        "confirmed_data"
        if total_sar is not None and total_sar > 0
        else "confirmed_zero"
        if total_sar == 0
        else "unknown_incomplete"
    )
    reasons = [] if amount_complete else [
        "unified_projection_financial_reconciliation_or_fx_incomplete"
    ]
    return {
        "rows": rows if amount_complete else [],
        "daily_sar": daily_sar,
        "daily_state": daily_state,
        "hourly_sar": hourly_sar,
        "total_sar": total_sar,
        "bank_commissions": (
            _dashboard_bank_commissions(
                account,
                cost,
                spend_native=total_native,
            )
            if amount_complete
            else None
        ),
        "quality": {
            "status": "complete" if amount_complete else "incomplete",
            "data_state": data_state,
            "coverage_complete": amount_complete,
            "amount_complete": amount_complete,
            "complete": amount_complete,
            "connected": True,
            "reason_codes": reasons,
            "timezone": timezone_name,
            "source_collection": SNAPCHAT_DAILY_PROJECTIONS_COLLECTION,
            "amount_field": "base_spend_native",
            "fx_authority": "mezan_ad_account_cost_settings_v2",
            "reconciliation_status": reconciliation_status,
            "financial_status": financial_status,
            "source_sync_run_ids": sorted({
                str(run_id)
                for projection in projections
                for run_id in list(projection.get("source_sync_run_ids") or [])
                if run_id
            }),
        },
        "source_contract": "unified-marketing-data-v1:dashboard-spend",
        "contract_version": "unified-marketing-data-v1",
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


__all__ = [
    "load_snapchat_v2_account_report",
    "load_snapchat_v2_dashboard_spend",
]
