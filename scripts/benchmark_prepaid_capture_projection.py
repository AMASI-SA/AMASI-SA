"""Compare historical and candidate capture for identical synthetic event prefixes.

Historical-base study only: this branch is not a production release candidate.
No server, database, dotenv or live source access occurs.
"""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['GIT_NO_LAZY_FETCH'] = '1'
import argparse
import json
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'backend/tests')]
SOURCE = 'de76aed339be775e2878071c92063d0ab9df06df'


def forbidden(*_args, **_kwargs):
    raise RuntimeError('No network in synthetic prepaid benchmark')


def run(module, history, count):
    import operational_balance_service as service
    from test_operational_balance_activity_cash_projection import state_fixture, receipt, STAMP
    from test_operational_balance_activity_prepaid_projection import prepaid
    state = state_fixture()
    state['movements'] = [receipt(i) for i in range(history)]
    module.now = lambda: STAMP
    original = service.report
    calls = []
    service.report = lambda *args, **kw: (calls.append(1), original(*args, **kw))[1]
    snapshots = []
    started = time.perf_counter()
    try:
        for i in range(count):
            event = prepaid(state, i)
            module.capture_after_balances(state, event)
            snapshots.append(event['financial_activity_snapshots'])
            state['audit'].append(event)
    finally:
        service.report = original
    elapsed = time.perf_counter() - started
    return elapsed, snapshots, state, len(calls)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--history', type=int, default=5000)
    parser.add_argument('--count', type=int, default=100)
    args = parser.parse_args()
    if min(args.repeat, args.history, args.count) < 1:
        parser.error('sizes must be positive')
    socket.socket.connect = forbidden
    socket.create_connection = forbidden
    import operational_financial_activity as candidate
    original = types.ModuleType('historical_prepaid_capture')
    code = subprocess.check_output(['git', 'show', SOURCE + ':backend/operational_financial_activity.py'], cwd=ROOT, text=True)
    exec(compile(code, SOURCE + ':activity.py', 'exec'), original.__dict__)
    # Warm normal calculation imports before timing either version.
    run(candidate, 1, 1)
    run(original, 1, 1)
    before, after = [], []
    for _ in range(args.repeat):
        b, bs, state_before, bc = run(original, args.history, args.count)
        a, ac, state_after, cc = run(candidate, args.history, args.count)
        assert bs == ac and state_before == state_after
        assert bc == args.count and cc == 0
        before.append(b)
        after.append(a)
    print(json.dumps({'historical_source': SOURCE, 'history': args.history, 'charges': args.count,
        'baseline_seconds': before, 'candidate_seconds': after,
        'baseline_median': statistics.median(before), 'candidate_median': statistics.median(after),
        'all_prefix_snapshots_and_state_equal': True, 'baseline_full_report_calls': args.count,
        'candidate_full_report_calls': 0, 'production_effect': 'unverified; synthetic only'}))


if __name__ == '__main__':
    main()
