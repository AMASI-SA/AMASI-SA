"""Isolated recursive-copy lifetime prototype; production source is unchanged.

The prototype wraps only the existing copier's final four statements in
try/finally, setting its private recursive clone cell to None on every exit.
It neither clears the memo nor changes traversal, hooks, checkpoints or values.
GC-disabled weakref probes expose retained acyclic input. Timings use six fresh
processes with normal GC and a bounded subset of the existing item fixture.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
from copy import deepcopy
import gc
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import platform
import resource
import socket
import statistics
import subprocess
import sys
import time
from types import FunctionType
import weakref

HERE = Path(__file__).resolve()
ROOT = HERE.parents[4]
OUTPUT = HERE.with_name("measure_report_copy_lifetime_result.json")
SOURCE = ROOT / "backend/operational_report_copy.py"
FIXTURE = HERE.with_name("profile_source_preparation.py")
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]


def no_network(*args, **kwargs):
    raise AssertionError("This offline copy experiment has no network capability")


socket.socket.connect = no_network
socket.create_connection = no_network

from bson import BSON
import pytest
import operational_report_copy as copying


def prototype():
    tree = ast.parse(inspect.getsource(copying.copy_report_value))
    function = tree.body[0]
    tail = function.body[-4:]
    assert [type(node) for node in tail] == [ast.Expr, ast.With, ast.Expr, ast.Return]
    assert ast.unparse(tail[0]) == ast.unparse(tail[2]) == "preparation_checkpoint()"
    assert ast.unparse(tail[-1]) == "return result"
    function.body[-4:] = [ast.Try(body=tail, handlers=[], orelse=[], finalbody=[
        ast.Assign(targets=[ast.Name(id="clone", ctx=ast.Store())], value=ast.Constant(None))])]
    namespace = {}
    exec(compile(ast.fix_missing_locations(tree), "<isolated-copy-lifetime-prototype>", "exec"), namespace)
    # Use the existing module's globals so its real checkpoints, diagnostics,
    # fallback hooks and test monkeypatches keep their existing semantics.
    return FunctionType(namespace["copy_report_value"].__code__, copying.__dict__,
                        "copy_report_value_lifetime_prototype")


class Marker:
    def __init__(self):
        self.value = [42]


class Rejected:
    def __deepcopy__(self, memo):
        raise ValueError("synthetic copy rejected")


def lifetime_attempt(copier, mode):
    marker = Marker()
    first = [marker]
    source = {"first": first, "alias": first}
    if mode == "hook_error":
        source["invalid"] = Rejected()
    elif mode == "cancel_during":
        source["rows"] = [{"nested": [i]} for i in range(600)]
    calls, result, outcome = 0, None, "returned"
    original = copying.preparation_checkpoint

    def checkpoint():
        nonlocal calls
        calls += 1
        stop_at = 1 if mode == "cancel_entry" else 2
        if mode.startswith("cancel_") and calls == stop_at:
            raise asyncio.CancelledError
        original()

    try:
        copying.preparation_checkpoint = checkpoint
        try:
            result = copier(source)
        except ValueError as error:
            assert mode == "hook_error" and str(error) == "synthetic copy rejected"
            outcome = "ValueError"
        except asyncio.CancelledError:
            assert mode.startswith("cancel_")
            outcome = "CancelledError"
    finally:
        copying.preparation_checkpoint = original
    if result is not None:
        assert result["first"] is result["alias"] and result["first"] is not first
        assert result["first"][0] is not marker and result["first"][0].value == [42]
    # Returning only the weakref and copied output releases every local strong
    # input reference before the caller observes lifetime with GC disabled.
    return weakref.ref(marker), result, outcome, calls


def lifetime_checks(candidate):
    observations = []
    was_enabled = gc.isenabled()
    try:
        gc.disable()
        for variant, copier in (("deepcopy", deepcopy), ("current", copying.copy_report_value),
                                ("prototype", candidate)):
            modes = ("success", "hook_error") if variant == "deepcopy" else (
                "success", "hook_error", "cancel_entry", "cancel_during", "cancel_exit")
            for mode in modes:
                reference, result, outcome, calls = lifetime_attempt(copier, mode)
                retained = reference() is not None
                expected_retention = variant == "current" and mode != "cancel_entry"
                assert retained is expected_retention, (variant, mode, retained)
                expected_outcome = ("ValueError" if mode == "hook_error" else
                                    "CancelledError" if mode.startswith("cancel_") else "returned")
                assert outcome == expected_outcome
                observations.append({"variant": variant, "mode": mode,
                    "input_retained_without_gc": retained, "output_retained": result is not None,
                    "outcome": outcome, "preparation_checkpoints": calls})
                # Cleanup is outside every lifetime observation and timing.
                result = None
                gc.collect()
                assert reference() is None
    finally:
        if was_enabled:
            gc.enable()
    return observations


def parity_checks(candidate):
    import test_operational_balance_report_copy as existing
    names = (
        "test_bson_python_types_and_nonstring_keys_use_the_standard_semantics",
        "test_aliases_and_cycles_cross_plain_containers_and_fallback_tuples",
        "test_custom_hooks_subclasses_shared_memo_and_original_keepalive",
        "test_copy_failure_and_cooperative_cancellation_return_no_partial_value",
        "test_custom_hook_memo_override_applies_to_later_atomic_values_and_keys",
        "test_completed_container_restores_its_memo_after_a_custom_hook",
    )
    for name in names:
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(copying, "copy_report_value", candidate)
            function = getattr(existing, name)
            function(monkeypatch) if inspect.signature(function).parameters else function()
    # A custom hook is allowed to retain the stdlib memo after copying. The
    # prototype must not clear it to obtain its lifetime improvement.
    for copier in (deepcopy, copying.copy_report_value, candidate):
        captured = []

        class MemoHook:
            def __deepcopy__(self, memo):
                captured.append(memo)
                return "copied-hook"

        first = [Marker()]
        source = {"first": first, "hook": MemoHook(), "again": first}
        result = copier(source)
        memo = captured[0]
        assert memo[id(source)] is result
        assert memo[id(first)] is result["first"] is result["again"]
        assert any(value is source for value in memo[id(memo)])
        assert any(value is first for value in memo[id(memo)])
    return [*names, "escaped_custom_hook_memo_remains_intact"]


def item_fixture(count):
    spec = importlib.util.spec_from_file_location("synthetic_source_copy_fixture", FIXTURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.COUNT = count // 4
    data = module.fixture()
    items = [item for row in data["unified_orders"]
             for item in row["raw_by_source"]["salla_direct"]["items"]]
    assert len(items) == count
    return items


def rss_bytes():
    return int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")


def timing_child(variant, count):
    candidate = prototype()
    copier = {"deepcopy": deepcopy, "current": copying.copy_report_value, "prototype": candidate}[variant]
    source = item_fixture(count)
    expected = BSON.encode({"items": source})
    expected_sha = hashlib.sha256(expected).hexdigest()
    bson_bytes = len(expected)
    del expected
    assert gc.isenabled()
    # Normalize setup garbage before the measured copy loop. GC stays enabled
    # throughout copying, and no explicit collection occurs in that interval.
    gc.collect()
    counts, gc_ms, gc_started = [0, 0, 0], [0.0], {}

    def observed_gc(phase, info):
        generation = info["generation"]
        if phase == "start":
            gc_started[generation] = time.perf_counter()
        else:
            counts[generation] += 1
            gc_ms[0] += (time.perf_counter() - gc_started.pop(generation)) * 1000

    before_rss = rss_bytes()
    gc.callbacks.append(observed_gc)
    try:
        started, cpu_started = time.perf_counter(), time.process_time()
        result = [copier(item) for item in source]
        duration_ms = (time.perf_counter() - started) * 1000
        cpu_ms = (time.process_time() - cpu_started) * 1000
    finally:
        gc.callbacks.remove(observed_gc)
    after_rss = rss_bytes()
    assert len(result) == count
    assert hashlib.sha256(BSON.encode({"items": result})).hexdigest() == expected_sha
    assert hashlib.sha256(BSON.encode({"items": source})).hexdigest() == expected_sha
    for index in (0, 1, count // 2, count - 1):
        assert result[index] is not source[index]
        assert result[index]["options"] is not source[index]["options"]
        result[index]["product"]["images"][0]["alt"] = "changed output only"
        assert source[index]["product"]["images"][0]["alt"] == "Synthetic"
    return {"variant": variant, "items_copied": count, "input_bson_bytes": bson_bytes,
        "duration_ms": round(duration_ms, 3), "process_cpu_ms": round(cpu_ms, 3),
        "gc_collections": counts, "gc_ms": round(gc_ms[0], 3),
        "gc_enabled": gc.isenabled(), "gc_thresholds": list(gc.get_threshold()),
        "rss_before": before_rss, "rss_after": after_rss,
        "process_peak_rss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        "logical_bson_sha256": expected_sha, "complete_bson_parity": True,
        "input_unchanged": True, "sampled_nested_output_independence": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", choices=("deepcopy", "current", "prototype"))
    parser.add_argument("--items", type=int, default=8192)
    args = parser.parse_args()
    if args.items % 4 or not 4 <= args.items <= 8192:
        parser.error("items must be a multiple of four between 4 and 8192")
    if args.child:
        print(json.dumps(timing_child(args.child, args.items), sort_keys=True))
        return
    assert not OUTPUT.exists(), "Retain existing measurement; do not overwrite or silently rerun"
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    candidate = prototype()
    lifetime = lifetime_checks(candidate)
    parity = parity_checks(candidate)
    runs = []
    for variant in ("deepcopy", "current", "prototype", "prototype", "current", "deepcopy"):
        completed = subprocess.run([sys.executable, "-B", str(HERE), "--child", variant,
            "--items", str(args.items)], capture_output=True, text=True, timeout=45)
        if completed.returncode:
            raise RuntimeError(f"Synthetic {variant} child failed: {completed.stderr[-3000:]}")
        runs.append(json.loads(completed.stdout))
        print(f"Completed isolated {variant} timing sample {len(runs)}", flush=True)
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == source_hash
    assert len({row["logical_bson_sha256"] for row in runs}) == 1
    medians = {variant: statistics.median(row["duration_ms"] for row in runs if row["variant"] == variant)
               for variant in ("deepcopy", "current", "prototype")}
    result = {
        "scope": "Isolated copier lifetime prototype and bounded synthetic item copies; no production edits",
        "python": platform.python_version(), "script_sha256": hashlib.sha256(HERE.read_bytes()).hexdigest(),
        "copier_source_sha256_before_and_after": source_hash,
        "fixture_script_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "prototype": "Existing final four statements wrapped in try/finally; only clone = None added on exit",
        "lifetime_method": "GC disabled while dropping acyclic original graph and retaining copy; explicit collection only for cleanup",
        "lifetime_observations": lifetime, "existing_parity_checks_passed": parity,
        "timing_method": "Six fresh processes: deepcopy/current/prototype/prototype/current/deepcopy; normal GC during timing; setup collection before timing",
        "timing_runs": runs, "median_ms": medians,
        "limitations": [
            "8,192 items at most; exact existing synthetic item shape, not the complete 76,604-item source collector.",
            "No real financial data, database/network work, production memory pressure or production speed was measured.",
            "RSS/high-water include interpreter and fixture allocations; weakref checks establish retention causality.",
            "The prototype is not a production change and does not replace any additional copy call site.",
        ],
    }
    with OUTPUT.open("x") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"result": str(OUTPUT), "median_ms": medians,
                      "lifetime_cases": len(lifetime), "parity_checks": len(parity)}, sort_keys=True))


if __name__ == "__main__":
    main()
