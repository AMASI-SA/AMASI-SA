#!/usr/bin/env python3
"""One-shot, fenced PR1956 handoff. No publish and no lease removal."""
import sys
sys.dont_write_bytecode = True

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

ROOT = Path('/app')
J = 'b05cd3c9b25ce22df1b2dd06c23d97606bd632c6'
A = '7a1de33535081393fbf3597523d722ec308b2ca8'
B = '4da5d711717146088d879f4d8426cda9958f9d36'
TREE = '49140bb5e22b368788c181362f316218344e9d41'
RELEASE = 'rg5-4890bb5f409d7f6080ad2c19f881ed5edfe30b8f93b9502fbb3e45977f6a28f1'
ACTOR = 'codex-bundle-conditional-cost-1956-20261116'
BRANCH = 'hotfix/prod-snap-meta-final'
CACHE_FILES = {
    'frontend_build_identity.cpython-311.pyc': (39622, 1794782590938187103),
    'release_protocol_v5.cpython-311.pyc': (80941, 1794782590950187103),
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args]).decode().strip()


def save(name, value):
    path = receipts / name
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    tmp.replace(path)


def phase(name, **fields):
    row = {'phase': name, **fields}
    save('phase.json', row)
    print(json.dumps(row, sort_keys=True), flush=True)


def untracked_metadata():
    raw = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '--others', '--exclude-standard', '-z'])
    paths = [os.fsdecode(x) for x in raw.split(b'\0') if x]
    require(len(paths) <= 5000, 'Untracked inventory exceeds bounded scope')
    rows = []
    for relative in sorted(paths):
        p = Path(relative)
        require(not p.is_absolute() and '..' not in p.parts, 'Unsafe untracked path')
        s = (ROOT / p).lstat()
        rows.append([relative, s.st_size, s.st_mtime_ns, s.st_mode])
    return rows


def cache_inventory():
    cache = ROOT / 'backend/__pycache__'
    if not cache.exists() and not cache.is_symlink():
        return None
    s = cache.lstat()
    require(stat.S_ISDIR(s.st_mode) and not stat.S_ISLNK(s.st_mode), 'Cache is not a real directory')
    require(stat.S_IMODE(s.st_mode) == 0o755 and s.st_mtime_ns == 1794782590950187103,
            'Cache directory differs from the observed inventory')
    require({p.name for p in cache.iterdir()} == set(CACHE_FILES), 'Unexpected cache members')
    require(not git('ls-files', '--', 'backend/__pycache__'), 'Cache contains tracked paths')
    records = []
    for name, (size, mtime) in sorted(CACHE_FILES.items()):
        p = cache / name
        s = p.lstat()
        require(stat.S_ISREG(s.st_mode) and not stat.S_ISLNK(s.st_mode), 'Cache member is not regular')
        require(s.st_size == size and s.st_mtime_ns == mtime and stat.S_IMODE(s.st_mode) == 0o644,
                'Cache member differs from the observed inventory')
        subprocess.run(['git', '-C', str(ROOT), 'check-ignore', '-q', '--', str(p.relative_to(ROOT))], check=True)
        records.append({'name': name, 'bytes': size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()})
    return records


receipts = Path(sys.argv[1]).absolute()
require(receipts.parent == Path('/tmp') and receipts.name.startswith('mz-bundle-release-1956-'), 'Unexpected receipt directory')
require(receipts.resolve() == receipts and receipts.is_dir() and not receipts.is_symlink(), 'Receipt path is not a real directory')
require(not list(receipts.iterdir()), 'Receipt directory must be new and empty')
sys.path.insert(0, str(ROOT))
from scripts import production_release_guard as guard

