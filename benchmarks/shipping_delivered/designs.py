"""Benchmark-only source reservation treatments; never imported by runtime.

D fuses the real write and canonical read. The query is conditional on exact
tenant/order/provider presence; eligibility is checked from the returned document
inside the same transaction. Do not substitute repository list-status expressions:
assembly uses mapped DTO.status, whose precedence is different.
The counter exists ONLY in disposable benchmark fixtures, not production schema.
"""
from contextlib import asynccontextmanager
from unittest.mock import patch

from fastapi import HTTPException
from pymongo import ReturnDocument

import operational_atomic as atomic
import preparation_piece_operations as ops
from order_engine.repository import MongoOrderRepository, _V2_CANONICAL_ROOT_FIELDS
from order_engine.service import _map_row


@asynccontextmanager
async def design_adapter(design):
    if design not in {"baseline", "canonical", "conditional"}:
        raise ValueError(design)
    original = ops._current_assembly_order

    async def current(db, **kwargs):
        if atomic._ACTIVE.get() is None or design == "baseline":
            return await original(db, **kwargs)
        identity = {"user_id": str(kwargs["user_id"]),
                    "order_number": str(kwargs["order_number"]).strip()}
        change = {"$inc": {"benchmark_only_canonical_fence": 1}}
        if design == "canonical":
            result = await db.unified_orders.update_one(identity, change)
            if result.matched_count != 1:
                raise AssertionError("benchmark canonical document missing")
            return await original(db, **kwargs)
        document = await db.unified_orders.find_one_and_update(
            {**identity, "raw_by_source.salla_direct": {"$exists": True}},
            change,
            projection={"_id": 0, "order_number": 1, "order_date": 1,
                        "order_status": 1, "raw_by_source.salla_direct": 1,
                        **{field: 1 for field in _V2_CANONICAL_ROOT_FIELDS}},
            return_document=ReturnDocument.AFTER,
            upsert=False,
        )
        row = MongoOrderRepository._to_discovery_row(document)
        if row is None:
            raise HTTPException(status_code=409, detail={
                "code": "benchmark_canonical_order_unavailable"})
        order = _map_row(row.salla_raw, current_status=row.current_status)
        ops.require_assembly_order_open(order)
        return order

    async def writer(db, owner, callback):
        # Status writers already modify the same canonical BSON document.
        # No additional owner lock or revision participation is introduced.
        return await callback(db)

    with patch.object(ops, "_current_assembly_order", current):
        yield writer
