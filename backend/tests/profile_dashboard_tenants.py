"""Same-run, bounded dashboard tenant attribution against disposable local Mongo.

matrix runs fresh unprofiled/profiled workers over the SAME fixture per case.
No application source is changed. Inclusive stages/driver waits MUST NOT be added.
Task-dispatch segments are disjoint on the event-loop thread. They measure actual
running wall/thread CPU, including nested calls; synchronous category counters
subtract nested measured children. Future-ready callbacks provide only a LOWER
BOUND on runnable delay (callbacks themselves can be delayed by a busy loop).
"""
from __future__ import annotations
import argparse
import asyncio
from collections import defaultdict
from contextvars import ContextVar
import functools
import inspect
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import threading
import time
import types
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'backend/tests'), str(ROOT / 'backend'), str(ROOT)]
import benchmark_dashboard_summary_memory as bench
from fastapi.encoders import jsonable_encoder
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import monitoring

TENANT = ContextVar('profile_tenant', default='unattributed')


def metric():
    return dict(calls=0, wall_seconds=0., cpu_seconds=0.)


class Attribution:
    def __init__(self):
        self.active = defaultdict(metric)
        self.sync = defaultdict(lambda: defaultdict(metric))
        self.stages = defaultdict(lambda: defaultdict(metric))
        self.waits = defaultdict(lambda: dict(suspensions=0, suspended_task_seconds=0.,
                                             runnable_delay_lower_bound_seconds=0.))
        self.stack = []  # synchronous calls only, event-loop thread only
        self.restores = []

    @staticmethod
    def add(target, wall, cpu):
        target['calls'] += 1
        target['wall_seconds'] += wall
        target['cpu_seconds'] += cpu

    @types.coroutine
    def drive(self, coroutine, stage=None):
        """Transparent await protocol; do not manually await yielded Futures."""
        iterator = coroutine.__await__()
        value, error = None, None
        tenant = TENANT.get()
        while True:
            start, cpu = time.perf_counter(), time.thread_time()
            try:
                if error is None:
                    yielded = iterator.send(value)
                else:
                    yielded = iterator.throw(error)
                    error = None
            except StopIteration as stopped:
                return stopped.value
            finally:
                elapsed, consumed = time.perf_counter() - start, time.thread_time() - cpu
                target = self.active[tenant] if stage is None else self.stages[tenant][stage]
                self.add(target, elapsed, consumed)
            suspended = time.perf_counter()
            ready = [None]
            def mark_ready(_, cell=ready):
                cell[0] = time.perf_counter()
            if stage is None and isinstance(yielded, asyncio.Future):
                yielded.add_done_callback(mark_ready)
            try:
                value = yield yielded
            except BaseException as caught:
                value, error = None, caught
            resumed = time.perf_counter()
            if stage is None:
                wait = self.waits[tenant]
                wait['suspensions'] += 1
                wait['suspended_task_seconds'] += resumed - suspended
                if yielded is None:
                    wait['runnable_delay_lower_bound_seconds'] += resumed - suspended
                elif ready[0] is not None:
                    wait['runnable_delay_lower_bound_seconds'] += resumed - ready[0]

    def factory(self, loop, coroutine, **kwargs):
        async def run():
            return await self.drive(coroutine)
        return asyncio.Task(run(), loop=loop, **kwargs)

    def sync_wrapper(self, function, category):
        @functools.wraps(function)
        def call(*args, **kwargs):
            tenant = TENANT.get()
            start, cpu = time.perf_counter(), time.thread_time()
            frame = [0., 0.]
            self.stack.append(frame)
            try:
                return function(*args, **kwargs)
            finally:
                wall, consumed = time.perf_counter() - start, time.thread_time() - cpu
                self.stack.pop()
                self.add(self.sync[tenant][category], wall - frame[0], consumed - frame[1])
                if self.stack:
                    self.stack[-1][0] += wall
                    self.stack[-1][1] += consumed
        return call

    def async_wrapper(self, function, category):
        @functools.wraps(function)
        async def call(*args, **kwargs):
            tenant = TENANT.get()
            start = time.perf_counter()
            try:
                return await self.drive(function(*args, **kwargs), category + '_active_inclusive')
            finally:
                self.add(self.stages[tenant][category + '_wall_inclusive'], time.perf_counter() - start, 0.)
        return call

    def patch(self, owner, name, category, asynchronous=False):
        original = owner[name] if isinstance(owner, dict) else getattr(owner, name)
        wrapper = self.async_wrapper if asynchronous else self.sync_wrapper
        replacement = wrapper(original, category)
        if isinstance(owner, dict):
            owner[name] = replacement
        else:
            setattr(owner, name, replacement)
        self.restores.append((owner, name, original))

    def install(self):
        import dashboard_spill as spill
        import dashboard_order_reads as orders
        import dashboard_product_pages as products
        import dashboard_recurring_reads as recurring
        import dashboard_financial_pages as labels
        import dashboard_order_accumulator as accumulator
        import balances
        for name in ('_decode', '_decode_many'):
            self.patch(spill, name, 'cache_decoding')
        self.patch(spill, '_store_value', 'cache_encoding')
        # These modules bind codec aliases at import time, before patching.
        for module in (products, recurring):
            self.patch(module, '_decode', 'cache_decoding')
            self.patch(module, '_store_value', 'cache_encoding')
        self.patch(spill.DashboardSpill, 'execute', 'sqlite_execute_and_bookkeeping')
        self.patch(spill.DashboardSpill, 'executemany', 'sqlite_execute_and_bookkeeping')
        self.patch(labels, 'rollup_payments', 'financial_labels')
        self.patch(accumulator.DashboardOrderAccumulator, 'observe_fee_batch', 'financial_fees')
        self.patch(accumulator.DashboardOrderAccumulator, 'observe', 'order_accumulation')
        self.patch(balances, 'compute_balances', 'compute_balances')
        self.patch(orders, '_load_bounded', 'order_snapshot', True)
        self.patch(products, 'load_product_context', 'product_catalog', True)
        # Cursor.to_list includes driver work, Future wait, and scheduling delay.
        # This is deliberately reported as inclusive, not Mongo server duration.
        from motor.motor_asyncio import AsyncIOMotorCursor
        self.patch(AsyncIOMotorCursor, 'to_list', 'batch_reads', True)
        # Factory builds/extracts globals after the module patches are installed.
        original = bench.v2_endpoint
        def endpoint(db, mode):
            result = original(db, mode)
            namespace = inspect.unwrap(result).__globals__
            self.patch(namespace, 'build_mezan_v2_product_cost', 'product_aggregation', True)
            return result
        bench.v2_endpoint = endpoint
        self.restores.append((bench, 'v2_endpoint', original))

    def restore(self):
        for owner, name, original in reversed(self.restores):
            if isinstance(owner, dict):
                owner[name] = original
            else:
                setattr(owner, name, original)

    def snapshot(self):
        return {key: dict(value) for key, value in self.active.items()}


