"""Cabinet numbering contracts against an owned local MongoDB replica only."""

import asyncio
import os

from fastapi import FastAPI
import httpx
import pytest
from pymongo.errors import DuplicateKeyError

from demand_read_mongo_fixture import read_db, read_mongo_uri as owned_mongo_uri
from warehouse_cabinet_numbering import (
    COUNTERS,
    ensure_cabinet_numbering_indexes,
    next_cabinet_number,
)
from warehouse_location_routes import CABINETS, LOCATIONS, WAREHOUSES
from warehouse_location_v2_routes import ensure_v2_indexes, make_warehouse_location_v2_router
from warehouse_room_routes import ROOMS, ensure_section_indexes, make_warehouse_room_router


OWNER = "numbering-test-owner"
WAREHOUSE = "numbering-test-warehouse"
NUMBERING = "cabinet-column-row"
NUMBER_KEYS = [("user_id", 1), ("warehouse_id", 1), ("cabinet_number", 1)]
pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="session")
def read_mongo_uri(tmp_path_factory, request):
    # This module never adopts a URI, even another test server's environment URI.
    assert not os.environ.get("DEMAND_READ_TEST_MONGO_URI"), (
        "Cabinet numbering tests require their owned DEMAND_READ_TEST_MONGOD"
    )
    if not os.environ.get("DEMAND_READ_TEST_MONGOD"):
        pytest.skip("DEMAND_READ_TEST_MONGOD required; no database URI fallback")
    yield from owned_mongo_uri.__wrapped__(tmp_path_factory, request)


def cabinet(identifier, number, *, section="section-a", status="active",
            owner=OWNER, warehouse=WAREHOUSE, numbering="legacy"):
    row = {
        "id": identifier,
        "user_id": owner,
        "warehouse_id": warehouse,
        "cabinet_number": number,
        "code": identifier,
        "location_numbering": numbering,
        "status": status,
    }
    if section is not None:
        row.update(room_id=section, section_id=section)
    return row


async def cabinets_snapshot(db, selector=None):
    return await db[CABINETS].find(selector or {}, {"_id": 0}).sort("id", 1).to_list(200)


@pytest.mark.parametrize("maximum_status", ["active", "disabled", "archived"])
async def test_seed_includes_all_sections_and_statuses_but_not_other_scopes(read_db, maximum_status):
    await read_db[CABINETS].insert_many([
        cabinet("direct", 4, section=None),
        cabinet("section-a-first", 7, section="section-a"),
        cabinet("section-b-maximum", 41, section="section-b", status=maximum_status),
        cabinet("other-warehouse", 8000, warehouse="other-warehouse"),
        cabinet("other-owner", 9000, owner="other-owner"),
    ])
    before = await cabinets_snapshot(read_db)
    await ensure_cabinet_numbering_indexes(read_db)

    assert await next_cabinet_number(read_db, user_id=OWNER, warehouse_id=WAREHOUSE) == 42
    assert await next_cabinet_number(read_db, user_id=OWNER, warehouse_id=WAREHOUSE) == 43
    assert await cabinets_snapshot(read_db) == before


@pytest.mark.parametrize(("counter_value", "first_number"), [
    (None, 42),
    (0, 42),
    (7, 42),
    (90, 91),
])
async def test_concurrent_allocations_respect_missing_stale_and_high_counters(
    read_db, counter_value, first_number,
):
    await read_db[CABINETS].insert_many([
        cabinet("existing-a", 7, section="section-a"),
        cabinet("existing-b", 41, section="section-b", status="archived"),
    ])
    before = await cabinets_snapshot(read_db)
    await ensure_cabinet_numbering_indexes(read_db)
    key = f"cabinet:{OWNER}:{WAREHOUSE}"
    if counter_value is not None:
        await read_db[COUNTERS].insert_one({"key": key, "value": counter_value})

    start = asyncio.Event()

    async def allocate(index):
        await start.wait()
        number = await next_cabinet_number(read_db, user_id=OWNER, warehouse_id=WAREHOUSE)
        await read_db[CABINETS].insert_one(cabinet(
            f"allocated-{index}", number, section=f"section-{index % 3}", numbering=NUMBERING,
        ))
        return number

    workers = [asyncio.create_task(allocate(index)) for index in range(24)]
    start.set()
    numbers = await asyncio.wait_for(asyncio.gather(*workers), timeout=15)

    assert sorted(numbers) == list(range(first_number, first_number + 24))
    assert await read_db[COUNTERS].count_documents({"key": key}) == 1
    counter = await read_db[COUNTERS].find_one({"key": key})
    assert counter["value"] == first_number + 23
    assert await read_db[CABINETS].count_documents({"location_numbering": NUMBERING}) == 24
    assert await cabinets_snapshot(read_db, {"location_numbering": "legacy"}) == before


