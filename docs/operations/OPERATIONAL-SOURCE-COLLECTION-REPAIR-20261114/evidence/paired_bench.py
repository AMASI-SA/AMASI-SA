"""Bounded synthetic pairs; baseline is read from immutable J, with no database."""
import argparse
import ast
from datetime import datetime, timezone
from decimal import Decimal
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
BASE = '9c71435f4a19977c09f4331dee8fddbfa12a45eb'
LEGACY_PATH = 'backend/operational_delivery_evidence.py'
HELPER_PATH = 'backend/operational_courier_fee_history.py'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def baseline():
    raw = subprocess.check_output(['git', 'show', BASE + ':' + LEGACY_PATH], cwd=REPO)
    tree = ast.parse(raw)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'recorded_courier_fee')
    namespace = {'Decimal': Decimal}
    exec(compile(ast.Module(body=[function], type_ignores=[]), BASE + ':' + LEGACY_PATH, 'exec'), namespace)
    return namespace['recorded_courier_fee'], sha(raw)


def fingerprint(value):
    return sha(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def fixture(n, direct):
    from operational_balance_store import initial
    state = initial('synthetic-owner')
    state.update(status='active', started_at='2026-10-05T00:00:00+00:00')
    facts = {('shipping:' if direct else 'capture:') + str(i):
             ({'cost': '10.00', 'currency': 'SAR'} if direct else
              {'order_id': str(i), 'amount': '100.00', 'currency': 'SAR', 'provider': 'salla'})
             for i in range(n)}
    obligations = {'shipping:' + str(i): {'id': 'shipping:' + str(i), 'kind': 'shipping',
                   'confirmed': '0.00', 'estimated': '10.00', 'currency': 'SAR',
                   'party_type': 'courier', 'party_id': 'synthetic-carrier', 'direction': 'payable'}
                   for i in range(n)}
    state['engine'] = {'facts': facts, 'obligations': obligations}
    state['movements'] = [{'id': 'movement-' + str(i), 'kind': 'collection',
                          'direction': 'incoming', 'amount': '100.00', 'currency': 'SAR',
                          'allocations': [{'obligation_id': 'provider:' + str(i), 'amount': '100.00'}]}
                          for i in range(n)]
    return state


def worker(case, n, calls):
    assert 1 <= calls <= n <= 20000
    sys.path.insert(0, str(REPO / 'backend'))
    from operational_courier_fee_history import build_recorded_courier_fee_lookup
    from operational_demand_refresh import calculation_checkpoint
    calculation_checkpoint()  # The live request already has this module loaded.
    legacy, old_sha = baseline()
    helper_sha = sha((REPO / HELPER_PATH).read_bytes())
    state = fixture(n, case == 'direct')
    before = fingerprint(state)
    ids = [str(i) for i in range(calls)]

    clock, cpu = time.perf_counter(), time.process_time()
    old = [legacy(state, identity) for identity in ids]
    old_wall, old_cpu = time.perf_counter() - clock, time.process_time() - cpu
    assert fingerprint(state) == before

    clock, cpu = time.perf_counter(), time.process_time()
    lookup = build_recorded_courier_fee_lookup(state)
    build_wall, build_cpu = time.perf_counter() - clock, time.process_time() - cpu
    new = [lookup(identity) for identity in ids]
    new_wall, new_cpu = time.perf_counter() - clock, time.process_time() - cpu
    assert old == new == [case == 'direct'] * calls
    assert fingerprint(state) == before
    assert sha((REPO / HELPER_PATH).read_bytes()) == helper_sha
    print(json.dumps({'case': case, 'rows_per_history_group': n, 'calls': calls,
        'baseline_commit': BASE, 'baseline_source_sha256': old_sha, 'helper_sha256': helper_sha,
        'legacy_wall_seconds': old_wall, 'legacy_cpu_seconds': old_cpu,
        'indexed_total_wall_seconds': new_wall, 'indexed_total_cpu_seconds': new_cpu,
        'index_build_wall_seconds': build_wall, 'index_build_cpu_seconds': build_cpu,
        'indexed_lookup_wall_seconds': new_wall - build_wall,
        'wall_speedup_including_build': old_wall / new_wall,
        'all_results_equal_and_expected': True, 'inputs_unchanged': True,
        'input_sha256': before, 'database_access': False}), flush=True)


def run():
    os.umask(0o077)
    helper_sha = sha((REPO / HELPER_PATH).read_bytes())
    source, old_sha = baseline()
    del source
    meta = {'baseline_commit': BASE, 'baseline_source_sha256': old_sha,
            'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
            'helper_sha256': helper_sha, 'harness_sha256': sha(Path(__file__).read_bytes()),
            'cwd': str(REPO), 'database_access': False}
    assert meta['head'] == BASE
    with (ROOT / 'paired-identity.json').open('x') as stream:
        stream.write(json.dumps(meta, indent=2) + '\n')
    jobs = [('absent', 1000, 1000), ('absent', 4000, 4000),
            ('absent', 20000, 250), ('direct', 20000, 20000)]
    for case, n, calls in jobs:
        assert sha((REPO / HELPER_PATH).read_bytes()) == helper_sha
        label = f'paired-{case}-{n}-{calls}'
        command = [sys.executable, '-B', str(Path(__file__).resolve()), '--child', case,
                   '--rows', str(n), '--calls', str(calls)]
        env = os.environ.copy()
        env.update(PYTHONDONTWRITEBYTECODE='1', PYTHON_DOTENV_DISABLED='1')
        started = datetime.now(timezone.utc).isoformat()
        print(json.dumps({'label': label, 'phase': 'started', 'started_at': started}), flush=True)
        clock = time.monotonic()
        timed_out = False
        with (ROOT / (label + '.stdout')).open('xb') as out, (ROOT / (label + '.stderr')).open('xb') as err:
            child = subprocess.Popen(command, stdout=out, stderr=err, env=env, cwd=REPO, start_new_session=True)
            try:
                actual_exit = child.wait(timeout=60)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(child.pid, signal.SIGKILL)
                actual_exit = child.wait()
        result = {'label': label, 'argv': command, 'started_at': started,
                  'elapsed_seconds': time.monotonic() - clock, 'actual_exit': actual_exit,
                  'timeout_seconds': 60, 'timed_out': timed_out,
                  'stdout_sha256': sha((ROOT / (label + '.stdout')).read_bytes()),
                  'stderr_sha256': sha((ROOT / (label + '.stderr')).read_bytes())}
        with (ROOT / (label + '.result.json')).open('x') as stream:
            stream.write(json.dumps(result, indent=2) + '\n')
        # The child's actual outcome is durable before parsing or asserting it.
        if actual_exit == 0:
            result['measurement'] = json.loads((ROOT / (label + '.stdout')).read_bytes())
        print(json.dumps(result), flush=True)
        if actual_exit != 0:
            raise SystemExit(actual_exit)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--child', choices=('absent', 'direct'))
    parser.add_argument('--rows', type=int)
    parser.add_argument('--calls', type=int)
    args = parser.parse_args()
    if args.child:
        worker(args.child, args.rows, args.calls)
    else:
        run()