class TenantReads(monitoring.CommandListener):
    def __init__(self):
        self.rows = defaultdict(lambda: dict(commands=0, documents=0, driver_seconds=0., failures=0))
        self.pending = {}
        self.lock = threading.Lock()

    def started(self, event):
        if event.command_name in {'find', 'getMore', 'aggregate'}:
            with self.lock:
                self.pending[(event.connection_id, event.request_id)] = TENANT.get()

    def succeeded(self, event):
        if event.command_name not in {'find', 'getMore', 'aggregate'}:
            return
        with self.lock:
            tenant = self.pending.pop((event.connection_id, event.request_id), 'unattributed')
            row = self.rows[tenant]
            cursor = event.reply.get('cursor', {})
            row['commands'] += 1
            row['documents'] += len(cursor.get('firstBatch', cursor.get('nextBatch', [])))
            row['driver_seconds'] += event.duration_micros / 1e6

    def failed(self, event):
        with self.lock:
            tenant = self.pending.pop((event.connection_id, event.request_id), 'unattributed')
            self.rows[tenant]['failures'] += 1


def source():
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()
    return dict(head=git('rev-parse', 'HEAD'), tree=git('rev-parse', 'HEAD^{tree}'),
                tracked_dirty=bool(git('status', '--porcelain', '--untracked-files=no')))


