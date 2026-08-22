"""Per-item export controls for the stage-one review workflow.

These controls never delete Salla data. They only decide which reviewed
fields/items are exported to a supplier preparation file and which items are
routed directly to Mezan's internal preparation queue.
"""
from __future__ import annotations

from typing import Any, Callable, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order
from order_item_engine.mapper import map_order_item_identities
from order_review_routes import (
    EVENTS,
    REVIEW_COMPLETED_STAGES,
    WORKFLOWS,
    _merchant_user_id,
    _normalized,
    _now,
    _require_reviewer,
    _state_map,
    _text,
)


SUPPLIER_FILE_ROUTE = "supplier_file"
INTERNAL_PREPARATION_ROUTE = "internal_preparation"
DIRECT_ASSEMBLY_ROUTE = "direct_assembly"
ASSIGNMENT_DEFAULTS = "order_review_preparation_assignment_defaults"
PREPARATION_MANAGE_DEFAULT_ROLES = {"owner", "admin", "operations"}


class ReviewExportControlPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manual_hidden_spec_keys: Optional[list[str]] = Field(
        default=None,
        max_length=60,
    )
    preparation_route: Optional[
        Literal["supplier_file", "internal_preparation", "direct_assembly"]
    ] = None
    assigned_employee_id: Optional[str] = Field(default=None, max_length=120)
    # The requested behavior is persistent by default. A reviewer may explicitly
    # disable this for a one-order override.
    save_assignment_as_default: bool = True


def normalize_spec_keys(values: Any) -> list[str]:
    return sorted({
        _normalized(value)
        for value in (values or [])
        if _text(value)
    })


def operational_excluded_keys(
    operational_items: list[dict[str, Any]],
    order_item_id: str,
) -> list[str]:
    return sorted({
        _normalized(spec.get("key") or spec.get("name"))
        for row in operational_items
        if _text(row.get("source_order_item_id")) == order_item_id
        for spec in (row.get("linked_specs") or [])
        if isinstance(spec, dict) and _text(spec.get("key") or spec.get("name"))
    })


def preparation_assignment_product_key(item: Any) -> str:
    """Stable product-level identity used for future-order assignment defaults."""
    source = getattr(item, "source", None)
    product_id = _text(
        getattr(item, "product_id", None)
        or getattr(item, "parent_product_id", None)
        or getattr(source, "source_product_id", None)
    )
    if product_id:
        return f"product:{product_id}"

    sku = _normalized(getattr(item, "sku", None))
    if sku:
        return f"sku:{sku}"

    name = _normalized(getattr(item, "name", None))
    if name:
        return f"name:{name}"

    return f"order-item:{_text(getattr(item, 'order_item_id', None))}"


def user_can_manage_preparation(user_doc: dict[str, Any]) -> bool:
    """Resolve the existing preparation.manage permission without importing server."""
    role = _text(user_doc.get("role")).lower() or "viewer"
    allowed = role in PREPARATION_MANAGE_DEFAULT_ROLES
    if "preparation.manage" in set(user_doc.get("extra_permissions") or []):
        allowed = True
    if "preparation.manage" in set(user_doc.get("denied_permissions") or []):
        allowed = False
    return allowed


def assignable_employee_view(user_doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _text(user_doc.get("id")),
        "name": _text(user_doc.get("name")) or _text(user_doc.get("email")),
        "email": _text(user_doc.get("email")),
        "role": _text(user_doc.get("role")) or "viewer",
    }


def resolve_responsible_employee_id(
    *,
    target_route: str,
    payload_employee_id: Any,
    current_employee_id: Any,
    default_employee_id: Any,
    eligible_employee_ids: set[str],
) -> Optional[str]:
    """Require an eligible responsible employee for every internal item."""
    if target_route != INTERNAL_PREPARATION_ROUTE:
        return None

    employee_id = (
        _text(payload_employee_id)
        or _text(current_employee_id)
        or _text(default_employee_id)
    )
    if not employee_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "responsible_employee_required",
                "message": "يجب تحديد الموظف المسؤول قبل توجيه المنتج للتجهيز.",
            },
        )
    if employee_id not in eligible_employee_ids:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "responsible_employee_unavailable",
                "message": (
                    "الموظف المحدد غير متاح أو لا يملك صلاحية إدارة التجهيز. "
                    "اختر موظفًا آخر."
                ),
            },
        )
    return employee_id


