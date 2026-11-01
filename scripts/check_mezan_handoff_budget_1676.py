#!/usr/bin/env python3
"""Read-only capacity check for transferring already-built PR1676 files.

No install, rebuild, worktree deletion, branch promotion or release lease.
A passing result applies only to the small local handoff, not Cloud Build.
"""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

APP = Path('/app')
REVIEW = Path('/tmp/mezan-release-1674-fg57cbud/rehearsal')
BASE = '3bac875cfbcbc7f66849edae83c478dd194d9c12'
SOURCE = '667d94a3b01b7a59f58b753dfbf7d52c80764e02'
TARGET = '5a11cfcc3b600479ff69d19d76bc565019a70bfd'
RELEASE = 'rg5-d63e7b8c4834f9e1c133126799ec47cac167658abb58009220590b4ff3572bb7'
MIB = 1024**2
ENV = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_PAGER='cat', PAGER='cat',
           PYTHONDONTWRITEBYTECODE='1', GIT_TERMINAL_PROMPT='0')
for name in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'PYTHONHOME', 'PYTHONPATH'):
    ENV.pop(name, None)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(args, cwd):
    result = subprocess.run(args, cwd=cwd, env=ENV, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError('Read check failed: ' + args[0] + '\n' + result.stderr[-1800:])
    return result.stdout.strip()


def git(root, *args):
    return run(['git', '--no-pager', *args], root)


def allocated_size(path, block):
    info = path.lstat()
    require(not stat.S_ISLNK(info.st_mode), 'Artifact uses a symbolic link: ' + str(path))
    if stat.S_ISREG(info.st_mode):
        return ((info.st_size + block - 1) // block) * block, 1
    require(stat.S_ISDIR(info.st_mode), 'Unexpected artifact file type: ' + str(path))
    total, count = block, 1
    for child in path.iterdir():
        size, entries = allocated_size(child, block)
        total += size
        count += entries
    return total, count


def required_budget(source_bytes, artifact_bytes):
    # Allow another full copy plus 256 MiB headroom for this handoff only.
    return 2 * (source_bytes + artifact_bytes) + 256 * MIB


def main():
    for root in (APP, REVIEW):
        require(root.is_dir() and root.resolve() == root, 'Missing/redirected folder: ' + str(root))
    require(git(APP, 'rev-parse', 'HEAD') == BASE, '/app changed; do not reuse this plan.')
    require(git(APP, 'branch', '--show-current') == 'hotfix/prod-snap-meta-final', 'Unexpected /app branch.')
    require(not git(APP, 'diff', '--name-only', 'HEAD', '--'), '/app has tracked modifications; preserved.')
    state = json.loads(run([sys.executable, '-B', 'scripts/production_release_guard.py', 'status'], APP))
    require(state.get('active') is False, 'An active release lease must not be disturbed.')
    require(git(REVIEW, 'rev-parse', 'HEAD') == TARGET, 'Rehearsal commit changed.')
    require(not git(REVIEW, 'status', '--porcelain=v1', '--untracked-files=all'), 'Rehearsal source changed.')
    git(APP, 'cat-file', '-e', TARGET + '^{commit}')

    intent = json.loads((REVIEW / 'release/release-intent-v5.json').read_text())
    require(intent.get('source_git_sha') == SOURCE, 'Reviewed source mismatch.')
    require(intent.get('runtime_identity', {}).get('release_id') == RELEASE, 'Release ID mismatch.')
    identity = json.loads((REVIEW / 'backend/release_identity.json').read_text())
    require(identity == intent['runtime_identity'], 'Materialized identity differs from intent.')
    run([sys.executable, '-B', 'scripts/verify_frontend_build.py',
         '--expected-git-sha', SOURCE, '--reviewed-intent-v5'], REVIEW)
    print('REVIEWED_ARTIFACT_CHECK_PASS', flush=True)

    space = os.statvfs(APP)
    block = max(space.f_frsize, 4096)
    changed = set(git(REVIEW, 'diff', '--name-only', '-z', BASE, TARGET, '--').split('\0')) - {''}
    source_bytes, source_count = 0, 0
    for record in git(REVIEW, 'ls-tree', '-r', '-l', '-z', TARGET).split('\0'):
        if not record:
            continue
        metadata, path = record.split('\t', 1)
        if path not in changed:
            continue
        mode, kind, oid, size = metadata.split()
        require(kind == 'blob' and mode in ('100644', '100755'), 'Unsupported source entry: ' + path)
        source_bytes += ((int(size) + block - 1) // block) * block + block
        source_count += 1
    artifact_bytes, artifact_entries = 0, 0
    for path in ('frontend/build', 'frontend/.release/reproducible-build.json', 'backend/release_identity.json'):
        size, entries = allocated_size(REVIEW / path, block)
        artifact_bytes += size
        artifact_entries += entries
    available = space.f_bavail * space.f_frsize
    needed = required_budget(source_bytes, artifact_bytes)
    print(json.dumps({
        'changed_source_files': source_count,
        'changed_source_MiB': round(source_bytes / MIB, 2),
        'built_artifacts_MiB': round(artifact_bytes / MIB, 2),
        'available_APP_MiB': round(available / MIB, 2),
        'transfer_budget_including_256_MiB_reserve': round(needed / MIB, 2),
        'no_package_install_or_rebuild_in_APP': True,
        'production_changed': False
    }, indent=2), flush=True)
    require(available >= needed, 'Insufficient local handoff space; nothing transferred or deleted.')
    require(not space.f_files or space.f_favail >= 4 * (source_count + artifact_entries) + 4096,
            'Insufficient free file slots; nothing transferred.')
    print('=== APP_HANDOFF_BUDGET_PASS ===')
    print('Capacity check only. No merge, copy, deployment, or financial write.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('HANDOFF_CHECK_STOPPED:', error, flush=True)
        raise SystemExit(1)
