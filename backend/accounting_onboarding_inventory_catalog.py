"""Read-only opening inventory catalogue. No index creation, sync, or legacy fallback.

Warehouse V2 imports warehouse_location_routes' collection constants; those
collections (despite their unversioned names) are its authoritative store.
"""
from typing import Any
from fastapi import HTTPException

PRODUCTS = "mezan_products_v2"
COMPONENTS = "mezan_cost_resources_v2"
CATEGORIES = "mezan_component_categories_v2"
LOCATIONS = "warehouse_locations"
WAREHOUSES = "warehouse_locations_warehouses"
LIMIT = 5000


def _active(row):
    return row.get("archived") is not True and row.get("status") != "inactive" and row.get("is_active") is not False


def _list(value):
    return value if isinstance(value, list) else []


def _image(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return next((_image(value.get(k)) for k in ("url", "original", "src", "thumbnail") if _image(value.get(k))), None)
    return None


def _options(row):
    result = []
    for option in _list(row.get("options")):
        if not isinstance(option, dict) or option.get("id") is None:
            continue
        values = [{"id": str(v["id"]), "name": v.get("name") or v.get("value") or v.get("label"), "image": _image(v.get("image") or v.get("image_url"))} for v in _list(option.get("values") or option.get("options")) if isinstance(v, dict) and v.get("id") is not None]
        result.append({"id": str(option["id"]), "name": option.get("name") or option.get("title") or option.get("label"), "values": values})
    return result


def _variants(row):
    result = []
    options = _options(row)
    for variant in _list(row.get("variants")):
        if not isinstance(variant, dict) or variant.get("id") is None:
            continue  # Never manufacture a selectable inventory identity.
        selections = variant.get("selections") or variant.get("options") or variant.get("values") or variant.get("attributes") or []
        if isinstance(selections, dict):
            selections = [{"name": k, "value": v} for k, v in selections.items()]
        resolved = []
        for s in _list(selections):
            if not isinstance(s, dict):
                continue
            option = next((o for o in options if str(o["id"]) == str(s.get("option_id"))), {})
            value = next((v for v in option.get("values", []) if str(v["id"]) == str(s.get("value_id"))), {})
            resolved.append({"name": s.get("name") or s.get("option_name") or option.get("name"), "value": s.get("value") or s.get("value_name") or value.get("name")})
        result.append({"id": str(variant["id"]), "name": variant.get("name") or variant.get("title"), "display_name": variant.get("display_name"), "sku": variant.get("sku"), "barcode": variant.get("barcode") or variant.get("gtin"), "image": _image(variant.get("image") or variant.get("image_url")), "selections": resolved})
    return result


def _product(row):
    return {"id": row.get("mezan_product_id") or row.get("id"), "mezan_product_id": row.get("mezan_product_id"), "salla_product_id": row.get("salla_product_id"), "name": row.get("name"), "sku": row.get("sku"), "barcode": row.get("barcode"), "main_image": _image(row.get("main_image")) or next((_image(image) for image in _list(row.get("images")) if _image(image)), None), "options": _options(row), "variants": _variants(row), "variants_count": max(int(row.get("variants_count") or 0), len(_list(row.get("variants")))), "variants_required": bool(row.get("variants_required"))}


async def get_inventory_catalog(db: Any, owner: str) -> dict:
    warnings = []
    async def read(collection):
        rows = await db[collection].find({"user_id": str(owner)}, {"_id": 0}).sort("id", 1).to_list(length=LIMIT + 1)
        if len(rows) > LIMIT:
            warnings.append(f"{collection}_catalog_truncated")
        return rows[:LIMIT]
    products = await read(PRODUCTS)
    components = await read(COMPONENTS)
    categories = await read(CATEGORIES)
    warehouses = {str(w["id"]): w for w in await read(WAREHOUSES) if w.get("id") and _active(w)}
    locations = await read(LOCATIONS)
    return {
        "products": [_product(p) for p in products if _active(p) and (p.get("mezan_product_id") or p.get("id"))],
        "components": [{k: c.get(k) for k in ("id", "name", "code", "unit", "kind", "category_ids", "track_inventory")} for c in components if _active(c) and c.get("track_inventory") is True and c.get("kind") != "service"],
        "categories": [{"id": c.get("id"), "name": c.get("name")} for c in categories if _active(c)],
        "locations": [{"id": l.get("id"), "warehouse_id": l.get("warehouse_id"), "warehouse_name": warehouses[str(l.get("warehouse_id"))].get("name"), "code": l.get("code"), "name": l.get("name"), "barcode": l.get("barcode") or l.get("qr_payload")} for l in locations if l.get("state") != "disabled" and str(l.get("warehouse_id")) in warehouses],
        "warnings": warnings,
        "sources": {"products": PRODUCTS, "components": COMPONENTS, "categories": CATEGORIES, "locations": LOCATIONS, "warehouses": WAREHOUSES},
        "read_only": True,
    }


async def validate_inventory_setup(db: Any, owner: str, rows: list) -> None:
    """Accept incomplete draft facts, but reject any selected noncanonical ID."""
    async def lookup(collection, identity, keys=("id",)):
        if not identity:
            return None
        query = {"user_id": str(owner), "$or": [{k: str(identity)} for k in keys]}
        return await db[collection].find_one(query, {"_id": 0})
    def invalid(index, field):
        raise HTTPException(422, detail={"code": "onboarding_inventory_identity_invalid", "stage": 10, "row": index, "field": field, "message": "هوية المخزون المختارة غير موجودة أو غير نشطة في كتالوج ميزان 2."})
    identities = set()
    for index, row in enumerate(rows):
        if row.get("item_type") == "PRODUCT":
            product = await lookup(PRODUCTS, row.get("product_id"), ("mezan_product_id", "id"))
            if row.get("product_id") and (not product or not _active(product)):
                invalid(index, "product_id")
            if row.get("variant_id") and (not product or str(row["variant_id"]) not in {v["id"] for v in _variants(product)}):
                invalid(index, "variant_id")
            selected = row.get("selected_options") or {}
            if not isinstance(selected, dict):
                invalid(index, "selected_options")
            for key, value in selected.items():
                option = next((o for o in _options(product or {}) if o["id"] == str(key)), {})
                if str(value) not in {v["id"] for v in option.get("values", [])}:
                    invalid(index, "selected_options")
            if row.get("variant_id") and selected:
                invalid(index, "selected_options")
            identity = ("product", str((product or {}).get("mezan_product_id") or (product or {}).get("id") or ""), str(row.get("variant_id") or ""), tuple(sorted(selected.items()))) if product else None
        elif row.get("item_type") == "STOCK_COMPONENT":
            component = await lookup(COMPONENTS, row.get("resource_id"))
            if row.get("resource_id") and (not component or not _active(component) or component.get("track_inventory") is not True or component.get("kind") == "service"):
                invalid(index, "resource_id")
            if row.get("category_id") and (not component or str(row["category_id"]) not in [str(c) for c in component.get("category_ids", [])]):
                # A category selection before a component is a valid partial draft.
                category = await lookup(CATEGORIES, row["category_id"])
                if component or not category or not _active(category):
                    invalid(index, "category_id")
            if row.get("product_id") or row.get("variant_id") or row.get("selected_options"):
                invalid(index, "item_type")
            identity = ("component", str(row.get("resource_id"))) if component else None
        else:
            invalid(index, "item_type")
        if identity and identity in identities:
            invalid(index, "duplicate_identity")
        if identity:
            identities.add(identity)
        for a in row.get("allocations") or []:
            if a.get("location_id"):
                location = await lookup(LOCATIONS, a["location_id"])
                warehouse = await lookup(WAREHOUSES, (location or {}).get("warehouse_id"))
                if not location or location.get("state") == "disabled" or not warehouse or not _active(warehouse):
                    invalid(index, "allocations.location_id")
