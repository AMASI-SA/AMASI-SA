"""Read-only source review tests with one owned disposable loopback Mongo."""
import sys
sys.dont_write_bytecode = True

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import resource
import subprocess
import tempfile
from types import SimpleNamespace
import xml.etree.ElementTree as ET

REPO = Path('/workspace/scratch/dfe063f5aca0/mezan-server-r3')
OUTPUT = Path('/workspace/scratch/dfe063f5aca0/server-r3-evidence')
BASE = '02cd99e5b2b68c95341c87590abcf572c885e697'
PREVIOUS_SOURCE = '022224d26d6e77a7af93206d30883c2cf575cbb8'
MONGOD = '/workspace/scratch/36b1bc151c92/mezan-test-mongo/bin/mongod'
SELECTORS = [
    'backend/tests/test_demand_read_store.py',
    'backend/tests/test_demand_read_jobs.py',
    'backend/tests/test_demand_read_runner_lifecycle.py',
    'backend/tests/test_operational_workspace_demand_reads.py',
    'backend/tests/test_operational_workspace_demand_http.py',
    'backend/tests/test_dashboard_demand_builders.py',
    'backend/tests/test_assembly_demand_builders.py',
    'backend/tests/test_operational_movement_reversal_effects.py',
    'backend/tests/test_operational_movement_reversal_routes.py',
    'backend/tests/test_operational_workspace_account_history.py',
    'backend/tests/test_operational_ad_and_entry_latency.py',
    'backend/tests/test_customer_inventory_unified_activity.py',
    'backend/tests/test_operational_inventory_payment_allocation.py',
    'backend/tests/test_operational_inventory_purchase_receipts.py::test_partial_then_remaining_receipts_are_once_only_and_financially_independent',
    'backend/tests/test_operational_workspace_customer_collections.py',
    'backend/tests/test_operational_balance_web_page_grants.py',
]
SOURCE_PATHS = [
    'backend/demand_read_runner.py',
    'backend/operational_balance_workspace.py',
    'backend/operational_workspace_read_builders.py',
    'backend/operational_workspace_reversals.py',
    'backend/operational_movement_reversal.py',
    'backend/operational_inventory_payment_allocation.py',
    'backend/operational_web_page_policy.py',
]


def hashes():
    paths = sorted(set(SOURCE_PATHS + [value.split('::')[0] for value in SELECTORS]))
    return {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest() for name in paths}


def write(name, value):
    (OUTPUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    OUTPUT.mkdir(exist_ok=True)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    os.environ['PYTHONPATH'] = str(REPO / 'backend') + ':' + str(REPO / 'backend/tests')
    for name in ('MONGO_URL', 'DB_NAME', 'DEMAND_READ_TEST_MONGO_URI',
                 'OPERATIONAL_TEST_MONGO_URI', 'MZ2_TEST_MONGO_URI'):
        os.environ.pop(name, None)
    os.environ['DEMAND_READ_TEST_MONGOD'] = MONGOD
    sys.path[:0] = [str(REPO / 'backend'), str(REPO / 'backend/tests')]
    from demand_read_mongo_fixture import read_mongo_uri

    before = hashes()
    assert subprocess.check_output(['git', '--no-optional-locks', '-C', str(REPO),
        'rev-parse', 'HEAD'], text=True).strip() == BASE
    changed_backend = subprocess.check_output(['git', '--no-optional-locks', '-C', str(REPO),
        'diff', PREVIOUS_SOURCE, '--name-only', '--', 'backend'], text=True).splitlines()
    assert set(changed_backend) == {'backend/demand_read_runner.py',
                                     'backend/tests/test_demand_read_runner_lifecycle.py'}

    with tempfile.TemporaryDirectory(prefix='mezan-server-r3-review-') as temporary:
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
                    '-q', '--tb=short', '--asyncio-mode=auto',
                    '-o', 'asyncio_default_fixture_loop_scope=function', *SELECTORS,
                    '--basetemp=' + str(Path(temporary) / 'pytest'),
                    '--junitxml=' + str(OUTPUT / 'backend-integration.xml')]
            write('backend-command.json', {
                'cwd': str(REPO), 'argv': argv, 'reviewed_base': BASE,
                'previous_source': PREVIOUS_SOURCE, 'changed_backend_from_previous_source': changed_backend,
                'database': 'owned disposable loopback replica, UUID-scoped databases only',
                'environment': {'PYTHON_DOTENV_DISABLED': '1', 'PYTHONDONTWRITEBYTECODE': '1'},
                'source_sha256': before,
                'dependencies': {name: importlib.metadata.version(name) for name in
                    ('pytest', 'pytest-asyncio', 'pymongo', 'motor', 'mongomock-motor')},
            })
            with (OUTPUT / 'backend-integration.log').open('w') as log:
                process = subprocess.Popen(argv, cwd=REPO, env=dict(os.environ), text=True,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    print(line, end='', flush=True)
                code = process.wait()
            cases = list(ET.parse(OUTPUT / 'backend-integration.xml').iter('testcase'))
            counts = {}
            for case in cases:
                name = case.get('classname', '').rsplit('.', 1)[-1]
                counts[name] = counts.get(name, 0) + 1
            outcomes = {tag: sum(case.find(tag) is not None for case in cases)
                        for tag in ('failure', 'error', 'skipped')}
            unchanged = hashes() == before
            unique = len({(case.get('classname'), case.get('name')) for case in cases})
            passed = code == 0 and not any(outcomes.values()) and unchanged
            passed = passed and counts.get('test_demand_read_runner_lifecycle') == 3
            passed = passed and counts.get('test_customer_inventory_unified_activity') == 5
            passed = passed and counts.get('test_operational_workspace_demand_reads') == 48
            summary = {'result': 'PASS' if passed else 'FAIL', 'exit_code': code,
                       'tests': len(cases), 'unique_tests': unique, 'suites': counts,
                       'reviewed_source_files_unchanged': unchanged, **outcomes}
            write('backend-summary.json', summary)
            print('R3_BACKEND_SUMMARY', json.dumps(summary, sort_keys=True), flush=True)
            return 0 if passed else 1
        finally:
            fixture.close()


if __name__ == '__main__':
    raise SystemExit(main())
