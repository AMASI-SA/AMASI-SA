from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from accounting_module_contract import accounting_permissions_for_user
from accounting_module_permission_routes import install_accounting_permission_routes
from component_workspace_cost_compat_routes import make_component_workspace_cost_compat_router
from order_engine import _original_make_order_engine_router
from product_control_center_routes import DRAFTS, make_product_control_center_router
from product_creation_routes import DRAFTS as CREATION_DRAFTS, make_product_creation_router
from product_option_cost_routes import RESOURCES
from warehouse_location_routes import WAREHOUSES, make_warehouse_location_router
from warehouse_location_v2_routes import make_warehouse_location_v2_router
from order_engine import routes as order_routes
from product_v2_routes import PRODUCTS, make_product_v2_router
from web_page_access import (
    COMPONENTS_PAGE, FIELD, INTAKE_PAGE, ORDERS_PAGE, PRODUCTS_PAGE, WAREHOUSES_PAGE,
    enforce_web_catalog_access, make_web_page_access_router, web_page_access_for_user,
)


GRANTS = "/api/web-page-access-v1"
ORDERS = "/api/orders-v2"
CATALOGUE = "/api/products-v2"
REPORTS_PAGE = "accounting.journals_reports.view"
LEGACY_PAGE = "accounting.home.view"
ACTION = "accounting.journals.manual_create"


@pytest.fixture
def store():
    return AsyncMongoMockClient().test_web_page_access


async def build_client(store):
    await store.users.insert_many([
        {"id": "owner", "role": "owner"},
        {"id": "other-owner", "role": "owner"},
        {"id": "employee", "role": "user", "created_by": "owner", "name": "Test employee"},
        {"id": "outsider", "role": "user", "created_by": "other-owner"},
        {"id": "driver", "role": "store_driver", "created_by": "owner"},
        {"id": "deleted", "role": "user", "created_by": "owner", "deleted_at": "2026-11-09"},
    ])

    async def current_user(request: Request):
        user = await store.users.find_one({"id": request.headers["test-actor"]}, {"_id": 0})
        enforce_web_catalog_access(user, request.url.path, request.method)
        return user

    app = FastAPI()
    router = APIRouter(prefix="/api")
    router.include_router(make_web_page_access_router(store, current_user))
    router.include_router(make_product_v2_router(store, current_user))
    router.include_router(_original_make_order_engine_router(store, current_user))
    router.include_router(make_product_control_center_router(store, current_user))
    router.include_router(make_product_creation_router(store, current_user))
    router.include_router(make_component_workspace_cost_compat_router(store, current_user))
    router.include_router(make_warehouse_location_router(store, current_user))
    router.include_router(make_warehouse_location_v2_router(store, current_user))
    install_accounting_permission_routes(router, store, current_user)

    @router.get("/test-session-pages")
    async def test_session_pages(request: Request):
        return web_page_access_for_user(await current_user(request))

    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def actor(user_id):
    return {"test-actor": user_id}


async def save(client, modes, revision=0, member="employee", by="owner"):
    return await client.put(
        f"{GRANTS}/{member}",
        headers=actor(by),
        json={"page_modes": modes, "expected_revision": revision},
    )


@pytest.mark.asyncio
async def test_only_the_merchant_owner_can_assign_known_pages_to_an_active_team_member(store):
    async with await build_client(store) as client:
        listing = await client.get(GRANTS, headers=actor("owner"))
        assert listing.status_code == 200
        assert [row["id"] for row in listing.json()["members"]] == ["employee"]
        assert {row["section"] for row in listing.json()["pages"]} >= {"products", "orders", "accounting"}
        assert {row["id"] for row in listing.json()["pages"] if row["section"] == "products"} == {
            PRODUCTS_PAGE, INTAKE_PAGE, WAREHOUSES_PAGE, "products.components",
            "products.inventory", "products.receiving",
        }

        assert (await client.get(GRANTS, headers=actor("employee"))).status_code == 403
        assert (await save(client, {PRODUCTS_PAGE: "read"}, by="employee")).status_code == 403
        assert (await save(client, {PRODUCTS_PAGE: "read"}, member="outsider")).status_code == 404
        assert (await save(client, {PRODUCTS_PAGE: "read"}, member="driver")).status_code == 404
        assert (await save(client, {PRODUCTS_PAGE: "read"}, member="deleted")).status_code == 404
        assert (await save(client, {"settings.permissions": "read"})).status_code == 422
        assert (await save(client, {ORDERS_PAGE: "write"})).status_code == 422


