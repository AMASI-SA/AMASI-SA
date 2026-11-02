"""Real Mongo proof of compact, indexed, bounded authority reads.

Requires explicit credential-free MZ2_TEST_MONGO_URI on127.0.0.1:port. Every
integration test uses a disposable UUID database; no production fallback.
"""
from contextlib import asynccontextmanager
from copy import deepcopy
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest

from fulfillment_v2_routes import ensure_fulfillment_indexes
from order_cost_authority import CORRECTIONS, LIFECYCLES, SNAPSHOTS, load_order_cost_authority
from stock_component_consumption_service import PLANS, UNITS


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_order_cost_authority.py"
SPEC = importlib.util.spec_from_file_location("_order_cost_benchmark_fixture", SCRIPT)
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


@asynccontextmanager
async def isolated_database(*, count=None):
    uri = os.environ.get("MZ2_TEST_MONGO_URI")
    if not uri:
        pytest.skip("Explicit local MZ2_TEST_MONGO_URI required; no production fallback")
    capture = bench.CommandCapture()
    client = bench.create_client(uri, capture)
    name = bench.DATABASE_PREFIX + uuid4().hex
    db = client[name]
    try:
        hello = await client.admin.command("hello")
        assert hello.get("setName") and hello.get("isWritablePrimary")
        orders = await bench.seed_fixtures(db, count=count, metadata_kib=40) if count is not None else []
        yield db, capture, orders
    finally:
        capture.enabled = False
        assert name.startswith(bench.DATABASE_PREFIX)
        await client.drop_database(name)
        assert name not in await client.list_database_names()
        client.close()


@pytest.mark.parametrize("uri", [
    "", "mongodb://production.example:27017/", "mongodb+srv://127.0.0.1/",
    "mongodb://user:secret@127.0.0.1:27561/", "mongodb://127.0.0.1:27561/production",
    "mongodb://127.0.0.1:27561,production.example:27017/",
])
def test_benchmark_rejects_implicit_remote_credentialed_and_named_databases(uri):
    with pytest.raises(ValueError, match="Explicit credential-free"):
        bench.local_mongo_uri(uri)


@pytest.mark.asyncio
async def test_startup_creates_real_owner_order_authority_indexes():
    async with isolated_database() as (db, _, _):
        await ensure_fulfillment_indexes(db)
        for collection, prefix in [
            (LIFECYCLES, [("user_id", 1), ("order_number", 1)]),
            (SNAPSHOTS, [("user_id", 1), ("order_number", 1), ("order_item_id", 1)]),
            (CORRECTIONS, [("user_id", 1), ("order_number", 1), ("order_item_id", 1)]),
            (PLANS, [("user_id", 1), ("order_id", 1)]),
            (UNITS, [("user_id", 1), ("plan_id", 1), ("order_line_id", 1)]),
        ]:
            indexes = await db[collection].index_information()
            assert any(index["key"][:len(prefix)] == prefix for index in indexes.values()), collection


@pytest.mark.asyncio
async def test_real_compact_projection_preserves_cost_and_excludes_frozen_metadata_on_wire():
    async with isolated_database(count=80) as (db, capture, orders):
        compact, compact_metrics = await bench.measured(
            capture, lambda: load_order_cost_authority(db, bench.OWNER, orders),
        )
        full, full_metrics = await bench.measured(
            capture, lambda: load_order_cost_authority(db, bench.OWNER, orders, include_frozen_metadata=True),
        )

        assert bench.resolve_cohort(orders, compact) == bench.resolve_cohort(orders, full) == 28800
        assert not bench.contains_frozen_definitions(compact)
        assert bench.contains_frozen_definitions(full)
        assert compact_metrics["reply_bson_bytes"] < full_metrics["reply_bson_bytes"] / 2
        assert compact_metrics["logical_reads"] == full_metrics["logical_reads"] == 6


@pytest.mark.asyncio
async def test_authority_batches_are_constant_per_cohort_and_read_only_across_boundary():
    async with isolated_database(count=251) as (db, capture, orders):
        baseline = {collection: await db[collection].count_documents({})
                    for collection in [LIFECYCLES, PLANS, UNITS, SNAPSHOTS, CORRECTIONS]}
        for count, reads in [(1, 6), (80, 6), (250, 6), (251, 11)]:
            context, metrics = await bench.measured(
                capture, lambda: load_order_cost_authority(db, bench.OWNER, orders[:count]),
            )
            assert bench.resolve_cohort(orders[:count], context) == count * 360
            assert metrics["logical_reads"] == reads
            assert context["read_batches"] == (1 if count <= 250 else 2)
            assert set(metrics["commands"]) <= {"find", "aggregate", "getMore"}
            for command in capture.queries:
                query = command.get("filter") if "find" in command else command["pipeline"][0]["$match"]
                assert query["user_id"] == bench.OWNER
        assert baseline == {collection: await db[collection].count_documents({}) for collection in baseline}


@pytest.mark.asyncio
async def test_real_scoped_query_plans_use_indexes_without_collection_scans():
    async with isolated_database(count=80) as (db, capture, orders):
        _, metrics = await bench.measured(
            capture, lambda: load_order_cost_authority(db, bench.OWNER, orders),
        )
        assert metrics["logical_reads"] == 6
        explanations = await bench.explain_queries(db, deepcopy(capture.queries))

        assert {row["collection"] for row in explanations} == {LIFECYCLES, PLANS, UNITS, SNAPSHOTS, CORRECTIONS}
        for row in explanations:
            assert row["collection_scan"] is False, row
            assert "IXSCAN" in row["stages"], row
