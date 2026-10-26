"""Supplier category/service contracts using an isolated in-memory catalog."""
import pytest
from mongomock import MongoClient
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from component_category_policy import COMPONENT_CATEGORIES
from mezan_supplier_management_routes import MEZAN_SUPPLIERS_V2, make_mezan_supplier_management_router
from product_option_cost_routes import RESOURCES
from supplier_mobile_routes import make_supplier_mobile_router
from preparation_supplier_dispatch import _active_suppliers
from supplier_category_service_policy import require_supplier_product_categories
from product_v2_routes import PRODUCTS
from fastapi import HTTPException


@pytest.fixture
def catalog():
    mongo = MongoClient()
    raw = mongo.supplier_category_contract
    db = AsyncMongoMockClient(mock_mongo_client=mongo).supplier_category_contract
    raw[COMPONENT_CATEGORIES].insert_many([
        {"user_id": "owner", "id": "clothes", "name": "ملابس"},
        {"user_id": "owner", "id": "plated", "name": "مطليات"},
        {"user_id": "other", "id": "private", "name": "خاص"},
        {"user_id": "owner", "id": "stopped", "status": "inactive"},
    ])
    raw[RESOURCES].insert_many([
        {"user_id": "owner", "id": "embroidery", "name": "تطريز", "kind": "service", "category_ids": ["clothes"]},
        {"user_id": "owner", "id": "engraving", "name": "حفر", "kind": "service", "category_ids": ["plated"]},
        {"user_id": "owner", "id": "shared", "name": "تغليف", "kind": "service", "category_ids": ["clothes", "plated"]},
        {"user_id": "owner", "id": "stock", "kind": "stock_component", "track_inventory": True, "category_ids": ["clothes"]},
        {"user_id": "owner", "id": "inactive", "kind": "service", "status": "inactive", "category_ids": ["clothes"]},
        {"user_id": "other", "id": "foreign", "kind": "service", "category_ids": ["clothes"]},
    ])
    app = FastAPI()
    actor = lambda: {"id": "owner", "role": "owner"}
    app.include_router(make_mezan_supplier_management_router(db, actor))
    app.include_router(make_supplier_mobile_router(db, actor), prefix="/reports/suppliers")
    return db, TestClient(app), raw


@pytest.mark.parametrize("path", ["/suppliers-v2", "/reports/suppliers/mobile"])
def test_category_only_supplier_does_not_acquire_services_automatically(catalog, path):
    db, client, raw = catalog
    response = client.post(path, json={"company_name": "مورد", "category_ids": ["clothes", "plated"]})
    assert response.status_code == 201, response.text
    saved = raw[MEZAN_SUPPLIERS_V2].find_one({"user_id": "owner"})
    assert saved["category_ids"] == ["clothes", "plated"]
    assert saved["service_ids"] == []
    assert saved["service_links"] == []


@pytest.mark.parametrize("path", ["/suppliers-v2", "/reports/suppliers/mobile"])
def test_only_explicit_services_from_selected_categories_are_saved(catalog, path):
    db, client, raw = catalog
    response = client.post(path, json={"company_name": "مورد", "category_ids": ["clothes"], "service_ids": ["shared", "shared"]})
    assert response.status_code == 201, response.text
    assert raw[MEZAN_SUPPLIERS_V2].find_one({})["service_ids"] == ["shared"]


@pytest.mark.parametrize("path", ["/suppliers-v2", "/reports/suppliers/mobile"])
@pytest.mark.parametrize("service", ["engraving", "stock", "inactive", "foreign", "missing"])
def test_invalid_service_is_rejected_before_any_write(catalog, path, service):
    db, client, raw = catalog
    response = client.post(path, json={"company_name": "مورد", "category_ids": ["clothes"], "service_ids": [service]})
    assert response.status_code == 422, response.text
    assert raw[MEZAN_SUPPLIERS_V2].count_documents({}) == 0


@pytest.mark.parametrize("category_ids", [[], ["private"], ["missing"], ["stopped"]])
@pytest.mark.parametrize("path", ["/suppliers-v2", "/reports/suppliers/mobile"])
def test_categories_must_be_present_active_and_owned(catalog, path, category_ids):
    db, client, raw = catalog
    response = client.post(path, json={"company_name": "مورد", "category_ids": category_ids, "service_ids": []})
    assert response.status_code == 422, response.text
    assert raw[MEZAN_SUPPLIERS_V2].count_documents({}) == 0


