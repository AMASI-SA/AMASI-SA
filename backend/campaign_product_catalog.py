"""Narrow owner-scoped current catalog identity for campaign research.

raw_salla is replaced by both list sync and details refresh. The separate
raw_salla_details snapshot can be older; neither it nor legacy products may
supply a stale destination when the current snapshot lacks a URL.
"""
from product_v2_routes import PRODUCTS


async def current_catalog_product(db, user_id, product_id):
    product = await db[PRODUCTS].find_one(
        {"user_id": user_id, "salla_product_id": str(product_id)},
        {"_id": 0, "name": 1, "archived": 1, "raw_salla.id": 1,
         "raw_salla.url": 1, "raw_salla.urls.product": 1}, max_time_ms=2000)
    if not product or product.get("archived") is True:
        return None
    raw = product.get("raw_salla") or {}
    if not isinstance(raw, dict) or str(raw.get("id")) != str(product_id):
        return None
    urls = raw.get("urls") if isinstance(raw.get("urls"), dict) else {}
    url = urls.get("product") or raw.get("url")
    return {"name": product.get("name"), "url": url if isinstance(url, str) else None}
