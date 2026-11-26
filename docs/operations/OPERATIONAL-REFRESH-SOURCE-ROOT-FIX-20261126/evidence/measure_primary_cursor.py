"""One bounded, offline, real-Motor primary-read experiment.

The separate loopback responder pre-encodes synthetic BSON before admitting a
client. It is not MongoDB: no database, provider, filesystem-backed database,
production configuration, source mutation or financial writer is used. Counts
approximate the observed order cohort; payload byte/object distributions are
explicit synthetic assumptions, not measurements of production documents.

Four fresh clients compare unchanged four-way admission with an orders-first
schedule (A/B/B/A). Both retain every source row and all proof containers under
the existing per-read 15-second budget, 20,001 sentinel and query/projection.
The schedule is experimental code here only; no runtime patch is installed.
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import json
import os
from pathlib import Path
import resource
import selectors
import socketserver
import struct
import subprocess
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[4]
ORDER_COUNT = 19200
OTHER_COUNT = 4800
SNAPSHOT_COUNT = 38400
MAX_BATCH_BYTES = 15 * 1024 * 1024  # Conservative synthetic reply, below 16 MiB.
OTHER_NAMES = (
    "mezan_preparation_pieces_v1", "store_delivery_assignments", "store_delivery_collections",
)
NAMES = ("unified_orders", *OTHER_NAMES)
OWNER = "synthetic-owner"
DBNAME = "owned_primary_wire"
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]


def order(index):
    # All names, receipts and identifiers are generated, never copied from a
    # merchant. Complete nested payment/shipping/item evidence is retained.
    oid, number = str(index + 1), str(index + 100000)
    raw = {
        "id": oid, "reference_id": number, "currency": "SAR",
        "created_at": "2026-11-01T09:00:00+00:00", "updated_at": "2026-11-25T20:00:00+00:00",
        "status": {"id": "delivered", "slug": "delivered", "name": "Synthetic delivered"},
        "payment_method": "mada", "payment": {"method": "mada", "status": "paid",
            "amount": {"amount": 345.0, "currency": "SAR"}, "refunds": [],
            "transactions": [{"id": "tx-" + oid, "type": "payment", "amount": "345.00",
                "currency": "SAR", "status": "paid", "created_at": "2026-11-01T09:01:00Z"}]},
        "amounts": {"total": {"amount": 345.0, "currency": "SAR"},
            "sub_total": {"amount": 300.0, "currency": "SAR"},
            "tax": {"percent": "15", "amount": {"amount": 45.0, "currency": "SAR"}}},
        "shipping": {"shipment_id": "shipment-" + oid, "tracking_number": "track-" + oid,
            "company": "Synthetic courier", "status": "delivered", "cost": {"amount": 30.0, "currency": "SAR"}},
        "shipments": [{"id": "shipment-" + oid, "tracking_number": "track-" + oid,
            "status": "delivered", "history": [{"status": "in_transit", "at": "2026-11-24T10:00:00Z"},
                {"status": "delivered", "at": "2026-11-25T20:00:00Z"}]}],
        "customer": {"id": "customer-" + oid, "name": "Synthetic Customer",
            "address": {"country": "SA", "city": "Synthetic City", "street": "Synthetic street"}},
        "items": [{"id": f"{oid}-{item}", "product_id": str(item + 1), "quantity": 1,
            "name": "Synthetic product", "price": {"amount": 75.0, "currency": "SAR"},
            "product": {"id": str(item + 1), "name": "Synthetic product", "options": [],
                "images": [{"url": "https://example.invalid/synthetic-" + str(item), "alt": "Synthetic"}]},
            "variant": {"id": str(item + 11), "options": [{"id": "color", "value": "blue"}]},
            "options": [{"id": f"option-{option}", "name": "Synthetic option",
                "values": [{"id": f"value-{option}", "name": "Synthetic choice", "price": 0.0}],
                "metadata": {"required": True, "kind": "choice", "source": "synthetic"}}
                for option in range(3)],
            "custom_fields": [{"key": "synthetic-note", "value": "synthetic-value"}],
            "taxes": [{"name": "VAT", "percent": "15", "amount": "11.25"}]}
            for item in range(4)],
    }
    return {"user_id": OWNER, "order_id": oid, "salla_order_id": oid, "order_number": number,
        "raw_by_source": {"salla_direct": raw,
            "other_native_source": {"proof": [None, {"shape": "retained", "values": [1, "2", False]}]}},
        "g47_salla_snapshot": {"requires_authoritative_refresh": False,
            "source_updated_at": "2026-11-25T20:00:00+00:00", "component_pending": False},
        "salla_shipping_current": {"shipment_id": "shipment-" + oid, "tracking_number": "track-" + oid,
            "company_code": "synthetic", "company_name": "Synthetic courier", "status": "delivered"},
        "salla_paid_sar_evidence": {"source": "synthetic", "amount": "345.00", "currency": "SAR"},
        "salla_first_delivered": {"order_id": oid, "observed_at": "2026-11-25T20:00:00+00:00"},
        "salla_operational_return_waybills": []}


def other_row(name, index):
    return {"user_id": OWNER, "id": f"{name}-{index}", "order_number": str(index + 100000),
        "status": "delivered", "active": True, "amount": "30.00", "currency": "SAR",
        "native_evidence": {"source": "synthetic", "version": 1,
            "items": [{"id": f"item-{item}", "quantity": 1,
                "allocations": [{"id": f"allocation-{j}", "amount": "7.50", "currency": "SAR",
                    "proof": {"state": "approved", "reference": f"proof-{index}-{item}-{j}"}}
                    for j in range(4)]} for item in range(4)]}}


def snapshot_bytes():
    from bson import encode
    return encode({"owner_id": OWNER, "revision": 7,
        "obligations": [{"id": f"obligation-{i}", "party_type": "store_driver",
            "party_id": "synthetic-driver", "source_id": str(i % ORDER_COUNT + 1),
            "amount": "30.00", "remaining": "30.00", "currency": "SAR",
            "evidence": {"native": {"type": "synthetic", "confirmed_at": "2026-11-25T20:00:00Z",
                "binding": {"order_id": str(i % ORDER_COUNT + 1), "assignment_id": f"assignment-{i}"}},
                "versions": [{"id": str(j), "kind": "synthetic", "amount": "30.00"} for j in range(3)]}}
            for i in range(SNAPSHOT_COUNT)],
        "engine": {"facts": {f"fact-{i}": {"source": "synthetic", "amount": "30.00",
            "date": "2026-11-25", "proof": {"identity": str(i), "sequence": [i, i + 1]}}
            for i in range(ORDER_COUNT)}}})


def encode_batches(name):
    from bson import encode
    rows, row_bytes, batches, digest = [], 0, [], hashlib.sha256()
    count = ORDER_COUNT if name == "unified_orders" else OTHER_COUNT
    cursor_id = 71 + NAMES.index(name)
    for index in range(count):
        row = order(index) if name == "unified_orders" else other_row(name, index)
        encoded_row = encode(row)
        digest.update(encoded_row)
        row_bytes += len(encoded_row)
        rows.append(row)
        if (not batches and len(rows) == 101) or row_bytes >= MAX_BATCH_BYTES or index == count - 1:
            field = "firstBatch" if not batches else "nextBatch"
            payload = encode({"ok": 1.0, "cursor": {"id": cursor_id if index < count - 1 else 0,
                "ns": DBNAME + "." + name, field: rows}})
            assert len(payload) < 16 * 1024 * 1024
            batches.append(payload)
            rows, row_bytes = [], 0
    return batches, digest.hexdigest()


def wire_main():
    from bson import decode, encode
    from operational_balance_sources import REFRESH_ORDER_FIELDS
    batches, expected = {}, {}
    for name in NAMES:
        batches[name], expected[name] = encode_batches(name)
    metadata = {"order_count": ORDER_COUNT, "other_rows_per_collection": OTHER_COUNT,
        "batch_bytes": {name: [len(batch) for batch in value] for name, value in batches.items()},
        "expected_sha256": expected, "fixture_first_batch_rows": 101,
        "fixture_max_batch_bytes": MAX_BATCH_BYTES, "production_payload_distribution_known": False}
    handshake = encode({"ok": 1.0, "ismaster": True, "isWritablePrimary": True, "helloOk": True,
        "minWireVersion": 0, "maxWireVersion": 25, "logicalSessionTimeoutMinutes": 30,
        "maxBsonObjectSize": 16777216, "maxMessageSizeBytes": 48000000, "maxWriteBatchSize": 100000})
    okay = encode({"ok": 1.0})
    state, lock = {}, threading.Lock()

    class Handler(socketserver.BaseRequestHandler):
        def read(self, count):
            chunks = []
            while count:
                piece = self.request.recv(count)
                if not piece:
                    raise EOFError
                chunks.append(piece)
                count -= len(piece)
            return b"".join(chunks)

        def handle(self):
            self.request.settimeout(45)
            try:
                while True:
                    length, request_id, _, opcode = struct.unpack("<iiii", self.read(16))
                    assert 16 <= length <= 1024 * 1024
                    payload = self.read(length - 16)
                    if opcode == 2004:
                        end = payload.index(b"\0", 4)
                        command = decode(payload[end + 9:])
                    else:
                        assert opcode == 2013 and payload[4] == 0
                        command = decode(payload[5:])
                    name = next(iter(command)).lower()
                    if name in {"ismaster", "hello"}:
                        body = handshake
                    elif name == "find":
                        collection = command["find"]
                        assert collection in NAMES and command["filter"] == {"user_id": OWNER}
                        assert "batchSize" not in command and "sort" not in command and "limit" not in command
                        if collection == "unified_orders":
                            assert command["projection"] == {"_id": 0, **dict.fromkeys(REFRESH_ORDER_FIELDS, 1)}
                        else:
                            assert "projection" not in command
                        assert 0 < command["maxTimeMS"] <= 15000
                        with lock:
                            state[collection] = 1
                        body = batches[collection][0]
                    elif name == "getmore":
                        collection = command["collection"]
                        assert command["getMore"] == 71 + NAMES.index(collection)
                        with lock:
                            offset = state[collection]
                            state[collection] += 1
                        body = batches[collection][offset]
                    elif name == "killcursors":
                        body = encode({"ok": 1.0, "cursorsKilled": command["cursors"],
                            "cursorsUnknown": [], "cursorsAlive": [], "cursorsNotFound": []})
                    elif name in {"ping", "endsessions"}:
                        body = okay
                    else:
                        raise AssertionError("unexpected synthetic command " + name)
                    prefix = struct.pack("<iqii", 0, 0, 0, 1) if opcode == 2004 else struct.pack("<iB", 0, 0)
                    self.request.sendall(struct.pack("<iiii", len(prefix) + len(body) + 16, 1,
                        request_id, 1 if opcode == 2004 else 2013) + prefix)
                    self.request.sendall(body)
            except (EOFError, ConnectionError, TimeoutError):
                return

    class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
        daemon_threads = True
        allow_reuse_address = False

    with Server(("127.0.0.1", 0), Handler) as server:
        print(json.dumps({"port": server.server_address[1], "fixture": metadata}), flush=True)
        server.serve_forever(poll_interval=.1)


def current_rss_kib():
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    return None


async def client_main(port, schedule):
    import bson
    import motor
    import pymongo
    from motor.frameworks import asyncio as motor_asyncio
    from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCursor
    from pymongo.synchronous.cursor import Cursor
    from pymongo.synchronous.pool import Connection
    import operational_demand_refresh as demand
    from operational_balance_sources import refresh_source_rows
    from operational_read_runtime import load_collections, SourceReadError

    # Every client starts in a new process. Snapshot preparation and the
    # connection handshake precede measurements and add no simulated DB reads.
    encoded_snapshot = snapshot_bytes()
    resident = bson.decode(encoded_snapshot)
    snapshot_size = len(encoded_snapshot)
    del encoded_snapshot
    client = AsyncIOMotorClient(f"mongodb://127.0.0.1:{port}/?directConnection=true",
        connect=False, serverSelectionTimeoutMS=2000, connectTimeoutMS=2000,
        maxPoolSize=20, waitQueueTimeoutMS=2000)
    await client.admin.command("ping")
    database = client[DBNAME]
    context = demand.DemandRefreshContext(None, database, OWNER, "synthetic-actor", None)
    token = demand._CURRENT.set(context)
    events, gc_events, pending_gc, lags = [], [], {}, []
    operations = []
    original_receive, original_unpack = Connection.receive_message, Cursor._unpack_response
    original_refresh, original_more = Cursor._refresh, AsyncIOMotorCursor._get_more
    original_executor = motor_asyncio.run_on_executor

    def timed(label, function):
        def observed(instance, *args, **kwargs):
            started, cpu = time.perf_counter(), time.thread_time()
            try:
                return function(instance, *args, **kwargs)
            finally:
                collection = getattr(getattr(instance, "_collection", None), "name", None)
                events.append({"stage": label, "collection": collection,
                    "started": started, "wall_ms": (time.perf_counter() - started) * 1000,
                    "thread_cpu_ms": (time.thread_time() - cpu) * 1000})
        return observed

    def get_more(cursor):
        started = time.perf_counter()
        future = original_more(cursor)
        operation = {"collection": cursor.collection.name, "started": started}
        operations.append(operation)
        def done(_):
            operation["wall_ms"] = (time.perf_counter() - started) * 1000
        future.add_done_callback(done)
        return future

    def observed_executor(loop, function, *args, **kwargs):
        # Motor captured Cursor._refresh when its class was generated. Patching
        # Cursor._refresh afterward would not instrument the real worker.
        if function is original_refresh:
            function = timed("driver_refresh", function)
        return original_executor(loop, function, *args, **kwargs)

    def on_gc(phase, info):
        key = (threading.get_ident(), info["generation"])
        if phase == "start":
            pending_gc[key] = time.perf_counter()
        elif key in pending_gc:
            gc_events.append({"generation": info["generation"],
                "wall_ms": (time.perf_counter() - pending_gc.pop(key)) * 1000,
                "collected": info["collected"], "uncollectable": info["uncollectable"]})

    running = True
    async def heartbeat():
        while running:
            started = time.perf_counter()
            await asyncio.sleep(.005)
            lags.append(max(0, (time.perf_counter() - started - .005) * 1000))

    Connection.receive_message = timed("socket_receive", original_receive)
    Cursor._unpack_response = timed("bson_unpack", original_unpack)
    motor_asyncio.run_on_executor = observed_executor
    AsyncIOMotorCursor._get_more = get_more
    gc.callbacks.append(on_gc)
    heartbeat_task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    rss_before, peak_before = current_rss_kib(), resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    gc_counts_before, gc_stats_before = gc.get_count(), gc.get_stats()
    start, cpu = time.perf_counter(), time.process_time()
    error, result = None, {}
    try:
        if schedule == "concurrent":
            result = await load_collections(database, OWNER, NAMES,
                reader=refresh_source_rows, observe_primary_orders=True)
        else:
            result.update(await load_collections(database, OWNER, ["unified_orders"],
                reader=refresh_source_rows, observe_primary_orders=True))
            result.update(await load_collections(database, OWNER, OTHER_NAMES,
                reader=refresh_source_rows, observe_primary_orders=True))
    except SourceReadError as failure:
        error = {"code": failure.code, "collection": failure.collection}
    finally:
        wall, process_cpu = (time.perf_counter() - start) * 1000, (time.process_time() - cpu) * 1000
        rss_after, peak_after = current_rss_kib(), resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        gc_counts_after, gc_stats_after = gc.get_count(), gc.get_stats()
        running = False
        await heartbeat_task
        gc.callbacks.remove(on_gc)
        Connection.receive_message, Cursor._unpack_response = original_receive, original_unpack
        motor_asyncio.run_on_executor = original_executor
        AsyncIOMotorCursor._get_more = original_more
        demand._CURRENT.reset(token)
        await asyncio.wait_for(asyncio.to_thread(client.close), 5)
    assert resident["owner_id"] == OWNER and len(resident["obligations"]) == SNAPSHOT_COUNT
    assert all("wall_ms" in operation for operation in operations)
    summaries = {}
    for label in {event["stage"] for event in events}:
        values = [event for event in events if event["stage"] == label]
        summaries[label] = {"count": len(values),
            "wall_sum_ms": round(sum(v["wall_ms"] for v in values), 3),
            "thread_cpu_sum_ms": round(sum(v["thread_cpu_ms"] for v in values), 3),
            "wall_max_ms": round(max(v["wall_ms"] for v in values), 3)}
    digests = {}
    for name, rows in result.items():
        digest = hashlib.sha256()
        for row in rows:
            digest.update(bson.encode(row))
        digests[name] = digest.hexdigest()
    ordered_lags = sorted(lags)
    gc_by_gen = {}
    for generation in range(3):
        rows = [row for row in gc_events if row["generation"] == generation]
        gc_by_gen[str(generation)] = {"count": len(rows),
            "wall_sum_ms": round(sum(row["wall_ms"] for row in rows), 3),
            "wall_max_ms": round(max((row["wall_ms"] for row in rows), default=0), 3),
            "collected": sum(row["collected"] for row in rows)}
    return {"schedule": schedule, "wall_ms": round(wall, 3), "process_cpu_ms": round(process_cpu, 3),
        "versions": {"python": sys.version.split()[0], "motor": motor.version,
            "pymongo": pymongo.version, "bson_c_extension": bson.has_c()},
        "error": error, "row_counts": {name: len(rows) for name, rows in result.items()},
        "sha256": digests, "resident_snapshot_bson_bytes": snapshot_size,
        "rss_before_kib": rss_before, "rss_after_kib": rss_after,
        "process_peak_rss_before_kib": peak_before, "process_peak_rss_after_kib": peak_after,
        "read_rss_growth_kib": rss_after - rss_before,
        "gc_thresholds": list(gc.get_threshold()), "gc_by_generation": gc_by_gen,
        "gc_counts_before": list(gc_counts_before), "gc_counts_after": list(gc_counts_after),
        "gc_stats_before": gc_stats_before, "gc_stats_after": gc_stats_after,
        "explicit_gc_collect_used": False, "gc_enabled": gc.isenabled(),
        "event_loop_lag_ms": {"samples": len(lags), "max": round(max(lags, default=0), 3),
            "p95": round(ordered_lags[min(len(ordered_lags) - 1, int(len(ordered_lags) * .95))], 3) if lags else None},
        "nested_driver_stage_summaries": summaries,
        "driver_batch_operations": [{**operation, "started": round((operation["started"] - start) * 1000, 3),
            "wall_ms": round(operation["wall_ms"], 3)} for operation in operations],
        "primary_orders_reads": list(context.primary_orders_reads),
        "stage_timings": list(context.stage_timings),
        "driver_events": [{**event, "started": round((event["started"] - start) * 1000, 3),
            "wall_ms": round(event["wall_ms"], 3), "thread_cpu_ms": round(event["thread_cpu_ms"], 3)} for event in events]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wire", action="store_true")
    parser.add_argument("--client", choices=("concurrent", "orders_first"))
    parser.add_argument("--port", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.wire:
        wire_main()
        return
    if args.client:
        assert args.port and 0 < args.port < 65536
        print(json.dumps(asyncio.run(client_main(args.port, args.client))))
        return
    assert args.output is not None
    command = [sys.executable, "-B", str(Path(__file__).resolve())]
    # No ambient Mongo URI is read; only the loopback port returned by our
    # owned responder may be passed to a child client.
    wire = subprocess.Popen([*command, "--wire"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    results = []
    source_names = ("backend/operational_read_runtime.py", "backend/operational_balance_sources.py")
    source_before = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_names}
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(wire.stdout, selectors.EVENT_READ)
            if not selector.select(45):
                raise RuntimeError("synthetic responder did not become ready")
        line = wire.stdout.readline()
        if not line:
            raise RuntimeError("synthetic responder exited: " + wire.stderr.read())
        ready = json.loads(line)
        for index, schedule in enumerate(("concurrent", "orders_first", "orders_first", "concurrent"), 1):
            run = subprocess.run([*command, "--client", schedule, "--port", str(ready["port"])],
                capture_output=True, text=True, timeout=50, check=True)
            result = json.loads(run.stdout)
            result["run"] = index
            results.append(result)
            print(json.dumps({"run": index, "schedule": schedule, "wall_ms": result["wall_ms"],
                "orders": result["primary_orders_reads"], "error": result["error"]}), flush=True)
        expected = ready["fixture"]["expected_sha256"]
        successful = [result for result in results if result["error"] is None]
        parity = bool(successful) and all(result["sha256"] == expected for result in successful)
        source_after = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_names}
        payload = {"method": "Offline synthetic wire responder in separate process; real Motor/PyMongo; fresh-client A/B/B/A",
            "production_data_or_db_used": False, "production_latency_measured": False,
            "runtime_source_changed": False, "financial_writer_used": False,
            "per_read_timeout_seconds": 15, "application_row_sentinel": 20001,
            "complete_read_count": len(successful),
            "successful_projection_cohort_and_payload_identical": parity,
            "source_sha256_before": source_before, "source_sha256_after": source_after,
            "runtime_source_unchanged_during_experiment": source_before == source_after,
            "fixture": ready["fixture"], "runs": results,
            "limitations": ["Synthetic byte/object distributions and competing-source counts are not production measurements.",
                "Loopback omits real Mongo query execution, server load and external network latency.",
                "GC/driver/event-loop timings overlap; do not sum nested stages.",
                "Socket receive metrics cover all synthetic sources; per-source BSON unpack metrics identify collections.",
                "This isolated primary experiment verifies per-source15s only, not the whole visible85s refresh deadline.",
                "Peak RSS is a fresh client-process high-water mark; before/after distinguishes fixture setup from reads.",
                "Two observations per schedule are mechanism evidence, not a production percentile or reliability proof."]}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n")
        assert parity and source_before == source_after, "whole-row parity or source identity failed; inspect evidence"
    finally:
        wire.terminate()
        try:
            wire.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            wire.kill()
            wire.communicate(timeout=5)


if __name__ == "__main__":
    main()
