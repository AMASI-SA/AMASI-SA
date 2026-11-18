"""Fast correctness checks for the separate real-Mongo report benchmark."""
from collections import Counter
from copy import deepcopy
import importlib.util
from pathlib import Path

from mongomock_motor import AsyncMongoMockClient
import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("supplier_cost_benchmark", ROOT / "scripts/testing/supplier_cost_reporting/benchmark.py")
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


@pytest.mark.asyncio
async def test_benchmark_fixture_independent_cost_oracle_and_three_surfaces():
    orders, data, expected = benchmark.fixture(48, history_count=48)
    assert sum(row["received_units"] for row in expected) == 60
    assert sum(row["estimated_units"] for row in expected) == 36
    assert len(data[benchmark.candidate.INVOICES]) == 60
    db = AsyncMongoMockClient().synthetic_report_benchmark
    for collection, rows in data.items():
        if rows:
            await db[collection].insert_many(deepcopy(rows))
    baseline, _ = benchmark.frozen_helper()
    values = {}
    for arm, module in (("baseline", baseline), ("candidate", benchmark.candidate)):
        with benchmark.helper_arm(module, Counter()):
            values[arm] = {}
            for surface in ("executive", "top-products", "sold"):
                value = await benchmark.build(benchmark.ReadDB(db), orders, surface)
                benchmark.oracle(value, surface, expected)
                values[arm][surface] = value
    assert values["baseline"] == values["candidate"]


def test_benchmark_monthly_fixture_has_partial_receipts_and_cross_batch_invoices():
    orders, data, expected = benchmark.fixture(6000, history_count=0)
    numbers = {order["order_number"]: index // 250 for index, order in enumerate(orders)}
    pieces = {piece["piece_id"]: numbers[piece["order_number"]] for piece in data[benchmark.candidate.PIECES]}
    assert len(data[benchmark.candidate.PIECES]) == 7500
    assert all(row["mz2_txn_group_id"] for row in data[benchmark.candidate.INVOICES])
    assert any(len({pieces[pid] for line in invoice["lines"] for pid in line["piece_ids"]}) >= 6
               for invoice in data[benchmark.candidate.INVOICES])
    assert {row["received_units"] for row in expected} == {0, 1, 2}
    assert all(row["cost"] > 0 for row in expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("uri", [
    "mongodb://example.com:27017/?directConnection=true",
    "mongodb://127.0.0.1:27018,remote.example:27018/",
    "mongodb+srv://remote.example/",
    "mongodb://127.0.0.1:27018/",
])
async def test_benchmark_refuses_nonlocal_database(uri, monkeypatch):
    def forbidden_client(*args, **kwargs):
        raise AssertionError("Must reject unsafe URI before creating a client")

    monkeypatch.setattr(benchmark, "AsyncIOMotorClient", forbidden_client)
    with pytest.raises(AssertionError, match="loopback"):
        await benchmark.run(uri, output="unused.json", count=48, repeats=2, history_count=0)
