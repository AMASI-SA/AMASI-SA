#!/usr/bin/env python3
"""PR1925 original /app rehearsal. Default: preflight; --build: fetch/FF/build.

No lease, publication, cleanup, quarantine, or restoration is performed.
Evidence stays in a private /tmp directory. Do not paste log/file contents.
Use the task's separate temporary terminal; leave original terminals untouched.
The operator must separately inspect unsaved Untitled/editor buffers without
saving or discarding them. Filesystem hashes cannot observe those buffers.
Fill all reviewed identity placeholders and the fresh inventory count before
running. This helper always captures a new full manifest before any /app change.
"""
import argparse
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
from datetime import datetime, timezone

APP = Path('/app')
BRANCH = 'hotfix/prod-snap-meta-final'
SOURCE = '616db2a06344027020fafc00c938f71c1e2b1874'
BASE = '77e4acf33e8f5432d97bda5d7208983f0fadc599'
RELEASE = '<REVIEWED_RUNTIME_RELEASE_ID_REQUIRED>'

REVIEWED_B = '<REVIEWED_INTENT_B_REQUIRED>'
REVIEWED_M = '<REVIEWED_NORMAL_MERGE_M_REQUIRED>'
REVIEWED_B_TREE = '<REVIEWED_B_AND_M_TREE_REQUIRED>'
INTENT_SHA256 = '<REVIEWED_INTENT_BYTES_SHA256_REQUIRED>'
EXPECTED_UNTRACKED_COUNT = None  # Required fresh /app inventory; never reuse a prior count.

REVIEWED_INTENT_COMMIT = REVIEWED_B
REVIEWED_TREE = REVIEWED_B_TREE
INTENT = 'release/release-intent-v5.json'
APPEND_ONLY = '.emergent/stripped_env_names'

def validate_reviewed_configuration():
    """Unfilled templates stop before creating evidence or touching /app."""
    identities = {"SOURCE": SOURCE, "BASE": BASE, "INTENT_COMMIT": REVIEWED_B,
                  "MERGE": REVIEWED_M, "TREE": REVIEWED_B_TREE}
    for label, value in identities.items():
        if not isinstance(value, str) or len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
            raise SystemExit("Fill the exact reviewed " + label + " before running this template")
    if not isinstance(RELEASE, str) or not RELEASE.startswith("rg5-") or len(RELEASE) != 68 or any(c not in "0123456789abcdef" for c in RELEASE[4:]):
        raise SystemExit("Fill the exact reviewed deterministic runtime release ID")
    if not isinstance(INTENT_SHA256, str) or len(INTENT_SHA256) != 64 or any(c not in "0123456789abcdef" for c in INTENT_SHA256):
        raise SystemExit("Fill the reviewed intent byte SHA256")
    if type(EXPECTED_UNTRACKED_COUNT) is not int or EXPECTED_UNTRACKED_COUNT < 0:
        raise SystemExit("Review the current untracked inventory and fill its exact count")


class Stop(Exception):
    pass


def utc():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=True, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def run(label, argv, *, cwd=APP, timeout=30, allowed=(0,), stdin_data=None):
    """Persist actual child result before evaluating its exit or any postcondition."""
    started, clock = utc(), time.monotonic()
    output, error = EVIDENCE / (label + '.stdout'), EVIDENCE / (label + '.stderr')
    proc, code, problem = None, None, None
    with output.open('wb') as stdout, error.open('wb') as stderr:
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=CHILD_ENV,
                                    stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                                    stdout=stdout, stderr=stderr, start_new_session=True)
            proc.communicate(input=stdin_data, timeout=timeout)
            code = proc.returncode
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            problem = type(exc).__name__
            if proc is not None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    code = proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    code = proc.wait()
        except OSError as exc:
            problem = type(exc).__name__
        ended, elapsed = utc(), time.monotonic() - clock
        stdout.flush(); stderr.flush()
        os.fsync(stdout.fileno()); os.fsync(stderr.fileno())
    result = {'command': argv, 'cwd': str(cwd), 'started_at': started,
              'ended_at': ended, 'elapsed_seconds': elapsed, 'actual_exit': code,
              'problem': problem, 'stdout': str(output), 'stderr': str(error)}
    write_json(EVIDENCE / (label + '.result.json'), result)
    if problem or code not in allowed:
        raise Stop(label + '_failed_see_private_result')
    return output.read_bytes()