async def test_partial_index_coexists_with_an_existing_global_unique_index(read_db):
    old_name = await read_db[CABINETS].create_index(NUMBER_KEYS, unique=True, sparse=True)
    await read_db[CABINETS].insert_one(cabinet("old-cabinet", 1))
    before = await cabinets_snapshot(read_db)

    await ensure_cabinet_numbering_indexes(read_db)
    await ensure_cabinet_numbering_indexes(read_db)

    indexes = await read_db[CABINETS].index_information()
    assert indexes[old_name]["unique"] is True
    assert indexes[old_name]["sparse"] is True
    assert "partialFilterExpression" not in indexes[old_name]
    partial = indexes["uq_warehouse_short_cabinet_number"]
    assert partial["key"] == NUMBER_KEYS
    assert partial["unique"] is True
    assert partial["partialFilterExpression"] == {"location_numbering": NUMBERING}
    assert await cabinets_snapshot(read_db) == before
    assert await next_cabinet_number(read_db, user_id=OWNER, warehouse_id=WAREHOUSE) == 2

    await read_db[CABINETS].insert_one(cabinet("new-cabinet", 2, numbering=NUMBERING))
    with pytest.raises(DuplicateKeyError):
        await read_db[CABINETS].insert_one(cabinet(
            "duplicate-new-cabinet", 2, section="section-b", numbering=NUMBERING,
        ))


async def test_legacy_duplicates_survive_without_a_global_unique_index(read_db):
    await read_db[CABINETS].insert_many([
        cabinet("legacy-a", 1, section="section-a"),
        cabinet("legacy-b", 1, section="section-b"),
    ])
    before = await cabinets_snapshot(read_db)

    await ensure_cabinet_numbering_indexes(read_db)
    await ensure_cabinet_numbering_indexes(read_db)

    assert await cabinets_snapshot(read_db) == before
    assert await next_cabinet_number(read_db, user_id=OWNER, warehouse_id=WAREHOUSE) == 2
    await read_db[CABINETS].insert_one(cabinet("new-two", 2, numbering=NUMBERING))
    # The new partial constraint does not retroactively constrain legacy rows.
    await read_db[CABINETS].insert_one(cabinet("legacy-two", 2, section="section-c"))
    with pytest.raises(DuplicateKeyError):
        await read_db[CABINETS].insert_one(cabinet(
            "duplicate-new-two", 2, section="section-b", numbering=NUMBERING,
        ))


async def test_route_indexes_and_direct_section_creation_preserve_legacy_duplicates(read_db):
    await read_db[WAREHOUSES].insert_one({
        "id": WAREHOUSE, "user_id": OWNER, "code": "TEST-WH", "name": "Test warehouse",
        "status": "active", "location_numbering": "legacy",
    })
    await read_db[ROOMS].insert_many([
        {"id": f"section-{letter}", "user_id": OWNER, "warehouse_id": WAREHOUSE,
         "code": f"S{number:02d}", "name": f"Section {letter}", "status": "active",
         "room_number": number, "section_number": number, "capabilities": ["cabinets"]}
        for number, letter in [(1, "a"), (2, "b")]
    ])
    await read_db[CABINETS].insert_many([
        cabinet("legacy-a", 1, section="section-a"),
        cabinet("legacy-b", 1, section="section-b"),
    ])
    before = await cabinets_snapshot(read_db)
    await ensure_section_indexes(read_db)
    await ensure_v2_indexes(read_db)

    async def current_owner():
        return {"id": OWNER, "role": "owner"}

    app = FastAPI()
    app.include_router(make_warehouse_location_v2_router(read_db, current_owner))
    app.include_router(make_warehouse_room_router(read_db, current_owner))
    payload = {"length": 1, "width": 3, "purpose": "permanent_storage",
               "location_numbering": NUMBERING}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://owned-test") as client:
        direct = await client.post("/warehouse-locations-v2/cabinets", json={
            **payload, "warehouse_id": WAREHOUSE,
        })
        section = await client.post("/warehouse-locations-v2/sections/section-a/cabinets", json=payload)

    assert direct.status_code == 201, direct.text
    assert section.status_code == 201, section.text
    direct_cabinet = direct.json()["cabinet"]
    section_cabinet = section.json()["cabinet"]
    assert [direct_cabinet["cabinet_number"], section_cabinet["cabinet_number"]] == [2, 3]
    assert section_cabinet["section_id"] == "section-a"
    for created, expected in [(direct_cabinet, ["211", "221", "231"]),
                              (section_cabinet, ["311", "321", "331"])]:
        rows = await read_db[LOCATIONS].find({"cabinet_id": created["id"]}).sort("number", 1).to_list(4)
        assert len(rows) == 3
        assert [row["display_code"] for row in rows] == expected
    assert await cabinets_snapshot(read_db, {"location_numbering": "legacy"}) == before
    warehouse = await read_db[WAREHOUSES].find_one({"id": WAREHOUSE})
    assert warehouse["location_numbering"] == "legacy"
