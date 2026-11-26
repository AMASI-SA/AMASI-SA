"""Bounded offline measurement of native-gap/full-state duplicate decoding.

Uses the real native _gaps and archive.unpack functions with synthetic data.
The isolated prototype retains validated decoded packets for one subsequent
unpack of the exact owner/revision/packet. It is an optimistic performance
experiment, not a production implementation or complete safety proof.

No application database, network, environment file, provider, or production
request is used. Inline audit is deliberate: audit pages and authorization
I/O belong to the independent boundary experiment, not this decode comparison.

Run using the existing test environment, with dotenv and bytecode disabled:
  python -B native_decode_reuse_measure.py
"""
from __future__ import annotations

import argparse
import ast
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import socket
import statistics
import subprocess
import sys
import threading
import time
import tracemalloc


HERE = Path(__file__).resolve()
ROOT = HERE.parents[4]
OUTPUT = HERE.with_name("native_decode_reuse_measure_result.json")
OWNER = "synthetic-native-decode-owner"
MIB = 1024 * 1024
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "backend"))


def deny_connect(self, address):
    raise AssertionError("This benchmark has no network capability")


socket.socket.connect = deny_connect
socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(
    AssertionError("This benchmark has no network capability")
)

import bson
from bson import BSON, Decimal128
import pymongo

import operational_balance_archive as archive
from operational_balance_store import initial
from operational_demand_refresh import DemandRefreshContext, _CURRENT, run_calculation
import operational_visible_evidence as native


def runtime_hashes():
    return {
        name: hashlib.sha256((ROOT / "backend" / name).read_bytes()).hexdigest()
        for name in (
            "operational_balance_archive.py", "operational_visible_evidence.py",
            "operational_balance_store.py", "operational_demand_refresh.py",
        )
    }


def fixture(records):
    state = initial(OWNER)
    state.update(status="active", started_at="2026-11-01T00:00:00+03:00", revision=16949)
    engine = state["engine"] = {
        "as_of": "2026-11-26T01:51:00+03:00", "audit": [], "issues": [],
        "facts": {}, "obligations": {}, "delivery_register": {},
        "provider_fee_inputs": {}, "order_versions": {}, "cod_reports": {},
        "provider_reports": {}, "profit_snapshot": {"version": 1, "complete": False},
        "customer_tender_coverage": {"sar": {"proof_lookups": []}},
    }
    for i in range(records):
        number = str(100000 + i)
        identity = "order-" + number
        payment_key, delivery_key = "payment:" + identity, "delivery_event:" + identity
        date = "2026-11-25T19:21:00+03:00"
        version = "2026-11-26T01:48:00+03:00"
        engine["facts"][payment_key] = {
            "id": payment_key, "order_id": identity, "order_number": number,
            "party_type": "payment_provider", "party_id": "provider-" + str(i % 4),
            "amount": "142.50", "currency": "SAR", "occurred_at": date,
            "source": "salla_direct", "recognition_state": "confirmed",
            "evidence": {"source_updated_at": version, "native_id": "receipt-" + number,
                         "legs": [{"method": "mada", "amount": "142.50", "currency": "SAR"}]},
        }
        engine["facts"][delivery_key] = {
            "id": delivery_key, "order_id": identity, "order_number": number,
            "party_type": "courier", "party_id": "courier-" + str(i % 3),
            "amount": "18.50", "currency": "SAR", "occurred_at": date,
            "source": "native_delivery", "recognition_state": "confirmed",
            "evidence": {"shipment_id": "shipment-" + number, "source_updated_at": version,
                         "history": [{"status": "delivered", "date": date, "source_id": identity}]},
        }
        for kind, amount in (("courier", "18.50"), ("supplier", "72.25")):
            key = kind + ":" + identity
            engine["obligations"][key] = {
                "id": key, "party_type": kind, "party_id": kind + "-" + str(i % 41),
                "name": "Synthetic " + kind, "currency": "SAR",
                "confirmed": amount, "expected": "0.00", "paid": "0.00",
                "remaining": amount, "source_ids": [payment_key, delivery_key],
                "original_date": date, "order_number": number,
                "components": [
                    {"kind": "principal", "amount": amount, "quantity": 2},
                    {"kind": "tax", "amount": "0.00", "quantity": 1},
                ],
            }
        engine["delivery_register"][identity] = {
            "order_id": identity, "order_number": number, "source_version": version,
            "first_delivered_at": date, "party_type": "courier",
            "party_id": "courier-" + str(i % 3),
            "events": [{"status": "delivered", "occurred_at": date,
                        "shipment_id": "shipment-" + number}],
        }
        engine["provider_fee_inputs"][identity] = {
            "order_id": identity, "gross": "142.50", "currency": "SAR",
            "provider": "provider-" + str(i % 4), "method": "mada",
            "source_version": version, "rate": Decimal128("0.0175"),
            "calculation": {"percentage": "2.49", "fixed": "1.00", "tax": "0.52"},
        }
        engine["order_versions"][identity] = {"version": version, "order_number": number}
        if i % 5 == 0:
            engine["issues"].append({
                "code": "operational_delivery_timestamp_missing", "source_id": identity,
                "order_number": number, "party_type": "courier",
                "details": {"component": "shipping_fee", "source_version": version,
                            "required_action": "synthetic_review"},
            })
        if i % 40 == 0:
            engine["customer_tender_coverage"]["sar"]["proof_lookups"].append(
                {"order_id": identity, "order_number": number}
            )
    state["movements"] = [{
        "id": "synthetic-retained-movement", "amount": "100.01", "currency": "SAR",
        "allocations": [{"obligation_id": "supplier:order-100000", "amount": "100.01"}],
    }]
    state["courier_tariffs"] = {"version": 1, "contracts": []}
    return state