def git(label, *args, allowed=(0,), timeout=30, stdin_data=None):
    return run(label, ['git', *args], allowed=allowed, timeout=timeout, stdin_data=stdin_data)


def inactive(label):
    raw = run(label, [sys.executable, '-B', 'scripts/production_release_guard.py', 'status'], timeout=15)
    if (EVIDENCE / (label + '.stderr')).stat().st_size:
        raise Stop(label + '_unexpected_stderr')
    state = json.loads(raw)
    if not isinstance(state, dict) or state.get('active') is not False:
        raise Stop(label + '_not_inactive')
    if state.get('incomplete_lease_directory'):
        raise Stop(label + '_incomplete_lease_directory')


def clean(label):
    if git(label, 'status', '--porcelain=v1', '-z', '--untracked-files=no', '--ignore-submodules=none'):
        raise Stop(label + '_tracked_or_index_dirty')


def snapshot(label):
    paths = git(label + '-paths', 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0')
    manifest, prefix = {}, None
    for raw in sorted(path for path in paths if path):
        relative = os.fsdecode(raw)
        parts = PurePosixPath(relative).parts
        if PurePosixPath(relative).is_absolute() or '..' in parts:
            raise Stop('unsafe_untracked_path')
        path = APP / relative
        if any((APP.joinpath(*parts[:index])).is_symlink() for index in range(1, len(parts))):
            raise Stop('untracked_parent_is_symlink')
        before = path.lstat()
        digest = hashlib.sha256()
        size, saved = 0, []
        if stat.S_ISREG(before.st_mode):
            kind = 'file'
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, 'rb') as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(chunk); size += len(chunk)
                    if relative == APPEND_ONLY:
                        saved.append(chunk)
        elif stat.S_ISLNK(before.st_mode):
            kind = 'symlink'
            data = os.fsencode(os.readlink(path))
            digest.update(data); size = len(data)
        else:
            raise Stop('unsupported_untracked_file_type')
        after = path.lstat()
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise Stop('untracked_changed_during_snapshot')
        manifest[relative] = {'kind': kind, 'mode': stat.S_IMODE(before.st_mode),
                              'size': size, 'sha256': digest.hexdigest()}
        if relative == APPEND_ONLY:
            if kind != 'file':
                raise Stop('append_sidecar_is_not_regular_file')
            prefix = b''.join(saved)
    write_json(EVIDENCE / (label + '.manifest.json'), manifest)
    if prefix is not None:
        (EVIDENCE / (label + '.stripped-env.bin')).write_bytes(prefix)
    return manifest, prefix


def preserved(label, baseline, old_prefix, *, allow_append):
    current, prefix = snapshot(label)
    changed = sorted(path for path in baseline.keys() | current.keys()
                     if baseline.get(path) != current.get(path))
    if allow_append and APPEND_ONLY in changed and old_prefix is not None and prefix is not None:
        old, new = baseline[APPEND_ONLY], current[APPEND_ONLY]
        if old['kind'] == new['kind'] == 'file' and old['mode'] == new['mode'] and prefix.startswith(old_prefix):
            changed.remove(APPEND_ONLY)
    write_json(EVIDENCE / (label + '.preservation.json'),
               {'before_count': len(baseline), 'after_count': len(current), 'changed_paths': changed,
                'append_allowed': allow_append, 'preserved': not changed})
    if changed:
        raise Stop(label + '_untracked_changed_no_restoration_attempted')


def no_ignored_backend_cache(label):
    candidates = []
    for directory, subdirs, files in os.walk(APP / 'backend', followlinks=False):
        for name in list(subdirs):
            if name == '__pycache__':
                candidates.append(str((Path(directory) / name).relative_to(APP)) + '/')
                subdirs.remove(name)
        candidates.extend(str((Path(directory) / name).relative_to(APP))
                          for name in files if name.endswith(('.pyc', '.pyo')))
    ignored = []
    if candidates:
        raw = git(label + '-ignored', 'check-ignore', '--stdin', '-z', allowed=(0, 1),
                  stdin_data=b'\0'.join(os.fsencode(path) for path in candidates) + b'\0')
        ignored = [os.fsdecode(path) for path in raw.split(b'\0') if path]
    write_json(EVIDENCE / (label + '.json'), {'ignored_backend_cache_paths': ignored})
    if ignored:
        raise Stop('ignored_backend_pycache_requires_separate_review_no_files_changed')


