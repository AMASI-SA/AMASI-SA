"""Lifecycle, rollback and races on an exclusively owned local Mongo replica."""
import asyncio
from copy import deepcopy
import os

from fastapi import FastAPI, HTTPException
import httpx
import pytest

from demand_read_mongo_fixture import read_db, read_mongo_uri as owned_mongo_uri
import operational_atomic
import warehouse_cabinet_lifecycle as lifecycle
from warehouse_location_routes import CABINETS, EVENTS, LOCATIONS, WAREHOUSES, make_warehouse_location_router
from warehouse_location_v2_routes import make_warehouse_location_v2_router
from warehouse_room_routes import ROOMS, make_warehouse_room_router
from inventory_receipt_service import InventoryLocationCapacityError, place_inventory_receipt


pytestmark = pytest.mark.asyncio
OWNER = "cabinet-archive-test-owner"
ACTOR = {"id": OWNER, "role": "owner", "is_owner": True}
WAREHOUSE = "archive-warehouse"
SECTION = "archive-section"
CABINET = "old-cabinet"


@pytest.fixture(scope="session")
def read_mongo_uri(tmp_path_factory, request):
    assert not os.environ.get("DEMAND_READ_TEST_MONGO_URI"), "Archive tests require their own Mongo process"
    if not os.environ.get("DEMAND_READ_TEST_MONGOD"):
        pytest.skip("DEMAND_READ_TEST_MONGOD required; no URI fallback")
    yield from owned_mongo_uri.__wrapped__(tmp_path_factory, request)


async def seed(db):
    await db[WAREHOUSES].insert_one({"user_id": OWNER, "id": WAREHOUSE, "code": "RUH-WH001", "name": "الفرع", "status": "active"})
    await db[ROOMS].insert_one({"user_id": OWNER, "id": SECTION, "warehouse_id": WAREHOUSE, "status": "active", "section_number": 1})
    cabinet = {"user_id": OWNER, "id": CABINET, "warehouse_id": WAREHOUSE, "warehouse_code": "RUH-WH001",
               "room_id": SECTION, "section_id": SECTION, "code": "S01-C01", "name": "الدولاب القديم",
               "cabinet_number": 1, "length": 3, "width": 3, "total_locations": 9,
               "purpose": "permanent_storage", "location_numbering": "legacy", "status": "active"}
    await db[CABINETS].insert_one(deepcopy(cabinet))
    for number in range(1, 10):
        code = f"RUH-WH001-S01-C01-{number:03d}"
        await db[LOCATIONS].insert_one({"user_id": OWNER, "id": f"old-{number}", "warehouse_id": WAREHOUSE,
            "room_id": SECTION, "section_id": SECTION, "cabinet_id": CABINET, "cabinet_code": "S01-C01",
            "number": number, "row": (number - 1) // 3 + 1, "column": (number - 1) % 3 + 1,
            "code": code, "barcode_value": code, "qr_value": "MEZAN-LOCATION:" + code,
            "purpose": "permanent_storage", "state": "empty", "occupancy": None,
            "max_items": 100, "updated_at": "original-time"})
    await db[CABINETS].insert_one({**cabinet, "id": "new-cabinet", "cabinet_number": 2, "code": "S01-C02", "name": "الدولاب الجديد"})
    await db["warehouse_location_counters"].insert_one({"user_id": OWNER, "key": f"cabinet:{OWNER}:{WAREHOUSE}", "value": 2})
    await db["mezan_role_assignments_v2"].insert_one({"owner_user_id": OWNER, "user_id": "employee", "warehouse_ids": [WAREHOUSE], "workplace_warehouse_id": WAREHOUSE})
    await db["mz2_atomic_owners"].insert_one({"_id": OWNER, "revision": 7, "writes_paused": True})
    await db["ledger_entries"].insert_one({"user_id": OWNER, "id": "financial-sentinel", "amount": 12})


