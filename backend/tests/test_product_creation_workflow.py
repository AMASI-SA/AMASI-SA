"""Exercise the governed creation lifecycle with a fake provider boundary."""
import asyncio
from copy import deepcopy

import pytest
from fastapi import HTTPException

import product_creation_routes as creation
from product_creation_routes import DRAFTS, ProductCreationDraftRequest, make_product_creation_router


def matches(row, query):
    return all(row.get(key) != value["$ne"] if isinstance(value, dict) and "$ne" in value
               else row.get(key) == value for key, value in query.items())


class Collection:
    def __init__(self):
        self.rows = []

    async def create_index(self, *args, **kwargs):
        pass

    async def find_one(self, query, projection=None):
        return deepcopy(next((row for row in self.rows if matches(row, query)), None))

    async def insert_one(self, row):
        self.rows.append(deepcopy(row))

    async def find_one_and_update(self, query, update, **kwargs):
        row = next((row for row in self.rows if matches(row, query)), None)
        if row is None:
            return None
        row.update(deepcopy(update.get("$set", {})))
        for key in update.get("$unset", {}):
            row.pop(key, None)
        return deepcopy(row)

    async def update_one(self, query, update, **kwargs):
        return await self.find_one_and_update(query, update)


class DB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())

    @property
    def salla_integrations(self):
        return self["salla_integrations"]


OWNER = {"id": "owner-1", "role": "owner"}


def endpoints(db):
    router = make_product_creation_router(db, lambda: OWNER)
    return {route.endpoint.__name__: route.endpoint for route in router.routes}


def request(**changes):
    values = {
        "name": "هدية بالاسم", "sku": "CUSTOM-100", "price": 239,
        "product_type": "service", "fulfillment_type": "requires_preparation",
        "inventory_policy": "branch_stock_required",
        "options": [
            {"name": "المقاس", "type": "choice", "required": True,
             "values": [f"{age} سنة" for age in range(1, 11)]},
            {"name": "الاسم المطلوب", "type": "text", "required": False},
        ],
    }
    values.update(changes)
    return ProductCreationDraftRequest(**values)


@pytest.mark.parametrize("drop_options", [False, True])
def test_draft_preview_approval_publish_and_readback(monkeypatch, drop_options):
    async def scenario():
        db = DB()
        await db.salla_integrations.insert_one({
            "user_id": "owner-1", "status": "connected", "scope": "products.read_write",
        })
        routes = endpoints(db)
        calls = []
        remote = None

        async def provider(db, merchant_id, method, path, **kwargs):
            nonlocal remote
            assert merchant_id == "owner-1"
            calls.append((method, path, deepcopy(kwargs)))
            if path.startswith("/products/sku/"):
                return {"data": remote}
            if method == "POST" and path == "/products":
                payload = kwargs["json"]
                assert payload["product_type"] == "service"
                assert payload["options"][0]["type"] == "select"
                assert len(payload["options"][0]["values"]) == 10
                assert payload["options"][1]["type"] == "text"
                remote = {**payload, "id": 123, "type": payload["product_type"]}
                return {"data": deepcopy(remote)}
            if method == "GET" and path == "/products/123":
                return {"data": {**deepcopy(remote), **({"options": []} if drop_options else {})}}
            raise AssertionError(f"Unexpected provider call: {method} {path}")

        async def save(db, *, merchant_id, draft, raw, actor_id):
            assert draft["options"] == request().model_dump()["options"]
            return {"mezan_product_id": "mpv2_123", "salla_product_id": "123"}

        monkeypatch.setattr(creation, "call_salla", provider)
        monkeypatch.setattr(creation, "_save_created_product", save)
        result = await routes["create_draft"](request(), OWNER)
        draft_id = result["draft"]["id"]
        preview = await routes["preview_draft"](draft_id, OWNER)
        assert preview["salla_payload"]["options"][1]["type"] == "text"
        assert not calls
        with pytest.raises(HTTPException) as denied:
            await routes["publish_draft"](draft_id, {"confirmation": creation.PUBLISH_CONFIRMATION}, OWNER)
        assert denied.value.detail["code"] == "product_creation_draft_not_approved"
        await routes["approve_draft"](draft_id, OWNER)
        result = await routes["publish_draft"](draft_id, {"confirmation": creation.PUBLISH_CONFIRMATION}, OWNER)
        assert result["verified"] is not drop_options
        assert result["draft"]["status"] == ("published_unverified" if drop_options else "published")
        if drop_options:
            assert result["draft"]["verify_error"] == "product_options_mismatch"
        await routes["publish_draft"](draft_id, {"confirmation": creation.PUBLISH_CONFIRMATION}, OWNER)
        assert len([call for call in calls if call[:2] == ("POST", "/products")]) == 1

    asyncio.run(scenario())


def test_editing_options_revokes_approval_and_keeps_tenant_isolation():
    async def scenario():
        db = DB()
        routes = endpoints(db)
        draft = (await routes["create_draft"](request(), OWNER))["draft"]
        await routes["approve_draft"](draft["id"], OWNER)
        legacy = ProductCreationDraftRequest(**request().model_dump(exclude={"product_type", "options"}))
        preserved = await routes["update_draft"](draft["id"], legacy, OWNER)
        assert preserved["draft"]["product_type"] == "service"
        assert preserved["draft"]["options"] == draft["options"]
        updated = await routes["update_draft"](draft["id"], request(options=[]), OWNER)
        assert updated["draft"]["status"] == "draft"
        assert updated["draft"]["approved_at"] is None
        assert updated["draft"]["options"] == []
        with pytest.raises(HTTPException) as denied:
            await routes["get_creation_draft"](draft["id"], {"id": "owner-2", "role": "owner"})
        assert denied.value.status_code == 404
        assert len(db[DRAFTS].rows) == 1

    asyncio.run(scenario())
