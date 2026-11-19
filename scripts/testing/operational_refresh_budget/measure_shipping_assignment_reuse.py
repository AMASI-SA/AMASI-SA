"""Synthetic exact-original fee-source benchmark. No network, DB, or source writes.

The baseline functions are loaded unchanged from the pinned local Git revision.
Timing uses plain dictionaries; row-access instrumentation is measured separately.
The candidate includes one index build even though collect_sources already owns it.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from statistics import median
import subprocess
import sys
from time import perf_counter
import types


BASE = 'c3c1cd26d93ab24d6e08003278b9910526055cc7'
FILES = {
    'prepaid': 'backend/operational_prepaid_shipping.py',
    'deferred': 'backend/operational_completed_shipping.py',
}


def sha(value):
    return hashlib.sha256(value).hexdigest()


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def frozen(repo, path, kind):
    content = git(repo, 'show', BASE + ':' + path)
    module = types.ModuleType('frozen_shipping_' + kind)
    exec(compile(content, BASE + ':' + path, 'exec'), module.__dict__)
    return getattr(module, 'source_event' if kind == 'prepaid' else 'deferred_source_event'), sha(content)


def measure(kind, call, producer, assignment, indexed, calls, size, *, candidate, instrument=False):
    rows = [assignment(id=str(i), order_id='unrelated-' + str(i),
                       order_number='unrelated-' + str(i)) for i in range(size)]
    _, args = producer(kind, rows)
    original = deepcopy(args)
    row_ids = {id(row) for row in rows}
    visits = 0

    def profile(_frame, event, function):
        nonlocal visits
        if (event == 'c_call' and getattr(function, '__name__', '') == 'get'
                and id(getattr(function, '__self__', None)) in row_ids):
            visits += 1

    previous = sys.getprofile()
    if instrument:
        sys.setprofile(profile)
    started = perf_counter()
    try:
        kwargs = {'review_only': True}
        if candidate:
            kwargs['assignment_index'] = indexed.build_delivery_assignment_index(rows, 'owner')
        result = None
        for _ in range(calls):
            result = call(*args, **kwargs)
        elapsed = perf_counter() - started
    finally:
        if instrument:
            sys.setprofile(previous)
    assert args == original
    event = result[1] if kind == 'prepaid' else result
    assert event['amount'] == '34.50'
    return {'elapsed_seconds': elapsed, 'assignment_row_gets': visits, 'event': event}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--calls', type=int, default=5000)
    parser.add_argument('--assignments', type=int, default=20000)
    parser.add_argument('--runs', type=int, default=3)
    options = parser.parse_args()
    repo = options.repo.resolve()
    assert options.calls > 0 and 0 < options.assignments <= 20000 and options.runs > 0
    sys.path[:0] = [str(repo), str(repo / 'backend'), str(repo / 'backend/tests')]
    import operational_delivery_assignment_index as indexed
    from test_operational_balance_shipping_assignment_reuse import producer, assignment

    result = {
        'synthetic_only': True,
        'scope': 'Repeated pure source calls over one unchanged synthetic assignment cohort',
        'baseline_revision': BASE,
        'candidate_base_revision': git(repo, 'rev-parse', 'HEAD').decode().strip(),
        'harness_sha256': sha(Path(__file__).read_bytes()),
        'python': sys.version.split()[0],
        'baseline_file_sha256': {},
        'candidate_file_sha256': {},
        'calls_per_run': options.calls,
        'assignments': options.assignments,
        'runs': options.runs,
        'candidate_index_build_included': True,
        'timing_instrumented': False,
        'measurements': [],
    }
    paths = [*FILES.values(), 'backend/operational_delivery_assignment_index.py',
             'backend/operational_balance_sources.py',
             'backend/tests/test_operational_balance_shipping_assignment_reuse.py']
    result['candidate_file_sha256'] = {path: sha((repo / path).read_bytes()) for path in paths}
    for kind, path in FILES.items():
        baseline, original_sha = frozen(repo, path, kind)
        result['baseline_file_sha256'][path] = original_sha
        candidate, _ = producer(kind, [])
        for call, use_index in ((baseline, False), (candidate, True)):
            measure(kind, call, producer, assignment, indexed, 1, 1, candidate=use_index)
        before, after = [], []
        last = None
        for _ in range(options.runs):
            old = measure(kind, baseline, producer, assignment, indexed,
                          options.calls, options.assignments, candidate=False)
            new = measure(kind, candidate, producer, assignment, indexed,
                          options.calls, options.assignments, candidate=True)
            assert old['event'] == new['event']
            before.append(old['elapsed_seconds'])
            after.append(new['elapsed_seconds'])
            last = new['event']
        old_count = measure(kind, baseline, producer, assignment, indexed, 128, 128,
                            candidate=False, instrument=True)
        new_count = measure(kind, candidate, producer, assignment, indexed, 128, 128,
                            candidate=True, instrument=True)
        assert old_count['event'] == new_count['event']
        assert new_count['assignment_row_gets'] <= 128 * 12 < old_count['assignment_row_gets']
        row = {
            'producer': kind,
            'original_seconds': before,
            'candidate_seconds': after,
            'original_median_seconds': median(before),
            'candidate_median_seconds': median(after),
            'exact_event_equal': True,
            'unchanged_inputs': True,
            'known_event_amount_sar': last['amount'],
            'event_sha256': sha(json.dumps(last, sort_keys=True, ensure_ascii=False).encode()),
            'separate_work_count': {
                'calls': 128, 'assignments': 128,
                'original_assignment_row_gets': old_count['assignment_row_gets'],
                'candidate_assignment_row_gets': new_count['assignment_row_gets'],
                'budget': 128 * 12,
                'original_fails_budget_candidate_passes': True,
            },
        }
        result['measurements'].append(row)
        print(json.dumps(row, sort_keys=True), flush=True)
    for path, expected in result['candidate_file_sha256'].items():
        assert sha((repo / path).read_bytes()) == expected, 'source changed during benchmark: ' + path
    payload = (json.dumps(result, indent=2, sort_keys=True) + '\n').encode()
    fd = os.open(options.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(payload)
    print(json.dumps({'output': str(options.output), 'sha256': sha(payload),
                      'baseline_file_sha256': result['baseline_file_sha256']}), flush=True)


if __name__ == '__main__':
    main()
