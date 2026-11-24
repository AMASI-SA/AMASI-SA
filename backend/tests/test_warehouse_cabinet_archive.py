"""Boundary cases for the empty-cabinet contract (no database required)."""
from copy import deepcopy

from fastapi import HTTPException
import pytest

from warehouse_cabinet_lifecycle import _empty, _valid_marker, validate_slot_set


@pytest.mark.parametrize("occupancy", [None, {"items": [], "total_quantity": 0}, {"items": [], "total_quantity": 0.0}])
def test_archive_accepts_only_proven_empty_occupancy(occupancy):
    assert _empty({"occupancy": occupancy})


@pytest.mark.parametrize("occupancy", [
    {}, [], "", 0, {"items": []}, {"total_quantity": 0},
    {"items": [{"quantity": 0}], "total_quantity": 0},
    {"items": [], "total_quantity": -1}, {"items": [], "total_quantity": "0"},
    {"items": [], "total_quantity": False}, {"items": [], "total_quantity": float("nan")},
    {"items": [], "total_quantity": 0, "order_number": "held"},
])
def test_archive_rejects_unknown_or_nonempty_occupancy(occupancy):
    assert not _empty({"occupancy": occupancy})


@pytest.mark.parametrize("quantity", [1, -1, "0", None, False, float("nan")])
def test_archive_rejects_legacy_quantity_even_without_occupancy(quantity):
    assert not _empty({"occupancy": None, "current_quantity": quantity})


def test_archive_preserves_a_recognized_legacy_zero():
    assert _empty({"occupancy": None, "current_quantity": 0})


@pytest.mark.parametrize("previous_state", [[], {}, None, 0, True, "occupied"])
def test_malformed_archive_marker_fails_closed(previous_state):
    assert not _valid_marker({"archive_id": "a" * 32, "cabinet_id": "cabinet",
                              "previous_state": previous_state, "snapshot_sha256": "b" * 64})


def test_conflicting_section_aliases_cannot_create_an_unrestorable_archive():
    cabinet = {"id": "cabinet", "user_id": "owner", "warehouse_id": "warehouse", "code": "C01",
               "length": 1, "width": 1, "total_locations": 1, "room_id": "room-a", "section_id": "room-b"}
    location = {"id": "slot", "code": "W-C01-001", "user_id": "owner", "warehouse_id": "warehouse",
                "cabinet_id": "cabinet", "cabinet_code": "C01", "row": 1, "column": 1, "number": 1,
                "room_id": "room-a", "section_id": "room-b"}
    before = deepcopy((cabinet, location))
    with pytest.raises(HTTPException) as error:
        validate_slot_set(cabinet, [location])
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "cabinet_integrity_unknown"
    assert (cabinet, location) == before


@pytest.mark.parametrize("alias", ["", " ", [], {}, 0, False])
def test_malformed_parent_alias_is_rejected_before_archiving(alias):
    cabinet = {"id": "cabinet", "user_id": "owner", "warehouse_id": "warehouse", "code": "C01",
               "length": 1, "width": 1, "total_locations": 1, "room_id": alias, "section_id": alias}
    location = {"id": "slot", "code": "W-C01-001", "user_id": "owner", "warehouse_id": "warehouse",
                "cabinet_id": "cabinet", "cabinet_code": "C01", "row": 1, "column": 1, "number": 1,
                "room_id": alias, "section_id": alias}
    with pytest.raises(HTTPException) as error:
        validate_slot_set(cabinet, [location])
    assert error.value.status_code == 409 and error.value.detail["code"] == "cabinet_integrity_unknown"
