import copy
import pytest
from fastapi import HTTPException
from accounting_onboarding_inventory_catalog import get_inventory_catalog, validate_inventory_setup

class Cursor:
    def __init__(self, rows): self.rows = rows
    def sort(self, *args): return self
    async def to_list(self, length): return copy.deepcopy(self.rows[:length])

class ReadOnlyCollection:
    def __init__(self, rows): self.rows = rows
    def match(self, row, query):
        return all(any(self.match(row, q) for q in value) if key == "$or" else row.get(key) == value for key, value in query.items())
    def find(self, query, projection): return Cursor([r for r in self.rows if self.match(r, query)])
    async def find_one(self, query, projection): return next((copy.deepcopy(r) for r in self.rows if self.match(r, query)), None)

class ReadOnlyDB:
    def __init__(self, collections): self.collections = collections; self.reads = []
    def __getitem__(self, key):
        self.reads.append(key)
        assert key in {"mezan_products_v2", "mezan_cost_resources_v2", "mezan_component_categories_v2", "warehouse_locations", "warehouse_locations_warehouses"}, "Legacy access forbidden"
        return ReadOnlyCollection(self.collections.get(key, []))

@pytest.fixture
def db():
    return ReadOnlyDB({
        "mezan_products_v2": [{"user_id": "owner", "mezan_product_id": "p1", "name": "Abaya", "main_image": "https://catalog.test/product.jpg", "sku": "SKU", "barcode": "BAR", "options": [{"id": "color", "name": "Color", "values": [{"id": "black", "name": "Black"}]}], "variants": [{"id": "v1", "image": "https://catalog.test/black.jpg", "options": [{"option_id": "color", "value_id": "black"}]}, {"id": "v2"}]}, {"user_id": "other", "mezan_product_id": "foreign"}, {"user_id": "owner", "mezan_product_id": "inactive", "status": "inactive"}],
        "mezan_cost_resources_v2": [{"user_id": "owner", "id": "c1", "kind": "stock_component", "track_inventory": True, "unit": "meter", "category_ids": ["cat"]}, {"user_id": "owner", "id": "service", "kind": "service", "track_inventory": True}, {"user_id": "owner", "id": "inactive", "track_inventory": True, "status": "inactive"}],
        "mezan_component_categories_v2": [{"user_id": "owner", "id": "cat", "name": "Fabric"}],
        "warehouse_locations_warehouses": [{"user_id": "owner", "id": "w", "name": "Warehouse"}],
        "warehouse_locations": [{"user_id": "owner", "id": "loc", "warehouse_id": "w", "code": "A1"}],
        "products": [{"user_id": "owner", "id": "legacy-product"}],
        "components": [{"user_id": "owner", "id": "legacy-component"}],
    })

@pytest.mark.asyncio
async def test_read_only_canonical_catalog_excludes_legacy_foreign_and_service(db):
    result = await get_inventory_catalog(db, "owner")
    assert result["read_only"] is True
    assert [p["id"] for p in result["products"]] == ["p1"]
    p = result["products"][0]
    assert p["main_image"] == "https://catalog.test/product.jpg"
    assert p["variants"][0]["image"] == "https://catalog.test/black.jpg"
    assert p["variants"][0]["selections"] == [{"name": "Color", "value": "Black"}]
    assert [c["id"] for c in result["components"]] == ["c1"]
    assert result["locations"][0]["warehouse_name"] == "Warehouse"
    assert len(db.reads) == 5  # No index or mutation capability exists on fixture.

@pytest.mark.asyncio
async def test_real_variants_remain_distinct_without_physical_distribution(db):
    rows = [{"item_type": "PRODUCT", "product_id": "p1", "variant_id": v, "allocations": []} for v in ["v1", "v2"]]
    await validate_inventory_setup(db, "owner", rows)
    await validate_inventory_setup(db, "owner", [{"item_type": "STOCK_COMPONENT", "resource_id": "c1", "category_id": "cat"}])
    await validate_inventory_setup(db, "owner", [{"item_type": "PRODUCT", "product_id": ""}])

@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [{"product_id": "legacy-product"}, {"product_id": "foreign"}, {"product_id": "inactive"}, {"variant_id": "fabricated"}, {"selected_options": {"color": "invented"}}, {"variant_id": "v1", "selected_options": {"color": "black"}}])
async def test_rejects_noncanonical_or_inconsistent_selection(db, patch):
    with pytest.raises(HTTPException) as exc:
        await validate_inventory_setup(db, "owner", [{"item_type": "PRODUCT", "product_id": "p1", **patch}])
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "onboarding_inventory_identity_invalid"

@pytest.mark.asyncio
async def test_component_and_warehouse_validation_no_legacy_fallback(db):
    for row in [{"item_type": "STOCK_COMPONENT", "resource_id": "legacy-component"}, {"item_type": "STOCK_COMPONENT", "resource_id": "service"}, {"item_type": "PRODUCT", "product_id": "p1", "allocations": [{"location_id": "foreign"}]}]:
        with pytest.raises(HTTPException): await validate_inventory_setup(db, "owner", [row])
    row = {"item_type": "PRODUCT", "product_id": "p1", "variant_id": "v1"}
    with pytest.raises(HTTPException): await validate_inventory_setup(db, "owner", [row, row])
