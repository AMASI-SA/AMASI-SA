"""One bounded offline reducer profile using real synthetic collector output.

No runtime edit, database/network call, live configuration or financial write.
The initial state comes from the real store constructor; retained history is
only the unchanged output of the first real reducer call, never padded rows.
Both calls run once under cProfile. These instrumented times are diagnostic,
not a comparison or a forecast of live service/packing/CAS latency.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import cProfile
from datetime import datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import pickle
import pstats
import resource
import signal
import subprocess
import sys
import time


ROOT = Path('/workspace/scratch/1f2d9548e6ff/mezan-refresh-source-root-fix')
EVIDENCE = ROOT / 'docs/operations/OPERATIONAL-REFRESH-COMPLETION-20261126/evidence'
EXPECTED_SOURCE = '7b4da05fba49d7b2e923ae3ac304af7e15dbcaa6'
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
sys.dont_write_bytecode = True
sys.path[:0] = [str(EVIDENCE), str(ROOT / 'backend'), str(ROOT / 'backend/tests')]

import profile_source_preparation as fixture_module
from bson import ObjectId
import operational_balance_engine as engine
from operational_balance_store import initial


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()


def runtime_hashes():
    tracked = subprocess.check_output(
        ['git', '-C', str(ROOT), 'ls-files', '-z', '--', 'backend'])
    digest = hashlib.sha256()
    files = {}
    for raw in sorted(tracked.split(b'\0')):
        if not raw or not raw.endswith(b'.py') or b'/tests/' in raw:
            continue
        name = raw.decode()
        value = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        files[name] = value
        digest.update(raw + b'\0' + value.encode() + b'\0')
    return {'manifest_sha256': digest.hexdigest(), 'files': files}


def fingerprint(value):
    class Sink:
        def __init__(self):
            self.digest, self.size = hashlib.sha256(), 0
        def write(self, chunk):
            self.digest.update(chunk)
            self.size += len(chunk)
    sink = Sink()
    pickle.Pickler(sink, protocol=5).dump(value)
    return {'sha256': sink.digest.hexdigest(), 'pickle_bytes': sink.size}


def counts(state):
    financial = state.get('engine', {})
    values = {key: len(financial.get(key, {})) for key in (
        'obligations', 'facts', 'audit', 'issues', 'order_versions',
        'provider_reports', 'cod_reports', 'delivery_register')}
    values['obligation_kinds'] = dict(Counter(
        row.get('kind') for row in financial.get('obligations', {}).values()))
    values['issue_codes'] = dict(Counter(row['code'] for row in financial.get('issues', [])))
    values['provider_fee_orders'] = len(financial.get('provider_fee_inputs', {}).get('orders', {}))
    values['manual_movements'] = len(state.get('movements', []))
    values['openings'] = len(state.get('openings', {}))
    values['requests'] = len(state.get('requests', {}))
    return values


def profile_rows(profiler):
    stats = pstats.Stats(profiler)
    rows = []
    for (filename, line, function), (primitive, total, own, cumulative, _callers) in stats.stats.items():
        try:
            path = str(Path(filename).relative_to(ROOT))
        except ValueError:
            path = filename
        rows.append({'file': path, 'line': line, 'function': function,
                     'primitive_calls': primitive, 'total_calls': total,
                     'self_ms': round(own * 1000, 3),
                     'cumulative_ms': round(cumulative * 1000, 3)})
    selected = {'reconcile', 'copy_report_value', 'deepcopy', '_deepcopy_dict',
                'refresh_provider_fee_inputs', 'reconcile_credits', 'refresh_basis',
                'append_unique_issue', 'put', 'fact', 'fingerprint', 'rollback',
                'checkpoint', 'facts_for', 'money', 'amount', 'calculation_checkpoint'}
    return {
        'top_cumulative': sorted(rows, key=lambda row: row['cumulative_ms'], reverse=True)[:50],
        'top_self': sorted(rows, key=lambda row: row['self_ms'], reverse=True)[:50],
        'selected_functions': [row for row in rows if row['function'] in selected],
        'total_calls': stats.total_calls,
        'primitive_calls': stats.prim_calls,
    }


def measured_reconcile(label, state, sources, destination):
    before_state, before_sources = fingerprint(state), fingerprint(sources)
    gc.collect()  # Explicit untimed collection; GC remains enabled during work.
    gc_start, gc_elapsed, gc_counts = {}, [], Counter()
    def observe_gc(phase, info):
        generation = info['generation']
        if phase == 'start':
            gc_start[generation] = time.perf_counter_ns()
        elif generation in gc_start:
            gc_elapsed.append(time.perf_counter_ns() - gc_start.pop(generation))
            gc_counts[generation] += 1
    profiler = cProfile.Profile()
    gc_before = gc.get_stats()
    gc.callbacks.append(observe_gc)
    wall, cpu = time.perf_counter_ns(), time.process_time_ns()
    try:
        profiler.enable()
        result = engine.reconcile(state, sources, fixture_module.NOW)
    finally:
        profiler.disable()
        wall_ms = (time.perf_counter_ns() - wall) / 1e6
        cpu_ms = (time.process_time_ns() - cpu) / 1e6
        gc.callbacks.remove(observe_gc)
    assert fingerprint(state) == before_state, 'reducer changed its input state'
    assert fingerprint(sources) == before_sources, 'reducer changed its source output'
    assert result is not state
    for key in ('engine', 'openings', 'movements', 'requests', 'audit'):
        assert result[key] is not state[key], key
    if state.get('engine', {}).get('audit'):
        assert result['engine']['audit'] is not state['engine']['audit']
        assert result['engine']['audit'][0] is not state['engine']['audit'][0]
        assert result['engine']['audit'][0]['after'] is not state['engine']['audit'][0]['after']
    profiler.dump_stats(str(destination / f'{label}.pstats'))
    output = {'mode': label, 'wall_ms_instrumented': wall_ms,
              'process_cpu_ms_instrumented': cpu_ms,
              'gc_wall_ms': sum(gc_elapsed) / 1e6,
              'gc_collections': dict(gc_counts),
              'gc_before': gc_before, 'gc_after': gc.get_stats(),
              'process_high_water_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'input_state': before_state, 'source_output': before_sources,
              'result': fingerprint(result), 'result_counts': counts(result),
              'input_state_unchanged': True, 'source_output_unchanged': True,
              'sampled_nested_history_independent': True,
              **profile_rows(profiler)}
    print(json.dumps({'phase': label, 'instrumented_wall_ms': round(wall_ms, 3),
                      'obligations': output['result_counts']['obligations'],
                      'audit': output['result_counts']['audit']}), flush=True)
    return result, output


async def collect(state):
    data = fixture_module.fixture()
    for index, binding in enumerate(data['mezan_product_option_cost_bindings_v2']):
        binding.update(_id=ObjectId(f'{index + 1:024x}'),
                       created_at=datetime(2026, 11, 1),
                       updated_at=datetime(2026, 11, 2, tzinfo=timezone.utc))
    original_data, source_sizes = fixture_module.source_fingerprint(data)
    original_state = fingerprint(state)
    database = fixture_module.collector_database(data)
    original_cursor = fixture_module.Cursor.to_list
    async def prepared_rows(cursor, count=None, *, length=None):
        return list(cursor.values[:length if length is not None else count])
    fixture_module.Cursor.to_list = prepared_rows
    try:
        output = await asyncio.wait_for(fixture_module.sources.collect_sources(
            database, fixture_module.OWNER, fixture_module.START, fixture_module.NOW,
            operational_state=state), timeout=85)
    finally:
        fixture_module.Cursor.to_list = original_cursor
    assert fixture_module.source_fingerprint(data)[0] == original_data
    assert fingerprint(state) == original_state
    assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    summary = {'orders': len(output['orders']),
               'items': sum(len(order['items']) for order in output['orders']),
               'issue_codes': dict(Counter(row['code'] for row in output['issues'])),
               'queries_by_collection': dict(Counter(database.reads)),
               'query_sequence_sha256': hashlib.sha256(json.dumps(database.reads).encode()).hexdigest(),
               'input_bson_sizes': source_sizes, 'input_sha256': original_data,
               'full_output': fingerprint(output),
               'source_input_unchanged': True, 'initial_state_unchanged': True,
               'owned_async_tasks_joined': True}
    assert summary['orders'] == 19151 and summary['items'] == 76604
    # The caller retains only normalized financial input, not fake DB fixtures.
    return output, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    destination = args.output_dir.resolve()
    assert ROOT not in destination.parents and destination != ROOT
    destination.mkdir(parents=True, exist_ok=True)
    assert not (destination / 'result.json').exists(), 'refuse a second full profile in this destination'
    assert git('rev-parse', 'HEAD') == EXPECTED_SOURCE
    assert not git('status', '--porcelain'), 'frozen source must remain clean'
    hashes = runtime_hashes()
    started = time.time()
    def bounded(_signal, _frame):
        raise TimeoutError('offline diagnostic exceeded its 240-second total bound')
    prior_handler = signal.signal(signal.SIGALRM, bounded)
    signal.alarm(240)
    try:
        state = initial(fixture_module.OWNER)
        state.update(status='active', started_at=fixture_module.START)
        original_state = fingerprint(state)
        print('Preparing and collecting one 19,151-order synthetic cohort.', flush=True)
        sources, collector = asyncio.run(collect(state))
        print('Collector complete; profiling one first reducer and one exact replay.', flush=True)
        first, first_metrics = measured_reconcile('first_reconcile', state, sources, destination)
        replay, replay_metrics = measured_reconcile('retained_replay', first, sources, destination)
        assert replay == first, 'same-source same-clock replay changed the valid retained state'
        assert fingerprint(state) == original_state
        assert runtime_hashes() == hashes
        assert git('rev-parse', 'HEAD') == EXPECTED_SOURCE and not git('status', '--porcelain')
        result = {'experiment': 'actual_collector_output_first_and_retained_reducer_profile',
                  'source_commit': EXPECTED_SOURCE, 'runtime_sha256': hashes,
                  'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'clock': fixture_module.NOW, 'started_at': fixture_module.START,
                  'elapsed_total_seconds_including_setup_and_hashes': time.time() - started,
                  'collector': collector, 'runs': [first_metrics, replay_metrics],
                  'same_clock_replay_equal': True, 'original_initial_state_unchanged': True,
                  'runtime_unchanged': True,
                  'limitations': [
                    'Synthetic processing-order distribution; live financial/history distribution is unknown.',
                    'Retained state is only the actual first reducer result; no invented or padded history.',
                    'No confirmed invoices/deliveries, manual movements, ads, wages or native statement history.',
                    'One instrumented cProfile call per mode; overhead prevents treating times as plain latency.',
                    'GC remains enabled; explicit untimed gc.collect precedes each reducer.',
                    'Inclusive cProfile times overlap and must not be added together.',
                    'RSS is cumulative one-process high-water, not an isolated peak comparison.',
                    'No concurrent profit/native evidence, service tail, packing, database writes or CAS.',
                    'No implication that the live 85-second refresh deadline is now sufficient.',
                  ]}
        (destination / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
        print('Profile and preservation assertions complete; no source changes or live access.', flush=True)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, prior_handler)


if __name__ == '__main__':
    main()
