"""Read-only Snapchat Campaign and Ad Squad settings projections.

This module reads only the provider-backed native entity catalogue populated by
snapchat_native_entities_sync. It never calls Snapchat, writes MongoDB,
schedules work, backfills facts, or reads reporting/performance data.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, Query

from .snapchat_native_data_common import (
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
    SNAPCHAT_PROVIDER_ID,
    _collection,
)

SETTINGS_SYNC_RUN_COLLECTION = "mezan_integration_sync_runs_v2"
INTEGRATION_ACCOUNTS_COLLECTION = "mezan_integration_accounts_v2"
SETTINGS_FRESHNESS_MAX_AGE_SECONDS = 30 * 60
MAX_SETTINGS_ROWS = 500
MAX_CAMPAIGN_CHILD_ROWS = 10_000
MAX_VISIBLE_SETTINGS_ROWS = 100
SUPPORTED_ENTITY_TYPES = ("campaign", "ad_squad")
_COMPLETE = "settings_complete"
_NOT_LOADED = "settings_not_loaded"
_SYNC_FAILED = "settings_sync_failed"
_STALE = "settings_stale"
_UNAVAILABLE_AR = "غير متاح — فشل جلب الإعدادات"
_UNSUPPORTED_CAMPAIGN_BUDGET_AR = "غير متاح من Snapchat على هذا المستوى"
_IDENTITY_CONTRACT_NAME = "snapchat_v2_provider_id_is_unified_id_v1"


def _identity_contract(
    unified_entity_id: str,
    provider_entity_id: str | None,
) -> dict[str, Any]:
    """Native settings sync and the Unified adapter both canonicalize Snapchat id."""
    return {
        "name": _IDENTITY_CONTRACT_NAME,
        "requires_equal": True,
        "ids_equal": (
            None
            if provider_entity_id is None
            else unified_entity_id == provider_entity_id
        ),
        "unified_id_source": "mezan_snapchat_entities_v2.external_id",
        "provider_id_source": "mezan_snapchat_entities_v2.provider_snapshot.id",
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now_value(now: datetime | Callable[[], datetime] | None) -> datetime:
    value = now() if callable(now) else now
    return _as_utc(value) or _utcnow()


def _safe_id(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _provider_snapshot(row: dict[str, Any]) -> dict[str, Any] | None:
    snapshot = row.get("provider_snapshot")
    return snapshot if isinstance(snapshot, dict) else None


def _provider_field(row: dict[str, Any], field: str) -> tuple[Any, bool]:
    """Return the exact provider value and proof that the field was present."""
    snapshot = _provider_snapshot(row)
    if snapshot is None or field not in snapshot:
        return None, False
    return deepcopy(snapshot.get(field)), True


def _provider_updated_at(row: dict[str, Any]) -> Any:
    value, present = _provider_field(row, "updated_at")
    if present and value is not None:
        return value
    return row.get("updated_at_provider")


def _micro_integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and value.is_integer() else None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    if parsed < 0 or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def micro_to_account_currency(value: Any) -> float | None:
    """Convert exact micro-currency units to the ad-account currency."""
    parsed = _micro_integer(value)
    if parsed is None:
        return None
    return float(Decimal(parsed) / Decimal(1_000_000))


def micro_to_usd(value: Any, account_currency: Any) -> float | None:
    """Return USD only when the provider account itself is denominated in USD."""
    if str(account_currency or "").strip().upper() != "USD":
        return None
    return micro_to_account_currency(value)


def bid_semantic_for_strategy(bid_strategy: Any) -> str:
    strategy = str(bid_strategy or "").strip().upper()
    if strategy == "TARGET_COST":
        return "target_cost"
    if strategy == "LOWEST_COST_WITH_MAX_BID":
        return "max_bid"
    return "bid"


async def _cursor_rows(
    cursor: Any,
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(limit + 1)
    if hasattr(cursor, "to_list"):
        rows = list(await cursor.to_list(length=limit + 1))
    elif isinstance(cursor, list):
        rows = list(cursor[: limit + 1])
    else:
        rows = [row async for row in cursor]
    return rows[:limit], len(rows) > limit


async def _find_rows(
    collection: Any,
    query: dict[str, Any],
    projection: dict[str, int],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    return await _cursor_rows(collection.find(query, projection), limit=limit)


async def _latest_sync_run(db: Any, user_id: str) -> dict[str, Any] | None:
    cursor = _collection(db, SETTINGS_SYNC_RUN_COLLECTION).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "run_type": "analytics_refresh",
        },
        {
            "_id": 0,
            "run_id": 1,
            "status": 1,
            "started_at": 1,
            "finished_at": 1,
            "summary": 1,
            "error": 1,
        },
    )
    if hasattr(cursor, "sort"):
        cursor = cursor.sort("started_at", -1)
    rows, _ = await _cursor_rows(cursor, limit=1)
    return rows[0] if rows else None


def _settings_quality(
    row: dict[str, Any] | None,
    *,
    entity_type: str,
    account_currency: str | None,
    latest_run: dict[str, Any] | None,
    now: datetime,
    mapping_status: str,
    required_financial_field_available: bool,
) -> dict[str, Any]:
    if row is None:
        status = _NOT_LOADED
        reason = "native_entity_row_missing"
        freshness_seconds = None
        settings_synced_at = None
    else:
        settings_synced_at = row.get("last_observed_at")
        observed = _as_utc(settings_synced_at)
        snapshot = _provider_snapshot(row)
        if snapshot is None:
            status = _NOT_LOADED
            reason = "provider_snapshot_missing"
            freshness_seconds = (
                None
                if observed is None
                else max(0, int((now - observed).total_seconds()))
            )
        elif observed is None:
            status = _NOT_LOADED
            reason = "settings_synced_at_missing"
            freshness_seconds = None
        else:
            age = int((now - observed).total_seconds())
            freshness_seconds = max(0, age)
            if age < -300:
                status = _SYNC_FAILED
                reason = "settings_synced_at_in_future"
            else:
                run_started = _as_utc((latest_run or {}).get("started_at"))
                run_status = str((latest_run or {}).get("status") or "").lower()
                run_after_settings = bool(run_started and run_started > observed)
                terminal_run_without_entity_proof = bool(
                    run_after_settings
                    and run_status in {"complete", "partial", "failed"}
                )
                if terminal_run_without_entity_proof:
                    status = _SYNC_FAILED
                    reason = (
                        "latest_native_settings_sync_failed"
                        if run_status == "failed"
                        else "latest_native_sync_missing_entity_specific_proof"
                    )
                elif freshness_seconds > SETTINGS_FRESHNESS_MAX_AGE_SECONDS:
                    status = _STALE
                    reason = "settings_older_than_freshness_threshold"
                else:
                    status = _COMPLETE
                    reason = "provider_snapshot_fresh"

    currency_is_usd = str(account_currency or "").upper() == "USD"
    financial_controls_allowed = bool(
        status == _COMPLETE
        and mapping_status == "verified"
        and currency_is_usd
        and required_financial_field_available
    )
    if status == _COMPLETE and mapping_status != "verified":
        status = _SYNC_FAILED
        reason = "provider_identity_mapping_unverified"
        financial_controls_allowed = False
    elif status == _COMPLETE and not currency_is_usd:
        reason = "account_currency_unknown_or_not_usd"
    elif status == _COMPLETE and not required_financial_field_available:
        reason = "required_provider_financial_field_missing"

    return {
        "settings_status": status,
        "freshness_seconds": freshness_seconds,
        "freshness_threshold_seconds": SETTINGS_FRESHNESS_MAX_AGE_SECONDS,
        "reason": reason,
        "financial_controls_allowed": financial_controls_allowed,
        "settings_synced_at": settings_synced_at,
        "provider_updated_at": _provider_updated_at(row) if row is not None else None,
        "mapping_status": mapping_status,
        "source_mode": row.get("source_mode") if row is not None else None,
        "provider_snapshot_proof": bool(
            row is not None and _provider_snapshot(row) is not None
        ),
        "latest_sync_run_id": (
            (latest_run or {}).get("run_id") if latest_run else None
        ),
        "latest_sync_run_status": (
            (latest_run or {}).get("status") if latest_run else None
        ),
    }


def _field_availability(
    *,
    field_present: bool,
    parsed_micro: int | None,
    missing_reason: str,
) -> str:
    if not field_present:
        return missing_reason
    if parsed_micro is None:
        return "invalid_provider_value"
    return "available"


def _mapping_status(
    row: dict[str, Any],
    *,
    unified_entity_id: str,
    expected_provider_entity_id: str | None = None,
    expected_parent_unified_id: str | None = None,
) -> str:
    external_id = _safe_id(row.get("external_id"))
    snapshot = _provider_snapshot(row)
    snapshot_id = _safe_id((snapshot or {}).get("id"))
    if (
        row.get("source_mode") != SNAPCHAT_NATIVE_SYNC_SOURCE_MODE
        or external_id is None
        or snapshot_id != external_id
        or unified_entity_id != external_id
    ):
        return "unverified"
    if expected_provider_entity_id and expected_provider_entity_id != external_id:
        return "mismatch"
    row_parent = _safe_id(row.get("campaign_id"))
    snapshot_parent = _safe_id((snapshot or {}).get("campaign_id"))
    if row.get("entity_type") == "ad_squad" and (
        row_parent is None or snapshot_parent != row_parent
    ):
        return "unverified"
    if expected_parent_unified_id and expected_parent_unified_id != snapshot_parent:
        return "parent_mismatch"
    return "verified"


def _base_item(
    row: dict[str, Any],
    *,
    unified_entity_id: str,
    account: dict[str, Any] | None,
    latest_run: dict[str, Any] | None,
    now: datetime,
    expected_provider_entity_id: str | None = None,
    expected_parent_unified_id: str | None = None,
) -> dict[str, Any]:
    entity_type = str(row.get("entity_type") or "")
    snapshot = _provider_snapshot(row) or {}
    provider_entity_id = _safe_id(snapshot.get("id"))
    provider_parent_id = (
        _safe_id(snapshot.get("campaign_id")) if entity_type == "ad_squad" else None
    )
    mapping_status = _mapping_status(
        row,
        unified_entity_id=unified_entity_id,
        expected_provider_entity_id=expected_provider_entity_id,
        expected_parent_unified_id=expected_parent_unified_id,
    )
    account_currency_raw = (account or {}).get("currency")
    account_currency = str(account_currency_raw or "").strip().upper() or None

    daily_raw, daily_present = _provider_field(row, "daily_budget_micro")
    daily_micro = _micro_integer(daily_raw)
    if entity_type == "campaign":
        # Campaign daily_budget_micro is an optional provider Daily Spend Cap.
        # An absent key means the setting is unavailable at this level. Null,
        # zero, and malformed values are present but unusable and must never be
        # rendered as a real zero budget or replaced with a child aggregate.
        if not daily_present:
            daily_availability = "unsupported_at_provider_level"
        elif daily_micro is None or daily_micro == 0:
            daily_micro = None
            daily_availability = "invalid_provider_value"
        else:
            daily_availability = "available"
    else:
        daily_availability = _field_availability(
            field_present=daily_present,
            parsed_micro=daily_micro,
            missing_reason="provider_field_missing",
        )

    bid_raw, bid_present = _provider_field(row, "bid_micro")
    bid_micro = _micro_integer(bid_raw)
    bid_strategy_raw, _ = _provider_field(row, "bid_strategy")
    bid_strategy = (
        str(bid_strategy_raw).strip().upper() if bid_strategy_raw is not None else None
    )
    bid_availability = _field_availability(
        field_present=bid_present,
        parsed_micro=bid_micro,
        missing_reason=(
            "unsupported_by_strategy"
            if bid_strategy == "AUTO_BID"
            else "provider_field_missing"
        ),
    )

    quality = _settings_quality(
        row,
        entity_type=entity_type,
        account_currency=account_currency,
        latest_run=latest_run,
        now=now,
        mapping_status=mapping_status,
        required_financial_field_available=daily_availability == "available",
    )
    if (
        entity_type == "campaign"
        and daily_availability != "available"
        and quality["settings_status"] == _COMPLETE
    ):
        quality["reason"] = daily_availability
    bid_control_allowed = bool(
        quality["settings_status"] == _COMPLETE
        and mapping_status == "verified"
        and account_currency == "USD"
        and bid_availability == "available"
        and entity_type == "ad_squad"
    )
    quality["financial_field_controls"] = {
        "daily_budget": {
            "allowed": quality["financial_controls_allowed"],
            "reason": (
                "available"
                if quality["financial_controls_allowed"]
                else (
                    daily_availability
                    if daily_availability == "unsupported_at_provider_level"
                    else quality["reason"]
                )
            ),
        },
        "bid": {
            "allowed": bid_control_allowed,
            "reason": (
                "available"
                if bid_control_allowed
                else (
                    bid_availability
                    if bid_availability != "available"
                    else quality["reason"]
                )
            ),
        },
    }

    def provider_setting(name: str) -> Any:
        value, present = _provider_field(row, name)
        return value if present else None

    return {
        "entity_type": entity_type,
        "unified_entity_id": unified_entity_id,
        "provider_entity_id": provider_entity_id,
        "provider_parent_id": provider_parent_id,
        "identity_contract": _identity_contract(
            unified_entity_id,
            provider_entity_id,
        ),
        "mapping_status": mapping_status,
        "mapping_verified": mapping_status == "verified",
        "mapping_source": (
            "mezan_snapchat_entities_v2.provider_snapshot.id"
            if mapping_status == "verified"
            else None
        ),
        "ad_account_id": _safe_id(row.get("ad_account_id")),
        "display_name": provider_setting("name") or row.get("display_name"),
        "account_currency": account_currency,
        "account_currency_raw": account_currency_raw,
        "daily_budget_micro": deepcopy(daily_raw) if daily_present else None,
        "daily_budget_account_currency": (
            micro_to_account_currency(daily_micro)
            if daily_availability == "available" and account_currency
            else None
        ),
        "daily_budget_usd": (
            micro_to_usd(daily_micro, account_currency)
            if daily_availability == "available"
            else None
        ),
        "daily_budget_availability": daily_availability,
        "bid_micro": deepcopy(bid_raw) if bid_present else None,
        "bid_account_currency": (
            micro_to_account_currency(bid_micro)
            if bid_availability == "available" and account_currency
            else None
        ),
        "bid_usd": (
            micro_to_usd(bid_micro, account_currency)
            if bid_availability == "available"
            else None
        ),
        "bid_availability": bid_availability,
        "bid_strategy": bid_strategy,
        "bid_semantic": bid_semantic_for_strategy(bid_strategy),
        "optimization_goal": provider_setting("optimization_goal"),
        "billing_event": provider_setting("billing_event"),
        "conversion_window": provider_setting("conversion_window"),
        "status": provider_setting("status"),
        "settings_synced_at": row.get("last_observed_at"),
        "provider_updated_at": _provider_updated_at(row),
        "quality": quality,
        "daily_budget_unavailable_message_ar": (
            _UNSUPPORTED_CAMPAIGN_BUDGET_AR
            if daily_availability == "unsupported_at_provider_level"
            else (None if daily_availability == "available" else _UNAVAILABLE_AR)
        ),
        "unavailable_message_ar": (
            None
            if quality["settings_status"] == _COMPLETE
            and daily_availability == "available"
            else (
                _UNSUPPORTED_CAMPAIGN_BUDGET_AR
                if daily_availability == "unsupported_at_provider_level"
                else _UNAVAILABLE_AR
            )
        ),
    }


def _missing_item(
    *,
    entity_type: str,
    unified_entity_id: str,
    expected_provider_entity_id: str | None = None,
    expected_parent_unified_id: str | None = None,
) -> dict[str, Any]:
    quality = _settings_quality(
        None,
        entity_type=entity_type,
        account_currency=None,
        latest_run=None,
        now=_utcnow(),
        mapping_status="unverified",
        required_financial_field_available=False,
    )
    quality["financial_field_controls"] = {
        "daily_budget": {"allowed": False, "reason": quality["reason"]},
        "bid": {"allowed": False, "reason": quality["reason"]},
    }
    return {
        "entity_type": entity_type,
        "unified_entity_id": unified_entity_id,
        "provider_entity_id": None,
        "provider_parent_id": None,
        "identity_contract": _identity_contract(unified_entity_id, None),
        "requested_provider_entity_id": expected_provider_entity_id,
        "requested_parent_unified_id": expected_parent_unified_id,
        "mapping_status": "unverified",
        "mapping_verified": False,
        "mapping_source": None,
        "ad_account_id": None,
        "display_name": None,
        "account_currency": None,
        "account_currency_raw": None,
        "daily_budget_micro": None,
        "daily_budget_account_currency": None,
        "daily_budget_usd": None,
        "daily_budget_availability": "provider_field_missing",
        "bid_micro": None,
        "bid_account_currency": None,
        "bid_usd": None,
        "bid_availability": "provider_field_missing",
        "bid_strategy": None,
        "bid_semantic": "bid",
        "optimization_goal": None,
        "billing_event": None,
        "conversion_window": None,
        "status": None,
        "settings_synced_at": None,
        "provider_updated_at": None,
        "quality": quality,
        "daily_budget_unavailable_message_ar": _UNAVAILABLE_AR,
        "unavailable_message_ar": _UNAVAILABLE_AR,
    }


def _account_by_provider_id(
    account_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for account in account_rows:
        for value in (
            account.get("external_account_id"),
            account.get("ad_account_id"),
        ):
            provider_id = _safe_id(value)
            if provider_id:
                output[provider_id] = account
    return output


def _ad_squad_catalog_coverage(
    latest_run: dict[str, Any] | None,
    campaign_item: dict[str, Any],
    account_id: str | None,
    account_rows: list[dict[str, Any]],
    *,
    truncated: bool,
) -> dict[str, Any]:
    run_status = str((latest_run or {}).get("status") or "").lower()
    base = {
        "complete": False,
        "reason": "child_catalog_proof_missing",
        "latest_sync_run_id": (latest_run or {}).get("run_id"),
        "latest_sync_run_status": (latest_run or {}).get("status"),
        "provider_account_ad_squad_count": None,
        "loaded_account_ad_squad_count": 0,
        "unpartitioned_ad_squad_count": 0,
        "invalid_parent_mapping_count": 0,
        "account_catalog_truncated": truncated,
        "proof_mode": None,
    }
    if run_status != "complete":
        if run_status == "partial":
            base["reason"] = "child_catalog_sync_partial"
        elif run_status == "failed":
            base["reason"] = "child_catalog_sync_failed"
        elif run_status:
            base["reason"] = "child_catalog_sync_not_complete"
        return base

    started = _as_utc((latest_run or {}).get("started_at"))
    finished = _as_utc((latest_run or {}).get("finished_at"))
    if started is None or finished is None or finished < started:
        base["reason"] = "child_catalog_run_window_invalid"
        return base

    campaign_observed = _as_utc(campaign_item.get("settings_synced_at"))
    if (
        campaign_observed is None
        or campaign_observed < started
        or campaign_observed > finished
    ):
        base["reason"] = "child_catalog_campaign_observation_missing"
        return base

    summary = (latest_run or {}).get("summary")
    summary = summary if isinstance(summary, dict) else {}
    observed_rows = _rows_observed_in_latest_run(latest_run, account_rows)
    unpartitioned_count = sum(
        1 for row in observed_rows if _safe_id(row.get("campaign_id")) is None
    )
    invalid_parent_count = sum(
        1
        for row in observed_rows
        if (
            _safe_id(row.get("campaign_id")) is None
            or _safe_id((_provider_snapshot(row) or {}).get("campaign_id")) is None
            or _safe_id(row.get("campaign_id"))
            != _safe_id((_provider_snapshot(row) or {}).get("campaign_id"))
        )
    )
    base.update(
        {
            "loaded_account_ad_squad_count": len(observed_rows),
            "unpartitioned_ad_squad_count": unpartitioned_count,
            "invalid_parent_mapping_count": invalid_parent_count,
        }
    )
    if truncated:
        base["reason"] = "child_catalog_truncated"
        return base
    if invalid_parent_count:
        base["reason"] = "child_catalog_parent_mapping_invalid"
        return base

    if "entity_counts" in summary:
        counts_by_account = summary.get("entity_counts")
        if not isinstance(counts_by_account, dict):
            base["reason"] = "child_catalog_count_invalid"
            return base
        account_counts = counts_by_account.get(account_id)
        if not isinstance(account_counts, dict) or "ad_squad" not in account_counts:
            base["reason"] = "child_catalog_count_missing"
            return base
        raw_count = account_counts.get("ad_squad")
        provider_count = (
            None if isinstance(raw_count, bool) else _micro_integer(raw_count)
        )
        if provider_count is None:
            base["reason"] = "child_catalog_count_invalid"
            return base
        base["provider_account_ad_squad_count"] = provider_count
        base["proof_mode"] = "sync_run_count_verified"
        if len(observed_rows) != provider_count:
            base["reason"] = "child_catalog_account_count_mismatch"
            return base
    else:
        # A complete native run means every entity endpoint paginated without
        # errors. Its run window therefore proves the rows observed for this
        # account even on scheduler summaries that omit entity_counts.
        base["proof_mode"] = "complete_sync_run_window"

    base.update(
        {
            "complete": True,
            "reason": "available",
        }
    )
    return base


def _rows_observed_in_latest_run(
    latest_run: dict[str, Any] | None,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only catalog rows proved present in the latest native sync run."""
    started = _as_utc((latest_run or {}).get("started_at"))
    finished = _as_utc((latest_run or {}).get("finished_at"))
    if started is None or finished is None or finished < started:
        return []
    output: list[dict[str, Any]] = []
    for row in rows:
        observed = _as_utc(row.get("last_observed_at"))
        if observed is not None and started <= observed <= finished:
            output.append(row)
    return output


