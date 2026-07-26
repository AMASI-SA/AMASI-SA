import asyncio

import pytest
from fastapi import HTTPException

from warehouse_location_routes import CABINETS, EVENTS, LOCATIONS, WAREHOUSES
from warehouse_location_v2_routes import COUNTERS
from warehouse_reset_routes import _counter_scope_filter, _require_owner, reset_warehouse_data
from warehouse_room_routes import ROOMS


class DeleteResult:
    def __init__(self, deleted_count):
        self.deleted_count = deleted_count


class FakeCollection:
    def __init__(self, deleted_count):
        self.deleted_count = deleted_count
        self.queries = []

    async def delete_many(self, query):
        self.queries.append(query)
        return DeleteResult(self.deleted_count)


class FakeDb:
    def __init__(self):
        self.collections = {
            LOCATIONS: FakeCollection(24),
            CABINETS: FakeCollection(2),
            ROOMS: FakeCollection(3),
            WAREHOUSES: FakeCollection(1),
            EVENTS: FakeCollection(8),
            COUNTERS: FakeCollection(6),
        }

    def __getitem__(self, name):
        return self.collections[name]


def test_owner_guard_accepts_owner_and_rejects_employee():
    assert _require_owner({"id": "owner-1", "role": "owner"})["id"] == "owner-1"
    assert _require_owner({"id": "owner-2", "is_owner": True})["id"] == "owner-2"

    with pytest.raises(HTTPException) as exc:
        _require_owner({"id": "employee-1", "role": "warehouse"})
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "owner_required"


def test_counter_filter_is_scoped_to_current_merchant():
    query = _counter_scope_filter("owner.1")
    assert query["$or"][0] == {"user_id": "owner.1"}
    assert "owner\\.1" in query["$or"][1]["key"]["$regex"]


def test_reset_deletes_only_merchant_scoped_collections():
    db = FakeDb()
    deleted = asyncio.run(reset_warehouse_data(db, user_id="owner-1"))

    assert deleted == {
        "locations": 24,
        "cabinets": 2,
        "sections": 3,
        "branches": 1,
        "events": 8,
        "counters": 6,
    }
    for collection_name in [LOCATIONS, CABINETS, ROOMS, WAREHOUSES, EVENTS]:
        assert db[collection_name].queries == [{"user_id": "owner-1"}]
    assert db[COUNTERS].queries == [_counter_scope_filter("owner-1")]
