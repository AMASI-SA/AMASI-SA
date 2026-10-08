"""A-only experiment; production function remains unchanged until evidence review."""
import ast
import gc
import json
import math
import os
import platform
import statistics
import subprocess
import textwrap
import time
import weakref
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

import pytest
import preparation_piece_operations as ops
import order_engine.service as service
from order_engine.repository import MongoOrderRepository
import test_backend_performance_board_mongo as fixture_module

RETAINED_HEAD = '8d87df8a55a42b7e0dcb0e2048c15729243b94e4'


def make_handler(stream, meter=None):
    source = subprocess.check_output(['git', 'show', RETAINED_HEAD + ':backend/preparation_piece_operations.py'], text=True, encoding='utf-8')
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_assembly_order_board')
    source = ast.get_source_segment(source, node)
    begin = source.index('    orders_by_number = {}')
    loop = source.index('    for workflow in workflows:', begin)
    end = source.index('    if state == "in_progress":', loop)
    body = source[loop + len('    for workflow in workflows:\n'):end]
    if stream:
        # Same eligibility/display body; return instead of appending to all rows.
        helper = textwrap.dedent(body).replace('continue', 'return None')
        helper = helper.replace('rows.append({', 'return {').replace('        })', '        }')
        # Closing append parenthesis is at indentation zero after dedent.
        helper = helper.rstrip()
        assert helper.endswith('})')
        helper = helper[:-2] + '}'
        replacement = '    def display_row(workflow, orders_by_number):\n' + textwrap.indent(helper, '        ') + '\n'
        replacement += '''    workflows_by_number = defaultdict(list)
    for index, workflow in enumerate(workflows):
        workflows_by_number[_text(workflow.get("order_number"))].append((index, workflow))
    indexed_rows = {}
    for start in range(0, len(candidate_numbers), 500):
        numbers = candidate_numbers[start:start + 500]
        orders_by_number = await get_orders(repository, user_id=user_id, order_numbers=numbers)
        for number in numbers:
            for index, workflow in workflows_by_number[number]:
                row = display_row(workflow, orders_by_number)
                if row is not None:
                    indexed_rows[index] = row
        del orders_by_number
    # Restore original workflow order before stable final sorting (duplicate ties).
    rows = [indexed_rows[index] for index in sorted(indexed_rows)]
'''
        source = source[:begin] + replacement + source[end:]
        if meter:
            source = source.replace('row = display_row(workflow, orders_by_number)', 'row = meter.call("display_eligibility_ms", display_row, workflow, orders_by_number)')
    elif meter:
        source = source[:loop] + '    display_started = time.perf_counter()\n' + source[loop:end] + '    meter.values["display_eligibility_ms"] += (time.perf_counter() - display_started) * 1000\n' + source[end:]
    if meter:
        source = source.replace('    if state == "in_progress":\n        rows.sort(', '    sort_started = time.perf_counter()\n    if state == "in_progress":\n        rows.sort(')
        source = source.replace('    total = len(rows)', '    meter.values["sorting_ms"] += (time.perf_counter() - sort_started) * 1000\n    total = len(rows)')
    namespace = dict(vars(ops)); namespace.update(meter=meter, time=time, defaultdict=defaultdict)
    exec(compile(source, 'retained' if not stream else 'experimental-stream', 'exec'), namespace)
    return namespace['_assembly_order_board']


class Meter:
    def __init__(self):
        self.values = defaultdict(float)
        self.live = 0
        self.refs = []
        self.starts = {}

    def call(self, name, fn, *args, **kwargs):
        started = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            self.values[name] += (time.perf_counter() - started) * 1000

    def gc(self, phase, info):
        generation = info['generation']
        if phase == 'start':
            self.starts[generation] = time.perf_counter()
        else:
            self.values['gc_ms'] += (time.perf_counter() - self.starts[generation]) * 1000
            self.values['gc_gen' + str(generation)] += 1

    def mapped(self, fn, *args, **kwargs):
        wall, cpu = time.perf_counter(), time.thread_time()
        try:
            dto = fn(*args, **kwargs)
        finally:
            self.values['mapping_wall_ms'] += (time.perf_counter() - wall) * 1000
            self.values['mapping_cpu_ms'] += (time.thread_time() - cpu) * 1000
        self.live += 1
        self.values['peak_live_dtos'] = max(self.values['peak_live_dtos'], self.live)
        def released(_):
            self.live -= 1
        self.refs.append(weakref.ref(dto, released))
        return dto


