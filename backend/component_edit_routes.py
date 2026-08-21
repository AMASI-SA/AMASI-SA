"""Editable shared components with explicit stock-cost authority."""
from __future__ import annotations

import uuid
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException

from component_edit_policy import component_cost_metadata
from product_cost_revision import bump_product_cost_revision
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import AUDIT, BINDINGS, RESOURCES, _now, ensure_indexes
from product_v2_routes import _number, _text


def _kind_fields(payload: dict[str, Any], current: dict[str, Any] | None = None) -> tuple[str, str, bool]:
    current = current or {}
    kind = _text(payload.get("kind")) or _text(current.get("kind")) or "service"
    if kind not in {"service", "stock_component"}:
        raise HTTPException(status_code=422, detail={"code": "invalid_component_kind"})
    track_inventory = kind == "stock_component"
    unit = _text(payload.get("unit")) or _text(current.get("unit")) or ("piece" if track_inventory else "job")
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
    return bool((current or {}).get("requires_preparation"))


def make_component_edit_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(tags=["Mezan Component Editing"])

    @router.post("/components-v2")
    async def create_component(payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        await ensure_indexes(db)
        user_id = str(user["id"])
        name = _text(payload.get("name"))
        code = _text(payload.get("code")).upper()
        if not name or not code:
            raise HTTPException(status_code=422, detail={"code": "invalid_component"})
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
            "description": _text(payload.get("description")),
            "track_inventory": track_inventory,
            "requires_preparation": _requires_preparation(
                payload,
                kind=kind,
            ),
            **component_cost_metadata(track_inventory=track_inventory, amount=amount),
            "created_at": now,
            "updated_at": now,
        }
        try:
            await db[RESOURCES].insert_one(row)
        except Exception as exc:
            if "duplicate" in str(exc).lower():
                raise HTTPException(status_code=409, detail={"code": "component_code_exists"}) from exc
            raise
        saved = dict(row)
        saved.pop("_id", None)
        return {"ok": True, "resource": saved}

    @router.put("/components-v2/{resource_id}")
    async def update_component(resource_id: str, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        await ensure_indexes(db)
        user_id = str(user["id"])
        before = await db[RESOURCES].find_one({"user_id": user_id, "id": resource_id}, {"_id": 0})
        if not before:
            raise HTTPException(status_code=404, detail={"code": "component_not_found"})
        name = _text(payload.get("name")) or _text(before.get("name"))
        code = (_text(payload.get("code")) or _text(before.get("code"))).upper()
        if not name or not code:
            raise HTTPException(status_code=422, detail={"code": "invalid_component"})
        duplicate = await db[RESOURCES].find_one({"user_id": user_id, "code": code, "id": {"$ne": resource_id}}, {"_id": 1})
        if duplicate:
            raise HTTPException(status_code=409, detail={"code": "component_code_exists"})
        kind, unit, track_inventory = _kind_fields(payload, before)
        requested_cost = payload.get("unit_cost", before.get("initial_unit_cost") if before.get("track_inventory") else before.get("unit_cost"))
        amount = _number(requested_cost)
        if amount is not None and amount < 0:
            raise HTTPException(status_code=422, detail={"code": "invalid_cost"})
        purchase_cost = _number(before.get("unit_cost")) if before.get("cost_source") == "purchase_invoice" and track_inventory else None
        now = _now()
        patch = {
            "name": name,
            "code": code,
            "kind": kind,
            "unit": unit,
            "category": _text(payload.get("category")) or _text(before.get("category")) or "other",
            "description": _text(payload.get("description")) if "description" in payload else _text(before.get("description")),
            "track_inventory": track_inventory,
            "requires_preparation": _requires_preparation(
                payload,
                kind=kind,
                current=before,
            ),
            **component_cost_metadata(track_inventory=track_inventory, amount=amount, purchase_cost=purchase_cost),
            "updated_at": now,
        }
        await db[RESOURCES].update_one({"user_id": user_id, "id": resource_id}, {"$set": patch})
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
            "before": {key: before.get(key) for key in ("name", "code", "kind", "unit", "unit_cost", "initial_unit_cost", "cost_source", "requires_preparation")},
            "after": {key: patch.get(key) for key in ("name", "code", "kind", "unit", "unit_cost", "initial_unit_cost", "cost_source", "requires_preparation")},
            "impacted_bindings": impacted,
            "impacted_option_bindings": option_impacted,
            "impacted_product_bindings": product_impacted,
            "created_at": now,
        })
        saved = await db[RESOURCES].find_one({"user_id": user_id, "id": resource_id}, {"_id": 0})
        return {"ok": True, "resource": saved, "impacted_bindings": impacted}

    @router.put("/components-v2/{resource_id}/cost")
    async def update_component_cost(resource_id: str, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        amount = _number(payload.get("amount"))
        if amount is None or amount < 0:
            raise HTTPException(status_code=422, detail={"code": "invalid_cost"})
        before = await db[RESOURCES].find_one({"user_id": user_id, "id": resource_id}, {"_id": 0})
        if not before:
            raise HTTPException(status_code=404, detail={"code": "component_not_found"})
        if before.get("track_inventory") and before.get("cost_source") == "purchase_invoice" and before.get("cost_authoritative"):
            raise HTTPException(status_code=409, detail={"code": "purchase_cost_authoritative"})
        metadata = component_cost_metadata(track_inventory=bool(before.get("track_inventory")), amount=amount)
        now = _now()
        await db[RESOURCES].update_one({"user_id": user_id, "id": resource_id}, {"$set": {**metadata, "updated_at": now}})
        await bump_product_cost_revision(db, user_id)
        impacted = (
            await db[BINDINGS].count_documents(
                {"user_id": user_id, "resource_id": resource_id}
            )
            + await db[PRODUCT_RESOURCE_BINDINGS].count_documents(
                {"user_id": user_id, "resource_id": resource_id}
            )
        )
        await db[AUDIT].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "event_type": "resource_cost_changed",
            "resource_id": resource_id,
            "before": before.get("unit_cost"),
            "after": metadata.get("unit_cost"),
            "cost_source": metadata.get("cost_source"),
            "impacted_bindings": impacted,
            "created_at": now,
        })
        return {"ok": True, "amount": metadata.get("unit_cost"), "cost_source": metadata.get("cost_source"), "impacted_bindings": impacted}

    from resource_catalog_mobile_routes import make_resource_catalog_mobile_router
    mobile_catalog_router = make_resource_catalog_mobile_router(db, current_user)
    router.routes.extend(mobile_catalog_router.routes)
    return router
