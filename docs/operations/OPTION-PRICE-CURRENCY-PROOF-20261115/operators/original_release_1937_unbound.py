#!/usr/bin/env python3
"""One original-workspace fast-forward and governed rehearsal; no publication."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path('/app')
BASE = '331b7917b3c01d064ee2a17affb4714f3a296331'
MERGE = 'PENDING_NORMAL_MERGE_PR1937'
TREE = 'c9352dd3369a61d75f28f9959cf398647a86e9b0'
BRANCH = 'hotfix/prod-snap-meta-final'
INTENT_SHA256 = 'da8f8da015d25f42beecfa96d59d2e3bcd2157e3158e4cea5624d9d3bae0e755'
PROOF_SHA256 = '944261ddfd6a423de27d2bff328bc3c82287881c0ddefe5e0977ea7fd7c6ed86'
EVIDENCE = Path('/tmp/ready-source-detail-release-1937/original')
EXPECTED_PROOF = Path(__file__).with_name('expected-package-boundary.json')
APPEND_ONLY = '.emergent/stripped_env_names'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def save(name, value):
    (EVIDENCE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')

def command(name, args, cwd=ROOT, environment=None):
    started = datetime.now(timezone.utc).isoformat()
    save(name + '.started.json', {'args': args, 'cwd': str(cwd), 'started_at': started})
    with (EVIDENCE / (name + '.stdout')).open('wb') as out, (EVIDENCE / (name + '.stderr')).open('wb') as err:
        result = subprocess.run(args, cwd=cwd, env=environment, stdout=out, stderr=err)
    save(name + '.json', {'exit_code': result.returncode, 'started_at': started,
                         'finished_at': datetime.now(timezone.utc).isoformat(),
                         'stdout_sha256': digest((EVIDENCE / (name + '.stdout')).read_bytes()),
                         'stderr_sha256': digest((EVIDENCE / (name + '.stderr')).read_bytes())})
    if result.returncode != 0:
        raise RuntimeError(name + ' failed; inspect retained stdout/stderr before retry')
    return (EVIDENCE / (name + '.stdout')).read_text()

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode().strip()

def clean_tracked():
    for args in (['git', 'diff', '--quiet'], ['git', 'diff', '--cached', '--quiet']):
        if subprocess.run(args, cwd=ROOT).returncode != 0:
            raise RuntimeError('tracked source is not clean')

def inactive_status(name, guard):
    # Caller owns the official operation mutex. Calling status() here deadlocks.
    result = {'active': guard.LEASE_PATH.exists(), 'lease_directory_exists': guard.LEASE_DIR.exists(),
              'observed_under_official_operation_mutex': True}
    save(name + '.json', result)
    if result['active'] or result['lease_directory_exists']:
        raise RuntimeError('a release lease owns the workspace')
    return result

def snapshot():
    names = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=ROOT).split(b'\0')
    records = {}
    for raw in names:
        if not raw:
            continue
        name = os.fsdecode(raw)
        path = ROOT / name
        info = path.lstat()
        mode = info.st_mode
        metadata = {'mode': stat.S_IMODE(mode), 'inode': info.st_ino, 'mtime_ns': info.st_mtime_ns}
        if stat.S_ISLNK(mode):
            records[name] = {'kind': 'symlink', 'target': os.readlink(path), **metadata}
        elif stat.S_ISREG(mode):
            data = path.read_bytes()
            records[name] = {'kind': 'file', 'bytes': len(data), 'sha256': digest(data), **metadata}
        else:
            raise RuntimeError('unsupported untracked file type')
    return records

def assert_preserved(before, after):
    if before.keys() != after.keys():
        raise RuntimeError('unrelated path set changed')
    for name, old in before.items():
        new = after[name]
        if name != APPEND_ONLY:
            if old != new:
                raise RuntimeError('unrelated path changed: ' + name)
            continue
        if old['kind'] != 'file' or new['kind'] != 'file' or new['bytes'] < old['bytes']:
            raise RuntimeError('bookkeeping file was replaced or truncated')
        if any(old[k] != new[k] for k in ['mode', 'inode']):
            raise RuntimeError('bookkeeping identity changed')
        if digest((ROOT/name).read_bytes()[:old['bytes']]) != old['sha256']:
            raise RuntimeError('bookkeeping prefix changed')

def preflight_generated_proof_root():
    root = ROOT / 'frontend' / '.release'
    try:
        info = root.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode):
        raise RuntimeError('generated proof root is not a real directory')
    allowed = {'reproducible-build.json', 'reproducible-build.json.tmp'}
    for path in root.iterdir():
        if path.name not in allowed or not stat.S_ISREG(path.lstat().st_mode):
            raise RuntimeError('unexpected entry in generated proof directory')

def run_under_lock(guard):
    EVIDENCE.mkdir(parents=True, exist_ok=False)
    try:
        if digest(EXPECTED_PROOF.read_bytes()) != PROOF_SHA256:
            raise RuntimeError('accepted package proof bytes changed')
        inactive_status('status-before', guard)
        if git('branch', '--show-current') != BRANCH or git('rev-parse', 'HEAD') != BASE:
            raise RuntimeError('unexpected original branch or base')
        clean_tracked()
        before = snapshot()
        save('untracked-before.json', before)
        command('fetch', ['git', 'fetch', 'origin', BRANCH])
        if git('rev-parse', 'origin/' + BRANCH) != MERGE:
            raise RuntimeError('production ref advanced unexpectedly')
        command('fast-forward', ['git', 'merge', '--ff-only', 'origin/' + BRANCH])
        if git('rev-parse', 'HEAD') != MERGE or git('rev-parse', 'HEAD^{tree}') != TREE:
            raise RuntimeError('original merge identity differs')
        clean_tracked()
        if digest((ROOT/'release/release-intent-v5.json').read_bytes()) != INTENT_SHA256:
            raise RuntimeError('intent bytes differ')
        after_sync = snapshot()
        save('untracked-after-sync.json', after_sync)
        assert_preserved(before, after_sync)
        inactive_status('status-before-build', guard)
        environment = dict(os.environ, REACT_APP_BACKEND_URL='https://mezansalla.com', PYTHONDONTWRITEBYTECODE='1')
        preflight_generated_proof_root()
        command('intent-preflight', [sys.executable, '-B', '-c', "import sys; sys.path.insert(0, 'scripts'); from emergent_deployment_adapter import load_release_intent; load_release_intent(); print('release intent verified')"], environment=environment)
        command('adapter', ['yarn', 'build'], ROOT/'frontend', environment)
        proof = json.loads(command('packages', ['python', '-B', 'scripts/emergent_deployment_adapter.py', 'verify-packages'], environment=environment))
        if proof != json.loads(EXPECTED_PROOF.read_text()):
            raise RuntimeError('original package proof differs from accepted B artifacts')
        after = snapshot()
        save('untracked-after.json', after)
        assert_preserved(before, after)
        assert_preserved(after_sync, after)
        clean_tracked()
        if git('rev-parse', 'HEAD') != MERGE or git('rev-parse', 'HEAD^{tree}') != TREE:
            raise RuntimeError('source moved during rehearsal')
        inactive_status('status-after', guard)
        result = {'ok': True, 'merge': MERGE, 'tree': TREE, 'untracked_count': len(before),
                  'untracked_preserved': True, 'package_proof_matches_accepted_B': True,
                  'append_only_before_bytes': before.get(APPEND_ONLY, {}).get('bytes'),
                  'append_only_after_bytes': after.get(APPEND_ONLY, {}).get('bytes'),
                  'active_lease': False, 'finished_at': datetime.now(timezone.utc).isoformat()}
        save('result.json', result)
        print(json.dumps(result, sort_keys=True), flush=True)
    except Exception as exc:
        failed = {'ok': False, 'error_type': type(exc).__name__, 'reason': str(exc),
                  'observed_at': datetime.now(timezone.utc).isoformat()}
        save('failed.json', failed)
        print(json.dumps(failed, sort_keys=True), flush=True)
        raise

def main():
    # Suppress bytecode before importing governed source, including without -B.
    sys.dont_write_bytecode = True
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    sys.path.insert(0, str(ROOT/'scripts'))
    import production_release_guard as guard
    with guard._release_operation_lock():
        run_under_lock(guard)

if __name__ == '__main__':
    main()

