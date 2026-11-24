"""Human cabinet/column/row numbers must not become scanner identities."""
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient

from operational_purchase_metadata import _locations
from warehouse_location_labels import location_label_fields
from warehouse_location_routes import CABINETS, LOCATIONS, WAREHOUSES, make_warehouse_location_router
from warehouse_location_v2_routes import COUNTERS, make_warehouse_location_v2_router
from warehouse_room_routes import ROOMS, make_warehouse_room_router


MODE = "cabinet-column-row"


@pytest.mark.parametrize("cabinet,column,row,expected", [
    (1, 1, 1, "111"), (1, 2, 1, "121"), (1, 3, 1, "131"),
    (1, 2, 3, "123"), (4, 5, 6, "456"), (9, 9, 9, "999"),
    (1, 12, 3, "1-12-3"), (11, 2, 3, "11-2-3"),
    (1, 2, 10, "1-2-10"), (10, 12, 100, "10-12-100"),
])
def test_owner_numbering_is_cabinet_then_column_then_row(cabinet, column, row, expected):
    row_doc = {"row": row, "column": column, "number": 97, "code": "SCANNER-097"}
    before = deepcopy(row_doc)
    fields = location_label_fields({"location_numbering": MODE}, cabinet, row_doc)
    assert fields == {"display_code": expected, "location_numbering": MODE, "cabinet_number": cabinet}
    assert row_doc == before
    assert "code" not in fields and "barcode_value" not in fields


def test_existing_numbering_policies_keep_their_meaning():
    row = {"row": 3, "column": 2}
    assert location_label_fields({}, 1, row) == {}
    assert location_label_fields({"location_numbering": "legacy"}, 1, row) == {}
    assert location_label_fields({"location_numbering": "cabinet-row-slot"}, 1, row) == {
        "display_code": "1-3-2", "location_numbering": "cabinet-row-slot", "cabinet_number": 1,
    }


async def setup(mode="legacy", *, legacy_number=None):
    db = AsyncMongoMockClient().warehouse_short_codes_test
    await db.users.insert_one({"id": "owner", "role": "owner"})
    warehouse = {"id": "warehouse", "user_id": "owner", "name": "Warehouse", "code": "WH01",
                 "status": "active", "location_numbering": mode, "inventory_scope": "standalone"}
    await db[WAREHOUSES].insert_one(deepcopy(warehouse))
    await db[ROOMS].insert_many([
        {"id": "section-a", "user_id": "owner", "warehouse_id": "warehouse", "code": "S01",
         "room_number": 1, "name": "A", "capabilities": ["cabinets"], "status": "active"},
        {"id": "section-b", "user_id": "owner", "warehouse_id": "warehouse", "code": "S02",
         "room_number": 2, "name": "B", "capabilities": ["cabinets"], "status": "active"},
    ])
    if legacy_number is not None:
        await db[CABINETS].insert_one({
            "id": "existing-cabinet", "user_id": "owner", "warehouse_id": "warehouse", "room_id": "section-a",
            "cabinet_number": legacy_number, "code": f"S01-C{legacy_number:02d}", "name": "Existing",
            "location_numbering": "legacy", "length": 1, "width": 1, "total_locations": 1, "status": "active",
        })
        await db[LOCATIONS].insert_one({
            "id": "existing-location", "user_id": "owner", "warehouse_id": "warehouse",
            "cabinet_id": "existing-cabinet", "row": 1, "column": 1, "number": 1,
            "code": "WH01-EXISTING-001", "barcode_value": "WH01-EXISTING-001",
            "qr_value": "MEZAN-LOCATION:WH01-EXISTING-001", "state": "empty", "occupancy": None,
        })
    app = FastAPI()

    async def principal():
        return {"id": "owner", "role": "owner"}

    app.include_router(make_warehouse_location_router(db, principal))
    app.include_router(make_warehouse_location_v2_router(db, principal))
    app.include_router(make_warehouse_room_router(db, principal))
    return db, warehouse, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_new_cabinet_in_existing_warehouse_has_three_short_labels_and_original_scan_contract():
    db, warehouse, client = await setup()
    before = await db[WAREHOUSES].find_one({"id": "warehouse"})
    async with client:
        response = await client.post("/warehouse-locations-v2/sections/section-a/cabinets", json={
            "length": 1, "width": 3, "location_numbering": MODE,
        })
        assert response.status_code == 201, response.text
        cabinet = response.json()["cabinet"]
        assert cabinet["cabinet_number"] == 1
        assert cabinet["location_numbering"] == MODE
        rows = await db[LOCATIONS].find({"cabinet_id": cabinet["id"]}).sort("number", 1).to_list(20)
        assert [row["display_code"] for row in rows] == ["111", "121", "131"]
        assert [(row["column"], row["row"]) for row in rows] == [(1, 1), (2, 1), (3, 1)]
        assert len({row["id"] for row in rows}) == 3
        assert [row["barcode_value"] for row in rows] == [
            "WH01-S01-C01-001", "WH01-S01-C01-002", "WH01-S01-C01-003",
        ]
        for row in rows:
            assert row["code"] == row["barcode_value"]
            assert row["qr_value"] == "MEZAN-LOCATION:" + row["barcode_value"]
        scan = await client.get("/warehouse-locations-v2/locations/scan/WH01-S01-C01-002")
        assert scan.status_code == 200
        assert scan.json()["location"]["id"] == rows[1]["id"]
        assert (await client.get("/warehouse-locations-v2/locations/scan/121")).status_code == 404
        metadata = _locations([warehouse], rows)
        assert [row["name"] for row in metadata] == ["111", "121", "131"]
        assert [row["id"] for row in metadata] == [row["id"] for row in rows]
    assert await db[WAREHOUSES].find_one({"id": "warehouse"}) == before