@pytest.mark.asyncio
async def test_streaming_experiment_real_mongo():
    fixture = fixture_module.BoardPerformanceMongoTests()
    await fixture.asyncSetUp()
    results = {'retained_head': RETAINED_HEAD, 'experiment_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
               'python': platform.python_version(), 'platform': platform.platform(), 'mongo': '8.0.12', 'replica_set': 'performancepr1', 'sizes': []}
    original_map = service._map_row
    original_convert = MongoOrderRepository._to_discovery_row
    try:
        for size in (50, 500, 5000):
            await fixture.populate(size)
            if size == 500:
                await fixture.db.unified_orders.drop_index('user_id_1_order_number_1')
                await fixture.db.unified_orders.create_index([('user_id', 1), ('order_number', 1)], unique=True)
            # Duplicate workflow identity exercises stable tie ordering, without
            # changing the mandated workflow count.
            if size == 50:
                row = await fixture.db[ops.WORKFLOWS].find_one({'user_id': 'owner', 'order_number': '100001'})
                row.pop('_id'); row['carrier_name'] = 'duplicate workflow'
                await fixture.db[ops.WORKFLOWS].delete_one({'user_id': 'owner', 'order_number': '100049'})
                await fixture.db[ops.WORKFLOWS].insert_one(row)
                await fixture.db.unified_orders.drop_index('user_id_1_order_number_1')
                await fixture.db.unified_orders.create_index([('user_id', 1), ('order_number', 1)])
                raw = await fixture.db.unified_orders.find_one({'user_id': 'owner', 'order_number': '100002'})
                raw.pop('_id'); raw['order_status'] = 'completed'
                await fixture.db.unified_orders.insert_one(raw)
                malformed = await fixture.db.unified_orders.find_one({'user_id': 'owner', 'order_number': '100003'})
                await fixture.db.unified_orders.update_one({'_id': malformed['_id']}, {'$set': {'raw_by_source.salla_direct': None}})
                malformed.pop('_id'); await fixture.db.unified_orders.insert_one(malformed)
            before = await fixture.snapshot()
            # Full contract proof against original serial baseline and current PR.
            for state in ('in_progress', 'completed'):
                for query, offset, limit in (('', 0, 50), ('#1000', 3, 7), ('missing', 0, 50), ('', size + 1, 10)):
                    args = dict(user_id='owner', state=state, query=query, offset=offset, limit=limit)
                    expected = await fixture.baseline(fixture.db, **args)
                    assert await make_handler(False)(fixture.db, **args) == expected
                    assert await make_handler(True)(fixture.db, **args) == expected
            args = dict(user_id='owner', state='in_progress', query='', offset=0, limit=50)
            expected = await make_handler(False)(fixture.db, **args)
            samples = {'retained': [], 'stream': []}
            # Compile once: discarded exec namespaces/code objects would add
            # harness-only cyclic garbage to the GC measurements.
            handlers = {variant: make_handler(variant == 'stream', Meter())
                        for variant in samples}
            # Natural GC, balanced AB/BA ordering, two warmups + 30 samples each.
            gc.collect()
            for repeat in range(32):
                for variant in (('retained', 'stream') if repeat % 2 == 0 else ('stream', 'retained')):
                    meter = Meter(); handler = handlers[variant]
                    handler.__globals__['meter'] = meter
                    fixture.commands.reset()
                    original_succeeded = fixture.commands.succeeded
                    def succeeded(event):
                        meter.values['mongo_ms'] += event.duration_micros / 1000
                    fixture.commands.succeeded = succeeded
                    gc.callbacks.append(meter.gc)
                    started = time.perf_counter(); cpu = time.thread_time()
                    try:
                        with patch.object(service, '_map_row', lambda *a, **k: meter.mapped(original_map, *a, **k)), patch.object(MongoOrderRepository, '_to_discovery_row', staticmethod(lambda *a, **k: meter.call('repository_conversion_ms', original_convert, *a, **k))):
                            actual = await handler(fixture.db, **args)
                        meter.values['handler_ms'] = (time.perf_counter() - started) * 1000
                        meter.values['handler_cpu_ms'] = (time.thread_time() - cpu) * 1000
                        assert actual == expected
                        assert meter.live == 0
                        meter.values['mongo_commands'] = sum(fixture.commands.counts.values())
                        if repeat >= 2:
                            samples[variant].append(dict(meter.values))
                    finally:
                        gc.callbacks.remove(meter.gc)
                        fixture.commands.succeeded = original_succeeded
            assert before == await fixture.snapshot()
            assert [r['mongo_commands'] for r in samples['retained']] == [r['mongo_commands'] for r in samples['stream']]
            if size == 5000:
                assert max(r['peak_live_dtos'] for r in samples['stream']) <= 500
                assert min(r['peak_live_dtos'] for r in samples['retained']) > 4000
            summary = {variant: {key: {'p50': statistics.median(row.get(key, 0) for row in rows), 'p95': sorted(row.get(key, 0) for row in rows)[math.ceil(.95 * len(rows))-1]} for key in sorted({k for row in rows for k in row})} for variant, rows in samples.items()}
            results['sizes'].append({'size': size, 'summary': summary, 'samples': samples})
        Path(os.environ.get('PERF_STREAM_EVIDENCE_PATH', '../stream-evidence.json')).write_text(json.dumps(results, indent=2), encoding='utf-8')
    finally:
        await fixture.cleanup()
