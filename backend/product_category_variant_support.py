"""Readable product categories, Google taxonomy, and variant labels for Mezan Product V2."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException

from salla_integration.service import SallaError, call_salla


GOOGLE_PRODUCT_TAXONOMY_URL = (
    "https://www.google.com/basepages/producttype/taxonomy-with-ids.en-US.txt"
)
GOOGLE_TAXONOMY_TTL_SECONDS = 24 * 60 * 60
_GOOGLE_TAXONOMY_CACHE: dict[str, Any] = {
    "items": [],
    "version": None,
    "loaded_at": 0.0,
}
_GOOGLE_TAXONOMY_LOCK = asyncio.Lock()


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("name", "label", "title", "value", "text"):
            if value.get(key) not in (None, "", [], {}):
                return _text(value.get(key))
        return ""
    return str(value).strip()


def _parse_google_taxonomy(source: str) -> tuple[str | None, list[dict[str, Any]]]:
    """Parse Google's official ``ID - full path`` taxonomy text format."""
    version: str | None = None
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for raw_line in str(source or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            prefix = "# Google_Product_Taxonomy_Version:"
            if line.startswith(prefix):
                version = line[len(prefix):].strip() or None
            continue
        if " - " not in line:
            continue

        category_id, path = line.split(" - ", 1)
        category_id = category_id.strip()
        path = path.strip()
        if not category_id.isdigit() or not path or category_id in seen_ids:
            continue

        seen_ids.add(category_id)
        items.append({
            "id": category_id,
            "path": path,
            "name": path.rsplit(" > ", 1)[-1].strip(),
            "depth": path.count(" > "),
        })

    return version, items


async def _get_google_taxonomy() -> tuple[str | None, list[dict[str, Any]]]:
    now = time.monotonic()
    cached_items = _GOOGLE_TAXONOMY_CACHE.get("items") or []
    loaded_at = float(_GOOGLE_TAXONOMY_CACHE.get("loaded_at") or 0.0)
    if cached_items and now - loaded_at < GOOGLE_TAXONOMY_TTL_SECONDS:
        return _GOOGLE_TAXONOMY_CACHE.get("version"), cached_items

    async with _GOOGLE_TAXONOMY_LOCK:
        now = time.monotonic()
        cached_items = _GOOGLE_TAXONOMY_CACHE.get("items") or []
        loaded_at = float(_GOOGLE_TAXONOMY_CACHE.get("loaded_at") or 0.0)
        if cached_items and now - loaded_at < GOOGLE_TAXONOMY_TTL_SECONDS:
            return _GOOGLE_TAXONOMY_CACHE.get("version"), cached_items

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                response = await client.get(GOOGLE_PRODUCT_TAXONOMY_URL)
                response.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            if cached_items:
                return _GOOGLE_TAXONOMY_CACHE.get("version"), cached_items
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "google_taxonomy_unavailable",
                    "message": "تعذر تحميل تصنيفات Google الرسمية مؤقتًا.",
                },
            ) from exc

        version, items = _parse_google_taxonomy(response.text)
        if len(items) < 1000:
            if cached_items:
                return _GOOGLE_TAXONOMY_CACHE.get("version"), cached_items
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "google_taxonomy_invalid",
                    "message": "استجابة تصنيفات Google غير مكتملة.",
                },
            )

        _GOOGLE_TAXONOMY_CACHE.update({
            "items": items,
            "version": version,
            "loaded_at": time.monotonic(),
        })
        return version, items


def _category_row(value: Any, parent_path: str = "", parent_id: str = "") -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    category_id = _text(value.get("id") or value.get("category_id"))
    name = _text(value.get("name") or value.get("title") or value.get("label"))
    if not category_id and not name:
        return None
    status = _text(value.get("status") or value.get("visibility") or "active").lower() or "active"
    resolved_parent_id = _text(value.get("parent_id") or value.get("parentId") or value.get("parent") or parent_id)
    path = _text(value.get("path") or value.get("full_name") or value.get("breadcrumb"))
    if not path:
        path = " ← ".join(part for part in (parent_path, name) if part)
    return {
        "id": category_id,
        "name": name or category_id,
        "path": path or name or category_id,
        "parent_id": resolved_parent_id,
        "status": status,
        "is_hidden": status in {"hidden", "inactive", "disabled"},
    }


def _flatten_categories(rows: Any, parent_path: str = "", parent_id: str = "") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for value in rows if isinstance(rows, list) else []:
        row = _category_row(value, parent_path, parent_id)
        if not row:
            continue
        result.append(row)
        children = (
            value.get("children")
            or value.get("sub_categories")
            or value.get("subcategories")
            or value.get("items")
            or []
        )
        result.extend(_flatten_categories(children, row["path"], row["id"]))
    return result