def client(db, user=ACTOR):
    app = FastAPI()
    async def current_user():
        return user
    for factory in (make_warehouse_location_router, make_warehouse_location_v2_router, make_warehouse_room_router):
        app.include_router(factory(db, current_user))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://owned-test")


async def rows(db, collection):
    return await db[collection].find({}, {"_id": 0}).sort("id", 1).to_list(100)


async def hierarchy(db):
    return {name: await rows(db, name) for name in (WAREHOUSES, ROOMS, CABINETS, LOCATIONS, EVENTS,
        "warehouse_location_counters", "mezan_role_assignments_v2", "ledger_entries")}


async def test_archive_restore_preserves_parents_new_cabinet_identity_history_and_original_disabled(read_db):
    db = read_db
    await seed(db)
    await db[LOCATIONS].update_one({"id": "old-2"}, {"$set": {"state": "disabled"}})
    for collection, record in (
        ("mezan_inventory_receipts_v2", {"id": "receipt", "location_id": "old-1", "warehouse_id": WAREHOUSE, "status": "posted"}),
        ("mezan_inventory_reservations_v2", {"id": "old-reservation", "status": "consumed", "allocations": [{"location_id": "old-1"}]}),
        ("mezan_component_consumption_units_v1", {"id": "old-unit", "state": "released", "allocations": [{"location_id": "old-1"}]}),
        ("mezan_bundle_definitions_v2", {"id": "bundle", "items": [], "history": [{"items": [{"location_id": "old-1"}]}]}),
    ):
        await db[collection].insert_one({"user_id": OWNER, **record})
    before = await hierarchy(db)
    history = {name: await rows(db, name) for name in ("mezan_inventory_receipts_v2", "mezan_inventory_reservations_v2",
        "mezan_component_consumption_units_v1", "mezan_bundle_definitions_v2")}
    async with client(db) as api:
        archived = await api.post(f"/warehouse-locations-v2/cabinets/{CABINET}/archive")
        assert archived.status_code == 200, archived.text
        assert archived.json()["cabinet"]["status"] == "archived"
        assert archived.json()["already_archived"] is False
        assert (await api.post(f"/warehouse-locations-v2/cabinets/{CABINET}/archive")).json()["already_archived"] is True
        assert (await api.get(f"/warehouse-locations-v2/warehouses/{WAREHOUSE}/archived-cabinets")).json()["total"] == 1
        assert [c["id"] for c in (await api.get(f"/warehouse-locations/cabinets?warehouse_id={WAREHOUSE}")).json()["items"]] == ["new-cabinet"]
        assert (await api.get(f"/warehouse-locations/locations?cabinet_id={CABINET}")).json()["items"] == []
        assert (await api.get(f"/warehouse-locations/warehouses/{WAREHOUSE}")).json()["cabinet_count"] == 1
        current = await rows(db, LOCATIONS)
        assert len(current) == 9 and {row["state"] for row in current} == {"disabled"}
        for previous, now in zip(before[LOCATIONS], current):
            assert now["cabinet_archive"]["previous_state"] == previous["state"]
            assert {k: v for k, v in now.items() if k not in {"state", "cabinet_archive", "updated_at", "updated_by"}} == {
                k: v for k, v in previous.items() if k not in {"state", "updated_at"}}
        restored = await api.post(f"/warehouse-locations-v2/cabinets/{CABINET}/restore")
        assert restored.status_code == 200, restored.text
        assert restored.json()["cabinet"]["status"] == "active"
        assert (await api.post(f"/warehouse-locations-v2/cabinets/{CABINET}/restore")).json()["already_restored"] is True
        assert (await api.get(f"/warehouse-locations-v2/warehouses/{WAREHOUSE}/archived-cabinets")).json()["total"] == 0
    after = await hierarchy(db)
    for name in (WAREHOUSES, ROOMS, "warehouse_location_counters", "mezan_role_assignments_v2", "ledger_entries"):
        assert after[name] == before[name]
    assert next(c for c in after[CABINETS] if c["id"] == "new-cabinet") == next(c for c in before[CABINETS] if c["id"] == "new-cabinet")
    assert [(row["id"], row["state"], row["occupancy"]) for row in after[LOCATIONS]] == [(row["id"], row["state"], row["occupancy"]) for row in before[LOCATIONS]]
    assert all("cabinet_archive" not in row for row in after[LOCATIONS])
    assert sorted(row["event_type"] for row in after[EVENTS]) == ["cabinet_archived", "cabinet_restored"]
    for name, original in history.items():
        assert await rows(db, name) == original
    assert (await db.mz2_atomic_owners.find_one({"_id": OWNER}))["writes_paused"] is True


