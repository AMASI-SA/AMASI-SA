"""Synthetic source-collection CPU measurements; never opens a database."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
REPO = Path('/workspace/scratch/019fa8a18b3d/mezan-source-collection-fix')
EXPECTED = '9c71435f4a19977c09f4331dee8fddbfa12a45eb'
FILES = ('backend/operational_delivery_evidence.py', 'backend/operational_balance_sources.py',
         'backend/operational_product_cost_policy.py')

def sha(value):
    return hashlib.sha256(value).hexdigest()

def fingerprint(value):
    return sha(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode())

def identity():
    return {name: sha((REPO / name).read_bytes()) for name in FILES}

class Rows(list):
    def __init__(self, values, counts, key):
        super().__init__(values); self.counts, self.key = counts, key
    def __iter__(self):
        for value in super().__iter__():
            self.counts[self.key] += 1
            yield value

class Mapping(dict):
    def __init__(self, values, counts, key):
        super().__init__(values); self.counts, self.key = counts, key
    def __iter__(self):
        for key in super().__iter__():
            self.counts[self.key] += 1
            yield key
    def items(self):
        for item in super().items():
            self.counts[self.key + '_items'] += 1
            yield item

def worker(case, n, calls, instrument):
    assert 1 <= calls <= n <= 20000
    sys.path.insert(0, str(REPO / 'backend'))
    from operational_delivery_evidence import recorded_courier_fee, _driver_assignment
    from operational_balance_sources import cost_components
    from operational_balance_store import initial
    counts = Counter()
    def counted(values, key):
        return Rows(values, counts, key) if instrument else values
    if case.startswith('fee_'):
        state = initial('synthetic-owner')
        state.update(status='active', started_at='2026-10-05T00:00:00+00:00')
        facts = {('shipping:' if case == 'fee_direct' else 'capture:') + str(i):
                 ({'cost': '10.00', 'currency': 'SAR'} if case == 'fee_direct' else
                  {'order_id': str(i), 'amount': '100.00', 'currency': 'SAR', 'provider': 'salla'})
                 for i in range(n)}
        obligations = {'shipping:' + str(i): {'id': 'shipping:' + str(i), 'kind': 'shipping',
                       'confirmed': '0.00', 'estimated': '10.00', 'currency': 'SAR',
                       'party_type': 'courier', 'party_id': 'synthetic-carrier', 'direction': 'payable'}
                       for i in range(n)}
        state['engine'] = {'facts': Mapping(facts, counts, 'fact_key_visits') if instrument else facts,
                          'obligations': Mapping(obligations, counts, 'obligation_visits') if instrument else obligations}
        state['movements'] = counted([{'id': 'movement-' + str(i), 'kind': 'collection',
                                      'direction': 'incoming', 'amount': '100.00', 'currency': 'SAR',
                                      'allocations': counted([{'obligation_id': 'provider:' + str(i), 'amount': '100.00'}],
                                                             'allocation_visits')} for i in range(n)], 'movement_visits')
        data = state
        def operation(i):
            assert recorded_courier_fee(state, str(i)) is (case == 'fee_direct')
    elif case == 'driver':
        assignments = counted([{'id': 'a' + str(i), 'user_id': 'synthetic-owner',
                               'order_id': str(i), 'order_number': str(100000 + i),
                               'driver_id': 'd' + str(i % 20), 'active': True, 'status': 'delivered',
                               'delivered_at': '2026-10-06T12:00:00+00:00'} for i in range(n)], 'assignment_visits')
        data = assignments
        def operation(i):
            assert _driver_assignment(assignments, 'synthetic-owner', str(i), str(100000 + i)) is assignments[i]
    else:
        data = {
            'mezan_cost_resources_v2': counted([{'id': 'r' + str(i), 'user_id': 'synthetic-owner',
                'unit_cost': '1.00', 'kind': 'material', 'status': 'active', 'cost_currency': 'SAR'}
                for i in range(n)], 'resource_visits'),
            'mezan_product_resource_bindings_v2': counted([{'id': 'b' + str(i), 'user_id': 'synthetic-owner',
                'salla_product_id': str(i), 'resource_id': 'r' + str(i), 'quantity': '1'}
                for i in range(n)], 'resource_binding_visits'),
            'mezan_product_option_cost_bindings_v2': counted([{'id': 'c' + str(i), 'user_id': 'synthetic-owner',
                'salla_product_id': str(i), 'option_id': 'color', 'value_id': 'red', 'mode': 'direct',
                'direct_amount': '3', 'quantity': '1', 'cost_currency': 'SAR'} for i in range(n)], 'option_binding_visits'),
            'mezan_products_v2': [], '_option_catalog_errors': set(),
        }
        profile = {'base_cost': '20.00', 'cost_currency': 'SAR'}
        def operation(i):
            value, services = cost_components(data, str(i), {'id': 'item-' + str(i), 'quantity': '1', 'options': []}, profile)
            assert str(value) == '21.00' and services == {}
    before = fingerprint(data)
    counts.clear()
    started, cpu = time.perf_counter(), time.process_time()
    for i in range(calls):
        operation(i)
    wall, used = time.perf_counter() - started, time.process_time() - cpu
    measured_counts = dict(counts)
    assert fingerprint(data) == before, 'synthetic inputs changed'
    print(json.dumps({'case': case, 'rows_per_collection': n, 'calls': calls, 'instrumented': instrument,
        'wall_seconds': wall, 'cpu_seconds': used, 'operation_counts': measured_counts,
        'all_results_expected': True, 'inputs_unchanged': True, 'input_sha256': before,
        'source_hashes': identity(), 'database_access': False}), flush=True)

def run_jobs(suite):
    os.umask(0o077)
    meta = {'expected_commit': EXPECTED,
            'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
            'tree': subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=REPO, text=True).strip(),
            'source_hashes': identity(), 'benchmark_sha256': sha(Path(__file__).read_bytes())}
    assert meta['head'] == EXPECTED, 'checkout moved before measurements'
    (ROOT / (suite + '-identity.json')).write_text(json.dumps(meta, indent=2) + '\n')
    cases = ['fee_absent'] if suite == 'fee' else ['driver', 'cost']
    jobs = [(case, n, n, True) for case in cases for n in (64, 128)]
    jobs += [(case, n, n, False) for case in cases for n in ((1000, 4000) if suite == 'fee' else (1000, 2000))]
    jobs += [(case, 20000, 250, False) for case in cases]
    if suite == 'fee':
        jobs += [('fee_direct', 20000, 20000, False)]
    for case, n, calls, instrument in jobs:
        assert identity() == meta['source_hashes'], 'source changed during measurements'
        label = f'{case}-{n}-{calls}-' + ('counts' if instrument else 'timing')
        command = [sys.executable, '-B', str(Path(__file__).resolve()), '--case', case,
                   '--rows', str(n), '--calls', str(calls)] + (['--instrument'] if instrument else [])
        env = os.environ.copy()
        env.update(PYTHONDONTWRITEBYTECODE='1', PYTHON_DOTENV_DISABLED='1')
        started = datetime.now(timezone.utc).isoformat()
        clock = time.monotonic()
        timed_out = False
        with (ROOT / (label + '.stdout')).open('xb') as out, (ROOT / (label + '.stderr')).open('xb') as err:
            child = subprocess.Popen(command, stdout=out, stderr=err, env=env, cwd=REPO, start_new_session=True)
            try:
                exit_code = child.wait(timeout=35)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(child.pid, signal.SIGKILL)
                exit_code = child.wait()
        result = {'label': label, 'argv': command, 'started_at': started,
                  'elapsed_seconds': time.monotonic() - clock, 'actual_exit': exit_code,
                  'timeout_seconds': 35, 'timed_out': timed_out,
                  'stdout_sha256': sha((ROOT / (label + '.stdout')).read_bytes()),
                  'stderr_sha256': sha((ROOT / (label + '.stderr')).read_bytes())}
        (ROOT / (label + '.result.json')).write_text(json.dumps(result, indent=2) + '\n')
        if exit_code == 0:
            result['measurement'] = json.loads((ROOT / (label + '.stdout')).read_bytes())
        print(json.dumps(result), flush=True)
        if exit_code != 0 and not timed_out:
            raise SystemExit(exit_code)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', choices=('fee', 'remaining'))
    parser.add_argument('--case', choices=('fee_absent', 'fee_direct', 'driver', 'cost'))
    parser.add_argument('--rows', type=int)
    parser.add_argument('--calls', type=int)
    parser.add_argument('--instrument', action='store_true')
    args = parser.parse_args()
    if args.suite:
        run_jobs(args.suite)
    else:
        worker(args.case, args.rows, args.calls, args.instrument)
