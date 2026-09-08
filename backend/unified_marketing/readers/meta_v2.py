"""Read persisted native Meta V2 evidence through Unified Marketing.

This module deliberately has no HTTP/provider client and no database write
method. Native ingestion owns provider reads and projection writes; every
Unified read below fails closed when stored evidence is incomplete.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from snapchat_v2.salla_outcomes import load_salla_campaign_outcomes
from unified_marketing.adapters.meta_v2 import (
    adapt_meta_v2_row,
    build_meta_v2_unified_report,
)
from unified_marketing.contract import UnifiedMarketingDailySeries

META_ACCOUNT_COLLECTION = "mezan_integration_accounts_v2"
META_REPORTING_COLLECTION = "mezan_meta_performance_daily_v2"
META_CAMPAIGN_REPORTING_COLLECTION = "mezan_meta_campaign_performance_daily_v2"
META_ENTITY_SNAPSHOT_COLLECTION = "mezan_meta_entity_snapshots_v2"
META_ENTITY_FACT_COLLECTION = "mezan_meta_entity_performance_daily_v2"
META_ENTITY_COVERAGE_COLLECTION = "mezan_meta_entity_coverage_daily_v2"
MAX_ENTITY_ROWS = 20_000

PROVIDER_TYPES = {"campaign": "campaign", "ad_group": "adset", "ad": "ad"}
METRIC_FIELDS = (
    "spend_native",
    "spend_sar",
    "impressions",
    "clicks",
    "purchases",
    "purchase_value_native",
)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _expected_dates(date_from: date, date_to: date) -> list[str]:
    days = (date_to - date_from).days + 1
    if days < 1 or days > 31:
        raise ValueError("unified_marketing_meta_date_range_invalid")
    return [(date_from + timedelta(days=offset)).isoformat() for offset in range(days)]


async def _rows(cursor: Any, *, limit: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(limit + 1)
    if hasattr(cursor, "to_list"):
        try:
            values = list(await cursor.to_list(length=limit + 1))
        except TypeError:
            values = list(await cursor.to_list(limit + 1))
    else:
        values = []
        async for row in cursor:
            values.append(row)
            if len(values) > limit:
                break
    if len(values) > limit:
        raise ValueError("unified_marketing_meta_source_row_limit_reached")
    return values


async def _selected_accounts(db: Any, user_id: str) -> list[dict[str, Any]]:
    cursor = db[META_ACCOUNT_COLLECTION].find(
        {
            "user_id": str(user_id),
            "provider": "meta_ads",
            "mezan_selected": True,
            "connection_status": "connected",
        },
        {"_id": 0},
    )
    if hasattr(cursor, "sort"):
        cursor = cursor.sort([("external_account_id", 1)])
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(2)
    if hasattr(cursor, "to_list"):
        try:
            return list(await cursor.to_list(length=2))
        except TypeError:
            return list(await cursor.to_list(2))
    rows: list[dict[str, Any]] = []
    async for row in cursor:
        rows.append(row)
        if len(rows) == 2:
            break
    return rows


async def _account(db: Any, user_id: str) -> dict[str, Any]:
    accounts = await _selected_accounts(db, user_id)
    if not accounts:
        raise ValueError("unified_marketing_meta_selected_account_missing")
    if len(accounts) != 1:
        raise ValueError("unified_marketing_meta_selected_account_ambiguous")
    row = dict(accounts[0])
    account_id = str(
        row.get("ad_account_id") or row.get("external_account_id") or ""
    ).strip()
    if not account_id:
        raise ValueError("unified_marketing_meta_account_identity_missing")
    if not account_id.startswith("act_"):
        account_id = f"act_{account_id}"
    timezone_name = str(row.get("timezone") or "").strip()
    currency = str(row.get("currency") or "").strip().upper()
    if not timezone_name or not currency:
        raise ValueError("unified_marketing_meta_account_context_missing")
    return {
        **row,
        "ad_account_id": account_id,
        "external_account_id": account_id,
        "currency": currency,
        "timezone": timezone_name,
        "active": str(row.get("account_status") or "1") == "1",
    }


async def load_meta_v2_account_identity(db: Any, user_id: str) -> dict[str, Any] | None:
    try:
        account = await _account(db, str(user_id))
    except ValueError as exc:
        if str(exc) == "unified_marketing_meta_selected_account_missing":
            return None
        raise
    return {
        "provider": "meta_ads",
        "id": account["ad_account_id"],
        "name": account.get("display_name") or account["ad_account_id"],
        "currency": account["currency"],
        "timezone": account["timezone"],
        "last_sync_at": account.get("last_sync_at") or account.get("last_observed_at"),
        "freshness_source": META_ACCOUNT_COLLECTION,
    }


async def _snapshots(
    db: Any,
    user_id: str,
    account_id: str,
    provider_type: str | None = None,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {
        "user_id": str(user_id),
        "provider": "meta_ads",
        "ad_account_id": account_id,
    }
    if provider_type:
        query["entity_type"] = provider_type
    cursor = db[META_ENTITY_SNAPSHOT_COLLECTION].find(query, {"_id": 0})
    if hasattr(cursor, "sort"):
        cursor = cursor.sort([("entity_type", 1), ("external_id", 1)])
    return await _rows(cursor, limit=MAX_ENTITY_ROWS)


async def load_meta_v2_entity_metadata(
    db: Any,
    user_id: str,
    *,
    entity_level: str,
    entity_id: str,
) -> dict[str, Any]:
    provider_type = PROVIDER_TYPES.get(str(entity_level or "").strip().lower())
    if provider_type is None:
        raise ValueError(f"unsupported_unified_marketing_entity_level:{entity_level}")
    account = await _account(db, str(user_id))
    row = (
        await db[META_ENTITY_SNAPSHOT_COLLECTION].find_one(
            {
                "user_id": str(user_id),
                "provider": "meta_ads",
                "ad_account_id": account["ad_account_id"],
                "entity_type": provider_type,
                "external_id": str(entity_id),
            },
            {"_id": 0},
        )
        or {}
    )
    return {
        "provider": "meta_ads",
        "account_id": account["ad_account_id"],
        "entity_level": str(entity_level),
        "entity_id": str(entity_id),
        "creative_id": row.get("creative_id"),
        "creative_type": row.get("creative_type"),
        "media_id": row.get("media_id"),
        "destination_url": row.get("destination_url"),
        "created_at": row.get("created_time"),
        "updated_at": row.get("updated_time") or row.get("observed_at"),
        "status": row.get("effective_status") or row.get("status"),
        "quality": {
            "status": "complete" if row else "unavailable",
            "source": META_ENTITY_SNAPSHOT_COLLECTION,
            "read_only": True,
        },
    }


async def _find_range(
    db: Any,
    collection: str,
    query: dict[str, Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    cursor = db[collection].find(query, {"_id": 0})
    if hasattr(cursor, "sort"):
        cursor = cursor.sort([("date", 1), ("external_id", 1)])
    return await _rows(cursor, limit=limit)


async def _account_facts(
    db: Any, user_id: str, account_id: str, dates: list[str]
) -> list[dict[str, Any]]:
    return await _find_range(
        db,
        META_REPORTING_COLLECTION,
        {
            "user_id": str(user_id),
            "provider": "meta_ads",
            "ad_account_id": account_id,
            "date": {"$gte": dates[0], "$lte": dates[-1]},
        },
        limit=len(dates) + 1,
    )


async def _entity_facts(
    db: Any,
    user_id: str,
    account_id: str,
    provider_type: str,
    dates: list[str],
) -> list[dict[str, Any]]:
    collection = (
        META_CAMPAIGN_REPORTING_COLLECTION
        if provider_type == "campaign"
        else META_ENTITY_FACT_COLLECTION
    )
    query: dict[str, Any] = {
        "user_id": str(user_id),
        "provider": "meta_ads",
        "ad_account_id": account_id,
        "date": {"$gte": dates[0], "$lte": dates[-1]},
    }
    if provider_type != "campaign":
        query["entity_type"] = provider_type
    return await _find_range(db, collection, query, limit=MAX_ENTITY_ROWS)


async def _coverage(
    db: Any,
    user_id: str,
    account_id: str,
    provider_type: str,
    dates: list[str],
) -> list[dict[str, Any]]:
    return await _find_range(
        db,
        META_ENTITY_COVERAGE_COLLECTION,
        {
            "user_id": str(user_id),
            "provider": "meta_ads",
            "ad_account_id": account_id,
            "entity_type": provider_type,
            "date": {"$gte": dates[0], "$lte": dates[-1]},
        },
        limit=len(dates) + 1,
    )


def _fact_entity_id(row: dict[str, Any], provider_type: str) -> str:
    return str(
        row.get("external_id")
        or row.get(f"{provider_type}_id")
        or (row.get("campaign_id") if provider_type == "campaign" else "")
        or ""
    ).strip()


def _sum(rows: list[dict[str, Any]], field: str) -> float:
    return round(sum(_number(row.get(field)) or 0.0 for row in rows), 6)


def _coverage_complete(coverage: list[dict[str, Any]], dates: list[str]) -> bool:
    by_date = {str(row.get("date")): row for row in coverage}
    return bool(
        len(coverage) == len(by_date) == len(dates)
        and set(by_date) == set(dates)
        and all(
            row.get("status") == "complete"
            and row.get("amount_complete") is True
            and row.get("source_only") is True
            and row.get("attribution_mode") == "account_setting+unified"
            for row in by_date.values()
        )
    )


def _facts_complete(
    facts: list[dict[str, Any]], account: dict[str, Any], dates: list[str]
) -> bool:
    expected_currency = str(account.get("currency") or "").upper()
    return all(
        str(row.get("date")) in dates
        and str(row.get("date_start"))
        == str(row.get("date"))
        == str(row.get("date_stop"))
        and row.get("source_only") is True
        and row.get("attribution_mode") == "account_setting+unified"
        and str(row.get("currency_native") or "").upper() == expected_currency
        for row in facts
    )


def _settings_row(snapshot: dict[str, Any]) -> dict[str, Any]:
    provider_type = str(snapshot.get("entity_type") or "")
    external_id = str(snapshot.get("external_id") or "")
    return {
        **snapshot,
        "external_id": external_id,
        "name": snapshot.get("name") or external_id,
        "campaign_id": (
            external_id if provider_type == "campaign" else snapshot.get("campaign_id")
        ),
        "ad_group_id": (
            external_id
            if provider_type == "adset"
            else snapshot.get("ad_group_id") or snapshot.get("adset_id")
        ),
        "active": str(
            snapshot.get("effective_status") or snapshot.get("status") or ""
        ).upper()
        == "ACTIVE",
    }


async def _level_native(
    db: Any,
    user_id: str,
    account: dict[str, Any],
    *,
    provider_type: str,
    dates: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    account_id = account["ad_account_id"]
    snapshots = [
        _settings_row(row)
        for row in await _snapshots(db, user_id, account_id, provider_type)
    ]
    facts = await _entity_facts(db, user_id, account_id, provider_type, dates)
    coverage = await _coverage(db, user_id, account_id, provider_type, dates)
    complete = bool(
        _coverage_complete(coverage, dates)
        and snapshots
        and all(row.get("source_only") is True for row in snapshots)
        and _facts_complete(facts, account, dates)
    )
    facts_by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        entity_id = _fact_entity_id(fact, provider_type)
        if entity_id:
            facts_by_entity[entity_id].append(fact)
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        entity_facts = facts_by_entity.get(str(snapshot["external_id"]), [])
        amount_complete = bool(
            complete and all(fact.get("spend_sar") is not None for fact in entity_facts)
        )
        rows.append(
            {
                **snapshot,
                **{field: _sum(entity_facts, field) for field in METRIC_FIELDS},
                "source_fact_count": len(dates) if complete else len(entity_facts),
                "performance_sync_status": (
                    "complete" if complete and amount_complete else "partial"
                ),
                "amount_complete": amount_complete,
                "reconciliation_status": "pending",
                "source_collection": (
                    META_CAMPAIGN_REPORTING_COLLECTION
                    if provider_type == "campaign"
                    else META_ENTITY_FACT_COLLECTION
                ),
                "observed_dates": sorted(
                    str(row.get("date"))
                    for row in coverage
                    if row.get("status") == "complete"
                ),
                "expected_dates": dates,
            }
        )
    totals = {
        field: round(sum(_number(row.get(field)) or 0.0 for row in rows), 6)
        for field in METRIC_FIELDS
    }
    totals.update(
        {
            "source_fact_count": sum(
                int(row.get("source_fact_count") or 0) for row in rows
            ),
            "performance_sync_status": (
                "complete"
                if complete and all(row.get("amount_complete") for row in rows)
                else "partial"
            ),
            "amount_complete": complete
            and all(row.get("amount_complete") for row in rows),
            "reconciliation_status": "pending",
            "source_collection": (
                rows[0].get("source_collection")
                if rows
                else META_ENTITY_FACT_COLLECTION
            ),
            "observed_dates": sorted(
                str(row.get("date"))
                for row in coverage
                if row.get("status") == "complete"
            ),
            "expected_dates": dates,
        }
    )
    return rows, totals


def _metric_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(
        abs((_number(left.get(field)) or 0.0) - (_number(right.get(field)) or 0.0))
        <= 0.01
        for field in METRIC_FIELDS
    )


async def _account_native(
    db: Any, user_id: str, account: dict[str, Any], dates: list[str]
) -> dict[str, Any]:
    facts = await _account_facts(db, user_id, account["ad_account_id"], dates)
    by_date = {str(row.get("date")): row for row in facts}
    complete = bool(
        len(facts) == len(by_date) == len(dates)
        and set(by_date) == set(dates)
        and all(
            row.get("spend_sar") is not None
            and row.get("source_only") is True
            and row.get("attribution_mode") == "account_setting+unified"
            and str(row.get("account_timezone")) == account["timezone"]
            and str(row.get("currency_native") or "").upper() == account["currency"]
            and str(row.get("date_start"))
            == str(row.get("date"))
            == str(row.get("date_stop"))
            for row in facts
        )
    )
    totals = {field: _sum(facts, field) for field in METRIC_FIELDS}
    totals.update(
        {
            "source_fact_count": len(facts),
            "performance_sync_status": "complete" if complete else "partial",
            "amount_complete": complete,
            "reconciliation_status": "reconciled" if complete else "partial",
            "source_collection": META_REPORTING_COLLECTION,
            "observed_dates": sorted(by_date),
            "expected_dates": dates,
            "external_id": account["ad_account_id"],
            "settings_fields_present": ["account_status"],
            "status": account.get("account_status"),
            "active": account.get("active"),
        }
    )
    return totals


async def _salla(
    db: Any,
    user_id: str,
    account: dict[str, Any],
    date_from: date,
    date_to: date,
    campaign_rows: list[dict[str, Any]],
    platform_purchases: int,
) -> dict[str, Any]:
    identities = [
        {
            "account_id": account["ad_account_id"],
            "campaign_id": row.get("external_id"),
            "campaign_name": row.get("name"),
        }
        for row in campaign_rows
        if row.get("external_id")
    ]
    return await load_salla_campaign_outcomes(
        db,
        str(user_id),
        account_id=account["ad_account_id"],
        date_from=date_from,
        date_to=date_to,
        timezone_name=account["timezone"],
        identities=identities,
        platform_purchases=platform_purchases,
        campaign_spend_sar={
            str(row.get("external_id")): _number(row.get("spend_sar")) or 0.0
            for row in campaign_rows
        },
        provider="meta_ads",
    )


def _period(date_from: date, date_to: date, timezone_name: str) -> dict[str, Any]:
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "timezone": timezone_name,
        "action_report_time": "conversion",
    }


def _attach_salla(
    rows: list[dict[str, Any]], salla: dict[str, Any], available: bool
) -> None:
    for row in rows:
        match = dict(
            (salla.get("by_campaign") or {}).get(str(row.get("external_id")), {})
        )
        match.update(
            {
                "status": "complete" if available else "partial",
                "attribution_scope": "exact_campaign_match",
                "roas": (
                    round(
                        float(match.get("sales_sar") or 0)
                        / float(row.get("spend_sar") or 0),
                        6,
                    )
                    if available and float(row.get("spend_sar") or 0) > 0
                    else None
                ),
            }
        )
        row["salla_results"] = match


async def load_meta_v2_account_report(
    db: Any,
    user_id: str,
    *,
    date_from: date,
    date_to: date,
    timezone_name: str,
) -> dict[str, Any]:
    account = await _account(db, str(user_id))
    if timezone_name != account["timezone"]:
        raise ValueError("unified_marketing_meta_requires_account_timezone")
    dates = _expected_dates(date_from, date_to)
    totals = await _account_native(db, str(user_id), account, dates)
    campaign_rows, campaign_totals = await _level_native(
        db, str(user_id), account, provider_type="campaign", dates=dates
    )
    totals["reconciliation_status"] = (
        "reconciled"
        if totals.get("amount_complete")
        and campaign_totals.get("amount_complete")
        and campaign_totals.get("performance_sync_status") == "complete"
        and _metric_equal(totals, campaign_totals)
        else "partial"
    )
    try:
        salla = await _salla(
            db,
            str(user_id),
            account,
            date_from,
            date_to,
            campaign_rows,
            int(totals.get("purchases") or 0),
        )
        summary = dict(salla.get("summary") or {})
        available = summary.get("coverage_status") == "complete"
    except Exception as exc:
        salla = {
            "by_campaign": {},
            "orders": [],
            "orders_total": 0,
            "orders_returned": 0,
            "truncated": False,
        }
        summary = {"coverage_status": "partial", "reason": type(exc).__name__}
        available = False
    _attach_salla(campaign_rows, salla, available)
    totals["salla_results"] = {
        "status": "complete" if available else "partial",
        "orders": summary.get("campaign_matched_orders") if available else None,
        "sales_sar": (
            summary.get("campaign_matched_financial_sales_sar") if available else None
        ),
        "roas": (
            round(
                float(summary.get("campaign_matched_financial_sales_sar") or 0)
                / float(totals.get("spend_sar") or 0),
                6,
            )
            if available and float(totals.get("spend_sar") or 0) > 0
            else None
        ),
        "attribution_scope": "account_sum_of_exact_campaign_matches",
    }
    campaign_report = build_meta_v2_unified_report(
        account_value=account,
        period_value=_period(date_from, date_to, timezone_name),
        entity_type="campaign",
        rows=campaign_rows,
        totals=campaign_totals,
        sync_status=str(campaign_totals.get("performance_sync_status")),
    )
    base = build_meta_v2_unified_report(
        account_value=account,
        period_value=_period(date_from, date_to, timezone_name),
        entity_type="ad_account",
        rows=[totals],
        totals=totals,
        sync_status=str(totals.get("performance_sync_status")),
        orders=list(salla.get("orders") or []),
        order_summary={
            **summary,
            "orders_total": int(salla.get("orders_total") or 0),
            "orders_returned": int(salla.get("orders_returned") or 0),
            "truncated": bool(salla.get("truncated")),
        },
    )
    from unified_marketing.readers.snapchat_v2_decision_evidence import (
        _derive_account_profitability,
    )

    profitability = _derive_account_profitability(base, campaign_report)
    if profitability is not None:
        base["totals"]["commerce_profitability"] = profitability
    return base


async def load_meta_v2_entity_report(
    db: Any,
    user_id: str,
    *,
    entity_level: str,
    date_from: date,
    date_to: date,
    timezone_name: str,
    include_stale: bool = True,
) -> dict[str, Any]:
    del include_stale
    provider_type = PROVIDER_TYPES.get(str(entity_level or "").strip().lower())
    if provider_type is None:
        raise ValueError(f"unsupported_unified_marketing_entity_level:{entity_level}")
    account = await _account(db, str(user_id))
    if timezone_name != account["timezone"]:
        raise ValueError("unified_marketing_meta_requires_account_timezone")
    dates = _expected_dates(date_from, date_to)
    rows, totals = await _level_native(
        db, str(user_id), account, provider_type=provider_type, dates=dates
    )
    account_totals = await _account_native(db, str(user_id), account, dates)
    reconciled = bool(
        totals.get("performance_sync_status") == "complete"
        and totals.get("amount_complete")
        and account_totals.get("amount_complete")
        and _metric_equal(totals, account_totals)
    )
    totals["reconciliation_status"] = "reconciled" if reconciled else "partial"
    for row in rows:
        row["reconciliation_status"] = totals["reconciliation_status"]
    orders: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    if provider_type == "campaign":
        try:
            salla = await _salla(
                db,
                str(user_id),
                account,
                date_from,
                date_to,
                rows,
                int(account_totals.get("purchases") or 0),
            )
            summary = dict(salla.get("summary") or {})
            available = summary.get("coverage_status") == "complete"
        except Exception as exc:
            salla = {
                "by_campaign": {},
                "orders": [],
                "orders_total": 0,
                "orders_returned": 0,
                "truncated": False,
            }
            summary = {"coverage_status": "partial", "reason": type(exc).__name__}
            available = False
        _attach_salla(rows, salla, available)
        orders = list(salla.get("orders") or [])
        summary.update(
            {
                "orders_total": int(salla.get("orders_total") or 0),
                "orders_returned": int(salla.get("orders_returned") or 0),
                "truncated": bool(salla.get("truncated")),
            }
        )
    return build_meta_v2_unified_report(
        account_value=account,
        period_value=_period(date_from, date_to, timezone_name),
        entity_type=provider_type,
        rows=rows,
        totals=totals,
        sync_status=str(totals.get("performance_sync_status")),
        orders=orders,
        order_summary=summary,
    )


async def load_meta_v2_entity_readiness_evidence(
    db: Any,
    user_id: str,
    *,
    entity_level: str,
    date_from: date,
    date_to: date,
    timezone_name: str,
) -> dict[str, Any]:
    report = await load_meta_v2_entity_report(
        db,
        user_id,
        entity_level=entity_level,
        date_from=date_from,
        date_to=date_to,
        timezone_name=timezone_name,
        include_stale=False,
    )
    quality = dict((report.get("totals") or {}).get("quality") or {})
    complete = bool(
        report.get("rows")
        and quality.get("sync_status") == "complete"
        and quality.get("coverage_status") == "complete"
        and quality.get("reconciliation_status") == "reconciled"
    )
    return {
        "contract_version": report.get("contract_version"),
        "provider": "meta_ads",
        "entity_level": entity_level,
        "contract_valid": True,
        "complete": complete,
        "row_count": len(report.get("rows") or []),
        "source_fact_count": int(quality.get("source_fact_count") or 0),
        "sync_status": quality.get("sync_status"),
        "coverage_status": quality.get("coverage_status"),
        "decision_eligibility": {
            "eligible": False,
            "reason": "meta_shadow_not_accepted",
        },
    }


async def load_meta_v2_entity_daily_series(
    db: Any,
    user_id: str,
    *,
    entity_level: str,
    entity_ids: list[str],
    date_from: date,
    date_to: date,
    timezone_name: str,
) -> dict[str, Any]:
    provider_type = PROVIDER_TYPES.get(str(entity_level or "").strip().lower())
    if provider_type is None:
        raise ValueError(f"unsupported_unified_marketing_entity_level:{entity_level}")
    ids = sorted({str(item).strip() for item in entity_ids if str(item).strip()})
    if not ids or len(ids) > 500:
        raise ValueError("unified_marketing_meta_entity_ids_invalid")
    account = await _account(db, str(user_id))
    if timezone_name != account["timezone"]:
        raise ValueError("unified_marketing_meta_requires_account_timezone")
    dates = _expected_dates(date_from, date_to)
    snapshots = {
        str(row.get("external_id")): _settings_row(row)
        for row in await _snapshots(
            db, str(user_id), account["ad_account_id"], provider_type
        )
        if str(row.get("external_id")) in ids
    }
    facts = await _entity_facts(
        db, str(user_id), account["ad_account_id"], provider_type, dates
    )
    coverage = await _coverage(
        db, str(user_id), account["ad_account_id"], provider_type, dates
    )
    coverage_dates = {
        str(row.get("date"))
        for row in coverage
        if row.get("status") == "complete" and row.get("amount_complete") is True
    }
    by_key = {
        (str(row.get("date")), _fact_entity_id(row, provider_type)): row
        for row in facts
    }
    output = []
    for day in dates:
        for entity_id in ids:
            snapshot = snapshots.get(entity_id)
            if not snapshot:
                continue
            fact = dict(by_key.get((day, entity_id)) or {})
            complete = day in coverage_dates and (
                not fact or fact.get("spend_sar") is not None
            )
            row = {
                **snapshot,
                **{field: _number(fact.get(field)) or 0.0 for field in METRIC_FIELDS},
                "source_fact_count": 1 if complete else 0,
                "performance_sync_status": "complete" if complete else "partial",
                "amount_complete": complete,
                "reconciliation_status": "pending",
                "source_collection": (
                    META_CAMPAIGN_REPORTING_COLLECTION
                    if provider_type == "campaign"
                    else META_ENTITY_FACT_COLLECTION
                ),
            }
            output.append(
                adapt_meta_v2_row(
                    row,
                    account_value=account,
                    period_value=_period(
                        date.fromisoformat(day),
                        date.fromisoformat(day),
                        timezone_name,
                    ),
                    entity_type=provider_type,
                    default_sync_status=row["performance_sync_status"],
                )
            )
    return UnifiedMarketingDailySeries(
        provider="meta_ads",
        entity_level=entity_level,
        account={
            "id": account["ad_account_id"],
            "name": account.get("display_name") or account["ad_account_id"],
            "currency": account["currency"],
            "timezone": account["timezone"],
        },
        period=_period(date_from, date_to, timezone_name),
        rows=output,
        source_fact_count=sum(int(row.quality.source_fact_count) for row in output),
        decision_eligibility={
            "eligible": False,
            "reason": "meta_shadow_not_accepted",
        },
    ).model_dump(mode="json")


async def load_meta_v2_dashboard_spend(
    db: Any,
    user_id: str,
    *,
    date_from: date,
    date_to: date,
    timezone_name: str,
) -> dict[str, Any]:
    report = await load_meta_v2_account_report(
        db,
        user_id,
        date_from=date_from,
        date_to=date_to,
        timezone_name=timezone_name,
    )
    total = dict(report.get("totals") or {})
    return {
        "provider": "meta_ads",
        "account": report.get("account"),
        "period": report.get("period"),
        "spend_sar": (
            ((total.get("delivery") or {}).get("spend_sar") or {}).get("amount")
        ),
        "quality": total.get("quality"),
        "lineage": total.get("lineage"),
        "decision_eligibility": report.get("decision_eligibility"),
    }


__all__ = [
    "META_ACCOUNT_COLLECTION",
    "META_ENTITY_COVERAGE_COLLECTION",
    "META_ENTITY_FACT_COLLECTION",
    "META_ENTITY_SNAPSHOT_COLLECTION",
    "load_meta_v2_account_identity",
    "load_meta_v2_account_report",
    "load_meta_v2_dashboard_spend",
    "load_meta_v2_entity_daily_series",
    "load_meta_v2_entity_metadata",
    "load_meta_v2_entity_readiness_evidence",
    "load_meta_v2_entity_report",
]