@pytest.mark.parametrize(("collection", "document", "code"), [
    ("mezan_inventory_reservations_v2", {"status": "active", "allocations": [{"location_id": "old-1"}]}, "cabinet_active_reservations"),
    ("mezan_inventory_reservations_v2", {"status": "unknown", "allocations": [{"location_id": "old-1"}]}, "cabinet_active_reservations"),
    ("mezan_component_consumption_units_v1", {"state": "reserved", "allocations": [{"location_id": "old-1"}]}, "cabinet_reserved_components"),
    ("mezan_bundle_definitions_v2", {"items": [{"location_id": "old-1", "required": False}]}, "cabinet_bundle_reference"),
])
async def test_archive_blocks_current_references_without_any_partial_mutation(read_db, collection, document, code):
    await seed(read_db)
    await read_db[collection].insert_one({"user_id": OWNER, **document})
    before = await hierarchy(read_db)
    async with client(read_db) as api:
        response = await api.post(f"/warehouse-locations-v2/cabinets/{CABINET}/archive")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == code and detail["blocker"]["count"] == 1 and detail["message"]
    assert await hierarchy(read_db) == before


@pytest.mark.parametrize("change", [
    {"state": "reserved"}, {"state": "occupied"}, {"state": "unknown"}, {"state": []},
    {"occupancy": {"items": [{"quantity": 0}], "total_quantity": 0}},
    {"occupancy": {"items": [], "total_quantity": 1}}, {"current_quantity": 1},
])
async def test_archive_rejects_stock_holds_and_unknown_state(read_db, change):
    await seed(read_db)
    await read_db[LOCATIONS].update_one({"id": "old-1"}, {"$set": change})
    before = await hierarchy(read_db)
    async with client(read_db) as api:
        response = await api.post(f"/warehouse-locations-v2/cabinets/{CABINET}/archive")
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "cabinet_locations_not_empty"
    assert await hierarchy(read_db) == before


@pytest.mark.parametrize("defect", ["missing", "duplicate_coordinate", "wrong_parent", "wrong_total", "section_aliases"])
async def test_archive_requires_exact_complete_structure(read_db, defect):
    await seed(read_db)
    if defect == "missing":
        await read_db[LOCATIONS].delete_one({"id": "old-9"})
    elif defect == "duplicate_coordinate":
        await read_db[LOCATIONS].update_one({"id": "old-9"}, {"$set": {"row": 1, "column": 1, "number": 1}})
    elif defect == "wrong_parent":
        await read_db[LOCATIONS].update_one({"id": "old-9"}, {"$set": {"warehouse_id": "elsewhere"}})
    elif defect == "wrong_total":
        await read_db[CABINETS].update_one({"id": CABINET}, {"$set": {"total_locations": 8}})
    else:
        await read_db[CABINETS].update_one({"id": CABINET}, {"$set": {"section_id": "elsewhere"}})
    before = await hierarchy(read_db)
    async with client(read_db) as api:
        response = await api.post(f"/warehouse-locations-v2/cabinets/{CABINET}/archive")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "cabinet_integrity_unknown"
    assert await hierarchy(read_db) == before


