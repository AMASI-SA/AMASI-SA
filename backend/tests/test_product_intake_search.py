import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from ai_store_operations_foundation import make_ai_store_operations_router
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import BINDINGS, RESOURCES
from product_v2_routes import PRODUCTS
from web_page_access import FIELD, INTAKE_PAGE


@pytest.mark.asyncio
async def test_intake_combines_category_resource_and_exact_surcharge_filters_before_paging():
    db = AsyncMongoMockClient().intake
    await db.users.insert_one({"id": "store", "role": "owner"})
    await db[RESOURCES].insert_many([
        {"id": "sewing", "user_id": "store", "kind": "service"},
        {"id": "bag", "user_id": "store", "kind": "stock_component"},
        {"id": "stopped", "user_id": "store", "kind": "service", "status": "inactive"},
        {"id": "foreign", "user_id": "another", "kind": "service"},
    ])

    def product(product_id, **fields):
        return {"id": product_id, "salla_product_id": product_id, "name": f"Fur {product_id}", "user_id": "store", **fields}

    def priced_options(value):
        return [{"id": "gift", "values": [{"id": value, "additional_price": 75}]}]

    await db[PRODUCTS].insert_many([
        product("other-merchant", user_id="another", categories=[{"id": 20}], options=priced_options("yes")),
        product("other-category", categories=[{"id": 30}], options=priced_options("yes")),
        product(
            "same-option", categories=[{"id": "20", "parent_id": "10"}],
            options=[{"id": "gift", "values": [{"id": "yes", "price": {"amount": 75}}]}],
        ),
        product("different-option", categories=[{"id": 20}], options=priced_options("different")),
        product("direct", raw_salla={"categories": [{"id": 20}]}),
        product(
            "no-links", categories=[{"id": 20}], price=75,
            options=[{"id": "gift", "values": [{"id": "yes", "price": 0}]}],
            raw_salla={"options": priced_options("yes")},
        ),
        product("archived", categories=[{"id": 20}], archived=True, options=priced_options("yes")),
    ])
    await db[PRODUCTS].insert_many([
        product(f"newer-{index}", name=f"Other product {index}", salla_created_at="2026-10-01", categories=[{"id": 20}])
        for index in range(610)
    ])
    await db[BINDINGS].insert_many([
        {"user_id": "store", "salla_product_id": product_id, "mode": "resource", "resource_id": "sewing",
         "option_id": "gift", "value_id": "yes"}
        for product_id in ("same-option", "different-option", "other-category", "archived")
    ] + [
        {"user_id": "store", "salla_product_id": "no-links", "mode": "direct", "direct_amount": 20,
         "option_id": "gift", "value_id": "yes"},
        {"user_id": "another", "salla_product_id": "same-option", "mode": "resource", "resource_id": "foreign",
         "option_id": "gift", "value_id": "yes"},
    ])
    await db[PRODUCT_RESOURCE_BINDINGS].insert_many([
        {"user_id": "store", "salla_product_id": "same-option", "resource_id": "bag"},
        {"user_id": "store", "salla_product_id": "direct", "resource_id": "sewing"},
        {"user_id": "store", "salla_product_id": "no-links", "resource_id": "stopped"},
        {"user_id": "store", "salla_product_id": "no-links", "resource_id": "foreign"},
    ])

    async def current_user(request: Request):
        return {
            "id": "test-employee", "role": "user", "created_by": "store",
            FIELD: {"owner_id": "store", "grants": [{"id": INTAKE_PAGE, "mode": "read"}]},
        }

    app = FastAPI()
    app.include_router(make_ai_store_operations_router(db, current_user))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async def fetch(**filters):
            response = await client.get("/ai-store-operations/product-intake", params={"status": "all", **filters})
            assert response.status_code == 200, response.text
            return response.json()

        exact = await fetch(category_ids="20", components=True, service_scope="service_surcharge", limit=1, q="Fur")
        assert exact["total"] == 1
        assert exact["items"][0]["mezan_product_id"] == "same-option"
        assert exact["items"][0]["links"]["service_surcharge"] is True
        priced_service = await fetch(category_ids="20", service_scope="service_surcharge")
        assert [row["mezan_product_id"] for row in priced_service["items"]] == ["same-option"]
        priced_options_only = await fetch(category_ids="20", option_surcharge=True)
        assert {row["mezan_product_id"] for row in priced_options_only["items"]} == {"same-option", "different-option"}
        assert (await fetch(category_ids="20", service_scope="independent_service"))["items"][0]["mezan_product_id"] == "direct"
        without_links = await fetch(category_ids="20", services=False, option_surcharge=False, q="Fur")
        assert [row["mezan_product_id"] for row in without_links["items"]] == ["no-links"]
        no_option = await fetch(category_ids="20", services=True, service_scope="no_option_service")
        assert [row["mezan_product_id"] for row in no_option["items"]] == ["direct"]
        same_category = await fetch(category_ids="20", services=True, limit=1)
        next_page = await fetch(category_ids="20", services=True, limit=1, page=2)
        assert same_category["total"] == next_page["total"] == 3
        assert same_category["items"][0]["mezan_product_id"] != next_page["items"][0]["mezan_product_id"]