@pytest.mark.asyncio
async def test_products_only_grant_is_scoped_read_access_and_can_be_revoked(store):
    async with await build_client(store) as client:
        await store[PRODUCTS].insert_many([
            {
                "id": "our-product", "salla_product_id": "123", "user_id": "owner",
                "name": "Example product", "raw_salla": {"private": True}, "cost": 10,
                "variants": [{"id": "v1", "sku": "SKU-1", "cost_price": 10}],
            },
            {"id": "foreign-product", "salla_product_id": "124", "user_id": "other-owner"},
            {"id": "archived-product", "salla_product_id": "125", "user_id": "owner", "archived": True},
        ])
        staff = actor("employee")
        assert (await client.get("/api/test-session-pages", headers=staff)).json() is None

        saved = await save(client, {PRODUCTS_PAGE: "read"})
        assert saved.status_code == 200
        assert saved.json()["member"]["revision"] == 1
        pages = (await client.get("/api/test-session-pages", headers=staff)).json()["pages"]
        assert [(page["id"], page["to"], page["mode"]) for page in pages] == [(PRODUCTS_PAGE, "/products-v2", "read")]

        listing = await client.get(CATALOGUE, params={"include_archived": True}, headers=staff)
        assert listing.status_code == 200
        assert [item["id"] for item in listing.json()["items"]] == ["our-product"]
        detail = (await client.get(f"{CATALOGUE}/123", headers=staff)).json()
        assert detail["name"] == "Example product"
        assert detail["variants"] == [{"id": "v1", "sku": "SKU-1"}]
        assert {"raw_salla", "cost", "user_id"}.isdisjoint(detail)
        assert (await client.get(f"{CATALOGUE}/foreign-product", headers=staff)).status_code == 404
        assert (await client.get(ORDERS, headers=staff)).status_code == 403

        assert (await save(client, {ORDERS_PAGE: "read"})).status_code == 409
        assert (await save(client, {}, revision=1)).status_code == 200
        assert (await client.get(CATALOGUE, headers=staff)).status_code == 403
        assert (await client.get("/api/test-session-pages", headers=staff)).json() == {"pages": []}