def item_export_control_view(
    state: Optional[dict[str, Any]],
    *,
    operational_items: list[dict[str, Any]],
    order_item_id: str,
    product_key: Optional[str] = None,
    default_assignment: Optional[dict[str, Any]] = None,
    employees_by_id: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, Any]:
    state = state or {}
    employees_by_id = employees_by_id or {}
    manual = normalize_spec_keys(
        state.get("manual_supplier_export_excluded_spec_keys")
    )
    operational = operational_excluded_keys(operational_items, order_item_id)
    hidden = sorted(set(manual) | set(operational))
    default_route = _text((default_assignment or {}).get("preparation_route"))
    route = (
        _text(state.get("preparation_route"))
        or default_route
        or SUPPLIER_FILE_ROUTE
    )
    if route not in {
        SUPPLIER_FILE_ROUTE,
        INTERNAL_PREPARATION_ROUTE,
        DIRECT_ASSEMBLY_ROUTE,
    }:
        route = SUPPLIER_FILE_ROUTE
    supplier_export = (
        bool(state.get("supplier_export"))
        if "supplier_export" in state
        else route == SUPPLIER_FILE_ROUTE
    )

    state_employee_id = _text(state.get("assigned_employee_id")) or None
    default_employee_id = _text(
        (default_assignment or {}).get("assigned_employee_id")
    ) or None
    usable_default_id = (
        default_employee_id
        if default_employee_id and default_employee_id in employees_by_id
        else None
    )
    assigned_employee_id = (
        state_employee_id or usable_default_id
        if route == INTERNAL_PREPARATION_ROUTE
        else None
    )
    assigned_employee = employees_by_id.get(assigned_employee_id or "")
    default_employee = employees_by_id.get(usable_default_id or "")

    return {
        "order_item_id": order_item_id,
        "product_key": product_key,
        "manual_hidden_spec_keys": manual,
        "operational_hidden_spec_keys": operational,
        "hidden_spec_keys": hidden,
        "preparation_route": route,
        "supplier_export": supplier_export,
        "direct_assembly": route == DIRECT_ASSEMBLY_ROUTE,
        "route_source": (
            "order"
            if _text(state.get("preparation_route"))
            else "default"
            if default_route
            else "system"
        ),
        "default_preparation_route": default_route or SUPPLIER_FILE_ROUTE,
        "preparation_status": (
            _text(state.get("preparation_status"))
            or (
                "in_progress"
                if route == INTERNAL_PREPARATION_ROUTE
                else "awaiting_assembly"
                if route == DIRECT_ASSEMBLY_ROUTE
                else "pending_file"
            )
        ),
        "assigned_employee_id": assigned_employee_id,
        "assigned_employee_name": (
            assigned_employee.get("name") if assigned_employee else None
        ),
        "assigned_employee_valid": bool(assigned_employee),
        "assignment_source": (
            "order"
            if state_employee_id
            else "default"
            if usable_default_id
            else None
        ),
        "default_assigned_employee_id": usable_default_id,
        "default_assigned_employee_name": (
            default_employee.get("name") if default_employee else None
        ),
    }


def apply_export_control_patch(
    current: dict[str, Any],
    payload: ReviewExportControlPatch,
    *,
    operational_items: list[dict[str, Any]],
    order_item_id: str,
    actor_id: str,
) -> dict[str, Any]:
    state = dict(current)
    fields = payload.model_fields_set

    if "manual_hidden_spec_keys" in fields:
        manual = normalize_spec_keys(payload.manual_hidden_spec_keys)
        operational = operational_excluded_keys(operational_items, order_item_id)
        state["manual_supplier_export_excluded_spec_keys"] = manual
        state["supplier_export_excluded_spec_keys"] = sorted(
            set(manual) | set(operational)
        )

    if "assigned_employee_id" in fields:
        state["assigned_employee_id"] = (
            _text(payload.assigned_employee_id) or None
        )

    if "preparation_route" in fields and payload.preparation_route:
        route = payload.preparation_route
        state["preparation_route"] = route
        if route == INTERNAL_PREPARATION_ROUTE:
            state["supplier_export"] = False
            state["preparation_status"] = "in_progress"
        elif route == DIRECT_ASSEMBLY_ROUTE:
            state["supplier_export"] = False
            state["preparation_status"] = "awaiting_assembly"
            state["assigned_employee_id"] = None
        else:
            state["supplier_export"] = True
            state["preparation_status"] = "pending_file"
            # The product is no longer assigned for internal execution.
            state["assigned_employee_id"] = None

    state.update({
        "order_item_id": order_item_id,
        "review_status": state.get("review_status") or "pending_review",
        "control_revision": int(state.get("control_revision") or 0) + 1,
        "export_controls_updated_at": _now(),
        "export_controls_updated_by": actor_id,
    })
    return state


