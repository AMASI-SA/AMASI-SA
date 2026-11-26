"""Compare complete-state copying through real reconcile using synthetic rows.

Run with the existing test environment; this script performs no DB/network I/O:

    PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 python -B \
      docs/operations/OPERATIONAL-REFRESH-SOURCE-ROOT-FIX-20261126/evidence/measure_reconciliation_copy.py

The default 19,143 orders matches an observed source row count, but the generated
57,429 audit events/obligations and 19,143 facts are synthetic, not measured live
history sizes. No field is omitted or shared to reduce the complete-state copy.
Timing is a diagnostic comparison, never a CI threshold or live prediction.
"""
import argparse
from copy import deepcopy
import gc
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "backend"))

from bson import BSON
import operational_balance_engine as engine
import operational_configured_provider_fees as fees
from operational_report_copy import copy_report_value

START = "2026-10-05T09:00:00+00:00"
NOW = "2026-10-08T12:00:00+00:00"


def fixture(records):
    # Same normalized order shape as the existing pure reducer tests.
    def order(index):
        return {"id": str(index), "created_at": "2026-10-06T09:00:00+00:00",
            "updated_at": "2026-10-06T10:00:00+00:00", "currency": "SAR", "status": "in_progress",
            "items": [{"id": "i1", "cost": "100", "quantity": "2", "supplier_id": "s1"}],
            "carrier": {"id": "imile", "cost": "17.25"}, "cod_amount": "500", "order_number": str(index)}

    sources = {"orders": [order(index) for index in range(records)], "provider_fee_settings": {}}
    state = engine.reconcile({"owner_id": "synthetic-profile-owner", "status": "active",
                              "started_at": START}, sources, NOW)
    state["engine"]["facts"].update({f"proof:{index}": {
        "order_id": str(index), "amount": "100.01", "currency": "SAR",
        "evidence": {"source": "synthetic", "parts": [{"amount": "20.01", "reference": str(index)}]}}
        for index in range(records)})
    state["engine"]["provider_fee_inputs"]["orders"] = {str(index): {
        "order_number": str(index), "currency": "SAR", "created_at": START,
        "payment": {"provider": "tabby", "gross": "100.01", "refunds": [{"id": str(index), "amount": "1.01"}]}}
        for index in range(records)}
    state["engine"]["profit_snapshot"] = {"version": 1,
        "cohort": {"orders": records, "pending_orders": 0, "pending_order_details": []},
        "components": {"net_sales": {"amount": "1.00", "complete": True}}}
    state["engine"]["issues"] = [{"code": "synthetic_retained_issue", "source_id": str(index)}
                                  for index in range(1000)]
    return state, sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=19143)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.records <= 20000 or not 1 <= args.repeats <= 5:
        parser.error("records must be 1..20000 and repeats must be 1..5")
    state, sources = fixture(args.records)
    encoded = BSON.encode(state)
    before_hash, state_bytes = sha256(encoded).hexdigest(), len(encoded)
    del encoded
    original_complete, original_engine, original_fees = engine.copy_report_value, engine.deepcopy, fees.deepcopy
    samples = {name: [] for name in ("stdlib", "plain_container_copy")}
    reference = None

    def run(kind):
        timings = {"initial_copy_ms": 0.0, "other_engine_copy_ms": 0.0, "provider_input_copy_ms": 0.0}

        def complete(value):
            started = perf_counter()
            result = copy_report_value(value) if kind == "plain_container_copy" else deepcopy(value)
            timings["initial_copy_ms"] += (perf_counter() - started) * 1000
            return result

        def local(value, *args, **kwargs):
            started = perf_counter()
            result = deepcopy(value, *args, **kwargs)
            timings["other_engine_copy_ms"] += (perf_counter() - started) * 1000
            return result

        def fee(value, *args, **kwargs):
            started = perf_counter()
            result = deepcopy(value, *args, **kwargs)
            timings["provider_input_copy_ms"] += (perf_counter() - started) * 1000
            return result

        engine.copy_report_value, engine.deepcopy, fees.deepcopy = complete, local, fee
        gc.collect()
        try:
            started = perf_counter()
            result = engine.reconcile(state, sources, NOW)
            timings["total_ms"] = (perf_counter() - started) * 1000
        finally:
            engine.copy_report_value, engine.deepcopy, fees.deepcopy = original_complete, original_engine, original_fees
        return result, {key: round(value, 3) for key, value in timings.items()}

    # The real reducer's fixture construction warms imports before both paths.
    for repeat in range(args.repeats):
        kinds = ("stdlib", "plain_container_copy") if repeat % 2 == 0 else ("plain_container_copy", "stdlib")
        for kind in kinds:
            result, timing = run(kind)
            if reference is None:
                reference = result
            else:
                assert result == reference
            assert result["engine"]["audit"][0] is not state["engine"]["audit"][0]
            assert result["engine"]["audit"][0]["after"] is not state["engine"]["audit"][0]["after"]
            assert result["engine"]["facts"]["proof:0"]["evidence"] is not state["engine"]["facts"]["proof:0"]["evidence"]
            samples[kind].append(timing)
            if result is not reference:
                del result
    assert sha256(BSON.encode(state)).hexdigest() == before_hash
    parts = {}
    for key in ("audit", "facts", "provider_fee_inputs", "profit_snapshot", "issues", "obligations"):
        value = state["engine"][key]
        gc.collect()
        started = perf_counter()
        result = deepcopy(value)
        parts[key] = {"copy_ms": round((perf_counter() - started) * 1000, 3),
                      "bson_bytes": len(BSON.encode({"v": value})), "count": len(value)}
        del result
    print(json.dumps({
        "scope": "synthetic real reconcile; no live audit/fact counts known", "orders": args.records,
        "state_bson_bytes": state_bytes, "audit_events": len(state["engine"]["audit"]),
        "facts": len(state["engine"]["facts"]), "obligations": len(state["engine"]["obligations"]),
        "samples": samples, "median_ms": {name: {
            key: median(sample[key] for sample in rows) for key in rows[0]} for name, rows in samples.items()},
        "standalone_field_copy": parts, "logical_results_equal": True, "input_unchanged": True,
        "sampled_nested_history_independent": True,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
