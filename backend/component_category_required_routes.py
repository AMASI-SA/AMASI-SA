"""Category-required component writes for Mezan Components V2.

This router deliberately shadows the older component create/update/category routes
when registered first. New services and stock components must belong to at least
one preparation category. Existing unclassified rows remain readable, but any
edit must classify them first. Historical order cost snapshots are untouched.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException

from component_edit_policy import component_cost_metadata
from component_status_policy import (
    COMPONENT_STATUSES,
    COMPONENT_STATUS_ACTIVE,
    COMPONENT_STATUS_INACTIVE,
    component_status,
)
from component_workspace_cost_compat_routes import (
    COMPONENT_CATEGORIES,
    COMPONENT_GROUPS,
)
from product_cost_revision import bump_product_cost_revision
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import AUDIT, BINDINGS, RESOURCES, _now, ensure_indexes
from product_v2_routes import _number, _text


def _unique_ids(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list) else []:
        item_id = _text(value)
        if item_id and item_id not in seen:
            seen.add(item_id)
            result.append(item_id)
    return result


def _kind_fields(
    payload: dict[str, Any],
    current: dict[str, Any] | None = None,
) -> tuple[str, str, bool]:
    current = current or {}
    kind = _text(payload.get("kind")) or _text(current.get("kind")) or "service"
    if kind not in {"service", "stock_component"}:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_component_kind"},
        )
    track_inventory = kind == "stock_component"
    unit = (
        _text(payload.get("unit"))
        or _text(current.get("unit"))
        or ("piece" if track_inventory else "job")
    )
    return kind, unit, track_inventory


def _requires_preparation(
    payload: dict[str, Any],
    *,
    kind: str,
    current: dict[str, Any] | None = None,
) -> bool:
    if kind != "service":
        return False
    if "requires_preparation" in payload:
        return payload.get("requires_preparation") is True
    if current is None:
        # New work services block shipping by default unless explicitly disabled.
        return True
    return bool(current.get("requires_preparation"))


async def _validated_category_ids(
    db: Any,
    *,
    user_id: str,
    values: Any,
) -> list[str]:
    category_ids = _unique_ids(values)
    if not category_ids:
        raise HTTPException(
            status_code=422,
            detail={"code": "component_category_required"},
        )
    rows = await db[COMPONENT_CATEGORIES].find(
        {
            "user_id": user_id,
            "id": {"$in": category_ids},
            "status": {"$ne": "inactive"},
        },
        {"_id": 0, "id": 1},
    ).to_list(length=500)
    found = {_text(row.get("id")) for row in rows}
    missing = [category_id for category_id in category_ids if category_id not in found]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "component_category_not_found",
                "category_ids": missing,
            },
        )
    return category_ids


async def _protect_group_categories(
    db: Any,
    *,
    user_id: str,
    resource_id: str,
    category_ids: list[str],
) -> None:
    protected = await db[COMPONENT_GROUPS].find_one(
        {
            "user_id": user_id,
            "resource_ids": resource_id,
            "category_id": {"$nin": category_ids},
        },
        {"_id": 0, "id": 1, "category_id": 1},
    )
    if protected:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "component_category_used_by_group",
                "group_id": protected.get("id"),
                "category_id": protected.get("category_id"),
            },
        )


def make_component_category_required_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(tags=["Mezan Component Required Category"])

    @router.post("/components-v2", status_code=201)
    async def create_component(
        payload: dict = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await ensure_indexes(db)
        user_id = str(user["id"])
        name = _text(payload.get("name"))
        code = _text(payload.get("code")).upper()
        if not name or not code:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_component"},
            )
        category_ids = await _validated_category_ids(
            db,
            user_id=user_id,
            values=payload.get("category_ids"),
        )
        kind, unit, track_inventory = _kind_fields(payload)
        amount = _number(payload.get("unit_cost"))
        if amount is not None and amount < 0:
            raise HTTPException(status_code=422, detail={"code": "invalid_cost"})
        now = _now()
        row = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "name": name,
            "code": code,
            "kind": kind,
            "unit": unit,
            "category": _text(payload.get("category")) or "other",
            "category_ids": category_ids,
            "description": _text(payload.get("description")),
            "track_inventory": track_inventory,
            "requires_preparation": _requires_preparation(payload, kind=kind),
            "status": COMPONENT_STATUS_ACTIVE,
            **component_cost_metadata(
                track_inventory=track_inventory,
                amount=amount,
            ),
            "created_at": now,
            "updated_at": now,
        }
        try:
            await db[RESOURCES].insert_one(dict(row))
        except Exception as exc:
            if "duplicate" in str(exc).lower():
                raise HTTPException(
                    status_code=409,
                    detail={"code": "component_code_exists"},
                ) from exc
            raise
        row.pop("_id", None)
        return {"ok": True, "resource": row}

    @router.put("/components-v2/{resource_id}")
    async def update_component(
        resource_id: str,
        payload: dict = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await ensure_indexes(db)
        user_id = str(user["id"])
        selector = {"user_id": user_id, "id": _text(resource_id)}
        before = await db[RESOURCES].find_one(selector, {"_id": 0})
        if not before:
            raise HTTPException(
                status_code=404,
                detail={"code": "component_not_found"},
            )
        name = _text(payload.get("name")) or _text(before.get("name"))
        code = (_text(payload.get("code")) or _text(before.get("code"))).upper()
        if not name or not code:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_component"},
            )
        duplicate = await db[RESOURCES].find_one(
            {"user_id": user_id, "code": code, "id": {"$ne": resource_id}},
            {"_id": 1},
        )
        if duplicate:
            raise HTTPException(
                status_code=409,
                detail={"code": "component_code_exists"},
            )
        category_ids = await _validated_category_ids(
            db,
            user_id=user_id,
            values=(
                payload.get("category_ids")
                if "category_ids" in payload
                else before.get("category_ids")
            ),
        )
        await _protect_group_categories(
            db,
            user_id=user_id,
            resource_id=_text(resource_id),
            category_ids=category_ids,
        )
        kind, unit, track_inventory = _kind_fields(payload, before)
        requested_cost = payload.get(
            "unit_cost",
            before.get("initial_unit_cost")
            if before.get("track_inventory")
            else before.get("unit_cost"),
        )
        amount = _number(requested_cost)
        if amount is not None and amount < 0:
            raise HTTPException(status_code=422, detail={"code": "invalid_cost"})
        purchase_cost = (
            _number(before.get("unit_cost"))
            if before.get("cost_source") == "purchase_invoice" and track_inventory
            else None
        )
        now = _now()
        patch = {
            "name": name,
            "code": code,
            "kind": kind,
            "unit": unit,
            "category": (
                _text(payload.get("category"))
                or _text(before.get("category"))
                or "other"
            ),
            "category_ids": category_ids,
            "description": (
                _text(payload.get("description"))
                if "description" in payload
                else _text(before.get("description"))
            ),
            "track_inventory": track_inventory,
            "requires_preparation": _requires_preparation(
                payload,
                kind=kind,
                current=before,
            ),
            **component_cost_metadata(
                track_inventory=track_inventory,
                amount=amount,
                purchase_cost=purchase_cost,
            ),
            "updated_at": now,
        }
        await db[RESOURCES].update_one(selector, {"$set": patch})
        await bump_product_cost_revision(db, user_id)
        option_impacted = await db[BINDINGS].count_documents(
            {"user_id": user_id, "resource_id": resource_id}
        )
        product_impacted = await db[PRODUCT_RESOURCE_BINDINGS].count_documents(
            {"user_id": user_id, "resource_id": resource_id}
        )
        impacted = option_impacted + product_impacted
        await db[AUDIT].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "event_type": "resource_updated",
            "resource_id": resource_id,
            "before": {
                key: before.get(key)
                for key in (
                    "name",
                    "code",
                    "kind",
                    "unit",
                    "category_ids",
                    "unit_cost",
                    "initial_unit_cost",
                    "cost_source",
                    "requires_preparation",
                )
            },
            "after": {
                key: patch.get(key)
                for key in (
                    "name",
                    "code",
                    "kind",
                    "unit",
                    "category_ids",
                    "unit_cost",
                    "initial_unit_cost",
                    "cost_source",
                    "requires_preparation",
                )
            },
            "impacted_bindings": impacted,
            "impacted_option_bindings": option_impacted,
            "impacted_product_bindings": product_impacted,
            "created_at": now,
        })
        saved = await db[RESOURCES].find_one(selector, {"_id": 0})
        return {"ok": True, "resource": saved, "impacted_bindings": impacted}

    @router.put("/components-v2/{resource_id}/status")
    async def update_component_status(
        resource_id: str,
        payload: dict = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        """Soft-stop or reactivate a component without deleting history."""
        user_id = str(user["id"])
        resource_id = _text(resource_id)
        requested_status = _text(payload.get("status")).lower()
        if requested_status not in COMPONENT_STATUSES:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_component_status"},
            )
        selector = {"user_id": user_id, "id": resource_id}
        before = await db[RESOURCES].find_one(selector, {"_id": 0})
        if not before:
            raise HTTPException(
                status_code=404,
                detail={"code": "component_not_found"},
            )
        previous_status = component_status(before)
        now = _now()
        patch: dict[str, Any] = {
            "status": requested_status,
            "updated_at": now,
            "status_updated_at": now,
            "status_updated_by": str(user.get("id") or ""),
        }
        update: dict[str, Any] = {"$set": patch}
        if requested_status == COMPONENT_STATUS_INACTIVE:
            patch.update({
                "stopped_at": now,
                "stopped_by": str(user.get("id") or ""),
                "stop_reason": _text(payload.get("reason")),
            })
        else:
            update["$unset"] = {
                "stopped_at": "",
                "stopped_by": "",
                "stop_reason": "",
            }
        await db[RESOURCES].update_one(selector, update)
        await bump_product_cost_revision(db, user_id)
        option_impacted = await db[BINDINGS].count_documents(
            {"user_id": user_id, "resource_id": resource_id}
        )
        product_impacted = await db[PRODUCT_RESOURCE_BINDINGS].count_documents(
            {"user_id": user_id, "resource_id": resource_id}
        )
        impacted = option_impacted + product_impacted
        await db[AUDIT].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "event_type": "resource_status_changed",
            "resource_id": resource_id,
            "before": previous_status,
            "after": requested_status,
            "reason": _text(payload.get("reason")),
            "impacted_bindings": impacted,
            "impacted_option_bindings": option_impacted,
            "impacted_product_bindings": product_impacted,
            "historical_order_snapshots_unchanged": True,
            "created_at": now,
        })
        saved = await db[RESOURCES].find_one(selector, {"_id": 0})
        return {
            "ok": True,
            "resource": saved,
            "status": requested_status,
            "changed": previous_status != requested_status,
            "impacted_bindings": impacted,
            "historical_order_snapshots_unchanged": True,
        }

    @router.put("/components-v2/{resource_id}/categories")
    async def assign_resource_categories(
        resource_id: str,
        payload: dict = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = str(user["id"])
        resource_id = _text(resource_id)
        resource = await db[RESOURCES].find_one(
            {"user_id": user_id, "id": resource_id},
            {"_id": 0},
        )
        if not resource:
            raise HTTPException(
                status_code=404,
                detail={"code": "component_not_found"},
            )
        category_ids = await _validated_category_ids(
            db,
            user_id=user_id,
            values=payload.get("category_ids"),
        )
        await _protect_group_categories(
            db,
            user_id=user_id,
            resource_id=resource_id,
            category_ids=category_ids,
        )
        await db[RESOURCES].update_one(
            {"user_id": user_id, "id": resource_id},
            {"$set": {"category_ids": category_ids, "updated_at": _now()}},
        )
        return {
            "ok": True,
            "resource_id": resource_id,
            "category_ids": category_ids,
        }

    return router


__all__ = [
    "_requires_preparation",
    "_unique_ids",
    "make_component_category_required_router",
]
