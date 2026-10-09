"""Benchmark D mapping parity and rollback, on disposable local Mongo only.

No runtime imports this module. The benchmark counter is fixture-only metadata.
"""
import argparse
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from benchmarks.shipping_delivered.designs import design_adapter
from benchmarks.shipping_delivered.isolation import localhost_only
from operational_atomic import operational_owner
import preparation_piece_operations as ops


COUNTER = "benchmark_only_canonical_fence"
WHEN = "2026-09-26T12:00:00+00:00"


def document(status, root_status="in_progress"):
    return {"user_id": "owner", "order_number": "synthetic-1", "order_date": WHEN,
            "order_status": root_status, "customer_name": "Synthetic canonical customer",
            "shipping_city": "Synthetic city", "raw_by_source": {"salla_direct": {
                "id": "synthetic-1", "reference_id": "synthetic-1", "date": WHEN,
                "status": status, "payment_method": "cod",
                "amounts": {"total": {"amount": 100, "currency": "SAR"}},
                "items": [{"id": "line-1", "product_id": "product-1",
                           "name": "Synthetic product", "quantity": 1}],
            }}}


def cases():
    rows = [
        ("slug_delivered_root_open", document({"slug": "delivered", "name": "Delivered"}), True),
        ("slug_open_root_delivered", document({"slug": "in_progress"}, "delivered"), False),
        ("native_delivered_without_slug", document({"name": "delivered"}), True),
        ("uppercase_delivered", document({"slug": "  DELIVERED  "}), True),
        ("empty_slug_native_delivered", document({"slug": "", "name": "delivered"}), True),
        ("whitespace_slug_preserves_mapper_precedence", document({"slug": " ", "name": "delivered"}), False),
        ("arabic_native_preserves_existing_guard", document({"name": "تم التوصيل"}), False),
        ("customized_root_does_not_override_dto_slug", document({
            "slug": "in_progress", "name": "قيد التنفيذ", "customized": {"name": "delivered"}}, "delivered"), False),
        ("raw_string_delivered", document("delivered"), True),
    ]
    fallback = document({"name": "in_progress"})
    fallback["raw_by_source"]["salla_direct"]["status_slug"] = "delivered"
    rows.append(("top_level_provider_status_slug", fallback, True))
    for name in ("wrong_tenant", "wrong_order", "missing_provider", "malformed_provider", "missing_order_date"):
        row = document({"slug": "in_progress"})
        if name == "wrong_tenant":
            row["user_id"] = "other-owner"
        elif name == "wrong_order":
            row["order_number"] = "other-order"
        elif name == "missing_provider":
            row.pop("raw_by_source")
        elif name == "malformed_provider":
            row["raw_by_source"]["salla_direct"] = "not-an-object"
        else:
            row.pop("order_date")
        rows.append((name, row, None))
    return rows


async def snapshot(db):
    return {name: await db[name].find({}).sort("_id", 1).to_list(100)
            for name in await db.list_collection_names()}


async def evaluate(db, design):
    captured = {}

    async def read(scoped):
        order = await ops._current_assembly_order(scoped, user_id="owner", order_number="synthetic-1")
        captured["dto"] = order.model_dump(mode="json") if order is not None else None
        ops.require_assembly_order_open(order)
        return captured["dto"]

    try:
        async with design_adapter(design):
            await operational_owner(db, "owner", read)
        return {"denied": False, "dto": captured.get("dto")}
    except HTTPException as exc:
        return {"denied": True, "dto": captured.get("dto"), "http_status": exc.status_code,
                "code": exc.detail.get("code") if isinstance(exc.detail, dict) else str(exc.detail)}


async def run_case(client, name, source, expected_denied):
    db = client["shipping_semantics_" + uuid4().hex]
    try:
        await db.unified_orders.insert_one(deepcopy(source))
        await db.mz2_atomic_owners.insert_one({"_id": "owner", "revision": 0})
        for collection in (ops.WORKFLOWS, ops.PIECES, ops.PIECE_EVENTS, "warehouse_locations"):
            await db[collection].insert_one({"user_id": "owner", "synthetic_marker": "unchanged"})
        baseline = await evaluate(db, "baseline")
        before = await snapshot(db)
        conditional = await evaluate(db, "conditional")
        after = await snapshot(db)
        if expected_denied is None:
            assert not baseline["denied"] and baseline["dto"] is None, baseline
            assert conditional["denied"] and conditional["code"] == "benchmark_canonical_order_unavailable", conditional
        else:
            assert baseline["denied"] == expected_denied, baseline
            assert conditional["denied"] == expected_denied, conditional
            if expected_denied:
                assert conditional["code"] == baseline["code"] == "assembly_order_delivered"
            else:
                assert conditional["dto"] == baseline["dto"], (baseline, conditional)
        if conditional["denied"]:
            assert after == before, "Denied transaction persisted changes, including fence/owner metadata"
        else:
            expected = deepcopy(before)
            expected["unified_orders"][0][COUNTER] = expected["unified_orders"][0].get(COUNTER, 0) + 1
            expected["mz2_atomic_owners"][0]["revision"] += 1
            assert after == expected, "Business document changed or counter not incremented exactly once"
        return {"name": name, "pass": True, "baseline": baseline, "conditional": conditional,
                "business_unchanged": True, "denial_rollback_verified": conditional["denied"],
                "intentional_fail_closed_difference": expected_denied is None}
    finally:
        assert db.name.startswith("shipping_semantics_")
        await client.drop_database(db.name)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="benchmark-results/semantics.json")
    args = parser.parse_args()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {"cases": [], "pass": 0, "fail": 0, "skipped": 0, "production_writes": 0,
              "limitations": ["Arabic aliases intentionally retain the existing exact-delivered guard semantics.",
                              "Invalid/missing canonical rows deliberately fail closed in D; baseline returns None.",
                              "Exercises the shared canonical load/guard used by physical and virtual paths; route safety is tested separately."]}
    client = None
    try:
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        assert uri == "mongodb://127.0.0.1:27018/?replicaSet=shippingbenchmark", "Explicit disposable local Mongo URI required"
        localhost_only()
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        hello = await client.admin.command("hello")
        version = (await client.admin.command("buildInfo"))["version"]
        assert version == "8.0.12" and hello.get("setName") == "shippingbenchmark" and hello.get("isWritablePrimary")
        report.update(mongo_version=version, replica_set=hello["setName"], primary=True, mongomock=False)
        for name, source, expected in cases():
            try:
                result = await run_case(client, name, source, expected)
                report["pass"] += 1
            except Exception as exc:
                result = {"name": name, "pass": False, "error_type": type(exc).__name__, "error": str(exc)}
                report["fail"] += 1
            report["cases"].append(result)
    except Exception as exc:
        report["environment_error"] = {"type": type(exc).__name__, "error": str(exc)}
        report["fail"] += 1
    finally:
        if client is not None:
            client.close()
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("pass", "fail", "skipped")}))
    if report["fail"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
