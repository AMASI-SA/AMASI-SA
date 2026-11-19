#!/usr/bin/env python3
"""Reproduce supplier-return replay costs with synthetic, immutable inputs.

Only the engine implementation is selected from the frozen Git baseline. Both
engines use the same installed dependencies and unchanged shared helpers. No
database, network, customer record, or production endpoint is accessed.
"""
import argparse
import cProfile
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import time
import types


BASELINE = "c3c1cd26d93ab24d6e08003278b9910526055cc7"
START = "2026-10-05T09:00:00+00:00"
NOW = "2026-10-08T12:00:00+00:00"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def fixture(count, baseline, history_mib=0):
    state = {"owner_id": "synthetic-owner", "status": "active", "started_at": START,
        "engine": {"facts": {f"unrelated:{i}": {"proof": str(i),
            "fields": {"valid": True, "source": "synthetic"}} for i in range(count * 5)}}}
    orders = [{"id": f"o{i}", "created_at": "2026-10-06T09:00:00+00:00",
        "updated_at": "2026-10-06T10:00:00+00:00", "currency": "SAR", "status": "in_progress",
        "items": [{"id": "i1", "cost": "100", "quantity": "2", "supplier_id": "s1"}],
        "carrier": {"id": "imile", "cost": "17.25"}, "cod_amount": "500"} for i in range(count)]
    sources = {"orders": orders, "supplier_receipts": [{"id": f"r{i}", "order_id": f"o{i}",
        "item_id": "i1", "supplier_id": "s1", "amount": "200.00", "currency": "SAR",
        "accepted_at": NOW} for i in range(count)], "supplier_returns": [
        {"id": f"ret{i}", "receipt_id": f"r{i}", "accepted": True, "amount": "20.00",
         "accepted_at": NOW} for i in range(count)]}
    saved = baseline.reconcile(state, sources, NOW)
    if history_mib:
        # Scale byte size with ordinary nested audit rows. This is explicitly
        # synthetic, not a reconstruction of the private production ledger.
        model = {"id": "synthetic-history-000000", "action": "operational_reconciled",
            "at": NOW, "before": None, "after": {"source": "synthetic",
            "proofs": [{"id": f"proof-{i}", "recorded_at": NOW, "note": "x" * 96}
                       for i in range(12)]}}
        missing = max(0, history_mib * 1024 * 1024 - len(encoded(saved)))
        rows = (missing + len(encoded(model)) - 1) // len(encoded(model))
        for index in range(rows):
            event = deepcopy(model)
            event["id"] = f"synthetic-history-{index:06d}"
            saved["engine"]["audit"].append(event)
    return saved, sources


def profile(function, state, sources):
    profiler = cProfile.Profile()
    started = time.perf_counter()
    profiler.enable()
    result = function(state, sources, NOW)
    profiler.disable()
    elapsed = time.perf_counter() - started
    stats = profiler.getstats()
    builtins = {entry.code: entry.callcount for entry in stats if isinstance(entry.code, str)}
    top = sorted([{"function": getattr(entry.code, "co_name", str(entry.code)),
        "module": Path(getattr(entry.code, "co_filename", "builtin")).name,
        "line": getattr(entry.code, "co_firstlineno", None), "calls": entry.callcount,
        "cumulative_seconds": round(entry.totaltime, 6),
        "self_seconds": round(entry.inlinetime, 6)} for entry in stats],
        key=lambda row: row["cumulative_seconds"], reverse=True)[:12]
    return result, {"elapsed_seconds": round(elapsed, 6),
        "string_prefix_checks": builtins.get("<method 'startswith' of 'str' objects>", 0),
        "dictionary_get_calls": builtins.get("<method 'get' of 'dict' objects>", 0), "top": top}


def run_case(count, baseline, candidate, history_mib=0):
    state, sources = fixture(count, baseline, history_mib)
    before = encoded([state, sources])
    ordinary, profiled = {}, {}
    expected = None
    for name, module in (("baseline", baseline), ("candidate", candidate)):
        started = time.perf_counter()
        result = module.reconcile(state, sources, NOW)
        ordinary[name] = round(time.perf_counter() - started, 6)
        output = encoded(result)
        assert output == encoded(state), f"{name}: replay changed saved state"
        assert encoded([state, sources]) == before, f"{name}: mutated its inputs"
        if expected is None:
            expected = output
        assert output == expected, "complete ordered financial outputs differ"
        repeated, profiled[name] = profile(module.reconcile, state, sources)
        assert encoded(repeated) == expected, f"{name}: profiling changed output"
        assert encoded([state, sources]) == before, f"{name}: profiled run mutated its inputs"
    return {"orders": count, "receipt_facts": count, "source_returns": count,
        "facts": len(state["engine"]["facts"]), "obligations": len(state["engine"]["obligations"]),
        "audit_rows": len(state["engine"]["audit"]), "synthetic_history_target_mib": history_mib,
        "input_bytes": len(before), "state_bytes": len(encoded(state)),
        "input_sha256": digest(before), "complete_ordered_output_sha256": digest(expected),
        "all_outputs_equal_saved_state": True, "all_inputs_unchanged": True,
        "ordinary_elapsed_seconds": ordinary, "separate_profiled_work": profiled}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--baseline", default=BASELINE)
    parser.add_argument("--sizes", default="256,512,1024,2048")
    parser.add_argument("--history-mib", type=int, default=20)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    sys.path.insert(0, str(repo / "backend"))
    candidate = importlib.import_module("operational_balance_engine")
    old = subprocess.check_output(["git", "show", f"{args.baseline}:backend/operational_balance_engine.py"], cwd=repo)
    baseline = types.ModuleType("frozen_receipt_return_baseline")
    exec(compile(old, f"baseline-{args.baseline[:12]}/operational_balance_engine.py", "exec"), baseline.__dict__)
    source_path = repo / "backend" / "operational_balance_engine.py"
    evidence = {"schema": "synthetic-receipt-return-profile-v1", "observed_at": datetime.now(timezone.utc).isoformat(),
        "baseline_git_sha": args.baseline, "baseline_engine_sha256": digest(old),
        "candidate_engine_sha256": digest(source_path.read_bytes()),
        "python_version": sys.version, "ordinary_runs_per_engine_case": 1, "profiled_runs_per_engine_case": 1,
        "data_kind": "synthetic_only", "production_actions": False,
        "timing_note": "Local diagnostic timing; deterministic call counts establish work growth. No live latency claim.",
        "cases": []}
    cases = [(int(value), 0) for value in args.sizes.split(",")]
    if args.history_mib:
        cases.append((512, args.history_mib))
    for count, history in cases:
        case = run_case(count, baseline, candidate, history)
        evidence["cases"].append(case)
        args.output.write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps({key: case[key] for key in ("orders", "synthetic_history_target_mib", "state_bytes",
            "ordinary_elapsed_seconds", "all_outputs_equal_saved_state", "all_inputs_unchanged")}), flush=True)


if __name__ == "__main__":
    main()