def partition_review_items_for_preparation(
    items: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Canonical split used by the future preparation-file generator."""
    supplier_items: list[dict[str, Any]] = []
    internal_items: list[dict[str, Any]] = []
    direct_assembly_items: list[dict[str, Any]] = []
    for row in items or []:
        route = _text(row.get("preparation_route")) or SUPPLIER_FILE_ROUTE
        supplier_export = row.get("supplier_export") is not False
        if route == DIRECT_ASSEMBLY_ROUTE:
            direct_assembly_items.append(row)
        elif route == INTERNAL_PREPARATION_ROUTE or not supplier_export:
            internal_items.append(row)
        else:
            supplier_items.append(row)
    return {
        "supplier_file_items": supplier_items,
        "internal_preparation_items": internal_items,
        "direct_assembly_items": direct_assembly_items,
    }


def make_order_review_export_controls_router(
    db: Any,
    current_user: Callable,
) -> APIRouter:
    router = APIRouter(
        prefix="/order-review-export-controls-v1",
        tags=["order-review-export-controls"],
    )
    repository = MongoOrderRepository(db)

    async def _order_and_items(user_id: str, order_number: str):
        try:
            order = await get_order(
                repository,
                user_id=user_id,
                order_number=order_number,
            )
        except OrderNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "order_not_found"},
            ) from exc
        identities = map_order_item_identities(order)
        return order, {
            _text(item.order_item_id): item
            for item in identities
            if _text(item.order_item_id)
        }

    async def _assignable_employees(user_id: str) -> list[dict[str, Any]]:
        docs = await db.users.find(
            {"created_by": user_id},
            {
                "_id": 0,
                "id": 1,
                "name": 1,
                "email": 1,
                "role": 1,
                "extra_permissions": 1,
                "denied_permissions": 1,
                "disabled": 1,
                "is_active": 1,
                "deleted_at": 1,
            },
        ).sort("name", 1).to_list(500)
        return [
            assignable_employee_view(row)
            for row in docs
            if row.get("disabled") is not True
            and row.get("is_active") is not False
            and not row.get("deleted_at")
            and user_can_manage_preparation(row)
            and _text(row.get("id"))
        ]

    async def _assignment_defaults(
        user_id: str,
        product_keys: set[str],
    ) -> dict[str, dict[str, Any]]:
        if not product_keys:
            return {}
        rows = await db[ASSIGNMENT_DEFAULTS].find(
            {
                "user_id": user_id,
                "product_key": {"$in": sorted(product_keys)},
            },
            {"_id": 0},
        ).to_list(500)
        return {
            _text(row.get("product_key")): row
            for row in rows
            if _text(row.get("product_key"))
        }

    async def _ensure_assignment_default_indexes() -> None:
        await db[ASSIGNMENT_DEFAULTS].create_index(
            [("user_id", 1), ("product_key", 1)],
            unique=True,
        )

    @router.get("/{order_number}")
    async def get_export_controls(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        _, items_by_id = await _order_and_items(user_id, order_number)
        workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order_number},
            {"_id": 0},
        ) or {}
        states = _state_map(workflow)
        operational_items = list(workflow.get("operational_items") or [])

        employees = await _assignable_employees(user_id)
        employees_by_id = {row["id"]: row for row in employees}
        product_keys = {
            preparation_assignment_product_key(item)
            for item in items_by_id.values()
        }
        defaults = await _assignment_defaults(user_id, product_keys)

        ordered_items = sorted(
            items_by_id.values(),
            key=lambda item: int(getattr(item, "line_index", 0) or 0),
        )
        return {
            "order_number": order_number,
            "stage": workflow.get("stage") or "pending_review",
            "employees": employees,
            "items": [
                item_export_control_view(
                    states.get(_text(item.order_item_id)),
                    operational_items=operational_items,
                    order_item_id=_text(item.order_item_id),
                    product_key=preparation_assignment_product_key(item),
                    default_assignment=defaults.get(
                        preparation_assignment_product_key(item)
                    ),
                    employees_by_id=employees_by_id,
                )
                for item in ordered_items
            ],
        }

    @router.patch("/{order_number}/items/{order_item_id:path}")
    async def patch_export_controls(
        order_number: str,
        order_item_id: str,
        payload: ReviewExportControlPatch,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        actor_id = _text(reviewer.get("id"))
        order, items_by_id = await _order_and_items(user_id, order_number)
        item = items_by_id.get(order_item_id)
        if item is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "order_item_not_found"},
            )
        if not payload.model_fields_set:
            raise HTTPException(
                status_code=422,
                detail={"code": "export_control_update_required"},
            )

        workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order_number},
            {"_id": 0},
        )
        if (workflow or {}).get("stage") in REVIEW_COMPLETED_STAGES:
            raise HTTPException(
                status_code=409,
                detail={"code": "review_already_completed"},
            )

        states = _state_map(workflow)
        operational_items = list((workflow or {}).get("operational_items") or [])
        current = states.get(order_item_id, {
            "order_item_id": order_item_id,
            "review_status": "pending_review",
        })

        employees = await _assignable_employees(user_id)
        employees_by_id = {row["id"]: row for row in employees}
        eligible_employee_ids = set(employees_by_id)
        product_key = preparation_assignment_product_key(item)
        defaults = await _assignment_defaults(user_id, {product_key})
        default_assignment = defaults.get(product_key) or {}

        target_route = (
            payload.preparation_route
            or _text(current.get("preparation_route"))
            or _text(default_assignment.get("preparation_route"))
            or SUPPLIER_FILE_ROUTE
        )
        responsible_employee_id = resolve_responsible_employee_id(
            target_route=target_route,
            payload_employee_id=(
                payload.assigned_employee_id
                if "assigned_employee_id" in payload.model_fields_set
                else None
            ),
            current_employee_id=current.get("assigned_employee_id"),
            default_employee_id=default_assignment.get("assigned_employee_id"),
            eligible_employee_ids=eligible_employee_ids,
        )

        effective_values = payload.model_dump(exclude_unset=True)
        if target_route == INTERNAL_PREPARATION_ROUTE:
            effective_values["assigned_employee_id"] = responsible_employee_id
        elif payload.preparation_route in {
            SUPPLIER_FILE_ROUTE,
            DIRECT_ASSEMBLY_ROUTE,
        }:
            effective_values["assigned_employee_id"] = None
        effective_payload = ReviewExportControlPatch(**effective_values)

        states[order_item_id] = apply_export_control_patch(
            current,
            effective_payload,
            operational_items=operational_items,
            order_item_id=order_item_id,
            actor_id=actor_id,
        )
        now = _now()

        if workflow:
            result = await db[WORKFLOWS].update_one(
                {
                    "user_id": user_id,
                    "order_number": order_number,
                    "revision": int(workflow.get("revision") or 0),
                },
                {"$set": {
                    "items": list(states.values()),
                    "updated_at": now,
                    "updated_by": actor_id,
                }},
            )
            if not result.matched_count:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "review_revision_conflict"},
                )
        else:
            try:
                await db[WORKFLOWS].insert_one({
                    "user_id": user_id,
                    "order_number": order_number,
                    "order_id": order.order_id,
                    "stage": "pending_review",
                    "revision": 0,
                    "items": list(states.values()),
                    "operational_items": [],
                    "created_at": now,
                    "updated_at": now,
                    "updated_by": actor_id,
                })
            except DuplicateKeyError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "review_revision_conflict"},
                ) from exc

        default_updated = False
        if (
            payload.preparation_route is not None
            and payload.save_assignment_as_default
        ):
            await _ensure_assignment_default_indexes()
            await db[ASSIGNMENT_DEFAULTS].update_one(
                {
                    "user_id": user_id,
                    "product_key": product_key,
                },
                {
                    "$set": {
                        "preparation_route": target_route,
                        "assigned_employee_id": (
                            responsible_employee_id
                            if target_route == INTERNAL_PREPARATION_ROUTE
                            else None
                        ),
                        "updated_at": now,
                        "updated_by": actor_id,
                    },
                    "$setOnInsert": {
                        "created_at": now,
                        "created_by": actor_id,
                    },
                },
                upsert=True,
            )
            default_assignment = {
                "preparation_route": target_route,
                "assigned_employee_id": (
                    responsible_employee_id
                    if target_route == INTERNAL_PREPARATION_ROUTE
                    else None
                ),
            }
            default_updated = True

        await db[EVENTS].insert_one({
            "user_id": user_id,
            "order_number": order_number,
            "order_item_id": order_item_id,
            "product_key": product_key,
            "event_type": "review_export_controls_updated",
            "changed_fields": sorted(payload.model_fields_set),
            "assigned_employee_id": responsible_employee_id,
            "assignment_default_updated": default_updated,
            "occurred_at": now,
            "actor_id": actor_id,
        })
        return item_export_control_view(
            states[order_item_id],
            operational_items=operational_items,
            order_item_id=order_item_id,
            product_key=product_key,
            default_assignment=default_assignment,
            employees_by_id=employees_by_id,
        )

    return router
