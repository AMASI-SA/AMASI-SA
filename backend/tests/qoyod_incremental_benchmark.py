"""Offline, fixed-workload comparison; never connects to production/providers.

Counts API-level collection reads and documents delivered to Python, not Mongo
executionStats. The heartbeat measures loop responsiveness, not employee APIs.
"""
import asyncio
import json
import sys
import time
import tracemalloc
from collections import Counter
from datetime import datetime, timezone

from mongomock_motor import AsyncMongoMockClient
import conftest  # test-only PyMongo/mongomock bulk API compatibility
from integrations.qoyod_manual import auto_send


class Cursor:
    def __init__(self, cursor, counts):
        self.cursor, self.counts = cursor, counts

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.cursor = getattr(self.cursor, name)(*args, **kwargs)
            return self
        return call

    def __aiter__(self):
        self.iterator = self.cursor.__aiter__()
        return self

    async def __anext__(self):
        value = await self.iterator.__anext__()
        self.counts['returned_documents'] += 1
        return value


class Collection:
    def __init__(self, collection, counts):
        self.collection, self.counts = collection, counts

    def find(self, *args, **kwargs):
        self.counts['find'] += 1
        return Cursor(self.collection.find(*args, **kwargs), self.counts)

    async def find_one(self, *args, **kwargs):
        self.counts['find_one'] += 1
        return await self.collection.find_one(*args, **kwargs)

    def __getattr__(self, name):
        value = getattr(self.collection, name)
        if name in {'update_one', 'update_many', 'insert_one', 'delete_one', 'bulk_write'}:
            async def write(*args, **kwargs):
                self.counts['writes'] += 1
                return await value(*args, **kwargs)
            return write
        return value


class DB:
    def __init__(self, db, counts):
        self.db, self.counts = db, counts

    def __getattr__(self, name):
        return Collection(getattr(self.db, name), self.counts)


async def measure():
    db = AsyncMongoMockClient().benchmark
    await db.unified_orders.insert_many([
        {'user_id': 'owner', 'order_number': str(i).zfill(6),
         'order_date': '2026-11-01', 'order_status': 'completed',
         'payment_status': 'paid', 'payment_method': 'mada', 'total_amount': 100}
        for i in range(1000)
    ])
    counts = Counter()
    wrapped = DB(db, counts)
    heartbeat = []
    running = True

    async def pulse():
        while running:
            start = time.perf_counter()
            await asyncio.sleep(.001)
            heartbeat.append(max(0, time.perf_counter() - start - .001) * 1000)

    task = asyncio.create_task(pulse())
    tracemalloc.start()
    cpu, wall = time.process_time(), time.perf_counter()
    for _ in range(10):
        candidates, _counts = await auto_send._load_candidate_rows(
            wrapped, settings={'invoice_trigger_statuses': ['completed']},
            orders_user_id='owner', batch_limit=5,
        )
        assert len(candidates) == 5
        await asyncio.sleep(.002)
    elapsed = time.perf_counter() - wall
    cpu_elapsed = time.process_time() - cpu
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    running = False
    await task
    return dict(counts, wall_ms=round(elapsed * 1000, 2),
                cpu_ms=round(cpu_elapsed * 1000, 2), python_peak_bytes=peak,
                heartbeat_max_lag_ms=round(max(heartbeat, default=0), 2))


async def main():
    result = {'at_utc': datetime.now(timezone.utc).isoformat(),
              'mode': sys.argv[1], 'environment': 'Windows/mongomock, offline',
              'orders': 1000, 'ticks_per_trial': 10,
              'employee_latency_measured': False,
              'trials': [await measure() for _ in range(3)]}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
