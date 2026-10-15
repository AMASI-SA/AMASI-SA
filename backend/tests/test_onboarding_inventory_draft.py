"""Stage 10 metadata-only HTTP contracts; disposable in-memory Mongo adapter."""
import asyncio
from copy import deepcopy

from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient
import pytest

from accounting_onboarding import install_onboarding_routes
from accounting_financial_accounts import PERMISSIONS

BASE = "/accounting-module/onboarding"


def test_inventory_draft_roundtrip_cas_replay_lock_and_zero_financial_writes():
    async def exercise():
        db = AsyncMongoMockClient()["stage10"]
        actor = {"id": "owner", "role": "owner", "is_active": True, "disabled": False}
        await db.users.insert_one(actor)
        async def user():
            return actor
        router = APIRouter()
        install_onboarding_routes(router, db, user, {})
        app = FastAPI()
        app.include_router(router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async def call(method, path, data=None, status=200):
                response = await client.request(method, BASE + path, json=data)
                assert response.status_code == status, response.text
                return response.json()
            session = await call("POST", "/sessions", {"idempotency_key": "create-inventory", "cutover_at": "2026-10-01T00:00:00+03:00", "cutover_timezone": "Asia/Riyadh"})
            path = f"/sessions/{session['id']}/inventory-draft"
            draft = {"rows": [
                {"item_type": "PRODUCT", "product_v2_id": "p", "variant_id": "a", "opening_quantity": "2", "opening_unit_cost": "15", "allocations": []},
                {"item_type": "PRODUCT", "product_v2_id": "p", "variant_id": "b", "opening_quantity": "", "opening_unit_cost": "", "allocations": [{"location_id": "l", "quantity": "1", "scanned_location_barcode": "BIN"}]}], "financial_lines": []}
            payload = {"version": 1, "idempotency_key": "save-inventory-001", "draft": draft}
            await db.mz2_onboarding_sessions.update_one({"id": session["id"]}, {"$set": {
                "sections.inventory.status": "complete", "sections.inventory.data.lines": [{"category": "inventory_asset"}],
                "preview": {"hash": "stale"}, "status": "previewed"}})
            saved = await call("PUT", path, payload)
            assert saved["sections"]["inventory"]["status"] == "incomplete"
            assert saved["sections"]["inventory"]["data"]["lines"] == [{"category": "inventory_asset"}]
            assert saved["preview"] is None
            assert saved["version"] == 2 and saved["status"] == "draft"
            resumed = await call("GET", f"/sessions/{session['id']}")
            assert resumed == saved
            assert [r["variant_id"] for r in resumed["inventory_draft"]["rows"]] == ["a", "b"]
            assert resumed["inventory_draft"]["rows"][0]["allocations"] == []
            assert resumed["inventory_draft"]["rows"][1]["allocations"][0]["location_id"] == "l"
            replay = await call("PUT", path, payload)
            assert replay["existing"] and replay["version"] == 2
            conflict = await call("PUT", path, {**payload, "idempotency_key": "stale-inventory"}, 409)
            assert conflict["detail"]["code"] == "onboarding_version_conflict"
            changed = deepcopy(payload)
            changed["draft"]["rows"][0]["variant_id"] = "c"
            conflict = await call("PUT", path, changed, 409)
            assert conflict["detail"]["code"] == "onboarding_idempotency_conflict"
            await db.mz2_onboarding_sessions.update_one({"id": session["id"]}, {"$set": {"status": "reviewed"}})
            locked = await call("PUT", path, {**payload, "version": 2, "idempotency_key": "locked-inventory"}, 409)
            assert locked["detail"]["code"] == "onboarding_session_locked"
            await db.users.update_one({"id": "owner"}, {"$set": {"disabled": True}})
            await call("PUT", path, payload, 403)
            names = await db.list_collection_names()
            for name in names:
                if name not in {"users", "mz2_onboarding_sessions"}:
                    assert await db[name].count_documents({}) == 0, name
    asyncio.run(exercise())


def test_inventory_draft_rejects_unbounded_unknown_and_physical_approval_fields():
    from pydantic import ValidationError
    from accounting_onboarding_contract import InventoryDraftSave
    for draft in ({"rows": [{"physical_approval_verified": True}]}, {"rows": [{"opening_quantity": "1" * 81}]},
                  {"financial_lines": [{"ledger_posted": True}]}, {"rows": [{"allocations": [{}] * 101}]}):
        with pytest.raises(ValidationError):
            InventoryDraftSave(version=1, idempotency_key="draft-test", draft=draft)


def test_inventory_routes_enforce_owner_scope_permissions_and_catalog_read_only():
    async def exercise():
        db = AsyncMongoMockClient()["stage10-permissions"]
        actors = [
            {"id": "owner", "role": "owner", "is_active": True},
            {"id": "other", "role": "owner", "is_active": True},
            {"id": "viewer", "role": "employee", "created_by": "owner", "is_active": True,
             "accounting_permissions": [PERMISSIONS["opening_view"]]},
            {"id": "denied", "role": "employee", "created_by": "owner", "is_active": True}]
        await db.users.insert_many(actors)
        await db.mezan_products_v2.insert_many([
            {"user_id": "owner", "mezan_product_id": "p", "name": "Visible"},
            {"user_id": "other", "mezan_product_id": "foreign", "name": "Private"}])
        await db.products.insert_one({"user_id": "owner", "id": "legacy"})
        await db.mezan_cost_resources_v2.insert_one({"user_id": "owner", "id": "component", "status": "active",
            "track_inventory": True, "kind": "material", "unit": "meter", "category_ids": ["fabric"]})
        active = [actors[0]]
        async def user():
            return active[0]
        router = APIRouter()
        install_onboarding_routes(router, db, user, {})
        app = FastAPI()
        app.include_router(router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(BASE + "/sessions", json={"idempotency_key": "permission-create", "cutover_at": "2026-10-01T00:00:00+03:00", "cutover_timezone": "Asia/Riyadh"})
            assert response.status_code == 200
            path = BASE + f"/sessions/{response.json()['id']}/inventory-draft"
            payload = {"version": 1, "idempotency_key": "permission-save", "draft": {"rows": []}}
            active[0] = actors[1]
            assert (await client.put(path, json=payload)).status_code == 404
            active[0] = actors[2]
            assert (await client.put(path, json=payload)).status_code == 403
            before = {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}
            response = await client.get(BASE + "/inventory-catalog")
            assert response.status_code == 200
            assert [p["id"] for p in response.json()["products"]] == ["p"]
            component = response.json()["components"][0]
            assert component["status"] == "active" and component["track_inventory"] is True
            assert component["kind"] == "material" and component["unit"] == "meter"
            assert component["category_ids"] == ["fabric"]
            after = {name: await db[name].find({}).to_list(None) for name in before}
            assert before == after
            active[0] = actors[3]
            assert (await client.get(BASE + "/inventory-catalog")).status_code == 403
    asyncio.run(exercise())
