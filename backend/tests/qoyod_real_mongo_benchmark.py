"""Bounded synthetic timing on a loopback-only test replica set.

Run baseline and candidate in separate processes with different PYTHONPATHs.
Uses driver command monitoring only; profiling is disabled for timing.
"""
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import monitoring

from integrations.qoyod.models import ensure_qoyod_indexes
from integrations.qoyod_manual import auto_send
from qoyod_incremental_benchmark import DB


class Commands(monitoring.CommandListener):
    def __init__(self):
        self.lock = threading.Lock()
        self.counts = Counter()
        self.times = defaultdict(float)

    def started(self, event):
        pass

    def succeeded(self, event):
        with self.lock:
            self.counts[event.command_name] += 1
            self.times[event.command_name] += event.duration_micros / 1000

    def failed(self, event):
        with self.lock:
            self.counts['failed:' + event.command_name] += 1

    def reset(self):
        with self.lock:
            self.counts.clear()
            self.times.clear()

    def snapshot(self):
        with self.lock:
            return dict(self.counts), {k: round(v, 3) for k, v in self.times.items()}


async def trial(rows):
    uri = os.environ['QOYOD_TEST_MONGO_URI']
    assert uri.startswith('mongodb://127.0.0.1:')
    commands = Commands()
    client = AsyncIOMotorClient(uri, event_listeners=[commands], tz_aware=True, serverSelectionTimeoutMS=5000)
    db = client['qoyod_test_benchmark_' + uuid.uuid4().hex]
    assert (await client.admin.command('hello')).get('setName')
    await ensure_qoyod_indexes(db)
    await db.unified_orders.create_index([('user_id', 1), ('order_number', 1)], unique=True)
    await db.unified_orders.create_index([('user_id', 1), ('order_date', -1)])
    await db.unified_orders.insert_many([{
        'user_id': 'owner', 'order_number': str(i).zfill(6), 'order_date': '2026-11-01',
        'order_status': 'completed', 'payment_status': 'paid', 'payment_method': 'mada', 'total_amount': 100,
    } for i in range(rows)])
    # First100 are unsent; remaining records represent already-sent history.
    await db.qoyod_invoices.insert_many([{
        'user_id': 'main', 'qoyod_invoice_id': str(i + 10000), 'salla_order_id': 'invoice:' + str(i),
        'reference': str(i).zfill(6), 'qoyod_official_reference': str(i).zfill(6),
        'reference_provenance': 'qoyod.reference', 'raw_response': {'reference': str(i).zfill(6)},
        'source': 'plan_b_send', 'issue_date': '2026-11-01', 'total': 100, 'paid_amount': 100,
    } for i in range(100, rows)])
    # Precreate candidate indexes for both modes; setup is outside measurement.
    await db.qoyod_order_followup.create_index([('user_id', 1), ('order_number', 1)], unique=True,
                                             name='qoyod_followup_owner_order')
    await db.qoyod_order_followup.create_index([('user_id', 1), ('priority', 1), ('next_check_at', 1), ('order_number', 1)],
                                             name='qoyod_followup_due')
    counts = Counter()
    wrapped = DB(db, counts)
    commands.reset()
    cpu, wall = time.process_time(), time.perf_counter()
    selected = []
    for _ in range(10):
        candidates, _ = await auto_send._load_candidate_rows(wrapped,
            settings={'invoice_trigger_statuses': ['completed']}, orders_user_id='owner', batch_limit=5)
        assert len(candidates) == 5
        selected.extend(r['order_number'] for r in candidates)
    elapsed, cpu_elapsed = time.perf_counter() - wall, time.process_time() - cpu
    wire, durations = commands.snapshot()
    result = {'orders': rows, 'sent_history': rows - 100, 'ticks': 10,
        'selected_count': len(selected), 'collection_api_calls': dict(counts),
        'wall_ms': round(elapsed * 1000, 3), 'client_cpu_ms': round(cpu_elapsed * 1000, 3),
        'wire_commands': wire, 'driver_command_ms': durations,
        'driver_read_ms': round(sum(durations.get(k, 0) for k in ('find', 'getMore', 'aggregate', 'count', 'distinct')), 3),
        'driver_all_command_ms': round(sum(durations.values()), 3),
        'profiling_enabled': False}
    assert db.name.startswith('qoyod_test_benchmark_')
    await client.drop_database(db.name)
    client.close()
    return result


async def main():
    mode, rows, repeats = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    assert rows in {1000, 5000} and 1 <= repeats <= 5
    root = Path(auto_send.__file__).resolve().parents[2]
    source = {}
    for path in ('qoyod_auto_unified/incremental.py', 'qoyod_auto_unified/queue_select.py',
                 'integrations/qoyod/candidate_orders.py', 'integrations/qoyod/models.py'):
        file = root / path
        if file.exists():
            source[path] = hashlib.sha256(file.read_bytes()).hexdigest()
    print(json.dumps({'mode': mode, 'at_utc': datetime.now(timezone.utc).isoformat(),
        'engine': 'MongoDB8.0.12, isolated loopback replica set, WiredTiger0.25GiB',
        'profile_scope': 'disabled; wire CommandListener only, no plan logging overhead',
        'production_or_provider_requests': False, 'source_sha256': source,
        'trials': [await trial(rows) for _ in range(repeats)]}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
