"""Evidence orchestration only: unchanged C5 browser, public409 probe, existing G47 tests.

No source patches, public-route remounting, credentials or Production endpoints.
Creates a new owned loopback replica; preserves all outputs and original tests.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HEAD = '88cc9131027fd6783a46e2e00fc6aaec4788b8fa'
TREE = '57483f44efa381ca872262f5a8bc61d2a87cae93'
p = argparse.ArgumentParser()
p.add_argument('--root', required=True)
p.add_argument('--output', required=True)
p.add_argument('--node', required=True)
p.add_argument('--mongod', required=True)
p.add_argument('--playwright', required=True)
p.add_argument('--probe-only', action='store_true', help='Reuse separately retained successful browser evidence; run remaining probe/API checks only')
a = p.parse_args()
root, out = Path(a.root).resolve(), Path(a.output).resolve()
assert out.is_absolute() and not out.exists() and out.drive.upper() == 'D:'
out.mkdir(parents=True)
for part in ('mongo-data', 'temp', 'setup', 'focused'):
    (out / part).mkdir()
def save(name, value):
    (out / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
def git(*args):
    return subprocess.check_output(['git', *args], cwd=root).decode().strip()
def source():
    assert git('rev-parse', 'HEAD') == HEAD and git('rev-parse', 'HEAD^{tree}') == TREE
    assert not git('status', '--porcelain')
    files = git('ls-files', 'backend', 'frontend', 'scripts', '.github', 'AGENTS.md').splitlines()
    return {f: hashlib.sha256((root / f).read_bytes()).hexdigest() for f in files if (root / f).is_file()}
before = source()
save('source-before.json', before)
allow = {'SYSTEMROOT', 'WINDIR', 'PATH', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMFILES',
         'PROGRAMFILES(X86)', 'PROCESSOR_ARCHITECTURE', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE', 'PROGRAMDATA', 'NUMBER_OF_PROCESSORS'}
env = {k: v for k, v in os.environ.items() if k.upper() in allow}
uri = 'mongodb://127.0.0.1:27137/?replicaSet=mz2uat'
env.update(PYTHON_DOTENV_DISABLED='1', PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1',
           PYTHONPATH=str(root / 'backend') + os.pathsep + str(root / 'backend/tests'),
           JWT_SECRET=secrets.token_urlsafe(64), MZ2_TEST_MONGO_URI=uri, MONGO_URL=uri,
           DB_NAME='unused_isolated_uat', TEMP=str(out / 'temp'), TMP=str(out / 'temp'),
           MZ2_C5_DIST=str(out / 'dist'), MZ2_C5_EVIDENCE=str(out / 'setup'), MZ2_C5_EXECUTE='isolated-authorized',
           MZ2_C5_ORIGIN='http://127.0.0.1:18773', MZ2_C5_PORT='18773', PLAYWRIGHT_MODULE=a.playwright)
commands = []
flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
def run(label, argv, timeout=600):
    commands.append({'label': label, 'argv': argv, 'cwd': str(root)})
    save('commands.json', commands)
    with (out / (label + '.log')).open('w', encoding='utf-8') as log:
        result = subprocess.run(argv, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                                timeout=timeout, creationflags=flags)
    print(json.dumps({'stage': label, 'exit_code': result.returncode}), flush=True)
    assert result.returncode == 0, label + ' failed; original evidence retained'
def http(path, body=None, token=None):
    req = urllib.request.Request('http://127.0.0.1:18773' + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + token} if token else {})})
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)
for port in (27137, 18773):
    with socket.socket() as check:
        check.bind(('127.0.0.1', port))
assert shutil.disk_usage(out).free > 10 * 1024**3
mongo_argv = [a.mongod, '--dbpath', str(out / 'mongo-data'), '--port', '27137', '--bind_ip', '127.0.0.1',
              '--logpath', str(out / 'mongo.log'), '--replSet', 'mz2uat', '--oplogSize', '256',
              '--setParameter', 'transactionLifetimeLimitSeconds=5']
mongo = subprocess.Popen(mongo_argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, env=env, creationflags=flags)
save('mongo-process.json', {'pid': mongo.pid, 'argv': mongo_argv})
from pymongo import MongoClient
client = MongoClient('mongodb://127.0.0.1:27137/?directConnection=true', serverSelectionTimeoutMS=1000)
server = None
server_log = None
status = 'INCOMPLETE'
def verify_owned_mongo():
    assert mongo.poll() is None
    opts = client.admin.command('getCmdLineOpts')['parsed']
    assert Path(opts['storage']['dbPath']).resolve() == (out / 'mongo-data').resolve()
    assert opts['net']['port'] == 27137 and opts['net']['bindIp'] == '127.0.0.1'
    assert opts['replication']['replSet'] == 'mz2uat'
    return opts
try:
    for _ in range(50):
        assert mongo.poll() is None
        try:
            client.admin.command('ping')
            break
        except Exception:
            time.sleep(.2)
    options = verify_owned_mongo()
    client.admin.command('replSetInitiate', {'_id': 'mz2uat', 'members': [{'_id': 0, 'host': '127.0.0.1:27137'}]})
    for _ in range(80):
        if client.admin.command('hello').get('isWritablePrimary'):
            break
        time.sleep(.2)
    assert client.admin.command('hello')['isWritablePrimary']
    initial = client.list_database_names()
    assert set(initial) <= {'admin', 'config', 'local'}
    save('environment.json', {'environment_identifier': out.name, 'head': HEAD, 'tree': TREE,
        'mongo_version': client.server_info()['version'], 'options': options, 'initial_databases': initial,
        'disk_free_bytes': shutil.disk_usage(out).free, 'environment_policy': 'OS allowlist; fresh synthetic JWT never persisted; dotenv disabled; loopback replica only',
        'production_financial_writes': 0, 'production_verified': False})
    run('build', [a.node, 'scripts/testing/mz2_business_uat/build.cjs'])
    server_log = (out / 'server.log').open('w', encoding='utf-8')
    server_argv = [sys.executable, 'scripts/testing/mz2_business_uat/run_server.py']
    commands.append({'label': 'server', 'argv': server_argv, 'cwd': str(root)})
    save('commands.json', commands)
    server = subprocess.Popen(server_argv, cwd=root, env=env, stdin=subprocess.DEVNULL,
                              stdout=server_log, stderr=subprocess.STDOUT, creationflags=flags)
    save('server-process.json', {'pid': server.pid, 'argv': server_argv})
    for _ in range(100):
        assert server.poll() is None, 'C5 server stopped'
        try:
            if http('/__test/proof')[0] == 200:
                break
        except Exception:
            time.sleep(.2)
    if not a.probe_only:
        run('browser', [a.node, 'scripts/testing/mz2_business_uat/browser.cjs'])
    # Actual shipped router, existing JWT verifier and permissions. No route override.
    _, pre = http('/__test/proof')
    _, auth = http('/__test/bootstrap')
    observed = []
    for path, body, code in [
        ('/api/financial-provider-apps/accounting-module/financial-accounts/opening-balances/drafts/synthetic/post', {}, 'opening_onboarding_required'),
        ('/api/financial-provider-apps/accounting-module/financial-accounts/transition', {'target': 'v2_active', 'expected_revision': 0, 'activation_ref': 'synthetic-unavailable-authorization'}, 'onboarding_activation_locked')]:
        http_status, result = http(path, body, auth['token'])
        observed.append({'method': 'POST', 'path': path, 'request': body, 'status': http_status, 'response': result})
        save('public-409-observed.json', observed)
        assert http_status == 409 and result['detail']['code'] == code
    _, post = http('/__test/proof')
    assert pre['fingerprints'] == post['fingerprints'] and pre['controls'] == post['controls']
    assert pre['financial_counts'] == post['financial_counts']
    save('public-409-probe.json', {'requests': observed, 'before': pre, 'after': post,
         'database_and_controls_unchanged': True, 'production_financial_writes': 0})
    (out / 'setup/stop-server').write_text('stop owned C5 fixture', encoding='utf-8')
    server.wait(timeout=30)
    assert server.returncode == 0
    server_log.close()
    assert json.loads((out / 'setup/cleanup-proof.json').read_text())['removed']
    test_class = 'backend/tests/test_g47_opening_inventory.py::OpeningInventoryIntegration::'
    selected = [test_class + name for name in (
        'test_import_has_no_stock_effect_and_preserves_exact_evidence_bytes',
        'test_approve_base_product_two_variants_component_and_two_locations',
        'test_concurrent_retry_initializes_once_and_new_evidence_cannot_backfill',
        'test_transaction_failure_after_projections_rolls_back_and_retry_succeeds')]
    # Unmodified existing tests. Synthetic sealed opening is a declared prerequisite,
    # not evidence that public Opening/Activation or real physical stock was approved.
    env['MZ2_UAT_OBSERVER_OUTPUT'] = str(out / 'focused/http-observations.jsonl')
    run('physical-api', [sys.executable, '-u', str(Path(__file__).with_name('observe_existing_tests.py')), '--noconftest', '-vv', '--tb=short',
        '-o', 'asyncio_mode=auto', *selected, '--junitxml=' + str(out / 'focused/physical-api.xml')])
    status = 'PROBE_AND_PHYSICAL_API_PASS' if a.probe_only else 'ACCEPTANCE_SUBSETS_PASS_FULL_BUSINESS_UAT_NOT_PASS'
finally:
    if server is not None and server.poll() is None:
        (out / 'setup/stop-server').write_text('stop owned C5 fixture', encoding='utf-8')
        server.wait(timeout=30)
    if server_log is not None and not server_log.closed:
        server_log.close()
    if mongo.poll() is None:
        verify_owned_mongo()
        final_dbs = client.list_database_names()
        save('databases-before-shutdown.json', final_dbs)
        assert set(final_dbs) <= {'admin', 'config', 'local'}, 'Owned fixture DB remains; retain for audit'
        try:
            client.admin.command('shutdown')
        except Exception:
            pass
        mongo.wait(timeout=30)
    client.close()
    after = source()
    save('source-after.json', after)
    assert before == after
    for port in (27137, 18773):
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', port)) != 0
    save('result.json', {'head': HEAD, 'tree': TREE, 'status': status, 'source_unchanged': True,
        'server_exit_code': None if server is None else server.returncode, 'mongo_exit_code': mongo.returncode,
        'ports_closed': [27137, 18773], 'data_retained': True, 'production_financial_writes': 0,
        'production_verified': False, 'write_control': 'UNCHANGED', 'opening_public_positive': 'NOT_EXECUTED',
        'activation_public_positive': 'NOT_EXECUTED', 'physical_api_scope': 'Existing API + synthetic verified-opening prerequisite; not attested business stock'})
    print(json.dumps({'status': status, 'output': str(out)}), flush=True)
