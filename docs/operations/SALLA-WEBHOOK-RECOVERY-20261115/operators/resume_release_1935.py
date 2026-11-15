#!/usr/bin/env python3
"""Resume the original M rehearsal after preserving two ignored bytecode files.

No merge, source edit, lease preparation, publication, or cache deletion occurs.
The original failed attempt and every unrelated untracked path remain intact.
"""
import sys

sys.dont_write_bytecode = True

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
from datetime import datetime, timezone

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

ORIGINAL_OPERATOR = Path(__file__).with_name('original_release_1935.py')
ORIGINAL_OPERATOR_SHA256 = '5ea00d759706151f89914ae96c2afd5db9d3d485be917baf037cd25f6c785e27'
ORIGINAL_EVIDENCE = Path('/tmp/salla-webhook-release-1935/original')
EVIDENCE = Path('/tmp/salla-webhook-release-1935/resume-bytecode-1')
QUARANTINE = Path('/app/.git/salla-webhook-release-1935-bytecode')
CACHE_RELATIVE = 'backend/__pycache__'
CACHE_CONTENT = {
    'frontend_build_identity.cpython-311.pyc': {
        'bytes': 39622,
        'sha256': '489ba93d9df88a1e4f9a7e703f5ab838f93cf60e5f6f60fe10b7eee45361b67b',
    },
    'release_protocol_v5.cpython-311.pyc': {
        'bytes': 80941,
        'sha256': 'c1c09df55f54c1692c24af99f4001907d6717ff06f72b95c21ec58209962143d',
    },
}
CACHE_NAMES = frozenset(CACHE_CONTENT)
ORIGINAL_OUTPUT_HASHES = {
    'adapter.stdout': '39e8c99b49f04709f92dd3dab437d25686d350f99bcbfbabb95451a36129ee5b',
    'adapter.stderr': '69b3ab66053b42d01223b2ca5f87ccb8c74b8454f952fecfcca8efb5fd4ae470',
    'fast-forward.stdout': '0326a09d2ed771026004abec1b7a26a3dd66ffd8a012c372519dba599516ff90',
}
CACHE_REFUSAL = (
    'EMERGENT_DEPLOYMENT_REFUSED: release source manifests are invalid: '
    'Backend runtime source contains prepackaged bytecode: __pycache__'
)
ORIGINAL_FAILURE = 'adapter failed; inspect retained stdout/stderr before retry'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def entry_exists(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def real_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.absolute() != path.resolve(strict=True):
        raise RuntimeError('expected a real directory without symlinked parents: ' + str(path))
    return info


def regular_bytes(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.absolute() != path.resolve(strict=True):
        raise RuntimeError('expected a regular file without symlinked parents: ' + str(path))
    return path.read_bytes()


def metadata(info):
    # Renaming preserves these fields; ctime and read-induced atime are excluded.
    return {
        'mode': stat.S_IMODE(info.st_mode), 'device': info.st_dev,
        'inode': info.st_ino, 'mtime_ns': info.st_mtime_ns,
        'uid': info.st_uid, 'gid': info.st_gid, 'nlink': info.st_nlink,
    }


def load_original_operator():
    raw = regular_bytes(ORIGINAL_OPERATOR)
    if digest(raw) != ORIGINAL_OPERATOR_SHA256:
        raise RuntimeError('original helper operator bytes changed')
    spec = importlib.util.spec_from_file_location('_pinned_original_release_1935', ORIGINAL_OPERATOR)
    module = importlib.util.module_from_spec(spec)
    # Execute the verified source bytes, never a sibling cached .pyc.
    exec(compile(raw, str(ORIGINAL_OPERATOR), 'exec', dont_inherit=True), module.__dict__)
    if module.ROOT != Path('/app') or module.EVIDENCE != ORIGINAL_EVIDENCE:
        raise RuntimeError('original helper locations differ')
    if ORIGINAL_OPERATOR.resolve().is_relative_to(module.ROOT):
        raise RuntimeError('operator must remain outside the source workspace')
    return module


def remote_sha(base):
    ref = 'refs/heads/' + base.BRANCH
    output = subprocess.check_output(
        ['git', 'ls-remote', '--exit-code', 'origin', ref], cwd=base.ROOT,
    ).decode().splitlines()
    if output != [base.MERGE + '\t' + ref]:
        raise RuntimeError('production remote is no longer the reviewed merge')
    return base.MERGE


def pin_workspace(base, *, check_remote):
    real_directory(base.ROOT)
    real_directory(base.ROOT / 'backend')
    git_directory = base.ROOT / '.git'
    real_directory(git_directory)
    if base.git('rev-parse', '--absolute-git-dir') != str(git_directory):
        raise RuntimeError('Git metadata must be the real original /app/.git directory')
    if base.git('branch', '--show-current') != base.BRANCH:
        raise RuntimeError('unexpected original branch')
    if base.git('rev-parse', 'HEAD') != base.MERGE:
        raise RuntimeError('resume requires the already fast-forwarded merge M')
    if base.git('rev-parse', 'HEAD^{tree}') != base.TREE:
        raise RuntimeError('resume source tree differs')
    if base.git('rev-parse', 'origin/' + base.BRANCH) != base.MERGE:
        raise RuntimeError('recorded production tracking ref differs')
    base.clean_tracked()
    if digest(regular_bytes(base.ROOT / 'release/release-intent-v5.json')) != base.INTENT_SHA256:
        raise RuntimeError('tracked release intent bytes differ')
    if digest(regular_bytes(base.EXPECTED_PROOF)) != base.PROOF_SHA256:
        raise RuntimeError('accepted package proof bytes differ')
    if check_remote:
        remote_sha(base)


def original_receipts(base):
    real_directory(ORIGINAL_EVIDENCE)
    names = ['failed.json', 'untracked-before.json', 'untracked-after-sync.json',
             'status-before.json', 'status-before-build.json']
    for command in ('fetch', 'fast-forward', 'adapter'):
        names.extend(command + suffix for suffix in
                     ('.started.json', '.json', '.stdout', '.stderr'))
    raw = {name: regular_bytes(ORIGINAL_EVIDENCE / name) for name in names}
    for name, expected_hash in ORIGINAL_OUTPUT_HASHES.items():
        if digest(raw[name]) != expected_hash:
            raise RuntimeError('original output differs from the independently observed receipt: ' + name)
    record = {
        name: {'bytes': len(data), 'sha256': digest(data),
               **metadata((ORIGINAL_EVIDENCE / name).lstat())}
        for name, data in raw.items()
    }
    expected_commands = {
        'fetch': (['git', 'fetch', 'origin', base.BRANCH], str(base.ROOT)),
        'fast-forward': (['git', 'merge', '--ff-only', 'origin/' + base.BRANCH], str(base.ROOT)),
        'adapter': (['yarn', 'build'], str(base.ROOT / 'frontend')),
    }
    for name, (args, cwd) in expected_commands.items():
        receipt = json.loads(raw[name + '.json'])
        started = json.loads(raw[name + '.started.json'])
        if started.get('args') != args or started.get('cwd') != cwd:
            raise RuntimeError('original command identity differs: ' + name)
        exit_code = receipt.get('exit_code')
        if type(exit_code) is not int or exit_code != (1 if name == 'adapter' else 0):
            raise RuntimeError('original command outcome differs: ' + name)
        for stream in ('stdout', 'stderr'):
            if receipt.get(stream + '_sha256') != digest(raw[name + '.' + stream]):
                raise RuntimeError('original command output hash differs: ' + name + '.' + stream)
    refusal_lines = [line for line in raw['adapter.stderr'].decode('utf-8').splitlines()
                     if line.startswith('EMERGENT_DEPLOYMENT_REFUSED:')]
    if refusal_lines != [CACHE_REFUSAL]:
        raise RuntimeError('original failure is not the single reviewed bytecode refusal')
    failed = json.loads(raw['failed.json'])
    if (failed.get('ok') is not False or failed.get('error_type') != 'RuntimeError'
            or failed.get('reason') != ORIGINAL_FAILURE):
        raise RuntimeError('original failure receipt differs')
    for name in ('status-before.json', 'status-before-build.json'):
        state = json.loads(raw[name])
        if (state.get('active') is not False or state.get('lease_directory_exists') is not False
                or state.get('observed_under_official_operation_mutex') is not True):
            raise RuntimeError('original lease receipt differs: ' + name)
    for name in ('result.json', 'packages.started.json', 'packages.json', 'packages.stdout', 'packages.stderr'):
        if entry_exists(ORIGINAL_EVIDENCE / name):
            raise RuntimeError('original attempt proceeded beyond the reviewed failure')
    before = json.loads(raw['untracked-before.json'])
    after_sync = json.loads(raw['untracked-after-sync.json'])
    if not isinstance(before, dict) or not isinstance(after_sync, dict) or not before:
        raise RuntimeError('original preservation snapshots are unavailable')
    base.assert_preserved(before, after_sync)
    return record, before, after_sync


def cache_layout(base, *, expected_present):
    caches = []
    bytecode = []
    def failed_walk(error):
        raise error
    for directory, directories, files in os.walk(
            base.ROOT / 'backend', followlinks=False, onerror=failed_walk):
        current = Path(directory)
        caches.extend((current / name).relative_to(base.ROOT).as_posix()
                      for name in directories if name == '__pycache__')
        bytecode.extend((current / name).relative_to(base.ROOT).as_posix()
                        for name in files if Path(name).suffix.casefold() in {'.pyc', '.pyo'})
    expected_caches = [CACHE_RELATIVE] if expected_present else []
    expected_bytecode = sorted(CACHE_RELATIVE + '/' + name for name in CACHE_NAMES) if expected_present else []
    if sorted(caches) != expected_caches or sorted(bytecode) != expected_bytecode:
        raise RuntimeError('Backend bytecode layout differs from the sole reviewed cache')


def cache_snapshot(path):
    directory_info = real_directory(path)
    mode = stat.S_IMODE(directory_info.st_mode)
    if mode & 0o7022 or mode & 0o500 != 0o500:
        raise RuntimeError('cache directory permissions are unsafe')
    entries = sorted(path.iterdir(), key=lambda item: item.name)
    if {entry.name for entry in entries} != CACHE_NAMES or len(entries) != 2:
        raise RuntimeError('cache entries differ from the two reviewed filenames')
    records = {}
    for entry in entries:
        info = entry.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or mode & 0o7133 or not mode & 0o400):
            raise RuntimeError('cache entry is not a safe ordinary bytecode file: ' + entry.name)
        data = regular_bytes(entry)
        if {'bytes': len(data), 'sha256': digest(data)} != CACHE_CONTENT[entry.name]:
            raise RuntimeError('cache content differs from the independently observed bytes: ' + entry.name)
        records[entry.name] = {'bytes': len(data), 'sha256': digest(data), **metadata(info)}
    return {'directory': metadata(directory_info), 'files': records}


def assert_cache_ignored_untracked(base):
    if base.git('ls-files', '--cached', '--', CACHE_RELATIVE):
        raise RuntimeError('cache contains tracked content')
    for name in sorted(CACHE_NAMES):
        relative = CACHE_RELATIVE + '/' + name
        result = subprocess.run(['git', 'check-ignore', '--quiet', '--', relative], cwd=base.ROOT)
        if result.returncode != 0:
            raise RuntimeError('cache entry is not ignored: ' + relative)


def preserved(base, originals, current):
    for previous in originals:
        base.assert_preserved(previous, current)


def run_under_lock(base, guard):
    created = False
    base.EVIDENCE = EVIDENCE
    try:
        if entry_exists(guard.LEASE_DIR) or entry_exists(guard.LEASE_PATH):
            raise RuntimeError('a release lease owns the workspace')
        if entry_exists(EVIDENCE):
            raise RuntimeError('resume evidence already exists; this operator must not be rerun')
        if entry_exists(QUARANTINE):
            raise RuntimeError('private Git metadata quarantine already exists')
        real_directory(EVIDENCE.parent)
        pin_workspace(base, check_remote=True)
        receipts, original_before, original_after_sync = original_receipts(base)
        before = base.snapshot()
        preserved(base, (original_before, original_after_sync), before)
        cache_layout(base, expected_present=True)
        assert_cache_ignored_untracked(base)
        cache = base.ROOT / CACHE_RELATIVE
        cache_before = cache_snapshot(cache)
        if cache_before['directory']['device'] != real_directory(QUARANTINE.parent).st_dev:
            raise RuntimeError('quarantine would cross filesystems; no copy/delete fallback is allowed')
        # No cache or source mutation occurs until all preceding observations pass.
        EVIDENCE.mkdir(mode=0o700, exist_ok=False)
        created = True
        base.save('original-receipts.json', receipts)
        base.save('untracked-before.json', before)
        base.save('cache-before.json', cache_before)
        base.save('preflight.json', {
            'original_operator_sha256': ORIGINAL_OPERATOR_SHA256,
            'original_evidence': str(ORIGINAL_EVIDENCE), 'merge': base.MERGE,
            'tree': base.TREE, 'remote_merge': base.MERGE,
            'intent_sha256': base.INTENT_SHA256, 'package_proof_sha256': base.PROOF_SHA256,
            'python_bytecode_disabled': sys.dont_write_bytecode,
            'quarantine_root': str(QUARANTINE),
            'quarantine_git_directory': str(QUARANTINE.parent),
            'quarantine_device': cache_before['directory']['device'],
        })
        base.inactive_status('status-before', guard)
        quarantine = QUARANTINE
        quarantine.mkdir(mode=0o700, exist_ok=False)
        destination = quarantine / 'backend-__pycache__'
        if entry_exists(destination) or quarantine.stat().st_dev != cache_before['directory']['device']:
            raise RuntimeError('quarantine destination is not fresh on the same filesystem')
        pin_workspace(base, check_remote=False)
        current = base.snapshot()
        preserved(base, (original_before, original_after_sync, before), current)
        if original_receipts(base)[0] != receipts or cache_snapshot(cache) != cache_before:
            raise RuntimeError('original evidence or cache changed during preflight')
        cache_layout(base, expected_present=True)
        assert_cache_ignored_untracked(base)
        os.rename(cache, destination)
        # Atomic rename is the only cache operation. In particular, EXDEV is fatal.
        cache_after = cache_snapshot(destination)
        if cache_after != cache_before or entry_exists(cache):
            raise RuntimeError('quarantined bytecode differs or the source cache reappeared')
        base.save('quarantine.json', {
            'source': str(cache), 'destination': str(destination),
            'method': 'same-device atomic rename', 'preserved': True,
            'metadata': cache_after,
        })
        cache_layout(base, expected_present=False)
        after_quarantine = base.snapshot()
        base.save('untracked-after-quarantine.json', after_quarantine)
        preserved(base, (original_before, original_after_sync, before), after_quarantine)
        pin_workspace(base, check_remote=False)
        base.inactive_status('status-before-build', guard)
        environment = dict(os.environ, REACT_APP_BACKEND_URL='https://mezansalla.com',
                           PYTHONDONTWRITEBYTECODE='1')
        base.command('adapter', ['yarn', 'build'], base.ROOT / 'frontend', environment)
        proof = json.loads(base.command(
            'packages', ['python', '-B', 'scripts/emergent_deployment_adapter.py', 'verify-packages'],
            environment=environment,
        ))
        if proof != json.loads(regular_bytes(base.EXPECTED_PROOF)):
            raise RuntimeError('resumed package proof differs from accepted B artifacts')
        after = base.snapshot()
        base.save('untracked-after.json', after)
        preserved(base, (original_before, original_after_sync, before, after_quarantine), after)
        pin_workspace(base, check_remote=True)
        cache_layout(base, expected_present=False)
        if cache_snapshot(destination) != cache_before or original_receipts(base)[0] != receipts:
            raise RuntimeError('quarantined cache or original attempt evidence changed during rehearsal')
        base.inactive_status('status-after', guard)
        result = {
            'ok': True, 'merge': base.MERGE, 'tree': base.TREE,
            'untracked_count': len(before), 'untracked_preserved': True,
            'original_attempt_preserved': True, 'package_proof_matches_accepted_B': True,
            'quarantined_cache_files': sorted(CACHE_NAMES),
            'quarantine_directory': str(destination),
            'append_only_before_bytes': original_before.get(base.APPEND_ONLY, {}).get('bytes'),
            'append_only_after_bytes': after.get(base.APPEND_ONLY, {}).get('bytes'),
            'active_lease': False, 'finished_at': datetime.now(timezone.utc).isoformat(),
        }
        base.save('result.json', result)
        print(json.dumps(result, sort_keys=True), flush=True)
    except Exception as exc:
        failed = {'ok': False, 'error_type': type(exc).__name__, 'reason': str(exc),
                  'observed_at': datetime.now(timezone.utc).isoformat()}
        if created:
            base.save('failed.json', failed)
        print(json.dumps(failed, sort_keys=True), flush=True)
        raise


def main():
    base = load_original_operator()
    # -B prevents writes but can still read existing .pyc files. Verify their
    # independently observed bytes before importing the guard, then repeat the
    # complete preflight under its official operation mutex before any movement.
    pin_workspace(base, check_remote=False)
    cache_layout(base, expected_present=True)
    assert_cache_ignored_untracked(base)
    cache_snapshot(base.ROOT / CACHE_RELATIVE)
    sys.path.insert(0, str(base.ROOT / 'scripts'))
    import production_release_guard as guard
    with guard._release_operation_lock():
        run_under_lock(base, guard)


if __name__ == '__main__':
    main()
