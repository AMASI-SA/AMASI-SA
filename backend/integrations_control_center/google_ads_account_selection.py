"""Owner-selected Google Ads accounts for native Mezan V2 reporting.

Google OAuth discovery can expose more than one directly accessible customer.
Mezan syncs reporting only for accounts selected by the merchant owner. A
single discovered account is selected automatically. Selection changes Mezan
metadata only and never mutates Google Ads, campaigns, accounting, or Qoyod.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

GOOGLE_ADS_PROVIDER_ID = "google_ads"
GOOGLE_ADS_ACCOUNT_SELECTION_SOURCE_MODE = "google_ads_account_selection_v2"
MAX_GOOGLE_ADS_SELECTED_ACCOUNTS = 20


class GoogleAdsAccountSelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_ids: list[str] = Field(
        min_length=1,
        max_length=MAX_GOOGLE_ADS_SELECTED_ACCOUNTS,
    )

    @field_validator("account_ids")
    @classmethod
    def normalize_account_ids(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            account_id = "".join(ch for ch in str(raw or "") if ch.isdigit())
            if not account_id:
                raise ValueError("account_ids must contain Google Ads customer IDs")
            if len(account_id) > 32:
                raise ValueError("Google Ads customer ID is too long")
            if account_id not in seen:
                seen.add(account_id)
                normalized.append(account_id)
        if not normalized:
            raise ValueError("select at least one Google Ads account")
        return normalized


class GoogleAdsSelectableAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    mezan_integration_account_id: str | None = None
    display_name: str | None = None
    currency: str | None = None
    timezone: str | None = None
    selected: bool
    selection_status: str
    selected_at: str | None = None


class GoogleAdsAccountSelectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    discovered_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    selection_required: bool
    accounts: list[GoogleAdsSelectableAccount]
    source_only: bool = True
    provider_write_reached: bool = False
    campaign_write_reached: bool = False
    accounting_write_reached: bool = False
    qoyod_write_reached: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _account_id(row: dict[str, Any]) -> str:
    return "".join(
        ch
        for ch in str(
            row.get("ad_account_id")
            or row.get("external_account_id")
            or ""
        )
        if ch.isdigit()
    )


async def _find_discovered_accounts(db: Any, user_id: str) -> list[dict[str, Any]]:
    cursor = db.mezan_integration_accounts_v2.find(
        {
            "user_id": user_id,
            "provider": GOOGLE_ADS_PROVIDER_ID,
            "connection_provenance": "api_connection",
            "connection_status": {"$in": ["connected", "needs_reauth"]},
        },
        {
            "_id": 0,
            "mezan_integration_account_id": 1,
            "external_account_id": 1,
            "ad_account_id": 1,
            "display_name": 1,
            "currency": 1,
            "timezone": 1,
            "mezan_selected": 1,
            "selection_status": 1,
            "selected_at": 1,
        },
    )
    if hasattr(cursor, "sort"):
        cursor = cursor.sort([("display_name", 1), ("external_account_id", 1)])
    rows = (
        await cursor.to_list(length=200)
        if hasattr(cursor, "to_list")
        else [row async for row in cursor]
    )
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        account_id = _account_id(row)
        if not account_id or account_id in seen:
            continue
        seen.add(account_id)
        selected = row.get("mezan_selected") is True
        output.append(
            {
                "account_id": account_id,
                "mezan_integration_account_id": row.get(
                    "mezan_integration_account_id"
                ),
                "display_name": row.get("display_name"),
                "currency": row.get("currency"),
                "timezone": row.get("timezone"),
                "selected": selected,
                "selection_status": row.get("selection_status")
                or ("selected" if selected else "discovered"),
                "selected_at": row.get("selected_at") if selected else None,
            }
        )
    return output


def _selection_response(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    selected_count = sum(item.get("selected") is True for item in accounts)
    return {
        "provider": GOOGLE_ADS_PROVIDER_ID,
        "discovered_count": len(accounts),
        "selected_count": selected_count,
        "selection_required": bool(accounts) and selected_count == 0,
        "accounts": accounts,
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


async def get_google_ads_account_selection(
    db: Any,
    user_id: str,
) -> dict[str, Any]:
    return _selection_response(await _find_discovered_accounts(db, user_id))


async def save_google_ads_account_selection(
    db: Any,
    user_id: str,
    payload: GoogleAdsAccountSelectionInput,
) -> dict[str, Any]:
    accounts = await _find_discovered_accounts(db, user_id)
    if not accounts:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "google_ads_accounts_not_discovered",
                "message": "لا توجد حسابات Google Ads مكتشفة داخل ميزان.",
            },
        )
    selected_ids = set(payload.account_ids)
    known_ids = {item["account_id"] for item in accounts}
    unknown = sorted(selected_ids - known_ids)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "google_ads_account_selection_unknown_ids",
                "message": "بعض حسابات Google Ads ليست ضمن الحسابات المكتشفة.",
                "unknown_account_ids": unknown[:20],
            },
        )

    now_iso = _now_iso()
    for account in accounts:
        selected = account["account_id"] in selected_ids
        await db.mezan_integration_accounts_v2.update_one(
            {
                "user_id": user_id,
                "provider": GOOGLE_ADS_PROVIDER_ID,
                "external_account_id": account["account_id"],
            },
            {
                "$set": {
                    "mezan_selected": selected,
                    "selection_status": "selected" if selected else "discovered",
                    "selected_at": now_iso if selected else None,
                    "selection_updated_at": now_iso,
                    "last_observed_at": now_iso,
                }
            },
        )

    await db.mezan_integrations_v2.update_one(
        {"user_id": user_id, "provider": GOOGLE_ADS_PROVIDER_ID},
        {
            "$set": {
                "selected_account_count": len(selected_ids),
                "account_selection_required": False,
                "account_selection_updated_at": now_iso,
                "updated_at": now_iso,
            }
        },
        upsert=True,
    )
    await db.mezan_integration_sync_runs_v2.insert_one(
        {
            "run_id": str(uuid.uuid4()),
            "user_id": user_id,
            "provider": GOOGLE_ADS_PROVIDER_ID,
            "run_type": "google_ads_account_selection",
            "status": "complete",
            "started_at": now_iso,
            "finished_at": now_iso,
            "source_mode": GOOGLE_ADS_ACCOUNT_SELECTION_SOURCE_MODE,
            "summary": {
                "discovered_count": len(accounts),
                "selected_count": len(selected_ids),
                "selected_account_ids": sorted(selected_ids),
                "provider_write_reached": False,
                "campaign_write_reached": False,
                "accounting_write_reached": False,
                "qoyod_write_reached": False,
            },
            "error": None,
        }
    )
    return await get_google_ads_account_selection(db, user_id)


async def load_selected_google_ads_accounts(
    db: Any,
    user_id: str,
) -> list[dict[str, Any]]:
    cursor = db.mezan_integration_accounts_v2.find(
        {
            "user_id": user_id,
            "provider": GOOGLE_ADS_PROVIDER_ID,
            "connection_provenance": "api_connection",
            "connection_status": "connected",
            "mezan_selected": True,
        },
        {
            "_id": 0,
            "mezan_integration_account_id": 1,
            "external_account_id": 1,
            "ad_account_id": 1,
            "display_name": 1,
            "currency": 1,
            "timezone": 1,
            "last_sync_at": 1,
        },
    )
    if hasattr(cursor, "sort"):
        cursor = cursor.sort("display_name", 1)
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(MAX_GOOGLE_ADS_SELECTED_ACCOUNTS + 1)
    rows = (
        await cursor.to_list(length=MAX_GOOGLE_ADS_SELECTED_ACCOUNTS + 1)
        if hasattr(cursor, "to_list")
        else [row async for row in cursor]
    )
    output: list[dict[str, Any]] = []
    for row in rows:
        account_id = _account_id(row)
        if account_id:
            output.append(
                {
                    **row,
                    "ad_account_id": account_id,
                    "external_account_id": account_id,
                }
            )
    if not output:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "google_ads_accounts_not_selected",
                "message": "اختر حساب Google Ads واحدًا على الأقل قبل المزامنة.",
            },
        )
    if len(output) > MAX_GOOGLE_ADS_SELECTED_ACCOUNTS:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "google_ads_account_limit_exceeded",
                "message": (
                    f"يمكن اختيار {MAX_GOOGLE_ADS_SELECTED_ACCOUNTS} "
                    "حساب Google Ads كحد أقصى."
                ),
            },
        )
    return output


async def preserve_google_ads_account_selection(
    db: Any,
    user_id: str,
    persist_projection: Callable[..., Any],
    **projection_kwargs: Any,
) -> None:
    existing = await _find_discovered_accounts(db, user_id)
    selected_at_by_id = {
        item["account_id"]: item.get("selected_at")
        for item in existing
        if item.get("selected") is True
    }
    await persist_projection(db, user_id=user_id, **projection_kwargs)
    refreshed = await _find_discovered_accounts(db, user_id)
    now_iso = _now_iso()
    if not selected_at_by_id and len(refreshed) == 1:
        selected_at_by_id[refreshed[0]["account_id"]] = now_iso
    selected_count = 0
    for account in refreshed:
        account_id = account["account_id"]
        selected = account_id in selected_at_by_id
        selected_count += int(selected)
        await db.mezan_integration_accounts_v2.update_one(
            {
                "user_id": user_id,
                "provider": GOOGLE_ADS_PROVIDER_ID,
                "external_account_id": account_id,
            },
            {
                "$set": {
                    "mezan_selected": selected,
                    "selection_status": (
                        "auto_selected_single"
                        if selected and len(refreshed) == 1
                        else "selected"
                        if selected
                        else "discovered"
                    ),
                    "selected_at": selected_at_by_id.get(account_id)
                    if selected
                    else None,
                    "selection_updated_at": now_iso,
                }
            },
        )
    await db.mezan_integrations_v2.update_one(
        {"user_id": user_id, "provider": GOOGLE_ADS_PROVIDER_ID},
        {
            "$set": {
                "selected_account_count": selected_count,
                "account_selection_required": bool(refreshed)
                and selected_count == 0,
                "account_selection_updated_at": now_iso,
                "updated_at": now_iso,
            }
        },
        upsert=True,
    )


def attach_google_ads_account_selection_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get(
        f"/{GOOGLE_ADS_PROVIDER_ID}/accounts-selection",
        response_model=GoogleAdsAccountSelectionResponse,
    )
    async def read_selection(user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        return await get_google_ads_account_selection(db, str(owner["id"]))

    @router.put(
        f"/{GOOGLE_ADS_PROVIDER_ID}/accounts-selection",
        response_model=GoogleAdsAccountSelectionResponse,
    )
    async def write_selection(
        payload: GoogleAdsAccountSelectionInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await save_google_ads_account_selection(
            db,
            str(owner["id"]),
            payload,
        )


__all__ = [
    "GOOGLE_ADS_PROVIDER_ID",
    "MAX_GOOGLE_ADS_SELECTED_ACCOUNTS",
    "GoogleAdsAccountSelectionInput",
    "GoogleAdsAccountSelectionResponse",
    "attach_google_ads_account_selection_routes",
    "get_google_ads_account_selection",
    "load_selected_google_ads_accounts",
    "preserve_google_ads_account_selection",
    "save_google_ads_account_selection",
]