async def worker(database, tenants, profiled):
    if not database.startswith(bench.DB_PREFIX):
        raise ValueError('dedicated disposable database required')
    attribution, reads = Attribution(), TenantReads()
    client = AsyncIOMotorClient(bench.URI, event_listeners=[reads], serverSelectionTimeoutMS=3000)
    await client.admin.command('ping')
    mongo_version = (await client.admin.command('buildInfo'))['version']
    loop, old_factory = asyncio.get_running_loop(), asyncio.get_running_loop().get_task_factory()
    if profiled:
        attribution.install()
        loop.set_task_factory(attribution.factory)
    endpoint = bench.v2_endpoint(client[database], 'after')
    probe = bench.SpillProbe()
    probe.install()
    rss_start = bench.rss()[0]
    start, cpu, loop_cpu = time.perf_counter(), time.process_time(), time.thread_time()
    async def request(owner):
        request_start = time.perf_counter()
        response = await endpoint(user={'id': owner, 'role': 'owner'}, from_date='2026-09-01',
                                  to_date='2026-09-30', payment_methods=None, shipping_companies=None)
        endpoint_wall = time.perf_counter() - request_start
        encode = time.perf_counter()
        safe = jsonable_encoder(response)
        wire = json.dumps(safe, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        serialization = time.perf_counter() - encode
        finances = {key: safe.get(key) for key in ('totals', 'monthly', 'payment_breakdown', 'shipping_breakdown',
                                                  'source_breakdown', 'month_kpis', 'currency_conversion', 'net_sales_config')}
        return dict(tenant=owner, endpoint_seconds=endpoint_wall, json_seconds=serialization,
                    response_bytes=len(wire), financial_signature=bench.signature(bench.financial_values(finances)),
                    totals=safe.get('totals'), finished_since_start=time.perf_counter() - start,
                    active_at_completion=attribution.snapshot())
    tasks = []
    try:
        for index in range(tenants):
            # Keep IDs stable between the 1/2/3/4 cases: seed uses tenant-N for
            # multi-tenant fixtures; 1-tenant original fixture uses owner.
            owner = 'owner' if tenants == 1 else f'tenant-{index}'
            token = TENANT.set(owner)
            tasks.append(asyncio.create_task(request(owner)))
            TENANT.reset(token)
        results = await asyncio.gather(*tasks)
        wall = time.perf_counter() - start
        process_cpu, event_loop_cpu = time.process_time() - cpu, time.thread_time() - loop_cpu
        peak = bench.rss()[1]
        active = attribution.snapshot()
        for result in results:
            owner = result['tenant']
            snapshot = result.pop('active_at_completion')
            # Includes coordinator child tasks, counts each loop dispatch once.
            own = snapshot.get(owner, metric())['wall_seconds']
            all_active = sum(row['wall_seconds'] for row in snapshot.values())
            result['completion_wall_partition'] = dict(own_task_active_seconds=own,
                other_tasks_active_seconds=all_active-own,
                loop_callbacks_idle_and_measurement_residual_seconds=result['finished_since_start']-all_active)
        sync = dict(attribution.sync)
        for owner, row in active.items():
            sync.setdefault(owner, {})['python_and_uninstrumented'] = dict(calls=None,
                wall_seconds=row['wall_seconds']-sum(v['wall_seconds'] for v in sync[owner].values()),
                cpu_seconds=row['cpu_seconds']-sum(v['cpu_seconds'] for v in sync[owner].values()))
        if profiled:
            for owner, row in active.items():
                assert sync[owner]['python_and_uninstrumented']['wall_seconds'] >= -0.001
                assert sync[owner]['python_and_uninstrumented']['cpu_seconds'] >= -0.001
            assert reads.rows.get('unattributed', {}).get('documents', 0) == 0, 'Mongo tenant attribution lost'
        assert not any(row['failures'] for row in reads.rows.values()), 'Mongo command failed'
        return dict(source=source(), profiled=profiled, tenants=tenants, wall_seconds=wall,
            process_cpu_seconds=process_cpu, event_loop_cpu_seconds=event_loop_cpu,
            background_threads_cpu_seconds=process_cpu-event_loop_cpu,
            rss_baseline_bytes=rss_start, rss_peak_bytes=peak,
            task_dispatch_disjoint=active, sync_categories_exclusive=sync,
            async_stages_inclusive_nonadditive=dict(attribution.stages),
            suspended_tasks_inclusive_nonadditive=dict(attribution.waits),
            mongo_driver_inclusive_nonadditive=dict(reads.rows), requests=results,
            environment=dict(python=sys.version, platform=platform.platform(), cpu_count=os.cpu_count(),
                             mongo=mongo_version, sqlite=sqlite3.sqlite_version),
            limitations=['SQLite counters cover execute/executemany plus wrapper bookkeeping; cursor fetch is in Python/uninstrumented.',
              'Driver timing is client command duration, not isolated Mongo server execution.',
              'Runnable delay is a lower bound; callback dispatch delay is not independently observable.',
              'Synchronous active wall minus thread CPU includes descheduling, blocking I/O, and shared-resource contention; causes are not separately identified.',
              'Async stage, Mongo and suspended-task timings overlap and must never be added to totals.',
              'Completion snapshots omit their own final dispatch tail; that small tail remains in loop residual.',
              'Profile wrappers add overhead; compare the paired fresh unprofiled worker on the same fixture.'],
            **probe.finish())
    finally:
        if not probe.stopped.is_set():
            probe.finish()
        loop.set_task_factory(old_factory)
        attribution.restore()
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['matrix', 'worker'])
    parser.add_argument('--count', type=int, default=100000)
    parser.add_argument('--tenants', nargs='+', type=int, default=[1, 2, 3, 4])
    parser.add_argument('--database')
    parser.add_argument('--profiled', action='store_true')
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.count < 1 or any(n not in range(1, 5) for n in args.tenants):
        parser.error('positive count and tenants1..4 required')
    if args.mode == 'worker':
        result = asyncio.run(worker(args.database or '', args.tenants[0], args.profiled))
    else:
        samples = []
        for tenants in args.tenants:
            database = bench.DB_PREFIX + uuid.uuid4().hex
            try:
                fixture = bench.child('seed', '--database', database, '--counts', args.count,
                                      '--products', min(args.count, 1000), '--item-bytes', 1024, '--tenants', tenants)
                pair = {}
                for profiled in (False, True):
                    command = [sys.executable, __file__, 'worker', '--database', database, '--tenants', str(tenants)]
                    if profiled:
                        command.append('--profiled')
                    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                               encoding='utf-8', check=True)
                    pair['profiled' if profiled else 'unprofiled'] = json.loads(completed.stdout)
                signatures = lambda key: {row['tenant']: row['financial_signature'] for row in pair[key]['requests']}
                assert signatures('profiled') == signatures('unprofiled'), 'Instrumentation changed financial result'
                assert all(case['spill_cleanup_complete'] for case in pair.values()), 'Spill cleanup failed'
                samples.append(dict(fixture=fixture, **pair,
                    paired_financial_signatures_equal=signatures('profiled') == signatures('unprofiled')))
                # Persist after each completed pair; never lose prior case evidence.
                if args.output:
                    Path(args.output).write_text(json.dumps(dict(source=source(), samples=samples), indent=2), encoding='utf-8')
            finally:
                bench.child('cleanup', '--database', database)
        result = dict(source=source(), samples=samples)
    encoded = json.dumps(result, indent=2)
    if args.output:
        Path(args.output).write_text(encoded, encoding='utf-8')
    print(encoded)


if __name__ == '__main__':
    main()
