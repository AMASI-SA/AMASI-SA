"""Persist native Meta hierarchy, settings, and child-level daily evidence.

This module is part of native ingestion. It may read Meta and write only the
dedicated analytical projection collections below. Unified Marketing consumes
those stored projections and never reaches the provider.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx

from .meta_campaign_reporting import (
    META_CAMPAIGN_PAGE_SIZE,
    _action_value,
    _fx_to_sar,
    _minor_to_native,
    _paged_get,
)
from .meta_oauth_security import META_PROVIDER_ID, meta_appsecret_proof, meta_graph_base

META_ENTITY_SNAPSHOT_COLLECTION = "mezan_meta_entity_snapshots_v2"
META_ENTITY_FACT_COLLECTION = "mezan_meta_entity_performance_daily_v2"
META_ENTITY_COVERAGE_COLLECTION = "mezan_meta_entity_coverage_daily_v2"
META_ENTITY_SOURCE_MODE = "meta_entity_reporting_v2"

CATALOG_FIELDS = {
    "campaign": (
        "status",
        "effective_status",
        "daily_budget",
        "lifetime_budget",
    ),
    "adset": (
        "status",
        "effective_status",
        "daily_budget",
        "lifetime_budget",
        "bid_amount",
        "bid_strategy",
        "billing_event",
        "optimization_goal",
    ),
    "ad": ("status", "effective_status"),
}


async def ensure_meta_entity_reporting_indexes(db: Any) -> None:
    await db[META_ENTITY_SNAPSHOT_COLLECTION].create_index(
        [
            ("user_id", 1),
            ("ad_account_id", 1),
            ("entity_type", 1),
            ("external_id", 1),
        ],
        unique=True,
        name="meta_entity_snapshot_unique",
    )
    await db[META_ENTITY_FACT_COLLECTION].create_index(
        [
            ("user_id", 1),
            ("ad_account_id", 1),
            ("entity_type", 1),
            ("external_id", 1),
            ("date", 1),
        ],
        unique=True,
        name="meta_entity_fact_unique",
    )
    await db[META_ENTITY_COVERAGE_COLLECTION].create_index(
        [
            ("user_id", 1),
            ("ad_account_id", 1),
            ("entity_type", 1),
            ("date", 1),
        ],
        unique=True,
        name="meta_entity_coverage_unique",
    )


def _campaign_snapshots(
    account: dict[str, Any], campaign_catalog: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    currency = str(account.get("currency") or "").strip().upper() or None
    return {
        campaign_id: {
            "entity_type": "campaign",
            "external_id": campaign_id,
            "name": metadata.get("campaign_name") or campaign_id,
            "campaign_id": campaign_id,
            "ad_group_id": None,
            "objective": metadata.get("objective"),
            "status": metadata.get("status"),
            "effective_status": metadata.get("effective_status"),
            "daily_budget_native": _minor_to_native(
                metadata.get("daily_budget_minor"), currency
            ),
            "lifetime_budget_native": _minor_to_native(
                metadata.get("lifetime_budget_minor"), currency
            ),
            "start_time": metadata.get("start_time"),
            "end_time": metadata.get("stop_time"),
            "created_time": metadata.get("created_time"),
            "updated_time": metadata.get("updated_time"),
            "settings_fields_present": list(CATALOG_FIELDS["campaign"]),
        }
        for campaign_id, metadata in campaign_catalog.items()
    }


def _child_snapshot(
    row: dict[str, Any], *, entity_type: str, currency: str | None
) -> dict[str, Any] | None:
    external_id = str(row.get("id") or "").strip()
    if not external_id:
        return None
    campaign_id = str(row.get("campaign_id") or "").strip() or None
    ad_group_id = (
        external_id
        if entity_type == "adset"
        else str(row.get("adset_id") or "").strip() or None
    )
    return {
        "entity_type": entity_type,
        "external_id": external_id,
        "name": row.get("name") or external_id,
        "campaign_id": campaign_id,
        "ad_group_id": ad_group_id,
        "status": row.get("status"),
        "effective_status": row.get("effective_status"),
        "daily_budget_native": _minor_to_native(row.get("daily_budget"), currency),
        "lifetime_budget_native": _minor_to_native(
            row.get("lifetime_budget"), currency
        ),
        "bid_amount_native": _minor_to_native(row.get("bid_amount"), currency),
        "bid_strategy": row.get("bid_strategy"),
        "billing_event": row.get("billing_event"),
        "optimization_goal": row.get("optimization_goal"),
        "start_time": row.get("start_time"),
        "end_time": row.get("end_time"),
        "created_time": row.get("created_time"),
        "updated_time": row.get("updated_time"),
        "creative_id": (
            (row.get("creative") or {}).get("id")
            if isinstance(row.get("creative"), dict)
            else None
        ),
        "settings_fields_present": list(CATALOG_FIELDS[entity_type]),
    }


async def fetch_meta_entity_catalog(
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
    *,
    campaign_catalog: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    account_id = str(account.get("ad_account_id") or "").strip()
    base_params = {
        "access_token": access_token,
        "appsecret_proof": meta_appsecret_proof(access_token),
        "limit": META_CAMPAIGN_PAGE_SIZE,
    }
    adset_rows, adset_calls = await _paged_get(
        client,
        f"{meta_graph_base()}/{account_id}/adsets",
        {
            **base_params,
            "fields": (
                "id,name,campaign_id,status,effective_status,daily_budget,"
                "lifetime_budget,bid_amount,bid_strategy,billing_event,"
                "optimization_goal,start_time,end_time,created_time,updated_time"
            ),
        },
        operation="meta_adset_catalog",
    )
    ad_rows, ad_calls = await _paged_get(
        client,
        f"{meta_graph_base()}/{account_id}/ads",
        {
            **base_params,
            "fields": (
                "id,name,campaign_id,adset_id,status,effective_status,"
                "creative{id,name},created_time,updated_time"
            ),
        },
        operation="meta_ad_catalog",
    )
    currency = str(account.get("currency") or "").strip().upper() or None
    adsets = {
        item["external_id"]: item
        for row in adset_rows
        if (item := _child_snapshot(row, entity_type="adset", currency=currency))
    }
    ads = {
        item["external_id"]: item
        for row in ad_rows
        if (item := _child_snapshot(row, entity_type="ad", currency=currency))
    }
    return {
        "entities": {
            "campaign": _campaign_snapshots(account, campaign_catalog),
            "adset": adsets,
            "ad": ads,
        },
        "provider_calls": adset_calls + ad_calls,
    }


async def persist_meta_entity_snapshots(
    db: Any,
    user_id: str,
    account: dict[str, Any],
    *,
    entity_catalog: dict[str, dict[str, dict[str, Any]]],
    observed_at: str,
) -> int:
    account_id = str(account.get("ad_account_id") or "").strip()
    saved = 0
    for entity_type in ("campaign", "adset", "ad"):
        values = entity_catalog.get(entity_type) or {}
        await db[META_ENTITY_SNAPSHOT_COLLECTION].delete_many(
            {
                "user_id": user_id,
                "provider": META_PROVIDER_ID,
                "ad_account_id": account_id,
                "entity_type": entity_type,
            }
        )
        for snapshot in values.values():
            document = {
                **snapshot,
                "user_id": user_id,
                "provider": META_PROVIDER_ID,
                "ad_account_id": account_id,
                "currency_native": account.get("currency"),
                "source_mode": META_ENTITY_SOURCE_MODE,
                "source_only": True,
                "observed_at": observed_at,
                "updated_at": observed_at,
            }
            await db[META_ENTITY_SNAPSHOT_COLLECTION].update_one(
                {
                    "user_id": user_id,
                    "provider": META_PROVIDER_ID,
                    "ad_account_id": account_id,
                    "entity_type": entity_type,
                    "external_id": document["external_id"],
                },
                {"$set": document, "$setOnInsert": {"created_at": observed_at}},
                upsert=True,
            )
            saved += 1
    return saved


async def record_meta_entity_coverage(
    db: Any,
    user_id: str,
    account: dict[str, Any],
    day: date,
    *,
    entity_type: str,
    provider_rows: int,
    catalog_entities: int,
    observed_at: str,
    amount_complete: bool,
) -> None:
    account_id = str(account.get("ad_account_id") or "").strip()
    document = {
        "user_id": user_id,
        "provider": META_PROVIDER_ID,
        "ad_account_id": account_id,
        "entity_type": entity_type,
        "date": day.isoformat(),
        "status": "complete",
        "amount_complete": bool(amount_complete),
        "provider_rows": max(0, int(provider_rows)),
        "catalog_entities": max(0, int(catalog_entities)),
        "attribution_mode": "account_setting+unified",
        "source_mode": META_ENTITY_SOURCE_MODE,
        "source_only": True,
        "observed_at": observed_at,
        "updated_at": observed_at,
    }
    await db[META_ENTITY_COVERAGE_COLLECTION].update_one(
        {
            "user_id": user_id,
            "provider": META_PROVIDER_ID,
            "ad_account_id": account_id,
            "entity_type": entity_type,
            "date": day.isoformat(),
        },
        {"$set": document, "$setOnInsert": {"created_at": observed_at}},
        upsert=True,
    )


async def sync_meta_entity_day(
    db: Any,
    user_id: str,
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
    day: date,
    *,
    entity_type: str,
    entity_catalog: dict[str, dict[str, Any]],
    observed_at: str,
) -> dict[str, Any]:
    if entity_type not in {"adset", "ad"}:
        raise ValueError(f"unsupported_meta_entity_type:{entity_type}")
    account_id = str(account.get("ad_account_id") or "").strip()
    identifiers = (
        "adset_id,adset_name,campaign_id,campaign_name"
        if entity_type == "adset"
        else "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name"
    )
    rows, provider_calls = await _paged_get(
        client,
        f"{meta_graph_base()}/{account_id}/insights",
        {
            "access_token": access_token,
            "appsecret_proof": meta_appsecret_proof(access_token),
            "fields": (
                f"{identifiers},spend,impressions,clicks,actions,action_values,"
                "account_currency,date_start,date_stop"
            ),
            "time_range": json.dumps(
                {"since": day.isoformat(), "until": day.isoformat()},
                separators=(",", ":"),
            ),
            "time_increment": 1,
            "level": entity_type,
            "action_report_time": "conversion",
            "use_account_attribution_setting": "true",
            "use_unified_attribution_setting": "true",
            "limit": META_CAMPAIGN_PAGE_SIZE,
        },
        operation=f"meta_{entity_type}_insights",
    )
    documents: list[dict[str, Any]] = []
    account_fx_rate, _ = _fx_to_sar(account.get("currency"))
    amount_complete = account_fx_rate is not None
    for row in rows:
        external_id = str(row.get(f"{entity_type}_id") or "").strip()
        if not external_id:
            continue
        snapshot = entity_catalog.get(external_id) or {}
        currency = (
            str(row.get("account_currency") or account.get("currency") or "")
            .strip()
            .upper()
            or None
        )
        fx_rate, fx_source = _fx_to_sar(currency)
        amount_complete = amount_complete and fx_rate is not None
        spend_native = float(row.get("spend") or 0)
        purchases, purchase_action_type = _action_value(row.get("actions"))
        purchase_value, purchase_value_action_type = _action_value(
            row.get("action_values")
        )
        documents.append(
            {
                "user_id": user_id,
                "provider": META_PROVIDER_ID,
                "ad_account_id": account_id,
                "entity_type": entity_type,
                "external_id": external_id,
                "name": row.get(f"{entity_type}_name")
                or snapshot.get("name")
                or external_id,
                "campaign_id": row.get("campaign_id") or snapshot.get("campaign_id"),
                "ad_group_id": (
                    external_id
                    if entity_type == "adset"
                    else row.get("adset_id") or snapshot.get("ad_group_id")
                ),
                "date": day.isoformat(),
                "date_start": row.get("date_start") or day.isoformat(),
                "date_stop": row.get("date_stop") or day.isoformat(),
                "currency_native": currency,
                "spend_native": spend_native,
                "fx_rate_to_sar": fx_rate,
                "fx_source": fx_source,
                "spend_sar": (
                    round(spend_native * fx_rate, 2) if fx_rate is not None else None
                ),
                "impressions": int(float(row.get("impressions") or 0)),
                "clicks": int(float(row.get("clicks") or 0)),
                "purchases": purchases,
                "purchase_value_native": purchase_value,
                "purchase_value_sar": (
                    round(purchase_value * fx_rate, 2) if fx_rate is not None else None
                ),
                "purchase_action_type": purchase_action_type,
                "purchase_value_action_type": purchase_value_action_type,
                "attribution_mode": "account_setting+unified",
                "source_mode": META_ENTITY_SOURCE_MODE,
                "source_only": True,
                "accounting_eligible": False,
                "observed_at": observed_at,
                "updated_at": observed_at,
            }
        )
    await db[META_ENTITY_FACT_COLLECTION].delete_many(
        {
            "user_id": user_id,
            "provider": META_PROVIDER_ID,
            "ad_account_id": account_id,
            "entity_type": entity_type,
            "date": day.isoformat(),
        }
    )
    for document in documents:
        await db[META_ENTITY_FACT_COLLECTION].update_one(
            {
                "user_id": user_id,
                "provider": META_PROVIDER_ID,
                "ad_account_id": account_id,
                "entity_type": entity_type,
                "external_id": document["external_id"],
                "date": day.isoformat(),
            },
            {"$set": document, "$setOnInsert": {"created_at": observed_at}},
            upsert=True,
        )
    await record_meta_entity_coverage(
        db,
        user_id,
        account,
        day,
        entity_type=entity_type,
        provider_rows=len(documents),
        catalog_entities=len(entity_catalog),
        observed_at=observed_at,
        amount_complete=amount_complete,
    )
    return {
        "rows_saved": len(documents),
        "provider_calls": provider_calls,
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


__all__ = [
    "CATALOG_FIELDS",
    "META_ENTITY_COVERAGE_COLLECTION",
    "META_ENTITY_FACT_COLLECTION",
    "META_ENTITY_SNAPSHOT_COLLECTION",
    "META_ENTITY_SOURCE_MODE",
    "ensure_meta_entity_reporting_indexes",
    "fetch_meta_entity_catalog",
    "persist_meta_entity_snapshots",
    "record_meta_entity_coverage",
    "sync_meta_entity_day",
]
