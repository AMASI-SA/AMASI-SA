"""Temporary synthetic benchmark; no live database, environment file, or writes."""
import ast
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import json
import statistics
import subprocess
import time

import pytest

import operational_balance_archive as archive
import operational_balance_store as store
import operational_demand_refresh as demand
from test_operational_balance_archive_visible_batches import (
    ObservedCollection, archived_fixture, assert_read_only, load_visible,
)


BASE = "4860548f0b032e8e3beca18a4776413cd7e0368b"
source = subprocess.run(
    ["git", "show", BASE + ":backend/operational_balance_archive.py"],
    check=True, capture_output=True, text=True).stdout
node = next(node for node in ast.parse(source).body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "unpack")
namespace = dict(archive.__dict__)
exec(compile(ast.Module(body=[node], type_ignores=[]), "immutable-baseline-unpack", "exec"), namespace)
baseline = namespace["unpack"]


async def sample(mode, repetition):
    db, expected = archived_fixture(64)
    before = deepcopy(db.data)
    original_read = ObservedCollection.find_one
    original_update = ObservedCollection.update_one
    original_stage = demand.refresh_stage
    timings = {}
    delay = 0.003

    async def read(collection, *args, **kwargs):
        await asyncio.sleep(delay)
        return await original_read(collection, *args, **kwargs)

    async def update(collection, *args, **kwargs):
        if collection.name == demand.LEASES:
            await asyncio.sleep(delay)
        return await original_update(collection, *args, **kwargs)

    async def verify_delay(_db, _stats):
        # Model the four metadata reads performed by a browser-owner verify.
        # These are simulated delays, not a measured production latency.
        await asyncio.sleep(4 * delay)

    @contextmanager
    def timed_stage(stage):
        start = time.perf_counter()
        renewals = sum(row[1] == "renew" for row in db.writes)
        try:
            with original_stage(stage):
                yield
        finally:
            if stage == "snapshot_load":
                timings["snapshot_ms"] = round((time.perf_counter() - start) * 1000, 3)
                timings["snapshot_lease_renewals"] = sum(row[1] == "renew" for row in db.writes) - renewals

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ObservedCollection, "find_one", read)
        patch.setattr(ObservedCollection, "update_one", update)
        patch.setattr(demand, "refresh_stage", timed_stage)
        patch.setattr(store, "unpack", baseline if mode == "immutable_base" else archive.unpack)
        result, stats = await load_visible(db, patch, verify_hook=verify_delay)
    assert result == expected
    assert db.page_reads == 64
    assert_read_only(db, before)
    return {"mode": mode, "repetition": repetition, "synthetic_pages": 64,
            "simulated_database_operation_ms": delay * 1000,
            "snapshot_verify_calls": stats["snapshot_gates"],
            "audit_page_reads": db.page_reads, **timings,
            "full_result_equal": True, "input_unchanged": True,
            "nonlease_writes": 0, "orphan_tasks": 0}


async def main():
    rows = []
    for repetition in range(1, 4):
        for mode in ("immutable_base", "current_source"):
            row = await sample(mode, repetition)
            rows.append(row)
            print(json.dumps(row, sort_keys=True), flush=True)
    medians = {mode: statistics.median(row["snapshot_ms"] for row in rows if row["mode"] == mode)
               for mode in ("immutable_base", "current_source")}
    print(json.dumps({"median_snapshot_ms": medians,
                      "simulated_speedup": round(medians["immutable_base"] / medians["current_source"], 3)},
                     sort_keys=True))


asyncio.run(main())
