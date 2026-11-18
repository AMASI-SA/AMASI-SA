#!/usr/bin/env python3
"""Synthetic pure-reducer baseline comparison; no database or network access."""
import argparse
from copy import deepcopy
import gc
from hashlib import sha256
import json
from pathlib import Path
import platform
import subprocess
import sys
from time import perf_counter, thread_time
import types


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.repository).resolve()
    sys.path.insert(0, str(root / "backend"))
    path = "backend/operational_balance_engine.py"
    baseline = subprocess.check_output(["git", "show", args.baseline + ":" + path], cwd=root)
    candidate = (root / path).read_bytes()
    modules = []
    for name, text in (("baseline", baseline), ("candidate", candidate)):
        module = types.ModuleType("synthetic_engine_issue_" + name)
        exec(compile(text, name + ":" + path, "exec"), module.__dict__)
        modules.append(module)
    start, end = "2026-11-01T00:00:00+00:00", "2026-11-18T00:00:00+00:00"
    state = {"owner_id": "synthetic", "status": "active", "started_at": start}
    for module in modules:
        module.reconcile(state, {"orders": [{"id": "warm"}]}, end)
    measurements = []
    for count in (1000, 6000, 12000):
        source = {"issues": [{"code": "source_gap", "source_id": str(i), "status": "incomplete"}
                             for i in range(count)], "orders": [{"id": str(i)} for i in range(count)]}
        untouched = deepcopy(source)
        results, timings = [], []
        for module in modules:
            gc.collect()
            cpu, wall = thread_time(), perf_counter()
            results.append(module.reconcile(state, source, end))
            timings.append({"cpu_seconds": thread_time() - cpu, "wall_seconds": perf_counter() - wall})
        assert results[0] == results[1]
        assert source == untouched and state == {"owner_id": "synthetic", "status": "active", "started_at": start}
        assert len(results[1]["engine"]["issues"]) == 2 * count
        assert "_operational_issue_index" not in json.dumps(results[1])
        measurements.append({"orders": count, "source_issues": count, "final_issues": count * 2,
            "baseline": timings[0], "candidate": timings[1], "complete_outputs_equal": True,
            "inputs_unchanged": True,
            "baseline_membership_row_visits_derived_from_unique_cohort": count * count + count * (count - 1) // 2})
    receipt = {"synthetic_only": True, "database_access": False, "network_access": False,
        "python": platform.python_version(), "baseline_git_sha": args.baseline,
        "baseline_source_sha256": sha256(baseline).hexdigest(),
        "candidate_source_sha256": sha256(candidate).hexdigest(),
        "measurements": measurements,
        "limits": ["Single local observations, not production latency evidence.",
                   "Membership row visits are a derived count; the timing workloads use ordinary dicts/lists.",
                   "This workload isolates diagnostic deduplication, not every live reconciliation phase."]}
    output = Path(args.output)
    output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