def test_update_validates_category_change_without_losing_saved_supplier(catalog):
    db, client, raw = catalog
    original = {"id": "existing", "user_id": "owner", "company_name": "مورد", "category_ids": ["clothes"], "service_ids": ["embroidery"], "service_links": [{"service_id": "embroidery"}]}
    raw[MEZAN_SUPPLIERS_V2].insert_one(dict(original))
    response = client.put("/suppliers-v2/existing", json={"company_name": "مورد", "category_ids": ["plated"], "service_ids": ["embroidery"]})
    assert response.status_code == 422, response.text
    assert raw[MEZAN_SUPPLIERS_V2].find_one({"id": "existing"})["category_ids"] == ["clothes"]


def test_workspace_supplies_category_and_service_picker_without_inactive_or_foreign_rows(catalog):
    _, client, _ = catalog
    data = client.get("/suppliers-v2/workspace").json()
    assert {row["id"] for row in data["categories"]} == {"clothes", "plated"}
    assert {row["id"] for row in data["services"]} == {"embroidery", "engraving", "shared"}
    assert next(row for row in data["services"] if row["id"] == "shared")["category_ids"] == ["clothes", "plated"]
    assert data["rules"]["service_required"] is False


@pytest.mark.asyncio
async def test_category_only_supplier_is_available_for_dispatch_and_legacy_is_preserved(catalog):
    db, _, _ = catalog
    await db[MEZAN_SUPPLIERS_V2].insert_many([
        {"user_id": "owner", "id": "category-only", "category_ids": ["clothes"], "service_ids": []},
        {"user_id": "owner", "id": "legacy", "service_ids": ["embroidery"]},
        {"user_id": "owner", "id": "disabled", "category_ids": ["clothes"], "status": "inactive"},
        {"user_id": "other", "id": "foreign", "category_ids": ["clothes"]},
    ])
    assert {row["id"] for row in await _active_suppliers(db, user_id="owner")} == {"category-only", "legacy"}


@pytest.mark.asyncio
async def test_dispatch_without_services_accepts_each_selected_category(catalog):
    db, _, _ = catalog
    await db[PRODUCTS].insert_many([
        {"user_id": "owner", "id": "p1", "salla_product_id": "s1", "cost_category_id": "clothes"},
        {"user_id": "owner", "id": "p2", "cost_category_id": "plated"},
    ])
    await require_supplier_product_categories(db, user_id="owner",
        supplier={"category_ids": ["clothes", "plated"], "service_ids": []},
        pieces=[{"product_id": "s1"}, {"product_id": "p2"}])


@pytest.mark.asyncio
@pytest.mark.parametrize("product,expected", [
    ({"user_id": "owner", "id": "p1", "cost_category_id": "plated"}, "supplier_dispatch_category_mismatch"),
])
async def test_dispatch_rejects_explicit_category_mismatch(catalog, product, expected):
    db, _, raw = catalog
    await db[PRODUCTS].insert_one(product)
    before = list(raw[MEZAN_SUPPLIERS_V2].find({}))
    with pytest.raises(HTTPException) as caught:
        await require_supplier_product_categories(db, user_id="owner", supplier={"category_ids": ["clothes"]}, pieces=[{"product_id": "p1"}])
    assert caught.value.detail["code"] == expected
    assert list(raw[MEZAN_SUPPLIERS_V2].find({})) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("products", [
    [],
    [{"user_id": "owner", "id": "p1"}],
    [{"user_id": "owner", "id": "p1", "cost_category_id": ""}],
    [{"user_id": "other", "id": "p1", "cost_category_id": "plated"}],
    [{"user_id": "owner", "id": "p1", "cost_category_id": "clothes"},
     {"user_id": "owner", "id": "duplicate", "salla_product_id": "p1", "cost_category_id": "plated"}],
])
async def test_dispatch_does_not_require_product_classification_or_mutate_catalog(catalog, products):
    db, _, raw = catalog
    if products:
        await db[PRODUCTS].insert_many(products)
    before = list(raw[PRODUCTS].find({}))
    supplier = {"category_ids": ["clothes"], "service_ids": []}
    await require_supplier_product_categories(
        db, user_id="owner", supplier=supplier, pieces=[{"product_id": "p1"}])
    assert list(raw[PRODUCTS].find({})) == before
    assert supplier == {"category_ids": ["clothes"], "service_ids": []}


@pytest.mark.asyncio
async def test_existing_service_only_supplier_is_not_silently_reclassified_or_blocked(catalog):
    db, _, _ = catalog
    supplier = {"service_ids": ["embroidery"]}
    await require_supplier_product_categories(db, user_id="owner", supplier=supplier, pieces=[{"product_id": "p1"}])
    assert supplier == {"service_ids": ["embroidery"]}
