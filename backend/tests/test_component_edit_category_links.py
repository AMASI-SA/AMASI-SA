"""HTTP + disposable Mongo coverage for the canonical component category contract.

Set BUILD20_CATEGORY_TEST_MONGO_URL to a dedicated loopback Mongo instance.
No production database, merchant credentials or provider calls are used.
Both legacy registration and the newer category-required route order are tested.
"""
from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

import component_category_required_routes as required_routes
import component_edit_routes as edit_routes
import component_workspace_cost_compat_routes as workspace_routes
from component_category_policy import COMPONENT_CATEGORIES, COMPONENT_GROUPS
from product_option_cost_routes import AUDIT, RESOURCES

USER = "build20-synthetic-owner"
OTHER = "build20-synthetic-other"
MONGO_URL = os.environ.get("BUILD20_CATEGORY_TEST_MONGO_URL", "")


@pytest_asyncio.fixture
async def db():
    if not MONGO_URL:
        pytest.skip("dedicated BUILD20_CATEGORY_TEST_MONGO_URL required")
    parsed = urlparse(MONGO_URL)
    assert parsed.scheme == "mongodb" and parsed.hostname in {"localhost", "127.0.0.1"}
    assert not parsed.username and not parsed.password and parsed.path in {"", "/"}
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    database = client["build20_component_test_" + uuid.uuid4().hex]
    await client.admin.command("ping")
    try:
        await database[COMPONENT_CATEGORIES].insert_many([
            {"user_id": USER, "id": key, "name": key, "normalized_name": key, "status": "active"}
            for key in ("cat-a", "cat-b", "cat-c")
        ] + [{"user_id": OTHER, "id": "foreign-cat", "name": "foreign", "normalized_name": "foreign"}])
        yield database
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest_asyncio.fixture(params=["legacy", "canonical"])
async def api(request, db):
    app = FastAPI()

    async def current_user():
        return {"id": USER}

    if request.param == "canonical":
        app.include_router(required_routes.make_component_category_required_router(db, current_user), prefix="/api")
    app.include_router(workspace_routes.make_component_workspace_cost_compat_router(db, current_user), prefix="/api")
    app.include_router(edit_routes.make_component_edit_router(db, current_user), prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/") as client:
        yield client


def resource_payload(kind="service", **changes):
    return {"name": "Synthetic component", "code": "test-resource", "kind": kind,
            "unit_cost": 12.5, "category_ids": ["cat-a"], **changes}


async def create(api, kind="service", **changes):
    response = await api.post("components-v2", json=resource_payload(kind, **changes))
    assert response.status_code in {200, 201}, response.text
    assert response.json()["ok"] is True
    return response.json()["resource"]


async def snapshot(db, resource_id):
    return await db[RESOURCES].find_one({"user_id": USER, "id": resource_id}, {"_id": 0})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["service", "stock_component"])
async def test_create_stores_categories_in_same_resource(api, db, kind):
    resource = await create(api, kind)
    stored = await snapshot(db, resource["id"])
    assert resource["category_ids"] == stored["category_ids"] == ["cat-a"]
    assert resource["kind"] == stored["kind"] == kind
    assert stored["track_inventory"] is (kind == "stock_component")
    assert stored["code"] == "TEST-RESOURCE"
    assert await db[RESOURCES].count_documents({"user_id": USER}) == 1