def _attach_campaign_aggregate(
    campaign_item: dict[str, Any],
    children: list[dict[str, Any]],
    *,
    truncated: bool,
    catalog_coverage: dict[str, Any],
) -> None:
    loaded_total = len(children)
    account_catalog_complete = catalog_coverage.get("complete") is True
    # The caller partitions only rows observed in the latest complete account-
    # wide catalog. Once that account catalog is proven, every campaign
    # partition (including an empty one) is complete.
    catalog_complete = account_catalog_complete
    if account_catalog_complete is False:
        catalog_reason = str(
            catalog_coverage.get("reason") or "child_catalog_proof_missing"
        )
    else:
        catalog_reason = "available"
    all_settings_complete = all(
        child["quality"]["settings_status"] == _COMPLETE
        and child["mapping_status"] == "verified"
        for child in children
    )
    all_budgets_available = all(
        child["daily_budget_availability"] == "available" for child in children
    )
    all_statuses_available = all(
        _safe_id(child.get("status")) is not None for child in children
    )
    all_bid_strategies_available = all(
        _safe_id(child.get("bid_strategy")) is not None for child in children
    )
    currency = campaign_item.get("account_currency")
    same_currency = all(child.get("account_currency") == currency for child in children)

    budget_complete = bool(
        catalog_complete
        and not truncated
        and all_settings_complete
        and all_budgets_available
        and same_currency
    )
    active_count_complete = bool(
        catalog_complete
        and not truncated
        and all_settings_complete
        and all_statuses_available
    )
    bid_strategy_complete = bool(
        catalog_complete
        and not truncated
        and all_settings_complete
        and all_bid_strategies_available
    )

    def unavailable_reason(field_reason: str) -> str:
        if truncated:
            return "child_catalog_truncated"
        if not catalog_complete:
            return catalog_reason
        if not all_settings_complete:
            return "child_settings_incomplete"
        return field_reason

    if budget_complete:
        sum_micro = sum(
            _micro_integer(child.get("daily_budget_micro")) or 0 for child in children
        )
        budget_availability = "available"
    else:
        sum_micro = None
        budget_availability = unavailable_reason(
            "child_budget_field_missing"
            if not all_budgets_available
            else "child_currency_mismatch"
        )

    if active_count_complete:
        active_count = sum(
            1
            for child in children
            if str(child.get("status") or "").upper() == "ACTIVE"
        )
        active_count_availability = "available"
    else:
        active_count = None
        active_count_availability = unavailable_reason("child_status_field_missing")

    if bid_strategy_complete:
        strategies = sorted(
            {str(child.get("bid_strategy")).upper() for child in children}
        )
        bid_strategies_availability = "available"
    else:
        strategies = []
        bid_strategies_availability = unavailable_reason(
            "child_bid_strategy_field_missing"
        )

    shared = None
    row_shared = campaign_item.pop("_shared_properties", None)
    if isinstance(row_shared, dict):
        shared = row_shared.get("shared_ad_squad_bid_strategy")

    aggregate = {
        "ad_squad_count": (
            loaded_total if catalog_complete and not truncated else None
        ),
        "loaded_ad_squad_count": loaded_total,
        "active_ad_squad_count": active_count,
        "daily_budget_sum_micro": sum_micro,
        "daily_budget_sum_account_currency": (
            micro_to_account_currency(sum_micro)
            if sum_micro is not None and currency
            else None
        ),
        "daily_budget_sum_usd": (
            micro_to_usd(sum_micro, currency) if sum_micro is not None else None
        ),
        "daily_budget_sum_availability": budget_availability,
        "catalog_coverage": {
            **deepcopy(catalog_coverage),
            "campaign_children_complete": catalog_complete,
            "campaign_children_reason": catalog_reason,
        },
        "budget_coverage": {
            "complete": budget_complete,
            "loaded_count": sum(
                1
                for child in children
                if child["daily_budget_availability"] == "available"
            ),
            "total_count": loaded_total,
            "truncated": truncated,
            "catalog_complete": catalog_complete,
        },
        "active_count_availability": active_count_availability,
        "status_coverage": {
            "complete": active_count_complete,
            "loaded_count": sum(
                1 for child in children if _safe_id(child.get("status")) is not None
            ),
            "total_count": loaded_total,
            "truncated": truncated,
            "catalog_complete": catalog_complete,
        },
        "ad_squad_bid_strategies": strategies,
        "bid_strategies_availability": bid_strategies_availability,
        "bid_strategy_coverage": {
            "complete": bid_strategy_complete,
            "loaded_count": sum(
                1
                for child in children
                if _safe_id(child.get("bid_strategy")) is not None
            ),
            "total_count": loaded_total,
            "truncated": truncated,
            "catalog_complete": catalog_complete,
        },
        "shared_ad_squad_bid_strategy": shared,
    }
    campaign_item["campaign_aggregate"] = aggregate
    campaign_item["ad_squad_count"] = aggregate["ad_squad_count"]
    campaign_item["active_ad_squad_count"] = aggregate["active_ad_squad_count"]
    campaign_item["active_ad_squads_availability"] = aggregate[
        "active_count_availability"
    ]
    campaign_item["ad_squad_daily_budget_sum_micro"] = aggregate[
        "daily_budget_sum_micro"
    ]
    campaign_item["ad_squad_daily_budget_sum_usd"] = aggregate["daily_budget_sum_usd"]
    campaign_item["ad_squad_daily_budget_sum_availability"] = aggregate[
        "daily_budget_sum_availability"
    ]
    campaign_item["ad_squad_bid_strategies"] = aggregate["ad_squad_bid_strategies"]
    campaign_item["ad_squad_bid_strategies_availability"] = aggregate[
        "bid_strategies_availability"
    ]
    # Stable aliases used by the Snapchat V2 page contract.
    campaign_item["ad_squads_daily_budget_micro"] = aggregate["daily_budget_sum_micro"]
    campaign_item["ad_squads_daily_budget_usd"] = aggregate["daily_budget_sum_usd"]
    campaign_item["active_ad_squads"] = aggregate["active_ad_squad_count"]
    campaign_item["shared_ad_squad_bid_strategy"] = aggregate[
        "shared_ad_squad_bid_strategy"
    ]


