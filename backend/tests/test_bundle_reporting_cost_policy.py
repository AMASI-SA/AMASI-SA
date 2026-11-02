import asyncio
import os
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI, HTTPException
from mongomock_motor import AsyncMongoMockClient
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure

import bundle_reporting_cost_policy as policy


OWNER = "synthetic-owner"
PRODUCT = "synthetic-shawl"


async def seed(db, *, with_profile=True):
    await db.mezan_products_v2.insert_one({
        "user_id": OWNER, "salla_product_id": PRODUCT, "mezan_product_id": "mp-shawl",
        "id": "local-shawl", "name": "Synthetic shawl", "options": [], "variants": [],
    })
    await db[policy.BUNDLES].insert_one({
        "user_id": OWNER, "product_id": PRODUCT, "revision": 3, "schema_version": 1,
        "product_type": "composite", "items": [{"kind": "product", "product_id": "rose", "quantity": "1"}],
    })
    if with_profile:
        await db[policy.COST_PROFILES].insert_one({
            "user_id": OWNER, "salla_product_id": PRODUCT, "base_cost": 15,
            "variant_costs": {"one": 17}, "notes": "existing evidence",
            "composition_components": {"option_resource:name": {"cost_treatment": "independent", "assembly_origin": "supplier", "evidence_ref": "existing service"}},
        })
    for name in ("mezan_product_operation_profiles_v2", "mezan_product_option_cost_bindings_v2",
                 "mezan_product_resource_bindings_v2", "mezan_cost_resources_v2",
                 "mezan_component_order_lifecycle_v1", "mezan_component_consumption_plans_v1",
                 "mezan_component_consumption_units_v1", "mezan_preparation_pieces_v1",
                 "mezan_supplier_invoices_v2", "mezan_journal_entries", "warehouse_locations",
                 "mezan_product_cost_revisions_v2"):
        await db[name].insert_one({"_id": "sentinel", "user_id": OWNER, "untouched": True})


def payload(**overrides):
    return {"expected_revision": 0, "expected_bundle_revision": 3,
            "include_parent_base": True, "evidence_ref": "Approved base cost 15 SAR", **overrides}


async def stored(db):
    return {name: await db[name].find({}).sort("_id", 1).to_list(100)
            for name in await db.list_collection_names() if not name.startswith("system.")}


@pytest_asyncio.fixture
async def db(monkeypatch):
    db = AsyncMongoMockClient().bundle_reporting_policy_test

    async def local(database, owner, callback, *, profile):
        assert owner == OWNER and profile == "bundle_policy"
        return await callback(database)

    monkeypatch.setattr(policy, "operational_owner", local)
    await seed(db)
    return db


@pytest.mark.asyncio
async def test_read_is_default_disabled_and_performs_no_writes(db):
    before = await stored(db)
    assert await policy.get_bundle_reporting_cost_policy(db, OWNER, "mp-shawl") == {
        "revision": 0, "bundle_revision": 3, "include_parent_base": False, "evidence_ref": "",
    }
    assert await stored(db) == before


@pytest.mark.asyncio
async def test_save_changes_only_reporting_field_and_preserves_all_operations(db):
    before = await stored(db)
    result = await policy.save_bundle_reporting_cost_policy(db, OWNER, PRODUCT, payload(), "actor")
    after = await stored(db)
    saved = after[policy.COST_PROFILES][0].pop(policy.POLICY_FIELD)
    assert after == before
    assert result == {"revision": 1, "bundle_revision": 3, "include_parent_base": True,
                      "evidence_ref": "Approved base cost 15 SAR"}
    assert saved == {**{key: result[key] for key in ("revision", "include_parent_base", "evidence_ref")},
                     "updated_by": "actor", "updated_at": saved["updated_at"]}
    assert saved["updated_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_disabling_retains_evidence_and_advances_audit_revision(db):
    await policy.save_bundle_reporting_cost_policy(db, OWNER, PRODUCT, payload(), "actor-one")
    result = await policy.save_bundle_reporting_cost_policy(
        db, OWNER, PRODUCT, payload(expected_revision=1, include_parent_base=False, evidence_ref="  "), "actor-two")
    assert result["revision"] == 2 and result["include_parent_base"] is False
    assert result["evidence_ref"] == "Approved base cost 15 SAR"
    profile = await db[policy.COST_PROFILES].find_one({"user_id": OWNER})
    assert profile[policy.POLICY_FIELD]["updated_by"] == "actor-two"
    assert profile["base_cost"] == 15


@pytest.mark.asyncio
async def test_first_policy_can_create_cost_profile_without_inventing_base_price(db):
    await db[policy.COST_PROFILES].delete_many({})
    await policy.save_bundle_reporting_cost_policy(db, OWNER, PRODUCT, payload(), "actor")
    profile = await db[policy.COST_PROFILES].find_one({"user_id": OWNER}, {"_id": 0})
    assert set(profile) == {"user_id", "salla_product_id", policy.POLICY_FIELD}
    assert profile[policy.POLICY_FIELD]["revision"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change,code", [
    ({"unexpected": True}, "invalid_payload"),
    ({"expected_revision": True}, "invalid_revision"),
    ({"expected_revision": -1}, "invalid_revision"),
    ({"expected_revision": 0.0}, "invalid_revision"),
    ({"expected_bundle_revision": False}, "invalid_revision"),
    ({"expected_bundle_revision": -1}, "invalid_revision"),
    ({"include_parent_base": "true"}, "invalid_parent_base"),
    ({"include_parent_base": 1}, "invalid_parent_base"),
    ({"evidence_ref": None}, "invalid_evidence"),
    ({"evidence_ref": []}, "invalid_evidence"),
    ({"evidence_ref": "x" * 1001}, "invalid_evidence"),
    ({"evidence_ref": "   "}, "evidence_required"),
])
async def test_invalid_input_has_zero_writes(db, change, code):
    before = await stored(db)
    with pytest.raises(HTTPException) as exc:
        await policy.save_bundle_reporting_cost_policy(db, OWNER, PRODUCT, payload(**change), "actor")
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "bundle_reporting_policy_" + code
    assert await stored(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["expected_revision", "expected_bundle_revision", "include_parent_base", "evidence_ref"])
