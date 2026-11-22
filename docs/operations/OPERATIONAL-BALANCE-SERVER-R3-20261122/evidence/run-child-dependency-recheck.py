"""Recheck only actual child paths after installing the declared missing dotenv."""
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
SELECTORS = [
    'backend/tests/test_demand_read_jobs.py::test_actual_assembly_child_reads_canonical_fixture_and_publishes_ready',
    'backend/tests/test_operational_workspace_demand_http.py::test_actual_child_keeps_health_and_bootstrap_responsive_on_same_large_fixture',
    'backend/tests/test_demand_read_runner_lifecycle.py',
]


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    assert importlib.metadata.version('python-dotenv') == '1.2.2'
    os.environ.update(PYTHONDONTWRITEBYTECODE='1', PYTHON_DOTENV_DISABLED='1',
        PYTHONPATH=str(REPO / 'backend') + ':' + str(REPO / 'backend/tests'),
        DEMAND_READ_TEST_MONGOD='/workspace/scratch/36b1bc151c92/mezan-test-mongo/bin/mongod')
    for name in ('MONGO_URL', 'DB_NAME', 'DEMAND_READ_TEST_MONGO_URI', 'OPERATIONAL_TEST_MONGO_URI', 'MZ2_TEST_MONGO_URI'):
        os.environ.pop(name, None)
    sys.path[:0] = [str(REPO / 'backend'), str(REPO / 'backend/tests')]
    from demand_read_mongo_fixture import read_mongo_uri
    before = json.loads((OUTPUT / 'backend-command.json').read_text())['source_sha256']
    assert all(hashlib.sha256((REPO / name).read_bytes()).hexdigest() == value for name, value in before.items())
    with tempfile.TemporaryDirectory(prefix='mezan-r3-child-recheck-') as temporary:
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
                    '-q', '--tb=short', '--asyncio-mode=auto', '-o', 'asyncio_default_fixture_loop_scope=function',
                    *SELECTORS, '--basetemp=' + str(Path(temporary) / 'pytest'),
                    '--junitxml=' + str(OUTPUT / 'child-dependency-recheck.xml')]
            (OUTPUT / 'child-dependency-recheck-command.json').write_text(json.dumps({
                'argv': argv, 'cwd': str(REPO), 'reason': 'First run failed before build because local venv lacked dotenv',
                'installed_declared_dependency': 'python-dotenv==1.2.2', 'source_sha256': before,
                'database': 'owned disposable loopback replica, UUID-scoped databases only',
            }, indent=2, sort_keys=True) + '\n')
            with (OUTPUT / 'child-dependency-recheck.log').open('w') as log:
                process = subprocess.Popen(argv, cwd=REPO, env=dict(os.environ), text=True,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    print(line, end='', flush=True)
                code = process.wait()
            cases = list(ET.parse(OUTPUT / 'child-dependency-recheck.xml').iter('testcase'))
            counts = {tag: sum(case.find(tag) is not None for case in cases) for tag in ('failure', 'error', 'skipped')}
            unchanged = all(hashlib.sha256((REPO / name).read_bytes()).hexdigest() == value for name, value in before.items())
            passed = code == 0 and len(cases) == 5 and not any(counts.values()) and unchanged
            summary = {'result': 'PASS' if passed else 'FAIL', 'exit_code': code, 'tests': len(cases),
                       'source_unchanged': unchanged, **counts}
            (OUTPUT / 'child-dependency-recheck-summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
            print('CHILD_RECHECK_SUMMARY', json.dumps(summary, sort_keys=True), flush=True)
            return 0 if passed else 1
        finally:
            fixture.close()


if __name__ == '__main__':
    raise SystemExit(main())