try:
    require(guard.REPO_ROOT == ROOT and guard.GIT_DIR.is_dir() and not guard.GIT_DIR.is_symlink(), 'Expected original Git worktree')
    with guard._release_operation_lock():
        require(not guard.LEASE_DIR.exists() and not guard.LEASE_DIR.is_symlink(), 'Another or incomplete release lease exists')
        require(git('branch', '--show-current') == BRANCH, 'Wrong branch')
        require(git('rev-parse', 'HEAD') == J, 'Original head changed')
        require(not git('status', '--porcelain', '--untracked-files=no'), 'Tracked worktree is dirty')
        git('fetch', 'origin', BRANCH)
        require(git('rev-parse', 'origin/' + BRANCH) == J, 'Remote Production advanced')
        before = untracked_metadata()
        save('untracked-before.json', before)
        observed_cache = cache_inventory()
        save('bytecode-before.json', observed_cache)
        phase('ready_for_merge', source_A=A, expected_B=B, current_J=J,
              untracked_count=len(before), bytecode_count=len(observed_cache or []))
        submission = receipts / 'merged-head.json'
        deadline = time.monotonic() + 120
        while not submission.exists():
            require(time.monotonic() < deadline, 'Timed out before merge receipt; no deployment attempted')
            time.sleep(0.5)
        require(submission.is_file() and not submission.is_symlink(), 'Unsafe merge receipt')
        handoff = json.loads(submission.read_text())
        require(handoff.get('pr') == 1956 and handoff.get('expected_head_sha') == B, 'Wrong merge receipt')
        merged = handoff.get('deployment_git_sha')
        require(isinstance(merged, str) and re.fullmatch('[0-9a-f]{40}', merged), 'Invalid merged SHA')
        git('fetch', 'origin', BRANCH)
        require(git('rev-parse', 'origin/' + BRANCH) == merged, 'Remote does not match the merged receipt')
        require(git('show', '-s', '--format=%P', merged).split() == [J, B], 'Merge parents differ')
        require(git('rev-parse', merged + '^{tree}') == TREE, 'Merged tree differs from reviewed B')
        require(git('rev-parse', 'HEAD') == J and not git('status', '--porcelain', '--untracked-files=no'), 'Original worktree changed')
        require(cache_inventory() == observed_cache, 'Bytecode inventory changed during merge')
        if observed_cache is not None:
            cache = ROOT / 'backend/__pycache__'
            destination = receipts / 'quarantined-backend-pycache'
            require(cache.stat().st_dev == receipts.stat().st_dev, 'Quarantine requires an atomic same-filesystem move')
            cache.rename(destination)
            for row in observed_cache:
                p = destination / row['name']
                require(p.stat().st_size == row['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest() == row['sha256'], 'Quarantine byte preservation failed')
            save('bytecode-quarantine.json', {'path': str(destination), 'files': observed_cache, 'preserved': True})
        git('merge', '--ff-only', merged)
        require(git('rev-parse', 'HEAD') == merged, 'Fast-forward did not reach M')
        phase('building', deployment_git_sha=merged, source_A=A)
        env = dict(os.environ, REACT_APP_BACKEND_URL='https://mezansalla.com', PYTHONDONTWRITEBYTECODE='1')
        with (receipts / 'adapter.log').open('w') as log:
            subprocess.run(['yarn', 'build'], cwd=ROOT / 'frontend', env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        require(git('rev-parse', 'HEAD') == merged and not git('status', '--porcelain', '--untracked-files=no'), 'Source changed during build')
        after = untracked_metadata()
        save('untracked-after.json', after)
        require(after == before, 'Unrelated untracked path or metadata changed')
        require(not guard.LEASE_DIR.exists() and not guard.LEASE_DIR.is_symlink(), 'Lease appeared during handoff')
        prepared = guard._prepare_locked(ACTOR)
        save('prepare.json', prepared)
        expected = {'source_git_sha': A, 'source_base_git_sha': J, 'deployment_git_sha': merged,
                    'actor': ACTOR, 'release_id': RELEASE, 'protocol_version': 5}
        require(all(prepared.get(k) == v for k, v in expected.items()), 'Prepared identity differs; do not publish')
        phase('prepared', **expected, prepared_at=prepared['prepared_at'], untracked_count=len(after),
              untracked_metadata_preserved=True, bytecode_preserved=observed_cache is not None)
except Exception as exc:
    phase('failed', error_type=type(exc).__name__, detail=str(exc)[:2000])
    raise
