"""One category-first supplier contract for web and Android; no data migration."""
from typing import Any

from fastapi import HTTPException

from component_category_policy import COMPONENT_CATEGORIES, unique_category_ids
from product_option_cost_routes import RESOURCES
from product_v2_routes import PRODUCTS, _text


async def supplier_category_selection(
    db: Any, *, user_id: str, category_ids: list[str], service_ids: list[str],
) -> tuple[list[dict], list[dict]]:
    category_ids = unique_category_ids(category_ids)
    service_ids = unique_category_ids(service_ids)
    if not category_ids:
        raise HTTPException(422, detail={"code": "supplier_category_required"})
    categories = await db[COMPONENT_CATEGORIES].find(
        {"user_id": user_id, "id": {"$in": category_ids}, "status": {"$ne": "inactive"}},
        {"_id": 0, "id": 1, "name": 1},
    ).to_list(len(category_ids))
    by_category = {_text(row.get("id")): row for row in categories}
    if any(category_id not in by_category for category_id in category_ids):
        raise HTTPException(422, detail={"code": "supplier_category_not_found"})
    rows = await db[RESOURCES].find(
        {"user_id": user_id, "id": {"$in": service_ids}, "kind": "service",
         "track_inventory": {"$ne": True}, "status": {"$ne": "inactive"}},
        {"_id": 0, "id": 1, "name": 1, "code": 1, "unit": 1, "unit_cost": 1,
         "requires_preparation": 1, "category_ids": 1},
    ).to_list(max(1, len(service_ids))) if service_ids else []
    by_service = {_text(row.get("id")): row for row in rows}
    if any(service_id not in by_service for service_id in service_ids):
        raise HTTPException(422, detail={"code": "supplier_service_not_found"})
    allowed = set(category_ids)
    if any(not allowed.intersection(unique_category_ids(row.get("category_ids"))) for row in rows):
        raise HTTPException(422, detail={"code": "supplier_service_category_mismatch"})
    return (
        [{"category_id": cid, "category_name": _text(by_category[cid].get("name")) or cid}
         for cid in category_ids],
        [{"service_id": sid, "service_name": _text(by_service[sid].get("name")) or sid,
          "service_code": _text(by_service[sid].get("code")) or None,
          "unit": _text(by_service[sid].get("unit")) or "job",
          "unit_cost": by_service[sid].get("unit_cost"),
          "requires_preparation": bool(by_service[sid].get("requires_preparation")),
          "category_ids": unique_category_ids(by_service[sid].get("category_ids"))}
         for sid in service_ids],
    )


async def require_supplier_product_categories(db: Any, *, user_id: str, supplier: dict, pieces: list[dict]) -> None:
    """Category-only suppliers can accept their products without service bindings.

    Preserve existing service-only suppliers until an operator explicitly assigns
    their categories. Never infer a broader dispatch scope from service names.
    """
    allowed = set(unique_category_ids(supplier.get("category_ids")))
    if not allowed:
        return
    identifiers = list({_text(piece.get("product_id")) for piece in pieces} - {""})
    products = await db[PRODUCTS].find(
        {"user_id": user_id, "$or": [
            {field: {"$in": identifiers}} for field in ("id", "mezan_product_id", "salla_product_id")
        ]},
        {"_id": 0, "id": 1, "mezan_product_id": 1, "salla_product_id": 1, "cost_category_id": 1},
    ).to_list(max(1, len(identifiers) * 3)) if identifiers else []
    by_identifier: dict[str, list[dict]] = {}
    for product in products:
        for identifier in {_text(product.get(field)) for field in ("id", "mezan_product_id", "salla_product_id")} - {""}:
            by_identifier.setdefault(identifier, []).append(product)
    for piece in pieces:
        matches = by_identifier.get(_text(piece.get("product_id")), [])
        if len(matches) != 1 or not _text(matches[0].get("cost_category_id")):
            raise HTTPException(409, detail={"code": "supplier_dispatch_product_category_required",
                "message": "حدّد تصنيف المنتج في إعدادات المنتجات قبل إسناده لهذا المورد."})
        if _text(matches[0].get("cost_category_id")) not in allowed:
            raise HTTPException(409, detail={"code": "supplier_dispatch_category_mismatch",
                "message": "تصنيف المنتج غير مرتبط بالمورد المحدد. اختر موردًا من تصنيف المنتج."})