def _build_category_catalog(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build full breadcrumb paths from Salla's flat parent_id response.

    Salla's List Categories response is commonly flat and exposes `parent_id`.
    Nested children may also arrive through `sub_categories`/`items`.  This
    function supports both forms and preserves hidden categories for admin use.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _text(row.get("id"))
        if not key:
            continue
        current = by_id.get(key, {})
        by_id[key] = {**current, **row}

    def resolve_path(category_id: str, trail: set[str] | None = None) -> tuple[str, int]:
        trail = set(trail or set())
        row = by_id.get(category_id) or {}
        name = _text(row.get("name")) or category_id
        parent_id = _text(row.get("parent_id"))
        if not parent_id or parent_id in {"0", category_id} or parent_id not in by_id or parent_id in trail:
            return name, 0
        parent_path, parent_depth = resolve_path(parent_id, trail | {category_id})
        return f"{parent_path} ← {name}", parent_depth + 1

    result: list[dict[str, Any]] = []
    for category_id, row in by_id.items():
        path, depth = resolve_path(category_id)
        hidden = bool(row.get("is_hidden"))
        result.append({
            **row,
            "path": f"{path} — مخفي" if hidden else path,
            "depth": depth,
            "status_label": "مخفي" if hidden else "نشط",
        })
    return sorted(result, key=lambda row: (row.get("path") or row.get("name") or "").casefold())


def _selection_label(selection: Any, value_lookup: dict[str, tuple[str, str]], option_lookup: dict[str, str]) -> str:
    if isinstance(selection, dict):
        option_id = _text(selection.get("option_id") or selection.get("attribute_id"))
        value_id = _text(selection.get("value_id") or selection.get("id"))
        explicit_option = _text(selection.get("option_name") or selection.get("name") or selection.get("attribute"))
        explicit_value = _text(selection.get("value_name") or selection.get("value") or selection.get("label") or selection.get("title"))
        if value_id and value_id in value_lookup:
            option_name, value_name = value_lookup[value_id]
            return f"{option_name}: {value_name}" if option_name else value_name
        option_name = explicit_option or option_lookup.get(option_id, "")
        if explicit_value:
            return f"{option_name}: {explicit_value}" if option_name else explicit_value
        return option_name
    key = _text(selection)
    if key in value_lookup:
        option_name, value_name = value_lookup[key]
        return f"{option_name}: {value_name}" if option_name else value_name
    return ""


def enrich_product_patch(raw: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    categories_raw = raw.get("categories") or raw.get("category") or []
    if isinstance(categories_raw, dict):
        categories_raw = [categories_raw]
    categories = _build_category_catalog(_flatten_categories(categories_raw))
    if categories:
        patch["categories"] = categories

    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    google_category = _text(
        raw.get("google_product_category")
        or raw.get("google_category")
        or raw.get("google_taxonomy")
        or metadata.get("google_product_category")
        or metadata.get("google_category")
    )
    if google_category:
        patch["google_category"] = google_category

    options = patch.get("options") or []
    option_lookup: dict[str, str] = {}
    value_lookup: dict[str, tuple[str, str]] = {}
    for option in options:
        option_id = _text(option.get("id"))
        option_name = _text(option.get("name"))
        if option_id:
            option_lookup[option_id] = option_name
        for value in option.get("values") or []:
            value_id = _text(value.get("id"))
            value_name = _text(value.get("name"))
            if value_id:
                value_lookup[value_id] = (option_name, value_name)

    raw_variants = raw.get("variants") or raw.get("skus") or raw.get("product_variants") or []
    variants = patch.get("variants") or []
    for index, variant in enumerate(variants):
        raw_variant = raw_variants[index] if isinstance(raw_variants, list) and index < len(raw_variants) and isinstance(raw_variants[index], dict) else {}
        selections = raw_variant.get("options") or raw_variant.get("values") or raw_variant.get("attributes") or variant.get("selections") or []
        if isinstance(selections, dict):
            selections = [{"name": key, "value": value} for key, value in selections.items()]
        labels = [label for label in (_selection_label(item, value_lookup, option_lookup) for item in (selections if isinstance(selections, list) else [])) if label]
        explicit_name = _text(raw_variant.get("name") or raw_variant.get("title") or variant.get("name"))
        display_name = " — ".join(labels) or explicit_name
        if display_name and not display_name.isdigit():
            variant["display_name"] = display_name
            variant["name"] = explicit_name or display_name
    return patch


def install_product_category_variant_support() -> None:
    import product_v2_details_routes as details_module

    original = details_module._details_patch
    if getattr(original, "_mezan_category_variant_support", False):
        return

    def wrapped(raw: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        return enrich_product_patch(raw, original(raw, *args, **kwargs))

    wrapped._mezan_category_variant_support = True  # type: ignore[attr-defined]
    details_module._details_patch = wrapped


def make_product_category_catalog_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/products-v2", tags=["Products V2 Categories"])

    @router.get("/google-taxonomy")
    async def google_taxonomy(user: dict = Depends(current_user)) -> dict[str, Any]:
        # Auth dependency is intentionally required even though the taxonomy
        # itself is public; this keeps all Product OS catalog surfaces private.
        _ = user
        version, items = await _get_google_taxonomy()
        return {
            "ok": True,
            "items": items,
            "total": len(items),
            "version": version,
            "source": "google_official_taxonomy",
        }

    @router.get("/category-catalog")
    async def category_catalog(user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        categories: list[dict[str, Any]] = []
        page = 1
        try:
            while page <= 20:
                response = await call_salla(
                    db,
                    user_id,
                    "GET",
                    "/categories",
                    params={"page": page, "per_page": 100, "with": "items"},
                )
                rows = response.get("data") if isinstance(response, dict) else None
                if not isinstance(rows, list) or not rows:
                    break
                categories.extend(_flatten_categories(rows))
                pagination = response.get("pagination") or {}
                total_pages = int(pagination.get("totalPages") or pagination.get("total_pages") or pagination.get("last_page") or 0)
                if total_pages and page >= total_pages:
                    break
                page += 1
        except SallaError as exc:
            raise HTTPException(status_code=exc.status_code if exc.status_code != 200 else 400, detail={"message": str(exc), "needs_reauth": exc.needs_reauth}) from exc

        items = _build_category_catalog(categories)
        return {"ok": True, "items": items, "total": len(items)}

    return router