"""Read-only independent evidence check; does not post, activate or deploy."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess
from datetime import datetime, timezone

from pymongo import MongoClient

ROOT = Path('C:/Users/amasi/mz2-late-evidence-linear-20261002')
OUT = Path(__file__).resolve().parent
HEAD = '88cc9131027fd6783a46e2e00fc6aaec4788b8fa'
TREE = '57483f44efa381ca872262f5a8bc61d2a87cae93'
BASE = '83363097d48e034dc7140a60c290efc684e1ffde'

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode('utf-8').strip()

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

assert git('rev-parse', 'HEAD') == HEAD
assert git('rev-parse', 'HEAD^{tree}') == TREE
assert not git('status', '--porcelain')
smoke = OUT / 'smoke-b-88cc9131'
result = json.loads((smoke / 'result.json').read_text(encoding='utf-8'))
runtime = json.loads((smoke / 'runtime-source-before.json').read_text(encoding='utf-8'))
assert result['status'] == 'ACCEPTANCE_PROBE_PASS'
assert result['production_verified'] is False
assert result['source'] == result['source_after'] == runtime
assert runtime['head'] == HEAD and runtime['tree'] == TREE and runtime['status'] == ''
for name, sha in runtime['source_hashes'].items():
    assert digest(ROOT / name) == sha, name
probe = result['probe']
assert probe['database_before'] == probe['database_after']
assert probe['canonical_control_before'] == probe['canonical_control_after']
assert probe['canonical_control_before']['paused'] is True
assert [x['status'] for x in probe['transcript'] if x.get('status')] == [202, 200, 200, 404, 423]
assert result['cleanup']['task_pid_stopped'] and result['cleanup']['database_absent']
network = smoke / 'denied-network.jsonl'
assert not network.exists() or network.stat().st_size == 0
with socket.socket() as connection:
    assert connection.connect_ex(('127.0.0.1', 18769)) != 0
with MongoClient('mongodb://127.0.0.1:27135/?replicaSet=mz2late', serverSelectionTimeoutMS=4000) as client:
    assert result['environment']['database'] not in client.list_database_names()
    opts = client.admin.command('getCmdLineOpts')['parsed']
    assert opts['net']['bindIp'] == '127.0.0.1' and opts['net']['port'] == 27135
    assert opts['replication']['replSet'] == 'mz2late'
    assert client.admin.command('hello')['setName'] == 'mz2late'
save('SMOKE-B-VERIFIED.json', {
    'verified_at': datetime.now(timezone.utc).isoformat(),
    'status': 'PASS_ACCEPTANCE_ONLY', 'production_verified': False,
    'head': HEAD, 'tree': TREE, 'environment': result['environment'],
    'http_statuses': [202, 200, 200, 404, 423],
    'whole_database_fingerprint': probe['database_before']['sha256'],
    'collection_count': len(probe['database_before']['collections']),
    'whole_database_unchanged': True, 'control_unchanged': True,
    'writes_paused_before_after': [True, True],
    'parent_child_current_sources_equal': True,
    'source_manifest_sha256': runtime['source_manifest_sha256'],
    'external_network_attempts': 0,
    'owned_application_port_closed': True, 'owned_database_absent': True,
    'runtime_production_release_identity_verified': False,
    'production_financial_writes_by_this_task': 0,
    'artifacts': {p.name: {'sha256': digest(p), 'bytes': p.stat().st_size} for p in smoke.iterdir() if p.is_file()},
    'limits': ['Acceptance paused-barrier execution only', 'Not a production package/deployment proof', 'No final business UAT, inventory approval, positive opening or activation assertion']
})

def tree_entries(ref):
    raw = subprocess.check_output(['git', 'ls-tree', '-r', '-z', ref], cwd=ROOT)
    entries = {}
    for row in raw.split(b'\0'):
        if not row:
            continue
        meta, path = row.split(b'\t', 1)
        mode, kind, oid = meta.decode().split()
        entries[path.decode('utf-8')] = {'mode': mode, 'kind': kind, 'git_oid': oid}
    return entries

left, right = tree_entries(BASE), tree_entries(HEAD)
changes = []
for name in sorted(left.keys() | right.keys()):
    if left.get(name) == right.get(name):
        continue
    changes.append({'path': name, 'status': 'A' if name not in left else 'D' if name not in right else 'M',
                    'base': left.get(name), 'candidate': right.get(name)})
save('CHANGED-FILES.json', {'production_base': BASE, 'integration_head': HEAD, 'tree': TREE,
                          'rename_policy': 'Exact path/blob manifest; renames represented as delete/add',
                          'count': len(changes), 'files': changes})
guard_paths = ['backend/accounting_write_control.py', 'backend/accounting_atomic.py',
               'backend/accounting_writer_transition.py', 'scripts/production_release_guard.py']
frozen = tree_entries('48bb39d983fe7003bc972c624a1508655a16f570')
save('GUARD-SOURCE-PROOF.json', {'head': HEAD, 'tree': TREE, 'guards': [
    {'path': name, 'current_sha256': digest(ROOT / name), 'current_git_blob': right[name]['git_oid'],
     'same_as_48bb': right[name] == frozen.get(name), 'same_as_production_base': right[name] == left.get(name)}
    for name in guard_paths], 'source_changes_during_final_acceptance': False,
    'production_access': 'None; no claim about unrelated actors or global production state'})
print(json.dumps({'smoke_b': 'PASS_ACCEPTANCE_ONLY', 'candidate': HEAD, 'changed_files': len(changes)}))
