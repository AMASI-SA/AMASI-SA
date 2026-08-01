"""Supplier-file-only replacement text for reviewed product specifications.

The original Salla specification remains unchanged and visible in the review UI.
A reviewer may save an alternative full line for the supplier preparation file,
for example replacing ``المقاس: 54 انش`` with ``المقاس 54 انش``.

Replacement defaults are product + specification scoped. They are snapshotted
into each order when that order is opened for review so later default changes do
not retroactively alter already reviewed orders.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order
from order_item_engine.mapper import map_order_item_identities
from order_review_export_controls import preparation_assignment_product_key
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


SPEC_REPLACEMENT_DEFAULTS = "order_review_spec_replacement_defaults"
ORDER_OVERRIDE_FIELD = "supplier_export_spec_replacement_overrides"
MAX_REPLACEMENT_LENGTH = 500


class SpecReplacementPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_key: str = Field(min_length=1, max_length=160)
    replacement_text: Optional[str] = Field(
        default=None,
        max_length=MAX_REPLACEMENT_LENGTH,
    )
    # Persistent is the requested default. Clearing this checkbox applies the
    # replacement to the current order only.
    save_as_default: bool = True


def canonical_spec_key(value: Any) -> str:
    normalized = _normalized(value).rstrip(":：").strip()
    aliases = {
        "لون": "color",
        "اللون": "color",
        "لون المنتج": "color",
        "مقاس": "size",
        "المقاس": "size",
        "مقاس المنتج": "size",
        "خامة": "material",
        "الخامة": "material",
        "مادة": "material",
        "المادة": "material",
    }
    return aliases.get(normalized, normalized)


def _display_value(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "value", "text", "label", "answer"):
            visible = _text(value.get(key))
            if visible:
                return visible
        return ""
    return _text(value)


def extract_item_specs(item: Any) -> list[dict[str, str]]:
    """Return stable, de-duplicated display specs without changing source data."""
    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(name: Any, value: Any) -> None:
        display_name = _text(name).rstrip(":：").strip()
        display_value = _display_value(value)
        spec_key = canonical_spec_key(display_name)
        if not spec_key or not display_name or not display_value or spec_key in seen:
            return
        seen.add(spec_key)
        rows.append({
            "spec_key": spec_key,
            "name": display_name,
            "value": display_value,
        })

    for option in getattr(item, "options", None) or []:
        add(getattr(option, "name", None), getattr(option, "value", None))

    for field in getattr(item, "custom_fields", None) or []:
        if not isinstance(field, dict):
            continue
        add(
            field.get("name")
            or field.get("label")
            or field.get("title")
            or field.get("question")
            or field.get("key"),
            field.get("value")
            if field.get("value") not in (None, "")
            else field.get("answer")
            if field.get("answer") not in (None, "")
            else field.get("selected")
            if field.get("selected") not in (None, "")
            else field.get("choice")
            if field.get("choice") not in (None, "")
            else field.get("text")
            if field.get("text") not in (None, "")
            else field.get("response"),
        )

    add("اللون", getattr(item, "color", None))
    add("المقاس", getattr(item, "size", None))
    add("الخامة", getattr(item, "material", None))
    return rows


def replacement_override_map(state: Optional[dict[str, Any]]) -> dict[str, Optional[str]]:
    result: dict[str, Optional[str]] = {}
    for row in (state or {}).get(ORDER_OVERRIDE_FIELD, []) or []:
        if not isinstance(row, dict):
            continue
        spec_key = canonical_spec_key(row.get("spec_key"))
        if not spec_key:
            continue
        # None is an explicit current-order clear and therefore suppresses the
        # product default for this order.
        replacement = _text(row.get("replacement_text")) or None
        result[spec_key] = replacement
    return result


def replacement_override_rows(
    values: dict[str, Optional[str]],
) -> list[dict[str, Optional[str]]]:
    return [
        {"spec_key": key, "replacement_text": values[key]}
        for key in sorted(values)
    ]


def effective_spec_rows(
    item: Any,
    state: Optional[dict[str, Any]],
    defaults: Optional[dict[str, str]],
) -> list[dict[str, Any]]:
    defaults = defaults or {}
    overrides = replacement_override_map(state)
    rows: list[dict[str, Any]] = []
    for spec in extract_item_specs(item):
        spec_key = spec["spec_key"]
        if spec_key in overrides:
            replacement_text = overrides[spec_key]
            source = "order" if replacement_text else "order_clear"
        else:
            replacement_text = _text(defaults.get(spec_key)) or None
            source = "default" if replacement_text else None
        original_text = f"{spec['name']}: {spec['value']}"
        rows.append({
            **spec,
            "original_text": original_text,
            "replacement_text": replacement_text,
            "replacement_source": source,
            "file_text": replacement_text or original_text,
        })
    return rows


def supplier_file_spec_lines(
    item: Any,
    state: Optional[dict[str, Any]],
    defaults: Optional[dict[str, str]] = None,
) -> list[str]:
    """Canonical contract for the future preparation-file generator.

    Hidden specs are omitted. A replacement line fully replaces the original
    label/value only in the supplier file.
    """
    hidden = {
        canonical_spec_key(value)
        for value in (state or {}).get("supplier_export_excluded_spec_keys", []) or []
        if canonical_spec_key(value)
    }
    return [
        row["file_text"]
        for row in effective_spec_rows(item, state, defaults)
        if row["spec_key"] not in hidden
    ]


def materialize_defaults_into_state(
    item: Any,
    state: Optional[dict[str, Any]],
    defaults: Optional[dict[str, str]],
) -> tuple[dict[str, Any], bool]:
    """Snapshot product defaults into one order exactly once per spec."""
    current = dict(state or {})
    overrides = replacement_override_map(current)
    changed = False
    valid_keys = {row["spec_key"] for row in extract_item_specs(item)}
    for spec_key, replacement in (defaults or {}).items():
        key = canonical_spec_key(spec_key)
        visible = _text(replacement)
        if not key or key not in valid_keys or not visible or key in overrides:
            continue
        overrides[key] = visible
        changed = True
    if changed:
        current[ORDER_OVERRIDE_FIELD] = replacement_override_rows(overrides)
    return current, changed


def item_replacement_view(
    item: Any,
    state: Optional[dict[str, Any]],
    defaults: Optional[dict[str, str]],
) -> dict[str, Any]:
    product_key = preparation_assignment_product_key(item)
    rows = effective_spec_rows(item, state, defaults)
    return {
        "order_item_id": _text(getattr(item, "order_item_id", None)),
        "product_key": product_key,
        "specs": rows,
        "file_spec_lines": supplier_file_spec_lines(item, state, defaults),
        "has_unmaterialized_defaults": any(
            row.get("replacement_source") == "default" for row in rows
        ),
    }


def make_order_review_spec_replacements_router(
    db: Any,
    current_user: Callable,
) -> APIRouter:
    router = APIRouter(
        prefix="/order-review-spec-replacements-v1",
        tags=["order-review-spec-replacements"],
    )
    repository = MongoOrderRepository(db)

    async def ensure_indexes() -> None:
        await db[SPEC_REPLACEMENT_DEFAULTS].create_index(
            [("user_id", 1), ("product_key", 1), ("spec_key", 1)],
            unique=True,
        )

    async def order_and_items(user_id: str, order_number: str):
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

    async def defaults_for_items(
        user_id: str,
        items_by_id: dict[str, Any],
    ) -> dict[str, dict[str, str]]:
        product_keys = {
            preparation_assignment_product_key(item)
            for item in items_by_id.values()
        }
        if not product_keys:
            return {}
        docs = await db[SPEC_REPLACEMENT_DEFAULTS].find(
            {
                "user_id": user_id,
                "product_key": {"$in": sorted(product_keys)},
            },
            {"_id": 0},
        ).to_list(5000)
        result: dict[str, dict[str, str]] = {}
        for doc in docs:
            product_key = _text(doc.get("product_key"))
            spec_key = canonical_spec_key(doc.get("spec_key"))
            replacement = _text(doc.get("replacement_text"))
            if product_key and spec_key and replacement:
                result.setdefault(product_key, {})[spec_key] = replacement
        return result

    async def response_view(
        user_id: str,
        order_number: str,
        items_by_id: dict[str, Any],
        workflow: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        states = _state_map(workflow)
        defaults = await defaults_for_items(user_id, items_by_id)
        ordered = sorted(
            items_by_id.values(),
            key=lambda item: int(getattr(item, "line_index", 0) or 0),
        )
        return {
            "order_number": order_number,
            "stage": (workflow or {}).get("stage") or "pending_review",
            "items": [
                item_replacement_view(
                    item,
                    states.get(_text(item.order_item_id)),
                    defaults.get(preparation_assignment_product_key(item), {}),
                )
                for item in ordered
            ],
        }

    async def save_states(
        *,
        user_id: str,
        actor_id: str,
        order: Any,
        workflow: Optional[dict[str, Any]],
        states: dict[str, dict[str, Any]],
    ) -> None:
        now = _now()
        if workflow:
            result = await db[WORKFLOWS].update_one(
                {
                    "user_id": user_id,
                    "order_number": order.order_number,
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
                    detail={
                        "code": "review_revision_conflict",
                        "message": "تغير الطلب أثناء الحفظ؛ حدّث الصفحة وأعد المحاولة.",
                    },
                )
            return
        try:
            await db[WORKFLOWS].insert_one({
                "user_id": user_id,
                "order_number": order.order_number,
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
                detail={
                    "code": "review_revision_conflict",
                    "message": "تغير الطلب أثناء الحفظ؛ حدّث الصفحة وأعد المحاولة.",
                },
            ) from exc

    @router.get("/{order_number}")
    async def get_spec_replacements(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        await ensure_indexes()
        _, items_by_id = await order_and_items(user_id, order_number)
        workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order_number},
            {"_id": 0},
        )
        return await response_view(user_id, order_number, items_by_id, workflow)

    @router.post("/{order_number}/materialize-defaults")
    async def materialize_spec_defaults(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        actor_id = _text(reviewer.get("id"))
        await ensure_indexes()
        order, items_by_id = await order_and_items(user_id, order_number)
        workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order_number},
            {"_id": 0},
        )
        if (workflow or {}).get("stage") in REVIEW_COMPLETED_STAGES:
            return await response_view(user_id, order_number, items_by_id, workflow)

        states = _state_map(workflow)
        defaults = await defaults_for_items(user_id, items_by_id)
        changed = False
        for order_item_id, item in items_by_id.items():
            current = states.get(order_item_id, {
                "order_item_id": order_item_id,
                "review_status": "pending_review",
            })
            next_state, row_changed = materialize_defaults_into_state(
                item,
                current,
                defaults.get(preparation_assignment_product_key(item), {}),
            )
            if row_changed:
                next_state.update({
                    "order_item_id": order_item_id,
                    "review_status": next_state.get("review_status") or "pending_review",
                    "spec_replacements_materialized_at": _now(),
                    "spec_replacements_materialized_by": actor_id,
                })
                states[order_item_id] = next_state
                changed = True

        if changed:
            await save_states(
                user_id=user_id,
                actor_id=actor_id,
                order=order,
                workflow=workflow,
                states=states,
            )
            workflow = await db[WORKFLOWS].find_one(
                {"user_id": user_id, "order_number": order_number},
                {"_id": 0},
            )
        return await response_view(user_id, order_number, items_by_id, workflow)

    @router.patch("/{order_number}/items/{order_item_id:path}")
    async def patch_spec_replacement(
        order_number: str,
        order_item_id: str,
        payload: SpecReplacementPatch,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        actor_id = _text(reviewer.get("id"))
        await ensure_indexes()
        order, items_by_id = await order_and_items(user_id, order_number)
        item = items_by_id.get(order_item_id)
        if item is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "order_item_not_found"},
            )
        workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order_number},
            {"_id": 0},
        )
        if (workflow or {}).get("stage") in REVIEW_COMPLETED_STAGES:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "review_already_completed",
                    "message": "اكتملت مراجعة الطلب ولا يمكن تغيير نصوص ملفه.",
                },
            )

        spec_key = canonical_spec_key(payload.spec_key)
        valid_specs = {
            row["spec_key"]: row for row in extract_item_specs(item)
        }
        if spec_key not in valid_specs:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "supplier_spec_not_found",
                    "message": "المواصفة المحددة غير موجودة في هذا المنتج.",
                },
            )

        replacement = _text(payload.replacement_text) or None
        states = _state_map(workflow)
        current = dict(states.get(order_item_id) or {
            "order_item_id": order_item_id,
            "review_status": "pending_review",
        })
        overrides = replacement_override_map(current)
        overrides[spec_key] = replacement
        current[ORDER_OVERRIDE_FIELD] = replacement_override_rows(overrides)
        current.update({
            "order_item_id": order_item_id,
            "review_status": current.get("review_status") or "pending_review",
            "spec_replacements_updated_at": _now(),
            "spec_replacements_updated_by": actor_id,
        })
        states[order_item_id] = current
        await save_states(
            user_id=user_id,
            actor_id=actor_id,
            order=order,
            workflow=workflow,
            states=states,
        )

        product_key = preparation_assignment_product_key(item)
        now = _now()
        if payload.save_as_default:
            if replacement:
                await db[SPEC_REPLACEMENT_DEFAULTS].update_one(
                    {
                        "user_id": user_id,
                        "product_key": product_key,
                        "spec_key": spec_key,
                    },
                    {
                        "$set": {
                            "replacement_text": replacement,
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
            else:
                await db[SPEC_REPLACEMENT_DEFAULTS].delete_one({
                    "user_id": user_id,
                    "product_key": product_key,
                    "spec_key": spec_key,
                })

        await db[EVENTS].insert_one({
            "user_id": user_id,
            "order_number": order_number,
            "order_item_id": order_item_id,
            "product_key": product_key,
            "spec_key": spec_key,
            "event_type": "supplier_file_spec_replacement_updated",
            "replacement_text": replacement,
            "default_updated": bool(payload.save_as_default),
            "occurred_at": now,
            "actor_id": actor_id,
        })

        refreshed_workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order_number},
            {"_id": 0},
        )
        defaults = await defaults_for_items(user_id, items_by_id)
        refreshed_state = _state_map(refreshed_workflow).get(order_item_id)
        return {
            "order_number": order_number,
            "item": item_replacement_view(
                item,
                refreshed_state,
                defaults.get(product_key, {}),
            ),
        }

    return router
