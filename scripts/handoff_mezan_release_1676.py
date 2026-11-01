#!/usr/bin/env python3
"""Handoff the owner-authorized, merged PR1676; prepare, never publish.

Copy ONLY the verified ignored build/proof/identity from the retained rehearsal.
Preserve previous generated outputs under .git; preserve all unrelated files.
Use fast-forward only and the unchanged Release Guard prepare/prepublish CLI.
No install, rebuild, Git push, reset, cleanup, service command or database call.
"""
from __future__ import annotations
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

APP = Path('/app')
ROOT = Path('/tmp/mezan-release-1674-fg57cbud')
REVIEW = ROOT / 'rehearsal'
BASE = '3bac875cfbcbc7f66849edae83c478dd194d9c12'
SOURCE = '667d94a3b01b7a59f58b753dfbf7d52c80764e02'
INTENT = '5a11cfcc3b600479ff69d19d76bc565019a70bfd'
MERGED = 'ffa145f22dbf4167649736336083d0d16f9fed27'
BRANCH = 'hotfix/prod-snap-meta-final'
RELEASE = 'rg5-d63e7b8c4834f9e1c133126799ec47cac167658abb58009220590b4ff3572bb7'
ARTIFACT_TREE = 'cd9e939b0429486b02426b4209b1a8554f8fb240b8214741120cf4e7b4eae0e1'
GENERATED = ('frontend/build', 'frontend/.release/reproducible-build.json',
             'backend/release_identity.json')
RESERVE = 256 * 1024**2
ACTOR = 'arafat-pr1676-verified-handoff'
ENV = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0',
           GIT_PAGER='cat', PAGER='cat', PYTHONDONTWRITEBYTECODE='1')
for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'PYTHONHOME', 'PYTHONPATH',
            'BASH_ENV', 'ENV'):
    ENV.pop(key, None)


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sanitized(value):
    value = re.sub(r'(https?://)[^\s/@]+@', r'\1[REDACTED]@', str(value))
    return re.sub(r'\b(?:github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9_]+)\b',
                  '[REDACTED]', value)


def run(args, cwd=APP, timeout=180):
    result = subprocess.run(args, cwd=cwd, env=ENV, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, timeout=timeout)
    if result.returncode:
        detail = (result.stderr or result.stdout)[-2400:]
        raise RuntimeError(sanitized('Command failed (exit ' + str(result.returncode) +
                                    '): ' + args[0] + '\n' + detail))
    return result.stdout.strip()


def git(root, *args):
    return run(['git', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
                '-c', 'core.hooksPath=/dev/null', '--no-pager', *args], root)


def real_dir(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
            and path.resolve(strict=True) == path, 'Missing or redirected directory: ' + str(path))
    return info


def safe_path(root, relative, allow_missing=False):
    """Reject symlink parents or mounts anywhere below an established root."""
    path = root
    parts = Path(relative).parts
    require(parts and not Path(relative).is_absolute() and '..' not in parts,
            'Unsafe relative path.')
    device = real_dir(root).st_dev
    for index, part in enumerate(parts):
        path /= part
        if not path.exists() and not path.is_symlink():
            require(allow_missing, 'Missing path: ' + str(path))
            continue
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_dev == device
                and not path.is_mount(), 'Redirected/mounted artifact path: ' + str(path))
        if index < len(parts) - 1:
            require(stat.S_ISDIR(info.st_mode), 'Artifact parent is not a directory.')
    return path


def snapshot(path):
    """Hash every regular file and capture directory membership; never follow links."""
    records = {}
    device = path.lstat().st_dev

    def visit(current, relative):
        info = current.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_dev == device
                and not current.is_mount(), 'Unsafe artifact entry: ' + str(current))
        if stat.S_ISDIR(info.st_mode):
            records[relative] = ('directory',)
            for child in sorted(current.iterdir()):
                visit(child, relative + '/' + child.name)
        else:
            require(stat.S_ISREG(info.st_mode), 'Artifact is not a regular file: ' + str(current))
            with current.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            after = current.lstat()
            require((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) ==
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
                    'Artifact changed while reading: ' + str(current))
            records[relative] = ('file', info.st_size, digest, stat.S_IMODE(info.st_mode))
    visit(path, '.')
    return records


