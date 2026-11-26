"""Measure the real packet reader using synthetic data only; no I/O or DB.

Run from any directory with the repository's existing Python environment:

    PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 python -B \
      docs/operations/OPERATIONAL-REFRESH-SOURCE-ROOT-FIX-20261126/evidence/measure_archive_decode.py

The legacy comparison adds only the former BSON(bytes) wrapper. Both variants
use the public default BSON decoder and the complete _unpack_packets helper.
This excludes audit page I/O, authorization and the rest of live snapshot_unpack.
Timing is diagnostic evidence, never a CI threshold or a production prediction.
"""
import argparse
import gc
from hashlib import sha256
import json
from pathlib import Path
import platform
from statistics import median
import sys
from time import perf_counter
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "backend"))

import bson
from bson import BSON, Decimal128
import pymongo

import operational_balance_archive as archive
from operational_balance_store import initial


def fixture(records):
    state = initial("synthetic-decode-owner")
    state.update(status="active", started_at="2026-11-01T00:30:00+03:00", revision=7)
    state["engine"] = {
        "as_of": "2026-11-12T01:07:44+03:00", "audit": [], "issues": [],
        "facts": {
            f"proof:{i}": {"order_id": str(i), "amount": "100.01",
                            "proof": "source evidence " * 180, "typed": Decimal128("100.01")}
            for i in range(records)
        },
        "obligations": {
            f"shipping:{i}": {"id": f"shipping:{i}", "confirmed": "20.01", "currency": "SAR"}
            for i in range(90)
        },
        "provider_fee_inputs": {"version": 1, "orders": {
            str(i): {"gross": "100.01", "data": "known source " * 100} for i in range(records)
        }},
        "salary_days": {"salary:employee:2026-11-01": "100.00"},
    }
    state["movements"] = [{"id": "retained", "amount": "97.01", "allocation": "provider:o"}]
    state["requests"] = {"retry": {"id": "retained"}}
    state["openings"] = {"bank": {"amount": "300.01"}}
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=7200)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.records <= 12000 or not 1 <= args.repeats <= 5:
        parser.error("records must be 1..12000 and repeats must be 1..5")

    expected = fixture(args.records)
    stored = archive.compact_financial_snapshot(expected, 12 * 1024 * 1024)
    packet = stored[archive.SNAPSHOT]
    before = sha256(BSON.encode(stored)).hexdigest()
    direct_decode = archive.decode_bson
    readers = {"legacy_wrapper": lambda data: BSON(data).decode(), "direct_public": direct_decode}
    elapsed = {name: [] for name in readers}

    def run(reader, *, trace=False):
        gc.collect()
        archive.decode_bson = reader
        if trace:
            tracemalloc.start()
        try:
            started = perf_counter()
            logical = archive._unpack_packets(stored)
            milliseconds = (perf_counter() - started) * 1000
            peak = tracemalloc.get_traced_memory()[1] if trace else None
        finally:
            if trace:
                tracemalloc.stop()
            archive.decode_bson = direct_decode
        assert logical == expected, "logical financial evidence changed"
        assert type(logical["engine"]["facts"]["proof:0"]["typed"]) is Decimal128
        return milliseconds, peak

    # Warm lazy imports before measuring either implementation. Alternate the
    # timing order so the baseline is not always measured with a colder heap.
    for reader in readers.values():
        run(reader)
    for repeat in range(args.repeats):
        names = list(readers) if repeat % 2 == 0 else list(reversed(readers))
        for name in names:
            milliseconds, _ = run(readers[name])
            elapsed[name].append(round(milliseconds, 3))
    peaks = {name: run(reader, trace=True)[1] for name, reader in readers.items()}
    assert sha256(BSON.encode(stored)).hexdigest() == before, "stored packet changed"

    print(json.dumps({
        "fixture": "synthetic_only",
        "python": platform.python_version(), "pymongo": pymongo.version,
        "bson_c_extension": bson.has_c(), "records": args.records,
        "uncompressed_packet_bytes": packet["size"], "compressed_packet_bytes": len(packet["data"]),
        "samples_ms": elapsed, "median_ms": {name: median(samples) for name, samples in elapsed.items()},
        "traced_peak_bytes": peaks,
        "traced_peak_bytes_eliminated": peaks["legacy_wrapper"] - peaks["direct_public"],
        "logical_values_and_types_equal": True, "stored_packet_unchanged": True,
        "scope": "_unpack_packets only; excludes audit I/O, authorization and live source reads",
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
