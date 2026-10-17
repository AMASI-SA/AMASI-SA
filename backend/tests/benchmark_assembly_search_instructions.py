"""Compare pinned b99 search with current search using disposable loopback Mongo.

Requires the b99f9fe30a1310dff1dcb807b4c4eab807a87a2f Git object (fetch-depth: 0).
Run explicitly with the same environment as test_assembly_search_instruction_batch.
It is intentionally outside unit-test discovery and never fetches remote history.
"""
import ast
import asyncio
import json
from pathlib import Path
import statistics
import subprocess

import preparation_piece_operations as pieces
from test_assembly_search_instruction_batch import AssemblySearchInstructionTests

BASELINE = "b99f9fe30a1310dff1dcb807b4c4eab807a87a2f"


def baseline_search():
    root = Path(__file__).resolve().parents[2]
    def source(path):
        return subprocess.check_output(["git", "show", f"{BASELINE}:{path}"], cwd=root).decode("utf-8")
    old_notes = {"__name__": "search_baseline_notes"}
    exec(compile(source("backend/order_tracking_notes.py"), "baseline/order_tracking_notes.py", "exec"), old_notes)
    tree = ast.parse(source("backend/preparation_piece_operations.py"))
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_assembly_search")
    namespace = dict(vars(pieces))
    namespace["enforce_stage_instructions"] = old_notes["enforce_stage_instructions"]
    exec(compile(ast.Module(body=[function], type_ignores=[]), "baseline/assembly_search.py", "exec"), namespace)
    return namespace["_assembly_search"]


async def run():
    fixture = AssemblySearchInstructionTests()
    await fixture.asyncSetUp()
    try:
        before = baseline_search()
        await fixture.instruction("notice")
        measurements = []
        for count in (1, 10, 50):
            await fixture.seed(count)
            await fixture.read(before)
            await fixture.read(pieces._assembly_search)
            old_times, new_times = [], []
            for _ in range(7):
                old, old_ms, old_queries = await fixture.read(before)
                new, new_ms, new_queries = await fixture.read(pieces._assembly_search)
                fixture.assertEqual(old, new)
                fixture.assertEqual((old_queries, new_queries), (count, 1))
                old_times.append(old_ms)
                new_times.append(new_ms)
            measurements.append({"pieces": count, "before_median_ms": round(statistics.median(old_times), 3),
                "after_median_ms": round(statistics.median(new_times), 3),
                "before_instruction_queries": count, "after_instruction_queries": 1})
        print("ASSEMBLY_SEARCH_BENCHMARK " + json.dumps({"baseline": BASELINE,
            "environment": "synthetic fixtures, real loopback Mongo; source/eligibility held constant; seven interleaved warm samples",
            "measurements": measurements}, sort_keys=True))
    finally:
        await fixture.asyncTearDown()


if __name__ == "__main__":
    asyncio.run(run())
