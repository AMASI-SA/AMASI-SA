"""Synthetic read-payload experiment; no live database or RAM/latency claim.

Run this file in a fresh Python process for each --repo. The unmodified public
cost builders receive an isolated mongomock_motor database and a read-only
recording wrapper. Counts are BSON re-encodings of documents actually returned
by catalog cursors, excluding MongoDB protocol overhead; this is not RSS.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--products", type=int, default=1000)
    return parser.parse_args()


ARGS = parse_args()
SOURCE_PATH = ARGS.repo / "backend" / "dashboard_v2_routes.py"
SOURCE_BYTES_AT_IMPORT = SOURCE_PATH.read_bytes()
sys.path.insert(0, str(ARGS.repo / "backend"))
from bson import BSON, ObjectId
from mongomock_motor import AsyncMongoMockClient
import dashboard_v2_routes as dashboard
from product_v2_routes import PRODUCTS


OWNER = "synthetic-payload-owner"
WRITE_METHODS = {
    "insert_one", "insert_many", "update_one", "update_many", "replace_one",
    "delete_one", "delete_many", "bulk_write", "find_one_and_update",
    "find_one_and_delete", "find_one_and_replace", "drop", "create_index",
    "create_indexes", "drop_index", "drop_indexes",
}


class RecordedCursor:
    def __init__(self, cursor, record):
        self.cursor, self.record = cursor, record

    def observe(self, row):
        self.record["rows_returned"] += 1
        self.record["bson_bytes_returned"] += len(BSON.encode(row))
        if "description" in (row.get("raw_salla_details") or {}):
            self.record["full_payload_rows"] += 1

    async def to_list(self, length=None):
        rows = await self.cursor.to_list(length=length)
        for row in rows:
            self.observe(row)
        return rows

    def __aiter__(self):
        self._iterator = self.cursor.__aiter__()
        return self

    async def __anext__(self):
        row = await self._iterator.__anext__()
        self.observe(row)
        return row

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class ReadOnlyCollection:
    def __init__(self, collection, reads):
        self.collection, self.reads = collection, reads

    def find(self, query, projection=None, *args, **kwargs):
        record = {
            "collection": self.collection.name, "operation": "find",
            "rows_returned": 0, "bson_bytes_returned": 0,
            "full_payload_rows": 0,
        }
        self.reads.append(record)
        return RecordedCursor(
            self.collection.find(query, projection, *args, **kwargs), record,
        )

    async def find_one(self, *args, **kwargs):
        self.reads.append({"collection": self.collection.name, "operation": "find_one"})
        return await self.collection.find_one(*args, **kwargs)

    def __getattr__(self, name):
        if name in WRITE_METHODS:
            raise AssertionError("Builder attempted write: " + name)
        return getattr(self.collection, name)


class ReadOnlyDB:
    def __init__(self, database):
        self.database, self.reads = database, []

    def __getitem__(self, name):
        return ReadOnlyCollection(self.database[name], self.reads)

    def __getattr__(self, name):
        return self[name]


def fixture_products(count):
    return [{
        "_id": ObjectId(f"{index + 1:024x}"),
        "user_id": OWNER,
        "salla_product_id": f"p-{index}",
        "name": f"Synthetic product {index}",
        "sku": f"SKU-{index}",
        "raw_salla_details": {
            "cost_price": {"amount": "37.50"},
            "description": "x" * 16384,
        },
        "raw_salla": {"images": ["image-payload" * 2048]},
    } for index in range(count)]


ORDER = {
    "user_id": OWNER, "order_number": "synthetic-order", "currency": "SAR",
    "order_date": "2026-11-22", "order_status": "تم التنفيذ",
    "total_amount": 200,
    "products": [{
        "order_item_id": "synthetic-line", "product_id": "p-0",
        "quantity": 2, "total": 200,
    }],
}


async def main():
    client = AsyncMongoMockClient()
    database = client.synthetic_catalog_payload
    products = fixture_products(ARGS.products)
    await database[PRODUCTS].insert_many(products)
    builders = [
        ("standard", dashboard.build_mezan_v2_product_cost, {}),
        ("cooperative", dashboard.build_mezan_v2_product_cost_cooperative, {}),
        ("card", dashboard.build_mezan_v2_product_cost_cooperative,
         {"include_product_rows": False}),
    ]
    observations = []
    for name, builder, options in builders:
        for cohort_name, orders in [("empty", []), ("one_product", [ORDER])]:
            read_only = ReadOnlyDB(database)
            before = deepcopy(orders)
            result = await builder(read_only, OWNER, orders, **options)
            assert orders == before, "Builder changed its input order"
            assert result["total"] == (0 if cohort_name == "empty" else 75)
            catalog = [row for row in read_only.reads if row["collection"] == PRODUCTS]
            payload = json.dumps(result, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False).encode()
            observations.append({
                "builder": name, "cohort": cohort_name,
                "catalog_reads": len(catalog),
                "catalog_rows_returned": sum(row["rows_returned"] for row in catalog),
                "catalog_bson_bytes_returned": sum(row["bson_bytes_returned"] for row in catalog),
                "full_catalog_payload_rows": sum(row["full_payload_rows"] for row in catalog),
                "all_recorded_find_calls": len(read_only.reads),
                "cost_total_sar": result["total"],
                "result_sha256": hashlib.sha256(payload).hexdigest(),
                "catalog_read_details": catalog,
            })
    assert SOURCE_PATH.read_bytes() == SOURCE_BYTES_AT_IMPORT, "Source changed during measurement"
    evidence = {
        "kind": "synthetic_catalog_bson_payload_comparison_v1",
        "metric": "BSON-encoded documents returned by catalog cursor, not wire overhead, RSS, or production latency",
        "database": "isolated mongomock_motor, no live data",
        "repository_head": subprocess.check_output(
            ["git", "-C", str(ARGS.repo), "rev-parse", "HEAD"], text=True).strip(),
        "dashboard_source_sha256": hashlib.sha256(SOURCE_BYTES_AT_IMPORT).hexdigest(),
        "fixture": {
            "products": ARGS.products, "matched_products": 1,
            "description_bytes_per_product": 16384,
            "image_text_bytes_per_product": len("image-payload" * 2048),
            "expected_cost_sar": "2 units x 37.50 = 75.00",
            "companion_collections": "empty",
            "variant_arrays": "absent",
        },
        "observations": observations,
        "limits": [
            "The retained owner-wide alias scan still scales with catalog size.",
            "Whole variant arrays and companion cost/profile reads are not bounded by this experiment.",
            "No production RAM, latency, query-plan, concurrency, or recovery claim.",
        ],
    }
    ARGS.output.parent.mkdir(parents=True, exist_ok=True)
    ARGS.output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(evidence, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