def main():
    inactive('01-guard-initial')
    clean('02-clean-initial')
    if git('03-branch', 'branch', '--show-current').decode().strip() != BRANCH:
        raise Stop('original_operator_branch_mismatch')
    if git('03-head', 'rev-parse', 'HEAD').decode().strip() not in {BASE, MERGE}:
        raise Stop('original_operator_head_is_not_reviewed_base_or_merge')
    markers = [name for name in ('GITHUB_ACTIONS', 'GITHUB_SHA', 'GITHUB_WORKSPACE') if name in CHILD_ENV]
    if markers:
        write_json(EVIDENCE / 'github-markers-present.json', {'names_only': markers})
        raise Stop('github_markers_present_explanation_required_not_removed')
    baseline, prefix = snapshot('04-before')
    if len(baseline) != EXPECTED_UNTRACKED_COUNT:
        raise Stop('untracked_count_changed_review_before_adopting_new_baseline')
    no_ignored_backend_cache('05-cache-preflight')
    if not ARGS.build:
        return {'phase': 'preflight', 'ready': True, 'untracked_count': len(baseline)}
    git('06-fetch', 'fetch', '--no-tags', '--no-recurse-submodules', 'origin',
        f'refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}', timeout=120)
    if git('07-remote', 'rev-parse', f'origin/{BRANCH}').decode().strip() != MERGE:
        raise Stop('production_moved_from_reviewed_merge')
    if git('08-tree', 'rev-parse', MERGE + '^{tree}').decode().strip() != TREE:
        raise Stop('merge_tree_differs_from_reviewed_B_tree')
    if git('08-B-tree', 'rev-parse', INTENT_COMMIT + '^{tree}').decode().strip() != TREE:
        raise Stop('reviewed_B_tree_mismatch')
    if git('08-B-parent', 'rev-list', '--parents', '-n', '1', INTENT_COMMIT).decode().split() != [INTENT_COMMIT, SOURCE]:
        raise Stop('reviewed_B_is_not_direct_child_of_A')
    if git('08-M-parents', 'rev-list', '--parents', '-n', '1', MERGE).decode().split() != [MERGE, BASE, INTENT_COMMIT]:
        raise Stop('normal_merge_parents_are_not_reviewed_J_and_B')
    git('09-source-ancestor', 'merge-base', '--is-ancestor', SOURCE, MERGE)
    if git('10-intent-only', 'diff', '--name-only', '-z', SOURCE, MERGE) != os.fsencode(INTENT) + b'\0':
        raise Stop('A_to_M_is_not_exact_intent_only')
    intent_bytes = git('11-intent', 'show', MERGE + ':' + INTENT)
    if hashlib.sha256(intent_bytes).hexdigest() != INTENT_SHA256:
        raise Stop('intent_bytes_do_not_match_reviewed_digest')
    intent = json.loads(intent_bytes)
    if not isinstance(intent, dict) or (intent.get('source_git_sha'), intent.get('source_base_git_sha')) != (SOURCE, BASE):
        raise Stop('intent_source_mismatch')
    identity = intent.get('runtime_identity')
    if not isinstance(identity, dict) or any(identity.get(key) != value for key, value in {
            'source_git_sha': SOURCE, 'source_base_git_sha': BASE, 'release_id': RELEASE, 'protocol_version': 5,
            'branch': BRANCH}.items()):
        raise Stop('intent_runtime_identity_mismatch')
    target_paths = [os.fsdecode(path) for path in git('12-target-paths', 'ls-tree', '-r', '--name-only', '-z', MERGE).split(b'\0') if path]
    if any(a == b or a.startswith(b + '/') or b.startswith(a + '/') for a in baseline for b in target_paths):
        raise Stop('untracked_path_conflicts_with_target_tree')
    inactive('13-guard-before-ff')
    clean('14-clean-before-ff')
    preserved('15-preserved-before-ff', baseline, prefix, allow_append=False)
    git('16-fast-forward', 'merge', '--ff-only', '--no-edit', MERGE, timeout=120)
    if git('17-head', 'rev-parse', 'HEAD').decode().strip() != MERGE:
        raise Stop('post_ff_head_mismatch')
    clean('18-clean-before-build')
    preserved('19-preserved-before-build', baseline, prefix, allow_append=False)
    no_ignored_backend_cache('20-cache-before-build')
    inactive('21-guard-before-build')
    run('22-build', ['yarn', 'build'], cwd=APP / 'frontend', timeout=None)
    # 22-build.result.json already contains the actual child exit and timings.
    identity_bytes = (APP / 'backend/release_identity.json').read_bytes()
    if json.loads(identity_bytes) != identity:
        raise Stop('materialized_runtime_identity_differs_from_reviewed_intent')
    write_json(EVIDENCE / '22-identity-check.json', {
        'source_git_sha': SOURCE, 'release_id': RELEASE, 'matches_reviewed_intent': True,
        'identity_sha256': hashlib.sha256(identity_bytes).hexdigest()})
    clean('23-clean-after-build')
    if git('23-head-after-build', 'rev-parse', 'HEAD').decode().strip() != MERGE:
        raise Stop('head_changed_during_build')
    preserved('24-after-build', baseline, prefix, allow_append=True)
    inactive('25-guard-after-build')
    return {'phase': 'build', 'ready': True, 'source_sha': SOURCE, 'deployment_sha': MERGE,
            'intent_commit': INTENT_COMMIT, 'tree': TREE, 'release_id': RELEASE,
            'source_base_sha': BASE, 'intent_sha256': INTENT_SHA256,
            'untracked_count': len(baseline), 'lease_created': False}


