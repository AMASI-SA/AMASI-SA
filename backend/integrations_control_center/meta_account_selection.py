"""Owner-selected Meta ad accounts for native Mezan V2 reporting.

Meta OAuth discovery may return several ad accounts. Mezan syncs reporting only
for accounts explicitly selected by the merchant owner. When exactly one account
is discovered, it is selected automatically. Selection is internal to Mezan and
never changes Meta, campaigns, accounting, Qoyod, or credentials.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .meta_oauth_security import META_PROVIDER_ID

META_ACCOUNT_SELECTION_SOURCE_MODE = "meta_business_account_selection_v2"
MAX_META_SELECTED_ACCOUNTS = 20


class MetaAccountSelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_ids: list[str] = Field(min_length=1, max_length=MAX_META_SELECTED_ACCOUNTS)

    @field_validator("account_ids")
    @classmethod
    def normalize_account_ids(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            account_id = str(raw or "").strip()
            if not account_id:
                raise ValueError("account_ids must not contain empty values")
            if not account_id.startswith("act_"):
                account_id = f"act_{account_id}"
            if len(account_id) > 160:
                raise ValueError("account_id is too long")
            if account_id not in seen:
                seen.add(account_id)
                normalized.append(account_id)
        if not normalized:
            raise ValueError("select at least one Meta ad account")
        return normalized


class MetaSelectableAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    mezan_integration_account_id: str | None = None
    display_name: str | None = None
    currency: str | None = None
    timezone: str | None = None
    business_id: str | None = None
    business_name: str | None = None
    account_status: int | str | None = None
    selected: bool
    selection_status: str
    selected_at: str | None = None


class MetaAccountSelectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    discovered_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    selection_required: bool
    accounts: list[MetaSelectableAccount]
    source_only: bool = True
    provider_write_reached: bool = False
    campaign_write_reached: bool = False
    accounting_write_reached: bool = False
    qoyod_write_reached: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _account_id(row: dict[str, Any]) -> str:
    value = str(row.get("ad_account_id") or row.get("external_account_id") or "").strip()
    if value and not value.startswith("act_"):
        value = f"act_{value}"
    return value


async def _find_discovered_accounts(db: Any, user_id: str) -> list[dict[str, Any]]:
    cursor = db.mezan_integration_accounts_v2.find(
        {
            "user_id": user_id,
            "provider": META_PROVIDER_ID,
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
            "business_id": 1,
            "business_name": 1,
            "account_status": 1,
            "mezan_selected": 1,
            "selection_status": 1,
            "selected_at": 1,
        },
    )
    if hasattr(cursor, "sort"):
        cursor = cursor.sort([("display_name", 1), ("external_account_id", 1)])
    rows = await cursor.to_list(length=200) if hasattr(cursor, "to_list") else [row async for row in cursor]
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
                "mezan_integration_account_id": row.get("mezan_integration_account_id"),
                "display_name": row.get("display_name"),
                "currency": row.get("currency"),
                "timezone": row.get("timezone"),
                "business_id": row.get("business_id"),
                "business_name": row.get("business_name"),
                "account_status": row.get("account_status"),
                "selected": selected,
                "selection_status": row.get("selection_status") or ("selected" if selected else "discovered"),
                "selected_at": row.get("selected_at") if selected else None,
            }
        )
    return output


def _selection_response(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    selected_count = sum(item.get("selected") is True for item in accounts)
    return {
        "provider": META_PROVIDER_ID,
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


async def get_meta_account_selection(db: Any, user_id: str) -> dict[str, Any]:
    return _selection_response(await _find_discovered_accounts(db, user_id))


async def save_meta_account_selection(
    db: Any,
    user_id: str,
    payload: MetaAccountSelectionInput,
) -> dict[str, Any]:
    accounts = await _find_discovered_accounts(db, user_id)
    if not accounts:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "meta_accounts_not_discovered",
                "message": "لا توجد حسابات Meta مكتشفة داخل ميزان.",
            },
        )
    selected_ids = set(payload.account_ids)
    known_ids = {item["account_id"] for item in accounts}
    unknown = sorted(selected_ids - known_ids)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "meta_account_selection_unknown_ids",
                "message": "بعض أرقام حسابات Meta ليست ضمن الحسابات المكتشفة.",
                "unknown_account_ids": unknown[:20],
            },
        )

    now_iso = _now_iso()
    for account in accounts:
        selected = account["account_id"] in selected_ids
        await db.mezan_integration_accounts_v2.update_one(
            {
                "user_id": user_id,
                "provider": META_PROVIDER_ID,
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
        {"user_id": user_id, "provider": META_PROVIDER_ID},
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
    run_id = str(uuid.uuid4())
    await db.mezan_integration_sync_runs_v2.insert_one(
        {
            "run_id": run_id,
            "user_id": user_id,
            "provider": META_PROVIDER_ID,
            "run_type": "meta_account_selection",
            "status": "complete",
            "started_at": now_iso,
            "finished_at": now_iso,
            "source_mode": META_ACCOUNT_SELECTION_SOURCE_MODE,
            "summary": {
                "discovered_count": len(accounts),
                "selected_count": len(selected_ids),
                "selected_account_ids": sorted(selected_ids),
                "legacy_collection_read": False,
                "legacy_collection_write": False,
                "provider_write_reached": False,
                "campaign_write_reached": False,
                "accounting_write_reached": False,
                "qoyod_write_reached": False,
            },
            "error": None,
        }
    )
    return await get_meta_account_selection(db, user_id)


async def load_selected_meta_accounts(db: Any, user_id: str) -> list[dict[str, Any]]:
    cursor = db.mezan_integration_accounts_v2.find(
        {
            "user_id": user_id,
            "provider": META_PROVIDER_ID,
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
            "business_id": 1,
            "business_name": 1,
            "last_sync_at": 1,
        },
    )
    if hasattr(cursor, "sort"):
        cursor = cursor.sort("display_name", 1)
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(MAX_META_SELECTED_ACCOUNTS + 1)
    rows = await cursor.to_list(length=MAX_META_SELECTED_ACCOUNTS + 1) if hasattr(cursor, "to_list") else [row async for row in cursor]
    output: list[dict[str, Any]] = []
    for row in rows:
        account_id = _account_id(row)
        if account_id:
            output.append({**row, "ad_account_id": account_id, "external_account_id": account_id})
    if not output:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "meta_accounts_not_selected",
                "message": "اختر حساب Meta واحدًا على الأقل داخل ميزان قبل المزامنة.",
            },
        )
    if len(output) > MAX_META_SELECTED_ACCOUNTS:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "meta_account_limit_exceeded",
                "message": f"يمكن اختيار {MAX_META_SELECTED_ACCOUNTS} حساب Meta كحد أقصى.",
            },
        )
    return output


def install_meta_selection_projection_preservation() -> None:
    from . import meta_connections as connection_module

    original = connection_module.persist_meta_projection
    if getattr(original, "_mezan_preserves_meta_account_selection", False):
        return

    async def wrapped_projection(
        db: Any,
        *,
        user_id: str,
        token_payload: dict[str, Any],
        debug_data: dict[str, Any],
        discovery: dict[str, Any],
        provider_error: str | None = None,
    ) -> None:
        existing = await _find_discovered_accounts(db, user_id)
        selected_at_by_id = {
            item["account_id"]: item.get("selected_at")
            for item in existing
            if item.get("selected") is True
        }
        await original(
            db,
            user_id=user_id,
            token_payload=token_payload,
            debug_data=debug_data,
            discovery=discovery,
            provider_error=provider_error,
        )
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
                    "provider": META_PROVIDER_ID,
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
                        "selected_at": selected_at_by_id.get(account_id) if selected else None,
                        "selection_updated_at": now_iso,
                    }
                },
            )
        await db.mezan_integrations_v2.update_one(
            {"user_id": user_id, "provider": META_PROVIDER_ID},
            {
                "$set": {
                    "selected_account_count": selected_count,
                    "account_selection_required": bool(refreshed) and selected_count == 0,
                    "account_selection_updated_at": now_iso,
                    "updated_at": now_iso,
                }
            },
            upsert=True,
        )

    wrapped_projection._mezan_preserves_meta_account_selection = True  # type: ignore[attr-defined]
    connection_module.persist_meta_projection = wrapped_projection


def attach_meta_account_selection_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    install_meta_selection_projection_preservation()

    @router.get(
        f"/{META_PROVIDER_ID}/accounts-selection",
        response_model=MetaAccountSelectionResponse,
    )
    async def read_selection(user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        return await get_meta_account_selection(db, str(owner["id"]))

    @router.put(
        f"/{META_PROVIDER_ID}/accounts-selection",
        response_model=MetaAccountSelectionResponse,
    )
    async def write_selection(
        payload: MetaAccountSelectionInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await save_meta_account_selection(db, str(owner["id"]), payload)


__all__ = [
    "MAX_META_SELECTED_ACCOUNTS",
    "MetaAccountSelectionInput",
    "MetaAccountSelectionResponse",
    "attach_meta_account_selection_routes",
    "get_meta_account_selection",
    "load_selected_meta_accounts",
    "save_meta_account_selection",
]
