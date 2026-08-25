"""Product-first ordering and one-field PDF card sorting for reviewed items.

The reviewed stage stays aggregated by Salla product so a merchant can select a
large quantity quickly. Each product may persist exactly one specification
field that controls the order of its source cards inside the generated PDF.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING

from order_review_routes import _merchant_user_id, _require_reviewer, _text


PREFERENCES = "mezan_reviewed_product_sort_preferences_v1"
MISSING_VALUE_LABEL = "غير محدد"
MAX_PREFERENCE_ROWS = 10000

_EXCLUDED_SPEC_HINTS = (
    "الاسم", "اسم العميل", "الاسم اللي", "ملاحظ", "رسالة", "رساله",
    "كتابة", "كتابه", "عبارة", "عباره", "اهداء", "إهداء",
    "customer name", "name on", "note", "message", "greeting",
)


class ReviewedProductSortPreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_key: str = Field(min_length=1, max_length=500)
    spec_key: str | None = Field(default=None, max_length=160)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized(value: Any) -> str:
    return " ".join(_text(value).casefold().replace("_", " ").split())


def _line_quantity(line: dict[str, Any]) -> int:
    raw = line.get("remaining_quantity") if line.get("remaining_quantity") is not None else line.get("quantity")
    try:
        number = float(raw or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    rounded = int(round(number))
    if number <= 0 or abs(number - rounded) > 0.000001:
        return 0
    return rounded


def _natural_key(value: Any) -> tuple:
    normalized = _normalized(value)
    parts = re.split(r"(\d+(?:\.\d+)?)", normalized)
    return tuple(
        (0, float(part)) if re.fullmatch(r"\d+(?:\.\d+)?", part or "") else (1, part)
        for part in parts if part != ""
    )


def _is_sortable_spec_key(value: Any) -> bool:
    key = _normalized(value)
    return bool(key) and not any(hint in key for hint in _EXCLUDED_SPEC_HINTS)


def _options(line: dict[str, Any]) -> dict[str, Any]:
    raw = line.get("options_normalized")
    return dict(raw) if isinstance(raw, dict) else {}


def _spec_value(line: dict[str, Any], spec_key: Any) -> str:
    wanted = _normalized(spec_key)
    if not wanted:
        return ""
    for key, value in _options(line).items():
        if _normalized(key) == wanted:
            return _text(value)
    return ""


def reviewed_product_sort_candidates(product: dict[str, Any]) -> list[dict[str, Any]]:
    field_labels: dict[str, str] = {}
    value_quantities: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    value_cards: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    value_labels: dict[str, dict[str, str]] = defaultdict(dict)

    for line in product.get("source_lines") or []:
        if not isinstance(line, dict):
            continue
        quantity = _line_quantity(line)
        if quantity <= 0:
            continue
        for raw_key, raw_value in _options(line).items():
            label = _text(raw_key)
            value = _text(raw_value)
            normalized_key = _normalized(label)
            normalized_value = _normalized(value)
            if not normalized_key or not normalized_value or not _is_sortable_spec_key(label):
                continue
            field_labels.setdefault(normalized_key, label)
            value_quantities[normalized_key][normalized_value] += quantity
            value_cards[normalized_key][normalized_value] += 1
            value_labels[normalized_key].setdefault(normalized_value, value)

    candidates: list[dict[str, Any]] = []
    for normalized_key, label in field_labels.items():
        values = [
            {
                "value": value_labels[normalized_key][normalized_value],
                "quantity": quantity,
                "card_count": value_cards[normalized_key][normalized_value],
            }
            for normalized_value, quantity in value_quantities[normalized_key].items()
        ]
        values.sort(key=lambda row: (-int(row["quantity"]), _natural_key(row["value"])))
        candidates.append({
            "key": label,
            "normalized_key": normalized_key,
            "label": label,
            "distinct_value_count": len(values),
            "values": values,
        })
    candidates.sort(key=lambda row: _natural_key(row.get("label")))
    return candidates


def _sort_source_lines(source_lines: list[dict[str, Any]], *, spec_key: str) -> list[dict[str, Any]]:
    totals: dict[str, int] = defaultdict(int)
    display_values: dict[str, str] = {}
    for line in source_lines:
        value = _spec_value(line, spec_key)
        normalized_value = _normalized(value)
        if normalized_value:
            totals[normalized_value] += _line_quantity(line)
            display_values.setdefault(normalized_value, value)

    def sort_key(line: dict[str, Any]) -> tuple:
        value = _spec_value(line, spec_key)
        normalized_value = _normalized(value)
        return (
            1 if not normalized_value else 0,
            -totals.get(normalized_value, 0),
            _natural_key(display_values.get(normalized_value, value)),
            -_line_quantity(line),
            _text(line.get("reviewed_at")),
            _text(line.get("order_number")),
            int(line.get("line_index") or 0),
        )

    return sorted([dict(line) for line in source_lines if isinstance(line, dict)], key=sort_key)


def apply_reviewed_product_sorting(
    catalog: dict[str, Any],
    preference_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    preferences = {
        _text(row.get("group_key")): _text(row.get("spec_key"))
        for row in (preference_rows or []) if _text(row.get("group_key"))
    }
    products: list[dict[str, Any]] = []
    for raw_product in catalog.get("products") or []:
        if not isinstance(raw_product, dict):
            continue
        product = dict(raw_product)
        source_lines = [dict(line) for line in (product.get("source_lines") or []) if isinstance(line, dict)]
        product["source_lines"] = source_lines
        candidates = reviewed_product_sort_candidates(product)
        candidate_by_normalized = {_normalized(row.get("key")): row for row in candidates}
        requested = preferences.get(_text(product.get("group_key")), "")
        selected = candidate_by_normalized.get(_normalized(requested))
        active_key = _text(selected.get("key")) if selected else ""
        if active_key:
            product["source_lines"] = _sort_source_lines(source_lines, spec_key=active_key)
        product["preparation_sort_spec"] = active_key or None
        product["preparation_sort_label"] = _text(selected.get("label")) if selected else None
        product["preparation_sort_candidates"] = candidates
        products.append(product)

    products.sort(key=lambda row: (
        -int(row.get("remaining_quantity") or row.get("quantity") or 0),
        _natural_key(row.get("name")),
        _text(row.get("group_key")),
    ))
    return {**catalog, "products": products}


def order_selections_by_product_rank(
    products: list[dict[str, Any]], selections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rank = {
        _text(product.get("group_key")): index
        for index, product in enumerate(products or []) if _text(product.get("group_key"))
    }
    return sorted(
        [dict(selection) for selection in selections or []],
        key=lambda row: (
            rank.get(_text(row.get("group_key")), len(rank) + 1),
            _text(row.get("group_key")),
        ),
    )


async def ensure_reviewed_product_sorting_indexes(db: Any) -> None:
    await db[PREFERENCES].create_index(
        [("user_id", ASCENDING), ("group_key", ASCENDING)],
        unique=True,
        name="uq_reviewed_product_sort_preference_v1",
    )


async def _load_preferences(db: Any, user_id: str) -> list[dict[str, Any]]:
    try:
        return await db[PREFERENCES].find({"user_id": user_id}, {"_id": 0}).to_list(MAX_PREFERENCE_ROWS)
    except (AttributeError, TypeError):
        return []


_INSTALLED = False
_ORIGINAL_CONTEXT_LOADER: Callable | None = None
_ORIGINAL_PLAN_ALLOCATIONS: Callable | None = None


def install_reviewed_product_sorting() -> None:
    global _INSTALLED, _ORIGINAL_CONTEXT_LOADER, _ORIGINAL_PLAN_ALLOCATIONS
    if _INSTALLED:
        return
    import reviewed_products_catalog as catalog_module
    import reviewed_preparation_batches as batch_module

    _ORIGINAL_CONTEXT_LOADER = catalog_module.load_reviewed_product_context
    _ORIGINAL_PLAN_ALLOCATIONS = batch_module.plan_preparation_allocations

    async def load_context_with_product_sorting(
        database: Any,
        *,
        user_id: str,
        limit: int = catalog_module.MAX_REVIEWED_ORDERS,
        reviewed_date: str = "",
    ) -> dict[str, Any]:
        assert _ORIGINAL_CONTEXT_LOADER is not None
        context = await _ORIGINAL_CONTEXT_LOADER(
            database,
            user_id=user_id,
            limit=limit,
            reviewed_date=reviewed_date,
        )
        preferences = await _load_preferences(database, user_id)
        context["catalog"] = apply_reviewed_product_sorting(context.get("catalog") or {}, preferences)
        return context

    def plan_allocations_in_catalog_order(
        products: list[dict[str, Any]], selections: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        assert _ORIGINAL_PLAN_ALLOCATIONS is not None
        return _ORIGINAL_PLAN_ALLOCATIONS(
            products,
            order_selections_by_product_rank(products, selections),
        )

    catalog_module.load_reviewed_product_context = load_context_with_product_sorting
    batch_module.load_reviewed_product_context = load_context_with_product_sorting
    batch_module.plan_preparation_allocations = plan_allocations_in_catalog_order
    _INSTALLED = True


def make_reviewed_product_sorting_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/reviewed-product-sorting-v1", tags=["Reviewed Product Sorting"])

    @router.put("/preference")
    async def save_preference(
        payload: ReviewedProductSortPreferenceRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        import reviewed_products_catalog as catalog_module

        context = await catalog_module.load_reviewed_product_context(
            db,
            user_id=user_id,
            limit=catalog_module.MAX_REVIEWED_ORDERS,
        )
        product = next((
            row for row in (context.get("catalog") or {}).get("products") or []
            if _text(row.get("group_key")) == payload.group_key
        ), None)
        if not product:
            raise HTTPException(status_code=404, detail={"code": "reviewed_product_not_available"})

        requested = _text(payload.spec_key)
        candidates = reviewed_product_sort_candidates(product)
        candidate = next((
            row for row in candidates
            if _normalized(row.get("key")) == _normalized(requested)
        ), None) if requested else None
        if requested and not candidate:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "reviewed_product_sort_spec_unavailable",
                    "message": "اختر مواصفة موجودة حاليًا في بطاقات هذا المنتج.",
                },
            )

        await ensure_reviewed_product_sorting_indexes(db)
        selector = {"user_id": user_id, "group_key": payload.group_key}
        if candidate:
            stored_key = _text(candidate.get("key"))
            await db[PREFERENCES].update_one(
                selector,
                {
                    "$set": {
                        **selector,
                        "spec_key": stored_key,
                        "spec_label": _text(candidate.get("label")),
                        "updated_at": _now_iso(),
                        "updated_by": _text(reviewer.get("id")),
                    },
                    "$setOnInsert": {"created_at": _now_iso()},
                },
                upsert=True,
            )
        else:
            stored_key = ""
            await db[PREFERENCES].delete_one(selector)

        return {
            "ok": True,
            "group_key": payload.group_key,
            "spec_key": stored_key or None,
            "spec_label": _text((candidate or {}).get("label")) or None,
            "candidates": candidates,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }

    return router


__all__ = [
    "MISSING_VALUE_LABEL",
    "PREFERENCES",
    "apply_reviewed_product_sorting",
    "ensure_reviewed_product_sorting_indexes",
    "install_reviewed_product_sorting",
    "make_reviewed_product_sorting_router",
    "order_selections_by_product_rank",
    "reviewed_product_sort_candidates",
]
