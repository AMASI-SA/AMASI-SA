from copy import deepcopy

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

from product_component_category_share import (
    ShareComponentRequest, share_and_link_component, PRODUCTS, RESOURCES,
    COMPONENT_CATEGORIES, PRODUCT_RESOURCE_BINDINGS, BINDINGS, AUDIT,
)


async def seed():
    db = AsyncMongoMockClient().component_share
    await db[PRODUCTS].insert_one({"id": "shawl", "salla_product_id": "123", "user_id": "owner",
        "name": "Shawl", "cost_category_id": "clothes"})
    await db[COMPONENT_CATEGORIES].insert_many([
        {"id": category, "name": category, "user_id": "owner", "status": "active"}
        for category in ["clothes", "packing", "gifts"]])
    await db[RESOURCES].insert_one({"id": "box", "user_id": "owner", "name": "Box", "kind": "stock_component",
        "category_ids": ["packing", "gifts"], "track_inventory": True, "unit_cost": 35, "stock_quantity": 70,
        "status": "active", "cost_source": "purchase_invoice"})
    await db.historical_invoices.insert_one({"id": "old", "total": 350, "resource_id": "box"})
    return db


async def share(db, **overrides):
    args = {"user_id": "owner", "product_id": "shawl", "resource_id": "box",
        "payload": ShareComponentRequest(source_category_id="packing", product_category_id="clothes")}
    return await share_and_link_component(db, **{**args, **overrides})


@pytest.mark.asyncio
async def test_shares_same_component_and_links_once_without_changing_cost_stock_or_history():
    db = await seed()
    before = await db[RESOURCES].find_one({"id": "box"}, {"_id": 0})
    invoice = await db.historical_invoices.find_one({}, {"_id": 0})
    first = await share(db)
    second = await share(db)
    resource = await db[RESOURCES].find_one({"id": "box"}, {"_id": 0})
    assert resource["category_ids"] == ["packing", "gifts", "clothes"]
    assert {key: value for key, value in resource.items() if key not in {"category_ids", "updated_at"}} == {
        key: value for key, value in before.items() if key not in {"category_ids", "updated_at"}}
    assert await db[RESOURCES].count_documents({}) == 1
    assert await db[PRODUCT_RESOURCE_BINDINGS].count_documents({}) == 1
    binding = await db[PRODUCT_RESOURCE_BINDINGS].find_one({})
    assert binding["salla_product_id"] == "123" and binding["resource_id"] == "box" and binding["quantity"] == 1
    assert first["idempotent"] is False and second["idempotent"] is True
    assert await db[AUDIT].count_documents({}) == 1
    assert await db.historical_invoices.find_one({}, {"_id": 0}) == invoice


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["foreign", "no_category", "stale_category", "service", "inactive", "wrong_source", "option_conflict"])
async def test_invalid_share_cannot_change_resource_or_create_product_link(case):
    db = await seed()
    overrides = {}
    if case == "foreign": overrides["user_id"] = "another-owner"
    if case == "no_category": await db[PRODUCTS].update_one({}, {"$unset": {"cost_category_id": ""}})
    if case == "stale_category": await db[PRODUCTS].update_one({}, {"$set": {"cost_category_id": "gifts"}})
    if case == "service": await db[RESOURCES].update_one({}, {"$set": {"kind": "service", "track_inventory": False}})
    if case == "inactive": await db[RESOURCES].update_one({}, {"$set": {"status": "inactive"}})
    if case == "wrong_source": await db[RESOURCES].update_one({}, {"$set": {"category_ids": ["gifts"]}})
    if case == "option_conflict": await db[BINDINGS].insert_one({"user_id": "owner", "salla_product_id": "123", "mode": "resource", "resource_id": "box"})
    before = deepcopy(await db[RESOURCES].find_one({}, {"_id": 0}))
    with pytest.raises(HTTPException): await share(db, **overrides)
    assert await db[RESOURCES].find_one({}, {"_id": 0}) == before
    assert await db[PRODUCT_RESOURCE_BINDINGS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_manual_share_preserves_existing_group_provenance():
    db = await seed()
    await db[PRODUCT_RESOURCE_BINDINGS].insert_one({"user_id": "owner", "salla_product_id": "123",
        "resource_id": "box", "id": "kept", "group_ids": ["group-one"], "manual_link": False, "quantity": 1})
    await share(db)
    binding = await db[PRODUCT_RESOURCE_BINDINGS].find_one({})
    assert binding["id"] == "kept" and binding["group_ids"] == ["group-one"] and binding["manual_link"] is True
