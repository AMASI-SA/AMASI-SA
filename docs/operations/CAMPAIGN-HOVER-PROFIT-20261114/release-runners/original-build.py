"""Run the reviewed PR1920 adapter without a lease, preserving unrelated files."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path('/app')
SOURCE = '2cf4d70d7e0c23c11048cad5c610e8c0c60d7eeb'
BASE = '4c623c640fb63ac3c138fa4a9cabb20e2b39dca7'
TREE = '14dfe135d6633f72c92cb1cb359f4b9933ac176a'
BRANCH = 'hotfix/prod-snap-meta-final'
RELEASE = 'rg5-5c3980b0f83f3859023070ef846486e877849ca7ca848367a181995ca32c1d5e'
assert len(sys.argv) == 2 and re.fullmatch(r'[0-9a-f]{40}', sys.argv[1])
DEPLOYMENT = sys.argv[1]
os.umask(0o077)
EVIDENCE = Path(tempfile.mkdtemp(prefix='mezan-pr1920-original-', dir='/tmp'))
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', REACT_APP_BACKEND_URL='https://mezansalla.com')
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from scripts.production_release_guard import _release_operation_lock, LEASE_DIR, LEASE_PATH


def run(name, argv, cwd=ROOT):
    started = time.monotonic()
    with (EVIDENCE / (name + '.log')).open('w') as handle:
        result = subprocess.run(argv, cwd=cwd, env=ENV, stdout=handle, stderr=subprocess.STDOUT)
    record = {'argv': argv, 'cwd': str(cwd), 'exit_code': result.returncode,
              'seconds': time.monotonic() - started}
    (EVIDENCE / (name + '.json')).write_text(json.dumps(record, indent=2) + '\n')
    if result.returncode:
        raise RuntimeError(f'{name} failed with exit {result.returncode}; evidence={EVIDENCE}')
    return (EVIDENCE / (name + '.log')).read_text()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, env=ENV).decode().strip()


def inactive(name):
    result = json.loads(run(name, ['python', '-B', 'scripts/production_release_guard.py', 'status']))
    assert result.get('active') is False and not result.get('incomplete_lease_directory'), result


def locked_inactive(name):
    # The official operation mutex is already held; status() is not reentrant.
    assert not LEASE_PATH.exists() and not LEASE_DIR.exists(), 'Active or incomplete release lease'
    (EVIDENCE / (name + '.json')).write_text(json.dumps({'active': False, 'under_operation_lock': True}) + '\n')


def preserved_files():
    paths = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=ROOT, env=ENV)
    result = {}
    for raw in paths.split(b'\0'):
        if not raw:
            continue
        name = os.fsdecode(raw)
        path = ROOT / name
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            payload = {'link': os.readlink(path)}
        else:
            assert stat.S_ISREG(metadata.st_mode), name
            digest = hashlib.sha256()
            with path.open('rb') as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(chunk)
            payload = {'bytes': metadata.st_size, 'sha256': digest.hexdigest()}
        result[name] = dict(payload, mode=metadata.st_mode, inode=metadata.st_ino, mtime_ns=metadata.st_mtime_ns)
    return result


print(f'EVIDENCE={EVIDENCE}', flush=True)
inactive('01-guard-before')
with _release_operation_lock():
    locked_inactive('01b-guard-under-lock')
    assert git('branch', '--show-current') == BRANCH
    assert git('rev-parse', 'HEAD') == BASE
    assert not git('status', '--porcelain', '--untracked-files=no')
    before = preserved_files()
    (EVIDENCE / 'untracked-before.json').write_text(json.dumps(before, indent=2) + '\n')
    run('02-fetch', ['git', 'fetch', 'origin', BRANCH])
    assert git('rev-parse', 'origin/' + BRANCH) == DEPLOYMENT
    assert git('rev-parse', DEPLOYMENT + '^{tree}') == TREE
    assert git('rev-list', '--parents', '-n', '1', DEPLOYMENT).split()[1:] == [BASE, 'dcb97791d6ef8ad394575542eb4048c6c867bbba']
    assert git('diff', '--name-only', SOURCE, DEPLOYMENT) == 'release/release-intent-v5.json'
    locked_inactive('03-guard-before-fast-forward')
    run('04-fast-forward', ['git', 'merge', '--ff-only', DEPLOYMENT])
    assert not git('status', '--porcelain', '--untracked-files=no')
    assert preserved_files() == before
    locked_inactive('05-guard-before-build')
    run('06-governed-adapter', ['yarn', 'build'], ROOT / 'frontend')
    run('07-package-proof', ['python', '-B', 'scripts/emergent_deployment_adapter.py', 'verify-packages'])
    intent = json.loads((ROOT / 'release/release-intent-v5.json').read_text())
    identity = json.loads((ROOT / 'backend/release_identity.json').read_text())
    assert identity == intent['runtime_identity']
    assert identity['source_git_sha'] == SOURCE and identity['release_id'] == RELEASE
    assert git('rev-parse', 'HEAD') == DEPLOYMENT and not git('status', '--porcelain', '--untracked-files=no')
    after = preserved_files()
    (EVIDENCE / 'untracked-after.json').write_text(json.dumps(after, indent=2) + '\n')
    assert after == before
    locked_inactive('08-guard-after-build')
summary = {'source': SOURCE, 'deployment': DEPLOYMENT, 'tree': TREE, 'release_id': RELEASE,
           'adapter_exit': 0, 'package_proof_exit': 0, 'preserved_untracked_files': len(before),
           'guard_inactive': True, 'evidence': str(EVIDENCE)}
(EVIDENCE / 'result.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary, indent=2), flush=True)
