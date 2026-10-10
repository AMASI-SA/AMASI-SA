"""Synthetic, loopback-only query benchmark. Never accepts a Production URI.

Run with backend on PYTHONPATH; writes adjacent JSON and drops only its UUID DB.
Candidate indexes exist only in this disposable database. No query hints.
"""
import asyncio
import json
import statistics
import time
from pathlib import Path
from uuid import uuid4

from bson.codec_options import CodecOptions
from motor.motor_asyncio import AsyncIOMotorClient
from fulfillment_v2_routes import _inventory_evidence_queries, _load_inventory_evidence
from product_inventory_receipt_routes import ensure_inventory_receipt_indexes


def locations(count):
    return [{"id": f"loc-{i}", "occupancy": {"items": [
        {"receipt_id": f"owner-{i}-0"}, {"receipt_id": f"owner-{i}-1"},
    ]}} for i in range(count)]


def old_queries(rows):
    for offset in range(0, len(rows), 100):
        yield offset, {"user_id": "owner", "location_id": {"$in": [r["id"] for r in rows[offset:offset + 100]]}}


def indexes(plan):
    found = set()
    def visit(value):
        if isinstance(value, dict):
            if "indexName" in value:
                found.add(value["indexName"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(plan)
    return sorted(found)


async def seed(collection, per_location):
    batch = []
    for owner in ["owner", "other-1", "other-2", "other-3", "other-4"]:
        for loc in range(1000):
            for slot in range(per_location):
                identity = f"{owner}-{loc}-{slot}"
                row = dict(id=identity, idempotency_key=identity, user_id=owner,
                    location_id=f"loc-{loc}", warehouse_id="warehouse",
                    source_type="purchase_invoice", source_id=f"invoice-{loc}", source_line_id=identity,
                    purchase_invoice_id=f"invoice-{loc}", purchase_invoice_line_id=identity,
                    status="posted", posted_at="2026-01-01T00:00:00Z", quantity=10,
                    product_id="synthetic-product", configuration_key="synthetic-configuration",
                    specifications={"color": "gold"}, evidence="synthetic-" * 20)
                if slot == 0:
                    row["status"] = ("posted", "pending", "rejected")[loc % 3]
                if slot == 2:
                    row.update(source_type="opening_inventory", adopted_receipt_ids=[f"{owner}-{loc}-1"])
                batch.append(row)
                if len(batch) == 5000:
                    await collection.insert_many(batch)
                    batch = []
    if batch:
        await collection.insert_many(batch)


async def measure(db, mode, count, per_location):
    c = db.mezan_inventory_receipts_v2
    rows = locations(count)
    optimized = mode.startswith("optimized")
    queries = list(_inventory_evidence_queries("owner", rows) if optimized else old_queries(rows))
    totals = {"docs_examined": 0, "keys_examined": 0, "returned": 0, "explain_ms": 0}
    plans, used = [], set()
    for _, query in queries:
        explain = await db.command("explain", {"find": c.name, "filter": query, "limit": 20001},
            verbosity="executionStats", codec_options=CodecOptions(unicode_decode_error_handler="replace"))
        stats = explain["executionStats"]
        for target, source in [("docs_examined", "totalDocsExamined"), ("keys_examined", "totalKeysExamined"),
                               ("returned", "nReturned"), ("explain_ms", "executionTimeMillis")]:
            totals[target] += stats[source]
        plan = explain["queryPlanner"]["winningPlan"]
        used.update(indexes(plan))
        if not plans:
            plans.append(plan)
    expected = count * (3 if optimized else per_location)
    assert totals["returned"] == expected
    elapsed = []
    for _ in range(3):
        began = time.perf_counter()
        if optimized:
            result = await _load_inventory_evidence(db, "owner", rows)
            unique = {str(r["_id"]) for group in result.values() for r in group}
            assert len(unique) == expected
            for i in range(count):
                current = result[(f"loc-{i}", f"owner-{i}-0")]
                assert len(current) == 1 and current[0]["status"] == ("posted", "pending", "rejected")[i % 3]
                adopted = result[(f"loc-{i}", f"owner-{i}-1")]
                assert {r["source_type"] for r in adopted} == {"purchase_invoice", "opening_inventory"}
        else:
            actual = 0
            for _, query in queries:
                actual += len(await c.find(query).to_list(length=20001))
            assert actual == expected
        elapsed.append(round((time.perf_counter() - began) * 1000, 2))
    return dict(mode=mode, locations=count, queries=len(queries), expected_receipts=expected,
                **totals, read_ms=elapsed, median_read_ms=statistics.median(elapsed),
                indexes_used=sorted(used), winning_plan_first_batch=plans[0])


async def main():
    client = AsyncIOMotorClient("mongodb://127.0.0.1:27462/?replicaSet=operationalphysical", serverSelectionTimeoutMS=5000)
    hello = await client.admin.command("hello")
    assert hello.get("setName") == "operationalphysical" and hello["isWritablePrimary"]
    out = {"synthetic_only": True, "mongo_version": (await client.admin.command("buildInfo"))["version"],
           "no_hints": True, "owner_locations": 1000, "active_references_per_location": 2,
           "expected_proofs_per_location": 3, "samples_per_measurement": 3, "datasets": []}
    try:
        for per_location in (20, 40):
            db = client["inventory_projection_benchmark_test_" + uuid4().hex[:20]]
            try:
                c = db.mezan_inventory_receipts_v2
                await seed(c, per_location)
                await ensure_inventory_receipt_indexes(db)
                entry = {"documents": await c.count_documents({}), "owner_documents": per_location * 1000,
                         "history_receipts_per_location": per_location, "existing_indexes": await c.index_information(), "results": []}
                for candidate in (False, True):
                    if candidate:
                        await c.create_index([("user_id", 1), ("location_id", 1), ("id", 1)], name="candidate_owner_location_id")
                        await c.create_index([("user_id", 1), ("location_id", 1), ("source_type", 1), ("adopted_receipt_ids", 1)], name="candidate_owner_location_adoption")
                        entry["candidate_indexes"] = await c.index_information()
                    for optimized in (False, True):
                        mode = ("optimized" if optimized else "location_history") + ("_candidate_indexes" if candidate else "_existing_indexes")
                        for count in (10, 100, 1000):
                            result = await measure(db, mode, count, per_location)
                            entry["results"].append(result)
                            print(json.dumps({k: v for k, v in result.items() if k != "winning_plan_first_batch"}), flush=True)
                out["datasets"].append(entry)
            finally:
                assert db.name.startswith("inventory_projection_benchmark_test_")
                await client.drop_database(db.name)
    finally:
        client.close()
        Path(__file__).with_suffix(".json").write_text(json.dumps(out, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
