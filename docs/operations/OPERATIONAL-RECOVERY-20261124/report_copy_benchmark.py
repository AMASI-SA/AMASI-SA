"""Pure synthetic same-process comparison; no database, startup or network."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import statistics
import sys
import time


def forbidden(*_args, **_kwargs):
    raise RuntimeError("Report copy benchmark forbids network connections")


os.environ["PYTHON_DOTENV_DISABLED"] = "1"
socket.socket.connect = socket.create_connection = forbidden
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]

from test_operational_provider_report_profile_memo import repeated_state, VIEWS
from test_operational_balance_cod_projection_performance import cod_fixture
from operational_financial_position import build_financial_position
import operational_report_copy as copying


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def main():
    # Reuse the existing monthly benchmark's approved pure fixtures unchanged.
    state, cod = repeated_state(4000), cod_fixture(movements=10000, claims=5000)
    state["movements"].extend(cod["movements"])
    state["openings"].update(cod["openings"])
    state["engine"]["obligations"].update(cod["engine"]["obligations"])
    state["engine"]["cod_reports"] = cod["engine"]["cod_reports"]
    original = encoded(state)
    candidate = copying.copy_report_value
    results, parity = {"standard": [], "candidate": []}, []
    try:
        for view in VIEWS:
            copying.copy_report_value = deepcopy
            expected = encoded(build_financial_position(state, view=view))
            copying.copy_report_value = candidate
            assert encoded(build_financial_position(state, view=view)) == expected
            assert encoded(state) == original
            parity.append(view)
        for round_number in range(5):
            order = ("standard", "candidate") if round_number % 2 == 0 else ("candidate", "standard")
            for mode in order:
                copying.copy_report_value = deepcopy if mode == "standard" else candidate
                started, cpu_started = time.perf_counter(), time.process_time()
                result = build_financial_position(state, view="providers")
                results[mode].append({"wall_ms": round((time.perf_counter()-started)*1000, 3),
                    "cpu_ms": round((time.process_time()-cpu_started)*1000, 3)})
                assert encoded(state) == original
        copy_stages = {}
        for mode, copier in (("standard", deepcopy), ("candidate", candidate)):
            stages = []
            def observed(value):
                started = time.perf_counter()
                result = copier(value)
                stages.append({"type": type(value).__name__, "length": len(value),
                    "ms": round((time.perf_counter()-started)*1000, 3)})
                return result
            copying.copy_report_value = observed
            build_financial_position(state, view="providers")
            copy_stages[mode] = stages
        print(json.dumps({"synthetic_only": True, "input_bytes": len(original),
            "input_sha256": hashlib.sha256(original).hexdigest(),
            "counts": {"provider_orders": 4000, "cod_claims": 5000, "movements": 10000},
            "input_unchanged": encoded(state) == original, "byte_identical_views": parity,
            "samples": results, "medians": {mode: {key: statistics.median(row[key] for row in values)
                for key in ("wall_ms", "cpu_ms")} for mode, values in results.items()},
            "copy_stages": copy_stages}, sort_keys=True))
    finally:
        copying.copy_report_value = candidate


if __name__ == "__main__":
    main()
