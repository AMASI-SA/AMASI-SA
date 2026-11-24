"""Allocate cabinet numbers once per owner/warehouse, across all sections.

Older section counters may overlap. Reserve above existing cabinet numbers and
the shared counter without rewriting old cabinets, labels or scanner codes.
"""
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from warehouse_location_routes import CABINETS


COUNTERS = "warehouse_location_counters"
MAX_NUMBERED_CABINETS = 20_000


def _integer(value: Any, *, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _invalid_number() -> HTTPException:
    return HTTPException(409, detail={
        "code": "cabinet_numbering_invalid",
        "message": "تعذّر تحديد رقم الدولاب التالي. راجع ترقيم الدواليب الحالي أولًا.",
    })


async def ensure_cabinet_numbering_indexes(db: Any) -> None:
    await db[COUNTERS].create_index("key", unique=True)
    # Existing legacy numbers can repeat between sections. Do not impose a new
    # global constraint on them or drop an index installed by an older version.
    await db[CABINETS].create_index(
        [("user_id", 1), ("warehouse_id", 1), ("cabinet_number", 1)],
        unique=True,
        partialFilterExpression={"location_numbering": "cabinet-column-row"},
        name="uq_warehouse_short_cabinet_number",
    )


async def next_cabinet_number(db: Any, *, user_id: str, warehouse_id: str) -> int:
    rows = await db[CABINETS].find(
        {"user_id": user_id, "warehouse_id": warehouse_id,
         "cabinet_number": {"$exists": True}},
        {"_id": 0, "cabinet_number": 1},
    ).to_list(length=MAX_NUMBERED_CABINETS + 1)
    if len(rows) > MAX_NUMBERED_CABINETS:
        raise HTTPException(409, detail={"code": "cabinet_numbering_review_required"})
    highest = 0
    for row in rows:
        number = row["cabinet_number"]
        if not _integer(number, minimum=1):
            raise _invalid_number()
        highest = max(highest, number)

    key = f"cabinet:{user_id}:{warehouse_id}"
    counter = await db[COUNTERS].find_one({"key": key}, {"_id": 0, "value": 1})
    if counter is not None and not _integer(counter.get("value"), minimum=0):
        raise _invalid_number()
    floor_update = {
        "$max": {"value": highest},
        "$setOnInsert": {"created_at": datetime.now(timezone.utc).isoformat()},
    }
    try:
        await db[COUNTERS].update_one({"key": key}, floor_update, upsert=True)
    except DuplicateKeyError:
        # A simultaneous first allocation may have created the unique key.
        await db[COUNTERS].update_one({"key": key}, {"$max": {"value": highest}})
    counter = await db[COUNTERS].find_one_and_update(
        {"key": key}, {"$inc": {"value": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if counter is None or not _integer(counter.get("value"), minimum=1):
        raise _invalid_number()
    return counter["value"]