async def test_missing_input_is_rejected(db, field):
    data = payload()
    data.pop(field)
    with pytest.raises(HTTPException) as exc:
        await policy.save_bundle_reporting_cost_policy(db, OWNER, PRODUCT, data, "actor")
    assert exc.value.detail["code"] == "bundle_reporting_policy_invalid_payload"


@pytest.mark.asyncio
@pytest.mark.parametrize("change,code", [
    ({"expected_revision": 1}, "revision_conflict"),
    ({"expected_bundle_revision": 2}, "bundle_revision_conflict"),
])
async def test_stale_policy_or_bundle_is_rejected_without_writes(db, change, code):
    before = await stored(db)
    with pytest.raises(HTTPException) as exc:
        await policy.save_bundle_reporting_cost_policy(db, OWNER, PRODUCT, payload(**change), "actor")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "bundle_reporting_policy_" + code
    assert await stored(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("definition", [None, "normal"])
async def test_only_saved_composite_can_receive_policy(db, definition):
    if definition is None:
        await db[policy.BUNDLES].delete_many({})
    else:
        await db[policy.BUNDLES].update_one({"user_id": OWNER}, {"$set": {"product_type": definition}})
    before = await stored(db)
    with pytest.raises(HTTPException) as exc:
        await policy.save_bundle_reporting_cost_policy(db, OWNER, PRODUCT, payload(), "actor")
    assert exc.value.detail["code"] == "bundle_reporting_policy_composite_required"
    assert await stored(db) == before


@pytest.mark.asyncio
async def test_foreign_product_is_never_read_or_updated(db):
    await db.mezan_products_v2.insert_one({"user_id": "foreign", "salla_product_id": "foreign-product"})
    before = await stored(db)
    for operation in (
        policy.get_bundle_reporting_cost_policy(db, OWNER, "foreign-product"),
        policy.save_bundle_reporting_cost_policy(db, OWNER, "foreign-product", payload(), "actor"),
    ):
        with pytest.raises(HTTPException) as exc:
            await operation
        assert exc.value.status_code == 404
    assert await stored(db) == before


@pytest.mark.asyncio
async def test_stored_invalid_policy_is_not_silently_reset(db):
    await db[policy.COST_PROFILES].update_one({"user_id": OWNER}, {"$set": {policy.POLICY_FIELD: {"revision": True}}})
    before = await stored(db)
    with pytest.raises(HTTPException) as exc:
        await policy.save_bundle_reporting_cost_policy(db, OWNER, PRODUCT, payload(), "actor")
    assert exc.value.detail["code"] == "bundle_reporting_policy_stored_policy_invalid"
    assert await stored(db) == before


@pytest.mark.asyncio
async def test_http_uses_cost_permissions_owner_scope_and_mobile_actor(db, monkeypatch):
    calls = []

    async def permission(database, user, required):
        calls.append((user, required))
        return OWNER

    async def current_user():
        return {"id": "owner-shaped-session", "_mobile_actor_id": "employee"}

    monkeypatch.setattr(policy, "require_product_permission", permission)
    router = APIRouter(prefix="/products-v2")
    policy.register_bundle_reporting_cost_policy_routes(router, db, current_user)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        read = await client.get(f"/products-v2/{PRODUCT}/bundle-reporting-cost-policy")
        saved = await client.put(f"/products-v2/{PRODUCT}/bundle-reporting-cost-policy", json=payload())
    assert read.status_code == saved.status_code == 200
    assert [permission for _, permission in calls] == ["products.cost.read", "products.cost.write"]
    row = await db[policy.COST_PROFILES].find_one({"user_id": OWNER})
    assert row[policy.POLICY_FIELD]["updated_by"] == "employee"


@pytest.mark.asyncio
async def test_http_permission_denial_has_no_business_writes(db, monkeypatch):
    async def deny(database, user, permission):
        raise HTTPException(403, "product_permission_required")

    async def current_user():
        return {"id": "reader"}

    monkeypatch.setattr(policy, "require_product_permission", deny)
    router = APIRouter(prefix="/products-v2")
    policy.register_bundle_reporting_cost_policy_routes(router, db, current_user)
    app = FastAPI()
    app.include_router(router)
    before = await stored(db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(f"/products-v2/{PRODUCT}/bundle-reporting-cost-policy", json=payload())
    assert response.status_code == 403
    assert await stored(db) == before


@pytest_asyncio.fixture
async def real_db():
    uri = os.environ.get("MZ2_TEST_MONGO_URI")
    if not uri:
        pytest.skip("requires isolated loopback replica set")
    assert urlsplit(uri).hostname in {"127.0.0.1", "localhost", "::1"}
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    name = "mz2_bundle_reporting_policy_" + uuid4().hex
    db = client[name]
    try:
        assert (await db.command("hello")).get("setName")
        await db.mz2_atomic_owners.insert_one({"_id": OWNER, "revision": 0, "writes_paused": False, "control_revision": 0})
        await seed(db)
        yield db
    finally:
        assert db.name == name and name.startswith("mz2_bundle_reporting_policy_")
        await client.drop_database(name)
        client.close()


@pytest.mark.asyncio
async def test_real_transaction_changes_only_policy_and_owner_fence(real_db):
    before = await stored(real_db)
    await policy.save_bundle_reporting_cost_policy(real_db, OWNER, PRODUCT, payload(), "actor")
    after = await stored(real_db)
    after[policy.COST_PROFILES][0].pop(policy.POLICY_FIELD)
    assert after["mz2_atomic_owners"][0]["revision"] == before["mz2_atomic_owners"][0]["revision"] + 1
    after["mz2_atomic_owners"] = before["mz2_atomic_owners"]
    assert after == before


@pytest.mark.asyncio
async def test_real_concurrent_policy_cas_has_one_winner(real_db):
    results = await asyncio.gather(
        policy.save_bundle_reporting_cost_policy(real_db, OWNER, PRODUCT, payload(), "actor-one"),
        policy.save_bundle_reporting_cost_policy(real_db, OWNER, PRODUCT, payload(), "actor-two"),
        return_exceptions=True,
    )
    assert len([value for value in results if isinstance(value, dict)]) == 1
    failures = [value for value in results if isinstance(value, HTTPException)]
    assert len(failures) == 1 and failures[0].detail["code"] == "bundle_reporting_policy_revision_conflict"
    assert (await real_db.mz2_atomic_owners.find_one({"_id": OWNER}))["revision"] == 1
    assert (await real_db[policy.COST_PROFILES].find_one({"user_id": OWNER}))[policy.POLICY_FIELD]["revision"] == 1


@pytest.mark.asyncio
async def test_real_changed_bundle_rejects_and_rolls_back_fence(real_db):
    from operational_atomic import operational_owner

    async def change(scoped):
        await scoped[policy.BUNDLES].update_one(
            {"user_id": OWNER, "product_id": PRODUCT}, {"$inc": {"revision": 1}})

    await operational_owner(real_db, OWNER, change, profile="bundle_catalog")
    before = await stored(real_db)
    with pytest.raises(HTTPException) as exc:
        await policy.save_bundle_reporting_cost_policy(real_db, OWNER, PRODUCT, payload(), "actor")
    assert exc.value.detail["code"] == "bundle_reporting_policy_bundle_revision_conflict"
    assert await stored(real_db) == before


@pytest.mark.asyncio
async def test_real_policy_write_failure_rolls_back_all_effects(real_db):
    await real_db.command({"collMod": policy.COST_PROFILES,
                           "validator": {policy.POLICY_FIELD: {"$exists": False}}})
    before = await stored(real_db)
    with pytest.raises(OperationFailure):
        await policy.save_bundle_reporting_cost_policy(real_db, OWNER, PRODUCT, payload(), "actor")
    assert await stored(real_db) == before
