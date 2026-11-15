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
BASE = '9afb38cf0dc0d8e8cd887f2c2cdb4a6be5a7ca68'
MERGE = '331b7917b3c01d064ee2a17affb4714f3a296331'
TREE = '62632e21efa7d1ed1e6f42740d1e73321e947c1d'
BRANCH = 'hotfix/prod-snap-meta-final'
INTENT_SHA256 = '50624daf51b28d1a504a9d62c9a1e9355b0d6bf641c79d0151cb2152f2039012'
PROOF_SHA256 = '9f763811000725857e83ab14d17fbac01426469a83e8e97b8aabbe62df3ee05a'
EVIDENCE = Path('/tmp/salla-webhook-release-1935/original')
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
    sys.path.insert(0, str(ROOT/'scripts'))
    import production_release_guard as guard
    with guard._release_operation_lock():
        run_under_lock(guard)

if __name__ == '__main__':
    main()