def native_projection(stored):
    # The native query does not fetch audit history or the full aggregate.
    # revision is the sole extra metadata field the proposed handoff would need.
    fields = ("issues", "customer_tender_coverage", "facts", "obligations", "delivery_register")
    return {
        "owner_id": stored["owner_id"], "revision": stored["revision"],
        "status": stored["status"], "started_at": stored["started_at"],
        "engine": {key: deepcopy(stored["engine"][key]) for key in fields},
        "movements": [{"allocations": deepcopy(row["allocations"])} for row in stored["movements"]],
        "courier_tariffs": deepcopy(stored["courier_tariffs"]),
        archive.SNAPSHOT: deepcopy(stored[archive.SNAPSHOT]),
        archive.ISSUES: deepcopy(stored[archive.ISSUES]),
    }


class NoDatabase:
    def __getitem__(self, name):
        raise AssertionError("No database read is admitted by this decode-only measurement")


class NoDisconnect:
    async def is_disconnected(self):
        return False


def rss_bytes():
    return int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")


class Probe:
    def __init__(self, prototype, projected):
        self.prototype, self.projected = prototype, projected
        self.phase, self.captured, self.validated = "native", {}, None
        self.counts = {"native": 0, "full": 0}
        self.decode_ms = {"native": 0.0, "full": 0.0}
        self.decode_cpu_ms = {"native": 0.0, "full": 0.0}
        self.hit, self.original_decode, self.original_unpack = False, archive.decode_bson, archive._unpack_packets

    def decode(self, data):
        started, cpu = time.perf_counter(), time.thread_time()
        result = self.original_decode(data)
        self.decode_ms[self.phase] += (time.perf_counter() - started) * 1000
        self.decode_cpu_ms[self.phase] += (time.thread_time() - cpu) * 1000
        self.counts[self.phase] += 1
        if self.prototype and self.phase == "native":
            self.captured["snapshot" if "fields" in result else "issues"] = result
        return result

    def accept_native(self):
        if self.prototype:
            # Publication happens only after both original native validators return.
            assert set(self.captured) == {"snapshot", "issues"}
            self.validated, self.captured = self.captured, {}

    def unpack(self, state):
        if not self.prototype:
            return self.original_unpack(state)
        payload, self.validated = self.validated, None  # Consume once, including a miss.
        if (payload is None or state.get("owner_id") != self.projected.get("owner_id")
                or type(state.get("revision")) is not int
                or state["revision"] != self.projected["revision"]
                or state.get(archive.SNAPSHOT) != self.projected[archive.SNAPSHOT]
                or state.get(archive.ISSUES) != self.projected[archive.ISSUES]):
            return self.original_unpack(state)
        # Fresh markers/inline data still belong to this full state. Exact packet
        # equality binds all packet metadata and bytes to the validated decode.
        engine = state.get("engine", {})
        fields = state[archive.SNAPSHOT]["fields"]
        marked = {key for key in archive.SNAPSHOT_FIELDS if engine.get(key) == archive.SNAPSHOT_MARK}
        if marked != set(fields):
            raise archive.ArchiveError("financial snapshot marker mismatch")
        issues = engine.get("issues", [])
        if not isinstance(issues, list) or issues[:1] != [archive.ISSUE_MARK]:
            raise archive.ArchiveError("invalid diagnostic packet")
        archive._checkpoint()
        result = {key: value for key, value in state.items() if key not in (archive.SNAPSHOT, archive.ISSUES)}
        result["engine"] = {
            **engine, **payload["snapshot"]["fields"],
            "issues": payload["issues"]["issues"] + issues[1:],
        }
        archive._checkpoint()
        self.hit = True
        return result

    @contextmanager
    def installed(self):
        archive.decode_bson, archive._unpack_packets = self.decode, self.unpack
        try:
            yield
        finally:
            archive.decode_bson, archive._unpack_packets = self.original_decode, self.original_unpack
            self.captured.clear()
            self.validated = None


