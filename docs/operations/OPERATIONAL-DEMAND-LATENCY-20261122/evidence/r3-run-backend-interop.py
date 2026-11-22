"""R3 focused acceptance using the repository's owned loopback Mongo fixture."""
import sys
sys.dont_write_bytecode = True

import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import yaml

REPO = Path('/workspace/scratch/e3ebab9db878/mezan-latency-r3')
OUTPUT = Path('/workspace/scratch/e3ebab9db878/r3-validation')
BASE = '24270ebdfdebb83282389308a2349cdb27e2b8e4'
MONGOD = '/workspace/scratch/36b1bc151c92/mezan-test-mongo/bin/mongod'
SELECTORS = [
    'backend/tests/test_demand_read_store.py',
    'backend/tests/test_demand_read_jobs.py',
    'backend/tests/test_demand_read_runner_lifecycle.py',
    'backend/tests/test_operational_workspace_demand_reads.py',
    'backend/tests/test_operational_workspace_demand_http.py',
    'backend/tests/test_dashboard_demand_builders.py',
    'backend/tests/test_assembly_demand_builders.py',
    'backend/tests/test_customer_inventory_unified_activity.py',
    'backend/tests/test_operational_inventory_purchase_receipts.py::test_partial_then_remaining_receipts_are_once_only_and_financially_independent',
    'backend/tests/test_operational_inventory_payment_allocation.py::test_native_owner_allocation_concurrent_replay_keeps_cash_and_original_identity',
]


def write(name, value):
    (OUTPUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def check_workflow():
    relative = '.github/workflows/operational-balance-web.yml'
    path = REPO / relative
    current = path.read_text()
    original = subprocess.check_output(['git', '--no-optional-locks', '-C', str(REPO),
        'show', BASE + ':' + relative], text=True)
    additions = ['          backend/tests/test_demand_read_runner_lifecycle.py\n',
                 "              ('test_demand_read_runner_lifecycle', 3),\n"]
    assert all(current.count(line) == 1 for line in additions)
    assert current.replace(additions[0], '').replace(additions[1], '') == original
    workflow = yaml.safe_load(current)
    steps = workflow['jobs']['backend']['steps']
    selected = next(step['run'] for step in steps if step.get('name') ==
                    'Run durable demand-read store, child, HTTP and builder contracts')
    assert shlex.split(selected).count(SELECTORS[2]) == 1
    gate = next(step['run'] for step in steps if step.get('name') ==
                'Reject missing, skipped or failed demand-read acceptance')
    ast.parse(gate.split("python - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0])
    write('workflow-union-review.json', {
        'base': BASE, 'result': 'PASS', 'source_edits_required': False,
        'preserves_all_base_bytes_except_two_insertions': True,
        'lifecycle_selector_occurrences': 1, 'lifecycle_minimum_cases': 3,
        'base_customer_inventory_selectors_counts_browser_acceptance': 'unchanged',
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
    })


def main():
    OUTPUT.mkdir(exist_ok=True)
    check_workflow()
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    os.environ['PYTHONPATH'] = str(REPO / 'backend') + ':' + str(REPO / 'backend/tests')
    for name in ('MONGO_URL', 'DB_NAME', 'DEMAND_READ_TEST_MONGO_URI',
                 'OPERATIONAL_TEST_MONGO_URI', 'MZ2_TEST_MONGO_URI'):
        os.environ.pop(name, None)
    os.environ['DEMAND_READ_TEST_MONGOD'] = MONGOD
    sys.path[:0] = [str(REPO / 'backend'), str(REPO / 'backend/tests')]
    from demand_read_mongo_fixture import read_mongo_uri

    with tempfile.TemporaryDirectory(prefix='mezan-r3-backend-interop-') as temporary:
        class Factory:
            def mktemp(self, prefix):
                return Path(tempfile.mkdtemp(prefix=prefix + '-', dir=temporary))

        fixture = read_mongo_uri.__wrapped__(Factory(), SimpleNamespace(config=SimpleNamespace()))
        try:
            uri = next(fixture)
            assert uri.startswith('mongodb://127.0.0.1:') and 'replicaSet=demandreadtest' in uri
            for name in ('DEMAND_READ_TEST_MONGO_URI', 'OPERATIONAL_TEST_MONGO_URI', 'MZ2_TEST_MONGO_URI'):
                os.environ[name] = uri
            argv = [sys.executable, '-B', '-m', 'pytest', '--noconftest', '-p', 'no:cacheprovider',
                '-q', *SELECTORS, '--asyncio-mode=auto', '--tb=short',
                '--basetemp=' + str(Path(temporary) / 'pytest'),
                '--junitxml=' + str(OUTPUT / 'backend-demand-customer-inventory.xml')]
            paths = [item.split('::')[0] for item in SELECTORS] + ['backend/demand_read_runner.py']
            write('backend-interop-command.json', {
                'cwd': str(REPO), 'argv': argv, 'production_base': BASE,
                'database': 'owned disposable loopback replica, UUID-scoped databases only',
                'environment': {'PYTHON_DOTENV_DISABLED': '1', 'PYTHONDONTWRITEBYTECODE': '1'},
                'test_source_sha256': {path: hashlib.sha256((REPO / path).read_bytes()).hexdigest() for path in paths},
                'dependencies': {name: importlib.metadata.version(name) for name in
                    ('pytest', 'pytest-asyncio', 'pymongo', 'motor', 'mongomock-motor')},
            })
            with (OUTPUT / 'backend-demand-customer-inventory.log').open('w') as log:
                process = subprocess.Popen(argv, cwd=REPO, env=dict(os.environ), text=True,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    print(line, end='', flush=True)
                code = process.wait()
            cases = list(ET.parse(OUTPUT / 'backend-demand-customer-inventory.xml').iter('testcase'))
            counts = {}
            for case in cases:
                name = case.get('classname', '').rsplit('.', 1)[-1]
                counts[name] = counts.get(name, 0) + 1
            outcomes = {tag: sum(case.find(tag) is not None for case in cases)
                        for tag in ('failure', 'error', 'skipped')}
            passed = code == 0 and not any(outcomes.values()) and counts.get('test_demand_read_runner_lifecycle') == 3
            passed = passed and counts.get('test_customer_inventory_unified_activity') == 3
            summary = {'result': 'PASS' if passed else 'FAIL', 'exit_code': code,
                       'tests': len(cases), 'suites': counts, **outcomes}
            write('backend-interop-summary.json', summary)
            print('INTEROP_SUMMARY', json.dumps(summary, sort_keys=True), flush=True)
            return 0 if passed else 1
        finally:
            fixture.close()


if __name__ == '__main__':
    raise SystemExit(main())
