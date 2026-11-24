"""Reversible, owner-serialized retirement of a complete empty cabinet.

Warehouse/section identities, numbering, stock history and assignments are never
changed. Only cabinet lifecycle metadata and the states of its proven empty
locations may be written through the restricted transaction capability.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import re
import uuid

from bson.json_util import CANONICAL_JSON_OPTIONS, dumps
from fastapi import Depends, HTTPException

from operational_atomic import operational_owner
from warehouse_location_routes import (
    CABINETS, EVENTS, LOCATIONS, WAREHOUSES, _merchant_user_id, _require_manager,
)


ROOMS = "warehouse_rooms"
MARKER = "cabinet_archive"
MAX_LOCATIONS = 5000
_HEX = re.compile(r"^[a-f0-9]{64}$")
_TOKEN = re.compile(r"^[a-f0-9]{32}$")
_MESSAGES = {
    "cabinet_not_found": "لم يعد الدولاب موجودًا في هذا المتجر.",
    "cabinet_integrity_unknown": "تعذر التأكد من اكتمال الدولاب وخاناته؛ لم يتغير شيء.",
    "cabinet_parent_unavailable": "يجب أن يكون فرع الدولاب وقسمه فعالين قبل استعادته.",
    "cabinet_locations_not_empty": "لا يمكن أرشفة الدولاب: توجد خانات مشغولة أو محجوزة أو بيانات إشغال غير محسومة.",
    "cabinet_active_reservations": "لا يمكن أرشفة الدولاب: بعض خاناته مرتبطة بحجوزات مخزون حالية.",
    "cabinet_reserved_components": "لا يمكن أرشفة الدولاب: بعض خاناته مرتبطة بمكونات أو قطع محجوزة للتجهيز.",
    "cabinet_bundle_reference": "لا يمكن أرشفة الدولاب: إعداد منتج أو حزمة ما زال يحدد إحدى خاناته. عدّل هذا الإعداد أولًا.",
    "cabinet_archive_changed": "تغير الدولاب أو إحدى خاناته؛ حدّث الصفحة وأعد المحاولة. لم تُطبق العملية جزئيًا.",
    "cabinet_not_archived": "هذا الدولاب ليس مؤرشفًا بهذه الآلية ولا يحتاج إلى استعادة.",
    "cabinet_archive_list_incomplete": "تعذر عرض قائمة الدواليب المؤرشفة كاملة.",
}


def fail(code, *, count=None, status=409):
    detail = {"code": code, "message": _MESSAGES[code]}
    if count is not None:
        detail["blocker"] = {"type": code, "count": count}
    raise HTTPException(status, detail=detail)


def _digest(value):
    return hashlib.sha256(dumps(value, json_options=CANONICAL_JSON_OPTIONS,
                               sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _snapshot(row, *, cabinet=False):
    ignored = {"_id", "updated_at", "updated_by", MARKER}
    ignored |= ({"status", "archive", "restored_at", "restored_by", "restored_archive_id"}
                if cabinet else {"state"})
    return _digest({key: value for key, value in row.items() if key not in ignored})


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _empty(location):
    """Unknown metadata, zero-quantity historical items and holds fail closed."""
    if "current_quantity" in location and (type(location["current_quantity"]) not in {int, float}
                                            or location["current_quantity"] != 0):
        return False
    occupancy = location.get("occupancy")
    return (occupancy is None or (
        isinstance(occupancy, dict)
        and set(occupancy) == {"items", "total_quantity"}
        and occupancy["items"] == []
        and type(occupancy["total_quantity"]) in {int, float}
        and occupancy["total_quantity"] == 0
    ))


def validate_slot_set(cabinet, locations):
    if any(value is not None and not _text(value)
           for value in (cabinet.get("section_id"), cabinet.get("room_id"))):
        fail("cabinet_integrity_unknown")
    if (cabinet.get("section_id") and cabinet.get("room_id")
            and cabinet["section_id"] != cabinet["room_id"]):
        fail("cabinet_integrity_unknown")
    dimensions = (cabinet.get("length"), cabinet.get("width"), cabinet.get("total_locations"))
    if (any(type(value) is not int or value < 1 for value in dimensions)
            or dimensions[0] * dimensions[1] != dimensions[2]
            or dimensions[0] > 100 or dimensions[1] > 100
            or dimensions[2] > MAX_LOCATIONS or len(locations) != dimensions[2]
            or not all(_text(cabinet.get(key)) for key in ("id", "user_id", "warehouse_id", "code"))):
        fail("cabinet_integrity_unknown")
    seen_ids, seen_codes, seen_positions = set(), set(), set()
    for location in locations:
        row, column, number = (location.get(key) for key in ("row", "column", "number"))
        if (any(type(value) is not int for value in (row, column, number))
                or not 1 <= row <= dimensions[0] or not 1 <= column <= dimensions[1]
                or number != (row - 1) * dimensions[1] + column
                or not all(_text(location.get(key)) for key in ("id", "code"))
                or location.get("user_id") != cabinet["user_id"]
                or location.get("cabinet_id") != cabinet["id"]
                or location.get("warehouse_id") != cabinet["warehouse_id"]
                or location.get("cabinet_code") != cabinet["code"]
                or any(location.get(key) != cabinet.get(key) for key in ("room_id", "section_id"))
                or ("grid_x" in location and location["grid_x"] != column)
                or ("grid_y" in location and location["grid_y"] != row)
                or location["id"] in seen_ids or location["code"] in seen_codes
                or (row, column) in seen_positions):
            fail("cabinet_integrity_unknown")
        seen_ids.add(location["id"])
        seen_codes.add(location["code"])
        seen_positions.add((row, column))


def _condition(row, *, cabinet=False):
    query = {"id": row["id"], "user_id": row["user_id"]}
    if "_id" in row:
        query["_id"] = row["_id"]
    if cabinet:
        query.update(status=row.get("status"), archive=row["archive"] if "archive" in row else {"$exists": False})
    else:
        query.update(state=row.get("state"), occupancy=deepcopy(row.get("occupancy")))
        query[MARKER] = deepcopy(row[MARKER]) if MARKER in row else {"$exists": False}
    return query


def _location_manifest(locations):
    return _digest(sorted(({
        "id": row["id"], "previous_state": row[MARKER]["previous_state"],
        "snapshot_sha256": row[MARKER]["snapshot_sha256"],
    } for row in locations), key=lambda row: row["id"]))


def _valid_archive(archive):
    return (isinstance(archive, dict)
            and set(archive) == {"version", "id", "archived_at", "archived_by", "location_count",
                                 "locations_sha256", "cabinet_sha256"}
            and archive["version"] == 1 and type(archive["version"]) is int
            and isinstance(archive["id"], str) and bool(_TOKEN.fullmatch(archive["id"]))
            and _text(archive["archived_at"]) and _text(archive["archived_by"])
            and type(archive["location_count"]) is int and 1 <= archive["location_count"] <= MAX_LOCATIONS
            and all(isinstance(archive[key], str) and _HEX.fullmatch(archive[key])
                    for key in ("locations_sha256", "cabinet_sha256")))


def _valid_marker(marker):
    return (isinstance(marker, dict)
            and set(marker) == {"archive_id", "cabinet_id", "previous_state", "snapshot_sha256"}
            and isinstance(marker["archive_id"], str) and bool(_TOKEN.fullmatch(marker["archive_id"]))
            and _text(marker["cabinet_id"]) and marker["previous_state"] in ("empty", "disabled")
            and isinstance(marker["snapshot_sha256"], str) and bool(_HEX.fullmatch(marker["snapshot_sha256"])))


def verify_archived(cabinet, locations):
    archive = cabinet.get("archive")
    if (cabinet.get("status") != "archived" or not _valid_archive(archive)
            or archive["cabinet_sha256"] != _snapshot(cabinet, cabinet=True)
            or archive["location_count"] != len(locations)):
        fail("cabinet_archive_changed")
    for location in locations:
        marker = location.get(MARKER)
        if (location.get("state") != "disabled" or not _empty(location) or not _valid_marker(marker)
                or marker["archive_id"] != archive["id"] or marker["cabinet_id"] != cabinet["id"]
                or marker["snapshot_sha256"] != _snapshot(location)):
            fail("cabinet_archive_changed")
    if _location_manifest(locations) != archive["locations_sha256"]:
        fail("cabinet_archive_changed")
    return archive


async def _active_references(db, owner, locations):
    ids = [row["id"] for row in locations]
    # Unknown states on a matching reference are not proof of release. Historical
    # released/consumed allocations and bundle history remain untouched.
    checks = (
        ("mezan_inventory_reservations_v2", "allocations.location_id", "status",
         {"$nin": ["released", "consumed"]}, "cabinet_active_reservations"),
        ("mezan_component_consumption_units_v1", "allocations.location_id", "state",
         {"$nin": ["released", "consumed"]}, "cabinet_reserved_components"),
        ("mezan_bundle_definitions_v2", "items.location_id", None, None, "cabinet_bundle_reference"),
    )
    for collection, path, state, allowed, code in checks:
        query = {"user_id": owner, path: {"$in": ids}}
        if state:
            query[state] = allowed
        count = await db[collection].count_documents(query)
        if count:
            fail(code, count=count)


async def _active_parents(db, owner, cabinet):
    warehouse = await db[WAREHOUSES].find_one({"user_id": owner, "id": cabinet["warehouse_id"], "status": "active"})
    if not warehouse:
        fail("cabinet_parent_unavailable")
    section = cabinet.get("section_id") or cabinet.get("room_id")
    if cabinet.get("section_id") and cabinet.get("room_id") and cabinet["section_id"] != cabinet["room_id"]:
        fail("cabinet_integrity_unknown")
    if section is not None and not _text(section):
        fail("cabinet_integrity_unknown")
    if section and not await db[ROOMS].find_one({
            "user_id": owner, "id": section, "warehouse_id": cabinet["warehouse_id"], "status": "active"}):
        fail("cabinet_parent_unavailable")


def _public(cabinet):
    return {key: value for key, value in cabinet.items() if key != "_id"}


async def change_cabinet(db, actor, cabinet_id, *, restore=False):
    actor = _require_manager(actor)
    owner = _merchant_user_id(actor)
    actor_id = actor.get("id")
    if not _text(owner) or not _text(actor_id):
        fail("cabinet_integrity_unknown")

    async def change(scoped):
        cabinets = await scoped[CABINETS].find({"user_id": owner, "id": cabinet_id}).to_list(2)
        if not cabinets:
            fail("cabinet_not_found", status=404)
        if len(cabinets) != 1:
            fail("cabinet_integrity_unknown")
        cabinet = cabinets[0]
        locations = await scoped[LOCATIONS].find({"user_id": owner, "cabinet_id": cabinet_id}).to_list(MAX_LOCATIONS + 1)
        validate_slot_set(cabinet, locations)
        if restore and cabinet.get("status") == "active":
            if (_TOKEN.fullmatch(str(cabinet.get("restored_archive_id", "")))
                    and "archive" not in cabinet and all(MARKER not in row for row in locations)):
                return {"ok": True, "cabinet": _public(cabinet), "already_restored": True}
            fail("cabinet_not_archived")
        if cabinet.get("status") == "archived":
            archive = verify_archived(cabinet, locations)
            if not restore:
                return {"ok": True, "cabinet": _public(cabinet), "already_archived": True}
        elif restore or cabinet.get("status") != "active" or "archive" in cabinet:
            fail("cabinet_integrity_unknown")
        else:
            blocked = [row for row in locations if row.get("state") not in ("empty", "disabled")
                       or not _empty(row) or MARKER in row]
            if blocked:
                fail("cabinet_locations_not_empty", count=len(blocked))
            await _active_references(scoped, owner, locations)
            archive = {"version": 1, "id": uuid.uuid4().hex, "archived_at": datetime.now(timezone.utc).isoformat(),
                       "archived_by": actor_id, "location_count": len(locations),
                       "cabinet_sha256": _snapshot(cabinet, cabinet=True)}
        if restore:
            await _active_parents(scoped, owner, cabinet)
        stamp = datetime.now(timezone.utc).isoformat()
        changed_locations = []
        for location in locations:
            if restore:
                values = {"state": location[MARKER]["previous_state"], "updated_at": stamp, "updated_by": actor_id}
                update = {"$set": values, "$unset": {MARKER: ""}}
            else:
                marker = {"archive_id": archive["id"], "cabinet_id": cabinet_id,
                          "previous_state": location["state"], "snapshot_sha256": _snapshot(location)}
                values = {"state": "disabled", MARKER: marker, "updated_at": stamp, "updated_by": actor_id}
                update = {"$set": values}
                changed_locations.append({**location, **values})
            result = await scoped[LOCATIONS].update_one(_condition(location), update)
            if result.matched_count != 1:
                fail("cabinet_archive_changed")
        if restore:
            values = {"status": "active", "restored_at": stamp, "restored_by": actor_id,
                      "restored_archive_id": archive["id"], "updated_at": stamp, "updated_by": actor_id}
            update = {"$set": values, "$unset": {"archive": ""}}
        else:
            archive["locations_sha256"] = _location_manifest(changed_locations)
            values = {"status": "archived", "archive": archive, "updated_at": stamp, "updated_by": actor_id}
            update = {"$set": values}
        result = await scoped[CABINETS].update_one(_condition(cabinet, cabinet=True), update)
        if result.matched_count != 1:
            fail("cabinet_archive_changed")
        await scoped[EVENTS].insert_one({
            "id": uuid.uuid4().hex, "user_id": owner,
            "event_type": "cabinet_restored" if restore else "cabinet_archived",
            "cabinet_id": cabinet_id, "warehouse_id": cabinet["warehouse_id"],
            "archive_id": archive["id"], "location_count": len(locations),
            "actor_id": actor_id, "occurred_at": stamp,
        })
        updated = {**cabinet, **values}
        if restore:
            updated.pop("archive", None)
        return {"ok": True, "cabinet": _public(updated),
                "already_restored" if restore else "already_archived": False}

    return await operational_owner(db, owner, change, profile="warehouse_lifecycle")


def validate_insert(name, doc):
    return (name == EVENTS and isinstance(doc, dict)
            and set(doc) == {"id", "user_id", "event_type", "cabinet_id", "warehouse_id", "archive_id",
                             "location_count", "actor_id", "occurred_at"}
            and doc["event_type"] in ("cabinet_archived", "cabinet_restored")
            and all(_text(doc[key]) for key in ("id", "user_id", "cabinet_id", "warehouse_id", "actor_id", "occurred_at"))
            and isinstance(doc["archive_id"], str) and bool(_TOKEN.fullmatch(doc["archive_id"]))
            and type(doc["location_count"]) is int and 1 <= doc["location_count"] <= MAX_LOCATIONS)


async def validate_update(db, owner, name, query, update):
    """Capability check: no inserts/deletes, quantities, identity or parent writes."""
    if (name not in {CABINETS, LOCATIONS} or not isinstance(query, dict)
            or not isinstance(update, dict) or not set(update) <= {"$set", "$unset"}
            or query.get("user_id") != owner or not _text(query.get("id"))):
        return False
    before = await db[name].find_one({"user_id": owner, "id": query["id"]})
    if not before or query != _condition(before, cabinet=name == CABINETS):
        return False
    values = update.get("$set")
    if not isinstance(values, dict) or not _text(values.get("updated_at")) or not _text(values.get("updated_by")):
        return False
    if name == LOCATIONS:
        if not _empty(before):
            return False
        if "$unset" in update:
            marker = before.get(MARKER)
            return (before.get("state") == "disabled" and _valid_marker(marker)
                    and marker["snapshot_sha256"] == _snapshot(before)
                    and set(values) == {"state", "updated_at", "updated_by"}
                    and values["state"] == marker["previous_state"] and update["$unset"] == {MARKER: ""})
        marker = values.get(MARKER)
        return (set(update) == {"$set"} and set(values) == {"state", MARKER, "updated_at", "updated_by"}
                and before.get("state") in ("empty", "disabled") and MARKER not in before
                and values["state"] == "disabled" and _valid_marker(marker)
                and marker["cabinet_id"] == before.get("cabinet_id")
                and marker["previous_state"] == before["state"] and marker["snapshot_sha256"] == _snapshot(before))
    if "$unset" in update:
        archive = before.get("archive")
        return (before.get("status") == "archived" and _valid_archive(archive)
                and set(values) == {"status", "restored_at", "restored_by", "restored_archive_id", "updated_at", "updated_by"}
                and values["status"] == "active" and values["restored_archive_id"] == archive["id"]
                and values["restored_at"] == values["updated_at"] and values["restored_by"] == values["updated_by"]
                and update["$unset"] == {"archive": ""})
    archive = values.get("archive")
    return (set(update) == {"$set"} and set(values) == {"status", "archive", "updated_at", "updated_by"}
            and before.get("status") == "active" and "archive" not in before and values["status"] == "archived"
            and _valid_archive(archive) and archive["cabinet_sha256"] == _snapshot(before, cabinet=True)
            and archive["archived_by"] == values["updated_by"])


def attach_cabinet_lifecycle_routes(router, db, current_user):
    @router.post("/cabinets/{cabinet_id}/archive")
    async def archive_cabinet(cabinet_id: str, user=Depends(current_user)):
        return await change_cabinet(db, user, cabinet_id)

    @router.post("/cabinets/{cabinet_id}/restore")
    async def restore_cabinet(cabinet_id: str, user=Depends(current_user)):
        return await change_cabinet(db, user, cabinet_id, restore=True)

    @router.get("/warehouses/{warehouse_id}/archived-cabinets")
    async def archived_cabinets(warehouse_id: str, user=Depends(current_user)):
        owner = _merchant_user_id(_require_manager(user))
        if not await db[WAREHOUSES].find_one({"user_id": owner, "id": warehouse_id}):
            raise HTTPException(404, detail={"code": "warehouse_not_found"})
        rows = await db[CABINETS].find({"user_id": owner, "warehouse_id": warehouse_id, "status": "archived"}, {"_id": 0}).sort(
            [("cabinet_number", 1), ("id", 1)]).to_list(MAX_LOCATIONS + 1)
        if len(rows) > MAX_LOCATIONS:
            fail("cabinet_archive_list_incomplete")
        return {"items": rows, "total": len(rows)}