@pytest.mark.parametrize("user", [
    {"id": "other-owner", "role": "owner", "is_owner": True},
    {"id": "reader", "role": "viewer", "created_by": OWNER},
    {"id": "denied", "role": "warehouse", "created_by": OWNER, "denied_permissions": ["warehouse.manage"]},
])
async def test_archive_tenant_and_manager_boundary(read_db, user):
    await seed(read_db)
    before = await hierarchy(read_db)
    async with client(read_db, user) as api:
        for action in ("archive", "restore"):
            response = await api.post(f"/warehouse-locations-v2/cabinets/{CABINET}/{action}")
            assert response.status_code == (404 if user["id"] == "other-owner" else 403)
        response = await api.get(f"/warehouse-locations-v2/warehouses/{WAREHOUSE}/archived-cabinets")
        assert response.status_code == (404 if user["id"] == "other-owner" else 403)
    assert await hierarchy(read_db) == before


@pytest.mark.parametrize("defect", ["barcode", "previous_state", "missing_marker", "occupancy", "parent"])
async def test_restore_requires_intact_archive_and_active_parents(read_db, defect):
    await seed(read_db)
    await lifecycle.change_cabinet(read_db, ACTOR, CABINET)
    if defect == "parent":
        await read_db[ROOMS].update_one({"id": SECTION}, {"$set": {"status": "disabled"}})
    elif defect == "missing_marker":
        await read_db[LOCATIONS].update_one({"id": "old-1"}, {"$unset": {"cabinet_archive": ""}})
    else:
        change = {"barcode": {"barcode_value": "changed"}, "previous_state": {"cabinet_archive.previous_state": "occupied"},
                  "occupancy": {"occupancy": {"items": [], "total_quantity": 2}}}[defect]
        await read_db[LOCATIONS].update_one({"id": "old-1"}, {"$set": change})
    before = await hierarchy(read_db)
    async with client(read_db) as api:
        response = await api.post(f"/warehouse-locations-v2/cabinets/{CABINET}/restore")
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == ("cabinet_parent_unavailable" if defect == "parent" else "cabinet_archive_changed")
    assert await hierarchy(read_db) == before


@pytest.mark.parametrize("restore", [False, True])
async def test_lifecycle_transaction_rolls_back_all_slots_and_cabinet_when_audit_fails(read_db, monkeypatch, restore):
    await seed(read_db)
    if restore:
        await lifecycle.change_cabinet(read_db, ACTOR, CABINET)
    before = await hierarchy(read_db)
    original = lifecycle.validate_insert
    monkeypatch.setattr(lifecycle, "validate_insert", lambda name, doc: False if name == EVENTS else original(name, doc))
    with pytest.raises(HTTPException):
        await lifecycle.change_cabinet(read_db, ACTOR, CABINET, restore=restore)
    assert await hierarchy(read_db) == before


@pytest.mark.parametrize("target", ["ledger_entries", WAREHOUSES, ROOMS, "warehouse_location_counters"])
async def test_lifecycle_capability_cannot_write_finance_parents_or_counters(read_db, target):
    await seed(read_db)
    before = await hierarchy(read_db)
    async def forbidden(scoped):
        try:
            await scoped[target].update_one({"user_id": OWNER}, {"$set": {"amount": 999}})
        except HTTPException:
            pass  # Catching the refusal must still poison the transaction.
        return "not-authorized"
    with pytest.raises(HTTPException):
        await operational_atomic.operational_owner(read_db, OWNER, forbidden, profile="warehouse_lifecycle")
    assert await hierarchy(read_db) == before


async def test_archive_and_restore_retries_are_serialized(read_db):
    await seed(read_db)
    archived = await asyncio.gather(*(lifecycle.change_cabinet(read_db, ACTOR, CABINET) for _ in range(4)))
    assert sum(not row["already_archived"] for row in archived) == 1
    restored = await asyncio.gather(*(lifecycle.change_cabinet(read_db, ACTOR, CABINET, restore=True) for _ in range(4)))
    assert sum(not row["already_restored"] for row in restored) == 1
    assert await read_db[EVENTS].count_documents({}) == 2