@pytest.mark.asyncio
async def test_existing_cabinets_and_locations_are_not_renamed_or_renumbered():
    db, _, client = await setup(legacy_number=7)
    old_cabinet = await db[CABINETS].find_one({"id": "existing-cabinet"})
    old_location = await db[LOCATIONS].find_one({"id": "existing-location"})
    await db[COUNTERS].insert_one({"key": "cabinet:owner:warehouse", "value": 2})
    async with client:
        first = await client.post("/warehouse-locations-v2/sections/section-b/cabinets", json={
            "length": 1, "width": 3, "location_numbering": MODE,
        })
        assert first.status_code == 201, first.text
        second = await client.post("/warehouse-locations-v2/sections/section-a/cabinets", json={
            "length": 1, "width": 1, "location_numbering": MODE,
        })
        assert second.status_code == 201, second.text
    assert [first.json()["cabinet"]["cabinet_number"], second.json()["cabinet"]["cabinet_number"]] == [8, 9]
    new_ids = [first.json()["cabinet"]["id"], second.json()["cabinet"]["id"]]
    rows = await db[LOCATIONS].find({"cabinet_id": {"$in": new_ids}}).to_list(20)
    assert {row["display_code"] for row in rows} == {"811", "821", "831", "911"}
    assert await db[CABINETS].find_one({"id": "existing-cabinet"}) == old_cabinet
    assert await db[LOCATIONS].find_one({"id": "existing-location"}) == old_location


@pytest.mark.asyncio
async def test_direct_and_section_creation_share_the_new_number_sequence():
    db, _, client = await setup(mode=MODE)
    async with client:
        direct = await client.post("/warehouse-locations-v2/cabinets", json={
            "warehouse_id": "warehouse", "length": 3, "width": 2,
        })
        assert direct.status_code == 201, direct.text
        section = await client.post("/warehouse-locations-v2/sections/section-b/cabinets", json={
            "length": 1, "width": 1,
        })
        assert section.status_code == 201, section.text
    assert direct.json()["cabinet"]["cabinet_number"] == 1
    assert section.json()["cabinet"]["cabinet_number"] == 2
    location = await db[LOCATIONS].find_one({"cabinet_id": direct.json()["cabinet"]["id"], "column": 2, "row": 3})
    assert location["display_code"] == "123"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected", [("legacy", None), ("cabinet-row-slot", "1-3-2")])
async def test_older_clients_keep_inherited_numbering(mode, expected):
    db, _, client = await setup(mode=mode)
    async with client:
        response = await client.post("/warehouse-locations-v2/sections/section-a/cabinets", json={"length": 3, "width": 2})
        assert response.status_code == 201, response.text
    location = await db[LOCATIONS].find_one({"cabinet_id": response.json()["cabinet"]["id"], "column": 2, "row": 3})
    assert location.get("display_code") == expected


