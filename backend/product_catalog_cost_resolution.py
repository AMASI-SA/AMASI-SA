"""Canonical current-catalog resolution for product cost calculations.

Products V2 can hold the current Salla cost in the normalized light record or
inside the latest full Salla snapshot.  Historical order lines can also refer
to the same product through older product/variant aliases.  Dashboard and ad
profitability callers must resolve both through this one read-only contract so
the product editor and cost warnings cannot disagree.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from product_v2_routes import _number


NAME_ALIAS_PREFIX = "__mezan_exact_name__:"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def normalize_product_name(value: Any) -> str:
    text = " ".join(_text(value).replace("_", " ").casefold().split())
    return "".join(
        character for character in text
        if character.isalnum() or character.isspace()
    ).strip()


def _first_number(*values: Any) -> float | None:
    for value in values:
        parsed = _number(value)
        if parsed is not None:
            return parsed
    return None


def _raw_variants(product: dict[str, Any]) -> list[dict[str, Any]]:
    details = _dict(product.get("raw_salla_details"))
    light = _dict(product.get("raw_salla"))
    candidates = (
        details.get("variants")
        or details.get("skus")
        or details.get("product_variants")
        or light.get("variants")
        or light.get("skus")
        or []
    )
    return [row for row in _list(candidates) if isinstance(row, dict)]


def enrich_current_salla_cost(product: dict[str, Any]) -> dict[str, Any]:
    """Fill normalized Salla base/variant costs from the current full snapshot."""
    row = dict(product or {})
    details = _dict(row.get("raw_salla_details"))
    light = _dict(row.get("raw_salla"))
    if row.get("cost_price_from_salla") in (None, ""):
        recovered = _first_number(
            details.get("cost_price"),
            details.get("cost"),
            light.get("cost_price"),
            light.get("cost"),
            row.get("cost_price"),
            row.get("cost"),
        )
        if recovered is not None:
            row["cost_price_from_salla"] = recovered

    current_variants = [
        dict(value) for value in _list(row.get("variants"))
        if isinstance(value, dict)
    ]
    by_id = {
        _text(value.get("id")): value
        for value in current_variants if _text(value.get("id"))
    }
    by_sku = {
        _text(value.get("sku")).casefold(): value
        for value in current_variants if _text(value.get("sku"))
    }
    for raw in _raw_variants(row):
        raw_id = _text(raw.get("id"))
        raw_sku = _text(raw.get("sku")).casefold()
        target = by_id.get(raw_id) if raw_id else None
        if target is None and raw_sku:
            target = by_sku.get(raw_sku)
        if target is None:
            target = {
                "id": raw_id or None,
                "sku": _text(raw.get("sku")) or None,
            }
            current_variants.append(target)
            if raw_id:
                by_id[raw_id] = target
            if raw_sku:
                by_sku[raw_sku] = target
        if target.get("cost_price_from_salla") in (None, ""):
            recovered = _first_number(
                raw.get("cost_price_from_salla"),
                raw.get("cost_price"),
                raw.get("cost"),
            )
            if recovered is not None:
                target["cost_price_from_salla"] = recovered
    row["variants"] = current_variants
    return row


def index_current_catalog_products(
    products: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    """Index enriched products by every safe current/historical identity."""
    products_by_id: dict[str, dict[str, Any]] = {}
    products_by_variant: dict[str, dict[str, Any]] = {}
    products_by_sku: dict[str, dict[str, Any]] = {}
    names: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for raw_product in products:
        product = enrich_current_salla_cost(raw_product)
        for product_id in (
            product.get("salla_product_id"),
            product.get("mezan_product_id"),
            product.get("id"),
        ):
            identity = _text(product_id)
            if identity:
                products_by_id[identity] = product
        sku = _text(product.get("sku")).casefold()
        if sku:
            products_by_sku[sku] = product
        name = normalize_product_name(product.get("name"))
        if name:
            names[name].append(product)
        for variant in _list(product.get("variants")):
            if not isinstance(variant, dict):
                continue
            variant_id = _text(variant.get("id"))
            if variant_id:
                products_by_variant[variant_id] = product
            variant_sku = _text(variant.get("sku")).casefold()
            if variant_sku:
                products_by_sku[variant_sku] = product

    for name, matched_products in names.items():
        identities = {
            _text(product.get("salla_product_id"))
            or _text(product.get("mezan_product_id"))
            or _text(product.get("id"))
            for product in matched_products
        } - {""}
        if len(identities) == 1:
            products_by_sku[f"{NAME_ALIAS_PREFIX}{name}"] = matched_products[0]
    return products_by_id, products_by_variant, products_by_sku


def _candidate_values(item: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for key in keys:
        value = _text(item.get(key))
        if value and value not in values:
            values.append(value)
    for nested_key in ("product", "variant", "sku", "source_product", "source_variant"):
        nested = _dict(item.get(nested_key))
        for key in keys:
            value = _text(nested.get(key))
            if value and value not in values:
                values.append(value)
    return values


def resolve_current_catalog_line_product(
    item: dict[str, Any],
    *,
    products_by_id: dict[str, dict[str, Any]],
    products_by_variant: dict[str, dict[str, Any]],
    products_by_sku: dict[str, dict[str, Any]],
    base_resolver: Callable[..., dict[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    """Resolve a line conservatively across canonical and historical aliases."""
    if base_resolver is not None:
        product = base_resolver(
            item,
            products_by_id=products_by_id,
            products_by_variant=products_by_variant,
            products_by_sku=products_by_sku,
        )
        if product is not None:
            return product

    for value in _candidate_values(
        item,
        (
            "salla_product_id", "parent_product_id", "product_id",
            "source_product_id", "source_id", "id",
        ),
    ):
        if value in products_by_id:
            return products_by_id[value]
    for value in _candidate_values(
        item,
        (
            "variant_id", "product_variant_id", "source_variant_id",
            "sku_id", "id",
        ),
    ):
        if value in products_by_variant:
            return products_by_variant[value]
    for value in _candidate_values(
        item,
        ("sku", "product_sku", "variant_sku", "code"),
    ):
        product = products_by_sku.get(value.casefold())
        if product is not None:
            return product

    name = normalize_product_name(
        item.get("name")
        or _dict(item.get("product")).get("name")
        or _dict(item.get("variant")).get("name")
    )
    return products_by_sku.get(f"{NAME_ALIAS_PREFIX}{name}") if name else None


__all__ = [
    "NAME_ALIAS_PREFIX",
    "enrich_current_salla_cost",
    "index_current_catalog_products",
    "normalize_product_name",
    "resolve_current_catalog_line_product",
]