if __name__ == '__main__':
    def commit_sha(value):
        if len(value) != 40 or any(char not in '0123456789abcdef' for char in value):
            raise argparse.ArgumentTypeError('A complete lowercase Git SHA is required')
        return value

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--intent-commit', type=commit_sha, default=REVIEWED_INTENT_COMMIT)
    parser.add_argument('--deployment-sha', type=commit_sha, default=REVIEWED_M)
    parser.add_argument('--deployment-tree', type=commit_sha, default=REVIEWED_TREE)
    ARGS = parser.parse_args()
    validate_reviewed_configuration()
    if ARGS.build and not all((ARGS.intent_commit, ARGS.deployment_sha, ARGS.deployment_tree)):
        parser.error('--build requires reviewed --intent-commit, --deployment-sha and --deployment-tree')
    if (ARGS.intent_commit, ARGS.deployment_sha, ARGS.deployment_tree) != (REVIEWED_INTENT_COMMIT, REVIEWED_M, REVIEWED_TREE):
        parser.error('Intent commit/tree differ from the reviewed PR1925 handoff')
    INTENT_COMMIT, MERGE, TREE = ARGS.intent_commit, ARGS.deployment_sha, ARGS.deployment_tree
    os.umask(0o077)
    EVIDENCE = Path(tempfile.mkdtemp(prefix='mezan-pr1925-original-', dir='/tmp'))
    print(json.dumps({'phase': 'started', 'evidence_dir': str(EVIDENCE)}), flush=True)
    CHILD_ENV = os.environ.copy()
    CHILD_ENV['PYTHONDONTWRITEBYTECODE'] = '1'
    CHILD_ENV['REACT_APP_BACKEND_URL'] = 'https://mezansalla.com'
    try:
        outcome = main()
        outcome.update(evidence_dir=str(EVIDENCE), finished_at=utc())
        write_json(EVIDENCE / 'outcome.json', outcome)
        print(json.dumps(outcome, ensure_ascii=True))
    except BaseException as exc:
        outcome = {'ready': False, 'reason': str(exc) if isinstance(exc, Stop) else type(exc).__name__,
                   'evidence_dir': str(EVIDENCE), 'finished_at': utc()}
        write_json(EVIDENCE / 'outcome.json', outcome)
        print(json.dumps(outcome, ensure_ascii=True))
        raise SystemExit(1)