@pytest.mark.asyncio
async def test_create_normalizes_empty_and_duplicate_ids(api, db):
    resource = await create(api, category_ids=[" cat-a ", "", "cat-a", None, "cat-b", " "])
    assert resource["category_ids"] == ["cat-a", "cat-b"]
    assert (await snapshot(db, resource["id"]))["category_ids"] == ["cat-a", "cat-b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("categories", [["missing"], ["foreign-cat"], ["cat-a", "missing"]])
@pytest.mark.parametrize("kind", ["service", "stock_component"])
async def test_invalid_category_never_inserts_resource(api, db, categories, kind):
    response = await api.post("components-v2", json=resource_payload(kind, category_ids=categories))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "component_category_not_found"
    assert await db[RESOURCES].count_documents({}) == 0


@pytest.mark.asyncio
async def test_update_persists_new_category_list(api, db):
    resource = await create(api)
    response = await api.put(f"components-v2/{resource['id']}", json={"category_ids": ["cat-b", "cat-b", " cat-c "]})
    assert response.status_code == 200, response.text
    assert response.json()["resource"]["category_ids"] == ["cat-b", "cat-c"]
    assert (await snapshot(db, resource["id"]))["category_ids"] == ["cat-b", "cat-c"]


@pytest.mark.asyncio
async def test_update_omitting_category_ids_preserves_saved_value(api, db):
    resource = await create(api, category_ids=["cat-a", "cat-b"])
    response = await api.put(f"components-v2/{resource['id']}", json={"name": "Renamed"})
    assert response.status_code == 200, response.text
    stored = await snapshot(db, resource["id"])
    assert stored["category_ids"] == response.json()["resource"]["category_ids"] == ["cat-a", "cat-b"]
    assert stored["name"] == "Renamed"
    audit = await db[AUDIT].find_one({"resource_id": resource["id"], "event_type": "resource_updated"})
    assert audit["before"]["category_ids"] == audit["after"]["category_ids"] == ["cat-a", "cat-b"]


@pytest.mark.asyncio
async def test_invalid_update_changes_nothing(api, db):
    resource = await create(api)
    before = await snapshot(db, resource["id"])
    response = await api.put(f"components-v2/{resource['id']}", json={"name": "Must not persist", "category_ids": ["foreign-cat"]})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "component_category_not_found"
    assert await snapshot(db, resource["id"]) == before
    assert await db[AUDIT].count_documents({}) == 0


async def add_group(db, resource_id, *, user_id=USER, category="cat-a"):
    await db[COMPONENT_GROUPS].insert_one({
        "id": "group-1", "user_id": user_id, "category_id": category,
        "group_kind": "service", "member_signature": resource_id + "|another",
        "resource_ids": [resource_id, "another"],
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("category_ids", [["cat-b"], []])
async def test_remove_category_used_by_group_rejects_entire_edit(api, db, category_ids):
    resource = await create(api)
    await add_group(db, resource["id"])
    before = await snapshot(db, resource["id"])
    for path in (f"components-v2/{resource['id']}", f"components-v2/{resource['id']}/categories"):
        response = await api.put(path, json={"name": "Must not persist", "category_ids": category_ids})
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == {"code": "component_category_used_by_group", "group_id": "group-1", "category_id": "cat-a"}
        assert await snapshot(db, resource["id"]) == before
    assert await db[AUDIT].count_documents({}) == 0


@pytest.mark.asyncio
async def test_keep_group_category_and_add_another_is_allowed(api, db):
    resource = await create(api)
    await add_group(db, resource["id"])
    response = await api.put(f"components-v2/{resource['id']}", json={"category_ids": ["cat-a", "cat-b"]})
    assert response.status_code == 200, response.text
    assert (await snapshot(db, resource["id"]))["category_ids"] == ["cat-a", "cat-b"]
    assert await db[COMPONENT_GROUPS].count_documents({}) == 1


@pytest.mark.asyncio
async def test_other_tenant_group_does_not_block_edit(api, db):
    resource = await create(api)
    await add_group(db, resource["id"], user_id=OTHER)
    response = await api.put(f"components-v2/{resource['id']}", json={"category_ids": ["cat-b"]})
    assert response.status_code == 200, response.text
    assert (await snapshot(db, resource["id"]))["category_ids"] == ["cat-b"]


@pytest.mark.asyncio
async def test_group_with_other_members_does_not_block_edit(api, db):
    resource = await create(api)
    await add_group(db, "unrelated")
    response = await api.put(f"components-v2/{resource['id']}", json={"category_ids": ["cat-b"]})
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_cannot_update_other_tenant_resource(api, db):
    await db[RESOURCES].insert_one({"user_id": OTHER, "id": "foreign-resource", **resource_payload()})
    response = await api.put("components-v2/foreign-resource", json={"category_ids": ["cat-a"]})
    assert response.status_code == 404
    assert (await db[RESOURCES].find_one({"id": "foreign-resource"}))["category_ids"] == ["cat-a"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["service", "stock_component"])
async def test_workspace_readback_uses_canonical_identity_kind_and_categories(api, db, kind):
    resource = await create(api, kind)
    response = await api.get("components-v2/workspace")
    assert response.status_code == 200, response.text
    workspace = response.json()
    rows = [r for r in workspace["components"] if r["id"] == resource["id"]]
    assert len(rows) == 1
    assert rows[0]["code"] == resource["code"]
    assert rows[0]["kind"] == kind and rows[0]["category_ids"] == ["cat-a"]
    assert workspace["meta"]["source"] == RESOURCES == "mezan_cost_resources_v2"
    assert next(c for c in workspace["categories"] if c["id"] == "cat-a")["resource_count"] == 1
    assert next(c for c in workspace["categories"] if c["id"] == "cat-b")["resource_count"] == 0
    await api.put(f"components-v2/{resource['id']}", json={"category_ids": ["cat-b"]})
    refreshed = (await api.get("components-v2/workspace")).json()
    assert next(c for c in refreshed["categories"] if c["id"] == "cat-a")["resource_count"] == 0
    assert next(c for c in refreshed["categories"] if c["id"] == "cat-b")["resource_count"] == 1


def test_all_category_mutations_share_the_existing_group_guard():
    assert edit_routes.validate_category_change is workspace_routes.validate_category_change
    assert edit_routes.validate_category_change is required_routes.validate_category_change
    assert COMPONENT_CATEGORIES == "mezan_component_categories_v2"
    assert COMPONENT_GROUPS == "mezan_component_groups_v2"