@pytest.mark.asyncio
async def test_new_warehouse_can_choose_the_new_policy_and_unknown_policy_is_rejected():
    db, _, client = await setup()
    async with client:
        response = await client.post("/warehouse-locations-v2/warehouses", json={
            "name": "New", "district": "District", "street": "Street", "location_numbering": MODE,
        })
        assert response.status_code == 201, response.text
        assert response.json()["location_numbering"] == MODE
        invalid = await client.post("/warehouse-locations-v2/sections/section-a/cabinets", json={
            "length": 1, "width": 3, "location_numbering": "cabinet-column-row-bin",
        })
        assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_foreign_warehouse_and_its_numbers_are_outside_the_creator_scope():
    db, _, client = await setup()
    await db[WAREHOUSES].insert_one({"id": "foreign", "user_id": "other", "code": "OTHER", "status": "active"})
    await db[ROOMS].insert_one({"id": "foreign-section", "user_id": "other", "warehouse_id": "foreign", "status": "active"})
    await db[COUNTERS].insert_one({"key": "cabinet:other:foreign", "value": 40})
    async with client:
        response = await client.post("/warehouse-locations-v2/sections/foreign-section/cabinets", json={
            "length": 1, "width": 3, "location_numbering": MODE,
        })
        assert response.status_code == 404
    assert (await db[COUNTERS].find_one({"key": "cabinet:other:foreign"}))["value"] == 40
    assert await db[CABINETS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_legacy_and_new_clients_share_numbers_without_resetting_old_section_counters():
    db, _, client = await setup(legacy_number=7)
    await db[COUNTERS].insert_one({"key": "section-cabinet:owner:section-b", "value": 7})
    async with client:
        legacy = await client.post("/warehouse-locations-v2/sections/section-b/cabinets", json={"length": 1, "width": 1})
        assert legacy.status_code == 201, legacy.text
        direct = await client.post("/warehouse-locations-v2/cabinets", json={
            "warehouse_id": "warehouse", "length": 1, "width": 1, "location_numbering": MODE,
        })
        assert direct.status_code == 201, direct.text
    assert legacy.json()["cabinet"]["cabinet_number"] == 8
    assert direct.json()["cabinet"]["cabinet_number"] == 9
    assert "display_code" not in await db[LOCATIONS].find_one({"cabinet_id": legacy.json()["cabinet"]["id"]})
    assert (await db[LOCATIONS].find_one({"cabinet_id": direct.json()["cabinet"]["id"]}))["display_code"] == "911"
    assert await db[CABINETS].count_documents({"cabinet_number": 7}) == 1
    assert (await db[COUNTERS].find_one({"key": "section-cabinet:owner:section-b"}))["value"] == 7


@pytest.mark.asyncio
async def test_higher_counter_is_never_reset_and_missing_legacy_numbers_are_not_inferred():
    db, _, client = await setup(legacy_number=2)
    await db[CABINETS].insert_one({
        "id": "manual", "user_id": "owner", "warehouse_id": "warehouse", "code": "99", "status": "active",
    })
    await db[CABINETS].insert_one({
        "id": "foreign-high", "user_id": "other", "warehouse_id": "warehouse", "cabinet_number": 90,
    })
    await db[COUNTERS].insert_one({"key": "cabinet:owner:warehouse", "value": 11})
    async with client:
        response = await client.post("/warehouse-locations-v2/cabinets", json={
            "warehouse_id": "warehouse", "length": 1, "width": 1, "location_numbering": MODE,
        })
        assert response.status_code == 201, response.text
    assert response.json()["cabinet"]["cabinet_number"] == 12
    assert (await db[LOCATIONS].find_one({"cabinet_id": response.json()["cabinet"]["id"]}))["display_code"] == "12-1-1"
    assert "cabinet_number" not in await db[CABINETS].find_one({"id": "manual"})


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [0, -1, "2", True, 2.5, None])
async def test_invalid_existing_cabinet_numbers_are_reported_without_a_write(invalid):
    db, _, client = await setup(legacy_number=2)
    await db[CABINETS].update_one({"id": "existing-cabinet"}, {"$set": {"cabinet_number": invalid}})
    before = await db[CABINETS].find_one({"id": "existing-cabinet"})
    async with client:
        response = await client.post("/warehouse-locations-v2/sections/section-b/cabinets", json={
            "length": 1, "width": 1, "location_numbering": MODE,
        })
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "cabinet_numbering_invalid"
    assert await db[CABINETS].find_one({"id": "existing-cabinet"}) == before
    assert await db[CABINETS].count_documents({}) == 1
    assert await db[COUNTERS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_invalid_counter_is_not_silently_repaired():
    db, _, client = await setup()
    await db[COUNTERS].insert_one({"key": "cabinet:owner:warehouse", "value": "12"})
    async with client:
        response = await client.post("/warehouse-locations-v2/cabinets", json={
            "warehouse_id": "warehouse", "length": 1, "width": 1, "location_numbering": MODE,
        })
        assert response.status_code == 409, response.text
    assert (await db[COUNTERS].find_one({"key": "cabinet:owner:warehouse"}))["value"] == "12"
    assert await db[CABINETS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_opening_catalog_preserves_short_label_location_id_and_full_barcode():
    from accounting_onboarding_domains import onboarding_inventory_catalog

    db, _, client = await setup()
    async with client:
        response = await client.post("/warehouse-locations-v2/sections/section-a/cabinets", json={
            "length": 1, "width": 2, "location_numbering": MODE,
        })
        assert response.status_code == 201
    stored = await db[LOCATIONS].find_one({"display_code": "121"})
    result = await onboarding_inventory_catalog(db, "owner")
    row = next(row for row in result["locations"] if row["id"] == stored["id"])
    assert row["display_code"] == "121"
    assert row["warehouse_id"] == "warehouse"
    assert row["code"] == row["barcode_value"] == "WH01-S01-C01-002"
    assert row["provenance"] == "AMBIGUOUS" and row["physical_approval_verified"] is False
    assert await db[LOCATIONS].find_one({"id": stored["id"]}) == stored


@pytest.mark.asyncio
async def test_purchase_catalog_distinguishes_same_short_code_in_different_warehouses(monkeypatch):
    from unittest.mock import AsyncMock
    import purchase_invoices_routes as routes

    db = AsyncMongoMockClient().warehouse_short_code_catalog_test
    actor = {"id": "owner", "role": "owner"}
    await db.users.insert_one(dict(actor))
    await db[WAREHOUSES].insert_many([
        {"id": "first", "user_id": "owner", "name": "المخزن الأول", "code": "WH01"},
        {"id": "second", "user_id": "owner", "name": "المخزن الثاني", "code": "WH02"},
        {"id": "foreign", "user_id": "other", "name": "Foreign", "code": "OTHER"},
    ])
    await db[LOCATIONS].insert_many([
        {"id": "one", "user_id": "owner", "warehouse_id": "first", "purpose": "permanent_storage",
         "display_code": "111", "code": "WH01-1-001", "barcode_value": "WH01-1-001"},
        {"id": "two", "user_id": "owner", "warehouse_id": "second", "purpose": "permanent_storage",
         "display_code": "111", "code": "WH02-1-001", "barcode_value": "WH02-1-001"},
        {"id": "foreign-location", "user_id": "other", "warehouse_id": "foreign", "purpose": "permanent_storage",
         "display_code": "111", "code": "OTHER-1-001", "barcode_value": "OTHER-1-001"},
    ])
    monkeypatch.setattr(routes, "get_current_user_from_db", AsyncMock(return_value=actor))
    monkeypatch.setattr(routes, "approved_account_mappings", AsyncMock(return_value=({}, {"supplier": []})))
    app = FastAPI()
    routes.attach_purchase_invoice_routes(app, db)
    before = await db[LOCATIONS].find({}).to_list(10)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/purchase-invoices/catalog")
        assert response.status_code == 200, response.text
    rows = {row["id"]: row for row in response.json()["locations"]}
    assert set(rows) == {"one", "two"}
    assert rows["one"]["display_code"] == rows["two"]["display_code"] == "111"
    assert rows["one"]["warehouse_name"] == "المخزن الأول"
    assert rows["two"]["warehouse_name"] == "المخزن الثاني"
    assert rows["one"]["barcode"] == rows["one"]["code"] == "WH01-1-001"
    assert rows["two"]["barcode"] == rows["two"]["code"] == "WH02-1-001"
    assert await db[LOCATIONS].find({}).to_list(10) == before
