"""Real Mongo derivation behind the frontend component workspace contract.

Unique loopback databases only. HTTP adapters and workspace derivation are real;
no mock computes product_usages or manufactures successful binding identities.
"""
import os
from uuid import uuid4
from urllib.parse import urlparse

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from component_workspace_cost_compat_routes import make_component_workspace_cost_compat_router
from product_option_cost_routes import make_product_option_cost_router, RESOURCES, BINDINGS
from product_fulfillment_routes import make_product_fulfillment_router
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_v2_routes import PRODUCTS

OWNER = "synthetic-workspace-owner"
OTHER = "synthetic-workspace-other"


@pytest_asyncio.fixture
async def workspace_api():
    uri = os.environ["MZ2_TEST_MONGO_URI"]
    parsed = urlparse(uri)
    assert parsed.scheme == "mongodb" and parsed.hostname in {"localhost", "127.0.0.1"}
    assert not parsed.username and not parsed.password and parsed.path in {"", "/"}
    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    db = mongo["component_workspace_derivation_" + uuid4().hex]
    await mongo.admin.command("ping")
    try:
        resources = [{"user_id": OWNER, "id": f"resource-{i}", "code": f"RESOURCE-{i}",
                      "name": f"Synthetic {i}", "track_inventory": i < 4, "status": "active"}
                     for i in range(7)]
        await db[RESOURCES].insert_many(resources + [{**resources[0], "user_id": OTHER, "name": "Foreign"}])
        products = [{"user_id": OWNER, "id": f"product-{i}", "mezan_product_id": f"product-{i}",
                     "salla_product_id": f"salla-{i}", "sku": sku, "name": f"Synthetic product {i}",
                     "options": [{"id": key, "name": key, "values": [{"id": "yes", "name": "Yes"}]}
                                 for key in ("engraving", "gift_wrap")]}
                    for i, sku in enumerate(("AMS10026", "AMS-SECOND"))]
        await db[PRODUCTS].insert_many(products)
        app = FastAPI()
        async def current_user():
            return {"id": OWNER}
        # Production precedence: richer workspace handler precedes compatibility.
        app.include_router(make_component_workspace_cost_compat_router(db, current_user))
        app.include_router(make_product_option_cost_router(db, current_user))
        app.include_router(make_product_fulfillment_router(db, current_user))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield db, client
    finally:
        await mongo.drop_database(db.name)
        mongo.close()


async def workspace(client):
    response = await client.get("/components-v2/workspace")
    assert response.status_code == 200, response.text
    return response.json()


def resource(result, key):
    return next(row for row in result["components"] if row["id"] == key)


@pytest.mark.asyncio
async def test_workspace_derives_multiple_product_usages_and_removes_only_selected_link(workspace_api):
    db, client = workspace_api
    for i, quantity in enumerate((1, 2)):
        response = await client.put(f"/products-v2/product-{i}/resource-links/resource-0", json={"quantity": quantity})
        assert response.status_code == 200, response.text
    # Same resource key in another owner must never add a third usage.
    await db[PRODUCT_RESOURCE_BINDINGS].insert_one({"user_id": OTHER, "id": "foreign-link",
        "salla_product_id": "salla-0", "resource_id": "resource-0", "quantity": 999})
    result = await workspace(client)
    assert result["meta"]["mode"] == "production" and result["meta"]["writes_enabled"] is True
    assert result["meta"]["historical_orders_unchanged"] is True
    assert len(result["components"]) == 7 and len(result["products"]) == 2
    assert sum(row["track_inventory"] for row in result["components"]) == 4
    assert sum(not row["track_inventory"] for row in result["components"]) == 3
    assert all(row["reference_cost"]["amount"] is None for row in result["components"])
    assert all("stock_quantity" not in row and "quantity" not in row for row in result["components"])
    usages = resource(result, "resource-0")["product_usages"]
    assert len(usages) == 2 and len({row["id"] for row in usages}) == 2
    assert {row["product_sku"]: row["quantity"] for row in usages} == {"AMS10026": 1, "AMS-SECOND": 2}
    assert all(row["condition"] is None and row["source"] == "product" for row in usages)
    assert resource(result, "resource-4")["product_usages"] == []
    response = await client.delete("/products-v2/product-0/resource-links/resource-0")
    assert response.status_code == 200, response.text
    usages = resource(await workspace(client), "resource-0")["product_usages"]
    assert len(usages) == 1 and usages[0]["product_id"] == "product-1" and usages[0]["quantity"] == 2
    assert (await db[PRODUCT_RESOURCE_BINDINGS].find_one({"id": "foreign-link"}))["quantity"] == 999


@pytest.mark.asyncio
async def test_shared_value_key_keeps_real_option_binding_ids_and_conditions_distinct(workspace_api):
    db, client = workspace_api
    for option in ("engraving", "gift_wrap"):
        response = await client.put(f"/products-v2/product-0/option-costs/{option}/yes",
            json={"mode": "resource", "resource_id": "resource-4", "quantity": 1})
        assert response.status_code == 200, response.text
    usages = resource(await workspace(client), "resource-4")["product_usages"]
    assert len(usages) == 2 and len({row["id"] for row in usages}) == 2
    assert {(row["condition"]["option_key"], row["condition"]["value_key"]) for row in usages} == {
        ("engraving", "yes"), ("gift_wrap", "yes")}
    assert all(row["product_sku"] == "AMS10026" and row["source"] == "option" and row["quantity"] == 1 for row in usages)
    assert {row["condition"]["option_name"] for row in usages} == {"engraving", "gift_wrap"}
    assert all(row["condition"]["value_name"] == "Yes" for row in usages)
    persisted = await db[BINDINGS].find({"user_id": OWNER}).to_list(10)
    assert {row["id"] for row in usages} == {row["id"] for row in persisted}
    response = await client.delete("/products-v2/product-0/option-costs/engraving/yes")
    assert response.status_code == 200, response.text
    usages = resource(await workspace(client), "resource-4")["product_usages"]
    assert len(usages) == 1 and usages[0]["condition"]["option_key"] == "gift_wrap"


@pytest.mark.asyncio
async def test_unknown_option_is_rejected_without_binding_or_usage(workspace_api):
    db, client = workspace_api
    before = await workspace(client)
    response = await client.put("/products-v2/product-0/option-costs/size/large",
        json={"mode": "resource", "resource_id": "resource-4", "quantity": 1})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "option_value_not_found"
    assert await db[BINDINGS].count_documents({"user_id": OWNER}) == 0
    assert await workspace(client) == before
