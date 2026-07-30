"""Owner-selected Snapchat ad accounts for native Mezan V2 operations.

OAuth discovery remains provider-owned and may expose many ad accounts. Mezan only
syncs analytics or diagnoses tracking for accounts explicitly selected by the
merchant owner. Selection is an internal Mezan control-plane mutation; it never
changes Snapchat, credentials, campaigns, events, accounting, or Qoyod.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .snapchat_native_data_common import (
    MAX_SYNC_ACCOUNTS,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    _collection,
)

ACCOUNT_SELECTION_SOURCE_MODE = "snapchat_marketing_account_selection_v2"


class SnapchatAccountSelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_ids: list[str] = Field(
        min_length=1,
        max_length=MAX_SYNC_ACCOUNTS,
    )

    @field_validator("account_ids")
    @classmethod
    def normalize_account_ids(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            account_id = str(item or "").strip()
            if not account_id:
                raise ValueError("account_ids must not contain empty values")
            if len(account_id) > 160:
                raise ValueError("account_id is too long")
            if account_id in seen:
                continue
            seen.add(account_id)
            normalized.append(account_id)
        if not normalized:
            raise ValueError("select at least one Snapchat ad account")
        return normalized


class SnapchatSelectableAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    mezan_integration_account_id: str | None = None
    display_name: str | None = None
    currency: str | None = None
    timezone: str | None = None
    organization_id: str | None = None
    organization_name: str | None = None
    account_status: str | None = None
    selected: bool
    selection_status: str
    selected_at: str | None = None


class SnapchatAccountSelectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    discovered_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    selection_required: bool
    accounts: list[SnapchatSelectableAccount]
    source_only: bool = True
    provider_write_reached: bool = False
    campaign_write_reached: bool = False
    event_write_reached: bool = False
    accounting_write_reached: bool = False
    qoyod_write_reached: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _find_discovered_accounts(db: Any, user_id: str) -> list[dict[str, Any]]:
    cursor = _collection(db, "mezan_integration_accounts_v2").find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
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
            "organization_id": 1,
            "organization_name": 1,
            "account_status": 1,
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
        account_id = str(
            row.get("ad_account_id") or row.get("external_account_id") or ""
        ).strip()
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
                "organization_id": row.get("organization_id"),
                "organization_name": row.get("organization_name"),
                "account_status": row.get("account_status"),
                "selected": selected,
                "selection_status": "selected" if selected else "discovered",
                "selected_at": row.get("selected_at") if selected else None,
            }
        )
    return output


def _selection_response(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    selected_count = sum(item.get("selected") is True for item in accounts)
    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "discovered_count": len(accounts),
        "selected_count": selected_count,
        "selection_required": selected_count == 0,
        "accounts": accounts,
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "event_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


async def get_snapchat_account_selection(db: Any, user_id: str) -> dict[str, Any]:
    accounts = await _find_discovered_accounts(db, user_id)
    return _selection_response(accounts)


async def save_snapchat_account_selection(
    db: Any,
    user_id: str,
    payload: SnapchatAccountSelectionInput,
) -> dict[str, Any]:
    accounts = await _find_discovered_accounts(db, user_id)
    if not accounts:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_accounts_not_discovered",
                "message": "لا توجد حسابات Snapchat مكتشفة داخل ميزان.",
            },
        )

    selected_ids = set(payload.account_ids)
    known_ids = {item["account_id"] for item in accounts}
    unknown = sorted(selected_ids - known_ids)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "snapchat_account_selection_unknown_ids",
                "message": "بعض أرقام حسابات Snapchat ليست ضمن الحسابات المكتشفة.",
                "unknown_account_ids": unknown[:20],
            },
        )

    now_iso = _now_iso()
    collection = _collection(db, "mezan_integration_accounts_v2")
    for account in accounts:
        account_id = account["account_id"]
        selected = account_id in selected_ids
        await collection.update_one(
            {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "external_account_id": account_id,
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

    await _collection(db, "mezan_integrations_v2").update_one(
        {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID},
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
    await _collection(db, "mezan_integration_sync_runs_v2").insert_one(
        {
            "run_id": run_id,
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "run_type": "snapchat_account_selection",
            "status": "complete",
            "started_at": now_iso,
            "finished_at": now_iso,
            "source_mode": ACCOUNT_SELECTION_SOURCE_MODE,
            "summary": {
                "discovered_count": len(accounts),
                "selected_count": len(selected_ids),
                "selected_account_ids": sorted(selected_ids),
                "legacy_collection_read": False,
                "legacy_collection_write": False,
                "provider_write_reached": False,
                "campaign_write_reached": False,
                "event_write_reached": False,
                "accounting_write_reached": False,
                "qoyod_write_reached": False,
            },
            "error": None,
        }
    )
    return await get_snapchat_account_selection(db, user_id)


async def _load_selected_accounts(db: Any, user_id: str) -> list[dict[str, Any]]:
    cursor = _collection(db, "mezan_integration_accounts_v2").find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
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
            "organization_id": 1,
            "organization_name": 1,
            "last_sync_at": 1,
        },
    )
    if hasattr(cursor, "sort"):
        cursor = cursor.sort("display_name", 1)
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(MAX_SYNC_ACCOUNTS + 1)
    rows = (
        await cursor.to_list(length=MAX_SYNC_ACCOUNTS + 1)
        if hasattr(cursor, "to_list")
        else [row async for row in cursor]
    )
    output: list[dict[str, Any]] = []
    for row in rows:
        account_id = str(
            row.get("ad_account_id") or row.get("external_account_id") or ""
        ).strip()
        if account_id:
            output.append({**row, "ad_account_id": account_id})
    if not output:
        raise SnapchatNativeSyncError(
            "snapchat_accounts_not_selected",
            "اختر حساب Snapchat واحدًا على الأقل داخل ميزان قبل التشغيل.",
            status_code=409,
            retryable=False,
        )
    if len(output) > MAX_SYNC_ACCOUNTS:
        raise SnapchatNativeSyncError(
            "snapchat_account_limit_exceeded",
            f"يمكن اختيار {MAX_SYNC_ACCOUNTS} حساب Snapchat كحد أقصى.",
            status_code=409,
            retryable=False,
        )
    return output


def install_snapchat_selected_account_filters() -> None:
    """Require explicit owner selection for analytics and tracking reads."""
    from . import snapchat_native_data_sync as data_module
    from . import snapchat_native_tracking_diagnostics as tracking_module

    current_data = data_module.SnapchatNativeDataSync._accounts
    if not getattr(current_data, "_mezan_selected_accounts_only", False):

        async def selected_data_accounts(self: Any, user_id: str) -> list[dict[str, Any]]:
            return await _load_selected_accounts(self.db, user_id)

        selected_data_accounts._mezan_selected_accounts_only = True  # type: ignore[attr-defined]
        data_module.SnapchatNativeDataSync._accounts = selected_data_accounts

    current_tracking = tracking_module.SnapchatTrackingDiagnostics._accounts
    if not getattr(current_tracking, "_mezan_selected_accounts_only", False):

        async def selected_tracking_accounts(
            self: Any,
            user_id: str,
        ) -> list[dict[str, Any]]:
            return await _load_selected_accounts(self.db, user_id)

        selected_tracking_accounts._mezan_selected_accounts_only = True  # type: ignore[attr-defined]
        tracking_module.SnapchatTrackingDiagnostics._accounts = (
            selected_tracking_accounts
        )


def install_snapchat_selection_projection_preservation() -> None:
    """Preserve the internal selection when OAuth re-discovers account rows."""
    from . import snapchat_connections as connection_module

    original = connection_module.persist_snapchat_projection
    if getattr(original, "_mezan_preserves_account_selection", False):
        return

    async def wrapped_projection(
        db: Any,
        *,
        user_id: str,
        token_payload: dict[str, Any],
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
            discovery=discovery,
            provider_error=provider_error,
        )
        now_iso = _now_iso()
        refreshed = await _find_discovered_accounts(db, user_id)
        collection = _collection(db, "mezan_integration_accounts_v2")
        selected_count = 0
        for account in refreshed:
            account_id = account["account_id"]
            selected = account_id in selected_at_by_id
            selected_count += int(selected)
            await collection.update_one(
                {
                    "user_id": user_id,
                    "provider": SNAPCHAT_PROVIDER_ID,
                    "external_account_id": account_id,
                },
                {
                    "$set": {
                        "mezan_selected": selected,
                        "selection_status": (
                            "selected" if selected else "discovered"
                        ),
                        "selected_at": (
                            selected_at_by_id.get(account_id) or now_iso
                            if selected
                            else None
                        ),
                        "selection_updated_at": now_iso,
                    }
                },
            )
        await _collection(db, "mezan_integrations_v2").update_one(
            {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID},
            {
                "$set": {
                    "selected_account_count": selected_count,
                    "account_selection_required": selected_count == 0,
                    "account_selection_updated_at": now_iso,
                    "updated_at": now_iso,
                }
            },
            upsert=True,
        )

    wrapped_projection._mezan_preserves_account_selection = True  # type: ignore[attr-defined]
    connection_module.persist_snapchat_projection = wrapped_projection


def install_snapchat_selection_snapshot_and_actions() -> None:
    """Expose selection state to action policy without adding it to public cards."""
    from . import service as service_module

    service_class = service_module.IntegrationsControlCenterService
    current_snapshot = service_class._v2_snapshot
    if not getattr(current_snapshot, "_mezan_account_selection_snapshot", False):

        async def wrapped_snapshot(
            self: Any,
            user_id: str,
            definition: Any,
        ):
            result = await current_snapshot(self, user_id, definition)
            if not result or definition.provider != SNAPCHAT_PROVIDER_ID:
                return result
            snapshot, health = result
            selected_count = await _collection(
                self.db,
                "mezan_integration_accounts_v2",
            ).count_documents(
                {
                    "user_id": user_id,
                    "provider": SNAPCHAT_PROVIDER_ID,
                    "connection_provenance": "api_connection",
                    "connection_status": "connected",
                    "mezan_selected": True,
                }
            )
            safe_snapshot = dict(snapshot)
            safe_snapshot["selected_account_count"] = int(selected_count)
            safe_snapshot["account_selection_required"] = selected_count == 0
            return safe_snapshot, health

        wrapped_snapshot._mezan_account_selection_snapshot = True  # type: ignore[attr-defined]
        service_class._v2_snapshot = wrapped_snapshot

    current_actions = service_module._actions
    if getattr(current_actions, "_mezan_account_selection_actions", False):
        return

    def wrapped_actions(definition: Any, snapshot: dict) -> dict:
        actions = current_actions(definition, snapshot)
        if definition.provider != SNAPCHAT_PROVIDER_ID:
            return actions
        selected_count = int(snapshot.get("selected_account_count") or 0)
        if selected_count < 1:
            reason = "اختر حساب Snapchat واحدًا على الأقل داخل ميزان أولًا."
            if "sync_data" in actions:
                actions["sync_data"] = {
                    "enabled": False,
                    "reason": reason,
                    "href": None,
                }
            if "tracking_diagnostics" in actions:
                actions["tracking_diagnostics"] = {
                    "enabled": False,
                    "reason": reason,
                    "href": None,
                }
        return actions

    wrapped_actions._mezan_account_selection_actions = True  # type: ignore[attr-defined]
    service_module._actions = wrapped_actions


def install_snapchat_account_selection() -> None:
    install_snapchat_selection_projection_preservation()
    install_snapchat_selected_account_filters()
    install_snapchat_selection_snapshot_and_actions()


def attach_snapchat_account_selection_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    install_snapchat_account_selection()

    @router.get(
        f"/{SNAPCHAT_PROVIDER_ID}/accounts-selection",
        response_model=SnapchatAccountSelectionResponse,
        name="get_snapchat_account_selection",
    )
    async def get_selection(user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        return await get_snapchat_account_selection(db, str(owner["id"]))

    @router.put(
        f"/{SNAPCHAT_PROVIDER_ID}/accounts-selection",
        response_model=SnapchatAccountSelectionResponse,
        name="save_snapchat_account_selection",
    )
    async def save_selection(
        payload: SnapchatAccountSelectionInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await save_snapchat_account_selection(
            db,
            str(owner["id"]),
            payload,
        )


__all__ = [
    "ACCOUNT_SELECTION_SOURCE_MODE",
    "SnapchatAccountSelectionInput",
    "SnapchatAccountSelectionResponse",
    "attach_snapchat_account_selection_routes",
    "get_snapchat_account_selection",
    "install_snapchat_account_selection",
    "save_snapchat_account_selection",
]
