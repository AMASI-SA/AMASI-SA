"""Real bounded Mongo projection preserves proof and refuses truncated stock."""
from copy import deepcopy
import os
from urllib.parse import urlsplit
import uuid

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from integrations_control_center.google_ads_creation_inventory import PRODUCT_PROJECTION
from product_inventory_proof import inventory_readiness
from product_v2_routes import normalize_salla_product
from tests.test_google_ads_creation_inventory import NOW, detailed, prepare, raw_product, variant


@pytest_asyncio.fixture
async def catalog():
    uri = os.getenv("GOOGLE_ADS_TEST_MONGO_URI")
    if not uri:
        pytest.skip("explicit disposable local Mongo required")
    assert urlsplit(uri).hostname in {"127.0.0.1", "localhost", "::1"}
    client = AsyncIOMotorClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000)
    await client.admin.command("ping")
    name = "google_inventory_proof_test_" + uuid.uuid4().hex
    try:
        yield client[name].mezan_products_v2
    finally:
        await client.drop_database(name)
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("variants", [[], [variant()]])
async def test_projected_actual_details_writer_row_is_eligible_without_raw_inventory(catalog, variants):
    product = detailed(raw_product(variants=variants))
    await catalog.insert_one(deepcopy(product))
    projected = await catalog.find_one({"salla_product_id": "1"}, PRODUCT_PROJECTION)
    assert projected["variants_count"] == len(variants)
    assert inventory_readiness(projected, NOW, 3600)[0] == "available"
    assert prepare(projected)["ready_for_paused_preview"]
    for key in ("raw_salla", "raw_salla_details"):
        assert set(projected[key]) == {"urls"} and set(projected[key]["urls"]) == {"product"}


@pytest.mark.asyncio
async def test_actual_slice_cannot_grant_completeness_to_more_than_fifty_variants(catalog):
    product = detailed(raw_product(variants=[variant(1936825372+i) for i in range(51)]))
    await catalog.insert_one(product)
    projected = await catalog.find_one({"salla_product_id": "1"}, PRODUCT_PROJECTION)
    assert len(projected["variants"]) == 50 and projected["variants_count"] == 51
    assert inventory_readiness(projected, NOW, 3600) == ("unproved", None)


@pytest.mark.asyncio
async def test_recent_sync_set_retains_old_metadata_but_cannot_reuse_it(catalog):
    raw = raw_product(variants=[variant()])
    product = detailed(raw)
    await catalog.insert_one(product)
    light = {key: value for key, value in raw.items() if key not in {"variants", "options"}}
    await catalog.update_one({"salla_product_id": "1"}, {"$set": normalize_salla_product(light, user_id="owner", synced_at=NOW)})
    projected = await catalog.find_one({"salla_product_id": "1"}, PRODUCT_PROJECTION)
    assert projected["inventory_evidence_v1"]["source_revision"] != projected["source_revision"]
    assert projected["details_synced_at"] == projected["last_synced_at"]
    assert inventory_readiness(projected, NOW, 3600) == ("unproved", None)