async def _aggregate_visible_campaign_children(
    db: Any,
    *,
    user_id: str,
    campaigns: dict[str, dict[str, Any]],
    latest_run: dict[str, Any] | None,
    now: datetime,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Return one scalar Ad Squad aggregate per visible campaign.

    No Ad Squad document leaves MongoDB.  A complete native run plus the run
    observation window proves the visible campaign partitions; any missing or
    malformed financial field makes its aggregate unavailable.
    """
    collection = _collection(db, SNAPCHAT_ENTITY_COLLECTION)
    if not callable(getattr(collection, "aggregate", None)):
        return {}
    started = _as_utc((latest_run or {}).get("started_at"))
    finished = _as_utc((latest_run or {}).get("finished_at"))
    if started is None or finished is None or finished < started:
        return {}
    account_ids = sorted(
        {
            account_id
            for campaign in campaigns.values()
            if (account_id := _safe_id(campaign.get("ad_account_id")))
        }
    )
    campaign_ids = sorted(campaigns)
    budget_value = {
        "$convert": {
            "input": "$provider_snapshot.daily_budget_micro",
            "to": "long",
            "onError": None,
            "onNull": None,
        }
    }
    mapping_valid = {
        "$and": [
            {"$eq": ["$source_mode", SNAPCHAT_NATIVE_SYNC_SOURCE_MODE]},
            {"$eq": ["$external_id", "$provider_snapshot.id"]},
            {"$eq": ["$campaign_id", "$provider_snapshot.campaign_id"]},
        ]
    }
    pipeline = [
        {
            "$match": {
                "user_id": str(user_id),
                "provider": SNAPCHAT_PROVIDER_ID,
                "entity_type": "ad_squad",
                "ad_account_id": {"$in": account_ids},
                "campaign_id": {"$in": campaign_ids},
                "deleted": {"$ne": True},
                "last_observed_at": {"$gte": started, "$lte": finished},
            }
        },
        {
            "$set": {
                "_budget_value": budget_value,
                "_mapping_valid": mapping_valid,
            }
        },
        {
            "$group": {
                "_id": {
                    "account_id": "$ad_account_id",
                    "campaign_id": "$campaign_id",
                },
                "ad_squad_count": {"$sum": 1},
                "budget_count": {
                    "$sum": {"$cond": [{"$gt": ["$_budget_value", 0]}, 1, 0]}
                },
                "daily_budget_sum_micro": {
                    "$sum": {"$cond": [{"$gt": ["$_budget_value", 0]}, "$_budget_value", 0]}
                },
                "status_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$strLenCP": {"$ifNull": ["$provider_snapshot.status", ""]}}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "active_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": [{"$toUpper": {"$ifNull": ["$provider_snapshot.status", ""]}}, "ACTIVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "strategy_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$strLenCP": {"$ifNull": ["$provider_snapshot.bid_strategy", ""]}}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "strategies": {"$addToSet": "$provider_snapshot.bid_strategy"},
                "mapping_valid_count": {
                    "$sum": {"$cond": ["$_mapping_valid", 1, 0]}
                },
                "oldest_observed_at": {"$min": "$last_observed_at"},
            }
        },
        {"$limit": MAX_VISIBLE_SETTINGS_ROWS},
    ]
    try:
        cursor = collection.aggregate(pipeline, allowDiskUse=False, maxTimeMS=15_000)
    except TypeError:
        cursor = collection.aggregate(pipeline)
    rows, _ = await _cursor_rows(cursor, limit=MAX_VISIBLE_SETTINGS_ROWS)
    return {
        (
            _safe_id((row.get("_id") or {}).get("account_id")) or "",
            _safe_id((row.get("_id") or {}).get("campaign_id")) or "",
        ): row
        for row in rows
    }


def _attach_scalar_campaign_aggregate(
    campaign: dict[str, Any],
    scalar: dict[str, Any] | None,
    *,
    latest_run: dict[str, Any] | None,
    now: datetime,
) -> None:
    scalar = dict(scalar or {})
    count = int(scalar.get("ad_squad_count") or 0)
    started = _as_utc((latest_run or {}).get("started_at"))
    finished = _as_utc((latest_run or {}).get("finished_at"))
    campaign_observed = _as_utc(campaign.get("settings_synced_at"))
    run_complete = bool(
        str((latest_run or {}).get("status") or "").lower() == "complete"
        and started is not None
        and finished is not None
        and finished >= started
        and campaign_observed is not None
        and started <= campaign_observed <= finished
    )
    oldest = _as_utc(scalar.get("oldest_observed_at"))
    fresh = count == 0 or bool(
        oldest is not None
        and 0 <= (now - oldest).total_seconds() <= SETTINGS_FRESHNESS_MAX_AGE_SECONDS
    )
    mapping_complete = int(scalar.get("mapping_valid_count") or 0) == count
    settings_complete = run_complete and fresh and mapping_complete
    budget_complete = settings_complete and int(scalar.get("budget_count") or 0) == count
    status_complete = settings_complete and int(scalar.get("status_count") or 0) == count
    strategy_complete = settings_complete and int(scalar.get("strategy_count") or 0) == count
    currency = campaign.get("account_currency")
    budget_micro = int(scalar.get("daily_budget_sum_micro") or 0) if budget_complete else None
    reason = (
        "available"
        if settings_complete
        else "child_catalog_sync_not_complete"
        if not run_complete
        else "child_settings_stale"
        if not fresh
        else "child_identity_mapping_unverified"
    )
    strategies = sorted(
        str(value).upper()
        for value in list(scalar.get("strategies") or [])
        if _safe_id(value)
    ) if strategy_complete else []
    aggregate = {
        "ad_squad_count": count if run_complete else None,
        "loaded_ad_squad_count": 0,
        "active_ad_squad_count": int(scalar.get("active_count") or 0) if status_complete else None,
        "daily_budget_sum_micro": budget_micro,
        "daily_budget_sum_account_currency": (
            micro_to_account_currency(budget_micro) if budget_micro is not None and currency else None
        ),
        "daily_budget_sum_usd": micro_to_usd(budget_micro, currency) if budget_micro is not None else None,
        "daily_budget_sum_availability": "available" if budget_complete else reason,
        "catalog_coverage": {
            "complete": run_complete,
            "reason": "available" if run_complete else reason,
            "proof_mode": "complete_sync_run_visible_partition_aggregate",
            "python_child_rows_materialized": 0,
        },
        "budget_coverage": {
            "complete": budget_complete,
            "loaded_count": int(scalar.get("budget_count") or 0),
            "total_count": count,
            "truncated": False,
            "catalog_complete": run_complete,
        },
        "active_count_availability": "available" if status_complete else reason,
        "status_coverage": {
            "complete": status_complete,
            "loaded_count": int(scalar.get("status_count") or 0),
            "total_count": count,
            "truncated": False,
            "catalog_complete": run_complete,
        },
        "ad_squad_bid_strategies": strategies,
        "bid_strategies_availability": "available" if strategy_complete else reason,
        "bid_strategy_coverage": {
            "complete": strategy_complete,
            "loaded_count": int(scalar.get("strategy_count") or 0),
            "total_count": count,
            "truncated": False,
            "catalog_complete": run_complete,
        },
        "shared_ad_squad_bid_strategy": campaign.pop("_shared_properties", None),
    }
    if isinstance(aggregate["shared_ad_squad_bid_strategy"], dict):
        aggregate["shared_ad_squad_bid_strategy"] = aggregate["shared_ad_squad_bid_strategy"].get("shared_ad_squad_bid_strategy")
    campaign["campaign_aggregate"] = aggregate
    campaign["ad_squad_count"] = aggregate["ad_squad_count"]
    campaign["active_ad_squad_count"] = aggregate["active_ad_squad_count"]
    campaign["active_ad_squads_availability"] = aggregate["active_count_availability"]
    campaign["ad_squad_daily_budget_sum_micro"] = budget_micro
    campaign["ad_squad_daily_budget_sum_usd"] = aggregate["daily_budget_sum_usd"]
    campaign["ad_squad_daily_budget_sum_availability"] = aggregate["daily_budget_sum_availability"]
    campaign["ad_squad_bid_strategies"] = strategies
    campaign["ad_squad_bid_strategies_availability"] = aggregate["bid_strategies_availability"]
    campaign["ad_squads_daily_budget_micro"] = budget_micro
    campaign["ad_squads_daily_budget_usd"] = aggregate["daily_budget_sum_usd"]
    campaign["active_ad_squads"] = aggregate["active_ad_squad_count"]
    campaign["shared_ad_squad_bid_strategy"] = aggregate["shared_ad_squad_bid_strategy"]


async def list_financial_management_settings(
    db: Any,
    user_id: str,
    entity_type: Literal["campaign", "ad_squad"] | None = None,
    unified_entity_id: str | None = None,
    parent_unified_id: str | None = None,
    *,
    unified_entity_ids: list[str] | None = None,
    now: datetime | Callable[[], datetime] | None = None,
    limit: int = MAX_SETTINGS_ROWS,
) -> dict[str, Any]:
    """Return one bounded, database-only settings projection for the page."""
    if entity_type is not None and entity_type not in SUPPORTED_ENTITY_TYPES:
        raise ValueError("entity_type must be campaign or ad_squad")
    bounded_limit = max(1, min(int(limit), MAX_SETTINGS_ROWS))
    requested_id = _safe_id(unified_entity_id)
    requested_ids = list(dict.fromkeys(
        value
        for raw in list(unified_entity_ids or [])
        if (value := _safe_id(raw)) is not None
    ))
    if len(requested_ids) > MAX_VISIBLE_SETTINGS_ROWS:
        raise ValueError("visible settings batch cannot exceed 100 entity IDs")
    if requested_id and requested_ids:
        raise ValueError("use unified_entity_id or unified_entity_ids, not both")
    requested_parent_id = _safe_id(parent_unified_id)
    if requested_parent_id and entity_type != "ad_squad":
        raise ValueError("parent_unified_id is only valid for ad_squad settings")
    types = [entity_type] if entity_type else list(SUPPORTED_ENTITY_TYPES)
    query: dict[str, Any] = {
        "user_id": str(user_id),
        "provider": SNAPCHAT_PROVIDER_ID,
        "entity_type": {"$in": types},
        "deleted": {"$ne": True},
    }
    if requested_id:
        query["external_id"] = requested_id
    elif requested_ids:
        query["external_id"] = {"$in": requested_ids}
    if requested_parent_id:
        # The unified Snapchat campaign ID is the provider campaign ID in the
        # existing native entity catalogue. Filtering before limit prevents
        # unrelated campaigns from consuming the bounded Ad Squad page.
        query["campaign_id"] = requested_parent_id

    projection = {
        "_id": 0,
        "user_id": 1,
        "provider": 1,
        "ad_account_id": 1,
        "mezan_integration_account_id": 1,
        "entity_type": 1,
        "external_id": 1,
        "campaign_id": 1,
        "ad_squad_id": 1,
        "display_name": 1,
        "source_mode": 1,
        "last_observed_at": 1,
        "updated_at_provider": 1,
        "provider_snapshot": 1,
    }
    rows, rows_truncated = await _find_rows(
        _collection(db, SNAPCHAT_ENTITY_COLLECTION),
        query,
        projection,
        limit=bounded_limit,
    )

    account_rows, _ = await _find_rows(
        _collection(db, INTEGRATION_ACCOUNTS_COLLECTION),
        {"user_id": str(user_id), "provider": SNAPCHAT_PROVIDER_ID},
        {
            "_id": 0,
            "external_account_id": 1,
            "ad_account_id": 1,
            "currency": 1,
        },
        limit=100,
    )
    accounts = _account_by_provider_id(account_rows)
    latest_run = await _latest_sync_run(db, str(user_id))
    current = _now_value(now)

    items: list[dict[str, Any]] = []
    rows_by_campaign: dict[str, dict[str, Any]] = {}
    for row in rows:
        external_id = _safe_id(row.get("external_id"))
        if external_id is None:
            continue
        item = _base_item(
            row,
            unified_entity_id=external_id,
            account=accounts.get(_safe_id(row.get("ad_account_id")) or ""),
            latest_run=latest_run,
            now=current,
            expected_parent_unified_id=requested_parent_id,
        )
        if item["entity_type"] == "campaign":
            shared, present = _provider_field(row, "shared_properties")
            item["_shared_properties"] = shared if present else None
            rows_by_campaign[external_id] = item
        items.append(item)

    children_truncated = False
    collection = _collection(db, SNAPCHAT_ENTITY_COLLECTION)
    scalar_aggregation = callable(getattr(collection, "aggregate", None))
    if rows_by_campaign and scalar_aggregation:
        child_scalars = await _aggregate_visible_campaign_children(
            db,
            user_id=str(user_id),
            campaigns=rows_by_campaign,
            latest_run=latest_run,
            now=current,
        )
        for campaign_id, campaign in rows_by_campaign.items():
            account_id = _safe_id(campaign.get("ad_account_id")) or ""
            _attach_scalar_campaign_aggregate(
                campaign,
                child_scalars.get((account_id, campaign_id)),
                latest_run=latest_run,
                now=current,
            )
    elif rows_by_campaign:
        campaign_account_ids = sorted(
            {
                account_id
                for campaign in rows_by_campaign.values()
                if (account_id := _safe_id(campaign.get("ad_account_id")))
            }
        )
        child_rows, children_truncated = await _find_rows(
            _collection(db, SNAPCHAT_ENTITY_COLLECTION),
            {
                "user_id": str(user_id),
                "provider": SNAPCHAT_PROVIDER_ID,
                "entity_type": "ad_squad",
                "ad_account_id": {"$in": campaign_account_ids},
                "deleted": {"$ne": True},
            },
            projection,
            limit=MAX_CAMPAIGN_CHILD_ROWS,
        )
        account_rows: dict[str, list[dict[str, Any]]] = {
            account_id: [] for account_id in campaign_account_ids
        }
        for row in child_rows:
            account_id = _safe_id(row.get("ad_account_id"))
            if account_id in account_rows:
                account_rows[account_id].append(row)

        observed_child_rows = _rows_observed_in_latest_run(latest_run, child_rows)
        children_by_campaign: dict[tuple[str, str], list[dict[str, Any]]] = {
            (
                _safe_id(campaign.get("ad_account_id")) or "",
                campaign_id,
            ): []
            for campaign_id, campaign in rows_by_campaign.items()
        }
        for row in observed_child_rows:
            account_id = _safe_id(row.get("ad_account_id")) or ""
            campaign_id = _safe_id(row.get("campaign_id"))
            external_id = _safe_id(row.get("external_id"))
            partition_key = (account_id, campaign_id or "")
            if partition_key not in children_by_campaign or external_id is None:
                continue
            children_by_campaign[partition_key].append(
                _base_item(
                    row,
                    unified_entity_id=external_id,
                    account=accounts.get(_safe_id(row.get("ad_account_id")) or ""),
                    latest_run=latest_run,
                    now=current,
                )
            )
        for campaign_id, campaign in rows_by_campaign.items():
            account_id = _safe_id(campaign.get("ad_account_id"))
            _attach_campaign_aggregate(
                campaign,
                children_by_campaign.get((account_id or "", campaign_id), []),
                truncated=children_truncated,
                catalog_coverage=_ad_squad_catalog_coverage(
                    latest_run,
                    campaign,
                    account_id,
                    account_rows.get(account_id or "", []),
                    truncated=children_truncated,
                ),
            )

    if requested_id and not items:
        items = [
            _missing_item(
                entity_type=entity_type or "campaign",
                unified_entity_id=requested_id,
                expected_parent_unified_id=requested_parent_id,
            )
        ]

    items.sort(
        key=lambda item: (
            0 if item["entity_type"] == "campaign" else 1,
            str(item.get("display_name") or ""),
            str(item.get("unified_entity_id") or ""),
        )
    )
    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "source_collection": SNAPCHAT_ENTITY_COLLECTION,
        "settings_source": "existing_native_entity_settings_sync",
        "provider_write_calls": 0,
        "settings_freshness_threshold_seconds": SETTINGS_FRESHNESS_MAX_AGE_SECONDS,
        "rows_truncated": rows_truncated,
        "children_truncated": children_truncated,
        "requested_parent_unified_id": requested_parent_id,
        "requested_entity_ids": requested_ids,
        "settings_rows_materialized": len(items),
        "child_rows_materialized": 0 if scalar_aggregation else None,
        "items": items,
    }


async def resolve_financial_management_settings(
    db: Any,
    user_id: str,
    entity_type: Literal["campaign", "ad_squad"],
    unified_entity_id: str,
    provider_entity_id: str | None = None,
    parent_unified_id: str | None = None,
    *,
    now: datetime | Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Resolve and verify provider IDs before any financial proposal."""
    requested_id = _safe_id(unified_entity_id)
    if requested_id is None:
        raise ValueError("unified_entity_id is required")
    output = await list_financial_management_settings(
        db,
        str(user_id),
        entity_type,
        requested_id,
        None,
        now=now,
        limit=1,
    )
    item = output["items"][0]
    if item.get("provider_entity_id") is None:
        return _missing_item(
            entity_type=entity_type,
            unified_entity_id=requested_id,
            expected_provider_entity_id=_safe_id(provider_entity_id),
            expected_parent_unified_id=_safe_id(parent_unified_id),
        )

    row = await _collection(db, SNAPCHAT_ENTITY_COLLECTION).find_one(
        {
            "user_id": str(user_id),
            "provider": SNAPCHAT_PROVIDER_ID,
            "entity_type": entity_type,
            "external_id": requested_id,
        },
        {
            "_id": 0,
            "entity_type": 1,
            "external_id": 1,
            "campaign_id": 1,
            "source_mode": 1,
            "provider_snapshot": 1,
        },
    )
    mapping_status = _mapping_status(
        row or {},
        unified_entity_id=requested_id,
        expected_provider_entity_id=_safe_id(provider_entity_id),
        expected_parent_unified_id=_safe_id(parent_unified_id),
    )
    if mapping_status != "verified":
        item["mapping_status"] = mapping_status
        item["mapping_verified"] = False
        item["mapping_source"] = None
        item["quality"]["settings_status"] = _SYNC_FAILED
        item["quality"]["reason"] = (
            "provider_entity_id_mismatch"
            if mapping_status == "mismatch"
            else (
                "provider_parent_id_mismatch"
                if mapping_status == "parent_mismatch"
                else "provider_identity_mapping_unverified"
            )
        )
        item["quality"]["mapping_status"] = mapping_status
        item["quality"]["financial_controls_allowed"] = False
        for control in item["quality"]["financial_field_controls"].values():
            control["allowed"] = False
            control["reason"] = item["quality"]["reason"]
        item["unavailable_message_ar"] = _UNAVAILABLE_AR
    return item


def attach_snapchat_entity_settings_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    """Attach one GET-only endpoint; no provider client is constructed."""

    @router.get(f"/{SNAPCHAT_PROVIDER_ID}/management/entity-settings")
    async def entity_settings(
        entity_type: Literal["campaign", "ad_squad"] = Query(...),
        unified_entity_id: str | None = Query(
            default=None, min_length=1, max_length=128
        ),
        parent_unified_id: str | None = Query(
            default=None, min_length=1, max_length=128
        ),
        unified_entity_ids: str | None = Query(default=None, max_length=13_000),
        limit: int = Query(default=MAX_SETTINGS_ROWS, ge=1, le=MAX_SETTINGS_ROWS),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        raw_requested_ids = unified_entity_ids if isinstance(unified_entity_ids, str) else ""
        requested_ids = [
            value.strip()
            for value in raw_requested_ids.split(",")
            if value.strip()
        ]
        if any(len(value) > 128 for value in requested_ids):
            raise ValueError("unified entity ID cannot exceed 128 characters")
        kwargs: dict[str, Any] = {"limit": limit}
        if requested_ids:
            kwargs["unified_entity_ids"] = requested_ids
        return await list_financial_management_settings(
            db,
            str(owner["id"]),
            entity_type,
            unified_entity_id,
            parent_unified_id,
            **kwargs,
        )


__all__ = [
    "MAX_SETTINGS_ROWS",
    "MAX_VISIBLE_SETTINGS_ROWS",
    "SETTINGS_FRESHNESS_MAX_AGE_SECONDS",
    "SUPPORTED_ENTITY_TYPES",
    "attach_snapchat_entity_settings_routes",
    "bid_semantic_for_strategy",
    "list_financial_management_settings",
    "micro_to_account_currency",
    "micro_to_usd",
    "resolve_financial_management_settings",
]
