"""Predeclared paired acceptance judgment; never averages away a failed block."""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path


MIB = 1024 * 1024
COMPARISONS = (("B", "A1"), ("C", "A1"), ("C", "B"))
SPECS = (("paced", "p95_ms"), ("paced", "p99_ms"),
         ("paced", "cpu_percent"), ("capacity", "cpu_percent"),
         ("paced", "rss_peak_bytes"), ("capacity", "rss_peak_bytes"),
         ("capacity", "throughput"))


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _value(cell, phase, metric):
    value = (cell or {}).get("phases", {}).get(phase, {}).get(metric)
    return value if _number(value) else None


def _budget(metric, reference):
    if metric == "p95_ms":
        return min(5.0, max(0.25, 0.05 * reference))
    if metric == "p99_ms":
        return min(10.0, max(0.50, 0.05 * reference))
    if metric == "cpu_percent":
        return 1.0
    if metric == "rss_peak_bytes":
        return 8 * MIB
    return 0.05 * reference


def judge(evidence):
    """Eight fixed blocks. Missing/duplicate cells cannot silently disappear."""
    indexed = {}
    problems = []
    for cell in evidence.get("results", []):
        key = (cell.get("block"), cell.get("variant"))
        if key[0] not in range(8) or key[1] not in ("A1", "A2", "B", "C"):
            problems.append("unexpected_cell")
            continue
        if key in indexed:
            problems.append("duplicate_cell")
            indexed[key] = None
        else:
            indexed[key] = cell
    rows = []
    safety_rows = []
    for block in range(8):
        cells = {variant: indexed.get((block, variant)) for variant in ("A1", "A2", "B", "C")}
        for variant, cell in cells.items():
            failures, missing = [], []
            if cell is None:
                missing.append("cell")
            else:
                if cell.get("functional_pass") is False:
                    failures.append("functional_pass_false")
                elif cell.get("functional_pass") is not True:
                    missing.append("functional_pass")
                if cell.get("valid") is not True:
                    missing.append("valid_measurement")
                for phase in ("paced", "capacity"):
                    for metric in ("errors", "timeouts"):
                        value = _value(cell, phase, metric)
                        if value is None:
                            missing.append(f"{phase}.{metric}")
                        elif value > 0:
                            failures.append(f"{phase}.{metric}")
            safety_rows.append({"block": block, "variant": variant,
                                "result": "FAIL" if failures else "INCONCLUSIVE" if missing else "PASS",
                                "failures": failures, "missing": missing})
        for phase, metric in SPECS:
            a1, a2 = (_value(cells[v], phase, metric) for v in ("A1", "A2"))
            aa_available = a1 is not None and a2 is not None and (metric != "throughput" or a1 > 0)
            aa_limit = _budget(metric, a1) / 2 if aa_available else None
            aa_difference = abs(a2 - a1) if aa_available else None
            aa_valid = bool(aa_available and cells["A1"].get("valid") is True
                            and cells["A2"].get("valid") is True and aa_difference <= aa_limit)
            for candidate, reference in COMPARISONS:
                old, new = (_value(cells[v], phase, metric) for v in (reference, candidate))
                available = old is not None and new is not None and (metric != "throughput" or old > 0)
                limit = _budget(metric, old) if available else None
                # Throughput decreases are harmful; all other increases are harmful.
                delta = new - old if available else None
                harmful_delta = -delta if available and metric == "throughput" else delta
                exceeds = harmful_delta > limit if available else None
                valid = bool(available and aa_valid and cells[candidate].get("valid") is True
                             and cells[reference].get("valid") is True)
                rows.append({"block": block, "comparison": f"{candidate}-{reference}",
                             "phase": phase, "metric": metric, "reference": old, "candidate": new,
                             "signed_delta": delta, "harmful_delta": harmful_delta, "budget": limit,
                             "aa_absolute_difference": aa_difference, "aa_half_budget": aa_limit,
                             "aa_valid": aa_valid, "measurement_valid": valid,
                             "observed_exceeds": exceeds,
                             "result": "INCONCLUSIVE" if not valid else "FAIL" if exceeds else "PASS"})

    def aggregate(results):
        return "FAIL" if "FAIL" in results else "INCONCLUSIVE" if "INCONCLUSIVE" in results else "PASS"

    summaries = []
    for comparison in (f"{c}-{r}" for c, r in COMPARISONS):
        for phase, metric in SPECS:
            selected = [r for r in rows if r["comparison"] == comparison and r["phase"] == phase and r["metric"] == metric]
            summaries.append({"comparison": comparison, "phase": phase, "metric": metric,
                              "result": aggregate([r["result"] for r in selected]),
                              "blocks": len(selected), "observed_exceedances": sum(r["observed_exceeds"] is True for r in selected)})
    safety = aggregate([r["result"] for r in safety_rows])
    overall = aggregate([safety] + [r["result"] for r in summaries] + (["INCONCLUSIVE"] if problems else []))
    return {"overall": overall, "safety": safety, "schema_problems": problems,
            "block_indexing": "0..7", "comparisons": [f"{c}-{r}" for c, r in COMPARISONS],
            "summaries": summaries, "paired_rows": rows, "safety_rows": safety_rows,
            "interpretation": "Profiler-free bounded synthetic acceptance only; prior FAILs unchanged."}


def selftest():
    base = {"results": [{"block": b, "variant": v, "valid": True, "functional_pass": True,
            "phases": {"paced": {"p95_ms": 0.5, "p99_ms": 0.8, "cpu_percent": 10,
                       "rss_peak_bytes": 30 * MIB, "errors": 0, "timeouts": 0},
                       "capacity": {"throughput": 1000, "cpu_percent": 90,
                       "rss_peak_bytes": 30 * MIB, "errors": 0, "timeouts": 0}}}
            for b in range(8) for v in ("A1", "A2", "B", "C")]}
    assert judge(base)["overall"] == "PASS"
    failed = copy.deepcopy(base)
    failed["results"][3]["phases"]["paced"]["p95_ms"] = 0.8
    assert judge(failed)["overall"] == "FAIL"
    noisy = copy.deepcopy(failed)
    noisy["results"][1]["phases"]["paced"]["p95_ms"] = 0.7
    result = judge(noisy)
    assert result["overall"] == "INCONCLUSIVE"
    assert any(r["observed_exceeds"] is True and r["result"] == "INCONCLUSIVE" for r in result["paired_rows"])
    noisy["results"][3]["phases"]["paced"]["timeouts"] = 1
    assert judge(noisy)["overall"] == "FAIL"
    missing = copy.deepcopy(base)
    missing["results"].pop()
    assert judge(missing)["overall"] == "INCONCLUSIVE"
    throughput = copy.deepcopy(base)
    throughput["results"][3]["phases"]["capacity"]["throughput"] = 949
    assert judge(throughput)["overall"] == "FAIL"
    return {"result": "PASS", "cases": 6}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        print(json.dumps(selftest()))
        return 0
    if not args.input or not args.output:
        parser.error("--input and --output required")
    report = judge(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"overall": report["overall"], "safety": report["safety"]}))
    return 0  # Collection success is not acceptance; verdict lives in the artifact.


if __name__ == "__main__":
    raise SystemExit(main())
