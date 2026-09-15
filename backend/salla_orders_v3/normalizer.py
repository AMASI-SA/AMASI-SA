"""Pure normalization for Salla List Order Items payloads."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Iterable, Optional


OPTION_SOURCES = (
    "options",
    "choices",
    "attributes",
    "product_options",
    "selected_options",
    "customer_options",
)

CUSTOM_FIELD_SOURCES = (
    "custom_fields",
    "customizations",
    "personalization",
    "fields",
    "questions",
    "attachments",
    "files",
)

_NAME_KEYS = (
    "name",
    "label",
    "title",
    "question",
    "key",
    "option",
    "filename",
    "file_name",
)

_VALUE_KEYS = (
    "value",
    "values",
    "selected",
    "choice",
    "answer",
    "option_value",
    "text",
    "response",
    "url",
    "file_url",
    "download_url",
)


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(*values: Any) -> Any:
    for value in values:
        if _present(value):
            return value
    return None


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, dict):
        return _number(
            _first(
                value.get("amount"),
                value.get("value"),
                value.get("total"),
                value.get("price"),
            ),
            default,
        )
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def display_value(value: Any) -> Any:
    """Return a display-safe scalar without dropping ``0`` or ``False``."""
    if isinstance(value, dict):
        for key in (
            "value",
            "name",
            "label",
            "text",
            "option_value",
            "selected",
            "choice",
            "answer",
            "response",
            "title",
            "url",
            "file_url",
            "download_url",
            "filename",
            "file_name",
        ):
            if key not in value or not _present(value.get(key)):
                continue
            visible = display_value(value.get(key))
            if _present(visible) or visible in (0, False):
                return visible

        visible_children = []
        for child in value.values():
            visible = display_value(child)
            if _present(visible) or visible in (0, False):
                visible_children.append(visible)
        if not visible_children:
            return None
        return " / ".join(str(child) for child in visible_children)

    if isinstance(value, (list, tuple, set)):
        visible_values = []
        for entry in value:
            visible = display_value(entry)
            if _present(visible) or visible in (0, False):
                visible_values.append(visible)
        if not visible_values:
            return None
        return " / ".join(str(entry) for entry in visible_values)

    return value


def _looks_like_named_row(value: dict[str, Any]) -> bool:
    return any(key in value for key in _NAME_KEYS) and any(
        key in value for key in _VALUE_KEYS
    )


def _collection_rows(value: Any) -> Iterable[tuple[Optional[str], Any, Any]]:
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, dict):
                yield None, entry, entry
            elif _present(entry) or entry in (0, False):
                yield None, entry, entry
        return

    if isinstance(value, dict):
        if _looks_like_named_row(value):
            yield None, value, value
            return
        for key, entry in value.items():
            yield _text(key), entry, {key: deepcopy(entry)}
        return

    if _present(value) or value in (0, False):
        yield None, value, value


def normalize_named_values(value: Any, *, source: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for mapping_name, entry, raw in _collection_rows(value):
        row = entry if isinstance(entry, dict) else {}
        name = _text(
            _first(
                mapping_name,
                *(row.get(key) for key in _NAME_KEYS),
                source,
            )
        )
        raw_value = None
        if isinstance(entry, dict):
            for key in _VALUE_KEYS:
                if key in entry and (
                    _present(entry.get(key)) or entry.get(key) in (0, False)
                ):
                    raw_value = entry.get(key)
                    break
        else:
            raw_value = entry

        if raw_value is None and mapping_name is not None:
            raw_value = entry

        visible = display_value(raw_value)
        if not name or (not _present(visible) and visible not in (0, False)):
            continue

        normalized.append({
            "name": name,
            "value": visible,
            "source": source,
            "raw": deepcopy(raw),
        })
    return normalized


def _media_url(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, dict):
        for key in (
            "url",
            "original",
            "src",
            "full",
            "medium",
            "thumbnail",
        ):
            candidate = _media_url(value.get(key))
            if candidate:
                return candidate
    if isinstance(value, list):
        for entry in value:
            candidate = _media_url(entry)
            if candidate:
                return candidate
    return None


def _stable_order_item_id(
    *,
    order_number: str,
    source_item_id: Optional[str],
    item: dict[str, Any],
    index: int,  # kept in the call contract for traceability; not hashed
) -> str:
    if source_item_id:
        return f"salla:{order_number}:{source_item_id}"

    signature = {
        "order_number": order_number,
        "product_id": item.get("product_id"),
        "parent_product_id": item.get("parent_product_id"),
        "variant_id": item.get("variant_id") or item.get("product_sku_id"),
        "sku": item.get("sku"),
        "name": item.get("name"),
        "quantity": item.get("quantity"),
        "options": item.get("options"),
        "custom_fields": item.get("custom_fields"),
        "customizations": item.get("customizations"),
        "personalization": item.get("personalization"),
        "amounts": item.get("amounts"),
    }
    canonical = json.dumps(
        signature,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"salla:{order_number}:generated:{digest}"


def normalize_order_item(
    item: dict[str, Any],
    *,
    order_number: str,
    index: int,
) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise TypeError("Salla order item must be an object")

    raw_item = deepcopy(item)
    product = _dict(item.get("product"))
    variant = _dict(_first(item.get("variant"), product.get("variant")))
    amounts = _dict(item.get("amounts"))

    source_item_id = _text(
        _first(item.get("id"), item.get("item_id"), item.get("order_item_id"))
    )
    product_id = _text(_first(item.get("product_id"), product.get("id")))
    parent_product_id = _text(
        _first(item.get("parent_product_id"), product.get("parent_id"))
    )
    variant_id = _text(
        _first(
            item.get("variant_id"),
            item.get("product_sku_id"),
            variant.get("id"),
            product.get("variant_id"),
        )
    )
    sku = _text(
        _first(
            variant.get("sku"),
            item.get("sku"),
            product.get("sku"),
        )
    )
    name = _text(
        _first(
            item.get("name"),
            variant.get("name"),
            product.get("name"),
        )
    ) or "منتج بدون اسم"

    options: list[dict[str, Any]] = []
    for source in OPTION_SOURCES:
        options.extend(normalize_named_values(item.get(source), source=source))
    for source_owner in (variant, product):
        if source_owner.get("options") is not None:
            options.extend(
                normalize_named_values(source_owner.get("options"), source="options")
            )

    custom_fields: list[dict[str, Any]] = []
    for source in CUSTOM_FIELD_SOURCES:
        custom_fields.extend(
            normalize_named_values(item.get(source), source=source)
        )

    quantity = _number(item.get("quantity"), 1.0)

    image_url = _media_url(
        _first(
            item.get("image_url"),
            item.get("image"),
            item.get("thumbnail"),
            product.get("main_image"),
            product.get("image"),
            product.get("thumbnail"),
        )
    )

    return {
        "order_item_id": _stable_order_item_id(
            order_number=order_number,
            source_item_id=source_item_id,
            item=item,
            index=index,
        ),
        "source_item_id": source_item_id,
        "product_id": product_id,
        "parent_product_id": parent_product_id,
        "variant_id": variant_id,
        "sku": sku,
        "name": name,
        "quantity": quantity,
        "unit_price": _number(
            _first(
                amounts.get("price_without_tax"),
                amounts.get("price"),
                item.get("price"),
            )
        ),
        "total_price": _number(
            _first(amounts.get("total"), item.get("total"))
        ),
        "discount": _number(
            _first(
                amounts.get("total_discount"),
                amounts.get("discount"),
                item.get("discount"),
            )
        ),
        "tax": _number(_first(amounts.get("tax"), item.get("tax"))),
        "options": options,
        "custom_fields": custom_fields,
        "image_url": image_url,
        "raw_item": raw_item,
    }


def normalize_order_items(
    items: Iterable[dict[str, Any]],
    *,
    order_number: str,
) -> list[dict[str, Any]]:
    result = []
    identities: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        normalized = normalize_order_item(
            item,
            order_number=order_number,
            index=index,
        )
        identity = normalized["order_item_id"]
        if identity in identities:
            if normalized.get("source_item_id"):
                raise ValueError(f"duplicate Salla order_item_id: {identity}")
            # Identical provider rows without IDs are indistinguishable. Keep a
            # deterministic occurrence suffix while different option/custom
            # field signatures remain stable across provider reordering.
            occurrence = 2
            candidate = f"{identity}:{occurrence}"
            while candidate in identities:
                occurrence += 1
                candidate = f"{identity}:{occurrence}"
            normalized["order_item_id"] = candidate
            identity = candidate
        identities.add(identity)
        result.append(normalized)
    return result
