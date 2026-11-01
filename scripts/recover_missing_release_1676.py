#!/usr/bin/env python3
"""Restore the frozen PR1676 build, retain a small persistent copy, then hand off.

Only generated files are rebuilt, using the already-committed intent. Never
freeze a different intent, commit/push, delete a worktree, reset, or publish.
The unchanged hash-pinned handoff performs the authorized /app update and
prepare/prepublish. Large clones/caches live on /tmp; verified output and
journals live under /app/.git so /tmp is not their only copy.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import urllib.request
from urllib.parse import urlparse

APP = Path('/app')
DURABLE = APP / '.git/mezan-recovery-1676'
REMOTE = 'https://github.com/AMASI-SA/AMASI-SA.git'
BRANCH = 'hotfix/prod-snap-meta-final'
BASE = '3bac875cfbcbc7f66849edae83c478dd194d9c12'
SOURCE = '667d94a3b01b7a59f58b753dfbf7d52c80764e02'
INTENT = '5a11cfcc3b600479ff69d19d76bc565019a70bfd'
MERGED = 'ffa145f22dbf4167649736336083d0d16f9fed27'
RELEASE = 'rg5-d63e7b8c4834f9e1c133126799ec47cac167658abb58009220590b4ff3572bb7'
HANDOFF_SHA = 'd2e33f50b55a3cf523ab21b99a13f4a949f7cf47'
HANDOFF_HASH = 'e60698361d08587825754e222727ba983813256ad10824697f343eaa6fefcc89'
HANDOFF_URL = ('https://raw.githubusercontent.com/AMASI-SA/AMASI-SA/' + HANDOFF_SHA +
               '/scripts/handoff_mezan_release_1676.py')
GENERATED = ('frontend/build', 'frontend/.release/reproducible-build.json',
             'backend/release_identity.json')
MIB = 1024**2
STAMP = {'schema': 1, 'source_git_sha': SOURCE, 'intent_git_sha': INTENT,
         'deployment_git_sha': MERGED, 'release_id': RELEASE}
HOST_KEYS = ('PATH', 'LANG', 'LC_ALL', 'TZ', 'ALL_PROXY', 'HTTPS_PROXY', 'HTTP_PROXY',
             'NO_PROXY', 'SSL_CERT_DIR', 'SSL_CERT_FILE', 'all_proxy', 'https_proxy',
             'http_proxy', 'no_proxy')


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def load_handoff(raw):
    require(hashlib.sha256(raw).hexdigest() == HANDOFF_HASH,
            'Original handoff integrity mismatch; nothing executed.')
    module = types.ModuleType('verified_handoff_1676')
    exec(compile(raw, 'hash-pinned-original-handoff-1676', 'exec'), module.__dict__)
    for key, expected in [('BASE', BASE), ('SOURCE', SOURCE), ('INTENT', INTENT),
                          ('MERGED', MERGED), ('RELEASE', RELEASE)]:
        require(getattr(module, key) == expected, 'Handoff pin changed: ' + key)
    return module


def real_dir(path, *, create=False, private=False):
    if create and not path.exists() and not path.is_symlink():
        path.mkdir(mode=0o700)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
            and path.resolve(strict=True) == path, 'Unsafe directory: ' + str(path))
    require(info.st_uid == os.geteuid(), 'Unexpected directory owner: ' + str(path))
    if private:
        require(not stat.S_IMODE(info.st_mode) & 0o077, 'Directory is not private: ' + str(path))


def safe_json(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
            and info.st_nlink == 1 and info.st_size <= 4 * MIB,
            'Unsafe checkpoint: ' + str(path))
    value = json.loads(path.read_text(encoding='utf-8'))
    require(isinstance(value, dict), 'Checkpoint is not an object.')
    return value


def write_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(value, output, indent=2, ensure_ascii=False)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_stamp(value):
    require(all(value.get(key) == expected for key, expected in STAMP.items()),
            'Checkpoint belongs to another release; preserved.')


def check_app(h):
    real_dir(APP)
    real_dir(APP / '.git')
    head = h.assert_workspace()
    state = h.guard('status')
    require(state.get('active') is False and not state.get('incomplete_lease_directory'),
            'An active/incomplete release lease exists. It was not cleared or reused.')
    origin = h.git(APP, 'remote', 'get-url', 'origin')
    parsed = urlparse(origin)
    require((parsed.scheme == 'https' and parsed.hostname == 'github.com'
             and parsed.path.rstrip('/').removesuffix('.git') == '/AMASI-SA/AMASI-SA')
            or origin in ('git@github.com:AMASI-SA/AMASI-SA.git',
                          'git@github.com:AMASI-SA/AMASI-SA'), 'Unexpected origin.')
    h.assert_remote()
    return head


def check_space(path, minimum, minimum_inodes=4096):
    space = os.statvfs(path)
    free = space.f_bavail * space.f_frsize
    print('FREE_MiB', str(path), round(free / MIB, 2), flush=True)
    require(free >= minimum, 'Insufficient space at ' + str(path) + '; nothing deleted.')
    require(not space.f_files or space.f_favail >= minimum_inodes,
            'Insufficient file slots at ' + str(path))


def build_environment(root):
    for name in ('home', 'cache', 'tmp'):
        real_dir(root / name, create=True, private=True)
    env = {key: os.environ[key] for key in HOST_KEYS if key in os.environ}
    env.update(HOME=str(root / 'home'), XDG_CACHE_HOME=str(root / 'cache'),
               TMPDIR=str(root / 'tmp'), TMP=str(root / 'tmp'), TEMP=str(root / 'tmp'),
               CI='false', PYTHONDONTWRITEBYTECODE='1', GIT_TERMINAL_PROMPT='0',
               GIT_PAGER='cat', PAGER='cat', REACT_APP_BACKEND_URL='https://mezansalla.com')
    # No bootstrap or Github-CI markers: B must consume its frozen reviewed intent.
    return env


def stream_command(h, argv, cwd, env):
    log_path = DURABLE / 'recovery-build.log'
    descriptor = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a', encoding='utf-8') as log:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   encoding='utf-8', errors='replace')
        assert process.stdout is not None
        for line in process.stdout:
            safe = h.sanitized(line.rstrip('\n'))
            print(safe, flush=True)
            log.write(safe + '\n')
        code = process.wait()
    require(code == 0, 'Frozen-intent build failed; see ' + str(log_path))


def verify_source(h, review):
    real_dir(review)
    require(h.git(review, 'rev-parse', '--show-toplevel') == str(review), 'Wrong recovery repository.')
    require(h.git(review, 'rev-parse', 'HEAD') == INTENT, 'Recovery source is not frozen B.')
    require(not h.git(review, 'status', '--porcelain=v1', '--untracked-files=all'),
            'Recovery source changed; no overwrite permitted.')
    h.git(review, 'merge-base', '--is-ancestor', SOURCE, INTENT)
    h.git(review, 'merge-base', '--is-ancestor', INTENT, MERGED)
    require(h.git(review, 'rev-parse', INTENT + '^{tree}') ==
            h.git(review, 'rev-parse', MERGED + '^{tree}'), 'Merge tree differs from B.')
    require(h.git(review, 'diff', '--name-only', SOURCE, INTENT) == 'release/release-intent-v5.json',
            'A/B relation is not intent-only.')
    intent = safe_json(review / 'release/release-intent-v5.json')
    h.validate_intent(intent)
    return intent


def verify_result(h, review, intent):
    for relative in GENERATED:
        h.snapshot(h.safe_path(review, relative))
    h.verify_artifacts(review, intent)
    result = json.loads(h.run([sys.executable, '-B', 'scripts/emergent_deployment_adapter.py',
                              'verify-packages'], review, timeout=300))
    require(result.get('source_git_sha') == SOURCE and result.get('release_id') == RELEASE,
            'Package proof identity mismatch.')
    require(result.get('backend', {}).get('isolated_verified') is True
            and result.get('backend', {}).get('health_verified') is True
            and result.get('frontend', {}).get('isolated_runtime_artifact_verified') is True,
            'Package boundary verification failed.')
    require(result.get('frontend', {}).get('artifact_tree_sha256') == h.ARTIFACT_TREE,
            'Recovered artifact hash differs from the reviewed artifact.')
    return result


def normalized_snapshot(h, path):
    return {name: list(value) for name, value in h.snapshot(path).items()}


def copy_generated(h, source_root, target_root):
    for relative in GENERATED:
        source = h.safe_path(source_root, relative)
        expected = h.snapshot(source)
        target = h.safe_path(target_root, relative, allow_missing=True)
        require(not target.exists() and not target.is_symlink(),
                'Generated destination already exists; preserved: ' + str(target))
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        h.safe_path(target_root, relative, allow_missing=True)
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True)
        else:
            shutil.copy2(source, target, follow_symlinks=False)
        require(h.snapshot(target) == expected, 'Recovered copy hash mismatch.')


def save_artifacts(h, review, intent):
    destination = DURABLE / 'verified-artifacts'
    if destination.exists() or destination.is_symlink():
        validate_saved(h, intent)
        return
    block = max(os.statvfs(APP).f_frsize, 4096)
    needed = sum(h.allocated(h.snapshot(h.safe_path(review, rel)), block) for rel in GENERATED)
    check_space(APP, 300 * MIB + 2 * needed)
    incoming = Path(tempfile.mkdtemp(prefix='.artifacts-', dir=str(DURABLE)))
    copy_generated(h, review, incoming)
    records = {rel: normalized_snapshot(h, incoming / rel) for rel in GENERATED}
    write_json(incoming / 'manifest.json', {**STAMP, 'records': records})
    os.rename(incoming, destination)
    validate_saved(h, intent)
    print('PERSISTENT_VERIFIED_ARTIFACTS_SAVED', str(destination), flush=True)


def validate_saved(h, intent):
    root = DURABLE / 'verified-artifacts'
    real_dir(root, private=True)
    manifest = safe_json(root / 'manifest.json')
    validate_stamp(manifest)
    require(set(manifest.get('records', {})) == set(GENERATED), 'Saved output membership mismatch.')
    for relative in GENERATED:
        require(normalized_snapshot(h, h.safe_path(root, relative)) == manifest['records'][relative],
                'Persistent artifact changed; preserved for review.')
    require(safe_json(root / 'backend/release_identity.json') == intent['runtime_identity'],
            'Saved runtime identity differs from frozen intent.')
    return root


def ensure_review(h, state, state_file):
    root_text = state.get('build_root')
    if root_text:
        root = Path(root_text)
        require(root.parent == Path('/tmp') and root.name.startswith('mezan-1676-recovery-'),
                'Invalid recorded recovery path.')
        if root.is_symlink():
            raise RuntimeError('Recorded recovery path is redirected.')
    else:
        root = None
    if root is None or not root.exists():
        real_dir(Path('/tmp'))
        # /tmp is normally owned by root; on this root-operated host this is expected.
        require(Path('/tmp').stat().st_dev != APP.stat().st_dev,
                'Build scratch space is on the constrained /app filesystem.')
        check_space(Path('/tmp'), 5 * 1024**3, 100000)
        root = Path(tempfile.mkdtemp(prefix='mezan-1676-recovery-', dir='/tmp'))
        state.update(build_root=str(root), phase='source_recovery')
        write_json(state_file, state)
    real_dir(root, private=True)
    require(root.stat().st_dev != APP.stat().st_dev, 'Build directory shares the full /app filesystem.')
    review = root / 'rehearsal'
    if not review.exists() and not review.is_symlink():
        print('=== RECOVERING EXACT COMMITTED RELEASE — NO NEW INTENT ===', flush=True)
        h.run(['git', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', 'clone',
               '--no-checkout', '--single-branch', '--branch', BRANCH, REMOTE, str(review)],
              APP, timeout=600)
        require(h.git(review, 'rev-parse', 'origin/' + BRANCH) == MERGED,
                'Production changed while cloning.')
        h.git(review, 'checkout', '--detach', INTENT)
    return root, review


def main():
    with urllib.request.urlopen(HANDOFF_URL, timeout=45) as response:
        raw = response.read(200001)
    h = load_handoff(raw)
    print('=== CHECKING LOCAL STATE AND MERGED RELEASE ===', flush=True)
    check_app(h)
    check_space(APP, 300 * MIB)
    real_dir(DURABLE, create=True, private=True)
    with h.locked_file(DURABLE / 'recovery.lock'):
        state_file = DURABLE / 'recovery-state.json'
        if state_file.exists() or state_file.is_symlink():
            state = safe_json(state_file)
            validate_stamp(state)
            require(state.get('phase') not in ('handoff_started', 'handoff_completed'),
                    'A handoff was already started. Preserve durable journals; do not repeat blindly.')
        else:
            state = {**STAMP, 'phase': 'initial', 'publish_performed': False}
            write_json(state_file, state)
        root, review = ensure_review(h, state, state_file)
        intent = verify_source(h, review)
        original_env = h.ENV.copy()
        env = build_environment(root)
        h.ENV.update({name: env[name] for name in ('TMPDIR', 'TMP', 'TEMP')})
        # Prefer already verified outputs. Never re-create a frozen intent.
        persistent = DURABLE / 'verified-artifacts'
        have_all = all((review / rel).exists() for rel in GENERATED)
        have_any = any((review / rel).exists() or (review / rel).is_symlink() for rel in GENERATED)
        if persistent.exists() or persistent.is_symlink():
            validate_saved(h, intent)
            if not have_any:
                copy_generated(h, persistent, review)
            else:
                require(have_all, 'Partial generated outputs exist; preserve them and review the log.')
        elif not have_all:
            check_app(h)
            check_space(root, 5 * 1024**3, 100000)
            state.update(phase='rebuilding_frozen_artifacts')
            write_json(state_file, state)
            print('=== RECREATING LOST GENERATED FILES USING FROZEN INTENT ===', flush=True)
            stream_command(h, [sys.executable, '-B', 'scripts/emergent_deployment_adapter.py', 'build'],
                           review, env)
        proof = verify_result(h, review, intent)
        verify_source(h, review)
        print('FROZEN_RELEASE_RESTORED_AND_VERIFIED', RELEASE, flush=True)
        save_artifacts(h, review, intent)
        write_json(DURABLE / 'package-proof.json', proof)
        write_json(DURABLE / 'checkpoint.json', {
            **STAMP, 'rehearsal_passed': True, 'production_changed': False,
            'generated_files_recovered': True, 'review_directory': str(review),
            'persistent_artifacts': str(persistent)})
        state.update(phase='verified_outputs_saved')
        write_json(state_file, state)
        check_app(h)
        # Only runtime locations change; the original handoff and its guards
        # remain byte-for-byte hash-pinned. Its journals are now persistent too.
        h.ROOT = DURABLE
        h.REVIEW = review
        state.update(phase='handoff_started')
        write_json(state_file, state)
        print('=== RUNNING VERIFIED HANDOFF — NO PUBLISH ===', flush=True)
        h.main()
        state.update(phase='handoff_completed')
        write_json(state_file, state)
        print('RECOVERY_RECORD', str(state_file), flush=True)
        print('PERSISTENT_ARTIFACT_COPY', str(persistent), flush=True)
        print('Publish was not performed. Do not rerun preparation after the lease is active.', flush=True)
        h.ENV = original_env


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('RECOVERY_STOPPED:', str(error), flush=True)
        print('No automatic Publish. Preserve /app/.git/mezan-recovery-1676 and all handoff backups.', flush=True)
        raise SystemExit(1)
