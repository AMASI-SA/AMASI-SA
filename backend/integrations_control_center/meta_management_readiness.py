"""Read-only Meta campaign-management readiness checks.

This module deliberately performs no provider mutation.  It proves the native
V2 credential, granted scopes, readable ad accounts and the current user's ad
account tasks before Mezan exposes any campaign-management action.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from fastapi import Depends, HTTPException, Query

from .meta_account_selection import get_meta_account_selection
from .meta_native_reporting import _credential
from .meta_oauth_security import (
    debug_meta_token,
    meta_appsecret_proof,
    meta_graph_base,
)

REQUIRED_SCOPES = frozenset({"ads_read", "ads_management", "business_management"})
WRITE_TASKS = frozenset({"MANAGE", "ADVERTISE"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_error(response: httpx.Response) -> str:
    try:
        payload = response.json() or {}
        error = payload.get("error") or {}
        code = error.get("code")
        subcode = error.get("error_subcode")
        return ":".join(str(item) for item in (code, subcode) if item is not None) or f"http_{response.status_code}"
    except Exception:  # noqa: BLE001
        return f"http_{response.status_code}"


async def _graph_get(
    client: httpx.AsyncClient,
    access_token: str,
    path: str,
    *,
    fields: str,
    extra_params: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    params = {
        "access_token": access_token,
        "appsecret_proof": meta_appsecret_proof(access_token),
        "fields": fields,
        "limit": 100,
    }
    params.update(extra_params or {})
    response = await client.get(
        f"{meta_graph_base()}/{path.lstrip('/')}",
        params=params,
    )
    if response.status_code >= 400:
        return None, _safe_error(response)
    payload = response.json() or {}
    return payload if isinstance(payload, dict) else None, None


async def _graph_list(
    client: httpx.AsyncClient,
    access_token: str,
    path: str,
    *,
    fields: str,
    limit: int = 500,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    after: str | None = None
    for _ in range(10):
        params = {
            "access_token": access_token,
            "appsecret_proof": meta_appsecret_proof(access_token),
            "fields": fields,
            "limit": min(100, max(1, limit - len(rows))),
        }
        if after:
            params["after"] = after
        response = await client.get(
            f"{meta_graph_base()}/{path.lstrip('/')}", params=params
        )
        if response.status_code >= 400:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "meta_management_read_failed",
                    "message": "تعذر قراءة كيانات الحساب الإعلاني من Meta.",
                    "provider_code": _safe_error(response),
                },
            )
        payload = response.json() or {}
        page_rows = payload.get("data") or []
        rows.extend(row for row in page_rows if isinstance(row, dict))
        if len(rows) >= limit:
            break
        next_after = str((((payload.get("paging") or {}).get("cursors") or {}).get("after")) or "").strip()
        if not next_after or next_after == after:
            break
        after = next_after
    return rows[:limit]


def _tasks_for_user(payload: dict[str, Any] | None, external_user_id: str) -> list[str]:
    rows = (payload or {}).get("data") or []
    for row in rows:
        if str(row.get("id") or "") == external_user_id:
            granted = list(row.get("tasks") or []) + list(row.get("permitted_tasks") or [])
            return sorted({str(task).upper() for task in granted if task})
    return []


async def inspect_meta_management_readiness(db: Any, user_id: str) -> dict[str, Any]:
    access_token = await _credential(db, user_id, _now())
    debug = await debug_meta_token(access_token)
    scopes = sorted({str(scope) for scope in debug.get("scopes") or [] if scope})
    missing_scopes = sorted(REQUIRED_SCOPES - set(scopes))
    external_user_id = str(debug.get("user_id") or "")
    selection = await get_meta_account_selection(db, user_id)
    accounts = [row for row in selection.get("accounts") or [] if row.get("selected")]
    if not accounts:
        accounts = list(selection.get("accounts") or [])

    account_results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        for account in accounts:
            account_id = str(account.get("account_id") or "")
            metadata, metadata_error = await _graph_get(
                client,
                access_token,
                account_id,
                fields="id,name,account_status,disable_reason,currency,timezone_name,business{id,name}",
            )
            business_id = str(((metadata or {}).get("business") or {}).get("id") or "").strip()
            assigned, assigned_error = await _graph_get(
                client,
                access_token,
                f"{account_id}/assigned_users",
                fields="id,name,tasks,user_type,permitted_tasks",
                extra_params={"business": business_id} if business_id else None,
            )
            tasks = _tasks_for_user(assigned, external_user_id)
            role_verified = bool(tasks)
            write_task_present = bool(WRITE_TASKS.intersection(tasks))
            active = str((metadata or {}).get("account_status") or account.get("account_status") or "") == "1"
            readable = metadata is not None
            ready = not missing_scopes and readable and active and role_verified and write_task_present
            account_results.append(
                {
                    "account_id": account_id,
                    "display_name": (metadata or {}).get("name") or account.get("display_name") or account_id,
                    "currency": (metadata or {}).get("currency") or account.get("currency"),
                    "timezone": (metadata or {}).get("timezone_name") or account.get("timezone"),
                    "account_status": (metadata or {}).get("account_status") or account.get("account_status"),
                    "disable_reason": (metadata or {}).get("disable_reason"),
                    "business_id": business_id or None,
                    "readable": readable,
                    "role_verified": role_verified,
                    "tasks": tasks,
                    "write_task_present": write_task_present,
                    "ready": ready,
                    "errors": [item for item in (metadata_error, assigned_error) if item],
                }
            )

    write_ready = any(row["ready"] for row in account_results)
    capabilities = {
        "campaign_status_update": write_ready,
        "adset_status_update": write_ready,
        "ad_status_update": write_ready,
        "campaign_budget_update": write_ready,
        "adset_budget_update": write_ready,
        "adset_bid_update": write_ready,
        "campaign_create": write_ready,
        "campaign_clone": write_ready,
    }
    return {
        "provider": "meta_ads",
        "checked_at": _now().isoformat(),
        "token_valid": bool(debug.get("is_valid")),
        "scopes": scopes,
        "missing_scopes": missing_scopes,
        "accounts": account_results,
        "capabilities": capabilities,
        "write_ready": write_ready,
        "read_only_check": True,
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


async def read_meta_campaign_hierarchy(
    db: Any,
    user_id: str,
    account_id: str,
) -> dict[str, Any]:
    selection = await get_meta_account_selection(db, user_id)
    allowed = {
        str(row.get("account_id") or "")
        for row in selection.get("accounts") or []
    }
    normalized = account_id if account_id.startswith("act_") else f"act_{account_id}"
    if normalized not in allowed:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "meta_account_not_discovered",
                "message": "الحساب الإعلاني غير موجود ضمن حسابات Meta المكتشفة في ميزان.",
            },
        )
    access_token = await _credential(db, user_id, _now())
    async with httpx.AsyncClient(timeout=25.0) as client:
        campaigns = await _graph_list(
            client,
            access_token,
            f"{normalized}/campaigns",
            fields=(
                "id,name,status,effective_status,objective,buying_type,"
                "daily_budget,lifetime_budget,budget_remaining,start_time,stop_time"
            ),
        )
        adsets = await _graph_list(
            client,
            access_token,
            f"{normalized}/adsets",
            fields=(
                "id,name,campaign_id,status,effective_status,daily_budget,"
                "lifetime_budget,bid_amount,bid_strategy,billing_event,"
                "optimization_goal,start_time,end_time"
            ),
        )
        ads = await _graph_list(
            client,
            access_token,
            f"{normalized}/ads",
            fields="id,name,campaign_id,adset_id,status,effective_status,creative{id,name}",
        )
    return {
        "provider": "meta_ads",
        "account_id": normalized,
        "campaigns": campaigns,
        "adsets": adsets,
        "ads": ads,
        "counts": {
            "campaigns": len(campaigns),
            "adsets": len(adsets),
            "ads": len(ads),
        },
        "fetched_at": _now().isoformat(),
        "read_only_check": True,
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }
def attach_meta_management_readiness_routes(
    router: Any,
    db: Any,
    current_user: Callable,
    require_access: Callable[[Any], dict],
) -> None:
    @router.get("/meta_ads/management-readiness")
    async def meta_management_readiness(user: dict = Depends(current_user)) -> dict:
        principal = require_access(user)
        return await inspect_meta_management_readiness(db, str(principal["id"]))

    @router.get("/meta_ads/management-hierarchy")
    async def meta_management_hierarchy(
        account_id: str = Query(min_length=4, max_length=160),
        user: dict = Depends(current_user),
    ) -> dict:
        principal = require_access(user)
        return await read_meta_campaign_hierarchy(
            db, str(principal["id"]), account_id
        )


__all__ = [
    "attach_meta_management_readiness_routes",
    "inspect_meta_management_readiness",
    "read_meta_campaign_hierarchy",
]
