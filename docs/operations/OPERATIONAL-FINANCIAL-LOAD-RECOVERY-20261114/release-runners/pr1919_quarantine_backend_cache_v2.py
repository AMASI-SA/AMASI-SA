#!/usr/bin/env python3
"""Quarantine only the two reviewed ignored PR1919 backend bytecode files.

Root must review and run this in the original operator terminal. Logs remain
in private /tmp. A new private child of the existing /app/.release-preserved
directory holds only the exact reviewed cache, moved by same-device rename.
No deletion, restoration, source edit, build, prepare, prepublish, verify or
publication is performed.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import signal
import stat
import subprocess
import sys
import tempfile
import time


APP = Path('/app')
BASE = '23cc06da003c18c0f3fc6c2e749aad1326666166'
BRANCH = 'hotfix/prod-snap-meta-final'
REHEARSAL = Path('/tmp/mezan-pr1919-original-9l31h7ck')
CACHE_RELATIVE = 'backend/__pycache__'
EXPECTED_NAMES = {
    'frontend_build_identity.cpython-311.pyc',
    'release_protocol_v5.cpython-311.pyc',
}
EXPECTED_CACHE_BYTES = 120563
EXPECTED_UNTRACKED_COUNT = 120
PRESERVATION_PARENT = APP / '.release-preserved'
EXPECTED_PRESERVATION_DEVICE = 66330
PAYLOAD_ROOT = None
RENAMED = False


class Stop(Exception):
    pass


def utc():
    return datetime.now(timezone.utc).isoformat()


def save(name, value):
    with (QUARANTINE / name).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=True, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())


def run(label, argv, *, stdin_data=None, timeout=30):
    """Keep actual read-only child outcomes before checking their result."""
    out, err = QUARANTINE / (label + '.stdout'), QUARANTINE / (label + '.stderr')
    started, clock = utc(), time.monotonic()
    proc, code, problem = None, None, None
    with out.open('xb') as stdout, err.open('xb') as stderr:
        try:
            proc = subprocess.Popen(argv, cwd=APP, env=CHILD_ENV,
                stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, start_new_session=True)
            proc.communicate(input=stdin_data, timeout=timeout)
            code = proc.returncode
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            problem = type(exc).__name__
            if proc is not None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                code = proc.wait()
        except OSError as exc:
            problem = type(exc).__name__
        stdout.flush(); stderr.flush()
        os.fsync(stdout.fileno()); os.fsync(stderr.fileno())
    save(label + '.result.json', {
        'command': argv, 'cwd': str(APP), 'started_at': started, 'ended_at': utc(),
        'elapsed_seconds': time.monotonic() - clock, 'actual_exit': code,
        'problem': problem, 'stdout': str(out), 'stderr': str(err)})
    if problem is not None or code != 0:
        raise Stop(label + '_child_failed_see_saved_actual_result')
    return out.read_bytes()


def git(label, *args, stdin_data=None):
    return run(label, ['git', *args], stdin_data=stdin_data)


def require_inactive(label):
    state = json.loads(run(label, [sys.executable, '-B',
        'scripts/production_release_guard.py', 'status'], timeout=15))
    if not isinstance(state, dict) or set(state) != {'active'} or state['active'] is not False:
        raise Stop(label + '_guard_is_not_exactly_inactive')


def require_checkout(label):
    if git(label + '-head', 'rev-parse', 'HEAD').decode().strip() != BASE:
        raise Stop(label + '_head_is_not_reviewed_J')
    if git(label + '-branch', 'branch', '--show-current').decode().strip() != BRANCH:
        raise Stop(label + '_branch_mismatch')
    if git(label + '-clean', 'status', '--porcelain=v1', '-z',
           '--untracked-files=no', '--ignore-submodules=none'):
        raise Stop(label + '_tracked_or_index_dirty')


def stable_fields(info):
    return {name: getattr(info, name) for name in (
        'st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid',
        'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}


def safe_path(relative):
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or '..' in parsed.parts or not parsed.parts:
        raise Stop('unsafe_relative_path')
    for index in range(1, len(parsed.parts)):
        if APP.joinpath(*parsed.parts[:index]).is_symlink():
            raise Stop('source_parent_is_symlink')
    return APP / relative


def regular_file(path):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise Stop('cache_member_is_not_a_regular_file')
    digest, size = hashlib.sha256(), 0
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as handle:
        if stable_fields(os.fstat(handle.fileno())) != stable_fields(before):
            raise Stop('file_identity_changed_before_read')
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
            size += len(chunk)
        if stable_fields(os.fstat(handle.fileno())) != stable_fields(before):
            raise Stop('file_changed_during_read')
    if stable_fields(path.lstat()) != stable_fields(before) or size != before.st_size:
        raise Stop('file_changed_during_snapshot')
    return {'kind': 'file', 'mode': stat.S_IMODE(before.st_mode),
            'size': size, 'sha256': digest.hexdigest()}, stable_fields(before)


def untracked_snapshot(label, baseline):
    paths = git(label + '-paths', 'ls-files', '--others', '--exclude-standard', '-z')
    manifest = {}
    for raw in sorted(path for path in paths.split(b'\0') if path):
        relative = os.fsdecode(raw)
        path = safe_path(relative)
        before = path.lstat()
        if stat.S_ISREG(before.st_mode):
            entry, _ = regular_file(path)
        elif stat.S_ISLNK(before.st_mode):
            data = os.fsencode(os.readlink(path))
            if stable_fields(path.lstat()) != stable_fields(before):
                raise Stop('untracked_symlink_changed_during_read')
            entry = {'kind': 'symlink', 'mode': stat.S_IMODE(before.st_mode),
                     'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        else:
            raise Stop('unsupported_untracked_member_type')
        manifest[relative] = entry
    save(label + '.manifest.json', manifest)
    changed = sorted(path for path in baseline.keys() | manifest.keys()
                     if baseline.get(path) != manifest.get(path))
    preserved = not changed and len(manifest) == EXPECTED_UNTRACKED_COUNT
    save(label + '.preservation.json', {
        'before_count': len(baseline), 'after_count': len(manifest),
        'changed_paths': changed, 'preserved': preserved})
    if not preserved:
        raise Stop(label + '_untracked_baseline_changed_no_restoration_attempted')


def cache_manifest(path):
    before = path.lstat()
    if not stat.S_ISDIR(before.st_mode) or path.is_symlink():
        raise Stop('reviewed_cache_is_not_a_real_directory')
    if {entry.name for entry in path.iterdir()} != EXPECTED_NAMES:
        raise Stop('reviewed_cache_member_scope_changed')
    files = {}
    for name in sorted(EXPECTED_NAMES):
        entry, identity = regular_file(path / name)
        if identity['st_dev'] != before.st_dev or identity['st_nlink'] != 1:
            raise Stop('cache_member_crosses_device_or_is_hardlinked')
        files[name] = {**entry, 'identity': identity}
    if (stable_fields(path.lstat()) != stable_fields(before)
            or {entry.name for entry in path.iterdir()} != EXPECTED_NAMES):
        raise Stop('cache_directory_changed_during_snapshot')
    if sum(entry['size'] for entry in files.values()) != EXPECTED_CACHE_BYTES:
        raise Stop('reviewed_cache_total_bytes_changed')
    return {'directory_identity': stable_fields(before), 'files': files}


def main():
    global RENAMED, PAYLOAD_ROOT
    if APP.resolve(strict=True) != APP or (APP / 'backend').resolve(strict=True) != APP / 'backend':
        raise Stop('app_or_backend_ancestor_is_not_canonical')
    require_inactive('01-guard-before')
    require_checkout('02-checkout-before')
    baseline = json.loads((REHEARSAL / '04-before.manifest.json').read_bytes())
    if not isinstance(baseline, dict) or len(baseline) != EXPECTED_UNTRACKED_COUNT:
        raise Stop('failed_rehearsal_baseline_count_mismatch')
    untracked_snapshot('03-untracked-before', baseline)
    if git('04-tracked-cache', 'ls-files', '-z', '--', CACHE_RELATIVE):
        raise Stop('reviewed_cache_contains_tracked_files')
    if git('04-nonignored-cache', 'ls-files', '--others', '--exclude-standard', '-z', '--', CACHE_RELATIVE):
        raise Stop('reviewed_cache_contains_nonignored_files')
    ignored_paths = [CACHE_RELATIVE + '/', *[CACHE_RELATIVE + '/' + name for name in sorted(EXPECTED_NAMES)]]
    ignored = git('04-cache-ignore', 'check-ignore', '--stdin', '-z',
        stdin_data=b'\0'.join(os.fsencode(p) for p in ignored_paths) + b'\0')
    if sorted(os.fsdecode(p) for p in ignored.split(b'\0') if p) != sorted(ignored_paths):
        raise Stop('reviewed_cache_scope_is_not_entirely_ignored')

    source = safe_path(CACHE_RELATIVE)
    before = cache_manifest(source)
    save('05-cache-before.manifest.json', before)
    # The existing parent is not generically ignored. Store no logs or
    # manifests here: only the __pycache__ directory and its ignored files.
    parent_info = PRESERVATION_PARENT.lstat()
    if (not stat.S_ISDIR(parent_info.st_mode) or PRESERVATION_PARENT.is_symlink()
            or PRESERVATION_PARENT.resolve(strict=True) != PRESERVATION_PARENT
            or parent_info.st_uid != 0 or stat.S_IMODE(parent_info.st_mode) != 0o755
            or parent_info.st_dev != EXPECTED_PRESERVATION_DEVICE
            or parent_info.st_dev != before['directory_identity']['st_dev']):
        raise Stop('reviewed_preservation_parent_changed_no_cache_changed')
    if git('05-preservation-parent-tracked', 'ls-files', '-z', '--', '.release-preserved'):
        raise Stop('preservation_parent_contains_tracked_files')
    save('05-preservation-parent.json', {
        'path': str(PRESERVATION_PARENT), 'identity': stable_fields(parent_info),
        'expected_device': EXPECTED_PRESERVATION_DEVICE, 'mode': '0755', 'owner_uid': 0})
    require_inactive('05-guard-before-private-child')
    if stable_fields(PRESERVATION_PARENT.lstat()) != stable_fields(parent_info):
        raise Stop('preservation_parent_changed_before_private_child')
    PAYLOAD_ROOT = Path(tempfile.mkdtemp(
        prefix='mezan-pr1919-cache-', dir=PRESERVATION_PARENT))
    payload_info = PAYLOAD_ROOT.lstat()
    if (not stat.S_ISDIR(payload_info.st_mode) or PAYLOAD_ROOT.is_symlink()
            or payload_info.st_uid != os.geteuid() or stat.S_IMODE(payload_info.st_mode) != 0o700
            or payload_info.st_dev != EXPECTED_PRESERVATION_DEVICE):
        raise Stop('private_same_device_payload_directory_required')
    destination_parent = PAYLOAD_ROOT / 'backend'
    destination_parent.mkdir(mode=0o700)
    destination = destination_parent / '__pycache__'
    destination_relative = destination.relative_to(APP).as_posix()
    destination_paths = [destination_relative + '/',
        *[destination_relative + '/' + name for name in sorted(EXPECTED_NAMES)]]
    destination_ignored = git('05-destination-ignore', 'check-ignore', '--stdin', '-z',
        stdin_data=b'\0'.join(os.fsencode(p) for p in destination_paths) + b'\0')
    if sorted(os.fsdecode(p) for p in destination_ignored.split(b'\0') if p) != sorted(destination_paths):
        raise Stop('exact_quarantine_payload_is_not_ignored_no_cache_changed')
    save('05-private-payload.json', {
        'path': str(PAYLOAD_ROOT), 'identity': stable_fields(payload_info),
        'destination': str(destination), 'all_payload_paths_ignored': True})
    if (source.lstat().st_dev != destination_parent.stat().st_dev
            or source.parent.lstat().st_dev != destination_parent.stat().st_dev):
        raise Stop('atomic_same_device_rename_unavailable_no_cache_changed')
    if os.path.lexists(destination):
        raise Stop('quarantine_destination_already_exists')
    require_inactive('06-guard-before-rename')
    require_checkout('06-checkout-before-rename')
    untracked_snapshot('06-untracked-before-rename', baseline)
    current = cache_manifest(source)
    if current != before:
        raise Stop('cache_changed_since_reviewed_manifest_no_cache_changed')
    save('07-rename-intent.json', {
        'source': str(source), 'destination': str(destination),
        'same_device': True, 'expected_members': sorted(EXPECTED_NAMES),
        'expected_bytes': EXPECTED_CACHE_BYTES, 'started_at': utc()})

    # The only /app content mutation. Opening both real parent directories
    # avoids following a substituted path at the point of rename.
    with ExitStack() as handles:
        source_fd = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        handles.callback(os.close, source_fd)
        destination_fd = os.open(destination_parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        handles.callback(os.close, destination_fd)
        if (os.fstat(source_fd).st_dev != before['directory_identity']['st_dev']
                or os.fstat(destination_fd).st_dev != before['directory_identity']['st_dev']):
            raise Stop('parent_device_changed_before_rename')
        actual = os.stat(source.name, dir_fd=source_fd, follow_symlinks=False)
        if stable_fields(actual) != before['directory_identity']:
            raise Stop('source_directory_identity_changed_before_rename')
        os.rename(source.name, destination.name,
                  src_dir_fd=source_fd, dst_dir_fd=destination_fd)
        RENAMED = True
        os.fsync(source_fd)
        os.fsync(destination_fd)
    save('07-rename-completed.json', {
        'source': str(source), 'destination': str(destination),
        'same_device': True, 'completed_at': utc()})

    after = cache_manifest(destination)
    save('08-cache-after.manifest.json', after)
    # Directory ctime may change on rename; every child inode, mode, size,
    # timestamps and SHA256 must remain identical. Directory identity/mode
    # and all other recorded directory fields must remain unchanged.
    before_dir = {k: v for k, v in before['directory_identity'].items() if k != 'st_ctime_ns'}
    after_dir = {k: v for k, v in after['directory_identity'].items() if k != 'st_ctime_ns'}
    if before['files'] != after['files'] or before_dir != after_dir:
        raise Stop('post_rename_cache_manifest_differs_quarantine_retained')
    if os.path.lexists(source):
        raise Stop('source_cache_reappeared_quarantine_retained')
    untracked_snapshot('09-untracked-after', baseline)
    require_checkout('10-checkout-after')
    require_inactive('11-guard-after')
    if os.path.lexists(source):
        raise Stop('source_cache_reappeared_before_completion_quarantine_retained')
    outcome = {'completed': True, 'cache_quarantined': True,
        'source_head_unchanged': BASE, 'tracked_clean': True, 'guard_inactive': True,
        'cache_files': len(EXPECTED_NAMES), 'cache_bytes': EXPECTED_CACHE_BYTES,
        'cache_bytes_modes_and_inodes_preserved': True,
        'untracked_count': EXPECTED_UNTRACKED_COUNT, 'untracked_preserved': True,
        'rehearsal_evidence': str(REHEARSAL), 'quarantine_dir': str(QUARANTINE),
        'quarantine_payload': str(destination), 'private_payload_root': str(PAYLOAD_ROOT),
        'finished_at': utc()}
    save('outcome.json', outcome)
    print(json.dumps(outcome, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if (REHEARSAL.parent != Path('/tmp') or REHEARSAL.is_symlink()
            or REHEARSAL.resolve(strict=True) != REHEARSAL):
        raise SystemExit('Reviewed failed rehearsal evidence is unavailable or unsafe')
    info = REHEARSAL.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise SystemExit('Reviewed failed rehearsal evidence must be private and operator-owned')
    failed = json.loads((REHEARSAL / 'outcome.json').read_bytes())
    if (not isinstance(failed, dict) or failed.get('ready') is not False
            or failed.get('reason') != 'ignored_backend_pycache_requires_separate_review_no_files_changed'
            or failed.get('evidence_dir') != str(REHEARSAL)):
        raise SystemExit('Exact reviewed pre-fetch cache failure evidence is required')
    os.umask(0o077)
    QUARANTINE = Path(tempfile.mkdtemp(prefix='mezan-pr1919-cache-quarantine-', dir='/tmp'))
    CHILD_ENV = os.environ.copy()
    CHILD_ENV['PYTHONDONTWRITEBYTECODE'] = '1'
    print(json.dumps({'phase': 'started', 'quarantine_dir': str(QUARANTINE)}), flush=True)
    try:
        main()
    except BaseException as exc:
        failure = {'completed': False, 'cache_quarantined': RENAMED,
            'reason': str(exc) if isinstance(exc, Stop) else type(exc).__name__,
            'quarantine_dir': str(QUARANTINE), 'quarantine_retained': True,
            'private_payload_root': str(PAYLOAD_ROOT) if PAYLOAD_ROOT is not None else None,
            'restoration_attempted': False, 'finished_at': utc()}
        save('outcome.json', failure)
        print(json.dumps(failure, ensure_ascii=True), flush=True)
        raise SystemExit(1)
