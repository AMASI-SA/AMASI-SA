"""Current-catalog fallback for Snapchat campaign product profitability.

Some historical Salla order lines carry a variant/product identity that no
longer matches the lightweight product record exactly, even though Products V2
can locate the current product by its SKU or unique product name. In that case
the campaign profitability engine previously treated the line as unpriced.

This compatibility adapter keeps the existing Mezan V2 -> Salla cost priority,
but makes catalog resolution consistent with the Products V2 workspace:

* Salla product IDs and variant IDs, including common source aliases;
* base or variant SKU;
* exact normalized product name only when it is unique in the catalog.

It also recovers the current Salla base/variant cost from the full product
snapshot when the light catalog projection has not yet copied that field. The
adapter is read-only and never writes to Salla, accounting, or Qoyod.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from dashboard_v2_routes import _to_list
from product_v2_routes import PRODUCTS, _number

from . import snapchat_campaign_profitability as profitability

SOURCE_MODE = "snapchat_campaign_current_catalog_salla_cost_v1"
NAME_ALIAS_PREFIX = "__mezan_exact_name__:"


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_product_name(value: Any) -> str:
    text = " ".join(_text(value).replace("_", " ").casefold().split())
    return "".join(character for character in text if character.isalnum() or character.isspace()).strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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
    """Return one catalog product with current Salla costs filled conservatively."""
    row = dict(product or {})
    details = _dict(row.get("raw_salla_details"))
    light = _dict(row.get("raw_salla"))
    if row.get("cost_price_from_salla") in (None, ""):
        recovered = _first_number(
            details.get("cost_price"),
            details.get("cost"),
            light.get("cost_price"),
            light.get("cost"),
        )
        if recovered is not None:
            row["cost_price_from_salla"] = recovered

    current_variants = [dict(value) for value in _list(row.get("variants")) if isinstance(value, dict)]
    by_id = {_text(value.get("id")): value for value in current_variants if _text(value.get("id"))}
    by_sku = {_text(value.get("sku")).casefold(): value for value in current_variants if _text(value.get("sku"))}
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
            recovered = _first_number(raw.get("cost_price"), raw.get("cost"))
            if recovered is not None:
                target["cost_price_from_salla"] = recovered
    row["variants"] = current_variants
    return row


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


def resolve_campaign_line_product(
    item: dict[str, Any],
    *,
    products_by_id: dict[str, dict[str, Any]],
    products_by_variant: dict[str, dict[str, Any]],
    products_by_sku: dict[str, dict[str, Any]],
    base_resolver: Any,
) -> dict[str, Any] | None:
    """Resolve an order line without guessing across duplicate product names."""
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
            "salla_product_id",
            "parent_product_id",
            "product_id",
            "source_product_id",
            "source_id",
            "id",
        ),
    ):
        if value in products_by_id:
            return products_by_id[value]

    for value in _candidate_values(
        item,
        (
            "variant_id",
            "product_variant_id",
            "source_variant_id",
            "sku_id",
            "id",
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
    if name:
        return products_by_sku.get(f"{NAME_ALIAS_PREFIX}{name}")
    return None


async def enrich_cost_context(db: Any, user_id: str, context: dict[str, Any]) -> dict[str, Any]:
    """Merge current full product snapshots and add unique exact-name aliases."""
    rows = await _to_list(
        db[PRODUCTS].find(
            {"user_id": user_id},
            {
                "_id": 0,
                "salla_product_id": 1,
                "mezan_product_id": 1,
                "name": 1,
                "sku": 1,
                "main_image": 1,
                "cost_price_from_salla": 1,
                "variants": 1,
                "raw_salla": 1,
                "raw_salla_details": 1,
            },
        ),
        100_000,
    )
    enriched_rows = [enrich_current_salla_cost(row) for row in rows]
    products_by_id = context["products_by_id"]
    products_by_variant = context["products_by_variant"]
    products_by_sku = context["products_by_sku"]

    names: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for product in enriched_rows:
        product_id = _text(product.get("salla_product_id"))
        if product_id:
            products_by_id[product_id] = product
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

    for name, products in names.items():
        identities = {
            _text(product.get("salla_product_id"))
            or _text(product.get("mezan_product_id"))
            for product in products
        }
        identities.discard("")
        if len(identities) == 1:
            products_by_sku[f"{NAME_ALIAS_PREFIX}{name}"] = products[0]

    context["catalog_cost_resolution"] = {
        "source_mode": SOURCE_MODE,
        "current_catalog_products": len(enriched_rows),
        "unique_name_aliases": sum(1 for products in names.values() if len({
            _text(product.get("salla_product_id")) or _text(product.get("mezan_product_id"))
            for product in products
        } - {""}) == 1),
        "read_only": True,
    }
    return context


def install_current_catalog_salla_cost_resolution() -> None:
    """Patch the shared profitability resolver once at router composition time."""
    current_loader = profitability._load_cost_context
    if getattr(current_loader, "_mezan_current_catalog_cost", False):
        return
    current_resolver = profitability._line_product

    async def load_context(db: Any, user_id: str) -> dict[str, Any]:
        context = await current_loader(db, user_id)
        return await enrich_cost_context(db, user_id, context)

    def resolve_line(
        item: dict[str, Any],
        *,
        products_by_id: dict[str, dict[str, Any]],
        products_by_variant: dict[str, dict[str, Any]],
        products_by_sku: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        return resolve_campaign_line_product(
            item,
            products_by_id=products_by_id,
            products_by_variant=products_by_variant,
            products_by_sku=products_by_sku,
            base_resolver=current_resolver,
        )

    load_context._mezan_current_catalog_cost = True  # type: ignore[attr-defined]
    resolve_line._mezan_current_catalog_cost = True  # type: ignore[attr-defined]
    profitability._load_cost_context = load_context
    profitability._line_product = resolve_line


__all__ = [
    "NAME_ALIAS_PREFIX",
    "SOURCE_MODE",
    "enrich_cost_context",
    "enrich_current_salla_cost",
    "install_current_catalog_salla_cost_resolution",
    "normalize_product_name",
    "resolve_campaign_line_product",
]
