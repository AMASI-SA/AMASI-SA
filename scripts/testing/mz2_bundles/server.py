"""Loopback-only UI acceptance fixture using real bundle routers and Mongo.

Run with PYTHONPATH=backend and MZ2_TEST_MONGO_URI pointing at a local replica.
The only catalog data is synthetic; no production environment is loaded.
"""
import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI
import uvicorn

from tests.test_bundle_fulfillment_v2 import BundleMongoTests
from bundle_catalog_v2 import make_bundle_router
from bundle_fulfillment_v2 import make_bundle_operations_router

assert urlsplit(os.environ["MZ2_TEST_MONGO_URI"]).hostname in {"127.0.0.1", "localhost"}
fixture = BundleMongoTests()


@asynccontextmanager
async def lifespan(app):
    await fixture.asyncSetUp()
    db = fixture.db
    await db.mezan_products_v2.update_one({"id": "root"}, {"$set": {"name": "بكج الهدية الشتوية", "options": [{
        "id": "size", "name": "اختر نوع البكج", "type": "radio", "required": True,
        "values": [{"id": "two", "name": "بكج منتجين"}, {"id": "three", "name": "بكج 3 منتجات"}, {"id": "four", "name": "بكج 4 منتجات"}]}]}})
    for pid, name, cost in [("a", "هودي شتوي", 50), ("b", "سلسال", 30), ("c", "عطر", 25), ("d", "ساعة", 40)]:
        await db.mezan_products_v2.update_one({"id": pid}, {"$set": {"name": name, "cost_price": cost}})
    from bundle_catalog_v2 import COST_PROFILES, PRODUCT_RESOURCE_BINDINGS, RESOURCES
    for pid, rid, name, cost in [("a", "hoodie-bag", "كيس الهودي", 2), ("b", "necklace-box", "علبة السلسال", 3), ("b", "necklace-bag", "كيس السلسال", 1)]:
        await db[RESOURCES].insert_one({"user_id": "owner", "id": rid, "name": name, "unit_cost": cost, "track_inventory": True, "unit": "piece", "status": "active"})
        await db[PRODUCT_RESOURCE_BINDINGS].insert_one({"user_id": "owner", "salla_product_id": pid, "id": "bind-" + rid, "resource_id": rid, "quantity": 1})
        await db[COST_PROFILES].update_one({"user_id": "owner", "salla_product_id": pid}, {"$set": {"composition_components.product_resource:bind-" + rid: {"cost_treatment": "independent", "assembly_origin": "inhouse", "evidence_ref": "Synthetic acceptance fixture"}}}, upsert=True)
    await db.mezan_cost_resources_v2.insert_one({"user_id": "owner", "id": "pack", "name": "كيس وتغليف وبطاقة", "kind": "service", "track_inventory": False, "unit_cost": 20, "status": "active"})
    await db[RESOURCES].update_one({"id": "pack"}, {"$set": {"name": "تغليف البكج الخاص", "unit_cost": 5}})
    def linked(pid, bag):
        return {**fixture.item(pid, "auto"), "component_overrides": [{"binding_kind": "product_resource", "binding_id": "bind-" + bag, "use": False}]}
    items = [{"kind": "resource", "resource_id": "pack", "quantity": 1, "sourcing_mode": "stock"}, linked("a", "hoodie-bag"), linked("b", "necklace-bag")]
    for option, products in [("three", ["c"]), ("four", ["c", "d"])]:
        for pid in products:
            items.append({**fixture.item(pid, "supplier", "supplier-a" if pid == "c" else "supplier-b"),
                          "condition": {"option_id": "size", "value_id": option}})
    await fixture.define(items, schema_version=2)
    await fixture.lot("a", 5)
    await fixture.lot("b", 5)
    from stock_component_consumption_service import reserve_component_stock
    await reserve_component_stock(db, merchant_id="owner", order_id="100", lines=[{
        "order_line_id": "line", "product_id": "root", "quantity": 1,
        "options_raw": [{"option_id": "size", "value_id": "four"}]}],
        source_version=1, source_created_at="2026-10-16T00:00:00+00:00")
    await db.mezan_products_v2.insert_one({"user_id": "owner", "id": "pullover",
        "mezan_product_id": "pullover", "salla_product_id": "pullover", "name": "بلوفر شتوي", "cost_price": 50,
        "options": [{"id": "count", "name": "عدد البلوفرات", "type": "radio", "required": True,
            "values": [{"id": str(n), "name": label} for n, label in [(1, "بلوفر واحد"), (2, "بلوفرين"), (3, "3 بلوفرات"), (4, "4 بلوفرات")]]}]})
    await fixture.define([{"kind": "resource", "resource_id": "pack", "quantity": 1, "sourcing_mode": "stock"},
        *[{"kind": "current_product", "quantity": n, "sourcing_mode": "stock",
            "condition": {"option_id": "count", "value_id": str(n)}} for n in range(1, 5)]], pid="pullover")
    await fixture.lot("pullover", 12)
    await reserve_component_stock(db, merchant_id="owner", order_id="101", lines=[{
        "order_line_id": "pullover-line", "product_id": "pullover", "quantity": 3,
        "options_raw": [{"option_id": "count", "value_id": "2"}]}],
        source_version=1, source_created_at="2026-10-16T00:00:00+00:00")
    async def user():
        return {"id": "owner", "role": "owner"}
    app.include_router(make_bundle_router(db, user), prefix="/api")
    app.include_router(make_bundle_operations_router(db, user), prefix="/api")
    yield
    await fixture.asyncTearDown()


app = FastAPI(lifespan=lifespan)


@app.get("/api/products-v2")
async def products(q: str = ""):
    rows = await fixture.db.mezan_products_v2.find({"user_id": "owner"}, {"_id": 0}).to_list(100)
    return {"items": [r for r in rows if q.casefold() in r["name"].casefold()]}


@app.get("/api/products-v2/{product_id}")
async def product(product_id: str):
    return await fixture.db.mezan_products_v2.find_one({"user_id": "owner", "id": product_id}, {"_id": 0})


@app.get("/api/components-v2/workspace")
async def components():
    return {"components": await fixture.db.mezan_cost_resources_v2.find({"user_id": "owner"}, {"_id": 0}).to_list(100)}


@app.get("/api/suppliers-v2/workspace")
async def suppliers():
    return {"suppliers": [{"id": "supplier-a", "name": "مورد العطور"}, {"id": "supplier-b", "name": "مورد الساعات"}]}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8017)