async def test_archived_receipt_attempt_cannot_even_initialize_occupancy(read_db):
    await seed(read_db)
    await lifecycle.change_cabinet(read_db, ACTOR, CABINET)
    before = await hierarchy(read_db)
    with pytest.raises(InventoryLocationCapacityError):
        await place_inventory_receipt(read_db, merchant_id=OWNER, location_id="old-1", receipt_id="new-receipt",
            inventory_item={"quantity": 1, "receipt_id": "new-receipt"}, quantity=1,
            scanned_barcode="RUH-WH001-S01-C01-001", occurred_at="now")
    assert await hierarchy(read_db) == before


async def test_placement_cas_retains_repeated_quantities_and_rejects_archive_race(read_db, monkeypatch):
    await seed(read_db)
    payload = {"scanned_barcode": "RUH-WH001-S01-C01-001", "product_id": "product", "quantity": 1}
    async with client(read_db) as api:
        for _ in range(2):
            response = await api.post("/warehouse-locations-v2/locations/old-1/place", json=payload)
            assert response.status_code == 201, response.text
    assert (await read_db[LOCATIONS].find_one({"id": "old-1"}))["occupancy"]["total_quantity"] == 2

    # A deterministic interleave at the actual Motor write boundary: the old
    # placement already read an empty slot, then retirement commits first.
    await read_db[LOCATIONS].update_one({"id": "old-1"}, {"$set": {"state": "empty", "occupancy": None}})
    original = type(read_db[LOCATIONS]).update_one
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked(collection, query, update, *args, **kwargs):
        if collection.name == LOCATIONS and update.get("$set", {}).get("state") == "occupied" and not kwargs.get("session"):
            entered.set()
            await release.wait()
        return await original(collection, query, update, *args, **kwargs)
    monkeypatch.setattr(type(read_db[LOCATIONS]), "update_one", blocked)
    async with client(read_db) as api:
        task = asyncio.create_task(api.post("/warehouse-locations-v2/locations/old-1/place", json=payload))
        await asyncio.wait_for(entered.wait(), 5)
        await lifecycle.change_cabinet(read_db, ACTOR, CABINET)
        release.set()
        response = await asyncio.wait_for(task, 5)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "location_state_changed"
    location = await read_db[LOCATIONS].find_one({"id": "old-1"})
    assert location["state"] == "disabled" and location["occupancy"] is None
    assert await read_db[EVENTS].count_documents({"event_type": "product_placed_after_scan"}) == 2


async def test_archive_retries_fresh_state_when_placement_commits_first(read_db, monkeypatch):
    await seed(read_db)
    checked, resume = asyncio.Event(), asyncio.Event()
    original = lifecycle._active_references
    async def pause_after_read(db, owner, locations):
        await original(db, owner, locations)
        checked.set()
        await resume.wait()
    monkeypatch.setattr(lifecycle, "_active_references", pause_after_read)
    archive = asyncio.create_task(lifecycle.change_cabinet(read_db, ACTOR, CABINET))
    await asyncio.wait_for(checked.wait(), 5)
    try:
        async with client(read_db) as api:
            placed = await api.post("/warehouse-locations-v2/locations/old-1/place", json={
                "scanned_barcode": "RUH-WH001-S01-C01-001", "product_id": "product", "quantity": 1})
        assert placed.status_code == 201, placed.text
    finally:
        resume.set()
    with pytest.raises(HTTPException) as error:
        await asyncio.wait_for(archive, 8)
    assert error.value.detail["code"] == "cabinet_locations_not_empty"
    assert (await read_db[CABINETS].find_one({"id": CABINET}))["status"] == "active"
    assert await read_db[LOCATIONS].count_documents({"cabinet_archive": {"$exists": True}}) == 0
    assert (await read_db[LOCATIONS].find_one({"id": "old-1"}))["occupancy"]["total_quantity"] == 1
    assert await read_db[EVENTS].count_documents({"event_type": "cabinet_archived"}) == 0


