"""The direct amount is a per-product surcharge, not a resource unit price."""
import httpx
import pytest
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient

from product_option_cost_routes import BINDINGS, PRODUCTS, RESOURCES, make_product_option_cost_router


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,quantity,expected_quantity,expected_cost", [
    ("direct", 60, 1, 60),
    ("direct", -4, 1, 60),
    ("resource", 3, 3, 180),
])
async def test_save_direct_amount_does_not_inherit_hidden_resource_quantity(
    mode, quantity, expected_quantity, expected_cost,
):
    db = AsyncMongoMockClient().test_direct_option_quantity
    await db[PRODUCTS].insert_one({
        "user_id": "owner", "id": "product", "salla_product_id": "native-product",
        "options": [{"id": "size", "name": "Size", "values": [{"id": "child", "name": "Child"}]}],
    })
    await db[RESOURCES].insert_one({"user_id": "owner", "id": "resource", "status": "active", "unit_cost": 60})
    # Simulate a pre-existing resource binding carrying its legitimate quantity.
    await db[BINDINGS].insert_one({"user_id": "owner", "id": "binding", "salla_product_id": "native-product",
        "option_id": "size", "value_id": "child", "mode": "resource", "resource_id": "resource", "quantity": 60})
    app = FastAPI()
    async def owner():
        return {"id": "owner"}
    app.include_router(make_product_option_cost_router(db, owner))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/products-v2/product/option-costs/size/child", json={
            "mode": mode, "resource_id": "resource", "direct_amount": 60, "quantity": quantity,
        })
    assert response.status_code == 200, response.text
    stored = await db[BINDINGS].find_one({"id": "binding"})
    assert stored["quantity"] == expected_quantity
    assert response.json()["binding"]["resolved_amount"] == expected_cost
    if mode == "direct":
        # Reporting/operational readers multiply these stored fields.
        assert stored["direct_amount"] * stored["quantity"] == 60
        assert stored["resource_id"] is None
