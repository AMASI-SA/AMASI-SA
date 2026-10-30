"""P2 catalogue provenance over real HTTP/Mongo; isolated read-only acceptance."""
import os
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
import pytest

from accounting_onboarding import install_onboarding_routes


@pytest.mark.asyncio
async def test_variant_source_provenance_owner_isolation_and_zero_mutations_real_mongo():
    uri = os.environ.get("MZ2_TEST_MONGO_URI")
    if not uri:
        pytest.skip("Dedicated loopback replica set required")
    assert urlparse(uri).hostname in {"127.0.0.1", "localhost"}
    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    db = mongo["mz2_p2_catalog_" + uuid4().hex]
    try:
        assert (await mongo.admin.command("hello")).get("setName")
        owner = {"id": "p2-owner", "role": "owner", "is_active": True}
        await db.users.insert_one(owner)
        option = {"id": "size", "type": "select", "name": "Size", "values": [{"id": "s", "name": "Small"}]}
        text_option = {"id": "name", "type": "text", "name": "Inscription"}
        old_text = {"options": [text_option], "variants": [], "updated_at": "2026-10-01T00:00:00Z"}
        fresh_stock = {"options": [option], "updated_at": "2026-10-02T00:00:00Z"}
        await db.mezan_products_v2.insert_many([
            {"user_id": owner["id"], "mezan_product_id": "missing", "options": [option], "variants": [], "variants_count": 0},
            {"user_id": owner["id"], "mezan_product_id": "known", "options": [option], "variants": [{"id": "size-s"}]},
            {"user_id": owner["id"], "mezan_product_id": "invalid", "options": [option], "variants": [{"name": "No canonical ID"}]},
            {"user_id": owner["id"], "mezan_product_id": "empty", "options": [text_option], "variants": [],
             "raw_salla_details": {"options": [text_option], "variants": []}},
            {"user_id": owner["id"], "mezan_product_id": "plain", "variants": []},
            {"user_id": owner["id"], "mezan_product_id": "stale-details", "options": [text_option], "variants": [],
             "raw_salla_details": old_text, "raw_salla": fresh_stock},
            {"user_id": owner["id"], "mezan_product_id": "late-empty", "options": [option], "variants": [],
             "raw_salla_details": {**fresh_stock, "variants": [{"id": "canonical-s"}]}, "raw_salla": old_text},
            {"user_id": owner["id"], "mezan_product_id": "unordered", "options": [text_option], "variants": [],
             "raw_salla_details": {"options": [text_option], "variants": []}, "raw_salla": {"options": [option]}},
            {"user_id": "foreign-owner", "mezan_product_id": "foreign", "variants": [{"id": "private"}]},
        ])
        async def actor():
            return owner
        async def fingerprint():
            return {name: await db[name].find({}).sort("_id", 1).to_list(None) for name in await db.list_collection_names()}
        router = APIRouter()
        install_onboarding_routes(router, db, actor, {})
        app = FastAPI()
        app.include_router(router)
        before = await fingerprint()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            first = await http.get("/accounting-module/onboarding/inventory-catalog")
            second = await http.get("/accounting-module/onboarding/inventory-catalog")
            assert first.status_code == second.status_code == 200
            assert first.json() == second.json()
        products = {row["id"]: row for row in first.json()["products"]}
        assert set(products) == {"missing", "known", "invalid", "empty", "plain", "stale-details", "late-empty", "unordered"}
        assert products["missing"]["variants_required"] and products["missing"]["variants_source_missing"]
        assert [v["id"] for v in products["known"]["variants"]] == ["size-s"]
        assert products["invalid"]["variants"] == [] and products["invalid"]["unresolved_variants_count"] == 1
        assert products["empty"]["variants_source_available"] and not products["empty"]["variants_required"]
        assert not products["plain"]["variants_required"]
        for key in ("stale-details", "unordered"):
            assert products[key]["variants_required"] and products[key]["variants_source_missing"]
            assert products[key]["variants"] == []
        assert products["stale-details"]["options"] == [option]
        assert products["late-empty"]["options"] == [option]
        assert products["late-empty"]["variants_required"] and not products["late-empty"]["variants_source_missing"]
        assert [v["id"] for v in products["late-empty"]["variants"]] == ["canonical-s"]
        assert await fingerprint() == before
    finally:
        await mongo.drop_database(db.name)
        mongo.close()