def allocated(records, block):
    return sum(block if item[0] == 'directory' else
               ((item[1] + block - 1) // block) * block for item in records.values())


def save_state(path, payload):
    temporary = path.with_name(path.name + '.new')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextlib.contextmanager
def locked_file(path):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and info.st_uid == os.geteuid(), 'Unsafe coordination file.')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def verify_artifacts(root, intent):
    require(json.loads((root / GENERATED[2]).read_text()) == intent['runtime_identity'],
            'Runtime identity differs from reviewed intent.')
    result = json.loads(run([sys.executable, '-B', 'scripts/verify_frontend_build.py',
                            '--expected-git-sha', SOURCE, '--reviewed-intent-v5'], root))
    require(result['frontend_build'] == intent['frontend_build'] and
            result['frontend_reproducibility'] == intent['frontend_reproducibility'],
            'Generated files differ from frozen release evidence.')


def guard(action, *args):
    return json.loads(run([sys.executable, '-B', 'scripts/production_release_guard.py',
                           action, *args]))


def assert_workspace():
    require(git(APP, 'rev-parse', '--show-toplevel') == str(APP), 'Wrong /app repository.')
    require(git(APP, 'branch', '--show-current') == BRANCH, 'The /app branch changed.')
    head = git(APP, 'rev-parse', 'HEAD')
    require(head in (BASE, MERGED), 'Unexpected /app commit. Nothing overwritten.')
    require(not git(APP, 'status', '--porcelain=v1', '--untracked-files=no'),
            'Tracked local changes exist. Nothing overwritten.')
    return head


def assert_remote():
    output = git(APP, 'ls-remote', '--exit-code', 'origin', 'refs/heads/' + BRANCH)
    require(output.split() == [MERGED, 'refs/heads/' + BRANCH],
            'GitHub production branch advanced. This frozen handoff must not be reused.')


def validate_intent(intent):
    require(intent.get('source_git_sha') == SOURCE and intent.get('source_base_git_sha') == BASE
            and intent.get('protocol_version') == 5 and intent.get('branch') == BRANCH,
            'Intent source/base/protocol differs from the approved release.')
    require(intent.get('runtime_identity', {}).get('release_id') == RELEASE
            and intent.get('frontend_build', {}).get('artifact_tree_sha256') == ARTIFACT_TREE,
            'Frozen release identity differs from the approved rehearsal.')


def main():
    for folder in (APP, APP / '.git', ROOT, REVIEW):
        real_dir(folder)
    with locked_file(ROOT / 'handoff-1676.lock'):
        print('=== VERIFYING MERGED SOURCE AND EXISTING REHEARSAL ===', flush=True)
        initial_head = assert_workspace()
        initial_guard = guard('status')
        require(initial_guard.get('active') is False and
                not initial_guard.get('incomplete_lease_directory'),
                'A release is already reserved. Do not clear or repeat it.')
        origin = git(APP, 'remote', 'get-url', 'origin')
        parsed = urlparse(origin)
        require((parsed.scheme == 'https' and parsed.hostname == 'github.com'
                 and parsed.path.rstrip('/').removesuffix('.git') == '/AMASI-SA/AMASI-SA')
                or origin in ('git@github.com:AMASI-SA/AMASI-SA.git',
                              'git@github.com:AMASI-SA/AMASI-SA'), 'Unexpected origin repository.')
        assert_remote()
        require(git(REVIEW, 'rev-parse', 'HEAD') == INTENT, 'Rehearsal HEAD changed.')
        require(not git(REVIEW, 'status', '--porcelain=v1', '--untracked-files=all'),
                'Rehearsal source contains changes.')
        checkpoint = json.loads((ROOT / 'checkpoint.json').read_text())
        require(checkpoint.get('rehearsal_passed') is True and
                checkpoint.get('source_git_sha') == SOURCE and
                checkpoint.get('intent_git_sha') == INTENT and checkpoint.get('release_id') == RELEASE,
                'Successful rehearsal checkpoint is missing or different.')
        intent = json.loads((REVIEW / 'release/release-intent-v5.json').read_text())
        validate_intent(intent)
        expected = {}
        for relative in GENERATED:
            expected[relative] = snapshot(safe_path(REVIEW, relative))
            safe_path(APP, relative, allow_missing=True)
            require(not git(APP, 'ls-files', '--', relative), 'Generated output is tracked; stopped.')
            require(git(APP, 'check-ignore', '--', relative) == relative,
                    'Generated output is not ignored; stopped.')
        verify_artifacts(REVIEW, intent)
        print('REVIEWED_ARTIFACT_CHECK_PASS', flush=True)
        space = os.statvfs(APP)
        block = max(space.f_frsize, 4096)
        changed = set(git(REVIEW, 'diff', '--name-only', '-z', BASE, INTENT).split('\0')) - {''}
        source_bytes = 0
        for record in git(REVIEW, 'ls-tree', '-r', '-l', '-z', INTENT).split('\0'):
            if not record:
                continue
            metadata, relative = record.split('\t', 1)
            if relative in changed:
                mode, kind, oid, size = metadata.split()
                require(kind == 'blob' and mode in ('100644', '100755'), 'Unsupported source entry.')
                source_bytes += ((int(size) + block - 1) // block) * block + block
        artifact_bytes = sum(allocated(records, block) for records in expected.values())
        budget = 2 * (source_bytes + artifact_bytes) + RESERVE
        available = space.f_bavail * space.f_frsize
        require(available >= budget, 'Local space fell below the handoff budget. No copy started.')
        require(not space.f_files or space.f_favail >= 4096 + 4 *
                (len(changed) + sum(map(len, expected.values()))), 'Too few free file slots.')
        print('HANDOFF_SPACE_PASS', round(available / 1024**2, 2), 'MiB available;',
              round(budget / 1024**2, 2), 'MiB budget', flush=True)
        git(APP, 'fetch', '--no-tags', 'origin', 'refs/heads/' + BRANCH +
            ':refs/remotes/origin/' + BRANCH)
        require(git(APP, 'rev-parse', 'origin/' + BRANCH) == MERGED, 'Fetched production SHA changed.')
        require(git(APP, 'rev-parse', MERGED + '^{tree}') ==
                git(APP, 'rev-parse', INTENT + '^{tree}'), 'Merged tree differs from reviewed B.')
        git(APP, 'merge-base', '--is-ancestor', INTENT, MERGED)
        require(git(APP, 'diff', '--name-only', SOURCE, MERGED) == 'release/release-intent-v5.json',
                'Source A to merged deployment is not intent-only.')
        before_untracked = git(APP, 'ls-files', '--others', '--exclude-standard', '-z')
        journal_dir = Path(tempfile.mkdtemp(prefix='handoff-1676-', dir=str(ROOT)))
        journal_file = journal_dir / 'state.json'
        state = dict(source_git_sha=SOURCE, deployment_git_sha=MERGED, release_id=RELEASE,
                     phase='validated', publish_performed=False, financial_write=False)
        save_state(journal_file, state)
        print('HANDOFF_JOURNAL', journal_file, flush=True)
        # Serialize source/artifact handoff with the existing guard lifecycle lock.
        # Do not call guard status/prepare while holding its lock in this process.
        operation_lock = APP / '.git/.mezan-production-release.lock.operation.lock'
        with locked_file(operation_lock):
            lease_dir = APP / '.git/mezan-production-release.lock'
            require(not lease_dir.exists() and not lease_dir.is_symlink(),
                    'A release lease appeared; no transfer allowed.')
            require(assert_workspace() == initial_head, 'Workspace changed concurrently.')
            assert_remote()
            current_space = os.statvfs(APP)
            require(current_space.f_bavail * current_space.f_frsize >= budget,
                    'Available space changed before staging.')
            staging = Path(tempfile.mkdtemp(prefix='mezan-handoff-1676-', dir=str(APP / '.git')))
            state.update(backup_directory=str(staging / 'previous'), phase='staging')
            save_state(journal_file, state)
            old = {}
            for relative in GENERATED:
                target = safe_path(APP, relative, allow_missing=True)
                old[relative] = snapshot(target) if target.exists() else None
                incoming = staging / 'incoming' / relative
                incoming.parent.mkdir(parents=True, exist_ok=True)
                source = safe_path(REVIEW, relative)
                if source.is_dir():
                    shutil.copytree(source, incoming, symlinks=True)
                else:
                    shutil.copy2(source, incoming, follow_symlinks=False)
                require(snapshot(incoming) == expected[relative], 'Staged artifact hash mismatch.')
            state['phase'] = 'artifacts_staged'
            save_state(journal_file, state)
            require(assert_workspace() == initial_head, 'Workspace changed before fast-forward.')
            if initial_head == BASE:
                git(APP, 'merge', '--ff-only', '--no-edit', MERGED)
            require(assert_workspace() == MERGED, 'Workspace failed to reach the merged commit.')
            state['phase'] = 'source_fast_forwarded'
            save_state(journal_file, state)
            print('APP_SOURCE_FAST_FORWARD_PASS', MERGED, flush=True)
            for relative in GENERATED:
                target = safe_path(APP, relative, allow_missing=True)
                observed = snapshot(target) if target.exists() else None
                require(observed == old[relative], 'Previous output changed concurrently; stopped.')
                target.parent.mkdir(parents=True, exist_ok=True)
                safe_path(APP, relative, allow_missing=True)
                backup = staging / 'previous' / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    os.replace(target, backup)
                try:
                    os.replace(staging / 'incoming' / relative, target)
                except Exception:
                    if backup.exists() and not target.exists():
                        os.replace(backup, target)
                    raise
                require(snapshot(target) == expected[relative], 'Transferred artifact hash mismatch.')
                state['last_transferred'] = relative
                save_state(journal_file, state)
            state['phase'] = 'artifacts_transferred'
            save_state(journal_file, state)
            verify_artifacts(APP, intent)
            require(assert_workspace() == MERGED, 'Final workspace check failed.')
            require(git(APP, 'ls-files', '--others', '--exclude-standard', '-z') == before_untracked,
                    'Unrelated untracked files changed concurrently. Review before publishing.')
            space = os.statvfs(APP)
            require(space.f_bavail * space.f_frsize >= RESERVE, 'Reserve depleted; do not publish.')
            assert_remote()
            state['phase'] = 'local_identity_verified'
            save_state(journal_file, state)
        print('APP_VERIFIED_ARTIFACT_HANDOFF_PASS', flush=True)
        print('=== PREPARING THE UNCHANGED RELEASE GUARD — NOT PUBLISHING ===', flush=True)
        prepared = guard('prepare', '--actor', ACTOR)
        require(prepared.get('source_git_sha') == SOURCE and prepared.get('deployment_git_sha') == MERGED
                and prepared.get('release_id') == RELEASE, 'Prepared lease identity mismatch.')
        state.update(phase='lease_prepared', prepared_at=prepared.get('prepared_at'))
        save_state(journal_file, state)
        ready = guard('prepublish')
        require(ready.get('ready_to_publish') is True and ready.get('source_git_sha') == SOURCE
                and ready.get('deployment_git_sha') == MERGED and ready.get('release_id') == RELEASE,
                'Prepublish did not approve the exact reviewed release.')
        state.update(phase='ready_for_owner_publish', ready_to_publish=True)
        save_state(journal_file, state)
        print('=== APP_READY_FOR_PUBLISH ===', flush=True)
        print(json.dumps({**ready, 'backup_directory': state['backup_directory'],
                          'handoff_journal': str(journal_file), 'publish_performed': False,
                          'no_rebuild_or_package_install': True}, ensure_ascii=False, indent=2), flush=True)
        print('Workspace updated; release lease active. No Publish click, service restart or financial write.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('HANDOFF_STOPPED:', sanitized(error), flush=True)
        print('Do not publish or reset. Files may already be staged/transferred; keep the journal and backups.', flush=True)
        print('No automatic Publish, service restart or financial write was performed.', flush=True)
        raise SystemExit(1)
