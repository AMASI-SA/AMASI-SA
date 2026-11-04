"""Verify actual Mongo plan selection for the Ready POST's existing lookups."""
import os
from uuid import uuid4
from urllib.parse import urlsplit

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

import preparation_piece_operations as pieces
from fulfillment_v2_routes import ensure_fulfillment_indexes
from preparation_supplier_dispatch import ensure_supplier_dispatch_indexes


@pytest.mark.asyncio
async def test_ready_lookups_only_examine_matching_rows():
    uri = os.environ.get("MZ2_TEST_MONGO_URI")
    if not uri:
        pytest.skip("Explicit local MZ2_TEST_MONGO_URI required")
    connection = urlsplit(uri)
    assert (connection.scheme, connection.hostname) == ("mongodb", "127.0.0.1")
    assert connection.port and not connection.username and not connection.password
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    db = client["ready_lookup_test_" + uuid4().hex]
    try:
        await pieces.ensure_piece_operation_indexes(db)
        await ensure_supplier_dispatch_indexes(db)
        await ensure_fulfillment_indexes(db)
        await db[pieces.PIECES].insert_many([
            {"user_id": owner, "order_number": str(i), "order_item_id": "line", "batch_id": str(i),
             "unit_index": 1, "piece_id": f"piece-{i}", "id": f"alias-{i}", "status": "ready_for_assembly"}
            for owner in ("a", "b") for i in range(800)
        ])
        await db[pieces.WORKFLOWS].insert_many([
            {"user_id": owner, "order_number": str(i),
             "operational_items": [{"operational_item_id": f"annotation-{i}"}],
             "items": [{"direct_assembly_piece_ids": [f"direct-{i}"]}]}
            for owner in ("a", "b") for i in range(800)
        ])
        await pieces.ensure_assembly_piece_lookup_indexes(db)
        await pieces.ensure_assembly_piece_lookup_indexes(db)
        for value in ("piece-713", "alias-713", "direct-713"):
            await assert_plan(db, pieces.PIECES, {
                "user_id": "b", "$or": [{"piece_id": value}, {"id": value}],
            }, 0 if value.startswith("direct") else 1)
        for value in ("annotation-713", "direct-713", "missing"):
            await assert_plan(db, pieces.WORKFLOWS, {
                "user_id": "b", "$or": [{"operational_items.operational_item_id": value},
                                        {"items.direct_assembly_piece_ids": value}],
            }, 0 if value == "missing" else 1)
        await assert_plan(db, pieces.PIECES, {
            "user_id": "b", "order_number": "713", "status": {"$ne": pieces.PIECE_STATUS_CANCELLED},
            "$or": [{"experiment_archived_at": {"$exists": False}}, {"experiment_archived_at": None}],
        }, 1)
    finally:
        await client.drop_database(db.name)
        client.close()


async def assert_plan(db, collection, query, expected):
    result = await db.command("explain", {"find": collection, "filter": query}, verbosity="executionStats")
    stats = result["executionStats"]
    assert stats["nReturned"] == expected
    assert stats["totalDocsExamined"] <= expected
    assert stats["totalKeysExamined"] <= 2
