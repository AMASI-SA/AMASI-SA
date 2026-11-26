"""One offline baseline/candidate pair for a retained provider-orders copy.

Preparation runs the existing real collector fixture once and retains only the
actual first reducer result and its normalized sources. Fresh measurement
children consume the identical serialized pair. Only the one provider-orders
map copier differs; all other runtime paths and financial inputs are unchanged.
No database, live request, source edit, deadline change, or repeated sweep.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
import gc
import hashlib
import json
import os
from pathlib import Path
import pickle
import resource
import signal
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time


ROOT = Path('/workspace/scratch/1f2d9548e6ff/mezan-refresh-source-root-fix')
OLD_PROFILE = Path('/workspace/scratch/1f2d9548e6ff/completion-release-prep/reducer-profile')
BASE = '33be2d0e1b51faa355d55707c0c374f4c1d6bbe2'
PROVIDER_PATH = 'backend/operational_configured_provider_fees.py'
PROVIDER_SHA = '7fc1f873b1726d323928a12c0a9370c0568318b43e7661a47058889c6f013cd9'
FIXTURE_PATH = ROOT / 'docs/operations/OPERATIONAL-REFRESH-COMPLETION-20261126/evidence/profile_source_preparation.py'
FIXTURE_SHA = '402c2a4df247373da7c06da004c81a64313cbb8e060996c62f863c9991dfc19b'
PROFILE_SHA = '1cbda56ef7fd0ad64fb9a9b10c33f30cbc69111ea2d6e5c547293f91b3f21fff'
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
sys.dont_write_bytecode = True


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True, timeout=20).strip()


def safety(event, args):
    if event in {'socket.connect', 'socket.connect_ex', 'socket.bind', 'socket.getaddrinfo'}:
        raise AssertionError('network access is forbidden in this offline measurement')
    if event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
        name = os.path.basename(os.fsdecode(args[0]))
        if name == '.env' or name.startswith('.env.'):
            raise AssertionError('credential configuration reads are forbidden')


def load_profile(expected_head):
    assert git('rev-parse', 'HEAD') == expected_head
    changed = set(git('diff', BASE, '--name-only', '--', 'backend').splitlines())
    assert changed <= {PROVIDER_PATH, 'backend/tests/test_operational_balance_provider_order_copy.py'}, changed
    assert sha(ROOT / PROVIDER_PATH) == PROVIDER_SHA
    assert sha(FIXTURE_PATH) == FIXTURE_SHA
    assert sha(OLD_PROFILE / 'profile_actual_collector_reducer.py') == PROFILE_SHA
    sys.path[:0] = [str(OLD_PROFILE), str(ROOT / 'backend'), str(ROOT / 'backend/tests')]
    import profile_actual_collector_reducer as profile
    return profile


def prepare(args):
    profile = load_profile(args.expected_head)
    import operational_configured_provider_fees as fees
    state = profile.initial(profile.fixture_module.OWNER)
    state.update(status='active', started_at=profile.fixture_module.START)
    runtime = profile.runtime_hashes()
    sources, collector = asyncio.run(profile.collect(state))
    original_copier = fees.copy_report_value
    try:
        # The retained fixture comes from the old map-copy behavior, not from
        # either measured child and not from padded or invented history.
        fees.copy_report_value = deepcopy
        retained = profile.engine.reconcile(state, sources, profile.fixture_module.NOW)
    finally:
        fees.copy_report_value = original_copier
    counts = profile.counts(retained)
    assert counts['provider_fee_orders'] == 14364
    assert counts['obligations'] == counts['audit'] == 90968
    pair = (retained, sources)
    payload = args.input_dir / 'retained-and-sources.pickle'
    with payload.open('xb') as stream:
        pickle.dump(pair, stream, protocol=5)
    with payload.open('rb') as stream:
        loaded_state, loaded_sources = pickle.load(stream)
    # Pickle is not a canonical identity encoding: loading can coalesce atomic
    # identities even when the complete values are unchanged. Bind the exact
    # pair bytes and prove round-trip value equality, then compare fresh-loaded
    # graphs consistently in both measured children. The focused regression
    # tests separately prove aliases/cycles/custom-copy semantics of the runtime.
    assert (loaded_state, loaded_sources) == pair, 'fixture round trip changed complete values'
    assert profile.counts(loaded_state) == counts
    assert profile.runtime_hashes() == runtime and git('rev-parse', 'HEAD') == args.expected_head
    write_json(args.input_dir / 'fixture.json', {
        'expected_head': args.expected_head, 'payload_sha256': sha(payload),
        'runtime': runtime, 'collector': collector, 'counts': counts,
        'retained_state': profile.fingerprint(loaded_state), 'sources': profile.fingerprint(loaded_sources),
        'before_serialization_retained_state': profile.fingerprint(retained),
        'before_serialization_sources': profile.fingerprint(sources),
        'complete_serialization_roundtrip_values_equal': True,
        'first_state_uses_standard_retained_map_copy': True,
    })
    print('Prepared one actual-collector retained fixture; no owned async tasks remain.', flush=True)


def rss_bytes():
    return int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')


def measure(args):
    profile = load_profile(args.expected_head)
    import operational_configured_provider_fees as fees
    import operational_report_copy as copying
    metadata = json.loads((args.input_dir / 'fixture.json').read_bytes())
    payload = args.input_dir / 'retained-and-sources.pickle'
    assert metadata['expected_head'] == args.expected_head and sha(payload) == metadata['payload_sha256']
    assert profile.runtime_hashes() == metadata['runtime']
    with payload.open('rb') as stream:
        state, sources = pickle.load(stream)
    before_state, before_sources = profile.fingerprint(state), profile.fingerprint(sources)
    assert before_state == metadata['retained_state'] and before_sources == metadata['sources']
    copy_metrics = {'calls': 0, 'wall_ms': 0.0, 'thread_cpu_ms': 0.0, 'orders': 0}
    helper_metrics = {'calls': 0, 'wall_ms': 0.0, 'thread_cpu_ms': 0.0}
    copier = deepcopy if args.phase == 'baseline' else copying.copy_report_value
    old_copy, old_refresh = fees.copy_report_value, fees.refresh_provider_fee_inputs

    def timed_copy(value):
        copy_metrics['calls'] += 1
        copy_metrics['orders'] += len(value)
        wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
        try:
            return copier(value)
        finally:
            copy_metrics['wall_ms'] += (time.perf_counter_ns() - wall) / 1e6
            copy_metrics['thread_cpu_ms'] += (time.thread_time_ns() - cpu) / 1e6

    def timed_refresh(*values, **kwargs):
        helper_metrics['calls'] += 1
        wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
        try:
            return old_refresh(*values, **kwargs)
        finally:
            helper_metrics['wall_ms'] += (time.perf_counter_ns() - wall) / 1e6
            helper_metrics['thread_cpu_ms'] += (time.thread_time_ns() - cpu) / 1e6

    gc_started, gc_times, gc_counts = {}, [], Counter()
    def observe_gc(event, info):
        generation = info['generation']
        if event == 'start':
            gc_started[generation] = time.perf_counter_ns()
        elif generation in gc_started:
            gc_times.append(time.perf_counter_ns() - gc_started.pop(generation))
            gc_counts[generation] += 1

    stop, ready = Event(), Event()
    samples = {'count': 0, 'peak_bytes': rss_bytes(), 'errors': []}
    def sample_rss():
        try:
            while True:
                samples['peak_bytes'] = max(samples['peak_bytes'], rss_bytes())
                samples['count'] += 1
                ready.set()
                if stop.wait(0.01):
                    break
        except BaseException as error:
            samples['errors'].append(type(error).__name__)
            ready.set()

    assert gc.isenabled()
    rss_before, gc_before = rss_bytes(), gc.get_stats()
    high_water_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    sampler = Thread(target=sample_rss, name='owned-rss-sampler', daemon=False)
    fees.copy_report_value, fees.refresh_provider_fee_inputs = timed_copy, timed_refresh
    gc.callbacks.append(observe_gc)
    try:
        sampler.start()
        assert ready.wait(5) and not samples['errors']
        wall, cpu, thread_cpu = time.perf_counter_ns(), time.process_time_ns(), time.thread_time_ns()
        try:
            result = profile.engine.reconcile(state, sources, profile.fixture_module.NOW)
        finally:
            wall_ms = (time.perf_counter_ns() - wall) / 1e6
            cpu_ms = (time.process_time_ns() - cpu) / 1e6
            thread_cpu_ms = (time.thread_time_ns() - thread_cpu) / 1e6
    finally:
        stop.set()
        if sampler.ident is not None:
            sampler.join(5)
        fees.copy_report_value, fees.refresh_provider_fee_inputs = old_copy, old_refresh
        gc.callbacks.remove(observe_gc)
    assert not sampler.is_alive() and not samples['errors']
    rss_after = rss_bytes()
    high_water_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    gc_after = gc.get_stats()
    assert copy_metrics['calls'] == helper_metrics['calls'] == 1 and copy_metrics['orders'] == 14364
    assert result == state, 'valid retained same-clock replay changed financial values'
    assert profile.fingerprint(state) == before_state and profile.fingerprint(sources) == before_sources
    new_orders = result['engine']['provider_fee_inputs']['orders']
    old_orders = state['engine']['provider_fee_inputs']['orders']
    assert new_orders is not old_orders
    for identity in list(old_orders)[:16]:
        assert new_orders[identity] is not old_orders[identity]
        assert new_orders[identity]['payment'] is not old_orders[identity]['payment']
    assert result['engine']['audit'][0]['after'] is not state['engine']['audit'][0]['after']
    assert profile.runtime_hashes() == metadata['runtime'] and git('rev-parse', 'HEAD') == args.expected_head
    measured = {
        'mode': args.phase, 'whole_retained_reducer_wall_ms': wall_ms,
        'whole_retained_reducer_process_cpu_ms': cpu_ms, 'whole_retained_reducer_thread_cpu_ms': thread_cpu_ms,
        'retained_map_copy': copy_metrics, 'provider_input_helper': helper_metrics,
        'rss_before_bytes': rss_before, 'rss_after_bytes': rss_after,
        'rss_peak_sampled_bytes': max(samples['peak_bytes'], rss_before, rss_after),
        'rss_samples': samples['count'], 'process_high_water_rss_kib_before': high_water_before,
        'process_high_water_rss_kib_after': high_water_after,
        'gc_wall_ms': sum(gc_times) / 1e6, 'gc_collections': dict(gc_counts),
        'gc_before': gc_before, 'gc_after': gc_after,
        'input_state': before_state, 'sources': before_sources, 'output': profile.fingerprint(result),
        'output_counts': profile.counts(result), 'same_clock_replay_values_equal': True,
        'input_state_and_sources_unchanged': True, 'sampled_nested_provider_and_audit_independence': True,
        'owned_sampler_joined': True, 'runtime_unchanged': True,
    }
    write_json(args.output_dir / (args.phase + '.json'), measured)
    print(json.dumps({'mode': args.phase, 'whole_reducer_ms': round(wall_ms, 3),
                      'map_ms': round(copy_metrics['wall_ms'], 3),
                      'helper_ms': round(helper_metrics['wall_ms'], 3)}), flush=True)


def join_owned(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()


def pair(args):
    assert args.output_dir.resolve() != ROOT and ROOT not in args.output_dir.resolve().parents
    args.output_dir.mkdir(parents=True, exist_ok=False)
    load_profile(args.expected_head)
    deadline = time.monotonic() + 240
    with tempfile.TemporaryDirectory(prefix='owned-provider-fixture-', dir=args.output_dir) as temporary:
        input_dir = Path(temporary)
        for phase in ('prepare', 'baseline', 'candidate'):
            process = None
            remaining = deadline - time.monotonic()
            assert remaining > 0, 'offline pair exhausted its 240-second child budget'
            try:
                process = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()),
                    '--phase', phase, '--expected-head', args.expected_head, '--input-dir', str(input_dir),
                    '--output-dir', str(args.output_dir.resolve())], cwd=ROOT,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
                stdout, stderr = process.communicate(timeout=remaining)
            except BaseException:
                if process is not None:
                    join_owned(process)
                raise
            (args.output_dir / (phase + '.stdout.txt')).write_text(stdout)
            (args.output_dir / (phase + '.stderr.txt')).write_text(stderr)
            assert process.returncode == 0, f'{phase} failed; inspect the saved bounded child log'
            print(stdout.strip(), flush=True)
        fixture = json.loads((input_dir / 'fixture.json').read_bytes())
        baseline = json.loads((args.output_dir / 'baseline.json').read_bytes())
        candidate = json.loads((args.output_dir / 'candidate.json').read_bytes())
        for key in ('input_state', 'sources', 'output', 'output_counts'):
            assert baseline[key] == candidate[key], f'pair parity failed: {key}'
        assert baseline['output_counts'] == fixture['counts']
        result = {'experiment': 'one_retained_provider_map_copy_pair', 'expected_head': args.expected_head,
            'base_deployment': BASE, 'script_sha256': sha(__file__), 'candidate_provider_sha256': PROVIDER_SHA,
            'fixture_sha256': FIXTURE_SHA, 'existing_profile_sha256': PROFILE_SHA,
            'fixture': fixture, 'baseline': baseline, 'candidate': candidate,
            'full_output_pickle_hash_equal': True, 'identical_retained_state_and_sources': True,
            'all_owned_children_joined': True, 'network_and_credential_reads_denied': True,
            'limitations': [
                'One baseline then one candidate in fresh processes; no repeated variance estimate.',
                'One fixed 19,151-order synthetic cohort; live financial/history mix differs.',
                'Actual first reducer state contains 90,968 obligations and audits; it is not padded.',
                'Serialization round-trip values are fully equal; admission fingerprints describe fresh-loaded graphs.',
                'Pickle may coalesce atomic identities; original alias/cycle/custom-copy contracts are covered by focused tests.',
                'Preparation/collector transport is excluded; only retained reducer replay is timed.',
                'Normal GC stays enabled with no explicit child GC collection or disable.',
                'RSS is sampled every 10 ms and may miss shorter peaks; process high-water is also recorded.',
                'Nested ownership sampling complements focused alias/BSON/failure tests; it is not a full graph scan.',
                'No service coverage/recovery proof, archive packing, CAS, DB, or concurrent production work is measured.',
                'A local reducer gain does not demonstrate completion within the production 85-second total budget.',
            ]}
        write_json(args.output_dir / 'result.json', result)
    print('One pair finished; temporary fixture removed and every owned child joined.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('pair', 'prepare', 'baseline', 'candidate'), default='pair')
    parser.add_argument('--expected-head', required=True)
    parser.add_argument('--input-dir', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    sys.addaudithook(safety)
    if args.phase == 'pair':
        pair(args)
    elif args.phase == 'prepare':
        prepare(args)
    else:
        measure(args)


if __name__ == '__main__':
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt('owned offline provider-copy measurement interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    main()