async def child(variant, records, trace):
    hashes_before = runtime_hashes()
    expected = fixture(records)
    expected_bytes = BSON.encode(expected)
    expected_sha = hashlib.sha256(expected_bytes).hexdigest()
    expected_native = native_projection(archive.compact_issues(
        archive.compact_financial_snapshot(expected, 12 * MIB), 12 * MIB))
    stored = archive.compact_issues(
        archive.compact_financial_snapshot(expected, 12 * MIB), 12 * MIB)
    packet_size = stored[archive.SNAPSHOT]["size"]
    if not 31 * MIB <= packet_size <= 54 * MIB:
        raise ValueError(f"Dense fixture packet outside31..54MiB: {packet_size}")
    packed_bytes = BSON.encode(stored)
    stored_sha = hashlib.sha256(packed_bytes).hexdigest()
    # Two separately decoded documents model the two existing snapshot reads;
    # both are prepared outside the measured section.
    projected = BSON.encode(expected_native).decode()
    fresh_stored = packed_bytes.decode()
    native_reference = native._gaps(expected_native)
    native_sha = hashlib.sha256(BSON.encode(native_reference)).hexdigest()
    fixture_info = {
        "orders": records, "facts": 2 * records, "obligations": 2 * records,
        "financial_packet_bytes": packet_size,
        "financial_packet_compressed_bytes": len(stored[archive.SNAPSHOT]["data"]),
        "issue_packet_bytes": stored[archive.ISSUES]["size"],
        "issue_packet_compressed_bytes": len(stored[archive.ISSUES]["data"]),
        "full_logical_bson_bytes": len(expected_bytes),
    }
    del expected, expected_bytes, expected_native, native_reference, stored, packed_bytes

    db = NoDatabase()
    gates = 0
    async def verify():
        nonlocal gates
        gates += 1
    context = DemandRefreshContext(NoDisconnect(), db, OWNER, OWNER, verify)
    token = _CURRENT.set(context)
    await run_calculation(lambda: None)  # Warm the thread, not the measured codecs.
    gates = 0
    probe = Probe(variant == "prototype", projected)
    samples, stopped = [], threading.Event()
    def sample_rss():
        while not stopped.is_set():
            samples.append(rss_bytes())
            stopped.wait(.005)
    sampler = threading.Thread(target=sample_rss, daemon=False)
    collections, gc_started, gc_ms = [0, 0, 0], {}, [0.0]
    def gc_callback(phase, info):
        gen = info["generation"]
        if phase == "start":
            gc_started[gen] = time.perf_counter()
        else:
            collections[gen] += 1
            gc_ms[0] += (time.perf_counter() - gc_started.pop(gen, time.perf_counter())) * 1000
    before_rss = rss_bytes()
    high_water_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    gc_before = gc.get_count()
    phase_memory = {}
    try:
        with probe.installed():
            if trace:
                tracemalloc.start()
            gc.callbacks.append(gc_callback)
            sampler.start()
            started, cpu = time.perf_counter(), time.process_time()
            native_started = time.perf_counter()
            gaps = await run_calculation(native._gaps, projected)
            native_ms = (time.perf_counter() - native_started) * 1000
            probe.accept_native()
            if trace:
                phase_memory["after_native_view"] = list(tracemalloc.get_traced_memory())
            native_actual_sha = None
            # Keep parity hashing outside all timing and allocation measurements.
            native_parity_values = (gaps["owner_id"], len(gaps["engine"]["facts"]),
                                    len(gaps["engine"]["obligations"]), len(gaps["engine"]["issues"]))
            del gaps
            if trace:
                phase_memory["after_native_view_released"] = list(tracemalloc.get_traced_memory())
            rss_between = rss_bytes()
            probe.phase = "full"
            full_started = time.perf_counter()
            logical = await archive.unpack(db, OWNER, fresh_stored)
            full_ms = (time.perf_counter() - full_started) * 1000
            elapsed_ms = (time.perf_counter() - started) * 1000
            cpu_ms = (time.process_time() - cpu) * 1000
            if trace:
                phase_memory["after_full_unpack"] = list(tracemalloc.get_traced_memory())
            after_rss = rss_bytes()
            high_water_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
            gc_after = gc.get_count()
            gc.callbacks.remove(gc_callback)
            stopped.set()
            sampler.join()
            if trace:
                tracemalloc.stop()
        # Full BSON equality includes values, key order, and BSON types.
        assert hashlib.sha256(BSON.encode(logical)).hexdigest() == expected_sha
        assert hashlib.sha256(BSON.encode(fresh_stored)).hexdigest() == stored_sha
        assert type(logical["engine"]["provider_fee_inputs"]["order-100000"]["rate"]) is Decimal128
        # Independently verify the native projection outside the measurement.
        native_actual_sha = hashlib.sha256(BSON.encode(native._gaps(projected))).hexdigest()
        assert native_actual_sha == native_sha
        assert native_parity_values == (OWNER, records * 2, records * 2, (records + 4) // 5)
        assert probe.counts == ({"native": 2, "full": 0} if variant == "prototype"
                                else {"native": 2, "full": 2})
        assert probe.hit is (variant == "prototype")
        assert runtime_hashes() == hashes_before
        return {
            "variant": variant, "measurement": "memory_only" if trace else "timing",
            "fixture": fixture_info, "native_ms": round(native_ms, 3),
            "full_unpack_ms": round(full_ms, 3), "pair_ms": round(elapsed_ms, 3),
            "process_cpu_ms": round(cpu_ms, 3), "decode_counts": probe.counts,
            "decode_wall_ms": {k: round(v, 3) for k, v in probe.decode_ms.items()},
            "decode_thread_cpu_ms": {k: round(v, 3) for k, v in probe.decode_cpu_ms.items()},
            "gc_collections": collections, "gc_ms": round(gc_ms[0], 3),
            "gc_count_before": gc_before, "gc_count_after": gc_after,
            "rss_before": before_rss, "rss_between_stages": rss_between, "rss_after": after_rss,
            "sampled_peak_rss": max([before_rss, rss_between, after_rss, *samples]),
            "process_high_water_before": high_water_before, "process_high_water_after": high_water_after,
            "traced_memory_current_peak_by_phase": phase_memory,
            "authorization_callbacks": gates, "handoff_hit": probe.hit,
            "native_bson_sha256": native_sha, "full_bson_sha256": expected_sha,
            "stored_bson_sha256": stored_sha, "parity": True, "inputs_unchanged": True,
        }
    finally:
        if gc_callback in gc.callbacks:
            gc.callbacks.remove(gc_callback)
        stopped.set()
        if sampler.ident is not None:
            sampler.join()
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        _CURRENT.reset(token)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", choices=("baseline", "prototype"))
    parser.add_argument("--memory", action="store_true")
    parser.add_argument("--records", type=int, default=19151)
    args = parser.parse_args()
    if not 16000 <= args.records <= 20000:
        parser.error("records must be16000..20000")
    if args.child:
        print(json.dumps(asyncio.run(child(args.child, args.records, args.memory)), sort_keys=True))
        return
    assert not OUTPUT.exists(), "Existing evidence is retained; do not overwrite or silently rerun"
    ast.parse(HERE.read_text())
    hashes = runtime_hashes()
    runs = []
    for index, (variant, memory) in enumerate((
        ("baseline", False), ("prototype", False), ("prototype", False),
        ("baseline", False), ("baseline", True), ("prototype", True),
    )):
        argv = [sys.executable, "-B", str(HERE), "--child", variant, "--records", str(args.records)]
        if memory:
            argv.append("--memory")
        completed = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=180)
        if completed.returncode:
            raise RuntimeError(f"Offline child{index} failed: {completed.stderr[-2500:]}")
        runs.append(json.loads(completed.stdout))
        print(f"Completed offline {variant} {'memory' if memory else 'timing'} sample{index + 1}", flush=True)
    assert runtime_hashes() == hashes
    assert len({row["full_bson_sha256"] for row in runs}) == 1
    assert len({row["native_bson_sha256"] for row in runs}) == 1
    timings = {
        variant: [row["pair_ms"] for row in runs if row["variant"] == variant and row["measurement"] == "timing"]
        for variant in ("baseline", "prototype")
    }
    medians = {key: statistics.median(values) for key, values in timings.items()}
    result = {
        "scope": "Synthetic native _gaps then full archive.unpack; inline audit; no DB/provider/network",
        "prototype_limit": "Optimistic one-use validated-packet experiment only; no production implementation or cancellation/fallback safety claim",
        "environment_limit": "Local elapsed times do not estimate production native_evidence or snapshot_unpack; real authorization/audit/network costs excluded",
        "method": "Four fresh-process A/B/B/A timing runs, then two separate tracing runs; normal GC; no forced collection or GC disabling",
        "memory_note": "RSS samples can miss GIL-held transients; separate tracemalloc runs measure live/peak Python allocations but their timings are not compared",
        "python": platform.python_version(), "pymongo": pymongo.version, "bson_c_extension": bson.has_c(),
        "script_sha256": hashlib.sha256(HERE.read_bytes()).hexdigest(),
        "runtime_hashes_before_and_after": hashes, "runs": runs, "timing_samples_ms": timings,
        "median_pair_ms": medians,
        "median_pair_ms_saved": medians["baseline"] - medians["prototype"],
        "all_full_and_native_bson_parity": True, "all_stored_inputs_unchanged": True,
    }
    with OUTPUT.open("x") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"result": str(OUTPUT), "median_pair_ms": medians,
                      "saved_ms": result["median_pair_ms_saved"]}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
