"""One fresh-process offline collector A/B for a private native item view.

Only operational_remaining_bundle_sources' sole deepcopy(item) call is replaced
inside the candidate collector invocation. Public source/APIs remain unchanged.
The candidate admits exact JSON trees; aliases, cycles, hooks and other types
retain the original deepcopy at its original point in normalization.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import pickle
import resource
import signal
import subprocess
import sys
import time


ROOT = Path('/workspace/scratch/1f2d9548e6ff/mezan-refresh-source-root-fix')
EVIDENCE = ROOT / 'docs/operations/OPERATIONAL-REFRESH-COMPLETION-20261126/evidence'
SOURCE = '7b4da05fba49d7b2e923ae3ac304af7e15dbcaa6'
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
sys.dont_write_bytecode = True
sys.path[:0] = [str(EVIDENCE), str(ROOT / 'backend'), str(ROOT / 'backend/tests')]

import profile_source_preparation as fixture
from bson import ObjectId
import operational_remaining_bundle_sources as bundles
from operational_balance_store import initial


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()


def source_hashes():
    paths = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '-z', '--', 'backend'])
    values = {}
    for name in sorted(paths.split(b'\0')):
        if name.endswith(b'.py') and b'/tests/' not in name:
            path = name.decode()
            values[path] = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
    return values


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


def native_tree(value):
    """Inspect only exact builtins; never call user conversions or copy hooks."""
    if type(value) is not dict:
        return False, 0, 'root_type'
    pending, seen = [value], set()
    while pending:
        item = pending.pop()
        kind = type(item)
        if kind is str or kind is int or kind is bool or kind is type(None):
            continue
        if kind is float:
            if not math.isfinite(item):
                return False, len(seen), 'non_json_float'
            continue
        if kind is not dict and kind is not list:
            return False, len(seen), 'custom_or_non_json_value'
        identity = id(item)
        if identity in seen:
            return False, len(seen), 'repeated_container_or_cycle'
        seen.add(identity)
        if kind is dict:
            if any(type(key) is not str for key in item):
                return False, len(seen), 'custom_or_non_string_key'
            pending.extend(item.values())
        else:
            pending.extend(item)
    return True, len(seen), None


@contextmanager
def item_copy(enabled, counts):
    original = bundles.deepcopy
    def copy(value):
        counts['calls'] += 1
        if enabled:
            accepted, containers, reason = native_tree(value)
            counts['containers_examined'] += containers
            counts['max_containers_per_item'] = max(counts['max_containers_per_item'], containers)
            if accepted:
                counts['borrowed_items'] += 1
                # For an admitted acyclic, non-aliased exact dict/list tree,
                # stdlib deepcopy would create one container per tree node.
                # This new plain root replaces that root copy, leaving N-1
                # nested container clones unnecessary. No graph is retained.
                counts['nested_container_clones_avoided'] += containers - 1
                return dict(value)
            counts['fallback_reasons'][reason] += 1
        counts['full_item_deepcopies'] += 1
        return original(value)
    bundles.deepcopy = copy
    try:
        yield
    finally:
        bundles.deepcopy = original


def validate_prototype():
    """Tiny untimed admission controls; no collector, database or timing sweep."""
    tree = {'a': [{'b': 1}], 'c': None, 'd': 1.5}
    assert native_tree(tree) == (True, 3, None)
    shared = []
    assert not native_tree({'a': shared, 'b': shared})[0]
    cyclic = {}; cyclic['self'] = cyclic
    assert not native_tree(cyclic)[0]
    class Mapping(dict):
        def items(self): raise AssertionError('guard invoked custom items')
    class Text(str):
        def __str__(self): raise AssertionError('guard invoked custom conversion')
    class Hook:
        def __deepcopy__(self, memo): return {'original_hook': True}
    class Meta(type):
        def __eq__(self, other): raise AssertionError('guard invoked metaclass equality')
        def __hash__(self): raise AssertionError('guard invoked metaclass hashing')
    class Custom(metaclass=Meta):
        pass
    assert not native_tree(Mapping(a=1))[0]
    assert not native_tree({Text('key'): 1})[0]
    assert not native_tree({'a': Text('value')})[0]
    assert not native_tree({'a': Custom()})[0]
    for value in (b'bytes', datetime(2026, 1, 1), ObjectId('000000000000000000000001'),
                  float('nan'), float('inf'), Hook()):
        assert not native_tree({'a': value})[0]
    counts = copy_counts()
    with item_copy(True, counts):
        accepted = bundles.deepcopy(tree)
        assert accepted == tree and accepted is not tree and accepted['a'] is tree['a']
        copied = bundles.deepcopy({'a': shared, 'b': shared})
        assert copied['a'] is copied['b'] and copied['a'] is not shared
        cycle_copy = bundles.deepcopy(cyclic)
        assert cycle_copy is cycle_copy['self'] and cycle_copy is not cyclic
        assert bundles.deepcopy({'a': Hook()}) == {'a': {'original_hook': True}}
    return {'native_tree_admitted': True, 'aliases_cycles_custom_types_rejected': True,
            'original_deepcopy_fallback_preserved': True}


def copy_counts():
    return {'calls': 0, 'borrowed_items': 0, 'full_item_deepcopies': 0,
            'containers_examined': 0, 'max_containers_per_item': 0,
            'nested_container_clones_avoided': 0, 'fallback_reasons': Counter()}


def assert_patch_site():
    path = ROOT / 'backend/operational_remaining_bundle_sources.py'
    tree = ast.parse(path.read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == 'deepcopy']
    assert len(calls) == 1 and calls[0].lineno == 74
    assert isinstance(calls[0].args[0], ast.Name) and calls[0].args[0].id == 'item'


async def collect(mode):
    data = fixture.fixture()
    for index, binding in enumerate(data['mezan_product_option_cost_bindings_v2']):
        binding.update(_id=ObjectId(f'{index + 1:024x}'),
                       created_at=datetime(2026, 11, 1),
                       updated_at=datetime(2026, 11, 2, tzinfo=timezone.utc))
    before_data, sizes = fixture.source_fingerprint(data)
    state = initial(fixture.OWNER)
    state.update(status='active', started_at=fixture.START)
    before_state = fingerprint(state)
    database = fixture.collector_database(data)
    metrics = {'mode': mode, 'helpers': {}, 'stages': {}}
    copies = copy_counts()
    gc_start, ticks = {}, []
    def observe_gc(phase, info):
        generation = info['generation']
        if phase == 'start':
            gc_start[generation] = time.perf_counter_ns()
        elif generation in gc_start:
            ticks.append((generation, time.perf_counter_ns() - gc_start.pop(generation)))
    original_copy = bundles.deepcopy
    gc.collect()
    metrics['gc_before'] = gc.get_stats()
    gc.callbacks.append(observe_gc)
    try:
        # Neither label is the old fixture script's literal 'candidate', so
        # its rejected option/payment/bundle-copy prototypes are never installed.
        with item_copy(mode == 'private_view', copies), fixture.instrumentation('bundle_' + mode, metrics):
            wall, cpu = time.perf_counter_ns(), time.process_time_ns()
            result = await asyncio.wait_for(fixture.sources.collect_sources(
                database, fixture.OWNER, fixture.START, fixture.NOW,
                operational_state=state), timeout=85)
            metrics['wall_ms'] = (time.perf_counter_ns() - wall) / 1e6
            metrics['process_cpu_ms'] = (time.process_time_ns() - cpu) / 1e6
    finally:
        gc.callbacks.remove(observe_gc)
    assert bundles.deepcopy is original_copy
    assert fixture.source_fingerprint(data)[0] == before_data
    assert fingerprint(state) == before_state
    assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    assert len(result['orders']) == 19151
    assert sum(len(order['items']) for order in result['orders']) == 76604
    assert copies['calls'] == 76604
    if mode == 'private_view':
        assert copies['borrowed_items'] == 76604 and copies['full_item_deepcopies'] == 0
    else:
        assert copies['full_item_deepcopies'] == 76604 and copies['borrowed_items'] == 0
    metrics.update(copy_counts=copies, gc_after=gc.get_stats(),
        gc_wall_ms=sum(value for _, value in ticks) / 1e6,
        gc_collections=dict(Counter(generation for generation, _ in ticks)),
        process_high_water_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        output_orders=len(result['orders']), output_items=sum(len(row['items']) for row in result['orders']),
        output_issues=dict(Counter(row['code'] for row in result['issues'])),
        queries_by_collection=dict(Counter(database.reads)),
        query_sequence_sha256=hashlib.sha256(json.dumps(database.reads).encode()).hexdigest(),
        output=fingerprint(result), input_sha256=before_data, source_bson_bytes=sizes,
        source_input_unchanged=True, original_state_unchanged=True,
        public_copy_restored=True, owned_async_tasks_joined=True)
    for row in metrics['helpers'].values():
        row['wall_ms'] = round(row.pop('wall_ns') / 1e6, 3)
        row['thread_cpu_ms'] = round(row.pop('thread_cpu_ns') / 1e6, 3)
    return result, metrics


def worker(mode, destination):
    assert git('rev-parse', 'HEAD') == SOURCE and not git('status', '--porcelain')
    assert_patch_site()
    controls = validate_prototype()
    before = source_hashes()
    result, metrics = asyncio.run(collect(mode))
    assert source_hashes() == before
    assert git('rev-parse', 'HEAD') == SOURCE and not git('status', '--porcelain')
    metrics.update(source_commit=SOURCE, runtime_sha256=before, runtime_unchanged=True,
                   guard_controls=controls)
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / 'output.pickle').open('wb') as output:
        pickle.dump(result, output, protocol=5)
    (destination / 'metrics.json').write_text(json.dumps(metrics, indent=2) + '\n')
    print(json.dumps({'mode': mode, 'wall_ms': metrics['wall_ms'],
                      'process_cpu_ms': metrics['process_cpu_ms'],
                      'normalization_ms': metrics['stages']['bundle_order_normalization']['wall_ms'],
                      'full_item_deepcopies': metrics['copy_counts']['full_item_deepcopies']}), flush=True)


def run_child(mode, destination):
    command = [sys.executable, '-B', str(Path(__file__).resolve()),
               '--worker', mode, '--output-dir', str(destination)]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=135)
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            output, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            output, _ = process.communicate()
        (destination.parent / f'{mode}.log').write_text(output)
        raise
    (destination.parent / f'{mode}.log').write_text(output)
    print(output, end='', flush=True)
    if process.returncode != 0:
        raise RuntimeError(f'{mode} exited {process.returncode}; no automatic retry')


def parent(destination):
    destination.mkdir(parents=True, exist_ok=False)
    for mode in ('baseline', 'private_view'):
        print(f'Starting sole full collector {mode} in a fresh process.', flush=True)
        run_child(mode, destination / mode)
    runs = [json.loads((destination / mode / 'metrics.json').read_text())
            for mode in ('baseline', 'private_view')]
    with (destination / 'baseline/output.pickle').open('rb') as handle:
        baseline = pickle.load(handle)
    with (destination / 'private_view/output.pickle').open('rb') as handle:
        candidate = pickle.load(handle)
    assert baseline == candidate
    assert runs[0]['output'] == runs[1]['output']
    for key in ('input_sha256', 'query_sequence_sha256', 'queries_by_collection', 'runtime_sha256'):
        assert runs[0][key] == runs[1][key], key
    assert git('rev-parse', 'HEAD') == SOURCE and not git('status', '--porcelain')
    result = {'experiment': 'single_fresh_process_private_bundle_item_view_pair',
        'source_commit': SOURCE, 'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'runs': runs, 'complete_output_equal': True, 'complete_serialized_output_equal': True,
        'input_hashes_equal_and_preserved': True, 'query_sequences_equal': True,
        'source_hashes_equal_and_preserved': True, 'both_children_joined': True,
        'unchanged': ['all source query/projection/cap/cohort contracts',
                      'actual option authority and payment read-view opt-ins',
                      'all option/custom-field normalizers and selection/fingerprint helpers',
                      'authority, receipt/unit mapping and final financial projection'],
        'limitations': [
            'Synthetic cohort; actual live item shapes and bundle/history frequencies are unknown.',
            'No selected physical/financial bundle or supplier history in this large timing fixture.',
            'One fresh-process A/B pair; no repeated-sample stability or production guarantee.',
            'GC stays enabled; explicit untimed gc.collect precedes each collector.',
            'Inclusive helper/stage timings overlap and must not be summed.',
            'Per-process RSS includes imports/fixture construction, not isolated normalization allocation.',
            'Avoided nested container clone count applies only to admitted exact acyclic JSON trees.',
            'No full graph mutation snapshot or retained cross-request cache is created.',
            'No real Mongo transport, concurrent profit/native evidence, reconciliation or financial save.',
            'The85s limit bounds a synthetic source call, not the complete production request.',
            'Runtime adoption still requires populated financial, malformed, alias/hook and cancellation parity.',
        ]}
    (destination / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print('Single pair complete; outputs, queries, inputs and frozen source match.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--worker', choices=('baseline', 'private_view'))
    args = parser.parse_args()
    destination = args.output_dir.resolve()
    assert ROOT not in destination.parents and destination != ROOT
    if args.worker:
        worker(args.worker, destination)
    else:
        def stopped(_signal, _frame):
            raise KeyboardInterrupt('offline pair interrupted')
        previous = signal.signal(signal.SIGTERM, stopped)
        try:
            parent(destination)
        finally:
            signal.signal(signal.SIGTERM, previous)
