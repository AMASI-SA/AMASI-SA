"""Read-only Snapchat performance summaries limited to owner-selected accounts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from .snapchat_account_selection import _load_selected_accounts
from .snapchat_native_data_common import (
    BUSINESS_TIMEZONE,
    SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
    SNAPCHAT_PERFORMANCE_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatNativeSyncInput,
    _collection,
    _timezone,
    _utcnow,
    enumerate_native_sync_dates,
)


async def selected_snapchat_performance_summary(
    db: Any,
    user_id: str,
    *,
    from_date: str | None,
    to_date: str | None,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Aggregate ad-account rows while excluding every unselected account."""
    payload = SnapchatNativeSyncInput(
        from_date=from_date,
        to_date=to_date,
        days=1,
    )
    now_value = now().astimezone(timezone.utc)
    dates = enumerate_native_sync_dates(
        payload,
        today=now_value.astimezone(
            _timezone(BUSINESS_TIMEZONE)
        ).date(),
    )
    selected_accounts = await _load_selected_accounts(db, user_id)
    selected_ids = [
        str(account["ad_account_id"])
        for account in selected_accounts
    ]
    account_meta = {
        str(account["ad_account_id"]): account
        for account in selected_accounts
    }
    date_query = {
        "$gte": dates[0].isoformat(),
        "$lte": dates[-1].isoformat(),
    }
    cursor = _collection(
        db, SNAPCHAT_PERFORMANCE_COLLECTION
    ).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": {"$in": selected_ids},
            "entity_type": "ad_account",
            "date": date_query,
        },
        {
            "_id": 0,
            "ad_account_id": 1,
            "currency": 1,
            "spend_native": 1,
            "spend_sar": 1,
            "purchase_value_native": 1,
            "purchase_value_sar": 1,
        },
    )
    rows = (
        await cursor.to_list(length=5000)
        if hasattr(cursor, "to_list")
        else [row async for row in cursor]
    )

    per_account: dict[str, dict[str, Any]] = {}
    total_spend_sar = 0.0
    total_purchase_value_sar = 0.0
    numeric_fields = (
        "spend_native",
        "spend_sar",
        "purchase_value_native",
        "purchase_value_sar",
    )
    for row in rows:
        account_id = str(row.get("ad_account_id") or "")
        if not account_id:
            continue
        meta = account_meta.get(account_id, {})
        item = per_account.setdefault(
            account_id,
            {
                "account_id": account_id,
                "display_name": meta.get("display_name"),
                "currency": row.get("currency") or meta.get("currency"),
                "timezone": meta.get("timezone"),
                "rows": 0,
                **{field: 0.0 for field in numeric_fields},
            },
        )
        item["rows"] += 1
        for field in numeric_fields:
            value = row.get(field)
            if value is not None:
                item[field] += float(value)
        if row.get("spend_sar") is not None:
            total_spend_sar += float(row["spend_sar"])
        if row.get("purchase_value_sar") is not None:
            total_purchase_value_sar += float(
                row["purchase_value_sar"]
            )

    excluded_rows = await _collection(
        db, SNAPCHAT_PERFORMANCE_COLLECTION
    ).count_documents(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": {"$nin": selected_ids},
            "entity_type": "ad_account",
            "date": date_query,
        }
    )

    accounts: list[dict[str, Any]] = []
    for account_id in selected_ids:
        item = per_account.get(account_id)
        if item is None:
            meta = account_meta[account_id]
            item = {
                "account_id": account_id,
                "display_name": meta.get("display_name"),
                "currency": meta.get("currency"),
                "timezone": meta.get("timezone"),
                "rows": 0,
                **{field: 0.0 for field in numeric_fields},
            }
        for field in numeric_fields:
            item[field] = round(float(item[field]), 6)
        accounts.append(item)

    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "date_from": dates[0].isoformat(),
        "date_to": dates[-1].isoformat(),
        "selected_account_ids": selected_ids,
        "selected_account_count": len(selected_ids),
        "rows_included": len(rows),
        "unselected_rows_excluded": int(excluded_rows),
        "spend_sar": round(total_spend_sar, 6),
        "purchase_value_sar": round(
            total_purchase_value_sar, 6
        ),
        "accounts": accounts,
        "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
        "source_only": True,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def attach_snapchat_native_selected_read_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get(
        f"/{SNAPCHAT_PROVIDER_ID}/performance-summary",
        name="get_selected_snapchat_performance_summary",
    )
    async def read_selected_snapchat_performance_summary(
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        try:
            return await selected_snapchat_performance_summary(
                db,
                str(owner["id"]),
                from_date=from_date,
                to_date=to_date,
            )
        except SnapchatNativeSyncError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "provider": SNAPCHAT_PROVIDER_ID,
                    "status": "failed",
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    "source_only": True,
                    "accounting_write_reached": False,
                    "qoyod_write_reached": False,
                },
            ) from exc


__all__ = [
    "attach_snapchat_native_selected_read_routes",
    "selected_snapchat_performance_summary",
]
