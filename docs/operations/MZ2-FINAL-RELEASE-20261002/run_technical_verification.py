"""Finite isolated verification of an immutable source; no release or live actions."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from uuid import uuid4

from pymongo import MongoClient

p = argparse.ArgumentParser()
p.add_argument('--root', required=True)
p.add_argument('--output', required=True)
p.add_argument('--expected-head', required=True)
p.add_argument('--node', required=True)
p.add_argument('--mongod', required=True)
a = p.parse_args()
root, out = Path(a.root).resolve(), Path(a.output).resolve()
assert out.is_absolute() and out.drive.upper() == 'D:' and not out.exists()
assert root != out and root not in out.parents
out.mkdir(parents=True)
for name in ('temp', 'replica-data', 'standalone-data'):
    (out / name).mkdir()
assert shutil.disk_usage(out).free > 15 * 1024**3


def git(*args):
    return subprocess.check_output(['git', *args], cwd=root).decode().strip()


def save(name, value):
    (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def source():
    assert git('rev-parse', 'HEAD') == a.expected_head
    assert not git('status', '--porcelain'), 'Source must remain clean'
    paths = git('ls-files', 'backend', 'frontend', 'scripts', '.github', 'release', 'AGENTS.md').splitlines()
    return {f: hashlib.sha256((root / f).read_bytes()).hexdigest() for f in paths if (root / f).is_file()}


before = source()
head, tree = git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')
selection = json.loads(Path(__file__).with_name('BACKEND-SELECTION.json').read_text(encoding='utf-8'))
assert len(selection) == len(set(selection)) and all((root / f).is_file() for f in selection)
save('source-before.json', before)
save('backend-selection.json', selection)
allowed = {'SYSTEMROOT', 'WINDIR', 'PATH', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMFILES',
           'PROGRAMFILES(X86)', 'PROCESSOR_ARCHITECTURE', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE',
           'PROGRAMDATA', 'NUMBER_OF_PROCESSORS'}
env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
uri = 'mongodb://127.0.0.1:27141/?replicaSet=mz2release'
standalone_uri = 'mongodb://127.0.0.1:27142/?directConnection=true'
env.update(PYTHON_DOTENV_DISABLED='1', PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1',
           PYTHONPATH=str(root / 'backend') + os.pathsep + str(root / 'backend/tests'),
           JWT_SECRET=secrets.token_urlsafe(64), MZ2_TEST_MONGO_URI=uri,
           MZ2_AD_TEST_MONGO_URI=uri, BUILD20_INVOICE_TEST_MONGO_URL=uri,
           MZ2_TEST_STANDALONE_URI=standalone_uri, MONGO_URL=uri,
           DB_NAME='unused_release_' + uuid4().hex, TEMP=str(out / 'temp'), TMP=str(out / 'temp'),
           CI='true', REACT_APP_BACKEND_URL='http://127.0.0.1:1')
env['PATH'] = str(Path(a.node).parent) + os.pathsep + env.get('PATH', '')
flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
commands, results, fixtures = [], [], []
save('started.json', {'head': head, 'tree': tree, 'environment_identifier': out.name,
                     'selected_backend_files': len(selection), 'production_financial_writes': 0,
                     'policy': 'OS environment allowlist; dotenv disabled; synthetic JWT; explicit loopback disposable Mongo only; no inherited credentials',
                     'scope': 'Full previously declared native baseline plus PR1241 operational selection; full frontend; ordinary compile; Acceptance-only Smoke B'})


def run(label, argv, cwd):
    commands.append({'label': label, 'argv': argv, 'cwd': str(cwd)})
    save('commands.json', commands)
    start = time.monotonic()
    with (out / (label + '.log')).open('w', encoding='utf-8') as log:
        # Do not kill only the harness parent on an outer timeout: the existing
        # harness owns its bounded waits/finally; CPJ owns whole-job cancellation.
        r = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log,
                           stderr=subprocess.STDOUT, creationflags=flags)
    result = {'stage': label, 'exit_code': r.returncode, 'seconds': round(time.monotonic() - start, 3)}
    results.append(result)
    save('stage-results.json', results)
    print(json.dumps(result), flush=True)
    return r.returncode


def verify_fixture(entry):
    proc, client, port, directory, replica = entry
    assert proc.poll() is None, 'Owned Mongo process stopped unexpectedly'
    opts = client.admin.command('getCmdLineOpts')['parsed']
    assert Path(opts['storage']['dbPath']).resolve() == directory.resolve()
    assert opts['net']['port'] == port and opts['net']['bindIp'] == '127.0.0.1'
    assert opts.get('replication', {}).get('replSet') == replica
    return opts


def start_fixture(port, directory, replica=None):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', port))
    argv = [a.mongod, '--dbpath', str(directory), '--port', str(port), '--bind_ip', '127.0.0.1',
            '--logpath', str(out / (str(port) + '-mongo.log'))]
    if replica:
        argv += ['--replSet', replica, '--oplogSize', '256', '--setParameter', 'transactionLifetimeLimitSeconds=5']
    proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=flags)
    client = MongoClient(f'mongodb://127.0.0.1:{port}/?directConnection=true', serverSelectionTimeoutMS=1000)
    entry = (proc, client, port, directory, replica)
    fixtures.append(entry)
    save(str(port) + '-process.json', {'pid': proc.pid, 'argv': argv, 'started_at': time.time()})
    for _ in range(60):
        assert proc.poll() is None
        try:
            client.admin.command('ping')
            break
        except Exception:
            time.sleep(.25)
    opts = verify_fixture(entry)
    if replica:
        client.admin.command('replSetInitiate', {'_id': replica, 'members': [{'_id': 0, 'host': f'127.0.0.1:{port}'}]})
        for _ in range(80):
            if client.admin.command('hello').get('isWritablePrimary'):
                break
            time.sleep(.25)
    assert client.admin.command('hello')['isWritablePrimary']
    assert set(client.list_database_names()) <= {'admin', 'config', 'local'}
    save(str(port) + '-environment.json', {'mongo_version': client.server_info()['version'], 'options': opts,
                                          'databases_before': client.list_database_names()})


status = 'INCOMPLETE'
cleanup = []
errors = []
try:
    # Independent frontend checks retain failures without retrying or masking them.
    f = run('frontend', [a.node, 'node_modules/react-scripts/bin/react-scripts.js', 'test',
                        '--watchAll=false', '--runInBand', '--no-cache', '--json',
                        '--outputFile=' + str(out / 'frontend.json')], root / 'frontend')
    if f == 0:
        r = json.loads((out / 'frontend.json').read_text(encoding='utf-8'))
        assert r['success'] and r['numPassedTests'] and not r['numFailedTests']
        assert not r['numPendingTests'] and not r.get('numTodoTests', 0)
        save('frontend-summary.json', {k: r[k] for k in ['numPassedTestSuites', 'numPassedTests', 'numFailedTests', 'numPendingTests']})
    run('ordinary-build', [a.node, 'node_modules/vite/bin/vite.js', 'build', '--outDir', str(out / 'dist')],
        root / 'frontend')
    start_fixture(27141, out / 'replica-data', 'mz2release')
    start_fixture(27142, out / 'standalone-data')
    b = run('backend', [sys.executable, '-u', '-m', 'pytest', '--noconftest', '-v', '--tb=short',
                       '-o', 'asyncio_mode=auto', *selection, '--durations=15',
                       '--junitxml=' + str(out / 'backend.xml')], root)
    assert (out / 'backend.xml').is_file(), 'Missing required Backend evidence'
    suites = ET.parse(out / 'backend.xml')
    cases = list(suites.iter('testcase'))
    rejected = [{'classname': c.get('classname'), 'name': c.get('name'),
                 'states': [s for s in ('failure', 'error', 'skipped') if c.find(s) is not None]}
                for c in cases if any(c.find(s) is not None for s in ('failure', 'error', 'skipped'))]
    save('backend-summary.json', {'parent_cases': len(cases), 'rejected': rejected, 'exit_code': b,
                                  'suite_attributes': [s.attrib for s in suites.iter('testsuite')]})
    if b == 0:
        assert cases and not rejected
    # Uses the original full app/auth/network-denying harness, no guard changes.
    run('smoke-b', [sys.executable, 'scripts/testing/mz2_smoke_b_acceptance/run.py',
                    '--execute-after-c3-approved', '--mongo-uri', uri, '--port', '18775',
                    '--evidence', str(out / 'smoke-b')], root)
    assert (out / 'smoke-b/result.json').is_file(), 'Missing required Smoke B evidence'
    smoke = json.loads((out / 'smoke-b/result.json').read_text(encoding='utf-8'))
    assert smoke['production_verified'] is False and smoke['status'] == 'ACCEPTANCE_PROBE_PASS'
    assert smoke['source'] == smoke['source_after']
    assert smoke['source']['head'] == head and smoke['source']['tree'] == tree
    assert smoke['cleanup']['task_pid_stopped'] and smoke['cleanup']['database_absent']
    assert smoke['probe']['database_before'] == smoke['probe']['database_after']
    assert smoke['probe']['canonical_control_before'] == smoke['probe']['canonical_control_after']
    assert smoke['probe']['canonical_control_before']['paused'] is True
    status = 'TECHNICAL_CHECKS_PASS_ACCEPTANCE_ONLY' if all(r['exit_code'] == 0 for r in results) else 'FAILED_RETAINED'
except Exception as error:
    errors.append({'stage': 'execution', 'type': type(error).__name__, 'message': str(error)})
    status = 'FAILED_RETAINED'
finally:
    for entry in reversed(fixtures):
        proc, client, port, directory, replica = entry
        dbs, cleanup_error = None, None
        try:
            if proc.poll() is None:
                verify_fixture(entry)
                dbs = client.list_database_names()
                save(str(port) + '-databases-after.json', dbs)
                try:
                    client.admin.command('shutdown')
                except Exception:
                    pass
                proc.wait(timeout=30)
            if dbs is not None and set(dbs) - {'admin', 'config', 'local'}:
                raise RuntimeError('Test databases remain; data retained for diagnosis')
        except Exception as error:
            cleanup_error = {'port': port, 'type': type(error).__name__, 'message': str(error)}
            errors.append(cleanup_error)
        finally:
            # Popen retains the original Windows process handle; this cannot
            # terminate a reused PID or any unrelated Mongo instance.
            try:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=30)
            except Exception as error:
                errors.append({'stage': 'owned_process_stop', 'port': port, 'type': type(error).__name__})
            client.close()
            cleanup.append({'port': port, 'exit_code': proc.poll(), 'remaining_databases': dbs,
                            'data_retained': True, 'error': cleanup_error})
    unchanged = False
    try:
        after = source()
        save('source-after.json', after)
        unchanged = before == after
        assert unchanged
        for port in (27141, 27142, 18775):
            with socket.socket() as probe:
                assert probe.connect_ex(('127.0.0.1', port)) != 0, 'Fixture port remains open'
    except Exception as error:
        errors.append({'stage': 'final_integrity', 'type': type(error).__name__, 'message': str(error)})
    if errors:
        status = 'FAILED_RETAINED'
    save('finished.json', {'head': head, 'tree': tree, 'status': status, 'results': results,
                          'source_unchanged': unchanged, 'cleanup': cleanup, 'errors': errors, 'production_financial_writes': 0,
                          'full_business_uat': 'NOT_PASS_FINANCIAL_GO_LIVE_GATE',
                          'production_verified': False, 'release_guard': 'NOT_RUN', 'deploy': 'NOT_RUN'})
    print(json.dumps({'status': status, 'evidence': str(out)}), flush=True)
sys.exit(0 if status == 'TECHNICAL_CHECKS_PASS_ACCEPTANCE_ONLY' else 1)
