"""Governed employee and AI-store operational access for Mezan OS.

Authentication and user creation remain owned by the existing `/team/users`
service. This module adds an independent store-operations role to each existing
user, plus an append-only audit trail. It never stores passwords or tokens.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from ai_store_access_contract import (
    AI_ACTION_LOG,
    PERMISSIONS,
    RESPONSIBILITY_TYPES,
    ROLE_ASSIGNMENTS,
    ROLE_CATALOG,
    ROLE_LABELS,
    effective_permissions,
    validate_assignment,
)
from store_courier_dispatch_routes import make_store_courier_dispatch_router
from warehouse_location_routes import WAREHOUSES


AI_AGENT_ID = "mezan-ai-product-optimizer"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_owner(user: dict[str, Any]) -> None:
    if str(user.get("role") or "").lower() != "owner" and not user.get("is_owner"):
        raise HTTPException(status_code=403, detail={"code": "owner_required"})


async def _audit(db: Any, *, actor: dict[str, Any], action: str, target_id: str, before: Any, after: Any) -> None:
    await db[AI_ACTION_LOG].insert_one({
        "id": uuid.uuid4().hex,
        "actor_type": "human",
        "actor_id": str(actor.get("id") or ""),
        "actor_name": actor.get("name"),
        "action": action,
        "target_type": "store_operations_access",
        "target_id": target_id,
        "before": before,
        "after": after,
        "status": "completed",
        "occurred_at": _now(),
    })


def make_ai_store_access_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/ai-store-operations/access", tags=["AI Store Operations Access"])

    @router.get("")
    async def list_access(user: dict = Depends(current_user)) -> dict[str, Any]:
        _require_owner(user)
        owner_id = str(user.get("id") or "")
        users = await db.users.find(
            {"$or": [{"id": owner_id}, {"created_by": owner_id}]},
            {"_id": 0, "password_hash": 0, "security_answer_hash": 0},
        ).sort("created_at", -1).to_list(5000)
        user_ids = [str(row.get("id")) for row in users if row.get("id")]
        assignments = await db[ROLE_ASSIGNMENTS].find(
            {"user_id": {"$in": user_ids}},
            {"_id": 0},
        ).to_list(5000)
        warehouses = await db[WAREHOUSES].find(
            {"user_id": owner_id, "status": {"$ne": "disabled"}},
            {
                "_id": 0,
                "id": 1,
                "name": 1,
                "code": 1,
                "city": 1,
                "is_primary": 1,
            },
        ).sort([("is_primary", -1), ("created_at", 1)]).to_list(500)
        by_user = {str(row.get("user_id")): row for row in assignments}
        rows = []
        for member in users:
            assignment = by_user.get(str(member.get("id")))
            rows.append({
                "id": member.get("id"),
                "name": member.get("name"),
                "email": member.get("email"),
                "account_role": member.get("role"),
                "is_owner": str(member.get("role") or "").lower() == "owner",
                "assignment": assignment,
                "effective_permissions": effective_permissions(assignment),
            })
        return {
            "ok": True,
            "users": rows,
            "role_catalog": ROLE_CATALOG,
            "role_labels": ROLE_LABELS,
            "permissions": sorted(PERMISSIONS),
            "warehouses": warehouses,
            "fulfillment_responsibility_types": sorted(
                RESPONSIBILITY_TYPES
            ),
            "ai_agent": {
                "id": AI_AGENT_ID,
                "name": "Mezan AI Product Optimizer",
                "role_key": "ai_product_optimizer",
                "effective_permissions": ROLE_CATALOG["ai_product_optimizer"],
                "high_risk_execution": False,
                "publishing": False,
            },
        }

    @router.put("/{target_user_id}")
    async def save_access(target_user_id: str, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        _require_owner(user)
        owner_id = str(user.get("id") or "")
        target = await db.users.find_one(
            {
                "id": target_user_id,
                "$or": [
                    {"id": owner_id},
                    {"created_by": owner_id},
                ],
            },
            {
                "_id": 0,
                "id": 1,
                "name": 1,
                "email": 1,
                "role": 1,
                "created_by": 1,
            },
        )
        if not target:
            raise HTTPException(status_code=404, detail={"code": "team_user_not_found"})
        try:
            normalized = validate_assignment(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
        if normalized["role_key"] == "owner" and str(target.get("role") or "").lower() != "owner":
            raise HTTPException(status_code=422, detail={"code": "operational_owner_requires_account_owner"})
        if normalized["warehouse_ids"]:
            found = await db[WAREHOUSES].count_documents({
                "user_id": owner_id,
                "id": {"$in": normalized["warehouse_ids"]},
                "status": {"$ne": "disabled"},
            })
            if found != len(normalized["warehouse_ids"]):
                raise HTTPException(
                    status_code=422,
                    detail={"code": "warehouse_assignment_invalid"},
                )
        before = await db[ROLE_ASSIGNMENTS].find_one({"user_id": target_user_id}, {"_id": 0})
        now = _now()
        document = {
            "id": (before or {}).get("id") or uuid.uuid4().hex,
            "user_id": target_user_id,
            "user_name": target.get("name"),
            "user_email": target.get("email"),
            **normalized,
            "effective_permissions": effective_permissions(normalized),
            "updated_at": now,
            "updated_by": str(user.get("id") or ""),
        }
        if not before:
            document["created_at"] = now
            document["created_by"] = str(user.get("id") or "")
        await db[ROLE_ASSIGNMENTS].update_one({"user_id": target_user_id}, {"$set": document}, upsert=True)
        await _audit(db, actor=user, action="store_operations_role_assigned", target_id=target_user_id, before=before, after=document)
        return {"ok": True, "assignment": document}

    @router.get("/audit/log")
    async def audit_log(limit: int = Query(100, ge=1, le=500), user: dict = Depends(current_user)) -> dict[str, Any]:
        _require_owner(user)
        rows = await db[AI_ACTION_LOG].find(
            {"target_type": "store_operations_access"}, {"_id": 0}
        ).sort("occurred_at", -1).limit(limit).to_list(limit)
        return {"ok": True, "items": rows, "total": len(rows)}

    router.include_router(
        make_store_courier_dispatch_router(db, current_user)
    )
    return router