async def test_manager_archives_owner_cabinet_without_foreign_reference_leak(read_db):
    await seed(read_db)
    await read_db.mezan_bundle_definitions_v2.insert_one({"user_id": "other-owner", "items": [{"location_id": "old-1"}]})
    before = await rows(read_db, "mezan_role_assignments_v2")
    manager = {"id": "warehouse-manager", "role": "warehouse", "created_by": OWNER}
    async with client(read_db, manager) as api:
        response = await api.post(f"/warehouse-locations-v2/cabinets/{CABINET}/archive")
    assert response.status_code == 200, response.text
    assert response.json()["cabinet"]["archive"]["archived_by"] == manager["id"]
    assert await rows(read_db, "mezan_role_assignments_v2") == before


async def test_bundle_current_pin_blocks_archive_but_frozen_history_remains_readable(read_db):
    import bundle_catalog_v2 as bundles
    from product_v2_routes import PRODUCTS
    await seed(read_db)
    for key in ("root", "child"):
        await read_db[PRODUCTS].insert_one({"user_id": OWNER, "salla_product_id": key,
            "mezan_product_id": "mpv2_" + key, "name": key, "cost_price_from_salla": 10, "variants": []})
    payload = {"expected_revision": 0, "product_type": "composite", "items": [
        {"kind": "product", "product_id": "child", "quantity": "1", "location_id": "old-1"}]}
    saved = await bundles.save_bundle(read_db, OWNER, "root", payload, OWNER)
    frozen = saved["expansion"]["frozen_definitions"]
    with pytest.raises(HTTPException) as blocked:
        await lifecycle.change_cabinet(read_db, ACTOR, CABINET)
    assert blocked.value.detail["code"] == "cabinet_bundle_reference"
    await bundles.save_bundle(read_db, OWNER, "root", {"expected_revision": 1, "product_type": "normal", "items": []}, OWNER)
    selector = {"user_id": OWNER, "product_id": saved["definition"]["product_id"]}
    definition = await read_db[bundles.BUNDLES].find_one(selector)
    assert definition and definition["revision"] == 2
    await lifecycle.change_cabinet(read_db, ACTOR, CABINET)
    with pytest.raises(HTTPException) as error:
        await bundles.save_bundle(read_db, OWNER, "root", {**payload, "expected_revision": 2}, OWNER)
    assert error.value.detail["code"] == "bundle_location_unavailable"
    assert await read_db[bundles.BUNDLES].find_one(selector) == definition
    historical = await bundles.expand_bundle(read_db, OWNER, "root", frozen_definitions=frozen, strict=False)
    assert historical["leaves"][0]["location_id"] == "old-1"
    assert historical["leaves"] == saved["expansion"]["leaves"]


async def test_corrupt_active_state_cannot_make_archive_marker_selectable_or_receivable(read_db):
    from operational_purchase_metadata import locations as operational_locations
    from fulfillment_v2_routes import _inventory_eligibility, _inventory_rows
    await seed(read_db)
    await lifecycle.change_cabinet(read_db, ACTOR, CABINET)
    await read_db[LOCATIONS].update_one({"id": "old-1"}, {"$set": {"state": "empty"}})
    assert await operational_locations(read_db, OWNER) == []
    row = await read_db[LOCATIONS].find_one({"id": "old-1"})
    assert _inventory_eligibility(row, {}, {}) == "location_disabled"
    assert _inventory_rows([row], {}) == []
    before = await rows(read_db, LOCATIONS)
    with pytest.raises(InventoryLocationCapacityError):
        await place_inventory_receipt(read_db, merchant_id=OWNER, location_id="old-1", receipt_id="receipt",
            inventory_item={"quantity": 1, "receipt_id": "receipt"}, quantity=1,
            scanned_barcode="RUH-WH001-S01-C01-001", occurred_at="now")
    assert await rows(read_db, LOCATIONS) == before