@pytest.mark.asyncio
async def test_pages_across_sections_preserve_independent_accounting_actions_and_native_access(store, monkeypatch):
    async def list_orders(repository, *, user_id, **kwargs):
        assert user_id == "owner"
        return SimpleNamespace(items=[], next_cursor=None, skipped_invalid=0)

    async def summarize(db, *, user_id):
        assert user_id == "owner"
        return {"total": 0, "status_cards": [], "status_counts": {}}

    monkeypatch.setattr(order_routes, "list_orders", list_orders)
    monkeypatch.setattr(order_routes, "build_order_filter_summary", summarize)
    async with await build_client(store) as client:
        await store.users.update_one({"id": "employee"}, {"$set": {"accounting_permissions": [LEGACY_PAGE]}})
        assert (await save(client, {ORDERS_PAGE: "read", REPORTS_PAGE: "read"})).status_code == 200
        staff = actor("employee")
        assert (await client.get(ORDERS, headers=staff)).status_code == 200
        assert (await client.get(f"{ORDERS}/filters/summary", headers=staff)).status_code == 200
        assert (await client.get(f"{ORDERS}/123", headers=staff)).status_code == 403
        assert (await client.get(CATALOGUE, headers=staff)).status_code == 403
        member = await store.users.find_one({"id": "employee"})
        assert accounting_permissions_for_user(member) == [REPORTS_PAGE]
        assert accounting_permissions_for_user({**member, "_session_client": "amasi_mobile"}) == [LEGACY_PAGE]

        actions = "/api/accounting-module/permissions/users/employee"
        rejected = await client.put(actions, headers=actor("owner"), json={
            "actions_only": True, "permissions": [REPORTS_PAGE],
        })
        assert rejected.status_code == 422
        updated = await client.put(actions, headers=actor("owner"), json={
            "actions_only": True, "permissions": [ACTION],
        })
        assert updated.status_code == 200
        member = await store.users.find_one({"id": "employee"})
        assert member[FIELD]["grants"] == [
            {"id": REPORTS_PAGE, "mode": "read"}, {"id": ORDERS_PAGE, "mode": "read"},
        ]
        assert accounting_permissions_for_user(member) == [REPORTS_PAGE]
        assert set(member["accounting_permissions"]) == {LEGACY_PAGE, ACTION}
        assert (await save(client, {ORDERS_PAGE: "read", REPORTS_PAGE: "write"}, revision=1)).status_code == 200
        member = await store.users.find_one({"id": "employee"})
        assert set(accounting_permissions_for_user(member)) == {REPORTS_PAGE, ACTION}

        await store.users.update_one({"id": "owner"}, {"$set": {"disabled": True}})
        assert (await client.get(ORDERS, headers=staff)).status_code == 403


@pytest.mark.asyncio
async def test_product_write_saves_only_an_employee_draft_and_read_mode_blocks_it(store):
    async with await build_client(store) as client:
        await store[PRODUCTS].insert_many([
            {"id": "ours", "salla_product_id": "123", "user_id": "owner", "name": "Old name", "price": 100},
            {"id": "foreign", "salla_product_id": "124", "user_id": "other-owner", "name": "Other store"},
        ])
        await store[DRAFTS].insert_one({
            "id": "owner-draft", "user_id": "owner", "salla_product_id": "123", "status": "approved",
        })
        staff = actor("employee")
        draft_path = f"{CATALOGUE}/ours/control-center/draft"
        payload = {"changes": {"name": "New name", "price": 120}}
        assert (await save(client, {PRODUCTS_PAGE: "read"})).status_code == 200
        assert (await client.put(draft_path, json=payload, headers=staff)).status_code == 403
        assert (await save(client, {PRODUCTS_PAGE: "write"}, revision=1)).status_code == 200
        saved = await client.put(draft_path, json=payload, headers=staff)
        assert saved.status_code == 200
        draft = saved.json()["draft"]
        assert draft["actor_id"] == "employee" and draft["user_id"] == "owner"
        assert draft["changes"] == payload["changes"]
        updated = await client.put(draft_path, json={"changes": {"price": 125}}, headers=staff)
        assert updated.json()["draft"]["id"] == draft["id"]
        assert (await store[DRAFTS].find_one({"id": "owner-draft"}))["status"] == "approved"
        owner_view = await client.get(f"{CATALOGUE}/ours/control-center", headers=actor("owner"))
        assert owner_view.json()["draft"]["id"] == "owner-draft"
        assert (await store[PRODUCTS].find_one({"id": "ours"}))["name"] == "Old name"
        assert (await client.put(draft_path, json={"changes": {"cost_price": 7}}, headers=staff)).status_code == 422
        assert (await client.put(f"{CATALOGUE}/foreign/control-center/draft", json=payload, headers=staff)).status_code == 404
        assert (await client.post(f"{draft_path}/{draft['id']}/approve", headers=staff)).status_code == 403


