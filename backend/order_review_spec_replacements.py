"""Supplier-file-only name/value overrides for reviewed product specs.

Salla's original specification name and value remain unchanged. Reviewers may
change the supplier-file label, the supplier-file value, or both independently.
Product defaults are snapshotted into an order when it is opened for review so
later default changes never rewrite an older order.
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
MAX_COMPONENT_LENGTH = 500


class SpecReplacementPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_key: str = Field(min_length=1, max_length=160)
    replacement_name: Optional[str] = Field(
        default=None,
        max_length=MAX_COMPONENT_LENGTH,
    )
    replacement_value: Optional[str] = Field(
        default=None,
        max_length=MAX_COMPONENT_LENGTH,
    )
    # Backward-compatible during rollout. New clients send the two fields above.
    replacement_text: Optional[str] = Field(
        default=None,
        max_length=MAX_COMPONENT_LENGTH,
    )
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


def _item_value(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def extract_item_specs(item: Any) -> list[dict[str, str]]:
    """Return stable display specs without changing Salla source values."""
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

    for option in _item_value(item, "options") or []:
        add(_item_value(option, "name"), _item_value(option, "value"))

    normalized = _item_value(item, "options_normalized") or {}
    if isinstance(normalized, dict):
        for name, value in normalized.items():
            add(name, value)

    raw_options = _item_value(item, "options_raw") or []
    if isinstance(raw_options, dict):
        raw_options = [
            {"name": name, "value": value}
            for name, value in raw_options.items()
        ]
    for option in raw_options if isinstance(raw_options, list) else []:
        if not isinstance(option, dict):
            continue
        add(
            option.get("name") or option.get("label") or option.get("key"),
            option.get("value")
            if option.get("value") not in (None, "")
            else option.get("selected")
            if option.get("selected") not in (None, "")
            else option.get("text"),
        )

    for field in _item_value(item, "custom_fields") or []:
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

    add("اللون", _item_value(item, "color"))
    add("المقاس", _item_value(item, "size"))
    add("الخامة", _item_value(item, "material"))
    return rows


def _snapshot_spec_rows(state: Optional[dict[str, Any]]) -> list[dict[str, str]]:
    """Read immutable review options when the later live Salla line is sparse."""
    state = state or {}
    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(name: Any, value: Any) -> None:
        display_name = _text(name).rstrip(":：").strip()
        display_value = _display_value(value)
        spec_key = canonical_spec_key(display_name)
        if display_name and display_value and spec_key and spec_key not in seen:
            seen.add(spec_key)
            rows.append({
                "spec_key": spec_key,
                "name": display_name,
                "value": display_value,
            })

    def add_collection(value: Any) -> None:
        if isinstance(value, dict):
            for name, answer in value.items():
                add(name, answer)
            return
        if not isinstance(value, list):
            return
        for row in value:
            if not isinstance(row, dict):
                continue
            add(
                row.get("name")
                or row.get("label")
                or row.get("title")
                or row.get("question")
                or row.get("key")
                or row.get("spec_key"),
                row.get("value")
                if row.get("value") not in (None, "")
                else row.get("answer")
                if row.get("answer") not in (None, "")
                else row.get("selected")
                if row.get("selected") not in (None, "")
                else row.get("choice")
                if row.get("choice") not in (None, "")
                else row.get("text")
                if row.get("text") not in (None, "")
                else row.get("response"),
            )

    # The immutable review snapshot is authoritative. Older workflow rows used
    # several field names, so merge only missing customer fields from each
    # line-specific source. Product-catalog defaults are deliberately excluded.
    add_collection(state.get("specifications_snapshot"))
    add_collection(state.get("options"))
    add_collection(state.get("options_normalized"))
    add_collection(state.get("options_raw"))
    add_collection(state.get("custom_fields"))
    add("اللون", state.get("color"))
    add("المقاس", state.get("size"))
    add("الخامة", state.get("material"))
    return rows


def _without_redundant_component(value: Any, original: Any) -> Optional[str]:
    visible = _text(value)
    if not visible or visible == _text(original):
        return None
    return visible


def split_legacy_replacement_text(
    replacement_text: Any,
    *,
    original_name: str,
    original_value: str,
) -> dict[str, Optional[str]]:
    """Convert the old full-line format into separate name/value components."""
    visible = _text(replacement_text)
    if not visible:
        return {"replacement_name": None, "replacement_value": None}

    candidate_name: Optional[str] = None
    candidate_value: Optional[str] = None
    original_name_text = _text(original_name)

    if original_name_text and visible.startswith(original_name_text):
        remainder = visible[len(original_name_text):].lstrip(" :：-—")
        candidate_value = remainder or None
    elif ":" in visible or "：" in visible:
        separator = ":" if ":" in visible else "："
        left, right = visible.split(separator, 1)
        candidate_name = _text(left) or None
        candidate_value = _text(right) or None
    else:
        candidate_value = visible

    return {
        "replacement_name": _without_redundant_component(
            candidate_name,
            original_name,
        ),
        "replacement_value": _without_redundant_component(
            candidate_value,
            original_value,
        ),
    }


def replacement_components(
    raw: Optional[dict[str, Any]],
    spec: dict[str, str],
) -> dict[str, Optional[str]]:
    raw = raw or {}
    # Legacy rows may already have empty new keys after being read through the
    # compatibility map. The legacy full line still wins when both are blank.
    if (
        _text(raw.get("replacement_text"))
        and not _text(raw.get("replacement_name"))
        and not _text(raw.get("replacement_value"))
    ):
        return split_legacy_replacement_text(
            raw.get("replacement_text"),
            original_name=spec["name"],
            original_value=spec["value"],
        )
    return {
        "replacement_name": _without_redundant_component(
            raw.get("replacement_name"),
            spec["name"],
        ),
        "replacement_value": _without_redundant_component(
            raw.get("replacement_value"),
            spec["value"],
        ),
    }


def replacement_override_map(
    state: Optional[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in (state or {}).get(ORDER_OVERRIDE_FIELD, []) or []:
        if not isinstance(row, dict):
            continue
        spec_key = canonical_spec_key(row.get("spec_key"))
        if not spec_key:
            continue
        result[spec_key] = {
            "replacement_name": _text(row.get("replacement_name")) or None,
            "replacement_value": _text(row.get("replacement_value")) or None,
            "replacement_text": _text(row.get("replacement_text")) or None,
        }
    return result


def replacement_override_rows(
    values: dict[str, dict[str, Any]],
) -> list[dict[str, Optional[str]]]:
    return [
        {
            "spec_key": key,
            "replacement_name": _text(values[key].get("replacement_name")) or None,
            "replacement_value": _text(values[key].get("replacement_value")) or None,
        }
        for key in sorted(values)
    ]


def effective_spec_rows(
    item: Any,
    state: Optional[dict[str, Any]],
    defaults: Optional[dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    defaults = defaults or {}
    overrides = replacement_override_map(state)
    rows: list[dict[str, Any]] = []
    source_specs = extract_item_specs(item)
    live_keys = {row["spec_key"] for row in source_specs}
    source_specs.extend(
        row for row in _snapshot_spec_rows(state)
        if row["spec_key"] not in live_keys
    )
    for spec in source_specs:
        spec_key = spec["spec_key"]
        if spec_key in overrides:
            components = replacement_components(overrides[spec_key], spec)
            source = (
                "order"
                if components["replacement_name"] or components["replacement_value"]
                else "order_clear"
            )
        else:
            components = replacement_components(defaults.get(spec_key), spec)
            source = (
                "default"
                if components["replacement_name"] or components["replacement_value"]
                else None
            )

        file_name = components["replacement_name"] or spec["name"]
        file_value = components["replacement_value"] or spec["value"]
        original_text = f"{spec['name']}: {spec['value']}"
        has_replacement = bool(
            components["replacement_name"] or components["replacement_value"]
        )
        rows.append({
            **spec,
            "original_name": spec["name"],
            "original_value": spec["value"],
            "original_text": original_text,
            "replacement_name": components["replacement_name"],
            "replacement_value": components["replacement_value"],
            "replacement_source": source,
            "file_name": file_name,
            "file_value": file_value,
            "file_text": f"{file_name}: {file_value}",
            "replacement_text": (
                f"{file_name}: {file_value}" if has_replacement else None
            ),
        })
    return rows


def supplier_file_spec_fields(
    item: Any,
    state: Optional[dict[str, Any]],
    defaults: Optional[dict[str, dict[str, Any]]] = None,
) -> list[dict[str, str]]:
    """Structured canonical fields for the future preparation-file generator."""
    hidden = {
        canonical_spec_key(value)
        for value in (state or {}).get("supplier_export_excluded_spec_keys", []) or []
        if canonical_spec_key(value)
    }
    return [
        {
            "spec_key": row["spec_key"],
            "name": row["file_name"],
            "value": row["file_value"],
            "text": row["file_text"],
        }
        for row in effective_spec_rows(item, state, defaults)
        if row["spec_key"] not in hidden
    ]


def supplier_file_spec_lines(
    item: Any,
    state: Optional[dict[str, Any]],
    defaults: Optional[dict[str, dict[str, Any]]] = None,
) -> list[str]:
    return [
        row["text"]
        for row in supplier_file_spec_fields(item, state, defaults)
    ]


def materialize_defaults_into_state(
    item: Any,
    state: Optional[dict[str, Any]],
    defaults: Optional[dict[str, dict[str, Any]]],
) -> tuple[dict[str, Any], bool]:
    """Snapshot each product default into one order exactly once per spec."""
    current = dict(state or {})
    overrides = replacement_override_map(current)
    changed = False
    specs = {row["spec_key"]: row for row in extract_item_specs(item)}
    for spec_key, raw_default in (defaults or {}).items():
        key = canonical_spec_key(spec_key)
        spec = specs.get(key)
        if not spec or key in overrides:
            continue
        components = replacement_components(raw_default, spec)
        if not components["replacement_name"] and not components["replacement_value"]:
            continue
        overrides[key] = components
        changed = True
    if changed:
        current[ORDER_OVERRIDE_FIELD] = replacement_override_rows(overrides)
    return current, changed


def item_replacement_view(
    item: Any,
    state: Optional[dict[str, Any]],
    defaults: Optional[dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    product_key = preparation_assignment_product_key(item)
    rows = effective_spec_rows(item, state, defaults)
    return {
        "order_item_id": _text(getattr(item, "order_item_id", None)),
        "product_key": product_key,
        "specs": rows,
        "file_spec_fields": supplier_file_spec_fields(item, state, defaults),
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
    ) -> dict[str, dict[str, dict[str, Any]]]:
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
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for doc in docs:
            product_key = _text(doc.get("product_key"))
            spec_key = canonical_spec_key(doc.get("spec_key"))
            if not product_key or not spec_key:
                continue
            raw = {
                "replacement_name": _text(doc.get("replacement_name")) or None,
                "replacement_value": _text(doc.get("replacement_value")) or None,
                "replacement_text": _text(doc.get("replacement_text")) or None,
            }
            if any(raw.values()):
                result.setdefault(product_key, {})[spec_key] = raw
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
                    "message": "اكتملت مراجعة الطلب ولا يمكن تغيير حقول ملفه.",
                },
            )

        spec_key = canonical_spec_key(payload.spec_key)
        valid_specs = {
            row["spec_key"]: row for row in extract_item_specs(item)
        }
        spec = valid_specs.get(spec_key)
        if spec is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "supplier_spec_not_found",
                    "message": "المواصفة المحددة غير موجودة في هذا المنتج.",
                },
            )

        provided = payload.model_fields_set
        if not provided.intersection({
            "replacement_name",
            "replacement_value",
            "replacement_text",
        }):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "supplier_spec_replacement_update_required",
                    "message": "حدد اسم المواصفة أو قيمتها المراد تعديلها.",
                },
            )

        states = _state_map(workflow)
        current = dict(states.get(order_item_id) or {
            "order_item_id": order_item_id,
            "review_status": "pending_review",
        })
        defaults = await defaults_for_items(user_id, items_by_id)
        product_key = preparation_assignment_product_key(item)
        effective = next(
            row for row in effective_spec_rows(
                item,
                current,
                defaults.get(product_key, {}),
            )
            if row["spec_key"] == spec_key
        )

        if (
            "replacement_text" in provided
            and "replacement_name" not in provided
            and "replacement_value" not in provided
        ):
            components = split_legacy_replacement_text(
                payload.replacement_text,
                original_name=spec["name"],
                original_value=spec["value"],
            )
        else:
            components = {
                "replacement_name": (
                    _without_redundant_component(
                        payload.replacement_name,
                        spec["name"],
                    )
                    if "replacement_name" in provided
                    else effective.get("replacement_name")
                ),
                "replacement_value": (
                    _without_redundant_component(
                        payload.replacement_value,
                        spec["value"],
                    )
                    if "replacement_value" in provided
                    else effective.get("replacement_value")
                ),
            }

        overrides = replacement_override_map(current)
        overrides[spec_key] = components
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

        now = _now()
        if payload.save_as_default:
            if components["replacement_name"] or components["replacement_value"]:
                await db[SPEC_REPLACEMENT_DEFAULTS].update_one(
                    {
                        "user_id": user_id,
                        "product_key": product_key,
                        "spec_key": spec_key,
                    },
                    {
                        "$set": {
                            "replacement_name": components["replacement_name"],
                            "replacement_value": components["replacement_value"],
                            "updated_at": now,
                            "updated_by": actor_id,
                        },
                        "$unset": {"replacement_text": ""},
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
            "event_type": "supplier_file_spec_fields_updated",
            "replacement_name": components["replacement_name"],
            "replacement_value": components["replacement_value"],
            "default_updated": bool(payload.save_as_default),
            "occurred_at": now,
            "actor_id": actor_id,
        })

        refreshed_workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order_number},
            {"_id": 0},
        )
        refreshed_defaults = await defaults_for_items(user_id, items_by_id)
        refreshed_state = _state_map(refreshed_workflow).get(order_item_id)
        return {
            "order_number": order_number,
            "item": item_replacement_view(
                item,
                refreshed_state,
                refreshed_defaults.get(product_key, {}),
            ),
        }

    return router