@pytest.mark.asyncio
async def test_intake_write_creates_only_own_drafts_and_keeps_owner_approval_required(store):
    async with await build_client(store) as client:
        staff = actor("employee")
        drafts_url = f"{CATALOGUE}/creation-drafts"
        payload = {"name": "Test product", "sku": "INTAKE-TEST", "price": 35}
        await store[CREATION_DRAFTS].insert_one({
            "user_id": "owner", "id": "owner-only", "sku": "OWNER-SKU", "created_by": "owner", "status": "draft",
        })
        assert (await save(client, {INTAKE_PAGE: "read"})).status_code == 200
        assert (await client.post(drafts_url, json=payload, headers=staff)).status_code == 403
        assert (await save(client, {INTAKE_PAGE: "write"}, revision=1)).status_code == 200
        created = await client.post(drafts_url, json=payload, headers=staff)
        assert created.status_code == 200, created.text
        draft = created.json()["draft"]
        assert draft["created_by"] == "employee" and draft["user_id"] == "owner"
        assert (await client.get(f"{drafts_url}/owner-only", headers=staff)).status_code == 404
        assert (await client.post(f"{drafts_url}/{draft['id']}/approve", headers=staff)).status_code == 403
        await store[CREATION_DRAFTS].update_one({"id": draft["id"]}, {"$set": {"status": "approved"}})
        assert (await client.put(f"{drafts_url}/{draft['id']}", json=payload, headers=staff)).status_code == 409


@pytest.mark.asyncio
async def test_components_page_read_hides_costs_and_write_can_organize_components(store):
    async with await build_client(store) as client:
        await store[RESOURCES].insert_many([
            {"id": "ours", "user_id": "owner", "name": "Our component", "code": "C1", "unit_cost": 12},
            {"id": "theirs", "user_id": "other-owner", "name": "Foreign", "code": "C1"},
        ])
        staff = actor("employee")
        base = "/api/components-v2"
        assert (await save(client, {COMPONENTS_PAGE: "read"})).status_code == 200
        response = await client.get(f"{base}/workspace", headers=staff)
        assert response.status_code == 200
        assert [row["id"] for row in response.json()["components"]] == ["ours"]
        assert "unit_cost" not in response.json()["components"][0]
        assert (await client.post(f"{base}/categories", json={"name": "Test"}, headers=staff)).status_code == 403
        assert (await save(client, {COMPONENTS_PAGE: "write"}, revision=1)).status_code == 200
        created = await client.post(f"{base}/categories", json={"name": "Test"}, headers=staff)
        assert created.status_code == 201
        assert created.json()["category"]["name"] == "Test"
        outsider = await client.get(f"{base}/workspace", headers=actor("other-owner"))
        assert outsider.json()["categories"] == []


@pytest.mark.asyncio
async def test_warehouse_write_records_employee_and_stays_in_merchant_scope(store):
    async with await build_client(store) as client:
        await store[WAREHOUSES].insert_one({"id": "foreign", "user_id": "other-owner", "code": "F"})
        staff = actor("employee")
        list_url = "/api/warehouse-locations/warehouses"
        create_url = "/api/warehouse-locations-v2/warehouses"
        payload = {"name": "Test branch", "country": "السعودية", "city": "الرياض", "district": "Test", "street": "Main"}
        assert (await save(client, {WAREHOUSES_PAGE: "read"})).status_code == 200
        assert (await client.get(list_url, headers=staff)).json()["items"] == []
        assert (await client.post(create_url, json=payload, headers=staff)).status_code == 403
        assert (await save(client, {WAREHOUSES_PAGE: "write"}, revision=1)).status_code == 200
        created = await client.post(create_url, json=payload, headers=staff)
        assert created.status_code == 201
        assert created.json()["created_by"] == "employee"
        assert created.json()["user_id"] == "owner"
        assert [row["id"] for row in (await client.get(list_url, headers=staff)).json()["items"]] == [created.json()["id"]]
